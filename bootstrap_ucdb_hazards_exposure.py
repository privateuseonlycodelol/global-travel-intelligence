#!/usr/bin/env python3
"""
GHSL UCDB R2024A EXPOSURE theme: non-heat natural-hazard exposure.

Loads three population-exposure dimensions from the GHS-UCDB R2024A V1.2
EXPOSURE theme (11,422 urban centres, CC BY 4.0) and translates each to a
fixed 0-100 safety score:

* **Flood (100-year)** -- river flood from the JRC GloFAS/CEMS modelled
  inundation (Baugh et al. 2024) and coastal flood from Vousdoukas et al.
  (2018) / Dottori et al. (2016). ``flood_exposure_raw`` is the larger of the
  two 2025 population shares, so a city is not treated as safe when only one
  flood source is absent. Storm surge is represented here through the
  coastal-flood share.
* **Earthquake** -- population-weighted mean Modified Mercalli Intensity
  (MMI), computed from the nine MMI class population columns (class 1,
  2-3, 4, 5, 6, 7, 8, 9, 10; the 2-3 bin uses its midpoint 2.5). The UCDB
  also publishes a share of population at MMI >= 6, but that summary is
  almost binary (0 or 100) for most centres; the mean intensity keeps the
  score graded instead of collapsing to an in/out-of-zone flag.
* **Cyclone wind** -- population exposed to Saffir-Simpson category 1-2
  winds with a 100-year return period (STORM, Bloemendaal et al. 2023),
  divided by the 2025 UCDB population.

Heatwave, drought, cold-wave, wildfire, volcanic and ACLED conflict columns
in the same UCDB release are deliberately excluded: heat is already carried
by ``thermal_safety_score`` and must never be double-counted, and the
conflict columns have separate redistribution terms.

Fixed absolute anchors (100 = no exposure, 0 = severe exposure)
----------------------------------------------------------------
* ``flood_safety_score``: 100 at 0% exposed, 0 at >= 50% exposed, linear
  between. The 50% breakpoint marks a majority-scale population exposure to
  a 1-in-100-year flood.
* ``cyclone_safety_score``: the same 0-50% population-share anchor.
* ``seismic_safety_score``: 100 at mean MMI <= 4 (light shaking), 80 at
  MMI 5 (moderate), 50 at MMI 6 (strong, slight damage), 20 at MMI 7 (very
  strong, damage), 0 at MMI >= 8 (severe, considerable damage), linear
  between.
* ``hazard_safety_score``: unweighted mean of the three scores, only when
  all three are present; no renormalisation or imputation. The Council
  assigns a single 6% pillar to non-heat natural hazards, so the three
  sources are not sub-weighted.

Data availability
-----------------
The four selected 2025 columns cover all 11,422 centres. Eight small-island
centres (Mindelo, Le Port, Port Louis, Male, Joao Teves, Le Tampon,
Saint-Denis, Saint-Andre) have no population in any MMI class column; their
seismic raw/score and therefore their hazard score stay NULL rather than
being imputed as zero shaking.

Flood return-period label note
------------------------------
The UCDB theme PDF swaps the 10-year/100-year labels in the ``EX_010_SHP``
indicator *name* (its description says 10-year) and in the ``EX_100_SHP``
indicator *name* (its description says 100-year). The attribute IDs and the
value ordering agree with the descriptions: ``EX_100_SHP`` is the 100-year
extent and is >= ``EX_010_SHP`` for 5,909 of 6,710 non-zero centres. This
loader therefore uses ``EX_100_SHP_2025`` and does not average the pair.

Source: GHS-UCDB R2024A V1.2, European Commission JRC, CC BY 4.0.
https://human-settlement.emergency.copernicus.eu/

Usage
-----
    python bootstrap_ucdb_hazards_exposure.py
    python bootstrap_ucdb_hazards_exposure.py --report
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

DEFAULT_CACHE_DIR = Path("./data_cache/ucdb")
THEME_FOLDER = "EXPOSURE"
CSV_NAME = "GHS_UCDB_THEME_EXPOSURE_GLOBE_R2024A.csv"
THEME_URL = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
    "GHS_UCDB_GLOBE_R2024A/GHS_UCDB_THEME_GLOBE_R2024A/"
    "GHS_UCDB_THEME_EXPOSURE_GLOBE_R2024A/V1-2/"
    "GHS_UCDB_THEME_EXPOSURE_GLOBE_R2024A_V1_2.zip"
)
SOURCE = (
    "GHS-UCDB R2024A V1.2 exposure theme "
    "(JRC GloFAS 100-yr river flood; Vousdoukas/Dottori 100-yr coastal flood; "
    "GEM MMI earthquake; STORM 100-yr cyclone wind; CC BY 4.0)"
)
SOURCE_URL = THEME_URL
OBSERVED_PERIOD = "2025"
TARGET_YEAR = 2025
ID_COLUMN = "ID_UC_G0"

# Exact UCDB attribute families. The year suffix is resolved at run time so a
# later V1-x revision can move the year without changing the loader logic.
FLOOD_RIVER_PREFIX = "EX_100_SHP_"
FLOOD_COASTAL_PREFIX = "EX_CF1_SHP_"
CYCLONE_POP_PREFIX = "EX_100_S1P_"
POPULATION_PREFIX = "GC_POP_TOT_"

# MMI class bins published by UCDB and the representative intensity used for
# the population-weighted mean. The 2-3 bin is represented by its midpoint.
MMI_CLASSES: tuple[tuple[str, float], ...] = (
    ("EX_E01_POP_", 1.0),
    ("EX_E02_POP_", 2.5),
    ("EX_E04_POP_", 4.0),
    ("EX_E05_POP_", 5.0),
    ("EX_E06_POP_", 6.0),
    ("EX_E07_POP_", 7.0),
    ("EX_E08_POP_", 8.0),
    ("EX_E09_POP_", 9.0),
    ("EX_E10_POP_", 10.0),
)

HAZARD_PREFIXES: tuple[str, ...] = (
    FLOOD_RIVER_PREFIX,
    FLOOD_COASTAL_PREFIX,
    CYCLONE_POP_PREFIX,
    POPULATION_PREFIX,
    *(prefix for prefix, _ in MMI_CLASSES),
)

# Exposure share at which the safety score reaches zero. The same breakpoint
# is used for flood and cyclone so the two population-share dimensions remain
# directly comparable.
SEVERE_EXPOSURE_SHARE_PCT = 50.0

# 100 at MMI <= 4, then 80 @5, 50 @6, 20 @7, 0 at >=8. The breakpoints follow
# the MMI damage descriptions (light / moderate / strong / very strong /
# severe) rather than the data distribution.
SEISMIC_ANCHORS: tuple[tuple[float, float], ...] = (
    (0.0, 100.0),
    (4.0, 100.0),
    (5.0, 80.0),
    (6.0, 50.0),
    (7.0, 20.0),
    (8.0, 0.0),
    (10.0, 0.0),
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS urban_centre_hazard_exposure (
    urban_centre_id      BIGINT PRIMARY KEY,
    flood_exposure_raw   DOUBLE PRECISION,
    seismic_exposure_raw DOUBLE PRECISION,
    cyclone_exposure_raw DOUBLE PRECISION,
    flood_safety_score   DOUBLE PRECISION,
    seismic_safety_score DOUBLE PRECISION,
    cyclone_safety_score DOUBLE PRECISION,
    hazard_safety_score  DOUBLE PRECISION,
    source               TEXT NOT NULL,
    source_url           TEXT NOT NULL,
    observed_period      TEXT NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS urban_centre_hazard_exposure_score_idx
    ON urban_centre_hazard_exposure (hazard_safety_score);
"""

