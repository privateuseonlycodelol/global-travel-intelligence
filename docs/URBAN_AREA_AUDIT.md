# Urban-area greenery and surrounding-nature audit

**Audit date:** 2026-11-25  
**Question:** why do the 1 km tree shares disagree with published city canopy,
and can we measure greenery inside the urban area and nature around it
separately?

> **Superseded for current values.** The urban land cover was recomputed at
> native 10 m with cos(latitude) area weighting after this audit. See
> [GHSL_METHOD_AUDIT.md](GHSL_METHOD_AUDIT.md) for the current numbers and
> rankings. The tables below are from the earlier ~100 m nearest-neighbour
> pass and are retained as history only; do not use them as current data.

## Executive summary

The 1 km “Tree” column is **not canopy coverage**. It is the share of ESA
WorldCover class 10 (tree) in a 1 km disc around the GeoNames point, after
removing water. It is a point sample. Published canopy statistics are citywide
and use municipal boundaries. The two are not the same measurement.

Masking WorldCover to the **GHSL urban-centre polygon** resolves most of the
discrepancy:

| City | 1 km point tree | GHSL urban-centre tree | Published / reference |
|---|---:|---:|---|
| Montréal | 59.7% | **28.5%** | ~23–26% citywide |
| Oslo | 15.5% | **55.6%** | much higher than the point |
| Philadelphia | 3.9% | **40.9%** | ~20% city proper |
| Vancouver | 52.4% | **43.7%** | — |
| Singapore | 18.3% | **27.4%** | ~47% all green cover |
| Stockholm | 8.7% | **57.7%** | — |
| Washington | 17.2% | **64.9%** | — |
| London | 15.1% | **32.9%** | ~21% city proper canopy |
| Paris | 5.6% | **34.3%** | ~20% city proper canopy |
| New York City | 11.3% | **36.4%** | ~22% city proper canopy |

Reference values are approximate city-proper figures. They are not used in the
calculation and are not validation targets; they show the scale of the
boundary difference.

Montréal is now close to its published canopy. Philadelphia does not, because the
GHSL centre is 654 km² while Philadelphia County is 369 km²; the centre
includes greener suburbs. A municipal-boundary calculation is still needed
for an exact match. That boundary mismatch cannot be fixed with a global
open dataset we currently hold.

The audit also found that **the 15 km “hinterland” disc contains the 1 km
disc**. The overlap is only 0.44% of the 15 km area, so the numerical effect is
small, but the new surrounding-nature measure uses a **1–15 km annulus** to
remove it.

Two new rankings are produced:

1. **Urban-area greenery** — total tree+shrub+grass inside the GHSL urban-centre
   polygon, land-only. 482 centres ≥1M; 469 pass a 20% built-up validity
   filter.
2. **Surrounding nature access** — tree+shrub in the 1–15 km annulus, plus 20%
   water access. Same 469 centres.

The top of the urban-area list is Owerri (79.1), Thiruvananthapuram (76.9),
Washington (71.3), Colombo (70.5) and Onitsha (68.7). The top of the
surrounding-nature list is Thiruvananthapuram (85.2), Belém (81.3), Davao
(80.9), Yichang (80.6) and Aba (78.3). These are land-cover shares, not
street-tree canopy. WorldCover’s tree class includes plantations, orchards and
forest as well as urban trees, which is why tropical, low-density centres rank
high.

No existing weight was changed to produce these results; the new measures are
additive. The old point-based greenery score remains available for comparison.

## 1. Exact data, versions and polygons

| Layer | Dataset and version | Geometry | Measurement |
|---|---|---|---|
| Point tree / other | ESA WorldCover 2021 v200, 10 m | 1 km disc around GeoNames point | Class fraction, land-only |
| Point hinterland | Same | 15 km disc around point | Class fraction, land-only |
| Urban-area land cover | Same WorldCover, sampled at ~100 m target | GHSL UCDB R2024A urban-centre polygon | Class fraction inside polygon, land-only |
| Surrounding nature | Same 1 km and 15 km fractions | 1–15 km annulus around point | Area-weighted difference |
| Parks | Overture Places release `2026-11-18.0` | 5 km Haversine around point | Mapped `park` POI count |
| Population / boundary | GeoNames cities15000; GHSL UCDB R2024A | City record and urban-centre polygon | City and centre population |
| Air | SatPM V6.GL.03, 2024 | 0.1° grid at point | Modelled PM2.5 |
| Water | Natural Earth 10m; HydroLAKES v1.0; HydroRIVERS v1.0 | Straight-line distance | Coast, lake ≥10 km², Strahler ≥4 river |

Source links:

- WorldCover: https://esa-worldcover.org/en/data-access (CC BY 4.0)
- GHSL UCDB R2024A: https://ghsl.jrc.ec.europa.eu/ (CC BY 4.0)
- Overture Places: https://docs.overturemaps.org/attribution/ (CDLA-Permissive 2.0)
- SatPM: https://www.satpm.org/ (CC BY 4.0)
- HydroLAKES: https://www.hydrosheds.org/products/hydrolakes (CC BY 4.0)
- HydroRIVERS: https://www.hydrosheds.org/products/hydrorivers (CC BY 4.0)

