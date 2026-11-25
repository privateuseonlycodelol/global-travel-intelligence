#!/usr/bin/env python3
"""All-city quality ranking, one principal city per GHSL urban centre.

The headline ranking uses only the Core index from
``bootstrap_quality_index_all.py`` (environment, climate, safety/health,
affordability and connectivity). A principal city is the largest destination
in a GHSL urban centre; destinations without a GHSL match are their own
principal city. Cities need ``data_completeness >= 0.90`` to receive a Core
rank, but every principal city is exported.

Supplementary GHSL values travel with the Core rows. Experimental indicators
(walkability, Overture culture/food/parks/transit and physical greenery) are
written to a separate file and never mixed into the headline ranking.

Usage
-----
    python reports/generate_quality_rankings_all.py --db-url "$SUPABASE_DB_URL"
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg.rows import dict_row

OUT = Path(__file__).resolve().parent
MIN_COMPLETENESS = 0.90

CONTINENT_NAMES = {
    "AF": "Africa",
    "AN": "Antarctica",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "OC": "Oceania",
    "SA": "South America",
}

QUERY = """
SELECT q.destination_id,
       q.name,
       q.country_iso2,
       q.centre_population,
       d.population AS city_population,
       q.urban_centre_id,
       q.ghsl_match_status,
       m.urban_centre_name AS ghsl_centre,
       cf.continent,
       q.quality_score,
       q.environment_score,
       q.climate_score,
       q.safety_health_score,
       q.affordability_score,
       q.connectivity_score,
       q.data_completeness,
       q.uncertainty_tier,
       q.uncertainty_flags,
       q.missing_dimensions,
       q.ghsl_ndvi_score,
       q.ghsl_heat_stress_days,
       q.ghsl_hazard_events,
       q.ghsl_enhanced_score,
       q.provenance_summary,
       q.walkability_score,
       q.public_transport_score,
       q.transit_stops_5km,
       q.transit_observation_status,
       q.culture_food_score,
       q.poi_observation_status,
       q.park_score,
       q.park_observation_status,
       q.physical_green_score
FROM city_quality_index_all q
JOIN destinations d USING (destination_id)
LEFT JOIN destination_metro m USING (destination_id)
LEFT JOIN country_facts cf ON cf.country_iso2 = d.country_iso2
"""


def _round(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


QUALITY_FIELDS: list[tuple[str, Callable[[dict], Any]]] = [
    ("rank", lambda r: r.get("_rank")),
    ("city", lambda r: r.get("name")),
    ("country", lambda r: r.get("country_iso2")),
    ("continent", lambda r: r.get("continent")),
    ("city_population", lambda r: r.get("city_population")),
    ("urban_centre_id", lambda r: r.get("urban_centre_id")),
    ("ghsl_centre", lambda r: r.get("ghsl_centre")),
    ("centre_population", lambda r: r.get("centre_population")),
    ("quality_score", lambda r: _round(r.get("quality_score"))),
    ("environment", lambda r: _round(r.get("environment_score"))),
    ("climate", lambda r: _round(r.get("climate_score"))),
    ("safety_health", lambda r: _round(r.get("safety_health_score"))),
    ("affordability", lambda r: _round(r.get("affordability_score"))),
    ("connectivity", lambda r: _round(r.get("connectivity_score"))),
    (
        "data_completeness",
        lambda r: _round(r.get("data_completeness"), 3),
    ),
    ("uncertainty_tier", lambda r: r.get("uncertainty_tier")),
    ("uncertainty_flags", lambda r: r.get("uncertainty_flags")),
    ("missing_dimensions", lambda r: r.get("missing_dimensions")),
    ("ghsl_ndvi_score", lambda r: _round(r.get("ghsl_ndvi_score"))),
    (
        "ghsl_heat_stress_days",
        lambda r: _round(r.get("ghsl_heat_stress_days")),
    ),
    ("ghsl_hazard_events", lambda r: r.get("ghsl_hazard_events")),
    (
        "ghsl_enhanced_score",
        lambda r: _round(r.get("ghsl_enhanced_score")),
    ),
    ("provenance_summary", lambda r: r.get("provenance_summary")),
]

EXPERIMENTAL_FIELDS: list[tuple[str, Callable[[dict], Any]]] = [
    ("city", lambda r: r.get("name")),
    ("country", lambda r: r.get("country_iso2")),
    ("continent", lambda r: r.get("continent")),
    ("urban_centre_id", lambda r: r.get("urban_centre_id")),
    ("ghsl_centre", lambda r: r.get("ghsl_centre")),
    ("city_population", lambda r: r.get("city_population")),
    ("walkability_score", lambda r: _round(r.get("walkability_score"))),
    (
        "public_transport_score",
        lambda r: _round(r.get("public_transport_score")),
    ),
    ("transit_stops_5km", lambda r: r.get("transit_stops_5km")),
    (
        "transit_observation_status",
        lambda r: r.get("transit_observation_status"),
    ),
    ("culture_food_score", lambda r: _round(r.get("culture_food_score"))),
    ("poi_observation_status", lambda r: r.get("poi_observation_status")),
    ("park_score", lambda r: _round(r.get("park_score"))),
    ("park_observation_status", lambda r: r.get("park_observation_status")),
    (
        "physical_green_score",
        lambda r: _round(r.get("physical_green_score")),
    ),
]


def normalise_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert psycopg Decimal values to float for CSV and arithmetic."""
    result = []
    for row in rows:
        clean = dict(row)
        for key, value in clean.items():
            if isinstance(value, Decimal):
                clean[key] = float(value)
        result.append(clean)
    return result


