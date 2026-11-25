#!/usr/bin/env python3
"""
Monthly city PM2.5 from the SatPM V6.GL.03 global product.

The annual table answers "how polluted is this city". This one answers "when is
the smoke, inversion or monsoon clean-up": 12 monthly 0.1 degree estimates for
the same city points.

It is the same satellite/model/monitor-fused product as the annual layer, still
a city-point sample and not a monitor or a neighbourhood map.

Source: https://www.satpm.org/ and
https://satpmdata.s3.amazonaws.com/V6GL03/CNNPM25c0p10/Monthly/GL/

Usage
-----
    python bootstrap_air_quality_monthly.py --year 2024
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
import rasterio
import requests
from psycopg.rows import dict_row

from bootstrap_air_quality import sample

CACHE = Path("./data_cache/air_quality")
BASE = (
    "https://satpmdata.s3.amazonaws.com/V6GL03/CNNPM25c0p10/Monthly/GL/"
    "V6GL03.CNNPM25.0p10.GL.{year}{month:02d}-{year}{month:02d}.nc"
)
SOURCE = "SatPM V6.GL.03 monthly (CNN PM2.5, 0.1 degree)"
SOURCE_URL = "https://www.satpm.org/"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS air_quality_monthly (
    destination_id  TEXT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
    year            INT NOT NULL,
    month           INT NOT NULL CHECK (month BETWEEN 1 AND 12),
    pm25_ugm3       DOUBLE PRECISION NOT NULL,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (destination_id, year, month)
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def download(year: int, month: int) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"satpm_{year}_{month:02d}.nc"
    if path.exists():
        return path
    url = BASE.format(year=year, month=month)
    log(f"downloading {url}")
    with requests.get(url, timeout=180, stream=True) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
    return path


def load(database_url: str, year: int) -> dict:
    written = 0
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("SELECT destination_id, lat, lon FROM destinations")
            cities = cur.fetchall()
        for month in range(1, 13):
            path = download(year, month)
            batch = []
            with rasterio.open(path) as dataset:
                for city in cities:
                    value = sample(dataset, float(city["lat"]), float(city["lon"]))
                    if value is None:
                        continue
                    batch.append((
                        city["destination_id"], year, month, value, SOURCE,
                        SOURCE_URL, f"{year}-{month:02d}",
                    ))
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO air_quality_monthly (
                        destination_id, year, month, pm25_ugm3, source,
                        source_url, observed_period
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (destination_id, year, month) DO UPDATE SET
                        pm25_ugm3 = EXCLUDED.pm25_ugm3,
                        source = EXCLUDED.source,
                        source_url = EXCLUDED.source_url,
                        observed_period = EXCLUDED.observed_period,
                        updated_at = NOW()
                    """,
                    batch,
                )
            conn.commit()
            written += len(batch)
            log(f"  {year}-{month:02d}: {len(batch):,} cities")
    return {"rows": written, "year": year}


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
