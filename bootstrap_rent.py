#!/usr/bin/env python3
"""
Official city rents from Eurostat ``prc_colc_rents``.

The dataset publishes an average monthly rent in cities for five dwelling
types. It is a small, high-quality, official sample, not a global rent index:
42 cities in the EU/EFTA/UK/TR/Balkans. Values stay in the local currency and
are never FX-converted or mixed with short-let asking prices.

This table is profile context. It is intentionally **not** a rank input:
comparing EUR, CZK, HUF and GBP values as one cost score would be wrong, and
the rent bases differ from country-specific sources. ``housing_monthly``
remains the separate, empty-by-default Inside Airbnb table.

Source: Eurostat, ``prc_colc_rents``, "Average rent per month in cities by
type of dwelling", annual. Reuse with attribution.
https://ec.europa.eu/eurostat/databrowser/view/prc_colc_rents/default/table

Usage
-----
    python bootstrap_rent.py
    python bootstrap_rent.py --report
"""

from __future__ import annotations

import argparse
import os
from typing import Any

import psycopg
import requests
from psycopg.rows import dict_row

import place_profiles as pp

API = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
    "prc_colc_rents?format=JSON&lang=EN"
)
DATASET_URL = (
    "https://ec.europa.eu/eurostat/databrowser/view/prc_colc_rents/default/table"
)
SOURCE = "Eurostat prc_colc_rents (average rent per month in cities)"
BASIS = (
    "Eurostat average monthly rent in cities, annual, all tenancies; "
    "local currency, not FX-converted"
)

# English city names with the expected country, so a fuzzy match cannot land
# on a same-named city in another country. Culham and Valletta are published
# by Eurostat but are below the 40k city table's inclusion threshold.
CITY_QUERIES = {
    "BE_CAP": ("Brussels", "BE"),
    "BG_CAP": ("Sofia", "BG"),
    "CZ_CAP": ("Prague", "CZ"),
    "DK_CAP": ("Copenhagen", "DK"),
    "DE_CAP": ("Berlin", "DE"),
    "DE_DEL1": ("Karlsruhe", "DE"),
    "DE_DEL2": ("Munich", "DE"),
    "DE_DEL3": ("Bonn", "DE"),
    "EE_CAP": ("Tallinn", "EE"),
    "IE_CAP": ("Dublin", "IE"),
    "EL_CAP": ("Athens", "GR"),
    "ES_CAP": ("Madrid", "ES"),
    "FR_CAP": ("Paris", "FR"),
    "HR_CAP": ("Zagreb", "HR"),
    "IT_CAP": ("Rome", "IT"),
    "IT_DEL": ("Varese", "IT"),
    "CY_CAP": ("Nicosia", "CY"),
    "LV_CAP": ("Riga", "LV"),
    "LT_CAP": ("Vilnius", "LT"),
    "LU_CAP": ("Luxembourg", "LU"),
    "HU_CAP": ("Budapest", "HU"),
    "NL_DEL": ("The Hague", "NL"),
    "AT_CAP": ("Vienna", "AT"),
    "PL_CAP": ("Warsaw", "PL"),
    "PT_CAP": ("Lisbon", "PT"),
    "RO_CAP": ("Bucharest", "RO"),
    "SI_CAP": ("Ljubljana", "SI"),
    "SK_CAP": ("Bratislava", "SK"),
    "FI_CAP": ("Helsinki", "FI"),
    "SE_CAP": ("Stockholm", "SE"),
    "IS_CAP": ("Reykjavík", "IS"),
    "NO_CAP": ("Oslo", "NO"),
    "CH_CAP": ("Bern", "CH"),
    "CH_DEL": ("Geneva", "CH"),
    "UK_CAP": ("London", "GB"),
    "BA_CAP": ("Sarajevo", "BA"),
    "ME_CAP": ("Podgorica", "ME"),
    "MK_CAP": ("Skopje", "MK"),
    "AL_CAP": ("Tirana", "AL"),
    "RS_CAP": ("Belgrade", "RS"),
    "TR_CAP": ("Ankara", "TR"),
    "XK_CAP": ("Pristina", "XK"),
}

# National currency of the reference year. Kept explicit so a later currency
# change (for example Bulgaria adopting the euro in 2026) cannot relabel a
# 2025 rent.
CURRENCY_BY_ISO2 = {
    "BE": "EUR", "BG": "BGN", "CZ": "CZK", "DK": "DKK", "DE": "EUR",
    "EE": "EUR", "IE": "EUR", "GR": "EUR", "ES": "EUR", "FR": "EUR",
    "HR": "EUR", "IT": "EUR", "CY": "EUR", "LV": "EUR", "LT": "EUR",
    "LU": "EUR", "HU": "HUF", "NL": "EUR", "AT": "EUR", "PL": "PLN",
    "PT": "EUR", "RO": "RON", "SI": "EUR", "SK": "EUR", "FI": "EUR",
    "SE": "SEK", "IS": "ISK", "NO": "NOK", "CH": "CHF", "GB": "GBP",
    "BA": "BAM", "ME": "EUR", "MK": "MKD", "AL": "ALL", "RS": "RSD",
    "TR": "TRY", "XK": "EUR",
}

