#!/usr/bin/env python3
"""
Optional full green score: vegetation, parks, air quality and water access.

This is a first-party composite, not an observation. It is deliberately
separate from the vegetation-only score so the components stay visible.

Weights
-------
* 40% vegetation cover — 1 km and 15 km tree/shrub/grass (ESA WorldCover)
* 25% park access — mapped Overture parks per 100,000 residents, percentile
* 25% air quality — annual PM2.5 percentile, lower is better (SatPM)
* 10% water access — straight-line distance to the nearer of the coast and a
  HydroLAKES lake/reservoir, linear decay to zero at 50 km

The score exists only when all four components exist. A zero park count is
``none_listed``, not evidence of no parks, so those cities stay unmeasured.
No value is imputed.

Usage
-----
    python bootstrap_green_score.py
    python bootstrap_green_score.py --report
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCHEMA_SQL = """
CREATE OR REPLACE VIEW destination_green_score_full AS
WITH base AS (
    SELECT d.destination_id,
           d.population,
           g.green_score_0_100 AS vegetation_score,
           f.park_count_5km,
           CASE WHEN d.population > 0 AND f.park_count_5km > 0
                THEN f.park_count_5km::DOUBLE PRECISION
                     / d.population::DOUBLE PRECISION * 100000.0
           END AS parks_per_100k,
           a.pm25_ugm3,
           LEAST(
               ge.coast_distance_km,
               w.distance_km
           ) AS water_distance_km
    FROM destinations d
    JOIN destination_green_score g USING (destination_id)
    LEFT JOIN facility_metrics f USING (destination_id)
    LEFT JOIN air_quality_annual a USING (destination_id)
    LEFT JOIN destination_geography ge USING (destination_id)
    LEFT JOIN destination_inland_water w USING (destination_id)
),
scored AS (
    SELECT *,
           CASE WHEN parks_per_100k IS NOT NULL
                THEN 100.0 * PERCENT_RANK() OVER (ORDER BY parks_per_100k)
           END AS park_score,
           CASE WHEN pm25_ugm3 IS NOT NULL
                THEN 100.0 * (1.0 - PERCENT_RANK() OVER (ORDER BY pm25_ugm3 DESC))
           END AS air_score,
           CASE WHEN water_distance_km IS NOT NULL
                THEN 100.0 * GREATEST(0.0, 1.0 - water_distance_km / 50.0)
           END AS water_score
    FROM base
)
SELECT destination_id,
       vegetation_score,
       park_score,
       air_score,
       water_score,
       parks_per_100k,
       pm25_ugm3,
       water_distance_km,
       CASE WHEN vegetation_score IS NOT NULL
                  AND park_score IS NOT NULL
                  AND air_score IS NOT NULL
                  AND water_score IS NOT NULL
            THEN (
                0.40 * vegetation_score
              + 0.25 * park_score
              + 0.25 * air_score
              + 0.10 * water_score
            )
       END AS green_score_full_0_100,
       'v1'::text AS score_version,
       '40% vegetation, 25% parks, 25% air, 10% water'::text AS weights,
       'First-party composite from ESA WorldCover, Overture Places, SatPM and HydroLAKES/Natural Earth'::text AS source,
       '2021-2024'::text AS observed_period,
       NOW() AS computed_at
FROM scored;
"""


def log(message: str) -> None:
    print(message, flush=True)


def load(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                """
                SELECT count(*) AS rows,
                       count(green_score_full_0_100) AS scored
                FROM destination_green_score_full
                """
            )
            stats = dict(cur.fetchone())
        conn.commit()
    return stats


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS rows,
                       count(green_score_full_0_100) AS scored
                FROM destination_green_score_full
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT d.name,
                       round(g.green_score_full_0_100::numeric, 1) AS full_score,
                       round(g.vegetation_score::numeric, 1) AS vegetation,
                       round(g.park_score::numeric, 1) AS parks,
                       round(g.air_score::numeric, 1) AS air,
                       round(g.water_score::numeric, 1) AS water,
                       round(g.parks_per_100k::numeric, 1) AS parks_per_100k,
                       round(g.pm25_ugm3::numeric, 1) AS pm25,
                       round(g.water_distance_km::numeric, 1) AS water_km
                FROM destination_green_score_full g
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Trento', 'Paris', 'Innsbruck', 'Dubai')
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
