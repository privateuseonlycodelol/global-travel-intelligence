#!/usr/bin/env python3
"""
Import GHSCI city-summary results and expose them as separate metrics.

This is the last mile of the GHSCI pilot: the analysis produces
``<codename>_indicators_region.csv``; this loader stores the components in
``ghsci_city_indicators`` and computes the published 25-city-reference
z-score. Raw OSM extracts and the per-city database are not imported.

The comparable index is the sum of the three published z-scores:

    (local_nh_population_density - 11093.1) / 13637.9
  + (local_nh_intersection_density - 98.44) / 36.96
  + (local_daily_living - 1.028) / 0.915

It is not a 0-100 score. GHSCI and How Walkable correlate only ~0.2-0.3 and
are presented separately.

Usage
-----
    python bootstrap_ghsci.py \
        --csv /path/to/IT_Trento_2026_indicators_region.csv \
        --destination-id gn-3165243 --urban-centre-id 2884 \
        --urban-centre-name Trento --match-kind name \
        --db-url postgresql:///travel_global?host=/var/run/postgresql
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

REFERENCE = {
    "local_nh_population_density": (11093.1, 13637.9),
    "local_nh_intersection_density": (98.44, 36.96),
    "local_daily_living": (1.028, 0.915),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ghsci_city_indicators (
    destination_id  TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    urban_centre_id INT NOT NULL,
    urban_centre_name TEXT NOT NULL,
    match_kind      TEXT NOT NULL,
    area_km2        DOUBLE PRECISION,
    population      BIGINT,
    population_per_km2 DOUBLE PRECISION,
    intersections_per_km2 DOUBLE PRECISION,
    sample_points   INT,
    pct_fresh_food_500m DOUBLE PRECISION,
    pct_convenience_500m DOUBLE PRECISION,
    pct_transit_500m DOUBLE PRECISION,
    pct_open_space_500m DOUBLE PRECISION,
    pct_large_open_space_500m DOUBLE PRECISION,
    local_nh_population_density DOUBLE PRECISION,
    local_nh_intersection_density DOUBLE PRECISION,
    local_daily_living DOUBLE PRECISION,
    ghsci_walkability_index DOUBLE PRECISION,
    low_confidence  BOOLEAN NOT NULL DEFAULT FALSE,
    source          TEXT NOT NULL,
    source_url      TEXT,
    observed_period TEXT NOT NULL,
    ghsci_commit    TEXT,
    osm_extract_date TEXT,
    ghsl_epoch      TEXT,
    ucdb_release    TEXT,
    sample_interval_m INT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ghsci_jobs (
    urban_centre_id INT PRIMARY KEY,
    destination_id  TEXT,
    status          TEXT NOT NULL CHECK (status IN ('queued','running','done','failed')),
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error           TEXT
);
"""


def _number(row: dict[str, str], key: str) -> float | None:
    value = (row.get(key) or "").strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _int(row: dict[str, str], key: str) -> int | None:
    value = _number(row, key)
    return int(value) if value is not None else None


def reference_index(row: dict[str, str]) -> float | None:
    parts = []
    for key, (mean, sd) in REFERENCE.items():
        value = _number(row, key)
        if value is None:
            return None
        parts.append((value - mean) / sd)
    return round(sum(parts), 4)


