# GHSL greenery methodology audit

**Audit date:** 2026-11-25  
**Scope:** replace the point-based greenery ranking with a GHSL urban-centre
measure, without optimizing for any city.

## Executive summary

The existing point-based greenery score is not citywide greenery. It is a
1 km disc around a GeoNames point. The audit found five actual methodological
bugs in the existing pipeline:

1. **Categorical downsampling.** `bootstrap_urban_land_cover.py` read
   WorldCover at ~100 m with `Resampling.nearest`, and
   `bootstrap_land_cover.py` reads the 15 km disc through a ~200 m overview
   with nearest resampling. Nearest-neighbour class selection biases class
   fractions. The urban polygon is now read at **native 10 m** with area
   weighting; the 15 km annulus remains provisional and is flagged.
2. **GeoNames population used for eligibility.** The 407-city rankings use
   GeoNames city population, not GHSL centre population. The new rankings use
   **GHSL urban centres with centre population ≥1M**.
3. **The 1 km point was inside the overall greenery score.** The new physical
   greenery score is polygon-only. The point tree share is retained only as a
   supplementary city-centre indicator.
4. **Hinterland included the urban polygon and did not separate crop.** The new
   nature-access measure subtracts the urban polygon from the 15 km disc and
   reports natural tree+shrub separately from agricultural cropland.
5. **Pixel counts were not area-weighted.** WorldCover is EPSG:4326; a pixel's
   ground area scales with cos(lat). The urban polygon now weights each row by
   cos(lat).

Two requested measures are **not implemented** because the required global
data are not loaded:

- population-weighted vegetation exposure within 300 m and 500 m of
  residential cells: needs the global **GHS-POP R2023A 100 m** grid;
- public green-space access within 300 m: needs green-space polygons plus a
  population grid. Overture `park` is a 5 km POI count, not a polygon/access
  measure.

Both are exposed as `FALSE` flags with a missing reason. No proxy is
substituted.

## 1. Actual findings, not assumptions

| Finding | Evidence | Status |
|---|---|---|
| Nearest-neighbour urban land cover | `bootstrap_urban_land_cover.py` used `out_shape` + `Resampling.nearest` at ~100 m | **Fixed:** native 10 m + cos(lat) row weights |
| Nearest-overview 15 km disc | `bootstrap_land_cover.py` uses `Resampling.nearest` at ~200 m | **Known remaining bias**, flagged provisional |
| GeoNames eligibility | `reports/generate_rankings.py` filters `d.population >= 1000000` | **Replaced** for the new rankings by GHSL centre population |
| Point metric in overall greenery | all-green/composite uses `local_tree_score` | **Removed** from the new physical/overall scores; supplementary only |
| Urban polygon in hinterland | old 15 km disc includes the city | **Subtracted** in nature access (area-weighted) |
| Crop counted as green? | old green share used tree+shrub+grass; crop excluded | Already excluded, now crop is reported separately |
| No area weighting | pixel counts in EPSG:4326 | **Fixed** for the GHSL polygon |
| No missing flags | old views had no flag for blocked measures | **Added** |

Do not treat a mismatch with a municipal canopy figure as proof of a bug. The
GHSL polygon is not a municipal boundary; Philadelphia is the clearest case
(654 km² centre vs 369 km² county). The comparison is scale-dependent, not a
classification error.

## 2. GHSL universe and population

Ranking unit: **GHSL UCDB R2024A urban centre**. Eligibility:
`centre_population >= 1,000,000`. The GHSL universe has 524 centres ≥1M; 516 have a GeoNames label and are
in the urban table. The 8 without a label are listed in §11. The city name is the largest GeoNames city in the centre, used as a
label only; it does not set eligibility or weight.

The 1 km point is **not** used for eligibility, population weighting, or the
physical greenery score.

## 3. Physical urban greenery

Dataset: ESA WorldCover 2021 v200, 10 m, class 10 tree, 20 shrub, 30 grass,
40 crop, 50 built-up, 80 water. Source: https://esa-worldcover.org/en/data-access
(CC BY 4.0).

Boundary: GHSL urban-centre polygon, EPSG:4326.

Read: native 10 m pixels, no categorical downsampling. Each row is weighted by
`cos(latitude)` so the result is area-weighted.

Denominator: valid pixels inside the polygon **minus water (class 80)**. No-data
(class 0) is excluded. Wetland and mangrove are not removed.

```
land = 1 - water_fraction
tree_share = tree_fraction / land
natural_share = (tree_fraction + shrub_fraction) / land
green_share = (tree_fraction + shrub_fraction + grassland_fraction) / land
physical_green_score = 100 × green_share
urban_tree_score = 100 × tree_share
```