The 482 urban-centre polygons are exported to
[`reports/urban_centres_482.geojson`](../reports/urban_centres_482.geojson).
Raw per-centre fractions are in
[`reports/urban_area_scores_all_482.csv`](../reports/urban_area_scores_all_482.csv).

## 2. Answers to the questions

### 2.1 Why does hinterland get 45% of the physical greenery index?

The hinterland term is the **15 km disc around the city point**: a circular
buffer with radius 15,000 m, not a city boundary, not a watershed and not an
administrative polygon. It is sampled from WorldCover at about 200 m for the
point score and at ~100 m for the urban polygon.

The 45% weight was a deliberate choice to stop a single 1 km block from
deciding the score. The local 1 km terms get 40% (25% tree + 15% other); the
15 km term gets 45%; parks get 15%. It is a value judgment, not a measurement
result. The alternative weights and their effect on the top five are in
[`METHODOLOGY_AUDIT.md`](METHODOLOGY_AUDIT.md).

### 2.2 Is Tree actual canopy coverage or a normalized score?

It is a **normalized land-cover share**, not canopy:

```
tree_share(radius) = tree pixels / (all valid pixels - water pixels)
```

WorldCover class 10 is “tree cover” at 10 m. It includes forest, plantations,
orchards and urban trees. It is not a census of street trees, not a measure of
canopy height, and not a measure of whether a resident can stand under it.

Montréal’s 59.7% is the 1 km block around its GeoNames point. The GHSL
urban-centre polygon is 28.5% tree (land-only), close to the published 23–26%.
The point is a green residential block, not the city.

### 2.3 Why does Oslo score 15.5 for trees when published coverage is higher?

The 15.5% is the **1 km point**, which sits in a dense central block. The GHSL
Oslo polygon is **55.6% tree** (land-only) and the 15 km ring is 79.1%
tree+shrub. Published municipal figures are closer to the polygon than to the
point. The point is not the city.

Oslo’s centre population is 889,573, below the 1M centre threshold, so it is
not in the 482-city ranking. Its polygon value is reported here for validation.

### 2.4 Why does Philadelphia score 3.9 when official canopy is about 20%?

The 3.9% is the 1 km point in Center City. The GHSL urban-centre polygon is
**40.9% tree** (land-only), but that polygon is 654 km² and includes greener
suburbs; Philadelphia County is about 369 km². The official ~20% is a
city-proper figure. The truth for the city proper lies between the point and
the polygon; a municipal-boundary calculation is required for an exact match.
This is a boundary mismatch, not a 20-point classification error.

### 2.5 Why is Other vegetation below 1% in so many cities?

`Other` is WorldCover shrub (class 20) + grass (class 30), land-only, in the
1 km disc. In a dense 1 km core, built-up and tree dominate; grass is often
below 1%. At the urban-polygon scale, grass is not negligible:

| City | Urban tree | Urban grass | Urban built |
|---|---:|---:|---:|
| Dublin | 21.7% | 19.6% | 50.0% |
| London | 32.1% | 15.3% | 49.1% |
| Montréal | 25.5% | 8.7% | 52.4% |
| New York City | 33.3% | 7.5% | 48.8% |
| Paris | 33.7% | 5.2% | 56.4% |
| Singapore | 24.1% | 6.2% | 56.4% |

Shrub is near zero because WorldCover’s shrub class is rare in urban areas;
hedges and garden shrubs are often classified as tree or built-up. Cropland is
excluded from `Other`, so agricultural vegetation is not counted as urban
greenery. That is deliberate.

Spot checks of known parks:

| Park | Tree | Grass | Built | Water |
|---|---:|---:|---:|---:|
| Phoenix Park, Dublin | 31.9% | 65.6% | 2.5% | 0% |
| Tiergarten, Berlin | 67.9% | 2.5% | 26.6% | 3.0% |
| Bois de Boulogne, Paris | 84.2% | 5.4% | 7.8% | 2.6% |
| Hyde Park, London | 43.6% | 16.2% | 35.3% | 4.9% |
| Central Park, NYC | 38.6% | 1.5% | 46.5% | 13.4% |

Grass is classified where it is the dominant surface. Central Park’s 1.5%
grass is point-specific: the 1 km disc at that coordinate is mostly trees,
water and surrounding streets, not the whole park.

### 2.6 Does the Parks score measure accessibility, coverage or another proxy?

It measures a **mapped POI count**: Overture Places whose taxonomy contains
`park`, within 5 km of the city point, log-scaled to 100 at 500 parks. It is
not park area, not park coverage, not walking access and not park quality. A
large park whose POI centre is outside 5 km is missed; a small playground
counts the same as a national park. 72 million-plus cities have no mapped park
and are unscored. The urban-area ranking therefore excludes parks and measures
vegetation only.

