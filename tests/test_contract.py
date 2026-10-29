"""Contrat Pandera des données d'entraînement (TP3, Should)."""

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from velov.contracts import validate_training_data
from velov.data import simulate


def test_simulated_data_respects_contract():
    validate_training_data(simulate(days=2, n_stations=2))


def test_bikes_above_capacity_is_rejected():
    df = simulate(days=2, n_stations=1)
    df.loc[0, "bikes_available"] = df.loc[0, "capacity"] + 1
    with pytest.raises(SchemaErrors, match="bikes_available"):
        validate_training_data(df)


def test_naive_timestamp_is_rejected():
    df = simulate(days=2, n_stations=1)
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    with pytest.raises(SchemaErrors):
        validate_training_data(df)


def test_duplicate_station_hour_is_rejected():
    df = simulate(days=2, n_stations=1)
    with pytest.raises(SchemaErrors):
        validate_training_data(pd.concat([df, df.head(1)], ignore_index=True))


def test_unexpected_column_is_rejected():
    df = simulate(days=2, n_stations=1).assign(colonne_en_trop=1)
    with pytest.raises(SchemaErrors):
        validate_training_data(df)


def test_out_of_range_temperature_is_rejected():
    df = simulate(days=2, n_stations=1)
    df.loc[0, "temperature"] = 80.0
    with pytest.raises(SchemaErrors, match="temperature"):
        validate_training_data(df)
