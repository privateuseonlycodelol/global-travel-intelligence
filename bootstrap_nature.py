#!/usr/bin/env python3
"""
Global nature typology: landscape region and terrain around the city.

This is the first, uncontroversial nature layer:

* **Landscape** — RESOLVE Ecoregions 2017 (846 ecoregions, 14 biomes, 8
  realms). One point-in-polygon sample at the city centroid. The ecoregion is
  the surrounding region, not the view from the window; the built-up fraction
  from land cover is a separate layer.
* **Terrain** — Amatulli et al. 2018 GMTED 10 km median elevation, elevation
  standard deviation, Terrain Ruggedness Index and Topographic Position Index.
  This separates a high flat city (Denver), a high city (La Paz) and a valley
  under peaks (Innsbruck) in a way a single elevation cannot.

The 10 km min/max elevation grids were unavailable from the archive at build
time, so the table stores the median, standard deviation and TRI/TPI rather
than inventing a max-minus-min relief. Town elevation stays in
``destination_geography``.

Sources
-------
RESOLVE Ecoregions 2017, CC BY 4.0:
https://storage.googleapis.com/teow2016/Ecoregions2017.zip

Amatulli et al. 2018, CC BY 3.0:
https://doi.pangaea.de/10.1594/PANGAEA.867113

Usage
-----
    python bootstrap_nature.py
    python bootstrap_nature.py --report
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import numpy as np
import rasterio
import requests
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

CACHE = Path("./data_cache/nature")
ECOREGION_ZIP = CACHE / "Ecoregions2017.zip"
ECOREGION_SHP = CACHE / "Ecoregions2017.shp"
ECOREGION_URL = "https://storage.googleapis.com/teow2016/Ecoregions2017.zip"
RASTER_URLS = {
    "elevation_10KMmd_GMTEDmd.tif": "https://hs.pangaea.de/model/GMTED/elevation/elevation_10KMmd_GMTEDmd.tif",
    "elevation_10KMsd_GMTEDmd.tif": "https://hs.pangaea.de/model/GMTED/elevation/elevation_10KMsd_GMTEDmd.tif",
    "tri_10KMmd_GMTEDmd.tif": "https://hs.pangaea.de/model/GMTED/tri/tri_10KMmd_GMTEDmd.tif",
    "tpi_10KMmd_GMTEDmd.tif": "https://hs.pangaea.de/model/GMTED/tpi/tpi_10KMmd_GMTEDmd.tif",
}
LANDCOVER_DIR = CACHE / "landcover"
LANDCOVER_RECORD = "https://zenodo.org/api/records/3939050"
LANDCOVER_PATTERNS = {
    "tree": "Tree-CoverFraction",
    "shrub": "Shrub-CoverFraction",
    "grass": "Grass-CoverFraction",
    "crop": "Crops-CoverFraction",
    "bare": "Bare-CoverFraction",
    "built": "BuiltUp-CoverFraction",
    "permanent_water": "PermanentWater-CoverFraction",
    "seasonal_water": "SeasonalWater-CoverFraction",
    "snow": "Snow-CoverFraction",
    "moss": "MossLichen-CoverFraction",
}
LANDCOVER_SOURCE = "Copernicus Global Land Service LC100 v3.0.1 (2019)"
ECOREGION_SOURCE = "RESOLVE Ecoregions 2017 (CC BY 4.0)"
TERRAIN_SOURCE = "Amatulli et al. 2018 GMTED 10 km (CC BY 3.0)"
SOURCE_URL = "https://ecoregions2017.appspot.com/"

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS destination_landscape (
    destination_id  TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    eco_id          INTEGER,
    ecoregion_name  TEXT,
    biome_num       INTEGER,
    biome_name      TEXT,
    realm           TEXT,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS destination_terrain (
    destination_id        TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    radius_m              INTEGER NOT NULL DEFAULT 10000,
    elevation_median_m    DOUBLE PRECISION,
    elevation_sd_m        DOUBLE PRECISION,
    tri_median            DOUBLE PRECISION,
    tpi_median            DOUBLE PRECISION,
    relief_class          TEXT,
    position_class        TEXT,
    source                TEXT NOT NULL,
    source_url            TEXT NOT NULL,
    observed_period       TEXT NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def download(url: str, path: Path) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return path
    log(f"downloading {url}")
    response = requests.get(url, timeout=300)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path


def pg_string(database_url: str) -> str:
    info = conninfo_to_dict(database_url)
    parts = []
    for key in ("dbname", "host", "port", "user", "password"):
        value = info.get(key)
        if value:
            parts.append(f"{key}='{str(value).replace(chr(39), chr(92) + chr(39))}'")
    return "PG:" + " ".join(parts)


def ensure_ecoregions(database_url: str) -> None:
    download(ECOREGION_URL, ECOREGION_ZIP)
    if not ECOREGION_SHP.exists():
        with zipfile.ZipFile(ECOREGION_ZIP) as archive:
            archive.extractall(CACHE)
    subprocess.run(
        [
            "ogr2ogr", "-f", "PostgreSQL", pg_string(database_url),
            str(ECOREGION_SHP), "-nln", "ecoregions2017",
            "-t_srs", "EPSG:4326", "-nlt", "PROMOTE_TO_MULTI",
            "-lco", "GEOMETRY_NAME=geom", "-lco", "FID=fid", "-overwrite",
        ],
        check=True,
        capture_output=True,
    )


def ensure_rasters() -> dict[str, Path]:
    paths = {}
    for name, url in RASTER_URLS.items():
        paths[name] = download(url, CACHE / name)
    return paths


def ensure_landcover() -> dict[str, Path]:
    LANDCOVER_DIR.mkdir(parents=True, exist_ok=True)
    response = requests.get(LANDCOVER_RECORD, timeout=120)
    response.raise_for_status()
    files = response.json().get("files", [])
    paths: dict[str, Path] = {}
    for name, pattern in LANDCOVER_PATTERNS.items():
        match = next(
            (item for item in files if pattern in item.get("key", "") and item["key"].endswith(".tif")),
            None,
        )
        if match is None:
            raise RuntimeError(f"land-cover layer not found in Zenodo record: {pattern}")
        path = LANDCOVER_DIR / match["key"]
        if not path.exists() or path.stat().st_size != match["size"]:
            if path.with_suffix(path.suffix + ".part").exists():
                raise RuntimeError(f"{path.name} is still downloading; rerun when complete")
            log(f"downloading {match['key']}")
            with requests.get(match["links"]["self"], stream=True, timeout=300) as download:
                download.raise_for_status()
                tmp = path.with_suffix(path.suffix + ".part")
                with tmp.open("wb") as handle:
                    for chunk in download.iter_content(1 << 20):
                        handle.write(chunk)
                tmp.replace(path)
        paths[name] = path
    return paths


def _circle_mask(
    dataset: rasterio.DatasetReader, window, out_shape: tuple[int, int],
    lat: float, lon: float, radius_m: float,
) -> np.ndarray:
    transform = rasterio.windows.transform(window, dataset.transform)
    cols, rows = np.meshgrid(
        np.arange(out_shape[1]) + 0.5, np.arange(out_shape[0]) + 0.5
    )
    xs, ys = transform * (cols, rows)
    # haversine in metres
    p1 = np.radians(lat)
    p2 = np.radians(ys)
    dphi = p2 - p1
    dlambda = np.radians(xs - lon)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlambda / 2) ** 2
    distance = 2 * 6371008.8 * np.arcsin(np.minimum(1.0, np.sqrt(a)))
    return distance <= radius_m


def _sample_layer(
    dataset: rasterio.DatasetReader, lat: float, lon: float,
    radius_m: float, out_shape: tuple[int, int],
) -> np.ndarray | None:
    if lon <= -179.5 or lon >= 179.5:
        return None
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(0.05, abs(np.cos(np.radians(lat)))))
    left, right = lon - dlon, lon + dlon
    bottom, top = lat - dlat, lat + dlat
    left = max(left, dataset.bounds.left)
    right = min(right, dataset.bounds.right)
    bottom = max(bottom, dataset.bounds.bottom)
    top = min(top, dataset.bounds.top)
    if right <= left or top <= bottom:
        return None
    window = from_bounds(left, bottom, right, top, dataset.transform)
    window = window.round_offsets().round_lengths()
    if window.width <= 0 or window.height <= 0:
        return None
    data = dataset.read(
        1, window=window, out_shape=out_shape,
        resampling=Resampling.average,
    ).astype("float64")
    mask = _circle_mask(dataset, window, out_shape, lat, lon, radius_m)
    nodata = dataset.nodata
    valid = mask.copy()
    if nodata is not None:
        valid &= data != float(nodata)
    valid &= data >= 0
    valid &= data <= 100
    return data[valid]


def load_land_cover(conn: psycopg.Connection, paths: dict[str, Path]) -> int:
    datasets = {name: rasterio.open(path) for name, path in paths.items()}
    with conn.cursor() as cur:
        cur.execute("SELECT destination_id, lat, lon FROM destinations")
        cities = cur.fetchall()
        cur.execute("DELETE FROM destination_land_cover")
        rows = []
        try:
            for index, city in enumerate(cities, 1):
                lat, lon = float(city["lat"]), float(city["lon"])
                for radius in (1000, 15000):
                    shape = (20, 20) if radius == 1000 else (100, 100)
                    values = {}
                    valid_count = 0
                    for name, dataset in datasets.items():
                        sampled = _sample_layer(dataset, lat, lon, radius, shape)
                        values[name] = (
                            float(np.mean(sampled)) / 100.0
                            if sampled is not None and sampled.size else None
                        )
                        if name == "tree" and sampled is not None:
                            valid_count = int(sampled.size)
                    permanent = values.get("permanent_water")
                    seasonal = values.get("seasonal_water")
                    water = (
                        (permanent or 0.0) + (seasonal or 0.0)
                        if permanent is not None or seasonal is not None else None
                    )
                    rows.append((
                        city["destination_id"], radius,
                        values.get("tree"), values.get("shrub"),
                        values.get("grass"), values.get("crop"),
                        values.get("built"), values.get("bare"),
                        values.get("snow"), permanent, seasonal, water,
                        None, None, values.get("moss"), valid_count,
                        LANDCOVER_SOURCE,
                        "https://zenodo.org/records/3939050", "2019",
                    ))
                if index % 500 == 0:
                    log(f"  land cover {index:,}/{len(cities):,} cities")
        finally:
            for dataset in datasets.values():
                dataset.close()
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
        return len(rows)


def load_landscape(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM destination_landscape")
        cur.execute(
            """
            INSERT INTO destination_landscape (
                destination_id, eco_id, ecoregion_name, biome_num, biome_name,
                realm, source, source_url, observed_period
            )
            SELECT DISTINCT ON (d.destination_id)
                   d.destination_id,
                   e.eco_id::INTEGER,
                   e.eco_name,
                   e.biome_num::INTEGER,
                   e.biome_name,
                   e.realm,
                   %s, %s, '2017'
            FROM destinations d
            JOIN ecoregions2017 e ON ST_Within(d.centroid::geometry, e.geom)
            ORDER BY d.destination_id, ST_Area(e.geom)
            """,
            (ECOREGION_SOURCE, SOURCE_URL),
        )
        return cur.rowcount


def _read_raster(dataset: rasterio.DatasetReader, lat: float, lon: float) -> float | None:
    if not (dataset.bounds.left <= lon <= dataset.bounds.right
            and dataset.bounds.bottom <= lat <= dataset.bounds.top):
        return None
    row, col = dataset.index(lon, lat)
    if row < 0 or col < 0 or row >= dataset.height or col >= dataset.width:
        return None
    value = float(dataset.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
    if dataset.nodata is not None and value == float(dataset.nodata):
        return None
    if value <= -1000:
        return None
    return value


def relief_class(tri: float | None) -> str | None:
    if tri is None:
        return None
    if tri < 10:
        return "low relief"
    if tri < 40:
        return "rolling or hilly"
    return "high relief"


def position_class(tpi: float | None) -> str | None:
    if tpi is None:
        return None
    if tpi <= -0.5:
        return "valley or low position"
    if tpi >= 0.5:
        return "ridge or high position"
    return "slope or neutral"


def load_terrain(conn: psycopg.Connection, rasters: dict[str, Path]) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT destination_id, lat, lon FROM destinations")
        cities = cur.fetchall()
        cur.execute("DELETE FROM destination_terrain")
        rows = []
        datasets = {
            name: rasterio.open(path) for name, path in rasters.items()
        }
        try:
            for city in cities:
                lat, lon = float(city["lat"]), float(city["lon"])
                elevation = _read_raster(datasets["elevation_10KMmd_GMTEDmd.tif"], lat, lon)
                sd = _read_raster(datasets["elevation_10KMsd_GMTEDmd.tif"], lat, lon)
                tri = _read_raster(datasets["tri_10KMmd_GMTEDmd.tif"], lat, lon)
                tpi = _read_raster(datasets["tpi_10KMmd_GMTEDmd.tif"], lat, lon)
                rows.append((
                    city["destination_id"], 10000, elevation, sd, tri, tpi,
                    relief_class(tri), position_class(tpi),
                    TERRAIN_SOURCE, "https://doi.pangaea.de/10.1594/PANGAEA.867113",
                    "2018",
                ))
        finally:
            for dataset in datasets.values():
                dataset.close()
        cur.executemany(
            """
            INSERT INTO destination_terrain (
                destination_id, radius_m, elevation_median_m, elevation_sd_m,
                tri_median, tpi_median, relief_class, position_class,
                source, source_url, observed_period
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
        return len(rows)


def load(database_url: str, skip_landcover: bool = False) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        ensure_ecoregions(database_url)
        landscape = load_landscape(conn)
        conn.commit()
        rasters = ensure_rasters()
        terrain = load_terrain(conn, rasters)
        conn.commit()
        land_cover = 0
        if not skip_landcover:
            paths = ensure_landcover()
            land_cover = load_land_cover(conn, paths)
            conn.commit()
    return {"landscape": landscape, "terrain": terrain, "land_cover": land_cover}


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM destination_landscape")
            print({"landscape": cur.fetchone()["n"]})
            cur.execute("SELECT count(*) n FROM destination_terrain")
            print({"terrain": cur.fetchone()["n"]})
            cur.execute("SELECT count(*) n FROM destination_land_cover")
            print({"land_cover": cur.fetchone()["n"]})
            cur.execute(
                """
                SELECT d.name, l.biome_name, l.ecoregion_name,
                       t.elevation_median_m, t.elevation_sd_m, t.tri_median,
                       t.tpi_median, t.relief_class, t.position_class
                FROM destinations d
                LEFT JOIN destination_landscape l USING (destination_id)
                LEFT JOIN destination_terrain t USING (destination_id)
                WHERE d.name IN ('Trento', 'Paris', 'Innsbruck', 'Denver', 'La Paz')
                ORDER BY d.name
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--skip-landcover", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, skip_landcover=args.skip_landcover))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
