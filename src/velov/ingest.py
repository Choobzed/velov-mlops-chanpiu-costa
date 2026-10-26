"""Ingestion bronze : copie le flux GBFS Vélo'v dans DuckDB, sans doublon.

TP3, Must 1. Mode IA déclarée.

Le flux GBFS ne donne que l'état actuel des stations. Lancé régulièrement
(toutes les 5 minutes), ce script construit l'historique dont le modèle a besoin.
Il est idempotent : le relancer sur les mêmes données n'ajoute aucune ligne.

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


def ingest(db_path: Path = DEFAULT_DB) -> tuple[int, int]:
    """Télécharge station_status et l'ajoute au bronze. Renvoie (lignes reçues, lignes ajoutées)."""
    loaded_at = datetime.now(UTC)  # une seule valeur pour tout ce passage

    gbfs = fetch_json(GBFS_URL)
    status = fetch_json(find_feed_url(gbfs, "station_status"))
    rows = to_rows(status["data"]["stations"], loaded_at)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute(CREATE_BRONZE)
        before = con.execute("SELECT count(*) FROM bronze_station_status").fetchone()[0]
        con.executemany(INSERT_BRONZE, rows)
        after = con.execute("SELECT count(*) FROM bronze_station_status").fetchone()[0]

    return len(rows), after - before


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion bronze du flux GBFS Vélo'v dans DuckDB.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="fichier DuckDB")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    received, added = ingest(args.db)
    logger.info("%d stations reçues, %d nouvelles observations ajoutées dans %s", received, added, args.db)


if __name__ == "__main__":
    main()
