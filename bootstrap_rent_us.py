#!/usr/bin/env python3
"""
US Fair Market Rents from HUD's public ArcGIS service.

HUD publishes FMR polygons for metro areas and non-metro counties, with
0-4 bedroom rents. This loader:

* downloads the polygons (no API key) into ``hud_fmr_areas``;
* assigns each US destination to the containing FMR area, preferring a metro
  area over a county polygon and the smallest polygon on a tie;
* writes ``STUDIO``/``BED1``-``BED4`` monthly rents in USD to ``rent_monthly``.

An FMR is a 40th-percentile gross rent including essential utilities, used for
housing-voucher payment standards. It is a program benchmark, not an asking
rent and not a specific flat. It is profile context, not a rank input.

Source: HUD Fair Market Rents, US government public domain.
https://www.huduser.gov/portal/datasets/fmr.html

Usage
-----
    python bootstrap_rent_us.py
    python bootstrap_rent_us.py --report
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg
import requests
from psycopg.rows import dict_row

SERVICE = (
    "https://services.arcgis.com/VTyQ9soqVukalItT/ArcGIS/rest/services/"
    "Fair_Market_Rents/FeatureServer/0"
)
SOURCE = "HUD Fair Market Rents (FY2026)"
SOURCE_URL = "https://www.huduser.gov/portal/datasets/fmr.html"
BASIS = (
    "HUD Fair Market Rent, 40th percentile gross rent including essential "
    "utilities; FY2026; USD"
)
CACHE = Path("./data_cache/hud")
CACHE_FILE = CACHE / "fmr_areas.geojson"
CURRENCY = "USD"
YEAR = 2026
PERIOD = "FY2026"

TYPES = (
    ("STUDIO", 0, "fmr_0bdr"),
    ("BED1", 1, "fmr_1bdr"),
    ("BED2", 2, "fmr_2bdr"),
    ("BED3", 3, "fmr_3bdr"),
    ("BED4", 4, "fmr_4bdr"),
)

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS hud_fmr_areas (
    fmr_code    TEXT PRIMARY KEY,
    fmr_areaname TEXT NOT NULL,
    fmr_0bdr    INT,
    fmr_1bdr    INT,
    fmr_2bdr    INT,
    fmr_3bdr    INT,
    fmr_4bdr    INT,
    geom        geometry(Geometry, 4326)
);
CREATE INDEX IF NOT EXISTS idx_hud_fmr_areas_geom
    ON hud_fmr_areas USING GIST (geom);

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


def fetch_areas(session: requests.Session, refresh: bool = False) -> list[dict[str, Any]]:
    if CACHE_FILE.exists() and not refresh:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        features = payload.get("features", [])
        return [
            {
                "code": feature["properties"].get("FMR_CODE"),
                "name": feature["properties"].get("FMR_AREANAME"),
                "rents": {
                    key: feature["properties"].get(column.upper())
                    for key, _bedrooms, column in TYPES
                },
                "geometry": feature["geometry"],
            }
            for feature in features
            if feature.get("geometry")
        ]
    fields = (
        "FMR_CODE,FMR_AREANAME,FMR_0BDR,FMR_1BDR,FMR_2BDR,FMR_3BDR,FMR_4BDR"
    )
    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = session.get(
            f"{SERVICE}/query",
            params={
                "where": "1=1",
                "outFields": fields,
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "geojson",
                "resultOffset": offset,
                "resultRecordCount": 250,
                "orderByFields": "OBJECTID",
            },
            timeout=180,
        )
        response.raise_for_status()
        page = response.json().get("features", [])
        if not page:
            break
        features.extend(page)
        log(f"  fetched {len(features):,} FMR areas")
        if len(page) < 250:
            break
        offset += 250
    CACHE.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )
    return [
        {
            "code": feature["properties"].get("FMR_CODE"),
            "name": feature["properties"].get("FMR_AREANAME"),
            "rents": {
                key: feature["properties"].get(column.upper())
                for key, _bedrooms, column in TYPES
            },
            "geometry": feature["geometry"],
        }
        for feature in features
        if feature.get("geometry")
    ]


def load(database_url: str, refresh: bool = False) -> dict[str, Any]:
    session = requests.Session()
    session.headers["User-Agent"] = "travel-db-hud-fmr-loader/1.0"
    areas = fetch_areas(session, refresh=refresh)
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
            cur.execute("DELETE FROM hud_fmr_areas")
            for area in areas:
                cur.execute(
                    """
                    INSERT INTO hud_fmr_areas (
                        fmr_code, fmr_areaname, fmr_0bdr, fmr_1bdr, fmr_2bdr,
                        fmr_3bdr, fmr_4bdr, geom
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s,
                              ST_MakeValid(ST_GeomFromGeoJSON(%s)))
                    ON CONFLICT (fmr_code) DO UPDATE SET
                        fmr_areaname = EXCLUDED.fmr_areaname,
                        fmr_0bdr = EXCLUDED.fmr_0bdr,
                        fmr_1bdr = EXCLUDED.fmr_1bdr,
                        fmr_2bdr = EXCLUDED.fmr_2bdr,
                        fmr_3bdr = EXCLUDED.fmr_3bdr,
                        fmr_4bdr = EXCLUDED.fmr_4bdr,
                        geom = EXCLUDED.geom
                    """,
                    (
                        area["code"], area["name"],
                        area["rents"].get("STUDIO"), area["rents"].get("BED1"),
                        area["rents"].get("BED2"), area["rents"].get("BED3"),
                        area["rents"].get("BED4"), json.dumps(area["geometry"]),
                    ),
                )
            cur.execute(
                """
                CREATE TEMP TABLE matched_fmr ON COMMIT DROP AS
                SELECT DISTINCT ON (d.destination_id)
                       d.destination_id, f.fmr_code, f.fmr_areaname,
                       f.fmr_0bdr, f.fmr_1bdr, f.fmr_2bdr, f.fmr_3bdr, f.fmr_4bdr
                FROM destinations d
                JOIN hud_fmr_areas f ON ST_Within(d.centroid::geometry, f.geom)
                WHERE d.country_iso2 = 'US'
                ORDER BY d.destination_id,
                         (f.fmr_areaname ILIKE '%county%') ASC,
                         ST_Area(f.geom) ASC
                """
            )
            cur.execute("SELECT count(*) AS n FROM matched_fmr")
            matched = cur.fetchone()["n"]
            cur.execute("DELETE FROM rent_monthly WHERE source = %s", (SOURCE,))
            cur.execute(
                """
                SELECT destination_id, fmr_code, fmr_areaname,
                       fmr_0bdr, fmr_1bdr, fmr_2bdr, fmr_3bdr, fmr_4bdr
                FROM matched_fmr
                """
            )
            for row in cur.fetchall():
                for key, bedrooms, column in TYPES:
                    value = row.get(column)
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
                            row["destination_id"], key, bedrooms, float(value),
                            CURRENCY, BASIS, row["fmr_areaname"], row["fmr_code"],
                            YEAR, SOURCE, SOURCE_URL, PERIOD,
                        ),
                    )
                    written += 1
        conn.commit()
    return {
        "areas": len(areas),
        "matched_cities": matched,
        "rent_rows": written,
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
            cur.execute(
                """
                SELECT d.name, r.rent_local, r.area_name
                FROM rent_monthly r JOIN destinations d USING (destination_id)
                WHERE d.name IN ('New York City','Los Angeles','Mesa','Austin','Chicago')
                  AND r.dwelling_type = 'BED2'
                ORDER BY d.name
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, refresh=args.refresh))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
