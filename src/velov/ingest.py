"""Ingestion bronze : copie le flux GBFS Vélo'v dans DuckDB, sans doublon.

TP3, Must 1. Mode IA déclarée.

Le flux GBFS ne donne que l'état actuel des stations. Lancé régulièrement
(toutes les 5 minutes), ce script construit l'historique dont le modèle a besoin.
Il est idempotent : le relancer sur les mêmes données n'ajoute aucune ligne.

Flux collectés : station_status (état des stations, en continu) et
station_information (fiche des stations : nom, position, capacité).

Usage :
    python -m velov.ingest
    python -m velov.ingest --db data/velov.duckdb
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import duckdb

logger = logging.getLogger("velov.ingest")

GBFS_URL = "https://download.data.grandlyon.com/files/rdata/jcd_jcdecaux.jcdvelov/gbfs.json"
DEFAULT_DB = Path("data/velov.duckdb")

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

# station_information : la fiche des stations (nom, position, capacité). Elle change rarement
# et n'a pas d'heure de relevé par station. On garde donc une ligne par VERSION de fiche :
# record_hash est l'empreinte du contenu, et la clé (station_id, record_hash) fait qu'une
# fiche inchangée n'est jamais réinsérée. Si la capacité change, une nouvelle ligne apparaît.
CREATE_BRONZE_INFORMATION = """
CREATE TABLE IF NOT EXISTS bronze_station_information (
    station_id   VARCHAR,
    name         VARCHAR,
    lat          DOUBLE,
    lon          DOUBLE,
    capacity     INTEGER,
    raw          VARCHAR,
    record_hash  VARCHAR,
    loaded_at    TIMESTAMPTZ,
    PRIMARY KEY (station_id, record_hash)
)
"""

INSERT_BRONZE_INFORMATION = """
INSERT INTO bronze_station_information VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

    La fiche complète est gardée telle quelle dans `raw` (texte JSON). Son empreinte SHA-256,
    calculée sur le JSON aux clés triées, identifie une version de la fiche.
    """
    rows = []
    for station in stations:
        raw = json.dumps(station, sort_keys=True, ensure_ascii=False)
        record_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        rows.append(
            (
                station.get("station_id"),
                station.get("name"),
                station.get("lat"),
                station.get("lon"),
                station.get("capacity"),
                raw,
                record_hash,
                loaded_at,
            )
        )
    return rows


def _insert(con: duckdb.DuckDBPyConnection, table: str, create_sql: str, insert_sql: str, rows: list[tuple]) -> int:
    """Crée la table si besoin, insère les lignes sans doublon, renvoie le nombre de lignes ajoutées."""
    con.execute(create_sql)
    before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 (nom de table fixe)
    if rows:
        con.executemany(insert_sql, rows)
    after = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
    return after - before


def ingest(db_path: Path = DEFAULT_DB) -> dict[str, tuple[int, int]]:
    """Télécharge station_status et station_information et les ajoute au bronze.

    Renvoie, pour chaque flux, (lignes reçues, lignes ajoutées).
    """
    loaded_at = datetime.now(UTC)  # une seule valeur pour tout ce passage

    gbfs = fetch_json(GBFS_URL)
    status = fetch_json(find_feed_url(gbfs, "station_status"))
    information = fetch_json(find_feed_url(gbfs, "station_information"))
    status_rows = to_rows(status["data"]["stations"], loaded_at)
    information_rows = to_information_rows(information["data"]["stations"], loaded_at)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        added_status = _insert(con, "bronze_station_status", CREATE_BRONZE, INSERT_BRONZE, status_rows)
        added_information = _insert(
            con, "bronze_station_information", CREATE_BRONZE_INFORMATION, INSERT_BRONZE_INFORMATION, information_rows
        )

    return {
        "station_status": (len(status_rows), added_status),
        "station_information": (len(information_rows), added_information),
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
