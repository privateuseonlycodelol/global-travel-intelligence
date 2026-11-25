#!/usr/bin/env python3
"""
Urban-area greenery and surrounding-nature rankings from GHSL centre polygons.

Ranking universe: one principal city per GHSL urban centre with centre
population >= 1,000,000. The urban greenery score is measured inside the GHSL
polygon, not around a point. The surrounding-nature score is measured in the
1-15 km annulus around the city point, so it does not overlap the 1 km disc.

Urban greenery requires a built-up share of at least 20% inside the polygon;
below that the GHSL "centre" is a low-density rural region, not an urban area.
The surrounding-nature ranking does not need this filter because it is measured
outside the polygon.

Usage
-----
    python reports/generate_urban_rankings.py \
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
MIN_BUILT_UP = 0.20

QUERY = """
SELECT u.destination_id, d.name, d.country_iso2, d.population,
       m.urban_centre_id, m.urban_centre_name, m.centre_population,
       u.area_km2, u.tree_fraction, u.shrub_fraction, u.grassland_fraction,
       u.cropland_fraction, u.built_up_fraction, u.water_fraction,
       u.urban_tree_score_0_100, u.urban_green_score_0_100,
       s.tree_fraction AS annulus_tree_fraction,
       s.shrub_fraction AS annulus_shrub_fraction,
       s.grassland_fraction AS annulus_grass_fraction,
       s.water_fraction AS annulus_water_fraction,
       s.annulus_tree_share, s.annulus_tree_shrub_share,
       s.annulus_green_share, s.surrounding_nature_score_0_100,
       e.water_score, e.water_feature, e.water_distance_km
FROM destination_urban_greenery u
JOIN destinations d USING (destination_id)
LEFT JOIN destination_metro m USING (destination_id)
JOIN destination_surrounding_nature s USING (destination_id)
LEFT JOIN destination_environment_score e USING (destination_id)
ORDER BY u.urban_centre_id
"""

FIELDS = [
    ("rank_urban_greenery", lambda r: r.get("_rank_urban")),
    ("rank_surrounding_nature", lambda r: r.get("_rank_surrounding")),
    ("city", lambda r: r["name"]),
    ("country", lambda r: r["country_iso2"]),
    ("city_population", lambda r: r["population"]),
    ("ghsl_centre", lambda r: r["urban_centre_name"]),
    ("centre_population", lambda r: r["centre_population"]),
    ("centre_area_km2", lambda r: round(r["area_km2"], 1)),
    ("built_up_pct", lambda r: round(r["built_up_fraction"] * 100, 2)),
    ("urban_tree_pct", lambda r: round(r["tree_fraction"] * 100, 2)),
    ("urban_shrub_pct", lambda r: round(r["shrub_fraction"] * 100, 2)),
    ("urban_grass_pct", lambda r: round(r["grassland_fraction"] * 100, 2)),
    ("urban_crop_pct", lambda r: round(r["cropland_fraction"] * 100, 2)),
    ("urban_water_pct", lambda r: round(r["water_fraction"] * 100, 2)),
    ("urban_tree_score_0_100", lambda r: round(r["urban_tree_score_0_100"], 2)),
    ("urban_green_score_0_100", lambda r: round(r["urban_green_score_0_100"], 2)),
    ("annulus_tree_pct", lambda r: round(r["annulus_tree_share"] * 100, 2)),
    ("annulus_tree_shrub_pct", lambda r: round(r["annulus_tree_shrub_share"] * 100, 2)),
    ("annulus_water_pct", lambda r: round(r["annulus_water_fraction"] * 100, 2)),
    ("surrounding_nature_score_0_100", lambda r: round(r["surrounding_nature_score_0_100"], 2)),
    ("water_score", lambda r: round(r["water_score"], 2) if r["water_score"] is not None else None),
    ("water_feature", lambda r: r["water_feature"]),
    ("water_distance_km", lambda r: round(r["water_distance_km"], 2) if r["water_distance_km"] is not None else None),
    ("surrounding_access_score_0_100", lambda r: round(r["surrounding_access_score_0_100"], 2)),
]


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
                if row["surrounding_nature_score_0_100"] is not None:
                    row["surrounding_access_score_0_100"] = (
                        0.80 * row["surrounding_nature_score_0_100"]
                        + 0.20 * (row["water_score"] or 0.0)
                    )
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([name for name, _ in FIELDS])
        for row in rows:
            writer.writerow([fn(row) for _, fn in FIELDS])


def urban_md(rows: list[dict[str, Any]], limit: int = 100) -> str:
    lines = [
        "| # | City | Country | Urban green | Tree | Grass | Built | Area km² | Centre population |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(rows[:limit], 1):
        lines.append(
            f"| {i} | {row['name']} | {row['country_iso2']} | "
            f"{row['urban_green_score_0_100']:.1f} | "
            f"{row['urban_tree_score_0_100']:.1f} | "
            f"{row['grassland_fraction'] / (1 - row['water_fraction']) * 100:.1f} | "
            f"{row['built_up_fraction'] * 100:.1f} | "
            f"{row['area_km2']:.0f} | {row['centre_population'] or 0:,} |"
        )
    return "\n".join(lines) + "\n"


def surrounding_md(rows: list[dict[str, Any]], limit: int = 100) -> str:
    lines = [
        "| # | City | Country | Surrounding access | Tree+shrub | Water access | Built in centre | Area km² |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for i, row in enumerate(rows[:limit], 1):
        lines.append(
            f"| {i} | {row['name']} | {row['country_iso2']} | "
            f"{row['surrounding_access_score_0_100']:.1f} | "
            f"{row['annulus_tree_shrub_share'] * 100:.1f} | "
            f"{row['water_score']:.1f} | "
            f"{row['built_up_fraction'] * 100:.1f} | "
            f"{row['area_km2']:.0f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    args = parser.parse_args()

    rows = fetch(args.db_url)
    urban = [
        row for row in rows
        if row["built_up_fraction"] is not None
        and row["built_up_fraction"] >= MIN_BUILT_UP
        and row["urban_green_score_0_100"] is not None
    ]
    urban.sort(key=lambda r: (-r["urban_green_score_0_100"], -(r["population"] or 0)))
    for i, row in enumerate(urban, 1):
        row["_rank_urban"] = i

    surrounding = [
        row for row in rows
        if row["surrounding_access_score_0_100"] is not None
        and row["built_up_fraction"] is not None
        and row["built_up_fraction"] >= MIN_BUILT_UP
    ]
    surrounding.sort(
        key=lambda r: (-r["surrounding_access_score_0_100"], -(r["population"] or 0))
    )
    for i, row in enumerate(surrounding, 1):
        row["_rank_surrounding"] = i

    write_csv(OUT / "urban_greenery_top100.csv", urban[:100])
    write_csv(OUT / "surrounding_nature_top100.csv", surrounding[:100])
    write_csv(OUT / "urban_area_scores_all_482.csv", rows)
    (OUT / "urban_greenery_top100.md").write_text(urban_md(urban), encoding="utf-8")
    (OUT / "surrounding_nature_top100.md").write_text(
        surrounding_md(surrounding), encoding="utf-8"
    )
    print(
        {
            "centres": len(rows),
            "urban_ranked": len(urban),
            "surrounding_ranked": len(surrounding),
            "urban_top": urban[0]["name"],
            "surrounding_top": surrounding[0]["name"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
