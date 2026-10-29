"""Entraînement du modèle v0 et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline sklearn complet (prétraitement + modèle) -> models/model.joblib
- ses métadonnées (version, features, métriques, empreinte SHA-256, versions des
  librairies, hash des données) -> models/metadata.json

Sources de données acceptées (--data) :
- un fichier CSV (données simulées, `python -m velov.data`) ;
- une base DuckDB (.duckdb) : on lit alors la table gold `station_hourly`, construite par dbt
  à partir des vraies données Vélo'v (TP3).
Dans les deux cas, les données doivent respecter le contrat Pandera de velov.contracts
avant l'entraînement.

Usage :
    python -m velov.train --data data/velov_history.csv --out models/
    python -m velov.train --data data/velov.duckdb --out models/
    python -m velov.train --data data/velov.duckdb --test-days 2   # historique encore court
    python -m velov.train --seed 42 --mlflow              # journalise le run (MLFLOW_TRACKING_URI)
    python -m velov.train --seed 42 --mlflow --register   # et inscrit le modèle au registre
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from velov.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    RAW_COLUMNS,
    TARGET,
    make_training_frame,
)

MODEL_FILENAME = "model.joblib"
GOLD_TABLE = "station_hourly"  # table d'entraînement construite par dbt (dbt/models/gold)
METADATA_FILENAME = "metadata.json"
SKOPS_TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]
MAX_ITER: int = 200
LEARNING_RATE: float = 0.1
# Heures de pointe (heure de Lyon) : trajets domicile-travail du matin et du soir.
# C'est là que la prévision compte le plus pour un usager.
PEAK_HOURS: tuple[int, ...] = (7, 8, 9, 17, 18, 19)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_sha256(df: pd.DataFrame) -> str:
    """Empreinte du CONTENU des données, indépendante du fichier qui les stocke.

    Le SHA-256 d'un fichier DuckDB change dès que la base est réécrite, même si les lignes
    sont identiques : on hache donc les lignes, triées par clé, et non le fichier.
    """
    ordered = df.sort_values(["station_id", "timestamp"]).reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(ordered, index=False).to_numpy()
    return hashlib.sha256(row_hashes.tobytes()).hexdigest()


def load_training_data(data_path: Path) -> tuple[pd.DataFrame, str]:
    """Charge les données d'entraînement, vérifie le contrat Pandera, renvoie (données, empreinte).

    - .duckdb : lit la table gold station_hourly (vraies données, TP3) ;
    - sinon   : lit un CSV (données simulées, S1).
    """
    from velov.contracts import validate_training_data  # import local : Pandera hors de l'image

    if data_path.suffix == ".duckdb":
        import duckdb  # import local : DuckDB n'est pas requis pour servir le modèle

        with duckdb.connect(str(data_path), read_only=True) as con:
            con.execute("SET TimeZone = 'UTC'")  # instants renvoyés en UTC, quel que soit le poste
            raw = con.execute(f"SELECT {', '.join(RAW_COLUMNS)} FROM {GOLD_TABLE}").df()  # noqa: S608
        raw = validate_training_data(raw)
        data_hash = content_sha256(raw)
    else:
        raw = pd.read_csv(data_path, parse_dates=["timestamp"])
        raw = validate_training_data(raw)
        data_hash = sha256_of(data_path)
    return raw, data_hash


def build_pipeline(seed: int = 42) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(max_iter=MAX_ITER, learning_rate=LEARNING_RATE, random_state=seed)
    return Pipeline([("preprocess", preprocess), ("model", model)])


def _iso(ts: object) -> str:
    """Horodatage en texte ISO 8601 (UTC), pour metadata.json et les tags MLflow."""
    return pd.Timestamp(ts).tz_convert("UTC").isoformat()


def temporal_split(df: pd.DataFrame, test_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Découpage temporel : on teste sur le futur, jamais sur un échantillon aléatoire."""
    cutoff = pd.to_datetime(df["timestamp"]).max() - pd.Timedelta(days=test_days)
    ts = pd.to_datetime(df["timestamp"])
    train, test = df[ts <= cutoff], df[ts > cutoff]
    if train.empty or test.empty:
        raise ValueError("Split vide : augmenter l'historique ou réduire test_days")
    return train, test


