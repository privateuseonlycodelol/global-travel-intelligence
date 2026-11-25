#!/usr/bin/env python3
"""
GHSL UCDB hazard and health themes, joined by urban-centre ID.

Two small, global, open tables from the same GHS-UCDB R2024A release already
used for metro boundaries:

* **Hazard**: MYRIAD-HES multi-hazard event counts overlaid on each urban
  centre, 2015 window (2014-2016), plus counts by hazard type. The source is
  CC BY 4.0. Conflict-event columns in the same file are deliberately not
  loaded because their ACLED/UCDP provenance has separate redistribution
  terms.
* **Health**: healthsites.io/OSM counts and the share of urban-centre
  population within 1 km of a hospital or pharmacy. This is access, not
  quality, and it inherits OSM coverage gaps.

Both are urban-centre measures, not city-proper measures. A city without a
GHSL metro assignment has no row.

Source: GHS-UCDB R2024A V1.2, European Commission JRC.
https://human-settlement.emergency.copernicus.eu/

Usage
-----
    python bootstrap_ucdb_themes.py
    python bootstrap_ucdb_themes.py --report
"""

from __future__ import annotations

import argparse
import csv
import os
import zipfile
from pathlib import Path
from typing import Any

import psycopg
import requests
from psycopg.rows import dict_row

CACHE = Path("./data_cache/ucdb")
BASE = (
    "https://cidportal.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
    "GHS_UCDB_GLOBE_R2024A/GHS_UCDB_THEME_GLOBE_R2024A"
)
THEMES = {
    "HAZARD_RISK": {
        "url": f"{BASE}/GHS_UCDB_THEME_HAZARD_RISK_GLOBE_R2024A/V1-2/"
               "GHS_UCDB_THEME_HAZARD_RISK_GLOBE_R2024A_V1_2.zip",
        "csv": "GHS_UCDB_THEME_HAZARD_RISK_GLOBE_R2024A.csv",
    },
    "HEALTH": {
        "url": f"{BASE}/GHS_UCDB_THEME_HEALTH_GLOBE_R2024A/V1-2/"
               "GHS_UCDB_THEME_HEALTH_GLOBE_R2024A_V1_2.zip",
        "csv": "GHS_UCDB_THEME_HEALTH_GLOBE_R2024A.csv",
    },
}
HAZARD_SOURCE = "GHS-UCDB R2024A hazard theme (MYRIAD-HES, CC BY 4.0)"
HEALTH_SOURCE = "GHS-UCDB R2024A health theme (healthsites.io/OSM)"
SOURCE_URL = "https://human-settlement.emergency.copernicus.eu/"

