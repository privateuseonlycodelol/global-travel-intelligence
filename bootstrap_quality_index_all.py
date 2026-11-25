#!/usr/bin/env python3
"""
Globally consistent City Quality Index for every destination.

The index has three deliberately separated layers. Only the **Core** layer
contributes to ``quality_score`` and ``data_completeness``.

Core (weights sum to 1.00)
--------------------------
* **Environment (0.30)** - 60% annual PM2.5 on a fixed log scale (100 at
  5 ug/m3, 0 at 75) + 40% water proximity (coast, large lake >= 10 km2 or
  Strahler >= 4 river, 100 at 0 km falling to 0 at 50 km). The exact formulas
  from ``bootstrap_green_score.py`` are reused.
* **Climate (0.20)** - 70% mean monthly thermal comfort over 12 WorldClim
  months + 30% annual precipitation comfort. Thermal comfort penalises both
  cold (max < 18 C or min < 5 C) and extreme heat (max > 28 C or min > 22 C).
  Precipitation comfort peaks at 500-1400 mm and penalises both hyper-aridity
  (< 300 mm, the Atacama bug) and extreme monsoon/wetness (> 2200 mm).
* **Safety & health (0.25)** - country homicide (0.35, lower better), road
  deaths (0.25, lower better), life expectancy (0.25), physicians (0.15),
  percentile ranks within the global cohort.
* **Affordability (0.15)** - country price level vs USD (0.70, lower better)
  and out-of-pocket health spending (0.30, lower better), percentiles.
* **Connectivity (0.10)** - 70% Ookla broadband (down 50%, up 20%, latency 30%
  lower better) + 30% airport access (nearest airport <= 25 km scores 100,
  decaying to 0 at 250 km, blended with the log of weighted large/medium
  airports within 100 km). The broadband side is renormalised away when
  Ookla is missing or has fewer than 10 tests, so the airport component keeps
  ``connectivity_score`` available for the whole cohort.

The active Core weights are renormalised when a dimension is missing.
``data_completeness`` is the sum of the available Core weights (0-1) and
``missing_dimensions`` lists the absent Core dimensions.

Supplementary (never changes the Core score)
--------------------------------------------
GHSL UCDB R2024A matching and themes: ``urban_centre_id``,
``ghsl_match_status``, Sentinel-2 NDVI score, UTCI > 32 C heat-stress days and
2015 hazard-event counts. The optional GHSL tables are detected with
``to_regclass`` at runtime, so this script works before or after their
loaders run. ``ghsl_enhanced_score`` is 40% NDVI + 36% air + 24% water for
matched cities with NDVI, and NULL otherwise.

Experimental (never changes the Core score)
-------------------------------------------
How Walkable walkability, Overture culture/food/parks/transit, and the
WorldCover 10 m GHSL physical greenery. These are published separately because
their coverage or regional bias is not compatible with a global headline
index. Explicit observation statuses preserve the zero-vs-missing distinction:
a zero Overture count is ``none_listed``/``unobserved_or_unmapped``, not proof
of absence.

Uncertainty & provenance
------------------------
``uncertainty_tier`` is ``low`` only when completeness >= 0.95, Ookla has at
least 10 tests, and all four safety sub-indicators are present; ``medium`` at
completeness >= 0.90; otherwise ``high``. ``uncertainty_flags`` records
country-level proxies, missing/low-sample Ookla, missing homicide and missing
GHSL polygons. ``provenance_summary`` names the Core, Supplementary and
Experimental sources.

Usage
-----
    python bootstrap_quality_index_all.py
    python bootstrap_quality_index_all.py --report
"""

from __future__ import annotations

import argparse
import math
import os
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

CORE_WEIGHTS = {
    "environment": 0.30,
    "climate": 0.20,
    "safety_health": 0.25,
    "affordability": 0.15,
    "connectivity": 0.10,
}

# Optional GHSL UCDB tables. The loaders that create these tables may run
# before or after this one, so every reference is guarded by to_regclass().
GREENNESS_NDVI_SCORE_CANDIDATES = (
    "ndvi_score_0_100",
    "ndvi_score_100",
    "ndvi_score",
)
GREENNESS_NDVI_MEAN_CANDIDATES = (
    "ndvi_mean",
    "mean_ndvi",
    "ndvi_2025",
    "ndvi",
)
CLIMATE_HEAT_CANDIDATES = (
    "utci_over_32_days",
    "heat_stress_days",
    "utci_heat_stress_days",
    "utci_gt32_days",
    "heat_stress_days_utci32",
    "utci_days_gt32",
    "heat_stress_days_gt32",
    "utci_heat_stress_days_gt32",
)

