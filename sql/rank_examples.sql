-- Example calls for rank_destinations_v2().
-- Run with: psql "$SUPABASE_DB_URL" -f sql/rank_examples.sql
--
-- Signature:
--   rank_destinations_v2(
--     p_months INT[], p_ideal_temp_min, p_ideal_temp_max, p_max_temp_c,
--     p_min_population, p_countries TEXT[], p_require_airport,
--     p_w_climate, p_w_culture, p_w_food, p_w_low_crowding, p_limit
--   )

-- 1. One month in June, airport required, global.
SELECT name, country_iso2, population, iata_codes, final_score,
       data_completeness, metrics
FROM rank_destinations_v2(
    p_months => ARRAY[6],
    p_ideal_temp_min => 20.0,
    p_ideal_temp_max => 26.0,
    p_max_temp_c => 31.0,
    p_require_airport => TRUE,
    p_limit => 20
);

-- 2. Four-month stay (June-September), no month above 28 °C.
SELECT name, country_iso2, population, iata_codes, final_score,
       data_completeness, metrics
FROM rank_destinations_v2(
    p_months => ARRAY[6, 7, 8, 9],
    p_ideal_temp_min => 20.0,
    p_ideal_temp_max => 26.0,
    p_max_temp_c => 28.0,
    p_require_airport => TRUE,
    p_limit => 20
);

-- 3. Country-scoped: spring in Spain and Portugal.
SELECT name, country_iso2, population, iata_codes, final_score,
       data_completeness, metrics
FROM rank_destinations_v2(
    p_months => ARRAY[4, 5],
    p_countries => ARRAY['ES', 'PT'],
    p_limit => 15
);

-- 4. Skip the airport requirement (e.g. rail-focused trips).
SELECT name, country_iso2, population, iata_codes, final_score,
       data_completeness, metrics
FROM rank_destinations_v2(
    p_months => ARRAY[9],
    p_ideal_temp_min => 17.0,
    p_ideal_temp_max => 24.0,
    p_max_temp_c => 30.0,
    p_require_airport => FALSE,
    p_limit => 15
);

-- 5. Climate-only preview for cities without POIs yet.
SELECT name, country_iso2, population, final_score, data_completeness,
       metrics->>'avg_max_c' AS avg_max_c,
       metrics->>'avg_precip_mm' AS avg_precip_mm
FROM rank_destinations_v2(
    p_months => ARRAY[6],
    p_require_airport => TRUE,
    p_limit => 20
)
WHERE data_completeness < 0.6;
