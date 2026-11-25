# Global destinations DB (cities ≥ 40k)

PostGIS bootstrap for a travel-ranking database covering every GeoNames city
with **population ≥ 40,000** (~13k destinations after metro de-duplication),
with climate, recent monthly normals, air quality, official rents, facilities,
hazards, health access, travel advisories, geography, airports,
walkability/transit and optional housing data attached.

The goal is a **fixed number of bulk operations** instead of per-city API/S3
calls:

| Step | Source | Operation |
| --- | --- | --- |
| Cities | GeoNames `cities15000.zip` | one download, one `COPY` |
| Airports | OurAirports `airports.csv` | one download, one PostGIS join |
| Climate baseline | WorldClim 2.1 `5m` GeoTIFFs | one local raster sample per variable, one `COPY` |
| Recent climate | NASA POWER daily API | one API call per city, monthly normals |
| POIs | Overture Maps Places (S3 Parquet) | one DuckDB scan + grid hash join, one `COPY` |
| Broadband | Ookla Open Data (S3 Parquet) | one DuckDB scan + grid hash join |
| Walkability/transit | Overture Maps Transportation + Mobility Database GTFS | one transport scan + one CSV join |
| Walkability score (global) | How Walkable city ranking (Overture/OSM) | one CSV, name/alias match |
| Air quality (global) | SatPM V6.GL.03 annual + monthly PM2.5 (0.1°) | 13 NetCDFs, point samples |
| Rent (official) | Eurostat, ONS, HUD | three official loaders, local currency |
| Facilities | Overture Places | one DuckDB scan, 5 km counts |
| Hazards / health access | GHSL UCDB hazard + health themes | two CSVs, urban-centre join |
| Housing (optional) | Inside Airbnb city dumps | local CSV scan |
| Tourism pressure (optional) | Eurostat NUTS 2 tourism + population | one API + GISCO boundary join; annual and monthly |

## Files

| File | Purpose |
| --- | --- |
| `bootstrap_global_40k.py` | Phase 1 pipeline (cities, airports, climate, POIs) |
| `bootstrap_phase3.py` | Phase 3 pipeline (broadband, walkability/transit, optional housing) and `rank_destinations_v3` |
| `bootstrap_transit_stops.py` | Overture Places public-transport facility counts within 5 km |
| `bootstrap_supplemental.py` | optional official-data loaders (Eurostat NUTS 2 annual and monthly tourism pressure) |
| `bootstrap_places.py` | city name aliases (fuzzy search) and country facts/indicators (GeoNames, World Bank) |
| `bootstrap_walkability.py` | published 0-100 walkability score for 662 cities (How Walkable, CC BY 4.0) |
| `bootstrap_street_network.py` | urban-form context: street-intersection density and dead-end share (stored, not shown in profiles) |
| `bootstrap_metro.py` | authoritative GHSL UCDB polygon containment for metro grouping |
| `bootstrap_climate_recent.py` | recent monthly climate normals from NASA POWER (2015-2024) |
| `bootstrap_air_quality.py` | global annual city PM2.5 from SatPM V6.GL.03 |
| `bootstrap_air_quality_monthly.py` | monthly city PM2.5 seasonality from SatPM V6.GL.03 |
| `bootstrap_rent.py` | official Eurostat city rents in local currency |
| `bootstrap_rent_uk.py` | ONS local-authority rents for Great Britain and Northern Ireland |
| `bootstrap_rent_us.py` | HUD Fair Market Rents by metro/county polygon for the US |
| `bootstrap_advisories.py` | US State and UK FCDO country travel advisories |
| `bootstrap_facilities.py` | Overture Places daily-life facility counts within 5 km |
| `bootstrap_geography.py` | GeoNames elevation and Natural Earth coastline distance |
| `bootstrap_airport_access.py` | nearest scheduled-service airport and 100 km counts |
| `bootstrap_gtfs.py` | global GTFS scheduled headways and service span |
| `bootstrap_nature.py` | ecoregion landscape, Köppen class and 10 km terrain |
| `bootstrap_land_cover.py` | ESA WorldCover land-cover fractions at 1 km and 15 km |
| `bootstrap_green_score.py` | optional physical-greenery and environmental-quality scores |
| `bootstrap_inland_water.py` | nearest HydroLAKES lake/reservoir and nearest lake ≥10 km² per city |
| `bootstrap_rivers.py` | nearest HydroRIVERS major river per city |
| `bootstrap_ucdb_themes.py` | GHSL urban-centre hazard and health-access themes |
| `place_profiles.py` | place search, plain-language profiles and comparison used by the MCP server |
| `mcp_server.py` | Phase 4 FastMCP server (Streamable HTTP, optional SerpApi live pricing) |
| `Dockerfile` | container image for the pipeline and MCP server |
| `docker-compose.yml` | MCP service + optional local PostGIS profile |
| `requirements.txt` | Python dependencies for the full pipeline |
| `.env.example` | Supabase and SerpApi connection settings |
| `mcp.client.example.json` | example HTTP MCP client entry for the local server |
| `docs/DATA_SOURCES.md` | source-by-source provenance, licences, coverage and candidate sources |
| `docs/COVERAGE_REPORT.md` | measured coverage snapshot for the global run |
| `docs/PUBLIC_TRANSPORT.md` | GTFS scheduled-service method, coverage, limits and commands |
| `docs/NATURE.md` | nature typology, sources, what is loaded and what is rejected |
| `docs/METHODOLOGY_AUDIT.md` | green-score audit, formulas, two rankings, sensitivity and limits |
| `reports/generate_rankings.py` | reproducible million-plus greenery and environment rankings |
| `sql/rank_examples.sql` | Phase 1/v2 scorer examples |
| `sql/rank_examples_v3.sql` | Phase 3/v3 scorer examples |
| `tests/` | offline unit tests for the Phase 1, Phase 3 and MCP helpers |
| `data_cache/` | downloaded source data (git-ignored) |

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Python 3.10+ is required. `rasterio` and `duckdb` ship manylinux wheels, so no
GDAL install is needed on Linux/macOS.

