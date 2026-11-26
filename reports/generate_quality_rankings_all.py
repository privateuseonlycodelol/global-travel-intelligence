#!/usr/bin/env python3
"""Generate Council Phase 1 Intrinsic City Quality Index rankings & diagnostics.

Outputs:
* reports/quality_all_cities.csv — all 7,018 GHSL urban-centre principal cities
  (one per GHSL urban_centre_id, ordered by population DESC) with all three
  boards, Tier A/B/C, construct_coverage, and named holes.
* reports/quality_all_cities_top100.csv / .md — Tier A Phase 1 intrinsic cohort
  top 100 WITH continent distribution vs base rates, log-GDP/capita correlation
  audit, and pathological-city release gate diagnostics.
* reports/quality_all_cities_low_completeness.csv — Tier B & C GHSL urban
  centres outside the Tier A ordered cohort.
* reports/quality_unmatched_towns.csv — separate series for unmatched towns
  >15 km from any GHSL urban centre (15 km satellites are suppressed).
* reports/quality_experimental_indicators.csv — experimental & travel-context
  columns (walkability, Overture POIs/transit/parks with unobserved status,
  WorldCover 516, affordability, airports) published strictly separately.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

OUT = Path(__file__).resolve().parent

CONTINENT_LABELS = {
    "AF": "Africa (AF)",
    "AS": "Asia (AS)",
    "EU": "Europe (EU)",
    "NA": "North America (NA)",
    "OC": "Oceania (OC)",
    "SA": "South America (SA)",
}

CENTRES_QUERY = """
WITH ranked AS (
    SELECT q.*,
           d.population AS city_population,
           m.urban_centre_name,
           cf.continent,
           row_number() OVER (
               PARTITION BY q.urban_centre_id
               ORDER BY d.population DESC NULLS LAST, q.name
           ) AS rn
    FROM city_quality_index_all q
    JOIN destinations d USING (destination_id)
    LEFT JOIN destination_metro m USING (destination_id)
    LEFT JOIN country_facts cf ON cf.country_iso2 = q.country_iso2
    WHERE q.settlement_class = 'ghsl_centre'
)
SELECT * FROM ranked WHERE rn = 1
ORDER BY (tier = 'A') DESC,
         quality_score DESC NULLS LAST,
         city_population DESC NULLS LAST,
         name
"""

UNMATCHED_TOWNS_QUERY = """
SELECT q.*,
       d.population AS city_population,
       NULL::text AS urban_centre_name,
       cf.continent
