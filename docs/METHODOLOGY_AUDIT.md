# Methodology audit: greenery and environmental quality

**Audit date:** 2026-11-25  
**Scope:** the optional green/environment scores in `place_profiles` and the MCP
server. These scores are context. They are not in `rank_destinations`
`final_score`.

## Executive summary

The previous “all-encompassing green score” was not defensible as a green
score. It mixed an absolute vegetation share, a percentile of mapped park POIs
per GeoNames resident, a percentile of annual PM2.5 and a distance-to-water
decay. Air supplied about 41% of its variance against a printed 25% weight,
the park term was the coverage gate, and the water term counted ponds. The
name promised “green”; the number mostly sorted large, well-mapped cities by
clean air.

This audit replaces it with two explicitly separate indices:

1. **Physical urban greenery** — tree canopy, other local vegetation,
   surrounding tree/shrub cover and mapped park availability.
2. **Overall environmental quality** — the greenery index, air quality and
   water access.

The headline findings:

- **Hong Kong is first on physical greenery (52.9)**, followed by Oslo (52.1),
  Stockholm (48.5), Longyan (48.2) and Zunyi (48.0), among 407 ranked cities.
- **Stockholm is first on overall environmental quality (71.8)**, followed by
  Oslo (70.2), Brisbane (68.6), Sydney (66.3) and Jacksonville (65.9).
- **Montréal is 30th on greenery (40.5) and 8th on environmental quality
  (62.0)** under the balanced weights. It ranks first only under a
  point-heavy variant that gives 60% of the greenery weight to the 1 km disc.
- The 1 km disc is a **point sample**, not a citywide canopy measurement.
  Under a city-proper ≥1M rule, Montréal’s point is unusually green; under a
  GHSL-urban-centre ≥1M rule, Vancouver (greenery 44.9, environment 70.1)
  enters and ranks above it. Kitakyushu’s 1 km point is 94.8% tree, which
  shows how far a point can sit from the city it represents.
- We did not adjust weights or boundaries to preserve any city’s position.
  The remaining limitation is stated plainly: **a true citywide greenery
  measure requires zonal statistics over the GHSL urban-centre polygon, which
  is not yet computed.**

## 1. Data sources and what each number actually is

| Layer | Dataset, version, year | Resolution / method | Measurement type | Licence and link |
|---|---|---|---|---|
| Vegetation | ESA WorldCover 2021 v200, 11 classes | 10 m; fractions in 1 km and 15 km discs around the GeoNames point; 15 km sampled from ~200 m overviews | **Observed classification** (annual composite), not NDVI and not canopy height | CC BY 4.0, https://esa-worldcover.org/en/data-access |
| Parks | Overture Maps Places, release `2026-11-18.0` | Point/POI count whose taxonomy hierarchy contains `park`, within 5 km Haversine; bbox centres | **Mapped POI proxy** for public green space; not area, access or quality | CDLA-Permissive 2.0 (named providers; some Apache 2.0/CC0), https://docs.overturemaps.org/attribution/ |
| Air | SatPM V6.GL.03 annual and monthly PM2.5, 2024 | 0.1° (~11 km) grid, sampled at the city point | **Modelled estimate** (satellite/model/monitor fusion), not a monitor | CC BY 4.0, https://www.satpm.org/ |
| Coast | Natural Earth 10m coastline | Straight-line distance from the city point | **Measured geometry** | Public domain, https://www.naturalearthdata.com/ |
| Lakes | HydroLAKES v1.0 | Nearest lake/reservoir of ≥10 ha, plus nearest lake of ≥10 km² | **Measured geometry**, not water quality | CC BY 4.0, https://www.hydrosheds.org/products/hydrolakes |
| Rivers | HydroRIVERS v1.0 | Nearest reach with Strahler order ≥4 | **Measured geometry**, not water quality | CC BY 4.0, https://www.hydrosheds.org/products/hydrorivers |
| Population | GeoNames `cities15000` | City record | **Third-party city population**, usually city proper, sometimes prefecture/county | CC BY 4.0, https://www.geonames.org/ |
| Urban boundary | GHSL UCDB R2024A urban centres | Polygon containment; centre population and area | **Census-derived gridded estimate** | CC BY 4.0, https://ghsl.jrc.ec.europa.eu/ |

Raw derived measurements are in
[`reports/urban_scores_all_407.csv`](reports/urban_scores_all_407.csv). The
source rasters and POI tables are not redistributed here; the bootstrap
scripts and URLs above reproduce them.

## 2. Bugs and questionable assumptions found

| # | Problem in the previous score | Effect | Fix in this audit |
|---:|---|---|---|
| 1 | The lake term was computed whenever `coast_distance_km` was non-null, even when the lake was below 10 km² | Ponds earned water credit; Vienna 30.5, Ottawa 8.4, Edmonton 4.6, Berlin 5.9 | Lake term requires a lake ≥10 km² |
| 2 | `destination_inland_water` stored only the single nearest lake, often a pond | A large lake 10–30 km away was invisible | Added `large_lake_*` columns: nearest lake ≥10 km² |
| 3 | Hydrological water access had no rivers | Seine, Thames, Danube, Spree, Ottawa River, North Saskatchewan all absent | Added HydroRIVERS Strahler ≥4 |
| 4 | Water pixels stayed in the vegetation denominator | Coastal/lake cities were penalised: Singapore’s 15 km ring is 49.3% water, Stockholm’s 1 km disc 26.1% | Land-only shares: divide by `1 − water_fraction`; null if water ≥80% |
| 5 | Park score capped at 100 parks | 471 of 4,040 cities (11.7%) scored exactly 100 | Fixed log anchor at 500 parks; 8 of 407 (2.0%) now reach 100 |
| 6 | Air score floored at 25 µg/m³ | 1,300 of 4,040 cities (32%) scored exactly 0 | Log scale 5–75 µg/m³; 9 of 407 (2.2%) now score 0 |
| 7 | Eligibility used city population, dedupe used GHSL centres, parks divided by city population | Mixed municipal, urban and metropolitan boundaries | Population is only an eligibility threshold; no environmental component is divided by population |
| 8 | Metro labels used GHSL fragment names | “Fort Worth → Edgecliff Village”, “Shenzhen → Guangzhou” | Principal city is the largest city in the centre; centre name and population are shown, not used as the city label |
| 9 | One composite mixed greenery, parks, air and water | Air dominated variance; name was misleading | Two separate indices; components always exposed |

## 3. Exact formulas

### 3.1 Land-only shares

For each radius $r \in \{1\text{ km}, 15\text{ km}\}$:

$$
\text{land}_r = 1 - \text{water}_r
$$

$$
\text{tree}_r = \frac{\text{tree fraction}_r}{\text{land}_r}, \quad
\text{other}_r = \frac{\text{shrub fraction}_r + \text{grass fraction}_r}{\text{land}_r}
$$

A share is null when $\text{land}_r < 0.2$ (the point is mostly water). Raw
fractions, including cropland and built-up, stay in
`destination_land_cover` and `destination_green_score`.

### 3.2 Physical urban greenery (`destination_greenery_score`)

All components are 0–100:

