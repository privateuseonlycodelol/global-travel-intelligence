#!/usr/bin/env python3
"""
GHSL UCDB R2024A V1.2 urban form, density, and road completeness.

Two themes from the same GHS-UCDB R2024A V1.2 release (CC BY 4.0, 11,422
urban centres):

* **GHSL theme**: urban-centre area and population (2025), built-up surface
  (GHS-BUILT-S 2025), mean building height (GHS-BUILT-H 2020), built-up volume
  and built-up surface per capita.
* **INFRASTRUCTURES theme**: total road length inside the centre and road
  network density (Overture Maps, 2024).

The 0-100 ``urban_form_score`` is a fixed-anchor composite of satellite/GHSL
morphology only (no POI or Overture amenity counts):

* 50% walkable density curve peaking at 5,000-18,000 people/km2, with explicit
  penalties for auto-sprawl below 1,500/km2 and hyper-congestion above
  35,000/km2;
* 25% built-up compactness (share of the urban-centre area that is built-up);
* 25% verticality balance (mid-rise 8-12 m preferred; single-storey sprawl and
  tower-only extremes are penalised).

``road_km_per_built_km2`` is stored separately so the transit board can gate
local service on the presence of a mapped street network rather than on
mapped amenity density.

Both measures are urban-centre measures, not city-proper measures. A city
without a GHSL metro assignment has no row. All 11,422 GHSL urban centres are
loaded, including centres where a metric is missing, so coverage is explicit
rather than silently dropped.

Source: GHS-UCDB R2024A V1.2, European Commission JRC, CC BY 4.0.
https://human-settlement.emergency.copernicus.eu/

Usage
-----
    python bootstrap_urban_form.py
    python bootstrap_urban_form.py --validate
    python bootstrap_urban_form.py --report
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import zipfile
from pathlib import Path
from typing import Any, Sequence

import psycopg
import requests
from psycopg.rows import dict_row

CACHE = Path("./data_cache/ucdb")
BASE = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
    "GHS_UCDB_GLOBE_R2024A/GHS_UCDB_THEME_GLOBE_R2024A"
)
THEMES = {
    "GHSL": {
        "url": f"{BASE}/GHS_UCDB_THEME_GHSL_GLOBE_R2024A/V1-2/"
               "GHS_UCDB_THEME_GHSL_GLOBE_R2024A_V1_2.zip",
        "csv": "GHS_UCDB_THEME_GHSL_GLOBE_R2024A.csv",
    },
    "INFRASTRUCTURES": {
        "url": f"{BASE}/GHS_UCDB_THEME_INFRASTRUCTURES_GLOBE_R2024A/V1-2/"
               "GHS_UCDB_THEME_INFRASTRUCTURES_GLOBE_R2024A_V1_2.zip",
        "csv": "GHS_UCDB_THEME_INFRASTRUCTURES_GLOBE_R2024A.csv",
    },
}
SOURCE = (
    "GHS-UCDB R2024A V1.2 GHSL theme (GHS-POP/GHS-BUILT-S/GHS-BUILT-H, "
    "CC BY 4.0) and INFRASTRUCTURES theme (Overture Maps roads, 2024)"
)
SOURCE_URL = "https://human-settlement.emergency.copernicus.eu/"
OBSERVED_PERIOD = (
    "population/area 2025; built-up 2025; building height 2020; roads 2024"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS urban_centre_form (
    urban_centre_id        BIGINT PRIMARY KEY,
    pop_density_km2        DOUBLE PRECISION,
    built_up_fraction      DOUBLE PRECISION,
    mean_building_height_m DOUBLE PRECISION,
    road_length_km         DOUBLE PRECISION,
    road_km_per_built_km2  DOUBLE PRECISION,
    urban_form_score       DOUBLE PRECISION,
    source                 TEXT NOT NULL,
    source_url             TEXT NOT NULL,
    observed_period        TEXT NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS urban_centre_form_score_idx
    ON urban_centre_form (urban_form_score);
"""