SOURCE = "First-party composite: Core + Supplementary GHSL + Experimental layers"
OBSERVED_PERIOD = "1970-2026"
PROVENANCE_SUMMARY = (
    "Core: SatPM 2024 annual PM2.5; Natural Earth / HydroLAKES / HydroRIVERS "
    "water proximity; WorldClim 2.1 monthly climate; World Bank country "
    "indicators; Ookla 2026 broadband + OurAirports 2026 airport access. "
    "Supplementary: GHSL UCDB R2024A greenness/climate/hazard themes. "
    "Experimental: How Walkable, Overture POIs/transit/parks, ESA WorldCover "
    "2021 10 m physical greenery."
)

# Piecewise-linear precipitation comfort points: (annual mm, score).
PRECIP_COMFORT_POINTS = (
    (0.0, 0.0),
    (300.0, 20.0),
    (500.0, 100.0),
    (1400.0, 100.0),
    (2200.0, 40.0),
    (3500.0, 0.0),
)

# Weighted airport count (large * 2 + medium) that reaches a full breadth
# score. 20 weighted airports within 100 km is already exceptional.
AIRPORT_BREADTH_CAP = 20.0
AIRPORT_DISTANCE_FULL_KM = 25.0
AIRPORT_DISTANCE_ZERO_KM = 250.0