FROM city_quality_index_all q
JOIN destinations d USING (destination_id)
LEFT JOIN country_facts cf ON cf.country_iso2 = q.country_iso2
WHERE q.settlement_class = 'unmatched_town'
ORDER BY d.population DESC NULLS LAST, q.name
"""

SATELLITE_COUNT_QUERY = """
SELECT count(*) AS suppressed
FROM city_quality_index_all
WHERE settlement_class = 'satellite_suppressed'
"""


def _r(value: float | None, digits: int = 2) -> float | None:
    return round(float(value), digits) if value is not None else None


CENTRE_FIELDS = [
    ("rank", lambda r: r.get("_rank")),
    ("tier", lambda r: r["tier"]),
    ("city", lambda r: r["name"]),
    ("country", lambda r: r["country_iso2"]),
    ("continent", lambda r: r.get("continent")),
    ("city_population", lambda r: r.get("city_population")),
    ("urban_centre_id", lambda r: r.get("urban_centre_id")),
    ("ghsl_centre", lambda r: r.get("urban_centre_name")),
    ("centre_population", lambda r: r.get("centre_population")),
    ("quality_score", lambda r: _r(r.get("quality_score"))),
    ("biophysical_board", lambda r: _r(r.get("biophysical_board_score"))),
    ("institutional_board", lambda r: _r(r.get("institutional_board_score"))),
    ("services_board", lambda r: _r(r.get("services_board_score"))),
    ("air_score", lambda r: _r(r.get("air_score"))),
    ("waterfront_score", lambda r: _r(r.get("waterfront_score"))),
    ("thermal_heat_score", lambda r: _r(r.get("thermal_heat_score"))),
    ("thermal_safety_score", lambda r: _r(r.get("thermal_safety_score"))),
    ("ghsl_heat_stress_days", lambda r: _r(r.get("ghsl_heat_stress_days"), 1)),
    ("worldcover_green_score", lambda r: _r(r.get("worldcover_green_score"))),
    ("green_method", lambda r: r.get("green_method")),
    ("ghsl_ndvi_score", lambda r: _r(r.get("ghsl_ndvi_score"))),
    ("safety_prior_score", lambda r: _r(r.get("safety_prior_score"))),
    ("wash_prior_score", lambda r: _r(r.get("wash_prior_score"))),
    ("heritage_score", lambda r: _r(r.get("heritage_score"))),
    ("broadband_score", lambda r: _r(r.get("broadband_score"))),
    ("construct_coverage", lambda r: _r(r.get("construct_coverage"))),
    ("data_completeness", lambda r: _r(r.get("data_completeness"))),
    ("uncertainty_tier", lambda r: r.get("uncertainty_tier")),
    ("uncertainty_flags", lambda r: r.get("uncertainty_flags")),
    ("missing_dimensions", lambda r: r.get("missing_dimensions")),
    ("provenance_summary", lambda r: r.get("provenance_summary")),
]

PROFILE_FIELDS = [
    ("city", lambda r: r["name"]),
    ("country", lambda r: r["country_iso2"]),
    ("continent", lambda r: r.get("continent")),
    ("population_band", lambda r: r.get("population_band")),
    ("centre_population", lambda r: r.get("centre_population")),
    ("tier", lambda r: r["tier"]),
    ("quality_score", lambda r: _r(r.get("quality_score"))),
    ("health_profile", lambda r: _r(r.get("health_profile"))),
    ("nature_profile", lambda r: _r(r.get("nature_profile"))),
    ("urban_profile", lambda r: _r(r.get("urban_profile"))),
    ("is_pareto_optimal", lambda r: r.get("is_pareto_optimal")),
    ("lopo_max_delta", lambda r: _r(r.get("lopo_max_delta"))),
]

EXPERIMENTAL_FIELDS = [
    ("city", lambda r: r["name"]),
    ("country", lambda r: r["country_iso2"]),
    ("continent", lambda r: r.get("continent")),
    ("settlement_class", lambda r: r.get("settlement_class")),
    ("urban_centre_id", lambda r: r.get("urban_centre_id")),
    ("walkability_score", lambda r: _r(r.get("walkability_score"))),
    ("transit_stops_5km", lambda r: r.get("transit_stops_5km")),
    ("transit_observation_status", lambda r: r.get("transit_observation_status")),
    ("museum_culture_count", lambda r: r.get("museum_culture_count")),
    ("restaurant_count", lambda r: r.get("restaurant_count")),
    ("cuisine_entropy", lambda r: _r(r.get("cuisine_entropy"), 3)),
    ("poi_observation_status", lambda r: r.get("poi_observation_status")),
    ("park_count_5km", lambda r: r.get("park_count_5km")),
    ("park_observation_status", lambda r: r.get("park_observation_status")),
    ("worldcover_green_score", lambda r: _r(r.get("worldcover_green_score"))),
    ("ghsl_hazard_events", lambda r: r.get("ghsl_hazard_events")),
    ("price_level_vs_usd", lambda r: _r(r.get("price_level_vs_usd"), 3)),
    ("out_of_pocket_pct", lambda r: _r(r.get("out_of_pocket_pct"), 1)),
    ("airport_nearest_km", lambda r: _r(r.get("airport_nearest_km"), 1)),
]


def pearson_r(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def compute_gdp_diagnostics(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """Mandatory council release diagnostic: correlation with log GDP/capita."""
    out: dict[str, float | None] = {}
    for label, key in [
        ("quality_score", "quality_score"),
        ("biophysical_board", "biophysical_board_score"),
        ("institutional_board", "institutional_board_score"),
        ("broadband_score", "broadband_score"),
    ]:
        xs: list[float] = []
        ys: list[float] = []
        for r in rows:
            gdp = r.get("gdp_per_capita_ppp")
            val = r.get(key)
            if gdp is not None and float(gdp) > 0 and val is not None:
                xs.append(math.log(float(gdp)))
                ys.append(float(val))
        r_val = pearson_r(xs, ys)
        out[label] = round(r_val, 3) if r_val is not None else None
    return out


def compute_pathological_checks(
    centres: list[dict[str, Any]], tier_a: list[dict[str, Any]]
) -> dict[str, Any]:
    """Council release-gate diagnostics on known pathological cases."""
    top10_pct_n = max(1, len(tier_a) // 10)
    top_decile = tier_a[:top10_pct_n]
    hot_in_top_decile = sum(
        1
        for r in top_decile
        if r.get("ghsl_heat_stress_days") is not None
        and float(r["ghsl_heat_stress_days"]) >= 150
    )
    hot_high_thermal = sum(
        1
        for r in centres
        if r.get("ghsl_heat_stress_days") is not None
        and float(r["ghsl_heat_stress_days"]) >= 150
        and r.get("thermal_heat_score") is not None
        and float(r["thermal_heat_score"]) >= 50
    )
    china_zero_scored = sum(
        1
        for r in centres
        if r.get("country_iso2") == "CN"
        and (r.get("restaurant_count") or 0) == 0
        and r.get("poi_observation_status") == "observed"
    )
    unique_centres_top100 = len({r.get("urban_centre_id") for r in tier_a[:100]})
    return {
        "hot_ge150d_in_top_decile": hot_in_top_decile,
        "hot_ge150d_with_thermal_ge50": hot_high_thermal,
        "china_zero_pois_marked_observed": china_zero_scored,
        "unique_ghsl_centres_in_top100": unique_centres_top100,
    }


def population_band(pop: int | float | None) -> str:
    if pop is None or pop < 100_000:
        return "<100k"
    if pop < 300_000:
        return "100k-300k"
    if pop < 1_000_000:
        return "300k-1M"
    if pop < 5_000_000:
        return "1M-5M"
    return ">=5M"


def _weighted_exact(row: dict[str, Any], weights: dict[str, float]) -> float | None:
    if any(row.get(k) is None for k in weights):
        return None
    total = sum(weights.values())
    return sum(float(row[k]) * w for k, w in weights.items()) / total


def compute_profiles_and_pareto(centres: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compute Health/Nature/Urban profiles, LOPO sensitivity, and population-band Pareto sets."""
    lopo_weights = {
        "air_score": 0.15,
        "thermal_safety_score": 0.12,
        "worldcover_green_score": 0.14,
        "waterfront_score": 0.07,
        "safety_prior_score": 0.09,
        "wash_prior_score": 0.06,
        "heritage_score": 0.06,
        "broadband_score": 0.05,
    }
    enriched: list[dict[str, Any]] = []
    for r in centres:
        item = dict(r)
        item["population_band"] = population_band(r.get("centre_population"))
        item["health_profile"] = _weighted_exact(
            r,
            {
                "air_score": 0.35,
                "thermal_safety_score": 0.25,
                "safety_prior_score": 0.25,
                "wash_prior_score": 0.15,
            },
        )
        item["nature_profile"] = _weighted_exact(
            r, {"worldcover_green_score": 0.67, "waterfront_score": 0.33}
        )
        item["urban_profile"] = _weighted_exact(
            r, {"heritage_score": 0.45, "broadband_score": 0.35, "worldcover_green_score": 0.20}
        )
        base_q = r.get("quality_score")
        if base_q is not None and all(r.get(k) is not None for k in lopo_weights):
            deltas = []
            for drop_k in lopo_weights:
                sub = {k: w for k, w in lopo_weights.items() if k != drop_k}
                alt = _weighted_exact(r, sub)
                if alt is not None:
                    deltas.append(abs(float(base_q) - alt))
            item["lopo_max_delta"] = max(deltas) if deltas else None
        else:
            item["lopo_max_delta"] = None
        item["is_pareto_optimal"] = False
        enriched.append(item)

    # Population-band Pareto frontier over (health_profile, nature_profile, urban_profile)
    by_band: dict[str, list[dict[str, Any]]] = {}
    for item in enriched:
        if (
            item["tier"] == "A"
            and item["health_profile"] is not None
            and item["nature_profile"] is not None
            and item["urban_profile"] is not None
        ):
            by_band.setdefault(item["population_band"], []).append(item)

    for band_rows in by_band.values():
        for cand in band_rows:
            ch, cn, cu = cand["health_profile"], cand["nature_profile"], cand["urban_profile"]
            dominated = False
            for other in band_rows:
                if other is cand:
                    continue
                oh, on, ou = other["health_profile"], other["nature_profile"], other["urban_profile"]
                if (oh >= ch and on >= cn and ou >= cu) and (oh > ch or on > cn or ou > cu):
                    dominated = True
                    break
            cand["is_pareto_optimal"] = not dominated
    return enriched


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[tuple[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([name for name, _ in fields])
        for row in rows:
            writer.writerow([fn(row) for _, fn in fields])


def render_markdown(
    centres: list[dict[str, Any]],
    tier_a: list[dict[str, Any]],
    *,
    suppressed_satellites: int = 0,
    unmatched_towns: int = 0,
    limit: int = 100,
) -> str:
    top = tier_a[:limit]
    base_counts = Counter(r.get("continent") or "Unknown" for r in centres)
    top_counts = Counter(r.get("continent") or "Unknown" for r in top)
    total_centres = max(1, len(centres))
    total_top = max(1, len(top))

    gdp_diag = compute_gdp_diagnostics(tier_a)
    checks = compute_pathological_checks(centres, tier_a)

    lines = [
        "# Intrinsic City Quality Index — Phase 2 Tier A Top 100",
        "",
        "Three-board, absolute-anchor, no-imputation GHSL urban-centre cohort (cost/affordability, airports, life expectancy, physicians, road deaths, and WorldClim precipitation excluded). "
        f"Phase 2B active construct coverage = **0.74** of the 100% intrinsic reference vector (Air 15%, Daily UTCI Thermal Safety 12%, WorldCover 10 m Greenery 14%, Waterfront 7%, Homicide 9%, JMP WASH 6%, UNESCO Heritage 6%, Broadband 5%; 26% named holes: urban form, hospital care friction, non-heat hazards, gated transit). "
        f"GHSL urban centres: **{len(centres):,}** (Tier A: **{len(tier_a):,}**); suppressed 15 km satellites: **{suppressed_satellites:,}**; separate unmatched towns: **{unmatched_towns:,}**.",
        "",
        "## Continent distribution vs GHSL centre base rates",
        "",
        "| Continent | Top 100 | Top 100 Share | All GHSL Centres | Base Rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for code in sorted(set(base_counts) | set(top_counts), key=lambda c: (-top_counts[c], -base_counts[c], c)):
        label = CONTINENT_LABELS.get(code, code)
        lines.append(
            f"| {label} | {top_counts[code]} | {100.0 * top_counts[code] / total_top:.1f}% | "
            f"{base_counts[code]} | {100.0 * base_counts[code] / total_centres:.1f}% |"
        )

    lines.extend(
        [
            "",
            "## Mandatory release diagnostics",
            "",
            f"- **Log GDP/capita correlation (Tier A):** composite `r = {gdp_diag['quality_score']}`, biophysical `r = {gdp_diag['biophysical_board']}`, institutional (country-level) `r = {gdp_diag['institutional_board']}`, broadband `r = {gdp_diag['broadband_score']}`.",
            f"- **Pathological checks:** hot cities (≥150 UTCI>32 days) with thermal_heat ≥50 = `{checks['hot_ge150d_with_thermal_ge50']}`; hot cities in top decile = `{checks['hot_ge150d_in_top_decile']}`; China zero-POI rows falsely marked observed = `{checks['china_zero_pois_marked_observed']}`; unique GHSL centres in top 100 = `{checks['unique_ghsl_centres_in_top100']}`.",
            "",
            "## Top 100 (Tier A GHSL urban centres)",
            "",
            "| # | City | Country | Continent | Quality | Biophysical | Institutional | Services | Air | Thermal | Green | Shore | Heritage | Broadband |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )

    def fmt(val: Any) -> str:
        return f"{float(val):.1f}" if val is not None else "—"

    for i, row in enumerate(top, 1):
        cont = CONTINENT_LABELS.get(row.get("continent") or "", row.get("continent") or "—")
        lines.append(
            f"| {i} | {row['name']} | {row['country_iso2']} | {cont} | "
            f"{fmt(row.get('quality_score'))} | {fmt(row.get('biophysical_board_score'))} | "
            f"{fmt(row.get('institutional_board_score'))} | {fmt(row.get('services_board_score'))} | "
            f"{fmt(row.get('air_score'))} | {fmt(row.get('thermal_safety_score'))} | "
            f"{fmt(row.get('worldcover_green_score'))} | {fmt(row.get('waterfront_score'))} | "
            f"{fmt(row.get('heritage_score'))} | {fmt(row.get('broadband_score'))} |"
        )
    return "\n".join(lines) + "\n"


def _coerce_decimals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        for k, v in list(item.items()):
            if hasattr(v, "as_tuple"):
                item[k] = float(v)
        cleaned.append(item)
    return cleaned


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    args = parser.parse_args()
    with psycopg.connect(args.db_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(CENTRES_QUERY)
            centres = _coerce_decimals(cur.fetchall())
            cur.execute(UNMATCHED_TOWNS_QUERY)
            towns = _coerce_decimals(cur.fetchall())
            cur.execute(SATELLITE_COUNT_QUERY)
            suppressed = int(cur.fetchone()["suppressed"])

    tier_a = [r for r in centres if r["tier"] == "A"]
    tier_bc = [r for r in centres if r["tier"] != "A"]
    for i, row in enumerate(tier_a, 1):
        row["_rank"] = i

    profiles = compute_profiles_and_pareto(centres)
    pareto_count = sum(1 for r in profiles if r["is_pareto_optimal"])

    write_csv(OUT / "quality_all_cities.csv", centres, CENTRE_FIELDS)
    write_csv(OUT / "quality_all_cities_top100.csv", tier_a[:100], CENTRE_FIELDS)
    write_csv(OUT / "quality_all_cities_low_completeness.csv", tier_bc, CENTRE_FIELDS)
    write_csv(OUT / "quality_unmatched_towns.csv", towns, CENTRE_FIELDS)
    write_csv(OUT / "quality_profiles_and_pareto.csv", profiles, PROFILE_FIELDS)
    write_csv(
        OUT / "quality_experimental_indicators.csv",
        centres + towns,
        EXPERIMENTAL_FIELDS,
    )
    (OUT / "quality_all_cities_top100.md").write_text(
        render_markdown(
            centres,
            tier_a,
            suppressed_satellites=suppressed,
            unmatched_towns=len(towns),
        ),
        encoding="utf-8",
    )
    print(
        {
            "ghsl_centres": len(centres),
            "tier_a": len(tier_a),
            "tier_bc": len(tier_bc),
            "pareto_optimal_centres": pareto_count,
            "unmatched_towns": len(towns),
            "suppressed_satellites": suppressed,
            "top": tier_a[0]["name"] if tier_a else None,
            "gdp_diagnostics": compute_gdp_diagnostics(tier_a),
            "pathological_checks": compute_pathological_checks(centres, tier_a),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
