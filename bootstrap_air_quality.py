#!/usr/bin/env python3
"""
Annual city PM2.5 from the SatPM V6.GL.03 global product.

The raster is a 0.1 degree (~11 km) satellite/model/monitor-fused annual
estimate, CC BY 4.0. It is a city-point sample, not a monitor reading and not
a neighbourhood map. Values are stored separately from the country-level
World Bank PM2.5 indicator.

Source: https://www.satpm.org/ and
https://satpmdata.s3.amazonaws.com/V6GL03/CNNPM25c0p10/Annual/GL/

Usage:
    python bootstrap_air_quality.py --year 2024
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
import rasterio
import requests
from psycopg.rows import dict_row

CACHE = Path("./data_cache/air_quality")
BASE = (
    "https://satpmdata.s3.amazonaws.com/V6GL03/CNNPM25c0p10/Annual/GL/"
    "V6GL03.CNNPM25.0p10.GL.{year}01-{year}12.nc"
)
SOURCE = "SatPM V6.GL.03 (CNN PM2.5, 0.1 degree annual)"
SOURCE_URL = "https://www.satpm.org/"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS air_quality_annual (
    destination_id  TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    pm25_ugm3       DOUBLE PRECISION NOT NULL,
    year            INT NOT NULL,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def download(year: int) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"satpm_{year}.nc"
    if path.exists():
        return path
    url = BASE.format(year=year)
    log(f"downloading {url}")
    with requests.get(url, timeout=180, stream=True) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
    return path


def sample(dataset, lat: float, lon: float) -> float | None:
    if not (-60.0 <= lat <= 70.0 and -180.0 <= lon <= 180.0):
        return None
    row = int((70.0 - lat) / 0.1)
    col = int((lon + 180.0) / 0.1)
    if row < 0 or col < 0 or row >= dataset.height or col >= dataset.width:
        return None
    window = ((row, row + 1), (col, col + 1))
    value = float(dataset.read(1, window=window)[0, 0])
    if value <= -900 or value > 1000:
        return None
    return round(value, 3)


def load(database_url: str, year: int) -> dict:
    path = download(year)
    written = 0
    skipped = 0
    with rasterio.open(path) as dataset:
        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
                cur.execute("SELECT destination_id, name, lat, lon FROM destinations")
                cities = cur.fetchall()
                for city in cities:
                    value = sample(dataset, float(city["lat"]), float(city["lon"]))
                    if value is None:
                        skipped += 1
                        continue
                    cur.execute(
                        """
                        INSERT INTO air_quality_annual (
                            destination_id, pm25_ugm3, year, source, source_url,
                            observed_period
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (destination_id) DO UPDATE SET
                            pm25_ugm3 = EXCLUDED.pm25_ugm3,
                            year = EXCLUDED.year,
                            source = EXCLUDED.source,
                            source_url = EXCLUDED.source_url,
                            observed_period = EXCLUDED.observed_period,
                            updated_at = NOW()
                        """,
                        (
                            city["destination_id"], value, year, SOURCE, SOURCE_URL,
                            str(year),
                        ),
                    )
                    written += 1
            conn.commit()
    return {"written": written, "skipped": skipped, "year": year}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    print(load(args.db_url, args.year))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