SCHEMA_SQL = """
DROP TABLE IF EXISTS city_quality_index_all;

CREATE TABLE city_quality_index_all (
    destination_id              TEXT PRIMARY KEY,
    name                        TEXT NOT NULL,
    country_iso2                CHAR(2) NOT NULL,
    centre_population           BIGINT,

    -- Core raw components
    air_score                   DOUBLE PRECISION,
    water_score                 DOUBLE PRECISION,
    climate_avg_high_c          DOUBLE PRECISION,
    climate_annual_precip_mm    DOUBLE PRECISION,
    climate_rainy_days          DOUBLE PRECISION,
    homicide_per_100k           DOUBLE PRECISION,
    road_deaths_per_100k        DOUBLE PRECISION,
    life_expectancy_years       DOUBLE PRECISION,
    physicians_per_1000         DOUBLE PRECISION,
    price_level_vs_usd          DOUBLE PRECISION,
    out_of_pocket_pct           DOUBLE PRECISION,
    median_down_mbps            DOUBLE PRECISION,
    median_up_mbps              DOUBLE PRECISION,
    latency_ms                  DOUBLE PRECISION,
    broadband_total_tests       BIGINT,
    airport_nearest_distance_km DOUBLE PRECISION,
    airport_large_100km         INT,
    airport_medium_100km        INT,

    -- Core dimension scores
    environment_score           DOUBLE PRECISION,
    climate_score               DOUBLE PRECISION,
    safety_health_score         DOUBLE PRECISION,
    affordability_score         DOUBLE PRECISION,
    broadband_score             DOUBLE PRECISION,
    airport_access_score        DOUBLE PRECISION,
    connectivity_score          DOUBLE PRECISION,
    quality_score               DOUBLE PRECISION,
    data_completeness           DOUBLE PRECISION,
    missing_dimensions          TEXT,

    -- Supplementary GHSL layer (never changes quality_score)
    urban_centre_id             BIGINT,
    ghsl_match_status           TEXT,
    ghsl_ndvi_score             DOUBLE PRECISION,
    ghsl_heat_stress_days       DOUBLE PRECISION,
    ghsl_hazard_events          INT,
    ghsl_enhanced_score         DOUBLE PRECISION,

    -- Experimental layer (never changes quality_score)
    walkability_score           DOUBLE PRECISION,
    public_transport_score      DOUBLE PRECISION,
    transit_stops_5km           INT,
    culture_food_score          DOUBLE PRECISION,
    museum_culture_count        INT,
    restaurant_count            INT,
    cuisine_entropy             DOUBLE PRECISION,
    park_score                  DOUBLE PRECISION,
    park_count_5km              INT,
    physical_green_score        DOUBLE PRECISION,
    poi_observation_status      TEXT,
    transit_observation_status  TEXT,
    park_observation_status     TEXT,

    -- Uncertainty & provenance
    uncertainty_tier            TEXT,
    uncertainty_flags           TEXT,
    provenance_summary          TEXT,
    source                      TEXT NOT NULL,
    observed_period             TEXT NOT NULL,
    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

QUERY = """
WITH country AS (
    SELECT country_iso2,
           max(value) FILTER (WHERE indicator = 'price_level_vs_usd')
               AS price_level_vs_usd,
           max(value) FILTER (WHERE indicator = 'out_of_pocket_health_pct')
               AS out_of_pocket_pct,
           max(value) FILTER (WHERE indicator = 'homicide_per_100k')
               AS homicide_per_100k,
           max(value) FILTER (WHERE indicator = 'road_traffic_deaths_per_100k')
               AS road_deaths_per_100k,
           max(value) FILTER (WHERE indicator = 'life_expectancy_years')
               AS life_expectancy_years,
           max(value) FILTER (WHERE indicator = 'physicians_per_1000')
               AS physicians_per_1000
    FROM country_indicators
    GROUP BY country_iso2
),
recent AS (
    SELECT destination_id,
           avg(avg_high_c) AS recent_avg_high,
           avg(rainy_days) AS recent_rainy_days
    FROM climate_recent
    GROUP BY destination_id
),
monthly AS (
    SELECT destination_id,
           jsonb_agg(
               jsonb_build_object(
                   'month', month,
                   'median_temp_max_c', median_temp_max_c,
                   'median_temp_min_c', median_temp_min_c,
                   'precip_mm', precip_mm
               ) ORDER BY month
           ) FILTER (
               WHERE median_temp_max_c IS NOT NULL
                 AND median_temp_min_c IS NOT NULL
                 AND precip_mm IS NOT NULL
           ) AS climate_months
    FROM climate_monthly
    GROUP BY destination_id
)
SELECT d.destination_id,
       d.name,
       d.country_iso2,
       COALESCE(m.centre_population, d.population) AS centre_population,
       m.urban_centre_id,
       g.physical_green_score,
       a.pm25_ugm3,
       ge.coast_distance_km,
       iw.large_lake_distance_km,
       r.distance_km AS river_distance_km,
       p.museum_culture_count,
       p.restaurant_count,
       p.cuisine_entropy,
       fm.park_count_5km,
       w.score AS walkability_score,
       mm.transit_stops_5km,
       bb.median_down_mbps,
       bb.median_up_mbps,
       bb.avg_latency_ms AS latency_ms,
       bb.total_tests AS broadband_total_tests,
       ap.nearest_distance_km AS airport_nearest_distance_km,
       ap.large_airports_100km AS airport_large_100km,
       ap.medium_airports_100km AS airport_medium_100km,
       c.price_level_vs_usd,
       c.out_of_pocket_pct,
       c.homicide_per_100k,
       c.road_deaths_per_100k,
       c.life_expectancy_years,
       c.physicians_per_1000,
       rc.recent_avg_high,
       rc.recent_rainy_days,
       mo.climate_months
