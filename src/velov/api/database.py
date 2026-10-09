"""PostgreSQL storage for API predictions."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import psycopg

from velov.api.schemas import PredictionRequest, PredictionResponse

logger = logging.getLogger("velov.api.database")

CREATE_PREDICTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS predictions (
    id BIGSERIAL PRIMARY KEY,
    station_id INTEGER NOT NULL,
    observation_timestamp TIMESTAMPTZ NOT NULL,
    target_timestamp TIMESTAMPTZ NOT NULL,
    capacity INTEGER NOT NULL,
    bikes_available INTEGER NOT NULL,
    temperature DOUBLE PRECISION NOT NULL,
    is_raining BOOLEAN NOT NULL,
    predicted_bikes DOUBLE PRECISION NOT NULL,
    model_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

def database_configured() -> bool:
    return bool(os.getenv("DATABASE_URL") or os.getenv("PGHOST"))


def _connect() -> psycopg.Connection:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url)

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.getenv("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=(
            Path(os.environ["PGPASSWORD_FILE"]).read_text().strip()
            if os.getenv("PGPASSWORD_FILE")
            else os.environ["PGPASSWORD"]
        ),
    )


def initialize_database() -> None:
    with _connect() as connection:
        connection.execute(CREATE_PREDICTIONS_TABLE)


def check_database_connection() -> bool:
    try:
        with _connect() as connection:
            connection.execute("SELECT 1")
    except psycopg.Error:
        logger.exception("Connexion à PostgreSQL impossible")
        return False
    return True


def save_prediction(request: PredictionRequest, response: PredictionResponse) -> None:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO predictions (
                station_id, observation_timestamp, target_timestamp, capacity,
                bikes_available, temperature, is_raining, predicted_bikes, model_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request.station_id,
                request.timestamp,
                response.target_timestamp,
                request.capacity,
                request.bikes_available,
                request.temperature,
                request.is_raining,
                response.predicted_bikes,
                response.model_version,
            ),
        )
