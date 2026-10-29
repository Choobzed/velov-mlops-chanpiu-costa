"""Promotion d'un modèle au registre MLflow (TP4, Must 3).

Le registre garde toutes les versions du modèle `velov-availability`. Deux alias disent
lesquelles comptent :
- @challenger : la dernière version inscrite (`velov.train --mlflow --register`), candidate ;
- @champion   : la version qui doit être servie.
Une version ne change jamais ; c'est l'alias @champion qui se déplace, selon une règle écrite
AVANT de regarder les chiffres (S4 · 23), vérifiée dans cet ordre :

1. Même fenêtre : challenger et champion sont réévalués ici, sur les mêmes derniers jours des
   données actuelles. Comparer au score enregistré à l'entraînement du champion ne vaut rien :
   ses données de test étaient plus anciennes (S4 · 24).
2. Battre la persistance : sinon le modèle ne justifie pas sa complexité (EX-05).
3. Battre le champion d'au moins 2 % : en dessous, l'écart est de l'ordre du bruit lié à la
   seed (0,4 % entre les seeds 42 et 7, voir README_EXECUTION.md, section 11).

Par défaut, le script affiche sa décision sans rien changer. Un membre du binôme la relit, puis
relance avec --apply (validation humaine, S4 · 25). --apply :
- en cas de promotion : @champion passe sur le challenger, l'ancien champion devient
  @previous-champion (retour arrière en une commande), l'alias @challenger est retiré ;
- dans tous les cas : un tag `promotion` sur la version du challenger garde la décision,
  sa raison, la date et qui a validé (EX-14).

Usage :
    export MLFLOW_TRACKING_URI=http://192.168.1.232:5000
    python -m velov.train --seed 42 --mlflow --register   # nouvelle version, alias @challenger
    python -m velov.promote                                # affiche la décision, ne change rien
    python -m velov.promote --apply                        # applique la décision
"""

from __future__ import annotations

import argparse
import getpass
from datetime import UTC, datetime
from pathlib import Path

from velov.features import make_training_frame
from velov.tracking import CHALLENGER_ALIAS, REGISTERED_MODEL_NAME, _git
from velov.train import _iso, evaluate, load_training_data, temporal_split

CHAMPION_ALIAS = "champion"
PREVIOUS_ALIAS = "previous-champion"
MIN_GAIN = 0.02  # 2 % : au-dessus du bruit mesuré entre deux seeds


def decide(challenger: dict, champion: dict | None, min_gain: float = MIN_GAIN) -> tuple[bool, str]:
    """La règle de promotion. Renvoie (promouvoir ?, raison).

    challenger et champion sont des scores produits par score() : mae_model,
    mae_baseline_persistence, test_start, test_end. champion vaut None s'il n'y en a pas encore.
    """
    c = challenger
    # 1. Même fenêtre de test, sinon les deux MAE ne se comparent pas.
    if champion is not None and (c["test_start"], c["test_end"]) != (champion["test_start"], champion["test_end"]):
        return False, "fenêtres de test différentes : comparaison impossible"
    # 2. Battre la persistance.
    if c["mae_model"] >= c["mae_baseline_persistence"]:
        return False, (f"ne bat pas la persistance (MAE {c['mae_model']:.3f} >= {c['mae_baseline_persistence']:.3f})")
    if champion is None:
        return True, "premier champion"
    # 3. Battre le champion d'au moins min_gain.
    target = champion["mae_model"] * (1 - min_gain)
    if c["mae_model"] <= target:
        return True, f"MAE {c['mae_model']:.3f} <= {target:.3f} (champion - {min_gain:.0%})"
    return False, f"gain insuffisant : MAE {c['mae_model']:.3f} > {target:.3f} (champion - {min_gain:.0%})"


def evaluation_window(data_path: Path, test_days: int):
    """Les derniers jours des données actuelles : la fenêtre commune aux deux modèles."""
    raw, _ = load_training_data(data_path)  # même contrat Pandera qu'à l'entraînement
    _, test_df = temporal_split(make_training_frame(raw), test_days)
    return test_df


