#!/usr/bin/env python3
"""
Optional all-encompassing green score: vegetation, parks, air quality and water access.

This is a first-party composite, not an observation. It is deliberately
separate from the vegetation-only score so the components stay visible.

Weights
-------
* 40% vegetation cover — 0.5 × 1 km tree/shrub/grass + 0.5 × 15 km tree/shrub
* 20% parks — log-scaled mapped Overture park count, not per-capita
* 20% air quality — fixed 100 at 5 µg/m³ to 0 at 25 µg/m³ (SatPM 2024)
* 20% water access — coast or a lake of at least 10 km², size-weighted distance
  decay to 50 km

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
-- destination_green_score (vegetation only) is owned by
-- bootstrap_land_cover.py; this script only builds the composite on top.
DROP VIEW IF EXISTS destination_green_score_all CASCADE;
DROP VIEW IF EXISTS destination_green_score_full CASCADE;
DROP VIEW IF EXISTS destination_outdoor_index_with_parks CASCADE;
DROP VIEW IF EXISTS destination_outdoor_index CASCADE;

CREATE VIEW destination_green_score_all AS
WITH base AS (
    SELECT g.destination_id,
           g.green_score_0_100 AS vegetation_score,
           f.park_count_5km,
           a.pm25_ugm3,
           ge.coast_distance_km,
           w.distance_km AS lake_distance_km,
           w.lake_area_km2
    FROM destination_green_score g
    LEFT JOIN facility_metrics f USING (destination_id)
    LEFT JOIN air_quality_annual a USING (destination_id)
    LEFT JOIN destination_geography ge USING (destination_id)
    LEFT JOIN destination_inland_water w USING (destination_id)
),
scored AS (
    SELECT *,
           CASE WHEN park_count_5km > 0
                THEN LEAST(
                    100.0,
                    100.0 * LN(1.0 + park_count_5km) / LN(101.0)
                )
           END AS park_score,
           CASE WHEN pm25_ugm3 IS NOT NULL
                THEN 100.0 * GREATEST(0.0, LEAST(
                    1.0, (25.0 - pm25_ugm3) / (25.0 - 5.0)
                ))
           END AS air_score,
           CASE WHEN coast_distance_km IS NOT NULL
                     OR (lake_distance_km IS NOT NULL AND lake_area_km2 >= 10.0)
                THEN GREATEST(
                    COALESCE(
                        100.0 * GREATEST(0.0, 1.0 - coast_distance_km / 50.0),
                        0.0
                    ),
                    COALESCE(
                        100.0 * GREATEST(0.0, 1.0 - lake_distance_km / 50.0)
                        * LEAST(1.0, LN(1.0 + lake_area_km2) / LN(11.0)),
                        0.0
                    )
                )
           END AS water_score
    FROM base
)
SELECT destination_id,
       vegetation_score,
       park_score,
       air_score,
       water_score,
       park_count_5km,
       pm25_ugm3,
       coast_distance_km,
       lake_distance_km,
       lake_area_km2,
       CASE WHEN vegetation_score IS NOT NULL
                  AND park_score IS NOT NULL
                  AND air_score IS NOT NULL
                  AND water_score IS NOT NULL
            THEN 0.40 * vegetation_score
               + 0.20 * park_score
               + 0.20 * air_score
               + 0.20 * water_score
       END AS green_score_all_0_100,
       '40% vegetation, 20% parks, 20% air, 20% water'::text AS weights,
       'First-party: ESA WorldCover 2021, Overture Places, SatPM 2024, HydroLAKES/Natural Earth'::text AS source,
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
                       count(green_score_all_0_100) AS scored
                FROM destination_green_score_all
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
                       count(green_score_all_0_100) AS scored
                FROM destination_green_score_all
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT d.name,
                       round(g.green_score_all_0_100::numeric, 1) AS all_score,
                       round(g.vegetation_score::numeric, 1) AS vegetation,
                       round(g.park_score::numeric, 1) AS parks,
                       round(g.air_score::numeric, 1) AS air,
                       round(g.water_score::numeric, 1) AS water,
                       g.park_count_5km,
                       round(g.pm25_ugm3::numeric, 1) AS pm25,
                       round(g.coast_distance_km::numeric, 1) AS coast_km,
                       round(g.lake_distance_km::numeric, 1) AS lake_km
                FROM destination_green_score_all g
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Montréal', 'Paris', 'London', 'New York City', 'Zunyi', 'Ordos')
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
