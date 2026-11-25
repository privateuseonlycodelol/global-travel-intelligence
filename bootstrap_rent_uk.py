#!/usr/bin/env python3
"""
Official UK private rents from the ONS Price Index of Private Rents.

The ONS publishes an average monthly rent for every local authority in England,
Wales, Scotland and Northern Ireland, by dwelling type and bedroom count. This
loader reads the latest monthly XLSX, keeps the latest period per area, and
matches the area name to a loaded GB destination.

It writes into the same ``rent_monthly`` table as the Eurostat city rents, with
``area_name`` and ``area_code`` so the source geography is visible. Values stay
in GBP. A local-authority average is not a specific flat and mixes new and
existing tenancies; it is profile context, not a rank input.

Source: ONS, Price Index of Private Rents, Open Government Licence v3.0.
https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/priceindexofprivaterentsukmonthlypricestatistics

Usage
-----
    python bootstrap_rent_uk.py
    python bootstrap_rent_uk.py --report
"""

from __future__ import annotations

import argparse
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import openpyxl
import psycopg
import requests
from psycopg.rows import dict_row

import place_profiles as pp

DATASET_PAGE = (
    "https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/"
    "priceindexofprivaterentsukmonthlypricestatistics"
)
CACHE = Path("./data_cache/ons")
XLSX_CACHE = CACHE / "price_index_private_rents.xlsx"
SOURCE = "ONS Price Index of Private Rents (local authority)"
SOURCE_URL = DATASET_PAGE
BASIS = (
    "ONS average monthly rent, local authority, all tenancies (new and "
    "existing); GBP"
)
CURRENCY = "GBP"

AREA_ALIASES = {
    "lothian": "edinburgh",
}

