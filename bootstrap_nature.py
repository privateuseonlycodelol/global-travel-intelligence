#!/usr/bin/env python3
"""
Global nature typology: landscape region and terrain around the city.

This is the first, uncontroversial nature layer:

* **Landscape** — RESOLVE Ecoregions 2017 (846 ecoregions, 14 biomes, 8
  realms). One point-in-polygon sample at the city centroid. The ecoregion is
  the surrounding region, not the view from the window; the built-up fraction
  from land cover is a separate layer.
* **Climate class** — Beck et al. 2023 Köppen-Geiger 1991-2020 at 1 km, one
  point sample. A class label, not a score.
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
import rasterio
import requests
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
KOPPEN_URL = (
    "https://data.naturalcapitalalliance.stanford.edu/download/global/"
    "koppen_geiger_climatezones/koppen_geiger_climatezones_1991_2020_1km.tif"
)
KOPPEN_CACHE = CACHE / "koppen_1991_2020_1km.tif"
KOPPEN_SOURCE = "Beck et al. 2023 Köppen-Geiger 1991-2020 (CC BY 4.0)"
KOPPEN_LEGEND = {
    1: ("Af", "Tropical, rainforest"),
    2: ("Am", "Tropical, monsoon"),
    3: ("Aw", "Tropical, savannah"),
    4: ("BWh", "Arid, desert, hot"),
    5: ("BWk", "Arid, desert, cold"),
    6: ("BSh", "Arid, steppe, hot"),
    7: ("BSk", "Arid, steppe, cold"),
    8: ("Csa", "Temperate, dry summer, hot summer"),
    9: ("Csb", "Temperate, dry summer, warm summer"),
    10: ("Csc", "Temperate, dry summer, cold summer"),
    11: ("Cwa", "Temperate, dry winter, hot summer"),
    12: ("Cwb", "Temperate, dry winter, warm summer"),
    13: ("Cwc", "Temperate, dry winter, cold summer"),
    14: ("Cfa", "Temperate, no dry season, hot summer"),
    15: ("Cfb", "Temperate, no dry season, warm summer"),
    16: ("Cfc", "Temperate, no dry season, cold summer"),
    17: ("Dsa", "Cold, dry summer, hot summer"),
    18: ("Dsb", "Cold, dry summer, warm summer"),
    19: ("Dsc", "Cold, dry summer, cold summer"),
    20: ("Dsd", "Cold, dry summer, very cold winter"),
    21: ("Dwa", "Cold, dry winter, hot summer"),
    22: ("Dwb", "Cold, dry winter, warm summer"),
    23: ("Dwc", "Cold, dry winter, cold summer"),
    24: ("Dwd", "Cold, dry winter, very cold winter"),
    25: ("Dfa", "Cold, no dry season, hot summer"),
    26: ("Dfb", "Cold, no dry season, warm summer"),
    27: ("Dfc", "Cold, no dry season, cold summer"),
    28: ("Dfd", "Cold, no dry season, very cold winter"),
    29: ("ET", "Polar, tundra"),
    30: ("EF", "Polar, frost"),
}
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

CREATE TABLE IF NOT EXISTS destination_climate_class (
    destination_id TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    koppen_code    TEXT,
    koppen_label   TEXT,
    source         TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
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


def ensure_koppen() -> Path:
    return download(KOPPEN_URL, KOPPEN_CACHE)


def load_koppen(conn: psycopg.Connection, path: Path) -> int:
    with rasterio.open(path) as dataset:
        with conn.cursor() as cur:
            cur.execute("SELECT destination_id, lat, lon FROM destinations")
            cities = cur.fetchall()
            cur.execute("DELETE FROM destination_climate_class")
            rows = []
            for city in cities:
                value = _read_raster(dataset, float(city["lat"]), float(city["lon"]))
                code = label = None
                if value is not None:
                    code, label = KOPPEN_LEGEND.get(int(round(value)), (None, None))
                rows.append((
                    city["destination_id"], code, label, KOPPEN_SOURCE,
                    "https://www.gloh2o.org/koppen/", "1991-2020",
                ))
            cur.executemany(
                """
                INSERT INTO destination_climate_class (
                    destination_id, koppen_code, koppen_label, source,
                    source_url, observed_period
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
            return len(rows)


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


def load(database_url: str) -> dict[str, Any]:
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
        koppen_path = ensure_koppen()
        climate_class = load_koppen(conn, koppen_path)
        conn.commit()
    return {"landscape": landscape, "terrain": terrain, "climate_class": climate_class}


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM destination_landscape")
            print({"landscape": cur.fetchone()["n"]})
            cur.execute("SELECT count(*) n FROM destination_terrain")
            print({"terrain": cur.fetchone()["n"]})
            cur.execute("SELECT count(*) n FROM destination_climate_class")
            print({"climate_class": cur.fetchone()["n"]})
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
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
