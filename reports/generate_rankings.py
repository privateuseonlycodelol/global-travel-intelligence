#!/usr/bin/env python3
"""
Reproduce the two million-plus city rankings from the database.

Ranking unit: one principal city per GHSL urban centre, where the principal
city is the largest GeoNames city in that centre. Eligibility: city population
>= 1,000,000 and GHSL centre population >= 25% of the city population. The
boundary filter removes city records whose population is a prefecture or
county rather than an urban area.

Outputs
-------
reports/greenery_top100.csv
reports/environment_top100.csv
reports/urban_scores_all_407.csv
reports/greenery_top100.md
reports/environment_top100.md

Usage
-----
    python reports/generate_rankings.py \
        --db-url "postgresql:///travel_global?host=/var/run/postgresql&port=5432"
"""

from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

OUT = Path(__file__).resolve().parent
BOUNDARY_RATIO = 0.25

QUERY = """
SELECT g.destination_id, d.name, d.country_iso2, d.population,
       m.urban_centre_id, m.urban_centre_name, m.centre_population,
       g.greenery_score_0_100, g.local_tree_score, g.local_other_veg_score,
       g.hinterland_tree_shrub_score, g.park_score, g.park_count_5km,
       g.vegetation_only_score_0_100,
       g.local_tree_share, g.local_shrub_share, g.local_grass_share,
       g.hinterland_tree_share, g.hinterland_shrub_share,
       g.local_water_fraction, g.hinterland_water_fraction,
       e.environment_score_0_100, c.combined_score_0_100,
       e.air_score, e.pm25_ugm3,
       e.worst_month_pm25, e.water_score, e.water_feature,
       e.water_distance_km, e.coast_distance_km, e.large_lake_name,
       e.large_lake_area_km2, e.large_lake_distance_km, e.river_order,
       e.discharge_cms, e.river_distance_km
FROM destination_greenery_score g
JOIN destinations d USING (destination_id)
LEFT JOIN destination_metro m USING (destination_id)
LEFT JOIN destination_environment_score e USING (destination_id)
LEFT JOIN destination_combined_score c USING (destination_id)
WHERE g.greenery_score_0_100 IS NOT NULL AND d.population >= 1000000
"""


