#!/usr/bin/env python3
"""
Phase 3: broadband, walkability/transit and optional monthly housing data.

Adds four datasets to the Phase 1 destinations database:

  1. Ookla Open Data (fixed broadband)  -> broadband_metrics
  2. Overture Maps Transportation        -> mobility_metrics (walk + rail)
  3. Mobility Database GTFS feeds        -> mobility_metrics.gtfs_active_feeds
  4. Inside Airbnb listings (optional)   -> housing_monthly

and installs rank_destinations_v3(), an eight-dimension scorer.

The script assumes Phase 1 has already populated `destinations`. It is
idempotent: re-running updates the Phase 3 tables in place. A country-scoped
run only touches the cities in that scope.

Examples
--------
    # Fast partial load (Ookla + GTFS), no huge Overture scan:
    python bootstrap_phase3.py --countries SE,ES --skip-overture-transport

    # Full Phase 3 for one small country:
    python bootstrap_phase3.py --countries LU

    # Add optional Inside Airbnb medians from local city dumps:
    python bootstrap_phase3.py --countries ES --skip-ookla --skip-overture-transport \
        --skip-gtfs --airbnb-dir data_cache/airbnb
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import psycopg
import requests

CACHE_DIR = Path("./data_cache")

OOKLA_URL_TEMPLATE = (
    "https://ookla-open-data.s3.amazonaws.com/parquet/performance/type=fixed/"
    "year={year}/quarter={quarter}/{year}-{month}-01_performance_fixed_tiles.parquet"
)
DEFAULT_OOKLA_YEAR = 2026
DEFAULT_OOKLA_QUARTER = 2

MOBILITY_DB_URL = "https://files.mobilitydatabase.org/feeds_v2.csv"

DEFAULT_OVERTURE_RELEASE = "2026-11-18.0"
OVERTURE_S3_ROOT = "s3://overturemaps-us-west-2/release"

# Overture `class` values counted as pedestrian-friendly for the 2 km ratio.
# The denominator is every `subtype='road'` segment in the same radius.
PEDESTRIAN_CLASSES = (
    "footway",
    "pedestrian",
    "living_street",
    "steps",
    "cycleway",
    "path",
    "bridleway",
)

# Schengen short-stay area (29 countries as of 2026). Used only to implement
# the 90/180-day visitor rule; it is not legal advice.
SCHENGEN_ISO2 = (
    "AT", "BE", "BG", "CH", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GR", "HR", "HU", "IS", "IT", "LI", "LT", "LU", "LV",
    "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK",
)

PHASE3_SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS broadband_metrics (
    destination_id     TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    radius_km          DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    median_down_mbps   DOUBLE PRECISION,
    median_up_mbps     DOUBLE PRECISION,
    avg_latency_ms     DOUBLE PRECISION,
    total_tests        BIGINT,
    tile_count         INT,
    source             TEXT NOT NULL,
    observed_period    TEXT NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mobility_metrics (
    destination_id           TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    radius_km                DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    pedestrian_segments_2km  INT,
    total_road_segments_2km  INT,
    pedestrian_ratio_2km     DOUBLE PRECISION,
    rail_segments_5km        INT,
    gtfs_active_feeds        INT,
    transit_stops_5km        INT,
    transit_stops_source     TEXT,
    transit_stops_period     TEXT,
    source                   TEXT NOT NULL,
    observed_period          TEXT NOT NULL,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS housing_monthly (
    destination_id       TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    median_monthly_local   DOUBLE PRECISION,
    sample_listings      INT,
    source               TEXT NOT NULL,
    observed_period      TEXT NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS walkability_scores (
    destination_id  TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    score           SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    centre_score    SMALLINT CHECK (centre_score BETWEEN 0 AND 100),
    rank_position   INT,
    ranked_cities   INT NOT NULL,
    city_label      TEXT NOT NULL,
    region          TEXT,
    country_name    TEXT NOT NULL,
    population      BIGINT,
    source          TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    url             TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

PHASE3_SCORER_SQL = f"""
DROP FUNCTION IF EXISTS rank_destinations_v3;
CREATE OR REPLACE FUNCTION rank_destinations_v3(
    p_months              INT[],
    p_ideal_temp_min      DOUBLE PRECISION DEFAULT 20.0,
    p_ideal_temp_max      DOUBLE PRECISION DEFAULT 26.0,
    p_max_temp_c          DOUBLE PRECISION DEFAULT 31.0,
    p_min_population      INT DEFAULT 40000,
    p_countries           TEXT[] DEFAULT NULL,
    p_require_airport     BOOLEAN DEFAULT FALSE,
    p_exclude_schengen    BOOLEAN DEFAULT FALSE,
    p_min_broadband_mbps  DOUBLE PRECISION DEFAULT NULL,
    p_require_gtfs        BOOLEAN DEFAULT FALSE,
    p_w_climate           DOUBLE PRECISION DEFAULT 0.20,
    p_w_culture           DOUBLE PRECISION DEFAULT 0.15,
    p_w_food              DOUBLE PRECISION DEFAULT 0.15,
    p_w_walk              DOUBLE PRECISION DEFAULT 0.15,
    p_w_transit           DOUBLE PRECISION DEFAULT 0.15,
    p_w_broadband         DOUBLE PRECISION DEFAULT 0.10,
    p_w_low_crowding      DOUBLE PRECISION DEFAULT 0.05,
    p_w_housing           DOUBLE PRECISION DEFAULT 0.05,
    p_limit               INT DEFAULT 15
)
RETURNS TABLE (
    destination_id     TEXT,
    name               TEXT,
    country_iso2       CHAR(2),
    population         INT,
    iata_codes         TEXT[],
    excluded_for_no_evidence BIGINT,
    final_score        DOUBLE PRECISION,
    data_completeness  DOUBLE PRECISION,
    metrics            JSONB,
    provenance         JSONB
)
LANGUAGE sql STABLE AS $$
WITH schengen(iso2) AS (
    VALUES {", ".join(f"('{c}')" for c in SCHENGEN_ISO2)}
),
window_climate AS (
    SELECT
        c.destination_id,
        AVG(c.median_temp_max_c) AS avg_max_c,
        MAX(c.median_temp_max_c) AS peak_month_max_c,
        AVG(c.precip_mm)         AS avg_precip_mm,
        AVG(c.avg_rainy_days)    AS avg_rainy_days_mo,
        MIN(c.source)            AS climate_source,
        MIN(c.observed_period)   AS climate_period
    FROM climate_monthly c
    WHERE c.month = ANY(p_months)
    GROUP BY c.destination_id
),
filtered AS (
    SELECT
        d.destination_id,
        d.name,
        d.country_iso2,
        d.population,
        d.iata_codes,
        wc.avg_max_c,
        wc.peak_month_max_c,
        wc.avg_precip_mm,
        wc.avg_rainy_days_mo,
        CASE WHEN p.museum_culture_count > 0 THEN p.museum_culture_count END AS museum_culture_count,
        CASE WHEN p.restaurant_count > 0 THEN p.restaurant_count END AS restaurant_count,
        CASE WHEN p.restaurant_count > 0 THEN p.cuisine_entropy END AS cuisine_entropy,
        t.nights_per_resident,
        m.pedestrian_segments_2km,
        m.total_road_segments_2km,
        m.pedestrian_ratio_2km,
        m.rail_segments_5km,
        m.gtfs_active_feeds,
        m.transit_stops_5km,
        ws.score                 AS walkability_score_100,
        ws.centre_score          AS walkability_centre_score,
        ws.ranked_cities         AS walkability_ranked_cities,
        CASE WHEN b.total_tests >= 10 AND b.median_down_mbps > 0 THEN b.median_down_mbps END AS median_down_mbps,
        CASE WHEN b.total_tests >= 10 AND b.median_up_mbps > 0 THEN b.median_up_mbps END AS median_up_mbps,
        CASE WHEN b.total_tests >= 10 AND b.avg_latency_ms > 0 THEN b.avg_latency_ms END AS avg_latency_ms,
        b.total_tests,
        h.median_monthly_local,
        h.sample_listings,
        wc.climate_source,
        wc.climate_period,
        p.source                 AS poi_source,
        p.observed_period        AS poi_period,
        t.source                 AS crowding_source,
        t.observed_period        AS crowding_period,
        m.source                 AS mobility_source,
        m.observed_period        AS mobility_period,
        m.transit_stops_source   AS transit_stops_source,
        m.transit_stops_period   AS transit_stops_period,
        b.source                 AS broadband_source,
        b.observed_period        AS broadband_period,
        h.source                 AS housing_source,
        h.observed_period        AS housing_period
    FROM destinations d
    LEFT JOIN window_climate wc  ON wc.destination_id = d.destination_id
    LEFT JOIN poi_metrics p      ON p.destination_id  = d.destination_id
    LEFT JOIN tourism_pressure t ON t.destination_id  = d.destination_id
    LEFT JOIN mobility_metrics m ON m.destination_id  = d.destination_id
    LEFT JOIN walkability_scores ws ON ws.destination_id = d.destination_id
    LEFT JOIN broadband_metrics b ON b.destination_id = d.destination_id
    LEFT JOIN housing_monthly h  ON h.destination_id  = d.destination_id
    WHERE (p_min_population IS NULL OR d.population >= p_min_population)
      AND (p_countries IS NULL OR d.country_iso2 = ANY(p_countries))
      AND (NOT COALESCE(p_require_airport, FALSE) OR cardinality(d.iata_codes) > 0)
      AND (NOT COALESCE(p_exclude_schengen, FALSE) OR d.country_iso2 NOT IN (SELECT iso2 FROM schengen))
      AND (p_min_broadband_mbps IS NULL OR
           (b.total_tests >= 10 AND b.median_down_mbps >= p_min_broadband_mbps))
      AND (NOT COALESCE(p_require_gtfs, FALSE) OR COALESCE(m.gtfs_active_feeds, 0) > 0)
      AND (p_max_temp_c IS NULL OR wc.peak_month_max_c IS NULL OR wc.peak_month_max_c <= p_max_temp_c)
),
culture_ranks AS (
    SELECT destination_id,
           CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(LN(1 + museum_culture_count)) OVER ()
                    = MAX(LN(1 + museum_culture_count)) OVER ()
               THEN 0.5
               ELSE 0.05 + 0.90 * PERCENT_RANK() OVER (ORDER BY LN(1 + museum_culture_count))
           END AS s_culture
    FROM filtered
    WHERE museum_culture_count IS NOT NULL
),
food_values AS (
    SELECT destination_id,
           LN(1 + restaurant_count) AS restaurant_value,
           COALESCE(cuisine_entropy, 0) AS entropy_value
    FROM filtered
    WHERE restaurant_count IS NOT NULL
),
food_ranks AS (
    SELECT destination_id,
           0.50 * CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(restaurant_value) OVER () = MAX(restaurant_value) OVER ()
               THEN 0.5
               ELSE 0.05 + 0.90 * PERCENT_RANK() OVER (ORDER BY restaurant_value)
           END
         + 0.50 * CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(entropy_value) OVER () = MAX(entropy_value) OVER ()
               THEN 0.5
               ELSE 0.05 + 0.90 * PERCENT_RANK() OVER (ORDER BY entropy_value)
           END AS s_food
    FROM food_values
),
crowding_ranks AS (
    SELECT destination_id,
           CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(nights_per_resident) OVER () = MAX(nights_per_resident) OVER ()
               THEN 0.5
               ELSE 0.95 - 0.90 * PERCENT_RANK() OVER (ORDER BY nights_per_resident)
           END AS s_low_crowding
    FROM filtered
    WHERE nights_per_resident IS NOT NULL
),
walk_values AS (
    SELECT destination_id,
           walkability_score_100 AS score_value
    FROM filtered
    WHERE walkability_score_100 IS NOT NULL
),
walk_ranks AS (
    SELECT destination_id,
           CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(score_value) OVER () = MAX(score_value) OVER ()
               THEN 0.5
               ELSE 0.05 + 0.90 * PERCENT_RANK() OVER (ORDER BY score_value)
           END AS s_walk
    FROM walk_values
),
rail_ranks AS (
    SELECT destination_id,
           CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(LN(1 + rail_segments_5km)) OVER ()
                    = MAX(LN(1 + rail_segments_5km)) OVER ()
               THEN 0.5
               ELSE 0.05 + 0.90 * PERCENT_RANK() OVER (ORDER BY LN(1 + rail_segments_5km))
           END AS s_rail
    FROM filtered
    WHERE rail_segments_5km IS NOT NULL
),
gtfs_ranks AS (
    SELECT destination_id,
           CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(LN(1 + gtfs_active_feeds)) OVER ()
                    = MAX(LN(1 + gtfs_active_feeds)) OVER ()
               THEN 0.5
               ELSE 0.05 + 0.90 * PERCENT_RANK() OVER (ORDER BY LN(1 + gtfs_active_feeds))
           END AS s_gtfs
    FROM filtered
    WHERE gtfs_active_feeds IS NOT NULL
),
transit_stop_ranks AS (
    SELECT destination_id,
           CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(LN(1 + transit_stops_5km)) OVER ()
                    = MAX(LN(1 + transit_stops_5km)) OVER ()
               THEN 0.5
               ELSE 0.05 + 0.90 * PERCENT_RANK() OVER (ORDER BY LN(1 + transit_stops_5km))
           END AS s_transit_stops
    FROM filtered
    WHERE transit_stops_5km IS NOT NULL
),
transit_ranks AS (
    SELECT
        f.destination_id,
        (COALESCE(sr.s_transit_stops * 0.40, 0.0)
       + COALESCE(rr.s_rail * 0.30, 0.0)
       + COALESCE(gr.s_gtfs * 0.30, 0.0))
        / NULLIF(
            (CASE WHEN sr.s_transit_stops IS NOT NULL THEN 0.40 ELSE 0.0 END)
          + (CASE WHEN rr.s_rail IS NOT NULL THEN 0.30 ELSE 0.0 END)
          + (CASE WHEN gr.s_gtfs IS NOT NULL THEN 0.30 ELSE 0.0 END),
            0.0
        ) AS s_transit
    FROM filtered f
    LEFT JOIN transit_stop_ranks sr ON sr.destination_id = f.destination_id
    LEFT JOIN rail_ranks rr ON rr.destination_id = f.destination_id
    LEFT JOIN gtfs_ranks gr ON gr.destination_id = f.destination_id
    WHERE sr.s_transit_stops IS NOT NULL
       OR rr.s_rail IS NOT NULL
       OR gr.s_gtfs IS NOT NULL
),
broadband_ranks AS (
    SELECT destination_id,
           CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(LN(1 + median_down_mbps)) OVER ()
                    = MAX(LN(1 + median_down_mbps)) OVER ()
               THEN 0.5
               ELSE 0.05 + 0.90 * PERCENT_RANK() OVER (ORDER BY LN(1 + median_down_mbps))
           END AS s_broadband
    FROM filtered
    WHERE median_down_mbps IS NOT NULL
),
housing_ranks AS (
    SELECT destination_id,
           CASE
               WHEN COUNT(*) OVER () = 1
                 OR MIN(median_monthly_local) OVER () = MAX(median_monthly_local) OVER ()
               THEN 0.5
               ELSE 0.95 - 0.90 * PERCENT_RANK() OVER (ORDER BY median_monthly_local)
           END AS s_low_housing_cost
    FROM filtered
    WHERE median_monthly_local IS NOT NULL
),
scored AS (
    SELECT
        f.*,
        CASE WHEN f.avg_max_c IS NULL THEN NULL ELSE
            0.70 * GREATEST(0.0, 1.0 - 0.10 * GREATEST(
                p_ideal_temp_min - f.avg_max_c,
                f.avg_max_c - p_ideal_temp_max,
                0.0
            ))
            + 0.30 * CASE
                WHEN f.avg_rainy_days_mo IS NOT NULL THEN
                    GREATEST(0.0, 1.0 - f.avg_rainy_days_mo / 20.0)
                WHEN f.avg_precip_mm IS NOT NULL THEN
                    GREATEST(0.0, 1.0 - f.avg_precip_mm / 180.0)
                ELSE 0.5
            END
        END AS s_climate,
        cr.s_culture,
        fr.s_food,
        tr.s_low_crowding,
        wr.s_walk,
        xr.s_transit,
        br.s_broadband,
        hr.s_low_housing_cost
    FROM filtered f
    LEFT JOIN culture_ranks cr   ON cr.destination_id = f.destination_id
    LEFT JOIN food_ranks fr      ON fr.destination_id = f.destination_id
    LEFT JOIN crowding_ranks tr  ON tr.destination_id = f.destination_id
    LEFT JOIN walk_ranks wr      ON wr.destination_id = f.destination_id
    LEFT JOIN transit_ranks xr   ON xr.destination_id = f.destination_id
    LEFT JOIN broadband_ranks br ON br.destination_id = f.destination_id
    LEFT JOIN housing_ranks hr   ON hr.destination_id = f.destination_id
),
weighted AS (
    SELECT
        s.*,
        (CASE WHEN s.s_climate          IS NOT NULL THEN COALESCE(p_w_climate, 0)      ELSE 0 END
       + CASE WHEN s.s_culture          IS NOT NULL THEN COALESCE(p_w_culture, 0)      ELSE 0 END
       + CASE WHEN s.s_food             IS NOT NULL THEN COALESCE(p_w_food, 0)         ELSE 0 END
       + CASE WHEN s.s_walk             IS NOT NULL THEN COALESCE(p_w_walk, 0)         ELSE 0 END
       + CASE WHEN s.s_transit          IS NOT NULL THEN COALESCE(p_w_transit, 0)      ELSE 0 END
       + CASE WHEN s.s_broadband        IS NOT NULL THEN COALESCE(p_w_broadband, 0)    ELSE 0 END
       + CASE WHEN s.s_low_crowding     IS NOT NULL THEN COALESCE(p_w_low_crowding, 0) ELSE 0 END
       + CASE WHEN s.s_low_housing_cost IS NOT NULL THEN COALESCE(p_w_housing, 0)      ELSE 0 END
        ) AS active_weight,
        (COALESCE(p_w_climate, 0) + COALESCE(p_w_culture, 0)
       + COALESCE(p_w_food, 0) + COALESCE(p_w_walk, 0)
       + COALESCE(p_w_transit, 0) + COALESCE(p_w_broadband, 0)
       + COALESCE(p_w_low_crowding, 0) + COALESCE(p_w_housing, 0)
        ) AS total_weight,
        (COALESCE(s.s_climate,          0.5) * COALESCE(p_w_climate, 0)
       + COALESCE(s.s_culture,          0.5) * COALESCE(p_w_culture, 0)
       + COALESCE(s.s_food,             0.5) * COALESCE(p_w_food, 0)
       + COALESCE(s.s_walk,             0.5) * COALESCE(p_w_walk, 0)
       + COALESCE(s.s_transit,          0.5) * COALESCE(p_w_transit, 0)
       + COALESCE(s.s_broadband,        0.5) * COALESCE(p_w_broadband, 0)
       + COALESCE(s.s_low_crowding,     0.5) * COALESCE(p_w_low_crowding, 0)
       + COALESCE(s.s_low_housing_cost, 0.5) * COALESCE(p_w_housing, 0)
        ) AS raw_weighted_sum
    FROM scored s
)
SELECT
    w.destination_id,
    w.name,
    w.country_iso2,
    w.population,
    w.iata_codes,
    (SELECT count(*) FROM filtered) - count(*) OVER () AS excluded_for_no_evidence,
    ROUND((w.raw_weighted_sum / NULLIF(w.total_weight, 0))::NUMERIC, 4)::DOUBLE PRECISION AS final_score,
    ROUND(COALESCE(w.active_weight / NULLIF(w.total_weight, 0), 0)::NUMERIC, 2)::DOUBLE PRECISION AS data_completeness,
    jsonb_strip_nulls(jsonb_build_object(
        'avg_max_c', ROUND(w.avg_max_c::NUMERIC, 1),
        'peak_month_max_c', ROUND(w.peak_month_max_c::NUMERIC, 1),
        'avg_precip_mm', ROUND(w.avg_precip_mm::NUMERIC, 1),
        'avg_rainy_days_mo', ROUND(w.avg_rainy_days_mo::NUMERIC, 1),
        'museum_culture_count_5km', w.museum_culture_count,
        'restaurant_count_5km', w.restaurant_count,
        'cuisine_entropy', ROUND(w.cuisine_entropy::NUMERIC, 3),
        'nights_per_resident', ROUND(w.nights_per_resident::NUMERIC, 2),
        'pedestrian_segments_2km', w.pedestrian_segments_2km,
        'total_road_segments_2km', w.total_road_segments_2km,
        'pedestrian_ratio_2km', ROUND(w.pedestrian_ratio_2km::NUMERIC, 3),
        'walkability_score', w.walkability_score_100,
        'walkability_centre_score', w.walkability_centre_score,
        'walkability_ranked_cities', w.walkability_ranked_cities,
        'walkability_percentile', ROUND(w.s_walk::NUMERIC, 3),
        'walkability_basis', CASE WHEN w.walkability_score_100 IS NOT NULL
            THEN 'How Walkable 0-100, 5 km area average; not a global score for every city'
        END,
        'rail_segments_5km', w.rail_segments_5km,
        'gtfs_active_feeds', w.gtfs_active_feeds,
        'transit_stops_5km', w.transit_stops_5km,
        'transit_score', ROUND(w.s_transit::NUMERIC, 3),
        'median_down_mbps', ROUND(w.median_down_mbps::NUMERIC, 1),
        'median_up_mbps', ROUND(w.median_up_mbps::NUMERIC, 1),
        'avg_latency_ms', ROUND(w.avg_latency_ms::NUMERIC, 1),
        'total_speed_tests', w.total_tests,
        'median_monthly_local', ROUND(w.median_monthly_local::NUMERIC, 0),
        'sample_listings', w.sample_listings
    )) AS metrics,
    jsonb_strip_nulls(jsonb_build_object(
        'climate',   NULLIF(jsonb_strip_nulls(jsonb_build_object('source', w.climate_source,   'period', w.climate_period)), '{{}}'::jsonb),
        'poi',       NULLIF(jsonb_strip_nulls(jsonb_build_object('source', w.poi_source,       'period', w.poi_period)), '{{}}'::jsonb),
        'crowding',  NULLIF(jsonb_strip_nulls(jsonb_build_object('source', w.crowding_source,  'period', w.crowding_period)), '{{}}'::jsonb),
        'mobility',  NULLIF(jsonb_strip_nulls(jsonb_build_object('source', w.mobility_source,  'period', w.mobility_period)), '{{}}'::jsonb),
        'transit_stops', NULLIF(jsonb_strip_nulls(jsonb_build_object('source', w.transit_stops_source, 'period', w.transit_stops_period)), '{{}}'::jsonb),
        'broadband', NULLIF(jsonb_strip_nulls(jsonb_build_object('source', w.broadband_source, 'period', w.broadband_period)), '{{}}'::jsonb),
        'housing',   NULLIF(jsonb_strip_nulls(jsonb_build_object('source', w.housing_source,   'period', w.housing_period)), '{{}}'::jsonb)
    )) AS provenance
