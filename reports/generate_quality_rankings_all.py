#!/usr/bin/env python3
"""All-city quality ranking, one principal city per GHSL urban centre."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

OUT = Path(__file__).resolve().parent
MIN_COMPLETENESS = 0.90

QUERY = """
WITH base AS (
    SELECT q.*, d.population AS city_population,
           m.urban_centre_id, m.urban_centre_name,
           row_number() OVER (
               PARTITION BY COALESCE(m.urban_centre_id::text, q.destination_id)
               ORDER BY d.population DESC NULLS LAST, q.name
           ) AS rn
    FROM city_quality_index_all q
    JOIN destinations d USING (destination_id)
    LEFT JOIN destination_metro m USING (destination_id)
    WHERE q.quality_score IS NOT NULL
)
SELECT * FROM base WHERE rn = 1
ORDER BY quality_score DESC, city_population DESC
"""

FIELDS = [
    ("rank", lambda r: r.get("_rank")),
    ("city", lambda r: r["name"]),
    ("country", lambda r: r["country_iso2"]),
    ("city_population", lambda r: r["city_population"]),
    ("ghsl_centre", lambda r: r["urban_centre_name"]),
    ("centre_population", lambda r: r["centre_population"]),
    ("quality_score", lambda r: round(r["quality_score"], 2)),
    ("environment", lambda r: round(r["environment_score"], 2) if r["environment_score"] is not None else None),
    ("culture_food", lambda r: round(r["culture_food_score"], 2) if r["culture_food_score"] is not None else None),
    ("safety_health", lambda r: round(r["safety_health_score"], 2) if r["safety_health_score"] is not None else None),
    ("affordability", lambda r: round(r["affordability_score"], 2) if r["affordability_score"] is not None else None),
    ("walkability", lambda r: round(r["walkability_score"], 2) if r["walkability_score"] is not None else None),
    ("public_transport", lambda r: round(r["public_transport_score"], 2) if r["public_transport_score"] is not None else None),
    ("climate", lambda r: round(r["climate_score"], 2) if r["climate_score"] is not None else None),
    ("connectivity", lambda r: round(r["connectivity_score"], 2) if r["connectivity_score"] is not None else None),
    ("data_completeness", lambda r: round(r["data_completeness"], 2)),
    ("missing_dimensions", lambda r: r["missing_dimensions"]),
]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([name for name, _ in FIELDS])
        for row in rows:
            writer.writerow([fn(row) for _, fn in FIELDS])


def markdown(rows: list[dict], limit: int = 100) -> str:
    lines = [
        "| # | City | Country | Quality | Environment | Culture/food | Safety/health | Affordability | Walkability | Public transport | Climate | Connectivity |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
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
            f"{fmt('public_transport_score')} | {fmt('climate_score')} | "
            f"{fmt('connectivity_score')} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    args = parser.parse_args()
    with psycopg.connect(args.db_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            rows = []
            for row in cur.fetchall():
                row = dict(row)
                for key, value in list(row.items()):
                    if hasattr(value, "as_tuple"):
                        row[key] = float(value)
                rows.append(row)
    ranked = [r for r in rows if r["data_completeness"] >= MIN_COMPLETENESS]
    for i, row in enumerate(ranked, 1):
        row["_rank"] = i
    write_csv(OUT / "quality_all_cities.csv", rows)
    write_csv(OUT / "quality_all_cities_top100.csv", ranked[:100])
    write_csv(
        OUT / "quality_all_cities_low_completeness.csv",
        [r for r in rows if r["data_completeness"] < MIN_COMPLETENESS],
    )
    (OUT / "quality_all_cities_top100.md").write_text(markdown(ranked), encoding="utf-8")
    print(
        {
            "principal_cities": len(rows),
            "ranked_completeness_ge_0_90": len(ranked),
            "top": ranked[0]["name"] if ranked else None,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
