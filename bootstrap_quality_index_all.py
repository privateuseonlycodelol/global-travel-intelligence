#!/usr/bin/env python3
"""
Intrinsic City Quality Index (IQI) — Council Phase 1 implementation.

Measures intrinsic urban quality regardless of cost, following the consolidated
two-round Grok 4.7 High council methodology:

* **Strict cost / travel / GDP-clone exclusions (weight 0%):**
  affordability, price level, out-of-pocket health spending, airport access,
  life expectancy, physicians per 1,000, road traffic deaths, and WorldClim
  precipitation-as-comfort are excluded from the intrinsic score.
* **Three-board, absolute-anchor, no-imputation cohort architecture:**
  Every active indicator uses fixed 0–100 physical or epidemiological anchors
  (no cohort percentiles in the score, no median imputation, no silent
  renormalization across missing dimensions).

Reference intrinsic vector (100% total):
  Board 1 — Biophysical (55%):
    - Air quality (15%): SatPM PM2.5 log scale (100 at <=5 ug/m3, 0 at >=75).
    - Thermal safety (12%): UTCI heat/cold/severe tails. Phase 1 publishes
      GHSL UTCI > 32 C heat-stress days (100 at 0 days, 0 at >=120 days) as
      the heat term (`thermal_heat_score`); full thermal safety stays NULL
      until daily cold and severe tails are loaded.
    - Urban greenery (14%): WorldCover 10 m polygon (tree + shrub, crop/bare
      excluded) * canopy-height multiplier (0.85 + 0.15 * min(1, canopy_m/8)).
      GHSL NDVI (`ghsl_ndvi_score`) is published as a diagnostic column only
      and never mixed into WorldCover polygon ranks.
    - Waterfront & hinterland (7%): 1–15 km short-range shore proximity to
      coast, lake >= 10 km2, or Strahler >= 5 river (retiring the saturated
      50 km decay).
    - Urban form (7%): named hole (NULL) until GHS-POP 100 m + pre-registered
      validation.
  Board 2 — Country-Labelled Institutional (15%, never reorders within country):
    - Personal safety / violence prior (9%): UNODC/WHO national homicide
      piecewise anchor (100 <=1, 80 @3, 50 @10, 20 @25, 0 >=50 per 100k).
    - Water & sanitation / WASH prior (6%): mean of WHO/UNICEF JMP safely
      managed water & sanitation ONLY when BOTH are present (no basic-water
      fallback).
  Board 3 — City Care, Hazards & Gated Services (30%):
    - Healthcare access (7%): named hole (NULL) until Weiss friction raster.
    - Natural hazards, non-heat (6%): named hole (NULL) until flood + seismic
      + cyclone rasters (2015 MYRIAD counts kept as context only).
    - Public transport (6%): named hole (NULL) until road-gated guideway /
      coach-excluded local GTFS headway.
    - Cultural heritage (6%): named hole (NULL) until UNESCO core polygon
      intersection.
    - Digital connectivity (5%): Ookla fixed broadband absolute blend
      (50% down [10–150 Mbps], 20% up [5–50 Mbps], 30% latency [80–10 ms]),
      requiring >=10 tests AND >=2 tiles; 10–49 tests capped at Tier B.

Settlement classification & satellite suppression:
  - `ghsl_centre`: matched to a GHSL UCDB R2024A urban centre polygon.
  - `satellite_suppressed`: unmatched locality within 15 km of any GHSL centre.
  - `unmatched_town`: unmatched locality >15 km from any GHSL centre (published
    in a separate town file, never pooled with GHSL urban centres).

Usage
-----
    python bootstrap_quality_index_all.py --db-url "$DATABASE_URL"
    python bootstrap_quality_index_all.py --db-url "$DATABASE_URL" --report
"""

from __future__ import annotations

import argparse
import math
import os
from typing import Any, Sequence

import psycopg
from psycopg.rows import dict_row

