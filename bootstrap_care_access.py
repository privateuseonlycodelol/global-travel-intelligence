#!/usr/bin/env python3
"""
Weiss et al. (2020) global motorized travel time to healthcare (Care Access 7%).

Downloads the Malaria Atlas Project 1 km global motorized travel-time raster
(Weiss et al. 2020, Nature Medicine; CC BY 4.0) and reduces it to one travel
time per destination:

* destinations matched to a GHSL urban centre get the median valid travel time
  (minutes) of all raster pixels inside the centre polygon;
* unmatched destinations get a 3x3 median around the destination point;
* polygons smaller than one raster pixel fall back to the point sample.

Council anchor (100 at <= 10 min, 0 at >= 60 min):

    care_access_score = clamp(100 * (60 - median_travel_min) / 50, 0, 100)

NULL when the raster has no valid (non-negative, non-nodata) pixels.

Source: Weiss et al. (2020) Global maps of travel time to healthcare facilities,
Nature Medicine 26:1835-1838, Malaria Atlas Project, CC BY 4.0.
https://data.malariaatlas.org/geoserver/ows?service=CSW&version=2.0.1&request=DirectDownload&ResourceId=Explorer:2020_motorized_travel_time_to_healthcare

Usage
-----
    uv run --with-requirements requirements.txt python bootstrap_care_access.py --db-url "$DATABASE_URL"
    uv run --with-requirements requirements.txt python bootstrap_care_access.py --db-url "$DATABASE_URL" --report
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import time
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
import rasterio
import requests
from psycopg.rows import dict_row
from rasterio.features import geometry_mask
from rasterio.windows import Window

CACHE_DIR = Path("./data_cache/health")
WEISS_URL = (
    "https://data.malariaatlas.org/geoserver/ows?service=CSW&version=2.0.1"
    "&request=DirectDownload"
    "&ResourceId=Explorer:2020_motorized_travel_time_to_healthcare"
)
SOURCE = "Weiss et al. (2020) motorized travel time to healthcare (MAP 1 km)"
SOURCE_URL = (
    "https://data.malariaatlas.org/geoserver/ows?service=CSW&version=2.0.1"
    "&request=DirectDownload"
    "&ResourceId=Explorer:2020_motorized_travel_time_to_healthcare"
)
OBSERVED_PERIOD = "2020"
GEOTIFF_NAME = "weiss_2020_motorized_travel_time_to_healthcare.tif"
ARCHIVE_NAME = "weiss_2020_motorized_travel_time_to_healthcare.download"
GEOTIFF_SUFFIXES = (".tif", ".tiff", ".geotiff")
ANCHOR_NEAR_MIN = 10.0
ANCHOR_FAR_MIN = 60.0
MAX_POLYGON_PIXELS = 25_000_000

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS destination_care_access (
    destination_id      TEXT PRIMARY KEY
                        REFERENCES destinations(destination_id) ON DELETE CASCADE,
    urban_centre_id     BIGINT,
    median_travel_min   DOUBLE PRECISION,
    care_access_score   DOUBLE PRECISION,
    sample_method       TEXT NOT NULL,
    source              TEXT NOT NULL,
    source_url          TEXT NOT NULL,
    observed_period     TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_destination_care_access_uc
    ON destination_care_access (urban_centre_id);
"""

DESTINATION_SQL = """
SELECT d.destination_id,
       d.lat,
       d.lon,
       dm.urban_centre_id,
       ST_AsGeoJSON(uc.geom) AS geom_json
FROM destinations d
LEFT JOIN destination_metro dm USING (destination_id)
LEFT JOIN ghsl_urban_centres uc USING (urban_centre_id)
ORDER BY d.destination_id
"""

INSERT_SQL = """
INSERT INTO destination_care_access (
    destination_id, urban_centre_id, median_travel_min, care_access_score,
    sample_method, source, source_url, observed_period
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

STATS_SQL = """
SELECT count(*) AS destinations,
       count(median_travel_min) AS with_data,
       count(*) FILTER (WHERE median_travel_min <= 30) AS within_30_min,
       count(*) FILTER (WHERE care_access_score >= 100) AS score_100,
       count(*) FILTER (WHERE care_access_score = 0) AS score_0,
       round(avg(care_access_score)::numeric, 2) AS avg_score