$$
\begin{aligned}
\text{local tree} &= 100 \times \text{tree}_{1\text{ km}} \\
\text{local other} &= 100 \times \text{other}_{1\text{ km}} \\
\text{hinterland} &= 100 \times \frac{\text{tree}_{15\text{ km}} + \text{shrub}_{15\text{ km}}}{1} \\
\text{parks} &= \min\left(100,\ 100 \times \frac{\ln(1+\text{park count}_{5\text{ km}})}{\ln(501)}\right)
\end{aligned}
$$

$$
\text{greenery} =
0.25 \times \text{local tree}
+ 0.15 \times \text{local other}
+ 0.45 \times \text{hinterland}
+ 0.15 \times \text{parks}
$$

The score exists only when all four components exist. A zero park count is
`none_listed`, not a measured zero, so those cities are not scored.

**Why these weights.** Tree canopy is the most direct physical greenery
measure, so it leads the local terms. The 15 km ring gets 45% because the 1 km
disc is one point and can sit in an unusually green block; the ring is a
larger, more stable landscape sample. Parks get 15% because the only global
park layer we have is a mapped POI count, not area or access. The weights are
a value judgment; §6 shows the alternatives.

### 3.3 Air (`destination_environment_score.air_score`)

Annual PM2.5 in µg/m³, fixed log scale:

$$
\text{air} =
\begin{cases}
100 & \text{PM2.5} \le 5 \\
0 & \text{PM2.5} \ge 75 \\
100 \times \dfrac{\ln(75) - \ln(\text{PM2.5})}{\ln(75) - \ln(5)} & \text{otherwise}
\end{cases}
$$

This is cohort-independent. 100 is the WHO annual guideline (5 µg/m³); 0 is a
high-pollution anchor (75 µg/m³). The scale is log-linear because PM2.5
exposure–response is closer to log-linear than linear. It is not a health-risk
calculation.

### 3.4 Water (`destination_environment_score.water_score`)

$$
d = \min(\text{coast distance},\ \text{large-lake distance},\ \text{river distance})
$$

$$
\text{water} = 100 \times \max\left(0,\ 1 - \frac{d}{50\ \text{km}}\right)
$$

Large lakes are ≥10 km². Rivers are Strahler order ≥4. Distances are
straight-line to the feature, not walking routes. **This is water access, not
water quality.** No global city-level water-quality indicator (for example
fecal coliform, nutrient loading or chemical status) is loaded.

### 3.5 Overall environmental quality (`destination_environment_score`)

$$
\text{environment} =
0.50 \times \text{greenery}
+ 0.35 \times \text{air}
+ 0.15 \times \text{water}
$$

The score exists only when all three exist. Components are exposed.

### 3.6 Ranking universe

1. City population ≥ 1,000,000 (GeoNames).
2. One row per GHSL urban centre. The **principal city** is the largest
   GeoNames city in that centre, not the highest-scoring suburb.
3. Boundary filter: GHSL centre population ≥ 25% of city population. This
   removes prefecture/county records whose point is not an urban area.
4. Rank by the score, descending; tie-break by city population.

Current counts: 13,101 destinations → 5,000 with land cover → 4,038 with a
park-inclusive greenery score → 551 with city population ≥1M → 478 scored
(72 excluded for no mapped park, 1 for a mostly-water 15 km ring) → 477 with a
GHSL centre → 417 principal cities → 10 boundary-ambiguous → **407 ranked**.
One further scored city has no GHSL centre and is not ranked.

## 4. Answers to the specific questions

### 4.1 Vegetation

**What it measures.** ESA WorldCover class fractions: tree cover (class 10),
shrubland (20) and grassland (30), as a share of land in a 1 km disc and a
15 km disc. It is **not NDVI**, not leaf-area index, not canopy height and not
a species or quality assessment. Cropland, built-up, bare, snow and water are
excluded from the numerator; water is removed from the denominator.

**Why the numbers differ.** With land-only shares:

| City | 1 km tree | 1 km other | 15 km tree+shrub | 15 km water | Parks | Greenery |
|---|---:|---:|---:|---:|---:|---:|
| Montréal | 59.7% | 11.7% | 20.5% | 15.1% | 424 | 40.5 |
| Singapore | 18.3% | 3.9% | 31.6% | 49.3% | 273 | 32.9 |
| Stockholm | 8.7% | 0.7% | 71.2% | 18.4% | 361 | 48.5 |

Montréal’s GeoNames point sits in a very green residential block: 59.7% tree
in the 1 km disc. Its 15 km ring is only 20.5% tree+shrub after removing
water, so the balanced score is 40.5, not the old 44.4. Singapore’s 15 km ring
is almost half water; removing water raises the tree share from 16.0% to
31.6%. Its local point is dense built-up, so it scores 32.9. Stockholm’s local
point is a waterfront built-up block (8.7% tree), but the 15 km ring is 71.2%
forest, which is why it scores 48.5.

**Reconciliation with municipal greenery statistics.** It does not reconcile
directly. Municipal figures are citywide, use different boundaries (city
proper vs metro), different dates and different definitions (canopy vs all
green cover, including grass, parks and private gardens). A 1 km point sample
cannot reproduce a citywide canopy percentage. For example, published
estimates of Montréal’s citywide tree canopy are far below 59.7%; the 59.7%
is the block around the point. This is the largest unresolved limitation of
the greenery ranking. A citywide number needs zonal statistics over the GHSL
urban-centre polygon, not a point buffer.

**Season and imagery date.** WorldCover 2021 v200 is an annual composite
built mostly from 2021 Sentinel-1/2 imagery. It is not seasonal; deciduous
canopy in winter and summer is not separated. The 15 km ring is sampled from
overviews at about 200 m, so it is a landscape fraction, not a 10 m census.
Tropical evergreen, temperate deciduous, steppe grass and desert are all
counted by class, not by ecological quality.

### 4.2 Parks

**Why so many perfect scores?** The previous version capped at 100 parks:
`100 × ln(1+count)/ln(101)`. 471 of 4,040 cities (11.7%) hit 100. The current
version anchors 100 at 500 parks: `100 × ln(1+count)/ln(501)`. Only 8 of 407
cities (2.0%) now reach 100. The cap is explicit: **500 mapped park POIs
within 5 km**.

**What it measures.** A count of Overture Places whose taxonomy hierarchy
contains `park`, within a 5 km Haversine radius of the city point. It is not
park area, not park access, not parks per capita and not park density. A large
park whose POI centre is outside 5 km is missed; a small playground with a
park tag is counted the same as a national park.

**Coverage bias.** Overture combines multiple providers and OSM; density is
uneven. 72 of 551 million-plus cities have no mapped park in 5 km and are
therefore unscored, including Weifang (9.4M). The earlier council review found
58% zero-park dropout in China/Hong Kong/Taiwan and 48% in Africa, against 0%
in the US, Canada and the Western European sample. A zero is `none_listed`,
not proof that no park exists. This is why the score is not imputed.

**Forests, reserves and private green space.** They are not counted as parks
unless Overture tags them `park`. They do count in the vegetation layer if
they are tree/shrub/grass, regardless of whether a resident can enter them.
That is a deliberate distinction: vegetation is physical cover; parks are a
mapped public-amenity proxy. The index does not measure accessible green space
area, only mapped park presence.