# Full 100% normative reference vector (used to compute true construct_coverage).
REFERENCE_WEIGHTS = {
    "air": 0.15,
    "thermal_safety": 0.12,
    "urban_greenery": 0.14,
    "waterfront": 0.07,
    "urban_form": 0.07,
    "safety_prior": 0.09,
    "wash_prior": 0.06,
    "care_access": 0.07,
    "hazards": 0.06,
    "transit": 0.06,
    "heritage": 0.06,
    "broadband": 0.05,
}

# Phase 1 currently observable cohort pillars (42% of the 100% reference vector:
# air 15 + waterfront 7 + safety_prior 9 + wash_prior 6 + broadband 5 = 0.42).
# Thermal heat days are published as a diagnostic column, NOT ranked without
# the cold and severe UTCI tails (prevents sub-Arctic cities winning on zero heat).
PHASE1_BIOPHYSICAL_WEIGHTS = {
    "air": 0.15,
    "waterfront": 0.07,
}
PHASE1_INSTITUTIONAL_WEIGHTS = {
    "safety_prior": 0.09,
    "wash_prior": 0.06,
}
PHASE1_SERVICES_WEIGHTS = {
    "broadband": 0.05,
}
PHASE1_COHORT_WEIGHTS = {
    **PHASE1_BIOPHYSICAL_WEIGHTS,
    **PHASE1_INSTITUTIONAL_WEIGHTS,
    **PHASE1_SERVICES_WEIGHTS,
}
PHASE1_COHORT_WEIGHT_SUM = round(sum(PHASE1_COHORT_WEIGHTS.values()), 6)  # 0.42

HOMICIDE_ANCHORS: tuple[tuple[float, float], ...] = (
    (1.0, 100.0),
    (3.0, 80.0),
    (10.0, 50.0),
    (25.0, 20.0),
    (50.0, 0.0),
)

BROADBAND_MIN_TESTS = 10
BROADBAND_TIER_A_TESTS = 50
BROADBAND_MIN_TILES = 2

GREENNESS_NDVI_SCORE_CANDIDATES = ("ndvi_score_0_100", "ghsl_ndvi_score")
GREENNESS_CANOPY_CANDIDATES = ("canopy_height_m", "gr_cth_avg_2020")
CLIMATE_HEAT_CANDIDATES = (
    "utci_over_32_days",
    "heat_stress_days",
    "utci_heat_stress_days",
    "utci_gt32_days",
)

SOURCE = "Council Phase 1 IQI: SatPM, Natural Earth/HydroLAKES/HydroRIVERS, GHSL UCDB R2024A, UNODC/WHO, WHO/UNICEF JMP, Ookla"
OBSERVED_PERIOD = "2020-2026"
PROVENANCE_SUMMARY = (
    "Biophysical (34% active / 55% ref): SatPM 2024 PM2.5 (point), 1-15 km "
    "coast/lake/Strahler>=5 shore, GHSL UCDB 2020 UTCI>32 heat days (cold/severe "
    "NULL), WorldCover 10m polygon greenery (516 centres; GHSL NDVI diagnostic). "
    "Institutional (15% country-labelled): UNODC/WHO homicide, WHO/UNICEF JMP "
    "safely managed WASH. Services (5% active / 30% ref): Ookla fixed broadband "
    "(>=10 tests, >=2 tiles). Named holes (46%): cold/severe UTCI, full WorldCover, "
    "urban form, care friction, non-heat hazards, gated transit, UNESCO heritage."
)

