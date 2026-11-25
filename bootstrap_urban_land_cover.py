#!/usr/bin/env python3
"""
Urban-area land cover from ESA WorldCover inside the GHSL urban-centre polygon.

The 1 km point disc is not a citywide canopy measurement. This script masks the
WorldCover 2021 10 m classification to the GHSL UCDB R2024A urban-centre
polygon, so the tree/shrub/grass shares describe the built-up urban area rather
than one neighbourhood block.

It also exports the 407 ranked urban-centre polygons as GeoJSON for
reproducibility.

Source: ESA WorldCover 2021 v200, CC BY 4.0.
https://esa-worldcover.org/en/data-access

Usage
-----
    python bootstrap_urban_land_cover.py --limit 3
    python bootstrap_urban_land_cover.py
    python bootstrap_urban_land_cover.py --report
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
import rasterio
from psycopg.rows import dict_row
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from rasterio.enums import Resampling

BUCKET = "https://esa-worldcover.s3.amazonaws.com/v200/2021/map"
TILE = "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
SOURCE = "ESA WorldCover 2021 v200 (10 m), masked to GHSL urban centre"
SOURCE_URL = "https://esa-worldcover.org/en/data-access"
PERIOD = "2021"
TARGET_RES_M = 100.0
MAX_SIDE = 2000
OUT = Path(__file__).resolve().parent / "reports"

CLASS_COLUMNS = {
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
DROP VIEW IF EXISTS destination_urban_greenery CASCADE;
DROP VIEW IF EXISTS destination_surrounding_nature CASCADE;
DROP TABLE IF EXISTS destination_urban_land_cover;

CREATE TABLE destination_urban_land_cover (
    destination_id      TEXT PRIMARY KEY
                        REFERENCES destinations(destination_id) ON DELETE CASCADE,
    urban_centre_id     BIGINT NOT NULL,
    area_km2            DOUBLE PRECISION,
    tree_fraction       DOUBLE PRECISION,
    shrub_fraction      DOUBLE PRECISION,
    grassland_fraction  DOUBLE PRECISION,
    cropland_fraction   DOUBLE PRECISION,
    built_up_fraction   DOUBLE PRECISION,
    bare_fraction       DOUBLE PRECISION,
    snow_fraction       DOUBLE PRECISION,
    water_fraction      DOUBLE PRECISION,
    wetland_fraction    DOUBLE PRECISION,
    mangrove_fraction   DOUBLE PRECISION,
    moss_fraction       DOUBLE PRECISION,
    valid_pixel_count   INTEGER,
    source              TEXT NOT NULL,
    source_url          TEXT NOT NULL,
    observed_period     TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_destination_urban_land_cover_centre
    ON destination_urban_land_cover (urban_centre_id);

CREATE VIEW destination_urban_greenery AS
SELECT destination_id,
       urban_centre_id,
       area_km2,
       tree_fraction,
       shrub_fraction,
       grassland_fraction,
       cropland_fraction,
       built_up_fraction,
       water_fraction,
       valid_pixel_count,
       CASE WHEN 1.0 - water_fraction >= 0.2
            THEN tree_fraction / (1.0 - water_fraction) END AS urban_tree_share,
       CASE WHEN 1.0 - water_fraction >= 0.2
            THEN (tree_fraction + shrub_fraction + grassland_fraction)
                 / (1.0 - water_fraction) END AS urban_green_share,
       CASE WHEN 1.0 - water_fraction >= 0.2
            THEN 100.0 * tree_fraction / (1.0 - water_fraction) END
            AS urban_tree_score_0_100,
       CASE WHEN 1.0 - water_fraction >= 0.2
            THEN 100.0 * (tree_fraction + shrub_fraction + grassland_fraction)
                 / (1.0 - water_fraction) END
            AS urban_green_score_0_100,
       source, source_url, observed_period
FROM destination_urban_land_cover;

CREATE VIEW destination_surrounding_nature AS
WITH annulus AS (
    SELECT o.destination_id,
           (225.0 * f.tree_fraction - 1.0 * o.tree_fraction) / 224.0
               AS tree_fraction,
           (225.0 * f.shrub_fraction - 1.0 * o.shrub_fraction) / 224.0
               AS shrub_fraction,
           (225.0 * f.grassland_fraction - 1.0 * o.grassland_fraction) / 224.0
               AS grassland_fraction,
           (225.0 * f.cropland_fraction - 1.0 * o.cropland_fraction) / 224.0
               AS cropland_fraction,
           (225.0 * f.built_up_fraction - 1.0 * o.built_up_fraction) / 224.0
               AS built_up_fraction,
           (225.0 * f.water_fraction - 1.0 * o.water_fraction) / 224.0
               AS water_fraction
    FROM destination_land_cover o
    JOIN destination_land_cover f
      ON f.destination_id = o.destination_id AND f.radius_m = 15000
    WHERE o.radius_m = 1000
)
SELECT destination_id,
       tree_fraction,
       shrub_fraction,
       grassland_fraction,
       built_up_fraction,
       water_fraction,
       CASE WHEN 1.0 - water_fraction >= 0.2
            THEN tree_fraction / (1.0 - water_fraction) END
            AS annulus_tree_share,
       CASE WHEN 1.0 - water_fraction >= 0.2
            THEN (tree_fraction + shrub_fraction) / (1.0 - water_fraction) END
            AS annulus_tree_shrub_share,
       CASE WHEN 1.0 - water_fraction >= 0.2
            THEN (tree_fraction + shrub_fraction + grassland_fraction)
                 / (1.0 - water_fraction) END
            AS annulus_green_share,
       CASE WHEN 1.0 - water_fraction >= 0.2
            THEN 100.0 * (tree_fraction + shrub_fraction)
                 / (1.0 - water_fraction) END
            AS surrounding_nature_score_0_100,
       '1-15 km annulus, land-only tree+shrub'::text AS weights,
       'Derived from ESA WorldCover 2021 at 1 km and 15 km'::text AS source,
       '2021'::text AS observed_period
FROM annulus;
"""