INSERT_SQL = """
INSERT INTO urban_centre_hazard_exposure (
    urban_centre_id, flood_exposure_raw, seismic_exposure_raw,
    cyclone_exposure_raw, flood_safety_score, seismic_safety_score,
    cyclone_safety_score, hazard_safety_score, source, source_url,
    observed_period
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

MISSING_TOKENS = frozenset(
    {"", "-", "--", "na", "n/a", "n.a.", "null", "none", "nan", "#n/a"}
)


def log(message: str) -> None:
    print(message, flush=True)


def ensure_csv(cache_dir: Path | str = DEFAULT_CACHE_DIR) -> Path:
    """Return the cached theme CSV, downloading and extracting it if needed."""
    folder = Path(cache_dir) / THEME_FOLDER
    path = folder / CSV_NAME
    if path.exists():
        return path
    folder.mkdir(parents=True, exist_ok=True)
    archive_path = folder / f"{THEME_FOLDER}.zip"
    if not archive_path.exists():
        log(f"downloading {THEME_URL}")
        response = requests.get(THEME_URL, timeout=300)
        response.raise_for_status()
        archive_path.write_bytes(response.content)
    extract_archive(archive_path, folder)
    if not path.exists():
        matches = list(folder.rglob(CSV_NAME))
        if not matches:
            raise FileNotFoundError(f"{CSV_NAME} not found in {folder}")
        path = matches[0]
    return path


def extract_archive(archive_path: Path | str, folder: Path | str) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(folder)


def read_rows(
    path: Path | str,
    keep_prefixes: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """Read a UCDB CSV, optionally projecting to the hazard column families.

    The EXPOSURE CSV has 1,066 columns; keeping only ``ID_UC_G0`` and the
    prefixes this loader consumes avoids materialising roughly 12 million
    unused key/value pairs.
    """
    with Path(path).open(encoding="latin-1", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if header:
            header[0] = header[0].replace("ï»¿", "").replace("\ufeff", "")
        index = {
            name.strip(): position for position, name in enumerate(header)
        }
        if keep_prefixes is not None:
            prefixes = tuple(keep_prefixes)
            index = {
                name: position for name, position in index.items()
                if name == ID_COLUMN or name.startswith(prefixes)
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


def latest_column_at_most(
    columns: Iterable[str],
    prefix: str,
    max_year: int = TARGET_YEAR,
) -> tuple[str | None, int | None]:
    """Return the newest column matching ``prefix`` with a year <= max_year.

    The UCDB carries 1975-2030 for many series, including 2030 projections.
    Hazard scores use the latest observed/current epoch, 2025, and must not
    silently pick the 2030 projection.
    """
    best: tuple[str, int] | None = None
    for column in columns:
        if not column.startswith(prefix):
            continue
        suffix = column[len(prefix):]
        if not suffix.isdigit():
            continue
        year = int(suffix)
        if year > max_year:
            continue
        if best is None or year > best[1]:
            best = (column, year)
    if best is None:
        return None, None
    return best


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


def share_safety_score(raw: float | None) -> float | None:
    """Map a 0-100 population-share exposure to a bounded safety score.

    100 at zero exposure, 0 at ``SEVERE_EXPOSURE_SHARE_PCT`` or more, linear
    between. Values outside 0-100 are clamped rather than rejected.
    """
    if raw is None:
        return None
    share = max(0.0, min(100.0, float(raw)))
    score = 100.0 * (
        1.0 - min(share, SEVERE_EXPOSURE_SHARE_PCT) / SEVERE_EXPOSURE_SHARE_PCT
    )
    return round(score, 2)


def flood_safety_score(raw: float | None) -> float | None:
    return share_safety_score(raw)


def cyclone_safety_score(raw: float | None) -> float | None:
    return share_safety_score(raw)


def seismic_safety_score(mmi: float | None) -> float | None:
    """Map population-weighted mean MMI (0-10) to a bounded safety score."""
    if mmi is None:
        return None
    value = float(mmi)
    if value <= SEISMIC_ANCHORS[0][0]:
        return SEISMIC_ANCHORS[0][1]
    for (x0, y0), (x1, y1) in zip(SEISMIC_ANCHORS, SEISMIC_ANCHORS[1:]):
        if value <= x1:
            if x1 == x0:
                return y1
            return round(y0 + (y1 - y0) * (value - x0) / (x1 - x0), 2)
    return SEISMIC_ANCHORS[-1][1]


def hazard_safety_score(
    flood_score: float | None,
    seismic_score: float | None,
    cyclone_score: float | None,
) -> float | None:
    """Unweighted mean of the three hazard scores only when all are present."""
    if flood_score is None or seismic_score is None or cyclone_score is None:
        return None
    return round(
        (float(flood_score) + float(seismic_score) + float(cyclone_score))
        / 3.0,
        2,
    )


def parse_exposure(
    rows: Sequence[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse UCDB EXPOSURE rows into hazard-exposure records."""
    if not rows:
        return [], {
            "columns": {},
            "years": {},
            "duplicates": 0,
            "seismic_null": 0,
        }
    columns = list(rows[0])
    river_column, river_year = latest_column_at_most(
        columns, FLOOD_RIVER_PREFIX
    )
    coastal_column, coastal_year = latest_column_at_most(
        columns, FLOOD_COASTAL_PREFIX
    )
    cyclone_column, cyclone_year = latest_column_at_most(
        columns, CYCLONE_POP_PREFIX
    )
    population_column, population_year = latest_column_at_most(
        columns, POPULATION_PREFIX
    )
    mmi_columns: dict[float, str] = {}
    mmi_years: dict[float, int | None] = {}
    for prefix, midpoint in MMI_CLASSES:
        column, year = latest_column_at_most(columns, prefix)
        if column is not None:
            mmi_columns[midpoint] = column
        mmi_years[midpoint] = year

    required = {
        "flood_river": river_column,
        "flood_coastal": coastal_column,
        "cyclone_population": cyclone_column,
        "population": population_column,
    }
    missing = [name for name, column in required.items() if column is None]
    missing += [
        f"mmi_{midpoint:g}"
        for _, midpoint in MMI_CLASSES
        if midpoint not in mmi_columns
    ]
    if missing:
        raise ValueError(
            "missing required EXPOSURE columns: " + ", ".join(missing)
        )

    records: list[dict[str, Any]] = []
    for row in rows:
        centre = integer(row.get(ID_COLUMN))
        if centre is None:
            continue
        river = number(row.get(river_column))
        coastal = number(row.get(coastal_column))
        flood_values = [value for value in (river, coastal) if value is not None]
        flood_raw = max(flood_values) if flood_values else None

        population = number(row.get(population_column))
        cyclone_population = number(row.get(cyclone_column))
        cyclone_raw = None
        if (
            population is not None
            and population > 0
            and cyclone_population is not None
        ):
            cyclone_raw = round(
                min(
                    100.0,
                    max(0.0, 100.0 * cyclone_population / population),
                ),
                4,
            )

        mmi_total = 0.0
        mmi_weighted = 0.0
        for _, midpoint in MMI_CLASSES:
            column = mmi_columns[midpoint]
            value = number(row.get(column))
            if value is not None and value > 0:
                mmi_total += value
                mmi_weighted += value * midpoint
        seismic_raw = (
            round(mmi_weighted / mmi_total, 4) if mmi_total > 0 else None
        )

        flood_score = flood_safety_score(flood_raw)
        seismic_score = seismic_safety_score(seismic_raw)
        cyclone_score = cyclone_safety_score(cyclone_raw)
        records.append({
            "urban_centre_id": centre,
            "flood_exposure_raw": flood_raw,
            "seismic_exposure_raw": seismic_raw,
            "cyclone_exposure_raw": cyclone_raw,
            "flood_safety_score": flood_score,
            "seismic_safety_score": seismic_score,
            "cyclone_safety_score": cyclone_score,
            "hazard_safety_score": hazard_safety_score(
                flood_score, seismic_score, cyclone_score
            ),
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "observed_period": OBSERVED_PERIOD,
        })

    records, duplicates = dedupe_by_id(records)
    return records, {
        "columns": {
            "flood_river": river_column,
            "flood_coastal": coastal_column,
            "cyclone_population": cyclone_column,
            "population": population_column,
            "mmi": mmi_columns,
        },
        "years": {
            "flood_river": river_year,
            "flood_coastal": coastal_year,
            "cyclone_population": cyclone_year,
            "population": population_year,
            "mmi": mmi_years,
        },
        "duplicates": duplicates,
        "seismic_null": sum(
            1 for record in records
            if record["seismic_exposure_raw"] is None
        ),
    }