SCHEMA_SQL = """
DROP TABLE IF EXISTS city_quality_index_all;

CREATE TABLE city_quality_index_all (
    destination_id              TEXT PRIMARY KEY,
    name                        TEXT NOT NULL,
    country_iso2                CHAR(2) NOT NULL,
    urban_centre_id             BIGINT,
    settlement_class            TEXT NOT NULL,
    centre_population           BIGINT,
    gdp_per_capita_ppp          DOUBLE PRECISION,

    -- Board 1: Biophysical (absolute 0-100 anchors)
    pm25_ugm3                   DOUBLE PRECISION,
    air_score                   DOUBLE PRECISION,
    water_distance_km           DOUBLE PRECISION,
    waterfront_score            DOUBLE PRECISION,
    ghsl_heat_stress_days       DOUBLE PRECISION,
    thermal_heat_score          DOUBLE PRECISION,
    thermal_safety_score        DOUBLE PRECISION,
    worldcover_green_score      DOUBLE PRECISION,
    green_method                TEXT NOT NULL,
    ghsl_ndvi_score             DOUBLE PRECISION,
    ghsl_canopy_m               DOUBLE PRECISION,
    urban_form_score            DOUBLE PRECISION,
    biophysical_board_score     DOUBLE PRECISION,

    -- Board 2: Country-labelled Institutional (never reorders within country)
    homicide_per_100k           DOUBLE PRECISION,
    safety_prior_score          DOUBLE PRECISION,
    safely_managed_water_pct    DOUBLE PRECISION,
    safely_managed_sanit_pct    DOUBLE PRECISION,
    wash_prior_score            DOUBLE PRECISION,
    institutional_board_score   DOUBLE PRECISION,

    -- Board 3: City Care, Hazards & Gated Services
    care_access_score           DOUBLE PRECISION,
    hazard_score                DOUBLE PRECISION,
    ghsl_hazard_events          INT,
    transit_score               DOUBLE PRECISION,
    heritage_score              DOUBLE PRECISION,
    median_down_mbps            DOUBLE PRECISION,
    median_up_mbps              DOUBLE PRECISION,
    latency_ms                  DOUBLE PRECISION,
    broadband_total_tests       BIGINT,
    broadband_tile_count        INT,
    broadband_score             DOUBLE PRECISION,
    services_board_score        DOUBLE PRECISION,

    -- Cohort composite & explicit construct coverage / tiers (NO silent renormalization)
    quality_score               DOUBLE PRECISION,
    cohort_name                 TEXT NOT NULL,
    construct_coverage          DOUBLE PRECISION NOT NULL,
    data_completeness           DOUBLE PRECISION NOT NULL,
    tier                        TEXT NOT NULL,
    uncertainty_tier            TEXT NOT NULL,
    uncertainty_flags           TEXT,
    missing_dimensions          TEXT,

    -- Experimental & Travel-context columns (weight 0%; exported separately)
    walkability_score           DOUBLE PRECISION,
    transit_stops_5km           INT,
    transit_observation_status  TEXT NOT NULL,
    museum_culture_count        INT,
    restaurant_count            INT,
    cuisine_entropy             DOUBLE PRECISION,
    poi_observation_status      TEXT NOT NULL,
    park_count_5km              INT,
    park_observation_status     TEXT NOT NULL,
    price_level_vs_usd          DOUBLE PRECISION,
    out_of_pocket_pct           DOUBLE PRECISION,
    airport_nearest_km          DOUBLE PRECISION,

    provenance_summary          TEXT NOT NULL,
    source                      TEXT NOT NULL,
    observed_period             TEXT NOT NULL,
    computed_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

BASE_QUERY = """
WITH satellite AS (
    SELECT d.destination_id,
           EXISTS (
               SELECT 1
               FROM ghsl_urban_centres uc
               WHERE uc.geom && ST_Expand(ST_SetSRID(ST_MakePoint(d.lon, d.lat), 4326), 0.3)
                 AND ST_DWithin(
                     ST_SetSRID(ST_MakePoint(d.lon, d.lat), 4326)::geography,
                     uc.geom::geography,
                     15000
                 )
           ) AS within_15km_centre
    FROM destinations d
    LEFT JOIN destination_metro m ON m.destination_id = d.destination_id
    WHERE m.urban_centre_id IS NULL
)
SELECT d.destination_id,
       d.name,
       d.country_iso2,
       m.urban_centre_id,
       COALESCE(s.within_15km_centre, FALSE) AS within_15km_centre,
       COALESCE(m.centre_population, d.population) AS centre_population,
       a.pm25_ugm3,
       ge.coast_distance_km,
       iw.large_lake_distance_km,
       CASE WHEN r.river_order >= 5 THEN r.distance_km END AS major_river_distance_km,
       ulc.tree_fraction AS wc_tree_fraction,
       ulc.shrub_fraction AS wc_shrub_fraction,
       ulc.water_fraction AS wc_water_fraction,
       hz.events_2015 AS ghsl_hazard_events,
       c.homicide_per_100k,
       c.safely_managed_water_pct,
       c.safely_managed_sanitation_pct,
       c.gdp_per_capita_ppp,
       c.price_level_vs_usd,
       c.out_of_pocket_health_pct,
       bb.median_down_mbps,
       bb.median_up_mbps,
       bb.avg_latency_ms AS latency_ms,
       bb.total_tests AS broadband_total_tests,
       bb.tile_count AS broadband_tile_count,
       w.score AS walkability_score,
       mm.transit_stops_5km,
       p.museum_culture_count,
       p.restaurant_count,
       p.cuisine_entropy,
       fm.park_count_5km,
       ap.nearest_distance_km AS airport_nearest_km
