"""Surveillance du modèle en production : dérive des données et erreur réelle (TP6, étape 4).

Dans cet ordre :
1. Référence : la semaine qui précède la semaine rejouée, prise dans la fenêtre de test du
   modèle servi (metadata.json), avec les prédictions du modèle. Aucune des deux semaines n'a
   servi à l'entraînement.
2. Courant : les 7 derniers jours de la table predictions, pour la version servie, une ligne
   par station et par heure (la dernière prédiction si une heure a été rejouée deux fois).
3. Dérive, colonne par colonne, avec Evidently : rapport HTML dans <out>/drift.html.
4. Erreur réelle : la vérité d'une prédiction faite à t est la disponibilité observée à t + 1 h,
   qui arrive avec l'observation de l'heure suivante. MAE du modèle face à la persistance,
   globale et par station. Code de sortie 1 si une alerte de performance est levée.

Une dérive seule ne déclenche pas d'alerte : elle explique, l'erreur décide (S6).

Usage (données et modèle : les mêmes que ceux de l'API, voir GET /v1/model) :
    python -m velov.monitor --db "$DATABASE_URL" --data data/velov_history.csv --out reports/

Rédigé avec l'aide d'une IA (Claude, Anthropic).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from velov.features import FEATURES, add_features
from velov.train import METADATA_FILENAME, MODEL_FILENAME, load_training_data

NUMERIC_COLUMNS = ["temperature", "bikes_available", "occupancy_rate", "predicted_bikes"]
CATEGORICAL_COLUMNS = ["is_raining"]
DRIFT_COLUMNS = NUMERIC_COLUMNS + CATEGORICAL_COLUMNS

# Seuils d'alerte de performance (ratio = MAE du modèle / MAE de la persistance ; plus bas = mieux).
# À l'entraînement, le ratio est d'environ 0,71 (1,339 / 1,879). Justification : model card.
MAX_RATIO = 0.90  # le modèle global perd l'essentiel de son avance sur la persistance
MAX_STATION_RATIO = 1.0  # sur une station, le modèle fait pire que la persistance
MIN_STATION_PREDICTIONS = 24  # pas de verdict sur une station avec moins d'un jour de vérité

# Une ligne par station et par heure : DISTINCT ON garde la plus récente si une heure a été
# rejouée deux fois. On ne prend que les 7 derniers jours de la version servie.
# Un test de charge (Locust, TP5) écrit aussi dans la table, avec des stations, des instants et
# des remplissages tirés au hasard sur 30 jours : ses lignes faussent dérive et erreur. On les
# exclut par leur date d'écriture (created_at), avec --created-after : seules les lignes écrites
# depuis le rejeu comptent.
CURRENT_QUERY = """
WITH kept AS (
    SELECT * FROM predictions
    WHERE model_version = %(version)s
      AND (%(created_after)s::timestamptz IS NULL OR created_at >= %(created_after)s::timestamptz)
)
SELECT DISTINCT ON (station_id, observation_timestamp)
    station_id, observation_timestamp, capacity, bikes_available, temperature, is_raining,
    predicted_bikes, model_version
FROM kept
WHERE observation_timestamp > (SELECT max(observation_timestamp) FROM kept) - make_interval(days => %(days)s)
ORDER BY station_id, observation_timestamp, created_at DESC
"""


def load_served_model(model_dir: Path):
    metadata = json.loads((model_dir / METADATA_FILENAME).read_text())
    return joblib.load(model_dir / MODEL_FILENAME), metadata


def predict(model, df: pd.DataFrame) -> np.ndarray:
    """Comme l'API : mêmes features, prédiction bornée entre 0 et la capacité."""
    return np.clip(model.predict(add_features(df)[FEATURES]), 0, df["capacity"].to_numpy(dtype=float))


def reference_frame(data: pd.DataFrame, model, metadata: dict, days: int = 7) -> pd.DataFrame:
    """La semaine précédant celle que rejoue velov.replay (les `days` derniers jours des données)."""
    ts = pd.to_datetime(data["timestamp"], utc=True)
    end = ts.max() - pd.Timedelta(days=days)
    ref = data.assign(timestamp=ts)[(ts > end - pd.Timedelta(days=days)) & (ts <= end)].copy()
    test_start = metadata.get("data_period", {}).get("test_start")
    if test_start is not None and ref["timestamp"].min() < pd.Timestamp(test_start):
        print("Attention : la référence déborde de la fenêtre de test, une partie a servi à l'entraînement.")
    ref["predicted_bikes"] = predict(model, ref)
    return with_occupancy(ref)


