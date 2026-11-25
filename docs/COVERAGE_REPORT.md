# Coverage and Rating Verification Snapshot

Database: local PostGIS 15, database `travel_global`
Date: **2026-11-25** (walkability scores added 2026-11-22; climate, air, rent, facilities, geography, hazards and advisories added 2026-11-25)
Purpose: record what is actually loaded, what is missing, and the checks used
before trusting a ranking. This is evidence, not a claim that these are the
final production numbers.

## 1. Row counts

| Table / field | Rows | Notes |
| --- | ---: | --- |
| `destinations` | 13,101 | GeoNames global ≥40k, after metro dedupe |
| `destinations` with IATA | 11,073 | OurAirports spatial match |
| `climate_monthly` | 157,188 | WorldClim 2.1, 12 months × 13,099 cities |
| `climate_monthly.avg_rainy_days` non-null | 0 | WorldClim baseline has no wet-day field; recent counts are in `climate_recent` |
| `climate_monthly.avg_hot_days_30c` non-null | 0 | WorldClim baseline only |
| `climate_recent` | 56,148 | 4,679 cities × 12 months, NASA POWER 2015-2024 |
| `poi_metrics` | 13,101 | Overture Places global scan |
| `broadband_metrics` | 11,902 | Ookla 2026 Q2 fixed tiles |
| `mobility_metrics` | 9,553 | GTFS + transit stops + walk/rail rows |
| `mobility_metrics` with walk/rail | 1,131 | Overture Transportation loaded for DE, GB, TR, ES, FR, PT, SE and LU |
| `walkability_scores` | 662 | How Walkable 2026: 690 ranked cities, 662 matched by name/alias |
| `ghsci_city_indicators` | 25 | GHSCI: Trento pilot + 24 of the published 25-city study |
| `air_quality_annual` | 13,101 | SatPM V6.GL.03 annual PM2.5, 2024, 0.1° point sample |
| `air_quality_monthly` | 157,212 | SatPM V6.GL.03 monthly PM2.5, 13,101 cities × 12 months, 2024 |
| `destination_metro` | 9,730 | GHSL UCDB R2024A polygon containment (9,065) or ≤5 km edge match (665) |
| `ghsl_urban_centres` | 11,422 | GHSL UCDB R2024A urban-centre polygons, WGS84 |
| `mobility_metrics` with GTFS > 0 | 4,000 | Mobility Database |
| `mobility_metrics` with transit stops | 9,008 | Overture Places transit facilities within 5 km |
| `tourism_pressure` | 1,405 | Eurostat NUTS 2 2024; EU/EFTA only |
| `housing_monthly` | 0 | Optional Inside Airbnb load not run globally |
| `rent_monthly` | 6,725 | Eurostat 41 cities (2025) + ONS 121 GB areas (2026-10) + HUD 1,087 US cities (FY2026), local currency |
| `travel_advisory` | 439 | US State (214) + UK FCDO (225) country advisories, retrieved 2026-11-25 |
| `facility_metrics` | 13,101 | Overture Places counts within 5 km: hospital, pharmacy, school, grocery, park, beach |
| `airport_access` | 13,101 | OurAirports nearest scheduled-service airport + 100 km counts |
| `destination_geography` | 13,101 | GeoNames elevation (13,084) + Natural Earth coastline distance (13,101) |
| `urban_centre_hazard` | 11,422 | GHSL UCDB hazard theme: MYRIAD-HES event counts, 2015 window |
| `urban_centre_health` | 11,422 | GHSL UCDB health theme: hospital/pharmacy counts and 1 km access share |
| `tourism_monthly` | 16,860 | Eurostat NUTS 2 monthly nights, 1,405 cities × 12 months, 2024 |
| `gtfs_feed` | 571 | Active GTFS feeds processed; initial Italy/France batch, growing |
| `gtfs_feed_place` | 2,101 | Feeds with stops mapped to a city, not bbox-only |
| `transit_headway` | 3,952 | City × feed × day type × time band headway/span rows; 544 cities |
| `transit_city_weekday_peak` (view) | 155 | Cities with a weekday 07:00–09:00 headway and at least 50 mapped stops |
| `destination_landscape` | 12,927 | RESOLVE Ecoregions 2017 point-in-polygon match |
| `destination_terrain` | 13,101 | Amatulli 2018 GMTED 10 km median elevation, SD, TRI, TPI |
| `destination_land_cover` | 10,000 | ESA WorldCover 2021, 5,000 largest cities × 1 km and 15 km; growing |
| `destination_inland_water` | 13,101 | HydroLAKES nearest lake/reservoir shore, area and type; nearest lake ≥10 km² |
| `destination_climate_class` | 13,101 | Köppen-Geiger 1991-2020 class at the city point |
| `destination_river_access` | 13,101 | HydroRIVERS nearest Strahler ≥4 reach, order, discharge and distance |
| `destination_green_score` (view) | 5,000 | Optional land-only vegetation-cover score for cities with 1 km and 15 km land cover |
| `destination_greenery_score` (view) | 4,038 scored | Physical greenery: 25% local tree, 15% local other, 45% hinterland, 15% parks |
| `destination_environment_score` (view) | 4,038 scored | Environmental quality: 50% greenery, 35% air, 15% water access |
| `destination_combined_score` (view) | 4,038 scored | Combined: 50% greenery + 50% environment (effective 75/17.5/7.5) |
| `destination_all_green_score` (view) | 4,038 scored | All-encompassing: vegetation 40%, parks 20%, air 20%, water 20% |
| `destination_urban_land_cover` | 516 | WorldCover fractions inside the GHSL urban-centre polygon |
| `destination_urban_greenery` (view) | 516 | Urban tree/green scores inside the GHSL polygon, native 10 m |
| `destination_ghsl_greenery` (view) | 516 | GHSL-only physical greenery, nature flags, environment |
| `city_quality_index` | 516 | GHSL >=1M objective quality index; 445 with completeness >= 0.90 |
| `city_quality_index_all` | 13,101 | All-destination quality index; 10,389 principal cities, 2,316 completeness >= 0.90 |
| `destination_surrounding_nature` (view) | 5,000 | 1–15 km annulus tree+shrub shares |
| `flight_quote_cache` / `hotel_quote_cache` | 0 | Populated only by MCP stage 2 with SerpApi key |
| `destination_aliases` | 188,403 | GeoNames names/alternatenames for all 13,101 cities (`bootstrap_places.py`) |
| `country_facts` | 252 | GeoNames countryInfo; every destination has a country row |
| `country_indicators` | 2,454 | World Bank: homicide 177, PM2.5 200, life expectancy 216, GDP PPP 201, political stability 207, price level 202, physicians 193, beds 164, road deaths 190, out-of-pocket 193, safely managed water 151, sanitation 146, basic water 214 |
| `destination_admin1` | 13,094 | GeoNames first-level region (state/province) codes; 13,071 with a name |

