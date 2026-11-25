#!/usr/bin/env python3
"""
Commercial-airport access from the cached OurAirports CSV.

``destinations.iata_codes`` already lists nearby codes, but it does not say how
far the nearest one is or whether it is a large hub or a small regional field.
This table adds:

* the nearest airport with scheduled service, its IATA code, type and
  straight-line distance;
* how many large and medium airports with scheduled service lie within 100 km.

Distance is straight-line, not drive time. OurAirports is public domain.

Usage
-----
    python bootstrap_airport_access.py
    python bootstrap_airport_access.py --report
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

AIRPORTS_CSV = Path("./data_cache/airports.csv")
SOURCE = "OurAirports (scheduled-service airports)"
PERIOD = "OurAirports 2026"
SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS airport_access (
    destination_id             TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    nearest_iata               TEXT,
    nearest_name               TEXT,
    nearest_type               TEXT,
    nearest_distance_km        DOUBLE PRECISION,
    large_airports_100km       INT NOT NULL,
    medium_airports_100km      INT NOT NULL,
    source                     TEXT NOT NULL,
    observed_period            TEXT NOT NULL,
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def read_airports() -> list[dict[str, Any]]:
    rows = []
    with AIRPORTS_CSV.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("scheduled_service") != "yes":
                continue
            if row.get("type") not in ("large_airport", "medium_airport", "small_airport"):
                continue
            try:
                lat = float(row["latitude_deg"])
                lon = float(row["longitude_deg"])
            except (TypeError, ValueError):
                continue
            rows.append({
                "ident": row.get("ident"),
                "iata": (row.get("iata_code") or "").strip() or None,
                "name": row.get("name"),
                "type": row.get("type"),
                "lat": lat,
                "lon": lon,
            })
    return rows


def load(database_url: str) -> dict[str, Any]:
    airports = read_airports()
    log(f"read {len(airports):,} scheduled-service airports")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                """
                CREATE TEMP TABLE tmp_airports (
                    ident TEXT, iata TEXT, name TEXT, type TEXT,
                    lat DOUBLE PRECISION, lon DOUBLE PRECISION,
                    geom geometry(Point, 4326)
                ) ON COMMIT DROP
                """
            )
            cur.executemany(
                """
                INSERT INTO tmp_airports (ident, iata, name, type, lat, lon, geom)
                VALUES (%s, %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                """,
                [
                    (
                        row["ident"], row["iata"], row["name"], row["type"],
                        row["lat"], row["lon"], row["lon"], row["lat"],
                    )
                    for row in airports
                ],
            )
            cur.execute("CREATE INDEX ON tmp_airports USING GIST (geom)")
            cur.execute("DELETE FROM airport_access")
            cur.execute(
                """
                INSERT INTO airport_access (
                    destination_id, nearest_iata, nearest_name, nearest_type,
                    nearest_distance_km, large_airports_100km,
                    medium_airports_100km, source, observed_period
                )
                SELECT d.destination_id,
                       a.iata,
                       a.name,
                       a.type,
                       round((ST_Distance(
                           d.centroid::geography, a.geom::geography) / 1000.0
                       )::numeric, 1)::double precision,
                       (
                           SELECT count(*) FROM tmp_airports l
                           WHERE l.type = 'large_airport'
                             AND ST_DWithin(
                                 d.centroid::geography, l.geom::geography, 100000
                             )
                       ),
                       (
                           SELECT count(*) FROM tmp_airports m
                           WHERE m.type = 'medium_airport'
                             AND ST_DWithin(
                                 d.centroid::geography, m.geom::geography, 100000
                             )
                       ),
                       %s, %s
                FROM destinations d
                CROSS JOIN LATERAL (
                    SELECT * FROM tmp_airports
                    ORDER BY geom <-> d.centroid
                    LIMIT 1
                ) a
                """,
                (SOURCE, PERIOD),
            )
            cur.execute(
                "SELECT count(*) AS rows, count(nearest_iata) AS with_iata "
                "FROM airport_access"
            )
            stats = dict(cur.fetchone())
        conn.commit()
    stats["airports"] = len(airports)
    return stats


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) rows,
                       round(avg(nearest_distance_km)::numeric, 1) avg_km,
                       round(max(nearest_distance_km)::numeric, 1) max_km
                FROM airport_access
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT d.name, a.nearest_iata, a.nearest_type,
                       a.nearest_distance_km, a.large_airports_100km
                FROM airport_access a JOIN destinations d USING (destination_id)
                WHERE d.name IN ('London','Lyon','Trento','Canberra','La Paz')
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
