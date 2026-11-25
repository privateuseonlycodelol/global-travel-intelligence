#!/usr/bin/env python3
"""
Two optional first-party scores: physical urban greenery and overall
environmental quality.

Both are composites, not observations. Every component is stored and exposed
so the number can be audited or recomputed with different weights.

Physical urban greenery (``destination_greenery_score``)
--------------------------------------------------------
* 25% local tree canopy, 1 km disc, land-only
* 15% local shrub + grass, 1 km disc, land-only
* 45% hinterland tree + shrub, 15 km disc, land-only
* 15% mapped park availability, log-scaled count within 5 km

The local disc is a single point sample and can sit in an unusually green
block. The 45% hinterland weight keeps the score from being decided by that
one kilometre; a true citywide measure would use zonal statistics over the
GHSL urban-centre polygon, which is not yet computed.

Overall environmental quality (``destination_environment_score``)
----------------------------------------------------------------
* 50% physical greenery score
* 35% annual PM2.5 on a fixed log scale (100 at 5 ug/m3, 0 at 75)
* 15% water access (coast, lake >= 10 km2, or Strahler >= 4 river)

A zero park count is ``none_listed``, not evidence of no parks, so it stays
NULL and the park-inclusive score is not produced. No value is imputed.
Water is proximity, not water quality; no global city water-quality indicator
is loaded.

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
DROP VIEW IF EXISTS destination_combined_score CASCADE;
DROP VIEW IF EXISTS destination_environment_score CASCADE;
DROP VIEW IF EXISTS destination_greenery_score CASCADE;
DROP VIEW IF EXISTS destination_green_score_all CASCADE;
DROP VIEW IF EXISTS destination_green_score_full CASCADE;
DROP VIEW IF EXISTS destination_outdoor_index_with_parks CASCADE;
DROP VIEW IF EXISTS destination_outdoor_index CASCADE;

CREATE VIEW destination_greenery_score AS
WITH base AS (
    SELECT g.destination_id,
           g.local_tree_share,
           g.local_shrub_share,
           g.local_grass_share,
           g.hinterland_tree_share,
           g.hinterland_shrub_share,
           g.local_water_fraction,
           g.hinterland_water_fraction,
           g.green_score_0_100 AS vegetation_only_score_0_100,
           g.tree_score_0_100,
           f.park_count_5km
    FROM destination_green_score g
    LEFT JOIN facility_metrics f USING (destination_id)
),
scored AS (
    SELECT *,
           100.0 * local_tree_share AS local_tree_score,
           100.0 * (local_shrub_share + local_grass_share)
               AS local_other_veg_score,
           100.0 * (hinterland_tree_share + hinterland_shrub_share)
               AS hinterland_tree_shrub_score,
           CASE WHEN park_count_5km > 0
                THEN LEAST(
                    100.0,
                    100.0 * LN(1.0 + park_count_5km) / LN(501.0)
                )
           END AS park_score
    FROM base
)
SELECT destination_id,
       local_tree_score,
       local_other_veg_score,
       hinterland_tree_shrub_score,
       park_score,
       park_count_5km,
       CASE WHEN local_tree_score IS NOT NULL
                  AND local_other_veg_score IS NOT NULL
                  AND hinterland_tree_shrub_score IS NOT NULL
                  AND park_score IS NOT NULL
            THEN 0.25 * local_tree_score
               + 0.15 * local_other_veg_score
               + 0.45 * hinterland_tree_shrub_score
               + 0.15 * park_score
       END AS greenery_score_0_100,
       vegetation_only_score_0_100,
       tree_score_0_100,
       local_tree_share,
       local_shrub_share,
       local_grass_share,
       hinterland_tree_share,
       hinterland_shrub_share,
       local_water_fraction,
       hinterland_water_fraction,
       '25% local tree, 15% local shrub+grass, 45% hinterland tree+shrub, 15% mapped parks'::text
           AS weights,
       'First-party: ESA WorldCover 2021 + Overture Places'::text AS source,
       '2021/2026'::text AS observed_period,
       NOW() AS computed_at
FROM scored;

CREATE VIEW destination_environment_score AS
WITH worst AS (
    SELECT destination_id, MAX(pm25_ugm3) AS worst_month_pm25
    FROM air_quality_monthly
    GROUP BY destination_id
),
base AS (
    SELECT gr.destination_id,
           gr.greenery_score_0_100,
           gr.vegetation_only_score_0_100,
           gr.park_score,
           gr.park_count_5km,
           a.pm25_ugm3,
           w.worst_month_pm25,
           ge.coast_distance_km,
           iw.large_lake_name,
           iw.large_lake_area_km2,
           iw.large_lake_distance_km,
           r.river_order,
           r.discharge_cms,
           r.distance_km AS river_distance_km
    FROM destination_greenery_score gr
    LEFT JOIN air_quality_annual a USING (destination_id)
    LEFT JOIN worst w USING (destination_id)
    LEFT JOIN destination_geography ge USING (destination_id)
    LEFT JOIN destination_inland_water iw USING (destination_id)
    LEFT JOIN destination_river_access r USING (destination_id)
),
scored AS (
    SELECT *,
           CASE WHEN pm25_ugm3 IS NOT NULL
                THEN CASE
                    WHEN pm25_ugm3 <= 5.0 THEN 100.0
                    WHEN pm25_ugm3 >= 75.0 THEN 0.0
                    ELSE 100.0 * (LN(75.0) - LN(pm25_ugm3))
                         / (LN(75.0) - LN(5.0))
                END
           END AS air_score,
           CASE WHEN coast_distance_km IS NOT NULL
                      OR large_lake_distance_km IS NOT NULL
                      OR river_distance_km IS NOT NULL
                THEN 100.0 * GREATEST(
                    0.0,
                    1.0 - LEAST(
                        COALESCE(coast_distance_km, 1e9),
                        COALESCE(large_lake_distance_km, 1e9),
                        COALESCE(river_distance_km, 1e9)
                    ) / 50.0
                )
           END AS water_score
    FROM base
)
SELECT destination_id,
       greenery_score_0_100,
       vegetation_only_score_0_100,
       park_score,
       park_count_5km,
       air_score,
       pm25_ugm3,
       worst_month_pm25,
       water_score,
       coast_distance_km,
       large_lake_name,
       large_lake_area_km2,
       large_lake_distance_km,
       river_order,
       discharge_cms,
       river_distance_km,
       CASE WHEN coast_distance_km IS NOT NULL
                  OR large_lake_distance_km IS NOT NULL
                  OR river_distance_km IS NOT NULL
            THEN LEAST(
                COALESCE(coast_distance_km, 1e9),
                COALESCE(large_lake_distance_km, 1e9),
                COALESCE(river_distance_km, 1e9)
            )
       END AS water_distance_km,
       CASE WHEN river_distance_km IS NOT NULL
                  AND river_distance_km <= COALESCE(coast_distance_km, 1e9)
                  AND river_distance_km <= COALESCE(large_lake_distance_km, 1e9)
                 THEN 'river'
            WHEN large_lake_distance_km IS NOT NULL
                  AND large_lake_distance_km <= COALESCE(coast_distance_km, 1e9)
                 THEN 'large_lake'
            WHEN coast_distance_km IS NOT NULL THEN 'coast'
       END AS water_feature,
       CASE WHEN greenery_score_0_100 IS NOT NULL
                  AND air_score IS NOT NULL
                  AND water_score IS NOT NULL
            THEN 0.50 * greenery_score_0_100
               + 0.35 * air_score
               + 0.15 * water_score
       END AS environment_score_0_100,
       '50% greenery, 35% air, 15% water access'::text AS weights,
       'First-party: ESA WorldCover 2021, Overture Places, SatPM 2024, HydroLAKES, HydroRIVERS, Natural Earth'::text
           AS source,
       '2021/2024/2026/2026'::text AS observed_period,
       NOW() AS computed_at
FROM scored;

CREATE VIEW destination_combined_score AS
SELECT g.destination_id,
       g.greenery_score_0_100,
       e.environment_score_0_100,
       0.50 * g.greenery_score_0_100
         + 0.50 * e.environment_score_0_100 AS combined_score_0_100,
       '50% physical greenery, 50% environmental quality (effective 75% greenery, 17.5% air, 7.5% water)'::text
           AS weights,
       'First-party: physical greenery + environmental quality'::text AS source,
       '2021/2024/2026'::text AS observed_period,
       NOW() AS computed_at
FROM destination_greenery_score g
JOIN destination_environment_score e USING (destination_id)
WHERE g.greenery_score_0_100 IS NOT NULL
  AND e.environment_score_0_100 IS NOT NULL;
"""


