#!/usr/bin/env python3
"""
Public-transport access from Overture Maps Places.

Counts bus, train, metro, light-rail, tram and public-transit facilities
within 5 km of each city and writes them to mobility_metrics.transit_stops_5km.

Source: Overture Maps Places, release 2026-11-18.0
Categories verified against the live dataset on 2026-11-21:
  train_station (107,910), bus_station (73,526),
  public_transit_facility_or_service (31,406), metro_station (2,730),
  light_rail_and_subway_station (1,410) globally.

This is a supply/access measure, not a service-frequency or reliability
measure. Overture POI coverage varies by city; missing data stays NULL.

Usage:
    python bootstrap_transit_stops.py --countries SE,TR
    python bootstrap_transit_stops.py
    python bootstrap_transit_stops.py --query-only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg

from bootstrap_phase3 import (
    _cities_for_scope,
    _duckdb_connect,
    _execute_with_retries,
    _register_city_tables,
    _scan_envelopes,
    _upsert_dataframe,
    connect_db,
    log,
)

CACHE_DIR = Path("./data_cache")
DEFAULT_OVERTURE_RELEASE = "2026-11-18.0"
OVERTURE_S3_ROOT = "s3://overturemaps-us-west-2/release"
TRANSIT_SOURCE = "Overture Maps Places (transit stops)"

# Only specific public-transport facility categories. Generic
# `travel_and_transportation` is excluded because it includes travel agencies,
# parking and unrelated businesses.
TRANSIT_CATEGORIES = (
    "bus_station",
    "train_station",
    "metro_station",
    "public_transit_facility_or_service",
    "light_rail_and_subway_station",
    "ferry_terminal",
    "tram_stop",
    "bus_stop",
    "railway_station",
    "subway_station",
)


def ensure_columns(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.mobility_metrics') AS relation")
        if cur.fetchone()[0] is None:
            raise SystemExit(
                "mobility_metrics does not exist. Run bootstrap_phase3.py "
                "first so the Phase 3 schema is installed."
            )
        cur.execute(
            """
            ALTER TABLE mobility_metrics
                ADD COLUMN IF NOT EXISTS transit_stops_5km INT,
                ADD COLUMN IF NOT EXISTS transit_stops_source TEXT,
                ADD COLUMN IF NOT EXISTS transit_stops_period TEXT
            """
        )
    conn.commit()


def ingest_transit_stops(
    conn: psycopg.Connection,
    cities_df: pd.DataFrame,
    release: str,
    radius_km: float = 5.0,
) -> None:
    log(f"Public transport stops within {radius_km:.0f} km (Overture Places {release}) ...")
    s3 = f"{OVERTURE_S3_ROOT}/{release}/theme=places/type=place/*"
    duck = _duckdb_connect()
    try:
        _register_city_tables(duck, cities_df)
        envelopes = _scan_envelopes(cities_df, pad=0.15, radius_km=radius_km)
        category_list = ", ".join(f"'{c}'" for c in TRANSIT_CATEGORIES)
        scans = []
        for min_lon, max_lon, min_lat, max_lat in envelopes:
            scans.append(f"""
                SELECT
                    id,
                    (bbox.ymin + bbox.ymax) / 2.0 AS lat,
                    (bbox.xmin + bbox.xmax) / 2.0 AS lon,
                    ((CAST(FLOOR(((bbox.xmin + bbox.xmax) / 2.0) * 10) AS INT)
                        % 3600) + 3600) % 3600 AS gx,
                    CAST(FLOOR(((bbox.ymin + bbox.ymax) / 2.0) * 10) AS INT) AS gy
                FROM read_parquet('{s3}', hive_partitioning=1)
                WHERE bbox.xmin <= {max_lon!r}
                  AND bbox.xmax >= {min_lon!r}
                  AND bbox.ymin <= {max_lat!r}
                  AND bbox.ymax >= {min_lat!r}
                  AND taxonomy.primary IN ({category_list})
            """)
        places_sql = "\nUNION ALL\n".join(scans)
        sql = f"""
        WITH places AS (
            {places_sql}
        ),
        joined AS (
            SELECT DISTINCT
                c.destination_id,
                p.id
            FROM places p
            JOIN city_cells cc ON cc.gx = p.gx AND cc.gy = p.gy
            JOIN cities c ON c.destination_id = cc.destination_id
            WHERE haversine_km(c.lat, c.lon, p.lat, p.lon) <= {radius_km!r}
        )
        SELECT destination_id, COUNT(*) AS transit_stops_5km
        FROM joined
        GROUP BY destination_id
        """
        df = _execute_with_retries(duck, sql)
    finally:
        duck.close()

    df["transit_stops_source"] = TRANSIT_SOURCE
    df["transit_stops_period"] = f"overture-{release}"
    # source/observed_period are NOT NULL on mobility_metrics. For a
    # transit-only row they describe the only observation; for an existing
    # GTFS/walk row the ON CONFLICT update leaves the original provenance.
    df["source"] = TRANSIT_SOURCE
    df["observed_period"] = f"overture-{release}"
    scope_ids = cities_df["destination_id"].tolist()
    with conn.cursor() as cur:
        # Transit stops are authoritative for this scope; preserve GTFS,
        # walkability and rail columns owned by other loaders.
        cur.execute(
            """
            UPDATE mobility_metrics
            SET transit_stops_5km = NULL,
                transit_stops_source = NULL,
                transit_stops_period = NULL
            WHERE destination_id = ANY(%s)
            """,
            (scope_ids,),
        )
        cur.execute(
            """
            DELETE FROM mobility_metrics
            WHERE destination_id = ANY(%s)
              AND pedestrian_segments_2km IS NULL
              AND total_road_segments_2km IS NULL
              AND pedestrian_ratio_2km IS NULL
              AND rail_segments_5km IS NULL
              AND gtfs_active_feeds IS NULL
              AND transit_stops_5km IS NULL
            """,
            (scope_ids,),
        )
    if df.empty:
        conn.commit()
        n = 0
    else:
        n = _upsert_dataframe(
            conn, df, "mobility_metrics",
            ["destination_id", "transit_stops_5km", "transit_stops_source",
             "transit_stops_period", "source", "observed_period"],
            ["destination_id"],
            """
            transit_stops_5km = EXCLUDED.transit_stops_5km,
            transit_stops_source = EXCLUDED.transit_stops_source,
            transit_stops_period = EXCLUDED.transit_stops_period,
            updated_at = NOW()
            """,
        )
    log(f"  upserted transit-stop counts for {n:,} cities.")


def print_coverage(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM destinations")
        cities = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM mobility_metrics WHERE transit_stops_5km IS NOT NULL"
        )
        covered = cur.fetchone()[0]
        cur.execute(
            """
            SELECT d.name, d.country_iso2, m.transit_stops_5km
            FROM mobility_metrics m JOIN destinations d USING (destination_id)
            WHERE m.transit_stops_5km IS NOT NULL
            ORDER BY m.transit_stops_5km DESC, d.population DESC
            LIMIT 10
            """
        )
        top = cur.fetchall()
    log(f"  destinations: {cities:,}; with transit stops: {covered:,}")
    for name, country, count in top:
        log(f"    {name}, {country}: {count:,}")


def main() -> None:
    global CACHE_DIR
    parser = argparse.ArgumentParser(description="Overture public transport stop access")
    parser.add_argument("--min-pop", type=int, default=40000)
    parser.add_argument("--countries", default=None)
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--overture-release", default=DEFAULT_OVERTURE_RELEASE)
    parser.add_argument("--query-only", action="store_true")
    args = parser.parse_args()

    CACHE_DIR = args.cache_dir
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db_url = args.db_url or os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        log("Error: set SUPABASE_DB_URL (or pass --db-url).")
        sys.exit(1)
    countries = (
        [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if args.countries else None
    )

    with connect_db(db_url) as conn:
        ensure_columns(conn)
        if args.query_only:
            print_coverage(conn)
            return
        cities_df = _cities_for_scope(conn, args.min_pop, countries)
        if cities_df.empty:
            raise SystemExit("No destinations in scope.")
        log(f"  {len(cities_df):,} destinations in scope.")
        ingest_transit_stops(conn, cities_df, args.overture_release)
        print_coverage(conn)


if __name__ == "__main__":
    main()