FROM weighted w
WHERE w.active_weight > 0 AND w.total_weight > 0
ORDER BY final_score DESC, w.population DESC
LIMIT GREATEST(0, COALESCE(p_limit, 15));
$$;
"""

REQUIRED_COLUMNS = {
    "destinations": {
        "destination_id", "geoname_id", "name", "ascii_name", "country_iso2",
        "timezone", "centroid", "lat", "lon", "population", "nuts3_code",
        "iata_codes",
    },
    "climate_monthly": {
        "destination_id", "month", "median_temp_max_c", "median_temp_min_c",
        "precip_mm", "avg_rainy_days", "avg_hot_days_30c", "source",
        "observed_period",
    },
    "poi_metrics": {
        "destination_id", "radius_km", "museum_culture_count",
        "restaurant_count", "cuisine_entropy", "source", "observed_period",
    },
    "tourism_pressure": {
        "destination_id", "tourist_nights_annual", "nights_per_resident",
        "source", "observed_period",
    },
    "broadband_metrics": {
        "destination_id", "radius_km", "median_down_mbps", "median_up_mbps",
        "avg_latency_ms", "total_tests", "tile_count", "source",
        "observed_period", "updated_at",
    },
    "mobility_metrics": {
        "destination_id", "radius_km", "pedestrian_segments_2km",
        "total_road_segments_2km", "pedestrian_ratio_2km", "rail_segments_5km",
        "gtfs_active_feeds", "transit_stops_5km", "transit_stops_source",
        "transit_stops_period", "source", "observed_period", "updated_at",
    },
    "housing_monthly": {
        "destination_id", "median_monthly_local", "sample_listings", "source",
        "observed_period", "updated_at",
    },
}


def log(msg: str) -> None:
    print(msg, flush=True)


def timed(name: str):
    class _T:
        def __enter__(self):
            self.t0 = time.time()
            return self

        def __exit__(self, *exc):
            log(f"  ({name}: {time.time() - self.t0:.1f}s)")

    return _T()


def _is_transient_duckdb_error(exc: Exception) -> bool:
    """Retry transport failures, never SQL/binder/catalog/404 errors."""
    message = str(exc).lower()
    deterministic = (
        "404", "not found", "no files found", "binder error", "catalog error",
        "parser error", "conversion error", "does not exist", "referenced column",
        "401", "403", "syntax error", "out of memory", "permission denied",
        "failed to create directory",
    )
    if any(token in message for token in deterministic):
        return False
    return any(
        token in message
        for token in (
            "http", "could not resolve", "timeout", "timed out", "connection",
            "network", "i/o", "io error", "ssl", "tls", "dns", "429", "500",
            "502", "503", "504",
        )
    )


def _execute_with_retries(
    duck: duckdb.DuckDBPyConnection, sql: str, attempts: int = 3, base_wait: int = 5
) -> pd.DataFrame:
    """Retry long S3 scans; transient DNS/HTTP errors are common."""
    for attempt in range(1, attempts + 1):
        try:
            return duck.execute(sql).fetchdf()
        except duckdb.Error as exc:
            if attempt == attempts or not _is_transient_duckdb_error(exc):
                raise
            wait = base_wait * attempt
            log(f"  DuckDB/S3 error ({exc}); retrying in {wait}s "
                f"(attempt {attempt + 1}/{attempts}) ...")
            time.sleep(wait)
    raise AssertionError("unreachable")


def _duckdb_temp_dir() -> Path:
    """Pick a writable spill directory (DUCKDB_TEMP_DIR overrides the cache default)."""
    candidates = [CACHE_DIR / "duckdb_tmp", Path(tempfile.gettempdir()) / "travel_duckdb_tmp"]
    override = os.environ.get("DUCKDB_TEMP_DIR")
    if override:
        candidates.insert(0, Path(override))
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir())


def _duckdb_connect() -> duckdb.DuckDBPyConnection:
    duck = duckdb.connect(config={
        # The transportation scan is much larger than Places. 2 GiB OOMs on
        # Spain+Sweden; 8 GiB is the default, overridable for small hosts.
        "memory_limit": os.environ.get("DUCKDB_MEMORY_LIMIT", "12GiB"),
        "max_temp_directory_size": os.environ.get("DUCKDB_TEMP_LIMIT", "8GiB"),
        "threads": "2",
        "temp_directory": str(_duckdb_temp_dir()),
        "preserve_insertion_order": "false",
    })
    duck.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    # SQL-side haversine so the grid join can stay inside DuckDB.
    duck.execute(
        """
        CREATE OR REPLACE MACRO haversine_km(lat1, lon1, lat2, lon2) AS
            2 * 6371.0088 * asin(least(1.0, sqrt(
                pow(sin(radians(lat2 - lat1) / 2), 2)
                + cos(radians(lat1)) * cos(radians(lat2))
                  * pow(sin(radians(lon2 - lon1) / 2), 2)
            )));
        """
    )
    try:
        # Fewer concurrent S3 clients make the resolver flakier, not better.
        duck.execute("SET http_timeout=120;")
        duck.execute("SET http_retries=3;")
        duck.execute("SET http_retry_wait_ms=2000;")
        duck.execute("SET http_retry_backoff=2;")
    except duckdb.Error:
        pass
    return duck


def download_cached(url: str, dest: Path, retries: int = 3) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        try:
            log(f"  downloading {url} -> {dest.name} (attempt {attempt}/{retries}) ...")
            with requests.get(url, stream=True, timeout=(30, 300)) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            tmp.replace(dest)
            return dest
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            if attempt == retries:
                raise
            log(f"  download failed ({exc}); retrying ...")
            time.sleep(2 * attempt)
    return dest


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _scan_envelopes(
    cities_df: pd.DataFrame,
    pad: float = 0.25,
    max_countries: int = 40,
    radius_km: float | None = None,
) -> list[tuple[float, float, float, float]]:
    """
    Return (min_lon, max_lon, min_lat, max_lat) envelopes to scan.

    One envelope per country lets each Parquet scan prune to its own row
    groups. A single OR-of-envelopes filter would be correct but blocks
    DuckDB's row-group pruning and turns every --countries run into a global
    scan. Large country sets are grouped into 45-degree longitude bands instead
    of falling back to one world scan. Countries whose cities straddle the
    antimeridian fall back to the whole world (correct, rare).

    When ``radius_km`` is supplied, the pad is enlarged for the requested
    radius, including the longitude compression at high latitude.
    """
    if cities_df.empty:
        return []

    if radius_km is not None:
        max_abs_lat = float(cities_df["lat"].abs().max())
        lat_pad = max(pad, float(radius_km) / 111.0 + 0.05)
        lon_pad = max(
            pad,
            float(radius_km)
            / (111.0 * max(0.05, math.cos(math.radians(max_abs_lat))))
            + 0.05,
        )
    else:
        lat_pad = lon_pad = pad

    if cities_df["country_iso2"].nunique() > 100:
        # A true global run is faster as one world scan than as many small
        # scans; the Parquet predicate is the same and metadata is read once.
        return [(-180.0, 180.0, -90.0, 90.0)]

    if cities_df["country_iso2"].nunique() > max_countries:
        # Group into 45-degree longitude bands: global runs still prune, and a
        # 41-country European run does not degenerate into a world scan.
        bands = ((cities_df["lon"] + 180.0) // 45.0).astype(int).clip(0, 7)
        grouped = (
            cities_df.assign(_band=bands)
            .groupby("_band", dropna=False)
            .agg(
                min_lat=("lat", "min"), max_lat=("lat", "max"),
                min_lon=("lon", "min"), max_lon=("lon", "max"),
            )
        )
    else:
        grouped = cities_df.groupby("country_iso2", dropna=False).agg(
            min_lat=("lat", "min"), max_lat=("lat", "max"),
            min_lon=("lon", "min"), max_lon=("lon", "max"),
        )

    envelopes = []
    for row in grouped.itertuples(index=False):
        min_lon, max_lon = float(row.min_lon), float(row.max_lon)
        min_lat = max(-90.0, float(row.min_lat) - lat_pad)
        max_lat = min(90.0, float(row.max_lat) + lat_pad)
        if max_lon - min_lon > 180.0:
            envelopes.append((-180.0, 180.0, min_lat, max_lat))
            continue

        left = max(-180.0, min_lon - lon_pad)
        right = min(180.0, max_lon + lon_pad)
        envelopes.append((left, right, min_lat, max_lat))

        # A city just west of +180 can have neighbours just east of -180.
        # Add the opposite edge band so a 1-2 km dateline crossing is not lost.
        if right >= 180.0 - 1e-9 and left > -180.0:
            envelopes.append((
                -180.0, min(180.0, -180.0 + lon_pad), min_lat, max_lat,
            ))
        if left <= -180.0 + 1e-9 and right < 180.0:
            envelopes.append((
                max(-180.0, 180.0 - lon_pad), 180.0, min_lat, max_lat,
            ))
    return envelopes


def _cities_for_scope(
    conn: psycopg.Connection, min_pop: int, countries: list[str] | None
) -> pd.DataFrame:
    sql = """
        SELECT destination_id, name, country_iso2, lat, lon, population, iata_codes
        FROM destinations
        WHERE population >= %s
    """
    params: list = [min_pop]
    if countries:
        sql += " AND country_iso2 = ANY(%s)"
        params.append(countries)
    sql += " ORDER BY destination_id"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


def _register_city_tables(
    duck: duckdb.DuckDBPyConnection, cities_df: pd.DataFrame, halo: int = 3
) -> None:
    """Create `cities` and a `city_cells` halo table in DuckDB."""
    duck.register("cities_df", cities_df[["destination_id", "lat", "lon"]])
    duck.execute(
        """
        CREATE OR REPLACE TEMP TABLE cities AS
        SELECT destination_id, CAST(lat AS DOUBLE) AS lat, CAST(lon AS DOUBLE) AS lon,
               CAST(FLOOR(lon * 10) AS INT) AS gx,
               CAST(FLOOR(lat * 10) AS INT) AS gy
        FROM cities_df;
        """
    )
    # 0.1 degree cells. The default +/-3 cells is >5 km at every latitude up
    # to 78N. Airbnb matching uses a wider halo because its radius is 10 km.
    # Longitude cells wrap at the antimeridian.
    duck.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE city_cells AS
        SELECT
            c.destination_id,
            c.lat,
            c.lon,
            ((c.gx + dx) % 3600 + 3600) % 3600 AS gx,
            c.gy + dy AS gy
        FROM cities c
        CROSS JOIN range(-{int(halo)}, {int(halo) + 1}) AS rx(dx)
        CROSS JOIN range(-{int(halo)}, {int(halo) + 1}) AS ry(dy);
        """
    )


