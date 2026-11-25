# Scheduled public transport

Status: **global loader implemented; initial data loaded for Italy and France.**
Last updated: 2026-11-25.

## Why this exists

The database used to expose three public-transport proxies:

- `transit_stops_5km` — Overture Places facilities within 5 km;
- `gtfs_active_feeds` — Mobility Database feeds whose bounding box contains the
  city point;
- `rail_segments_5km` — Overture rail segments, loaded for only eight countries.

None of them is service. A stop count measures mapped facilities. A feed count
measures catalogue availability, and it is contaminated: Trento's seven
"feeds" included a Bosnia operator, a Berlin sleeper and a BusOne proxy tagged
to the wrong town. Paris's twelve included duplicate Île-de-France Mobilités
copies and feeds for other French regions. Rail segments say nothing about
whether a passenger train stops.

`bootstrap_gtfs.py` replaces those proxies with scheduled service computed from
GTFS.

## What is loaded

| Table | Meaning |
| --- | --- |
| `gtfs_feed` | One row per active Mobility Database feed actually processed: provider, country, original URL, licence, bbox, feed validity, content hash. |
| `gtfs_feed_place` | Feeds that have stops in a city. `stops_mapped` counts all mapped stops; `stops_in_centre` counts stops inside the GHSL urban centre. `radius_rule` is `urban_centre` or `nearest_5km`. |
| `transit_headway` | Per city, feed, day type, time band and geography band: median best headway, share of stops within 10/15/30 minutes, first/last departure, busiest stop, route-type mix, wheelchair coding. |
| `transit_city_service` | View: the best-covered feed for each city/day/time band. |
| `transit_city_weekday_peak` | View: one row per city for the weekday 07:00–09:00 metric used in profiles and comparisons. |

### Definitions