def log(message: str) -> None:
    print(message, flush=True)


def _require(cur: psycopg.Cursor, table: str) -> None:
    cur.execute("SELECT to_regclass(%s) AS name", (f"public.{table}",))
    if cur.fetchone()["name"] is None:
        raise RuntimeError(
            f"{table} is missing; run its bootstrap script before this one"
        )


def load(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            _require(cur, "destination_green_score")
            _require(cur, "destination_river_access")
            cur.execute(SCHEMA_SQL)
            cur.execute(
                """
                SELECT count(*) AS greenery_rows,
                       count(g.greenery_score_0_100) AS greenery_scored,
                       count(e.environment_score_0_100) AS environment_scored
                FROM destination_greenery_score g
                LEFT JOIN destination_environment_score e USING (destination_id)
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
                       count(g.greenery_score_0_100) AS greenery,
                       count(e.environment_score_0_100) AS environment
                FROM destination_greenery_score g
                LEFT JOIN destination_environment_score e USING (destination_id)
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT d.name, d.country_iso2,
                       round(g.greenery_score_0_100::numeric, 1) AS greenery,
                       round(g.local_tree_score::numeric, 1) AS local_tree,
                       round(g.local_other_veg_score::numeric, 1) AS local_other,
                       round(g.hinterland_tree_shrub_score::numeric, 1) AS hinterland,
                       round(g.park_score::numeric, 1) AS parks,
                       round(e.air_score::numeric, 1) AS air,
                       round(e.water_score::numeric, 1) AS water,
                       round(e.environment_score_0_100::numeric, 1) AS environment
                FROM destination_greenery_score g
                JOIN destinations d USING (destination_id)
                LEFT JOIN destination_environment_score e USING (destination_id)
                WHERE (d.name, d.country_iso2) IN (
                    ('Montréal', 'CA'), ('Paris', 'FR'), ('London', 'GB'),
                    ('New York City', 'US'), ('Trento', 'IT'), ('Zunyi', 'CN'),
                    ('Ordos', 'CN'), ('Singapore', 'SG'), ('Stockholm', 'SE')
                )
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