def _upsert_dataframe(
    conn: psycopg.Connection,
    df: pd.DataFrame,
    table: str,
    columns: list[str],
    conflict_columns: list[str],
    update_sql: str,
) -> int:
    """COPY a DataFrame into a temp table, then upsert into the target table."""
    if df.empty:
        return 0
    tmp = f"tmp_{table}"
    known_columns = set().union(*REQUIRED_COLUMNS.values())
    unknown = set(columns) - known_columns
    if unknown:
        raise ValueError(
            f"Refusing to upsert unknown columns into {table}: "
            + ", ".join(sorted(unknown))
        )
    coldefs = ", ".join(f"{c} {_pg_type(c)}" for c in columns)
    out = df[columns].copy()
    int_cols = {c for c in columns if _pg_type(c) in {"INT", "BIGINT"}}
    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE {tmp} ({coldefs}) ON COMMIT DROP;")
        with cur.copy(f"COPY {tmp} ({', '.join(columns)}) FROM STDIN") as copy:
            for raw in out.itertuples(index=False):
                row = []
                for col, v in zip(columns, raw):
                    if v is None:
                        row.append(None)
                        continue
                    try:
                        if pd.isna(v):
                            row.append(None)
                            continue
                    except (TypeError, ValueError):
                        pass
                    try:
                        if col in int_cols:
                            row.append(int(v))
                        elif _pg_type(col) == "DOUBLE PRECISION":
                            row.append(float(v))
                        else:
                            row.append(v)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"Invalid value for {table}.{col}: {v!r} "
                            f"(expected {_pg_type(col)})"
                        ) from exc
                copy.write_row(tuple(row))
        cur.execute(
            f"""
            INSERT INTO {table} ({", ".join(columns)})
            SELECT {", ".join(columns)} FROM {tmp}
            ON CONFLICT ({", ".join(conflict_columns)}) DO UPDATE SET
                {update_sql};
            """
        )
        rowcount = cur.rowcount
    conn.commit()
    return rowcount


