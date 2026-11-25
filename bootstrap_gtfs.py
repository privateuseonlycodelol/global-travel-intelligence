#!/usr/bin/env python3
"""
Global scheduled public-transport service from Mobility Database GTFS.

This replaces the misleading ``gtfs_active_feeds`` bbox count with actual
scheduled service:

* a feed counts for a city only when at least one of its stops lies inside the
  city's GHSL urban centre (or within 5 km of the city point when no centre is
  matched);
* weekday peak (07:00-09:00) and weekday/Sunday midday (10:00-16:00) median
  headways are computed from ``stop_times.txt`` and ``calendar`` /
  ``calendar_dates``;
* first/last departure, stop counts, route-type mix, and wheelchair coding are
  stored as aggregates.

No timetable, ``stop_times`` row or per-city API key is stored. The catalogue
is Mobility Database (CC0 metadata); each GTFS zip keeps its producer licence.
The same code path runs for every active feed and every city.

Usage
-----
    python bootstrap_gtfs.py --countries IT,FR
    python bootstrap_gtfs.py --countries IT,FR --feed-ids mdb-978,tdg-80921
    python bootstrap_gtfs.py --refresh
    python bootstrap_gtfs.py --report

The first production run can be scoped by country or feed size; the loader
itself has no city-specific branch. Feeds larger than ``--max-feed-mb`` are
skipped and reported rather than silently truncated.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import psycopg
import requests
from psycopg.rows import dict_row

CATALOG_URL = "https://files.mobilitydatabase.org/feeds_v2.csv"
CATALOG_CACHE = Path("./data_cache/gtfs/feeds_v2.csv")
FEED_CACHE = Path("./data_cache/gtfs/feeds")
TMP_ROOT = Path("./data_cache/gtfs/tmp")
SOURCE = "Mobility Database GTFS + operator timetable"
CATALOG_SOURCE = "Mobility Database feeds_v2.csv (CC0 metadata)"

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS gtfs_feed (
    feed_id          TEXT PRIMARY KEY,
    provider         TEXT,
    feed_name        TEXT,
    country_iso2     CHAR(2),
    direct_url       TEXT,
    license_url      TEXT,
    status           TEXT,
    bbox_min_lat     DOUBLE PRECISION,
    bbox_max_lat     DOUBLE PRECISION,
    bbox_min_lon     DOUBLE PRECISION,
    bbox_max_lon     DOUBLE PRECISION,
    feed_start_date  TEXT,
    feed_end_date    TEXT,
    content_hash     TEXT,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gtfs_feed_place (
    feed_id         TEXT NOT NULL REFERENCES gtfs_feed(feed_id) ON DELETE CASCADE,
    destination_id  TEXT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
    urban_centre_id BIGINT,
    stops_in_centre INT NOT NULL,
    stops_mapped    INT,
    radius_rule     TEXT NOT NULL,
    PRIMARY KEY (feed_id, destination_id)
);
CREATE INDEX IF NOT EXISTS idx_gtfs_feed_place_dest
    ON gtfs_feed_place (destination_id);
ALTER TABLE gtfs_feed_place ADD COLUMN IF NOT EXISTS stops_mapped INT;

CREATE TABLE IF NOT EXISTS transit_headway (
    destination_id           TEXT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
    feed_id                  TEXT NOT NULL REFERENCES gtfs_feed(feed_id) ON DELETE CASCADE,
    day_type                 TEXT NOT NULL CHECK (day_type IN ('weekday', 'sunday')),
    time_band                TEXT NOT NULL CHECK (time_band IN ('peak', 'midday')),
    radius_rule              TEXT NOT NULL DEFAULT 'urban_centre',
    service_date             DATE NOT NULL,
    stops                    INT NOT NULL,
    median_headway_min       DOUBLE PRECISION,
    pct_stops_headway_le_10  DOUBLE PRECISION,
    pct_stops_headway_le_15  DOUBLE PRECISION,
    pct_stops_headway_le_30  DOUBLE PRECISION,
    first_departure_min      INT,
    last_departure_min       INT,
    departures_at_busiest_stop INT,
    route_type_counts        JSONB,
    wheelchair_coded_pct     DOUBLE PRECISION,
    wheelchair_boardable_pct DOUBLE PRECISION,
    source_url               TEXT,
    license_url              TEXT,
    computed_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (destination_id, feed_id, day_type, time_band, radius_rule)
);
CREATE INDEX IF NOT EXISTS idx_transit_headway_dest
    ON transit_headway (destination_id, day_type, time_band);

CREATE OR REPLACE VIEW transit_city_service AS
SELECT DISTINCT ON (destination_id, day_type, time_band)
       *
FROM transit_headway
ORDER BY destination_id, day_type, time_band,
         CASE WHEN radius_rule = 'urban_centre' THEN 0 ELSE 1 END,
         stops DESC, median_headway_min ASC NULLS LAST;

CREATE OR REPLACE VIEW transit_city_weekday_peak AS
SELECT destination_id, median_headway_min, pct_stops_headway_le_15,
       stops, service_date, license_url
FROM transit_city_service
WHERE day_type = 'weekday' AND time_band = 'peak'
  AND stops >= 50;
"""


