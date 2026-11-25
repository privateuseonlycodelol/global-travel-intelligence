#!/usr/bin/env python3
"""
GHSL UCDB R2024A greenness and climate themes, joined by urban-centre ID.

Two global, open tables from the same GHS-UCDB R2024A V1.2 release already
used for metro boundaries and the hazard/health themes:

* **Greenness**: mean NDVI of the built-up area, 1985-2025, plus mean top tree
  canopy height, 2020. The source is Landsat and GHS-BUILT-S R2023A. The
  ``GR_SQM_*`` and ``GR_SHB_*`` columns in the same file are deliberately not
  loaded: their units are internally inconsistent (roughly 200x off), so they
  cannot be compared across cities.
* **Climate**: bioclimatic indicators (BIO1 mean annual temperature, BIO12
  annual precipitation), UTCI days above 32 C, and the Koppen-Geiger class.
  Historical reanalysis is the 2010 decade; UTCI is 2020; the Koppen class is
  current (2025).

Both are urban-centre measures, not city-proper measures. A city without a
GHSL metro assignment has no row. All 11,422 GHSL urban centres are loaded,
including centres where a metric is missing, so coverage is explicit rather
than silently dropped.

Source: GHS-UCDB R2024A V1.2, European Commission JRC, CC BY 4.0.
https://human-settlement.emergency.copernicus.eu/

Usage
-----
    python bootstrap_ucdb_greenness_climate.py
    python bootstrap_ucdb_greenness_climate.py --report
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
import requests
from psycopg.rows import dict_row

CACHE = Path("./data_cache/ucdb")
BASE = (
    "https://cidportal.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
    "GHS_UCDB_GLOBE_R2024A/GHS_UCDB_THEME_GLOBE_R2024A"
)
THEMES = {
    "GREENNESS": {
        "url": f"{BASE}/GHS_UCDB_THEME_GREENNESS_GLOBE_R2024A/V1-2/"
               "GHS_UCDB_THEME_GREENNESS_GLOBE_R2024A_V1_2.zip",
        "csv": "GHS_UCDB_THEME_GREENNESS_GLOBE_R2024A.csv",
    },
    "CLIMATE": {
        "url": f"{BASE}/GHS_UCDB_THEME_CLIMATE_GLOBE_R2024A/V1-2/"
               "GHS_UCDB_THEME_CLIMATE_GLOBE_R2024A_V1_2.zip",
        "csv": "GHS_UCDB_THEME_CLIMATE_GLOBE_R2024A.csv",
    },
}
SOURCE_URL = "https://human-settlement.emergency.copernicus.eu/"
GREENNESS_SOURCE = (
    "GHS-UCDB R2024A greenness theme "
    "(Landsat NDVI in built-up area; GHS-BUILT-S canopy height, CC BY 4.0)"
)
CLIMATE_SOURCE = (
    "GHS-UCDB R2024A climate theme "
    "(Wouters 2021 reanalysis bioclimatics; UTCI; Beck et al. 2018 "
    "Koppen-Geiger, CC BY 4.0)"
)

# Header detection is prefix based so a later GHSL revision can rename the
# year suffix without changing the loader.  NDV is preferred over GRN when
# both spellings exist for the same year.
NDVI_PREFIXES = ("GR_AVG_NDV_", "GR_AVG_GRN_")
CANOPY_PREFIXES = ("GR_CTH_AVG_",)
TEMPERATURE_PREFIXES = ("CL_B01_CUR_",)
PRECIPITATION_PREFIXES = ("CL_B12_CUR_",)
UTCI_PREFIXES = ("CL_UTC_T32_",)
KOPPEN_PREFIXES = ("CL_KOP_CUR_",)

# NDVI is mapped to 0-100 with a floor at 0.05 and a ceiling at 0.60.  Those
# bounds keep the mildly negative built-up NDVI values from forming a false
# "zero greenery" cluster while still spanning the observed city range.
NDVI_FLOOR = 0.05
NDVI_SPAN = 0.55

KOPPEN_LEGEND = {
    1: ("Af", "Tropical, rainforest"),
    2: ("Am", "Tropical, monsoon"),
    3: ("Aw", "Tropical, savannah"),
    4: ("BWh", "Arid, desert, hot"),
    5: ("BWk", "Arid, desert, cold"),
    6: ("BSh", "Arid, steppe, hot"),
    7: ("BSk", "Arid, steppe, cold"),
    8: ("Csa", "Temperate, dry summer, hot summer"),
    9: ("Csb", "Temperate, dry summer, warm summer"),
    10: ("Csc", "Temperate, dry summer, cold summer"),
    11: ("Cwa", "Temperate, dry winter, hot summer"),
    12: ("Cwb", "Temperate, dry winter, warm summer"),
    13: ("Cwc", "Temperate, dry winter, cold summer"),
    14: ("Cfa", "Temperate, no dry season, hot summer"),
    15: ("Cfb", "Temperate, no dry season, warm summer"),
    16: ("Cfc", "Temperate, no dry season, cold summer"),
    17: ("Dsa", "Cold, dry summer, hot summer"),
    18: ("Dsb", "Cold, dry summer, warm summer"),
    19: ("Dsc", "Cold, dry summer, cold summer"),
    20: ("Dsd", "Cold, dry summer, very cold winter"),
    21: ("Dwa", "Cold, dry winter, hot summer"),
    22: ("Dwb", "Cold, dry winter, warm summer"),
    23: ("Dwc", "Cold, dry winter, cold summer"),
    24: ("Dwd", "Cold, dry winter, very cold winter"),
    25: ("Dfa", "Cold, no dry season, hot summer"),
    26: ("Dfb", "Cold, no dry season, warm summer"),
    27: ("Dfc", "Cold, no dry season, cold summer"),
    28: ("Dfd", "Cold, no dry season, very cold winter"),
    29: ("ET", "Polar, tundra"),
    30: ("EF", "Polar, frost"),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS urban_centre_greenness (
    urban_centre_id  BIGINT PRIMARY KEY,
    ndvi             DOUBLE PRECISION,
    ndvi_score_0_100 DOUBLE PRECISION,
    canopy_height_m  DOUBLE PRECISION,
    source           TEXT NOT NULL,
    source_url       TEXT NOT NULL,
    observed_period  TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS urban_centre_greenness_score_idx
    ON urban_centre_greenness (ndvi_score_0_100);

CREATE TABLE IF NOT EXISTS urban_centre_climate (
    urban_centre_id    BIGINT PRIMARY KEY,
    mean_annual_temp_c DOUBLE PRECISION,
    annual_precip_mm   DOUBLE PRECISION,
    utci_over_32_days  DOUBLE PRECISION,
    koppen_code        TEXT,
    koppen_label       TEXT,
    source             TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    observed_period    TEXT NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS urban_centre_climate_koppen_idx
    ON urban_centre_climate (koppen_code);
"""