def _pg_type(column: str) -> str:
    if column in {"total_tests"}:
        return "BIGINT"
    if column in {
        "radius_km", "median_down_mbps", "median_up_mbps", "avg_latency_ms",
        "pedestrian_ratio_2km", "median_monthly_local",
    }:
        return "DOUBLE PRECISION"
    if column in {
        "tile_count", "pedestrian_segments_2km", "total_road_segments_2km",
        "rail_segments_5km", "gtfs_active_feeds", "sample_listings",
        "transit_stops_5km",
    }:
        return "INT"
    return "TEXT"


def verify_phase3(conn: psycopg.Connection) -> None:
    """Read-only schema check for --query-only / read-only replicas."""
    with conn.cursor() as cur:
        for table in REQUIRED_COLUMNS:
            cur.execute("SELECT to_regclass(%s) AS relation", (f"public.{table}",))
            if cur.fetchone()[0] is None:
                raise SystemExit(
                    f"Phase 3 table {table} is missing. Run bootstrap_phase3.py "
                    "with write access first, then retry --query-only."
                )
        cur.execute(
            "SELECT 1 FROM pg_proc WHERE proname = 'rank_destinations_v3' LIMIT 1"
        )
        if cur.fetchone() is None:
            raise SystemExit(
                "rank_destinations_v3() is missing. Run bootstrap_phase3.py "
                "with write access first."
            )


