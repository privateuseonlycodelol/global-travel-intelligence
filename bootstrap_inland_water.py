#!/usr/bin/env python3
"""
Nearest lake or reservoir from HydroLAKES, as global inland-water context.

HydroLAKES covers lakes and reservoirs of at least 10 ha. For each city this
stores the nearest lake shore distance, the lake name/area/type, and separately
the nearest lake of at least 10 km2. The second is needed because a city's
nearest water body is often a pond, while the lake that matters for the water
score may be farther away.

Small tarns, rivers and temporary pans are not in this layer. Rivers are loaded
by ``bootstrap_rivers.py``; seasonal wetlands remain a later GLWD step.

The original polygon file is imported, queried, and dropped again. Only the
derived city rows are kept, matching HydroLAKES' request not to redistribute the
full polygon set.

Source: HydroLAKES v1.0, CC BY 4.0.
https://www.hydrosheds.org/products/hydrolakes

Usage
-----
    python bootstrap_inland_water.py
    python bootstrap_inland_water.py --report
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
ARCHIVE = CACHE / "HydroLAKES_polys_v10_shp.zip"
URL = "https://data.hydrosheds.org/file/hydrolakes/HydroLAKES_polys_v10_shp.zip"
SOURCE = "HydroLAKES v1.0 (lakes >= 10 ha, CC BY 4.0)"
SOURCE_URL = "https://www.hydrosheds.org/products/hydrolakes"
PERIOD = "v1.0"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS destination_inland_water (
    destination_id  TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    lake_id         BIGINT,
    lake_name       TEXT,
    lake_type       INT,
    lake_area_km2    DOUBLE PRECISION,
    distance_km     DOUBLE PRECISION,
    large_lake_id       BIGINT,
    large_lake_name     TEXT,
    large_lake_type     INT,
    large_lake_area_km2 DOUBLE PRECISION,
    large_lake_distance_km DOUBLE PRECISION,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE destination_inland_water
    ADD COLUMN IF NOT EXISTS large_lake_id BIGINT,
    ADD COLUMN IF NOT EXISTS large_lake_name TEXT,
    ADD COLUMN IF NOT EXISTS large_lake_type INT,
    ADD COLUMN IF NOT EXISTS large_lake_area_km2 DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS large_lake_distance_km DOUBLE PRECISION;
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
        response = requests.get(URL, timeout=1800)
        response.raise_for_status()
        ARCHIVE.write_bytes(response.content)
    folder = CACHE / "hydrolakes"
    if not folder.exists() or not list(folder.rglob("*.shp")):
        folder.mkdir(exist_ok=True)
        with zipfile.ZipFile(ARCHIVE) as archive:
            archive.extractall(folder)
    matches = list(folder.rglob("*.shp"))
    if not matches:
        raise RuntimeError("HydroLAKES shapefile not found after extraction")
    return matches[0]


def load(database_url: str) -> dict[str, Any]:
    shapefile = ensure_source()
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        log("importing HydroLAKES polygons")
        subprocess.run(
            [
                "ogr2ogr", "-f", "PostgreSQL", pg_string(database_url),
                str(shapefile), "-nln", "hydrolakes",
                "-t_srs", "EPSG:4326", "-nlt", "PROMOTE_TO_MULTI",
                "-lco", "GEOMETRY_NAME=geom", "-lco", "FID=fid", "-overwrite",
                "-select", "Hylak_id,Lake_name,Lake_type,Lake_area",
            ],
            check=True,
            capture_output=True,
        )
        with conn.cursor() as cur:
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_hydrolakes_geom ON hydrolakes USING GIST (geom)"
            )
            # A city's nearest water body is often a pond. The score needs the
            # nearest lake that is large enough to count, which may be further
            # away, so query both and keep them separate.
            cur.execute(
                """
                DROP TABLE IF EXISTS hydrolakes_large;
                CREATE TABLE hydrolakes_large AS
                SELECT * FROM hydrolakes WHERE lake_area >= 10.0;
                CREATE INDEX idx_hydrolakes_large_geom
                    ON hydrolakes_large USING GIST (geom);
                """
            )
            cur.execute("DELETE FROM destination_inland_water")
            cur.execute(
                """
                INSERT INTO destination_inland_water (
                    destination_id, lake_id, lake_name, lake_type, lake_area_km2,
                    distance_km,
                    large_lake_id, large_lake_name, large_lake_type,
                    large_lake_area_km2, large_lake_distance_km,
                    source, source_url, observed_period
                )
                SELECT d.destination_id,
                       l.hylak_id::BIGINT,
                       NULLIF(trim(l.lake_name), ''),
                       l.lake_type::INT,
                       l.lake_area::DOUBLE PRECISION,
                       round((ST_Distance(
                           d.centroid::geography, l.geom::geography
                       ) / 1000.0)::numeric, 2)::DOUBLE PRECISION,
                       ll.hylak_id::BIGINT,
                       NULLIF(trim(ll.lake_name), ''),
                       ll.lake_type::INT,
                       ll.lake_area::DOUBLE PRECISION,
                       round((ST_Distance(
                           d.centroid::geography, ll.geom::geography
                       ) / 1000.0)::numeric, 2)::DOUBLE PRECISION,
                       %s, %s, %s
                FROM destinations d
                CROSS JOIN LATERAL (
                    SELECT * FROM hydrolakes
                    ORDER BY geom <-> d.centroid::geometry
                    LIMIT 1
                ) l
                LEFT JOIN LATERAL (
                    SELECT * FROM hydrolakes_large
                    ORDER BY geom <-> d.centroid::geometry
                    LIMIT 1
                ) ll ON TRUE
                """,
                (SOURCE, SOURCE_URL, PERIOD),
            )
            cur.execute("SELECT count(*) n FROM destination_inland_water")
            count = cur.fetchone()["n"]
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS hydrolakes_large")
            cur.execute("DROP TABLE IF EXISTS hydrolakes")
        conn.commit()
    return {"cities": count}


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM destination_inland_water")
            print({"inland_water": cur.fetchone()["n"]})
            cur.execute(
                """
                SELECT d.name, w.lake_name, w.lake_type, w.lake_area_km2,
                       w.distance_km
                FROM destination_inland_water w
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Trento', 'Paris', 'Annecy', 'Geneva', 'Como')
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