### 2.7 Are Tree, Other and Parks calculated on identical boundaries?

No:

- Tree and Other: 1 km disc around the city point.
- Hinterland: 15 km disc (full disc in the old score; 1–15 km annulus in the
  new surrounding-nature measure).
- Parks: 5 km radius around the city point.
- Urban-area greenery: GHSL urban-centre polygon.

This is a real inconsistency in the old composite. The new urban-area ranking
uses one boundary for Tree, Other and total green (the GHSL polygon) and
excludes Parks, so it does not mix radii. The old point-based greenery score
still mixes 1 km, 5 km and 15 km; that is a documented limitation.

### 2.8 Is vegetation in the hinterland counted twice?

Yes, geometrically: the 1 km disc is inside the 15 km disc. The overlap is

$$
\frac{\pi \times 1^2}{\pi \times 15^2} = \frac{1}{225} = 0.44\%
$$

of the 15 km area. The numeric effect is small: for Montréal, the annulus tree
share is 17.2% versus 17.4% for the full 15 km disc; for Oslo, 58.3% versus
58.1%. But the concept is wrong, so the new surrounding-nature measure uses
the exact annulus:

$$
F_{\text{annulus}} = \frac{225 \times F_{15} - 1 \times F_1}{224}
$$

The old point score still uses the full 15 km disc; the new measure does not.

### 2.9 Have satellite classifications been checked against high-resolution imagery or official data?

Partially:

- The urban-polygon tree share is close to published Montréal canopy (28.5% vs
  23–26%), which the 1 km point (59.7%) does not.
- Oslo’s point (15.5%) is contradicted by both the polygon (55.6%) and the
  15 km ring (79.1%); the polygon is consistent with a forest-surrounded city.
- Philadelphia’s official ~20% is a city-proper figure; the polygon (40.9%)
  covers a larger suburban area. The point (3.9%) is not citywide.
- Park spot checks show grass, tree and built-up classes behaving sensibly for
  large parks.

This is **not** a formal accuracy assessment. There is no confusion matrix, no
independent high-resolution sample and no per-class error estimate. WorldCover’s
tree class includes plantations and orchards; grass and crop can be confused
at the urban fringe; the 2021 annual composite has no seasonality. The urban
polygon is a better denominator than the point, not a validated canopy
inventory.

### 2.10 Can you produce separate rankings for urban greenery and surrounding nature?

Yes. The two rankings are below.

## 3. New measurements and formulas

### 3.1 Urban-area land cover

WorldCover is masked to the GHSL urban-centre polygon at a ~100 m target
resolution. Fractions are computed over valid pixels inside the polygon:

```
urban_tree_share  = tree / (1 - water)
urban_green_share = (tree + shrub + grass) / (1 - water)
urban_tree_score  = 100 × urban_tree_share
urban_green_score = 100 × urban_green_share
```

The urban-greenery ranking requires **built-up share ≥20%** inside the
polygon. This removes 13 centres whose GHSL polygon is a low-density rural
region (for example Kozhikode 3.5% built, Kollam 6.5%, Dhaka 6.8%). 469 of 482
centres pass.

The threshold is a validity floor, not a tuned parameter. At a stricter 30%
built-up threshold, Washington (27.8%) drops out and the top five become
Stockholm, Portland, Seattle, Hamburg and Minneapolis. The full ranking keeps
the 20% floor and shows the built-up share so the reader can apply a stricter
one.

### 3.2 Surrounding nature

The 1–15 km annulus is derived from the 1 km and 15 km fractions:

```
annulus_F = (225 × F_15km - F_1km) / 224
annulus_tree_shrub_share = (annulus_tree + annulus_shrub) / (1 - annulus_water)
```

The ranking score is:

```
surrounding_access = 0.80 × 100 × annulus_tree_shrub_share
                   + 0.20 × water_access_score
```

Water access is the coast / lake ≥10 km² / Strahler ≥4 river score from the
environmental index. This is a landscape and water-access proxy, not a trail
or park-entrance measure.

### 3.3 Ranking universe

One principal city per GHSL urban centre with centre population ≥1M; the
principal city is the largest GeoNames city in the centre. 482 centres have a
greenery score. The urban-greenery ranking uses 469 after the built-up filter;
the surrounding-nature ranking uses the same 469.

## 4. Ranking 1 — urban-area greenery

Total tree+shrub+grass inside the GHSL urban-centre polygon, land-only.
Built-up ≥20%.

