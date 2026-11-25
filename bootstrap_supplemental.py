#!/usr/bin/env python3
"""
Optional supplemental loaders for official data that is not part of Phase 1/3.

Currently implements:

* Eurostat regional tourism pressure (NUTS 2) -> tourism_pressure
  Dataset: tour_occ_nin2m (nights spent at tourist accommodation
  establishments by month and NUTS 2 region), annual total, unit=NR,
  c_resid=TOTAL, nace_r2=I551-I553.
  Population denominator: demo_r_pjangrp3 (population on 1 January by NUTS 3
  region), aggregated to NUTS 2.

The loader is deliberately conservative:

* It assigns a NUTS 2 region to a city by testing the city centroid against
  the official GISCO NUTS 2 boundary. It does not invent a city-level tourism
  count.
* `tourist_nights_annual` is the NUTS 2 total. `nights_per_resident` uses the
  NUTS 2 population, not the city population.
* Rows are written only where Eurostat actually publishes a value. Missing
  regions stay NULL and are reported.

Usage
-----
    python bootstrap_supplemental.py --eurostat-tourism --year 2024
    python bootstrap_supplemental.py --eurostat-tourism --countries ES,FR
    python bootstrap_supplemental.py --query-only

Sources and licences are documented in docs/DATA_SOURCES.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg
import requests

CACHE_DIR = Path("./data_cache")
NUTS2_GEOJSON_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_01M_2024_4326_LEVL_2.geojson"
)
EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
TOURISM_DATASET = "tour_occ_nin2m"
POPULATION_DATASET = "demo_r_pjangrp3"
TOURISM_SOURCE = "Eurostat tour_occ_nin2m + demo_r_pjangrp3 (NUTS 2)"


def log(message: str) -> None:
    print(message, flush=True)


def download_cached(url: str, dest: Path, retries: int = 3) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        try:
            log(f"  downloading {url} -> {dest.name} (attempt {attempt}/{retries}) ...")
            with requests.get(url, stream=True, timeout=(30, 300)) as response:
                response.raise_for_status()
                with open(tmp, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            tmp.replace(dest)
            return dest
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            if attempt == retries:
                raise
            log(f"  download failed ({exc}); retrying ...")
            time.sleep(2 * attempt)
    return dest


def fetch_eurostat(dataset: str, params: dict[str, str], retries: int = 3) -> dict:
    url = f"{EUROSTAT_BASE}/{dataset}"
    merged = {"format": "JSON", "lang": "en", **params}
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=merged, timeout=120)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if attempt == retries:
                raise
            log(f"  Eurostat {dataset} failed ({exc}); retrying ...")
            time.sleep(3 * attempt)
    raise AssertionError("unreachable")


def _index_to_code(payload: dict, dimension: str) -> dict[int, str]:
    category = payload["dimension"][dimension]["category"]["index"]
    return {int(index): str(code) for code, index in category.items()}


def _values_by_geo(payload: dict, dimension: str = "geo") -> dict[str, float]:
    """Parse a JSON-stat response whose non-geo dimensions are all size 1."""
    index_to_code = _index_to_code(payload, dimension)
    sizes = payload.get("size", [])
    if not sizes:
        return {}
    # The flat value index is the geo index only when every other dimension
    # has size 1. This loader requests exactly that shape and verifies it.
    non_geo = [
        size for name, size in zip(payload.get("id", []), sizes)
        if name != dimension
    ]
    if any(size != 1 for size in non_geo):
        raise ValueError(
            f"Unexpected JSON-stat shape for {payload.get('label')}: "
            f"non-geo dimensions are not all size 1 ({non_geo})"
        )
    out: dict[str, float] = {}
    for flat_index, value in payload.get("value", {}).items():
        try:
            code = index_to_code.get(int(flat_index))
            if code is not None and value is not None:
                out[code] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _values_by_geo_month(payload: dict) -> dict[tuple[str, str], float]:
    """Parse a JSON-stat response with geo and a 12-month dimension."""
    dims = payload.get("id", [])
    sizes = payload.get("size", [])
    if "geo" not in dims or "month" not in dims:
        return {}
    categories = {
        name: {int(index): str(code) for code, index in
               payload["dimension"][name]["category"]["index"].items()}
        for name in ("geo", "month")
    }
    out: dict[tuple[str, str], float] = {}
    for flat_index, value in payload.get("value", {}).items():
        if value is None:
            continue
        try:
            remainder = int(flat_index)
        except (TypeError, ValueError):
            continue
        coords: dict[str, int] = {}
        for name, size in reversed(list(zip(dims, sizes))):
            coords[name] = remainder % size
            remainder //= size
        geo = categories["geo"].get(coords["geo"])
        month = categories["month"].get(coords["month"])
        if geo and month and month.startswith("M"):
            out[(geo, month)] = float(value)
    return out


def fetch_nuts2_codes(cache_dir: Path) -> set[str]:
    path = download_cached(NUTS2_GEOJSON_URL, cache_dir / "NUTS_RG_01M_2024_4326_LEVL_2.geojson")
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    codes = set()
    for feature in payload.get("features", []):
        props = feature.get("properties") or {}
        if int(props.get("LEVL_CODE", 0)) == 2:
            code = props.get("NUTS_ID")
            if code:
                codes.add(str(code))
    return codes


def load_nuts2_geometry(conn: psycopg.Connection, cache_dir: Path) -> int:
    path = download_cached(NUTS2_GEOJSON_URL, cache_dir / "NUTS_RG_01M_2024_4326_LEVL_2.geojson")
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    features = [
        feature for feature in payload.get("features", [])
        if int((feature.get("properties") or {}).get("LEVL_CODE", 0)) == 2
    ]
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TEMP TABLE tmp_nuts2 (
                nuts2 TEXT PRIMARY KEY,
                geom  GEOMETRY(Geometry, 4326) NOT NULL
            ) ON COMMIT DROP;
            """
        )
        for feature in features:
            props = feature.get("properties") or {}
            code = props.get("NUTS_ID")
            geometry = feature.get("geometry")
            if not code or not geometry:
                continue
            cur.execute(
                "INSERT INTO tmp_nuts2 (nuts2, geom) "
                "VALUES (%s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)) "
                "ON CONFLICT (nuts2) DO NOTHING",
                (str(code), json.dumps(geometry)),
            )
        cur.execute("CREATE INDEX ON tmp_nuts2 USING GIST (geom);")
        cur.execute("SELECT COUNT(*) FROM tmp_nuts2")
        count = cur.fetchone()[0]
    return count