def init_phase3(conn: psycopg.Connection) -> None:
    log("[1/5] Applying Phase 3 schema and rank_destinations_v3 ...")
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.destinations') AS destinations")
        if cur.fetchone()[0] is None:
            raise SystemExit(
                "The destinations table does not exist. Run Phase 1 "
                "(bootstrap_global_40k.py) first, then re-run Phase 3."
            )
        try:
            cur.execute(PHASE3_SCHEMA_SQL)
        except psycopg.errors.UndefinedFile as exc:
            raise SystemExit(
                "PostGIS is not available on this database. Enable the postgis "
                "extension in Supabase and re-run."
            ) from exc
        # Phase 3 columns added after the first release.
        cur.execute(
            """
            ALTER TABLE mobility_metrics
                ADD COLUMN IF NOT EXISTS transit_stops_5km INT,
                ADD COLUMN IF NOT EXISTS transit_stops_source TEXT,
                ADD COLUMN IF NOT EXISTS transit_stops_period TEXT
            """
        )
        # Upgrade the Phase 3 draft's USD-labelled housing column. Inside
        # Airbnb prices are local-currency, so the honest name is *_local.
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'housing_monthly'
            """
        )
        housing_columns = {row[0] for row in cur.fetchall()}
        if (
            "median_monthly_local" not in housing_columns
            and "median_monthly_usd" in housing_columns
        ):
            cur.execute(
                "ALTER TABLE housing_monthly "
                "RENAME COLUMN median_monthly_usd TO median_monthly_local"
            )
        cur.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY(%s)
            """,
            (list(REQUIRED_COLUMNS),),
        )
        found: dict[str, set[str]] = defaultdict(set)
        for table, column in cur.fetchall():
            found[table].add(column)
    conn.commit()

    problems = []
    for table, required in REQUIRED_COLUMNS.items():
        missing = required - found.get(table, set())
        if missing:
            problems.append(f"  {table}: missing {', '.join(sorted(missing))}")
    if problems:
        raise SystemExit(
            "Existing tables are not compatible with the Phase 3 schema:\n"
            + "\n".join(problems)
            + "\nRun Phase 1 on a fresh database, or migrate the tables first."
        )

    with conn.cursor() as cur:
        cur.execute(PHASE3_SCORER_SQL)
    conn.commit()