### 4.3 Water

**What it measures.** Straight-line distance to the nearest of: Natural Earth
coastline; HydroLAKES lake ≥10 km²; HydroRIVERS reach with Strahler order ≥4.
Linear decay to zero at 50 km. It is **water access/proximity**, not water
quality, not beach quality, not swimming safety and not flood risk.

**Why the old numbers collapsed.** The old view computed the lake term even
when the lake was a pond, because `coast_distance_km` was always non-null. It
also only knew the single nearest lake. The new view uses the nearest large
lake and a major river:

| City | Old water | New water | What changed |
|---|---:|---:|---|
| Edmonton | 4.6 | 99.5 | North Saskatchewan River 0.27 km replaces a 0.1 km² pond |
| Berlin | 5.9 | 97.9 | Spree 1.06 km |
| Ottawa | 8.4 | 96.8 | Ottawa River 1.62 km |
| Vienna | 30.5 | 93.3 | Danube 3.37 km |
| Paris | 9.2 | 99.8 | Seine 0.12 km |
| London | 28.0 | 98.9 | Thames 0.55 km |

**Missing values.** Coast distance is present for every city, so the water
score is never null; 0 means the nearest of the three features is ≥50 km away.
That is a distance fact, not a judgment that the water is bad. The score is
not a water-quality measure, and no water-quality value is inferred from
missing data.

**Do oceans, lakes and rivers artificially influence the score?** Yes, by
design, but the influence is now explicit: 300 of 407 ranked cities use a
river, 21 a large lake and 86 the coast. A river city is no longer punished
for lacking a lake, and a pond no longer counts as a lake. The remaining
arbitrariness is the 10 km² lake threshold, the Strahler ≥4 river threshold
and the 50 km decay.

### 4.4 Air quality

**Pollutant and period.** Annual mean PM2.5 for 2024 from SatPM V6.GL.03, plus
the monthly maximum for seasonality. No NO2, ozone, PM10 or ultrafine
particles are loaded. The grid is 0.1° (~11 km), sampled at the city point; it
cannot resolve near-road or neighbourhood differences.

**Why exactly 0.0?** The log scale reaches 0 at 75 µg/m³ and is clipped there.
9 of 407 ranked cities are at 0 (2.2%), including Delhi (annual 96.5, worst
month 213.8). The old 25 µg/m³ floor put 1,300 of 4,040 cities (32%) at 0.
The new scale keeps discrimination among polluted cities while still giving a
clear zero anchor.

**Temporary events.** The annual value includes wildfire smoke, dust and
seasonal burning. The monthly layer exposes the worst month (for example
Zunyi 25.9 annual, 41.2 worst month). The score uses the annual value; it does
not remove wildfire months. That is a known source of year-to-year volatility.

### 4.5 Population and geographic boundaries

**What is mixed.** GeoNames city population is used only for the ≥1M
eligibility threshold. GHSL urban-centre population is used only for
deduplication and the boundary filter. No environmental component is divided
by either population. The park metric is a count, not per capita. This removes
the old per-capita denominator problem, but it does not make the city and
metro boundaries identical.

**Fort Worth / Edgecliff Village.** The Fort Worth GeoNames point falls inside
a GHSL fragment named Edgecliff Village with 267,808 residents, while the city
record has 1,008,106. Ratio 0.27, just above the 0.25 filter, so Fort Worth is
kept but flagged in the raw CSV. Its score is the score at the Fort Worth
point, not a claim about the whole Dallas–Fort Worth metroplex. The GHSL
centre name is shown only as context.

**Shenzhen / Guangzhou.** Shenzhen and Guangzhou are in one GHSL urban centre
of 43.0M. The principal city is Guangzhou (18.7M vs Shenzhen 17.5M), so
Guangzhou represents the centre. Shenzhen’s own point scores 44.2 greenery and
52.1 environment, but including both would double-count the same urban area.
This is a presentation choice, not a data merge.

**Vancouver, Helsinki and Amsterdam.** Their GeoNames city populations are
662k, 659k and 742k, below the 1M city threshold, so they are excluded even
though their GHSL centres are 2.2M, 1.0M and 1.2M. Vancouver’s scores would be
**48.0 greenery and 71.6 environment**, which would rank 5th on greenery and
2nd on environment if it were admitted.
This is the clearest evidence that the city-proper threshold is a boundary
artifact. The audit keeps the city-proper rule because the measurement point
is the city’s point, but reports this sensitivity rather than hiding it.

**Why not a pure GHSL urban-centre rule?** It is more consistent, but it
admits small principal cities whose point is not representative: Kitakyushu
(centre 1.2M, city 941k) has a 1 km point that is 94.8% tree and ranks first
on point-heavy greenery; Uyo (centre 1.6M, city 437k) is 73.3% tree; Garut
(centre 1.5M, city 132k) is 64.7% tree. A centre-based ranking would crown
those points. It would also exclude Oslo (centre 890k) and Ottawa (centre
605k), despite their city populations being over 1M. No single population
threshold is perfect. The chosen rule is documented, not presented as
universally correct.

**Can a consistent global urban-area definition be used?** Yes, but it needs
zonal statistics over the GHSL polygon, not a point sample. That is the next
methodological step. Until then, the ranking is a **principal-city point
sample**, and the centre population is shown so the reader can see the gap.

### 4.6 Scoring methodology

The exact formulas are in §3. The weights are:

- Greenery: **25% local tree, 15% local other vegetation, 45% hinterland
  tree+shrub, 15% mapped parks**.
- Environment: **50% greenery, 35% air, 15% water access**.

**Why these weights.** The greenery weights put 40% on the 1 km point and 45%
on the 15 km ring, so one green block cannot decide the city. Parks are 15%
because the count is a proxy. The environment weights keep greenery as the
largest term, air as the main health exposure and water as the smallest term
because it is access, not quality.

**Sensitivity.** The top five under alternative weights:

| Greenery variant | Top five |
|---|---|
| Headline 25/15/45/15 | Hong Kong, Oslo, Stockholm, Longyan, Zunyi |
| Point-heavy 35/25/25/15 | Montréal, Zunyi, Jamshedpur, Hong Kong, Kuala Lumpur |
| Hinterland-heavy 20/15/50/15 | Oslo, Hong Kong, Stockholm, Longyan, Zamboanga |
| Equal 25/25/25/25 | Hong Kong, Montréal, Kuala Lumpur, Oslo, Stockholm |
| No parks (45/30/25 vegetation) | Zunyi, Jamshedpur, Palembang, Montréal, Longyan |

| Environment variant | Top five |
|---|---|
| Headline 50/35/15 | Stockholm, Oslo, Brisbane, Sydney, Jacksonville |
| Equal 1/3 each | Stockholm, Brisbane, Oslo, Sydney, Auckland |
| Greenery-heavy 60/25/15 | Stockholm, Oslo, Brisbane, Jacksonville, Hong Kong |
| Air-heavy 40/45/15 | Stockholm, Brisbane, Oslo, Sydney, Auckland |
| No water 60/40/0 | Stockholm, Oslo, Brisbane, Sydney, Jacksonville |

