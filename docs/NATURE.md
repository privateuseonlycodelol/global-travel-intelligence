# Nature typology

Status: **landscape and terrain loaded; land cover, water, night sky and shore
are planned.** Last updated: 2026-11-25.

## Why not use coast distance and park counts

The old nature evidence was:

- `destination_geography.coast_distance_km` — straight-line distance to a
  coastline;
- `destination_geography.elevation_m` — the town's elevation;
- `facility_metrics.park_count_5km` and `beach_count_5km` — Overture Places
  counts.

Those answer three different questions and are easy to misread:

- 259 shoreline cities have zero mapped beach places, while 1,770 cities more
  than 50 km inland have at least one. Paris has 24 beach places and is 150 km
  from the sea; Annecy's beaches are on a lake.
- Innsbruck at 587 m sits under high peaks; Denver at 1,636 m is high and flat;
  La Paz at 3,782 m is a high city. One elevation cannot separate them.
- A 5 km park count grows with city size and with map coverage. It is an amenity
  count, not green space quality.

The nature profile therefore separates **landscape region**, **terrain**,
**land cover**, **water** and **shore**, each with its own denominator.

## Loaded now

### Landscape — RESOLVE Ecoregions 2017

- 846 terrestrial ecoregions, 14 biomes, 8 realms.
- One point-in-polygon sample at the city centroid.
- **12,927 of 13,101 cities** matched; 174 small islands or coastal points fall
  outside the polygons and stay NULL.
- The biome is the surrounding region, not the view from the window. The
  built-up land-cover fraction is a separate column.
- Source: RESOLVE Ecoregions 2017, CC BY 4.0,
  https://storage.googleapis.com/teow2016/Ecoregions2017.zip

### Climate class — Köppen-Geiger 1991–2020

- Beck et al. 2023, 1 km, 1991–2020. One point sample per city.
- **13,101 cities** loaded. Trento is **Cfa** (temperate, hot summer); Paris
  and Innsbruck are **Cfb** (temperate, warm summer); Dubai is **BWh** (hot
  desert).
- The class is a label, not a score. It sits beside the monthly climate and the
  ecoregion, not instead of them.
- Source: Beck et al. 2023, CC BY 4.0,
  https://www.gloh2o.org/koppen/

### Terrain — Amatulli et al. 2018, GMTED 10 km

- Median elevation, elevation standard deviation, Terrain Ruggedness Index
  (TRI) and Topographic Position Index (TPI) in the 10 km cell around the city.
- **13,101 cities** loaded.
- The 10 km minimum/maximum elevation grids were unavailable from the archive
  at build time, so the table stores median, standard deviation and TRI/TPI
  rather than inventing a max-minus-min relief.
- `relief_class`: low relief (TRI < 10), rolling or hilly (10–40), high relief
  (> 40).
- `position_class`: valley or low position (TPI ≤ −0.5), slope or neutral,
  ridge or high position (TPI ≥ 0.5). These are relative descriptions, not a
  landform survey.
- Source: Amatulli et al. 2018, CC BY 3.0,
  https://doi.pangaea.de/10.1594/PANGAEA.867113

| City | Biome | 1 km built-up / tree | 15 km tree / built-up | 10 km median elevation | Elevation SD | TRI | Reading |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Trento | Temperate Conifer Forests | 70% / 22% | 73% / 7% | 357 m | 250 m | 32 | valley under peaks |
| Paris | Temperate Broadleaf & Mixed Forests | 86% / 5% | 23% / 71% | 50 m | 19 m | 3 | low, urban |
| Innsbruck | Temperate Conifer Forests | 81% / 14% | 60% / 6% | 1,231 m | 607 m | 64 | high valley, large relief |
| Denver | Temperate Grasslands, Savannas & Shrublands | — | — | 1,629 m | 18 m | 3 | high and flat |
| La Paz, Bolivia | Montane Grasslands & Shrublands | — | — | 3,581 m | 237 m | 31 | very high, moderate relief |