FROM destinations d
LEFT JOIN destination_metro m ON m.destination_id = d.destination_id
LEFT JOIN satellite s ON s.destination_id = d.destination_id
LEFT JOIN air_quality_annual a ON a.destination_id = d.destination_id
LEFT JOIN destination_geography ge ON ge.destination_id = d.destination_id
LEFT JOIN destination_inland_water iw ON iw.destination_id = d.destination_id
LEFT JOIN destination_river_access r ON r.destination_id = d.destination_id
LEFT JOIN destination_urban_land_cover ulc ON ulc.destination_id = d.destination_id
LEFT JOIN urban_centre_hazard hz ON hz.urban_centre_id = m.urban_centre_id
LEFT JOIN broadband_metrics bb ON bb.destination_id = d.destination_id
LEFT JOIN walkability_scores w ON w.destination_id = d.destination_id
LEFT JOIN mobility_metrics mm ON mm.destination_id = d.destination_id
LEFT JOIN poi_metrics p ON p.destination_id = d.destination_id
LEFT JOIN facility_metrics fm ON fm.destination_id = d.destination_id
LEFT JOIN airport_access ap ON ap.destination_id = d.destination_id
LEFT JOIN (
    SELECT country_iso2,
           max(value) FILTER (WHERE indicator = 'homicide_per_100k') AS homicide_per_100k,
           max(value) FILTER (WHERE indicator = 'safely_managed_water_pct') AS safely_managed_water_pct,
           max(value) FILTER (WHERE indicator = 'safely_managed_sanitation_pct') AS safely_managed_sanitation_pct,
           max(value) FILTER (WHERE indicator = 'gdp_per_capita_ppp') AS gdp_per_capita_ppp,
           max(value) FILTER (WHERE indicator = 'price_level_vs_usd') AS price_level_vs_usd,
           max(value) FILTER (WHERE indicator = 'out_of_pocket_health_pct') AS out_of_pocket_health_pct
    FROM country_indicators
    GROUP BY country_iso2
) c ON c.country_iso2 = d.country_iso2
"""


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _piecewise(points: Sequence[tuple[float, float]], value: float) -> float:
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


def compute_air_score(pm25_ugm3: float | None) -> float | None:
    """Fixed log scale: 100 at <=5 ug/m3, 0 at >=75 ug/m3."""
    if pm25_ugm3 is None:
        return None
    val = float(pm25_ugm3)
    if val <= 5.0:
        return 100.0
    if val >= 75.0:
        return 0.0
    return 100.0 * (math.log(75.0) - math.log(val)) / (math.log(75.0) - math.log(5.0))


def compute_waterfront_score(
    coast_km: float | None,
    large_lake_km: float | None,
    major_river_km: float | None,
) -> tuple[float | None, float | None]:
    """Short-range 1-15 km shore proximity (coast, lake >=10 km2, Strahler >=5).

    Retires the saturated 50 km decay (which gave >56% of cities >=90).
    Returns (waterfront_score, nearest_distance_km).
    """
    dists = [float(d) for d in (coast_km, large_lake_km, major_river_km) if d is not None]
    if not dists:
        return None, None
    nearest = min(dists)
    if nearest <= 1.0:
        return 100.0, nearest
    if nearest >= 15.0:
        return 0.0, nearest
    return 100.0 * (15.0 - nearest) / 14.0, nearest


def compute_thermal_heat_score(utci_over_32_days: float | None) -> float | None:
    """Council UTCI heat term: 100 at 0 days, 0 at >=120 days/year."""
    if utci_over_32_days is None:
        return None
    return _clamp(100.0 * (1.0 - float(utci_over_32_days) / 120.0))


def compute_worldcover_green_score(
    tree_fraction: float | None,
    shrub_fraction: float | None,
    water_fraction: float | None,
    canopy_m: float | None = None,
) -> float | None:
    """Council WorldCover 10 m polygon greenery with canopy-height multiplier.

    Numerator is tree + shrub (crop, grass, and bare excluded); water is
    excluded from the denominator.
    """
    if tree_fraction is None or shrub_fraction is None or water_fraction is None:
        return None
    land = 1.0 - float(water_fraction)
    if land < 0.2:
        return None
    veg_share = (float(tree_fraction) + float(shrub_fraction)) / land
    base = 100.0 * min(1.0, max(0.0, veg_share) / 0.40)
    if canopy_m is not None:
        mult = 0.85 + 0.15 * min(1.0, max(0.0, float(canopy_m)) / 8.0)
    else:
        mult = 1.0
    return base * mult


def compute_homicide_score(homicide_per_100k: float | None) -> float | None:
    """Piecewise absolute anchor: 100 <=1, 80 @3, 50 @10, 20 @25, 0 >=50."""
    if homicide_per_100k is None:
        return None
    return _piecewise(HOMICIDE_ANCHORS, float(homicide_per_100k))


def compute_wash_score(
    safely_managed_water_pct: float | None,
    safely_managed_sanitation_pct: float | None,
) -> float | None:
    """Mean of JMP safely managed water & sanitation ONLY when both exist."""
    if safely_managed_water_pct is None or safely_managed_sanitation_pct is None:
        return None
    return _clamp(
        0.5 * (float(safely_managed_water_pct) + float(safely_managed_sanitation_pct))
    )


def compute_broadband_score(
    down_mbps: float | None,
    up_mbps: float | None,
    latency_ms: float | None,
    total_tests: int | None,
    tile_count: int | None,
) -> float | None:
    """Absolute broadband blend: 50% down [10-150], 20% up [5-50], 30% lat [80-10].

    Requires >=10 tests AND >=2 tiles; otherwise NULL.
    """
    if (
        total_tests is None
        or int(total_tests) < BROADBAND_MIN_TESTS
        or tile_count is None
        or int(tile_count) < BROADBAND_MIN_TILES
    ):
        return None
    if down_mbps is None or up_mbps is None or latency_ms is None:
        return None
    d = float(down_mbps)
    u = float(up_mbps)
    lat = float(latency_ms)
    if d <= 0 or u <= 0 or lat <= 0:
        return None
    down_s = _clamp(100.0 * (d - 10.0) / 140.0)
    up_s = _clamp(100.0 * (u - 5.0) / 45.0)
    lat_s = _clamp(100.0 * (80.0 - lat) / 70.0)
    return 0.50 * down_s + 0.20 * up_s + 0.30 * lat_s


def _exact_cohort_weighted(
    values: dict[str, float | None], weights: dict[str, float]
) -> float | None:
    """Return weighted average ONLY when every pillar in `weights` is non-NULL."""
    if any(values.get(k) is None for k in weights):
        return None
    total_w = sum(weights.values())
    return sum(float(values[k]) * w for k, w in weights.items()) / total_w


def classify_settlement(
    urban_centre_id: int | None, within_15km_centre: bool
) -> str:
    if urban_centre_id is not None:
        return "ghsl_centre"
    if within_15km_centre:
        return "satellite_suppressed"
    return "unmatched_town"


def score_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure offline scoring function for all rows (no DB access)."""
    scored: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        uc_id = row.get("urban_centre_id")
        settlement_class = classify_settlement(
            uc_id, bool(row.get("within_15km_centre"))
        )
        row["settlement_class"] = settlement_class

        # Board 1: Biophysical
        air_score = compute_air_score(row.get("pm25_ugm3"))
        waterfront_score, water_dist_km = compute_waterfront_score(
            row.get("coast_distance_km"),
            row.get("large_lake_distance_km"),
            row.get("major_river_distance_km"),
        )
        thermal_heat_score = compute_thermal_heat_score(
            row.get("ghsl_heat_stress_days")
        )
        wc_green = compute_worldcover_green_score(
            row.get("wc_tree_fraction"),
            row.get("wc_shrub_fraction"),
            row.get("wc_water_fraction"),
            row.get("ghsl_canopy_m"),
        )
        if wc_green is not None:
            green_method = "worldcover_10m_polygon"
        elif row.get("ghsl_ndvi_score") is not None:
            green_method = "ghsl_ndvi_diagnostic_only"
        else:
            green_method = "unobserved"

        row["air_score"] = air_score
        row["water_distance_km"] = water_dist_km
        row["waterfront_score"] = waterfront_score
        row["thermal_heat_score"] = thermal_heat_score
        row["thermal_safety_score"] = None  # Named hole: awaits cold + severe UTCI
        row["worldcover_green_score"] = wc_green
        row["green_method"] = green_method
        row["urban_form_score"] = None  # Named hole: awaits GHS-POP + validation

        # Board 2: Country-labelled Institutional
        safety_prior = compute_homicide_score(row.get("homicide_per_100k"))
        wash_prior = compute_wash_score(
            row.get("safely_managed_water_pct"),
            row.get("safely_managed_sanitation_pct"),
        )
        row["safety_prior_score"] = safety_prior
        row["safely_managed_sanit_pct"] = row.get("safely_managed_sanitation_pct")
        row["wash_prior_score"] = wash_prior

        # Board 3: Care, Hazards & Gated Services
        row["care_access_score"] = None  # Named hole: awaits Weiss friction
        row["hazard_score"] = None  # Named hole: awaits flood+seismic+cyclone
        row["transit_score"] = None  # Named hole: awaits gated rail / local GTFS
        row["heritage_score"] = None  # Named hole: awaits UNESCO core polygons

        total_tests = row.get("broadband_total_tests")
        tile_count = row.get("broadband_tile_count")
        broadband_score = compute_broadband_score(
            row.get("median_down_mbps"),
            row.get("median_up_mbps"),
            row.get("latency_ms"),
            total_tests,
            tile_count,
        )
        row["broadband_score"] = broadband_score

        # Exact board aggregates (NO silent renormalization!)
        pillar_vals = {
            "air": air_score,
            "waterfront": waterfront_score,
            "safety_prior": safety_prior,
            "wash_prior": wash_prior,
            "broadband": broadband_score,
        }
        row["biophysical_board_score"] = _exact_cohort_weighted(
            pillar_vals, PHASE1_BIOPHYSICAL_WEIGHTS
        )
        row["institutional_board_score"] = _exact_cohort_weighted(
            pillar_vals, PHASE1_INSTITUTIONAL_WEIGHTS
        )
        row["services_board_score"] = _exact_cohort_weighted(
            pillar_vals, PHASE1_SERVICES_WEIGHTS
        )

        # True construct coverage against the full 100% intrinsic reference vector
        observed_ref_weight = 0.0
        for key, w in PHASE1_COHORT_WEIGHTS.items():
            if pillar_vals[key] is not None:
                observed_ref_weight += w
        if wc_green is not None:
            observed_ref_weight += REFERENCE_WEIGHTS["urban_greenery"]
        construct_coverage = round(observed_ref_weight, 4)

        # Cohort composite ONLY when all 6 Phase 1 cohort pillars are observed
        # AND the locality is a GHSL urban centre (never pool unmatched towns!)
        if settlement_class == "ghsl_centre":
            cohort_score = _exact_cohort_weighted(pillar_vals, PHASE1_COHORT_WEIGHTS)
        else:
            cohort_score = None

        row["quality_score"] = cohort_score
        row["construct_coverage"] = construct_coverage
        # data_completeness = share of the Phase 1 cohort (0.54) observed
        phase1_obs = sum(
            w for k, w in PHASE1_COHORT_WEIGHTS.items() if pillar_vals[k] is not None
        )
        row["data_completeness"] = round(phase1_obs / PHASE1_COHORT_WEIGHT_SUM, 4)

        # Tier A / B / C & Uncertainty flags
        flags: list[str] = ["pm25_point_sample", "thermal_heat_only", "country_level_safety", "country_level_wash"]
        if settlement_class != "ghsl_centre":
            flags.append(settlement_class)
        if safety_prior is None:
            flags.append("homicide_missing")
        if wash_prior is None:
            flags.append("wash_missing")
        if broadband_score is None:
            if total_tests is not None and int(total_tests) > 0:
                flags.append("ookla_below_sample_floor")
            else:
                flags.append("ookla_missing")
        elif int(total_tests) < BROADBAND_TIER_A_TESTS:
            flags.append("ookla_tier_b_sample")

        if cohort_score is not None:
            cohort_name = "phase1_intrinsic_ghsl_centre"
            if int(total_tests) >= BROADBAND_TIER_A_TESTS:
                tier = "A"
                uncertainty_tier = "medium"  # Phase 1 construct_coverage is 0.54 (<1.0)
            else:
                tier = "B"
                uncertainty_tier = "medium"
        else:
            cohort_name = (
                "unmatched_town_series"
                if settlement_class == "unmatched_town"
                else (
                    "satellite_suppressed"
                    if settlement_class == "satellite_suppressed"
                    else "incomplete_ghsl_centre"
                )
            )
            tier = "C"
            uncertainty_tier = "high"

        row["cohort_name"] = cohort_name
        row["tier"] = tier
        row["uncertainty_tier"] = uncertainty_tier
        row["uncertainty_flags"] = ",".join(flags)

        missing = [k for k, v in pillar_vals.items() if v is None]
        missing.extend(
            ["thermal_cold_severe", "urban_form", "care_access", "hazards", "transit", "heritage"]
        )
        if wc_green is None:
            missing.append("worldcover_green")
        row["missing_dimensions"] = ",".join(missing)

        # Experimental zero-vs-unobserved observation statuses
        rest = row.get("restaurant_count")
        mus = row.get("museum_culture_count")
        transit = row.get("transit_stops_5km")
        parks = row.get("park_count_5km")
        if (rest or 0) > 0:
            row["poi_observation_status"] = "observed"
        elif (mus or 0) > 0:
            row["poi_observation_status"] = "unobserved_restaurants"
        else:
            row["poi_observation_status"] = "unobserved"
        row["transit_observation_status"] = (
            "observed" if (transit or 0) > 0 else "unobserved"
        )
        row["park_observation_status"] = (
            "observed" if (parks or 0) > 0 else "unobserved"
        )

        row["provenance_summary"] = PROVENANCE_SUMMARY
        row["source"] = SOURCE
        row["observed_period"] = OBSERVED_PERIOD
        scored.append(row)
    return scored


