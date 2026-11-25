#!/usr/bin/env python3
"""
Load the published GHSCI 25-city indicator study.

The Figshare package contains one row per study city with the same components
as a fresh GHSCI run, plus the published `all_cities_walkability` index. This
loader maps those rows to loaded destinations and stores them alongside the
Trento pilot. It is a separate, older evidence set (2021), not a 2026 run.

Source: https://doi.org/10.25439/rmt.15072009.v3
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg
from psycopg.rows import dict_row

from bootstrap_ghsci import SCHEMA_SQL
from place_profiles import normalize_name

STUDY_URL = "https://doi.org/10.25439/rmt.15072009.v3"
STUDY_SOURCE = "GHSCI 25-city study (2021)"
UPSERT_SQL = """
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
    source_url = EXCLUDED.source_url,
    observed_period = EXCLUDED.observed_period,
    osm_extract_date = EXCLUDED.osm_extract_date,
    ghsl_epoch = EXCLUDED.ghsl_epoch,
    ucdb_release = EXCLUDED.ucdb_release,
    sample_interval_m = EXCLUDED.sample_interval_m,
    updated_at = NOW()
"""


def _f(row: dict[str, str], key: str) -> float | None:
    value = (row.get(key) or "").strip()
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _i(row: dict[str, str], key: str) -> int | None:
    value = _f(row, key)
    return int(value) if value is not None else None


def find_destination(cur, city: str, iso2: str) -> str | None:
    cur.execute(
        """
        SELECT destination_id FROM destinations
        WHERE country_iso2 = %s AND lower(name) = lower(%s)
        ORDER BY population DESC LIMIT 1
        """,
        (iso2, city),
    )
    row = cur.fetchone()
    if row:
        return str(row["destination_id"])
    cur.execute(
        """
        SELECT d.destination_id
        FROM destination_aliases a JOIN destinations d USING (destination_id)
        WHERE d.country_iso2 = %s AND a.alias_norm = %s
        ORDER BY d.population DESC LIMIT 1
        """,
        (iso2, normalize_name(city)),
    )
    row = cur.fetchone()
    return str(row["destination_id"]) if row else None


def load(conn: psycopg.Connection, path: Path) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        cur.execute(
            "ALTER TABLE ghsci_city_indicators "
            "ALTER COLUMN urban_centre_id DROP NOT NULL"
        )
        rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
        loaded, skipped = 0, []
        for row in rows:
            city, iso2 = (row.get("City") or "").strip(), (row.get("ISO 3166-1 alpha-2") or "").strip()
            destination_id = find_destination(cur, city, iso2)
            if not destination_id:
                skipped.append(f"{city} ({iso2})")
                continue
            sample_points = _i(row, "urban_sample_point_count") or 0
            density = _f(row, "local_nh_population_density") or 0.0
            intersections = _f(row, "local_nh_intersection_density") or 0.0
            cur.execute(
                UPSERT_SQL,
                (
                    destination_id, None, "GHSCI 25-city study", "study",
                    _f(row, "Area (sqkm)"), _i(row, "Population estimate"),
                    _f(row, "Population per sqkm"), _f(row, "Intersections per sqkm"),
                    sample_points,
                    _f(row, "pop_pct_access_500m_fresh_food_market_score"),
                    _f(row, "pop_pct_access_500m_convenience_score"),
                    _f(row, "pop_pct_access_500m_pt_any_score"),
                    _f(row, "pop_pct_access_500m_public_open_space_any_score"),
                    _f(row, "pop_pct_access_500m_public_open_space_large_score"),
                    _f(row, "local_nh_population_density"),
                    _f(row, "local_nh_intersection_density"),
                    _f(row, "local_daily_living"),
                    _f(row, "all_cities_walkability"),
                    sample_points < 1000 or density <= 0 or intersections <= 0,
                    STUDY_SOURCE, STUDY_URL, "2021", None,
                    "2021-06-21", "2015", "GHS-UCDB R2019A", 30,
                ),
            )
            loaded += 1
        conn.commit()
    return {"loaded": loaded, "skipped": len(skipped), "skipped_cities": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    with psycopg.connect(args.db_url, row_factory=dict_row) as conn:
        result = load(conn, args.csv)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
