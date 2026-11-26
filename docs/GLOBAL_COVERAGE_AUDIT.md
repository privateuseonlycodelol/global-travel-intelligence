# Global Coverage Audit

**Audit date:** 2026-11-25  
**Database snapshot:** `travel_global` at commit `f01f62c8b6e0f8f871087741e906f2c825505bb7`  
**Scope:** every existing and candidate indicator/dataset considered for the City Quality Index (CQI), including the GHSL UCDB R2024A themes, ESA WorldCover, NASA POWER, WorldClim, ERA5, Overture, GTFS, How Walkable, Ookla and the World Bank/WHO/UN country series.  
**Method:** Wave 1 read-only audit. Coverage counts are `SELECT`-only queries against the snapshot; benchmarks were read-only against public endpoints. No database writes and no ETL were performed.

> **Denominator warning.** The product target "10,389 urban centres" is **10,389 principal cities**, a destination-level dedupe, not 10,389 GHSL polygons. GHSL UCDB R2024A contains **11,422 urban centres**. Only **7,018** principal cities are matched to a centre. Every percentage in this document states its denominator.

> **Ranking update (2026-11-26).** The all-city headline index was reweighted to seven dimensions: culture/food 25%, public transport 15%, walkability 15%, climate 15%, environment 10%, safety/health 10% and connectivity 10%. Affordability was removed from the score. All 10,389 principal cities are ranked; 544 have at least 0.90 coverage. The 2,316/22.3% completeness figures below describe the earlier eight-dimension CQI and are retained as an audit of that version. See [QUALITY_INDEX.md](QUALITY_INDEX.md).

## Contents

