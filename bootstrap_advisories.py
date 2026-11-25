#!/usr/bin/env python3
"""
Country travel advisories from two official governments.

Two separate scales are stored side by side and never averaged:

* **US Department of State** travel advisories, levels 1-4, JSON API.
* **UK FCDO** foreign travel advice, with the published ``alert_status``
  (for example ``avoid_all_travel_to_parts``) and review date.

Both are country-scoped. Neither is a city crime rate, and neither is copied
onto a destination row. The profile shows them in the country block with their
own source and date.

Usage
-----
    python bootstrap_advisories.py
    python bootstrap_advisories.py --report
"""

from __future__ import annotations

import argparse
import html
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg
import requests
from psycopg.rows import dict_row

import place_profiles as pp

US_URL = "https://cadataapi.state.gov/api/TravelAdvisories"
UK_INDEX_URL = "https://www.gov.uk/api/content/foreign-travel-advice"
US_SOURCE = "US Department of State Travel Advisory"
UK_SOURCE = "UK FCDO Foreign Travel Advice"

UK_LABELS = {
    "avoid_all_travel_to_whole_country": "Advise against all travel",
    "avoid_all_but_essential_travel_to_whole_country": (
        "Advise against all but essential travel"
    ),
    "avoid_all_travel_to_parts": "Advise against all travel to parts of the country",
    "avoid_all_but_essential_travel_to_parts": (
        "Advise against all but essential travel to parts of the country"
    ),
    None: "No blanket advisory against travel",
}