def load_eurostat_tourism(
    conn: psycopg.Connection, countries: list[str] | None, year: int
) -> None:
    nuts2_codes = fetch_nuts2_codes(CACHE_DIR)
    log(f"  {len(nuts2_codes):,} official NUTS 2 region codes in GISCO 2024.")

    with conn.cursor() as cur:
        cur.execute("ALTER TABLE destinations ADD COLUMN IF NOT EXISTS nuts2_code TEXT;")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_destinations_nuts2 "
            "ON destinations (nuts2_code);"
        )
    conn.commit()

    geometry_count = load_nuts2_geometry(conn, CACHE_DIR)
    log(f"  loaded {geometry_count:,} NUTS 2 geometries.")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE destinations d
            SET nuts2_code = n.nuts2
            FROM tmp_nuts2 n
            WHERE ST_Intersects(n.geom, d.centroid::geometry)
            """
        )
        assigned = cur.rowcount
    conn.commit()
    log(f"  assigned nuts2_code to {assigned:,} cities by centroid.")

    tourism_payload = fetch_eurostat(
        TOURISM_DATASET,
        {
            "time": str(year),
            "month": "TOTAL",
            "unit": "NR",
            "c_resid": "TOTAL",
            "nace_r2": "I551-I553",
        },
    )
    tourism = _values_by_geo(tourism_payload)
    tourism = {
        code: value for code, value in tourism.items()
        if code in nuts2_codes
    }
    log(f"  Eurostat {TOURISM_DATASET}: {len(tourism):,} NUTS 2 values for {year}.")

    monthly_payload = fetch_eurostat(
        TOURISM_DATASET,
        {
            "time": str(year),
            "unit": "NR",
            "c_resid": "TOTAL",
            "nace_r2": "I551-I553",
        },
    )
    monthly = _values_by_geo_month(monthly_payload)
    log(f"  Eurostat {TOURISM_DATASET}: {len(monthly):,} NUTS 2 month values.")

    population_payload = fetch_eurostat(
        POPULATION_DATASET,
        {
            "time": str(year),
            "sex": "T",
            "age": "TOTAL",
            "unit": "NR",
        },
    )
    population_by_geo = _values_by_geo(population_payload)
    population_nuts2: dict[str, float] = {}
    population_nuts3: dict[str, float] = {}
    for code, value in population_by_geo.items():
        if len(code) == 5:
            population_nuts3[code] = value
        elif len(code) == 4:
            population_nuts2[code] = value
    # Prefer the sum of NUTS 3 populations; fall back to a directly published
    # NUTS 2 total when NUTS 3 values are unavailable.
    summed: dict[str, float] = {}
    for code, value in population_nuts3.items():
        nuts2 = code[:4]
        summed[nuts2] = summed.get(nuts2, 0.0) + value
    population = {code: value for code, value in population_nuts2.items() if value > 0}
    for code, value in summed.items():
        population.setdefault(code, value)
    log(f"  population denominators for {len(population):,} NUTS 2 regions.")

    scope_sql = ""
    scope_params: list = []
    if countries:
        scope_sql = " AND d.country_iso2 = ANY(%s)"
        scope_params.append(countries)

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tourism_monthly (
                destination_id  TEXT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
                month           INT NOT NULL CHECK (month BETWEEN 1 AND 12),
                tourist_nights  BIGINT,
                nights_per_resident DOUBLE PRECISION,
                source          TEXT NOT NULL,
                observed_period TEXT NOT NULL,
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (destination_id, month)
            );
        """)
        # The run is authoritative for its scope: remove old tourism rows first
        # so a region that disappeared from the source does not keep stale data.
        cur.execute(
            "DELETE FROM tourism_pressure t USING destinations d "
            "WHERE t.destination_id = d.destination_id"
            + scope_sql,
            scope_params,
        )
        cur.execute(
            "DELETE FROM tourism_monthly t USING destinations d "
            "WHERE t.destination_id = d.destination_id"
            + scope_sql,
            scope_params,
        )
        for code in tourism:
            if code not in population:
                continue
            cur.execute(
                """
                INSERT INTO tourism_pressure (
                    destination_id, tourist_nights_annual, nights_per_resident,
                    source, observed_period
                )
                SELECT
                    d.destination_id,
                    %s::BIGINT,
                    ROUND((%s::DOUBLE PRECISION / NULLIF(%s::DOUBLE PRECISION, 0))::NUMERIC, 2),
                    %s,
                    %s
                FROM destinations d
                WHERE d.nuts2_code = %s
                """ + scope_sql + """
                ON CONFLICT (destination_id) DO UPDATE SET
                    tourist_nights_annual = EXCLUDED.tourist_nights_annual,
                    nights_per_resident = EXCLUDED.nights_per_resident,
                    source = EXCLUDED.source,
                    observed_period = EXCLUDED.observed_period,
                    updated_at = NOW()
                """,
                (
                    int(round(tourism[code])),
                    tourism[code],
                    population[code],
                    TOURISM_SOURCE,
                    str(year),
                    code,
                    *scope_params,
                ),
            )
    with conn.cursor() as cur:
        for (code, month), value in monthly.items():
            if code not in population or code not in nuts2_codes:
                continue
            cur.execute(
                """
                INSERT INTO tourism_monthly (
                    destination_id, month, tourist_nights, nights_per_resident,
                    source, observed_period
                )
                SELECT
                    d.destination_id,
                    %s::INT,
                    %s::BIGINT,
                    ROUND((%s::DOUBLE PRECISION / NULLIF(%s::DOUBLE PRECISION, 0))::NUMERIC, 2),
                    %s,
                    %s
                FROM destinations d
                WHERE d.nuts2_code = %s
                """ + scope_sql + """
                ON CONFLICT (destination_id, month) DO UPDATE SET
                    tourist_nights = EXCLUDED.tourist_nights,
                    nights_per_resident = EXCLUDED.nights_per_resident,
                    source = EXCLUDED.source,
                    observed_period = EXCLUDED.observed_period,
                    updated_at = NOW()
                """,
                (
                    int(month[1:]),
                    int(round(value)),
                    value,
                    population[code],
                    TOURISM_SOURCE + " monthly",
                    str(year),
                    code,
                    *scope_params,
                ),
            )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM tourism_pressure")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tourism_monthly")
        monthly_total = cur.fetchone()[0]
    log(f"  tourism_pressure rows now: {total:,}.")
    log(f"  tourism_monthly rows now: {monthly_total:,}.")