GREENNESS_INSERT_SQL = """
INSERT INTO urban_centre_greenness (
    urban_centre_id, ndvi, ndvi_score_0_100, canopy_height_m,
    source, source_url, observed_period
) VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

CLIMATE_INSERT_SQL = """
INSERT INTO urban_centre_climate (
    urban_centre_id, mean_annual_temp_c, annual_precip_mm,
    utci_over_32_days, koppen_code, koppen_label,
    source, source_url, observed_period
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

MISSING_TOKENS = frozenset(
    {"", "-", "--", "na", "n/a", "n.a.", "null", "none", "nan", "#n/a"}
)


def log(message: str) -> None:
    print(message, flush=True)


def ensure_csv(theme: str, cache_dir: Path | str = CACHE) -> Path:
    info = THEMES[theme]
    folder = Path(cache_dir) / theme
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
    if not path.exists():
        matches = list(folder.rglob(info["csv"]))
        if not matches:
            raise FileNotFoundError(f"{info['csv']} not found in {folder}")
        path = matches[0]
    return path


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="latin-1", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if header:
            header[0] = header[0].replace("ï»¿", "").replace("\ufeff", "")
        index = {
            name.strip(): position for position, name in enumerate(header)
        }
        rows = []
        for values in reader:
            if not values or not any(value.strip() for value in values):
                continue
            rows.append({
                name: values[position] if position < len(values) else ""
                for name, position in index.items()
            })
        return rows


def number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
    else:
        text = str(value).strip()
        if text.lower() in MISSING_TOKENS:
            return None
        try:
            parsed = float(text)
        except ValueError:
            return None
    if not math.isfinite(parsed):
        return None
    return parsed


def integer(value: Any) -> int | None:
    parsed = number(value)
    return int(round(parsed)) if parsed is not None else None


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def ndvi_score_0_100(ndvi: float | None) -> float | None:
    """Map built-up mean NDVI to a bounded 0-100 score."""
    if ndvi is None:
        return None
    return round(100.0 * clamp((ndvi - NDVI_FLOOR) / NDVI_SPAN), 2)


def latest_column(
    columns: Iterable[str], prefixes: Sequence[str]
) -> tuple[str | None, int | None]:
    """Return the newest column matching a prefix and its year suffix.

    Columns whose suffix is not a four-digit year, such as the ``XXXX``
    placeholder used in GHSL metadata, are ignored.
    """
    best: tuple[int, int, str] | None = None
    for column in columns:
        for rank, prefix in enumerate(prefixes):
            if not column.startswith(prefix):
                continue
            suffix = column[len(prefix):]
            if not suffix.isdigit():
                continue
            candidate = (int(suffix), -rank, column)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
            break
    if best is None:
        return None, None
    return best[2], best[0]


def dedupe_by_id(
    records: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Collapse duplicate urban-centre rows, keeping the last occurrence."""
    by_id: dict[int, dict[str, Any]] = {}
    duplicates = 0
    for record in records:
        key = int(record["urban_centre_id"])
        if key in by_id:
            duplicates += 1
        by_id[key] = record
    return list(by_id.values()), duplicates


def koppen_class(value: Any) -> tuple[str | None, str | None]:
    parsed = integer(value)
    if parsed is None:
        return None, None
    return KOPPEN_LEGEND.get(parsed, (None, None))


def _period(parts: Sequence[tuple[str, int | None]]) -> str:
    rendered = [f"{label} {year}" for label, year in parts if year is not None]
    return "; ".join(rendered) if rendered else "latest available"


def greenness_period(ndvi_year: int | None, canopy_year: int | None) -> str:
    return _period([("NDVI", ndvi_year), ("canopy height", canopy_year)])


def climate_period(
    temperature_year: int | None,
    precipitation_year: int | None,
    utci_year: int | None,
    koppen_year: int | None,
) -> str:
    parts: list[tuple[str, int | None]] = []
    if temperature_year is not None and temperature_year == precipitation_year:
        parts.append(("BIO1/BIO12", temperature_year))
    else:
        parts.append(("BIO1", temperature_year))
        parts.append(("BIO12", precipitation_year))
    parts.append(("UTCI", utci_year))
    parts.append(("Koppen-Geiger", koppen_year))
    return _period(parts)


def parse_greenness(
    rows: Sequence[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not rows:
        return [], {
            "ndvi_column": None,
            "ndvi_year": None,
            "canopy_column": None,
            "canopy_year": None,
            "duplicates": 0,
        }
    columns = list(rows[0])
    ndvi_column, ndvi_year = latest_column(columns, NDVI_PREFIXES)
    canopy_column, canopy_year = latest_column(columns, CANOPY_PREFIXES)
    period = greenness_period(ndvi_year, canopy_year)
    records = []
    for row in rows:
        centre = integer(row.get("ID_UC_G0"))
        if centre is None:
            continue
        ndvi = number(row.get(ndvi_column)) if ndvi_column else None
        canopy = number(row.get(canopy_column)) if canopy_column else None
        records.append({
            "urban_centre_id": centre,
            "ndvi": ndvi,
            "ndvi_score_0_100": ndvi_score_0_100(ndvi),
            "canopy_height_m": canopy,
            "source": GREENNESS_SOURCE,
            "source_url": SOURCE_URL,
            "observed_period": period,
        })
    records, duplicates = dedupe_by_id(records)
    return records, {
        "ndvi_column": ndvi_column,
        "ndvi_year": ndvi_year,
        "canopy_column": canopy_column,
        "canopy_year": canopy_year,
        "duplicates": duplicates,
    }


def parse_climate(
    rows: Sequence[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not rows:
        return [], {
            "temperature_column": None,
            "temperature_year": None,
            "precipitation_column": None,
            "precipitation_year": None,
            "utci_column": None,
            "utci_year": None,
            "koppen_column": None,
            "koppen_year": None,
            "duplicates": 0,
        }
    columns = list(rows[0])
    temperature_column, temperature_year = latest_column(
        columns, TEMPERATURE_PREFIXES
    )
    precipitation_column, precipitation_year = latest_column(
        columns, PRECIPITATION_PREFIXES
    )
    utci_column, utci_year = latest_column(columns, UTCI_PREFIXES)
    koppen_column, koppen_year = latest_column(columns, KOPPEN_PREFIXES)
    period = climate_period(
        temperature_year, precipitation_year, utci_year, koppen_year
    )
    records = []
    for row in rows:
        centre = integer(row.get("ID_UC_G0"))
        if centre is None:
            continue
        koppen_code, koppen_label = (
            koppen_class(row.get(koppen_column)) if koppen_column else (None, None)
        )
        records.append({
            "urban_centre_id": centre,
            "mean_annual_temp_c": (
                number(row.get(temperature_column))
                if temperature_column else None
            ),
            "annual_precip_mm": (
                number(row.get(precipitation_column))
                if precipitation_column else None
            ),
            "utci_over_32_days": (
                number(row.get(utci_column)) if utci_column else None
            ),
            "koppen_code": koppen_code,
            "koppen_label": koppen_label,
            "source": CLIMATE_SOURCE,
            "source_url": SOURCE_URL,
            "observed_period": period,
        })
    records, duplicates = dedupe_by_id(records)
    return records, {
        "temperature_column": temperature_column,
        "temperature_year": temperature_year,
        "precipitation_column": precipitation_column,
        "precipitation_year": precipitation_year,
        "utci_column": utci_column,
        "utci_year": utci_year,
        "koppen_column": koppen_column,
        "koppen_year": koppen_year,
        "duplicates": duplicates,
    }


def load(
    database_url: str, cache_dir: Path | str = CACHE
) -> dict[str, Any]:
    greenness_path = ensure_csv("GREENNESS", cache_dir)
    climate_path = ensure_csv("CLIMATE", cache_dir)
    greenness_rows, greenness_meta = parse_greenness(read_rows(greenness_path))
    climate, climate_meta = parse_climate(read_rows(climate_path))
    log(
        f"parsed {len(greenness_rows):,} greenness rows and "
        f"{len(climate):,} climate rows"
    )
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("DELETE FROM urban_centre_greenness")
            cur.executemany(
                GREENNESS_INSERT_SQL,
                [
                    (
                        row["urban_centre_id"],
                        row["ndvi"],
                        row["ndvi_score_0_100"],
                        row["canopy_height_m"],
                        row["source"],
                        row["source_url"],
                        row["observed_period"],
                    )
                    for row in greenness_rows
                ],
            )
            cur.execute("DELETE FROM urban_centre_climate")
            cur.executemany(
                CLIMATE_INSERT_SQL,
                [
                    (
                        row["urban_centre_id"],
                        row["mean_annual_temp_c"],
                        row["annual_precip_mm"],
                        row["utci_over_32_days"],
                        row["koppen_code"],
                        row["koppen_label"],
                        row["source"],
                        row["source_url"],
                        row["observed_period"],
                    )
                    for row in climate
                ],
            )
        conn.commit()
    return {
        "greenness_rows": len(greenness_rows),
        "climate_rows": len(climate),
        "greenness_duplicates": greenness_meta["duplicates"],
        "climate_duplicates": climate_meta["duplicates"],
        "greenness_columns": {
            "ndvi": greenness_meta["ndvi_column"],
            "canopy": greenness_meta["canopy_column"],
        },
        "climate_columns": {
            "temperature": climate_meta["temperature_column"],
            "precipitation": climate_meta["precipitation_column"],
            "utci": climate_meta["utci_column"],
            "koppen": climate_meta["koppen_column"],
        },
    }


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS rows,
                       count(ndvi) AS ndvi_rows,
                       count(ndvi_score_0_100) AS scored_rows,
                       count(canopy_height_m) AS canopy_rows,
                       round(avg(ndvi_score_0_100)::numeric, 1) AS avg_score
                FROM urban_centre_greenness
                """
            )
            print({"greenness": dict(cur.fetchone())})
            cur.execute(
                """
                SELECT count(*) AS rows,
                       count(mean_annual_temp_c) AS temperature_rows,
                       count(annual_precip_mm) AS precipitation_rows,
                       count(utci_over_32_days) AS utci_rows,
                       count(koppen_code) AS koppen_rows
                FROM urban_centre_climate
                """
            )
            print({"climate": dict(cur.fetchone())})
            cur.execute(
                """
                SELECT g.name, r.ndvi, r.ndvi_score_0_100, r.canopy_height_m
                FROM urban_centre_greenness r
                JOIN ghsl_urban_centres g USING (urban_centre_id)
                WHERE r.ndvi_score_0_100 IS NOT NULL
                ORDER BY r.ndvi_score_0_100 DESC
                LIMIT 5
                """
            )
            print({"greenest": [dict(row) for row in cur.fetchall()]})
            cur.execute(
                """
                SELECT g.name, c.mean_annual_temp_c, c.annual_precip_mm,
                       c.utci_over_32_days, c.koppen_code
                FROM urban_centre_climate c
                JOIN ghsl_urban_centres g USING (urban_centre_id)
                WHERE g.name IN ('Lisbon', 'Trento', 'Tokyo', 'Manila',
                                 'Istanbul', 'Nairobi')
                ORDER BY g.name
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--cache-dir", default=str(CACHE))
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, args.cache_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