## 2. What is missing and why

| Dimension | Status | Reason / next official source |
| --- | --- | --- |
| Walkability / rail | Partial (1,131 cities) | Overture Transportation is loaded for DE, GB, TR, ES, FR, PT, SE and LU. The global scan hit repeated DNS/OOM failures; run by country/region. |
| Walkability score | Partial (662 cities) | Published How Walkable 0-100 score for 690 cities of 300,000+; 662 matched to loaded destinations. Mainland China, South Korea and Russia are not ranked; smaller cities stay NULL. |
| Public transport stops | Broad (9,008 cities) | Overture Places transit facilities; not a complete stop-position/frequency feed. |
| Scheduled transit | Partial (Italy/France initial) | GTFS weekday peak, midday and Sunday headways; grows with country batches. |
| Tourism pressure | Partial (EU only) | Eurostat NUTS 2 annual and monthly nights for 1,405 cities; no global city-level official source. |
| Safety / crime | Country context loaded | US State levels 1-4 and UK FCDO advice for ~220 countries; no global city-level crime rate. |
| Long-term rent | Partial (~1,250 cities) | Eurostat city averages (41), ONS GB local-authority averages (121) and HUD US FMRs (1,087), in local currency. |
| Rainy/hot days | Partial (4,679 cities) | NASA POWER 2015-2024 monthly counts in `climate_recent`; the rest are not yet fetched. |
| Air quality | Loaded, not scored | SatPM V6.GL.03 annual and monthly city PM2.5 for 13,101 cities; exposed as a metric and seasonal block, not in `final_score`. |
| Facilities | Loaded, not scored | Overture Places presence counts for hospitals, pharmacies, schools, groceries, parks and beaches; zero is `none_listed`. |
| Airport access | Loaded, not scored | Nearest scheduled-service airport and large/medium counts within 100 km. |
| Natural hazards | Partial, metro only | Urban-centre event counts for 11 hazard types (MYRIAD-HES, 2015 window); no live alerts. |
| Healthcare access | Partial, metro only | Hospital/pharmacy counts and 1 km population access share from OSM/healthsites. |
| Metro clustering | Implemented (9,730 cities) | GHSL UCDB R2024A polygon containment; 3,371 cities below the UCDB cutoff stay individual. Rank dedupe is display-level. |