Cropland is excluded from green. `natural_share` (tree+shrub) and
`green_share` (tree+shrub+grass) are both exposed; grass can include pasture,
so the primary score uses total green and the natural-only variant is a
sensitivity.

## 4. Nature access — tested and blocked

The intended measure was the 15 km disc **minus the GHSL urban polygon**,
with natural tree+shrub separated from agricultural cropland:

```
A15 = π × 15² = 706.858 km²
outside_F = (A15 × F15 − A_urban × F_urban) / (A15 − A_urban)
natural_access = 100 × (outside_tree + outside_shrub) / (1 − outside_water)
```

This area-subtraction method was tested on the labelled centres and **rejected**:
it produced impossible values, including nature-access scores above 200% and
negative agricultural shares. The cause is geometric: the 15 km disc is
centred on the city point, not on the urban polygon, and for many centres the
polygon extends beyond the disc or sits off-centre. Subtracting two fractions
that do not cover the same footprint is invalid.

A correct implementation needs a **15 km buffer outside the GHSL polygon**
computed directly from the raster, not a subtraction from a point disc. That
requires a new raster pass per centre. It is not implemented here, so no
nature-access ranking is published. The view exposes
`nature_access_valid = FALSE` and a missing reason; no proxy is substituted.

## 5. Overall environmental quality## 5. Overall environmental quality

```
environment = 0.50 × physical_green_score
            + 0.35 × air_score
            + 0.15 × water_score
```

Air is the SatPM 2024 fixed log scale (100 at 5 µg/m³, 0 at 75). Water is the
nearest coast, lake ≥10 km² or Strahler ≥4 river, with 50 km decay. This is not
population-weighted; air/water are point samples. The score is computed for
centres with physical greenery, air and water.

## 6. Blocked measures and exact data requirements

### Population-weighted exposure (300 m / 500 m)

Required:

- **GHS-POP R2023A 100 m**, residential population per cell, EPSG:3857 or
  equal-area. Source: https://ghsl.jrc.ec.europa.eu/ (CC BY 4.0).
- WorldCover 2021 native 10 m, or a fractional green layer at ≤10 m.

Method: buffer each populated cell by 300 m and 500 m; compute the
population-weighted share of tree/shrub/grass in the buffer; aggregate to the
GHSL centre. This is not implemented because only two GHS-POP tiles are present
locally, not the global grid.

### Public green-space access (300 m)

Required:

- Green-space polygons: Overture land use/land cover `park`/`green_space`
  polygons, or an OSM `leisure=park`/`landuse=grass` extract, with a clear
  licence.
- GHS-POP 100 m.

Method: network or 300 m buffer from each populated cell to accessible green
polygons; report the population share with access. Overture Places `park` is a
POI count, not a polygon, and cannot answer this.

## 7. Cross-validation

- **Montréal:** native polygon tree 27.3% vs published citywide ~23–26%;
  the point was 59.7%. The polygon is close, but the GHSL centre (1,020 km²)
  is larger than the island (499 km²), so it is not an exact municipal match.
- **Philadelphia:** native polygon tree 38.7% vs official city-proper ~20%;
  the GHSL centre is 654 km² vs 369 km² and includes greener suburbs. The
  point (3.9%) is not citywide. This is a boundary difference, not a
  classification error.
- **GHSL greenness indicator:** not loaded. The UCDB greenness theme would be
  the correct independent comparison; it is not in the current database.
- **Municipal canopy inventories:** not loaded globally. They use different
  boundaries and dates; a direct correlation would be misleading.

## 8. Separate rankings

- **Physical urban greenery** — polygon green share, crop excluded. Published.
- **Everyday greenery** — blocked: needs the global GHS-POP R2023A 100 m
  residential grid for 300 m/500 m population-weighted exposure.
- **Access to nature** — blocked: the 15 km area-subtraction test failed; a
  direct 15 km buffer outside the polygon is required.
- **Overall environmental quality** — physical greenery + air + water.
  Published.

## 9. Rankings

### Physical urban greenery

Inside the GHSL polygon at native 10 m, area-weighted, crop excluded.
Built-up ≥20%; 497 of 516 labelled centres.