# Names used by one advisory publisher but not by GeoNames country_facts.
COUNTRY_ALIASES = {
    "macau": "MO",
    "gaza": "PS",
    "the west bank": "PS",
    "palestine": "PS",
    "bonaire": "BQ",
    "saba and sint eustatius": "BQ",
    "bonaire/st eustatius/saba": "BQ",
    "netherlands": "NL",
    "french saint martin": "MF",
    "the bahamas": "BS",
    "federated states of micronesia": "FM",
    "mexico travel advisory": "MX",
    "burma": "MM",
    "myanmar (burma)": "MM",
    "cote d'ivoire": "CI",
    "côte d'ivoire": "CI",
    "the kyrgyz republic": "KG",
    "the gambia": "GM",
    "naoero": "NR",
    "republic of congo": "CG",
    "congo": "CG",
    "usa": "US",
    "cape verde": "CV",
    "st kitts and nevis": "KN",
    "st lucia": "LC",
    "st maarten": "SX",
    "st vincent and the grenadines": "VC",
    "st martin and st barthelemy": "MF",
    "st pierre & miquelon": "PM",
    "st helena, ascension and tristan da cunha": "SH",
    "pitcairn island": "PN",
    "antarctica/british antarctic territory": "AQ",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS travel_advisory (
    country_iso2   CHAR(2) NOT NULL REFERENCES country_facts(country_iso2) ON DELETE CASCADE,
    source         TEXT NOT NULL,
    level          INT,
    level_label    TEXT,
    status_code    TEXT,
    headline       TEXT,
    summary        TEXT,
    url            TEXT NOT NULL,
    advisory_date  TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (country_iso2, source)
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def strip_html(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def us_level(title: str) -> int | None:
    match = re.search(r"Level\s*([1-4])", title or "")
    return int(match.group(1)) if match else None


def us_country_name(title: str) -> str:
    return re.split(r"\s+-\s+Level", title or "", maxsplit=1)[0].strip()


def uk_label(status: str | None) -> str:
    return UK_LABELS.get(status, status or UK_LABELS[None])


def country_lookup(cur: Any) -> dict[str, str]:
    cur.execute("SELECT country_iso2, name FROM country_facts")
    lookup = {
        pp.normalize_name(row["name"]): str(row["country_iso2"])
        for row in cur.fetchall()
    }
    for name, iso2 in COUNTRY_ALIASES.items():
        lookup[pp.normalize_name(name)] = iso2
    return lookup


def resolve_country(name: str, lookup: dict[str, str]) -> str | None:
    return lookup.get(pp.normalize_name(name))


def fetch_us(session: requests.Session) -> list[dict[str, Any]]:
    response = session.get(US_URL, timeout=90)
    response.raise_for_status()
    rows = []
    for item in response.json():
        title = item.get("Title") or ""
        rows.append({
            "country_name": us_country_name(title),
            "level": us_level(title),
            "level_label": title.split(": ", 1)[1] if ": " in title else None,
            "headline": title,
            "summary": strip_html(item.get("Summary")),
            "url": item.get("Link"),
            "advisory_date": str(item.get("Updated") or "")[:10] or None,
        })
    return rows


def fetch_uk(session: requests.Session) -> list[dict[str, Any]]:
    index = session.get(UK_INDEX_URL, timeout=90)
    index.raise_for_status()
    children = index.json()["links"]["children"]

    def one(child: dict[str, Any]) -> dict[str, Any] | None:
        try:
            response = session.get(child["api_url"], timeout=45)
            response.raise_for_status()
            details = response.json()["details"]
        except Exception:
            return None
        status = details.get("alert_status")
        if isinstance(status, list):
            status = status[0] if status else None
        return {
            "country_name": details["country"]["name"],
            "status_code": status,
            "level_label": uk_label(status),
            "headline": f"UK travel advice: {details['country']['name']}",
            "summary": None,
            "url": "https://www.gov.uk" + child["base_path"],
            "advisory_date": str(details.get("reviewed_at") or "")[:10] or None,
        }

    with ThreadPoolExecutor(max_workers=8) as pool:
        return [row for row in pool.map(one, children) if row]


def load(database_url: str) -> dict[str, Any]:
    session = requests.Session()
    session.headers["User-Agent"] = "travel-db-advisory-loader/1.0"
    us_rows = fetch_us(session)
    uk_rows = fetch_uk(session)
    written = {"us": 0, "uk": 0}
    skipped: list[str] = []
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            lookup = country_lookup(cur)
            cur.execute("DELETE FROM travel_advisory")
            for source, rows, key in (
                (US_SOURCE, us_rows, "us"),
                (UK_SOURCE, uk_rows, "uk"),
            ):
                for row in rows:
                    iso2 = resolve_country(row["country_name"], lookup)
                    if not iso2:
                        skipped.append(f"{source}: {row['country_name']}")
                        continue
                    cur.execute(
                        """
                        INSERT INTO travel_advisory (
                            country_iso2, source, level, level_label, status_code,
                            headline, summary, url, advisory_date
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (country_iso2, source) DO UPDATE SET
                            level = EXCLUDED.level,
                            level_label = EXCLUDED.level_label,
                            status_code = EXCLUDED.status_code,
                            headline = EXCLUDED.headline,
                            summary = EXCLUDED.summary,
                            url = EXCLUDED.url,
                            advisory_date = EXCLUDED.advisory_date,
                            updated_at = NOW()
                        """,
                        (
                            iso2, source, row.get("level"), row["level_label"],
                            row.get("status_code"), row["headline"], row["summary"],
                            row["url"], row["advisory_date"],
                        ),
                    )
                    written[key] += 1
        conn.commit()
    return {"us": written["us"], "uk": written["uk"], "skipped": skipped}


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source, count(*) rows, max(advisory_date) latest "
                "FROM travel_advisory GROUP BY source ORDER BY source"
            )
            for row in cur.fetchall():
                print(dict(row))
            cur.execute(
                """
                SELECT c.name, a.source, a.level, a.level_label, a.advisory_date
                FROM travel_advisory a JOIN country_facts c USING (country_iso2)
                WHERE c.country_iso2 IN ('FR','MX','US','GB','TH')
                ORDER BY c.name, a.source
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
