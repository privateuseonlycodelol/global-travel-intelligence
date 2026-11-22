#!/usr/bin/env python3
"""
Global street-network (urban form) loader.

This table is **urban-form context, not walkability**, and is not shown in
``place_profile``. Houston scores higher than Berlin on intersection density,
which is why it must not be presented as a walking verdict; the published
How Walkable score in ``bootstrap_walkability.py`` is the user-facing measure.
The loader is kept for analysis and future urban-form questions.

It adds a global street-connectivity measure that does not depend on the
Overture pedestrian scan:

* ``intersect_count_clean_topo`` per km² of built-up area, from the
  *Global Urban Street Networks Indicators* dataset (Boeing, 2026, CC0),
  computed from 2025 OpenStreetMap street networks for 10,351 urban areas.
* ``prop_deadend`` (share of street nodes that are dead ends) from the same
  dataset, as a second, cheap signal of car-oriented street layouts.

Urban areas are matched to loaded destinations by the Global Human Settlement
Layer urban-centre boundary, using the centroids published in the GHS-UCDB
geography theme (CC BY 4.0). A destination accepts a match when its name is one
of the urban centre's names, or when the two centres are within 12 km; the
maximum search radius is 25 km and the country must agree.

Intersection density is a standard *component* of walkability (a denser, more
connected grid), not a door-to-door walking score: it does not measure
sidewalks, crossings, terrain, safety or how many destinations are reachable on
foot. It is therefore not presented in profiles.

Usage
-----
    python bootstrap_street_network.py
    python bootstrap_street_network.py --refresh       # re-download source files
    python bootstrap_street_network.py --report        # coverage only, no writes

Requires Phase 1 (``destinations``). Sources and licences are documented in
docs/DATA_SOURCES.md.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sqlite3
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg
import requests
from psycopg.rows import dict_row

from place_profiles import normalize_name

CACHE_DIR = Path("./data_cache/walkability")

INDICATORS_DATASET_DOI = "doi:10.7910/DVN/ZTFPTB"
INDICATORS_FILE_ID = 11058506  # indicators.tab in that Dataverse dataset
INDICATORS_URL = f"https://dataverse.harvard.edu/api/access/datafile/{INDICATORS_FILE_ID}"
INDICATORS_CACHE = CACHE_DIR / "indicators.tab"

CENTROIDS_URL = (
    "https://cidportal.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
    "GHS_UCDB_GLOBE_R2024A/GHS_UCDB_THEME_GLOBE_R2024A/"
    "GHS_UCDB_THEME_GEOGRAPHY_GLOBE_R2024A/V1-2/"
    "GHS_UCDB_THEME_GEOGRAPHY_GLOBE_R2024A_V1_2.zip"
)
CENTROIDS_ZIP_CACHE = CACHE_DIR / "ucdb_geography.zip"
CENTROIDS_GPKG_CACHE = CACHE_DIR / "ucdb_geography.gpkg"
CENTROIDS_GPKG_NAME = "GHS_UCDB_THEME_GEOGRAPHY_GLOBE_R2024A.gpkg"

MAX_MATCH_KM = 25.0
NEARBY_MATCH_KM = 12.0
MAX_NAME_MATCH_KM = 100.0  # large urban centres can have a distant centroid
MOLLWEIDE_RADIUS_M = 6371008.8  # ESRI:54009 World Mollweide

SOURCE = (
    "Global Urban Street Networks Indicators (Boeing 2026) from 2025 "
    "OpenStreetMap; GHSL urban centre boundary"
)
PERIOD = "OSM 2025; GHSL UCDB R2024A"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS street_network_metrics (
    destination_id        TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    urban_centre_id       INT NOT NULL,
    urban_centre_name     TEXT NOT NULL,
    country_iso3          CHAR(3) NOT NULL,
    match_distance_km     DOUBLE PRECISION NOT NULL,
    match_kind            TEXT NOT NULL CHECK (match_kind IN ('name', 'nearby')),
    built_up_area_km2     DOUBLE PRECISION,
    intersection_count    INT,
    intersections_per_km2 DOUBLE PRECISION,
    dead_end_share        DOUBLE PRECISION,
    four_way_share        DOUBLE PRECISION,
    street_length_km      DOUBLE PRECISION,
    resident_population   BIGINT,
    source                TEXT NOT NULL,
    observed_period       TEXT NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_street_network_uc
    ON street_network_metrics (urban_centre_id);
"""


def log(message: str) -> None:
    print(message, flush=True)


@dataclass(frozen=True)
class UrbanCentre:
    uc_id: int
    name: str
    country_iso3: str
    lat: float
    lon: float
    names: frozenset[str]
    core_name: str
    built_up_area_km2: float | None
    intersection_count: int | None
    intersections_per_km2: float | None
    dead_end_share: float | None
    four_way_share: float | None
    street_length_km: float | None
    resident_population: int | None


@dataclass(frozen=True)
class Match:
    centre: UrbanCentre
    distance_km: float
    kind: str  # "name" or "nearby"