FROM destination_care_access
"""


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def compute_care_access_score(median_min: float | None) -> float | None:
    """Council anchor: 100 at <=10 min, 50 at 35 min, 0 at >=60 min."""
    if median_min is None:
        return None
    try:
        minutes = float(median_min)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(minutes) or minutes < 0.0:
        return None
    span = ANCHOR_FAR_MIN - ANCHOR_NEAR_MIN
    return clamp(100.0 * (ANCHOR_FAR_MIN - minutes) / span)


def valid_travel_times(
    values: Any, nodata: float | None = None
) -> np.ndarray:
    """Return finite, non-negative travel times, excluding the raster nodata."""
    array = np.asarray(values, dtype="float64")
    valid = np.isfinite(array)
    if nodata is not None:
        try:
            nodata_value = float(nodata)
        except (TypeError, ValueError):
            nodata_value = None
        if nodata_value is not None and math.isfinite(nodata_value):
            valid &= array != nodata_value
    valid &= array >= 0.0
    return array[valid]


def median_travel_time(
    values: Any, nodata: float | None = None
) -> float | None:
    """Median valid travel time in minutes, or None when no pixel is usable."""
    valid = valid_travel_times(values, nodata)
    if valid.size == 0:
        return None
    return float(np.median(valid))


def _download_file(
    url: str,
    dest: Path,
    *,
    retries: int = 3,
    timeout: tuple[int, int] = (30, 300),
) -> Path:
    """Stream a URL to ``dest`` with retries and a ``.part`` staging file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                written = 0
                next_report = 100 * 1024 * 1024
                with part.open("wb") as handle:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        written += len(chunk)
                        if written >= next_report:
                            if total:
                                print(
                                    f"  downloaded {written / 2**20:.0f}"
                                    f"/{total / 2**20:.0f} MiB",
                                    flush=True,
                                )
                            else:
                                print(
                                    f"  downloaded {written / 2**20:.0f} MiB",
                                    flush=True,
                                )
                            next_report += 100 * 1024 * 1024
            part.replace(dest)
            return dest
        except Exception as exc:  # network/HTTP failures are retried
            last_error = exc
            part.unlink(missing_ok=True)
            if attempt < retries:
                delay = 2 ** (attempt - 1)
                print(f"download failed ({exc}); retrying in {delay}s", flush=True)
                time.sleep(delay)
    if last_error is None:
        raise RuntimeError(f"download of {url} failed without an error")
    raise last_error


def _looks_like_geotiff(path: Path) -> bool:
    """True for classic and BigTIFF files, whatever their file extension."""
    try:
        with Path(path).open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return False
    return magic in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")


def materialize_geotiff(source: Path, target: Path) -> Path:
    """Turn a downloaded zip archive or direct GeoTIFF into ``target``."""
    source = Path(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            members = [
                item
                for item in archive.infolist()
                if not item.is_dir()
                and item.filename.lower().endswith(GEOTIFF_SUFFIXES)
                and not item.filename.startswith("__MACOSX/")
            ]
            if not members:
                raise ValueError(f"no GeoTIFF member found in {source}")
            member = max(members, key=lambda item: item.file_size)
            part = target.with_name(target.name + ".part")
            try:
                with archive.open(member) as src, part.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1 << 20)
                part.replace(target)
            finally:
                part.unlink(missing_ok=True)
        return target
    if source.suffix.lower() in GEOTIFF_SUFFIXES or _looks_like_geotiff(source):
        if source.resolve() != target.resolve():
            shutil.move(str(source), str(target))
        return target
    raise ValueError(f"expected a zip archive or GeoTIFF, got {source}")