def parse_summary(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise ValueError(f"{path} has {len(rows)} rows; expected one city summary")
    return rows[0]


def load(
    conn: psycopg.Connection,
    path: Path,
    destination_id: str,
    urban_centre_id: int,
    urban_centre_name: str,
    match_kind: str,
    source: str,
    source_url: str,
    observed_period: str,
    ghsci_commit: str | None,
    osm_extract_date: str | None,
    ghsl_epoch: str | None,
    ucdb_release: str | None,
    sample_interval_m: int | None,
) -> dict[str, object]:
    row = parse_summary(path)
    index = reference_index(row)
    sample_points = _int(row, "urban_sample_point_count") or 0
    density = _number(row, "local_nh_population_density") or 0.0
    intersections = _number(row, "local_nh_intersection_density") or 0.0
    low_confidence = sample_points < 1000 or density <= 0 or intersections <= 0
    values = (
        destination_id,
        urban_centre_id,
        urban_centre_name,
        match_kind,
        _number(row, "Area (sqkm)"),
        _int(row, "Population estimate"),
        _number(row, "Population per sqkm"),
        _number(row, "Intersections per sqkm"),
        sample_points,
        _number(row, "pop_pct_access_500m_fresh_food_market_score"),
        _number(row, "pop_pct_access_500m_convenience_score"),
        _number(row, "pop_pct_access_500m_pt_any_score"),
        _number(row, "pop_pct_access_500m_public_open_space_any_score"),
        _number(row, "pop_pct_access_500m_public_open_space_large_score"),
        _number(row, "local_nh_population_density"),
        _number(row, "local_nh_intersection_density"),
        _number(row, "local_daily_living"),
        index,
        low_confidence,
        source,
        source_url,
        observed_period,
        ghsci_commit,
        osm_extract_date,
        ghsl_epoch,
        ucdb_release,
        sample_interval_m,
    )
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute(
            """
            INSERT INTO ghsci_city_indicators (
                destination_id, urban_centre_id, urban_centre_name, match_kind,
                area_km2, population, population_per_km2, intersections_per_km2,
                sample_points, pct_fresh_food_500m, pct_convenience_500m,
                pct_transit_500m, pct_open_space_500m, pct_large_open_space_500m,
                local_nh_population_density, local_nh_intersection_density,
                local_daily_living, ghsci_walkability_index, low_confidence,
                source, source_url, observed_period, ghsci_commit,
                osm_extract_date, ghsl_epoch, ucdb_release, sample_interval_m
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (destination_id) DO UPDATE SET
                urban_centre_id = EXCLUDED.urban_centre_id,
                urban_centre_name = EXCLUDED.urban_centre_name,
                match_kind = EXCLUDED.match_kind,
                area_km2 = EXCLUDED.area_km2,
                population = EXCLUDED.population,
                population_per_km2 = EXCLUDED.population_per_km2,
                intersections_per_km2 = EXCLUDED.intersections_per_km2,
                sample_points = EXCLUDED.sample_points,
                pct_fresh_food_500m = EXCLUDED.pct_fresh_food_500m,
                pct_convenience_500m = EXCLUDED.pct_convenience_500m,
                pct_transit_500m = EXCLUDED.pct_transit_500m,
                pct_open_space_500m = EXCLUDED.pct_open_space_500m,
                pct_large_open_space_500m = EXCLUDED.pct_large_open_space_500m,
                local_nh_population_density = EXCLUDED.local_nh_population_density,
                local_nh_intersection_density = EXCLUDED.local_nh_intersection_density,
                local_daily_living = EXCLUDED.local_daily_living,
                ghsci_walkability_index = EXCLUDED.ghsci_walkability_index,
                low_confidence = EXCLUDED.low_confidence,
                source = EXCLUDED.source,
                observed_period = EXCLUDED.observed_period,
                ghsci_commit = EXCLUDED.ghsci_commit,
                osm_extract_date = EXCLUDED.osm_extract_date,
                ghsl_epoch = EXCLUDED.ghsl_epoch,
                ucdb_release = EXCLUDED.ucdb_release,
                sample_interval_m = EXCLUDED.sample_interval_m,
                updated_at = NOW()
            """,
            values,
        )
    conn.commit()
    return {"destination_id": destination_id, "index": index, "sample_points": sample_points,
            "low_confidence": low_confidence}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--destination-id", required=True)
    parser.add_argument("--urban-centre-id", required=True, type=int)
    parser.add_argument("--urban-centre-name", required=True)
    parser.add_argument("--match-kind", default="name")
    parser.add_argument("--source", default="GHSCI (healthysustainablecities/global-indicators)")
    parser.add_argument("--source-url", default="https://github.com/healthysustainablecities/global-indicators")
    parser.add_argument("--observed-period", default="2026-11-22")
    parser.add_argument("--ghsci-commit", default=None)
    parser.add_argument("--osm-extract-date", default=None)
    parser.add_argument("--ghsl-epoch", default="2025")
    parser.add_argument("--ucdb-release", default="GHS-UCDB R2024A")
    parser.add_argument("--sample-interval-m", type=int, default=30)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    with psycopg.connect(args.db_url, row_factory=dict_row) as conn:
        result = load(
            conn, args.csv, args.destination_id, args.urban_centre_id,
            args.urban_centre_name, args.match_kind, args.source,
            args.source_url, args.observed_period, args.ghsci_commit, args.osm_extract_date,
            args.ghsl_epoch, args.ucdb_release, args.sample_interval_m,
        )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
