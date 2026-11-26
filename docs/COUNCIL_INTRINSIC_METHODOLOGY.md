# Intrinsic City Quality Index — Council Methodology (Phase 1)

**Adopted:** 2026-11-26 (consolidated from a two-round independent council of two Grok 4.7 High agents)

## Core Principle

Measure the **intrinsic quality of an urban centre regardless of its cost**. Affordability, price level, wages, rents, purchasing power, out-of-pocket health spending, airport access, life expectancy, physician density, road traffic deaths, and WorldClim precipitation-as-comfort are **excluded (weight 0%)** from the intrinsic aggregate and kept only in separate travel/context exports.

## Three-Board, Absolute-Anchor, No-Imputation Architecture

Every active indicator uses **fixed 0–100 physical or epidemiological anchors** (no cohort percentiles in the score, no median imputation, and no silent renormalization across missing pillars). A point rank is published **only inside a declared cohort whose members share the exact same observed pillar set**.

### Full 100% Reference Vector vs Phase 2B Active Cohort (74%)

| # | Pillar | Board | Ref Weight | Phase 2B Status | Anchor / Rule |
| --- | --- | --- | ---: | --- | --- |
| 1 | Air quality | Biophysical | 15% | **Active** | SatPM PM2.5 log scale (`100` ≤5 µg/m³, `0` ≥75 µg/m³) |
| 2 | Thermal safety | Biophysical | 12% | **Active (all 13,101 cities)** | 5-year daily UTCI (Bröde 2012 from NASA POWER MERRA-2 S3 Zarr, 2020–2024): `0.45·heat(>32/120) + 0.30·cold(<0/150) + 0.25·severe((>38 + <-13)/30)` |
| 3 | Urban greenery | Biophysical | 14% | **Active (all 7,018 GHSL polygons)** | ESA WorldCover 2021 10 m on GHSL polygon `(tree+shrub)/land / 0.40 × canopy_mult`; GHSL NDVI stored as diagnostic only |
| 4 | Waterfront & hinterland | Biophysical | 7% | **Active (shore)** | 1–15 km decay to coast, lake ≥10 km², or Strahler ≥5 river (retiring the saturated 50 km decay) |
| 5 | Urban form | Biophysical | 7% | **Named hole** | Awaits GHS-POP 100 m + pre-registered validation |
| 6 | Personal safety (violence prior) | Country-labelled | 9% | **Active** | UNODC/WHO homicide piecewise (`100` ≤1, `80` @3, `50` @10, `20` @25, `0` ≥50 per 100k); never reorders within country |
| 7 | Water & sanitation (WASH) | Country-labelled | 6% | **Active** | Mean of WHO/UNICEF JMP safely managed water & sanitation ONLY when both exist (no basic-water fallback) |
| 8 | Healthcare access | City | 7% | **Named hole** | Awaits Weiss motorized hospital travel-time friction raster |
| 9 | Natural hazards (non-heat) | Exposure | 6% | **Named hole** | Awaits JRC flood + GEM seismic + cyclone rasters (2015 MYRIAD kept as context only) |
| 10 | Public transport | Services (gated) | 6% | **Named hole** | Awaits coach-excluded local GTFS headway / road-gated OSM guideway |
| 11 | Cultural heritage | Services | 6% | **Active (all 11,422 GHSL centres)** | Official UNESCO `whc001` Cultural & Mixed sites (including serial components) intersected with GHSL polygons (`0→40, 1→70, 2→85, 3+→100`) |
| 12 | Digital connectivity | Services | 5% | **Active** | Ookla fixed absolute blend (`50% down [10–150] + 20% up [5–50] + 30% lat [80–10]`), `≥10 tests & ≥2 tiles` (`10–49` capped at Tier B) |

## Settlement Classification & Satellite Suppression

- **`ghsl_centre`**: One principal city per GHSL UCDB R2024A urban centre (`7,018` matched centres).
- **`satellite_suppressed`**: Unmatched GeoNames localities within **15 km** of any GHSL urban centre (suppressed from independent ranking to eliminate suburb duplicates).
- **`unmatched_town`**: Remaining unmatched localities >15 km from any GHSL centre, exported separately in `reports/quality_unmatched_towns.csv` and never pooled into urban-centre ranks.

## Tiers & Release Gate Diagnostics

- **Tier A**: Complete Phase 1 GHSL urban-centre cohort (`construct_coverage = 0.42`, all 5 active pillars observed: air, waterfront, homicide prior, JMP WASH prior, broadband; GHSL UTCI>32 heat days are published as a diagnostic column without ranking heat-only cities) with Ookla `≥50 tests` and `≥2 tiles`. Only Tier A receives an ordered point rank.
- **Tier B**: Complete Phase 1 GHSL urban-centre cohort with Ookla `10–49 tests` (prevents censoring Africa/Asia while flagging lower sample precision).
- **Tier C**: Incomplete cohort, satellite, or unmatched town.
- **Mandatory release checks**: Every run reports log-GDP/capita correlation by board, top-100 continent shares vs GHSL centre base rates, and pathological checks (hot cities ≥150 UTCI>32 days, China zero-POI `unobserved` status, unique GHSL centres in top 100).