# (key, bedrooms, column index of the published rent)
TYPES = (
    ("ALL", None, 7),
    ("FLAT1", 1, 11),
    ("FLAT2", 2, 15),
    ("FLAT3", 3, 19),
    ("BED4", 4, 23),
    ("HOUSE_D", None, 27),
    ("HOUSE_SD", None, 31),
    ("HOUSE_TERRACE", None, 35),
    ("FLAT_MAISONETTE", None, 39),
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rent_monthly (
    destination_id  TEXT NOT NULL REFERENCES destinations(destination_id) ON DELETE CASCADE,
    dwelling_type   TEXT NOT NULL,
    bedrooms        INT,
    rent_local      DOUBLE PRECISION NOT NULL,
    currency_code   TEXT NOT NULL,
    rent_basis      TEXT NOT NULL,
    area_name       TEXT,
    area_code       TEXT,
    year            INT NOT NULL,
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (destination_id, dwelling_type)
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def latest_xlsx_url(session: requests.Session) -> str:
    response = session.get(DATASET_PAGE, timeout=90)
    response.raise_for_status()
    links = re.findall(r'href="(/file\?uri=[^"]+\.xlsx)"', response.text)
    if not links:
        raise RuntimeError("no ONS XLSX link found on the dataset page")
    return "https://www.ons.gov.uk" + links[0]


def download(session: requests.Session) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    url = latest_xlsx_url(session)
    log(f"downloading {url}")
    response = session.get(url, timeout=300)
    response.raise_for_status()
    XLSX_CACHE.write_bytes(response.content)
    return XLSX_CACHE


def number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def read_latest(path: Path) -> dict[str, dict[str, Any]]:
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheet = workbook["Table 1"]
    latest: dict[str, tuple[str, str, str, tuple[Any, ...]]] = {}
    for row in sheet.iter_rows(min_row=4, values_only=True):
        if not row or not row[0] or not row[1]:
            continue
        period = str(row[0])[:7]
        code = str(row[1])
        if code.startswith("K") or code.startswith("["):
            continue
        current = latest.get(code)
        if current is None or period > current[0]:
            latest[code] = (period, str(row[2]), str(row[3] or ""), tuple(row))
    return {
        code: {
            "period": period,
            "area_name": name,
            "region": region,
            "values": {
                key: number(values[column]) for key, _bedrooms, column in TYPES
            },
        }
        for code, (period, name, region, values) in latest.items()
    }


def candidate_names(area_name: str) -> list[tuple[int, str]]:
    """Return (score, normalized name) candidates; lower score is more exact."""
    name = pp.normalize_name(area_name)
    stripped = name
    for prefix in ("city of ", "london borough of ", "royal borough of ", "the city of "):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    for suffix in (" city of", " county of", " county", " district", " borough", " council", " brma"):
        if stripped.endswith(suffix):
            stripped = stripped[:-len(suffix)].strip()
            break
    candidates = [(0, name)]
    if stripped != name:
        candidates.append((1, stripped))
    tokens = re.split(r"[\s,\-]+", stripped)
    for size in range(len(tokens) - 1, 0, -1):
        for start in range(0, len(tokens) - size + 1):
            phrase = " ".join(tokens[start:start + size])
            candidates.append((2, phrase))
    return candidates


def match_destinations(cur: Any, areas: dict[str, dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    cur.execute(
        "SELECT destination_id, name, population FROM destinations "
        "WHERE country_iso2 = 'GB'"
    )
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cur.fetchall():
        record = dict(row)
        normalized = pp.normalize_name(record["name"])
        by_name[normalized].append(record)
        for prefix in (
            "city of ", "london borough of ", "royal borough of ", "the city of ",
        ):
            if normalized.startswith(prefix):
                by_name[normalized[len(prefix):]].append(record)
                break
    matches: dict[str, str] = {}
    unmatched: list[str] = []
    for code, area in sorted(areas.items()):
        best: tuple[int, dict[str, Any]] | None = None
        names = [area["area_name"]]
        alias = AREA_ALIASES.get(pp.normalize_name(area["area_name"]))
        if alias:
            names.append(alias)
        for name in names:
            for score, candidate in candidate_names(name):
                options = by_name.get(candidate)
                if not options:
                    continue
                pick = max(options, key=lambda row: row["population"] or 0)
                if best is None or score < best[0]:
                    best = (score, pick)
                if score == 0:
                    break
            if best is not None and best[0] == 0:
                break
        if best is None:
            unmatched.append(area["area_name"])
            continue
        matches[code] = best[1]["destination_id"]
    return matches, unmatched


def load(database_url: str) -> dict[str, Any]:
    session = requests.Session()
    session.headers["User-Agent"] = "travel-db-ons-rent-loader/1.0"
    path = download(session)
    areas = read_latest(path)
    written = 0
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                "ALTER TABLE rent_monthly ADD COLUMN IF NOT EXISTS area_name TEXT"
            )
            cur.execute(
                "ALTER TABLE rent_monthly ADD COLUMN IF NOT EXISTS area_code TEXT"
            )
            matches, unmatched = match_destinations(cur, areas)
            cur.execute("DELETE FROM rent_monthly WHERE source = %s", (SOURCE,))
            for code, destination_id in matches.items():
                area = areas[code]
                year = int(area["period"][:4])
                for key, bedrooms, _column in TYPES:
                    value = area["values"].get(key)
                    if value is None:
                        continue
                    cur.execute(
                        """
                        INSERT INTO rent_monthly (
                            destination_id, dwelling_type, bedrooms, rent_local,
                            currency_code, rent_basis, area_name, area_code,
                            year, source, source_url, observed_period
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (destination_id, dwelling_type) DO UPDATE SET
                            bedrooms = EXCLUDED.bedrooms,
                            rent_local = EXCLUDED.rent_local,
                            currency_code = EXCLUDED.currency_code,
                            rent_basis = EXCLUDED.rent_basis,
                            area_name = EXCLUDED.area_name,
                            area_code = EXCLUDED.area_code,
                            year = EXCLUDED.year,
                            source = EXCLUDED.source,
                            source_url = EXCLUDED.source_url,
                            observed_period = EXCLUDED.observed_period,
                            updated_at = NOW()
                        """,
                        (
                            destination_id, key, bedrooms, value, CURRENCY, BASIS,
                            area["area_name"], code, year, SOURCE, SOURCE_URL,
                            area["period"],
                        ),
                    )
                    written += 1
        conn.commit()
    return {
        "areas": len(areas),
        "matched_areas": len(matches),
        "rows": written,
        "unmatched_sample": unmatched[:15],
    }


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, count(DISTINCT destination_id) cities, count(*) rows,
                       min(observed_period) first_period, max(observed_period) last_period
                FROM rent_monthly GROUP BY source ORDER BY source
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
