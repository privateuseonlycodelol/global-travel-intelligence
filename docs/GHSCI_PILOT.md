# GHSCI pilot: Trento

Status: **working pilot, not production data**. Date: 2026-11-22.

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

## Trento results

Population-weighted, 500 m walk:

| Indicator | Value |
| --- | ---: |
| Residents within 500 m of a fresh-food market | 80.0% |
| Residents within 500 m of a convenience store | 73.5% |
| Residents within 500 m of public transport (OSM) | 97.5% |
| Residents within 500 m of any public open space | 96.9% |
| Residents within 500 m of large public open space | 55.3% |
| Intersections per km² | 108.3 |
| Population-weighted neighbourhood density | 6,233 people/km² |
| Daily living score (fresh food + convenience + transit, 0-3) | 2.51 |

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
