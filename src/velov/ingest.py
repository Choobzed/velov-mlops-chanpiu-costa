"""Ingestion bronze : copie le flux GBFS Vélo'v dans DuckDB, sans doublon.

TP3, Must 1. Mode IA déclarée.

Le flux GBFS ne donne que l'état actuel des stations. Lancé régulièrement
(toutes les 5 minutes), ce script construit l'historique dont le modèle a besoin.
Il est idempotent : le relancer sur les mêmes données n'ajoute aucune ligne.

Flux collectés : station_status (état des stations, en continu),
station_information (fiche des stations : nom, position, capacité) et la météo
actuelle à Lyon (Open-Meteo : température, précipitations), dont le modèle a besoin
et que Vélo'v ne fournit pas.

Usage :
    python -m velov.ingest
    python -m velov.ingest --db data/velov.duckdb
"""

from __future__ import annotations

import argparse
import json
import logging
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import duckdb

logger = logging.getLogger("velov.ingest")

GBFS_URL = "https://download.data.grandlyon.com/files/rdata/jcd_jcdecaux.jcdvelov/gbfs.json"
# Météo actuelle au centre de Lyon (place Bellecour), sans clé d'API. Les heures sont en GMT.
WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=45.7578&longitude=4.8320&current=temperature_2m,precipitation"
)
# Chemin absolu, calculé depuis l'emplacement de ce fichier (src/velov/ingest.py) :
# le script écrit toujours dans data/ à la racine du projet, quel que soit le dossier courant.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "velov.duckdb"

# Bronze : les champs du flux tels quels, plus loaded_at (ajouté par nous).
CREATE_BRONZE = """
CREATE TABLE IF NOT EXISTS bronze_station_status (
    station_id              VARCHAR,
    num_bikes_available     INTEGER,
    num_bikes_disabled      INTEGER,
    num_docks_available     INTEGER,
    num_docks_disabled      INTEGER,
    is_installed            BOOLEAN,
    is_renting              BOOLEAN,
    is_returning            BOOLEAN,
    vehicle_types_available VARCHAR,
    last_reported           BIGINT,
    loaded_at               TIMESTAMPTZ,
    PRIMARY KEY (station_id, last_reported)
)
"""

# Une observation déjà présente (même station, même last_reported) est ignorée :
# c'est ce qui rend l'ingestion idempotente.
INSERT_BRONZE = """
INSERT INTO bronze_station_status VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
"""

# station_information : la fiche de chaque station (nom, position, capacité). Elle change
# rarement et ne contient aucune heure de modification. On garde une ligne par VERSION de fiche :
# une fiche reçue n'est ajoutée que si elle diffère de la dernière version connue de sa station.
# Rien n'est jamais écrasé : le bronze garde l'historique, le silver ne montre que la plus récente.
CREATE_BRONZE_INFORMATION = """
CREATE TABLE IF NOT EXISTS bronze_station_information (
    station_id   VARCHAR,
    name         VARCHAR,
    lat          DOUBLE,
    lon          DOUBLE,
    capacity     INTEGER,
    raw          VARCHAR,      -- la fiche complète, telle que reçue (texte JSON)
    loaded_at    TIMESTAMPTZ,  -- quand cette version a été chargée
    PRIMARY KEY (station_id, loaded_at)
)
"""

# Les fiches reçues sont d'abord posées dans une table temporaire, puis comparées
# à la dernière version connue de chaque station.
CREATE_INCOMING_INFORMATION = """
CREATE OR REPLACE TEMP TABLE incoming_station_information AS
SELECT * FROM bronze_station_information LIMIT 0
"""

INSERT_INCOMING_INFORMATION = """
INSERT INTO incoming_station_information VALUES (?, ?, ?, ?, ?, ?, ?)
"""