- [Executive Summary](#executive-summary)
- [Denominator Clarification](#denominator-clarification)
- [Coverage Audit](#coverage-audit)
- [Indicator Classification](#indicator-classification)
- [Rejected Proxies and Methodological Gaps Register](#rejected-proxies-and-methodological-gaps-register)
- [Redesign Architecture and Expected Coverage Impact](#redesign-architecture-and-expected-coverage-impact)
- [Provenance and Reproducibility](#provenance-and-reproducibility)
- [Open Questions for the Lead](#open-questions-for-the-lead)

## Executive Summary

The audit reaches seven conclusions.

1. **A globally consistent Core index cannot be built from the current eight-dimension CQI.** Only **2,316 of 10,389 principal cities (22.3%)** reach `data_completeness >= 0.90`; **0%** of cities below 100k do. The 2,316-city complete set is **8.0% African**, against 15.6% of all principal cities, and the top 100 principal cities are **EU 61, AS 17, NA 15, OC 6, SA 1, AF 0**. The current all-city ranking is effectively a data-rich Europe/North America ranking.

2. **Nine destination-row-complete foundations already exist at 100%:** annual air quality, airport access, Köppen climate class, geography, inland-water distance, river access, terrain, and the **row** coverage of facilities and POIs. Monthly temperature and precipitation are at 99.98%. Safety/health and affordability country proxies are at 99.61% and 99.14%. These are the only defensible Core inputs held today; facility and POI rows are not Core comparative metrics because their zeros are `none_listed`, not measured zeros.

3. **One view-anchoring bug can be fixed immediately without new data.** Air and water scores are present for only **511 of 13,101 destinations (3.90%)** because the all-city view joins the 516-row GHSL greenery table and the 5,000-row land-cover table. Lifting the identical formulas to a destination-driven view yields **13,101 air and 13,101 water scores (100%)**, with a validation mean absolute error of **0.0000** against the 5,000 overlapping rows. This fix raises two components but does not by itself raise environment or completeness until the greenery gate is replaced.

4. **Three fixes globalize the two weakest Core dimensions.**
   - **WorldCover 10 m greenery:** extend the polygon method from 516 centres to the **7,018 matched GHSL polygons**, and add a documented 10 m disc for the **3,371 unmatched** principal cities. Measured cost is **64-106 min** single-threaded for the polygons plus **49 min** for the discs, or **20-40 min with 4-6 workers**, with about **0.5 GB** of transfer and under 1 GB RAM. This reaches **100% of principal cities** with a visible method flag.
   - **NASA POWER climate:** fill the remaining **8,422 destinations** (6,632 principal) with the same 2015-2024 daily request already used by `bootstrap_climate_recent.py`. Measured cost is **1.029 s and 179,198 bytes per city**, so **2.76 h serial and 1.51 GB** for all missing destinations, or **2.17 h** for principal cities. No code change is needed. The existing climate formula, however, is not a valid comfort metric: it ranks hyper-arid Atacama cities first (Alto Hospicio 99.9, Iquique 99.8, Calama 99.7) and correlates **-0.273** with GHSL UTCI >32 days.
   - **Air/water formula lift:** no download; recompute all 13,101 destinations from already-loaded tables.

5. **The current CQI weights put 40% on dimensions that fail the bar.** Culture/food (20%) is 100% nominal but has **2,579 restaurant zeros (19.7%)**, **China 1,144 zeros (80.3% of Chinese destinations)** and **Africa 42.5%**; walkability (10%) is **662 cities (5.05%)**, **0% below 300k** and **Africa 0.7%**; public transport (10%) is **9,008 cities with mapped transit facilities (68.76%)**, **GTFS 4,000 (30.53%)** and **headway 544 (4.15%)**. All three must move to the Experimental layer.

6. **The GHSL UCDB layer is excellent internally but cannot be the global Core.** It covers **11,422 of 11,422 centres (100%)** for most themes with an exact ID join, CC BY 4.0 with theme exceptions, about **432 MiB** total and measured load times under a minute for small themes. But only **7,018 of 10,389 principal cities (67.6%)** are matched, because 3,371 principal cities are below the UCDB cutoff or outside the 5 km buffer. UCDB themes cannot exceed 74.3% destination reach by exact ID join. They belong in a Supplementary centre-level layer.

7. **Four gaps have no defensible global city-level source in the evaluated set:** city-level crime, city-level rent, global walkability, and global transit frequency. They must remain explicit gaps, not proxies in the headline. City-level culture/food and recent global hazard probability are similarly unresolved.

### Current position at a glance

| Measure | Value |
|---|---:|
| Destinations | 13,101 |
| Principal cities (dedupe) | 10,389 |
| GHSL UCDB R2024A urban centres | 11,422 |
| Principal cities matched to a centre | 7,018 (67.6%) |
| Destinations matched to a centre | 9,730 (74.3%) |
| CQI `city_quality_index` (>=1M centres) | 516 |
| All-city CQI completeness >=0.90 | 3,037 / 13,101 (23.2%) |
| Principal CQI completeness >=0.90 | 2,316 / 10,389 (22.3%) |
| Top-100 principal cities by continent | EU 61, AS 17, NA 15, OC 6, SA 1, AF 0 |
| Air score / water score | 511 each (3.9%) |
| Walkability | 662 (5.05%) |
| Restaurant zeros | 2,579 (19.7%) |
| Transit stops / GTFS / headway | 9,008 (68.76%) / 4,000 (30.53%) / 544 (4.15%) |
| GHSL centres with hazard rows | 11,422 / 11,422 (100%) |
| GHSL theme destination reach | 9,730 (74.3%) |
| WorldCover 10 m urban land cover | 516 (3.9%) |
| NASA POWER recent climate | 4,679 (35.7%) |
| WorldCover 1 km/15 km land cover | 5,000 (38.2%) |

## Denominator Clarification

### Destination, principal-city and urban-centre universes

| Universe | Count | Definition |
|---|---:|---|
| Destinations | 13,101 | GeoNames global cities >=40k after metro dedupe |
| Principal cities | 10,389 | One row per `COALESCE(destination_metro.urban_centre_id::text, destination_id)`, keeping the highest-population row; 2,712 duplicate rows collapsed |
| Principal cities matched to a GHSL centre | 7,018 | One principal city per matched centre; 9,730 destinations match in total (9,065 by polygon containment plus 665 by <=5 km edge match; average 2.22 km, maximum 4.996 km) |
| Principal cities with no GHSL centre | 3,371 | 25.7% of destinations, 32.4% of principal cities |
| GHSL UCDB R2024A urban centres | 11,422 | 1 km GHS-SMOD polygons, 2025 fixed boundaries |
| Centres with a destination | 7,018 | 61.4% of centres |
| Centres without a destination | 4,404 | 38.6% of centres |
| Destination-to-centre matches | 9,730 | 74.3% of destinations |

All 13,101 destinations join to `country_facts` (252 country rows). Six continents have destinations: **AS 5,886; EU 2,242; AF 1,895; NA 1,650; SA 1,355; OC 73**. There are no Antarctic destinations.

### Population composition

| Population bracket | All destinations | Principal cities |
|---|---:|---:|
| <100k | 7,624 | 6,000 |
| 100k-300k | 3,607 | 2,838 |
| 300k-1M | 1,319 | 1,064 |
| 1M+ | 551 | 487 |
| **Total** | **13,101** | **10,389** |

### The 3,371 unmatched principal cities

- **3,045 (90.3%) are below 100k.** Only three are >=1M: **Dubai AE 3.79M** (6.18 km from the centre edge), **Budta PH 1.27M** (6.71 km) and **Xinxiang CN 1.05M** (6.85 km). All three sit just outside the 5 km nearby buffer.
- **Six destinations are in countries with no GHSL centre label at all:** Antigua and Barbuda, Guadeloupe, Guam, Kiribati, Northern Mariana Islands and U.S. Virgin Islands.
- For unmatched cities, the nearest same-country centre is: **0 within 5 km, 442 at 5-10 km, 1,011 at 10-25 km, 826 at 25-50 km, 671 at 50-100 km, and 421 none within 100 km**; median distance **25.4 km**.
- Any-country distances are only slightly better: **23 within 5 km** and **467 within 10 km** (439 of those same-country, 28 cross-border; lifting coverage to 10,197/13,101 = 77.83%). Country guards exclude cross-border matches.
- A 400-city random sample gives a longer distribution: **3 <=5 km, 52 <=10 km, 91 <=15 km, 167 <=25 km, 270 <=50 km**; median **31.2 km**, p90 **103.1 km**. Most unmatched cities genuinely need a point or disc measure, not a larger match radius.
- Principal-city no-centre share by population bracket: **<100k 50.7%, 100k-300k 10.0%, 300k-1M 3.6%, 1M+ 0.6%**. By continent: **OC 47.0%, SA 42.6%, EU 41.9%, NA 41.3%, AS 26.6%, AF 25.0%**. The bias is a size bias, not a single-continent defect.

### Match-radius trade-off

| Edge buffer | Destinations reached | Share of all destinations | Assessment |
|---:|---:|---:|---|
| 5 km (current) | 9,730 | 74.3% | Strict; Dubai and two other >=1M cities missed |
| 10 km | 10,195 | 77.8% | Defensible if a visible `nearby` provenance flag is added |
| 25 km | 11,183 | 85.4% | Too loose for a metro match |
| 50 km | 12,009 | 91.7% | Repeats the Hanoi/Quoc-Oai 19 km false-merge failure |

The recommendation from the evidence is a **10 km maximum** with a provenance flag, not a blanket 25-50 km buffer. UCDB themes alone cannot reach 90% because the UCDB universe is 50k+ centres; the >=90% route must be direct global sources or a country/admin1 fallback.

## Coverage Audit

### Criteria and rating rules

The audit covers the CQI dimensions and their candidate replacements. Non-CQI context tables (for example landscape, tourism and travel advisories) are outside this audit; the gaps that matter for a global quality index are recorded in the gap register.

| Code | Criterion | What is assessed |
|---|---|---|
| C1 | Coverage yield | Total number and percentage of **urban centres**, **principal cities** and **destinations** covered |
| C2 | Distribution | Coverage by continent, worst-covered countries, and population bracket (<100k, 100k-300k, 300k-1M, 1M+) |
| C3 | Geometry | Spatial resolution and geographical consistency (point, disc, polygon, country, zonal) |
| C4 | Construct and date | Measurement methodology, observation window and temporal comparability |
| C5 | Bias | Known geographical and socioeconomic biases, including zero-vs-missing conflation |
| C6 | Global feasibility | Whether the indicator can be calculated globally through spatial processing, with measured runtime and transfer where available |
| C7 | Access | Download availability, licensing, and computational requirements |

Rating convention: **PASS** means >=90% and no severe regional bias; **PARTIAL** means 70-90% or a construct/coverage limitation; **FAIL** means <70% or a severe regional bias. A centre-based dataset with 100% of GHSL centres may be **Supplementary** even when its destination reach is below 70%, because the match limit is a property of the destination universe, not of the source. Severe bias is assessed from the measured tables: a dimension is not Core if any continent falls below 50%, or if the <100k bracket falls below 50% while 1M+ exceeds 90%, or if a country-level zero/missing mechanism affects more than 20% of a region.

### Existing production indicators: seven-criteria matrix

#### Destination-complete and near-complete foundations

| Indicator | C1 Yield (all / principal / centres) | C2 Distribution | C3 Geometry | C4 Construct and date | C5 Known bias | C6 Global feasibility | C7 Access | Class |
|---|---|---|---|---|---|---|---|---|
| `air_quality_annual` | 13,101 (100%) / 10,389 (100%) / n.a. | Global grid; no regional gap identified | 0.1 degree point sample; globally consistent | SatPM V6.GL.03 annual PM2.5, 2024; 0 NULL; range 2.549-130.871 ug/m3 | City-point sample, not population-weighted | Already global; no processing | Loaded | **Core** |
| `airport_access` | 13,101 (100%) / 10,389 (100%) / n.a. | Global; 155 (1.18%) no nearest IATA; 13.0% >100 km; 37.9% zero large airports within 100 km | Point to nearest airport plus 100 km radius | OurAirports nearest scheduled-service airport | Nearest-airport semantics; remote and island cities legitimately far | Already global | Loaded | **Core (context)** |
| `climate_monthly` | 13,099 (99.98%) / 10,387 (99.98%) / n.a. | Only Labuan and Bandar Labuan missing | 5 arcmin WorldClim 2.1; point sample | 1970-2000 monthly climatology; temperature and precipitation only | Non-commercial licence; 1970-2000 baseline; `avg_rainy_days` and `avg_hot_days_30c` are 0% non-null in 157,188 rows | Already loaded; cannot produce a rainy-day score without a fill or model | Loaded; non-commercial | **Core for temperature/precipitation; not a climate-score source** |
| `destination_climate_class` | 13,101 (100%) / 10,389 (100%) / n.a. | 73 rows (0.56%) have NULL `koppen_code` | Point classification; globally consistent | Koppen-Geiger 1991-2020 class at the city point | 0.56% NULL; class is categorical, not a quality score | Already global | Loaded | **Core** |
| `destination_geography` | 13,101 (100%) / 10,389 (100%) / n.a. | Elevation NULL 17 (0.13%); coastline distance 0 NULL | Point elevation plus Natural Earth coastline distance | GeoNames elevation and Natural Earth coastline | GeoNames elevation quality varies by country | Already global | Loaded | **Core (context)** |
| `destination_inland_water` | 13,101 (100%) / 10,389 (100%) / n.a. | 10,807 (82.5%) <=25 km; 2,107 (16.1%) 25-100 km; 187 (1.4%) >100 km; maximum 809.6 km | Nearest HydroLAKES shore; nearest-feature distance | HydroLAKES nearest lake/reservoir shore, area and type; lakes >=10 km2 | "100%" is nearest-feature distance, not water presence; 187 cities are >100 km from a lake | Already global | Loaded | **Core with thresholded access (<=25 km: 82.5%)** |
| `destination_river_access` | 13,101 (100%) / 10,389 (100%) / n.a. | 11,486 (87.7%) <=25 km; 1,531 (11.7%) 25-100 km; 84 (0.6%) >100 km; maximum 7,441.9 km; discharge zero 216 | Nearest HydroRIVERS reach; nearest-feature distance | HydroRIVERS nearest Strahler >=4 reach, order, discharge and distance | "100%" is nearest-feature distance; 216 zero-discharge reaches | Already global | Loaded | **Core with thresholded access (<=25 km: 87.7%)** |
| `destination_terrain` | 13,101 (100%) / 10,389 (100%) / n.a. | All non-null at 10 km radius | 10 km median elevation, standard deviation, TRI and TPI | Amatulli 2018 GMTED | 10 km smoothing hides local relief | Already global | Loaded | **Core (context)** |
| `country_indicators` | 2,454 values / 216 ISO2 codes / 13 indicators | Destination-weighted: safely managed water 78.8%; sanitation 88.7%; homicide 93.3%; hospital beds 94.3%; all others 98.0-99.6% | Country-level; every city in a country shares the value | World Bank series; homicide, road deaths, life expectancy, physicians, price level, out-of-pocket and others | Country granularity; road deaths 183/190 values are 2019 and all are pre-2020; Taiwan missing from World Bank/WHO | No city-level spatial fix; country fill only | Loaded | **Core for 11 of 13; water and sanitation Supplementary** |
| `safety_health` composite | 13,048 (99.61%) / 10,354 (99.66%) / 516: 512 (99.22%) | Homicide missing 6.68% overall and 23.2% in Africa; top-decile safety/health within continent is OC 52.1%, EU 32.6%, AS 9.1%, NA 0%, SA 0%, AF 0% | Country-level composite | Homicide 35%, road deaths 25%, life expectancy 25%, physicians 15% | Country-level; stale road deaths; Taiwan null; homicide methodology break | No city-level fix; WHO/UNODC fill is country-level | Loaded | **Core (country-level proxy), conditional on homicide fill** |
| `affordability` composite | 12,988 (99.14%) / 10,297 (99.11%) / 516: 511 (99.03%) | Missing North Korea 57, Taiwan 39, Venezuela 99, Cuba 53 and small French/other territories; top-decile affordability within continent is AF 27.1%, AS 12.7%, NA 3.2%, OC 1.4%, SA 0.1%, EU 0% | Country-level composite | Price level 70%, out-of-pocket health spending 30% | Country price proxy, not city cost of living; 179 distinct price and 175 OOP values for 13,101 cities | No city-level fix | Loaded | **Core (country-level proxy)** |
| `broadband_metrics` | 11,902 (90.85%) / 9,251 (89.05%) / 516: 498 (96.51%) | Missing Russia 411, Iran 210, China 86, Cuba 53, North Korea 52, DRC 44, Nigeria 42; EU 78.9% only because Russia/Ukraine/Belarus are in the EU group, excluding them EU principal coverage is 100% (1,307/1,307); with `total_tests>=10`, principal coverage is 84.20% | Ookla fixed tiles | Ookla 2026 Q2 fixed broadband | Regulatory and measurement gaps; 531 cities (4.5%) have `total_tests<10` and 231 have <=1 tile; low-sample share AF 14.2% | No spatial fill; M-Lab NDT or ITU are candidates | Loaded; Ookla CC BY-NC-SA 4.0 | **Supplementary** |

#### Partial, thresholded and composite indicators

| Indicator | C1 Yield (all / principal / centres) | C2 Distribution | C3 Geometry | C4 Construct and date | C5 Known bias | C6 Global feasibility | C7 Access | Class |
|---|---|---|---|---|---|---|---|---|
| `facility_metrics` | 13,101 (100%) / 10,389 (100%) / n.a. | Zero rates: park 26.4%, hospital 18.6%, pharmacy 24.5%, school 15.1%, grocery 21.3%, beach 61.4%; park zero AF 65.1% vs EU 3.1% | 5 km point counts | Overture Places counts within 5 km | Zero is `none_listed`, not measured zero; Overture coverage bias; beach zero is largely inland | Rows already global; comparative metric not | Loaded; Overture CDLA-Permissive 2.0 | **Core rows; comparative metric Experimental** |
| `poi_metrics` | 13,101 (100%) / 10,389 (100%) / n.a. | Restaurant zero 19.7%, museum zero 10.6%, cuisine entropy zero 30.1%; AF 42.5/18.9/64.0, AS 27.9/17.1/40.9, EU 3.2/0.4/7.8, NA 1.5/0.2/3.7; China 1,144 restaurant zeros (80.3%) | 5 km point counts | Overture Places global scan | `COALESCE(...,0)` conflates no coverage with true zero; severe AF/AS/China bias; top-decile culture NA 26.5% vs AF 0.6% | Rows global; comparative metric not | Loaded; Overture CDLA-Permissive 2.0 | **Core rows; comparative metric Experimental** |
| `urban_centre_hazard` | 11,422 centres (100%) / 7,018 principal (67.6%) / 9,730 destinations (74.3%) | Principal AS 73.4%, EU 58.1%, AF 75.0%, SA 57.4%, NA 58.7%, OC 53.0%; small-city bias | Centre polygon; event counts | MYRIAD-HES 2015 window (2014-2016); 11,362 centres (99.5%) have >=1 event; heatwave 10,771 (94.3%), tropical cyclone 2,555, flood 1,801, earthquake 390 | 2015-only event count, not a probability or forecast; metro-only destination reach | Source is complete for centres; destination cap is the match | Loaded; GHSL CC BY 4.0, ACLED excluded | **Supplementary** |
| `urban_centre_health` | 11,422 centres (100%) / 7,018 principal (67.6%) / 9,730 destinations (74.3%); hospital count 6,237 (54.6%), pharmacy 2,396 (21.0%), hospital access share 6,434 (56.3%) | Principal match bias; hospital 1 km access share AS 33.7%, AF 34.7%, EU 34.2%, NA 21.4%, SA 39.7%, OC 21.0%; pharmacy 17.9/14.9/19.0/20.4/31.1/15.4 | Centre polygon; 1 km access share | healthsites.io/OSM, 2024-2025 | Sparse values; metro-only; OSM completeness varies | Source complete for centres; values not | Loaded; HEALTH ODbL | **Supplementary for hospital access; pharmacy Experimental** |
| `mobility_metrics` | 9,553 (72.9%) / 7,207 (69.4%); transit stops 9,008 (68.8%) / 6,778 (65.2%); GTFS 4,000 (30.5%) / 2,690 (25.9%); pedestrian ratio 1,131 (8.6%) / 831 (8.0%); headway 544 (4.15%) / 497 (4.78%) | Stops AF 30.4% vs EU 96.7%; principal AF 25.9% vs EU 92.9%; GTFS AF 3.5%; headway 33/516; GDP-PPP Q1 28.6% vs Q4 91.5% | 5 km stop counts; GTFS bbox; stop-level headway | Overture Places, Mobility Database and GTFS feeds | 4,093 destinations lack transit stops; 330 have GTFS but zero Overture stops; Overture counts stations/facilities, not bus stops; 546 cities are FlixBus/BlaBlaCar intercity-only; 527 of those still have Overture stops | Union of stops plus GTFS is 72.9%; no global frequency source | Loaded | **Experimental (severe regional bias)** |
| `walkability_scores` | 662 (5.05%) / 570 (5.49%) / 516: 251 (48.64%) | All-city AF 0.7%, AS 4.3%, EU 5.6%, NA 7.9%, SA 9.2%, OC 17.8%; 516-core AF 13.5%, AS 39.6%, EU 82.6%, NA 92.0%, SA 90.2%, OC 100%; 0% below 300k; only 95 of 192 countries have any value | City-level 0-100 score | How Walkable, observed 2026-10-22; 690 ranked cities >=300k, 662 matched | Structural exclusion of mainland China, South Korea and Russia; 0% below 300k; Africa 0.7% | No defensible >=90% global candidate | Loaded; CC BY 4.0 | **Experimental** |
| `destination_ghsl_greenery` | 516 (3.9%) / 516 (5.0%) / source 11,422 centres | Bracket: <100k 0.1%, 100k-300k 0.5%, 300k-1M 10.5%, 1M+ 64.4%; continents 2.1-6.8% | WorldCover 10 m fractions inside the GHSL polygon, cos(latitude) weighted | WorldCover 2021 | Only centres with UCDB population >=1M; 516 of 524 such centres; 5 of 516 lack air/water because they fall below the land-cover population threshold | Scalable to 7,018 by removing the >=1M filter; 64-106 min plus 49 min discs | Loaded; CC BY 4.0 | **Experimental until expanded; Core candidate** |
| `destination_urban_land_cover` | 516 (3.9%) / 516 (5.0%) / 11,422 source centres | <100k 0.1%, 100k-300k 0.6%, 300k-1M 13.1%, 1M+ 72.9%; continents 2.1-6.8% | Native 10 m, 11 classes, cos(latitude) area weights, polygon mask | WorldCover 2021 | >=1M filter; only 516 of 524 eligible centres; not a global city measure | Scalable to 7,018; measured ~2-2.5 h serial, ~0.5 GB transfer | Loaded; CC BY 4.0 | **Experimental until expanded; Core candidate** |
| `climate_recent` | 4,679 (35.7%) / 3,757 (36.2%) / n.a. | Principal bracket: <100k 1.6%, 100k-300k 74.4%, 300k-1M 100%, 1M+ 100%; missing set is exactly 8,422 destinations (6,632 principal), all <=120k; all-city continents 28.6-39.5% | 0.5 degree NASA POWER point | NASA POWER 2015-2024 daily to monthly normals | Small-city gap; current CQI formula rewards zero rain and ranks Atacama first | Yes: 8,422 missing = 2.76 h serial and 1.51 GB; 6,632 principal = 2.17 h; no code change | Loaded; public domain, citation requested | **Experimental until filled; Core candidate (data), construct needs redesign** |
| `destination_environment_score` | 5,000 (38.2%) / 4,010 (38.6%); 4,038 scored | Exactly the top 5,000 destinations by population; 0% below 100k; 86.8% of 100k-300k all-city; 100% of 300k+ | 1 km and 15 km discs; parks; air; water | WorldCover 2021, Overture, SatPM | Top-5,000 cutoff; 960 park zeros (19.2%) scored as NULL; mixed source | Not global until the greenery gate is replaced | Loaded | **Experimental** |
| `destination_land_cover` | 5,000 (38.2%) / 4,010 (38.6%) | Exactly the top 5,000 destinations; 0% below 100k; 86.8% of 100k-300k all-city | 1 km and 15 km discs | WorldCover 2021 | Top-5,000 cutoff; point-disc construct correlates only r=0.333 with polygon canopy | Global discs are 1.5 h; polygon method is preferable | Loaded; CC BY 4.0 | **Experimental** |
| `city_quality_index` | 516 / 516 / 516 centres | >=1M centres only; 445 (86.2%) reach completeness >=0.90 | Composite at GHSL centre | Eight dimensions with fixed weights | Scope-limited; dimension biases; renormalization | Not global; 516 centres | Loaded | **Experimental (scope-limited)** |
| `city_quality_index_all` | 13,101 / 10,389; `quality_score` 100% non-null; completeness >=0.90 is 3,037 (23.2%) all-city and 2,316 (22.3%) principal; all eight dimensions 625 (4.8%); mean completeness 0.663, median 0.600 | 0% of <100k complete; 47.9% of 100k-300k; 66.7% of 300k-1M; 77.9% of 1M+; top-100 principal EU 61, AS 17, NA 15, OC 6, SA 1, AF 0 | Composite at destination | Eight dimensions with renormalization | Renormalization hazard; Taiwan; missing dimensions; median completeness 0.60 | Not global; needs Core redesign | Loaded | **Experimental** |
| `street_network_metrics` | 8,444 (64.5%) / 6,747 (64.94%) / n.a. | Not fully audited by bracket | Street-network metrics | Boeing Global Urban Street Networks | Intersection density correlates **-0.181** with How Walkable; Houston outranks Berlin; docs call it urban-form context, not walkability | Global source but the construct is invalid for walkability | Loaded | **Rejected as a walkability proxy; context only** |
| `ghsci_city_indicators` | 25 destinations / n.a. / n.a. | 25-city study plus Trento pilot | City z-scores | GHSCI | Tiny sample; z-score not 0-100 | No | Loaded | **Validation only** |

### Coverage detail: continents, countries and population brackets

The table below is the authoritative all-destination yield for every existing indicator that is not at 100%.

| Indicator | All n (%) | Principal n (%) | Class |
|---|---:|---:|---|
| `destination_ghsl_greenery` | 516 (3.9) | 516 (5.0) | Experimental |
| `destination_urban_land_cover` | 516 (3.9) | 516 (5.0) | Experimental |
| `walkability_scores` | 662 (5.1) | 570 (5.5) | Experimental |
| `climate_recent` | 4,679 (35.7) | 3,757 (36.2) | Experimental |
| `destination_environment_score` | 5,000 (38.2) | 4,010 (38.6) | Experimental |
| `destination_land_cover` | 5,000 (38.2) | 4,010 (38.6) | Experimental |
| `mobility_metrics` | 9,553 (72.9) | 7,207 (69.4) | Experimental (severe regional bias) |
| `urban_centre_hazard` via metro | 9,730 (74.3) | 7,018 (67.6) | Supplementary |
| `urban_centre_health` via metro | 9,730 (74.3) | 7,018 (67.6) | Supplementary hospital access; Experimental pharmacy |
| `broadband_metrics` | 11,902 (90.8) | 9,251 (89.0) | Supplementary |
| `air_quality_annual` | 13,101 (100) | 10,389 (100) | Core |
| `airport_access` | 13,101 (100) | 10,389 (100) | Core |
| `climate_monthly` | 13,099 (100.0) | 10,387 (100) | Core for temperature/precipitation; rainy/hot fields 0% |
| `destination_climate_class` | 13,101 (100) | 10,389 (100) | Core |
| `destination_geography` | 13,101 (100) | 10,389 (100) | Core |
| `destination_inland_water` | 13,101 (100) | 10,389 (100) | Core (nearest-feature semantics) |
| `destination_river_access` | 13,101 (100) | 10,389 (100) | Core (nearest-feature semantics) |
| `destination_terrain` | 13,101 (100) | 10,389 (100) | Core |
| `facility_metrics` | 13,101 (100) | 10,389 (100) | Core rows; park zero bias |
| `poi_metrics` | 13,101 (100) | 10,389 (100) | Core rows; zero bias |

#### Coverage by continent

All-destination percentages for the partial indicators:

| Indicator | AS | EU | AF | NA | SA | OC |
|---|---:|---:|---:|---:|---:|---:|
| `mobility_metrics` | 70.5 | 96.7 | 30.4 | 91.5 | 79.9 | 90.4 |
| `urban_centre_hazard` / `urban_centre_health` | 78.8 | 65.9 | 78.6 | 73.6 | 63.8 | 57.5 |
| `broadband_metrics` | 93.0 | 78.9 | 86.4 | 96.7 | 99.8 | 100.0 |
| `walkability_scores` | 4.3 | 5.6 | 0.7 | 7.9 | 9.2 | 17.8 |
| `climate_recent` | 39.5 | 33.5 | 33.8 | 28.6 | 34.1 | 37.0 |
| `destination_environment_score` / `destination_land_cover` | 43.2 | 29.8 | 37.6 | 32.2 | 38.2 | 39.7 |
| `destination_ghsl_greenery` / `destination_urban_land_cover` | 4.8 | 2.1 | 4.7 | 3.0 | 3.0 | 6.8 |
| `air_quality_annual`, `airport_access`, `destination_terrain`, `destination_inland_water`, `destination_river_access`, `destination_geography`, `destination_climate_class`, `facility_metrics`, `poi_metrics` | 100 | 100 | 100 | 100 | 100 | 100 |

#### Coverage by population bracket

All-destination percentages:

| Indicator | <100k | 100k-300k | 300k-1M | 1M+ |
|---|---:|---:|---:|---:|
| `destination_ghsl_greenery` / `destination_urban_land_cover` | 0.1 | 0.5 | 10.5 | 64.4 |
| `walkability_scores` | 0.0 | 0.0 | 33.5 | 39.9 |
| `destination_environment_score` / `destination_land_cover` | 0.0 | 86.8 | 100.0 | 100.0 |
| `climate_recent` | 1.8 | 74.1 | 100.0 | 100.0 |
| `mobility_metrics` | 68.1 | 76.7 | 83.6 | 88.4 |
| `urban_centre_hazard` / `urban_centre_health` | 60.1 | 92.1 | 97.1 | 99.5 |
| `broadband_metrics` | 89.7 | 92.3 | 91.6 | 95.5 |

#### Worst-covered countries

- **Climate and land cover:** principal-city coverage (climate_recent / land_cover / GHSL theme) for countries with >=40 principal cities is: **CN 1,212: 43.5 / 46.4 / 66.8; IN 1,118: 31.4 / 34.8 / 93.5; BR 719: 26.7 / 30.5 / 47.7; US 614: 26.2 / 30.0 / 47.7; RU 393: 33.6 / 35.9 / 54.7; JP 262: 36.3 / 41.6 / 45.8; DE 206: 24.8 / 26.7 / 46.6; IT 137: 100.0 / 27.0 / 62.0**. Italy is the only country at 100% climate_recent below the 120k cutoff, evidence of a country batch run; the rest track the population cutoff.
- **Walkability:** countries with >=20 destinations and zero walkability include **China 1,424, Russia 413, Nigeria 149, Algeria 129, South Korea 107, DRC 101, Venezuela 99, Ethiopia 97, Angola 71, Tanzania 71, Iraq 69, North Korea 57**. Only 95 of 192 destination countries have any walkability value.
- **Broadband:** missing values are concentrated in **Russia 411, Iran 210, China 86, Cuba 53, North Korea 52, DRC 44, Nigeria 42, Syria 37, Ukraine 37, Angola 34, Ethiopia 30, Belarus 24, Tanzania 15, Cameroon 14, Myanmar 14**. The EU figure is depressed by Russia/Ukraine/Belarus being grouped into the EU continent; excluding those three, EU principal coverage is 100%.
- **Homicide:** missing values are concentrated in **Vietnam 179, DRC 101, Thailand 88, North Korea 57, Ivory Coast 51, Sudan 43, Taiwan 39, Syria 38, Madagascar 35, Mozambique 33, Libya 31, Somalia 26, Mali 24, Guinea 21, Chad 19, Cambodia 16, Gabon 10** (37 countries, 874 destinations).
- **Country water/sanitation:** missing safely managed water values include **China 1,424, Turkey 201, Argentina 116**; sanitation includes **Indonesia 303, Pakistan 231, Iran 212**.
- **Culture/food restaurant zeros:** **China 1,144 (80.3% of Chinese destinations and 44.4% of all restaurant zeros), Iran 160 (75.5%), Nigeria 90 (60.4%), Ethiopia 82 (84.5%), DRC 82 (81.2%), India 73 (5.4%), Russia 62 (15.0%), North Korea 55 (96.5%), Tanzania 49, Angola 43, Algeria 42, Pakistan 41**.
- **GHSL themes:** principal coverage is **AF 75.0%, AS 73.4%, EU 58.1%, NA 58.7%, SA 57.4%, OC 53.0%**. The pattern is a small-city bias, not a single-continent defect: **<100k 49.3%, 100k-300k 90.0%, 300k-1M 96.4%, 1M+ 99.4%** in the principal set.

#### Selection rules proven from data and code

- GHSL greenery and urban land cover are exactly the **516 destinations matched to GHSL centres with UCDB population >=1M** (524 such centres; minimum centre population 1,001,124). They are not the 516 largest destination rows: 512 are in the 100k+ destination set and 4 have destination population below 100k.
- `destination_land_cover` and `destination_environment_score` are exactly the **top 5,000 destinations by population**: the top 5,000 are 5,000/5,000 covered and ranks above 5,000 have zero coverage. That is 91.3% of 100k+ destinations.
- `climate_recent` is a population-ordered fetch that stopped at **4,679 destinations** (4,539 of the top 5,000; 140 outside the top 5,000), or 83.0% of 100k+ destinations.
- Walkability is the How Walkable published set of **690 cities >=300k**, matched to **662** destinations; 0% below 300k.
- Mobility carries three sub-indicators with very different reach: **transit stops 9,008 (68.8%), GTFS active feeds 4,000 (30.5%), pedestrian ratio 1,131 (8.6%)**; principal cities **6,778 (65.2%), 2,690 (25.9%), 831 (8.0%)**.

### Existing indicator bias register

| Bias | Measured evidence | Indicators affected | Required treatment |
|---|---|---|---|
| Overture zero-vs-missing conflation | `poi_metrics` has restaurant zero 2,579 (19.7%), museum zero 1,385 (10.6%), cuisine entropy zero 3,945 (30.1%); `facility_metrics` park zero 3,462 (26.4%), beach zero 8,050 (61.4%); loader writes `COALESCE(count, 0)` | `poi_metrics`, `facility_metrics`, culture/food | Replace zero with NULL when Overture has no coverage; keep an explicit `none_listed` flag |
| China restaurant zero mass | China 1,144 restaurant zeros = 80.3% of 1,424 Chinese destinations and 44.4% of all zeros | Culture/food | Do not score China culture/food until coverage is verified; country screen or official register |
| FlixBus/BlaBlaCar GTFS bbox inflation | 546 cities have only intercity FlixBus/BlaBlaCar feeds; 493 carry `gtfs_active_feeds>0`; top feeds are FlixBus France 693 cities, BlaBlaCar Bus 265, SNCF 140 | GTFS, public transport | Exclude intercity coach feeds from local-service flags or expose a separate `intercity_only` flag |
| Atacama hyper-aridity climate bug | Current formula gives Alto Hospicio 99.9, Iquique 99.8 and Calama 99.7 because zero rain earns the full 30% rain term; correlation with GHSL UTCI >32 days is -0.273; top-100 overlap with a heat-only score is 7/100 | Climate score, CQI | Replace with a seasonality-aware or UTCI comfort metric before promoting climate to Core |
| Taiwan missing World Bank/WHO indicators | 39 destinations; homicide, road deaths, life expectancy, physicians, price and out-of-pocket all missing; 0/39 reach completeness >=0.90; 8 Taiwan cities with completeness <0.50 score >=80 (maximum Yingge 89.1) | Safety/health, affordability, CQI | Add Taiwan national statistics (MOI/DGBAS) or exclude/flag Taiwan; add a renormalization guard |
| Small-city match bias | 3,371 unmatched principal cities, 90.3% below 100k; GHSL theme reach <100k 49.3% vs 1M+ 99.4%; principal no-centre share OC 47.0%, SA 42.6%, EU 41.9% | Every GHSL-derived layer | Keep a `nearby`/`unmatched` provenance flag; use direct global sources for Core |
| Europe/North America culture bias | Culture top-decile share NA 26.5%, EU 15.7%, OC 15.1%, SA 9.2%, AS 6.4%, AF 0.6%; median restaurants 516-core AF 65 vs EU 1,932.5; per-capita normalization does not remove it | Culture/food | Experimental; no headline ranking |
| Walkability structural exclusion | 662/13,101 (5.05%); 0% below 300k; Africa 0.7%; mainland China, South Korea and Russia not ranked; only 95/192 countries | Walkability | Experimental; context only |
| Broadband regulatory/measurement gaps | Missing Russia 411, Iran 210, China 86; with `total_tests>=10`, principal coverage falls to 84.20%; Africa low-sample 14.2% | Connectivity | Supplementary; M-Lab/ITU fill; do not treat low-sample tiles as measured |
| Country-level safety and affordability | 155 distinct homicide, 137 road, 185 life-expectancy, 171 physician, 179 price and 175 OOP values for 13,101 cities; top-decile safety OC 52.1% vs AF 0%; top-decile affordability AF 27.1% vs EU 0% | Safety/health, affordability | Label as country proxy; no city-level claim |
| Hazard recency | 2015 window (2014-2016) event counts; heatwave 10,771 of 11,422 centres; 60 centres have zero events | Hazard | Supplementary; label as historical event count, not probability |
| WorldClim baseline and licence | 1970-2000 climatology; non-commercial licence; `avg_rainy_days` and `avg_hot_days_30c` are 0% non-null in 157,188 rows | Climate | Core only for temperature/precipitation; replace or license for commercial use |
| GHSL GREENNESS unit inconsistency | `GR_SQM_GRN_2025 / GR_SHB_GRN_2025` implies built-up area of only 0.19% of centre area (median), about 200x below plausible; either `GR_SQM` is in 100 m2 units or `GR_SHB` is a percentage | GHSL GREENNESS | Do not ingest `GR_SQM_*`/`GR_SHB_*` without unit confirmation; use `GR_AVG_GRN_*` (0-0.79) and `GR_CTH_AVG_2020` (0-25.55 m) |
| Point-disc greenery construct | 1 km point tree share vs polygon urban tree share: Pearson 0.333, Spearman 0.443, bias -0.106, MAE 0.142; extremes +0.723 and -0.713; green share Pearson 0.331 | Land cover, environment | Not a citywide canopy measure; retain only as a city-centre supplementary sample |
| Nearest-feature water/river semantics | Inland water: 187 cities >100 km, maximum 809.6 km; river: 84 >100 km, maximum 7,441.9 km; 216 zero-discharge reaches | Water/river access | Report thresholded access (<=25 km) alongside nominal 100% |
| Airport nearest semantics | 155 destinations (1.18%) have no nearest IATA; 1,702 (13.0%) are >100 km; 4,970 (37.9%) have zero large airports within 100 km; maximum 776 km | Airport access | Core only as context, with distance bands |
| Country water/sanitation gaps | Safely managed water 10,318 (78.8%) and sanitation 11,627 (88.7%) destination-weighted; China, Turkey, Argentina, Indonesia, Pakistan and Iran are the largest gaps | Country indicators | Supplementary |
| GHSL NATURAL duplicate rows | V1-2 NATURAL_SYSTEMS has 11,433 rows for 11,422 IDs; 11 duplicate ID rows differ only in `NS_TPA_PER_2025` | GHSL NATURAL | Dedupe by ID before load |
| GHSL sparse themes | HEALTH pharmacy 21.0-44.4%; WATER marine 11.7-23.9%; NATURAL protected 4.4-18.5%; CLIMATE LCZ 10.5-28.0% | GHSL themes | Experimental |
| CQI renormalization | 8 cities with completeness <0.50 score >=80 (all Taiwan; maximum Yingge 89.1); 2,316 complete principal cities have walkability in only 563 (24.3%) | CQI | Do not renormalize below a minimum data completeness; publish missing_dimensions |
| CQI composite incompleteness | All-city median completeness 0.600; only 625 (4.8%) have all eight dimensions; 3,037 (23.2%) reach 0.90 | CQI | Two-tier or Core-only redesign |

### Candidate datasets: seven-criteria matrix

#### GHSL UCDB R2024A themes

Release facts: **15 thematic packages, 11,422 urban centres, 63 indicator groups, 473 indicators, 2,602 attributes**. Every V1-2 CSV has 11,422 data rows except NATURAL_SYSTEMS (11,433 rows, 11 duplicate IDs). V1-0/V1-1/V1-2 exist for all 15 themes; V1-2 is current (zips dated 2026-05-19). IDs are exactly identical to `ghsl_urban_centres`: 11,422 CSV IDs, 11,422 DB IDs, 11,422 intersection, zero either-side-only. Total download is about **432 MiB**.

| Theme | C1 Yield (centres / principal / destinations) | C2 Distribution | C3 Geometry | C4 Construct and dates | C5 Known bias | C6 Global feasibility | C7 Access | Class |
|---|---|---|---|---|---|---|---|---|
| CLIMATE | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 89/102 columns >=90% fill, minimum 10.5% | Severe match bias: EU 58.1% vs AF 75.0% principal; <100k 49.3% vs 1M+ 99.4% | Centre zonal means on 1 km SMOD polygons | 1970-2070; BIO1/BIO4/BIO7/BIO12/BIO15, Koppen, UTCI>32 days, SSP projections | LCZ only 10.5-28.0%; Koppen missing Male MV, Cadiz ES, Son Hai VN (3 = 0.026%); BIO1 vs WorldClim r=0.975, bias +0.39, MAE 1.10 | 18.4 MiB, under 1 min; destination cap is the 7,018 match | CC BY 4.0 | **Supplementary (non-LCZ near-core)** |
| EMISSIONS | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 583/583 columns >=90% fill, minimum 99.5% | Same match bias | Centre zonal statistics of EDGAR 0.01 degree | 1975-2024; CO2, GHG, PM2.5 totals and sector splits | EDGAR emissions are tonnes/year, not ambient concentration | 171.1 MiB; destination cap | EDGAR licence | **Core within UCDB; Supplementary globally** |
| EXPOSURE | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 792/1,059 columns >=90% fill, minimum 45.2% | Same match bias | Centre zonal statistics; 2025 and 2030 scenarios | 1975-2030; `EX_010_SHP_2025`, `EX_CF1_SHP_2025`, `EX_LEC_SHP_2025` | LECZ only 5,674 (58.3% of matched) | 42.4 MiB; destination cap | CC BY 4.0 | **Supplementary non-LECZ; LECZ Experimental** |
| GENERAL_CHARACTERISTICS | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 6/6 columns >=90% fill, minimum 99.9% | Same match bias | Centre polygon attributes | 1975-2030; name, population, area, density | None material | 6.6 MiB | CC BY 4.0 | **Core within UCDB; Supplementary globally** |
| GEOGRAPHY | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 28/30 columns >=90% fill, minimum 59.9% | Same match bias | Centre polygon and zonal geography | 2025; elevation, ecoregion, 24 bioclimatic variables | 59.9% minimum fill on some bioclimatic columns | 7.6 MiB | CC BY 4.0 | **Near-core within UCDB; Supplementary globally** |
| GHSL | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 544/544 columns >=90% fill, minimum 98.8% | Same match bias | Centre zonal statistics of GHS-POP and GHS-BUILT | 1975-2030; population, built-up surface, building height | None material | 76.8 MiB | CC BY 4.0 | **Core within UCDB; Supplementary globally** |
| GREENNESS | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 55/56 columns >=90% fill, minimum 69.6% | Same match bias | Centre zonal Landsat NDVI (30 m) and canopy height | 1985-2025; `GR_AVG_GRN_2025`, `GR_CTH_AVG_2020` | `GR_SQM_*`/`GR_SHB_*` about 200x unit inconsistency; 2 missing values (Mezhdurechensk RU, Doro Gowon NG); NDVI vs WorldCover tree r=0.818, canopy r=0.759-0.805 | 15.7 MiB, under 1 min | CC BY 4.0 | **Supplementary** |
| HAZARD_RISK | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 79/111 columns >=90% fill, minimum 0.1% | Same match bias; loaded 2015 subset | Centre event counts | 2000-2024 source, 2015 window loaded; heatwave, cyclone, flood, earthquake | 2015-only event count, not probability; ACLED conflict columns have separate terms | 9.2 MiB | CC BY 4.0; ACLED excluded | **Supplementary** |
| HEALTH | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 0/10 columns reach 90% fill; median 54.6%, minimum 21.0% | Same match bias; hospital 7,674 (78.9% of matched), pharmacy 4,322 (44.4%) | Centre counts and 1 km access share | 2024-2025; hospital, pharmacy and access shares | Sparse values; OSM/healthsites completeness | 7.1 MiB | Healthsites.io/OSM ODbL | **Supplementary hospital access; pharmacy Experimental** |
| INFRASTRUCTURES | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 10/10 columns >=90% fill, minimum 99.9% | Same match bias | Centre infrastructure indicators | 2020/2024; city infrastructure and road length | None material | 8.1 MiB | CC BY 4.0 | **Core within UCDB; Supplementary globally** |
| LULC | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 42/42 columns >=90% fill, 100% | Same match bias | Centre land-cover fractions at 50 m and 80 m | 2015/2019; `LU_HEC_050_2019`, `LU_HEC_080_2019` | Coarse epoch relative to WorldCover 2021 | 7.6 MiB | CC BY 4.0 | **Core within UCDB; Supplementary globally** |
| NATURAL_SYSTEMS | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 10/16 columns >=90% fill, minimum 4.4% | Same match bias | Centre protected-area indicators | 2025; `NS_ESB_BIO_2025` 100%, `NS_TPA_NAM_2025` 18.5%, `NS_MPA_NAM_2025` 4.4% | 11 duplicate rows; protected-area coverage 4.4-18.5% | 7.3 MiB | CC BY 4.0 | **Experimental** |
| SDG | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 36/36 columns >=90% fill, 100% | Same match bias | Centre zonal indicators | 1975-2030; land-use efficiency and population growth | None material | 14.7 MiB | CC BY 4.0 | **Core within UCDB; Supplementary globally** |
| SOCIOECONOMIC | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 110/176 columns >=90% fill, median 96.4%, minimum 0.0% | Same match bias | Centre country and city estimates | 1990-2023; GDP PPP, HDI, life expectancy | Country-derived for many columns; not a city price level | 31.2 MiB | CC BY 4.0 | **Supplementary** |
| WATER | 11,422 (100%) / 7,018 (67.6%) / 9,730 (74.3%); 3/21 columns >=90% fill, 11.7% | Same match bias; marine only coastal | Centre water indicators | 2018-2025; groundwater, water stress, marine | Groundwater 9,596 (98.6% of matched); marine 2,328 (23.9%) | 8.5 MiB | CC BY 4.0 | **Groundwater Supplementary; marine Experimental** |

**GHSL theme classification within the UCDB:** complete within-UCDB and >=99% fill with exact ID join: GENERAL_CHARACTERISTICS, GHSL, LULC, SDG, INFRASTRUCTURES, EMISSIONS. Near-core with minor misses: GEOGRAPHY, GREENNESS, CLIMATE (non-LCZ). Supplementary: HEALTH hospital access, EXPOSURE non-LECZ, HAZARD_RISK 2015, WATER groundwater, SOCIOECONOMIC HDI/GDP/life expectancy. Experimental: WATER marine, NATURAL protected areas, HEALTH pharmacy, CLIMATE LCZ, ACLED conflict.

#### Climate candidates

| Candidate | C1 Yield (destinations / principal / centres) | C2 Distribution | C3 Geometry | C4 Construct and dates | C5 Known bias | C6 Global feasibility | C7 Access | Class |
|---|---|---|---|---|---|---|---|---|
| NASA POWER complete | 100% potential: 13,101 / 10,389 / n.a. | None; global 0.5 degree grid | 0.5 degree daily; point at city | 2015-2024 daily, 3 parameters; monthly normals; same schema as `climate_recent` | No known geographic bias | Yes: 8,422 missing = 2.76 h serial, 1.51 GB; 6,632 principal = 2.17 h | Public domain, citation requested, no key, no set rate limit; NASA monitors usage | **Core** |
| WorldClim 2.1 | 99.98% temperature/precipitation; 0% rainy/hot fields | None material | 5 arcmin climatology; point sample | 1970-2000 monthly climatology | Non-commercial licence; baseline epoch; no wet-day or hot-day fields | Already loaded | Non-commercial | **Core for temperature/precipitation only; not a climate-score source** |
| GHSL CLIMATE R2024A | 74.3% destinations / 67.6% principal / 100% of 11,422 centres | Severe match bias: EU 58.1% vs AF 75.0%; <100k 49.3% vs 1M+ 99.4% | Centre zonal means | 1970-2070; BIO1/BIO4/BIO7/BIO12/BIO15, Koppen, UTCI>32 days, SSP projections | LCZ 10.5-28.0%; 3 missing Koppen | 18 MB, under 1 min | CC BY 4.0 | **Supplementary** |
| ERA5 daily statistics (CDS) | 100% potential | None; global 0.25 degree grid | 0.25 degree daily | 1940-present; t2m max/min, precipitation | Same ERA5 physics as NASA | About 45.5 GB per 10 years uncompressed; hours-days queue | CC-BY; versioned monthly updates | **Core candidate (strategic)** |
| Open-Meteo Historical API | 100% potential | None | ERA5 0.25 degree / ERA5-Land 0.1 degree | Historical reanalysis | None material | 132 HTTP calls at 100 locations; about 3.4M call units by the >2-week rule | Free non-commercial; $99/month Professional (5M calls) | **Core candidate (paid tier)** |
| ERA5-HEAT direct | 100% potential | None | Hourly UTCI 0.25 degree | 1940-present; hourly UTCI | Hourly volume is excessive for city statistics | Hourly about 36 GB/year; yearly stats about 4 MB/year/grid | Copernicus licence | **Supplementary; avoid hourly** |

#### Greenery and land-cover candidates

| Candidate | C1 Yield (destinations / principal / centres) | C2 Distribution | C3 Geometry | C4 Construct and dates | C5 Known bias | C6 Global feasibility | C7 Access | Class |
|---|---|---|---|---|---|---|---|---|
| WorldCover 10 m polygon (7,018) plus 10 m disc (3,371) | 100% principal (7,018 polygon plus 3,371 disc); 13,101 by inheritance or disc | No source bias; method differs for the 3,371 unmatched | 10 m, 11 classes, 2021; citywide polygon vs fixed disc | WorldCover 2021 | Point-vs-polygon r=0.333; the polygon method is the defensible citywide measure; the 3,371 disc must be labelled | Yes: 7,018 polygons 64-106 min plus 49 min discs; 20-40 min with 4-6 workers; about 0.5 GB; under 1 GB RAM | CC BY 4.0; 1,119 distinct 3 degree tiles; only city windows read | **Core** |
| WorldCover 1 km/15 km discs for all | 100% principal | None | Point sample, 2021 | WorldCover 2021 | Fails construct validity: r=0.333 with citywide tree share | 1.5 h | CC BY 4.0 | **Supplementary (city-centre context only)** |
| GHSL UCDB GREENNESS R2024A | 74.3% destinations / 67.6% principal / 100% of 11,422 centres | Severe match bias: EU 58.1%, SA 57.4%, OC 53.0% vs AF 75.0%; <100k 49.3% | Centre zonal Landsat NDVI 30 m plus canopy height | 1985-2025 | `GR_SQM_*`/`GR_SHB_*` about 200x unit inconsistency; use NDVI and canopy only; NDVI vs WorldCover tree r=0.818 | 16 MB, under 1 min | CC BY 4.0 | **Supplementary (standalone Experimental)** |
| Copernicus CGLS-LC100 | 100% potential | None | 100 m annual, 21 classes | 2015-2019 | Older (2019) and coarser (100 m) than WorldCover | About 2.4 GB per epoch, about 12 GB for five epochs | Copernicus free/open, attribution | **Supplementary fallback** |
| GHSL built-up surface | 100% of centres | None | Built-up surface only | Multiple epochs | No vegetation information | Small | CC BY 4.0 | **Not a greenery candidate** |

#### Mobility and walkability candidates

| Candidate | C1 Yield (destinations / principal / centres) | C2 Distribution | C3 Geometry | C4 Construct and dates | C5 Known bias | C6 Global feasibility | C7 Access | Class |
|---|---|---|---|---|---|---|---|---|
| `street_network_metrics` (Boeing) | 8,444 (64.5%) / 6,747 (64.94%) / n.a. | Not fully audited by bracket | Street-network metrics | Boeing Global Urban Street Networks | Intersection density correlates **-0.181** with How Walkable; Houston outranks Berlin; docs call it urban-form context | Global source, invalid construct for walkability | Loaded | **Rejected as walkability; context only** |
| GHSL density/compactness | 9,730 destinations / 7,018 principal / 11,422 centres | Severe match bias | Centre density metrics | GHSL UCDB | Density is not walkability; no >=90% destination mapping | Source complete; match-limited | CC BY 4.0 | **Rejected as walkability; Supplementary context** |
| OSM/Overture pedestrian network | 1,131 cities (8.6%) / 831 principal (8.0%) / n.a. | Only DE, GB, TR, ES, FR, PT, SE and LU | Pedestrian network metrics | Overture Transportation and OSM | Only 91 of 662 walkability cities; Pearson r=0.445 | Global scan incomplete; staged by country | Loaded for 8 countries | **Rejected as global walkability** |
| GHSCI | 25 destinations | 25-city study plus Trento pilot | City z-scores | GHSCI | Tiny sample; z-score not 0-100 | No | Loaded | **Validation only** |
| Overture amenity density | 100% nominal | Severe regional bias | 5 km POI density | Overture Places | r=0.768 with walkability but not independent: How Walkable itself uses Overture/OSM amenity counts | Rows global; construct not independent | Overture CDLA-Permissive 2.0 | **Rejected as independent walkability** |
| Overture transit stops | 9,008 (68.76%) / 6,778 (65.24%) / n.a. | AF 30.4% vs EU 96.7% all-city; principal AF 25.9% vs EU 92.9%; GDP-PPP Q1 28.6% vs Q4 91.5% | 5 km point counts | Overture Places transit facilities | Counts stations/facilities, not bus stops; 330 cities with GTFS but zero Overture stops; 4,093 destinations lack stops; 527 intercity-only cities still have Overture stops | Union with GTFS is 72.9%; no global frequency | Loaded | **Experimental; context only** |
| GTFS feeds | 4,000 (30.53%) / 2,690 (25.89%) / 516: 136 (26.36%) | AF 3.5% all-city; EU 68.9%; 516-core AF 5.6%, EU 76.1%, NA 68.0%, AS 17.2% | Feed bbox | Mobility Database and GTFS | FlixBus/BlaBlaCar bbox inflation; 546 intercity-only, 493 flagged | Cannot reach 90%; bbox is not local service | Loaded; CC0 metadata | **Experimental** |
| GTFS headway | 544 (4.15%) / 497 (4.78%) / 516: 33 (6.4%) | Only 33 of 516 >=1M centres | Stop-level headway and span | GTFS weekday peak, midday and Sunday | Unusable as a global measure | No global frequency source | Loaded | **Experimental** |

#### Safety, affordability, connectivity and culture candidates

| Candidate | C1 Yield (destinations / principal / centres) | C2 Distribution | C3 Geometry | C4 Construct and dates | C5 Known bias | C6 Global feasibility | C7 Access | Class |
|---|---|---|---|---|---|---|---|---|
| WHO GHO / UNODC homicide | Country-level; fills most of the 874 missing destinations, including Vietnam 179, DRC 101, Thailand 88 | Modelled estimates; uncertainty intervals | Country | Modelled country series | Differs from the existing UNODC reported series; no city-level variation | Country fill only | Not assessed in Wave 1 | **Core-conditional fill** |
| UN WPP life expectancy | Country-level; can fill Taiwan and others | Country | Country | UN WPP | Country-level | Country fill only | Not assessed | **Core-conditional fill** |
| WHO GHED out-of-pocket | Country-level; fills North Korea, Taiwan, Hong Kong, Kosovo and others | Country | Country | WHO GHED | Country-level | Country fill only | Not assessed | **Core-conditional fill** |
| WHO NHWA physicians | Country-level; fills Taiwan, Hong Kong, Kosovo | Country | Country | WHO NHWA | Country-level | Country fill only | Not assessed | **Core-conditional fill** |
| IMF WEO price level | Country-level; fills Venezuela; Taiwan still needs national data | Country | Country | IMF WEO | Country-level; Cuba and North Korea should stay null | Country fill only | Not assessed | **Core-conditional fill** |
| Taiwan national statistics (MOI/DGBAS) | 39 destinations | Only route for Taiwan | Country/national | National statistics | Not comparable to World Bank methodology without reconciliation | Not a global source | Not assessed | **Required for Taiwan** |
| M-Lab NDT | 100% potential | Global | Point/tile measurements | NDT broadband measurements | Cross-check, not identical to Ookla methodology | Global | CC0 | **Supplementary connectivity candidate** |
| ITU | Country-level only | Country | Country | ITU country statistics | Not city-level | Country fallback only | Not assessed | **Supplementary country fallback** |
| UNESCO World Heritage proximity | About 1,223 sites; global proximity potential | Global | Point/polygon proximity | Official World Heritage list | Culture only; food remains non-global; proximity is not a city quality measure by itself | Spatial join is trivial; construct is partial | Official list; licence not assessed | **Supplementary culture candidate** |
| GHSL SOCIOECONOMIC GDP/HDI | 74.3% destinations / 67.6% principal / 100% of 11,422 centres; 96.4% median fill | Severe match bias | Centre estimates | 1990-2023 | Country-derived for many columns; not a price level | Source complete; match-limited | CC BY 4.0 | **Supplementary context** |

#### Global spatial-processing feasibility (C6)

| Job | Measured benchmark | Extrapolated full cost | Transfer and memory | Verdict |
|---|---|---|---|---|
| WorldCover 10 m polygon fractions | 20-polygon sample: 10.9 s total, 0.55 s/city, 0.51 s median, 11.6 ms/km2, 1.3 Mpx/s; 5-polygon sample extrapolated to 71 min | 7,018 polygons: **64-106 min** single-threaded; 546,681 km2, 5.47e9 native 10 m pixels; 1,119 distinct 3 degree tiles, maximum 78 polygons per tile | One 3 degree tile is 94.85 MB for 1.05e9 pixels (0.09 bytes/pixel); full city windows **about 0.5 GB**; **under 1 GB RAM** | **PASS** |
| WorldCover 10 m disc for unmatched cities | `sample_city` at 1 km (200x200) plus 15 km (150x150) = 0.87 s/city | 3,371 unmatched: **49 min**; all 5,389 remaining: **78 min** | Same S3 access pattern | **PASS** |
| WorldCover combined plan | 7,018 polygons plus 3,371 discs | **2-2.5 h serial**; **20-40 min with 4-6 workers** | About 0.5 GB compressed | **PASS** |
| NASA POWER fill | One 2015-2024 daily request for 3 parameters: **1.029 s, 179,198 bytes** | 8,422 missing: **2.76 h serial, 1.51 GB**; 6,632 principal: **2.17 h serial** | HTTP JSON; no key | **PASS** |
| Air/water formula lift | Direct recompute on 13,101 destinations; overlap check against 5,000 rows gives **MAE 0.0000** and maximum absolute difference 0.000000 | No download; one view/query change | None | **PASS** |
| GHSL UCDB themes | 432 MiB total; 16-18 MiB for CLIMATE/GREENNESS; 171 MiB for EMISSIONS; exact ID join | Under 1 min for small themes | 432 MiB | **PARTIAL: source complete, destination cap 67.6%** |
| ERA5 daily statistics | 0.25 degree, 1940-present | About **45.5 GB per 10 years** uncompressed; hours-days queue | Not downloaded | **PARTIAL: strategic, not immediate** |
| CGLS-LC100 | 100 m annual, 2015-2019 | About **2.4 GB per epoch**, about **12 GB for five epochs** | Not downloaded | **PASS but older/coarser** |
| City-level crime | No global city source identified | Not measurable | n.a. | **FAIL** |
| City-level rent | Only regional Eurostat/ONS/HUD layers held; no global official city rent source | Not measurable | n.a. | **FAIL** |
| Global walkability | No candidate reaches 90% with a defensible construct | Not measurable | n.a. | **FAIL** |
| Global transit frequency | Headway 544 cities (4.15%); no global stop-level/frequency source | Not measurable | n.a. | **FAIL** |
| Recent global hazard probability | Only 2015 event counts; no global city probability or forecast | Not measurable | n.a. | **FAIL for current probability** |

#### Download, licensing and compute requirements (C7)

| Source / dataset | Access | Licence | Compute / transfer | Notes |
|---|---|---|---|---|
| SatPM air quality | Loaded | Not reassessed in Wave 1 | None | Annual 2024, 0.1 degree |
| OurAirports | Loaded | Not reassessed in Wave 1 | None | Nearest scheduled airport |
| WorldClim 2.1 | Loaded | **Non-commercial** | None | Hard constraint for a commercial product |
| NASA POWER | Public API, no key | Public domain, citation requested | 1.51 GB for the 8,422-city fill | No set rate limit; NASA monitors usage |
| ESA WorldCover 10 m | Public S3 | CC BY 4.0 | About 0.5 GB city windows; 2-2.5 h serial | Only city windows read; 1,119 tiles |
| GHSL UCDB R2024A | JRC CID portal | CC BY 4.0; ACLED conflict separate; HEALTH ODbL; EMISSIONS EDGAR | 432 MiB total; under 1 min for small themes | V1-2 current; exact IDs |
| ERA5 daily statistics | Copernicus CDS | CC-BY | About 45.5 GB per 10 years | Hours-days queue |
| Open-Meteo | Public API | Free non-commercial; $99/month Professional (5M calls) | 132 calls at 100 locations; about 3.4M call units by the >2-week rule | Paid tier required for commercial |
| ERA5-HEAT | Copernicus CDS | Copernicus licence | Hourly about 36 GB/year; yearly stats about 4 MB/year/grid | Avoid hourly |
| CGLS-LC100 | Copernicus land service | Free/open, attribution | About 2.4 GB per epoch | 100 m, 2015-2019 |
| Overture Places | Public S3 | CDLA-Permissive 2.0 | Loaded | Zero semantics need fixing |
| Mobility Database | Public | CC0 metadata | Loaded | Feed bbox inflation |
| Ookla | Loaded | CC BY-NC-SA 4.0 | None | 2026 Q2 fixed tiles |
| How Walkable | Loaded | CC BY 4.0 | None | 690 ranked cities |
| World Bank indicators | Loaded | Not reassessed in Wave 1 | None | Country-level |
| M-Lab NDT | Public | CC0 | Not measured | Connectivity cross-check |
| UNESCO World Heritage | Public list | Not assessed | Proximity join is trivial | About 1,223 sites |
| ACLED conflict | GHSL package | Separate terms | Do not load | Excluded intentionally |

## Indicator Classification

Classification rules: **Core** requires >=90% coverage and no severe regional bias; **Supplementary** is 70-90%, or 100% of GHSL centres with a destination-reach limitation; **Experimental** is below 70% or has a severe regional bias and is published separately. Rows marked **row coverage only** are context counts, not scored Core dimensions. Water and river distance metrics are Core; their thresholded access interpretations are Supplementary. The raw mapped-transit-facility count may be displayed as Supplementary context, but it is not a scored indicator and its 68.76% reach keeps it out of the Supplementary tier in this table.

### Core

| Indicator / dataset | Coverage | Basis | Notes |
|---|---:|---|---|
| `air_quality_annual` | 100% / 100% | Global 0.1 degree grid, 2024 | 0 NULL; range 2.549-130.871 ug/m3 |
| `airport_access` | 100% / 100% | Nearest scheduled airport | Context indicator; 1.18% no IATA, 13.0% >100 km |
| `destination_climate_class` | 100% / 100% | Koppen-Geiger 1991-2020 | 0.56% NULL |
| `destination_geography` | 100% / 100% | Point elevation plus coastline | 0.13% elevation NULL |
| `destination_inland_water` | 100% / 100% | Nearest lake shore | Core as distance; access <=25 km is Supplementary (82.5%) |
| `destination_river_access` | 100% / 100% | Nearest Strahler >=4 reach | Core as distance; access <=25 km is Supplementary (87.7%) |
| `destination_terrain` | 100% / 100% | 10 km terrain statistics | Context indicator |
| `climate_monthly` temperature/precipitation | 99.98% / 99.98% | WorldClim 2.1, 1970-2000 | Not a climate score; rainy/hot fields are 0% |
| `country_indicators` (11 of 13) | 98.0-99.6% | World Bank country series | Country-level; water and sanitation are Supplementary |
| `safety_health` composite | 99.61% / 99.66% | Country-level proxy | Conditional on homicide fill; label country-level |
| `affordability` composite | 99.14% / 99.11% | Country-level proxy | Fill Venezuela and Taiwan; Cuba/North Korea stay null |
| `facility_metrics` rows | 100% / 100% | Overture 5 km counts | Row coverage only; comparative metric is Experimental |
| `poi_metrics` rows | 100% / 100% | Overture 5 km counts | Row coverage only; comparative metric is Experimental |
| Air score after formula lift | 100% / 100% projected | Destination-driven formula | Validated MAE 0.0000 on 5,000 overlaps |
| Water score after formula lift | 100% / 100% projected | Destination-driven formula | 10 zero scores where nearest water is >50 km |
| NASA POWER climate complete | 100% potential | Global 0.5 degree daily, 2015-2024 | Data-ready; current formula still construct-blocked |
| WorldCover 10 m greenery | 100% principal projected | Polygon plus disc, 2021 | Method flag required for the 3,371 disc cities |
| ERA5 daily statistics | 100% potential | Global 0.25 degree daily | Strategic alternative to NASA POWER |

### Supplementary

| Indicator / dataset | Coverage | Basis | Notes |
|---|---:|---|---|
| `urban_centre_hazard` | 100% centres / 67.6% principal / 74.3% destinations | GHSL centre event counts | 2015 window only |
| Hospital access share | 100% centre rows / 56.3% values / 67.6% principal | GHSL HEALTH | Metro-only; globally consistent method |
| `broadband_metrics` | 90.85% / 89.05% | Ookla 2026 Q2 | 84.20% principal with `total_tests>=10`; borderline Core |
| Safely managed water | 78.8% destination-weighted | World Bank | Large China/Turkey/Argentina gaps |
| Safely managed sanitation | 88.7% destination-weighted | World Bank | Large Indonesia/Pakistan/Iran gaps |
| GHSL core-within-UCDB themes | 100% centres / 67.6% principal | GENERAL_CHARACTERISTICS, GHSL, LULC, SDG, INFRASTRUCTURES, EMISSIONS | Excellent within UCDB; match-limited globally |
| GHSL GEOGRAPHY, GREENNESS, CLIMATE non-LCZ | 100% centres / 67.6% principal | Near-core within UCDB | LCZ and unit caveats |
| GHSL SOCIOECONOMIC | 96.4% median fill / 67.6% principal | GDP, HDI, life expectancy | City context, not price |
| GHSL WATER groundwater | 98.6% of matched / 67.6% principal | Groundwater indicators | Marine is Experimental |
| GHSL EXPOSURE non-LECZ | 100% centres / 67.6% principal | Exposure indicators | LECZ only 58.3% of matched |
| CGLS-LC100 | 100% potential | 100 m, 2015-2019 | Older, coarser fallback |
| WorldCover 1 km/15 km discs | 100% principal | Point sample, 2021 | Construct-limited; city-centre context |
| M-Lab NDT | 100% potential | Global NDT | Connectivity cross-check |
| UNESCO World Heritage proximity | About 1,223 sites | Official list | Culture-only candidate; food remains non-global |
| GHSL built-up surface | 100% centres | GHSL | Built-up context, not greenery |

### Experimental

| Indicator / dataset | Coverage | Reason | Publication rule |
|---|---:|---|---|
| `walkability_scores` | 5.05% / 5.49% | Structural exclusion; 0% below 300k; Africa 0.7% | Separate, no headline ranking |
| `climate_recent` | 35.7% / 36.2% | Small-city gap until NASA POWER fill | Separate until 100% and formula redesigned |
| `destination_land_cover` | 38.2% / 38.6% | Top-5,000 cutoff; point-disc construct | Separate; label as point sample |
| `destination_environment_score` | 38.2% / 38.6% | Top-5,000 cutoff; mixed source | Separate |
| `destination_ghsl_greenery` | 3.9% / 5.0% | >=1M filter | Separate until expanded |
| `destination_urban_land_cover` | 3.9% / 5.0% | >=1M filter | Separate until expanded |
| `mobility_metrics` | 72.9% / 69.4% | Severe regional bias: AF 30.4% vs EU 96.7% | Separate; zero/missing flag |
| Overture transit stops | 68.76% / 65.24% | Below 70%; station/facility counts | Context only; no score |
| GTFS feeds | 30.53% / 25.89% | Bbox inflation; intercity-only feeds | Context only; `intercity_only` flag |
| GTFS headway | 4.15% / 4.78% | 544 cities; 33/516 | Not published as a global measure |
| Culture/food score | 100% nominal | Chinese 80.3% and African 42.5% restaurant zeros; top-decile NA 26.5% vs AF 0.6% | Separate; no headline ranking |
| POI/facility comparative scores | 100% nominal | Zero-vs-missing conflation | Separate; `none_listed` flag |
| Pharmacy access | 21.0-44.4% | Sparse values | Experimental |
| `city_quality_index` | 516 centres | >=1M scope only | Separate context |
| `city_quality_index_all` | 23.2% / 22.3% complete | Median completeness 0.60; renormalization | Separate; no headline ranking |
| GHSL WATER marine | 23.9% of matched | Coastal only | Experimental |
| GHSL NATURAL protected | 4.4-18.5% | Sparse; duplicate rows | Experimental |
| GHSL CLIMATE LCZ | 10.5-28.0% | Sparse | Experimental |
| ACLED conflict | n.a. | Separate licence terms | Do not load |
| WorldClim-only climate score | 99.98% temperature/precipitation | MAE 7.7 and 49/100 top-100 overlap vs NASA | Not equivalent to measured climate |
| `street_network_metrics` | 64.5% / 64.94% | r=-0.181 with walkability | Rejected as walkability |
| Overture amenity density | 100% nominal | r=0.768 but not independent | Rejected as walkability |
| GHSCI | 25 destinations | Tiny sample; z-scores | Validation only |

### Composite CQI status

`city_quality_index` is a 516-centre index (445 with completeness >=0.90). `city_quality_index_all` has 13,101 rows and a non-null `quality_score` for every row, but only **3,037 (23.2%)** reach completeness >=0.90 and only **625 (4.8%)** have all eight dimensions; the median completeness is **0.600**. Its dimension coverage is: physical green/air/water **3.9%**, climate **35.7%**, park **73.6%**, connectivity **90.8%**, affordability **99.1%**, safety/health **99.6%**, culture/food **100%**. It is not a global index; it is a data-rich-city index with explicit coverage flags.

## Rejected Proxies and Methodological Gaps Register

### Rejected proxies

| Proxy | Claimed use | Evidence against | Verdict |
|---|---|---|---|
| Street-network intersection density | Walkability | Pearson **-0.181** with How Walkable; Houston outranks Berlin; `bootstrap_street_network.py` explicitly calls it urban-form context | **Rejected** |
| 1 km WorldCover point disc | Citywide canopy/greenery | Pearson **0.333** tree and 0.331 green vs polygon; bias -0.106; MAE 0.142; extremes +0.723 and -0.713 | **Rejected as citywide; retained as city-centre sample** |
| GHSL GREENNESS `GR_SQM_*` / `GR_SHB_*` | Absolute green area | Implied built-up area is 0.19% of centre area (median), about **200x** below plausible; unit ambiguity | **Rejected columns; use NDVI and canopy height only** |
| NASA POWER/WorldClim rain-free formula | Climate quality / thermal comfort | Atacama tops the ranking (Alto Hospicio 99.9, Iquique 99.8, Calama 99.7); correlation with UTCI >32 days **-0.273**; top-100 overlap with heat-only 7/100; mean score 59.3 | **Rejected as a comfort metric; redesign required** |
| GHSL density/compactness | Walkability | Density is not walkability; no >=90% destination mapping | **Rejected** |
| OSM/Overture pedestrian network | Global walkability | 1,131 cities in 8 countries; 91 of 662 walkability cities; Pearson 0.445 | **Rejected as global** |
| Overture amenity density | Independent walkability | r=0.768 but How Walkable itself uses Overture/OSM amenity counts; inherits the same POI bias | **Rejected as independent evidence** |
| GHSCI 25-city study | Global index | 25 destinations; z-score not 0-100 | **Validation only** |
| Overture transit stops as complete stop inventory | Transit coverage/frequency | Counts stations/facilities, not bus stops; 330 cities with GTFS but zero Overture stops; 4,093 destinations lack stops | **Rejected as frequency; context only** |
| GTFS feed bbox | Local transit service | 546 cities have only FlixBus/BlaBlaCar; 493 carry `gtfs_active_feeds>0`; FlixBus France alone covers 693 cities | **Rejected as local service; intercity flag required** |
| GTFS headway | Global transit frequency | 544 cities (4.15%); 33 of 516 >=1M centres | **Rejected as global** |
| Overture POI zeros | True zero venues | China 1,144 restaurant zeros (80.3%); Africa 42.5%; `COALESCE(...,0)` in the loader | **Rejected; use NULL plus `none_listed`** |
| Country-level safety/affordability | City-level measures | 155 homicide, 137 road, 185 life, 171 physician, 179 price and 175 OOP values for 13,101 cities; top-decile distortions | **Rejected as city-level; accepted as labelled country proxy** |
| EDGAR emissions | Ambient PM2.5 | Tonnes/year emissions, not concentration | **Rejected as air-quality replacement** |
| GHSL WATER marine | Global water access | 2,328 of 9,730 matched (23.9%); coastal only | **Rejected as global** |
| GHSL NATURAL protected areas | Global nature access | 4.4-18.5% fill; 11 duplicate rows | **Rejected as global** |
| GHSL CLIMATE LCZ | Global climate | 10.5-28.0% fill | **Rejected as global** |
| GHSL HEALTH pharmacy | Global health access | 21.0-44.4% fill | **Rejected as global; hospital access is Supplementary** |
| WorldClim as commercial climate source | Commercial climate score | Non-commercial licence; 0% rainy/hot fields; 1970-2000 baseline | **Rejected for commercial use; temperature/precipitation only** |
| GHSL built-up surface | Greenery | No vegetation | **Not a candidate** |

### Remaining gaps with no defensible global city-level source

| Gap | Why it cannot be Core | Current best available | Decision |
|---|---|---|---|
| City-level crime | No global city-level crime rate; country homicide is 93.3% covered but 23.2% missing in Africa; travel advisories are country-level | Country homicide, road deaths and travel advisories | Keep country-level safety proxy; do not claim city crime |
| City-level rent | No global official city rent series; existing rent layers are regional (Eurostat/ONS/HUD) | Regional rent tables | Keep out of global Core; label as regional |
| Global walkability | No candidate reaches 90% with a defensible construct | How Walkable 662 cities; street density rejected | Experimental context only |
| Global transit frequency | No global stop-level/frequency source; headway 4.15% | Overture stops 68.76%; GTFS 30.53%; headway 544 cities | Experimental context only |
| City-level culture/food | Overture counts are coverage-biased; China 80.3% zeros; Africa 42.5% | Overture counts with zero screen; UNESCO culture proximity candidate | Experimental; official culture register only |
| Recent global hazard probability | Only 2015 event counts; no probability or forecast | GHSL 2015 event counts | Supplementary historical context |
| City-level health facility completeness | Hospital 54.6%, pharmacy 21.0% at centres; Overture counts biased | GHSL HEALTH and Overture facility counts | Supplementary hospital access; pharmacy Experimental |
| City-level cost of living | Country price level only; GHSL GDP is income context, not prices | World Bank price level / OOP | Country proxy only |
| 3,371 unmatched principal cities | No GHSL centre; UCDB themes cannot reach them | Direct global sources; 10 m discs | Direct-source Core; no nearest-centre imputation |
| Taiwan | World Bank/WHO do not report Taiwan; 0/39 complete | National statistics (MOI/DGBAS) | Fill or flag; add renormalization guard |
| China POI coverage | 80.3% restaurant zeros; 44.4% of all zeros | None verified | Do not score China culture/food |
| Road-death freshness | 183/190 values are 2019 and all are pre-2020 | World Bank series | Label as pre-2020; seek newer source |
| WorldClim commercial licence | Non-commercial | NASA POWER or ERA5 | Replace for commercial deployment |

### Documentation drift to reconcile

- `docs/QUALITY_INDEX.md` states GTFS headway exists for **6** of 516 centres; the current database has **33**. The limitation is directionally correct but numerically stale.
- `docs/COVERAGE_REPORT.md` states **2,316** cities with completeness >=0.90; that is the principal-city count. The all-city count is **3,037**. Both should be stated with their denominators.
- Documentation implies green scores for 5,000 land-cover rows; the actual `destination_environment_score` has **4,038** scored values because 960 park zeros are treated as NULL. Reconcile before publishing.

## Redesign Architecture and Expected Coverage Impact

The evidence supports a three-tier product rather than a single composite. The tiers differ by **what is measured**, **what is imputed**, and **what is published as a headline rank**.

### Tier 1: Core headline index

**Eligibility and dimensions.** A Core index should include only dimensions that are at least ~99% covered and free of severe regional bias. The defensible set after the measured fixes is:

| Core dimension | Inputs | Coverage after fix | Construct status |
|---|---|---:|---|
| Environment | WorldCover 10 m greenery 50%, air 31.25%, water 18.75% | 100% principal projected | Valid after WorldCover scaling; parks excluded from the gate because park zeros are 26.4% overall and 65.1% in Africa |
| Climate | NASA POWER 2015-2024 monthly normals | 100% projected | **Blocked**: current formula ranks Atacama first and correlates -0.273 with UTCI; replace with seasonality/UTCI before promotion |
| Safety and health | Country homicide, road deaths, life expectancy, physicians | 99.66% principal | Country-level proxy; fill homicide from WHO/UNODC; Taiwan national data |
| Affordability | Country price level and out-of-pocket | 99.11% principal | Country-level proxy; fill Venezuela and Taiwan; Cuba/North Korea stay null |

The environment weights above are the existing 40/25/15 greenery/air/water weights renormalized after removing the 20-point park component (40/80 = 50%, 25/80 = 31.25%, 15/80 = 18.75%). The Core composite weights should be confirmed by the lead; using the existing environment 25, safety 15, affordability 10 and climate 5, the four Core dimensions renormalize to **environment 45.5%, safety 27.3%, affordability 18.2%, climate 9.1%** (55 total). If connectivity is added, the five-dimension set renormalizes to **environment 41.7%, safety 25.0%, affordability 16.7%, climate 8.3%, connectivity 8.3%** (60 total).

**Coverage expectation.** Each Core dimension individually covers at least 99.1% of principal cities after the fixes. A composite requiring all four cannot exceed the least-covered dimension's **99.11%**, and a union bound puts the four-set intersection at no less than **98.77%** (safety 0.34% missing plus affordability 0.89% missing). The actual intersection must be measured after the ETL and scoring run; this is a design target, not a current CQI value. The important structural change is that the Core index is no longer 0% complete below 100k, because NASA POWER and WorldCover cover small cities directly.

**Guardrails.**
- Do not renormalize below a minimum number of present dimensions. The Taiwan case (8 cities with completeness <0.50 scoring >=80, maximum Yingge 89.1) is the canonical failure.
- Publish `data_completeness`, `missing_dimensions` and a `country_proxy` flag on every row.
- Keep parks, walkability, culture/food and transit out of the Core gate; connectivity is optional only if the `total_tests>=10` policy is accepted.
- Label country-level safety and affordability explicitly; do not present them as city measurements.

### Tier 2: Supplementary GHSL layer

- **Scope:** 11,422 UCDB R2024A centres; **7,018 principal cities (67.6%)** and **9,730 destinations (74.3%)** matched; 4,404 centres have no destination.
- **Content:** the 15 themes (432 MiB total), with the within-UCDB Core set (GENERAL_CHARACTERISTICS, GHSL, LULC, SDG, INFRASTRUCTURES, EMISSIONS), near-core GEOGRAPHY/GREENNESS/CLIMATE non-LCZ, and Supplementary HEALTH hospital access, EXPOSURE non-LECZ, HAZARD 2015, WATER groundwater, SOCIOECONOMIC HDI/GDP/life expectancy.
- **Rules:** expose a `match_kind` of `contained`, `nearby` or `unmatched`; never impute a centre for the 3,371 unmatched cities; never let a centre-level indicator count toward Core completeness; dedupe NATURAL by ID; skip ACLED; convert loader dashes to NULL, not zero.
- **Value:** this is the best available centre-level context and validation layer. It is not a global city index, because its universe is 50k+ centres and its destination match is 67.6% of principal cities.

### Tier 3: Experimental layer

- **Content:** walkability, culture/food, public transport/mobility, point-disc land cover, `climate_recent` until filled, `destination_ghsl_greenery` and `destination_urban_land_cover` until expanded, `city_quality_index` and `city_quality_index_all`, GHSCI, and all rejected proxies retained only as context.
- **Publication:** separate tables and CSVs, no headline rank, explicit `experimental` label and coverage/method flags.
- **Semantics:** replace POI/facility zeros with NULL plus `none_listed`; add `intercity_only` to GTFS; label Overture stops as "mapped transit facilities", not bus stops or frequency; label hazard as 2015 event counts; label country-level series.
- **Screening:** for culture/food, a <=10% zero screen retains only **8,461 of 13,101 destinations (64.6%)**; a <=20% screen retains 73.4%. No screen reaches 90%.

### Expected coverage impact

| Change | Before | After (projected) | Evidence |
|---|---:|---:|---|
| Air score | 511 / 13,101 (3.9%) | 13,101 / 13,101 (100%) | Formula lift; MAE 0.0000 on 5,000 overlaps |
| Water score | 511 / 13,101 (3.9%) | 13,101 / 13,101 (100%) | Formula lift; 10 zero scores >50 km from water |
| Environment score | 4,076 / 13,101 (31.1%) | 13,101 / 13,101 (100%) with WorldCover 10 m; 9,870 (75.3%) with UCDB GREENNESS union | WorldCover polygon plus disc; UCDB union alternative |
| Climate score | 4,679 / 13,101 (35.7%) | 13,101 / 13,101 (100%) | NASA POWER fill 2.76 h / 1.51 GB; construct still needs redesign |
| CQI completeness >=0.90, principal | 2,316 (22.3%) | 2,520 (24.3%) add climate; 2,672 (25.7%) add environment; **6,325 (60.9%)** add both | Current eight-dimension CQI with existing weights |
| Core-only composite, principal | Not available | 98.77-99.11% union-bound; verify intersection | Four Core dimensions at >=99.1% each; actual intersection after ETL |
| GHSL Supplementary layer | 7,018 principal / 9,730 destinations / 11,422 centres | Same; 100% of centres for core themes | Match cap is structural |
| Walkability | 570 principal (5.49%) | No change | No defensible global source |
| Culture/food | 13,101 nominal | 8,461 (64.6%) after <=10% zero screen | Overture bias; Experimental |
| Transit | 6,778 stops (65.24%); 2,690 GTFS (25.89%); 497 headway (4.78%) | No change | Experimental |

The **CQI completeness** row is the most important planning number: adding environment (25% weight) and climate (5%) lifts the principal set from **22.3% to 60.9%** complete without touching walkability or transport. The remaining gap is the 40% of weight held by culture/food, walkability and public transport, which cannot be fixed with the current sources. A Core-only index avoids that gap by design.

### Implementation order

| Order | Change | Expected gain | Measured cost | Owner decision |
|---:|---|---|---|---|
| 1 | Lift air/water formulas to a destination-driven view | Two components 3.9% -> 100% | Code only; no download | None |
| 2 | NASA POWER fill for 8,422 missing destinations | Climate 35.7% -> 100% | 2.76 h serial, 1.51 GB | Confirm climate construct before headline |
| 3 | WorldCover 10 m polygons plus discs | Greenery 516 -> 10,389 principal | 2-2.5 h serial, ~0.5 GB, 20-40 min parallel | Approve 10 m disc fallback for 3,371 |
| 4 | Homicide and Taiwan fills | Safety 99.66% and affordability 99.11% toward 100% | Source work | WHO/UNODC modelled series vs reported |
| 5 | Climate formula redesign | Construct validity | Product decision | Mild-and-dry vs UTCI vs seasonality |
| 6 | GHSL theme load | Supplementary layer 100% of centres | 432 MiB; under 1 min for small themes | Dedupe NATURAL; skip ACLED; dash->NULL |
| 7 | Zero/missing semantics fix | Removes culture/facility bias | Code only | NULL plus `none_listed` |
| 8 | Documentation reconciliation | Trust | Docs only | Headway 6 vs 33; completeness 2,316 vs 3,037 |

## Provenance and Reproducibility

- **Base commit:** `f01f62c8b6e0f8f871087741e906f2c825505bb7`.
- **Primary evidence:** Wave 1 audit synthesis at `/tmp/w1_full_findings.md`, containing four read-only investigations (current DB coverage, GHSL UCDB themes, climate/greenery, mobility/walkability/culture/safety/affordability/connectivity).
- **SQL:** `/tmp/cqi/flags.sql`, `/tmp/cqi/audit2.sql`, `/tmp/cqi/audit3.sql`, `/tmp/cqi/audit4.sql`, `/tmp/cqi/audit5.sql` (outside the repository).
- **Benchmarks:** `/tmp/cqi_audit/landcover_bench.py` (5 polygons; 71 min extrapolation) and `/tmp/cqi_audit/landcover_bench2.py` (20 polygons; 10.9 s, 941 km2, 13.7 Mpx, 0.55 s/city, 11.6 ms/km2, 1.3 Mpx/s); NASA POWER request returned HTTP 200, 179,198 bytes in 1.029 s.
- **Code references:** `bootstrap_quality_index_all.py:92-95,116,145-154,205-213,238-299`; `bootstrap_quality_index.py:141-149,201-209`; `bootstrap_ghsl_scores.py:39,68,74-75`; `bootstrap_urban_land_cover.py:165-188,233-277,280-310`; `bootstrap_green_score.py:119,140,149-168`; `bootstrap_land_cover.py:241-280,283-310,319-327`; `bootstrap_climate_recent.py:39,64-132,150-163,218-220`; `bootstrap_metro.py:51,216-296`; `bootstrap_ucdb_themes.py:47-58,175-255`; `bootstrap_global_40k.py:1000-1002`; `bootstrap_walkability.py:5-21,52-55`; `bootstrap_transit_stops.py:6-13,129-132,148-170,216`; `bootstrap_street_network.py:3-13`; `reports/generate_quality_rankings_all.py:14,16-31,96,103`.
- **Checks:** all SQL was `SELECT`/CTE only; no `INSERT`, `UPDATE`, `DELETE` or DDL; no database writes; all temporary files outside the repository. Row counts were re-verified: destinations 13,101; `city_quality_index_all` 13,101; `city_quality_index` 516; walkability 662; transit stops 9,008; GTFS 4,000; headway 544; broadband 11,902; hazard 11,422; health 11,422; urban centres 11,422; metro 9,730 with 7,018 centres; street network 8,444; GHSCI 25.
- **Rounding:** percentages are rounded to one decimal in narrative tables and two decimals where the source reports two. Counts are exact.
- **Known limitation:** this document is a synthesis; it does not re-run the geospatial benchmarks. The measured runtimes and byte counts are those recorded in the Wave 1 evidence, not new measurements.

## Open Questions for the Lead

1. **Authoritative denominator:** should Core targets be measured on all 13,101 destinations, the 10,389 principal cities, or the 100k+ subset? The environment and climate layers look very different under each (38.2% vs 91.3% of 100k+ for land cover; 35.7% vs 83.0% for climate_recent).
2. **Overture zero semantics:** should POI/facility zeros become NULL when coverage is absent, or remain zero with a `none_listed` flag? This directly changes the culture/food dimension for Africa and China.
3. **Hazard/health denominator:** should the audit report 100% of GHSL centres or 67.6% of principal cities? Both are true; the product claim must pick one and label it.
4. **Broadband exclusions:** accept the Russia/Iran/China gap and keep 90.85%, or require `total_tests>=10` and demote to 84.20%?
5. **Climate construct:** keep the current mild-and-dry formula, switch to a UTCI/seasonality metric, or publish two climate views? Coverage is fixable; the construct is not.
6. **Rainy-day semantics:** use a calibrated precipitation-to-rainy-day model (MAE 3.15 days/month, R2 0.740) or fill measured NASA POWER rainy days?
7. **WorldClim licence:** replace WorldClim for commercial deployment or obtain permission? This affects the Core label for `climate_monthly`.
8. **Greenery method for 3,371 unmatched cities:** 5 km 10 m disc (~28 min), existing 1 km/15 km discs (49 min), or no greenery for unmatched? Nearest-centre assignment is not defensible (median 31.2 km, p90 103.1 km).
9. **GHSL GREENNESS role:** use as the Supplementary NDVI/canopy trend and validation (r=0.818 with WorldCover tree share), not as the primary 11-class metric. Confirm the `GR_SQM_*`/`GR_SHB_*` units before any absolute-area use.
10. **Metro radius:** raise the fallback from 5 km to 10 km with a `nearby` flag (adds 467 destinations, 439 same-country, including Dubai) or keep 5 km strict?
11. **CQI weights:** confirm the four-dimension Core weights (environment 45.5%, safety 27.3%, affordability 18.2%, climate 9.1%) or the five-dimension variant with connectivity (41.7/25.0/16.7/8.3/8.3).
12. **Taiwan:** add national statistics (MOI/DGBAS) or exclude/flag Taiwan? The current renormalization produces 8 cities below 0.50 completeness scoring >=80.
13. **Homicide methodology:** accept WHO/UNODC modelled estimates, keep the World Bank reported series and flag Africa, or publish both?
14. **Public transport:** treat NULL as unknown and drop the dimension, use GTFS>0 as a binary local-transit flag (with bbox inflation), or invest in OSM/Transitland stop-level data?
15. **Culture replacement:** is a Core culture-only indicator (UNESCO World Heritage proximity, about 1,223 sites) acceptable, with food kept as Supplementary mapped restaurant density?
16. **GHSL climate projections:** include the 2040/2070 SSP projections already in the 18 MB CLIMATE file, or keep the product current-only?
