#!/usr/bin/env python3
"""
Place lookup and country context loaders.

Two small, global, bulk loads that make the database easier to query:

* ``destination_aliases`` -- every name GeoNames knows for each loaded city
  (official name, ASCII name and the ``alternatenames`` column of
  ``cities15000.zip``: exonyms such as "Lisbon"/"Lisboa"/"Lissabon", airport
  codes and native scripts). It is indexed with ``pg_trgm`` so the MCP server
  can resolve misspelled, accented or translated names and report ambiguity
  instead of silently picking the largest match.
* ``country_facts`` + ``country_indicators`` -- country-level context from
  GeoNames ``countryInfo.txt`` (capital, currency, languages, phone prefix,
  neighbours) and World Bank WDI/WGI indicators (price level vs USD, homicide
  rate, PM2.5 exposure, life expectancy, GDP per capita PPP, political
  stability). These are national values. They are stored per country and are
  never copied onto city rows; the MCP server labels them ``granularity:
  country``.

Usage
-----
    python bootstrap_places.py                 # aliases + country facts + indicators
    python bootstrap_places.py --skip-indicators
    python bootstrap_places.py --query-only

Requires Phase 1 (``destinations``) to be loaded first. Sources and licences
are documented in docs/DATA_SOURCES.md.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Iterable

import psycopg
import requests

import bootstrap_global_40k as phase1
from place_profiles import normalize_name

CACHE_DIR = Path("./data_cache")
COUNTRY_INFO_URL = "https://download.geonames.org/export/dump/countryInfo.txt"
WORLD_BANK_API = "https://api.worldbank.org/v2"
ALIAS_SOURCE = "GeoNames cities15000 (name, asciiname, alternatenames)"
COUNTRY_SOURCE = "GeoNames countryInfo.txt"
MAX_ALIAS_LENGTH = 120

# key -> (World Bank indicator id, World Bank source id or None, label, unit, better)
# ``better`` is "higher", "lower" or None and only drives how a percentile is read.
WORLD_BANK_INDICATORS: dict[str, tuple[str, int | None, str, str, str | None]] = {
    "homicide_per_100k": (
        "VC.IHR.PSRC.P5", None,
        "Intentional homicides per 100,000 people (UNODC via World Bank)",
        "per 100k people", "lower",
    ),
    "pm25_mean_annual_ugm3": (
        "EN.ATM.PM25.MC.M3", None,
        "PM2.5 air pollution, mean annual exposure",
        "µg/m³", "lower",
    ),
    "life_expectancy_years": (
        "SP.DYN.LE00.IN", None,
        "Life expectancy at birth",
        "years", "higher",
    ),
    "gdp_per_capita_ppp": (
        "NY.GDP.PCAP.PP.CD", None,
        "GDP per capita, PPP",
        "current international $", "higher",
    ),
    "political_stability_wgi": (
        "GOV_WGI_PV.EST", 3,
        "Political stability and absence of violence (WGI estimate, -2.5..+2.5)",
        "index", "higher",
    ),
}
# Derived: price level = PPP conversion factor (LCU per international $) /
# official exchange rate (LCU per USD), same year. 1.0 = US price level.
PRICE_LEVEL_KEY = "price_level_vs_usd"
PRICE_LEVEL_LABEL = (
    "Price level vs USA (PPP conversion factor / exchange rate; 1.0 = US prices)"
)
PRICE_LEVEL_PARTS = ("PA.NUS.PPP", "PA.NUS.FCRF")
INDICATOR_YEARS = "2012:2026"

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS destination_aliases (
    destination_id  TEXT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
    alias           TEXT NOT NULL,
    alias_norm      TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('name', 'ascii', 'alternate')),
    PRIMARY KEY (destination_id, alias_norm)
);
CREATE INDEX IF NOT EXISTS idx_destination_aliases_trgm
    ON destination_aliases USING GIN (alias_norm gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_destination_aliases_norm
    ON destination_aliases (alias_norm text_pattern_ops);

CREATE TABLE IF NOT EXISTS country_facts (
    country_iso2    CHAR(2) PRIMARY KEY,
    country_iso3    CHAR(3),
    name            TEXT NOT NULL,
    capital         TEXT,
    continent       CHAR(2),
    area_km2        DOUBLE PRECISION,
    population      BIGINT,
    currency_code   TEXT,
    currency_name   TEXT,
    phone_prefix    TEXT,
    tld             TEXT,
    languages       TEXT[] NOT NULL DEFAULT '{}',
    neighbours      TEXT[] NOT NULL DEFAULT '{}',
    source          TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS country_indicators (
    country_iso2    CHAR(2) NOT NULL REFERENCES country_facts(country_iso2) ON DELETE CASCADE,
    indicator       TEXT NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    year            INT NOT NULL,
    label           TEXT NOT NULL,
    unit            TEXT NOT NULL,
    better          TEXT CHECK (better IN ('higher', 'lower')),
    source          TEXT NOT NULL,
    source_indicator TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (country_iso2, indicator)
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def build_alias_rows(
    destinations: dict[int, str],
    geonames_lines: Iterable[str],
) -> list[tuple[str, str, str, str]]:
    """Return (destination_id, alias, alias_norm, kind) rows, one per normalized alias."""
    rows: list[tuple[str, str, str, str]] = []
    for line in geonames_lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 4 or not fields[0].isdigit():
            continue
        destination_id = destinations.get(int(fields[0]))
        if destination_id is None:
            continue
        seen: set[str] = set()
        candidates = [(fields[1], "name"), (fields[2], "ascii")]
        candidates += [(alt, "alternate") for alt in fields[3].split(",")]
        for alias, kind in candidates:
            alias = alias.strip()
            if not alias or len(alias) > MAX_ALIAS_LENGTH or "://" in alias:
                continue
            norm = normalize_name(alias)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            rows.append((destination_id, alias, norm, kind))
    return rows


def parse_country_info(text: str) -> list[dict[str, Any]]:
    """Parse GeoNames countryInfo.txt (tab-separated, '#' comment lines)."""
    countries: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) < 18 or len(f[0]) != 2:
            continue

        def num(value: str) -> float | None:
            try:
                return float(value) if value.strip() else None
            except ValueError:
                return None

        population = num(f[7])
        countries.append({
            "country_iso2": f[0].upper(),
            "country_iso3": f[1].upper() or None,
            "name": f[4],
            "capital": f[5] or None,
            "continent": f[8] or None,
            "area_km2": num(f[6]),
            "population": int(population) if population is not None else None,
            "tld": f[9] or None,
            "currency_code": f[10] or None,
            "currency_name": f[11] or None,
            "phone_prefix": f[12] or None,
            "languages": [code for code in f[15].split(",") if code],
            "neighbours": [code for code in f[17].split(",") if code],
        })
    return countries


def latest_values(records: Iterable[dict[str, Any]]) -> dict[str, tuple[float, int]]:
    """World Bank API records -> {iso2: (value, year)} keeping the latest non-null year."""
    latest: dict[str, tuple[float, int]] = {}
    for record in records:
        value = record.get("value")
        iso2 = ((record.get("country") or {}).get("id") or "").upper()
        try:
            year = int(record.get("date"))
        except (TypeError, ValueError):
            continue
        if value is None or len(iso2) != 2:
            continue
        if iso2 not in latest or year > latest[iso2][1]:
            latest[iso2] = (float(value), year)
    return latest


def price_level_values(
    ppp_records: Iterable[dict[str, Any]],
    fx_records: Iterable[dict[str, Any]],
) -> dict[str, tuple[float, int]]:
    """Latest same-year PPP / exchange-rate ratio per country."""
    def by_year(records: Iterable[dict[str, Any]]) -> dict[tuple[str, int], float]:
        out: dict[tuple[str, int], float] = {}
        for record in records:
            value = record.get("value")
            iso2 = ((record.get("country") or {}).get("id") or "").upper()
            try:
                year = int(record.get("date"))
            except (TypeError, ValueError):
                continue
            if value is not None and len(iso2) == 2:
                out[(iso2, year)] = float(value)
        return out

    ppp = by_year(ppp_records)
    fx = by_year(fx_records)
    result: dict[str, tuple[float, int]] = {}
    for (iso2, year), ppp_value in ppp.items():
        fx_value = fx.get((iso2, year))
        if not fx_value or fx_value <= 0 or ppp_value <= 0:
            continue
        if iso2 not in result or year > result[iso2][1]:
            result[iso2] = (ppp_value / fx_value, year)
    return result


def fetch_world_bank(indicator: str, source: int | None, retries: int = 4) -> list[dict[str, Any]]:
    """Fetch every country/year record for one indicator, cached on disk for a day."""
    cache_path = CACHE_DIR / "worldbank" / f"{indicator}.json"
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 86400:
        return json.loads(cache_path.read_text())
    params: dict[str, Any] = {
        "format": "json", "date": INDICATOR_YEARS, "per_page": 20000,
    }
    if source is not None:
        params["source"] = source
    url = f"{WORLD_BANK_API}/country/all/indicator/{indicator}"
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=90)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) < 2:
                raise ValueError(f"unexpected World Bank payload for {indicator}: {payload!r:.200}")
            records = payload[1] or []
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(records))
            return records
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"World Bank fetch failed for {indicator}: {last_error}")


def init_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def load_aliases(conn: psycopg.Connection) -> int:
    zip_path = phase1.download_cached(phase1.GEONAMES_URL, CACHE_DIR / "cities15000.zip")
    with conn.cursor() as cur:
        cur.execute("SELECT geoname_id, destination_id FROM destinations")
        destinations = {int(geoname_id): destination_id for geoname_id, destination_id in cur.fetchall()}
    with zipfile.ZipFile(zip_path) as zf, zf.open("cities15000.txt") as raw:
        lines = io.TextIOWrapper(raw, encoding="utf-8")
        rows = build_alias_rows(destinations, lines)
    with conn.cursor() as cur:
        # Aliases are derived entirely from the current destinations table.
        cur.execute("TRUNCATE destination_aliases")
        with cur.copy(
            "COPY destination_aliases (destination_id, alias, alias_norm, kind) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
    conn.commit()
    log(f"  loaded {len(rows):,} aliases for {len(destinations):,} destinations.")
    return len(rows)


def load_country_facts(conn: psycopg.Connection) -> int:
    path = phase1.download_cached(COUNTRY_INFO_URL, CACHE_DIR / "countryInfo.txt")
    countries = parse_country_info(path.read_text(encoding="utf-8"))
    period = time.strftime("%Y-%m", time.gmtime(path.stat().st_mtime))
    with conn.cursor() as cur:
        for c in countries:
            cur.execute(
                """
                INSERT INTO country_facts (
                    country_iso2, country_iso3, name, capital, continent, area_km2,
                    population, currency_code, currency_name, phone_prefix, tld,
                    languages, neighbours, source, observed_period, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (country_iso2) DO UPDATE SET
                    country_iso3 = EXCLUDED.country_iso3, name = EXCLUDED.name,
                    capital = EXCLUDED.capital, continent = EXCLUDED.continent,
                    area_km2 = EXCLUDED.area_km2, population = EXCLUDED.population,
                    currency_code = EXCLUDED.currency_code,
                    currency_name = EXCLUDED.currency_name,
                    phone_prefix = EXCLUDED.phone_prefix, tld = EXCLUDED.tld,
                    languages = EXCLUDED.languages, neighbours = EXCLUDED.neighbours,
                    source = EXCLUDED.source, observed_period = EXCLUDED.observed_period,
                    updated_at = NOW()
                """,
                (
                    c["country_iso2"], c["country_iso3"], c["name"], c["capital"],
                    c["continent"], c["area_km2"], c["population"],
                    c["currency_code"], c["currency_name"], c["phone_prefix"],
                    c["tld"], c["languages"], c["neighbours"],
                    COUNTRY_SOURCE, f"geonames-{period}",
                ),
            )
    conn.commit()
    log(f"  upserted {len(countries):,} countries.")
    return len(countries)


def load_country_indicators(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT country_iso2 FROM country_facts")
        known = {row[0] for row in cur.fetchall()}
    if not known:
        raise RuntimeError("country_facts is empty; load it before indicators.")

    loaded: list[tuple[Any, ...]] = []
    for key, (indicator, source_id, label, unit, better) in WORLD_BANK_INDICATORS.items():
        values = latest_values(fetch_world_bank(indicator, source_id))
        source = "World Bank WGI" if source_id == 3 else "World Bank WDI"
        for iso2, (value, year) in values.items():
            if iso2 in known:
                loaded.append((iso2, key, value, year, label, unit, better, source, indicator))
        log(f"  {key}: {sum(1 for r in loaded if r[1] == key):,} countries")

    ppp = fetch_world_bank(PRICE_LEVEL_PARTS[0], None)
    fx = fetch_world_bank(PRICE_LEVEL_PARTS[1], None)
    for iso2, (value, year) in price_level_values(ppp, fx).items():
        if iso2 in known:
            loaded.append((
                iso2, PRICE_LEVEL_KEY, value, year, PRICE_LEVEL_LABEL,
                "ratio to US", "lower", "World Bank WDI (derived)",
                "/".join(PRICE_LEVEL_PARTS),
            ))
    log(f"  {PRICE_LEVEL_KEY}: {sum(1 for r in loaded if r[1] == PRICE_LEVEL_KEY):,} countries")

    keys = list(WORLD_BANK_INDICATORS) + [PRICE_LEVEL_KEY]
    with conn.cursor() as cur:
        # Each run is authoritative for the indicators it loads.
        cur.execute("DELETE FROM country_indicators WHERE indicator = ANY(%s)", (keys,))
        cur.executemany(
            """
            INSERT INTO country_indicators (
                country_iso2, indicator, value, year, label, unit, better,
                source, source_indicator
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            loaded,
        )
    conn.commit()
    return len(loaded)


def print_coverage(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for label, sql in (
            ("destinations", "SELECT COUNT(*) FROM destinations"),
            ("destinations with aliases", "SELECT COUNT(DISTINCT destination_id) FROM destination_aliases"),
            ("aliases", "SELECT COUNT(*) FROM destination_aliases"),
            ("country_facts", "SELECT COUNT(*) FROM country_facts"),
            (
                "destinations with country facts",
                "SELECT COUNT(*) FROM destinations d JOIN country_facts c USING (country_iso2)",
            ),
        ):
            try:
                cur.execute(sql)
                log(f"  {label:34s} {cur.fetchone()[0]:>10,}")
            except psycopg.Error as exc:
                conn.rollback()
                log(f"  {label:34s} unavailable ({exc.__class__.__name__})")
        try:
            cur.execute(
                "SELECT indicator, COUNT(*), MIN(year), MAX(year) FROM country_indicators "
                "GROUP BY indicator ORDER BY indicator"
            )
            for indicator, count, min_year, max_year in cur.fetchall():
                log(f"  indicator {indicator:24s} {count:>5,} countries ({min_year}-{max_year})")
        except psycopg.Error:
            conn.rollback()


def main() -> None:
    global CACHE_DIR
    parser = argparse.ArgumentParser(description="Place aliases and country context loaders")
    parser.add_argument("--skip-aliases", action="store_true")
    parser.add_argument("--skip-countries", action="store_true")
    parser.add_argument("--skip-indicators", action="store_true")
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

    with phase1.connect_db(db_url) as conn:
        if args.query_only:
            print_coverage(conn)
            return
        init_schema(conn)
        if not args.skip_aliases:
            log("GeoNames aliases ...")
            load_aliases(conn)
        if not args.skip_countries:
            log("GeoNames country facts ...")
            load_country_facts(conn)
        if not args.skip_indicators:
            log("World Bank country indicators ...")
            load_country_indicators(conn)
        print_coverage(conn)


if __name__ == "__main__":
    main()