def load(
    database_url: str,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
) -> dict[str, Any]:
    path = ensure_csv(cache_dir)
    records, meta = parse_exposure(
        read_rows(path, keep_prefixes=HAZARD_PREFIXES)
    )
    log(f"parsed {len(records):,} urban-centre hazard-exposure rows")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("DELETE FROM urban_centre_hazard_exposure")
            cur.executemany(
                INSERT_SQL,
                [
                    (
                        record["urban_centre_id"],
                        record["flood_exposure_raw"],
                        record["seismic_exposure_raw"],
                        record["cyclone_exposure_raw"],
                        record["flood_safety_score"],
                        record["seismic_safety_score"],
                        record["cyclone_safety_score"],
                        record["hazard_safety_score"],
                        record["source"],
                        record["source_url"],
                        record["observed_period"],
                    )
                    for record in records
                ],
            )
        conn.commit()
    return {
        "rows": len(records),
        "flood_scored": sum(
            1 for record in records
            if record["flood_safety_score"] is not None
        ),
        "seismic_scored": sum(
            1 for record in records
            if record["seismic_safety_score"] is not None
        ),
        "cyclone_scored": sum(
            1 for record in records
            if record["cyclone_safety_score"] is not None
        ),
        "hazard_scored": sum(
            1 for record in records
            if record["hazard_safety_score"] is not None
        ),
        "seismic_null": meta["seismic_null"],
        "duplicates": meta["duplicates"],
        "columns": meta["columns"],
    }


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS rows,
                       count(flood_exposure_raw) AS flood_rows,
                       count(seismic_exposure_raw) AS seismic_rows,
                       count(cyclone_exposure_raw) AS cyclone_rows,
                       count(hazard_safety_score) AS hazard_rows,
                       round(avg(flood_safety_score)::numeric, 1)
                           AS avg_flood_score,
                       round(avg(seismic_safety_score)::numeric, 1)
                           AS avg_seismic_score,
                       round(avg(cyclone_safety_score)::numeric, 1)
                           AS avg_cyclone_score,
                       round(avg(hazard_safety_score)::numeric, 1)
                           AS avg_hazard_score,
                       round(min(hazard_safety_score)::numeric, 1)
                           AS min_hazard_score,
                       round(max(hazard_safety_score)::numeric, 1)
                           AS max_hazard_score
                FROM urban_centre_hazard_exposure
                """
            )
            print({"summary": dict(cur.fetchone())})
            cur.execute(
                """
                SELECT g.name, h.flood_exposure_raw, h.seismic_exposure_raw,
                       h.cyclone_exposure_raw, h.flood_safety_score,
                       h.seismic_safety_score, h.cyclone_safety_score,
                       h.hazard_safety_score
                FROM urban_centre_hazard_exposure h
                JOIN ghsl_urban_centres g USING (urban_centre_id)
                WHERE g.name IN (
                    'Lisbon', 'Tokyo', 'Manila', 'Istanbul', 'New Orleans',
                    'Shanghai', 'Port Louis', 'Kathmandu', 'Lima',
                    'Wellington', 'Miami', 'Nairobi'
                )
                ORDER BY g.name
                """
            )
            for row in cur.fetchall():
                print(dict(row))
            cur.execute(
                """
                SELECT g.name, h.hazard_safety_score
                FROM urban_centre_hazard_exposure h
                JOIN ghsl_urban_centres g USING (urban_centre_id)
                WHERE h.hazard_safety_score IS NOT NULL
                ORDER BY h.hazard_safety_score ASC, g.name
                LIMIT 5
                """
            )
            print({"lowest": [dict(row) for row in cur.fetchall()]})
            cur.execute(
                """
                SELECT g.name, h.hazard_safety_score
                FROM urban_centre_hazard_exposure h
                JOIN ghsl_urban_centres g USING (urban_centre_id)
                WHERE h.hazard_safety_score IS NOT NULL
                ORDER BY h.hazard_safety_score DESC, g.name
                LIMIT 5
                """
            )
            print({"highest": [dict(row) for row in cur.fetchall()]})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
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
