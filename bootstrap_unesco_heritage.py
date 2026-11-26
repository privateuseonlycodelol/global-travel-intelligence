#!/usr/bin/env python3
"""
UNESCO World Heritage cultural & mixed sites intersected with GHSL urban centres.

Downloads the official UNESCO World Heritage Centre open dataset (`whc001`)
from data.unesco.org, extracts every Cultural and Mixed site (including all
individual serial component coordinates in `components_list`), and intersects
them with all 11,422 GHSL UCDB R2024A urban-centre polygons (with a 2 km
edge buffer so historic cores adjacent to a polygon boundary are not missed).

Scoring uses the council's saturating anchor:
  0 sites -> 40.0 (uninformative prior, not a cultural zero)
  1 site  -> 70.0
  2 sites -> 85.0
  3+ sites -> 100.0

Source: UNESCO World Heritage Centre (`whc001`), CC BY-SA 3.0 IGO.
https://data.unesco.org/explore/dataset/whc001/

Usage
-----
    python bootstrap_unesco_heritage.py --db-url "$DATABASE_URL"
    python bootstrap_unesco_heritage.py --db-url "$DATABASE_URL" --report
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any

import psycopg
import requests
from psycopg.rows import dict_row

CACHE_DIR = Path("./data_cache/unesco")
UNESCO_CSV_URL = (
    "https://data.unesco.org/api/explore/v2.1/catalog/datasets/whc001/exports/csv"
)
SOURCE = "UNESCO World Heritage Centre (whc001, Cultural & Mixed sites)"
SOURCE_URL = "https://data.unesco.org/explore/dataset/whc001/"
OBSERVED_PERIOD = "2026"
EDGE_BUFFER_M = 2000

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS unesco_heritage_points (
    site_id         TEXT NOT NULL,
    component_idx   INT NOT NULL,
    site_name       TEXT NOT NULL,
    category        TEXT NOT NULL,
    date_inscribed  INT,
    iso_codes       TEXT,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,
    geom            geometry(Point, 4326) NOT NULL,
    PRIMARY KEY (site_id, component_idx)
);

CREATE INDEX IF NOT EXISTS idx_unesco_heritage_points_geom
    ON unesco_heritage_points USING gist (geom);

CREATE TABLE IF NOT EXISTS urban_centre_heritage (
    urban_centre_id      BIGINT PRIMARY KEY,
    cultural_site_count  INT NOT NULL,
    mixed_site_count     INT NOT NULL,
    total_heritage_sites INT NOT NULL,
    heritage_score_0_100 DOUBLE PRECISION NOT NULL,
    site_names           TEXT,
    source               TEXT NOT NULL,
    source_url           TEXT NOT NULL,
    observed_period      TEXT NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def compute_heritage_score(site_count: int) -> float:
    """Council saturating anchor: 0->40, 1->70, 2->85, 3+->100."""
    n = max(0, int(site_count))
    if n == 0:
        return 40.0
    if n == 1:
        return 70.0
    if n == 2:
        return 85.0
    return 100.0


def _parse_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if (-180.0 <= f <= 180.0) else None
    except (TypeError, ValueError):
        return None


def parse_unesco_csv(path: Path) -> list[dict[str, Any]]:
    """Parse Cultural and Mixed UNESCO sites into component point dicts."""
    csv.field_size_limit(10_000_000)
    points: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            category = (row.get("category") or "").strip()
            if category not in ("Cultural", "Mixed"):
                continue
            site_id = (row.get("id_no") or "").strip()
            name = (row.get("name_en") or "").strip()
            if not site_id or not name:
                continue
            year_raw = (row.get("date_inscribed") or "").strip()
            date_inscribed = int(year_raw) if year_raw.isdigit() else None
            iso_codes = (row.get("iso_codes") or "").strip()

            coords: list[tuple[float, float]] = []
            comp_raw = (row.get("components_list") or "").strip()
            if comp_raw:
                try:
                    comp_list = json.loads(comp_raw)
                    if isinstance(comp_list, list):
                        for item in comp_list:
                            lat = _parse_float(item.get("latitude"))
                            lon = _parse_float(item.get("longitude"))
                            if lat is not None and lon is not None and -90 <= lat <= 90:
                                coords.append((lat, lon))
                except json.JSONDecodeError:
                    pass

            if not coords:
                gp = (row.get("geo_point_2d") or "").strip()
                if "," in gp:
                    parts = gp.split(",")
                    lat = _parse_float(parts[0].strip())
                    lon = _parse_float(parts[1].strip())
                    if lat is not None and lon is not None and -90 <= lat <= 90:
                        coords.append((lat, lon))

            for idx, (lat, lon) in enumerate(coords):
                points.append(
                    {
                        "site_id": site_id,
                        "component_idx": idx,
                        "site_name": name,
                        "category": category,
                        "date_inscribed": date_inscribed,
                        "iso_codes": iso_codes,
                        "lat": lat,
                        "lon": lon,
                    }
                )
    return points


def ensure_csv(cache_dir: Path = CACHE_DIR) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "whc001.csv"
    if path.exists() and path.stat().st_size > 10_000:
        return path
    print(f"downloading {UNESCO_CSV_URL}", flush=True)
    with requests.get(UNESCO_CSV_URL, timeout=180, stream=True) as resp:
        resp.raise_for_status()
        with path.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
    return path


def load(database_url: str, cache_dir: Path = CACHE_DIR) -> dict[str, Any]:
    csv_path = ensure_csv(cache_dir)
    points = parse_unesco_csv(csv_path)
    unique_sites = len({p["site_id"] for p in points})

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("TRUNCATE TABLE unesco_heritage_points")
            cur.executemany(
                """
                INSERT INTO unesco_heritage_points (
                    site_id, component_idx, site_name, category,
                    date_inscribed, iso_codes, lat, lon, geom
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                )
                """,
                [
                    (
                        p["site_id"],
                        p["component_idx"],
                        p["site_name"],
                        p["category"],
                        p["date_inscribed"],
                        p["iso_codes"],
                        p["lat"],
                        p["lon"],
                        p["lon"],
                        p["lat"],
                    )
                    for p in points
                ],
            )
            cur.execute("TRUNCATE TABLE urban_centre_heritage")
            cur.execute(
                """
                WITH hits AS (
                    SELECT DISTINCT
                           uc.urban_centre_id,
                           p.site_id,
                           p.site_name,
                           p.category
                    FROM ghsl_urban_centres uc
                    JOIN unesco_heritage_points p
                      ON uc.geom && ST_Expand(p.geom, 0.05)
                     AND ST_DWithin(uc.geom::geography, p.geom::geography, %s)
                ),
                agg AS (
                    SELECT uc.urban_centre_id,
                           count(h.site_id) FILTER (WHERE h.category = 'Cultural')::int AS cultural_cnt,
                           count(h.site_id) FILTER (WHERE h.category = 'Mixed')::int AS mixed_cnt,
                           count(h.site_id)::int AS total_cnt,
                           string_agg(h.site_name, ' | ' ORDER BY h.site_name) AS site_names
                    FROM ghsl_urban_centres uc
                    LEFT JOIN hits h USING (urban_centre_id)
                    GROUP BY uc.urban_centre_id
                )
                INSERT INTO urban_centre_heritage (
                    urban_centre_id, cultural_site_count, mixed_site_count,
                    total_heritage_sites, heritage_score_0_100, site_names,
                    source, source_url, observed_period
                )
                SELECT urban_centre_id,
                       cultural_cnt,
                       mixed_cnt,
                       total_cnt,
                       CASE
                           WHEN total_cnt <= 0 THEN 40.0
                           WHEN total_cnt = 1 THEN 70.0
                           WHEN total_cnt = 2 THEN 85.0
                           ELSE 100.0
                       END,
                       site_names,
                       %s, %s, %s
                FROM agg
                """,
                (EDGE_BUFFER_M, SOURCE, SOURCE_URL, OBSERVED_PERIOD),
            )
            cur.execute(
                """
                SELECT count(*) AS centres,
                       count(*) FILTER (WHERE total_heritage_sites > 0) AS centres_with_sites,
                       count(*) FILTER (WHERE total_heritage_sites >= 3) AS centres_3plus,
                       max(total_heritage_sites) AS max_sites
                FROM urban_centre_heritage
                """
            )
            stats = dict(cur.fetchone())
        conn.commit()
    stats["unesco_unique_sites"] = unique_sites
    stats["unesco_component_points"] = len(points)
    return stats


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT uc.name, h.total_heritage_sites, h.heritage_score_0_100, h.site_names
                FROM urban_centre_heritage h
                JOIN ghsl_urban_centres uc USING (urban_centre_id)
                WHERE h.total_heritage_sites > 0
                ORDER BY h.total_heritage_sites DESC, uc.population DESC
                LIMIT 20
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, cache_dir=args.cache_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