def _table_columns(cur: psycopg.Cursor, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    )
    return {r["column_name"] for r in cur.fetchall()}


def _pick_column(columns: set[str], candidates: Sequence[str]) -> str | None:
    for c in candidates:
        if c in columns:
            return c
    return None


def _load_ghsl_themes(cur: psycopg.Cursor) -> dict[int, dict[str, Any]]:
    themes: dict[int, dict[str, Any]] = {}
    g_cols = _table_columns(cur, "urban_centre_greenness")
    ndvi_col = _pick_column(g_cols, GREENNESS_NDVI_SCORE_CANDIDATES)
    canopy_col = _pick_column(g_cols, GREENNESS_CANOPY_CANDIDATES)
    if ndvi_col or canopy_col:
        sel = ["urban_centre_id"]
        if ndvi_col:
            sel.append(f'"{ndvi_col}" AS ghsl_ndvi_score')
        if canopy_col:
            sel.append(f'"{canopy_col}" AS ghsl_canopy_m')
        cur.execute(f"SELECT {', '.join(sel)} FROM public.urban_centre_greenness")
        for r in cur.fetchall():
            entry = themes.setdefault(int(r["urban_centre_id"]), {})
            if ndvi_col:
                entry["ghsl_ndvi_score"] = r["ghsl_ndvi_score"]
            if canopy_col:
                entry["ghsl_canopy_m"] = r["ghsl_canopy_m"]

    c_cols = _table_columns(cur, "urban_centre_climate")
    heat_col = _pick_column(c_cols, CLIMATE_HEAT_CANDIDATES)
    if heat_col:
        cur.execute(
            f'SELECT urban_centre_id, "{heat_col}" AS ghsl_heat_stress_days '
            "FROM public.urban_centre_climate"
        )
        for r in cur.fetchall():
            themes.setdefault(int(r["urban_centre_id"]), {})[
                "ghsl_heat_stress_days"
            ] = r["ghsl_heat_stress_days"]
    return themes