- **Day types:** `weekday` (next Wednesday inside the feed's validity) and
  `sunday` (next Sunday inside validity).
- **Time bands:** `peak` 07:00–09:00; `midday` 10:00–16:00. Sunday uses midday
  only.
- **Headway:** for each stop and route, the median gap between consecutive
  scheduled departures in the band; a stop's best headway is the shortest
  route-level median with at least two departures. The city value is the
  median of those stop-level best headways.
- **Share within 15 minutes:** share of mapped stops whose best headway is at
  most 15 minutes in that band. The denominator is mapped stops, not people or
  land area.
- **Span:** first and last scheduled departure across all mapped stops on the
  service date. GTFS times after midnight appear as `24:xx`, `25:xx`, etc.
- **Route types:** GTFS `route_type`: tram, subway/metro, rail, bus, ferry,
  cable tram, aerial lift, funicular, trolleybus, monorail.
- **Wheelchair:** share of mapped stops with `wheelchair_boarding` coded, and
  share coded boardable (`1`). Blank is unknown, not inaccessible.

## How a feed is selected

1. Read the Mobility Database catalogue `feeds_v2.csv` (CC0 metadata).
2. Keep active static GTFS feeds, skip explicit non-official feeds, skip feeds
   that require authentication, and skip URLs containing Apigee credentials.
3. Prefer the Mobility Database mirror `urls.latest` (`.../latest.zip`) over a
   stale operator URL; fall back to the operator's direct URL.
4. Download, cap at `--max-feed-mb` (default 500 MB compressed), extract the
   needed files, and parse with DuckDB.
5. Keep a feed for a city only when at least one stop lies inside the city's
   GHSL urban centre. Stops outside every centre attach to the nearest city
   within 5 km and are labelled `nearest_5km`.
6. Compute service on a Wednesday and Sunday inside `feed_start_date` /
   `feed_end_date`, with `calendar.txt` and `calendar_dates.txt`.
7. Store aggregates only. No `stop_times` rows, no timetable mirror and no
   per-city API key.

The same code path runs for every feed and every city. `--countries` and
`--feed-ids` exist to scope a batch, not to branch behaviour.

## Current coverage

- Initial run: Italy and France.
- **571 feeds**, **2,101 feed-city links**, **3,952 headway rows**, **544 cities**.
- **155 cities** have a weekday 07:00–09:00 headway and at least 50 mapped
  stops; the rest have only Sunday or midday service, too few mapped stops, or
  no mapped feed.
- Counts are in `docs/COVERAGE_REPORT.md`.
- The catalogue has feeds in many other countries; rerun without `--countries`
  or with another country list to extend coverage. Feeds larger than the cap
  are reported and skipped, not truncated.

## Trento vs Paris, from the real feed

Service date 2026-11-25 (Wednesday) and 2026-11-29 (Sunday):

| | Trento | Paris |
|---|---:|---:|
| Weekday peak median headway | 15 min | 10 min |
| Weekday peak stops ≤15 min | 63.4% | 79.9% |
| Weekday midday median headway | 20 min | 15 min |
| Sunday midday median headway | 60 min | 19 min |
| Sunday midday stops ≤15 min | 0.6% | 39.8% |
| First–last departure | 05:01–24:25 | 00:00–31:14 |
| Modes | bus, cable tram | tram, metro, rail, bus, aerial lift, funicular |

Trento's weekday peak is a real 15-minute service, but Sunday falls to hourly
and almost no stop is better than 15 minutes. Paris is frequent all week, but
its Sunday median still doubles to 19 minutes. The Paris span runs past 31:00
because night services are included; that is not a claim that every route runs
all night.

## Ranking status

The `rank_destinations` transit score still uses the older availability
components (`transit_stops_5km`, `gtfs_active_feeds`, `rail_segments_5km`).
The new `transit_peak_headway_min` metric is available in profiles and
comparisons, but the rank SQL has not yet been migrated. Do not read a high
rank transit score as measured frequency until that migration is done.

## What this does not measure

- **Real-time reliability.** No delay or cancellation archive is loaded. The
  IDFM GTFS-RT rows are third-party or require a PRIM account; the Trento
  realtime row is a BusOne estimate stream, not observed GPS. Do not read a
  timetable as reliability.
- **Journey time and transfers.** Headway is not a trip time. A router over the
  same GTFS (R5/OpenTripPlanner) would be needed for generic origin–destination
  times. No Citymapper, Google, PRIM isochrone or per-city routing API is used.
- **Fares.** Neither primary feed has a GTFS fare table. The Trento sidecar is a
  provincial 2021 file; Paris tariffs are an Île-de-France-only product. Neither
  is global, so neither is loaded.
- **Ridership and mode share.** Paris validation data is regional and
  biannual; there is no comparable Trento series.
- **Population-weighted access.** The current share is by mapped stop. A later
  step can weight it by the GHSL population grid.
- **Every operator in a city.** `transit_city_service` picks the
  best-covered feed per city/day/band. A city with separate bus and rail
  operators may need a union view later; `inspect_destination` shows every
  processed feed. Profiles warn when the selected feed has fewer than 50 mapped
  stops, and those cities are excluded from the `transit_peak_headway_min`
  metric because a rail-only feed is not the city network.

## Sources and licences

- **Mobility Database** catalogue: CC0 metadata.
  https://files.mobilitydatabase.org/feeds_v2.csv
- **Trentino Trasporti** urban GTFS: CC BY 4.0, via
  https://www.trentinotrasporti.it/opendata/google_transit_urbano_tte.zip
  and the CKAN record on `dati.trentino.it`.
- **Île-de-France Mobilités** GTFS: Licence Mobilités. Aggregates only are
  stored; the timetable is not republished.
  https://transport.data.gouv.fr/datasets/reseau-urbain-et-interurbain-dile-de-france-mobilites
- Each other feed keeps its producer licence, stored in `gtfs_feed.license_url`
  and `transit_headway.license_url`.

## Commands

```bash
# Initial country batches
python bootstrap_gtfs.py --countries IT,FR

# One known feed, for debugging only
python bootstrap_gtfs.py --feed-ids mdb-978,tdg-80921

# Refresh catalog and zips
python bootstrap_gtfs.py --countries IT,FR --refresh

# Inspect coverage
python bootstrap_gtfs.py --report
```

Requires `psycopg`, `duckdb`, `pandas`, `requests` and PostGIS.