def ensure_geotiff(cache_dir: Path = CACHE_DIR, url: str = WEISS_URL) -> Path:
    """Return the cached 1 km travel-time GeoTIFF, downloading it if needed."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / GEOTIFF_NAME
    if target.exists() and target.stat().st_size > 0:
        return target
    download = cache_dir / ARCHIVE_NAME
    if not download.exists() or download.stat().st_size == 0:
        print(f"downloading {url}", flush=True)
        _download_file(url, download)
    return materialize_geotiff(download, target)


def geometry_bounds(
    geometry: dict[str, Any] | None,
) -> tuple[float, float, float, float] | None:
    """Return (minx, miny, maxx, maxy) for a GeoJSON geometry."""
    if not geometry:
        return None
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if (
                len(node) >= 2
                and isinstance(node[0], (int, float))
                and isinstance(node[1], (int, float))
            ):
                xs.append(float(node[0]))
                ys.append(float(node[1]))
                return
            for child in node:
                walk(child)

    walk(geometry.get("coordinates"))
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def sample_point(
    dataset: rasterio.io.DatasetReader,
    lat: float | None,
    lon: float | None,
    *,
    kernel: int = 3,
) -> float | None:
    """Median of a small pixel window around (lat, lon), or None."""
    if lat is None or lon is None:
        return None
    try:
        row, col = dataset.index(float(lon), float(lat))
    except (TypeError, ValueError):
        return None
    row = int(row)
    col = int(col)
    if not (0 <= row < dataset.height and 0 <= col < dataset.width):
        return None
    half = max(1, int(kernel)) // 2
    row0 = max(0, row - half)
    row1 = min(dataset.height, row + half + 1)
    col0 = max(0, col - half)
    col1 = min(dataset.width, col + half + 1)
    values = dataset.read(
        1, window=Window(col0, row0, col1 - col0, row1 - row0)
    )
    return median_travel_time(values, dataset.nodata)


def median_travel_time_in_geometry(
    dataset: rasterio.io.DatasetReader,
    geometry: dict[str, Any] | None,
) -> float | None:
    """Median valid travel time of pixels whose centre lies inside geometry."""
    bounds = geometry_bounds(geometry)
    if bounds is None:
        return None
    minx, miny, maxx, maxy = bounds
    # Dateline-spanning or continent-sized boxes are not urban centres; the
    # caller falls back to a point sample instead of reading a huge window.
    if maxx - minx > 180.0 or maxy - miny > 90.0:
        return None
    raster_bounds = dataset.bounds
    minx = max(minx, raster_bounds.left)
    maxx = min(maxx, raster_bounds.right)
    miny = max(miny, raster_bounds.bottom)
    maxy = min(maxy, raster_bounds.top)
    if maxx <= minx or maxy <= miny:
        return None

    window = dataset.window(minx, miny, maxx, maxy)
    row0 = max(0, int(math.floor(window.row_off)))
    col0 = max(0, int(math.floor(window.col_off)))
    row1 = min(dataset.height, int(math.ceil(window.row_off + window.height)))
    col1 = min(dataset.width, int(math.ceil(window.col_off + window.width)))
    if row1 <= row0 or col1 <= col0:
        return None
    if (row1 - row0) * (col1 - col0) > MAX_POLYGON_PIXELS:
        return None

    read_window = Window(col0, row0, col1 - col0, row1 - row0)
    values = dataset.read(1, window=read_window)
    transform = dataset.window_transform(read_window)
    try:
        inside = geometry_mask(
            [geometry],
            out_shape=values.shape,
            transform=transform,
            invert=True,
            all_touched=False,
        )
    except (TypeError, ValueError, rasterio.errors.RasterioError):
        return None
    if not inside.any():
        return None
    return median_travel_time(values[inside], dataset.nodata)


def sample_urban_centre(
    dataset: rasterio.io.DatasetReader,
    geometry: dict[str, Any] | None,
    lat: float | None,
    lon: float | None,
) -> tuple[float | None, str]:
    """Polygon median, falling back to a point sample for tiny polygons."""
    if geometry is None:
        return sample_point(dataset, lat, lon), "point_3x3"
    median = median_travel_time_in_geometry(dataset, geometry)
    if median is not None:
        return median, "urban_centre_median"
    return sample_point(dataset, lat, lon), "point_fallback"


def load(database_url: str, cache_dir: Path = CACHE_DIR) -> dict[str, Any]:
    raster_path = ensure_geotiff(Path(cache_dir))
    print(f"sampling {raster_path}", flush=True)

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(DESTINATION_SQL)
            destinations = cur.fetchall()
    print(f"loaded {len(destinations):,} destinations", flush=True)

    methods: Counter[str] = Counter()
    centre_cache: dict[int, tuple[float | None, str]] = {}
    records: list[tuple[Any, ...]] = []
    started = time.time()
    with rasterio.open(raster_path) as dataset:
        for index, row in enumerate(destinations, 1):
            lat = float(row["lat"]) if row["lat"] is not None else None
            lon = float(row["lon"]) if row["lon"] is not None else None
            centre_id = (
                int(row["urban_centre_id"])
                if row["urban_centre_id"] is not None
                else None
            )
            if centre_id is not None:
                if centre_id not in centre_cache:
                    geometry = (
                        json.loads(row["geom_json"])
                        if row["geom_json"]
                        else None
                    )
                    centre_cache[centre_id] = sample_urban_centre(
                        dataset, geometry, lat, lon
                    )
                median, method = centre_cache[centre_id]
            else:
                median = sample_point(dataset, lat, lon)
                method = "point_3x3"
            if median is None:
                method = "none"
            score = compute_care_access_score(median)
            methods[method] += 1
            records.append(
                (
                    row["destination_id"],
                    centre_id,
                    None if median is None else round(median, 2),
                    None if score is None else round(score, 2),
                    method,
                    SOURCE,
                    SOURCE_URL,
                    OBSERVED_PERIOD,
                )
            )
            if index % 1000 == 0 or index == len(destinations):
                elapsed = time.time() - started
                print(
                    f"  sampled {index:,}/{len(destinations):,} "
                    f"({elapsed:.0f}s)",
                    flush=True,
                )

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("TRUNCATE TABLE destination_care_access")
            cur.executemany(INSERT_SQL, records)
            cur.execute(STATS_SQL)
            stats = dict(cur.fetchone())
        conn.commit()

    return {
        "raster": str(raster_path),
        "written": len(records),
        "methods": dict(methods),
        "urban_centres_sampled": len(centre_cache),
        "elapsed_sec": round(time.time() - started, 1),
        **stats,
    }


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(STATS_SQL)
            print("coverage", dict(cur.fetchone()))
            cur.execute(
                """
                SELECT sample_method, count(*) AS destinations
                FROM destination_care_access
                GROUP BY sample_method
                ORDER BY destinations DESC
                """
            )
            for row in cur.fetchall():
                print("method", dict(row))
            for label, order in (
                ("best care access", "ASC"),
                ("worst care access", "DESC"),
            ):
                print(label)
                cur.execute(
                    f"""
                    SELECT d.name, d.country_iso2, c.median_travel_min,
                           c.care_access_score, c.sample_method
                    FROM destination_care_access c
                    JOIN destinations d USING (destination_id)
                    WHERE c.care_access_score IS NOT NULL
                    ORDER BY c.median_travel_min {order}, d.population DESC
                    LIMIT 10
                    """
                )
                for row in cur.fetchall():
                    print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, cache_dir=args.cache_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
