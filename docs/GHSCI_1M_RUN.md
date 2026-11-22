# GHSCI 1M+ run and on-demand queue

Status: **runbook**. The single-city pilot is working
([GHSCI_PILOT.md](GHSCI_PILOT.md)); this is the production plan for every city
of 1,000,000+ people, then on-demand for the rest.

## Scope

| | Count |
| --- | ---: |
| Destinations with population ≥ 1,000,000 | 551 |
| ... with a GHS-UCDB urban-centre match | 521 |
| Unique urban centres (one run each) | **469** |
| Unmatched destinations (need nearest-centroid mapping) | 30 |
| Top countries | China 166, India 50, Indonesia 16, Brazil 15, Russia 14, Nigeria 13, Mexico 12, Japan 11, USA 11, Pakistan 10 |

One GHSCI run serves every destination in the same urban centre.

## Inputs

| Input | Size | Notes |
| --- | ---: | --- |
| OSM planet PBF | 95.9 GB | `planet-261116.osm.pbf`; enables one-pass extracts for all 469 centres |
| GHSL R2023A 100 m, 2025 | 4.9 GB zip | global; unzip to tiles |
| GHS-UCDB R2024A geography | 11 MB | polygons; already downloaded |
| GTFS | optional | skipped; OSM stop access still produced |

Disk: 240 GB free. Planet + GHSL + outputs fit.

**Disk lifecycle:** the 96 GB planet is temporary. Cut the 469 clips in one
pass, then delete `planet.osm.pbf` before starting GHSCI. The clips are roughly
10-20 GB; delete each after its run. Final results are about 1 MB per centre.
Peak is Planet + clips (~110 GB); steady state is results only (~0.5 GB).
If that peak is unacceptable, use country extracts and delete each after its
batch; peak drops to roughly 10-30 GB, at the cost of more downloads.

## Per-centre pipeline

1. Polygon from UCDB by `ID_UC_G0`.
2. OSM clip: `osmium extract` from the planet. Use a single-pass
   `osmium extract --config` with all 469 bounding boxes, producing one PBF
   per centre; this avoids scanning 96 GB 469 times.
3. Region YAML: UTM zone by longitude, boundary, OSM path, shared population
   folder, `routing_engine: inmemory`, no GTFS.
4. `ghsci.Region(codename).analysis()`.
5. Export `indicators_region.csv`; drop the per-city database.

Pilot timing: 1.6 minutes for Trento (26 km², 94k people, 23.5k sample
points). Assume 3-5 minutes for large metros.

## Index

Per city, from the published 25-city reference:

```
z = (local_nh_population_density - 11093.1) / 13637.9
  + (local_nh_intersection_density - 98.44) / 36.96
  + (local_daily_living - 1.028) / 0.915
```

This reproduces the published `all_cities_walkability` (maximum error < 0.001).
It is a z-score sum, not 0-100, and separate from How Walkable.

## Throughput

| Workers | 469 centres at 3 min | at 5 min |
| ---: | ---: | ---: |
| 4 | 5.9 hours | 9.8 hours |
| 6 | 3.9 hours | 6.5 hours |
| 8 | 2.9 hours | 4.9 hours |

Memory: 1-2 GB per small city, several GB for large metros. On 31 GB / 12
cores, use 4-6 workers. Planet download is about one hour at 26 MB/s.

## On-demand queue

After the 1M+ batch:

1. Table `ghsci_jobs (urban_centre_id PK, status, requested_at, started_at,
   finished_at, error)`.
2. `place_profile` looks for a `ghsci_city_indicators` row. If missing, insert
   a `queued` job and return `walkability_status: "queued"`.
3. A worker picks jobs, prepares the same inputs for that centre (planet
   extract, polygon, GHSL tile), runs GHSCI, writes the row, drops the DB.
4. Cache key is `ID_UC_G0`, so every municipality in a metro reuses one run.

A new small city takes 1-3 minutes; large metros longer. The first request
sees `queued`, later requests see the cached components.

## Quality

- Require `urban_sample_point_count > 0` and a populated
  `local_nh_population_density`; otherwise mark the centre failed, not zero.
- China (166) and India (50) dominate the 1M+ list; OSM completeness varies.
  Store sample-point count, POI counts and a `low_confidence` flag rather than
  silently treating sparse OSM as low walkability.
- GHSCI and How Walkable correlate only 0.2-0.3 on overlapping cities; keep
  them as separate fields and never merge the scores.

## Run order

1. Download planet and GHSL.
2. Build the 469-centre config set and one-pass OSM extracts.
3. Run 4-6 workers; export CSV per centre; aggregate.
4. Load `ghsci_city_indicators` into the travel database.
5. Start the on-demand worker for the remaining ~10,900 centres.