def fetch(database_url: str) -> list[dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            rows = []
            for row in cur.fetchall():
                row = dict(row)
                for key, value in list(row.items()):
                    if isinstance(value, Decimal):
                        row[key] = float(value)
                rows.append(row)
    return rows


def principal_cities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        centre = row["urban_centre_id"]
        if not centre:
            continue
        if (
            centre not in groups
            or (row["population"] or 0) > (groups[centre]["population"] or 0)
        ):
            groups[centre] = row
    kept = []
    for row in groups.values():
        ratio = (row["centre_population"] or 0) / (row["population"] or 1)
        if ratio >= BOUNDARY_RATIO:
            kept.append(row)
    return kept


def rank(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(
        [row for row in rows if row.get(key) is not None],
        key=lambda row: (-row[key], -(row["population"] or 0)),
    )


FIELDS = [
    ("rank_greenery", lambda r: r.get("_rank_greenery")),
    ("rank_environment", lambda r: r.get("_rank_environment")),
    ("rank_combined", lambda r: r.get("_rank_combined")),
    ("city", lambda r: r["name"]),
    ("country", lambda r: r["country_iso2"]),
    ("city_population", lambda r: r["population"]),
    ("ghsl_centre", lambda r: r["urban_centre_name"]),
    ("centre_population", lambda r: r["centre_population"]),
    ("greenery_score_0_100", lambda r: round(r["greenery_score_0_100"], 2)),
    ("local_tree_score", lambda r: round(r["local_tree_score"], 2)),
    ("local_other_veg_score", lambda r: round(r["local_other_veg_score"], 2)),
    ("hinterland_tree_shrub_score", lambda r: round(r["hinterland_tree_shrub_score"], 2)),
    ("park_score", lambda r: round(r["park_score"], 2)),
    ("park_count_5km", lambda r: r["park_count_5km"]),
    ("vegetation_only_score_0_100", lambda r: round(r["vegetation_only_score_0_100"], 2)),
    ("local_tree_pct", lambda r: round(r["local_tree_share"] * 100, 2)),
    ("local_shrub_pct", lambda r: round(r["local_shrub_share"] * 100, 2)),
    ("local_grass_pct", lambda r: round(r["local_grass_share"] * 100, 2)),
    ("hinterland_tree_pct", lambda r: round(r["hinterland_tree_share"] * 100, 2)),
    ("hinterland_shrub_pct", lambda r: round(r["hinterland_shrub_share"] * 100, 2)),
    ("local_water_pct", lambda r: round(r["local_water_fraction"] * 100, 2)),
    ("hinterland_water_pct", lambda r: round(r["hinterland_water_fraction"] * 100, 2)),
    ("environment_score_0_100", lambda r: round(r["environment_score_0_100"], 2)),
    ("combined_score_0_100", lambda r: round(r["combined_score_0_100"], 2)),
    ("air_score", lambda r: round(r["air_score"], 2)),
    ("pm25_ugm3", lambda r: round(r["pm25_ugm3"], 2)),
    ("worst_month_pm25", lambda r: round(r["worst_month_pm25"], 2)),
    ("water_score", lambda r: round(r["water_score"], 2)),
    ("water_feature", lambda r: r["water_feature"]),
    ("water_distance_km", lambda r: round(r["water_distance_km"], 2)),
    ("coast_distance_km", lambda r: round(r["coast_distance_km"], 2)),
    ("large_lake_name", lambda r: r["large_lake_name"]),
    ("large_lake_area_km2", lambda r: round(r["large_lake_area_km2"], 2)),
    ("large_lake_distance_km", lambda r: round(r["large_lake_distance_km"], 2)),
    ("river_order", lambda r: r["river_order"]),
    ("discharge_cms", lambda r: round(r["discharge_cms"], 2)),
    ("river_distance_km", lambda r: round(r["river_distance_km"], 2)),
]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([name for name, _ in FIELDS])
        for row in rows:
            writer.writerow([fn(row) for _, fn in FIELDS])


def markdown(rows: list[dict[str, Any]], kind: str, limit: int = 100) -> str:
    if kind == "greenery":
        lines = [
            "| # | City | Country | Score | Local tree | Other veg | Hinterland | Parks | Population | GHSL centre |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for i, row in enumerate(rows[:limit], 1):
            lines.append(
                f"| {i} | {row['name']} | {row['country_iso2']} | "
                f"{row['greenery_score_0_100']:.1f} | {row['local_tree_score']:.1f} | "
                f"{row['local_other_veg_score']:.1f} | "
                f"{row['hinterland_tree_shrub_score']:.1f} | "
                f"{row['park_score']:.1f} | {row['population']:,} | "
                f"{row['urban_centre_name']} |"
            )
    elif kind == "combined":
        lines = [
            "| # | City | Country | Combined | Physical greenery | Environmental quality | Population | GHSL centre |",
            "|---:|---|---:|---:|---:|---:|---:|---|",
        ]
        for i, row in enumerate(rows[:limit], 1):
            lines.append(
                f"| {i} | {row['name']} | {row['country_iso2']} | "
                f"{row['combined_score_0_100']:.1f} | "
                f"{row['greenery_score_0_100']:.1f} | "
                f"{row['environment_score_0_100']:.1f} | "
                f"{row['population']:,} | {row['urban_centre_name']} |"
            )
    else:
        lines = [
            "| # | City | Country | Score | Greenery | Air | Water | PM2.5 | Population | GHSL centre |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for i, row in enumerate(rows[:limit], 1):
            lines.append(
                f"| {i} | {row['name']} | {row['country_iso2']} | "
                f"{row['environment_score_0_100']:.1f} | "
                f"{row['greenery_score_0_100']:.1f} | {row['air_score']:.1f} | "
                f"{row['water_score']:.1f} | {row['pm25_ugm3']:.1f} | "
                f"{row['population']:,} | {row['urban_centre_name']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    args = parser.parse_args()

    rows = principal_cities(fetch(args.db_url))
    greenery = rank(rows, "greenery_score_0_100")
    environment = rank(rows, "environment_score_0_100")
    combined = rank(rows, "combined_score_0_100")
    for i, row in enumerate(greenery, 1):
        row["_rank_greenery"] = i
    for i, row in enumerate(environment, 1):
        row["_rank_environment"] = i
    for i, row in enumerate(combined, 1):
        row["_rank_combined"] = i

    write_csv(OUT / "greenery_top100.csv", greenery[:100])
    write_csv(OUT / "environment_top100.csv", environment[:100])
    write_csv(OUT / "urban_scores_all_407.csv", greenery)
    (OUT / "greenery_top100.md").write_text(markdown(greenery, "greenery"), encoding="utf-8")
    (OUT / "environment_top100.md").write_text(
        markdown(environment, "environment"), encoding="utf-8"
    )
    write_csv(OUT / "combined_top100.csv", combined[:100])
    (OUT / "combined_top100.md").write_text(
        markdown(combined, "combined"), encoding="utf-8"
    )
    print(
        {
            "principal_cities": len(rows),
            "greenery_ranked": len(greenery),
            "environment_ranked": len(environment),
            "greenery_top": greenery[0]["name"],
            "environment_top": environment[0]["name"],
            "combined_top": combined[0]["name"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