def log(message: str) -> None:
    print(message, flush=True)


def parse_gtfs_time(value: str | None) -> int | None:
    """'07:05:00' -> 425; '25:10:00' -> 1510; blank/invalid -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return None
    if hours < 0 or minutes < 0 or minutes > 59 or seconds < 0 or seconds > 59:
        return None
    return hours * 60 + minutes


def month_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def next_weekday(start: date, weekday: int) -> date:
    """Next date on or after ``start`` with Python weekday (Mon=0)."""
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


def active_service_ids(
    calendar: pd.DataFrame | None,
    exceptions: pd.DataFrame | None,
    target: date,
) -> set[str]:
    active: set[str] = set()
    stamp = target.strftime("%Y%m%d")
    weekday_name = target.strftime("%A").lower()
    if calendar is not None and not calendar.empty:
        for row in calendar.to_dict("records"):
            start = str(row.get("start_date") or "").strip()
            end = str(row.get("end_date") or "").strip()
            if start <= stamp <= end and str(row.get(weekday_name, "0")).strip() == "1":
                active.add(str(row.get("service_id")))
    if exceptions is not None and not exceptions.empty:
        for row in exceptions.to_dict("records"):
            if str(row.get("date") or "").strip() != stamp:
                continue
            service_id = str(row.get("service_id")).strip()
            exception_type = str(row.get("exception_type") or "").strip()
            if exception_type == "1":
                active.add(service_id)
            elif exception_type == "2":
                active.discard(service_id)
    return active


def read_csv_small(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(
            path, dtype=str, keep_default_na=False, encoding="utf-8-sig",
            on_bad_lines="skip",
        )
    except UnicodeDecodeError:
        return pd.read_csv(
            path, dtype=str, keep_default_na=False, encoding="latin-1",
            on_bad_lines="skip",
        )


def read_feed_info(path: Path) -> dict[str, str]:
    frame = read_csv_small(path)
    if frame.empty:
        return {}
    return {str(k): str(v) for k, v in frame.iloc[0].to_dict().items()}


def catalog(refresh: bool = False) -> pd.DataFrame:
    CATALOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not CATALOG_CACHE.exists():
        log(f"downloading {CATALOG_URL}")
        response = requests.get(CATALOG_URL, timeout=180)
        response.raise_for_status()
        CATALOG_CACHE.write_bytes(response.content)
    frame = pd.read_csv(CATALOG_CACHE, dtype=str)
    return frame


def clean(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    text = str(value).strip()
    return text or None


def select_feeds(
    frame: pd.DataFrame,
    countries: list[str] | None,
    feed_ids: list[str] | None,
    max_feed_mb: float,
) -> list[dict[str, Any]]:
    frame = frame[
        (frame["data_type"] == "gtfs")
        & (frame["status"] == "active")
        & frame["urls.direct_download"].notna()
    ].copy()
    frame = frame[frame["is_official"].fillna("True").astype(str).str.lower() != "false"]
    auth = pd.to_numeric(frame["urls.authentication_type"], errors="coerce").fillna(0)
    frame = frame[auth == 0]
    frame = frame[~frame["urls.direct_download"].str.contains("apigee", case=False, na=False)]
    if countries:
        frame = frame[frame["location.country_code"].isin(countries)]
    if feed_ids:
        frame = frame[frame["id"].isin(feed_ids)]
    feeds = []
    for row in frame.to_dict("records"):
        size = None
        # The catalog does not carry file size; the downloader enforces the cap.
        feeds.append({
            "id": str(row["id"]),
            "provider": clean(row.get("provider")),
            "name": clean(row.get("name")),
            "country": clean(row.get("location.country_code")),
            "url": clean(row.get("urls.latest") or row.get("urls.direct_download")),
            "direct_url": clean(row.get("urls.direct_download")),
            "license": clean(row.get("urls.license")),
            "bbox": (
                clean(row.get("location.bounding_box.minimum_latitude")),
                clean(row.get("location.bounding_box.maximum_latitude")),
                clean(row.get("location.bounding_box.minimum_longitude")),
                clean(row.get("location.bounding_box.maximum_longitude")),
            ),
            "max_feed_mb": max_feed_mb,
        })
    feeds.sort(key=lambda item: (item["country"] or "", item["id"]))
    return feeds


def download_feed(feed: dict[str, Any], refresh: bool = False) -> tuple[Path | None, str]:
    FEED_CACHE.mkdir(parents=True, exist_ok=True)
    candidates = [feed.get("url")]
    direct = feed.get("direct_url")
    if direct and direct != feed.get("url"):
        candidates.append(direct)
    last_error = "no URL"
    for index, url in enumerate(candidates):
        if not url:
            continue
        suffix = "" if index == 0 else "-direct"
        path = FEED_CACHE / f"{feed['id']}{suffix}.zip"
        if path.exists() and not refresh:
            if zipfile.is_zipfile(path):
                return path, "cached"
            path.unlink(missing_ok=True)
        try:
            with requests.get(url, stream=True, timeout=120) as response:
                response.raise_for_status()
                length = response.headers.get("Content-Length")
                if length and int(length) > float(feed["max_feed_mb"]) * 1024 * 1024:
                    last_error = f"skipped: {int(length)//1024//1024} MB > cap"
                    continue
                total = 0
                with path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        total += len(chunk)
                        if total > float(feed["max_feed_mb"]) * 1024 * 1024:
                            handle.close()
                            path.unlink(missing_ok=True)
                            last_error = f"skipped: exceeded {feed['max_feed_mb']} MB cap"
                            break
                        handle.write(chunk)
                if not path.exists():
                    continue
            if zipfile.is_zipfile(path):
                return path, "downloaded" if index == 0 else "downloaded direct"
            path.unlink(missing_ok=True)
            last_error = "downloaded file is not a zip"
        except Exception as exc:
            path.unlink(missing_ok=True)
            last_error = f"download failed: {exc}"
    return None, last_error


def extract_zip(path: Path) -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="gtfs-", dir=TMP_ROOT))
    with zipfile.ZipFile(path) as archive:
        archive.extractall(folder)
    return folder


def find_file(folder: Path, name: str) -> Path | None:
    direct = folder / name
    if direct.exists():
        return direct
    matches = list(folder.rglob(name))
    return matches[0] if matches else None


def feed_dates(folder: Path, calendar: pd.DataFrame | None) -> tuple[date | None, date | None]:
    info = read_feed_info(folder / "feed_info.txt") if (folder / "feed_info.txt").exists() else {}
    start = month_day(info.get("feed_start_date"))
    end = month_day(info.get("feed_end_date"))
    if calendar is not None and not calendar.empty:
        starts = [month_day(value) for value in calendar.get("start_date", [])]
        ends = [month_day(value) for value in calendar.get("end_date", [])]
        starts = [value for value in starts if value]
        ends = [value for value in ends if value]
        if starts and start is None:
            start = min(starts)
        if ends and end is None:
            end = max(ends)
    return start, end


def target_dates(start: date | None, end: date | None) -> list[tuple[str, date]]:
    today = date.today()
    lower = max(today, start) if start else today
    upper = end
    dates = []
    for label, weekday in (("weekday", 2), ("sunday", 6)):
        candidate = next_weekday(lower, weekday)
        if upper is None or candidate <= upper:
            dates.append((label, candidate))
    return dates


def copy_stops(conn: psycopg.Connection, feed_id: str, stops: pd.DataFrame) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS tmp_gtfs_stops (
                feed_id TEXT, stop_id TEXT, stop_name TEXT,
                lon DOUBLE PRECISION, lat DOUBLE PRECISION,
                wheelchair TEXT, geom geometry(Point, 4326)
            ) ON COMMIT DROP
            """
        )
        cur.execute("TRUNCATE tmp_gtfs_stops")
        with cur.copy(
            "COPY tmp_gtfs_stops (feed_id, stop_id, stop_name, lon, lat, wheelchair) FROM STDIN"
        ) as copy:
            for row in stops.to_dict("records"):
                try:
                    lon = float(row.get("stop_lon"))
                    lat = float(row.get("stop_lat"))
                except (TypeError, ValueError):
                    continue
                copy.write_row((
                    feed_id, str(row.get("stop_id")), row.get("stop_name"),
                    lon, lat, row.get("wheelchair_boarding"),
                ))
        cur.execute(
            "UPDATE tmp_gtfs_stops SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)"
        )