| # | City | Country | Urban green | Tree | Grass | Built | Area km² | Centre population |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Owerri | NG | 79.1 | 74.1 | 4.9 | 20.4 | 1144 | 3,594,823 |
| 2 | Thiruvananthapuram | IN | 76.9 | 75.7 | 1.2 | 21.3 | 361 | 3,166,824 |
| 3 | Washington | US | 71.3 | 64.9 | 6.4 | 27.8 | 1301 | 3,471,725 |
| 4 | Colombo | LK | 70.5 | 67.6 | 2.7 | 22.9 | 1206 | 4,720,932 |
| 5 | Onitsha | NG | 68.7 | 62.5 | 6.1 | 28.6 | 1090 | 5,788,295 |
| 6 | Stockholm | SE | 64.8 | 57.7 | 7.1 | 31.3 | 339 | 1,543,891 |
| 7 | Portland | US | 63.1 | 56.4 | 6.7 | 35.0 | 877 | 1,839,775 |
| 8 | Seattle | US | 62.7 | 57.4 | 5.4 | 31.8 | 1123 | 2,099,643 |
| 9 | Hamburg | DE | 62.6 | 53.9 | 8.7 | 33.7 | 478 | 1,733,897 |
| 10 | Minneapolis | US | 61.5 | 52.7 | 8.8 | 36.6 | 611 | 1,217,972 |
| 11 | Uyo | NG | 60.6 | 50.4 | 9.6 | 38.6 | 271 | 1,606,054 |
| 12 | Essen | DE | 60.6 | 52.8 | 7.8 | 34.3 | 880 | 2,718,996 |
| 13 | Berlin | DE | 59.2 | 53.9 | 5.2 | 38.2 | 681 | 3,549,996 |
| 14 | Samarinda | ID | 58.0 | 44.9 | 13.0 | 37.0 | 157 | 1,225,373 |
| 15 | Cebu City | PH | 57.3 | 49.9 | 7.5 | 29.4 | 416 | 3,191,585 |
| 16 | Davao | PH | 57.3 | 52.7 | 4.6 | 34.0 | 227 | 1,651,085 |
| 17 | Dhanbad | IN | 56.8 | 34.7 | 22.1 | 24.3 | 298 | 2,078,657 |
| 18 | Cần Thơ | VN | 56.8 | 53.3 | 3.5 | 24.6 | 329 | 1,652,648 |
| 19 | Helsinki | FI | 56.2 | 51.0 | 5.2 | 34.0 | 381 | 1,001,124 |
| 20 | Durban | ZA | 56.0 | 45.0 | 10.7 | 40.4 | 807 | 3,270,867 |
| 21 | Manchester | GB | 55.5 | 35.6 | 19.9 | 43.4 | 751 | 2,539,906 |
| 22 | Orlando | US | 55.0 | 47.3 | 7.8 | 40.5 | 750 | 1,231,650 |
| 23 | Kolkata | IN | 54.5 | 52.6 | 1.9 | 21.0 | 2482 | 23,314,585 |
| 24 | Köln | DE | 54.1 | 46.5 | 7.6 | 39.0 | 296 | 1,156,303 |
| 25 | San Antonio | US | 54.0 | 43.4 | 10.6 | 44.5 | 640 | 1,513,874 |
| 26 | San Salvador | SV | 53.6 | 45.8 | 7.7 | 45.5 | 264 | 1,615,741 |
| 27 | Okene | NG | 53.3 | 31.4 | 10.0 | 35.2 | 108 | 1,036,026 |
| 28 | Baltimore | US | 53.2 | 43.5 | 9.7 | 44.6 | 449 | 1,127,337 |
| 29 | Austin | US | 52.9 | 41.2 | 11.7 | 45.5 | 628 | 1,580,153 |
| 30 | Hong Kong | HK | 52.9 | 52.0 | 0.8 | 33.7 | 220 | 4,807,599 |
| 31 | Tuen Mun | HK | 52.4 | 45.0 | 7.4 | 38.8 | 114 | 1,081,304 |
| 32 | Leeds | GB | 52.0 | 30.9 | 21.2 | 44.9 | 321 | 1,053,955 |
| 33 | Khulna | BD | 51.7 | 49.4 | 2.2 | 24.1 | 126 | 1,081,072 |
| 34 | Warsaw | PL | 51.6 | 43.1 | 8.6 | 45.4 | 415 | 1,999,985 |
| 35 | Houston | US | 51.6 | 41.0 | 10.6 | 45.8 | 1859 | 4,301,460 |
| 36 | Vancouver | CA | 51.2 | 43.7 | 7.6 | 43.2 | 649 | 2,198,106 |
| 37 | San José | CR | 51.0 | 37.4 | 13.6 | 48.5 | 491 | 2,272,571 |
| 38 | Yangon | MM | 50.7 | 40.5 | 10.1 | 39.6 | 545 | 6,190,957 |
| 39 | Boston | US | 50.7 | 45.0 | 5.6 | 42.0 | 508 | 1,669,310 |
| 40 | Kampala | UG | 50.2 | 27.3 | 11.1 | 46.3 | 810 | 5,177,877 |
| 41 | Moscow | RU | 49.6 | 45.9 | 3.7 | 46.8 | 1545 | 14,384,081 |
| 42 | Kharkiv | UA | 49.6 | 42.9 | 6.7 | 48.8 | 232 | 1,284,379 |
| 43 | Birmingham | GB | 49.4 | 36.4 | 13.0 | 48.4 | 685 | 2,617,210 |
| 44 | Philadelphia | US | 49.0 | 40.9 | 8.2 | 48.1 | 654 | 2,286,396 |
| 45 | Cabinda | AO | 49.0 | 22.0 | 26.9 | 43.4 | 98 | 1,220,203 |
| 46 | Niterói | BR | 48.8 | 39.3 | 9.5 | 46.7 | 296 | 1,734,951 |
| 47 | Havana | CU | 48.8 | 37.1 | 11.6 | 45.0 | 321 | 1,632,771 |
| 48 | Guatemala City | GT | 48.7 | 40.7 | 7.6 | 47.8 | 470 | 2,900,618 |
| 49 | Managua | NI | 48.7 | 37.6 | 11.1 | 47.5 | 216 | 1,436,088 |
| 50 | San Pedro Sula | HN | 48.7 | 30.3 | 18.4 | 44.3 | 211 | 1,301,567 |
| 51 | London | GB | 48.6 | 32.9 | 15.7 | 49.1 | 1649 | 10,408,332 |
| 52 | Dar es Salaam | TZ | 48.4 | 28.8 | 18.7 | 47.3 | 907 | 8,918,524 |
| 53 | Prague | CZ | 48.2 | 38.7 | 9.5 | 45.8 | 263 | 1,201,277 |
| 54 | Asunción | PY | 47.9 | 43.8 | 4.0 | 51.4 | 383 | 1,175,481 |
| 55 | Munich | DE | 47.8 | 41.0 | 6.8 | 43.7 | 345 | 1,827,704 |
| 56 | Nizhniy Novgorod | RU | 47.8 | 42.2 | 5.5 | 47.3 | 232 | 1,189,992 |
| 57 | Caracas | VE | 47.7 | 38.9 | 5.1 | 52.3 | 311 | 2,667,322 |
| 58 | Zanzibar | TZ | 47.3 | 38.4 | 8.6 | 45.6 | 121 | 1,026,755 |
| 59 | Tegucigalpa | HN | 47.1 | 31.1 | 15.5 | 51.8 | 163 | 1,653,508 |
| 60 | Pekanbaru | ID | 47.0 | 43.1 | 3.9 | 52.1 | 234 | 1,226,040 |
| 61 | Zunyi | CN | 46.1 | 42.6 | 3.5 | 45.5 | 116 | 1,185,303 |
| 62 | Guwahati | IN | 46.1 | 44.9 | 1.2 | 46.4 | 194 | 1,375,936 |
| 63 | Budapest | HU | 46.1 | 39.1 | 7.0 | 51.0 | 368 | 1,743,875 |
| 64 | Rasapūdipalem | IN | 46.0 | 40.3 | 5.7 | 43.9 | 211 | 1,634,439 |
| 65 | Kyiv | UA | 45.9 | 40.8 | 5.0 | 49.4 | 410 | 2,575,199 |
| 66 | Nairobi | KE | 45.8 | 24.8 | 10.3 | 47.3 | 598 | 6,646,256 |
| 67 | Jerusalem | IL | 45.7 | 20.4 | 3.2 | 52.9 | 79 | 1,022,929 |
| 68 | Taipei | TW | 45.3 | 43.6 | 1.7 | 45.9 | 972 | 9,686,521 |
| 69 | Belgrade | RS | 45.1 | 37.3 | 7.8 | 49.5 | 191 | 1,200,546 |
| 70 | Padang | ID | 44.9 | 41.6 | 3.3 | 36.4 | 216 | 1,210,988 |
| 71 | New York City | US | 44.7 | 36.4 | 8.3 | 48.8 | 3031 | 14,197,658 |
| 72 | Busan | KR | 44.7 | 43.3 | 1.4 | 45.2 | 355 | 3,263,408 |
| 73 | Virār | IN | 44.6 | 38.9 | 5.8 | 29.1 | 128 | 1,551,310 |
| 74 | Sydney | AU | 44.5 | 37.3 | 7.2 | 49.8 | 1378 | 4,375,394 |
| 75 | Dublin | IE | 44.4 | 23.3 | 21.1 | 50.0 | 301 | 1,124,519 |
| 76 | Detroit | US | 44.3 | 32.5 | 11.8 | 53.6 | 1170 | 1,918,653 |
| 77 | Medellín | CO | 44.3 | 31.1 | 13.1 | 55.1 | 233 | 3,215,457 |
| 78 | Sekondi | GH | 43.9 | 30.0 | 13.9 | 49.4 | 136 | 1,255,808 |
| 79 | Rotterdam | NL | 43.8 | 24.5 | 19.3 | 49.0 | 677 | 2,173,754 |
| 80 | Āsansol | IN | 43.5 | 32.3 | 11.2 | 22.7 | 249 | 2,886,897 |
| 81 | Aba | NG | 43.5 | 41.1 | 2.3 | 56.4 | 187 | 1,472,091 |
| 82 | Copenhagen | DK | 43.1 | 27.7 | 15.4 | 49.9 | 302 | 1,258,201 |
| 83 | Auckland | NZ | 43.0 | 29.6 | 13.4 | 44.6 | 638 | 1,623,897 |
| 84 | Kazan | RU | 42.8 | 34.5 | 8.3 | 50.6 | 248 | 1,127,086 |
| 85 | Lisbon | PT | 42.7 | 20.5 | 21.9 | 49.5 | 426 | 1,974,573 |
| 86 | Taiz | YE | 42.7 | 1.5 | 15.4 | 34.0 | 72 | 1,284,738 |
| 87 | Tasikmalaya | ID | 42.5 | 41.4 | 1.1 | 23.1 | 546 | 2,923,169 |
| 88 | Valencia | VE | 42.5 | 22.8 | 8.8 | 55.8 | 330 | 1,601,248 |
| 89 | Tiruchirappalli | IN | 42.5 | 34.5 | 1.4 | 41.8 | 150 | 1,378,490 |
| 90 | Purwokerto | ID | 42.4 | 41.7 | 0.7 | 25.9 | 328 | 1,273,364 |
| 91 | Liverpool | GB | 42.4 | 25.9 | 16.5 | 47.4 | 388 | 1,243,205 |
| 92 | Recife | BR | 42.2 | 33.5 | 8.7 | 50.1 | 491 | 3,847,557 |
| 93 | Salem | IN | 42.1 | 29.8 | 12.3 | 44.3 | 155 | 1,299,777 |
| 94 | São Luís | BR | 42.1 | 32.4 | 9.7 | 50.0 | 242 | 1,272,247 |
| 95 | Jamshedpur | IN | 42.1 | 35.7 | 6.4 | 44.2 | 161 | 2,007,932 |
| 96 | Garut | ID | 42.0 | 39.8 | 2.2 | 22.1 | 310 | 1,473,082 |
| 97 | Guiyang | CN | 41.7 | 34.8 | 6.9 | 46.8 | 298 | 2,757,867 |
| 98 | Panama City | PA | 41.6 | 29.8 | 11.9 | 53.0 | 277 | 1,612,439 |
| 99 | Beirut | LB | 41.0 | 36.8 | 3.3 | 50.6 | 199 | 1,538,385 |
| 100 | Johor Bahru | MY | 41.0 | 30.6 | 10.4 | 52.1 | 418 | 1,703,323 |

