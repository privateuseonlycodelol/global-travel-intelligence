#!/usr/bin/env python3
"""
Global land-cover fractions from ESA WorldCover 2021 at 10 m.

For every city, this samples two discs:

* **1 km** — the daily walk. Native 10 m classes, aggregated to fractions.
* **15 km** — the outing. The same classes, read through COG overviews at
  roughly 100 m so the job stays bounded.

Classes are kept separate: tree, shrub, grass, crop, built-up, bare, snow,
water, wetland, mangrove and moss. Cropland and built-up stay visible so a
farming town is not described as wild, and a dense city is not described as
green because its surrounding region is.

The tiles are read remotely from the open AWS bucket; only the city windows are
transferred. No global 100 GB download is required.

Source: ESA WorldCover 2021 v200, CC BY 4.0.
https://esa-worldcover.org/en/data-access
Citation: © ESA WorldCover project / contains modified Copernicus Sentinel data
(2021).

Usage
-----
    python bootstrap_land_cover.py
    python bootstrap_land_cover.py --limit 100
    python bootstrap_land_cover.py --report
"""

from __future__ import annotations

import argparse
import math
import os
from collections import Counter
from typing import Any

import numpy as np
import psycopg
import rasterio
from psycopg.rows import dict_row
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

BUCKET = "https://esa-worldcover.s3.amazonaws.com/v200/2021/map"
TILE = "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
SOURCE = "ESA WorldCover 2021 v200 (10 m)"
SOURCE_URL = "https://esa-worldcover.org/en/data-access"
PERIOD = "2021"