PRINCIPAL_QUERY = """
WITH scored AS (
    SELECT d.destination_id, d.name, d.population,
           m.urban_centre_id, m.centre_population
    FROM destinations d
    JOIN destination_greenery_score g USING (destination_id)
    JOIN destination_metro m USING (destination_id)
    WHERE g.greenery_score_0_100 IS NOT NULL
),
principal AS (
    SELECT DISTINCT ON (urban_centre_id) *
    FROM scored
    ORDER BY urban_centre_id, population DESC NULLS LAST, name
)
SELECT p.destination_id, p.name, p.urban_centre_id,
       g.name AS centre_name, g.area_km2,
       ST_AsGeoJSON(g.geom) AS geojson,
       ST_XMin(Box2D(g.geom)) AS min_lon,
       ST_XMax(Box2D(g.geom)) AS max_lon,
       ST_YMin(Box2D(g.geom)) AS min_lat,
       ST_YMax(Box2D(g.geom)) AS max_lat
FROM principal p
JOIN ghsl_urban_centres g USING (urban_centre_id)
WHERE p.centre_population >= 1000000
ORDER BY p.urban_centre_id
"""


def log(message: str) -> None:
    print(message, flush=True)


def tile_name(lat: float, lon: float) -> str:
    lat0 = int(math.floor((lat + 90.0) / 3.0) * 3 - 90)
    lon0 = int(math.floor((lon + 180.0) / 3.0) * 3 - 180)
    ns = "N" if lat0 >= 0 else "S"
    ew = "E" if lon0 >= 0 else "W"
    return f"{ns}{abs(lat0):02d}{ew}{abs(lon0):03d}"


def tile_origins(min_lat: float, max_lat: float, min_lon: float, max_lon: float):
    lat0 = int(math.floor((min_lat + 90.0) / 3.0) * 3 - 90)
    lat1 = int(math.floor((max_lat + 90.0) / 3.0) * 3 - 90)
    lon0 = int(math.floor((min_lon + 180.0) / 3.0) * 3 - 180)
    lon1 = int(math.floor((max_lon + 180.0) / 3.0) * 3 - 180)
    for lat in range(lat0, lat1 + 1, 3):
        for lon in range(lon0, lon1 + 1, 3):
            yield lat, lon


class TileCache:
    def __init__(self) -> None:
        self.datasets: dict[str, rasterio.DatasetReader | None] = {}

    def get(self, lat: float, lon: float) -> rasterio.DatasetReader | None:
        name = tile_name(lat, lon)
        if name not in self.datasets:
            url = f"{BUCKET}/{TILE.format(tile=name)}"
            try:
                self.datasets[name] = rasterio.open(url)
            except Exception:
                self.datasets[name] = None
        return self.datasets[name]

    def close(self) -> None:
        for dataset in self.datasets.values():
            if dataset is not None:
                dataset.close()