BEDROOMS = {"FLAT1": 1, "FLAT2": 2, "FLAT3": 3}
HEADLINE_TYPE = "FLAT2"

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


def _flat_index(dims: list[str], sizes: list[int], cats: dict[str, dict[str, int]],
                **coords: str) -> int:
    index = 0
    for dim, size in zip(dims, sizes):
        index = index * size + cats[dim][coords[dim]]
    return index


def parse(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return ``{geo_code: {building: row}}`` using the latest NAC year."""
    dims = payload["id"]
    sizes = payload["size"]
    cats = {name: dim["category"]["index"] for name, dim in payload["dimension"].items()}
    labels = payload["dimension"]["building"]["category"]["label"]
    values = payload["value"]
    out: dict[str, dict[str, Any]] = {}
    for geo in cats["geo"]:
        if geo not in CITY_QUERIES:
            continue
        buildings: dict[str, Any] = {}
        for building in cats["building"]:
            latest = None
            for year in cats["time"]:
                index = _flat_index(
                    dims, sizes, cats,
                    freq="A", building=building, currency="NAC", geo=geo, time=year,
                )
                value = values.get(str(index))
                if value is not None:
                    latest = (int(year), float(value))
            if latest is None:
                continue
            buildings[building] = {
                "dwelling_type": building,
                "label": labels.get(building, building),
                "bedrooms": BEDROOMS.get(building),
                "rent_local": latest[1],
                "year": latest[0],
            }
        if buildings:
            out[geo] = buildings
    return out


def load(database_url: str) -> dict[str, Any]:
    response = requests.get(API, timeout=120)
    response.raise_for_status()
    parsed = parse(response.json())
    written = 0
    skipped: list[str] = []
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(
                "ALTER TABLE rent_monthly ADD COLUMN IF NOT EXISTS area_name TEXT"
            )
            cur.execute(
                "ALTER TABLE rent_monthly ADD COLUMN IF NOT EXISTS area_code TEXT"
            )
            cur.execute(
                "SELECT country_iso2, currency_code FROM country_facts"
            )
            currency = {
                str(row["country_iso2"]).upper(): row["currency_code"]
                for row in cur.fetchall()
            }
            cur.execute("DELETE FROM rent_monthly WHERE source = %s", (SOURCE,))
            for geo, buildings in sorted(parsed.items()):
                name, iso2 = CITY_QUERIES[geo]
                # ONS is the local-authority source for Great Britain and
                # Northern Ireland; do not overwrite it with a city average.
                if iso2 == "GB":
                    skipped.append(f"{geo} {name} (covered by ONS)")
                    continue
                resolved = pp.resolve_place(cur, f"{name}, {iso2}")["resolved"]
                if not resolved or resolved["country_iso2"] != iso2:
                    skipped.append(f"{geo} {name}")
                    continue
                code = CURRENCY_BY_ISO2.get(iso2) or currency.get(iso2) or "UNK"
                for building in buildings.values():
                    cur.execute(
                        """
                        INSERT INTO rent_monthly (
                            destination_id, dwelling_type, bedrooms, rent_local,
                            currency_code, rent_basis, year, source, source_url,
                            observed_period, area_name, area_code
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (destination_id, dwelling_type) DO UPDATE SET
                            bedrooms = EXCLUDED.bedrooms,
                            rent_local = EXCLUDED.rent_local,
                            currency_code = EXCLUDED.currency_code,
                            rent_basis = EXCLUDED.rent_basis,
                            year = EXCLUDED.year,
                            source = EXCLUDED.source,
                            source_url = EXCLUDED.source_url,
                            observed_period = EXCLUDED.observed_period,
                            area_name = EXCLUDED.area_name,
                            area_code = EXCLUDED.area_code,
                            updated_at = NOW()
                        """,
                        (
                            resolved["destination_id"], building["dwelling_type"],
                            building["bedrooms"], building["rent_local"], code,
                            BASIS, building["year"], SOURCE, DATASET_URL,
                            str(building["year"]), resolved["name"], geo,
                        ),
                    )
                    written += 1
        conn.commit()
    return {
        "geographies": len(parsed),
        "rows": written,
        "skipped": skipped,
        "headline_type": HEADLINE_TYPE,
    }


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(DISTINCT destination_id) AS cities, count(*) AS rows,
                       min(year) AS first_year, max(year) AS last_year
                FROM rent_monthly
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT d.name, r.dwelling_type, r.rent_local, r.currency_code, r.year
                FROM rent_monthly r JOIN destinations d USING (destination_id)
                ORDER BY d.name, r.dwelling_type
                LIMIT 20
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