## 3. Transport and walkability

| Measurement | Coverage | Meaning |
| --- | ---: | --- |
| Overture pedestrian context (`pedestrian_ratio_2km`) | 1,131 cities | 8-country sample; context only, not the ranking score |
| Walkability score (`walkability_score`) | 662 cities | Published How Walkable 0-100: everyday places within a walk, averaged over the 5 km around the busiest point; used by the ranking where present |
| Walkability at the busiest point (`walkability_centre_score`) | 662 cities | Same source, single point; shows the core-vs-area gap (Phoenix 99 vs 56) |
| Transit facility count (`transit_stops_5km`) | 9,008 cities | Overture Places bus/train/metro/light-rail/public-transit facilities within 5 km |
| GTFS feed count (`gtfs_active_feeds`) | 4,000 cities | Mobility Database active static feeds whose bbox contains the city |
| Rail segments (`rail_segments_5km`) | 1,131 cities | Overture Transportation rail segments within 5 km |

The transport score combines available components as 40% transit facilities,
30% GTFS feeds, and 30% rail segments, renormalized when a component is
missing. Walkability combines the pedestrian-road ratio (60%) and pedestrian
segment count (40%). Neither is a frequency, reliability or journey-time
measure.

## 4. Rating checks

Run against `rank_destinations_v3` with all 12 months, `p_max_temp_c => NULL`,
and `p_limit => 20000`:

| Check | Result |
| --- | --- |
| Rows returned | 13,101 |
| `MIN(final_score)` | 0.1969 |
| `MAX(final_score)` | 0.8728 |
| `MIN(data_completeness)` | 0.50 |
| `MAX(data_completeness)` | 0.95 |
| NaN/Infinity scores | 0 |
| NaN/Infinity completeness | 0 |
| Rows with crowding evidence | 1,405 |
| Rows with walk/rail evidence | 1,131 |
| Rows with GTFS evidence | 4,000 |
| Rows with transit-stop evidence | 9,008 |
| Rows with broadband evidence | 11,902 |

Syntax and edge-case checks performed:

