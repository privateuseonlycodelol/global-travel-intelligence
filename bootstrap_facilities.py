#!/usr/bin/env python3
"""
Daily-life facility counts within 5 km, from Overture Places.

The existing POI loader counts restaurants and cultural venues. This one adds
the presence questions a family or long-stay resident asks:

* hospitals, pharmacies, schools, grocery stores, parks and beaches.

It is the same Overture release, the same 5 km radius and the same 0.1 degree
grid join as ``bootstrap_global_40k.py``. A count is **presence**, not quality,
and a zero means ``none_listed`` in the source, not proof that no hospital or
school exists. Sparse-map cities will look emptier than they are.

Source: Overture Maps Places, CDLA-Permissive 2.0.
https://docs.overturemaps.org/

Usage
-----
    python bootstrap_facilities.py --countries PT
    python bootstrap_facilities.py
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import duckdb
import pandas as pd
import psycopg
from psycopg.rows import dict_row

from bootstrap_global_40k import (
    DEFAULT_OVERTURE_RELEASE,
    OVERTURE_S3_ROOT,
    _duckdb_temp_dir,
    _execute_with_retries,
    _scan_envelopes,
    log,
)

SOURCE = "Overture Maps Places (facility counts)"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facility_metrics (
    destination_id      TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    hospital_count_5km  INT NOT NULL,
    pharmacy_count_5km  INT NOT NULL,
    school_count_5km    INT NOT NULL,
    grocery_count_5km   INT NOT NULL,
    park_count_5km      INT NOT NULL,
    beach_count_5km     INT NOT NULL,
    source              TEXT NOT NULL,
    observed_period     TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

FLAGS = {
    "is_hospital": "list_contains(taxonomy.hierarchy, 'hospital')",
    "is_pharmacy": (
        "list_contains(taxonomy.hierarchy, 'pharmacy') "
        "OR list_contains(taxonomy.hierarchy, 'pharmacy_and_drug_store')"
    ),
    "is_school": "list_contains(taxonomy.hierarchy, 'school')",
    "is_grocery": (
        "list_contains(taxonomy.hierarchy, 'grocery_store') "
        "OR list_contains(taxonomy.hierarchy, 'supermarket')"
    ),
    "is_park": "list_contains(taxonomy.hierarchy, 'park')",
    "is_beach": "list_contains(taxonomy.hierarchy, 'beach')",
}


def _scan_sql(places_s3: str, envelopes: list[tuple[float, float, float, float]]) -> str:
    selects = []
    for min_lon, max_lon, min_lat, max_lat in envelopes:
        flags = ",\n                ".join(
            f"COALESCE({expression}, FALSE) AS {name}"
            for name, expression in FLAGS.items()
        )
        selects.append(f"""
            SELECT
                id,
                (bbox.ymin + bbox.ymax) / 2.0 AS plat,
                (bbox.xmin + bbox.xmax) / 2.0 AS plon,
                CAST(FLOOR(((bbox.ymin + bbox.ymax) / 2.0) * 10) AS INT) AS gy,
                ((CAST(FLOOR(((bbox.xmin + bbox.xmax) / 2.0) * 10) AS INT)
                    % 3600) + 3600) % 3600 AS gx,
                {flags}
            FROM read_parquet('{places_s3}')
            WHERE bbox.xmin BETWEEN {min_lon!r} AND {max_lon!r}
              AND bbox.ymin BETWEEN {min_lat!r} AND {max_lat!r}
        """)
    return "\nUNION ALL\n".join(selects)


def scan(
    cities: pd.DataFrame, release: str
) -> pd.DataFrame:
    places_s3 = f"{OVERTURE_S3_ROOT}/{release}/theme=places/type=place/*"
    envelopes = _scan_envelopes(cities)
    places_sql = _scan_sql(places_s3, envelopes)
    distinct = "DISTINCT " if len(envelopes) > 1 else ""
    duck = duckdb.connect()
    try:
        duck.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
        duck.execute(
            f"SET memory_limit='{os.environ.get('DUCKDB_MEMORY_LIMIT', '6GB')}'; "
            f"SET temp_directory='{_duckdb_temp_dir().as_posix()}'; "
            "SET preserve_insertion_order=false;"
        )
        try:
            duck.execute("SET threads=8; SET http_client_pool_capacity=16;")
        except duckdb.Error as exc:
            log(f"  (optional DuckDB HTTP tuning unavailable: {exc})")
        duck.register("cities_df", cities[["destination_id", "lat", "lon"]])
        return _execute_with_retries(
            duck,
            f"""
            WITH city_cells AS (
                SELECT
                    c.destination_id,
                    c.lat AS clat,
                    c.lon AS clon,
                    (CAST(FLOOR(c.lat * 10) AS INT) + dy.d) AS gy,
                    ((CAST(FLOOR(c.lon * 10) AS INT) + dx.d) % 3600 + 3600) % 3600 AS gx
                FROM cities_df c
                CROSS JOIN (VALUES (-1), (0), (1)) AS dy(d)
                CROSS JOIN (VALUES (-2), (-1), (0), (1), (2)) AS dx(d)
            ),
            places AS (
                {places_sql}
            ),
            matched AS (
                SELECT {distinct}
                    cc.destination_id,
                    p.id,
                    p.is_hospital, p.is_pharmacy, p.is_school,
                    p.is_grocery, p.is_park, p.is_beach
                FROM places p
                JOIN city_cells cc ON cc.gy = p.gy AND cc.gx = p.gx
                WHERE 6371.0 * 2.0 * ASIN(SQRT(
                    POWER(SIN(RADIANS(p.plat - cc.clat) / 2.0), 2)
                    + COS(RADIANS(cc.clat)) * COS(RADIANS(p.plat))
                    * POWER(SIN(RADIANS(p.plon - cc.clon) / 2.0), 2)
                )) <= 5.0
            ),
            agg AS (
                SELECT destination_id,
                    COUNT(*) FILTER (WHERE is_hospital) AS hospital_count,
                    COUNT(*) FILTER (WHERE is_pharmacy) AS pharmacy_count,
                    COUNT(*) FILTER (WHERE is_school) AS school_count,
                    COUNT(*) FILTER (WHERE is_grocery) AS grocery_count,
                    COUNT(*) FILTER (WHERE is_park) AS park_count,
                    COUNT(*) FILTER (WHERE is_beach) AS beach_count
                FROM matched
                GROUP BY destination_id
            )
            SELECT c.destination_id,
                   COALESCE(a.hospital_count, 0) AS hospital_count,
                   COALESCE(a.pharmacy_count, 0) AS pharmacy_count,
                   COALESCE(a.school_count, 0) AS school_count,
                   COALESCE(a.grocery_count, 0) AS grocery_count,
                   COALESCE(a.park_count, 0) AS park_count,
                   COALESCE(a.beach_count, 0) AS beach_count
            FROM cities_df c
            LEFT JOIN agg a ON a.destination_id = c.destination_id
            """,
        )
    finally:
        duck.close()


def load(database_url: str, release: str, countries: list[str] | None) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            query = "SELECT destination_id, country_iso2, lat, lon FROM destinations"
            params: list[Any] = []
            if countries:
                query += " WHERE country_iso2 = ANY(%s)"
                params.append(countries)
            cur.execute(query, params)
            cities = pd.DataFrame([dict(row) for row in cur.fetchall()])
        log(f"scanning Overture Places for {len(cities):,} cities ({release})")
        frame = scan(cities, release)
        if len(frame) != len(cities):
            raise RuntimeError(
                f"facility join returned {len(frame):,} rows for {len(cities):,} cities"
            )
        rows = [
            (
                str(row.destination_id), int(row.hospital_count), int(row.pharmacy_count),
                int(row.school_count), int(row.grocery_count), int(row.park_count),
                int(row.beach_count), SOURCE, release,
            )
            for row in frame.itertuples(index=False)
        ]
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO facility_metrics (
                    destination_id, hospital_count_5km, pharmacy_count_5km,
                    school_count_5km, grocery_count_5km, park_count_5km,
                    beach_count_5km, source, observed_period
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (destination_id) DO UPDATE SET
                    hospital_count_5km = EXCLUDED.hospital_count_5km,
                    pharmacy_count_5km = EXCLUDED.pharmacy_count_5km,
                    school_count_5km = EXCLUDED.school_count_5km,
                    grocery_count_5km = EXCLUDED.grocery_count_5km,
                    park_count_5km = EXCLUDED.park_count_5km,
                    beach_count_5km = EXCLUDED.beach_count_5km,
                    source = EXCLUDED.source,
                    observed_period = EXCLUDED.observed_period,
                    updated_at = NOW()
                """,
                rows,
            )
        conn.commit()
    return {"cities": len(rows), "release": release}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--overture-release", default=DEFAULT_OVERTURE_RELEASE)
    parser.add_argument("--countries", default=None)
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    countries = (
        [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if args.countries else None
    )
    print(load(args.db_url, args.overture_release, countries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