def _population_key(row: dict[str, Any]) -> tuple:
    population = row.get("city_population")
    return (
        population is None,
        -(population or 0),
        row.get("name") or "",
    )


def _quality_key(row: dict[str, Any]) -> tuple:
    quality = row.get("quality_score")
    population = row.get("city_population")
    return (
        quality is None,
        -(quality or 0.0),
        population is None,
        -(population or 0),
        row.get("name") or "",
    )


def principal_cities(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the largest destination per GHSL centre, else per destination."""
    seen: set[str] = set()
    result = []
    for row in sorted(rows, key=_population_key):
        urban_centre_id = row.get("urban_centre_id")
        key = (
            f"uc:{urban_centre_id}"
            if urban_centre_id is not None
            else f"dest:{row.get('destination_id')}"
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(row))
    return result


def rank_principal_cities(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sort by Core quality and assign a rank only to complete-enough rows."""
    ordered = sorted(rows, key=_quality_key)
    rank = 0
    for row in ordered:
        completeness = row.get("data_completeness")
        if (
            row.get("quality_score") is not None
            and completeness is not None
            and completeness >= MIN_COMPLETENESS
        ):
            rank += 1
            row["_rank"] = rank
        else:
            row["_rank"] = None
    return ordered


def low_completeness(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get("data_completeness") is None
        or row["data_completeness"] < MIN_COMPLETENESS
    ]


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: list[tuple[str, Callable[[dict], Any]]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([name for name, _ in fields])
        for row in rows:
            writer.writerow([getter(row) for _, getter in fields])


def continent_label(code: str | None) -> str:
    if not code:
        return "Unknown"
    name = CONTINENT_NAMES.get(code)
    return f"{name} ({code})" if name else code


def continent_summary(
    rows: list[dict[str, Any]],
) -> list[tuple[str, int, float]]:
    counts = Counter(continent_label(row.get("continent")) for row in rows)
    total = len(rows)
    return sorted(
        (
            (name, count, 100.0 * count / total if total else 0.0)
            for name, count in counts.items()
        ),
        key=lambda item: (-item[1], item[0]),
    )


def _format(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|")


def markdown(rows: list[dict[str, Any]], limit: int = 100) -> str:
    top = rows[:limit]
    lines = [
        "# Global City Quality Index — Top 100",
        "",
        "Core ranking only (completeness >= 0.90). Supplementary GHSL values "
        "are shown but do not affect the rank; experimental indicators are "
        "exported separately.",
        "",
        "## Continent distribution (top 100)",
        "",
        "| Continent | Cities | Share |",
        "|---|---:|---:|",
    ]
    for name, count, share in continent_summary(top):
        lines.append(f"| {_md(name)} | {count} | {share:.1f}% |")
    lines.extend(
        [
            "",
            "## Top 100",
            "",
            "| # | City | Country | Continent | Quality | Environment | "
            "Climate | Safety/health | Affordability | Connectivity | "
            "Completeness | Uncertainty |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in top:
        lines.append(
            "| {rank} | {city} | {country} | {continent} | {quality} | "
            "{environment} | {climate} | {safety} | {affordability} | "
            "{connectivity} | {completeness} | {uncertainty} |".format(
                rank=row.get("_rank") or "",
                city=_md(row.get("name")),
                country=_md(row.get("country_iso2")),
                continent=_md(continent_label(row.get("continent"))),
                quality=_format(row.get("quality_score")),
                environment=_format(row.get("environment_score")),
                climate=_format(row.get("climate_score")),
                safety=_format(row.get("safety_health_score")),
                affordability=_format(row.get("affordability_score")),
                connectivity=_format(row.get("connectivity_score")),
                completeness=_format(row.get("data_completeness"), 2),
                uncertainty=_md(row.get("uncertainty_tier")),
            )
        )
    return "\n".join(lines) + "\n"


def write_exports(
    rows: list[dict[str, Any]], out_dir: Path
) -> dict[str, Any]:
    """Write every Core, low-completeness and Experimental export."""
    out_dir.mkdir(parents=True, exist_ok=True)
    principal = principal_cities(normalise_rows(rows))
    ranked = rank_principal_cities(principal)
    incomplete = low_completeness(ranked)
    experimental = sorted(principal, key=_population_key)
    ranked_only = [row for row in ranked if row.get("_rank") is not None]

    write_csv(out_dir / "quality_all_cities.csv", ranked, QUALITY_FIELDS)
    write_csv(
        out_dir / "quality_all_cities_top100.csv",
        ranked_only[:100],
        QUALITY_FIELDS,
    )
    write_csv(
        out_dir / "quality_all_cities_low_completeness.csv",
        incomplete,
        QUALITY_FIELDS,
    )
    write_csv(
        out_dir / "quality_experimental_indicators.csv",
        experimental,
        EXPERIMENTAL_FIELDS,
    )
    (out_dir / "quality_all_cities_top100.md").write_text(
        markdown(ranked_only), encoding="utf-8"
    )
    return {
        "principal_cities": len(principal),
        "ranked_completeness_ge_0_90": len(ranked_only),
        "low_completeness": len(incomplete),
        "experimental_rows": len(experimental),
        "top": ranked_only[0]["name"] if ranked_only else None,
        "continent_count": len(continent_summary(ranked_only[:100])),
    }


def fetch_rows(database_url: str) -> list[dict[str, Any]]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            return [dict(row) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    args = parser.parse_args()
    stats = write_exports(fetch_rows(args.db_url), OUT)
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
