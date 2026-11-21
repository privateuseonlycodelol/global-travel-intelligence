-- Example calls for rank_destinations_v3() (Phase 3, eight-dimension scorer).
-- Run with: psql "$SUPABASE_DB_URL" -f sql/rank_examples_v3.sql
--
-- Phase 3 tables read by the scorer:
--   broadband_metrics  (Ookla fixed broadband medians, 5 km radius)
--   mobility_metrics   (Overture walk/rail segments + GTFS active feeds)
--   housing_monthly    (optional Inside Airbnb 30+ night medians)
-- Phase 1 tables read as before:
--   destinations, climate_monthly, poi_metrics, tourism_pressure
--
-- Signature:
--   rank_destinations_v3(
--     p_months INT[], p_ideal_temp_min, p_ideal_temp_max, p_max_temp_c,
--     p_min_population, p_countries TEXT[], p_require_airport,
--     p_exclude_schengen, p_min_broadband_mbps, p_require_gtfs,
--     p_w_climate, p_w_culture, p_w_food, p_w_walk, p_w_transit,
--     p_w_broadband, p_w_low_crowding, p_w_housing, p_limit
--   )
--
-- Weights need not sum to exactly 1. Missing dimensions are neutral-imputed at
-- 0.5 for the score; data_completeness still reports how much real evidence a
-- row has. Each of the eight weights must stay in 0..1.

-- ---------------------------------------------------------------------------
-- 1. Short break: 3 days in June, mild and dry, big-city amenities.
--
-- Short trips do not need GTFS coverage at the destination: you walk or take
-- a taxi for three days. Do not set p_require_gtfs => TRUE here; it would
-- discard cities that simply have no published feed. Broadband is equally
-- irrelevant for a long weekend, so p_min_broadband_mbps stays NULL and the
-- broadband weight is zeroed.
--
-- Short-stay weights: climate 0.25, culture 0.25, food 0.20, walk 0.15,
-- transit 0.10, broadband 0.00, low crowding 0.05, housing 0.00.
-- ---------------------------------------------------------------------------
SELECT name, country_iso2, population, iata_codes,
       final_score, data_completeness,
       metrics->>'avg_max_c'             AS avg_max_c,
       metrics->>'avg_rainy_days_mo'     AS avg_rainy_days_mo,
       metrics->>'pedestrian_ratio_2km'  AS pedestrian_ratio_2km,
       provenance
FROM rank_destinations_v3(
    p_months             => ARRAY[6],
    p_ideal_temp_min     => 20.0,
    p_ideal_temp_max     => 26.0,
    p_max_temp_c         => 32.0,
    p_min_population     => 100000,
    -- Optional country filter; uncomment and edit to scope the search.
    -- p_countries       => ARRAY['ES', 'PT'],
    p_require_airport    => TRUE,
    p_exclude_schengen   => FALSE,
    p_min_broadband_mbps => NULL,
    p_require_gtfs       => FALSE,
    p_w_climate          => 0.25,
    p_w_culture          => 0.25,
    p_w_food             => 0.20,
    p_w_walk             => 0.15,
    p_w_transit          => 0.10,
    p_w_broadband        => 0.00,
    p_w_low_crowding     => 0.05,
    p_w_housing          => 0.00,
    p_limit              => 10
);

-- ---------------------------------------------------------------------------
-- 2. Extended stay: 4 months from June onward, work-friendly.
--
-- This is the case where broadband and GTFS matter: a fast, reliable line and
-- a published transit feed are baseline requirements, not nice-to-haves.
--
-- Extended-stay weights: climate 0.20, culture 0.10, food 0.10, walk 0.20,
-- transit 0.15, broadband 0.15, low crowding 0.05, housing 0.05.
--
-- p_exclude_schengen implements only the 90/180-day short-stay visitor rule
-- for non-EU/EEA passports (29-country Schengen area as of 2026). It is a
-- convenience filter, not legal advice, and it says nothing about national
-- visas, residence permits, tax residency, or bilateral agreements. Verify
-- entry rules with the relevant consulate before relying on them.
-- ---------------------------------------------------------------------------
SELECT name, country_iso2, population, iata_codes,
       final_score, data_completeness,
       metrics->>'median_down_mbps'      AS median_down_mbps,
       metrics->>'gtfs_active_feeds'     AS gtfs_active_feeds,
       metrics->>'median_monthly_local'    AS median_monthly_local,
       metrics->>'nights_per_resident'   AS nights_per_resident,
       provenance
FROM rank_destinations_v3(
    p_months             => ARRAY[6, 7, 8, 9],
    p_ideal_temp_min     => 20.0,
    p_ideal_temp_max     => 26.0,
    p_max_temp_c         => 29.0,
    p_min_population     => 40000,
    p_countries          => NULL,
    p_require_airport    => TRUE,
    p_exclude_schengen   => TRUE,
    p_min_broadband_mbps => 50.0,
    p_require_gtfs       => TRUE,
    p_w_climate          => 0.20,
    p_w_culture          => 0.10,
    p_w_food             => 0.10,
    p_w_walk             => 0.20,
    p_w_transit          => 0.15,
    p_w_broadband        => 0.15,
    p_w_low_crowding     => 0.05,
    p_w_housing          => 0.05,
    p_limit              => 15
);

-- ---------------------------------------------------------------------------
-- 3a. Inspect one city by destination_id, including the raw Phase 3 metrics
--     and the provenance recorded for each dimension.
-- ---------------------------------------------------------------------------
SELECT d.destination_id,
       d.name,
       d.country_iso2,
       d.population,
       d.iata_codes,
       b.median_down_mbps,
       b.median_up_mbps,
       b.avg_latency_ms,
       b.total_tests            AS broadband_tests,
       b.observed_period        AS broadband_period,
       m.pedestrian_ratio_2km,
       m.rail_segments_5km,
       m.gtfs_active_feeds,
       m.observed_period        AS mobility_period,
       h.median_monthly_local,
       h.sample_listings,
       h.observed_period        AS housing_period
FROM destinations d
LEFT JOIN broadband_metrics b ON b.destination_id = d.destination_id
LEFT JOIN mobility_metrics  m ON m.destination_id = d.destination_id
LEFT JOIN housing_monthly   h ON h.destination_id = d.destination_id
WHERE d.destination_id = 'gn-2960316';   -- Luxembourg City; edit as needed

-- ---------------------------------------------------------------------------
-- 3b. Twelve-month climate curve for the same city, with missing months
--     materialised as rows so the curve is easy to plot.
-- ---------------------------------------------------------------------------
SELECT m.month,
       c.median_temp_max_c,
       c.median_temp_min_c,
       c.precip_mm,
       c.avg_rainy_days,
       c.avg_hot_days_30c,
       c.source,
       c.observed_period
FROM generate_series(1, 12) AS m(month)
LEFT JOIN climate_monthly c
       ON c.destination_id = 'gn-2960316'
      AND c.month = m.month
ORDER BY m.month;
