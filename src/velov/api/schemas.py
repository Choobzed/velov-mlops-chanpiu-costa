"""Contrat d'entrée / sortie de l'API (validé par Pydantic, publié dans OpenAPI).

TP1, partie 2 : complétez les schémas. Mode : SANS IA pour cette partie.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class PredictionRequest(BaseModel):
    """Une observation de station à l'instant t."""

    model_config = ConfigDict(extra="forbid")

    station_id: int = Field(..., ge=1, description="Identifiant de la station")
    timestamp: AwareDatetime
    capacity: int = Field(..., gt=0, le=100)
    bikes_available: int = Field(..., ge=0)
    temperature: float = Field(..., ge=-30, le=50)
    is_raining: bool

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_bikes_available(self) -> Self:
        if self.bikes_available > self.capacity:
            raise ValueError("bikes_available ne peut pas dépasser capacity")
        return self


class PredictionResponse(BaseModel):
    station_id: int
    target_timestamp: AwareDatetime = Field(..., description="Instant prédit (t + 1 h)")
    predicted_bikes: float = Field(..., ge=0)
    model_version: str


# TP6 (IA déclarée) : prédictions par lot, utilisées par velov.replay pour rejouer une semaine
# (toutes les stations d'une heure en un seul appel).
class BatchPredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PredictionRequest] = Field(..., min_length=1, max_length=1000)


class BatchPredictionResponse(BaseModel):
    predictions: list[PredictionResponse]