INSERT_SQL = """
INSERT INTO urban_centre_form (
    urban_centre_id, pop_density_km2, built_up_fraction,
    mean_building_height_m, road_length_km, road_km_per_built_km2,
    urban_form_score, source, source_url, observed_period
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

# Fixed absolute anchors. The density curve peaks across the 5,000-18,000
# people/km2 band that supports walkable neighbourhoods, then falls away for
# both auto-sprawl and hyper-congestion. The compactness curve rewards a
# contiguous built footprint; the verticality curve prefers 8-12 m mid-rise
# (roughly 3-4 storeys) over both single-storey sprawl and tower-only form.
DENSITY_ANCHORS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (750.0, 0.0),
    (1500.0, 15.0),
    (2500.0, 55.0),
    (3500.0, 80.0),
    (5000.0, 100.0),
    (12000.0, 100.0),
    (18000.0, 100.0),
    (25000.0, 75.0),
    (35000.0, 40.0),
    (50000.0, 15.0),
    (80000.0, 0.0),
)
COMPACTNESS_ANCHORS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (0.02, 5.0),
    (0.05, 20.0),
    (0.10, 45.0),
    (0.15, 65.0),
    (0.20, 82.0),
    (0.28, 95.0),
    (0.35, 100.0),
    (0.45, 100.0),
)
VERTICALITY_ANCHORS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (2.5, 15.0),
    (3.5, 40.0),
    (5.0, 70.0),
    (6.5, 90.0),
    (8.0, 100.0),
    (12.0, 98.0),
    (15.0, 85.0),
    (18.0, 65.0),
    (25.0, 35.0),
    (40.0, 10.0),
)
DENSITY_WEIGHT = 0.50
COMPACTNESS_WEIGHT = 0.25
VERTICALITY_WEIGHT = 0.25

VALIDATION_SQL = """
SELECT w.destination_id,
       w.score              AS walkability_score,
       cf.continent         AS continent,
       m.urban_centre_id    AS urban_centre_id,
       p.restaurant_count   AS restaurant_count,
       f.urban_form_score   AS urban_form_score
FROM walkability_scores w
JOIN destinations d USING (destination_id)
LEFT JOIN country_facts cf ON cf.country_iso2 = d.country_iso2
JOIN destination_metro m USING (destination_id)
LEFT JOIN poi_metrics p USING (destination_id)
JOIN urban_centre_form f USING (urban_centre_id)
ORDER BY w.destination_id
"""

VALIDATION_COUNT_SQL = """
SELECT
    (SELECT count(*) FROM walkability_scores) AS walkability_rows,
    (SELECT count(*)
       FROM walkability_scores w
       JOIN destination_metro m USING (destination_id)) AS with_metro
"""

REPORT_SQL = """
SELECT count(*)                    AS rows,
       count(urban_form_score)     AS scored,
       round(avg(urban_form_score)::numeric, 2) AS mean_score,
       round(min(urban_form_score)::numeric, 2) AS min_score,
       round(max(urban_form_score)::numeric, 2) AS max_score
FROM urban_centre_form
"""

REPORT_SAMPLE_SQL = """
SELECT g.name,
       round(f.pop_density_km2::numeric, 1)       AS pop_density_km2,
       round(f.built_up_fraction::numeric, 3)     AS built_up_fraction,
       round(f.mean_building_height_m::numeric, 2) AS mean_height_m,
       round(f.road_length_km::numeric, 1)        AS road_length_km,
       round(f.road_km_per_built_km2::numeric, 1) AS road_km_per_built_km2,
       round(f.urban_form_score::numeric, 1)      AS urban_form_score