def score(model, test_df) -> dict:
    """Les erreurs d'un modèle sur la fenêtre, et la fenêtre elle-même."""
    return {
        **evaluate(model, test_df),
        "test_start": _iso(test_df["timestamp"].min()),
        "test_end": _iso(test_df["timestamp"].max()),
        "n_test": int(len(test_df)),
    }


def _version(client, alias: str):
    """La version qui porte cet alias, ou None si l'alias n'existe pas (encore)."""
    from mlflow.exceptions import MlflowException

    try:
        return client.get_model_version_by_alias(REGISTERED_MODEL_NAME, alias)
    except MlflowException:
        return None


def _load(version):
    import mlflow

    return mlflow.sklearn.load_model(f"models:/{REGISTERED_MODEL_NAME}/{version.version}")


def promote(data_path: Path, test_days: int = 14, apply: bool = False) -> dict:
    """Compare @challenger à @champion sur la même fenêtre, et applique la décision si apply."""
    import mlflow

    client = mlflow.MlflowClient()
    challenger_v = _version(client, CHALLENGER_ALIAS)
    if challenger_v is None:
        raise SystemExit(
            f"Aucun @{CHALLENGER_ALIAS} au registre : lancer d'abord `python -m velov.train --mlflow --register`"
        )
    champion_v = _version(client, CHAMPION_ALIAS)

    test_df = evaluation_window(data_path, test_days)
    challenger = score(_load(challenger_v), test_df)
    champion = score(_load(champion_v), test_df) if champion_v is not None else None
    ok, reason = decide(challenger, champion)

    result = {
        "promote": ok,
        "reason": reason,
        "challenger_version": challenger_v.version,
        "champion_version": champion_v.version if champion_v is not None else None,
        "challenger": challenger,
        "champion": champion,
        "applied": False,
    }
    if apply:
        _apply(client, challenger_v, champion_v, ok, reason)
        result["applied"] = True
    return result


def _apply(client, challenger_v, champion_v, ok: bool, reason: str) -> None:
    who = _git("config", "user.name")
    if who in ("", "unknown"):
        who = getpass.getuser()
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    if ok:
        if champion_v is not None:
            client.set_registered_model_alias(REGISTERED_MODEL_NAME, PREVIOUS_ALIAS, champion_v.version)
        client.set_registered_model_alias(REGISTERED_MODEL_NAME, CHAMPION_ALIAS, challenger_v.version)
        client.delete_registered_model_alias(REGISTERED_MODEL_NAME, CHALLENGER_ALIAS)
    decision = "promue" if ok else "refusée"
    client.set_model_version_tag(
        REGISTERED_MODEL_NAME, challenger_v.version, "promotion", f"{decision} le {stamp} par {who} : {reason}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Décide si le @challenger devient @champion.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--test-days", type=int, default=14, help="fenêtre commune de test (défaut : 14 jours)")
    parser.add_argument("--apply", action="store_true", help="appliquer la décision (sinon : simple affichage)")
    args = parser.parse_args()

    r = promote(args.data, args.test_days, apply=args.apply)
    c, ch = r["challenger"], r["champion"]
    print(f"Fenêtre de test : {c['test_start']} -> {c['test_end']}, {c['n_test']} lignes")
    print(f"challenger v{r['challenger_version']:<4}: MAE {c['mae_model']:.3f}")
    if ch is not None:
        print(f"champion   v{r['champion_version']:<4}: MAE {ch['mae_model']:.3f}")
    else:
        print("champion        : aucun pour l'instant")
    print(f"persistance     : MAE {c['mae_baseline_persistence']:.3f}")
    print(f"Décision : {'PROMOUVOIR' if r['promote'] else 'REFUSER'} ({r['reason']})")
    if not r["applied"]:
        print("Rien n'a changé. Pour appliquer : python -m velov.promote --apply")
    elif r["promote"]:
        previous = f", @{PREVIOUS_ALIAS} -> v{r['champion_version']}" if r["champion_version"] else ""
        print(f"Appliqué : @{CHAMPION_ALIAS} -> v{r['challenger_version']}{previous}")
    else:
        print(f"Appliqué : refus noté dans le tag `promotion` de la version {r['challenger_version']}")


if __name__ == "__main__":
    main()