INSERT_COLUMNS = [
    "destination_id",
    "name",
    "country_iso2",
    "urban_centre_id",
    "settlement_class",
    "centre_population",
    "gdp_per_capita_ppp",
    "pm25_ugm3",
    "air_score",
    "water_distance_km",
    "waterfront_score",
    "ghsl_heat_stress_days",
    "thermal_heat_score",
    "thermal_safety_score",
    "worldcover_green_score",
    "green_method",
    "ghsl_ndvi_score",
    "ghsl_canopy_m",
    "urban_form_score",
    "biophysical_board_score",
    "homicide_per_100k",
    "safety_prior_score",
    "safely_managed_water_pct",
    "safely_managed_sanit_pct",
    "wash_prior_score",
    "institutional_board_score",
    "care_access_score",
    "hazard_score",
    "ghsl_hazard_events",
    "transit_score",
    "heritage_score",
    "median_down_mbps",
    "median_up_mbps",
    "latency_ms",
    "broadband_total_tests",
    "broadband_tile_count",
    "broadband_score",
    "services_board_score",
    "quality_score",
    "cohort_name",
    "construct_coverage",
    "data_completeness",
    "tier",
    "uncertainty_tier",
    "uncertainty_flags",
    "missing_dimensions",
    "walkability_score",
    "transit_stops_5km",
    "transit_observation_status",
    "museum_culture_count",
    "restaurant_count",
    "cuisine_entropy",
    "poi_observation_status",
    "park_count_5km",
    "park_observation_status",
    "price_level_vs_usd",
    "out_of_pocket_pct",
    "airport_nearest_km",
    "provenance_summary",
    "source",
    "observed_period",
]