def with_occupancy(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(occupancy_rate=df["bikes_available"] / df["capacity"], is_raining=df["is_raining"].astype(bool))


def read_current(db_url: str, version: str, days: int = 7, created_after: str | None = None) -> pd.DataFrame:
    import psycopg  # import local : seule la lecture de la base en a besoin

    url = db_url.replace("postgresql+psycopg://", "postgresql://")  # forme SQLAlchemy acceptée
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(CURRENT_QUERY, {"version": version, "days": days, "created_after": created_after})
        columns = [c.name for c in cursor.description]
        rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=columns).rename(columns={"observation_timestamp": "timestamp"})
    if df.empty:
        raise SystemExit(f"Aucune prédiction du modèle {version} en base : lancer d'abord velov.replay")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    for col in ("temperature", "predicted_bikes"):
        df[col] = df[col].astype(float)
    return with_occupancy(df)


def drift(reference: pd.DataFrame, current: pd.DataFrame, html_path: Path | None = None) -> list[dict]:
    """Dérive par colonne avec Evidently. Renvoie, par colonne : méthode, score, seuil, dérive ou non."""
    from evidently import DataDefinition, Dataset, Report  # requirements-monitoring.txt
    from evidently.presets import DataDriftPreset

    definition = DataDefinition(numerical_columns=NUMERIC_COLUMNS, categorical_columns=CATEGORICAL_COLUMNS)
    snapshot = Report([DataDriftPreset(columns=DRIFT_COLUMNS)]).run(
        Dataset.from_pandas(current[DRIFT_COLUMNS], data_definition=definition),
        Dataset.from_pandas(reference[DRIFT_COLUMNS], data_definition=definition),
    )
    if html_path is not None:
        snapshot.save_html(str(html_path))
    results = []
    for metric in snapshot.dict()["metrics"]:
        name = metric["metric_name"]
        if not name.startswith("ValueDrift("):
            continue
        params = dict(part.split("=", 1) for part in name[len("ValueDrift(") : -1].split(","))
        score, threshold = float(metric["value"]), float(params["threshold"])
        # Une p-value dérive quand elle est PETITE ; une distance, quand elle est GRANDE.
        drifted = score < threshold if "p_value" in params["method"] else score >= threshold
        results.append(
            {
                "column": params["column"],
                "method": params["method"],
                "score": score,
                "threshold": threshold,
                "drift": drifted,
            }
        )
    return results


def with_truth(current: pd.DataFrame) -> pd.DataFrame:
    """Ajoute la vérité terrain : la disponibilité observée une heure plus tard, même station."""
    nxt = current[["station_id", "timestamp", "bikes_available"]].rename(columns={"bikes_available": "truth"})
    nxt = nxt.assign(timestamp=nxt["timestamp"] - timedelta(hours=1))
    out = current.merge(nxt, on=["station_id", "timestamp"], how="inner")
    return out.assign(
        err_model=(out["predicted_bikes"] - out["truth"]).abs(),
        err_persistence=(out["bikes_available"] - out["truth"]).abs(),
    )


def performance(current: pd.DataFrame) -> dict:
    scored = with_truth(current)
    by_station = (
        scored.groupby("station_id")
        .agg(n=("err_model", "size"), mae_model=("err_model", "mean"), mae_persistence=("err_persistence", "mean"))
        .assign(ratio=lambda d: d["mae_model"] / d["mae_persistence"])
        .sort_values("ratio", ascending=False)
    )
    mae_model, mae_persistence = scored["err_model"].mean(), scored["err_persistence"].mean()
    return {
        "n": int(len(scored)),
        "mae_model": float(mae_model),
        "mae_persistence": float(mae_persistence),
        "ratio": float(mae_model / mae_persistence),
        "by_station": by_station,
    }


def alerts(perf: dict, max_ratio: float = MAX_RATIO, max_station_ratio: float = MAX_STATION_RATIO) -> list[str]:
    """Les alertes de performance. Une liste vide : pas d'alerte."""
    found = []
    if perf["ratio"] > max_ratio:
        found.append(f"ratio global {perf['ratio']:.2f} > {max_ratio:.2f}")
    stations = perf["by_station"]
    bad = stations[(stations["n"] >= MIN_STATION_PREDICTIONS) & (stations["ratio"] > max_station_ratio)]
    for station_id, row in bad.iterrows():
        found.append(
            f"station {station_id} : ratio {row['ratio']:.2f} > {max_station_ratio:.2f} (pire que la persistance)"
        )
    return found