FROM urban_centre_form f
JOIN ghsl_urban_centres g USING (urban_centre_id)
WHERE g.name = ANY(%s)
ORDER BY g.name
"""

MISSING_TOKENS = frozenset(
    {"", "-", "--", "na", "n/a", "n.a.", "null", "none", "nan", "#n/a"}
)


def log(message: str) -> None:
    print(message, flush=True)


def ensure_csv(theme: str, cache_dir: Path | str = CACHE) -> Path:
    """Return the cached theme CSV, downloading and extracting it once."""
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


def interpolate(
    value: float | None,
    anchors: Sequence[tuple[float, float]],
) -> float | None:
    """Linearly interpolate a bounded score from fixed absolute anchors."""
    if value is None:
        return None
    points = sorted(anchors)
    if value <= points[0][0]:
        return float(points[0][1])
    if value >= points[-1][0]:
        return float(points[-1][1])
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if left_x <= value <= right_x:
            fraction = (value - left_x) / (right_x - left_x)
            return left_y + fraction * (right_y - left_y)
    return None


def density_score(pop_density_km2: float | None) -> float | None:
    """Walkable density curve: peak 5,000-18,000 people/km2."""
    return interpolate(pop_density_km2, DENSITY_ANCHORS)


def compactness_score(built_up_fraction: float | None) -> float | None:
    """Reward a contiguous built-up footprint inside the centre boundary."""
    return interpolate(built_up_fraction, COMPACTNESS_ANCHORS)


def verticality_score(mean_building_height_m: float | None) -> float | None:
    """Reward mid-rise balance over single-storey sprawl or tower extremes."""
    return interpolate(mean_building_height_m, VERTICALITY_ANCHORS)


def urban_form_score(
    pop_density_km2: float | None,
    built_up_fraction: float | None,
    mean_building_height_m: float | None,
) -> float | None:
    """Fixed-anchor 0-100 morphology score, density weighted at one half.

    Density is the required anchor: without a population density there is no
    urban-form observation. Missing compactness or verticality is handled by
    renormalising the remaining weights rather than silently scoring zero.
    """
    primary = density_score(pop_density_km2)
    if primary is None:
        return None
    parts = [
        (primary, DENSITY_WEIGHT),
        (compactness_score(built_up_fraction), COMPACTNESS_WEIGHT),
        (verticality_score(mean_building_height_m), VERTICALITY_WEIGHT),
    ]
    available = [(score, weight) for score, weight in parts
                 if score is not None]
    total_weight = sum(weight for _, weight in available)
    if total_weight <= 0:
        return None
    return round(
        sum(score * weight for score, weight in available) / total_weight,
        2,
    )


def road_km_per_built_km2(
    road_length_km: float | None,
    built_up_m2: float | None,
) -> float | None:
    """Mapped road km per built-up km2, the transit road-completeness ratio."""
    if road_length_km is None or built_up_m2 is None or built_up_m2 <= 0:
        return None
    return road_length_km / (built_up_m2 / 1_000_000.0)


def parse_form(
    ghsl_rows: Sequence[dict[str, str]],
    infrastructure_rows: Sequence[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join the GHSL and INFRASTRUCTURES themes by urban-centre ID."""
    infrastructure: dict[int, dict[str, str]] = {}
    duplicates = 0
    for row in infrastructure_rows:
        centre = integer(row.get("ID_UC_G0"))
        if centre is None:
            continue
        if centre in infrastructure:
            duplicates += 1
        infrastructure[centre] = row

    by_id: dict[int, dict[str, Any]] = {}
    for row in ghsl_rows:
        centre = integer(row.get("ID_UC_G0"))
        if centre is None:
            continue
        area_km2 = number(row.get("GC_UCA_KM2_2025"))
        population = number(row.get("GC_POP_TOT_2025"))
        built_up_m2 = number(row.get("GH_BUS_TOT_2025"))
        height_m = number(row.get("GH_BUH_AVG_2020"))
        road_row = infrastructure.get(centre, {})
        road_length_m = number(road_row.get("IN_ROA_LEN_2024"))

        density = (
            population / area_km2
            if population is not None and area_km2 and area_km2 > 0
            else None
        )
        built_up_fraction = (
            built_up_m2 / (area_km2 * 1_000_000.0)
            if built_up_m2 is not None and area_km2 and area_km2 > 0
            else None
        )
        road_length_km = (
            road_length_m / 1000.0 if road_length_m is not None else None
        )
        road_ratio = road_km_per_built_km2(road_length_km, built_up_m2)

        if centre in by_id:
            duplicates += 1
        by_id[centre] = {
            "urban_centre_id": centre,
            "pop_density_km2": density,
            "built_up_fraction": built_up_fraction,
            "mean_building_height_m": height_m,
            "road_length_km": road_length_km,
            "road_km_per_built_km2": road_ratio,
            "urban_form_score": urban_form_score(
                density, built_up_fraction, height_m
            ),
            "source": SOURCE,
            "source_url": SOURCE_URL,
            "observed_period": OBSERVED_PERIOD,
        }
    return list(by_id.values()), {
        "ghsl_rows": len(ghsl_rows),
        "infrastructure_rows": len(infrastructure_rows),
        "rows": len(by_id),
        "duplicates": duplicates,
    }