All later commands run **from this project directory with the virtualenv
active**. If you did not activate it, use `.venv/bin/python` explicitly instead
of `python`. These are not bare-host commands: a machine without Python,
PostgreSQL/PostGIS and the project files cannot run them as written.

## 1b. Docker (recommended on Unraid / Tower)

If the host Python lacks the `ssl` module or you do not want to install
PostGIS on the host, run the project in Docker.

```bash
cd /mnt/cache/appdata/travel

cp .env.example .env
# edit .env and set SUPABASE_DB_URL (and optionally SERPAPI_API_KEY)

docker compose up -d --build
curl http://127.0.0.1:8000/health
```

The `mcp` service builds the full image and runs `mcp_server.py` on port 8000.
Batch jobs use the same image:

```bash
docker compose run --rm mcp python bootstrap_transit_stops.py
docker compose run --rm mcp python bootstrap_phase3.py \
  --countries TR,PT,GB,FR,DE --skip-ookla --skip-gtfs
```

For a fully local PostGIS database instead of Supabase:

```bash
# in .env:
# SUPABASE_DB_URL=postgresql://postgres:travel@db:5432/travel_global

docker compose --profile local-db up -d --build
docker compose run --rm mcp python bootstrap_global_40k.py
```

The `db` service stores data in `./pgdata`; the app caches downloads in
`./data_cache` and DuckDB spill files in `./duckdb_spill`.



```bash
export SUPABASE_DB_URL="postgresql://postgres:<PASSWORD>@db.<PROJECT_REF>.supabase.co:5432/postgres"
```

Notes:

- Many new Supabase projects expose the direct `db.<ref>.supabase.co` host over
  **IPv6 only**. If your network has no IPv6, use the **session pooler**
  (dashboard → Connect → Session pooler):
  `postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
- The script disables client-side prepared statements automatically for
  `pooler.supabase.com` and port `6543`. The **session pooler (5432)** is the
  safest choice because the pipeline uses `COPY ... FROM STDIN` and DDL.
- Enable PostGIS once if `CREATE EXTENSION` is rejected:
  dashboard → Database → Extensions → `postgis`.
- Free tier (500 MB) is enough: ~13k cities, ~157k climate rows and ~13k POI
  rows measured 57–76 MB including indexes depending on vacuum state (about
  45 MB of that is `climate_monthly`; repeated upserts leave dead tuples until
  `VACUUM`).

## 3. Run

Fast end-to-end check (~1–2 min; Luxembourg is the smallest useful test):

```bash
python bootstrap_global_40k.py --countries LU
```

Country pair from the original plan:

```bash
python bootstrap_global_40k.py --countries SE,ES
```

Full global run:

```bash
python bootstrap_global_40k.py
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--countries SE,ES` | limit cities (still uses the same global schema) |
| `--skip-pois` | skip the Overture/S3 scan |
| `--skip-climate` | skip WorldClim sampling |
| `--skip-airports` | skip airport matching |
| `--no-metro-dedupe` | keep PPLA2 boroughs and same-municipality districts (`PPLX` is always excluded) |
| `--dedupe-radius-km 20` | radius for merging `PPLA2` boroughs |
| `--municipality-radius-km 15` | radius for merging same-municipality districts |
| `--municipality-pop-ratio 5` | how much larger the parent city must be |
| `--no-prune` | keep rows in scope that this run no longer selects |
| `--min-pop 40000` | city population threshold |
| `--airport-km 100` | airport search radius |
| `--overture-release 2026-11-18.0` | pin a different Overture release |
| `--db-url ...` | override `SUPABASE_DB_URL` |
| `--query-only` | only run coverage + ranking queries |

Run the offline tests with:

```bash
python -m unittest discover -s tests -v
```

### Start the local services

```bash
# Start PostgreSQL + the MCP server
./scripts/start_all.sh

# Check server health
./scripts/status_mcp.sh

# Stop only the MCP server
./scripts/stop_mcp.sh
```

The local server listens on `http://127.0.0.1:8000/mcp`; the health probe is
`http://127.0.0.1:8000/health`. `mcp.client.example.json` is a generic HTTP
client entry; client-specific config formats may differ.

The script is **idempotent**: re-running updates cities, climate and POIs in
place. Each run is authoritative for its scope: a global run prunes cities that
no longer pass the filters, and a `--countries` run prunes only within those
countries. Use `--no-prune` to keep stale rows. Airport updates are restricted
to the run's cities, so a small test run cannot wipe a global dataset's codes.

## 4. Verify

```bash
python bootstrap_global_40k.py --query-only
```

or from `psql`:

```sql
SELECT COUNT(*) FROM destinations;
SELECT COUNT(*) FROM climate_monthly;

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
```

More examples in [`sql/rank_examples.sql`](sql/rank_examples.sql).

## 5. Measured Phase 1 local timings

On a 12-core box with cached downloads and a local PostGIS 15:

| Step | Global (13k cities) | SE+ES (~200 cities) | LU |
| --- | --- | --- | --- |
| schema + scorer | < 1 s | < 1 s | < 1 s |
| GeoNames load + dedupe | ~2 s | < 1 s | < 1 s |
| airport matching | ~5–10 s | < 1 s | < 1 s |
| WorldClim sampling (3 × 12 rasters) | ~30–60 s | ~1.5 s | ~1 s |
| Overture POI scan | ~5–15 min (network bound) | ~1.5 min | ~50 s |
| verification queries | < 1 s | < 1 s | < 1 s |

Reference run in this workspace: **13,101 cities**, 11,073 with an IATA code,
157,188 climate rows, 76 MB total. A full-global POI pass depends on sustained
S3 bandwidth; the country-scoped passes (LU, SE+ES) are the recommended smoke
test before committing to it.

The first DuckDB/Overture query is slow because DuckDB fetches Parquet metadata
from S3; subsequent queries in the same process are much faster. Keep a warm
`data_cache/` between runs. Long S3 scans retry up to 5 times on transient
DNS/HTTP errors; each retry restarts the scan, so budget extra time on a flaky
network.