Montréal ranks 30th on balanced greenery, 1st on point-heavy greenery, 4th
with no parks, and 8th on balanced environment (9th–12th under the environment
variants). Its first place is not robust.
**Correlations and double counting.** Among the 407 ranked cities,
greenery–air correlation is 0.49, greenery–water 0.20, air–water 0.24.
Parks correlate only weakly with local tree cover (0.17 in the earlier
4,040-city cohort). They are related but not duplicates. In the environment
composite, air’s own variance is 37.8% of the total (about 51% if half of the
greenery–air covariance is attributed to it) against a 35% weight; greenery’s
own share is 26.8%. Air has the largest standard deviation (22.7 vs 10.9 for
greenery and 15.2 for water). This is stated rather than hidden; the separate
greenery ranking is the remedy if air dominance is unwanted.

**Should air and water be separated from physical greenery?** Yes. They are
separate rankings. The environment index is a deliberate composite for users
who want one environmental context number; the greenery index is the physical
cover measure. Neither is in `final_score`.

### 4.7 Coverage and validation

**Counts.** 13,101 destinations; 5,000 with WorldCover land cover; 4,038 with
a park-inclusive greenery score; 551 with city population ≥1M; 478 scored
(72 excluded for no mapped park, 1 for a mostly-water 15 km ring); 477 with a
GHSL centre; 417 principal cities; 10 boundary-ambiguous (centre <25% of city
population); **407 ranked**. One further scored city has no GHSL centre.

**Excluded million-plus cities.** The 72 no-park cities include Weifang
(9.4M) and other Chinese, African and Asian cities where Overture has no
mapped park in 5 km. Dakar is excluded because its 15 km ring is 80.2% water,
so the land-only share is null. One scored city has no GHSL centre match. The
10 boundary-ambiguous cities are Puyang, Kakamega, Malingao, Ordos, Yantai,
Wuwei, Yangzhou, Zhongwei, Taizhou and Yishui. They are listed in
`reports/excluded_1m.csv` with reasons. Missing components are null; nothing
is imputed.

**Validation.** The index has **not** been validated against independent
scientific studies or official municipal canopy inventories. The point sample
and the municipal boundary definitions are different, so a direct correlation
would be misleading. The sanity checks are internal: land-cover fractions sum
to 1; the air direction is correct; the water distance is the minimum of three
documented features; parks are a count, not a rate; and the rankings are
stable to reasonable weight changes at the top but not at the margin.

**Uncertainty.** There is no formal confidence interval because the source
errors (WorldCover class accuracy, Overture completeness, SatPM model error)
are not quantified per city. The weight sensitivity above is the available
uncertainty band. A city’s rank should be read as “in this index, with these
weights”, not as a precise environmental measurement.

### 4.8 Montréal

Montréal is **30th of 407 on balanced greenery (40.5)** and **8th on balanced
environmental quality (62.0)**. It is first on the point-heavy variant
(43.5) because its 1 km point is 59.7% tree, and fourth on the no-parks
vegetation variant. It is not first on the hinterland-heavy variant.

Its high point-heavy score is therefore partly an artifact of:
- **point sampling** — one green residential block, not citywide canopy;
- **boundary selection** — the city-proper ≥1M rule excludes Vancouver,
  whose point scores 44.9 greenery and 70.1 environment;
- **weighting** — the 60% local weight in the point-heavy variant;
- **park mapping** — 424 mapped parks give it a 97.4 park score.

It remains a genuinely green city with a strong local canopy and many mapped
parks. It is not demonstrably the greenest million-plus city in the world, and
this audit does not claim that. No weight or boundary was changed to keep it
first; the balanced weights move it to 30th.

## 5. Ranking 1: physical urban greenery

Balanced weights: 25% local tree, 15% local other vegetation, 45% hinterland
tree+shrub, 15% mapped parks. 407 principal cities, city population ≥1M,
GHSL centre population ≥25% of city population.

