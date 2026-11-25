#!/usr/bin/env python3
"""
Nearest major river from HydroRIVERS, as global water-access context.

HydroLAKES contains lakes and reservoirs but no rivers. A city on the Seine,
Thames, Danube, Spree or North Saskatchewan would otherwise look water-poor
because its nearest lake is a pond. This loader keeps the nearest river reach
with Strahler order >= 4, which removes small streams while retaining rivers
that a resident would recognise.

The original line network is imported, queried, and dropped again. Only the
derived city rows are kept.

Source: HydroRIVERS v1.0, CC BY 4.0 (free for scientific, educational and
commercial use).
https://www.hydrosheds.org/products/hydrorivers

Usage
-----
    python bootstrap_rivers.py
    python bootstrap_rivers.py --report
"""

from __future__ import annotations

import argparse
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import psycopg
import requests
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

CACHE = Path("./data_cache/nature")
ARCHIVE = CACHE / "HydroRIVERS_v10_shp.zip"
URL = "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_shp.zip"
SOURCE = "HydroRIVERS v1.0 (Strahler order >= 4, CC BY 4.0)"
SOURCE_URL = "https://www.hydrosheds.org/products/hydrorivers"
PERIOD = "v1.0"
MIN_ORDER = 4

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS destination_river_access (
    destination_id  TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    river_id        BIGINT,
    river_order     INT,
    discharge_cms   DOUBLE PRECISION,
    distance_km     DOUBLE PRECISION,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def pg_string(database_url: str) -> str:
    info = conninfo_to_dict(database_url)
    parts = []
    for key in ("dbname", "host", "port", "user", "password"):
        value = info.get(key)
        if value:
            parts.append(f"{key}='{str(value).replace(chr(39), chr(92) + chr(39))}'")
    return "PG:" + " ".join(parts)


def ensure_source() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    if not ARCHIVE.exists():
        log(f"downloading {URL}")
        response = requests.get(URL, timeout=3600, stream=True)
        response.raise_for_status()
        with ARCHIVE.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
    folder = CACHE / "hydrorivers"
    if not folder.exists() or not list(folder.rglob("*.shp")):
        folder.mkdir(exist_ok=True)
        with zipfile.ZipFile(ARCHIVE) as archive:
            archive.extractall(folder)
    matches = list(folder.rglob("*.shp"))
    if not matches:
        raise RuntimeError("HydroRIVERS shapefile not found after extraction")
    return matches[0]


def load(database_url: str) -> dict[str, Any]:
    shapefile = ensure_source()
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        log("importing HydroRIVERS reaches with Strahler order >= 4")
        subprocess.run(
            [
                "ogr2ogr", "-f", "PostgreSQL", pg_string(database_url),
                str(shapefile), "-nln", "hydrorivers",
                "-t_srs", "EPSG:4326", "-nlt", "PROMOTE_TO_MULTI",
                "-lco", "GEOMETRY_NAME=geom", "-lco", "FID=fid", "-overwrite",
                "-select", "HYRIV_ID,ORD_STRA,DIS_AV_CMS",
                "-where", f"ORD_STRA >= {MIN_ORDER}",
                "-progress",
            ],
            check=True,
        )
        with conn.cursor() as cur:
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_hydrorivers_geom "
                "ON hydrorivers USING GIST (geom)"
            )
            cur.execute("DELETE FROM destination_river_access")
            cur.execute(
                """
                INSERT INTO destination_river_access (
                    destination_id, river_id, river_order, discharge_cms,
                    distance_km, source, source_url, observed_period
                )
                SELECT d.destination_id,
                       r.hyriv_id::BIGINT,
                       r.ord_stra::INT,
                       r.dis_av_cms::DOUBLE PRECISION,
                       round((ST_Distance(
                           d.centroid::geography, r.geom::geography
                       ) / 1000.0)::numeric, 2)::DOUBLE PRECISION,
                       %s, %s, %s
                FROM destinations d
                CROSS JOIN LATERAL (
                    SELECT * FROM hydrorivers
                    ORDER BY geom <-> d.centroid::geometry
                    LIMIT 1
                ) r
                """,
                (SOURCE, SOURCE_URL, PERIOD),
            )
            cur.execute("SELECT count(*) n FROM destination_river_access")
            count = cur.fetchone()["n"]
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS hydrorivers")
        conn.commit()
    return {"cities": count}


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM destination_river_access")
            print({"river_access": cur.fetchone()["n"]})
            cur.execute(
                """
                SELECT d.name, r.river_order, round(r.discharge_cms::numeric, 1)
                       AS discharge_cms, round(r.distance_km::numeric, 2) AS distance_km
                FROM destination_river_access r
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Berlin', 'Ottawa', 'Vienna', 'Edmonton',
                                 'Paris', 'London', 'Montréal')
                ORDER BY d.name
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
