#!/usr/bin/env python3
"""
GHSL-only greenery views.

Ranking unit: GHSL urban centre, not GeoNames city. Population eligibility is
the GHSL centre population. The physical greenery measure uses the native 10 m
WorldCover fractions inside the GHSL polygon. The nature-access measure
subtracts the urban polygon from the 15 km disc and reports natural tree+shrub
separately from agricultural cropland.

The 1 km point tree share is retained only as a supplementary city-centre
indicator.

Not available yet:
* population-weighted 300 m / 500 m vegetation exposure: requires the global
  GHS-POP R2023A 100 m residential grid;
* 300 m public green-space access: requires green-space polygons plus a
  population grid. The Overture park table is a 5 km POI count, not a
  polygon/access measure.

Usage
-----
    python bootstrap_ghsl_scores.py
    python bootstrap_ghsl_scores.py --report
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCHEMA_SQL = """
DROP VIEW IF EXISTS destination_ghsl_greenery CASCADE;

CREATE VIEW destination_ghsl_greenery AS
WITH base AS (
    SELECT u.destination_id,
           d.name,
           d.country_iso2,
           d.population AS city_population,
           m.urban_centre_id,
           m.urban_centre_name,
           m.centre_population,
           u.area_km2,
           u.tree_fraction,
           u.shrub_fraction,
           u.grassland_fraction,
           u.cropland_fraction,
           u.built_up_fraction,
           u.water_fraction,
           u.valid_pixel_count,
           u.urban_tree_score_0_100,
           u.urban_green_score_0_100,
           g.local_tree_share AS point_tree_share,
           g.green_score_0_100 AS point_vegetation_score,
           lc.tree_fraction AS f15_tree,
           lc.shrub_fraction AS f15_shrub,
           lc.grassland_fraction AS f15_grass,
           lc.cropland_fraction AS f15_crop,
           lc.water_fraction AS f15_water,
           e.air_score,
           e.water_score,
           e.pm25_ugm3
    FROM destination_urban_greenery u
    JOIN destinations d ON d.destination_id = u.destination_id
    LEFT JOIN destination_metro m ON m.destination_id = u.destination_id
    LEFT JOIN destination_green_score g ON g.destination_id = u.destination_id
    LEFT JOIN destination_land_cover lc
      ON lc.destination_id = u.destination_id AND lc.radius_m = 15000
    LEFT JOIN destination_environment_score e
      ON e.destination_id = u.destination_id
),
calc AS (
    SELECT *,
           pi() * 225.0 AS disc15_area_km2,
           CASE WHEN area_km2 < 0.9 * pi() * 225.0
                     AND f15_tree IS NOT NULL
                THEN (pi() * 225.0 * f15_tree - area_km2 * tree_fraction)
                     / (pi() * 225.0 - area_km2) END AS outside_tree,
           CASE WHEN area_km2 < 0.9 * pi() * 225.0
                     AND f15_shrub IS NOT NULL
                THEN (pi() * 225.0 * f15_shrub - area_km2 * shrub_fraction)
                     / (pi() * 225.0 - area_km2) END AS outside_shrub,
           CASE WHEN area_km2 < 0.9 * pi() * 225.0
                     AND f15_grass IS NOT NULL
                THEN (pi() * 225.0 * f15_grass - area_km2 * grassland_fraction)
                     / (pi() * 225.0 - area_km2) END AS outside_grass,
           CASE WHEN area_km2 < 0.9 * pi() * 225.0
                     AND f15_crop IS NOT NULL
                THEN (pi() * 225.0 * f15_crop - area_km2 * cropland_fraction)
                     / (pi() * 225.0 - area_km2) END AS outside_crop,
           CASE WHEN area_km2 < 0.9 * pi() * 225.0
                     AND f15_water IS NOT NULL
                THEN (pi() * 225.0 * f15_water - area_km2 * water_fraction)
                     / (pi() * 225.0 - area_km2) END AS outside_water
    FROM base
),
shares AS (
    SELECT *,
           CASE WHEN 1.0 - water_fraction >= 0.2
                THEN tree_fraction / (1.0 - water_fraction) END
                AS urban_tree_share,
           CASE WHEN 1.0 - water_fraction >= 0.2
                THEN (tree_fraction + shrub_fraction)
                     / (1.0 - water_fraction) END
                AS urban_natural_share,
           CASE WHEN 1.0 - water_fraction >= 0.2
                THEN (tree_fraction + shrub_fraction + grassland_fraction)
                     / (1.0 - water_fraction) END
                AS urban_green_share,
           CASE WHEN outside_water IS NOT NULL
                THEN (outside_tree + outside_shrub) / (1.0 - outside_water)
           END AS outside_natural_share,
           CASE WHEN outside_water IS NOT NULL
                THEN (outside_tree + outside_shrub + outside_grass)
                     / (1.0 - outside_water)
           END AS outside_green_share,
           CASE WHEN outside_water IS NOT NULL
                THEN outside_crop / (1.0 - outside_water)
           END AS outside_crop_share
    FROM calc
)
SELECT destination_id,
       name,
       country_iso2,
       city_population,
       urban_centre_id,
       urban_centre_name,
       centre_population,
       area_km2,
       valid_pixel_count,
       built_up_fraction,
       tree_fraction,
       shrub_fraction,
       grassland_fraction,
       cropland_fraction,
       water_fraction,
       urban_tree_share,
       urban_natural_share,
       urban_green_share,
       100.0 * urban_tree_share AS urban_tree_score,
       100.0 * urban_natural_share AS urban_natural_score,
       100.0 * urban_green_share AS physical_green_score,
       NULL::double precision AS outside_natural_share,
       NULL::double precision AS outside_green_share,
       NULL::double precision AS outside_crop_share,
       NULL::double precision AS nature_access_score,
       NULL::double precision AS nature_access_with_grass_score,
       NULL::double precision AS agricultural_share,
       point_tree_share,
       point_vegetation_score,
       air_score,
       water_score,
       pm25_ugm3,
       CASE WHEN urban_green_share IS NOT NULL
                  AND air_score IS NOT NULL
                  AND water_score IS NOT NULL
            THEN 0.50 * 100.0 * urban_green_share
               + 0.35 * air_score
               + 0.15 * water_score
       END AS environment_score,
       built_up_fraction >= 0.20 AS built_up_valid,
       FALSE AS nature_access_valid,
       centre_population >= 1000000 AS ghsl_population_eligible,
       FALSE AS everyday_greenery_available,
       FALSE AS public_green_access_available,
       'everyday_greenery: GHS-POP 100m not loaded; public_green_access: green-space polygons not loaded; nature_access: area subtraction from a 15km disc produced impossible values, direct 15km buffer outside polygon not computed'::text
           AS missing_reason,
       'First-party: WorldCover 2021 native 10m inside GHSL UCDB R2024A polygon; 15km annulus provisional'::text
           AS source,
       '2021/2026'::text AS observed_period,
       NOW() AS computed_at
FROM shares;
"""


def load(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                """
                SELECT count(*) AS centres,
                       count(physical_green_score) AS physical,
                       count(nature_access_score) AS nature,
                       count(environment_score) AS environment
                FROM destination_ghsl_greenery
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
                SELECT name, centre_population,
                       round(physical_green_score::numeric, 1) AS physical,
                       round(urban_tree_score::numeric, 1) AS tree,
                       round(nature_access_score::numeric, 1) AS nature,
                       round(agricultural_share::numeric, 1) AS crop,
                       round(environment_score::numeric, 1) AS environment
                FROM destination_ghsl_greenery
                WHERE name IN ('Montréal', 'Oslo', 'Stockholm', 'Philadelphia')
                ORDER BY name
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