FROM destinations d
LEFT JOIN destination_metro m ON m.destination_id = d.destination_id
LEFT JOIN destination_ghsl_greenery g ON g.destination_id = d.destination_id
LEFT JOIN air_quality_annual a ON a.destination_id = d.destination_id
LEFT JOIN destination_geography ge ON ge.destination_id = d.destination_id
LEFT JOIN destination_inland_water iw ON iw.destination_id = d.destination_id
LEFT JOIN destination_river_access r ON r.destination_id = d.destination_id
LEFT JOIN poi_metrics p ON p.destination_id = d.destination_id
LEFT JOIN facility_metrics fm ON fm.destination_id = d.destination_id
LEFT JOIN walkability_scores w ON w.destination_id = d.destination_id
LEFT JOIN mobility_metrics mm ON mm.destination_id = d.destination_id
LEFT JOIN broadband_metrics bb ON bb.destination_id = d.destination_id
LEFT JOIN airport_access ap ON ap.destination_id = d.destination_id
LEFT JOIN country c ON c.country_iso2 = d.country_iso2
LEFT JOIN recent rc ON rc.destination_id = d.destination_id
LEFT JOIN monthly mo ON mo.destination_id = d.destination_id
"""

INSERT_COLUMNS = (
    "destination_id",
    "name",
    "country_iso2",
    "centre_population",
    "air_score",
    "water_score",
    "climate_avg_high_c",
    "climate_annual_precip_mm",
    "climate_rainy_days",
    "homicide_per_100k",
    "road_deaths_per_100k",
    "life_expectancy_years",
    "physicians_per_1000",
    "price_level_vs_usd",
    "out_of_pocket_pct",
    "median_down_mbps",
    "median_up_mbps",
    "latency_ms",
    "broadband_total_tests",
    "airport_nearest_distance_km",
    "airport_large_100km",
    "airport_medium_100km",
    "environment_score",
    "climate_score",
    "safety_health_score",
    "affordability_score",
    "broadband_score",
    "airport_access_score",
    "connectivity_score",
    "quality_score",
    "data_completeness",
    "missing_dimensions",
    "urban_centre_id",
    "ghsl_match_status",
    "ghsl_ndvi_score",
    "ghsl_heat_stress_days",
    "ghsl_hazard_events",
    "ghsl_enhanced_score",
    "walkability_score",
    "public_transport_score",
    "transit_stops_5km",
    "culture_food_score",
    "museum_culture_count",
    "restaurant_count",
    "cuisine_entropy",
    "park_score",
    "park_count_5km",
    "physical_green_score",
    "poi_observation_status",
    "transit_observation_status",
    "park_observation_status",
    "uncertainty_tier",
    "uncertainty_flags",
    "provenance_summary",
    "source",
    "observed_period",
)

INSERT_SQL = (
    "INSERT INTO city_quality_index_all ("
    + ", ".join(INSERT_COLUMNS)
    + ") VALUES ("
    + ", ".join(["%s"] * len(INSERT_COLUMNS))
    + ")"
)


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


def weighted_available(pairs: Sequence[tuple[float | None, float]]) -> float | None:
    active = [(value, weight) for value, weight in pairs if value is not None]
    if not active:
        return None
    return sum(value * weight for value, weight in active) / sum(
        weight for _, weight in active
    )


def compute_air_score(pm25_ugm3: float | None) -> float | None:
    """Annual PM2.5 on the fixed log scale used by bootstrap_green_score."""
    if pm25_ugm3 is None:
        return None
    if pm25_ugm3 <= 5.0:
        return 100.0
    if pm25_ugm3 >= 75.0:
        return 0.0
    return 100.0 * (math.log(75.0) - math.log(pm25_ugm3)) / (
        math.log(75.0) - math.log(5.0)
    )


def compute_water_score(
    coast_distance_km: float | None,
    large_lake_distance_km: float | None,
    river_distance_km: float | None,
) -> float | None:
    """Water proximity: 100 at 0 km, linear to 0 at 50 km, nearest feature."""
    distances = [
        distance
        for distance in (
            coast_distance_km,
            large_lake_distance_km,
            river_distance_km,
        )
        if distance is not None
    ]
    if not distances:
        return None
    return 100.0 * max(0.0, 1.0 - min(distances) / 50.0)


def _monthly_thermal_comfort(
    median_temp_max_c: float, median_temp_min_c: float
) -> float:
    """0-100 comfort for one month, penalising cold and extreme heat."""
    heat_penalty = max(0.0, median_temp_max_c - 28.0) + max(
        0.0, median_temp_min_c - 22.0
    )
    cold_penalty = max(0.0, 18.0 - median_temp_max_c) + max(
        0.0, 5.0 - median_temp_min_c
    )
    penalty = heat_penalty + cold_penalty
    return max(0.0, 100.0 * (1.0 - penalty / 20.0))


def _interpolate(points: Sequence[tuple[float, float]], value: float) -> float:
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return points[-1][1]


def compute_precip_comfort(annual_precip_mm: float | None) -> float | None:
    """0-100 annual precipitation comfort, penalising arid and monsoon ends."""
    if annual_precip_mm is None:
        return None
    return _interpolate(PRECIP_COMFORT_POINTS, annual_precip_mm)


def compute_climate_score(months: Sequence[dict[str, Any]] | None) -> float | None:
    """Climate score from 12 WorldClim monthly rows.

    Requires all 12 months with max/min temperature and precipitation, so a
    partial year cannot be mistaken for a full climatology.
    """
    if not months or len(months) != 12:
        return None
    thermal = 0.0
    annual_precip = 0.0
    for month in months:
        tmax = month.get("median_temp_max_c")
        tmin = month.get("median_temp_min_c")
        precip = month.get("precip_mm")
        if tmax is None or tmin is None or precip is None:
            return None
        thermal += _monthly_thermal_comfort(float(tmax), float(tmin))
        annual_precip += float(precip)
    thermal /= 12.0
    precip_comfort = compute_precip_comfort(annual_precip)
    if precip_comfort is None:
        return None
    return 0.70 * thermal + 0.30 * precip_comfort


def compute_airport_score(
    nearest_distance_km: float | None,
    large_airports_100km: int | None,
    medium_airports_100km: int | None,
) -> float | None:
    """Airport access: 75% nearest-airport distance + 25% airport breadth."""
    if nearest_distance_km is not None:
        if nearest_distance_km <= AIRPORT_DISTANCE_FULL_KM:
            distance_score: float | None = 100.0
        elif nearest_distance_km >= AIRPORT_DISTANCE_ZERO_KM:
            distance_score = 0.0
        else:
            distance_score = 100.0 * (
                (AIRPORT_DISTANCE_ZERO_KM - nearest_distance_km)
                / (AIRPORT_DISTANCE_ZERO_KM - AIRPORT_DISTANCE_FULL_KM)
            )
    else:
        distance_score = None

    if large_airports_100km is None and medium_airports_100km is None:
        breadth_score: float | None = None
    else:
        weighted = 2 * (large_airports_100km or 0) + (
            medium_airports_100km or 0
        )
        breadth_score = min(
            100.0,
            100.0 * math.log1p(weighted) / math.log1p(AIRPORT_BREADTH_CAP),
        )
    return weighted_available([(distance_score, 0.75), (breadth_score, 0.25)])


def classify_uncertainty(
    data_completeness: float | None,
    *,
    broadband_present: bool = False,
    broadband_total_tests: int | None = None,
    safety_subindicators_present: bool = False,
) -> str:
    """Return the explicit uncertainty tier for one city."""
    completeness = data_completeness if data_completeness is not None else 0.0
    broadband_ok = (
        broadband_present
        and broadband_total_tests is not None
        and broadband_total_tests >= 10
    )
    if completeness >= 0.95 and broadband_ok and safety_subindicators_present:
        return "low"
    if completeness >= 0.90:
        return "medium"
    return "high"


def uncertainty_flags(row: dict[str, Any]) -> str:
    """Comma-separated machine-readable uncertainty flags."""
    flags: list[str] = []
    if any(
        row.get(key) is not None
        for key in (
            "homicide_per_100k",
            "road_deaths_per_100k",
            "life_expectancy_years",
            "physicians_per_1000",
        )
    ):
        flags.append("country_level_safety")
    if (
        row.get("price_level_vs_usd") is not None
        or row.get("out_of_pocket_pct") is not None
    ):
        flags.append("country_level_affordability")
    total_tests = row.get("broadband_total_tests")
    if total_tests is None:
        flags.append("ookla_missing")
    elif total_tests < 10:
        flags.append("ookla_low_sample")
    elif row.get("broadband_score") is None:
        flags.append("ookla_missing")
    if row.get("homicide_per_100k") is None:
        flags.append("homicide_missing")
    if row.get("urban_centre_id") is None:
        flags.append("no_ghsl_polygon")
    return ",".join(flags)


def compute_ndvi_score(ndvi: float | None) -> float | None:
    """Map raw NDVI to the 0-100 scale used by the i1 greenness loader."""
    if ndvi is None:
        return None
    return round(100.0 * min(1.0, max(0.0, (float(ndvi) - 0.05) / 0.55)), 2)


def pick_column(
    available: Iterable[str], candidates: Sequence[str]
) -> str | None:
    """Case-insensitive first match from a list of candidate column names."""
    lookup = {column.lower(): column for column in available}
    for candidate in candidates:
        match = lookup.get(candidate.lower())
        if match is not None:
            return match
    return None


def _table_columns(cur: psycopg.Cursor, table: str) -> set[str]:
    """Columns of an optional public table, empty when it does not exist."""
    cur.execute("SELECT to_regclass(%s) AS name", (f"public.{table}",))
    row = cur.fetchone()
    if not row or row["name"] is None:
        return set()
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {record["column_name"] for record in cur.fetchall()}


def load_supplementary(cur: psycopg.Cursor) -> dict[int, dict[str, Any]]:
    """Read optional GHSL UCDB theme values keyed by urban_centre_id.

    Missing tables or missing candidate columns are simply skipped, so the
    script runs before and after the i1 greenness/climate loader.
    """
    values: dict[int, dict[str, Any]] = {}

    greenness_columns = _table_columns(cur, "urban_centre_greenness")
    ndvi_column = pick_column(
        greenness_columns, GREENNESS_NDVI_SCORE_CANDIDATES
    )
    ndvi_transform = None
    if ndvi_column is None:
        ndvi_column = pick_column(
            greenness_columns, GREENNESS_NDVI_MEAN_CANDIDATES
        )
        ndvi_transform = compute_ndvi_score
    if ndvi_column is not None:
        cur.execute(
            f'SELECT urban_centre_id, "{ndvi_column}" AS value '
            "FROM public.urban_centre_greenness"
        )
        for record in cur.fetchall():
            value = record["value"]
            values.setdefault(int(record["urban_centre_id"]), {})[
                "ghsl_ndvi_score"
            ] = ndvi_transform(value) if ndvi_transform else value

    climate_columns = _table_columns(cur, "urban_centre_climate")
    heat_column = pick_column(climate_columns, CLIMATE_HEAT_CANDIDATES)
    if heat_column is not None:
        cur.execute(
            f'SELECT urban_centre_id, "{heat_column}" AS value '
            "FROM public.urban_centre_climate"
        )
        for record in cur.fetchall():
            values.setdefault(int(record["urban_centre_id"]), {})[
                "ghsl_heat_stress_days"
            ] = record["value"]

    hazard_columns = _table_columns(cur, "urban_centre_hazard")
    if "events_2015" in hazard_columns:
        cur.execute(
            "SELECT urban_centre_id, events_2015 "
            "FROM public.urban_centre_hazard"
        )
        for record in cur.fetchall():
            values.setdefault(int(record["urban_centre_id"]), {})[
                "ghsl_hazard_events"
            ] = record["events_2015"]

    return values


def score_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute Core, Supplementary and Experimental values in place.

    Pure Python: no database or network access, so the whole scoring contract
    can be unit-tested offline.
    """
    for row in rows:
        row["air_score"] = compute_air_score(row.get("pm25_ugm3"))
        row["water_score"] = compute_water_score(
            row.get("coast_distance_km"),
            row.get("large_lake_distance_km"),
            row.get("river_distance_km"),
        )
        row["environment_score"] = weighted_available(
            [
                (row["air_score"], 0.60),
                (row["water_score"], 0.40),
            ]
        )

        months = row.get("climate_months")
        row["climate_score"] = compute_climate_score(months)
        recent_high = row.get("recent_avg_high")
        monthly_high: float | None = None
        annual_precip: float | None = None
        if months and len(months) == 12:
            highs = [month.get("median_temp_max_c") for month in months]
            precip = [month.get("precip_mm") for month in months]
            if all(value is not None for value in highs):
                monthly_high = sum(float(value) for value in highs) / 12.0
            if all(value is not None for value in precip):
                annual_precip = sum(float(value) for value in precip)
        row["climate_avg_high_c"] = (
            recent_high if recent_high is not None else monthly_high
        )
        row["climate_annual_precip_mm"] = annual_precip
        row["climate_rainy_days"] = row.get("recent_rainy_days")

        total_tests = row.get("broadband_total_tests")
        total_tests = int(total_tests) if total_tests is not None else None
        row["broadband_total_tests"] = total_tests
        broadband_row_present = total_tests is not None
        broadband_sample_ok = broadband_row_present and total_tests >= 10
        for key in ("median_down_mbps", "median_up_mbps", "latency_ms"):
            value = row.get(key)
            valid = (
                broadband_sample_ok
                and value is not None
                and float(value) > 0
            )
            row[f"_{key}_valid"] = valid

        row["airport_access_score"] = compute_airport_score(
            row.get("airport_nearest_distance_km"),
            row.get("airport_large_100km"),
            row.get("airport_medium_100km"),
        )

        museum = row.get("museum_culture_count")
        restaurant = row.get("restaurant_count")
        transit = row.get("transit_stops_5km")
        park_count = row.get("park_count_5km")
        row["culture_log"] = math.log1p(museum) if museum is not None else None
        row["restaurant_log"] = (
            math.log1p(restaurant) if restaurant is not None else None
        )
        row["transit_log"] = math.log1p(transit) if transit is not None else None
        if park_count is not None and park_count > 0:
            row["park_score"] = min(
                100.0, 100.0 * math.log1p(park_count) / math.log(501.0)
            )
        else:
            row["park_score"] = None

        row["ghsl_match_status"] = (
            "matched" if row.get("urban_centre_id") is not None else "unmatched"
        )
        row["poi_observation_status"] = (
            "observed"
            if (restaurant or 0) > 0 or (museum or 0) > 0
            else "none_listed_or_unmapped"
        )
        row["transit_observation_status"] = (
            "observed"
            if (transit or 0) > 0
            else "unobserved_or_unmapped"
        )
        row["park_observation_status"] = (
            "observed" if (park_count or 0) > 0 else "none_listed"
        )

        row.setdefault("source", SOURCE)
        row.setdefault("observed_period", OBSERVED_PERIOD)

    maps = {
        "culture": percentile_map([row.get("culture_log") for row in rows]),
        "restaurant": percentile_map(
            [row.get("restaurant_log") for row in rows]
        ),
        "cuisine": percentile_map(
            [row.get("cuisine_entropy") for row in rows]
        ),
        "homicide": percentile_map(
            [row.get("homicide_per_100k") for row in rows]
        ),
        "road": percentile_map(
            [row.get("road_deaths_per_100k") for row in rows]
        ),
        "life": percentile_map(
            [row.get("life_expectancy_years") for row in rows]
        ),
        "physicians": percentile_map(
            [row.get("physicians_per_1000") for row in rows]
        ),
        "price": percentile_map(
            [row.get("price_level_vs_usd") for row in rows]
        ),
        "oop": percentile_map([row.get("out_of_pocket_pct") for row in rows]),
        "down": percentile_map(
            [
                row.get("median_down_mbps")
                if row.get("_median_down_mbps_valid")
                else None
                for row in rows
            ]
        ),
        "up": percentile_map(
            [
                row.get("median_up_mbps")
                if row.get("_median_up_mbps_valid")
                else None
                for row in rows
            ]
        ),
        "latency": percentile_map(
            [
                row.get("latency_ms") if row.get("_latency_ms_valid") else None
                for row in rows
            ]
        ),
        "airport": percentile_map(
            [row.get("airport_access_score") for row in rows]
        ),
        "transit": percentile_map([row.get("transit_log") for row in rows]),
    }

    def pct(
        row: dict[str, Any], key: str, source: str, lower_better: bool = False
    ) -> float | None:
        value = row.get(source)
        if value is None:
            return None
        score = maps[key].get(value)
        if score is None:
            return None
        return 100.0 - score if lower_better else score

    for row in rows:
        row["culture_food_score"] = weighted_available(
            [
                (pct(row, "culture", "culture_log"), 0.50),
                (pct(row, "restaurant", "restaurant_log"), 0.30),
                (pct(row, "cuisine", "cuisine_entropy"), 0.20),
            ]
        )
        row["public_transport_score"] = pct(row, "transit", "transit_log")

        row["safety_health_score"] = weighted_available(
            [
                (pct(row, "homicide", "homicide_per_100k", True), 0.35),
                (pct(row, "road", "road_deaths_per_100k", True), 0.25),
                (pct(row, "life", "life_expectancy_years"), 0.25),
                (pct(row, "physicians", "physicians_per_1000"), 0.15),
            ]
        )
        row["affordability_score"] = weighted_available(
            [
                (pct(row, "price", "price_level_vs_usd", True), 0.70),
                (pct(row, "oop", "out_of_pocket_pct", True), 0.30),
            ]
        )
        row["broadband_score"] = weighted_available(
            [
                (
                    pct(row, "down", "median_down_mbps")
                    if row.get("_median_down_mbps_valid")
                    else None,
                    0.50,
                ),
                (
                    pct(row, "up", "median_up_mbps")
                    if row.get("_median_up_mbps_valid")
                    else None,
                    0.20,
                ),
                (
                    pct(row, "latency", "latency_ms", True)
                    if row.get("_latency_ms_valid")
                    else None,
                    0.30,
                ),
            ]
        )
        airport_pct = pct(row, "airport", "airport_access_score")
        row["connectivity_score"] = weighted_available(
            [
                (row["broadband_score"], 0.70),
                (airport_pct, 0.30),
            ]
        )

        dimensions = [
            ("environment", row["environment_score"]),
            ("climate", row["climate_score"]),
            ("safety_health", row["safety_health_score"]),
            ("affordability", row["affordability_score"]),
            ("connectivity", row["connectivity_score"]),
        ]
        active = [
            (value, CORE_WEIGHTS[name])
            for name, value in dimensions
            if value is not None
        ]
        row["quality_score"] = (
            sum(value * weight for value, weight in active)
            / sum(weight for _, weight in active)
            if active
            else None
        )
        row["data_completeness"] = sum(weight for _, weight in active)
        row["missing_dimensions"] = ",".join(
            name for name, value in dimensions if value is None
        )

        if row.get("urban_centre_id") is not None and row.get(
            "ghsl_ndvi_score"
        ) is not None:
            row["ghsl_enhanced_score"] = weighted_available(
                [
                    (row.get("ghsl_ndvi_score"), 0.40),
                    (row["air_score"], 0.36),
                    (row["water_score"], 0.24),
                ]
            )
        else:
            row["ghsl_enhanced_score"] = None
        row.setdefault("ghsl_ndvi_score", None)
        row.setdefault("ghsl_heat_stress_days", None)
        row.setdefault("ghsl_hazard_events", None)

        safety_present = all(
            row.get(key) is not None
            for key in (
                "homicide_per_100k",
                "road_deaths_per_100k",
                "life_expectancy_years",
                "physicians_per_1000",
            )
        )
        row["uncertainty_tier"] = classify_uncertainty(
            row["data_completeness"],
            broadband_present=row.get("broadband_score") is not None,
            broadband_total_tests=row.get("broadband_total_tests"),
            safety_subindicators_present=safety_present,
        )
        row["uncertainty_flags"] = uncertainty_flags(row)
        row["provenance_summary"] = PROVENANCE_SUMMARY

    return rows


