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
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import psycopg
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Gauge
from prometheus_fastapi_instrumentator import Instrumentator

from velov.api.database import (
    check_database_connection,
    database_configured,
    initialize_database,
    save_predictions,
)
from velov.api.schemas import (
    BatchPredictionRequest,
    BatchPredictionResponse,
    PredictionRequest,
    PredictionResponse,
)
from velov.features import FEATURES, add_features
from velov.train import METADATA_FILENAME, sha256_of

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("velov.api")

STATE: dict[str, Any] = {"model": None, "metadata": None}

# Seaux de latence de http_request_duration_seconds (S6). Ceux de l'instrumentateur par défaut
# sont 0,1 / 0,5 / 1 s : sans seau à 0,2 s, impossible de compter les requêtes sous le SLO.
LATENCY_BUCKETS = (0.05, 0.1, 0.2, 0.5, 1.0, 2.5)

# Quel modèle est servi : valeur 1, et la version en étiquette (panneau « Modèle servi »).
MODEL_INFO = Gauge("velov_model_info", "Modèle servi par l'API (valeur 1)", ["model_version"])


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
        MODEL_INFO.clear()  # une seule version à la fois, même après un rechargement
        MODEL_INFO.labels(model_version=STATE["metadata"]["model_version"]).set(1)
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

# Métriques HTTP (S6) : nombre de requêtes et durées par route, méthode et famille de statut
# (2xx, 4xx, 5xx), publiées sur /metrics. Le proxy bloque /metrics (404) : seul Prometheus les
# lit, depuis le réseau Docker (api:8000). /metrics lui-même n'est pas compté.
Instrumentator(should_group_status_codes=True, excluded_handlers=["/metrics"]).instrument(
    app, latency_lowr_buckets=LATENCY_BUCKETS
).expose(app, include_in_schema=False)


@app.middleware("http")
async def add_process_time(request: Request, call_next):
    """Ajoute à chaque réponse le temps passé DANS l'API, en millisecondes (TP5).

    Comparé à la latence vue par le client (Locust), il sépare le temps de notre code du reste :
    serveur HTTP, réseau et, quand l'API sature, attente dans la file.
    """
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - start) * 1000:.2f}"
    return response


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


@app.get("/v1/model")
def model_info() -> dict[str, Any]:
    """Les métadonnées du modèle servi (TP6). `data.path` désigne les données qui l'ont entraîné :
    velov.replay et velov.monitor doivent utiliser les mêmes."""
    metadata = STATE["metadata"]
    if metadata is None:
        raise HTTPException(status_code=503, detail="Modèle indisponible")
    return {**metadata, "data": {"path": metadata.get("data_source"), "sha256": metadata.get("data_sha256")}}


@app.post("/v1/predict", response_model=PredictionResponse)
def predict(request: PredictionRequest) -> PredictionResponse:
    return _predict([request])[0]


@app.post("/v1/predict/batch", response_model=BatchPredictionResponse)
def predict_batch(batch: BatchPredictionRequest) -> BatchPredictionResponse:
    """Plusieurs stations en un appel (TP6, utilisé par velov.replay). Un seul model.predict pour
    tout le lot : le coût fixe mesuré au TP5 n'est payé qu'une fois."""
    return BatchPredictionResponse(predictions=_predict(batch.items))


def _predict(requests: list[PredictionRequest]) -> list[PredictionResponse]:
    model = STATE["model"]
    metadata = STATE["metadata"]
    if model is None or metadata is None:
        raise HTTPException(status_code=503, detail="Modèle indisponible")

    frame = pd.DataFrame([request.model_dump() for request in requests])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    features = add_features(frame)[FEATURES]
    predictions = np.clip(model.predict(features), 0, frame["capacity"].to_numpy(dtype=float))
    responses = [
        PredictionResponse(
            station_id=request.station_id,
            target_timestamp=request.timestamp + timedelta(hours=1),
            predicted_bikes=float(prediction),
            model_version=metadata["model_version"],
        )
        for request, prediction in zip(requests, predictions, strict=True)
    ]

    if database_configured():
        try:
            save_predictions(requests, responses)
        except psycopg.Error as error:
            logger.exception("Échec de l'enregistrement des prédictions")
            raise HTTPException(status_code=503, detail="Enregistrement en base indisponible") from error
    return responses