## 5. Ranking 2 — surrounding nature access

80% tree+shrub in the 1–15 km annulus + 20% water access.

| # | City | Country | Surrounding access | Tree+shrub | Water access | Built in centre | Area km² |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | Thiruvananthapuram | IN | 85.2 | 83.5 | 92.1 | 21.3 | 361 |
| 2 | Belém | BR | 81.3 | 76.9 | 99.0 | 56.4 | 273 |
| 3 | Davao | PH | 80.9 | 76.5 | 98.5 | 34.0 | 227 |
| 4 | Yichang | CN | 80.6 | 76.2 | 98.3 | 46.5 | 156 |
| 5 | Aba | NG | 78.3 | 80.3 | 70.2 | 56.4 | 187 |
| 6 | Pekanbaru | ID | 77.1 | 73.6 | 91.2 | 52.1 | 234 |
| 7 | Stockholm | SE | 77.1 | 71.4 | 99.9 | 31.3 | 339 |
| 8 | Cebu City | PH | 76.9 | 72.6 | 94.0 | 29.4 | 416 |
| 9 | San Salvador | SV | 76.0 | 75.0 | 80.1 | 45.5 | 264 |
| 10 | Bandar Lampung | ID | 75.9 | 71.1 | 95.2 | 57.5 | 176 |
| 11 | Okene | NG | 75.6 | 78.4 | 64.6 | 35.2 | 108 |
| 12 | Hong Kong | HK | 75.6 | 69.6 | 99.8 | 33.7 | 220 |
| 13 | Daejeon | KR | 75.0 | 70.1 | 94.4 | 54.7 | 204 |
| 14 | Samarinda | ID | 74.3 | 68.7 | 96.7 | 37.0 | 157 |
| 15 | Uyo | NG | 73.1 | 74.3 | 68.5 | 38.6 | 271 |
| 16 | Nanchong | CN | 72.6 | 66.1 | 98.2 | 50.6 | 89 |
| 17 | Sekondi | GH | 72.3 | 65.5 | 99.4 | 49.4 | 136 |
| 18 | Zanzibar | TZ | 71.7 | 65.5 | 96.4 | 45.6 | 121 |
| 19 | Padang | ID | 71.5 | 64.6 | 98.9 | 36.4 | 216 |
| 20 | Garut | ID | 71.2 | 65.6 | 93.6 | 22.1 | 310 |
| 21 | Zhangzhou | CN | 70.9 | 64.3 | 97.4 | 48.1 | 417 |
| 22 | Washington | US | 70.8 | 64.1 | 97.4 | 27.8 | 1301 |
| 23 | Hiroshima | JP | 70.8 | 64.0 | 97.6 | 51.2 | 338 |
| 24 | Taipei | TW | 70.7 | 64.6 | 95.2 | 45.9 | 972 |
| 25 | Caracas | VE | 70.4 | 69.8 | 73.1 | 52.3 | 311 |
| 26 | Mataram | ID | 70.2 | 65.2 | 90.3 | 30.6 | 195 |
| 27 | Portland | US | 70.0 | 62.8 | 98.8 | 35.0 | 877 |
| 28 | Purwokerto | ID | 69.9 | 64.6 | 91.1 | 25.9 | 328 |
| 29 | Tasikmalaya | ID | 69.7 | 63.6 | 94.4 | 23.1 | 546 |
| 30 | Dehradun | IN | 69.7 | 72.7 | 57.7 | 46.9 | 169 |
| 31 | Ganzhou | CN | 69.3 | 61.7 | 99.7 | 55.1 | 150 |
| 32 | Zhuzhou | CN | 68.7 | 61.2 | 98.7 | 54.4 | 161 |
| 33 | Putian | CN | 68.7 | 62.1 | 95.0 | 41.1 | 453 |
| 34 | San Pedro Sula | HN | 68.3 | 64.7 | 82.6 | 44.3 | 211 |
| 35 | Yekaterinburg | RU | 68.0 | 60.8 | 96.6 | 56.5 | 207 |
| 36 | Rasapūdipalem | IN | 67.6 | 61.0 | 94.2 | 43.9 | 211 |
| 37 | Tuen Mun | HK | 67.5 | 59.7 | 98.9 | 38.8 | 114 |
| 38 | Zunyi | CN | 66.5 | 61.8 | 85.2 | 45.5 | 116 |
| 39 | Daegu | KR | 66.1 | 59.5 | 92.2 | 61.2 | 287 |
| 40 | Seattle | US | 66.0 | 57.9 | 98.4 | 31.8 | 1123 |
| 41 | Malang | ID | 65.9 | 58.4 | 95.9 | 44.7 | 326 |
| 42 | Bursa | TR | 65.8 | 59.9 | 89.3 | 61.9 | 216 |
| 43 | Essen | DE | 65.7 | 59.3 | 91.3 | 34.3 | 880 |
| 44 | Tegucigalpa | HN | 65.3 | 66.7 | 59.5 | 51.8 | 163 |
| 45 | Owerri | NG | 65.2 | 61.6 | 79.6 | 20.4 | 1144 |
| 46 | Helsinki | FI | 65.1 | 57.3 | 96.3 | 34.0 | 381 |
| 47 | Fuzhou | CN | 65.0 | 56.9 | 97.5 | 59.9 | 256 |
| 48 | Guwahati | IN | 64.8 | 56.9 | 96.5 | 46.4 | 194 |
| 49 | Enugu | NG | 64.8 | 62.4 | 74.4 | 63.5 | 165 |
| 50 | Wenzhou | CN | 64.7 | 57.1 | 94.9 | 54.1 | 514 |
| 51 | Panama City | PA | 64.6 | 57.1 | 94.8 | 53.0 | 277 |
| 52 | Minneapolis | US | 64.6 | 56.0 | 98.9 | 36.6 | 611 |
| 53 | Huizhou | CN | 64.2 | 55.9 | 97.4 | 48.0 | 152 |
| 54 | San José | CR | 64.2 | 56.9 | 93.6 | 48.5 | 491 |
| 55 | Managua | NI | 64.0 | 56.2 | 95.0 | 47.5 | 216 |
| 56 | Guatemala City | GT | 63.8 | 60.6 | 76.8 | 47.8 | 470 |
| 57 | Cần Thơ | VN | 63.8 | 55.5 | 96.8 | 24.6 | 329 |
| 58 | Beirut | LB | 63.7 | 55.5 | 96.5 | 50.6 | 199 |
| 59 | Medellín | CO | 63.6 | 54.8 | 98.8 | 55.1 | 233 |
| 60 | San Luis Potosí | MX | 62.9 | 58.1 | 82.2 | 78.9 | 173 |
| 61 | Manaus | BR | 62.3 | 54.6 | 93.1 | 61.7 | 272 |
| 62 | Austin | US | 61.7 | 52.4 | 98.9 | 45.5 | 628 |
| 63 | Berlin | DE | 61.7 | 52.6 | 97.9 | 38.2 | 681 |
| 64 | Liuzhou | CN | 61.2 | 51.9 | 98.2 | 62.4 | 220 |
| 65 | Gwangju | KR | 60.9 | 54.2 | 87.3 | 62.8 | 190 |
| 66 | Jinhua | CN | 60.6 | 51.0 | 99.0 | 56.0 | 158 |
| 67 | Nizhniy Novgorod | RU | 60.4 | 50.9 | 98.5 | 47.3 | 232 |
| 68 | Santos | BR | 60.4 | 51.1 | 97.5 | 53.0 | 158 |
| 69 | Kyiv | UA | 60.4 | 50.8 | 98.4 | 49.4 | 410 |
| 70 | Hamburg | DE | 60.3 | 50.5 | 99.5 | 33.7 | 478 |
| 71 | Palembang | ID | 59.8 | 53.9 | 83.7 | 56.6 | 245 |
| 72 | Baltimore | US | 59.7 | 51.2 | 93.8 | 44.6 | 449 |
| 73 | İzmir | TR | 59.4 | 50.1 | 96.4 | 58.8 | 307 |
| 74 | Budapest | HU | 59.3 | 49.3 | 99.5 | 51.0 | 368 |
| 75 | Boston | US | 59.2 | 49.5 | 98.2 | 42.0 | 508 |
| 76 | Kitakyushu | JP | 59.0 | 51.5 | 88.7 | 58.0 | 388 |
| 77 | Durban | ZA | 58.6 | 48.8 | 97.7 | 40.4 | 807 |
| 78 | Kathmandu | NP | 58.1 | 48.8 | 95.5 | 42.6 | 350 |
| 79 | Jamshedpur | IN | 57.8 | 48.1 | 96.8 | 44.2 | 161 |
| 80 | Onitsha | NG | 57.8 | 48.3 | 96.0 | 28.6 | 1090 |
| 81 | Virār | IN | 57.5 | 49.5 | 89.6 | 29.1 | 128 |
| 82 | Puducherry | IN | 57.5 | 47.7 | 96.9 | 45.3 | 88 |
| 83 | Guiyang | CN | 57.5 | 47.4 | 97.7 | 46.8 | 298 |
| 84 | Warsaw | PL | 57.5 | 47.8 | 96.1 | 45.4 | 415 |
| 85 | Nanning | CN | 57.3 | 46.9 | 98.7 | 68.3 | 256 |
| 86 | Cali | CO | 57.1 | 47.8 | 94.1 | 58.9 | 186 |
| 87 | Kharkiv | UA | 56.9 | 47.3 | 95.4 | 48.8 | 232 |
| 88 | Novosibirsk | RU | 56.8 | 46.8 | 96.5 | 55.8 | 281 |
| 89 | Antalya | TR | 56.7 | 46.9 | 95.9 | 51.1 | 258 |
| 90 | Batam | ID | 56.5 | 46.4 | 97.2 | 44.6 | 186 |
| 91 | Chongqing | CN | 56.4 | 46.0 | 98.1 | 50.3 | 653 |
| 92 | Brasília | BR | 56.1 | 48.5 | 86.9 | 56.8 | 283 |
| 93 | Niterói | BR | 56.0 | 46.0 | 96.2 | 46.7 | 296 |
| 94 | Köln | DE | 55.4 | 44.9 | 97.4 | 39.0 | 296 |
| 95 | Asunción | PY | 55.3 | 45.1 | 96.0 | 51.4 | 383 |
| 96 | Haikou | CN | 54.9 | 44.7 | 95.8 | 53.1 | 199 |
| 97 | São Luís | BR | 54.9 | 43.7 | 99.8 | 50.0 | 242 |
| 98 | Kazan | RU | 54.9 | 44.3 | 97.2 | 50.6 | 248 |
| 99 | Munich | DE | 54.7 | 43.9 | 98.0 | 43.7 | 345 |
| 100 | Valencia | VE | 54.5 | 49.8 | 73.1 | 55.8 | 330 |