| # | City | Country | Score | Local tree | Other veg | Hinterland | Parks | Population | GHSL centre |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | Hong Kong | HK | 52.9 | 26.8 | 0.5 | 69.2 | 100.0 | 7,396,076 | Hong Kong |
| 2 | Oslo | NO | 52.1 | 15.5 | 0.9 | 79.1 | 83.3 | 1,082,575 | Oslo |
| 3 | Stockholm | SE | 48.5 | 8.7 | 0.7 | 71.2 | 94.8 | 1,515,017 | Stockholm |
| 4 | Longyan | CN | 48.2 | 29.5 | 3.0 | 83.9 | 17.7 | 1,025,087 | Longyan |
| 5 | Zunyi | CN | 48.0 | 73.2 | 1.3 | 61.9 | 11.1 | 2,037,775 | Honghuagang |
| 6 | Zamboanga | PH | 47.5 | 12.4 | 3.7 | 77.2 | 60.9 | 1,018,849 | Zamboanga City |
| 7 | Sanya | CN | 47.2 | 35.1 | 1.1 | 74.7 | 31.3 | 1,031,396 | Sanya |
| 8 | Davao | PH | 46.8 | 7.5 | 0.4 | 76.0 | 71.3 | 1,848,947 | Davao City |
| 9 | Taipei | TW | 46.2 | 8.8 | 0.2 | 64.3 | 100.0 | 7,871,900 | Taipei |
| 10 | Caracas | VE | 46.1 | 15.9 | 0.6 | 69.5 | 71.5 | 3,000,000 | Caracas |
| 11 | Jamshedpur | IN | 45.7 | 54.6 | 5.4 | 48.1 | 63.6 | 1,339,438 | Jamshedpur |
| 12 | Bandar Lampung | ID | 44.6 | 17.1 | 0.4 | 70.8 | 55.7 | 1,088,200 | Bandar Lampung |
| 13 | Belém | BR | 44.3 | 2.6 | 3.9 | 76.7 | 57.2 | 1,499,641 | Belém |
| 14 | Jacksonville | US | 44.3 | 16.3 | 11.9 | 60.7 | 73.9 | 1,009,833 | Jacksonville |
| 15 | Kuala Lumpur | MY | 44.0 | 53.1 | 0.4 | 38.4 | 89.6 | 1,453,975 | Kuala Lumpur |
| 16 | Daejeon | KR | 44.0 | 11.1 | 1.1 | 69.8 | 64.2 | 1,441,203 | Daejeon |
| 17 | Kyiv | UA | 44.0 | 33.2 | 0.7 | 50.8 | 84.8 | 2,952,301 | Kyiv |
| 18 | Yekaterinburg | RU | 43.7 | 20.9 | 0.0 | 60.6 | 74.6 | 1,495,066 | Yekaterinburg |
| 19 | Hiroshima | JP | 43.7 | 8.9 | 3.1 | 63.8 | 81.9 | 1,200,754 | Hiroshima |
| 20 | Budapest | HU | 43.3 | 26.1 | 1.6 | 49.2 | 96.3 | 1,741,041 | Budapest |
| 21 | Palembang | ID | 43.2 | 39.2 | 16.4 | 53.8 | 44.6 | 1,801,367 | Palembang |
| 22 | Pekanbaru | ID | 43.1 | 5.3 | 0.5 | 73.3 | 58.1 | 1,167,599 | Pekanbaru |
| 23 | Santiago de los Caballeros | DO | 42.6 | 28.5 | 2.8 | 60.6 | 51.8 | 1,200,000 | Santiago de los Caballeros |
| 24 | Chattogram | BD | 42.5 | 21.1 | 0.2 | 60.6 | 66.1 | 3,920,222 | Chattogram |
| 25 | Bursa | TR | 42.5 | 9.6 | 0.4 | 59.7 | 87.7 | 3,101,833 | Bursa |
| 26 | Berlin | DE | 42.4 | 19.0 | 0.5 | 52.4 | 93.3 | 3,426,354 | Berlin |
| 27 | Jepara | ID | 42.3 | 21.2 | 3.0 | 66.7 | 43.6 | 1,257,912 | Jepara |
| 28 | Manaus | BR | 41.6 | 20.8 | 3.3 | 54.4 | 76.0 | 2,219,580 | Manaus |
| 29 | Brasília | BR | 40.7 | 11.5 | 43.7 | 48.3 | 63.2 | 2,207,718 | Brasilia |
| 30 | Montréal | CA | 40.5 | 59.7 | 11.7 | 20.5 | 97.4 | 1,762,949 | Montreal |
| 31 | Rasapūdipalem | IN | 40.5 | 11.4 | 1.4 | 60.6 | 67.6 | 1,728,128 | Visakhapatnam |
| 32 | İzmir | TR | 40.3 | 9.5 | 12.0 | 49.9 | 91.4 | 2,938,292 | Izmir |
| 33 | Changwon | KR | 40.0 | 15.1 | 2.9 | 61.2 | 55.2 | 1,025,702 | Changwon |
| 34 | Shaoguan | CN | 40.0 | 14.2 | 0.8 | 71.1 | 28.8 | 1,028,460 | Shaoguan |
| 35 | Ulsan | KR | 39.9 | 4.2 | 0.3 | 65.9 | 60.9 | 1,098,421 | Ulsan |
| 36 | Guilin | CN | 39.3 | 28.8 | 0.4 | 54.1 | 51.1 | 1,572,300 | Guilin |
| 37 | Medellín | CO | 38.6 | 4.3 | 2.5 | 54.6 | 83.7 | 1,999,979 | Medellín |
| 38 | Kharkiv | UA | 38.5 | 24.6 | 1.0 | 47.2 | 73.3 | 1,421,125 | Kharkiv |
| 39 | Brisbane | AU | 38.4 | 19.9 | 3.0 | 40.1 | 100.0 | 2,780,063 | Brisbane |
| 40 | Mérida | MX | 38.4 | 4.7 | 0.5 | 54.1 | 85.5 | 1,201,000 | Mérida |
| 41 | Aba | NG | 38.1 | 1.8 | 0.2 | 80.0 | 11.1 | 1,160,000 | Aba |
| 42 | Fuzhou | CN | 38.0 | 16.7 | 0.1 | 56.7 | 55.2 | 4,094,491 | Fuzhou |
| 43 | Ufa | RU | 37.9 | 20.9 | 1.0 | 50.8 | 64.5 | 1,120,547 | Ufa |
| 44 | Virār | IN | 37.9 | 26.7 | 1.3 | 49.4 | 58.5 | 1,222,390 | Vasai-Virar |
| 45 | Yichang | CN | 37.8 | 7.7 | 0.4 | 75.9 | 11.1 | 1,350,150 | Yichang |
| 46 | Sydney | AU | 37.7 | 23.3 | 6.1 | 35.5 | 100.0 | 5,638,830 | Sydney |
| 47 | Samara | RU | 37.7 | 31.9 | 1.2 | 44.3 | 64.2 | 1,163,399 | Samara |
| 48 | Huizhou | CN | 37.7 | 29.6 | 1.0 | 55.8 | 33.4 | 2,900,113 | Huizhou |
| 49 | Cần Thơ | VN | 37.6 | 14.8 | 2.7 | 55.3 | 57.2 | 1,507,187 | Cần Thơ |
| 50 | Daegu | KR | 37.4 | 2.8 | 0.1 | 59.3 | 66.9 | 2,365,523 | Daegu |
| 51 | Nairobi | KE | 37.4 | 27.6 | 9.1 | 42.2 | 67.4 | 4,397,073 | Nairobi |
| 52 | Köln | DE | 37.2 | 16.3 | 0.3 | 44.8 | 86.3 | 1,024,621 | Cologne |
| 53 | Nizhniy Novgorod | RU | 37.2 | 21.1 | 4.6 | 50.8 | 55.7 | 1,259,013 | Nizhny Novgorod |
| 54 | Hamburg | DE | 37.1 | 7.7 | 0.2 | 50.3 | 83.0 | 1,973,896 | Hamburg |
| 55 | Hezhou | CN | 37.0 | 17.0 | 7.2 | 64.4 | 17.7 | 1,005,490 | Hezhou |
| 56 | Ottawa | CA | 37.0 | 14.0 | 1.6 | 44.6 | 87.8 | 1,017,449 | Ottawa |
| 57 | Warsaw | PL | 36.9 | 9.3 | 0.4 | 47.6 | 87.5 | 1,702,139 | Warsaw |
| 58 | Wenzhou | CN | 36.9 | 19.0 | 1.2 | 56.9 | 42.5 | 2,650,000 | Wenzhou |
| 59 | Barquisimeto | VE | 36.9 | 6.7 | 10.3 | 60.3 | 43.6 | 1,240,714 | Barquisimeto |
| 60 | Antalya | TR | 36.7 | 8.3 | 0.4 | 46.7 | 90.4 | 1,335,002 | Antalya |
| 61 | Zhaoqing | CN | 36.7 | 11.0 | 1.3 | 69.1 | 17.7 | 1,553,109 | Zhaoqing |
| 62 | Sapporo | JP | 36.6 | 14.3 | 0.0 | 42.6 | 92.4 | 1,973,832 | Sapporo |
| 63 | Taichung | TW | 36.6 | 6.8 | 0.9 | 44.6 | 98.1 | 2,850,285 | Taichung |
| 64 | Quito | EC | 36.4 | 25.3 | 1.4 | 42.8 | 71.1 | 2,781,641 | Quito |
| 65 | Ganzhou | CN | 36.3 | 15.1 | 0.6 | 61.5 | 31.3 | 1,977,253 | Ganzhou |
| 66 | Minsk | BY | 36.1 | 29.2 | 2.7 | 36.9 | 78.4 | 1,742,124 | Minsk |
| 67 | Sendai | JP | 35.6 | 9.8 | 0.7 | 45.7 | 83.2 | 1,096,704 | Sendai |
| 68 | Vienna | AT | 35.6 | 10.7 | 1.1 | 41.0 | 95.3 | 1,691,468 | Vienna |
| 69 | Beirut | LB | 35.6 | 9.4 | 0.3 | 54.9 | 56.2 | 1,916,100 | Beirut |
| 70 | Xiamen | CN | 35.5 | 52.1 | 3.2 | 30.0 | 56.2 | 4,617,251 | Xiamen City |
| 71 | Qingyuan | CN | 35.4 | 18.2 | 0.0 | 61.1 | 22.3 | 1,738,424 | Qingyuan |
| 72 | Durban | ZA | 35.3 | 8.5 | 8.1 | 48.5 | 67.6 | 3,338,026 | Durban |
| 73 | Munich | DE | 35.1 | 10.6 | 0.4 | 43.7 | 84.7 | 1,505,005 | Munich |
| 74 | Houston | US | 35.1 | 13.0 | 2.9 | 42.7 | 80.9 | 2,314,157 | Houston |
| 75 | Putian | CN | 35.0 | 12.7 | 0.7 | 61.9 | 25.9 | 1,539,389 | Putian |
| 76 | Almaty | KZ | 35.0 | 37.4 | 0.4 | 34.6 | 66.9 | 1,977,011 | Almaty |
| 77 | Kazan | RU | 34.8 | 14.7 | 1.4 | 44.1 | 73.6 | 1,243,500 | Kazan |
| 78 | Krasnoyarsk | RU | 34.7 | 27.5 | 8.0 | 38.0 | 63.6 | 1,090,811 | Krasnoyarsk |
| 79 | Novosibirsk | RU | 34.7 | 12.4 | 0.5 | 46.7 | 70.1 | 1,612,833 | Novosibirsk |
| 80 | Batam | ID | 34.6 | 16.7 | 1.3 | 46.1 | 62.9 | 1,296,960 | Batam City |
| 81 | Moscow | RU | 34.4 | 9.3 | 0.5 | 40.9 | 90.6 | 10,381,222 | Moscow |
| 82 | Semarang | ID | 34.4 | 14.4 | 1.4 | 43.1 | 74.4 | 1,694,740 | Semarang |
| 83 | Voronezh | RU | 34.3 | 19.3 | 0.2 | 43.5 | 65.9 | 1,047,549 | Voronezh |
| 84 | Nanchong | CN | 34.3 | 11.1 | 1.2 | 65.9 | 11.1 | 1,858,875 | Shunqing |
| 85 | Philadelphia | US | 34.3 | 3.9 | 0.5 | 43.3 | 91.6 | 1,573,916 | Philadelphia |
| 86 | Rio de Janeiro | BR | 34.2 | 12.5 | 0.6 | 43.1 | 77.5 | 6,747,815 | Rio de Janeiro |
| 87 | Fukuoka | JP | 34.2 | 5.5 | 0.7 | 43.7 | 87.0 | 1,612,392 | Fukuoka |
| 88 | Yangon | MM | 34.2 | 47.3 | 0.1 | 26.9 | 68.3 | 4,477,638 | Yangon |
| 89 | Barcelona | ES | 34.1 | 5.3 | 0.1 | 39.6 | 100.0 | 1,686,208 | Barcelona |
| 90 | Huế | VN | 34.1 | 7.5 | 1.2 | 51.1 | 60.5 | 1,380,000 | Huế |
| 91 | Kathmandu | NP | 34.0 | 3.0 | 4.3 | 48.6 | 71.7 | 1,442,271 | Kathmandu |
| 92 | Cali | CO | 34.0 | 2.2 | 1.0 | 47.6 | 79.1 | 2,392,877 | Cali |
| 93 | Gwangju | KR | 33.9 | 3.2 | 0.9 | 54.0 | 57.6 | 1,401,235 | Gwangju |
| 94 | Fort Worth | US | 33.9 | 24.4 | 7.9 | 34.7 | 73.1 | 1,008,106 | Edgecliff Village |
| 95 | Zhuzhou | CN | 33.7 | 6.8 | 1.1 | 61.0 | 28.8 | 1,129,687 | Zhuzhou |
| 96 | Belgrade | RS | 33.6 | 16.2 | 0.8 | 37.1 | 84.7 | 1,273,651 | Belgrade |
| 97 | Chongqing | CN | 33.4 | 22.4 | 0.1 | 45.9 | 47.4 | 22,000,000 | Chongqing |
| 98 | Dallas | US | 33.1 | 5.2 | 4.0 | 42.0 | 81.9 | 1,326,087 | Dallas |
| 99 | Yaoundé | CM | 33.0 | 7.7 | 5.1 | 55.1 | 37.0 | 1,299,369 | Yaoundé |
| 100 | Recife | BR | 33.0 | 12.3 | 5.4 | 40.7 | 71.8 | 1,653,461 | Recife |