CLASS_FRACTIONS = {
    10: "tree_fraction",
    20: "shrub_fraction",
    30: "grassland_fraction",
    40: "cropland_fraction",
    50: "built_up_fraction",
    60: "bare_fraction",
    70: "snow_fraction",
    80: "water_fraction",
    90: "wetland_fraction",
    95: "mangrove_fraction",
    100: "moss_fraction",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS destination_land_cover (
    destination_id          TEXT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
    radius_m                INTEGER NOT NULL CHECK (radius_m IN (1000, 15000)),
    tree_fraction           DOUBLE PRECISION,
    shrub_fraction          DOUBLE PRECISION,
    grassland_fraction      DOUBLE PRECISION,
    cropland_fraction       DOUBLE PRECISION,
    built_up_fraction       DOUBLE PRECISION,
    bare_fraction           DOUBLE PRECISION,
    snow_fraction           DOUBLE PRECISION,
    permanent_water_fraction DOUBLE PRECISION,
    seasonal_water_fraction DOUBLE PRECISION,
    water_fraction          DOUBLE PRECISION,
    wetland_fraction        DOUBLE PRECISION,
    mangrove_fraction       DOUBLE PRECISION,
    moss_fraction           DOUBLE PRECISION,
    valid_pixel_count       INTEGER,
    source                  TEXT NOT NULL,
    source_url              TEXT NOT NULL,
    observed_period         TEXT NOT NULL,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (destination_id, radius_m)
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def tile_name(lat: float, lon: float) -> str:
    lat0 = int(math.floor((lat + 90.0) / 3.0) * 3 - 90)
    lon0 = int(math.floor((lon + 180.0) / 3.0) * 3 - 180)
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return f"{ns}{abs(lat0):02d}{ew}{abs(lon0):03d}"


class TileCache:
    def __init__(self) -> None:
        self.datasets: dict[str, rasterio.DatasetReader] = {}

    def get(self, lat: float, lon: float) -> rasterio.DatasetReader | None:
        name = tile_name(lat, lon)
        if name not in self.datasets:
            url = f"{BUCKET}/{TILE.format(tile=name)}"
            try:
                self.datasets[name] = rasterio.open(url)
            except Exception:
                self.datasets[name] = None  # type: ignore[assignment]
        return self.datasets[name]

    def close(self) -> None:
        for dataset in self.datasets.values():
            if dataset is not None:
                dataset.close()


def _tile_origins(min_lat: float, max_lat: float, min_lon: float, max_lon: float):
    lat0 = int(math.floor((min_lat + 90.0) / 3.0) * 3 - 90)
    lat1 = int(math.floor((max_lat + 90.0) / 3.0) * 3 - 90)
    lon0 = int(math.floor((min_lon + 180.0) / 3.0) * 3 - 180)
    lon1 = int(math.floor((max_lon + 180.0) / 3.0) * 3 - 180)
    for lat in range(lat0, lat1 + 1, 3):
        for lon in range(lon0, lon1 + 1, 3):
            yield lat, lon


def _circle_counts(
    dataset: rasterio.DatasetReader, left: float, bottom: float,
    right: float, top: float, lat: float, lon: float, radius_m: float,
    shape: tuple[int, int],
) -> tuple[Counter, int]:
    left = max(left, dataset.bounds.left)
    right = min(right, dataset.bounds.right)
    bottom = max(bottom, dataset.bounds.bottom)
    top = min(top, dataset.bounds.top)
    if right <= left or top <= bottom:
        return Counter(), 0
    window = from_bounds(left, bottom, right, top, dataset.transform)
    window = window.round_offsets().round_lengths()
    if window.width <= 0 or window.height <= 0:
        return Counter(), 0
    height_fraction = min(1.0, (top - bottom) / max(1e-9, (2 * radius_m / 111320.0)))
    width_fraction = min(1.0, (right - left) / max(1e-9, (2 * radius_m / (111320.0 * max(0.05, abs(math.cos(math.radians(lat))))))))
    out_h = max(1, min(shape[0], int(round(shape[0] * height_fraction))))
    out_w = max(1, min(shape[1], int(round(shape[1] * width_fraction))))
    data = dataset.read(
        1, window=window, out_shape=(out_h, out_w),
        resampling=Resampling.nearest,
    )
    transform = rasterio.transform.from_bounds(
        left, bottom, right, top, max(1, out_w), max(1, out_h)
    )
    cols, rows = np.meshgrid(
        np.arange(out_w) + 0.5, np.arange(out_h) + 0.5
    )
    xs, ys = transform * (cols, rows)
    p1 = math.radians(lat)
    p2 = np.radians(ys)
    dphi = p2 - p1
    dlambda = np.radians(xs - lon)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    distance = 2 * 6371008.8 * np.arcsin(np.minimum(1.0, np.sqrt(a)))
    mask = distance <= radius_m
    valid = mask & (data != 0)
    counts = Counter(int(value) for value in data[valid])
    return counts, int(valid.sum())


def sample_city(
    cache: TileCache, lat: float, lon: float, radius_m: float,
    shape: tuple[int, int],
) -> tuple[dict[str, float | None], int]:
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(0.05, abs(math.cos(math.radians(lat)))))
    left, right = lon - dlon, lon + dlon
    bottom, top = lat - dlat, lat + dlat
    total: Counter = Counter()
    valid_total = 0
    for lat0, lon0 in _tile_origins(bottom, top, left, right):
        centre_lat = lat0 + 1.5
        centre_lon = lon0 + 1.5
        dataset = cache.get(centre_lat, centre_lon)
        if dataset is None:
            continue
        counts, valid = _circle_counts(
            dataset, left, bottom, right, top, lat, lon, radius_m, shape
        )
        total.update(counts)
        valid_total += valid
    result = {name: None for name in CLASS_FRACTIONS.values()}
    result["permanent_water_fraction"] = None
    result["seasonal_water_fraction"] = None
    if valid_total:
        for code, name in CLASS_FRACTIONS.items():
            result[name] = total.get(code, 0) / valid_total
    return result, valid_total


def load(database_url: str, limit: int | None = None) -> dict[str, Any]:
    cache = TileCache()
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
                cur.execute("SELECT destination_id, lat, lon FROM destinations ORDER BY population DESC")
                cities = cur.fetchall()
                if limit:
                    cities = cities[:limit]
                cur.execute("DELETE FROM destination_land_cover")
                rows = []
                for index, city in enumerate(cities, 1):
                    lat, lon = float(city["lat"]), float(city["lon"])
                    for radius, shape in ((1000, (200, 200)), (15000, (150, 150))):
                        fractions, valid = sample_city(cache, lat, lon, radius, shape)
                        rows.append((
                            city["destination_id"], radius,
                            fractions.get("tree_fraction"),
                            fractions.get("shrub_fraction"),
                            fractions.get("grassland_fraction"),
                            fractions.get("cropland_fraction"),
                            fractions.get("built_up_fraction"),
                            fractions.get("bare_fraction"),
                            fractions.get("snow_fraction"),
                            fractions.get("permanent_water_fraction"),
                            fractions.get("seasonal_water_fraction"),
                            fractions.get("water_fraction"),
                            fractions.get("wetland_fraction"),
                            fractions.get("mangrove_fraction"),
                            fractions.get("moss_fraction"),
                            valid,
                            SOURCE, SOURCE_URL, PERIOD,
                        ))
                    if index % 100 == 0:
                        log(f"  {index:,}/{len(cities):,} cities")
                cur.executemany(
                    """
                    INSERT INTO destination_land_cover (
                        destination_id, radius_m, tree_fraction, shrub_fraction,
                        grassland_fraction, cropland_fraction, built_up_fraction,
                        bare_fraction, snow_fraction, permanent_water_fraction,
                        seasonal_water_fraction, water_fraction, wetland_fraction,
                        mangrove_fraction, moss_fraction, valid_pixel_count,
                        source, source_url, observed_period
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            conn.commit()
    finally:
        cache.close()
    return {"cities": len(cities), "rows": len(rows)}


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM destination_land_cover")
            print({"land_cover_rows": cur.fetchone()["n"]})
            cur.execute(
                """
                SELECT d.name, c.radius_m, c.tree_fraction, c.grassland_fraction,
                       c.cropland_fraction, c.built_up_fraction, c.bare_fraction,
                       c.water_fraction, c.wetland_fraction, c.valid_pixel_count
                FROM destination_land_cover c
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Trento', 'Paris', 'Innsbruck', 'Dubai', 'Annecy')
                ORDER BY d.name, c.radius_m
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