def average_ranks(values: Sequence[float]) -> list[float]:
    """Competition ranks with ties receiving their mean rank."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while (
            end + 1 < len(order)
            and values[order[end + 1]] == values[order[position]]
        ):
            end += 1
        rank = (position + end) / 2.0 + 1.0
        for index in range(position, end + 1):
            ranks[order[index]] = rank
        position = end + 1
    return ranks


def spearman_correlation(
    xs: Sequence[float | None],
    ys: Sequence[float | None],
) -> tuple[float | None, int]:
    """Spearman rho with average-rank ties and pairwise null removal."""
    pairs = [
        (float(x), float(y))
        for x, y in zip(xs, ys)
        if x is not None and y is not None
    ]
    if len(pairs) < 3:
        return None, len(pairs)
    rx = average_ranks([pair[0] for pair in pairs])
    ry = average_ranks([pair[1] for pair in pairs])
    count = len(rx)
    mean_x = sum(rx) / count
    mean_y = sum(ry) / count
    covariance = sum(
        (x - mean_x) * (y - mean_y) for x, y in zip(rx, ry)
    )
    variance_x = sum((x - mean_x) ** 2 for x in rx)
    variance_y = sum((y - mean_y) ** 2 for y in ry)
    denominator = math.sqrt(variance_x * variance_y)
    if denominator == 0:
        return None, count
    return covariance / denominator, count


def _correlation_entry(
    rows: Sequence[dict[str, Any]],
    x_key: str,
    y_key: str = "urban_form_score",
    transform: Any = None,
) -> dict[str, Any]:
    xs = [row.get(x_key) for row in rows]
    ys = [row.get(y_key) for row in rows]
    if transform is not None:
        xs = [
            transform(value) if value is not None else None
            for value in xs
        ]
    rho, count = spearman_correlation(xs, ys)
    return {
        "rho": round(rho, 3) if rho is not None else None,
        "n": count,
    }


def summarize_validation(
    rows: Sequence[dict[str, Any]],
    walkability_rows: int | None = None,
    with_metro: int | None = None,
) -> dict[str, Any]:
    """Compute both council validation guards from joined overlap rows."""
    by_continent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        continent = row.get("continent") or "UNKNOWN"
        by_continent.setdefault(str(continent), []).append(row)

    return {
        "overlap": {
            "walkability_rows": walkability_rows,
            "walkability_with_metro": with_metro,
            "validated_rows": len(rows),
        },
        "walkability": {
            "overall": _correlation_entry(rows, "walkability_score"),
            "by_continent": {
                continent: _correlation_entry(group, "walkability_score")
                for continent, group in sorted(by_continent.items())
            },
        },
        "restaurant_count": {
            "overall": _correlation_entry(
                rows,
                "restaurant_count",
                transform=math.log1p,
            ),
        },
    }


def validate(database_url: str) -> dict[str, Any]:
    """Read-only overlap validation against walkability and mapped amenities."""
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(VALIDATION_COUNT_SQL)
            counts = cur.fetchone()
            cur.execute(VALIDATION_SQL)
            rows = [dict(row) for row in cur.fetchall()]
    summary = summarize_validation(
        rows,
        walkability_rows=counts["walkability_rows"],
        with_metro=counts["with_metro"],
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(REPORT_SQL)
            print(dict(cur.fetchone()))
            cur.execute(
                REPORT_SAMPLE_SQL,
                (["Lisbon", "Trento", "Tokyo", "Manila", "Istanbul",
                  "Phoenix", "Houston", "Paris"],),
            )
            for row in cur.fetchall():
                print(dict(row))


def load(database_url: str, cache_dir: Path | str = CACHE) -> dict[str, Any]:
    ghsl_path = ensure_csv("GHSL", cache_dir)
    infrastructure_path = ensure_csv("INFRASTRUCTURES", cache_dir)
    ghsl_rows = read_rows(ghsl_path)
    infrastructure_rows = read_rows(infrastructure_path)
    records, meta = parse_form(ghsl_rows, infrastructure_rows)
    log(
        f"read {meta['ghsl_rows']:,} GHSL rows and "
        f"{meta['infrastructure_rows']:,} infrastructure rows; "
        f"{meta['rows']:,} joined records"
    )
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("DELETE FROM urban_centre_form")
            cur.executemany(
                INSERT_SQL,
                [
                    (
                        record["urban_centre_id"],
                        record["pop_density_km2"],
                        record["built_up_fraction"],
                        record["mean_building_height_m"],
                        record["road_length_km"],
                        record["road_km_per_built_km2"],
                        record["urban_form_score"],
                        record["source"],
                        record["source_url"],
                        record["observed_period"],
                    )
                    for record in records
                ],
            )
        conn.commit()
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--cache-dir", default=str(CACHE))
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.validate:
        validate(args.db_url)
        return 0
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, args.cache_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
