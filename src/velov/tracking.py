"""Journalisation d'un entraînement dans MLflow (TP4, Must 2).

Un run MLflow répond à la question : « d'où vient ce modèle, et que vaut-il ? »
On y envoie :
- les paramètres (seed, hyperparamètres, fenêtre de test) : de quoi le refaire à l'identique ;
- les métriques (MAE globale, MAE aux heures de pointe, MAE de la baseline) ;
- des tags de traçabilité : commit git, code modifié ou non, empreinte et période des données ;
- le modèle au format MLflow (avec sa signature), et le paquet que charge l'API
  (model.joblib + metadata.json) dans le dossier d'artefacts `package/`.

L'adresse du serveur n'est jamais écrite dans le code : MLflow lit la variable
d'environnement MLFLOW_TRACKING_URI (le serveur de la VM, ou sqlite:///mlflow.db en local).

Ce module importe velov.train : velov.train ne doit donc l'importer qu'à l'intérieur de
main() (import local), sinon les deux modules s'attendent l'un l'autre (import circulaire).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import joblib
import pandas as pd

from velov.features import FEATURES, make_training_frame
from velov.train import MODEL_FILENAME, SKOPS_TRUSTED_TYPES

EXPERIMENT_NAME = "velov-availability"
REGISTERED_MODEL_NAME = "velov-availability"


def _git(*args: str) -> str:
    """Résultat d'une commande git, ou "unknown" si git est absent (dans un conteneur, par exemple)."""
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def git_tags() -> dict[str, str]:
    """Le commit du code, et si des fichiers suivis étaient modifiés sans être commités."""
    commit = _git("rev-parse", "HEAD")
    if commit == "unknown":
        return {"git_commit": "unknown", "git_dirty": "unknown"}
    # --untracked-files=no : un fichier qui traîne (un CSV, un brouillon) ne compte pas,
    # seules les modifications du code suivi par git rendent le run "dirty".
    dirty = _git("status", "--porcelain", "--untracked-files=no") != ""
    return {"git_commit": commit, "git_dirty": str(dirty).lower()}


def _signature_sample() -> pd.DataFrame:
    """Deux lignes d'exemple, passées par les mêmes features que l'entraînement."""
    return make_training_frame(
        pd.DataFrame(
            {
                "station_id": [1, 1],
                "timestamp": pd.to_datetime(["2026-06-01T06:00Z", "2026-06-01T07:00Z"]),
                "capacity": [20, 20],
                "bikes_available": [8, 5],
                "temperature": [21.0, 22.0],
                "is_raining": [False, False],
            }
        )
    )[FEATURES]


def log_run(metadata: dict, out_dir: Path, register: bool = False) -> str:
    """Journalise un entraînement dans MLflow et renvoie l'identifiant du run.

    register=True inscrit aussi le modèle au registre (nouvelle version de
    REGISTERED_MODEL_NAME), première étape avant une promotion (Must 3).
    """
    import mlflow  # import local : MLflow n'est pas requis pour servir le modèle
    from mlflow.models import infer_signature

    pipeline = joblib.load(out_dir / MODEL_FILENAME)
    sample = _signature_sample()

    mlflow.set_experiment(EXPERIMENT_NAME)
    run_name = f"v{metadata['model_version']}-seed{metadata['params']['seed']}"
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params({"model": "HistGradientBoostingRegressor", **metadata["params"]})
        mlflow.log_metrics({k: v for k, v in metadata["metrics"].items() if k.startswith("mae")})
        mlflow.log_metrics({"n_train": metadata["metrics"]["n_train"], "n_test": metadata["metrics"]["n_test"]})

        period = metadata["data_period"]
        mlflow.set_tags(
            {
                **git_tags(),
                "data_source": metadata["data_source"],
                "data_sha256": metadata["data_sha256"],
                "data_start": period["start"],
                "data_end": period["end"],
                "test_start": period["test_start"],
                "test_end": period["test_end"],
                "model_version": metadata["model_version"],
            }
        )

        mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=infer_signature(sample, pipeline.predict(sample)),
            input_example=sample,
            registered_model_name=REGISTERED_MODEL_NAME if register else None,
            # Depuis MLflow 3.x, sklearn est sérialisé par défaut en skops (format sûr,
            # sans exécution de code arbitraire), plus en pickle. skops refuse les types
            # qu'il ne connaît pas : on déclare explicitement ceux qu'on a vérifiés.
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
        )
        # Le paquet que charge l'API, tel quel : model.joblib + metadata.json.
        mlflow.log_artifacts(str(out_dir), artifact_path="package")

    return run.info.run_id