def print_coverage(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM destinations")
        cities = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM destinations WHERE nuts2_code IS NOT NULL")
        assigned = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM tourism_pressure")
        pressure = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM tourism_pressure WHERE nights_per_resident IS NOT NULL"
        )
        scored = cur.fetchone()[0]
        cur.execute(
            "SELECT source, observed_period, COUNT(*) FROM tourism_pressure "
            "GROUP BY 1,2 ORDER BY 3 DESC LIMIT 5"
        )
        rows = cur.fetchall()
    log(f"  destinations: {cities:,}; nuts2 assigned: {assigned:,}; "
        f"tourism rows: {pressure:,}; with nights/resident: {scored:,}")
    for source, period, count in rows:
        log(f"    {source} | {period} | {count:,}")


def connect_db(db_url: str) -> psycopg.Connection:
    kwargs: dict = {"connect_timeout": 30, "application_name": "bootstrap_supplemental"}
    if "pooler.supabase.com" in db_url or ":6543" in db_url:
        kwargs["prepare_threshold"] = None
    conn = psycopg.connect(db_url, **kwargs)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute("SET statement_timeout = 0")
    return conn


def main() -> None:
    global CACHE_DIR
    parser = argparse.ArgumentParser(description="Supplemental official data loaders")
    parser.add_argument("--eurostat-tourism", action="store_true",
                        help="Load Eurostat NUTS 2 tourism pressure")
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--countries", default=None,
                        help="Comma-separated ISO-2 scope, e.g. ES,FR")
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--query-only", action="store_true")
    args = parser.parse_args()

    CACHE_DIR = args.cache_dir
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    db_url = args.db_url or os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        log("Error: set SUPABASE_DB_URL (or pass --db-url).")
        sys.exit(1)

    countries = (
        [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if args.countries else None
    )
    with connect_db(db_url) as conn:
        if args.query_only:
            print_coverage(conn)
            return
        if args.eurostat_tourism:
            log(f"Eurostat NUTS 2 tourism pressure, year {args.year} ...")
            load_eurostat_tourism(conn, countries, args.year)
            print_coverage(conn)
        else:
            parser.error("choose a loader, e.g. --eurostat-tourism")


if __name__ == "__main__":
    main()