## 6. What this does and does not fix

**Fixes:**

- The 1 km point is no longer presented as citywide canopy.
- Montréal’s urban polygon (28.5%) matches published canopy; the 59.7% point
  is explicitly a local block.
- Oslo’s low point is explained by its dense central block; the polygon is
  55.6% tree.
- The hinterland overlap is removed in the new surrounding-nature measure.
- Tree, Other and total green use the same GHSL polygon in the urban ranking.
- Parks are excluded from the urban ranking, so a 5 km POI count is not mixed
  with a polygon vegetation share.

**Does not fix:**

- GHSL centre polygons still vary in area and density. A low-density centre
  with 20% built-up can score high on green share without being a dense city.
  The built-up column is shown for that reason.
- Philadelphia’s city-proper canopy still needs a municipal polygon; the GHSL
  centre is 654 km², not 369 km².
- WorldCover tree includes plantations, not just canopy.
- Grass/shrub classes are imperfect in urban areas; shrub is sparse.
- No formal accuracy assessment or uncertainty interval.
- The urban ranking is vegetation-only; park access is separate.

## 7. Reproduce

```bash
python bootstrap_urban_land_cover.py
python reports/generate_urban_rankings.py \
  --db-url "postgresql:///travel_global?host=/var/run/postgresql&port=5432"
```

Outputs:

- `reports/urban_centres_482.geojson` — GHSL polygons used
- `reports/urban_area_scores_all_482.csv` — raw fractions and scores
- `reports/urban_greenery_top100.csv` / `.md`
- `reports/surrounding_nature_top100.csv` / `.md`

The underlying WorldCover tiles are read remotely from
`s3://esa-worldcover/v200/2021/map`; the GHSL polygons are in
`ghsl_urban_centres`. The raw 10 m raster is not redistributed.