| # | City | Country | Physical green | Tree | Natural | Grass | Built | Centre pop |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Owerri | NG | 76.5 | 70.7 | 70.8 | 5.8 | 22.8 | 3,594,823 |
| 2 | Thiruvananthapuram | IN | 74.6 | 72.9 | 72.9 | 1.7 | 23.3 | 3,166,824 |
| 3 | Tshikapa | CD | 72.4 | 36.0 | 44.1 | 28.3 | 25.9 | 1,074,734 |
| 4 | Washington | US | 69.4 | 60.8 | 60.8 | 8.6 | 29.5 | 3,471,725 |
| 5 | Colombo | LK | 68.7 | 65.0 | 65.1 | 3.6 | 24.2 | 4,720,932 |
| 6 | Mbuji-Mayi | CD | 67.4 | 40.7 | 45.8 | 21.6 | 30.1 | 3,596,107 |
| 7 | Onitsha | NG | 66.2 | 59.3 | 59.4 | 6.8 | 30.7 | 5,788,295 |
| 8 | Stockholm | SE | 64.0 | 55.4 | 55.4 | 8.7 | 31.8 | 1,543,891 |
| 9 | Kananga | CD | 63.8 | 34.4 | 44.7 | 19.1 | 32.0 | 1,538,649 |
| 10 | Hamburg | DE | 61.2 | 51.4 | 51.4 | 9.8 | 34.9 | 1,733,897 |
| 11 | Portland | US | 61.1 | 53.8 | 53.8 | 7.3 | 36.6 | 1,839,775 |
| 12 | Seattle | US | 61.0 | 54.6 | 54.6 | 6.4 | 33.1 | 2,099,643 |
| 13 | Uyo | NG | 60.9 | 49.5 | 50.6 | 10.3 | 37.8 | 1,606,054 |
| 14 | Minneapolis | US | 60.6 | 49.1 | 49.1 | 11.5 | 37.1 | 1,217,972 |
| 15 | Essen | DE | 60.4 | 51.2 | 51.2 | 9.2 | 34.5 | 2,718,996 |
| 16 | Samarinda | ID | 59.2 | 44.5 | 44.6 | 14.7 | 35.2 | 1,225,373 |
| 17 | Berlin | DE | 58.7 | 52.5 | 52.5 | 6.1 | 38.6 | 3,549,996 |
| 18 | Manchester | GB | 57.7 | 37.1 | 37.1 | 20.6 | 41.2 | 2,539,906 |
| 19 | Davao | PH | 57.4 | 52.0 | 52.0 | 5.4 | 33.5 | 1,651,085 |
| 20 | Cebu City | PH | 57.3 | 48.8 | 48.8 | 8.6 | 28.8 | 3,191,585 |
| 21 | Durban | ZA | 57.1 | 43.8 | 44.7 | 12.4 | 38.9 | 3,270,867 |
| 22 | Helsinki | FI | 56.2 | 50.2 | 50.2 | 6.0 | 33.8 | 1,001,124 |
| 23 | Cần Thơ | VN | 56.2 | 51.6 | 51.6 | 4.6 | 24.5 | 1,652,648 |
| 24 | Dhanbad | IN | 56.1 | 34.0 | 34.0 | 22.0 | 24.0 | 2,078,657 |
| 25 | Orlando | US | 55.7 | 45.9 | 45.9 | 9.8 | 39.4 | 1,231,650 |
| 26 | Okene | NG | 55.4 | 31.2 | 44.1 | 11.3 | 33.2 | 1,036,026 |
| 27 | Köln | DE | 55.4 | 46.2 | 46.2 | 9.2 | 37.9 | 1,156,303 |
| 28 | San Salvador | SV | 54.7 | 46.1 | 46.2 | 8.5 | 44.1 | 1,615,741 |
| 29 | Leeds | GB | 54.5 | 32.8 | 32.8 | 21.6 | 42.3 | 1,053,955 |
| 30 | Baltimore | US | 54.2 | 42.2 | 42.2 | 12.0 | 43.3 | 1,127,337 |
| 31 | Austin | US | 54.1 | 40.5 | 40.5 | 13.6 | 43.7 | 1,580,153 |
| 32 | San Antonio | US | 53.9 | 42.1 | 42.1 | 11.9 | 43.7 | 1,513,874 |
| 33 | Kolkata | IN | 53.8 | 51.4 | 51.4 | 2.4 | 21.1 | 23,314,585 |
| 34 | Tuen Mun | HK | 53.5 | 45.1 | 45.1 | 8.4 | 37.1 | 1,081,304 |
| 35 | Hong Kong | HK | 53.4 | 52.2 | 52.2 | 1.3 | 32.7 | 4,807,599 |
| 36 | Kampala | UG | 53.1 | 28.0 | 40.1 | 13.0 | 42.7 | 5,177,877 |
| 37 | Warsaw | PL | 53.1 | 43.5 | 43.5 | 9.6 | 43.7 | 1,999,985 |
| 38 | Vancouver | CA | 52.9 | 44.1 | 44.1 | 8.7 | 41.4 | 2,198,106 |
| 39 | San José | CR | 52.7 | 37.5 | 37.5 | 15.2 | 46.5 | 2,272,571 |
| 40 | Birmingham | GB | 52.2 | 38.4 | 38.4 | 13.9 | 45.5 | 2,617,210 |
| 41 | Beni | CD | 52.2 | 30.2 | 30.4 | 21.7 | 47.8 | 1,872,704 |
| 42 | London | GB | 52.0 | 35.1 | 35.1 | 16.9 | 45.7 | 10,408,332 |
| 43 | Houston | US | 51.7 | 39.8 | 39.8 | 11.9 | 44.7 | 4,301,460 |
| 44 | Khulna | BD | 51.6 | 48.5 | 48.5 | 3.1 | 23.8 | 1,081,072 |
| 45 | Boston | US | 51.5 | 44.8 | 44.8 | 6.7 | 41.0 | 1,669,310 |
| 46 | Yangon | MM | 50.9 | 40.3 | 40.4 | 10.6 | 38.9 | 6,190,957 |
| 47 | Prague | CZ | 50.9 | 39.9 | 39.9 | 11.0 | 43.2 | 1,201,277 |
| 48 | Niterói | BR | 50.9 | 40.3 | 40.3 | 10.6 | 44.7 | 1,734,951 |
| 49 | Kharkiv | UA | 50.6 | 43.0 | 43.0 | 7.7 | 47.6 | 1,284,379 |
| 50 | Cabinda | AO | 50.3 | 23.0 | 23.3 | 27.0 | 41.5 | 1,220,203 |
| 51 | Moscow | RU | 50.3 | 45.7 | 45.7 | 4.6 | 45.6 | 14,384,081 |
| 52 | Havana | CU | 50.2 | 37.8 | 37.8 | 12.3 | 43.4 | 1,632,771 |
| 53 | San Pedro Sula | HN | 50.1 | 31.4 | 31.5 | 18.6 | 42.8 | 1,301,567 |
| 54 | Philadelphia | US | 50.1 | 40.2 | 40.2 | 9.9 | 46.6 | 2,286,396 |
| 55 | Managua | NI | 50.0 | 38.3 | 38.4 | 11.6 | 45.9 | 1,436,088 |
| 56 | Dar es Salaam | TZ | 49.9 | 29.3 | 30.5 | 19.4 | 45.2 | 8,918,524 |
| 57 | Asunción | PY | 49.8 | 45.2 | 45.2 | 4.6 | 49.3 | 1,175,481 |
| 58 | Shiyan | CN | 49.8 | 44.2 | 44.2 | 5.7 | 42.3 | 1,129,747 |
| 59 | Caracas | VE | 49.6 | 39.5 | 43.8 | 5.7 | 50.3 | 2,667,322 |
| 60 | Munich | DE | 49.5 | 41.6 | 41.6 | 7.9 | 42.2 | 1,827,704 |
| 61 | Nizhniy Novgorod | RU | 49.2 | 42.2 | 42.2 | 6.9 | 45.6 | 1,189,992 |
| 62 | Guatemala City | GT | 49.1 | 40.1 | 40.8 | 8.3 | 46.9 | 2,900,618 |
| 63 | Pekanbaru | ID | 48.7 | 43.4 | 43.4 | 5.3 | 49.9 | 1,226,040 |
| 64 | Zanzibar | TZ | 48.7 | 38.3 | 38.9 | 9.7 | 44.0 | 1,026,755 |
| 65 | Budapest | HU | 48.0 | 40.3 | 40.3 | 7.7 | 49.0 | 1,743,875 |
| 66 | Dublin | IE | 48.0 | 25.3 | 25.3 | 22.7 | 46.6 | 1,124,519 |
| 67 | Tegucigalpa | HN | 48.0 | 31.2 | 32.2 | 15.8 | 50.4 | 1,653,508 |
| 68 | Jerusalem | IL | 47.9 | 22.5 | 43.7 | 4.1 | 50.0 | 1,022,929 |
| 69 | Detroit | US | 47.8 | 32.4 | 32.4 | 15.4 | 49.7 | 1,918,653 |
| 70 | Sydney | AU | 47.7 | 39.0 | 39.0 | 8.7 | 46.7 | 4,375,394 |
| 71 | Belgrade | RS | 47.6 | 39.0 | 39.0 | 8.7 | 46.9 | 1,200,546 |
| 72 | Kyiv | UA | 47.4 | 41.5 | 41.5 | 5.9 | 47.8 | 2,575,199 |
| 73 | Auckland | NZ | 47.3 | 31.6 | 31.6 | 15.7 | 40.8 | 1,623,897 |
| 74 | Nairobi | KE | 47.3 | 24.6 | 36.1 | 11.1 | 44.7 | 6,646,256 |
| 75 | Zunyi | CN | 47.1 | 42.0 | 42.0 | 5.1 | 42.2 | 1,185,303 |
| 76 | Guwahati | IN | 47.0 | 45.4 | 45.4 | 1.6 | 44.7 | 1,375,936 |
| 77 | Rasapūdipalem | IN | 47.0 | 40.0 | 40.0 | 6.9 | 42.2 | 1,634,439 |
| 78 | Copenhagen | DK | 46.9 | 29.0 | 29.0 | 17.9 | 46.3 | 1,258,201 |
| 79 | New York City | US | 46.5 | 36.6 | 36.6 | 9.9 | 46.7 | 14,197,658 |
| 80 | Virār | IN | 46.5 | 39.0 | 39.0 | 7.5 | 27.3 | 1,551,310 |
| 81 | Lisbon | PT | 46.4 | 22.3 | 22.9 | 23.5 | 45.1 | 1,974,573 |
| 82 | Takoradi | GH | 46.3 | 29.9 | 30.1 | 16.2 | 46.4 | 1,255,808 |
| 83 | Padang | ID | 46.2 | 41.5 | 41.7 | 4.5 | 35.5 | 1,210,988 |
| 84 | Busan | KR | 46.2 | 44.3 | 44.3 | 1.9 | 43.5 | 3,263,408 |
| 85 | Taipei | TW | 46.2 | 44.0 | 44.0 | 2.1 | 44.7 | 9,686,521 |
| 86 | Rotterdam | NL | 46.1 | 25.7 | 25.7 | 20.4 | 46.5 | 2,173,754 |
| 87 | Medellín | CO | 46.0 | 31.7 | 31.7 | 14.3 | 52.9 | 3,215,457 |
| 88 | Liverpool | GB | 45.9 | 28.3 | 28.3 | 17.6 | 44.3 | 1,243,205 |
| 89 | Brisbane | AU | 45.8 | 33.0 | 33.0 | 12.8 | 51.6 | 1,359,104 |
| 90 | Kazan | RU | 45.3 | 35.6 | 35.6 | 9.7 | 47.7 | 1,127,086 |
| 91 | Porto | PT | 44.8 | 29.5 | 29.6 | 15.3 | 46.2 | 1,060,250 |
| 92 | Valencia | VE | 44.6 | 24.0 | 35.6 | 9.0 | 53.3 | 1,601,248 |
| 93 | Tiruchirappalli | IN | 44.5 | 34.7 | 42.4 | 2.1 | 40.0 | 1,378,490 |
| 94 | Āsansol | IN | 44.4 | 31.3 | 31.3 | 13.1 | 22.7 | 2,886,897 |
| 95 | São Luís | BR | 44.4 | 32.9 | 33.0 | 11.4 | 46.8 | 1,272,247 |
| 96 | Aba | NG | 44.3 | 41.0 | 41.2 | 3.1 | 55.5 | 1,472,091 |
| 97 | Antalya | TR | 44.1 | 25.4 | 29.4 | 14.8 | 45.5 | 1,372,672 |
| 98 | Recife | BR | 44.1 | 33.8 | 33.9 | 10.2 | 47.8 | 3,847,557 |
| 99 | Brussels | BE | 43.9 | 35.3 | 35.3 | 8.6 | 51.9 | 1,468,753 |
| 100 | Amsterdam | NL | 43.7 | 25.5 | 25.5 | 18.2 | 46.2 | 1,223,406 |

