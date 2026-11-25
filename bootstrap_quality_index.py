#!/usr/bin/env python3
"""
Objective city quality index for GHSL urban centres >= 1M.

Seven dimensions, all from measured/open data:

1. Environment & parks (30%)
   physical urban greenery 40%, mapped parks 20%, air 25%, water access 15%
2. Culture & food (20%)
   culture venues, restaurants, cuisine variety (percentiles within cohort)
3. Safety & health (15%)
   homicide (lower), road deaths (lower), life expectancy, physicians
4. Affordability (10%)
   country price level (lower), out-of-pocket health spending (lower)
5. Walkability (10%)
   How Walkable score where available
6. Climate (10%)
   comfortable annual high and rainy days from NASA POWER recent normals
7. Connectivity (5%)
   download, upload, latency

Missing dimensions are dropped and the active weights are renormalized.
`data_completeness` is the share of the 1.0 total weight that was available.
The score is a context index, not a subjective "best city" judgment.

Usage
-----
    python bootstrap_quality_index.py
    python bootstrap_quality_index.py --report
"""

from __future__ import annotations

import argparse
import math
import os
from bisect import bisect_left, bisect_right
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCHEMA_SQL = """
DROP TABLE IF EXISTS city_quality_index;

CREATE TABLE city_quality_index (
    destination_id          TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    country_iso2            CHAR(2) NOT NULL,
    centre_population       BIGINT,
    physical_green_score    DOUBLE PRECISION,
    park_score              DOUBLE PRECISION,
    air_score               DOUBLE PRECISION,
    water_score             DOUBLE PRECISION,
    museum_culture_count    INT,
    restaurant_count        INT,
    cuisine_entropy         DOUBLE PRECISION,
    homicide_per_100k       DOUBLE PRECISION,
    road_deaths_per_100k    DOUBLE PRECISION,
    life_expectancy_years   DOUBLE PRECISION,
    physicians_per_1000     DOUBLE PRECISION,
    price_level_vs_usd      DOUBLE PRECISION,
    out_of_pocket_pct       DOUBLE PRECISION,
    walkability_score       DOUBLE PRECISION,
    transit_stops_5km       INT,
    public_transport_score  DOUBLE PRECISION,
    median_down_mbps        DOUBLE PRECISION,
    median_up_mbps          DOUBLE PRECISION,
    latency_ms              DOUBLE PRECISION,
    climate_avg_high_c      DOUBLE PRECISION,
    climate_rainy_days      DOUBLE PRECISION,
    environment_score       DOUBLE PRECISION,
    culture_food_score      DOUBLE PRECISION,
    safety_health_score     DOUBLE PRECISION,
    affordability_score     DOUBLE PRECISION,
    climate_score           DOUBLE PRECISION,
    connectivity_score      DOUBLE PRECISION,
    quality_score           DOUBLE PRECISION,
    data_completeness       DOUBLE PRECISION,
    missing_dimensions      TEXT,
    source                  TEXT NOT NULL,
    observed_period         TEXT NOT NULL,
    computed_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

QUERY = """
WITH base AS (
    SELECT g.destination_id,
           g.name,
           g.country_iso2,
           g.centre_population,
           g.physical_green_score,
           g.air_score,
           g.water_score,
           p.museum_culture_count,
           p.restaurant_count,
           p.cuisine_entropy,
           fm.park_count_5km,
           w.score AS walk_score,
           mm.transit_stops_5km,
           bb.median_down_mbps,
           bb.median_up_mbps,
           bb.avg_latency_ms,
           c.price_level_vs_usd,
           c.out_of_pocket_health_pct,
           c.homicide_per_100k,
           c.road_traffic_deaths_per_100k,
           c.life_expectancy_years,
           c.physicians_per_1000,
           cl.avg_high,
           cl.avg_rainy
    FROM destination_ghsl_greenery g
    LEFT JOIN poi_metrics p USING (destination_id)
    LEFT JOIN facility_metrics fm USING (destination_id)
    LEFT JOIN walkability_scores w USING (destination_id)
    LEFT JOIN mobility_metrics mm USING (destination_id)
    LEFT JOIN broadband_metrics bb USING (destination_id)
    LEFT JOIN (
        SELECT country_iso2,
               max(value) FILTER (WHERE indicator = 'price_level_vs_usd') AS price_level_vs_usd,
               max(value) FILTER (WHERE indicator = 'out_of_pocket_health_pct') AS out_of_pocket_health_pct,
               max(value) FILTER (WHERE indicator = 'homicide_per_100k') AS homicide_per_100k,
               max(value) FILTER (WHERE indicator = 'road_traffic_deaths_per_100k') AS road_traffic_deaths_per_100k,
               max(value) FILTER (WHERE indicator = 'life_expectancy_years') AS life_expectancy_years,
               max(value) FILTER (WHERE indicator = 'physicians_per_1000') AS physicians_per_1000
        FROM country_indicators
        GROUP BY country_iso2
    ) c USING (country_iso2)
    LEFT JOIN (
        SELECT destination_id,
               avg(avg_high_c) AS avg_high,
               avg(rainy_days) AS avg_rainy
        FROM climate_recent
        GROUP BY destination_id
    ) cl USING (destination_id)
)
SELECT * FROM base
"""

WEIGHTS = {
    "environment": 0.25,
    "culture_food": 0.20,
    "safety_health": 0.15,
    "affordability": 0.10,
    "walkability": 0.10,
    "public_transport": 0.10,
    "climate": 0.05,
    "connectivity": 0.05,
}


def percentile_map(values: list[float | None]) -> dict[float, float]:
    clean = sorted(value for value in values if value is not None)
    n = len(clean)
    if n <= 1:
        return {value: 50.0 for value in clean}
    result = {}
    for value in clean:
        lo = bisect_left(clean, value)
        hi = bisect_right(clean, value)
        result[value] = 100.0 * ((lo + hi - 1) / 2.0) / (n - 1)
    return result


def weighted_available(pairs: list[tuple[float | None, float]]) -> float | None:
    active = [(value, weight) for value, weight in pairs if value is not None]
    if not active:
        return None
    return sum(value * weight for value, weight in active) / sum(
        weight for _, weight in active
    )


def load(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            rows = [dict(row) for row in cur.fetchall()]

        for row in rows:
            if row["museum_culture_count"] is not None:
                row["culture_log"] = math.log1p(row["museum_culture_count"])
            else:
                row["culture_log"] = None
            if row["restaurant_count"] is not None:
                row["restaurant_log"] = math.log1p(row["restaurant_count"])
            else:
                row["restaurant_log"] = None
            if row["transit_stops_5km"] is not None:
                row["transit_log"] = math.log1p(row["transit_stops_5km"])
            else:
                row["transit_log"] = None
            if row["park_count_5km"] and row["park_count_5km"] > 0:
                row["park_score"] = min(
                    100.0,
                    100.0 * math.log1p(row["park_count_5km"]) / math.log(501.0),
                )
            else:
                row["park_score"] = None
            if row["avg_high"] is not None and row["avg_rainy"] is not None:
                comfort = max(
                    0.0,
                    1.0 - 0.10 * max(20.0 - row["avg_high"], row["avg_high"] - 26.0, 0.0),
                )
                rain = max(0.0, 1.0 - row["avg_rainy"] / 20.0)
                row["climate_score"] = 100.0 * (0.70 * comfort + 0.30 * rain)
            else:
                row["climate_score"] = None

        maps = {
            "culture": percentile_map([r["culture_log"] for r in rows]),
            "restaurant": percentile_map([r["restaurant_log"] for r in rows]),
            "cuisine": percentile_map([r["cuisine_entropy"] for r in rows]),
            "homicide": percentile_map([r["homicide_per_100k"] for r in rows]),
            "road": percentile_map([r["road_traffic_deaths_per_100k"] for r in rows]),
            "life": percentile_map([r["life_expectancy_years"] for r in rows]),
            "physicians": percentile_map([r["physicians_per_1000"] for r in rows]),
            "price": percentile_map([r["price_level_vs_usd"] for r in rows]),
            "oop": percentile_map([r["out_of_pocket_health_pct"] for r in rows]),
            "down": percentile_map([r["median_down_mbps"] for r in rows]),
            "up": percentile_map([r["median_up_mbps"] for r in rows]),
            "latency": percentile_map([r["avg_latency_ms"] for r in rows]),
            "transit": percentile_map([r["transit_log"] for r in rows]),
        }

        def pct(row: dict[str, Any], key: str, source: str, lower_better: bool = False):
            value = row.get(source)
            if value is None:
                return None
            score = maps[key].get(value)
            return 100.0 - score if lower_better and score is not None else score

        for row in rows:
            row["environment"] = weighted_available(
                [
                    (row["physical_green_score"], 0.40),
                    (row["park_score"], 0.20),
                    (row["air_score"], 0.25),
                    (row["water_score"], 0.15),
                ]
            )
            row["culture_food"] = weighted_available(
                [
                    (pct(row, "culture", "culture_log"), 0.50),
                    (pct(row, "restaurant", "restaurant_log"), 0.30),
                    (pct(row, "cuisine", "cuisine_entropy"), 0.20),
                ]
            )
            row["safety_health"] = weighted_available(
                [
                    (pct(row, "homicide", "homicide_per_100k", True), 0.35),
                    (pct(row, "road", "road_traffic_deaths_per_100k", True), 0.25),
                    (pct(row, "life", "life_expectancy_years"), 0.25),
                    (pct(row, "physicians", "physicians_per_1000"), 0.15),
                ]
            )
            row["affordability"] = weighted_available(
                [
                    (pct(row, "price", "price_level_vs_usd", True), 0.70),
                    (pct(row, "oop", "out_of_pocket_health_pct", True), 0.30),
                ]
            )
            row["connectivity"] = weighted_available(
                [
                    (pct(row, "down", "median_down_mbps"), 0.50),
                    (pct(row, "up", "median_up_mbps"), 0.20),
                    (pct(row, "latency", "avg_latency_ms", True), 0.30),
                ]
            )
            row["walkability"] = row["walk_score"]
            row["public_transport"] = pct(row, "transit", "transit_log")
            dimensions = [
                ("environment", row["environment"]),
                ("culture_food", row["culture_food"]),
                ("safety_health", row["safety_health"]),
                ("affordability", row["affordability"]),
                ("walkability", row["walkability"]),
                ("public_transport", row["public_transport"]),
                ("climate", row["climate_score"]),
                ("connectivity", row["connectivity"]),
            ]
            active = [(value, WEIGHTS[name]) for name, value in dimensions if value is not None]
            row["quality_score"] = (
                sum(value * weight for value, weight in active)
                / sum(weight for _, weight in active)
                if active else None
            )
            row["data_completeness"] = sum(weight for _, weight in active)
            row["missing_dimensions"] = ",".join(
                name for name, value in dimensions if value is None
            )

        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.executemany(
                """
                INSERT INTO city_quality_index (
                    destination_id, name, country_iso2, centre_population,
                    physical_green_score, park_score, air_score, water_score,
                    museum_culture_count, restaurant_count, cuisine_entropy,
                    homicide_per_100k, road_deaths_per_100k,
                    life_expectancy_years, physicians_per_1000,
                    price_level_vs_usd, out_of_pocket_pct,
                    walkability_score, transit_stops_5km,
                    public_transport_score, median_down_mbps, median_up_mbps,
                    latency_ms, climate_avg_high_c, climate_rainy_days,
                    environment_score, culture_food_score, safety_health_score,
                    affordability_score, climate_score, connectivity_score,
                    quality_score, data_completeness, missing_dimensions,
                    source, observed_period
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                [
                    (
                        row["destination_id"], row["name"], row["country_iso2"],
                        row["centre_population"], row["physical_green_score"],
                        row["park_score"], row["air_score"], row["water_score"],
                        row["museum_culture_count"], row["restaurant_count"],
                        row["cuisine_entropy"], row["homicide_per_100k"],
                        row["road_traffic_deaths_per_100k"],
                        row["life_expectancy_years"], row["physicians_per_1000"],
                        row["price_level_vs_usd"], row["out_of_pocket_health_pct"],
                        row["walk_score"], row["transit_stops_5km"],
                        row["public_transport"], row["median_down_mbps"],
                        row["median_up_mbps"], row["avg_latency_ms"],
                        row["avg_high"], row["avg_rainy"],
                        row["environment"], row["culture_food"],
                        row["safety_health"], row["affordability"],
                        row["climate_score"], row["connectivity"],
                        row["quality_score"], row["data_completeness"],
                        row["missing_dimensions"],
                        "First-party composite: GHSL, WorldCover, Overture, SatPM, NASA POWER, World Bank, How Walkable, Ookla",
                        "2021-2026",
                    )
                    for row in rows
                ],
            )
            cur.execute(
                """
                SELECT count(*) AS cities,
                       count(quality_score) AS scored,
                       round(avg(data_completeness)::numeric, 3) AS avg_completeness
                FROM city_quality_index
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
                SELECT name, country_iso2,
                       round(quality_score::numeric, 1) AS quality,
                       round(environment_score::numeric, 1) AS environment,
                       round(culture_food_score::numeric, 1) AS culture_food,
                       round(safety_health_score::numeric, 1) AS safety_health,
                       round(affordability_score::numeric, 1) AS affordability,
                       round(walkability_score::numeric, 1) AS walkability,
                       round(climate_score::numeric, 1) AS climate,
                       round(connectivity_score::numeric, 1) AS connectivity,
                       round(data_completeness::numeric, 2) AS completeness
                FROM city_quality_index
                ORDER BY quality_score DESC, centre_population DESC
                LIMIT 20
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