```sql
-- Empty optional filters must not exclude everything
SELECT count(*) FROM rank_destinations_v3(p_months=>ARRAY[6], p_max_temp_c=>NULL, p_limit=>20000);
SELECT count(*) FROM rank_destinations_v3(p_months=>ARRAY[6], p_min_population=>NULL, p_limit=>20000);
SELECT count(*) FROM rank_destinations_v3(p_months=>ARRAY[6], p_require_gtfs=>NULL, p_limit=>20000);

-- Negative limit is empty, not an error
SELECT count(*) FROM rank_destinations_v3(p_months=>ARRAY[6], p_limit=>-1);

-- Single-city relative dimensions are neutral, not zero
SELECT name, final_score, data_completeness
FROM rank_destinations_v3(p_months=>ARRAY[6], p_ideal_temp_min=>15,
                          p_countries=>ARRAY['LU'], p_limit=>5);
```

Observed results:

- `p_max_temp_c => NULL`: 13,101 rows.
- `p_min_population => NULL`: 13,101 rows.
- `p_require_gtfs => NULL`: 13,101 rows.
- `p_limit => -1`: 0 rows, no error.
- Luxembourg single-city query: score `0.576`, completeness `0.65` in the
  separate test database; relative dimensions score `0.5` instead of `0`.
- Global top-10 with `p_min_population => 200000` is San Francisco, London,
  Madrid, New York City, Paris, Brussels, Barcelona, Los Angeles, Seattle and
  Köln. Each row carries a `metro_group`; only one row per GHSL urban centre is
  returned by default. London boroughs and metro neighbours are therefore
  consolidated, not separate winners.

## 5. MCP verification

Against the global database:

```text
GET /health
{"status":"ok","database":"ok","cities_loaded":13101,
 "serpapi_configured":false,"live_pricing_enabled":false,
 "quote_ttl_hours":12.0,"mcp_path":"/mcp"}

MCP tools listed:
search_places, place_profile, compare_places, rank_destinations,
inspect_destination, refresh_live_quotes

rank_destinations(start_month=6, ideal_temp_min=20, ideal_temp_max=26,
                  min_population=200000, limit=3)
→ San Francisco, London, Paris. Each row carries a metro_group; the London
  group includes all matched boroughs, and only one row per GHSL centre is
  returned by default.

inspect_destination(city_name="Barcelona", country_iso2="ES")
→ 12 climate rows, 12 recent monthly normals, city PM2.5, rent, facilities,
  elevation/coast distance, airport access, monthly tourism, metro assignment
  and both country advisories. housing_monthly is NULL because Inside Airbnb
  was not loaded; rent_monthly is the official local-currency series.
```

Stage 2 correctly reports `disabled_missing_serpapi_key` when no key is set;
`refresh_live_quotes` raises a clear error rather than fabricating quotes.

## 6. Third-party attribution

Full details, licences and candidate sources are in
[`DATA_SOURCES.md`](DATA_SOURCES.md). The short version:

- **WorldClim**: non-commercial/academic use; redistribution/commercial use
  requires permission.
- **Ookla**: CC BY-NC-SA 4.0; non-commercial; attribution.
- **Overture Transportation**: ODbL; © OpenStreetMap contributors; share-alike.
- **Overture Places**: CDLA-Permissive 2.0 (some sources Apache 2.0/CC0).
- **Mobility Database**: catalog CC0; individual feeds retain provider terms.
- **Inside Airbnb**: CC BY 4.0; local currency.
- **Eurostat**: reuse with attribution, subject to the Eurostat copyright
  notice.
- **SerpApi**: proprietary API terms.

## 7. Honest conclusion

Functionality is verified end to end. The remaining gaps are data-coverage
gaps, not fabricated values:

- Walkability/rail is loaded for 1,131 cities across DE/GB/TR/ES/FR/PT/SE/LU, not globally.
- Tourism pressure is official but only NUTS 2/EU.
- Safety/crime remains country-level: US State and UK FCDO advisories plus the
  national homicide rate; there is no global city crime rate.
- Long-term rent is official but limited to about 160 Eurostat/ONS cities; there
  is no global city-level open rent series in the checked set.
- Missing dimensions are reported as NULL and reflected in
  `data_completeness`; the scorer's `0.5` neutral value is arithmetic only.