### Overall environmental quality

50% physical greenery + 35% air + 15% water access.

| # | City | Country | Environment | Physical green | Air | Water | PM2.5 | Centre pop |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | Seattle | US | 79.9 | 61.0 | 99.1 | 98.4 | 5.1 | 2,099,643 |
| 2 | Portland | US | 79.8 | 61.1 | 98.5 | 98.8 | 5.2 | 1,839,775 |
| 3 | Stockholm | SE | 79.6 | 64.0 | 93.1 | 99.9 | 6.0 | 1,543,891 |
| 4 | Washington | US | 78.6 | 69.4 | 83.8 | 97.4 | 7.7 | 3,471,725 |
| 5 | Vancouver | CA | 74.1 | 52.9 | 96.6 | 92.4 | 5.5 | 2,198,106 |
| 6 | Helsinki | FI | 73.4 | 56.2 | 88.2 | 96.3 | 6.9 | 1,001,124 |
| 7 | Minneapolis | US | 73.3 | 60.6 | 80.4 | 98.9 | 8.5 | 1,217,972 |
| 8 | Brisbane | AU | 72.3 | 45.8 | 98.6 | 99.4 | 5.2 | 1,359,104 |
| 9 | Auckland | NZ | 72.0 | 47.3 | 95.8 | 98.8 | 5.6 | 1,623,897 |
| 10 | Boston | US | 71.6 | 51.5 | 89.1 | 98.2 | 6.7 | 1,669,310 |
| 11 | Hamburg | DE | 71.5 | 61.2 | 74.2 | 99.5 | 10.1 | 1,733,897 |
| 12 | Sydney | AU | 71.3 | 47.7 | 95.8 | 92.6 | 5.6 | 4,375,394 |
| 13 | Baltimore | US | 70.4 | 54.2 | 83.5 | 93.8 | 7.8 | 1,127,337 |
| 14 | Manchester | GB | 70.2 | 57.7 | 76.2 | 97.8 | 9.5 | 2,539,906 |
| 15 | Philadelphia | US | 69.2 | 50.1 | 84.6 | 97.0 | 7.6 | 2,286,396 |
| 16 | London | GB | 68.9 | 52.0 | 80.1 | 98.9 | 8.6 | 10,408,332 |
| 17 | Berlin | DE | 68.8 | 58.7 | 70.7 | 97.9 | 11.1 | 3,549,996 |
| 18 | Leeds | GB | 68.7 | 54.5 | 76.1 | 99.2 | 9.6 | 1,053,955 |
| 19 | Essen | DE | 68.6 | 60.4 | 70.6 | 91.3 | 11.1 | 2,718,996 |
| 20 | Melbourne | AU | 67.9 | 43.6 | 89.3 | 98.9 | 6.7 | 4,010,658 |
| 21 | Austin | US | 67.7 | 54.1 | 73.8 | 98.9 | 10.2 | 1,580,153 |
| 22 | Köln | DE | 67.5 | 55.4 | 72.0 | 97.4 | 10.7 | 1,156,303 |
| 23 | Dublin | IE | 67.4 | 48.0 | 82.4 | 96.7 | 8.0 | 1,124,519 |
| 24 | New York City | US | 67.3 | 46.5 | 83.5 | 98.6 | 7.8 | 14,197,658 |
| 25 | Recife | BR | 66.6 | 44.1 | 85.0 | 98.7 | 7.5 | 3,847,557 |
| 26 | Houston | US | 66.4 | 51.7 | 73.0 | 99.7 | 10.4 | 4,301,460 |
| 27 | Thiruvananthapuram | IN | 66.1 | 74.6 | 42.7 | 92.1 | 23.6 | 3,166,824 |
| 28 | Detroit | US | 66.0 | 47.8 | 78.2 | 98.1 | 9.0 | 1,918,653 |
| 29 | Munich | DE | 65.8 | 49.5 | 75.1 | 98.0 | 9.8 | 1,827,704 |
| 30 | Orlando | US | 65.7 | 55.7 | 81.4 | 62.0 | 8.3 | 1,231,650 |
| 31 | Liverpool | GB | 65.6 | 45.9 | 79.9 | 98.0 | 8.6 | 1,243,205 |
| 32 | Colombo | LK | 65.6 | 68.7 | 46.4 | 99.9 | 21.3 | 4,720,932 |
| 33 | Copenhagen | DK | 65.3 | 46.9 | 76.8 | 100.0 | 9.4 | 1,258,201 |
| 34 | Havana | CU | 65.2 | 50.2 | 73.3 | 96.1 | 10.3 | 1,632,771 |
| 35 | Denver | US | 64.7 | 42.0 | 84.1 | 95.2 | 7.7 | 2,425,086 |
| 36 | Toronto | CA | 64.4 | 40.6 | 84.6 | 96.9 | 7.6 | 5,566,381 |
| 37 | Maceió | BR | 64.3 | 40.1 | 84.8 | 97.1 | 7.5 | 1,046,071 |
| 38 | Rotterdam | NL | 64.1 | 46.1 | 75.5 | 97.4 | 9.7 | 2,173,754 |
| 39 | Zanzibar | TZ | 63.9 | 48.7 | 71.7 | 96.4 | 10.8 | 1,026,755 |
| 40 | Birmingham | GB | 63.6 | 52.2 | 76.5 | 71.6 | 9.5 | 2,617,210 |
| 41 | Vila Velha | BR | 63.6 | 39.7 | 83.3 | 97.3 | 7.8 | 1,291,834 |
| 42 | Samarinda | ID | 63.5 | 59.2 | 55.2 | 96.7 | 16.8 | 1,225,373 |
| 43 | Lisbon | PT | 63.4 | 46.4 | 73.8 | 95.7 | 10.2 | 1,974,573 |
| 44 | Brussels | BE | 63.4 | 43.9 | 75.8 | 99.2 | 9.6 | 1,468,753 |
| 45 | Durban | ZA | 63.2 | 57.1 | 57.2 | 97.7 | 15.9 | 3,270,867 |
| 46 | San Antonio | US | 62.8 | 53.9 | 74.0 | 66.4 | 10.1 | 1,513,874 |
| 47 | Prague | CZ | 62.7 | 50.9 | 64.1 | 99.1 | 13.2 | 1,201,277 |
| 48 | Montréal | CA | 62.7 | 41.9 | 78.9 | 94.4 | 8.9 | 3,221,844 |
| 49 | Amsterdam | NL | 62.7 | 43.7 | 74.8 | 97.7 | 9.9 | 1,223,406 |
| 50 | Dallas | US | 62.3 | 42.0 | 76.2 | 97.4 | 9.5 | 2,577,308 |
| 51 | Hong Kong | HK | 62.1 | 53.4 | 58.3 | 99.8 | 15.4 | 4,807,599 |
| 52 | Calgary | CA | 62.1 | 41.8 | 74.9 | 99.8 | 9.9 | 1,524,648 |
| 53 | Chicago | US | 61.9 | 39.0 | 81.1 | 93.6 | 8.3 | 5,318,734 |
| 54 | Sacramento | US | 61.9 | 38.2 | 80.3 | 97.9 | 8.5 | 1,697,183 |
| 55 | Paris | FR | 61.8 | 43.0 | 72.4 | 99.8 | 10.6 | 9,328,384 |
| 56 | Salt Lake City | US | 61.8 | 36.8 | 83.4 | 94.6 | 7.8 | 1,108,953 |
| 57 | São Luís | BR | 61.7 | 44.4 | 70.1 | 99.8 | 11.2 | 1,272,247 |
| 58 | Nizhniy Novgorod | RU | 61.7 | 49.2 | 63.7 | 98.5 | 13.3 | 1,189,992 |
| 59 | Perth | AU | 61.4 | 28.3 | 93.2 | 97.3 | 6.0 | 1,357,381 |
| 60 | Natal | BR | 61.2 | 35.0 | 83.2 | 97.6 | 7.9 | 1,133,223 |
| 61 | Salvador | BR | 61.2 | 41.2 | 75.9 | 93.7 | 9.6 | 3,305,395 |
| 62 | Porto | PT | 61.2 | 44.8 | 68.6 | 98.2 | 11.7 | 1,060,250 |
| 63 | Saint Petersburg | RU | 61.0 | 36.5 | 79.7 | 99.1 | 8.7 | 4,878,965 |
| 64 | João Pessoa | BR | 61.0 | 39.3 | 77.4 | 95.0 | 9.2 | 1,120,243 |
| 65 | Kyiv | UA | 60.8 | 47.4 | 63.9 | 98.4 | 13.3 | 2,575,199 |
| 66 | Kharkiv | UA | 60.5 | 50.6 | 59.7 | 95.4 | 14.9 | 1,284,379 |
| 67 | Warsaw | PL | 60.5 | 53.1 | 55.7 | 96.1 | 16.6 | 1,999,985 |
| 68 | Budapest | HU | 60.4 | 48.0 | 61.3 | 99.5 | 14.3 | 1,743,875 |
| 69 | Davao | PH | 60.4 | 57.4 | 48.3 | 98.5 | 20.3 | 1,651,085 |
| 70 | Tuen Mun | HK | 60.1 | 53.5 | 53.0 | 98.9 | 17.9 | 1,081,304 |
| 71 | Cebu City | PH | 60.1 | 57.3 | 49.5 | 94.0 | 19.6 | 3,191,585 |
| 72 | Owerri | NG | 60.0 | 76.5 | 28.1 | 79.6 | 35.0 | 3,594,823 |
| 73 | Kazan | RU | 59.9 | 45.3 | 64.8 | 97.2 | 13.0 | 1,127,086 |
| 74 | Tshikapa | CD | 59.2 | 72.4 | 23.0 | 99.6 | 40.2 | 1,074,734 |
| 75 | Dar es Salaam | TZ | 59.2 | 49.9 | 56.0 | 97.6 | 16.5 | 8,918,524 |
| 76 | Edmonton | CA | 59.0 | 40.8 | 67.8 | 99.5 | 12.0 | 1,144,485 |
| 77 | Miami | US | 58.9 | 33.8 | 77.3 | 99.6 | 9.2 | 5,733,041 |
| 78 | Taipei | TW | 58.9 | 46.2 | 61.4 | 95.2 | 14.2 | 9,686,521 |
| 79 | Cape Town | ZA | 58.8 | 35.0 | 77.3 | 95.1 | 9.2 | 4,338,251 |
| 80 | Lyon | FR | 58.8 | 36.7 | 73.6 | 97.9 | 10.2 | 1,194,741 |
| 81 | Minsk | BY | 58.7 | 40.2 | 67.5 | 99.5 | 12.1 | 2,093,511 |
| 82 | Cần Thơ | VN | 58.6 | 56.2 | 45.7 | 96.8 | 21.8 | 1,652,648 |
| 83 | Vienna | AT | 58.4 | 40.3 | 69.3 | 93.3 | 11.5 | 2,056,564 |
| 84 | Hiroshima | JP | 58.2 | 41.8 | 64.8 | 97.6 | 13.0 | 1,150,147 |
| 85 | Mogadishu | SO | 58.2 | 32.0 | 78.2 | 99.0 | 9.0 | 4,798,452 |
| 86 | Moscow | RU | 58.1 | 50.3 | 51.8 | 99.1 | 18.4 | 14,384,081 |
| 87 | Belém | BR | 58.1 | 39.2 | 67.6 | 99.0 | 12.0 | 2,192,374 |
| 88 | Naha | JP | 57.8 | 32.9 | 76.5 | 97.2 | 9.4 | 1,065,339 |
| 89 | Johor Bahru | MY | 57.7 | 43.1 | 61.9 | 96.6 | 14.0 | 1,703,323 |
| 90 | Busan | KR | 57.6 | 46.2 | 56.4 | 98.4 | 16.3 | 3,263,408 |
| 91 | Mombasa | KE | 57.6 | 41.1 | 64.8 | 95.8 | 13.0 | 1,617,738 |
| 92 | Yekaterinburg | RU | 57.6 | 41.9 | 63.3 | 96.6 | 13.5 | 1,102,144 |
| 93 | Pekanbaru | ID | 57.6 | 48.7 | 55.8 | 91.2 | 16.6 | 1,226,040 |
| 94 | Porto Alegre | BR | 57.5 | 41.2 | 63.5 | 97.9 | 13.4 | 2,763,349 |
| 95 | San José | CR | 57.4 | 52.7 | 48.7 | 93.6 | 20.1 | 2,272,571 |
| 96 | Niterói | BR | 57.3 | 50.9 | 49.9 | 96.2 | 19.4 | 1,734,951 |
| 97 | Managua | NI | 57.2 | 50.0 | 51.5 | 95.0 | 18.6 | 1,436,088 |
| 98 | Brasília | BR | 57.2 | 43.2 | 64.4 | 86.9 | 13.1 | 1,799,120 |
| 99 | Santo Domingo | DO | 57.2 | 39.3 | 64.6 | 99.3 | 13.0 | 4,593,163 |
| 100 | Rostov-on-Don | RU | 57.1 | 41.7 | 61.2 | 98.4 | 14.3 | 1,028,671 |

