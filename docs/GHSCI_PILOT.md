# GHSCI pilot: Trento

Status: **working pilot; Trento and the 24 published GHSCI 25-city study
cities are loaded in `ghsci_city_indicators`. There is no on-demand worker.** Date: 2026-11-22.

## Why

The published How Walkable score covers 690 cities of 300,000+. Below that
threshold there is no ready-made score. The Global Healthy and Sustainable City
Indicators (GHSCI, `healthysustainablecities/global-indicators`) is the
peer-reviewed open-source method for computing walkability indicators from
OpenStreetMap, GHSL population and GTFS. This pilot checks whether it can run
here and what it actually produces.

## Environment

GHSCI's current release needs Python 3.14 and several system tools:

```bash
uv python install 3.14
uv venv /tmp/ghsci-venv314 --python 3.14
uv pip install --python /tmp/ghsci-venv314/bin/python \
  geopandas pyogrio osmnx shapely pyproj networkx psycopg2-binary \
  sqlalchemy geoalchemy2 pyarrow adbc-driver-postgresql adbc-driver-manager \
  gtfs-lite pygeometa fpdf2 tqdm rasterio contextily mapclassify geopy \
  babel arabic-reshaper python-bidi uharfbuzz openpyxl lxml "psycopg[binary]"

sudo apt-get install -y postgresql-15-pgrouting gdal-bin libgdal-dev \
  osmctools osm2pgsql
uv pip install --python /tmp/ghsci-venv314/bin/python "gdal==3.6.2"
```

The code assumes `/home/ghsci` and a Docker database; for a native run:

```bash
ln -s /tmp/ghsci /home/ghsci
# process/configuration/config.yml: db_host 127.0.0.1, db_port 5432,
# db_user root, db_pwd ghsci
```

Local patches required with this release (all in the GHSCI checkout, not this
repository):

1. `_03_create_network_resources.py`: accept a PBF timestamp on the same day as
   the configured publication date (the example clip and osmium extracts do not
   exactly match midnight).
2. `_03_create_network_resources.py`: run the `ALTER TABLE`/`UPDATE` block and
   the `SELECT MIN/MAX(ogc_fid)` as separate statements; SQLAlchemy 2.1 does not
   return rows from a multi-statement `execute`.
3. `ghsci.py` `raster_to_db`: use the configured `db_user` in the `psql` pipe,
   not the hard-coded `postgres`.
4. Use `routing_engine: inmemory` in the region configuration. The pgrouting
   path in this release calls `pgr_drivingDistance` with a `start_vid` column
   that pgRouting 3.4 does not return.

With those changes, the bundled Las Palmas example completes in 1.7 minutes.

## Trento inputs

| Input | Source |
| --- | --- |
| Boundary and urban region | GHS-UCDB R2024A geography layer, `Trento` / `Italy` |
| OpenStreetMap | Geofabrik `nord-est` 2026-11-20 extract, clipped with `osmium extract` |
| Population | GHSL R2023A 100 m tile `R4_C19`, 2025 epoch |
| GTFS | not loaded (optional) |

Region: 26.0 km², 94,058 residents, 3,617 people/km², 23,534 sample points.
Analysis time: **1.6 minutes**.

## Measured resources

Trento analysis, measured with GNU time on the 12-core / 31 GB host:

| Metric | Value |
| --- | ---: |
| Wall clock | 1 min 44 s |
| User CPU | 60 s |
| System CPU | 9.6 s |
| Average CPU | 67% of one core |
| Peak resident memory | ~1.03 GB |
| Filesystem read | ~1.2 GB |
| Filesystem write | ~100 MB |

So one small city is roughly **1 core, 1-2 GB RAM, under 2 minutes**. Large
metros are **not yet measured**: a synthetic 2M-node benchmark of the
neighbourhood pass alone suggests hours, so the earlier "5-15 minutes" figure
is unsafe. Profile a 300k+ node centre before scheduling a global batch. Six
workers on this host is a safe ceiling; eight risks memory pressure when
several large metros run together.