def map_stops(conn: psycopg.Connection, feed_id: str) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH reps AS (
                SELECT DISTINCT ON (m.urban_centre_id)
                       m.urban_centre_id, m.destination_id
                FROM destination_metro m
                JOIN destinations d USING (destination_id)
                ORDER BY m.urban_centre_id, d.population DESC NULLS LAST
            ),
            contained AS (
                SELECT DISTINCT ON (s.stop_id)
                       s.stop_id, r.destination_id, r.urban_centre_id,
                       s.wheelchair,
                       'urban_centre'::text AS radius_rule
                FROM tmp_gtfs_stops s
                JOIN ghsl_urban_centres g ON ST_Intersects(s.geom, g.geom)
                JOIN reps r USING (urban_centre_id)
                ORDER BY s.stop_id, ST_Area(g.geom)
            ),
            nearby AS (
                SELECT DISTINCT ON (s.stop_id)
                       s.stop_id, d.destination_id, m.urban_centre_id,
                       s.wheelchair,
                       'nearest_5km'::text AS radius_rule
                FROM tmp_gtfs_stops s
                JOIN destinations d
                  ON ST_DWithin(s.geom::geography, d.centroid::geography, 5000)
                LEFT JOIN destination_metro m ON m.destination_id = d.destination_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM contained c WHERE c.stop_id = s.stop_id
                )
                ORDER BY s.stop_id,
                         ST_Distance(s.geom::geography, d.centroid::geography)
            )
            SELECT * FROM contained UNION ALL SELECT * FROM nearby
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
    return pd.DataFrame(rows)