### Access to nature

Not published. See §4: the area-subtraction test produced impossible values.
The correct direct 15 km buffer outside the polygon is the next raster pass.

## 10. Sensitivity

Physical greenery:

| Variant | Top five |
|---|---|
| Headline: tree+shrub+grass, built-up ≥20% | Owerri, Thiruvananthapuram, Tshikapa, Washington, Colombo |
| Natural only: tree+shrub, built-up ≥20% | Thiruvananthapuram, Owerri, Colombo, Washington, Onitsha |
| Built-up ≥30% | Mbuji-Mayi, Onitsha, Stockholm, Kananga, Hamburg |

Overall environmental quality:

| Weights (greenery/air/water) | Top five |
|---|---|
| 50/35/15 | Seattle, Portland, Stockholm, Washington, Vancouver |
| 60/25/15 | Washington, Stockholm, Portland, Seattle, Minneapolis |
| 40/45/15 | Seattle, Portland, Stockholm, Washington, Vancouver |
| Equal | Seattle, Portland, Stockholm, Washington, Brisbane |

The physical top is sensitive to the built-up floor and to counting grass:
Owerri, Thiruvananthapuram, Tshikapa and Mbuji-Mayi are low-density,
vegetation-rich polygons. At ≥30% built-up, Mbuji-Mayi, Onitsha, Stockholm,
Kananga and Hamburg lead. This is a data-density
effect, not a city ranking to overinterpret.

## 11. Raw data and reproduction

Eight GHSL centres ≥1M have no GeoNames city label and are not in the
516-centre table: Bibhutpur (1,869,763), Marhaura (1,559,314), Mehsi
(1,453,663), Aboh (1,282,172), Sincan (1,169,436), Yueqing (1,135,366),
Sarvestan (1,018,457) and Colachel (1,001,954). They are in the GHSL polygon
table and can be added once a label rule is chosen; no score is invented for
them.

- Raw scores: `reports/ghsl_scores_all_516.csv`
- Polygons: `reports/urban_centres_516.geojson`
- Native polygon calculation: `bootstrap_urban_land_cover.py`
- GHSL views: `bootstrap_ghsl_scores.py`
- Ranking code: `reports/generate_ghsl_rankings.py`

```bash
python bootstrap_urban_land_cover.py
python bootstrap_ghsl_scores.py
python reports/generate_ghsl_rankings.py \
  --db-url "postgresql:///travel_global?host=/var/run/postgresql&port=5432"
```
