#!/usr/bin/env python3
"""
Objective city quality ranking for GHSL centres >= 1M.

Headline weights: environment 30, culture/food 20, safety/health 15,
affordability 10, walkability 10, climate 10, connectivity 5.

Only rows with data_completeness >= 0.90 are ranked; 25 centres with missing
dimensions are listed separately. No imputation.

Usage
-----
    python reports/generate_quality_rankings.py \
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
MIN_COMPLETENESS = 0.90

QUERY = """
SELECT destination_id, name, country_iso2, centre_population,
       environment_score, culture_food_score, safety_health_score,
       affordability_score, walkability_score, climate_score,
       connectivity_score, quality_score, data_completeness,
       missing_dimensions
FROM city_quality_index
ORDER BY quality_score DESC, centre_population DESC
"""

FIELDS = [
    ("rank", lambda r: r.get("_rank")),
    ("city", lambda r: r["name"]),
    ("country", lambda r: r["country_iso2"]),
    ("quality_score", lambda r: round(r["quality_score"], 2)),
    ("environment", lambda r: round(r["environment_score"], 2) if r["environment_score"] is not None else None),
    ("culture_food", lambda r: round(r["culture_food_score"], 2) if r["culture_food_score"] is not None else None),
    ("safety_health", lambda r: round(r["safety_health_score"], 2) if r["safety_health_score"] is not None else None),
    ("affordability", lambda r: round(r["affordability_score"], 2) if r["affordability_score"] is not None else None),
    ("walkability", lambda r: round(r["walkability_score"], 2) if r["walkability_score"] is not None else None),
    ("climate", lambda r: round(r["climate_score"], 2) if r["climate_score"] is not None else None),
    ("connectivity", lambda r: round(r["connectivity_score"], 2) if r["connectivity_score"] is not None else None),
    ("data_completeness", lambda r: round(r["data_completeness"], 2)),
    ("missing_dimensions", lambda r: r["missing_dimensions"]),
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


def markdown(rows: list[dict[str, Any]], limit: int = 100) -> str:
    lines = [
        "| # | City | Country | Quality | Environment | Culture/food | Safety/health | Affordability | Walkability | Climate | Connectivity |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(rows[:limit], 1):
        def fmt(key):
            value = row[key]
            return f"{value:.1f}" if value is not None else "—"
        lines.append(
            f"| {i} | {row['name']} | {row['country_iso2']} | "
            f"{row['quality_score']:.1f} | {fmt('environment_score')} | "
            f"{fmt('culture_food_score')} | {fmt('safety_health_score')} | "
            f"{fmt('affordability_score')} | {fmt('walkability_score')} | "
            f"{fmt('climate_score')} | {fmt('connectivity_score')} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    args = parser.parse_args()

    rows = fetch(args.db_url)
    ranked = [
        row for row in rows
        if row["quality_score"] is not None
        and row["data_completeness"] >= MIN_COMPLETENESS
    ]
    ranked.sort(key=lambda r: (-r["quality_score"], -(r["centre_population"] or 0)))
    for i, row in enumerate(ranked, 1):
        row["_rank"] = i

    excluded = [
        row for row in rows
        if row["quality_score"] is not None
        and row["data_completeness"] < MIN_COMPLETENESS
    ]

    write_csv(OUT / "quality_top100.csv", ranked[:100])
    write_csv(OUT / "quality_all_491.csv", ranked)
    write_csv(OUT / "quality_excluded_low_completeness.csv", excluded)
    (OUT / "quality_top100.md").write_text(markdown(ranked), encoding="utf-8")
    print(
        {
            "ranked": len(ranked),
            "excluded_low_completeness": len(excluded),
            "top": ranked[0]["name"] if ranked else None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