Town elevation remains in `destination_geography`; the two are shown as
separate lines.

### Inland water — HydroLAKES v1.0

- Nearest lake or reservoir shore, its name when published, area in km², and
  lake/reservoir/regulated-lake type.
- **13,101 cities** loaded. Lakes are at least 10 ha; rivers, small tarns and
  seasonal pans are not in this layer.
- The original polygon file is imported, queried and dropped; only derived city
  rows are kept, following HydroLAKES' request not to redistribute the full
  polygon set.
- Annecy is 1.0 km from a 25.5 km² lake, Como 0.7 km from a 145 km² lake and
  Geneva 0.4 km from a 572 km² lake. Coast distance cannot show any of that.
- Source: HydroLAKES v1.0, CC BY 4.0,
  https://www.hydrosheds.org/products/hydrolakes

### Land cover — ESA WorldCover 2021

- Tree, shrub, grass, crop, built-up, bare, snow, water, wetland, mangrove and
  moss fractions at **1 km** and **15 km**.
- 1 km uses native 10 m classes; 15 km uses COG overviews at about 200 m.
- The initial batch covers the largest 5,000 cities by population (**10,000
  rows**); the loader can be rerun for all 13,101.
- Cropland and built-up stay separate, so a farming region is not described as
  forest and a dense city is not described as green because its region is.
- Source: ESA WorldCover 2021 v200, CC BY 4.0,
  https://esa-worldcover.org/en/data-access

## Council decision and current trade-offs

The experience agent ranked landscape, coast, terrain, forest/green, water,
night sky and protected areas. The data-feasibility agent recommended the
Copernicus 100 m 2018 fractional land cover, Copernicus GLO-90 relief with the
GMBA mountain mask, and GLWD v2 wetlands.

What is loaded now uses the **already validated, licence-clean, higher-resolution
alternatives**:

- WorldCover 10 m rather than CGLS 100 m. It gives sharper 1 km composition but
  currently covers the largest 5,000 cities, not all 13,101.
- Amatulli GMTED 10 km median/SD/TRI/TPI rather than GLO-90 p95−p5. It is
  global and already loaded, but it is a coarser terrain description.
- HydroLAKES lakes rather than HydroLAKES plus GLWD wetlands. Rivers and
  seasonal wetlands remain missing.

The 1 km figure is a fixed daily-walk disc around the city point, **not the GHSL
urban-centre polygon**; the 15 km figure is the fixed hinterland. They are
labelled that way. The next upgrade is the CGLS 2018 fractions for all cities
and GLWD v2 wetland/river fractions; neither is required for the current
profile to be useful.

## Planned, globally repeatable

- **Shore type** — USGS coastal segment slope/erodibility, only for cities
  within about 30 km of the coast. Do not call a gentle shore a beach.
- **Night sky** — VIIRS annual radiance at the town and the darkest cell within
  25 km.
- **Protected areas** — held: WDPA's licence forbids commercial use of
  derivative works without written permission. Do not load it without that
  permission.

## Rejected for the global layer

- WDPA / Protected Planet without written permission.
- Falchi sky-brightness atlas (CC BY-NC).
- Sandy-shore points from Luijendijk et al. (not an open bulk download).
- Regional or city-only inventories (Natura 2000, PAD-US, Urban Atlas,
  ParkScore, Tree Equity, municipal tree inventories) as the global layer.
- OSM/Overture wood and park geometry as a second nature system; park and beach
  counts remain amenity counts with uneven coverage.
- Biodiversity Intactness, species-richness models, GBIF/iNaturalist density as
  “wildlife you will see”.
- Numbeo, Teleport and any crowd-sourced nature rating.

## Commands

```bash
python bootstrap_nature.py
python bootstrap_nature.py --report
```

Requires PostGIS, GDAL `ogr2ogr`, `rasterio` and `requests`.