def evaluate(pipeline: Pipeline, test_df: pd.DataFrame) -> dict[str, float]:
    """Erreurs du modèle et de la baseline sur une fenêtre de test (valeurs non arrondies).

    Utilisée à l'entraînement, et par velov.promote pour réévaluer challenger et champion
    sur la même fenêtre récente.
    """
    y_test = test_df[TARGET]
    pred = np.clip(pipeline.predict(test_df[FEATURES]), 0, test_df["capacity"])
    # Même erreur, mais seulement aux heures de pointe : la MAE globale est flattée
    # par les heures de nuit, où presque rien ne bouge.
    # y_test est une série pandas (filtre direct), pred un tableau numpy (.to_numpy()).
    is_peak = test_df["hour"].isin(PEAK_HOURS)
    return {
        "mae_model": float(mean_absolute_error(y_test, pred)),
        "mae_model_peak_hours": float(mean_absolute_error(y_test[is_peak], pred[is_peak.to_numpy()])),
        # Baseline "persistance" : dans 1 h, il y aura autant de vélos que maintenant.
        # Le modèle doit justifier sa complexité face à cette baseline (exigence EX-05).
        "mae_baseline_persistence": float(mean_absolute_error(y_test, test_df["bikes_available"])),
    }


def train(
    data_path: Path,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
) -> dict:
    """Entraîne, évalue contre une baseline, écrit l'artefact. Retourne les métadonnées."""
    if not data_path.exists():
        raise FileNotFoundError(
            f"{data_path} introuvable : lancer d'abord `python -m velov.data` (CSV simulé) "
            "ou `python -m velov.ingest` puis `dbt build` (base DuckDB)"
        )

    raw, data_hash = load_training_data(data_path)  # lève une erreur si le contrat est violé
    frame = make_training_frame(raw)
    train_df, test_df = temporal_split(frame, test_days)

    pipeline = build_pipeline(seed)
    pipeline.fit(train_df[FEATURES], train_df[TARGET])

    scores = evaluate(pipeline, test_df)

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / MODEL_FILENAME
    joblib.dump(pipeline, model_path)

    metadata = {
        "model_name": "velov-availability",
        "model_version": version,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": "Prédire le nombre de vélos disponibles à H+1 par station",
        "input_columns": RAW_COLUMNS,
        "features": FEATURES,
        "target": TARGET,
        "params": {
            "seed": seed,
            "max_iter": MAX_ITER,
            "learning_rate": LEARNING_RATE,
            "test_days": test_days,
        },
        "metrics": {
            **{name: round(value, 3) for name, value in scores.items()},
            "test_days": test_days,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        },
        "artifact": {"file": MODEL_FILENAME, "sha256": sha256_of(model_path)},
        "data_source": str(data_path),
        "data_sha256": data_hash,
        # Période couverte : deux runs ne sont comparables que s'ils sont évalués sur la
        # même fenêtre de test (règle de promotion, Must 3).
        "data_period": {
            "start": _iso(frame["timestamp"].min()),
            "end": _iso(frame["timestamp"].max()),
            "test_start": _iso(test_df["timestamp"].min()),
            "test_end": _iso(test_df["timestamp"].max()),
        },
        "runtime": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }
    (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraîne le modèle Vélo'v v0.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument(
        "--test-days", type=int, default=14, help="jours les plus récents réservés au test (défaut : 14)"
    )
    parser.add_argument("--seed", type=int, default=42, help="seed du modèle (défaut : 42)")
    parser.add_argument(
        "--mlflow", action="store_true", help="journaliser le run dans MLflow (serveur : MLFLOW_TRACKING_URI)"
    )
    parser.add_argument("--register", action="store_true", help="avec --mlflow : inscrire aussi le modèle au registre")
    args = parser.parse_args()

    metadata = train(args.data, args.out, version=args.version, test_days=args.test_days, seed=args.seed)
    m = metadata["metrics"]
    print(f"MAE modèle   : {m['mae_model']:.3f} vélos")
    print(f"MAE pointe   : {m['mae_model_peak_hours']:.3f} vélos (7-9 h et 17-19 h)")
    print(f"MAE baseline : {m['mae_baseline_persistence']:.3f} vélos (persistance)")
    print(f"Seed         : {metadata['params']['seed']}")
    print(f"Artefact     : {args.out / MODEL_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}...)")
    if args.mlflow:
        # Import local : velov.tracking importe velov.train, l'importer en haut du fichier
        # créerait un import circulaire. Et MLflow n'est requis que si on journalise.
        from velov.tracking import log_run

        run_id, version = log_run(metadata, args.out, register=args.register)
        print(f"Run MLflow   : {run_id}")
        if version is not None:
            print(f"Registre     : version {version}, alias @challenger (décision : python -m velov.promote)")


if __name__ == "__main__":
    main()