## 6. Ranking 2: overall environmental quality

50% physical greenery, 35% air, 15% water access. The same 407 cities.

| # | City | Country | Score | Greenery | Air | Water | PM2.5 | Population | GHSL centre |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | Stockholm | SE | 71.8 | 48.5 | 93.1 | 99.9 | 6.0 | 1,515,017 | Stockholm |
| 2 | Oslo | NO | 70.2 | 52.1 | 84.9 | 96.1 | 7.5 | 1,082,575 | Oslo |
| 3 | Brisbane | AU | 68.6 | 38.4 | 98.6 | 99.4 | 5.2 | 2,780,063 | Brisbane |
| 4 | Sydney | AU | 66.3 | 37.7 | 95.8 | 92.6 | 5.6 | 5,638,830 | Sydney |
| 5 | Jacksonville | US | 65.9 | 44.3 | 83.1 | 97.9 | 7.9 | 1,009,833 | Jacksonville |
| 6 | Auckland | NZ | 64.0 | 31.3 | 95.8 | 98.8 | 5.6 | 1,547,200 | Auckland |
| 7 | Ottawa | CA | 63.1 | 37.0 | 86.1 | 96.8 | 7.3 | 1,017,449 | Ottawa |
| 8 | Montréal | CA | 62.0 | 40.5 | 78.9 | 94.4 | 8.9 | 1,762,949 | Montreal |
| 9 | Hong Kong | HK | 61.9 | 52.9 | 58.3 | 99.8 | 15.4 | 7,396,076 | Hong Kong |
| 10 | Sanya | CN | 61.5 | 47.2 | 65.7 | 99.3 | 12.7 | 1,031,396 | Sanya |
| 11 | Philadelphia | US | 61.3 | 34.3 | 84.6 | 97.0 | 7.6 | 1,573,916 | Philadelphia |
| 12 | Santiago de los Caballeros | DO | 61.1 | 42.6 | 72.1 | 97.2 | 10.7 | 1,200,000 | Santiago de los Caballeros |
| 13 | Recife | BR | 61.0 | 33.0 | 85.0 | 98.7 | 7.5 | 1,653,461 | Recife |
| 14 | Adelaide | AU | 60.9 | 32.0 | 93.5 | 81.2 | 6.0 | 1,469,163 | Adelaide |
| 15 | Belém | BR | 60.7 | 44.3 | 67.6 | 99.0 | 12.0 | 1,499,641 | Belém |
| 16 | Berlin | DE | 60.6 | 42.4 | 70.7 | 97.9 | 11.1 | 3,426,354 | Berlin |
| 17 | Melbourne | AU | 59.9 | 27.6 | 89.3 | 98.9 | 6.7 | 5,435,590 | Melbourne |
| 18 | Hamburg | DE | 59.4 | 37.1 | 74.2 | 99.5 | 10.1 | 1,973,896 | Hamburg |
| 19 | Hiroshima | JP | 59.2 | 43.7 | 64.8 | 97.6 | 13.0 | 1,200,754 | Hiroshima |
| 20 | Kyiv | UA | 59.1 | 44.0 | 63.9 | 98.4 | 13.3 | 2,952,301 | Kyiv |
| 21 | Taipei | TW | 58.9 | 46.2 | 61.4 | 95.2 | 14.2 | 7,871,900 | Taipei |
| 22 | Perth | AU | 58.8 | 23.2 | 93.2 | 97.3 | 6.0 | 2,384,371 | Perth |
| 23 | Saint Petersburg | RU | 58.7 | 32.0 | 79.7 | 99.1 | 8.7 | 5,351,935 | Saint Petersburg |
| 24 | Munich | DE | 58.5 | 35.1 | 75.1 | 98.0 | 9.8 | 1,505,005 | Munich |
| 25 | Fort Worth | US | 58.5 | 33.9 | 78.7 | 93.5 | 8.9 | 1,008,106 | Edgecliff Village |
| 26 | Yekaterinburg | RU | 58.5 | 43.7 | 63.3 | 96.6 | 13.5 | 1,495,066 | Yekaterinburg |
| 27 | Köln | DE | 58.4 | 37.2 | 72.0 | 97.4 | 10.7 | 1,024,621 | Cologne |
| 28 | London | GB | 58.4 | 31.1 | 80.1 | 98.9 | 8.6 | 8,961,989 | London |
| 29 | Gqeberha | ZA | 58.3 | 25.7 | 88.5 | 96.6 | 6.8 | 1,050,078 | Gqeberha |
| 30 | Budapest | HU | 58.1 | 43.3 | 61.3 | 99.5 | 14.3 | 1,741,041 | Budapest |
| 31 | Houston | US | 58.1 | 35.1 | 73.0 | 99.7 | 10.4 | 2,314,157 | Houston |
| 32 | Dublin | IE | 58.0 | 29.4 | 82.4 | 96.7 | 8.0 | 1,024,027 | Dublin |
| 33 | Toronto | CA | 58.0 | 27.8 | 84.6 | 96.9 | 7.6 | 2,794,356 | Toronto |
| 34 | Dallas | US | 57.8 | 33.1 | 76.2 | 97.4 | 9.5 | 1,326,087 | Dallas |
| 35 | Ufa | RU | 57.8 | 37.9 | 69.2 | 97.5 | 11.5 | 1,120,547 | Ufa |
| 36 | Sapporo | JP | 57.7 | 36.6 | 71.2 | 96.7 | 10.9 | 1,973,832 | Sapporo |
| 37 | Samara | RU | 57.3 | 37.7 | 68.5 | 96.7 | 11.7 | 1,163,399 | Samara |
| 38 | Brussels | BE | 57.3 | 31.8 | 75.8 | 99.2 | 9.6 | 1,019,022 | Brussels |
| 39 | Maceió | BR | 56.6 | 24.8 | 84.8 | 97.1 | 7.5 | 1,031,597 | Maceió |
| 40 | Minsk | BY | 56.6 | 36.1 | 67.5 | 99.5 | 12.1 | 1,742,124 | Minsk |
| 41 | Sendai | JP | 56.4 | 35.6 | 73.5 | 85.7 | 10.3 | 1,096,704 | Sendai |
| 42 | Havana | CU | 56.2 | 32.3 | 73.3 | 96.1 | 10.3 | 2,163,824 | Havana |
| 43 | Vienna | AT | 56.0 | 35.6 | 69.3 | 93.3 | 11.5 | 1,691,468 | Vienna |
| 44 | Brasília | BR | 55.9 | 40.7 | 64.4 | 86.9 | 13.1 | 2,207,718 | Brasilia |
| 45 | Copenhagen | DK | 55.9 | 28.0 | 76.8 | 100.0 | 9.4 | 1,153,615 | Copenhagen |
| 46 | Batam | ID | 55.8 | 34.6 | 68.5 | 97.2 | 11.7 | 1,296,960 | Batam City |
| 47 | Salvador | BR | 55.8 | 30.3 | 75.9 | 93.7 | 9.6 | 2,711,840 | Salvador |
| 48 | Nizhniy Novgorod | RU | 55.7 | 37.2 | 63.7 | 98.5 | 13.3 | 1,259,013 | Nizhny Novgorod |
| 49 | Zamboanga | PH | 55.7 | 47.5 | 48.8 | 98.8 | 20.0 | 1,018,849 | Zamboanga City |
| 50 | New York City | US | 55.5 | 23.0 | 83.5 | 98.6 | 7.8 | 8,804,190 | New York City |
| 51 | Edmonton | CA | 55.1 | 32.9 | 67.8 | 99.5 | 12.0 | 1,010,899 | Edmonton |
| 52 | Davao | PH | 55.1 | 46.8 | 48.3 | 98.5 | 20.3 | 1,848,947 | Davao City |
| 53 | Pekanbaru | ID | 54.8 | 43.1 | 55.8 | 91.2 | 16.6 | 1,167,599 | Pekanbaru |
| 54 | Kazan | RU | 54.6 | 34.8 | 64.8 | 97.2 | 13.0 | 1,243,500 | Kazan |
| 55 | Cape Town | ZA | 54.6 | 26.6 | 77.3 | 95.1 | 9.2 | 4,772,846 | Cape Town |
| 56 | Longyan | CN | 54.6 | 48.2 | 46.1 | 95.7 | 21.5 | 1,025,087 | Longyan |
| 57 | Kharkiv | UA | 54.5 | 38.5 | 59.7 | 95.4 | 14.9 | 1,421,125 | Kharkiv |
| 58 | Jepara | ID | 54.1 | 42.3 | 51.9 | 98.4 | 18.4 | 1,257,912 | Jepara |
| 59 | Bandar Lampung | ID | 53.9 | 44.6 | 49.4 | 95.2 | 19.7 | 1,088,200 | Bandar Lampung |
| 60 | Fukuoka | JP | 53.8 | 34.2 | 64.1 | 95.4 | 13.2 | 1,612,392 | Fukuoka |
| 61 | Novosibirsk | RU | 53.8 | 34.7 | 62.7 | 96.5 | 13.7 | 1,612,833 | Novosibirsk |
| 62 | Voronezh | RU | 53.6 | 34.3 | 63.1 | 96.0 | 13.6 | 1,047,549 | Voronezh |
| 63 | Krasnoyarsk | RU | 53.5 | 34.7 | 62.4 | 95.1 | 13.8 | 1,090,811 | Krasnoyarsk |
| 64 | Ulsan | KR | 53.4 | 39.9 | 53.9 | 97.3 | 17.4 | 1,098,421 | Ulsan |
| 65 | Calgary | CA | 53.4 | 24.4 | 74.9 | 99.8 | 9.9 | 1,306,784 | Calgary |
| 66 | Paris | FR | 53.4 | 26.1 | 72.4 | 99.8 | 10.6 | 2,138,551 | Paris |
| 67 | Singapore | SG | 53.0 | 32.9 | 62.7 | 97.4 | 13.7 | 5,638,700 | Singapore |
| 68 | Prague | CZ | 52.6 | 30.6 | 64.1 | 99.1 | 13.2 | 1,165,581 | Prague |
| 69 | Daejeon | KR | 52.5 | 44.0 | 46.8 | 94.4 | 21.1 | 1,441,203 | Daejeon |
| 70 | Mérida | MX | 52.4 | 38.4 | 60.6 | 80.1 | 14.5 | 1,201,000 | Mérida |
| 71 | Warsaw | PL | 52.4 | 36.9 | 55.7 | 96.1 | 16.6 | 1,702,139 | Warsaw |
| 72 | Durban | ZA | 52.3 | 35.3 | 57.2 | 97.7 | 15.9 | 3,338,026 | Durban |
| 73 | Barcelona | ES | 52.3 | 34.1 | 61.4 | 91.7 | 14.2 | 1,686,208 | Barcelona |
| 74 | San Antonio | US | 52.0 | 32.3 | 74.0 | 66.4 | 10.1 | 1,526,656 | San Antonio |
| 75 | Medellín | CO | 51.9 | 38.6 | 50.8 | 98.8 | 18.9 | 1,999,979 | Medellín |
| 76 | Changwon | KR | 51.7 | 40.0 | 53.7 | 86.2 | 17.5 | 1,025,702 | Changwon |
| 77 | Birmingham | GB | 51.5 | 28.1 | 76.5 | 71.6 | 9.5 | 1,157,603 | Birmingham |
| 78 | Chicago | US | 51.5 | 18.2 | 81.1 | 93.6 | 8.3 | 2,664,452 | Chicago |
| 79 | Porto Alegre | BR | 51.5 | 29.2 | 63.5 | 97.9 | 13.4 | 1,488,252 | Porto Alegre |
| 80 | Cali | CO | 51.5 | 34.0 | 58.2 | 94.1 | 15.5 | 2,392,877 | Cali |
| 81 | Mogadishu | SO | 51.2 | 18.0 | 78.2 | 99.0 | 9.0 | 2,587,183 | Mogadishu |
| 82 | Palembang | ID | 51.2 | 43.2 | 48.7 | 83.7 | 20.1 | 1,801,367 | Palembang |
| 83 | Chelyabinsk | RU | 51.1 | 30.7 | 61.0 | 96.1 | 14.4 | 1,202,371 | Chelyabinsk |
| 84 | Fortaleza | BR | 50.9 | 19.9 | 74.6 | 98.8 | 10.0 | 2,400,000 | Fortaleza |
| 85 | Kuala Lumpur | MY | 50.8 | 44.0 | 40.2 | 98.4 | 25.3 | 1,453,975 | Kuala Lumpur |
| 86 | Bursa | TR | 50.8 | 42.5 | 46.3 | 89.3 | 21.4 | 3,101,833 | Bursa |
| 87 | Volgograd | RU | 50.8 | 25.7 | 67.9 | 94.6 | 11.9 | 1,013,533 | Volgograd |
| 88 | Caracas | VE | 50.8 | 46.1 | 47.9 | 73.1 | 20.5 | 3,000,000 | Caracas |
| 89 | Zunyi | CN | 50.5 | 48.0 | 39.2 | 85.2 | 25.9 | 2,037,775 | Honghuagang |
| 90 | Rome | IT | 50.5 | 29.4 | 61.8 | 94.3 | 14.1 | 2,318,895 | Rome |
| 91 | Quito | EC | 50.5 | 36.4 | 55.4 | 85.6 | 16.7 | 2,781,641 | Quito |
| 92 | Daegu | KR | 50.3 | 37.4 | 50.9 | 92.2 | 18.9 | 2,365,523 | Daegu |
| 93 | Rio de Janeiro | BR | 50.2 | 34.2 | 53.6 | 95.6 | 17.5 | 6,747,815 | Rio de Janeiro |
| 94 | Moscow | RU | 50.2 | 34.4 | 51.8 | 99.1 | 18.4 | 10,381,222 | Moscow |
| 95 | San Diego | US | 50.2 | 19.9 | 72.7 | 98.3 | 10.5 | 1,404,452 | San Diego |
| 96 | Huế | VN | 50.0 | 34.1 | 52.4 | 97.4 | 18.1 | 1,380,000 | Huế |
| 97 | Omsk | RU | 50.0 | 26.2 | 63.4 | 97.8 | 13.5 | 1,172,070 | Omsk |
| 98 | Tijuana | MX | 49.8 | 18.5 | 74.5 | 96.4 | 10.0 | 1,922,523 | Tijuana |
| 99 | Belo Horizonte | BR | 49.7 | 32.5 | 61.5 | 79.4 | 14.2 | 2,721,564 | Belo Horizonte |
| 100 | Belgrade | RS | 49.5 | 33.6 | 51.8 | 96.9 | 18.4 | 1,273,651 | Belgrade |

