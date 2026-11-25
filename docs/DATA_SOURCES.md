# Data Sources, Licences and Third-Party Provenance

Status date: **2026-11-25**. All external datasets below are **third-party
data**. The only first-party artifacts in this repository are the loader code,
the derived scores/percentiles, `data_completeness`, and the ranking SQL. No
missing city-level value is invented; see [Missing data policy](#missing-data-policy).

This document is deliberately explicit about what is **implemented and tested**
versus what is only a **verified candidate**. A candidate is not loaded into the
database just because its source exists.

## 1. Provenance rules used by the loaders

1. Every table that stores an external observation records `source` and
   `observed_period` (or a source string such as `Overture ...` and a release).
2. A missing value stays `NULL`; it is not replaced by a country average,
   another city's value, a synthetic number, or an LLM-generated estimate.
3. The v3 scorer may neutral-impute a missing dimension at `0.5` for ranking
   arithmetic. This is a scoring convention, **not an observation**. The
   returned `data_completeness` and `metrics` still show what is missing.
4. Scores (`final_score`), percentile ranks and `data_completeness` are
   **derived first-party calculations** from third-party inputs.
5. Live flight/hotel quotes are third-party SerpApi/Google data, cached with a
   TTL; they are not stored as historical facts.
6. Licences differ by source. Several sources are **not** suitable for
   unrestricted commercial redistribution; see the warnings in each entry.

## 2. Quality tiers

| Tier | Meaning | Examples |
| --- | --- | --- |
| A | Official statistical agency or primary measurement with published methodology | Eurostat, HUD, ONS, FBI UCR, UK Police, Copernicus/ERA5 |
| B | Open collaborative or derived geospatial dataset with a clear licence | Overture, OpenStreetMap, OurAirports, GeoNames |
| C | Commercial/proprietary API used only for an optional feature | SerpApi |
| Excluded | Crowd-sourced, unaudited, or unsuitable granularity for a deterministic city ranking | Numbeo, Walk Score, Teleport |

## 3. Implemented sources

### 3.1 GeoNames city list

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | GeoNames |
| Dataset | `cities15000.zip` (daily dump; cities with population ≥ 15,000) |
| URL | https://download.geonames.org/export/dump/cities15000.zip |
| Landing/terms | https://www.geonames.org/export/ |
| Licence | CC BY (GeoNames terms; attribution required; “free” data, provided “as is”) |
| Coverage | Global |
| Granularity | Place / city record |
| Used by | `bootstrap_global_40k.py` → `destinations` |
| Status | **Implemented, tested** |
| Notes | Population is city-proper, not metro. Feature codes and admin codes drive the metro-dedupe heuristics. |

Verified on 2026-11-21: the GeoNames export page states “cc-by licence” and
requires credit to GeoNames. The exact CC BY version is not printed on that
page; confirm the current GeoNames terms before redistribution.

### 3.2 OurAirports airports

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | OurAirports / David Megginson |
| Dataset | `airports.csv` |
| URL | https://davidmegginson.github.io/ourairports-data/airports.csv |
| Repository | https://github.com/davidmegginson/ourairports-data |
| Licence | Unlicense / public domain (repository licence metadata) |
| Coverage | Global |
| Granularity | Airport |
| Used by | `bootstrap_global_40k.py` → `destinations.iata_codes` |
| Status | **Implemented, tested** |
| Notes | Only large/medium scheduled-service airports with IATA codes are matched; straight-line radius, not travel time. |

### 3.3 WorldClim 2.1 climate

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | WorldClim |
| Dataset | WorldClim 2.1, 5 arc-minute monthly tmax/tmin/prec GeoTIFFs, 1970–2000 |
| URL | https://www.worldclim.org/data/worldclim21.html |
| Licence page | https://worldclim.org/about.html |
| Licence | **Free for academic and other non-commercial use. Redistribution or commercial use is not allowed without prior permission.** |
| Coverage | Global land |
| Granularity | ~9 km raster, sampled at city centroid |
| Used by | `bootstrap_global_40k.py` → `climate_monthly` |
| Status | **Implemented, tested** |
| Notes | Monthly means of daily max/min; precipitation is monthly total. This is a 1970–2000 climatology, not recent daily weather. |

This licence is a hard constraint: the current database contains WorldClim
values. If you deploy commercially or redistribute the derived tables, obtain
permission or replace this source. Do not describe WorldClim as unrestricted
open data.

### 3.4 Overture Maps Places

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Overture Maps Foundation |
| Dataset | `theme=places/type=place`, release `2026-11-18.0` |
| URL | `s3://overturemaps-us-west-2/release/2026-11-18.0/theme=places/type=place/*` |
| Attribution/licence | https://docs.overturemaps.org/attribution/ |
| Licence | Places: CDLA-Permissive 2.0 for the named providers; some source data is Apache 2.0 or CC0. Check the per-country/provider list. |
| Coverage | Global, variable density |
| Granularity | Place/POI, bbox center, 5 km Haversine |
| Used by | `bootstrap_global_40k.py` → `poi_metrics` |
| Status | **Implemented, global run completed** |
| Notes | Counts are based on bbox centers; large parks/malls whose center is outside 5 km are missed. Generic `restaurant` is excluded from cuisine entropy but included in restaurant count. |

### 3.5 Overture Maps Transportation

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Overture Maps Foundation |
| Dataset | `theme=transportation/type=segment`, release `2026-11-18.0` |
| URL | `s3://overturemaps-us-west-2/release/2026-11-18.0/theme=transportation/type=segment/*` |
| Attribution/licence | https://docs.overturemaps.org/attribution/ |
| Licence | **ODbL**; © OpenStreetMap contributors; Transportation includes TomTom data. Share-alike applies to derived databases. |
| Coverage | Global source; 1,131 cities loaded across DE, GB, TR, ES, FR, PT, SE and LU in the reference run |
| Granularity | Road/rail segment bbox center; 2 km walk radius, 5 km rail radius |
| Used by | `bootstrap_phase3.py` → `mobility_metrics` |
| Status | **Implemented; 1,131 cities loaded across DE, GB, TR, ES, FR, PT, SE and LU (2026-11-21). A single global scan hit DNS/OOM limits; use country/region runs.** |
| Notes | Segment centers approximate long segments. Pedestrian classes: `footway`, `pedestrian`, `living_street`, `steps`, `cycleway`, `path`, `bridleway`. |

The global `mobility_metrics` table currently has GTFS coverage but **no
walk/rail values** from this source. That is reported as missing, not imputed.

### 3.6 Ookla Open Data (fixed broadband)

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Ookla |
| Dataset | Speedtest fixed performance tiles, 2026 Q2 (`2026-04-01_performance_fixed_tiles.parquet`) |
| URL | https://ookla-open-data.s3.amazonaws.com/parquet/performance/type=fixed/year=2026/quarter=2/2026-04-01_performance_fixed_tiles.parquet |
| Repository/terms | https://github.com/teamookla/ookla-open-data |
| Licence | **CC BY-NC-SA 4.0** (non-commercial; attribution; share-alike) |
| Coverage | Global tiles (regional availability can change) |
| Granularity | ~610 m tiles; tests-weighted median within 5 km |
| Used by | `bootstrap_phase3.py` → `broadband_metrics` |
| Status | **Implemented, global run completed (11,902 cities)** |
| Notes | Tile centroids `tile_x`/`tile_y`; average speeds in kbps; tile aggregates, not user-level tests. |

Verified 2026-11-21: HTTP 200, object size 353,986,294 bytes. The repository
README states the CC BY-NC-SA 4.0 licence and attribution wording.

### 3.7 Mobility Database GTFS catalog

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | MobilityData |
| Dataset | `feeds_v2.csv` catalog |
| URL | https://files.mobilitydatabase.org/feeds_v2.csv |
| Terms | https://mobilitydatabase.org/terms-and-conditions |
| Licence | Catalog metadata: **CC0 1.0**. API code: Apache 2.0. Individual feed contents: **each provider's own licence**. |
| Coverage | Global catalog (2,730 active, non-superseded static GTFS feeds with usable bboxes in the 2026-11-21 snapshot) |
| Granularity | Feed bounding box → city containment |
| Used by | `bootstrap_phase3.py` → `mobility_metrics.gtfs_active_feeds` |
| Status | **Implemented, global run completed (4,000 cities)** |
| Notes | Bbox coverage is not service frequency. Feeds wider than 15° are ignored; antimeridian-crossing feeds are skipped. |

### 3.8 Inside Airbnb (optional housing)

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Inside Airbnb |
| Dataset | City/regional `listings.csv.gz` dumps |
| URL | https://insideairbnb.com/get-the-data/ |
| Licence | **CC BY 4.0**; attribution required |
| Coverage | City/regional, not global |
| Granularity | Listing; nearest destination within 10 km |
| Used by | `bootstrap_phase3.py --airbnb-dir` → `housing_monthly` |
| Status | **Implemented and tested with a local fixture; no global load** |
| Notes | Prices are in the listing's **local currency**, not USD. No FX conversion. `median_monthly_local` is only comparable within a currency area. |

### 3.9 Eurostat NUTS 2 tourism pressure (optional supplemental)

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Eurostat / European Commission |
| Datasets | `tour_occ_nin2m` (nights spent at tourist accommodation establishments by month and NUTS 2 region) and `demo_r_pjangrp3` (population on 1 January by NUTS 3 region) |
| API | `https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/tour_occ_nin2m` and `.../demo_r_pjangrp3` |
| Geography | GISCO NUTS 2 2024: https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_01M_2024_4326_LEVL_2.geojson |
| Licence | Eurostat reuse with attribution. Editorial content is CC BY 4.0; statistical data reuse is authorised with source acknowledgement under the Eurostat copyright notice, subject to its exceptions. |
| Coverage | EU/EFTA/candidate countries; **not global** |
| Granularity | NUTS 2 annual total and monthly nights; assigned to a city by centroid; denominator is NUTS 2 population |
| Used by | `bootstrap_supplemental.py --eurostat-tourism` → `tourism_pressure`, `tourism_monthly` |
| Status | **Implemented: 1,405 annual rows and 16,860 monthly rows; 1,638 cities assigned to a NUTS 2 region** |
| Notes | This is a **regional pressure proxy**, not a city-level tourist count. NUTS 3 tourism is only optionally published from 2023 and is not used here. |

Verified API examples on 2026-11-21:

- `tour_occ_nin2m?time=2024&month=TOTAL&unit=NR&c_resid=TOTAL&nace_r2=I551-I553`
  returned 827 geo categories and 794 values; Cataluña (`ES51`) was
  88,670,688 nights in 2024.
- `demo_r_pjangrp3?time=2024&sex=T&age=TOTAL&unit=NR` returned 2,141 geo
  categories and 1,767 values.
- `tour_occ_ninat` with `geo=LU000` returned an empty geo dimension: that
  country dataset is **not** a NUTS 3 dataset.
- `crim_off_cat` with `geo=DE11` returned an empty geo dimension: that crime
  dataset is **country-level**, not regional/city-level.

### 3.10 Public transport stops (Overture Places)

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Overture Maps Foundation |
| Dataset | Overture Places, release `2026-11-18.0`; specific public-transport facility categories |
| URL | `s3://overturemaps-us-west-2/release/2026-11-18.0/theme=places/type=place/*` |
| Categories used | `bus_station`, `train_station`, `metro_station`, `public_transit_facility_or_service`, `light_rail_and_subway_station`, `ferry_terminal`, `tram_stop`, `bus_stop`, `railway_station`, `subway_station` |
| Excluded category | `travel_and_transportation` — too broad (travel agencies, parking, unrelated businesses) |
| Licence | Overture Places: CDLA-Permissive 2.0 for named providers; some sources Apache 2.0/CC0; see Overture attribution page |
| Coverage | Global where Overture Places has transit facilities; not a complete stop-position feed |
| Granularity | Transit facility count within 5 km, distinct place IDs |
| Used by | `bootstrap_transit_stops.py` → `mobility_metrics.transit_stops_5km` |
| Status | **Implemented; global run completed on 2026-11-21: 9,008 cities with at least one transit facility** |
| Notes | Counts stations/facilities, not individual bus stop poles. It is an access/supply measure, not frequency, reliability or journey time. Missing cities stay NULL. |

Verified global category counts on 2026-11-21 from the live Overture Places
release:

```text
train_station                        107,910
bus_station                           73,526
public_transit_facility_or_service    31,406
metro_station                          2,730
light_rail_and_subway_station          1,410
```

This is the public-transport measure with the broadest city coverage in the
project: it reuses the same global Overture Places scan pattern as the POI
loader, so cities outside GTFS-covered regions can still receive a transit
access count. The 2026-11-21 run populated **9,008 of 13,101** destinations;
the highest counts were London (510), City of Westminster (481), Tokyo (468),
Islington (449), Osaka (428), Paris (408), Geneva (392) and New York City
(364).

### 3.11 SerpApi live quotes (optional Phase 4)

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | SerpApi (Google Flights/Google Hotels results) |
| Dataset | Query-time results |
| URL | https://serpapi.com/search |
| Terms | https://serpapi.com/legal |
| Licence | Proprietary API terms; not open data |
| Coverage | Query-dependent |
| Granularity | Route/date flight quote; city/date hotel median |
| Used by | `mcp_server.py` → `flight_quote_cache`, `hotel_quote_cache` |
| Status | **Implemented; stage 2 gracefully skips without `SERPAPI_API_KEY`** |
| Notes | Cached for `QUOTE_TTL_HOURS` (default 12). Do not present cached quotes as live after expiry. |

### 3.12 GeoNames aliases and country facts

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | GeoNames |
| Dataset | `cities15000.zip` `alternatenames` and admin1 columns; `admin1CodesASCII.txt`; `countryInfo.txt` |
| Licence | CC BY 4.0 (attribution required) |
| Coverage | Every loaded destination; 252 countries/territories |
| Granularity | City name aliases; country facts (capital, currency, languages, phone prefix, neighbours) |
| Used by | `bootstrap_places.py` → `destination_aliases`, `destination_admin1`, `country_facts` |
| Status | **Implemented** |
| Notes | Aliases are normalized (case, accents, punctuation) and trigram-indexed for `search_places`. They include exonyms, native scripts and some airport codes. Official-name matches rank above alternate-name matches. |

### 3.13 World Bank country indicators

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | World Bank WDI and WGI (homicide series sourced from UNODC) |
| Dataset | `VC.IHR.PSRC.P5` homicides per 100k, `EN.ATM.PM25.MC.M3` PM2.5 exposure, `SP.DYN.LE00.IN` life expectancy, `NY.GDP.PCAP.PP.CD` GDP per capita PPP, `GOV_WGI_PV.EST` political stability, `PA.NUS.GDP.PLI` price level (stored as a ratio), `SH.MED.PHYS.ZS` physicians per 1,000, `SH.MED.BEDS.ZS` hospital beds per 1,000, `SH.STA.TRAF.P5` road traffic deaths per 100k, `SH.XPD.OOPC.CH.ZS` out-of-pocket health spending share, `SH.H2O.SMDW.ZS` safely managed drinking water, `SH.STA.SMSS.ZS` safely managed sanitation, and `SH.H2O.BASW.ZS` basic drinking water. Deriving PPP / exchange rate was rejected: it breaks on currency redenominations (Bulgaria, Liberia) and official pegs (Iran). |
| Licence | CC BY 4.0 |
| Coverage | 146-216 countries per indicator; latest non-null year is kept and its year stored |
| Granularity | **Country** |
| Used by | `bootstrap_places.py` → `country_indicators`; `place_profile` / `compare_places` |
| Status | **Implemented** |
| Notes | National values are returned under `country` with `granularity: country` and are never copied onto city rows or used in `rank_destinations_v3`. Some homicide values are a decade old; the year is returned with each value. |

### 3.14 Published walkability score (How Walkable)

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | How Walkable (Oscar Higson-Spence) |
| Dataset | City walkability ranking, 690 cities in 95 countries, 0-100 |
| URL | https://howwalkable.com/cities; data file https://howwalkable.com/cities.csv |
| Attribution | "How Walkable" (https://howwalkable.com), with a link to the city ranking. |
| Licence | **CC BY 4.0**; attribution and link required |
| Coverage | 690 cities of 300,000+ people in 95 countries; mainland China, South Korea and Russia are not ranked |
| Granularity | City centre plus the surrounding 5 km (not the whole metro, not a neighbourhood) |
| Used by | `bootstrap_walkability.py` → `walkability_scores`; `place_profile` / `compare_places` / `rank_destinations` |
| Status | **Implemented; 662 of 690 ranked cities matched to loaded destinations (631 by name, 31 by GeoNames alias)** |
| Method | Counts everyday places (groceries, cafes, shops, parks, services) within a walk at points across the 5 km around the city's busiest point, from Overture Maps and OpenStreetMap; `score` is the average across that area, `centre_score` is the single busiest point. |
| Notes | Amenity proximity, not a sidewalk/crossing/safety audit. A car-dependent city with a dense core can score well (Houston 79). 28 ranked labels, mostly Japanese and Philippine districts, did not match a loaded destination. The site's point API was tested against all 690 ranked cities: its point score runs a median 26 points above the published 5 km score (r = 0.35; Phoenix +43), so it is not used for missing cities. |

The 28 unmatched labels are kept out of the database rather than mapped to a
neighbouring core city. `rank_destinations_v3` uses the 0–100 score where it
exists; cities without it are neutral (0.5) and are reported via
`excluded_for_no_evidence` when no active dimension has evidence.

### 3.15 GHSCI pilot and 25-city study

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Global Healthy and Sustainable City Indicators collaboration |
| Dataset | 25-city spatial indicator package (2022-05-02), plus a local Trento pilot run with GHSCI R2024A inputs |
| URL | https://doi.org/10.25439/rmt.15072009.v3 |
| Licence | Open Database License (ODbL) for the study outputs; GHSCI code MIT |
| Coverage | 24 loaded study cities (Vic is below the 40k cutoff) + Trento |
| Granularity | Urban study region; population-weighted 500 m network access + 1 km neighbourhood means |
| Used by | `bootstrap_ghsci_study.py` / `bootstrap_ghsci.py` → `ghsci_city_indicators`; `place_profile` / `compare_places` / `inspect_destination` |
| Status | **Pilot only; no on-demand worker.** The 2021 study and 2026 Trento rows are separate `observed_period` values. |
| Notes | The study index is the published `all_cities_walkability`; Trento's is the 25-city-reference z-score. Neither is 0–100 and neither is merged with How Walkable. |

### 3.16 Recent climate normals

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | NASA POWER / MERRA-2 |
| Dataset | Daily 2 m max/min temperature and corrected precipitation, 2015-2024, sampled at each city |
| URL | https://power.larc.nasa.gov/docs/services/api/temporal/daily/ |
| Licence | NASA open data; attribution requested (confirm current terms) |
| Coverage | Loaded in batches by `bootstrap_climate_recent.py`; 4,679 cities at the 2026-11-25 snapshot, remaining cities not yet fetched |
| Granularity | City point; monthly normals |
| Used by | `bootstrap_climate_recent.py` → `climate_recent`; `place_profile.recent_climate` |
| Status | **Implemented loader; not yet global.** |
| Notes | Separate from WorldClim 1970-2000. Adds rainy days, days >=30 C, warm nights and recent precipitation. A city-point sample; not a neighbourhood climatology. |

### 3.17 Annual city PM2.5

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Atmospheric Composition Analysis Group / Washington University in St. Louis (SatPM) |
| Dataset | V6.GL.03 CNN annual and monthly PM2.5, 0.1 degree, 2024 |
| URL | https://www.satpm.org/ ; S3 `satpmdata` |
| Licence | CC BY 4.0; attribution required |
| Coverage | Global land between 60 S and 70 N; 13,101 loaded cities sampled in 2026 |
| Granularity | 0.1 degree grid (~11 km); city-point sample |
| Used by | `bootstrap_air_quality.py` → `air_quality_annual`; `bootstrap_air_quality_monthly.py` → `air_quality_monthly`; `place_profile` |
| Status | **Implemented; 13,101 cities** |
| Notes | Satellite/model/monitor-fused estimate, not a monitor reading; it cannot resolve near-road or neighbourhood differences. The monthly layer adds seasonality (for example Delhi's November peak versus the monsoon low). Kept separate from the country-level World Bank PM2.5 indicator. |

### 3.18 Daily-life facilities

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | Overture Maps Foundation |
| Dataset | Places release `2026-11-18.0`, categories hospital, pharmacy, school, grocery/supermarket, park and beach |
| URL | https://docs.overturemaps.org/ |
| Licence | CDLA-Permissive 2.0 |
| Coverage | 13,101 cities; counts within 5 km of the city centroid |
| Granularity | Point-of-interest count; presence only |
| Used by | `bootstrap_facilities.py` → `facility_metrics`; `place_profile.facilities` |
| Status | **Implemented, global scan** |
| Notes | A zero is `none_listed`, not proof that no facility exists. Coverage is uneven: Beijing has far fewer mapped pharmacies and schools than European cities. Counts are not quality or capacity, and Overture's hospital tag includes many clinics, so it is not a hospital count. |

### 3.19 Elevation and coastline distance

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | GeoNames; Natural Earth |
| Dataset | GeoNames `cities15000` DEM field; Natural Earth 10m coastline |
| URL | https://www.geonames.org/ ; https://www.naturalearthdata.com/ |
| Licence | GeoNames CC BY 4.0; Natural Earth public domain |
| Coverage | 13,101 cities |
| Granularity | City record elevation; straight-line centroid-to-coast distance |
| Used by | `bootstrap_geography.py` → `destination_geography`; `place_profile.geography` |
| Status | **Implemented** |
| Notes | Elevation is the city record's DEM value, not a neighbourhood gradient. Coast distance is not a road distance and does not identify a beach. Daylight hours are computed geometrically from latitude. |

### 3.20 Airport access

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | OurAirports |
| Dataset | `airports.csv`, scheduled-service airports |
| URL | https://ourairports.com/data/ |
| Licence | Public domain |
| Coverage | 13,101 cities; 4,134 scheduled-service airports |
| Granularity | Nearest airport plus large/medium counts within 100 km |
| Used by | `bootstrap_airport_access.py` → `airport_access`; `place_profile.airport_access` |
| Status | **Implemented** |
| Notes | Straight-line distance, not drive time. A nearby small field can be closer than the useful commercial hub, so both distance and type are shown. |

### 3.21 Urban-centre hazard exposure

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | European Commission JRC, GHS-UCDB R2024A |
| Dataset | Hazard theme: MYRIAD-HES multi-hazard event counts, 2015 window (2014-2016) |
| URL | https://human-settlement.emergency.copernicus.eu/ ; Zenodo record 8269680 |
| Licence | CC BY 4.0 (MYRIAD-HES); EC reuse notice for the UCDB compilation |
| Coverage | 11,422 urban centres, global |
| Granularity | Urban centre, by hazard type |
| Used by | `bootstrap_ucdb_themes.py` → `urban_centre_hazard`; `place_profile.hazard_exposure` |
| Status | **Implemented** |
| Notes | Recorded hazard counts, not a probability or forecast. `source_event_count` is retained as raw evidence; the profile headline is `hazard_occurrences`, split by type. A dash in the source is stored as zero. Conflict columns in the same UCDB file are not loaded because their ACLED/UCDP provenance has separate redistribution terms. A city without a GHSL metro match has no row. |

### 3.22 Urban-centre health access

| Field | Value |
| --- | --- |
| Party | Third-party |
| Publisher | European Commission JRC, GHS-UCDB R2024A; underlying healthsites.io/OSM |
| Dataset | Health theme: hospital and pharmacy counts, density, per-capita and 1 km access share |
| URL | https://human-settlement.emergency.copernicus.eu/ ; https://healthsites.io/ |
| Licence | EC reuse notice; OSM/healthsites data inherit ODbL |
| Coverage | 11,422 urban centres, global |
| Granularity | Urban centre |
| Used by | `bootstrap_ucdb_themes.py` → `urban_centre_health`; `place_profile.health_access` |
| Status | **Implemented** |
| Notes | Physical access, not quality of care. Inherits OSM coverage gaps; a low share can mean sparse mapping rather than no hospital. |

## 4. Missing dimensions and verified candidate sources

These sources were checked but are **not** loaded. They are listed so a
reviewer can see the gap and the intended official path. Do not treat a
candidate as present data.

### 4.1 Tourism pressure beyond the EU

| Candidate | Granularity / coverage | Licence / status | Assessment |
| --- | --- | --- | --- |
| Eurostat `tour_occ_nin2m` / `tour_occ_ninat` | NUTS 2 / country; EU/EFTA | Eurostat reuse with attribution; verified | **Implemented for NUTS 2 (see 3.9).** Country-level `tour_occ_ninat` is not city-level. |
| OECD Tourism Statistics | Country/regional; OECD members | OECD terms; verify | Candidate for national context, not city ranking. |
| UNWTO Tourism Statistics | Country; global membership | UNWTO terms; verify | Country-level only. |
| National/regional tourism boards | City/region; country-specific | Varies by publisher | Requires per-country ingestion; not a single global source. |

There is no single official, global, city-level tourism-night dataset in the
sources checked. The current implementation therefore uses EU NUTS 2 only and
leaves the rest NULL.

### 4.2 Safety / crime

| Candidate | Granularity / coverage | Licence / status | Assessment |
| --- | --- | --- | --- |
| Eurostat `crim_off_cat` | **Country**; EU/EFTA | Eurostat reuse with attribution; verified API; `geo=DE11` empty | **Not city-level; do not use for a city safety score.** |
| FBI Crime Data Explorer API | Agency/city; US | US government data; API key required; verified endpoint docs | Candidate for US cities only. |
| UK Police API | Street/neighbourhood; UK | Open Government Licence; verified docs | Candidate for UK only; street-level aggregation needed. |
| National police open data | Country-specific | Varies | Candidate per country. |
| UNODC | Country; global | UNODC terms; verify | Country-level only. |
| US State Department advisories | Country; 214 countries (multiple entries can collapse) | US government work; JSON API verified | **Implemented**: levels 1-4, date, URL. |
| UK FCDO foreign travel advice | Country; ~225 entries | Open Government Licence v3.0; content API verified | **Implemented**: `alert_status`, review date, URL. |

**Decision:** `safety_crime` remains unavailable at city level. The national
homicide rate (via World Bank, section 3.13) and the US/UK travel advisories are
stored per country and shown only as country context. The two advisory scales
are separate rows and are never averaged; neither is copied onto a city. If a
US/UK-only city-crime mode is added, it must be labelled as such.

### 4.3 Housing / rent

| Candidate | Granularity / coverage | Licence / status | Assessment |
| --- | --- | --- | --- |
| Inside Airbnb | City/regional; global coverage varies | CC BY 4.0; verified | Implemented optional; short-term-rental price, local currency, not long-term rent. |
| HUD Fair Market Rents | Metro/county polygons; US | US government public domain; ArcGIS service verified 2026-11 | **Implemented**: FY2026 0-4 bedroom FMRs for 1,087 US cities. |
| UK ONS private rent | Local authority; UK | Open Government Licence v3.0; XLSX verified 2026-11 | **Implemented**: 121 GB local-authority areas, 2026-10, GBP. |
| Canada CMHC Rental Market | CMA; Canada | CMHC terms; verify | Candidate for Canada. |
| Australia ABS rent | State/city; Australia | ABS terms; verify | Candidate for Australia. |
| Eurostat `prc_colc_rents` | 42 city geographies; 41 loaded (London is covered by ONS); EU/EFTA/UK/TR/Balkans | Eurostat reuse with attribution; API verified | **Implemented**: average monthly rent by dwelling type, 2025, local currency. |
| Eurostat housing price/rent indices | NUTS; EU | Eurostat reuse with attribution; verify dataset | Candidate regional context, not city rent. |
| OECD Affordable Housing Database | Country; OECD | OECD terms; verify | Country-level only. |

**Decision:** `rent_monthly` stores official rents in local currency for about
1,250 cities: Eurostat city averages (41), ONS local-authority averages (121 GB
areas) and HUD Fair Market Rents (1,087 US cities). There is still no global
city-level long-term rent source in the checked set, and values are not
FX-converted or put into the rank. `housing_monthly` remains the separate
optional Inside Airbnb table.

### 4.4 Walkability / rail

| Candidate | Granularity / coverage | Licence / status | Assessment |
| --- | --- | --- | --- |
| Overture Transportation | Segment; 1,131 cities loaded (DE/GB/TR/ES/FR/PT/SE/LU) | ODbL; verified schema | Implemented; global scan blocked by DNS/OOM, country runs work. |
| How Walkable city ranking | City centre + 5 km; 690 cities/95 countries | CC BY 4.0; implemented | **Implemented** as the published 0-100 walkability score. |
| Boeing global street-network indicators | Urban area; 10,351 areas, global | CC0 1.0 (derived) / ODbL (OSM) | Loaded as urban-form context (`street_network_metrics`); not shown in profiles. |
| OpenStreetMap / Geofabrik | Road/path network; global | ODbL; verified | Candidate for a full pedestrian-network scan; large extracts and processing required. |
| GHSL (JRC) UCDB R2024A geography | Urban-centre polygons; 11,422 centres, global | EU JRC open data; CC BY 4.0 | **Implemented**: polygon containment assigns 9,730 cities to a metro (`destination_metro`). Grouping context only, not a walkability score. |
| WHO/UN-Habitat | Country/global reports | Varies | Not city-level walkability. |

**Decision:** Use the How Walkable score as the user-facing walkability number,
and keep the Overture pedestrian-network share as a second, local measure.
Boeing's street-grid indicators are stored as urban-form context only: the
Houston-outranks-Berlin result showed that intersection density alone is not a
walkability verdict. Do not substitute a built-up raster for walkability.

### 4.5 Rainy days / hot days

| Candidate | Granularity / coverage | Licence / status | Assessment |
| --- | --- | --- | --- |
| Open-Meteo Historical Weather API (ERA5/ERA5-Land) | Point/grid; global; 1940–present | API terms; ERA5/Copernicus citation; verified docs | Candidate for daily rainy/hot-day counts for finalists or batch jobs. |
| Copernicus CDS ERA5 | Grid; global | Copernicus licence; registration/API key | Candidate primary reanalysis. |
| NASA POWER | Point/grid; global | NASA terms; verify | Candidate daily climate/weather. |
| NOAA GHCN-D | Station; global but sparse | US government/open; verify | Candidate station observations, not uniform city coverage. |

**Decision:** `avg_rainy_days` and `avg_hot_days_30c` stay NULL until a
documented batch enrichment is run. Do not derive them from WorldClim monthly
means without labelling the approximation.

### 4.6 Broadband cross-checks

| Candidate | Granularity / coverage | Licence / status | Assessment |
| --- | --- | --- | --- |
| Ookla Open Data | Tile; global | CC BY-NC-SA 4.0; implemented | Primary source. |
| M-Lab NDT | Measurement/BigQuery; global | **CC0 1.0**; verified | Candidate cross-check; city aggregation methodology required. |
| ITU | Country; global | ITU terms; verify | Country-level only. |

### 4.7 Air quality and additional context (not currently scored)

| Candidate | Granularity / coverage | Licence / status | Assessment |
| --- | --- | --- | --- |
| OpenAQ | Monitor/station; global aggregator | Source licences vary; verify per provider | Candidate; must preserve per-source licence. |
| Copernicus CAMS | Model/reanalysis; global | Copernicus licence; verify | Candidate regional air quality. |
| World Bank | Country; global | CC BY 4.0; verify | Country context only. |
| WHO Global Health Observatory | Country; global | CC BY-NC-SA 3.0 IGO; verify | Country context only. |

## 5. Explicitly excluded sources

| Source | Why excluded |
| --- | --- |
| Numbeo | Crowd-sourced/self-reported; no probability sample or audited methodology; not suitable as a deterministic city-level fact. |
| Walk Score | Proprietary API; not open data; would make the ranking non-reproducible without a commercial licence. |
| Teleport | Proprietary/aggregated scores; not a primary measurement. |
| Google Places / Google Flights direct scraping | Proprietary terms; not used. SerpApi is used only as an optional, labelled commercial API. |
| LLM-generated city estimates | Never used. No model output is stored as data. |

## 6. Table-level lineage

| Table | Input(s) | Derived? | Source string in DB |
| --- | --- | --- | --- |
| `destinations` | GeoNames | No | n/a |
| `destinations.iata_codes` | OurAirports + PostGIS | Yes (spatial match) | n/a |
| `climate_monthly` | WorldClim 2.1 | No (sampled) | `WorldClim 2.1 (5m)` |
| `climate_recent` | NASA POWER daily temperature/precipitation | Yes (monthly normals) | `NASA POWER (MERRA-2)` |
| `air_quality_annual` | SatPM V6.GL.03 annual PM2.5 | No (sampled) | `SatPM V6.GL.03 (CNN PM2.5, 0.1 degree annual)` |
| `air_quality_monthly` | SatPM V6.GL.03 monthly PM2.5 | No (sampled) | `SatPM V6.GL.03 monthly (CNN PM2.5, 0.1 degree)` |
| `poi_metrics` | Overture Places | Yes (counts/entropy) | `Overture Maps Places ...` |
| `broadband_metrics` | Ookla | Yes (weighted median) | `Ookla Open Data (fixed)` |
| `mobility_metrics` (walk/rail) | Overture Transportation | Yes (counts/ratio) | `Overture Maps Transportation` |
| `mobility_metrics` (GTFS) | Mobility Database | Yes (bbox count) | `Mobility Database GTFS` |
| `mobility_metrics` (transit stops) | Overture Places | Yes (5 km distinct count) | `Overture Maps Places (transit stops)` |
| `walkability_scores` | How Walkable city ranking | No (published score; name/alias match) | `How Walkable (howwalkable.com), CC BY 4.0` |
| `ghsci_city_indicators` | GHSCI analysis (OSM + GHSL + UCDB) | Yes (access, density, index) | `GHSCI (healthysustainablecities/global-indicators)` |
| `street_network_metrics` | Boeing street-network indicators + GHSL UCDB centroids | Yes (density; name/distance match) | `Global Urban Street Networks Indicators (Boeing 2026) from 2025 OpenStreetMap; GHSL urban centre boundary` |
| `destination_metro` | GHSL UCDB R2024A urban-centre polygons | Yes (point-in-polygon; ≤5 km edge fallback) | `GHSL UCDB R2024A urban-centre boundary (European Commission JRC)` |
| `ghsl_urban_centres` | GHSL UCDB R2024A geography theme | No (geometry converted to WGS84) | n/a |
| `housing_monthly` | Inside Airbnb | Yes (median, nearest city) | `Inside Airbnb (local currency, 30+ night listings)` |
| `rent_monthly` | Eurostat `prc_colc_rents` + ONS PIPR + HUD FMR | No (latest published value per dwelling type) | `Eurostat prc_colc_rents (average rent per month in cities)`, `ONS Price Index of Private Rents (local authority)`, `HUD Fair Market Rents (FY2026)` |
| `travel_advisory` | US State Department + UK FCDO | No (country-level official advisory) | `US Department of State Travel Advisory`, `UK FCDO Foreign Travel Advice` |
| `facility_metrics` | Overture Places | Yes (5 km counts) | `Overture Maps Places (facility counts)` |
| `destination_geography` | GeoNames DEM + Natural Earth 10m coastline | Yes (straight-line distance) | `GeoNames cities15000 (dem, fallback elev)`, `Natural Earth 10m coastline (public domain)` |
| `airport_access` | OurAirports | Yes (nearest point and 100 km counts) | `OurAirports (scheduled-service airports)` |
| `urban_centre_hazard` | GHS-UCDB hazard theme (MYRIAD-HES) | No (zonal counts) | `GHS-UCDB R2024A hazard theme (MYRIAD-HES, CC BY 4.0)` |
| `urban_centre_health` | GHS-UCDB health theme (healthsites.io/OSM) | Yes (counts, density, access share) | `GHS-UCDB R2024A health theme (healthsites.io/OSM)` |
| `tourism_pressure` | Eurostat NUTS 2 tourism + population | Yes (NUTS 2 assignment/division) | `Eurostat tour_occ_nin2m + demo_r_pjangrp3 (NUTS 2)` |
| `tourism_monthly` | Eurostat NUTS 2 tourism + population | Yes (monthly nights / regional population) | `Eurostat tour_occ_nin2m + demo_r_pjangrp3 (NUTS 2) monthly` |
| `flight_quote_cache` | SerpApi Google Flights | No (cached response) | `SerpApi Google Flights` |
| `hotel_quote_cache` | SerpApi Google Hotels | No (cached response) | `SerpApi Google Hotels` |
| `destination_aliases` | GeoNames alternatenames | Yes (normalized) | n/a |
| `country_facts` | GeoNames countryInfo | No | `GeoNames countryInfo.txt` |
| `country_indicators` | World Bank WDI/WGI | No (price level index rescaled to a ratio) | `World Bank WDI`, `World Bank WGI` |
| `destination_admin1` | GeoNames cities15000 + admin1CodesASCII | No | n/a |
| `rank_destinations_v3` output | All of the above | **Yes, first-party derived** | n/a |
| `place_profile` percentiles | All city tables | **Yes, first-party derived** (percent rank over all cities with data) | n/a |

## 7. Verification log (2026-11-21)

Commands and observations actually used:

```text
Ookla Q2 2026 Parquet HEAD:
  HTTP/1.1 200 OK
  Content-Length: 353986294
  key: parquet/performance/type=fixed/year=2026/quarter=2/2026-04-01_performance_fixed_tiles.parquet

Ookla Parquet schema (DuckDB):
  quadkey, tile, tile_x, tile_y, avg_d_kbps, avg_u_kbps, avg_lat_ms,
  avg_lat_down_ms, avg_lat_up_ms, tests, devices, quarter, type, year

Overture Transportation schema (DuckDB, release 2026-11-18.0):
  id, names, subtype, class, subclass, subclass_rules, connectors,
  road_surface, road_flags, rail_flags, width_rules, level_rules,
  access_restrictions, speed_limits, prohibited_transitions, routes,
  destinations, sources, geometry, version, bbox, theme, type

Overture Places public-transport categories (global count):
  train_station                        107,910
  bus_station                           73,526
  public_transit_facility_or_service    31,406
  metro_station                          2,730
  light_rail_and_subway_station          1,410

Eurostat tour_occ_nin2m 2024, all NUTS 2:
  HTTP 200; 827 geo categories; 794 values
  ES51 (Cataluña) = 88,670,688 nights for 2024
  LU00 (Luxembourg) = 3,684,469 nights for 2024

Eurostat demo_r_pjangrp3 2024, NUTS 3:
  HTTP 200; 2,141 geo categories; 1,767 values

GISCO NUTS 2 2024 GeoJSON:
  HTTP 200; Content-Length 16,744,412; 299 features with LEVL_CODE=2

Mobility Database feeds_v2.csv:
  8,000 rows; data_type gtfs=4,709, gtfs_rt=2,017, gbfs=1,274
  active=6,046; 2,730 active/non-superseded GTFS feeds with usable bboxes
```

No claim in this document relies on an unverified page fetch. Where a licence
term could not be confirmed on an official landing page, the entry says so
explicitly (GeoNames CC BY version; WorldClim is confirmed non-commercial;
OECD/ITU/national sources are marked “verify”).

## 8. Reproduction commands

```bash
# Phase 1: GeoNames, OurAirports, WorldClim, Overture Places
python bootstrap_global_40k.py

# Phase 3: Ookla, Overture Transportation, Mobility Database, optional Airbnb
python bootstrap_phase3.py

# Public transport access: Overture Places transit facilities within 5 km
python bootstrap_transit_stops.py

# Supplemental: Eurostat NUTS 2 tourism pressure
python bootstrap_supplemental.py --eurostat-tourism --year 2024

# Coverage snapshot
python bootstrap_phase3.py --query-only
python bootstrap_supplemental.py --query-only
```

## 9. Missing-data policy (plain English)

- If a source does not publish a city-level value, the row is NULL.
- If only a region-level value exists, it is stored as a region-level value and
  labelled as such (`tourism_pressure` uses NUTS 2).
- If a source is country-level only (Eurostat crime, ITU, UNODC), it is **not**
  copied onto cities.
- If a dimension is missing, `rank_destinations_v3` may use `0.5` for the
  arithmetic, but `data_completeness` and `metrics` expose the gap.
- No low-quality proxy is used to make the table look complete.

This is the standard a reviewer should hold the project to. If a future loader
adds a candidate source, update this file, the YAML registry, and the table's
`source` column in the same change.
