#!/usr/bin/env python3
"""
Recent monthly climate normals from ERA5 via Open-Meteo.

WorldClim 2.1 in ``climate_monthly`` is a 1970-2000 climatology and has no
rainy-day or hot-day counts. This loader adds a separate, recent table:

* mean daily high / low by month;
* mean monthly precipitation;
* mean rainy days (>= 1 mm) per month;
* mean days >= 30 C per month;
* mean warm nights (>= 20 C) per month.

It does not overwrite WorldClim. The two periods stay separate and labelled.

Usage
-----
    python bootstrap_climate_recent.py --limit 20
    python bootstrap_climate_recent.py --countries IT,ES --start-year 2015 --end-year 2024
    python bootstrap_climate_recent.py --missing-only --sleep 0.2

Source: NASA POWER (MERRA-2), daily point API, free with attribution;
confirm terms before commercial redistribution.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import date

import psycopg
import requests
from psycopg.rows import dict_row

API = "https://power.larc.nasa.gov/api/temporal/daily/point"
SOURCE = "NASA POWER (MERRA-2)"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS climate_recent (
    destination_id  TEXT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
    month           INT NOT NULL CHECK (month BETWEEN 1 AND 12),
    avg_high_c      DOUBLE PRECISION,
    avg_low_c       DOUBLE PRECISION,
    precip_mm       DOUBLE PRECISION,
    rainy_days      DOUBLE PRECISION,
    hot_days_30c    DOUBLE PRECISION,
    warm_nights     DOUBLE PRECISION,
    years           INT NOT NULL,
    source          TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (destination_id, month)
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def monthly_normals(payload: dict, years: int) -> list[dict]:
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    precip = daily.get("precipitation_sum") or []
    buckets: dict[int, dict[str, list[float]]] = defaultdict(
        lambda: {"high": [], "low": [], "precip": [], "rain": [], "hot": [], "warm": []}
    )
    for index, stamp in enumerate(times):
        month = int(stamp[5:7]) if "-" in stamp else int(stamp[4:6])
        high = highs[index] if index < len(highs) else None
        low = lows[index] if index < len(lows) else None
        rain = precip[index] if index < len(precip) else None
        if high is not None and float(high) > -900:
            buckets[month]["high"].append(float(high))
            buckets[month]["hot"].append(1.0 if high >= 30 else 0.0)
        if low is not None and float(low) > -900:
            buckets[month]["low"].append(float(low))
            buckets[month]["warm"].append(1.0 if low >= 20 else 0.0)
        if rain is not None and float(rain) > -900:
            buckets[month]["precip"].append(float(rain))
            buckets[month]["rain"].append(1.0 if rain >= 1.0 else 0.0)
    rows = []
    for month in range(1, 13):
        b = buckets.get(month)
        if not b or not b["high"]:
            continue
        rows.append({
            "month": month,
            "avg_high_c": round(sum(b["high"]) / len(b["high"]), 2),
            "avg_low_c": round(sum(b["low"]) / len(b["low"]), 2) if b["low"] else None,
            "precip_mm": round(sum(b["precip"]) / years, 1) if b["precip"] else None,
            "rainy_days": round(sum(b["rain"]) / years, 1) if b["rain"] else None,
            "hot_days_30c": round(sum(b["hot"]) / years, 1) if b["hot"] else None,
            "warm_nights": round(sum(b["warm"]) / years, 1) if b["warm"] else None,
        })
    return rows


def fetch(session: requests.Session, lat: float, lon: float, start: str, end: str) -> dict:
    params = {
        "parameters": "T2M_MAX,T2M_MIN,PRECTOTCORR",
        "community": "AG",
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "start": start.replace("-", ""),
        "end": end.replace("-", ""),
        "format": "JSON",
    }
    for attempt in range(4):
        response = session.get(API, params=params, timeout=90)
        if response.status_code in (429, 503):
            wait = 20 * (attempt + 1)
            log(f"rate limited; sleeping {wait}s")
            time.sleep(wait)
            continue
        response.raise_for_status()
        parameters = response.json()["properties"]["parameter"]
        dates = sorted(parameters["T2M_MAX"])
        return {
            "daily": {
                "time": dates,
                "temperature_2m_max": [parameters["T2M_MAX"][d] for d in dates],
                "temperature_2m_min": [parameters["T2M_MIN"][d] for d in dates],
                "precipitation_sum": [parameters["PRECTOTCORR"][d] for d in dates],
            }
        }
    raise RuntimeError("NASA POWER rate limit persisted")


def load(
    database_url: str,
    start_year: int,
    end_year: int,
    limit: int | None,
    countries: list[str] | None,
    missing_only: bool,
    sleep_s: float,
) -> dict:
    start = f"{start_year}-01-01"
    end = f"{end_year}-12-31"
    years = end_year - start_year + 1
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            query = """
                SELECT d.destination_id, d.name, d.lat, d.lon
                FROM destinations d
                WHERE (%s::text[] IS NULL OR d.country_iso2 = ANY(%s))
                  AND (NOT %s OR NOT EXISTS (
                      SELECT 1 FROM climate_recent c WHERE c.destination_id = d.destination_id
                  ))
                ORDER BY d.population DESC
            """
            params: list = [countries, countries, missing_only]
            if limit is not None:
                query += " LIMIT %s"
                params.append(limit)
            cur.execute(query, params)
            cities = cur.fetchall()
        log(f"{len(cities):,} cities to fetch")
        session = requests.Session()
        session.headers["User-Agent"] = "travel-db-climate-loader/1.0"
        written = 0
        for index, city in enumerate(cities, 1):
            payload = fetch(session, float(city["lat"]), float(city["lon"]), start, end)
            rows = monthly_normals(payload, years)
            if not rows:
                log(f"  no data for {city['name']}")
                continue
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO climate_recent (
                            destination_id, month, avg_high_c, avg_low_c, precip_mm,
                            rainy_days, hot_days_30c, warm_nights, years, source,
                            observed_period
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (destination_id, month) DO UPDATE SET
                            avg_high_c = EXCLUDED.avg_high_c,
                            avg_low_c = EXCLUDED.avg_low_c,
                            precip_mm = EXCLUDED.precip_mm,
                            rainy_days = EXCLUDED.rainy_days,
                            hot_days_30c = EXCLUDED.hot_days_30c,
                            warm_nights = EXCLUDED.warm_nights,
                            years = EXCLUDED.years,
                            source = EXCLUDED.source,
                            observed_period = EXCLUDED.observed_period,
                            updated_at = NOW()
                        """,
                        (
                            city["destination_id"], row["month"], row["avg_high_c"],
                            row["avg_low_c"], row["precip_mm"], row["rainy_days"],
                            row["hot_days_30c"], row["warm_nights"], years, SOURCE,
                            f"{start_year}-{end_year}",
                        ),
                    )
            conn.commit()
            written += 1
            if index % 50 == 0:
                log(f"  {index:,}/{len(cities):,} cities")
            time.sleep(sleep_s)
    return {"cities": len(cities), "written": written, "period": f"{start_year}-{end_year}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--start-year", type=int, default=2015)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--countries", default=None, help="comma-separated ISO2 codes")
    parser.add_argument("--missing-only", action="store_true", default=True)
    parser.add_argument("--all", action="store_true", help="refetch existing rows")
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    countries = (
        [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if args.countries else None
    )
    result = load(
        args.db_url, args.start_year, args.end_year, args.limit, countries,
        not args.all, args.sleep,
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