def ingest_ookla(
    conn: psycopg.Connection,
    cities_df: pd.DataFrame,
    url: str,
    year: int,
    quarter: int,
    radius_km: float = 5.0,
) -> None:
    log(f"[2/5] Ookla fixed broadband within {radius_km:.0f} km ({year} Q{quarter}) ...")
    duck = _duckdb_connect()
    try:
        _register_city_tables(duck, cities_df)
        envelopes = _scan_envelopes(cities_df, pad=0.15, radius_km=radius_km)
        scans = []
        for min_lon, max_lon, min_lat, max_lat in envelopes:
            scans.append(f"""
                SELECT
                    quadkey, tile_x, tile_y,
                    ((CAST(FLOOR(tile_x * 10) AS INT) % 3600) + 3600) % 3600 AS gx,
                    CAST(FLOOR(tile_y * 10) AS INT) AS gy,
                    avg_d_kbps, avg_u_kbps, avg_lat_ms, tests
                FROM read_parquet('{url}')
                WHERE tile_x BETWEEN {min_lon!r} AND {max_lon!r}
                  AND tile_y BETWEEN {min_lat!r} AND {max_lat!r}
                  AND tests > 0
                  AND avg_d_kbps > 0
                  AND avg_u_kbps > 0
            """)
        tiles_sql = "\nUNION ALL\n".join(scans)
        sql = f"""
        WITH tiles AS (
            {tiles_sql}
        ),
        joined AS (
            SELECT DISTINCT
                c.destination_id,
                t.quadkey,
                t.avg_d_kbps / 1000.0 AS d_mbps,
                t.avg_u_kbps / 1000.0 AS u_mbps,
                t.avg_lat_ms,
                t.tests,
                haversine_km(c.lat, c.lon, t.tile_y, t.tile_x) AS dist_km
            FROM tiles t
            JOIN city_cells cc ON cc.gx = t.gx AND cc.gy = t.gy
            JOIN cities c ON c.destination_id = cc.destination_id
        ),
        nearby AS (
            SELECT * FROM joined WHERE dist_km <= {radius_km!r}
        ),
        down_ordered AS (
            SELECT *,
                   SUM(tests) OVER (
                       PARTITION BY destination_id ORDER BY d_mbps
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cum_tests,
                   SUM(tests) OVER (PARTITION BY destination_id) AS total_tests
            FROM nearby
        ),
        up_ordered AS (
            SELECT *,
                   SUM(tests) OVER (
                       PARTITION BY destination_id ORDER BY u_mbps
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cum_tests,
                   SUM(tests) OVER (PARTITION BY destination_id) AS total_tests
            FROM nearby
        ),
        med_down AS (
            SELECT destination_id, MIN(d_mbps) AS median_down_mbps
            FROM down_ordered
            WHERE cum_tests >= total_tests / 2.0
            GROUP BY destination_id
        ),
        med_up AS (
            SELECT destination_id, MIN(u_mbps) AS median_up_mbps
            FROM up_ordered
            WHERE cum_tests >= total_tests / 2.0
            GROUP BY destination_id
        ),
        agg AS (
            SELECT
                destination_id,
                SUM(tests) AS total_tests,
                COUNT(*) AS tile_count,
                SUM(avg_lat_ms * tests) FILTER (WHERE avg_lat_ms IS NOT NULL)
                  / NULLIF(SUM(tests) FILTER (WHERE avg_lat_ms IS NOT NULL), 0)
                  AS avg_latency_ms
            FROM nearby
            GROUP BY destination_id
        )
        SELECT
            a.destination_id,
            CAST({radius_km!r} AS DOUBLE) AS radius_km,
            d.median_down_mbps,
            u.median_up_mbps,
            a.avg_latency_ms,
            a.total_tests,
            a.tile_count,
            'Ookla Open Data (fixed)' AS source,
            '{year} Q{quarter}' AS observed_period
        FROM agg a
        LEFT JOIN med_down d ON d.destination_id = a.destination_id
        LEFT JOIN med_up u ON u.destination_id = a.destination_id
        """
        df = _execute_with_retries(duck, sql)
    finally:
        duck.close()

    scope_ids = cities_df["destination_id"].tolist()
    with conn.cursor() as cur:
        # The scan is authoritative for this scope: drop stale rows first so a
        # city that lost coverage does not keep an old broadband median.
        cur.execute(
            "DELETE FROM broadband_metrics WHERE destination_id = ANY(%s)",
            (scope_ids,),
        )
    if df.empty:
        conn.commit()
        n = 0
    else:
        n = _upsert_dataframe(
            conn, df, "broadband_metrics",
            ["destination_id", "radius_km", "median_down_mbps", "median_up_mbps",
             "avg_latency_ms", "total_tests", "tile_count", "source", "observed_period"],
            ["destination_id"],
            """
            radius_km = EXCLUDED.radius_km,
            median_down_mbps = EXCLUDED.median_down_mbps,
            median_up_mbps = EXCLUDED.median_up_mbps,
            avg_latency_ms = EXCLUDED.avg_latency_ms,
            total_tests = EXCLUDED.total_tests,
            tile_count = EXCLUDED.tile_count,
            source = EXCLUDED.source,
            observed_period = EXCLUDED.observed_period,
            updated_at = NOW()
            """,
        )
    log(f"  upserted broadband for {n:,} cities ({len(cities_df):,} in scope).")