## 7. What not to claim

- Do not claim the score is citywide canopy. It is a point sample plus a
  15 km landscape ring.
- Do not claim a park count is park area, park access or park quality.
- Do not claim water access is water quality, beach quality or swimming safety.
- Do not claim PM2.5 is the only air pollutant that matters; NO2 and ozone are
  missing.
- Do not claim a 0.1-point difference is meaningful; the weights and source
  errors are larger than that.
- Do not claim a city is “the greenest” without naming the universe (city
  proper vs urban centre) and the weights.
- Do not put either score in `final_score`.

## 8. Reproduction

```bash
python bootstrap_inland_water.py      # nearest lake + nearest large lake
python bootstrap_rivers.py            # nearest Strahler >= 4 river
python bootstrap_land_cover.py        # land-only vegetation view
python bootstrap_green_score.py       # greenery + environment views
python reports/generate_rankings.py \
  --db-url "postgresql:///travel_global?host=/var/run/postgresql&port=5432"
```

Outputs:

- `reports/greenery_top100.csv` and `.md`
- `reports/environment_top100.csv` and `.md`
- `reports/urban_scores_all_407.csv` — all raw measurements and scores
- `reports/excluded_1m.csv` — excluded million-plus cities and reasons

## 9. Remaining limitations

1. **No citywide zonal statistic.** The 1 km and 15 km discs are point
   buffers, not the GHSL urban-centre polygon. This is the main limitation.
2. **Land cover is 5,000 cities only.** The other 8,101 cities have no
   vegetation score. Expanding WorldCover coverage is the obvious next step.
3. **Overture park coverage is uneven.** 72 million-plus cities are unscored;
   China and Africa are under-represented. Park area and access are missing.
4. **PM2.5 only, 2024 only, 0.1° grid.** No NO2, ozone, PM10 or ultrafine
   particles; no multi-year average; no neighbourhood resolution.
5. **Water is access, not quality.** No fecal coliform, nutrient, chemical or
   beach-safety data. River/lake thresholds are documented but arbitrary.
6. **Boundary fragmentation.** GHSL centres split Fort Worth, Ottawa and
   others; city-proper populations mix communes and prefectures. The 25%
   filter removes the worst cases but does not solve the general problem.
7. **No formal uncertainty or independent validation.** Weight sensitivity is
   the available band. A future version should compare against municipal
   canopy inventories and a second land-cover product.