# On insère une fiche reçue SAUF s'il existe déjà, pour sa station, une dernière version
# identique sur tous les champs. IS NOT DISTINCT FROM est un "égal" qui sait comparer
# deux valeurs vides (NULL = NULL est "inconnu" en SQL, pas "vrai").
# raw contient la fiche complète : le comparer couvre aussi les champs non extraits en colonnes.
INSERT_CHANGED_INFORMATION = """
INSERT INTO bronze_station_information
SELECT incoming.*
FROM incoming_station_information AS incoming
WHERE NOT EXISTS (
    SELECT 1
    FROM (
        SELECT
            *,
            row_number() OVER (PARTITION BY station_id ORDER BY loaded_at DESC) AS version_rank
        FROM bronze_station_information
    ) AS latest
    WHERE latest.version_rank = 1
      AND latest.station_id = incoming.station_id
      AND latest.name     IS NOT DISTINCT FROM incoming.name
      AND latest.lat      IS NOT DISTINCT FROM incoming.lat
      AND latest.lon      IS NOT DISTINCT FROM incoming.lon
      AND latest.capacity IS NOT DISTINCT FROM incoming.capacity
      AND latest.raw      IS NOT DISTINCT FROM incoming.raw
)
ON CONFLICT DO NOTHING
"""


# Météo : Open-Meteo met à jour la météo "actuelle" toutes les 15 minutes, et donne l'heure de
# la mesure (current.time). Comme pour les relevés, cette heure est une clé naturelle :
# une mesure déjà enregistrée est ignorée.
CREATE_BRONZE_WEATHER = """
CREATE TABLE IF NOT EXISTS bronze_weather (
    observed_time   VARCHAR,      -- heure de la mesure, telle que fournie (GMT, ex. 2026-10-26T15:30)
    temperature_2m  DOUBLE,       -- température à 2 m, en °C
    precipitation   DOUBLE,       -- précipitations, en mm
    raw             VARCHAR,      -- la réponse "current" complète (texte JSON)
    loaded_at       TIMESTAMPTZ,
    PRIMARY KEY (observed_time)
)
"""

INSERT_BRONZE_WEATHER = """
INSERT INTO bronze_weather VALUES (?, ?, ?, ?, ?)
ON CONFLICT DO NOTHING
"""


def fetch_json(url: str) -> dict:
    """Télécharge `url` et renvoie le JSON sous forme de dictionnaire Python.

    TODO 1 : c'est l'équivalent Python de `curl`.
      - urllib.request.urlopen(url, timeout=...) ouvre la réponse ;
        mettez un timeout (10 secondes) pour ne pas attendre indéfiniment ;
      - json.load(reponse) transforme le texte reçu en dictionnaire ;
      - utilisez un bloc `with` pour que la connexion soit fermée.
    """
    # noqa S310 : l'adresse est fixe et en https, pas fournie par un utilisateur.
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        return json.load(response)


def find_feed_url(gbfs: dict, name: str) -> str:
    """Cherche dans gbfs.json l'adresse du flux `name` (par exemple "station_status").

    TODO 2 : rappel de la structure observée avec curl :
      gbfs["data"]["fr"]["feeds"] est une liste de {"name": ..., "url": ...}.
      Parcourez-la et renvoyez l'url dont le name correspond.
      Si aucun flux ne correspond, levez une ValueError avec un message clair.
    """
    for feed in gbfs["data"]["fr"]["feeds"]:
        if feed["name"] == name:
            return feed["url"]
    raise ValueError(f"Flux {name!r} absent de gbfs.json")


def to_rows(stations: list[dict], loaded_at: datetime) -> list[tuple]:
    """Transforme chaque station du flux en tuple, dans l'ordre des colonnes de la table.

    TODO 3 : pour chaque station, construisez un tuple de 11 valeurs, dans l'ordre
    exact du CREATE TABLE ci-dessus (station_id en premier, loaded_at en dernier).
      - station.get("champ") renvoie None si le champ est absent, au lieu de planter ;
      - vehicle_types_available est une liste : json.dumps(...) la transforme en texte JSON ;
      - loaded_at est le même pour toutes les lignes (il est passé en paramètre).
    """
    rows = []
    for station in stations:
        vehicle_types = station.get("vehicle_types_available")
        rows.append(
            (
                station.get("station_id"),
                station.get("num_bikes_available"),
                station.get("num_bikes_disabled"),
                station.get("num_docks_available"),
                station.get("num_docks_disabled"),
                station.get("is_installed"),
                station.get("is_renting"),
                station.get("is_returning"),
                json.dumps(vehicle_types) if vehicle_types is not None else None,
                station.get("last_reported"),
                loaded_at,
            )
        )
    return rows