def ingest_overture_transport(
    conn: psycopg.Connection,
    cities_df: pd.DataFrame,
    release: str,
    radius_km: float = 5.0,
    walk_radius_km: float = 2.0,
) -> None:
    log(f"[3/5] Overture Maps Transportation ({release}) walk/rail scan ...")
    s3 = f"{OVERTURE_S3_ROOT}/{release}/theme=transportation/type=segment/*"
    duck = _duckdb_connect()
    try:
        _register_city_tables(duck, cities_df)
        envelopes = _scan_envelopes(cities_df, pad=0.15, radius_km=radius_km)
        ped_list = ", ".join(f"'{c}'" for c in PEDESTRIAN_CLASSES)
        scans = []
        for min_lon, max_lon, min_lat, max_lat in envelopes:
            scans.append(f"""
                SELECT
                    id, subtype, class,
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
                  AND subtype IN ('road', 'rail')
            """)
        segments_sql = "\nUNION ALL\n".join(scans)
        sql = f"""
        WITH segments AS (
            {segments_sql}
        ),
        joined AS (
            SELECT DISTINCT
                c.destination_id,
                s.id,
                s.subtype,
                s.class,
                haversine_km(c.lat, c.lon, s.lat, s.lon) AS dist_km
            FROM segments s
            JOIN city_cells cc ON cc.gx = s.gx AND cc.gy = s.gy
            JOIN cities c ON c.destination_id = cc.destination_id
        ),
        agg AS (
            SELECT
                destination_id,
                COUNT(*) FILTER (
                    WHERE subtype = 'road' AND dist_km <= {walk_radius_km!r}
                ) AS total_road_segments_2km,
                COUNT(*) FILTER (
                    WHERE subtype = 'road' AND dist_km <= {walk_radius_km!r}
                      AND class IN ({ped_list})
                ) AS pedestrian_segments_2km,
                COUNT(*) FILTER (
                    WHERE subtype = 'rail' AND dist_km <= {radius_km!r}
                ) AS rail_segments_5km
            FROM joined
            GROUP BY destination_id
        )
        SELECT
            destination_id,
            CAST({radius_km!r} AS DOUBLE) AS radius_km,
            pedestrian_segments_2km,
            total_road_segments_2km,
            CASE WHEN total_road_segments_2km > 0
                 THEN pedestrian_segments_2km::DOUBLE / total_road_segments_2km
                 ELSE NULL END AS pedestrian_ratio_2km,
            rail_segments_5km,
            'Overture Maps Transportation' AS source,
            'overture-{release}' AS observed_period
        FROM agg
        """
        df = _execute_with_retries(duck, sql)
    finally:
        duck.close()

    scope_ids = cities_df["destination_id"].tolist()
    with conn.cursor() as cur:
        # Reset only the Overture-owned columns; GTFS coverage is preserved.
        cur.execute(
            """
            UPDATE mobility_metrics
            SET pedestrian_segments_2km = NULL,
                total_road_segments_2km = NULL,
                pedestrian_ratio_2km = NULL,
                rail_segments_5km = NULL,
                source = CASE WHEN gtfs_active_feeds IS NOT NULL
                              THEN 'Mobility Database GTFS'
                              WHEN transit_stops_5km IS NOT NULL
                              THEN COALESCE(transit_stops_source, source)
                              ELSE source END,
                observed_period = CASE WHEN gtfs_active_feeds IS NOT NULL
                    THEN COALESCE(NULLIF(trim(both '; ' from regexp_replace(
                        observed_period, 'overture-[^;]+;?[[:space:]]*', '', 'g'
                    )), ''), 'mobility coverage retained')
                    WHEN transit_stops_5km IS NOT NULL
                    THEN COALESCE(transit_stops_period, observed_period)
                    ELSE observed_period END
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
            ["destination_id", "radius_km", "pedestrian_segments_2km",
             "total_road_segments_2km", "pedestrian_ratio_2km", "rail_segments_5km",
             "source", "observed_period"],
            ["destination_id"],
            """
            radius_km = EXCLUDED.radius_km,
            pedestrian_segments_2km = EXCLUDED.pedestrian_segments_2km,
            total_road_segments_2km = EXCLUDED.total_road_segments_2km,
            pedestrian_ratio_2km = EXCLUDED.pedestrian_ratio_2km,
            rail_segments_5km = EXCLUDED.rail_segments_5km,
            source = CASE WHEN mobility_metrics.gtfs_active_feeds IS NOT NULL
                          THEN 'Overture Maps Transportation + Mobility Database GTFS'
                          ELSE EXCLUDED.source END,
            observed_period = CASE
                WHEN mobility_metrics.observed_period ~ 'overture-[^;]+'
                THEN regexp_replace(
                    mobility_metrics.observed_period,
                    'overture-[^;]+',
                    EXCLUDED.observed_period
                )
                WHEN mobility_metrics.observed_period ~ 'mobility-'
                THEN EXCLUDED.observed_period || '; ' || mobility_metrics.observed_period
                ELSE EXCLUDED.observed_period
            END,
            updated_at = NOW()
            """,
        )
    log(f"  upserted walk/rail metrics for {n:,} cities.")


def ingest_gtfs(conn: psycopg.Connection, cities_df: pd.DataFrame) -> None:
    log("[4/5] Mobility Database GTFS active-feed coverage ...")
    csv_path = download_cached(MOBILITY_DB_URL, CACHE_DIR / "feeds_v2.csv")
    feeds = pd.read_csv(csv_path, low_memory=False)

    required = {
        "id", "data_type", "status", "redirect.id",
        "location.bounding_box.minimum_latitude",
        "location.bounding_box.maximum_latitude",
        "location.bounding_box.minimum_longitude",
        "location.bounding_box.maximum_longitude",
    }
    missing = required - set(feeds.columns)
    if missing:
        raise SystemExit(
            "Mobility Database feeds_v2.csv is missing expected columns: "
            + ", ".join(sorted(missing))
        )

    feeds = feeds[
        (feeds["data_type"] == "gtfs")
        & (feeds["status"] == "active")
        & (feeds["redirect.id"].isna())
    ].copy()
    bbox_cols = [
        "location.bounding_box.minimum_latitude",
        "location.bounding_box.maximum_latitude",
        "location.bounding_box.minimum_longitude",
        "location.bounding_box.maximum_longitude",
    ]
    for col in bbox_cols:
        feeds[col] = pd.to_numeric(feeds[col], errors="coerce")
    feeds = feeds.dropna(subset=bbox_cols)
    feeds = feeds.rename(columns={
        bbox_cols[0]: "min_lat", bbox_cols[1]: "max_lat",
        bbox_cols[2]: "min_lon", bbox_cols[3]: "max_lon",
    })
    feeds = feeds[
        (feeds["max_lat"] >= feeds["min_lat"])
        & (feeds["max_lon"] >= feeds["min_lon"])
        & ((feeds["max_lat"] - feeds["min_lat"]) <= 15.0)
        & ((feeds["max_lon"] - feeds["min_lon"]) <= 15.0)
    ].drop_duplicates("id")
    log(f"  {len(feeds):,} active, non-superseded GTFS feeds with usable bboxes.")

    if feeds.empty:
        log("  no usable GTFS feeds; skipping.")
        return

    duck = _duckdb_connect()
    try:
        duck.register("feeds_df", feeds[["id", "min_lat", "max_lat", "min_lon", "max_lon"]])
        duck.register("cities_df2", cities_df[["destination_id", "lat", "lon"]])
        sql = """
        SELECT c.destination_id, COUNT(DISTINCT f.id) AS gtfs_active_feeds
        FROM cities_df2 c
        JOIN feeds_df f
          ON c.lat BETWEEN f.min_lat AND f.max_lat
         AND c.lon BETWEEN f.min_lon AND f.max_lon
        GROUP BY c.destination_id
        """
        df = duck.execute(sql).fetchdf()
    finally:
        duck.close()

    period = f"mobility-{datetime.now(timezone.utc):%Y-%m}"
    df["source"] = "Mobility Database GTFS"
    df["observed_period"] = period
    scope_ids = cities_df["destination_id"].tolist()
    with conn.cursor() as cur:
        # Reset only GTFS coverage; Overture walk/rail columns are preserved.
        cur.execute(
            """
            UPDATE mobility_metrics
            SET gtfs_active_feeds = NULL,
                source = CASE
                    WHEN pedestrian_segments_2km IS NOT NULL
                      OR total_road_segments_2km IS NOT NULL
                      OR rail_segments_5km IS NOT NULL
                    THEN 'Overture Maps Transportation'
                    WHEN transit_stops_5km IS NOT NULL
                    THEN COALESCE(transit_stops_source, source)
                    ELSE source
                END,
                observed_period = CASE
                    WHEN pedestrian_segments_2km IS NOT NULL
                      OR total_road_segments_2km IS NOT NULL
                      OR rail_segments_5km IS NOT NULL
                    THEN COALESCE(NULLIF(trim(both '; ' from regexp_replace(
                        observed_period, 'mobility-[0-9]{4}-[0-9]{2};?[[:space:]]*', '', 'g'
                    )), ''), 'overture coverage retained')
                    WHEN transit_stops_5km IS NOT NULL
                    THEN COALESCE(transit_stops_period, observed_period)
                    ELSE observed_period
                END
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
            ["destination_id", "gtfs_active_feeds", "source", "observed_period"],
            ["destination_id"],
            """
            gtfs_active_feeds = EXCLUDED.gtfs_active_feeds,
            source = CASE WHEN mobility_metrics.source LIKE '%Overture Maps Transportation%'
                          THEN 'Overture Maps Transportation + Mobility Database GTFS'
                          ELSE EXCLUDED.source END,
            observed_period = CASE
                WHEN mobility_metrics.observed_period ~ 'mobility-[0-9]{4}-[0-9]{2}'
                THEN regexp_replace(
                    mobility_metrics.observed_period,
                    'mobility-[0-9]{4}-[0-9]{2}',
                    EXCLUDED.observed_period
                )
                WHEN mobility_metrics.source LIKE '%Overture Maps Transportation%'
                     AND mobility_metrics.observed_period ~ 'overture-'
                THEN mobility_metrics.observed_period || '; ' || EXCLUDED.observed_period
                ELSE EXCLUDED.observed_period
            END,
            updated_at = NOW()
            """,
        )
    log(f"  upserted GTFS coverage for {n:,} cities.")