def load(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            themes = _load_ghsl_themes(cur)
            cur.execute(BASE_QUERY)
            raw_rows = [dict(r) for r in cur.fetchall()]

        for r in raw_rows:
            uc_id = r.get("urban_centre_id")
            if uc_id is not None and int(uc_id) in themes:
                r.update(themes[int(uc_id)])

        scored = score_rows(raw_rows)

        placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
        insert_sql = (
            f"INSERT INTO city_quality_index_all ({', '.join(INSERT_COLUMNS)}) "
            f"VALUES ({placeholders})"
        )
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.executemany(
                insert_sql,
                [tuple(r.get(col) for col in INSERT_COLUMNS) for r in scored],
            )
            cur.execute(
                """
                SELECT count(*) AS destinations,
                       count(*) FILTER (WHERE settlement_class = 'ghsl_centre') AS ghsl_destinations,
                       count(*) FILTER (WHERE settlement_class = 'satellite_suppressed') AS satellite_suppressed,
                       count(*) FILTER (WHERE settlement_class = 'unmatched_town') AS unmatched_towns,
                       count(*) FILTER (WHERE tier = 'A') AS tier_a,
                       count(*) FILTER (WHERE tier = 'B') AS tier_b,
                       count(*) FILTER (WHERE tier = 'C') AS tier_c,
                       round(avg(construct_coverage)::numeric, 3) AS avg_construct_coverage
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
                SELECT name, country_iso2, tier,
                       round(quality_score::numeric, 1) AS quality,
                       round(biophysical_board_score::numeric, 1) AS biophys,
                       round(institutional_board_score::numeric, 1) AS instit,
                       round(broadband_score::numeric, 1) AS broadband,
                       round(construct_coverage::numeric, 2) AS cov
                FROM city_quality_index_all
                WHERE tier = 'A'
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