def history(data: pd.DataFrame, model, metadata: dict, days: int = 7) -> pd.DataFrame:
    """Les seuils rejoués sur l'historique : ratio global et pire station, semaine par semaine.

    Les semaines d'entraînement sont optimistes (le modèle les a vues) ; seules celles de la
    fenêtre de test sont un vrai repère. Sert à justifier les seuils (combien de fausses alertes ?).
    """
    ts = pd.to_datetime(data["timestamp"], utc=True)
    df = with_occupancy(data.assign(timestamp=ts))
    df["predicted_bikes"] = predict(model, df)
    test_start = pd.Timestamp(metadata["data_period"]["test_start"]) if "data_period" in metadata else ts.max()
    rows, end = [], ts.max()
    while end - pd.Timedelta(days=days) >= ts.min():
        week = df[(df["timestamp"] > end - pd.Timedelta(days=days)) & (df["timestamp"] <= end)]
        perf = performance(week)
        worst = perf["by_station"].iloc[0]
        rows.append(
            {
                "fin_de_semaine": end.date().isoformat(),
                "jeu": "test" if week["timestamp"].min() >= test_start else "entraînement",
                "ratio_global": round(perf["ratio"], 3),
                "pire_station": int(perf["by_station"].index[0]),
                "ratio_pire_station": round(float(worst["ratio"]), 3),
                "alerte": bool(alerts(perf)),
            }
        )
        end -= pd.Timedelta(days=days)
    return pd.DataFrame(rows)


def render(
    version: str,
    n_ref: int,
    n_cur: int,
    drifts: list[dict],
    perf: dict,
    train_ratio: float | None,
    found: list[str],
    top: int = 5,
) -> str:
    lines = [f"Modèle {version} · référence {n_ref} · courant {n_cur}", "", "Dérive (score par colonne)"]
    for d in drifts:
        flag = "  dérive" if d["drift"] else ""
        lines.append(f"  {d['column']:<17} {d['score']:.3f}   ({d['method']}, seuil {d['threshold']}){flag}")
    lines += [
        "",
        f"Performance sur {perf['n']} prédictions",
        f"  MAE modèle       {perf['mae_model']:.3f}",
        f"  MAE persistance  {perf['mae_persistence']:.3f}  (ratio {perf['ratio']:.2f}"
        + (f" ; {train_ratio:.2f} à l'entraînement)" if train_ratio else ")"),
        "",
        f"Stations les plus mal prédites (ratio le plus haut, {top} premières)",
    ]
    for station_id, row in perf["by_station"].head(top).iterrows():
        lines.append(
            f"  station {station_id:<4} MAE {row['mae_model']:.3f} · persistance {row['mae_persistence']:.3f}"
            f" · ratio {row['ratio']:.2f}"
        )
    lines.append("")
    lines += [f"ALERTE : {a}" for a in found] if found else ["Pas d'alerte de performance"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dérive et erreur réelle du modèle servi.")
    parser.add_argument("--history", action="store_true", help="rejouer les seuils sur l'historique, sans base")
    parser.add_argument("--db", help="adresse PostgreSQL, ex. postgresql+psycopg://velov:...@127.0.0.1:5432/velov")
    parser.add_argument(
        "--data", type=Path, required=True, help="les données qui ont entraîné le modèle (GET /v1/model)"
    )
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--out", type=Path, default=Path("reports"))
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--created-after",
        help="ignorer les lignes écrites avant cette date (ex. 2026-11-20) : tests de charge, essais à la main",
    )
    parser.add_argument("--max-ratio", type=float, default=MAX_RATIO)
    parser.add_argument("--max-station-ratio", type=float, default=MAX_STATION_RATIO)
    args = parser.parse_args()

    model, metadata = load_served_model(args.model_dir)
    version = metadata["model_version"]
    data, _ = load_training_data(args.data)
    if args.history:
        print(history(data, model, metadata, args.days).to_string(index=False))
        return
    if not args.db:
        parser.error("--db est obligatoire (sauf avec --history)")
    reference = reference_frame(data, model, metadata, args.days)
    current = read_current(args.db, version, args.days, args.created_after)
    if len(current) > 1.1 * len(reference):
        print(
            f"Attention : {len(current)} lignes dans le courant pour {len(reference)} dans la référence. La table "
            "contient sans doute des lignes étrangères au rejeu (test de charge ?) : relancer avec --created-after."
        )

    args.out.mkdir(parents=True, exist_ok=True)
    drifts = drift(reference, current, args.out / "drift.html")
    perf = performance(current)
    found = alerts(perf, args.max_ratio, args.max_station_ratio)
    m = metadata.get("metrics", {})
    train_ratio = m["mae_model"] / m["mae_baseline_persistence"] if m.get("mae_baseline_persistence") else None

    print(render(version, len(reference), len(current), drifts, perf, train_ratio, found))
    summary = {
        "model_version": version,
        "n_reference": len(reference),
        "n_current": len(current),
        "current_period": [current["timestamp"].min().isoformat(), current["timestamp"].max().isoformat()],
        "drift": drifts,
        "performance": {k: v for k, v in perf.items() if k != "by_station"},
        "by_station": perf["by_station"].reset_index().to_dict(orient="records"),
        "train_ratio": train_ratio,
        "alerts": found,
    }
    (args.out / "monitor.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