def ingest_inside_airbnb(
    conn: psycopg.Connection,
    cities_df: pd.DataFrame,
    airbnb_dir: Path,
    room_type: str | None = "Entire home/apt",
    radius_km: float = 10.0,
) -> None:
    log(f"[5/5] Optional Inside Airbnb listings from {airbnb_dir} ...")
    if not airbnb_dir.exists():
        log("  directory does not exist; skipping housing.")
        return
    glob = str((airbnb_dir / "**" / "listings.csv.gz").resolve())
    if not list(airbnb_dir.glob("**/listings.csv.gz")):
        log("  no **/listings.csv.gz files found; skipping housing.")
        return

    duck = _duckdb_connect()
    try:
        _register_city_tables(duck, cities_df, halo=5)
        room_filter = ""
        params: list = []
        if room_type:
            room_filter = "AND room_type = ?"
            params.append(room_type)
        sql = f"""
        WITH listings AS (
            SELECT
                filename,
                id,
                TRY_CAST(latitude AS DOUBLE) AS lat,
                TRY_CAST(longitude AS DOUBLE) AS lon,
                TRY_CAST(regexp_replace(CAST(price AS VARCHAR), '[^0-9.]', '', 'g') AS DOUBLE) AS price,
                TRY_CAST(minimum_nights AS INT) AS min_nights,
                TRY_CAST(maximum_nights AS INT) AS max_nights,
                room_type,
                ((CAST(FLOOR(TRY_CAST(longitude AS DOUBLE) * 10) AS INT)
                    % 3600) + 3600) % 3600 AS gx,
                CAST(FLOOR(TRY_CAST(latitude AS DOUBLE) * 10) AS INT) AS gy
            FROM read_csv_auto('{glob}', union_by_name=true, header=true, filename=true)
            WHERE TRY_CAST(latitude AS DOUBLE) IS NOT NULL
              AND TRY_CAST(longitude AS DOUBLE) IS NOT NULL
              {room_filter}
        ),
        usable AS (
            SELECT * FROM listings
            WHERE price > 0
              AND min_nights IS NOT NULL AND min_nights <= 30
              AND (max_nights IS NULL OR max_nights = 0 OR max_nights >= 30)
        ),
        joined AS (
            SELECT
                u.filename, u.id, u.price,
                c.destination_id,
                haversine_km(c.lat, c.lon, u.lat, u.lon) AS dist_km
            FROM usable u
            JOIN city_cells cc ON cc.gx = u.gx AND cc.gy = u.gy
            JOIN cities c ON c.destination_id = cc.destination_id
        ),
        matched AS (
            SELECT * FROM joined
            WHERE dist_km <= {radius_km!r}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY filename, id ORDER BY dist_km
            ) = 1
        )
        SELECT
            destination_id,
            median(price * 30.0) AS median_monthly_local,
            COUNT(*) AS sample_listings,
            'Inside Airbnb (local currency, 30+ night listings)' AS source,
            'inside-airbnb-{datetime.now(timezone.utc):%Y-%m}' AS observed_period
        FROM matched
        GROUP BY destination_id
        """
        df = duck.execute(sql, params).fetchdf() if params else duck.execute(sql).fetchdf()
    finally:
        duck.close()

    scope_ids = cities_df["destination_id"].tolist()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM housing_monthly WHERE destination_id = ANY(%s)",
            (scope_ids,),
        )
    if df.empty:
        conn.commit()
        n = 0
    else:
        n = _upsert_dataframe(
            conn, df, "housing_monthly",
            ["destination_id", "median_monthly_local", "sample_listings", "source", "observed_period"],
            ["destination_id"],
            """
            median_monthly_local = EXCLUDED.median_monthly_local,
            sample_listings = EXCLUDED.sample_listings,
            source = EXCLUDED.source,
            observed_period = EXCLUDED.observed_period,
            updated_at = NOW()
            """,
        )
    log(f"  upserted housing medians for {n:,} cities.")


def print_coverage(
    conn: psycopg.Connection, countries: list[str] | None = None
) -> None:
    scope = "(%s::TEXT[] IS NULL OR d.country_iso2 = ANY(%s))"
    queries = {
        "destinations": f"SELECT COUNT(*) FROM destinations d WHERE {scope}",
        "climate": (
            "SELECT COUNT(*) FROM climate_monthly c "
            f"JOIN destinations d USING (destination_id) WHERE {scope}"
        ),
        "poi": (
            "SELECT COUNT(*) FROM poi_metrics p "
            f"JOIN destinations d USING (destination_id) WHERE {scope}"
        ),
        "broadband": (
            "SELECT COUNT(*) FROM broadband_metrics b "
            f"JOIN destinations d USING (destination_id) WHERE {scope}"
        ),
        "mobility": (
            "SELECT COUNT(*) FROM mobility_metrics m "
            f"JOIN destinations d USING (destination_id) WHERE {scope}"
        ),
        "gtfs": (
            "SELECT COUNT(*) FROM mobility_metrics m "
            f"JOIN destinations d USING (destination_id) WHERE {scope} "
            "AND m.gtfs_active_feeds > 0"
        ),
        "transit_stops": (
            "SELECT COUNT(*) FROM mobility_metrics m "
            f"JOIN destinations d USING (destination_id) WHERE {scope} "
            "AND m.transit_stops_5km IS NOT NULL"
        ),
        "tourism": (
            "SELECT COUNT(*) FROM tourism_pressure t "
            f"JOIN destinations d USING (destination_id) WHERE {scope}"
        ),
        "housing": (
            "SELECT COUNT(*) FROM housing_monthly h "
            f"JOIN destinations d USING (destination_id) WHERE {scope}"
        ),
    }
    log("\nCoverage:")
    with conn.cursor() as cur:
        for label, sql in queries.items():
            cur.execute(sql, (countries, countries))
            log(f"  {label:12s} {cur.fetchone()[0]:>9,}")

    log("\nTop 10 (June, 20-26C ideal, default v3 weights):")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT name, country_iso2, population, final_score,
                   ROUND(data_completeness::NUMERIC, 2),
                   metrics->>'median_down_mbps',
                   metrics->>'pedestrian_ratio_2km',
                   metrics->>'gtfs_active_feeds'
            FROM rank_destinations_v3(
                p_months => ARRAY[6],
                p_ideal_temp_min => 20.0,
                p_ideal_temp_max => 26.0,
                p_countries => %s::TEXT[],
                p_limit => 500
            ) r
            WHERE r.metrics ? 'median_down_mbps'
            LIMIT 10;
            """,
            (countries,),
        )
        for row in cur.fetchall():
            log(f"  {row[0]}, {row[1]}  pop={row[2]:,}  score={row[3]:.3f} "
                f"complete={row[4]}  down={row[5]}  walk={row[6]}  gtfs={row[7]}")


def connect_db(db_url: str) -> psycopg.Connection:
    kwargs: dict = {
        "connect_timeout": 30,
        "application_name": "bootstrap_phase3",
    }
    if "pooler.supabase.com" in db_url or ":6543" in db_url:
        kwargs["prepare_threshold"] = None
    conn = psycopg.connect(db_url, **kwargs)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute("SET statement_timeout = 0")
    return conn


def main() -> None:
    global CACHE_DIR
    parser = argparse.ArgumentParser(description="Phase 3 enrichment for the travel DB")
    parser.add_argument("--min-pop", type=int, default=40000)
    parser.add_argument("--countries", default=None,
                        help="Comma-separated ISO-2 codes, e.g. SE,ES")
    parser.add_argument("--db-url", default=None, help="Overrides SUPABASE_DB_URL")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--query-only", action="store_true")
    parser.add_argument("--skip-ookla", action="store_true")
    parser.add_argument("--skip-overture-transport", action="store_true")
    parser.add_argument("--skip-gtfs", action="store_true")
    parser.add_argument("--skip-airbnb", action="store_true")
    parser.add_argument("--ookla-year", type=int, default=DEFAULT_OOKLA_YEAR)
    parser.add_argument("--ookla-quarter", type=int, default=DEFAULT_OOKLA_QUARTER)
    parser.add_argument("--ookla-km", type=float, default=5.0)
    parser.add_argument("--overture-release", default=DEFAULT_OVERTURE_RELEASE)
    parser.add_argument("--airbnb-dir", type=Path, default=None)
    parser.add_argument("--airbnb-room-type", default="Entire home/apt",
                        help="Room type filter; pass '' for all types")
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
    if not 1 <= args.ookla_quarter <= 4:
        raise SystemExit("--ookla-quarter must be 1..4")

    with connect_db(db_url) as conn:
        if args.query_only:
            verify_phase3(conn)
            print_coverage(conn, countries)
            return

        init_phase3(conn)
        cities_df = _cities_for_scope(conn, args.min_pop, countries)
        if cities_df.empty:
            raise SystemExit(
                "No destinations in scope. Run Phase 1 first, or widen --countries/--min-pop."
            )
        log(f"  {len(cities_df):,} destinations in scope.")

        if not args.skip_ookla:
            month = (args.ookla_quarter - 1) * 3 + 1
            url = OOKLA_URL_TEMPLATE.format(
                year=args.ookla_year, quarter=args.ookla_quarter, month=f"{month:02d}"
            )
            ingest_ookla(conn, cities_df, url, args.ookla_year, args.ookla_quarter,
                         radius_km=args.ookla_km)
        else:
            log("[2/5] Ookla skipped.")

        if not args.skip_overture_transport:
            ingest_overture_transport(conn, cities_df, args.overture_release)
        else:
            log("[3/5] Overture transportation skipped.")

        if not args.skip_gtfs:
            ingest_gtfs(conn, cities_df)
        else:
            log("[4/5] GTFS skipped.")

        if args.airbnb_dir and not args.skip_airbnb:
            ingest_inside_airbnb(
                conn, cities_df, args.airbnb_dir,
                room_type=args.airbnb_room_type or None,
            )
        else:
            log("[5/5] Inside Airbnb skipped.")

        print_coverage(conn, countries)


if __name__ == "__main__":
    main()