HAZARD_TYPES = {
    "extreme_wind": "HZ_CEV_EWI_2015",
    "heatwave": "HZ_CEV_HEW_2015",
    "drought": "HZ_CEV_DRO_2015",
    "cold_wave": "HZ_CEV_COW_2015",
    "flood": "HZ_CEV_FLO_2015",
    "tsunami": "HZ_CEV_TSU_2015",
    "tropical_cyclone": "HZ_CEV_TCY_2015",
    "wildfire": "HZ_CEV_WLF_2015",
    "landslide": "HZ_CEV_LAN_2015",
    "earthquake": "HZ_CEV_EAR_2015",
    "volcanic": "HZ_CEV_VOL_2015",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS urban_centre_hazard (
    urban_centre_id            BIGINT PRIMARY KEY,
    events_2015                INT,
    hazards_2015               INT,
    max_hazards_per_event_2015 INT,
    extreme_wind_2015          INT,
    heatwave_2015              INT,
    drought_2015               INT,
    cold_wave_2015             INT,
    flood_2015                 INT,
    tsunami_2015               INT,
    tropical_cyclone_2015      INT,
    wildfire_2015              INT,
    landslide_2015             INT,
    earthquake_2015            INT,
    volcanic_2015              INT,
    source                     TEXT NOT NULL,
    source_url                 TEXT NOT NULL,
    observed_period            TEXT NOT NULL,
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS urban_centre_health (
    urban_centre_id              BIGINT PRIMARY KEY,
    hospitals_count              INT,
    pharmacies_count             INT,
    hospital_density_per_km2     DOUBLE PRECISION,
    pharmacy_density_per_km2     DOUBLE PRECISION,
    hospitals_per_million        DOUBLE PRECISION,
    pharmacies_per_million       DOUBLE PRECISION,
    population_with_hospital_1km DOUBLE PRECISION,
    population_with_pharmacy_1km DOUBLE PRECISION,
    hospital_access_share_pct    DOUBLE PRECISION,
    pharmacy_access_share_pct    DOUBLE PRECISION,
    source                       TEXT NOT NULL,
    source_url                   TEXT NOT NULL,
    observed_period              TEXT NOT NULL,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def log(message: str) -> None:
    print(message, flush=True)


def ensure_csv(theme: str) -> Path:
    info = THEMES[theme]
    folder = CACHE / theme
    path = folder / info["csv"]
    if path.exists():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    log(f"downloading {info['url']}")
    response = requests.get(info["url"], timeout=300)
    response.raise_for_status()
    archive_path = folder / f"{theme}.zip"
    archive_path.write_bytes(response.content)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(folder)
    return path


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="latin-1", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        header[0] = header[0].replace("ï»¿", "").replace("\ufeff", "")
        index = {name: position for position, name in enumerate(header)}
        rows = []
        for values in reader:
            if not values:
                continue
            rows.append({
                name: values[position] if position < len(values) else ""
                for name, position in index.items()
            })
        return rows


def number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if text in ("", "-", "NA", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def integer(value: str | None) -> int | None:
    parsed = number(value)
    return int(round(parsed)) if parsed is not None else None


def load(database_url: str) -> dict[str, Any]:
    hazard_path = ensure_csv("HAZARD_RISK")
    health_path = ensure_csv("HEALTH")
    hazards = read_rows(hazard_path)
    health = read_rows(health_path)
    log(f"read {len(hazards):,} hazard rows and {len(health):,} health rows")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("DELETE FROM urban_centre_hazard")
            for row in hazards:
                centre = integer(row.get("ID_UC_G0"))
                if centre is None:
                    continue
                values = {
                    key: integer(row.get(column)) or 0
                    for key, column in HAZARD_TYPES.items()
                }
                cur.execute(
                    """
                    INSERT INTO urban_centre_hazard (
                        urban_centre_id, events_2015, hazards_2015,
                        max_hazards_per_event_2015, extreme_wind_2015,
                        heatwave_2015, drought_2015, cold_wave_2015, flood_2015,
                        tsunami_2015, tropical_cyclone_2015, wildfire_2015,
                        landslide_2015, earthquake_2015, volcanic_2015,
                        source, source_url, observed_period
                    ) VALUES (
                        %(id)s, %(events)s, %(hazards)s, %(max)s,
                        %(extreme_wind)s, %(heatwave)s, %(drought)s, %(cold_wave)s,
                        %(flood)s, %(tsunami)s, %(tropical_cyclone)s,
                        %(wildfire)s, %(landslide)s, %(earthquake)s, %(volcanic)s,
                        %(source)s, %(url)s, %(period)s
                    )
                    """,
                    {
                        "id": centre,
                        "events": integer(row.get("HZ_CEV_TEV_2015")) or 0,
                        "hazards": integer(row.get("HZ_CEV_THZ_2015")) or 0,
                        "max": integer(row.get("HZ_CEV_MHZ_2015")),
                        **values,
                        "source": HAZARD_SOURCE,
                        "url": SOURCE_URL,
                        "period": "2015 (2014-2016 window)",
                    },
                )
            cur.execute("DELETE FROM urban_centre_health")
            for row in health:
                centre = integer(row.get("ID_UC_G0"))
                if centre is None:
                    continue
                hospital_per_million = number(row.get("HL_FPC_HOS_2025"))
                pharmacy_per_million = number(row.get("HL_FPC_PHA_2025"))
                cur.execute(
                    """
                    INSERT INTO urban_centre_health (
                        urban_centre_id, hospitals_count, pharmacies_count,
                        hospital_density_per_km2, pharmacy_density_per_km2,
                        hospitals_per_million, pharmacies_per_million,
                        population_with_hospital_1km, population_with_pharmacy_1km,
                        hospital_access_share_pct, pharmacy_access_share_pct,
                        source, source_url, observed_period
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        centre,
                        integer(row.get("HL_FCL_HOS_2024")),
                        integer(row.get("HL_FCL_PHA_2024")),
                        number(row.get("HL_FDE_HOS_2024")),
                        number(row.get("HL_FDE_PHA_2024")),
                        hospital_per_million * 1_000_000 if hospital_per_million is not None else None,
                        pharmacy_per_million * 1_000_000 if pharmacy_per_million is not None else None,
                        number(row.get("HL_POP_HOS_2025")),
                        number(row.get("HL_POP_PHA_2025")),
                        number(row.get("HL_SHP_HOS_2025")),
                        number(row.get("HL_SHP_PHA_2025")),
                        HEALTH_SOURCE, SOURCE_URL, "2024-2025",
                    ),
                )
        conn.commit()
    return {"hazard_rows": len(hazards), "health_rows": len(health)}


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) rows FROM urban_centre_hazard")
            print({"hazard": cur.fetchone()["rows"]})
            cur.execute("SELECT count(*) rows FROM urban_centre_health")
            print({"health": cur.fetchone()["rows"]})
            cur.execute(
                """
                SELECT g.name, h.events_2015, h.heatwave_2015, h.flood_2015,
                       h.earthquake_2015, h.tropical_cyclone_2015
                FROM urban_centre_hazard h
                JOIN ghsl_urban_centres g USING (urban_centre_id)
                WHERE g.name IN ('Lisbon','Trento','Tokyo','Manila','Istanbul')
                ORDER BY g.name
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