def _number(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _integer(value: str | None) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def mollweide_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Inverse ESRI:54009 (World Mollweide) for a spherical Earth.

    The UCDB centroid layer is published in Mollweide metres; its CSV has no
    geographic coordinates, so the GPKG point is converted here.
    """
    theta = math.asin(max(-1.0, min(1.0, y / (MOLLWEIDE_RADIUS_M * math.sqrt(2)))))
    phi = math.asin(
        max(-1.0, min(1.0, (2 * theta + math.sin(2 * theta)) / math.pi))
    )
    lon = math.pi * x / (2 * math.sqrt(2) * MOLLWEIDE_RADIUS_M * math.cos(theta))
    return math.degrees(lon), math.degrees(phi)


def read_gpkg_points(path: Path, layer: str = "UC_centroids") -> dict[int, tuple[float, float]]:
    """Return ``{urban_centre_id: (lon, lat)}`` from a GeoPackage point layer."""
    out: dict[int, tuple[float, float]] = {}
    with sqlite3.connect(path) as conn:
        for uc_id, blob in conn.execute(
            f'SELECT ID_UC_G0, geom FROM "{layer}"'
        ):
            if not blob or blob[:2] != b"GP":
                continue
            flags = blob[3]
            envelope = (flags >> 1) & 0x07
            header_length = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(envelope, 0)
            wkb = blob[header_length:]
            if len(wkb) < 21 or wkb[0] != 0x01:
                continue
            geometry_type = struct.unpack("<I", wkb[1:5])[0]
            if geometry_type not in (1, 0x80000001):
                continue
            x, y = struct.unpack("<dd", wkb[5:21])
            out[int(uc_id)] = mollweide_to_lonlat(x, y)
    return out


def read_indicators(path: Path) -> dict[int, dict[str, Any]]:
    """Read the Dataverse TSV into ``{uc_id: indicator columns}``."""
    out: dict[int, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            uc_id = _integer(row.get("uc_id"))
            if uc_id is None:
                continue
            out[uc_id] = row
    return out


def build_centres(
    indicators: dict[int, dict[str, Any]],
    centroids: dict[int, tuple[float, float]],
) -> dict[int, UrbanCentre]:
    centres: dict[int, UrbanCentre] = {}
    for uc_id, row in indicators.items():
        point = centroids.get(uc_id)
        if point is None:
            continue
        built_up_m2 = _number(row.get("built_up_area_m2"))
        built_up_km2 = built_up_m2 / 1_000_000 if built_up_m2 and built_up_m2 > 0 else None
        intersections = _integer(row.get("intersect_count_clean_topo"))
        density = (
            intersections / built_up_km2
            if intersections is not None and built_up_km2
            else None
        )
        street_length_m = _number(row.get("length_total"))
        raw_names = [part for part in (row.get("uc_names") or "").split(";") if part.strip()]
        core = normalize_name((row.get("core_city") or "").replace("_", " "))
        names = {normalize_name(part) for part in raw_names}
        names.discard("")
        if core:
            names.add(core)
        centres[uc_id] = UrbanCentre(
            uc_id=uc_id,
            name=(row.get("core_city") or "unnamed").replace("_", " "),
            country_iso3=(row.get("country_iso") or "").upper(),
            lon=point[0],
            lat=point[1],
            names=frozenset(names),
            core_name=core,
            built_up_area_km2=built_up_km2,
            intersection_count=intersections,
            intersections_per_km2=density,
            dead_end_share=_number(row.get("prop_deadend")),
            four_way_share=_number(row.get("prop_4way")),
            street_length_km=street_length_m / 1000 if street_length_m else None,
            resident_population=_integer(row.get("resident_pop")),
        )
    return centres


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * MOLLWEIDE_RADIUS_M * math.asin(min(1.0, math.sqrt(a))) / 1000


def _grid_key(lat: float, lon: float) -> tuple[int, int]:
    return round(lat * 2), round(lon * 2)


def match_destinations(
    destinations: Iterable[tuple[str, str, str | None, float, float]],
    centres: dict[int, UrbanCentre],
    iso3_by_iso2: dict[str, str],
    max_km: float = MAX_MATCH_KM,
    nearby_km: float = NEARBY_MATCH_KM,
) -> dict[str, Match]:
    """Match ``(destination_id, name, iso2, lat, lon)`` rows to urban centres.

    A name match wins even when a different urban centre is slightly closer, and
    is not limited by ``max_km``: a large urban centre's centroid can sit more
    than 25 km from the city record (Phoenix is 26 km). A nameless match is
    accepted only inside ``nearby_km``, which keeps a suburb attached to its
    metro while refusing a separate town 20 km away.
    """
    grid: dict[tuple[int, int], list[UrbanCentre]] = {}
    for centre in centres.values():
        grid.setdefault(_grid_key(centre.lat, centre.lon), []).append(centre)

    matches: dict[str, Match] = {}
    for destination_id, name, iso2, lat, lon in destinations:
        wanted_name = normalize_name(name)
        wanted_iso3 = iso3_by_iso2.get((iso2 or "").upper())
        base = _grid_key(lat, lon)
        candidates: list[tuple[int, float, UrbanCentre]] = []
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                for centre in grid.get((base[0] + dlat, base[1] + dlon), ()):
                    if wanted_iso3 and centre.country_iso3 != wanted_iso3:
                        continue
                    distance = haversine_km(lat, lon, centre.lat, centre.lon)
                    name_hit = bool(wanted_name) and wanted_name in centre.names
                    if name_hit:
                        if distance > MAX_NAME_MATCH_KM:
                            continue
                    elif distance > max_km:
                        continue
                    candidates.append((0 if name_hit else 1, distance, centre))
        candidates.sort(key=lambda item: (item[0], item[1]))
        for name_rank, distance, centre in candidates:
            if name_rank == 0:
                matches[destination_id] = Match(centre, distance, "name")
                break
            # Without a usable country code, never accept a nameless neighbour.
            if wanted_iso3 and distance <= nearby_km:
                matches[destination_id] = Match(centre, distance, "nearby")
                break
    return matches


def write_rows(
    conn: psycopg.Connection,
    matches: dict[str, Match],
) -> int:
    rows = []
    for destination_id, match in matches.items():
        centre = match.centre
        rows.append(
            (
                destination_id,
                centre.uc_id,
                centre.name,
                centre.country_iso3 or "UNK",
                match.distance_km,
                match.kind,
                centre.built_up_area_km2,
                centre.intersection_count,
                centre.intersections_per_km2,
                centre.dead_end_share,
                centre.four_way_share,
                centre.street_length_km,
                centre.resident_population,
                SOURCE,
                PERIOD,
            )
        )
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute("DELETE FROM street_network_metrics")
        cur.executemany(
            """
            INSERT INTO street_network_metrics (
                destination_id, urban_centre_id, urban_centre_name, country_iso3,
                match_distance_km, match_kind, built_up_area_km2, intersection_count,
                intersections_per_km2, dead_end_share, four_way_share,
                street_length_km, resident_population, source, observed_period
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def _download(url: str, path: Path, refresh: bool) -> None:
    if path.exists() and not refresh:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    with requests.get(url, timeout=180, stream=True) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)


def ensure_sources(refresh: bool) -> tuple[Path, Path]:
    _download(INDICATORS_URL, INDICATORS_CACHE, refresh)
    if refresh or not CENTROIDS_GPKG_CACHE.exists():
        _download(CENTROIDS_URL, CENTROIDS_ZIP_CACHE, refresh)
        CENTROIDS_GPKG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(CENTROIDS_ZIP_CACHE) as archive:
            with archive.open(CENTROIDS_GPKG_NAME) as source, CENTROIDS_GPKG_CACHE.open("wb") as target:
                target.write(source.read())
    return INDICATORS_CACHE, CENTROIDS_GPKG_CACHE


def load_iso3(cur: Any) -> dict[str, str]:
    cur.execute("SELECT country_iso2, country_iso3 FROM country_facts")
    mapping = {
        str(row["country_iso2"]).upper(): str(row["country_iso3"]).upper()
        for row in cur.fetchall()
        if row["country_iso3"]
    }
    if mapping:
        return mapping
    # country_facts is optional; fall back to the cached GeoNames file.
    path = Path("./data_cache/countryInfo.txt")
    if not path.exists():
        return {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0]:
            mapping[parts[0].upper()] = parts[1].upper()
    return mapping


def run(database_url: str, refresh: bool = False, report_only: bool = False) -> dict[str, Any]:
    indicators_path, centroids_path = ensure_sources(refresh)
    indicators = read_indicators(indicators_path)
    centres = build_centres(indicators, read_gpkg_points(centroids_path))
    log(f"urban centres with indicators + centroid: {len(centres):,}")

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT destination_id, name, country_iso2, lat, lon FROM destinations"
            )
            destinations = [
                (
                    row["destination_id"],
                    row["name"],
                    row["country_iso2"],
                    float(row["lat"]),
                    float(row["lon"]),
                )
                for row in cur.fetchall()
            ]
            iso3_by_iso2 = load_iso3(cur)
        matches = match_destinations(destinations, centres, iso3_by_iso2)
        summary = {
            "destinations": len(destinations),
            "urban_centres": len(centres),
            "matched": len(matches),
            "name_matches": sum(1 for m in matches.values() if m.kind == "name"),
            "nearby_matches": sum(1 for m in matches.values() if m.kind == "nearby"),
        }
        log(
            "matched {matched:,} of {destinations:,} destinations "
            "({name_matches:,} by name, {nearby_matches:,} nearby)".format(**summary)
        )
        if not report_only:
            written = write_rows(conn, matches)
            summary["written"] = written
            log(f"wrote {written:,} rows to street_network_metrics")
        else:
            missing = [d for d in destinations if d[0] not in matches]
            missing.sort(key=lambda d: d[1])
            if missing:
                sample = ", ".join(sorted({d[1] for d in missing})[:30])
                log(f"not matched ({len(missing):,}), examples: {sample}")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-url",
        default=os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL"),
        help="PostgreSQL URL (default: SUPABASE_DB_URL / DATABASE_URL)",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="re-download the source files"
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