def polygon_counts(
    cache: TileCache,
    geom: dict[str, Any],
    bounds: tuple[float, float, float, float],
) -> tuple[Counter, int]:
    min_lon, min_lat, max_lon, max_lat = bounds
    counts: Counter = Counter()
    valid_total = 0
    for lat0, lon0 in tile_origins(min_lat, max_lat, min_lon, max_lon):
        dataset = cache.get(lat0 + 1.5, lon0 + 1.5)
        if dataset is None:
            continue
        left = max(min_lon, dataset.bounds.left)
        right = min(max_lon, dataset.bounds.right)
        bottom = max(min_lat, dataset.bounds.bottom)
        top = min(max_lat, dataset.bounds.top)
        if right <= left or top <= bottom:
            continue
        window = from_bounds(left, bottom, right, top, dataset.transform)
        window = window.round_offsets().round_lengths()
        if window.width <= 0 or window.height <= 0:
            continue
        height_m = (top - bottom) * 111320.0
        width_m = (right - left) * 111320.0 * max(
            0.05, abs(math.cos(math.radians((top + bottom) / 2.0)))
        )
        out_h = max(1, min(MAX_SIDE, int(round(height_m / TARGET_RES_M))))
        out_w = max(1, min(MAX_SIDE, int(round(width_m / TARGET_RES_M))))
        data = dataset.read(
            1, window=window, out_shape=(out_h, out_w),
            resampling=Resampling.nearest,
        )
        transform = rasterio.transform.from_bounds(
            left, bottom, right, top, out_w, out_h
        )
        mask = geometry_mask(
            [geom], out_shape=(out_h, out_w), transform=transform, invert=True
        )
        valid = mask & (data != 0)
        counts.update(int(value) for value in data[valid])
        valid_total += int(valid.sum())
    return counts, valid_total


def load(
    database_url: str, limit: int | None = None,
    cities: list[str] | None = None,
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(PRINCIPAL_QUERY)
            rows = [dict(row) for row in cur.fetchall()]
        conn.commit()
        if cities:
            rows = [row for row in rows if row["name"] in cities]
        if limit:
            rows = rows[:limit]
        log(f"computing urban-centre land cover for {len(rows)} cities")
        cache = TileCache()
        features = []
        started = time.time()
        try:
            for index, row in enumerate(rows, 1):
                geom = json.loads(row["geojson"])
                bounds = (
                    row["min_lon"], row["min_lat"],
                    row["max_lon"], row["max_lat"],
                )
                counts, valid = polygon_counts(cache, geom, bounds)
                fractions = {
                    column: (counts.get(code, 0) / valid if valid else None)
                    for code, column in CLASS_COLUMNS.items()
                }
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO destination_urban_land_cover (
                            destination_id, urban_centre_id, area_km2,
                            tree_fraction, shrub_fraction, grassland_fraction,
                            cropland_fraction, built_up_fraction, bare_fraction,
                            snow_fraction, water_fraction, wetland_fraction,
                            mangrove_fraction, moss_fraction,
                            valid_pixel_count, source, source_url, observed_period
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            row["destination_id"], row["urban_centre_id"],
                            row["area_km2"],
                            fractions["tree_fraction"],
                            fractions["shrub_fraction"],
                            fractions["grassland_fraction"],
                            fractions["cropland_fraction"],
                            fractions["built_up_fraction"],
                            fractions["bare_fraction"],
                            fractions["snow_fraction"],
                            fractions["water_fraction"],
                            fractions["wetland_fraction"],
                            fractions["mangrove_fraction"],
                            fractions["moss_fraction"],
                            valid, SOURCE, SOURCE_URL, PERIOD,
                        ),
                    )
                conn.commit()
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "destination_id": row["destination_id"],
                            "city": row["name"],
                            "urban_centre_id": row["urban_centre_id"],
                            "centre_name": row["centre_name"],
                            "area_km2": row["area_km2"],
                        },
                        "geometry": geom,
                    }
                )
                if index % 25 == 0 or index == len(rows):
                    elapsed = time.time() - started
                    log(
                        f"{index}/{len(rows)} cities, {elapsed:.0f}s, "
                        f"last valid pixels {valid}"
                    )
        finally:
            cache.close()
    OUT.mkdir(exist_ok=True)
    (OUT / "urban_centres_482.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    return {"cities": len(rows), "geojson": str(OUT / "urban_centres_482.geojson")}


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) n,
                       round(avg((tree_fraction
                           / NULLIF(1 - water_fraction, 0) * 100))::numeric, 1)
                           AS avg_tree_pct
                FROM destination_urban_land_cover
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT d.name,
                       round((tree_fraction * 100)::numeric, 1) AS tree_pct,
                       round(((tree_fraction + shrub_fraction
                           + grassland_fraction)
                           / NULLIF(1 - water_fraction, 0) * 100)::numeric, 1)
                           AS green_pct,
                       round((built_up_fraction * 100)::numeric, 1) AS built_pct,
                       round((water_fraction * 100)::numeric, 1) AS water_pct
                FROM destination_urban_land_cover u
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Montréal', 'Oslo', 'Philadelphia',
                                 'Vancouver', 'Singapore')
                ORDER BY d.name
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--city", action="append", default=[])
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, args.limit, args.city or None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