def to_information_rows(stations: list[dict], loaded_at: datetime) -> list[tuple]:
    """Transforme chaque fiche de station en tuple, dans l'ordre des colonnes de bronze_station_information.

    La fiche complète est gardée telle quelle dans `raw` (texte JSON, clés triées pour que
    deux fiches identiques donnent toujours exactement le même texte).
    """
    rows = []
    for station in stations:
        raw = json.dumps(station, sort_keys=True, ensure_ascii=False)
        rows.append(
            (
                station.get("station_id"),
                station.get("name"),
                station.get("lat"),
                station.get("lon"),
                station.get("capacity"),
                raw,
                loaded_at,
            )
        )
    return rows


def to_weather_rows(payload: dict, loaded_at: datetime) -> list[tuple]:
    """Transforme la réponse Open-Meteo en une ligne de bronze_weather (aucune si la mesure manque)."""
    current = payload.get("current")
    if not current or current.get("time") is None:
        return []
    return [
        (
            current.get("time"),
            current.get("temperature_2m"),
            current.get("precipitation"),
            json.dumps(current, sort_keys=True),
            loaded_at,
        )
    ]


def _insert(con: duckdb.DuckDBPyConnection, table: str, create_sql: str, insert_sql: str, rows: list[tuple]) -> int:
    """Crée la table si besoin, insère les lignes sans doublon, renvoie le nombre de lignes ajoutées."""
    con.execute(create_sql)
    before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 (nom de table fixe)
    if rows:
        con.executemany(insert_sql, rows)
    after = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
    return after - before


def _insert_changed_information(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> int:
    """Ajoute les fiches nouvelles ou modifiées, renvoie le nombre de lignes ajoutées."""
    con.execute(CREATE_BRONZE_INFORMATION)
    before = con.execute("SELECT count(*) FROM bronze_station_information").fetchone()[0]
    con.execute(CREATE_INCOMING_INFORMATION)
    if rows:
        con.executemany(INSERT_INCOMING_INFORMATION, rows)
    con.execute(INSERT_CHANGED_INFORMATION)
    after = con.execute("SELECT count(*) FROM bronze_station_information").fetchone()[0]
    return after - before


def ingest(db_path: Path = DEFAULT_DB) -> dict[str, tuple[int, int]]:
    """Télécharge station_status, station_information et la météo, et les ajoute au bronze.

    Renvoie, pour chaque flux, (lignes reçues, lignes ajoutées).
    """
    loaded_at = datetime.now(UTC)  # une seule valeur pour tout ce passage

    gbfs = fetch_json(GBFS_URL)
    status = fetch_json(find_feed_url(gbfs, "station_status"))
    information = fetch_json(find_feed_url(gbfs, "station_information"))
    status_rows = to_rows(status["data"]["stations"], loaded_at)
    information_rows = to_information_rows(information["data"]["stations"], loaded_at)

    # Une panne du service météo ne doit pas empêcher de collecter les stations.
    try:
        weather_rows = to_weather_rows(fetch_json(WEATHER_URL), loaded_at)
    except (OSError, ValueError):
        logger.exception("Météo indisponible : collecte des stations seule pour ce passage")
        weather_rows = []

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        added_status = _insert(con, "bronze_station_status", CREATE_BRONZE, INSERT_BRONZE, status_rows)
        added_information = _insert_changed_information(con, information_rows)
        added_weather = _insert(con, "bronze_weather", CREATE_BRONZE_WEATHER, INSERT_BRONZE_WEATHER, weather_rows)

    return {
        "station_status": (len(status_rows), added_status),
        "station_information": (len(information_rows), added_information),
        "weather": (len(weather_rows), added_weather),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion bronze du flux GBFS Vélo'v dans DuckDB.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="fichier DuckDB")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    results = ingest(args.db)
    for feed, (received, added) in results.items():
        logger.info("%s : %d lignes reçues, %d nouvelles ajoutées dans %s", feed, received, added, args.db)


if __name__ == "__main__":
    main()