def load(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            rows = [dict(row) for row in cur.fetchall()]
            supplementary = load_supplementary(cur)

        for row in rows:
            centre_id = row.get("urban_centre_id")
            if centre_id is not None:
                extra = supplementary.get(int(centre_id), {})
                row["ghsl_ndvi_score"] = extra.get("ghsl_ndvi_score")
                row["ghsl_heat_stress_days"] = extra.get(
                    "ghsl_heat_stress_days"
                )
                row["ghsl_hazard_events"] = extra.get("ghsl_hazard_events")
            else:
                row["ghsl_ndvi_score"] = None
                row["ghsl_heat_stress_days"] = None
                row["ghsl_hazard_events"] = None

        score_rows(rows)

        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.executemany(
                INSERT_SQL,
                [
                    tuple(row.get(column) for column in INSERT_COLUMNS)
                    for row in rows
                ],
            )
            cur.execute(
                """
                SELECT count(*) AS cities,
                       count(quality_score) AS scored,
                       round(avg(data_completeness)::numeric, 3)
                           AS avg_completeness,
                       count(*) FILTER (WHERE uncertainty_tier = 'low')
                           AS low_uncertainty,
                       count(*) FILTER (WHERE uncertainty_tier = 'medium')
                           AS medium_uncertainty,
                       count(*) FILTER (WHERE uncertainty_tier = 'high')
                           AS high_uncertainty,
                       count(*) FILTER (WHERE ghsl_match_status = 'matched')
                           AS ghsl_matched,
                       count(ghsl_ndvi_score) AS ghsl_greenness,
                       count(ghsl_heat_stress_days) AS ghsl_climate
                FROM city_quality_index_all
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
                SELECT count(*) AS cities,
                       count(quality_score) AS scored,
                       round(avg(data_completeness)::numeric, 3)
                           AS avg_completeness,
                       count(*) FILTER (WHERE uncertainty_tier = 'low')
                           AS low_uncertainty,
                       count(*) FILTER (WHERE uncertainty_tier = 'medium')
                           AS medium_uncertainty,
                       count(*) FILTER (WHERE uncertainty_tier = 'high')
                           AS high_uncertainty
                FROM city_quality_index_all
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT name, country_iso2,
                       round(quality_score::numeric, 1) AS quality,
                       round(environment_score::numeric, 1) AS environment,
                       round(climate_score::numeric, 1) AS climate,
                       round(safety_health_score::numeric, 1) AS safety_health,
                       round(affordability_score::numeric, 1)
                           AS affordability,
                       round(connectivity_score::numeric, 1) AS connectivity,
                       round(data_completeness::numeric, 2) AS completeness,
                       uncertainty_tier
                FROM city_quality_index_all
                ORDER BY quality_score DESC NULLS LAST,
                         centre_population DESC NULLS LAST
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
