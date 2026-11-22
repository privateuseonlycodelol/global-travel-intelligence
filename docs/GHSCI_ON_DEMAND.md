# GHSCI on demand

Chosen model: **no planet file, no global batch**. Compute a city when it is
requested, cache the result, and keep only a bounded extract cache on disk.

This supersedes the 1M+ batch in [GHSCI_1M_RUN.md](GHSCI_1M_RUN.md). The scope
counts there are still useful for estimating demand.

## Disk budget

| Item | Size | Lifecycle |
| --- | ---: | --- |
| Country/state OSM extract | 0.5–4 GB typical | LRU cache, capped at 10 GB |
| GHSL 100 m tiles | ~30 MB each | LRU cache, capped at 2 GB |
| City OSM clip | 5–50 MB | deleted after the run |
| Per-city PostGIS database | 50–500 MB | dropped after export |
| Result row | ~1 KB | kept |
| Optional GeoPackage/figures | 1–20 MB | not kept by default |

Steady state: **results only**, well under 1 GB for all cities. Peak: one
country extract plus a few clips, **under ~15 GB** with the cap.

No 96 GB planet.

## Flow

1. `place_profile` resolves the destination to a GHS urban centre
   (`ID_UC_G0`).
2. Look up `ghsci_city_indicators`. If present, return it.
3. Otherwise insert a queued row in `ghsci_jobs` and return
   `walkability_status: "queued"`.
4. The worker:
   - finds the smallest Geofabrik extract for the country/state
     (e.g. US state, German state, Italian nord-est);
   - downloads it to the LRU cache if absent;
   - `osmium extract` the urban-centre bbox, sets the timestamp;
   - downloads the GHSL tile(s) covering the bbox into the tile cache;
   - runs GHSCI with `routing_engine: inmemory`, no GTFS;
   - exports `indicators_region.csv` and writes the result row;
   - deletes the clip and drops the per-city database.
5. The next request for that centre is served from the result table.

## Region loop (the "do each city, delete the raw data" model)

Yes: process one city at a time, keep only the result, and delete the raw
inputs. Do it region by region so a 0.5-4 GB extract is downloaded once for
all cities in that region, not once per city:

```
for region in regions:
    download region.osm.pbf          # 0.5-4 GB, temporary
    for centre in region.centres:
        osmium extract centre.pbf    # 5-50 MB
        download GHSL tile(s)        # ~30 MB each, cached
        run GHSCI(centre)
        write result row             # ~1 KB
        delete centre.pbf
        drop centre database
    delete region.osm.pbf
```

Disk peak: one region extract + one city clip + one database, usually
**under 5 GB**. Final state: result rows only, **~1 KB per city**.

The trade-off is time: each city still takes 1-5 minutes of analysis. The
region download is amortised across the region's cities. If a region has only
one requested city, download it, run it, delete it, and move on.

The same loop is what the queue worker does per request; the only difference
is whether the region is kept in the 10 GB cache or deleted immediately.

## Timing

| Step | First city in a region | Later cities |
| --- | ---: | ---: |
| Download extract | 1–5 min | cached |
| GHSL tile | seconds | cached |
| GHSCI analysis | 1–5 min | 1–5 min |
| Total | ~3–10 min | ~1–5 min |

Small cities like Trento are at the fast end (1.6 minutes of analysis). Large
metros take longer.

## Queue schema

```sql
CREATE TABLE ghsci_jobs (
    urban_centre_id  INT PRIMARY KEY,
    destination_id   TEXT,
    status           TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    error            TEXT
);

CREATE TABLE ghsci_city_indicators (
    urban_centre_id  INT PRIMARY KEY,
    destination_id   TEXT,
    area_km2         DOUBLE PRECISION,
    population       BIGINT,
    population_per_km2 DOUBLE PRECISION,
    intersections_per_km2 DOUBLE PRECISION,
    pct_fresh_food_500m DOUBLE PRECISION,
    pct_convenience_500m DOUBLE PRECISION,
    pct_transit_500m DOUBLE PRECISION,
    pct_open_space_500m DOUBLE PRECISION,
    pct_large_open_space_500m DOUBLE PRECISION,
    local_nh_population_density DOUBLE PRECISION,
    local_nh_intersection_density DOUBLE PRECISION,
    local_daily_living DOUBLE PRECISION,
    ghsci_walkability_index DOUBLE PRECISION,
    sample_points    INT,
    low_confidence   BOOLEAN NOT NULL DEFAULT FALSE,
    source           TEXT NOT NULL,
    observed_period  TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`ghsci_walkability_index` is the 25-city reference z-score sum:

```
z = (local_nh_population_density - 11093.1) / 13637.9
  + (local_nh_intersection_density - 98.44) / 36.96
  + (local_daily_living - 1.028) / 0.915
```

It is not 0–100 and is separate from How Walkable.

## Cache eviction

- OSM cache: before download, delete least-recently-used extracts until the
  total is under 10 GB minus the new file's size.
- GHSL cache: same, capped at 2 GB.
- Never cache the city clip or the PostGIS database.
- A lock file per extract prevents two workers downloading the same country.

## Quality flags

Set `low_confidence = TRUE` when any of:

- `sample_points < 1000`;
- population density is zero or missing;
- the OSM extract has fewer than 500 highway ways in the urban centre;
- the result came from a truncated/partial extract.

Do not turn a failed run into a zero. Leave the row absent and mark the job
failed with the error.

## Demand order

There is no need to precompute 1M+ first. The queue naturally prioritises:

1. cities already requested by users;
2. the 1M+ list if pre-warming is wanted later;
3. everything else.

If a warm cache is desired, precompute only the top ~100 destinations; the
rest cost 1–5 minutes on first request.