## Trento results

City-wide (region) values:

| Indicator | Value |
| --- | ---: |
| Area | 26.0 km² |
| Population | 94,058 |
| Population per km² | 3,617 |
| Intersections per km² | 108.3 |
| Urban sample points | 23,534 |

Population-weighted 500 m walk:

| Indicator | Value |
| --- | ---: |
| Residents within 500 m of a fresh-food market | 80.0% |
| Residents within 500 m of a convenience store | 73.5% |
| Residents within 500 m of public transport (OSM stops) | 97.5% |
| Residents within 500 m of any public open space | 96.9% |
| Residents within 500 m of large public open space | 55.3% |

The comparable index uses the **unweighted neighbourhood means**, not the
city-wide or population-weighted values above:

| Index input | Value |
| --- | ---: |
| `local_nh_population_density` | 4,796.7 |
| `local_nh_intersection_density` | 153.64 |
| `local_daily_living` | 2.001 |
| **25-city-reference index** | **2.10** |

Using the city-wide 108.3 or the population-weighted 6,233/2.51 would give a
different number (about 1.53); that is why the inputs are listed separately.

`pop_walkability` for Trento is 0.59, but that is a **within-city** relative
index; it is not comparable with another city's value.

The comparable GHSCI index uses the published 25-city reference statistics in
`process/configuration/indicators.yml`:

```
z = (local_nh_population_density - 11093.1) / 13637.9
  + (local_nh_intersection_density - 98.44) / 36.96
  + (local_daily_living - 1.028) / 0.915
```

This reproduces the published `all_cities_walkability` values for the 25-city
dataset (maximum error < 0.001). For Trento it gives **2.10**, which is 4th of
the 26 cities (88th percentile), driven by high intersection density (+1.49)
and daily living (+1.06) against low population density (-0.46).

The index is a z-score sum, not a 0-100 score, and it is not the same
construct as How Walkable: the two correlate only 0.2-0.3 across the 18
cities in both. Keep them as separate fields.

Trento's GHSCI index is computed from its own components; no batch `compare`
is required for this field.

## Sampling sweep

Measured on the same Trento centre, changing only
`sample_points.point_sampling_interval` in `process/configuration/config.yml`:

| Spacing | Sample points | Wall time | Fresh food | Convenience | Transit | Local pop density | Local intersection | Daily living |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 30 m | 23,534 | 103.6 s | 80.03% | 73.54% | 97.52% | 4,796.7 | 153.64 | 2.001 |
| 60 m | 13,849 | 102.0 s | 80.67% | 74.06% | 97.64% | 4,840.3 | 153.52 | 2.021 |
| 100 m | 10,409 | 102.5 s | 81.12% | 75.10% | 97.75% | 4,875.6 | 154.28 | 2.048 |

The 100 m setting cuts sample points by 56%, but wall time is unchanged: the
fixed cost is OSM import, network construction and PostGIS writes, not the
per-point routing. The 25-city-reference index moves from 2.096 at 30 m to
2.117 at 60 m and 2.170 at 100 m.

This does **not** support a 2-4x speedup from coarser sampling on small
cities. Large metros may differ because routing is a larger share; measure
those before adopting a fast profile.

## Scale

At 1.6 minutes for a small city (larger cities take longer), a full 13,101-city
run is days to weeks of compute, plus country OSM extracts and GHSL tiles.
A country or a stratified 50-100 city sample is practical; global coverage is a
batch project, not a one-off query.

## Next step

1. Run a stratified 50-100 city batch (or one country, e.g. Italy) through GHSCI.
2. Use GHSCI's own `compare` to produce `all_cities_*` fields.
3. Validate the resulting index against How Walkable on overlapping cities.
4. Only then store a `ghsci` walkability index; store the components regardless.
