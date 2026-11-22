#!/usr/bin/env python3
"""
Published city walkability scores.

Loads the *How Walkable* city ranking (https://howwalkable.com/cities),
a 0-100 score for 690 cities of at least 300,000 people in 95 countries.
The score counts everyday places (groceries, cafes, shops, parks, services)
within a walk, averaged over the 5 km around each city's busiest point, from
Overture Maps and OpenStreetMap. It is licensed CC BY 4.0.

Why this source: Walk Score is proprietary and covers only the US and Canada;
ITDP's Atlas publishes access components rather than a single score; the
Global Healthy and Sustainable City Indicators dataset covers 25 cities.
How Walkable is the only free, global, city-level 0-100 score found that has
a published method and a reusable licence.

Coverage limits (kept explicit in the database and in `place_profile`):
mainland China, South Korea and Russia are not ranked because their business
listings are not openly licensed; cities below 300,000 people are not ranked;
the score describes the 5 km around the centre, not the whole metro; and it
counts amenity proximity, not sidewalks, crossings, traffic safety or shade.

Usage
-----
    python bootstrap_walkability.py
    python bootstrap_walkability.py --refresh
    python bootstrap_walkability.py --report

Requires Phase 1 (``destinations``); uses ``destination_aliases`` when present.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg
import requests
from psycopg.rows import dict_row

from place_profiles import normalize_name

CACHE_DIR = Path("./data_cache/walkability")
SCORES_URL = "https://howwalkable.com/cities.csv"
SCORES_CACHE = CACHE_DIR / "howwalkable.csv"

RANKED_CITIES = 690
MAX_MATCH_KM = 60.0
SOURCE = "How Walkable (howwalkable.com), CC BY 4.0"
PERIOD = "2026-10-22"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS walkability_scores (
    destination_id  TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    score           SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    centre_score    SMALLINT CHECK (centre_score BETWEEN 0 AND 100),
    rank_position   INT,
    ranked_cities   INT NOT NULL,
    city_label      TEXT NOT NULL,
    region          TEXT,
    country_name    TEXT NOT NULL,
    population      BIGINT,
    source          TEXT NOT NULL,
    observed_period TEXT NOT NULL,
    url             TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def log(message: str) -> None:
    print(message, flush=True)


@dataclass(frozen=True)
class Destination:
    destination_id: str
    name: str
    country_iso2: str
    lat: float
    lon: float


@dataclass(frozen=True)
class ScoreRow:
    city: str
    region: str | None
    country: str
    score: int
    centre_score: int | None
    rank_position: int | None
    population: int | None
    url: str | None
    centre_lat: float
    centre_lon: float


@dataclass(frozen=True)
class ScoreMatch:
    row: ScoreRow
    distance_km: float
    match_kind: str  # "name" or "alias"


def _int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def read_scores(path: Path) -> list[ScoreRow]:
    rows: list[ScoreRow] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            score = _int(raw.get("score"))
            lat = raw.get("centre_lat")
            lon = raw.get("centre_lon")
            if score is None or not lat or not lon:
                continue
            rows.append(
                ScoreRow(
                    city=(raw.get("city") or "").strip(),
                    region=(raw.get("region") or "").strip() or None,
                    country=(raw.get("country") or "").strip(),
                    score=score,
                    centre_score=_int(raw.get("centre_score")),
                    rank_position=_int(raw.get("rank")),
                    population=_int(raw.get("population")),
                    url=(raw.get("url") or "").strip() or None,
                    centre_lat=float(lat),
                    centre_lon=float(lon),
                )
            )
    return rows


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(a)))


def build_country_map(cur: Any, country_info_path: Path) -> dict[str, str]:
    """Normalized country name -> ISO2, from country_facts or GeoNames."""
    mapping: dict[str, str] = {}
    cur.execute("SELECT to_regclass('country_facts') IS NOT NULL AS present")
    has_facts = cur.fetchone()["present"]
    if has_facts:
        cur.execute("SELECT country_iso2, name FROM country_facts")
        for row in cur.fetchall():
            mapping[normalize_name(row["name"])] = str(row["country_iso2"]).upper()
    if not mapping and country_info_path.exists():
        for line in country_info_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 5 and parts[0]:
                mapping[normalize_name(parts[4])] = parts[0].upper()
    for key, iso2 in list(mapping.items()):
        if key.startswith("the "):
            mapping.setdefault(key[4:], iso2)
    mapping.setdefault("united states of america", "US")
    mapping.setdefault("holland", "NL")
    return mapping


def match_scores(
    rows: Iterable[ScoreRow],
    destinations: Iterable[Destination],
    aliases: dict[tuple[str, str], list[Destination]],
    country_map: dict[str, str],
    max_km: float = MAX_MATCH_KM,
) -> tuple[dict[str, ScoreMatch], list[ScoreRow]]:
    """Match score rows to destinations by name + country, then aliases."""
    by_name: dict[tuple[str, str], list[Destination]] = {}
    for destination in destinations:
        key = (normalize_name(destination.name), destination.country_iso2.upper())
        by_name.setdefault(key, []).append(destination)

    matches: dict[str, ScoreMatch] = {}
    unmatched: list[ScoreRow] = []
    for row in rows:
        iso2 = country_map.get(normalize_name(row.country))
        if not iso2:
            unmatched.append(row)
            continue
        name = normalize_name(row.city)
        candidates = by_name.get((name, iso2))
        kind = "name"
        if not candidates:
            candidates = aliases.get((name, iso2))
            kind = "alias"
        if not candidates:
            unmatched.append(row)
            continue
        ranked = sorted(
            candidates,
            key=lambda d: haversine_km(
                row.centre_lat, row.centre_lon, d.lat, d.lon
            ),
        )
        best = ranked[0]
        distance = haversine_km(row.centre_lat, row.centre_lon, best.lat, best.lon)
        if distance > max_km:
            unmatched.append(row)
            continue
        previous = matches.get(best.destination_id)
        if previous is not None:
            # Two ranked labels for one destination: keep the better rank.
            if (previous.row.rank_position or 10**9) <= (row.rank_position or 10**9):
                continue
        matches[best.destination_id] = ScoreMatch(row, distance, kind)
    return matches, unmatched


def write_rows(conn: psycopg.Connection, matches: dict[str, ScoreMatch]) -> int:
    rows = [
        (
            destination_id,
            match.row.score,
            match.row.centre_score,
            match.row.rank_position,
            RANKED_CITIES,
            match.row.city,
            match.row.region,
            match.row.country,
            match.row.population,
            SOURCE,
            PERIOD,
            match.row.url,
        )
        for destination_id, match in matches.items()
    ]
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute("DELETE FROM walkability_scores")
        cur.executemany(
            """
            INSERT INTO walkability_scores (
                destination_id, score, centre_score, rank_position, ranked_cities,
                city_label, region, country_name, population, source,
                observed_period, url
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def load_aliases(cur: Any) -> dict[tuple[str, str], list[Destination]]:
    cur.execute("SELECT to_regclass('destination_aliases') IS NOT NULL AS present")
    present = cur.fetchone()["present"]
    if not present:
        return {}
    cur.execute(
        """
        SELECT a.alias_norm, d.destination_id, d.name, d.country_iso2, d.lat, d.lon
        FROM destination_aliases a
        JOIN destinations d USING (destination_id)
        """
    )
    aliases: dict[tuple[str, str], list[Destination]] = {}
    for row in cur.fetchall():
        destination = Destination(
            row["destination_id"],
            row["name"],
            str(row["country_iso2"]),
            float(row["lat"]),
            float(row["lon"]),
        )
        aliases.setdefault(
            (row["alias_norm"], str(row["country_iso2"]).upper()), []
        ).append(destination)
    return aliases


def load_destinations(cur: Any) -> list[Destination]:
    cur.execute(
        "SELECT destination_id, name, country_iso2, lat, lon FROM destinations"
    )
    return [
        Destination(
            row["destination_id"],
            row["name"],
            str(row["country_iso2"]),
            float(row["lat"]),
            float(row["lon"]),
        )
        for row in cur.fetchall()
    ]


def _download(url: str, path: Path, refresh: bool) -> None:
    if path.exists() and not refresh:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    with requests.get(url, timeout=60) as response:
        response.raise_for_status()
        path.write_bytes(response.content)


def run(
    database_url: str,
    refresh: bool = False,
    report_only: bool = False,
) -> dict[str, Any]:
    _download(SCORES_URL, SCORES_CACHE, refresh)
    rows = read_scores(SCORES_CACHE)

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            destinations = load_destinations(cur)
            aliases = load_aliases(cur)
            country_map = build_country_map(cur, Path("./data_cache/countryInfo.txt"))
        matches, unmatched = match_scores(rows, destinations, aliases, country_map)
        summary = {
            "scored_cities": len(rows),
            "destinations": len(destinations),
            "matched": len(matches),
            "name_matches": sum(1 for m in matches.values() if m.match_kind == "name"),
            "alias_matches": sum(1 for m in matches.values() if m.match_kind == "alias"),
            "unmatched": len(unmatched),
        }
        log(
            "matched {matched:,} of {scored_cities:,} scored cities "
            "({name_matches:,} by name, {alias_matches:,} by alias)".format(**summary)
        )
        if unmatched:
            sample = ", ".join(f"{r.city} ({r.country})" for r in unmatched[:20])
            log(f"unmatched ({len(unmatched)}): {sample}")
        if not report_only:
            written = write_rows(conn, matches)
            summary["written"] = written
            log(f"wrote {written:,} rows to walkability_scores")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL"),
        help="PostgreSQL URL (default: SUPABASE_DB_URL / DATABASE_URL)",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="re-download the source CSV"
    )
    parser.add_argument(
        "--report", action="store_true", help="print coverage without writing"
    )
    args = parser.parse_args(argv)
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL / DATABASE_URL")
    run(args.db_url, refresh=args.refresh, report_only=args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