def store_feed_places(
    conn: psycopg.Connection, feed_id: str, mapping: pd.DataFrame
) -> None:
    if mapping.empty:
        return
    grouped = (
        mapping.groupby(["destination_id", "urban_centre_id"], dropna=False)
        .agg(
            stops_mapped=("stop_id", "count"),
            stops_in_centre=("radius_rule", lambda values: sum(
                1 for value in values if value == "urban_centre"
            )),
            radius_rule=("radius_rule", lambda values: (
                "urban_centre" if "urban_centre" in set(values) else "nearest_5km"
            )),
        )
        .reset_index()
    )
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gtfs_feed_place WHERE feed_id = %s", (feed_id,))
        for row in grouped.to_dict("records"):
            cur.execute(
                """
                INSERT INTO gtfs_feed_place (
                    feed_id, destination_id, urban_centre_id,
                    stops_in_centre, stops_mapped, radius_rule
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (feed_id, destination_id) DO UPDATE SET
                    urban_centre_id = EXCLUDED.urban_centre_id,
                    stops_in_centre = EXCLUDED.stops_in_centre,
                    stops_mapped = EXCLUDED.stops_mapped,
                    radius_rule = EXCLUDED.radius_rule
                """,
                (
                    feed_id, row["destination_id"],
                    None if pd.isna(row["urban_centre_id"]) else int(row["urban_centre_id"]),
                    int(row["stops_in_centre"]), int(row["stops_mapped"]),
                    row["radius_rule"],
                ),
            )


