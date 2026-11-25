#!/usr/bin/env python3
"""
City elevation and straight-line coastline distance.

* **Elevation** comes from the GeoNames ``cities15000`` record already used to
  build ``destinations``: the ``dem`` field (digital elevation model), with the
  older ``elev`` field as a fallback. It is the city record's elevation, not a
  neighbourhood gradient.
* **Coast distance** is the straight-line distance from the city centroid to
  the nearest Natural Earth 10m coastline. It is not a road distance and it
  does not say whether the coast is a beach, a cliff or an industrial port.
  Natural Earth is public domain.

The values are exposed in a ``geography`` block. Daylight hours are computed
from latitude in ``place_profiles`` and need no table.

Requires GDAL's ``ogr2ogr`` on PATH to convert the Natural Earth shapefile.

Usage
-----
    python bootstrap_geography.py
    python bootstrap_geography.py --report
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import psycopg
import requests
from psycopg.rows import dict_row

CACHE = Path("./data_cache/geography")
CITIES_ZIP = Path("./data_cache/cities15000.zip")
COAST_URL = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_coastline.zip"
COAST_ZIP = CACHE / "ne_10m_coastline.zip"
COAST_SHP = CACHE / "ne_10m_coastline.shp"
COAST_GEOJSON = CACHE / "ne_10m_coastline.geojson"
ELEVATION_SOURCE = "GeoNames cities15000 (dem, fallback elev)"
COAST_SOURCE = "Natural Earth 10m coastline (public domain)"
ELEVATION_PERIOD = "GeoNames 2026"
COAST_PERIOD = "Natural Earth 10m"

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS destination_geography (
    destination_id     TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    elevation_m        DOUBLE PRECISION,
    coast_distance_km  DOUBLE PRECISION,
    elevation_source   TEXT,
    elevation_period   TEXT,
    coast_source       TEXT,
    coast_period       TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS natural_earth_coastline (
    feature_id  INT PRIMARY KEY,
    geom        geometry(Geometry, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_natural_earth_coastline_geom
    ON natural_earth_coastline USING GIST (geom);
"""


def log(message: str) -> None:
    print(message, flush=True)


def read_elevations() -> dict[int, float]:
    if not CITIES_ZIP.exists():
        raise SystemExit(f"{CITIES_ZIP} is missing; run bootstrap_global_40k.py once.")
    out: dict[int, float] = {}
    with zipfile.ZipFile(CITIES_ZIP) as archive:
        with archive.open("cities15000.txt") as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8")
            reader = csv.reader(text, delimiter="\t")
            for row in reader:
                if len(row) < 16:
                    continue
                try:
                    geoname_id = int(row[0])
                except ValueError:
                    continue
                for value in (row[16], row[15]):  # dem, then elev
                    try:
                        elevation = float(value)
                    except (TypeError, ValueError):
                        continue
                    if elevation > -1000:
                        out[geoname_id] = elevation
                        break
    return out


def download_coastline() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    if COAST_GEOJSON.exists():
        return
    if not COAST_ZIP.exists():
        log(f"downloading {COAST_URL}")
        response = requests.get(COAST_URL, timeout=180)
        response.raise_for_status()
        COAST_ZIP.write_bytes(response.content)
    with zipfile.ZipFile(COAST_ZIP) as archive:
        archive.extractall(CACHE)
    subprocess.run(
        [
            "ogr2ogr", "-f", "GeoJSON", str(COAST_GEOJSON), str(COAST_SHP),
            "-lco", "RFC7946=YES",
        ],
        check=True,
        capture_output=True,
    )


def load_coastline(conn: psycopg.Connection) -> int:
    download_coastline()
    payload = json.loads(COAST_GEOJSON.read_text(encoding="utf-8"))
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute("TRUNCATE natural_earth_coastline")
        for index, feature in enumerate(payload.get("features", [])):
            geometry = feature.get("geometry")
            if not geometry:
                continue
            cur.execute(
                "INSERT INTO natural_earth_coastline (feature_id, geom) "
                "VALUES (%s, ST_GeomFromGeoJSON(%s))",
                (index, json.dumps(geometry)),
            )
    conn.commit()
    return len(payload.get("features", []))


def load(database_url: str) -> dict[str, Any]:
    elevations = read_elevations()
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        count = load_coastline(conn)
        log(f"loaded {count:,} coastline features")
        with conn.cursor() as cur:
            cur.execute("SELECT destination_id, geoname_id FROM destinations")
            destinations = cur.fetchall()
            cur.execute("DELETE FROM destination_geography")
            rows = []
            for destination in destinations:
                elevation = elevations.get(int(destination["geoname_id"]))
                cur.execute(
                    """
                    SELECT round((ST_Distance(
                               d.centroid::geography, c.geom::geography) / 1000.0
                           )::numeric, 2)::double precision AS km
                    FROM destinations d
                    CROSS JOIN LATERAL (
                        SELECT geom FROM natural_earth_coastline
                        ORDER BY geom <-> d.centroid
                        LIMIT 1
                    ) c
                    WHERE d.destination_id = %s
                    """,
                    (destination["destination_id"],),
                )
                coast = cur.fetchone()["km"]
                rows.append((
                    destination["destination_id"], elevation, coast,
                    ELEVATION_SOURCE, ELEVATION_PERIOD, COAST_SOURCE, COAST_PERIOD,
                ))
            cur.executemany(
                """
                INSERT INTO destination_geography (
                    destination_id, elevation_m, coast_distance_km,
                    elevation_source, elevation_period, coast_source, coast_period
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
        conn.commit()
    return {
        "destinations": len(rows),
        "with_elevation": sum(1 for row in rows if row[1] is not None),
        "with_coast": sum(1 for row in rows if row[2] is not None),
    }


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) rows,
                       count(elevation_m) with_elevation,
                       count(coast_distance_km) with_coast,
                       round(max(elevation_m)::numeric, 0) max_elevation_m,
                       round(max(coast_distance_km)::numeric, 0) max_coast_km
                FROM destination_geography
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT d.name, g.elevation_m, g.coast_distance_km
                FROM destination_geography g JOIN destinations d USING (destination_id)
                WHERE d.name IN ('La Paz','Mexico City','Lisbon','Trento','Canberra')
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
