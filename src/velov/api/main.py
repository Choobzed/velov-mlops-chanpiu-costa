"""API de serving du modèle Vélo'v.

TP1, partie 3 : exposez le modèle. Mode : IA déclarée autorisée pour cette partie.

Endpoints attendus (niveaux du TP1 : Must, Should, Stretch) :
    POST /v1/predict        [Must]    une prédiction
    GET  /health            [Should]  liveness : le process répond (ne dépend pas du modèle)
    GET  /ready             [Should]  readiness : 200 si le modèle est chargé, 503 sinon
    GET  /v1/model, POST /v1/predict/batch   [Stretch]

Lancement :
    uvicorn velov.api.main:app --reload
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
import psycopg
from fastapi import FastAPI, HTTPException

from velov.api.database import (
    check_database_connection,
    database_configured,
    initialize_database,
    save_prediction,
)
from velov.api.schemas import PredictionRequest, PredictionResponse
from velov.features import FEATURES, add_features
from velov.train import METADATA_FILENAME, sha256_of

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("velov.api")

STATE: dict[str, Any] = {"model": None, "metadata": None}


def load_model(model_dir: Path) -> tuple[object, dict]:
    """Fourni : charge le modèle APRÈS avoir vérifié son empreinte SHA-256."""
    metadata_path = model_dir / METADATA_FILENAME
    if not metadata_path.exists():
        raise FileNotFoundError(f"{metadata_path} introuvable")
    metadata = json.loads(metadata_path.read_text())
    model_path = model_dir / metadata["artifact"]["file"]
    if sha256_of(model_path) != metadata["artifact"]["sha256"]:
        raise RuntimeError(f"Empreinte invalide pour {model_path}")
    return joblib.load(model_path), metadata


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fourni : exécuté une fois au démarrage (avant yield) et à l'arrêt (après yield)."""
    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    try:
        STATE["model"], STATE["metadata"] = load_model(model_dir)
        logger.info("Modèle %s chargé", STATE["metadata"]["model_version"])
    except Exception:
        logger.exception("Échec du chargement du modèle depuis %s", model_dir)
    if database_configured():
        initialize_database()
        logger.info("PostgreSQL initialisé")
    else:
        logger.info("PostgreSQL non configuré; les prédictions ne seront pas persistées")
    yield
    STATE.update(model=None, metadata=None)


app = FastAPI(title="Vélo'v availability API", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    if STATE["model"] is None or STATE["metadata"] is None:
        raise HTTPException(status_code=503, detail="Modèle indisponible")
    if database_configured() and not check_database_connection():
        raise HTTPException(status_code=503, detail="Base de données indisponible")
    return {"status": "ready", "model_version": STATE["metadata"]["model_version"]}


@app.post("/v1/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest) -> PredictionResponse:
    return _predict(request)


def _predict(request: PredictionRequest) -> PredictionResponse:
    model = STATE["model"]
    metadata = STATE["metadata"]
    if model is None or metadata is None:
        raise HTTPException(status_code=503, detail="Modèle indisponible")

    frame = pd.DataFrame([request.model_dump()])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    features = add_features(frame)[FEATURES]
    raw_prediction = float(model.predict(features)[0])
    prediction = min(max(raw_prediction, 0.0), float(request.capacity))
    response = PredictionResponse(
        station_id=request.station_id,
        target_timestamp=request.timestamp + timedelta(hours=1),
        predicted_bikes=prediction,
        model_version=metadata["model_version"],
    )

    if database_configured():
        try:
            save_prediction(request, response)
        except psycopg.Error as error:
            logger.exception("Échec de l'enregistrement de la prédiction")
            raise HTTPException(
                status_code=503, detail="Enregistrement en base indisponible"
            ) from error
    return response