def compute_service(
    folder: Path,
    feed: dict[str, Any],
    day_type: str,
    service_date: date,
    mapping: pd.DataFrame,
    active_services: set[str],
    conn: psycopg.Connection,
) -> list[dict[str, Any]]:
    if mapping.empty or not active_services:
        return []
    stop_times_path = find_file(folder, "stop_times.txt")
    trips_path = find_file(folder, "trips.txt")
    routes_path = find_file(folder, "routes.txt")
    if not all((stop_times_path, trips_path, routes_path)):
        return []
    service_list = ",".join("'" + value.replace("'", "''") + "'" for value in active_services)
    stop_map = mapping[["stop_id", "destination_id", "radius_rule", "wheelchair"]].copy()
    stop_map["stop_id"] = stop_map["stop_id"].astype(str)
    duck = duckdb.connect()
    try:
        duck.execute("SET memory_limit='6GB'; SET preserve_insertion_order=false;")
        duck.register("stop_map", stop_map)
        route_columns = [
            row[0] for row in duck.execute(
                f"DESCRIBE SELECT * FROM read_csv_auto('{routes_path}', all_varchar=true)"
            ).fetchall()
        ]
        route_type_expr = (
            "COALESCE(TRY_CAST(r.route_type AS INT), 3)"
            if "route_type" in route_columns else "3"
        )
        duck.execute(
            f"""
            CREATE TEMP VIEW trips AS
            SELECT * FROM read_csv_auto('{trips_path}', all_varchar=true)
            WHERE service_id IN ({service_list});
            CREATE TEMP VIEW routes AS
            SELECT * FROM read_csv_auto('{routes_path}', all_varchar=true);
            CREATE TEMP VIEW stop_times AS
            SELECT * FROM read_csv_auto('{stop_times_path}', all_varchar=true);
            """
        )
        base = """
            WITH dep AS (
                SELECT st.stop_id,
                       t.route_id,
                       {route_type_expr} AS route_type,
                       (CAST(split_part(st.departure_time, ':', 1) AS INT) * 60
                        + CAST(split_part(st.departure_time, ':', 2) AS INT)) AS dep_min
                FROM stop_times st
                JOIN trips t USING (trip_id)
                JOIN routes r USING (route_id)
                JOIN stop_map m ON m.stop_id = st.stop_id
                WHERE st.departure_time IS NOT NULL
                  AND st.departure_time <> ''
            ),
            band AS (
                SELECT * FROM dep
                WHERE dep_min BETWEEN {start} AND {end}
            ),
            gaps AS (
                SELECT stop_id, route_id, route_type,
                       median(dep_min - lag_dep) AS med_gap
                FROM (
                    SELECT *,
                           lag(dep_min) OVER (
                               PARTITION BY stop_id, route_id ORDER BY dep_min
                           ) AS lag_dep
                    FROM band
                )
                WHERE lag_dep IS NOT NULL AND dep_min > lag_dep
                GROUP BY 1, 2, 3
            ),
            best AS (
                SELECT stop_id, MIN(med_gap) AS best_gap
                FROM gaps
                GROUP BY 1
            ),
            joined AS (
                SELECT m.destination_id, m.radius_rule, b.best_gap, m.wheelchair
                FROM best b JOIN stop_map m USING (stop_id)
            )
        """
        bands = []
        if day_type == "weekday":
            bands.append(("peak", 420, 540))
            bands.append(("midday", 600, 960))
        else:
            bands.append(("midday", 600, 960))
        results = []
        for band_name, start, end in bands:
            sql = base.format(start=start, end=end, route_type_expr=route_type_expr) + """
                SELECT destination_id, radius_rule,
                       COUNT(*) AS stops,
                       median(best_gap) AS median_headway_min,
                       AVG(CASE WHEN best_gap <= 10 THEN 1 ELSE 0 END) AS pct10,
                       AVG(CASE WHEN best_gap <= 15 THEN 1 ELSE 0 END) AS pct15,
                       AVG(CASE WHEN best_gap <= 30 THEN 1 ELSE 0 END) AS pct30,
                       AVG(CASE WHEN wheelchair IN ('1','2') THEN 1 ELSE 0 END) AS wheelchair_coded,
                       AVG(CASE WHEN wheelchair = '1' THEN 1 ELSE 0 END) AS wheelchair_boardable
                FROM joined
                GROUP BY 1, 2
            """
            for row in duck.execute(sql).fetchall():
                results.append({
                    "destination_id": row[0],
                    "radius_rule": row[1],
                    "time_band": band_name,
                    "stops": int(row[2]),
                    "median_headway_min": float(row[3]) if row[3] is not None else None,
                    "pct10": float(row[4]) if row[4] is not None else None,
                    "pct15": float(row[5]) if row[5] is not None else None,
                    "pct30": float(row[6]) if row[6] is not None else None,
                    "wheelchair_coded": float(row[7]) if row[7] is not None else None,
                    "wheelchair_boardable": float(row[8]) if row[8] is not None else None,
                })
        # span and busiest stop over the whole day
        span_sql = """
            WITH dep AS (
                SELECT st.stop_id, t.route_id,
                       (CAST(split_part(st.departure_time, ':', 1) AS INT) * 60
                        + CAST(split_part(st.departure_time, ':', 2) AS INT)) AS dep_min
                FROM stop_times st
                JOIN trips t USING (trip_id)
                JOIN stop_map m ON m.stop_id = st.stop_id
                WHERE st.departure_time IS NOT NULL
                  AND st.departure_time <> ''
            ),
            per_stop AS (
                SELECT m.destination_id, dep.stop_id,
                       COUNT(*) AS departures,
                       MIN(dep_min) AS first_dep, MAX(dep_min) AS last_dep
                FROM dep JOIN stop_map m USING (stop_id)
                GROUP BY 1, 2
            )
            SELECT destination_id,
                   MIN(first_dep) AS first_dep,
                   MAX(last_dep) AS last_dep,
                   MAX(departures) AS busiest
            FROM per_stop GROUP BY 1
        """
        span = {}
        for row in duck.execute(span_sql).fetchall():
            span[row[0]] = {
                "first": int(row[1]) if row[1] is not None else None,
                "last": int(row[2]) if row[2] is not None else None,
                "busiest": int(row[3]) if row[3] is not None else None,
            }
        # route-type mix and wheelchair coding
        route_counts = defaultdict(dict)
        route_sql = """
            SELECT DISTINCT m.destination_id, {route_type_expr} AS route_type,
                   t.route_id
            FROM stop_times st
            JOIN trips t USING (trip_id)
            JOIN routes r USING (route_id)
            JOIN stop_map m ON m.stop_id = st.stop_id
            WHERE st.departure_time IS NOT NULL
        """
        route_sql = route_sql.format(route_type_expr=route_type_expr)
        for row in duck.execute(route_sql).fetchall():
            route_counts[row[0]][row[1]] = route_counts[row[0]].get(row[1], 0) + 1
        for item in results:
            extra = span.get(item["destination_id"], {})
            item["first_departure_min"] = extra.get("first")
            item["last_departure_min"] = extra.get("last")
            item["departures_at_busiest_stop"] = extra.get("busiest")
            item["route_type_counts"] = route_counts.get(item["destination_id"], {})
        return results
    finally:
        duck.close()