## 6. What was fixed relative to the draft

The draft's architecture is sound. These are the correctness/scaling changes in
this implementation:

1. **NYC boroughs are `PPLA2`, not `PPLX`.** Filtering only `PPLX` leaves
   Manhattan, Brooklyn, Queens and the Bronx in the table (verified against the
   live GeoNames dump). Two merge rules run after the `PPLX` filter:
   - `PPLA2` boroughs within 20 km of a ≥2× larger city (NYC boroughs);
   - any record sharing country/admin1/admin2/admin3 within 15 km of a ≥5×
     larger city (Barcelona's Sant Martí, Madrid's Chamartín, the Paris
     arrondissements, Warsaw's districts).

   The 5× threshold matters: admin3 is a county in some countries, so a lower
   ratio merged Venice into Mestre and Grevenbroich into Neuss. Separate
   municipalities in the same metro (L'Hospitalet, Badalona, Cambridge,
   Oakland) are kept. The result is **~13,100 cities** (13,101 in the reference
   run; raw ≥40k after the `PPLX` filter: 14,156). Disable both rules with
   `--no-metro-dedupe`.
2. **Country-scoped Overture scans.** A `--countries SE,ES` run now issues one
   Parquet scan per country envelope and unions the results by place `id`, so
   each scan can prune its own row groups. An OR-of-envelopes filter is
   logically equivalent but blocks DuckDB's row-group pruning and turned a
   2-country run into a 10+ minute global scan.
3. **Antimeridian-safe grid cells.** Longitude cells are wrapped with
   `((x % 3600) + 3600) % 3600`; the original 5×3 halo breaks at ±180°.
4. **Null-safe Overture flags.** `list_contains(NULL, ...)` returns `NULL`, not
   `FALSE`; flags are wrapped in `COALESCE`.
5. **Closed businesses excluded.** `operating_status = 'permanently_closed'` is
   filtered out (NULL is treated as open).
6. **Cuisine entropy is actually about cuisine.** Generic
   `taxonomy.primary = 'restaurant'` is excluded from the entropy buckets;
   otherwise ~2k generic restaurants in Barcelona alone dominate one bucket and
   flatten the signal.
7. **Airport reset is scoped.** The draft's unconditional reset/update would
   clear airports from cities outside a later `--countries` run. Both reset and
   match are restricted to the current run's `destination_id`s.
8. **Schema compatibility check.** `CREATE TABLE IF NOT EXISTS` silently keeps
   an incompatible Phase-1 table; the script now fails with a column-level
   message instead.
9. **Download hardening.** Retries, `.part` files and zip validation.
10. **Pooler support.** Prepared statements are disabled for Supabase poolers.
11. **Deterministic rankings.** Ties break on population, not physical row
    order.
12. **Verification respects `--countries`**, so a test run does not rank rows
    left over from earlier runs.
13. **`restaurant_count` and cuisine entropy are separated.** The draft
    computed the count from the same CTE that drops generic `restaurant`
    labels, undercounting restaurants by roughly a third. The count now
    includes every restaurant; only the entropy buckets exclude the generic
    label.
14. **Large airports are preferred.** Sorting purely by distance put Teterboro
    ahead of JFK and San Carlos ahead of San Jose. The query now orders by
    `large_airport` first, then distance, and stores 3 codes (the draft's
    `[1:4]` slice stored 4).

### Phase 3 fixes relative to the draft

1. **Ookla tile coordinates are longitude/latitude centroids**, not Web Mercator
   indices; the grid join uses `tile_x`/`tile_y` directly. The median is
   **tests-weighted**, not a plain median.
2. **Antimeridian-safe Ookla/Overture/listing cells** use wrapped longitude
   buckets, and each country gets its own pruned Parquet envelope (grouped into
   45° bands above 40 countries), with opposite-edge bands for cities just
   across the dateline.
3. **Mobility Database column names are the v2 ones**: `data_type='gtfs'`,
   `status='active'`, `redirect.id`, and the four
   `location.bounding_box.*` columns. `gtfs_rt` and `gbfs` feeds are excluded.
4. **Overture transportation values are verified**: `subtype` is `road`/`rail`
   and pedestrian classes include `footway`, `pedestrian`, `living_street`,
   `steps`, `cycleway`, `path` and `bridleway`. The separate Overture context
   field blends the 2 km pedestrian ratio (60%) with the log of pedestrian
   segment count (40%), so a tiny settlement cannot win on a fluke ratio of
   one footway. It is not the How Walkable score and is not used in ranking.
5. **GTFS-only transit is scored.** The draft's transit percentile required
   rail data, so `--skip-overture-transport` silently dropped GTFS from the
   score. The v3 scorer computes a rail percentile where rail exists and adds a
   GTFS-feed component, so GTFS still contributes without the huge transport
   scan.
6. **Provenance is structured for every dimension**, including mobility,
   broadband and housing, and empty dimensions are omitted instead of appearing
   as `{}`.
7. **Ties break on population**, not physical row order, as in v2.
8. **The short-stay exit-gate example no longer requires GTFS.** Requiring a
   feed for a three-day city break discarded cities that simply do not publish
   one; the corrected example is in `sql/rank_examples_v3.sql`.
9. **Inside Airbnb is a real optional loader**, not just a schema table. It
   matches listings by coordinates, filters 30-night-capable listings and
   records the sample size.
10. **The Schengen rule is explicit**: 29 countries, applied only to stays
    longer than 90 days for non-EU/EEA passports, with a legal caveat in the
    docs.
11. **Phase 4 split quote caches** key flight quotes by route+dates and hotel
    quotes by city+dates, so changing the departure airport only refetches
    flights.
12. **Percentile scores are non-degenerate and missing data is neutral.**
    Relative dimensions map to `0.05..0.95` (single/all-equal -> `0.5`), and
    missing dimensions are imputed at `0.5` for the score. Complete-but-equal
    data no longer scores zero, and a climate-only row cannot win just by
    renormalizing its best dimension to 100%; `data_completeness` still exposes
    thin evidence.
13. **Inside Airbnb currency is explicit.** The column is
    `median_monthly_local`, not `median_monthly_usd`; the loader performs no FX
    conversion, and the docs say to zero the housing weight for cross-currency
    searches.
14. **Public transport access is measured directly.** Overture Places transit
    facilities within 5 km provide a stop-density signal distinct from GTFS
    feed presence; GTFS remains a separate coverage/frequency proxy.
    Walkability combines the pedestrian-road ratio and pedestrian segment
    count from Overture Transportation.

## 7. Data caveats (read before trusting a ranking)

- **City population** comes from GeoNames city-proper records and can be years
  out of date. It is not a metro population. The source file is
  `cities15000.zip`, so `--min-pop` below 15,000 cannot add smaller places.
- **Metro grouping is polygon-based for display.** The city table keeps legally
  separate municipalities (L'Hospitalet, Badalona, Westminster, Islington), so
  their 5 km POI radii can overlap. `destination_metro` assigns each city point
  to a GHSL UCDB R2024A urban-centre polygon (9,730 cities into 7,018 centres;
  3,371 below the UCDB cutoff stay individual). `rank_destinations` returns one
  row per centre by default and exposes the shared `metro_group`; the
  percentiles themselves are still computed across all cities. Pass
  `--no-metro-dedupe` only to the city-table build, not to the ranker.
- **Official rents are local-currency and not a rank input.** Eurostat city
  averages, ONS local-authority averages and HUD Fair Market Rents have
  different bases (average tenancy, all tenancies, 40th-percentile voucher
  benchmark). The profile labels the source, area and period; do not compare
  the numbers across countries as if they were one statistic.
- **Recent climate is a separate NASA POWER layer.** It covers a growing subset
  of cities at roughly 0.5° and does not replace the WorldClim baseline.
- **Air quality is a model grid.** SatPM PM2.5 is a 0.1° city-point estimate,
  not a monitor reading; the monthly layer shows seasonality, not a daily alert.
- **Facilities and health access are presence, not quality.** Overture counts
  and OSM/healthsites access shares inherit map coverage gaps; a zero is
  `none_listed`, not proof that nothing exists.
- **Hazards are recorded metro event counts.** The GHSL/MYRIAD layer covers the
  2015 window and 11 hazard types, not a forecast or a live alert.
- **Travel advisories are country-scoped.** US State levels 1–4 and UK FCDO
  advice are separate official scales, never averaged, and never copied onto a
  city row.
- **Scheduled transit is not real-time reliability.** Headways come from static
  GTFS. They do not measure delays, cancellations, journey times, fares or
  ridership, and coverage is only where an operator publishes GTFS.
- **Nature is regional context, not a beach or trail finder.** Ecoregion is the
  surrounding biome; terrain is a 10 km cell; coast distance is a straight line.
  They do not say whether there is a sandy beach, a trailhead or a park you can
  enter. There are three optional scores: a land-only vegetation-cover score,
  a physical-greenery score (tree canopy, other vegetation, hinterland and
  mapped parks) and an overall environmental-quality score (greenery, air and
  water access). They show their components, none is a nature-quality
  judgment, and none is in `final_score`. The audit and rankings are in
  [docs/METHODOLOGY_AUDIT.md](docs/METHODOLOGY_AUDIT.md).
- **WorldClim 2.1 is a 1970–2000 climatology**, not recent daily ERA5. The
  `median_temp_max_c` / `median_temp_min_c` columns are monthly **means of daily
  max/min** (the Phase-1 column names are retained); `precip_mm` is the monthly
  total. WorldClim's own terms allow academic/non-commercial use and require
  permission for redistribution or commercial use; see section 12.
- **`avg_rainy_days` and `avg_hot_days_30c` are intentionally NULL** until the
  Phase-2/4 Open-Meteo enrichment. The scorer falls back to `precip_mm` for the
  rain component.
- **Overture POIs** use the place's bbox center and a 5 km Haversine radius.
  A large park or mall whose center is outside 5 km is not counted. Coverage is
  excellent in North America/Europe and thinner elsewhere; a zero count can
  mean "no data", not "no restaurants".
- **The walkability score is a city-centre measure, not a metro verdict.**
  `walkability_scores` holds the published How Walkable 0-100 score for 662
  loaded cities (690 ranked). It averages everyday-place proximity across the
  5 km around the busiest point, so a car-dependent city with a dense core can
  score well (Houston 79) while its suburbs do not. The profile also shows
  `walkability_centre_score` for the single busiest point (Phoenix 56 vs 99),
  so the core-versus-area gap is visible. It does not measure
  sidewalks, crossings, traffic safety, shade or transit, and mainland China,
  South Korea and Russia are not ranked because their listings are not openly
  licensed. Cities without a score stay NULL; the table is not a substitute for
  a neighbourhood-level audit. I also tested How Walkable's public point API
  against every ranked city: its single-point score runs a median 26 points
  above the published 5 km score (correlation 0.35; Phoenix +43), so it is not
  used to fill missing cities.
- **`tourism_pressure` is partial, not global.** The supplemental Eurostat
  loader fills NUTS 2 tourism nights for EU/EFTA regions (1,405 cities in the
  reference run); non-EU cities remain NULL. All cities in the same NUTS 2
  region share the same regional value, so it is a pressure proxy, not an
  independent city observation. There is no single official
  city-level global tourism dataset, so `s_low_crowding` is only present where
  Eurostat publishes a value. The v2 scorer renormalizes missing crowding; the
  v3 scorer neutral-imputes it at 0.5 and can reach 0.95 completeness. A high
  score with low completeness (e.g. 0.35) means thin evidence — treat it as
  provisional.
- **Some Chinese district records survive.** GeoNames tags district-level
  records such as Pudong and Minhang as `PPLA3`/`PPLA4` with district
  populations, not as subdivisions of a shared municipality code, so the dedupe
  rules keep them. Filter `country_iso2 <> 'CN'` or add a district exclusion if
  that skews your results.
- **Airports are straight-line within 100 km.** That can match an airport across
  water or a mountain range that is not practically reachable. Large airports
  are preferred over nearer medium/GA fields, then sorted by distance; only the
  3 best IATA codes are stored. Without that preference, Teterboro outranks JFK
  for New York and San Carlos outranks San Jose for San Francisco.
- **GEONAMES `PPLX` exclusion** removes Tokyo's special wards but not all
  borough-style records; see the metro pass above.

## 8. Scaling notes for Phase 4

`destination_id` is the stable anchor (`gn-<geonameid>`). Later sources can join
on it without schema rewrites:

- Open-Meteo daily ERA5: fill `avg_rainy_days` / `avg_hot_days_30c` for finalists.
- Ookla Open Data (global Parquet): broadband metrics keyed by tile, spatially
  joined to `centroid`.
- Overture Maps Transportation: walkability and rail segment counts keyed by
  segment bbox center.
- Mobility Database GTFS feeds: active-feed coverage keyed by feed bbox and
  joined by `destination_id`.

For global Overture runs, 5–15 minutes is bandwidth-bound. If that becomes the
bottleneck, materialize a filtered `places` Parquet subset in your own bucket
once per Overture release and point `OVERTURE_S3_ROOT` at it.

## 9. Phase 3: broadband, walkability, transit and housing

Phase 3 enriches the Phase 1 `destinations` table and installs
`rank_destinations_v3`. It assumes Phase 1 has already run on the same database.

```bash
# Fast partial load: Ookla + Mobility Database GTFS, no huge transportation scan
python bootstrap_phase3.py --countries SE,ES --skip-overture-transport

# Full Phase 3 for a small country (recommended first full test)
python bootstrap_phase3.py --countries LU

# Optional Inside Airbnb medians from local city dumps
python bootstrap_phase3.py --countries ES \
    --skip-ookla --skip-overture-transport --skip-gtfs \
    --airbnb-dir data_cache/airbnb

# Optional official EU tourism pressure (NUTS 2, Eurostat)
python bootstrap_supplemental.py --eurostat-tourism --year 2024

# Public transport access: Overture Places transit facilities within 5 km
python bootstrap_transit_stops.py

# Published 0-100 walkability score for 690 large cities
python bootstrap_walkability.py
```

| Flag | Effect |
| --- | --- |
| `--countries SE,ES` | only enrich cities in these countries |
| `--min-pop 40000` | only enrich cities at/above this population |
| `--skip-ookla` | skip the Ookla broadband scan |
| `--skip-overture-transport` | skip the Overture transportation scan |
| `--skip-gtfs` | skip Mobility Database GTFS |
| `--skip-airbnb` | skip Inside Airbnb even if `--airbnb-dir` is set |
| `--ookla-year 2026` | Ookla dataset year |
| `--ookla-quarter 2` | Ookla quarter (1–4) |
| `--ookla-km 5` | broadband search radius |
| `--overture-release 2026-11-18.0` | pin an Overture release |
| `--airbnb-dir DIR` | directory containing `**/listings.csv.gz` |
| `--airbnb-room-type "Entire home/apt"` | room-type filter; pass `''` for all |
| `--db-url ...` | override `SUPABASE_DB_URL` |
| `--cache-dir DIR` | download/cache directory |
| `--query-only` | only print coverage and a sample v3 ranking |

`bootstrap_phase3.py` is idempotent. A country-scoped run only updates the
cities in scope; re-running replaces the columns owned by each loader (Ookla
and housing are replaced, while Overture and GTFS preserve each other's
columns).

### What Phase 3 adds

| Table | Contents |
| --- | --- |
| `broadband_metrics` | tests-weighted median down/up Mbps, weighted latency, tile count, total tests |
| `mobility_metrics` | 2 km pedestrian-road ratio, 2 km road/pedestrian segment counts, 5 km rail segment count, active GTFS feed count, 5 km transit facility count |
| `housing_monthly` | median 30-night monthly cost in the listing's local currency, plus listing sample size (optional) |
| `tourism_pressure` | Eurostat NUTS 2 annual tourist nights and nights per NUTS 2 resident (EU coverage only) |
| `walkability_scores` | published 0-100 walkability score for 662 loaded cities (How Walkable, Overture/OSM) |

### Published walkability score

`bootstrap_walkability.py` loads the *How Walkable* city ranking: a 0-100
walkability score for 690 cities of 300,000+ people in 95 countries, published
under **CC BY 4.0**. It counts everyday places (groceries, cafes, shops, parks,
services) within a walk from Overture Maps and OpenStreetMap, averaged across
the 5 km around each city's busiest point.

The loader matches the ranked labels to loaded destinations by name and country,
then by the GeoNames aliases already in the database (Cologne -> Köln, Seville
-> Sevilla). In the reference run it matched **662 of 690** ranked cities.

What the score is and is not:

- It is a real published score, not a grid-density proxy, and it ranks Paris 99,
  Tokyo 98, Berlin 90 and Phoenix 56.
- It describes the area around the city centre, not the whole metro. A
  car-dependent city with a dense core (Houston 79) can score well.
- Mainland China, South Korea and Russia are not ranked because their business
  listings are not openly licensed; cities under 300,000 are not ranked; 28
  ranked labels (mostly Japanese and Philippine districts) did not match a
  loaded destination and stay NULL.
- It is amenity proximity, not sidewalk quality, crossings, traffic safety or
  shade.
- `rank_destinations` uses this same 0–100 score where it exists. Cities
  without it are neutral in the ranking, and `excluded_for_no_evidence`
  reports rows dropped for having no active evidence. The raw Overture
  `pedestrian_ratio_2km` remains a separate context field, not the score.
- A separate **Walkability (GHSCI)** group appears for Trento and the 24
  published GHSCI 25-city study cities: 500 m access percentages, density
  inputs and the 25-city-reference z-score. The study rows are 2021; Trento
  is 2026. It is not comparable with the How Walkable 0–100 score and is not
  queued on demand. See [docs/GHSCI_ON_DEMAND.md](docs/GHSCI_ON_DEMAND.md).

The previous street-grid experiment (`street_network_metrics`, intersections per
km² and dead-end share) is still loaded by `bootstrap_street_network.py` for
analysis, but it is **not shown in profiles**: Houston outranking Berlin on that
proxy showed it should not be presented as walkability.

`rank_destinations_v3` scores seven active dimensions with weights that sum to
1: climate, culture, food, walkability, transit, broadband and low crowding.
The housing-cost weight is zero by default because there is no comparable global
rent series; official local-currency rents are profile context. Missing
dimensions are **neutral-imputed at 0.5 for the score**
so a data-rich city is not punished for having measured, mediocre values;
`data_completeness` reports how much real evidence the row has, so check it
before trusting a high score. The Phase 3 tables are one row per city, so
a global load adds only a few MB on top of Phase 1. Direct SQL calls use the
extended-stay weights by default; the MCP server applies the
short-stay/extended-stay weight profiles automatically. Relative dimensions
(culture, food, walkability, transit, broadband, crowding, housing) are mapped
to `0.05..0.95`; a single available value or an all-equal set scores `0.5`
instead of collapsing to zero.

The transport sub-scores are explicit:

- **Walkability** = percentile of the How Walkable 0–100 score where a
  city has one (662 cities). Cities without it are neutral 0.5; the ranking
  reports `excluded_for_no_evidence` and never treats missing as zero.
- **Transit** = 40% transit-facility count percentile + 30% active GTFS feed
  percentile + 30% rail-segment percentile (available components renormalized).
- **Public transport access** is the 5 km count of Overture Places
  `bus_station`, `train_station`, `metro_station`,
  `public_transit_facility_or_service` and similar facilities.
- **`pedestrian_ratio_2km`** is separate Overture context, not the ranking
  walkability score.

```sql
SELECT name, country_iso2, population, final_score, data_completeness,
       metrics, provenance
FROM rank_destinations_v3(
    p_months => ARRAY[6],
    p_ideal_temp_min => 20.0,
    p_ideal_temp_max => 26.0,
    p_require_airport => TRUE,
    p_min_broadband_mbps => 50,
    p_require_gtfs => TRUE,
    p_limit => 10
);
```

More examples, including the short-stay and four-month extended-stay exit
gates, are in [`sql/rank_examples_v3.sql`](sql/rank_examples_v3.sql).

Large transportation scans are memory-bound. DuckDB gets **8 GiB** by default
and spills to `data_cache/duckdb_tmp` (falling back to the system temp
directory), so that path must be writable. Override with
`DUCKDB_MEMORY_LIMIT=12GiB` / `DUCKDB_TEMP_LIMIT=32GiB`, or run
country-scoped. A 2 GiB limit OOMs on Spain+Sweden; 8 GiB completed it.

### Phase 3 data caveats

- **Ookla** values are quarterly and tile-based. The scorer uses a
  **tests-weighted median** of tile medians within 5 km, not a user-level
  speed test. A city with no tiles has no broadband row.
- **Overture transportation** uses each segment's bbox center. Long motorway or
  rail segments whose center lies outside the radius are not counted even if
  part of the segment is nearby. Pedestrian classes are `footway`,
  `pedestrian`, `living_street`, `steps`, `path` and `bridleway`; the
  denominator is every road segment within 2 km. The walk score combines the
  pedestrian-road ratio (60%) with the pedestrian segment count (40%). The
  2026-11-21 run loaded walk/rail for **1,131 cities** across DE, GB, TR, ES,
  FR, PT, SE and LU; it is not global.
- **GTFS coverage** comes from Mobility Database feed bounding boxes, not from
  stop locations or service frequency. A national feed can cover many cities;
  feeds wider than 15° are ignored so one national file does not mark a whole
  continent as transit-served. `gtfs_active_feeds` counts active, non-superseded
  static GTFS feeds whose bbox contains the city. Feeds whose bbox crosses the
  antimeridian are skipped (rare).
- **Transit stop counts use Overture Places facilities** (`bus_station`,
  `train_station`, `metro_station`, `public_transit_facility_or_service`,
  `light_rail_and_subway_station`, and similar) within 5 km. The reference run
  populated **9,008 cities**. This counts stations/facilities, not individual
  bus stop poles, and is not a frequency, reliability or journey-time measure.
  Overture coverage varies by city.
- **Inside Airbnb** is optional and city-by-city. The loader matches listing
  coordinates to the nearest destination within 10 km, keeps listings that
  allow a 30-night stay (`minimum_nights <= 30` and `maximum_nights >= 30`,
  with `0` treated as unlimited), and estimates the monthly cost as
  `nightly_price * 30`. It does not
  model weekly/monthly discounts, taxes or cleaning fees. A median from a
  handful of listings is noisy; `sample_listings` is reported so you can apply
  your own minimum. **Inside Airbnb prices are in the listing's local
  currency**, not USD: the loader does not perform FX conversion, so
  `median_monthly_local` is only comparable within a currency area. The default
  housing weight is already zero; if you opt in with `p_w_housing`, keep the
  search inside one currency or convert the column with a dated rate first. Listings are attributed to the nearest destination within
  10 km, so a small-town dump near a larger city can inflate the larger city's
  sample.
- **The Schengen filter** implements the 90/180-day short-stay rule for
  non-EU/EEA passports by excluding all 29 Schengen countries on stays longer
  than 90 days. The SQL function applies `p_exclude_schengen` whenever it is
  true; the MCP server is what gates it on `duration > 90 and not
  passport_eu_eea`. It is a conservative planning heuristic, not legal advice,
  and it does not model long-stay visas or bilateral agreements.
- **Licences/attribution** differ by source (Ookla Open Data, Mobility
  Database, Overture Maps, Inside Airbnb). Check the upstream terms before
  redistributing the derived tables.

## 10. Phase 4: MCP server and live pricing

`mcp_server.py` exposes these tools over Streamable HTTP:

| Tool | Purpose |
| --- | --- |
| `search_places` | find a city by any spelling or language ("Cologne", "Köln", "Porto, PT"); lists candidates when a name is ambiguous |
| `place_profile` | everything known about one city: labelled values with units, global and in-country percentiles (100 = best among cities with data), climate summary, country context and an explicit list of what is not measured |
| `compare_places` | two to five cities side by side, with the best city per metric |
| `rank_destinations` | stage 1 SQL ranking, optional stage 2 live flight/hotel pricing |
| `inspect_destination` | one city's raw evidence rows and provenance |
| `refresh_live_quotes` | force-refresh SerpApi quotes for 1–5 finalists |

`search_places`, `place_profile` and `compare_places` work best after loading
aliases and country context (about 15 seconds, global):

```bash
python bootstrap_places.py
```

Without it, name search falls back to prefix matching on the official name
and the country section reports `status: not_loaded`.

Name lookup rules:

- Trailing `, <qualifier>` parts are read as a country code, country name or
  common synonym (`UK`, `USA`), or as a state/province/UK nation
  (`Austin, TX`, `London, Ontario`, `Glasgow, Scotland`). Several can be
  combined (`Austin, Texas, USA`); all must match. Raw region codes are only
  matched for US states, because other countries' GeoNames codes collide with
  ISO country codes. The `country_iso2` argument only ever means a country
  (`UK`, `USA`, `Portugal` are normalized; `CA` is Canada, never California).
  With a typed qualifier it is an extra filter (`Richmond, CA` + `CA` →
  British Columbia); a result outside it asks the user.
- An exact alternate name never silently beats a city whose official name is
  exactly what was typed (`Dili` asks between Dili, TL and Delhi).
- An exact official name beats an exact alternate name unless the
  alternate-name city is more than 5x larger (`Porto` → Portugal,
  `St Petersburg` → Russia).
- When the best match is only fuzzy, `place_profile`, `compare_places` and
  `inspect_destination` return `needs_choice: true` with candidates instead of
  describing a possibly wrong city.
- A qualifier that matches no loaded city ("Paris, TX": Paris, Texas is under
  40,000 people), a prefix shared by several cities ("Bath"), or several
  same-name cities of similar size ("Springfield") also return `needs_choice`
  with a `reason`. Candidates include their state/province (`region`).
  Canadian and Australian abbreviations (ON, BC, NSW, WA, ...) are understood.
- All-caps 3-4 letter alternate names (mostly airport codes such as SAN or
  GOA) only count as exact when typed in capitals.
- POI counts of zero are reported as `status: none_listed` and are not ranked:
  in Overture Maps, zero usually means missing coverage.
- Broadband values from fewer than 10 Ookla tests, or zero, are not ranked;
  `sample_size` reports the test count. Cuisine variety of 0 (untagged
  restaurants) is treated as not measured.
- Region and country names ask instead of guessing a city: `Bali` (region
  elsewhere with a bigger city), `Goa` / `Missouri` / `Henan` (reached only
  through an alternate name), `Egypt` / `usa` (country names). Exceptions: a
  city whose official name is the typed name in its own country (`Washington`,
  `Singapore`), or one that dominates its region (`New York`).
- Known limitation: a two-letter qualifier that is both a country and a US
  state (`Salem, IN`) is read either way; if only one exists in the data it is
  used without asking, because `Berlin, DE` and `Salem, IN` cannot be told
  apart without a smaller-city gazetteer.
- Percentiles and bands place ties mid-range; `reading_*` counts only places
  with a strictly worse value ("higher than 62% of the other 3,999 cities").
  Country rankings use each country's latest available year, so years can
  differ; quote them.
- `compare_places` asks about every uncertain name before comparing, marks
  `partial: true` when a name is not loaded, and leaves `best` empty when the
  top two values are within 5%.

`inspect_destination` now returns the 12-month climate once, under `climate`;
the duplicate `climate_monthly` and `climate_12m` keys were removed.

Example questions an MCP client can now answer directly: "What is Lisbon like
in March?", "Compare Porto, Valencia and Cologne for a three-month stay", or
"How safe and expensive is Portugal compared with other countries?". Country
indicators are national values and are labelled `granularity: country`.

```bash
export SUPABASE_DB_URL="postgresql://..."
export SERPAPI_API_KEY="..."      # optional; without it stage 2 is skipped
export QUOTE_TTL_HOURS="12"
python mcp_server.py
```

The server creates `flight_quote_cache` and `hotel_quote_cache` on startup.
Flight quotes are keyed by `(origin_iata, destination_iata, start_date,
end_date)`; hotel quotes are keyed by `(destination_id, start_date, end_date)`,
so changing the departure airport does not refetch hotels.

Stage 2 runs only for short stays with exact dates and an origin airport. It
calls SerpApi for the finalist pool (default 8, max 15), concurrently, and
re-ranks by static score plus live trip cost and flight duration. Without
dates, an origin airport or `SERPAPI_API_KEY`, the server returns stage 1
rankings untouched with `live_pricing_status` explaining why. Flight pricing
uses the first stored `iata_codes` entry (large-airport-first, then distance),
so in a multi-airport metro it may not be the cheapest international gateway
(e.g. LGA before JFK for New York).

Recommended first request after the database is loaded:

```bash
curl -s http://localhost:8000/health
```

Then point your MCP client at `http://localhost:8000/mcp`.

### Deploy

```bash
docker build -t travel-ranking .
docker run --rm -p 8000:8000 \
  -e SUPABASE_DB_URL="postgresql://..." \
  -e MCP_PATH="/mcp-<random-32-char-hex>" \
  travel-ranking
```

ChatGPT cannot reach `localhost`; use a public HTTPS host (Railway, Fly.io,
Render, Cloud Run) or OpenAI's Secure MCP Tunnel. The server is read-only except
for the two quote-cache tables, so a public endpoint relies on an unguessable
`MCP_PATH`; set `MCP_PATH` to a random value rather than leaving `/mcp` public.
Add `SERPAPI_API_KEY` only when live pricing is wanted.

## 11. Measured Phase 3 local timings

These are from the local PostGIS 15 workspace used for development, with warm
downloads and a single-country scope:

| Step | LU | Notes |
| --- | --- | --- |
| schema + scorer | < 1 s | `CREATE OR REPLACE FUNCTION` |
| Ookla (LU bbox) | ~10 s | one HTTPS Parquet scan |
| Overture transportation (LU bbox) | ~15 s | one S3 scan |
| Mobility Database GTFS join | ~1 s | global CSV, 2.7k usable feeds |
| Inside Airbnb (local fixture) | < 1 s | local files |
| Transit stops, global Overture Places | ~25 min | 9,008 cities; S3-bound |
| v3 ranking | < 1 s | expected sub-second on 13k rows |

Full global Phase 3 is bandwidth-bound, especially the Overture transportation
scan. Use `--countries` for a smoke test, then a single global pass when the
network is stable.

## 12. Third-party data, licences and provenance

Every dataset in this project is third-party data. The only first-party
artifacts are the derived scores, percentiles, `data_completeness` and the
loader code. The full source-by-source registry, with URLs, licences, coverage,
granularity and implementation status, is in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md); a measured snapshot is in
[`docs/COVERAGE_REPORT.md`](docs/COVERAGE_REPORT.md).

Implemented third-party sources:

| Dimension | Source | Licence / terms | Coverage |
| --- | --- | --- | --- |
| Cities | GeoNames `cities15000` | CC BY; attribution required | global |
| Airports | OurAirports | Unlicense/public domain | global |
| Climate | WorldClim 2.1 `5m` | **non-commercial academic use; redistribution/commercial use requires permission** | global |
| POIs | Overture Maps Places | CDLA-Permissive 2.0 (some source datasets Apache 2.0/CC0) | global |
| Walkability/rail | Overture Maps Transportation | **ODbL; © OpenStreetMap contributors; share-alike for derived databases** | partial: 1,131 cities across DE/GB/TR/ES/FR/PT/SE/LU |
| Walkability score (0-100) | How Walkable city ranking (Overture Maps + OpenStreetMap) | CC BY 4.0; attribution and link required | 690 cities ranked, 662 matched to loaded destinations |
| Annual PM2.5 | SatPM V6.GL.03 (CNN) | CC BY 4.0; attribution required | 13,101 cities, 0.1° city-point sample |
| Monthly PM2.5 | SatPM V6.GL.03 monthly | CC BY 4.0; attribution required | 13,101 cities × 12 months, 2024 |
| City rent | Eurostat `prc_colc_rents` + ONS PIPR + HUD FMR | Eurostat reuse / OGL v3.0 / public domain | ~1,250 cities in local currency; not a rank input |
| Travel advisories | US State Department + UK FCDO | Public domain / OGL v3.0 | ~220 countries each; two separate scales, not averaged |
| Facilities | Overture Maps Places | CDLA-Permissive 2.0 | 13,101 cities within 5 km: hospital, pharmacy, school, grocery, park, beach |
| Airport access | OurAirports | Public domain | 13,101 cities; nearest scheduled airport and 100 km counts |
| Scheduled transit | Mobility Database GTFS | CC0 catalogue; producer licences | weekday peak, midday and Sunday headways/span; initial Italy/France load |
| Nature | RESOLVE Ecoregions + Amatulli topography | CC BY 4.0 / CC BY 3.0 | 12,927 city landscapes; 13,101 terrains |
| Land cover | ESA WorldCover 2021 | CC BY 4.0 | 1 km and 15 km class fractions; initial largest-city batch |
| Inland water | HydroLAKES v1.0 | CC BY 4.0 | 13,101 cities; nearest lake/reservoir |
| Climate class | Köppen-Geiger 1991-2020 | CC BY 4.0 | 13,101 cities; class label only |
| Hazard exposure | GHSL UCDB / MYRIAD-HES | CC BY 4.0 | 11,422 urban centres; event counts by hazard type, 2015 window |
| Health access | GHSL UCDB / healthsites.io | ODbL / EC reuse | 11,422 urban centres; hospital and pharmacy access |
| Broadband | Ookla Open Data | **CC BY-NC-SA 4.0; non-commercial; attribution required** | global tiles |
| GTFS coverage | Mobility Database | catalog metadata CC0 1.0; individual feeds retain provider licences | global catalog |
| Public transport stops | Overture Places transit facilities | CDLA-Permissive 2.0 / per-provider terms | global where Overture Places has facilities |
| Housing (optional) | Inside Airbnb | CC BY 4.0; local currency, no FX conversion | city/regional dumps |
| Tourism pressure (optional) | Eurostat `tour_occ_nin2m` + `demo_r_pjangrp3` | Eurostat reuse with attribution (CC BY 4.0 for editorial content; statistical data reuse subject to Eurostat notice) | EU/EFTA NUTS 2 |
| Live quotes (optional) | SerpApi Google Flights/Hotels | proprietary API terms; not open data | query-time |

**Important commercial-use caveats.** WorldClim and Ookla both restrict
commercial use/redistribution; Overture Transportation is ODbL and share-alike; individual GTFS feeds and
Inside Airbnb data carry their own terms. The walkability score is CC BY 4.0
and must credit How Walkable with a link. Do not redistribute the derived database commercially without checking
those terms. `SerpApi` data is subject to SerpApi/Google terms.

**Missing data policy.** Null means unavailable; it is never replaced with a
fabricated city-level value. The v3 scorer neutral-imputes a missing dimension
at `0.5` **only for ranking arithmetic**, and `data_completeness` continues to
report the missing evidence. Where a global city-level official source does
not exist (for example safety/crime or rent), the dimension remains absent and
the candidate official sources are listed in `docs/DATA_SOURCES.md` rather
than filled with a low-quality proxy.

The supplementary official source currently implemented is Eurostat NUTS 2
tourism pressure:

```bash
python bootstrap_supplemental.py --eurostat-tourism --year 2024
```

It assigns a city to a NUTS 2 region by official GISCO boundaries, stores the
NUTS 2 annual nights and nights per NUTS 2 resident, and leaves non-EU or
unpublished regions NULL.

