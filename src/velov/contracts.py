"""Contrat de données de la table d'entraînement, vérifié par Pandera avant chaque entraînement.

TP3, Should. Le contrat dbt (S3) garantit les noms et les types des colonnes au moment où la
table gold est écrite. Pandera vérifie, au moment où l'entraînement la LIT, ce que dbt ne
contrôle pas : les plages de valeurs, la cohérence entre colonnes, l'unicité des lignes.
Si les données ne respectent pas le contrat, on refuse d'entraîner : un modèle entraîné sur
des données fausses ne lève aucune erreur, il prédit simplement mal.

Pandera n'est importé que dans les fonctions : l'API importe velov.train, et l'image de
serving ne doit pas embarquer les outils d'entraînement.
"""

from __future__ import annotations

import pandas as pd


def training_schema():
    """Le schéma attendu : les colonnes d'entrée du modèle (velov.features.RAW_COLUMNS)."""
    import pandera.pandas as pa

    return pa.DataFrameSchema(
        {
            # coerce=True : convertit les types compatibles (int32 -> int64...) et échoue sinon.
            "station_id": pa.Column("int64", pa.Check.ge(1), coerce=True),
            # Pas de conversion automatique ici : un instant sans fuseau est ambigu (8 h à Lyon
            # ou 8 h UTC ?), on le refuse au lieu de deviner.
            "timestamp": pa.Column(
                checks=pa.Check(
                    lambda s: isinstance(s.dtype, pd.DatetimeTZDtype),
                    element_wise=False,
                    error="timestamp doit être une date avec fuseau horaire",
                ),
                coerce=False,
            ),
            "capacity": pa.Column("int64", [pa.Check.gt(0), pa.Check.le(100)], coerce=True),
            "bikes_available": pa.Column("int64", pa.Check.ge(0), coerce=True),
            "temperature": pa.Column("float64", pa.Check.in_range(-30, 50), coerce=True),
            "is_raining": pa.Column("bool", coerce=True),
        },
        # Mêmes règles que le contrat de l'API (velov.api.schemas) : le modèle doit être
        # entraîné sur des données qu'il aurait acceptées en production.
        checks=pa.Check(
            lambda df: df["bikes_available"] <= df["capacity"],
            name="bikes_available_le_capacity",
            error="bikes_available doit être inférieur ou égal à capacity",
        ),
        unique=["station_id", "timestamp"],  # une ligne par station et par heure
        strict=True,  # aucune colonne en plus ou en moins
        name="velov_training_data",
    )


def validate_training_data(df: pd.DataFrame) -> pd.DataFrame:
    """Vérifie le contrat et renvoie les données (types normalisés). Lève une erreur sinon.

    lazy=True : on collecte TOUTES les violations avant d'échouer, pour un rapport complet
    plutôt qu'une erreur à la fois.
    """
    return training_schema().validate(df, lazy=True)