def store_feed_meta(conn: psycopg.Connection, feed: dict[str, Any], path: Path | None,
                    folder: Path | None, calendar: pd.DataFrame | None) -> tuple[date | None, date | None]:
    start = end = None
    if folder:
        start, end = feed_dates(folder, calendar)
    digest = None
    if path and path.exists():
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gtfs_feed (
                feed_id, provider, feed_name, country_iso2, direct_url,
                license_url, status, bbox_min_lat, bbox_max_lat, bbox_min_lon,
                bbox_max_lon, feed_start_date, feed_end_date, content_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (feed_id) DO UPDATE SET
                provider = EXCLUDED.provider,
                feed_name = EXCLUDED.feed_name,
                country_iso2 = EXCLUDED.country_iso2,
                direct_url = EXCLUDED.direct_url,
                license_url = EXCLUDED.license_url,
                bbox_min_lat = EXCLUDED.bbox_min_lat,
                bbox_max_lat = EXCLUDED.bbox_max_lat,
                bbox_min_lon = EXCLUDED.bbox_min_lon,
                bbox_max_lon = EXCLUDED.bbox_max_lon,
                feed_start_date = EXCLUDED.feed_start_date,
                feed_end_date = EXCLUDED.feed_end_date,
                content_hash = EXCLUDED.content_hash,
                fetched_at = NOW()
            """,
            (
                feed["id"], feed.get("provider"), feed.get("name"),
                feed.get("country"), feed.get("direct_url") or feed.get("url"),
                feed.get("license"),
                *feed["bbox"], start.isoformat() if start else None,
                end.isoformat() if end else None, digest,
            ),
        )
    return start, end


def process_feed(conn: psycopg.Connection, feed: dict[str, Any], refresh: bool) -> dict[str, Any]:
    feed_id = feed["id"]
    log(f"[{feed_id}] {feed.get('provider') or ''} {feed.get('country') or ''}")
    path, status = download_feed(feed, refresh)
    if path is None:
        log(f"  {status}")
        return {"feed": feed_id, "status": status}
    folder = extract_zip(path)
    try:
        calendar = read_csv_small(folder / "calendar.txt") if (folder / "calendar.txt").exists() else pd.DataFrame()
        exceptions = read_csv_small(folder / "calendar_dates.txt") if (folder / "calendar_dates.txt").exists() else pd.DataFrame()
        start, end = store_feed_meta(conn, feed, path, folder, calendar)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM gtfs_feed_place WHERE feed_id = %s", (feed_id,))
            cur.execute("DELETE FROM transit_headway WHERE feed_id = %s", (feed_id,))
        targets = target_dates(start, end)
        if not targets:
            log("  no in-range service date")
            return {"feed": feed_id, "status": "no in-range date"}
        stops_path = find_file(folder, "stops.txt")
        if stops_path is None:
            return {"feed": feed_id, "status": "no stops.txt"}
        stops = read_csv_small(stops_path)
        copy_stops(conn, feed_id, stops)
        mapping = map_stops(conn, feed_id)
        store_feed_places(conn, feed_id, mapping)
        if mapping.empty:
            log("  no stops mapped to a city")
            return {"feed": feed_id, "status": "no mapped stops"}
        rows = 0
        for day_type, service_date in targets:
            services = active_service_ids(calendar, exceptions, service_date)
            if not services:
                log(f"  {day_type} {service_date}: no active services")
                continue
            for item in compute_service(
                folder, feed, day_type, service_date, mapping, services, conn
            ):
                # only the dominant geography band is stored in the headline table
                if item["radius_rule"] != "urban_centre" and len(mapping) > 0:
                    # keep it; a city without a centre uses the nearest-5km band
                    pass
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO transit_headway (
                            destination_id, feed_id, day_type, time_band,
                            radius_rule, service_date, stops, median_headway_min,
                            pct_stops_headway_le_10, pct_stops_headway_le_15,
                            pct_stops_headway_le_30, first_departure_min,
                            last_departure_min, departures_at_busiest_stop,
                            route_type_counts, wheelchair_coded_pct,
                            wheelchair_boardable_pct, source_url, license_url
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (destination_id, feed_id, day_type, time_band, radius_rule)
                        DO UPDATE SET
                            service_date = EXCLUDED.service_date,
                            stops = EXCLUDED.stops,
                            median_headway_min = EXCLUDED.median_headway_min,
                            pct_stops_headway_le_10 = EXCLUDED.pct_stops_headway_le_10,
                            pct_stops_headway_le_15 = EXCLUDED.pct_stops_headway_le_15,
                            pct_stops_headway_le_30 = EXCLUDED.pct_stops_headway_le_30,
                            first_departure_min = EXCLUDED.first_departure_min,
                            last_departure_min = EXCLUDED.last_departure_min,
                            departures_at_busiest_stop = EXCLUDED.departures_at_busiest_stop,
                            route_type_counts = EXCLUDED.route_type_counts,
                            source_url = EXCLUDED.source_url,
                            license_url = EXCLUDED.license_url,
                            computed_at = NOW()
                        """,
                        (
                            item["destination_id"], feed_id, day_type,
                            item["time_band"], item["radius_rule"],
                            service_date, item["stops"],
                            item["median_headway_min"], item["pct10"],
                            item["pct15"], item["pct30"],
                            item["first_departure_min"], item["last_departure_min"],
                            item["departures_at_busiest_stop"],
                            json.dumps(item["route_type_counts"]),
                            item["wheelchair_coded"], item["wheelchair_boardable"],
                            feed.get("url"), feed.get("license"),
                        ),
                    )
                rows += 1
        conn.commit()
        log(f"  wrote {rows} service rows for {len(mapping)} stops")
        return {
            "feed": feed_id, "status": "ok", "mapped_stops": len(mapping),
            "service_rows": rows, "download": status,
        }
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM gtfs_feed")
            print({"gtfs_feed": cur.fetchone()["n"]})
            cur.execute("SELECT count(*) n FROM gtfs_feed_place")
            print({"gtfs_feed_place": cur.fetchone()["n"]})
            cur.execute("SELECT count(*) n FROM transit_headway")
            print({"transit_headway": cur.fetchone()["n"]})
            cur.execute(
                """
                SELECT d.name, t.day_type, t.time_band, t.stops,
                       round(t.median_headway_min::numeric, 1) AS median_headway_min,
                       round((t.pct_stops_headway_le_15 * 100)::numeric, 1) AS pct_le_15,
                       t.first_departure_min, t.last_departure_min, t.feed_id
                FROM transit_city_service t
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Trento', 'Paris')
                ORDER BY d.name, t.day_type, t.time_band
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--countries", default=None, help="comma-separated ISO2 codes")
    parser.add_argument("--feed-ids", default=None, help="comma-separated catalog feed ids")
    parser.add_argument("--max-feed-mb", type=float, default=500.0)
    parser.add_argument("--limit-feeds", type=int, default=None)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    countries = [c.strip().upper() for c in args.countries.split(",")] if args.countries else None
    feed_ids = [f.strip() for f in args.feed_ids.split(",")] if args.feed_ids else None
    feeds = select_feeds(
        catalog(refresh=args.refresh), countries, feed_ids, args.max_feed_mb
    )
    if args.limit_feeds:
        feeds = feeds[: args.limit_feeds]
    log(f"{len(feeds)} feeds selected")
    results = []
    with psycopg.connect(args.db_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        for feed in feeds:
            try:
                results.append(process_feed(conn, feed, args.refresh))
            except Exception as exc:
                conn.rollback()
                log(f"  FAILED: {exc}")
                results.append({"feed": feed["id"], "status": f"failed: {exc}"})
    ok = sum(1 for item in results if item.get("status") == "ok")
    log(f"finished: {ok}/{len(results)} feeds ok")
    for item in results:
        if item.get("status") != "ok":
            log(f"  {item['feed']}: {item['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
