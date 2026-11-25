# Running GHSCI for every city

> **Design only — no GHSCI values are served by the MCP server.** The pilot
> exists; the queue, tables and on-demand worker do not.

Status: **plan, with a working single-city pilot** (Trento, see
[GHSCI_PILOT.md](GHSCI_PILOT.md)). This is how to get from 1 city to ~11,400.

## 1. What "every city" means

Use the **GHS Urban Centre Database R2024A** as the unit: 11,422 urban centres
with a common boundary. A GeoNames destination is linked to the urban centre
that contains it; one run per centre is reused by every destination in that
centre (London boroughs, metro municipalities, etc.).

We already match 8,444 destinations to 10,351 urban centres for the street
network. That matching needs widening to all 11,422 centres, mostly by nearest
centroid within a country, because GHSCI only needs the centre polygon.

## 2. Inputs

| Input | Source | Size / note |
| --- | --- | --- |
| Urban-centre polygons | GHS-UCDB R2024A geography theme | 11 MB GeoPackage, already downloaded |
| Population | GHSL R2023A 100 m, 2025 epoch | 4.9 GB global zip, or 375 tiles (~95 MB each) |
| OpenStreetMap | Geofabrik continent/country PBFs, or planet | Europe ~30 GB; planet ~80 GB |
| GTFS | optional | skip initially; OSM transit stops still give `pt_osm_any` |
| GHSCI code | `healthysustainablecities/global-indicators` | MIT; local patches documented in the pilot |

Disk: 240 GB free here. Planet PBF + GHSL + per-city outputs fit.

## 3. Per-city pipeline

For each urban centre:

1. Extract its polygon from the UCDB GeoPackage to a GeoJSON.
2. `osmium extract -b <bbox>` from the continent/country PBF; set the PBF
   timestamp to the publication date (`osmconvert --timestamp=...`).
3. Create the region YAML: CRS (UTM zone by longitude), boundary, OSM path,
   shared population folder, `routing_engine: inmemory`, no GTFS.
4. Run `ghsci.Region(codename).analysis()`.
5. Export `indicators_region.csv` and drop the per-city database.
6. Insert the components into the travel database.

The pilot does this manually for Trento in 1.6 minutes.

## 4. The comparable index

GHSCI's `pop_walkability` is within-city. The comparable field is computed from
the published 25-city reference in `indicators.yml`:

```
z = (local_nh_population_density - 11093.1) / 13637.9
  + (local_nh_intersection_density - 98.44) / 36.96
  + (local_daily_living - 1.028) / 0.915
```

This reproduces the published `all_cities_walkability` for the 25-city dataset
(maximum error < 0.001), so it can be computed per city without a batch
`compare`. The result is a z-score sum, not 0-100. Keep the components as the
primary evidence and the index as a derived comparison.

## 5. Throughput

Measured: 1.6 minutes for Trento (26 km², 94k people, 23.5k sample points).
Larger cities take longer; assume an average of 3-5 minutes.

| Workers | 11,422 cities at 3 min | at 5 min |
| ---: | ---: | ---: |
| 4 | 6.0 days | 9.9 days |
| 8 | 3.0 days | 5.0 days |
| 32 (cloud) | 18 hours | 30 hours |

Memory: GHSCI peaks around 1-2 GB for a small city; large metros can be
several GB. On this 31 GB / 12-core host, 4-6 workers is the safe range. A
32-vCPU cloud machine cuts the elapsed time to about a day.

The bottleneck is OSM extraction and PostGIS writes, not the index formula.

## 6. On-demand mode

For a product, precompute the cities people actually ask for, and queue the
rest:

1. **Precompute:** all cities with population ≥ 100k, plus every How Walkable
   city and every destination with a trip. Roughly 4,000-5,000 centres.
2. **On demand:** when a profile is requested for an uncomputed centre, put a
   job on a queue (one worker per city). The first request returns
   `status: queued`; later requests return the cached result. A small city is
   ready in 1-3 minutes.
3. **Cache key:** UCDB `ID_UC_G0`, not destination id, so every municipality in
   a metro reuses one run.
4. **Store:** components and the index in `ghsci_city_indicators`; drop the
   per-city database after export.

The production choice is the on-demand model in
[GHSCI_ON_DEMAND.md](GHSCI_ON_DEMAND.md): no planet, a capped country-extract
cache (10 GB) and a GHSL tile cache (2 GB). This section's batch numbers are
kept only as an estimate of what a full precompute would cost.

This gives full coverage without a 3-10 day blocking batch.

## 7. Rollout

| Phase | Scope | Purpose |
| --- | --- | --- |
| 1 | Italy: 114 matched destinations | Complete one country; validate at scale |
| 2 | Spain, Germany, France, UK | ~1,000 cities; the bulk of European demand |
| 3 | North America, Japan, Brazil, Mexico, India | high-demand metros |
| 4 | remaining ~9,000 centres | full coverage / on-demand |

Phase 1 is the right next run: it is a complete country, includes small cities
like Trento, and exercises every failure mode (coastlines, islands, sparse OSM,
large metros) before a multi-day global job.

## 8. Quality checks

- Every run must have `urban_sample_point_count > 0` and a populated
  `local_nh_population_density`; otherwise mark the centre as failed, not zero.
- Compare the GHSCI index with How Walkable for the 18 overlapping cities; the
  two correlate only 0.2-0.3, so never merge them or rank them together.
- Store `source`, `observed_period` and the GHSCI release/commit with every row.
- Re-run a random 1% sample and compare components; differences > 5% mean the
  OSM extract or GHSL tile changed.

## 9. What is needed to start phase 1

- Italy OSM PBF (2.25 GB) and GHSL tiles R4_C19/C20, R5_C19/C20.
- A batch harness that creates the 114 configs, extracts the clips, runs 4
  workers, exports the CSVs and writes to `ghsci_city_indicators`.
- The pilot environment in `GHSCI_PILOT.md` (Python 3.14, GHSCI patches,
  pgRouting, GDAL, osmium, osm2pgsql).

No new data source or licence decision is required.
