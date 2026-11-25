#!/usr/bin/env python3
"""
GHSL-only rankings: physical urban greenery, nature access, environmental quality.

Ranking unit: GHSL urban centre with centre population >= 1M. The physical
greenery score is measured inside the GHSL polygon at native 10 m with cos(lat)
area weighting. Nature access subtracts the urban polygon from the 15 km disc
(provisional 15 km source) and reports natural tree+shrub separately from crop.

Usage
-----
    python reports/generate_ghsl_rankings.py \
      --db-url "postgresql:///travel_global?host=/var/run/postgresql&port=5432"
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

OUT = Path(__file__).resolve().parent

QUERY = """
SELECT destination_id, name, country_iso2, city_population,
       urban_centre_id, urban_centre_name, centre_population,
       area_km2, built_up_fraction, tree_fraction, shrub_fraction,
       grassland_fraction, cropland_fraction, water_fraction,
       urban_tree_score, urban_natural_score, physical_green_score,
       nature_access_score, nature_access_with_grass_score,
       agricultural_share, environment_score, air_score, water_score,
       pm25_ugm3, point_tree_share, built_up_valid, nature_access_valid,
       everyday_greenery_available, public_green_access_available,
       missing_reason
FROM destination_ghsl_greenery
WHERE ghsl_population_eligible
ORDER BY centre_population DESC
"""

FIELDS = [
    ("rank_physical", lambda r: r.get("_rank_physical")),
    ("rank_nature", lambda r: r.get("_rank_nature")),
    ("rank_environment", lambda r: r.get("_rank_environment")),
    ("city", lambda r: r["name"]),
    ("country", lambda r: r["country_iso2"]),
    ("ghsl_centre", lambda r: r["urban_centre_name"]),
    ("centre_population", lambda r: r["centre_population"]),
    ("centre_area_km2", lambda r: round(r["area_km2"], 1)),
    ("built_up_pct", lambda r: round(r["built_up_fraction"] * 100, 2)),
    ("tree_pct", lambda r: round(r["tree_fraction"] * 100, 2)),
    ("natural_pct", lambda r: round((r["tree_fraction"] + r["shrub_fraction"]) * 100, 2)),
    ("green_pct", lambda r: round((r["tree_fraction"] + r["shrub_fraction"] + r["grassland_fraction"]) * 100, 2)),
    ("crop_pct", lambda r: round(r["cropland_fraction"] * 100, 2)),
    ("water_pct", lambda r: round(r["water_fraction"] * 100, 2)),
    ("physical_green_score", lambda r: round(r["physical_green_score"], 2)),
    ("urban_tree_score", lambda r: round(r["urban_tree_score"], 2)),
    ("nature_access_score", lambda r: round(r["nature_access_score"], 2) if r["nature_access_score"] is not None else None),
    ("nature_access_with_grass_score", lambda r: round(r["nature_access_with_grass_score"], 2) if r["nature_access_with_grass_score"] is not None else None),
    ("agricultural_share_pct", lambda r: round(r["agricultural_share"] * 100, 2) if r["agricultural_share"] is not None else None),
    ("environment_score", lambda r: round(r["environment_score"], 2) if r["environment_score"] is not None else None),
    ("air_score", lambda r: round(r["air_score"], 2) if r["air_score"] is not None else None),
    ("water_score", lambda r: round(r["water_score"], 2) if r["water_score"] is not None else None),
    ("pm25_ugm3", lambda r: round(r["pm25_ugm3"], 2) if r["pm25_ugm3"] is not None else None),
    ("point_tree_pct_supplementary", lambda r: round(r["point_tree_share"] * 100, 2) if r["point_tree_share"] is not None else None),
    ("built_up_valid", lambda r: r["built_up_valid"]),
    ("nature_access_valid", lambda r: r["nature_access_valid"]),
    ("everyday_greenery_available", lambda r: r["everyday_greenery_available"]),
    ("public_green_access_available", lambda r: r["public_green_access_available"]),
    ("missing_reason", lambda r: r["missing_reason"]),
]


def fetch(database_url: str) -> list[dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            rows = []
            for row in cur.fetchall():
                row = dict(row)
                for key, value in list(row.items()):
                    if hasattr(value, "as_tuple"):
                        row[key] = float(value)
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([name for name, _ in FIELDS])
        for row in rows:
            writer.writerow([fn(row) for _, fn in FIELDS])


def table(rows: list[dict[str, Any]], kind: str, limit: int = 100) -> str:
    if kind == "physical":
        header = "| # | City | Country | Physical green | Tree | Natural | Grass | Built | Centre pop |"
        align = "|---:|---|---:|---:|---:|---:|---:|---:|---:|"
        cells = lambda r: (
            f"{r['physical_green_score']:.1f} | {r['urban_tree_score']:.1f} | "
            f"{r['urban_natural_score']:.1f} | "
            f"{r['grassland_fraction'] * 100 / max(1e-9, 1 - r['water_fraction']):.1f} | "
            f"{r['built_up_fraction'] * 100:.1f} | {r['centre_population']:,}"
        )
    elif kind == "nature":
        header = "| # | City | Country | Nature access | Natural | With grass | Crop | Built | Centre pop |"
        align = "|---:|---|---:|---:|---:|---:|---:|---:|---:|"
        cells = lambda r: (
            f"{r['nature_access_score']:.1f} | "
            f"{r['nature_access_score']:.1f} | "
            f"{r['nature_access_with_grass_score']:.1f} | "
            f"{r['agricultural_share'] * 100:.1f} | "
            f"{r['built_up_fraction'] * 100:.1f} | {r['centre_population']:,}"
        )
    else:
        header = "| # | City | Country | Environment | Physical green | Air | Water | PM2.5 | Centre pop |"
        align = "|---:|---|---:|---:|---:|---:|---:|---:|---:|"
        cells = lambda r: (
            f"{r['environment_score']:.1f} | {r['physical_green_score']:.1f} | "
            f"{r['air_score']:.1f} | {r['water_score']:.1f} | "
            f"{r['pm25_ugm3']:.1f} | {r['centre_population']:,}"
        )
    lines = [header, align]
    for i, row in enumerate(rows[:limit], 1):
        lines.append(f"| {i} | {row['name']} | {row['country_iso2']} | {cells(row)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    args = parser.parse_args()

    rows = fetch(args.db_url)
    physical = sorted(
        [r for r in rows if r["built_up_valid"] and r["physical_green_score"] is not None],
        key=lambda r: (-r["physical_green_score"], -(r["centre_population"] or 0)),
    )
    nature = []
    environment = sorted(
        [r for r in rows if r["built_up_valid"] and r["environment_score"] is not None],
        key=lambda r: (-r["environment_score"], -(r["centre_population"] or 0)),
    )
    for i, row in enumerate(physical, 1):
        row["_rank_physical"] = i
    for i, row in enumerate(environment, 1):
        row["_rank_environment"] = i

    write_csv(OUT / "ghsl_physical_greenery_top100.csv", physical[:100])
    write_csv(OUT / "ghsl_environment_top100.csv", environment[:100])
    write_csv(OUT / "ghsl_scores_all_516.csv", rows)
    (OUT / "ghsl_physical_greenery_top100.md").write_text(
        table(physical, "physical"), encoding="utf-8"
    )
    (OUT / "ghsl_environment_top100.md").write_text(
        table(environment, "environment"), encoding="utf-8"
    )
    print(
        {
            "centres": len(rows),
            "physical": len(physical),
            "nature": 0,
            "environment": len(environment),
            "physical_top": physical[0]["name"] if physical else None,
            "nature_top": None,
            "environment_top": environment[0]["name"] if environment else None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
