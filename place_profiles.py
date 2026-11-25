"""Place search, profiles and comparison for the MCP server.

Everything here takes an open psycopg cursor (``dict_row`` factory) and returns
plain dicts, so it can be unit-tested without the MCP runtime.

Design rules, matching docs/DATA_SOURCES.md:

* A value that was not measured is reported as ``status: "not_measured"`` with
  ``value: None``. It is never filled in.
* Percentiles are computed against **every loaded city that has a value**, not
  against the filtered result set, so a city's percentile does not change with
  the question asked.
* For metrics where lower is better (latency, tourist crowding, prices),
  the percentile is flipped so that 100 always means "best among cities with
  data". Population has no direction; its percentile means "larger than".
* Country indicators are national values (``granularity: "country"``) and
  are never presented as city measurements.
"""

from __future__ import annotations

import math
import re
from datetime import date
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

DESTINATION_ID_RE = re.compile(r"^gn-\d+$")
FUZZY_MIN_SIMILARITY = 0.4
MAX_COMPARE = 5


def normalize_name(value: str | None) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace.

    ``bootstrap_places.py`` stores aliases with this function and the server
    applies it to user input, so "São Paulo", "sao-paulo" and "SAO PAULO"
    compare equal. Non-Latin scripts are kept.
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = stripped.casefold().replace("ß", "ss")
    cleaned = re.sub(r"[^\w]+", " ", lowered, flags=re.UNICODE).replace("_", " ")
    return " ".join(cleaned.split())


@dataclass(frozen=True)
class Metric:
    key: str
    table: str          # alias of the joined table: d, p, m, b, t, h
    column: str
    label: str
    unit: str
    better: str | None  # "higher", "lower" or None
    group: str
    source_expr: str
    period_expr: str
    country_only: bool = False  # percentile only within the country (e.g. local currency)
    note: str | None = None
    value_sql: str | None = None  # overrides table.column
    sample_sql: str | None = None  # sample size shown next to the value
    zero_is_gap: bool = False     # 0 usually means "no map coverage": not ranked
    cohort: str = "cities"        # what the global percentile is among
    source_url_expr: str | None = None  # attribution URL, if the licence requires one


POI_NOTE = (
    "Counts what Overture Maps lists; coverage is thinner outside North America "
    "and Europe, so a low or zero count can mean missing map data."
)

MIN_BROADBAND_TESTS = 10
BROADBAND_VALUE = (
    "CASE WHEN b.total_tests >= " + str(MIN_BROADBAND_TESTS) + " AND {col} > 0 THEN {col} END"
)
BROADBAND_NOTE = (
    f"Tests-weighted median of Ookla tile mean speeds within 5 km; latency is the "
    f"tests-weighted mean. Values from fewer than {MIN_BROADBAND_TESTS} tests, or "
    "zero, are not ranked. sample_size is the number of tests."
)

OVERTURE_TRANSPORT_SOURCE = (
    "CASE WHEN m.pedestrian_segments_2km IS NOT NULL OR m.rail_segments_5km IS NOT NULL "
    "THEN 'Overture Maps Transportation' END"
)
OVERTURE_TRANSPORT_PERIOD = "substring(m.observed_period from 'overture-[^;]+')"

TABLES = {
    "p": "poi_metrics",
    "m": "mobility_metrics",
    "b": "broadband_metrics",
    "t": "tourism_pressure",
    "h": "housing_monthly",
    "v": "walkability_scores",
    "g": "ghsci_city_indicators",
    "a": "air_quality_annual",
}

METRICS: tuple[Metric, ...] = (
    Metric("population", "d", "population",
           "Population (GeoNames; usually the city proper, sometimes the wider city)",
           "people", None, "basics", "'GeoNames cities15000'", "NULL"),
    Metric("museum_culture_count", "p", "museum_culture_count",
           "Museums, galleries and cultural venues listed in Overture Maps within 5 km",
           "places", "higher", "things_to_do", "p.source", "p.observed_period",
           note=POI_NOTE, zero_is_gap=True),
    Metric("restaurant_count", "p", "restaurant_count",
           "Restaurants listed in Overture Maps within 5 km",
           "places", "higher", "food", "p.source", "p.observed_period",
           note=POI_NOTE, zero_is_gap=True),
    Metric("cuisine_variety", "p", "cuisine_entropy",
           "Cuisine variety among listed restaurants (Shannon diversity)", "index", "higher",
           "food", "p.source", "p.observed_period",
           value_sql="CASE WHEN p.restaurant_count > 0 THEN p.cuisine_entropy END",
           zero_is_gap=True,
           note="0 usually means the listed restaurants carry no cuisine tags."),
    Metric("pedestrian_street_share", "m", "pedestrian_ratio_2km",
           "Share of street segments within 2 km that are pedestrian/cycle paths",
           "ratio 0-1", "higher", "getting_around", OVERTURE_TRANSPORT_SOURCE,
           OVERTURE_TRANSPORT_PERIOD,
           note="Overture Transportation; loaded only for 1,131 cities in 8 countries.",
           cohort="cities in the 8-country Overture sample"),
    Metric("walkability_score", "v", "score",
           "Walkability score, 0-100 (5 km area average around the busiest point)",
           "score 0-100", "higher", "getting_around", "v.source", "v.observed_period",
           note=("Published by How Walkable from Overture Maps and OpenStreetMap: everyday places "
                 "within a walk, averaged across the 5 km around the city's busiest point. It is "
                 "a 5 km area average, not a whole-metro sidewalk, crossing or safety audit. "
                 "Only cities of 300,000+ in 95 countries are scored; mainland China, South Korea "
                 "and Russia are not ranked."),
           cohort="scored cities (300k+, 95 countries)",
           source_url_expr="v.url"),
    Metric("walkability_centre_score", "v", "centre_score",
           "Walkability at the single busiest point, 0-100",
           "score 0-100", "higher", "getting_around", "v.source", "v.observed_period",
           note=("The same How Walkable score at the busiest point only. Compare it with the "
                 "5 km area average to see how far walkability extends beyond the core."),
           cohort="scored cities (300k+, 95 countries)",
           source_url_expr="v.url"),
    Metric("ghsci_walkability_index", "g", "ghsci_walkability_index",
           "GHSCI walkability index (25-city-reference z-score)",
           "z-score sum", None, "walkability_ghsci", "g.source", "g.observed_period",
           note=("GHSCI components: population density, intersection density and daily living "
                 "access, standardised against the published 25-city reference. It is not 0-100 "
                 "and not comparable with the How Walkable score."),
           cohort="cities with a GHSCI run (2021 study + Trento 2026)",
           source_url_expr="g.source_url"),
    Metric("ghsci_sample_points", "g", "sample_points",
           "GHSCI sample points along the pedestrian network", "points", None,
           "walkability_ghsci", "g.source", "g.observed_period",
           note="Fewer than 1,000 points is flagged low confidence in the stored row.",
           cohort="cities with a GHSCI run (2021 study + Trento 2026)",
           source_url_expr="g.source_url"),
    Metric("ghsci_pct_fresh_food_500m", "g", "pct_fresh_food_500m",
           "Residents within a 500 m walk of a fresh-food market", "%", "higher",
           "walkability_ghsci", "g.source", "g.observed_period",
           note="GHSCI population-weighted network access; OSM destinations.",
           cohort="cities with a GHSCI run", source_url_expr="g.source_url"),
    Metric("ghsci_pct_convenience_500m", "g", "pct_convenience_500m",
           "Residents within a 500 m walk of a convenience store", "%", "higher",
           "walkability_ghsci", "g.source", "g.observed_period",
           note="GHSCI population-weighted network access; OSM destinations.",
           cohort="cities with a GHSCI run", source_url_expr="g.source_url"),
    Metric("ghsci_pct_transit_500m", "g", "pct_transit_500m",
           "Residents within a 500 m walk of a mapped public-transport stop", "%", "higher",
           "walkability_ghsci", "g.source", "g.observed_period",
           note="Any mapped OSM stop, not service frequency.",
           cohort="cities with a GHSCI run", source_url_expr="g.source_url"),
    Metric("ghsci_pct_open_space_500m", "g", "pct_open_space_500m",
           "Residents within a 500 m walk of public open space", "%", "higher",
           "walkability_ghsci", "g.source", "g.observed_period",
           note="GHSCI population-weighted network access.",
           cohort="cities with a GHSCI run", source_url_expr="g.source_url"),
    Metric("ghsci_pct_large_open_space_500m", "g", "pct_large_open_space_500m",
           "Residents within a 500 m walk of large public open space", "%", "higher",
           "walkability_ghsci", "g.source", "g.observed_period",
           note="GHSCI population-weighted network access; large means 1.5 ha or more.",
           cohort="cities with a GHSCI run", source_url_expr="g.source_url"),
    Metric("ghsci_local_population_density", "g", "local_nh_population_density",
           "GHSCI neighbourhood population density", "people/km²", None,
           "walkability_ghsci", "g.source", "g.observed_period",
           note="Unweighted mean across 1 km neighbourhoods; one of the index inputs.",
           cohort="cities with a GHSCI run", source_url_expr="g.source_url"),
    Metric("ghsci_local_intersection_density", "g", "local_nh_intersection_density",
           "GHSCI neighbourhood intersection density", "intersections/km²", None,
           "walkability_ghsci", "g.source", "g.observed_period",
           note="Unweighted mean across 1 km neighbourhoods; one of the index inputs.",
           cohort="cities with a GHSCI run", source_url_expr="g.source_url"),
    Metric("ghsci_daily_living", "g", "local_daily_living",
           "GHSCI daily-living score", "score 0-3", "higher",
           "walkability_ghsci", "g.source", "g.observed_period",
           note="Sum of access to fresh food, convenience and public transport; one of the index inputs.",
           cohort="cities with a GHSCI run", source_url_expr="g.source_url"),
    Metric("pm25_annual_ugm3", "a", "pm25_ugm3",
           "Annual PM2.5 concentration", "µg/m³", "lower", "environment",
           "a.source", "a.observed_period",
           note=("SatPM V6.GL.03 0.1° (~11 km) satellite/model/monitor-fused annual "
                 "estimate for 2024. City-point sample; not a monitor or a "
                 "neighbourhood map."),
           cohort="cities in the air-quality sample", source_url_expr="a.source_url"),
    Metric("transit_stations_5km", "m", "transit_stops_5km",
           "Bus, rail and metro stations within 5 km", "stations", "higher",
           "getting_around", "m.transit_stops_source", "m.transit_stops_period",
           note="Station/facility count, not service frequency."),
    Metric("transit_timetable_feeds", "m", "gtfs_active_feeds",
           "Public-transport timetable (GTFS) feeds whose area includes the city "
           "(potential coverage)", "feeds", "higher", "getting_around",
           "CASE WHEN m.gtfs_active_feeds IS NOT NULL THEN 'Mobility Database GTFS' END",
           "substring(m.observed_period from 'mobility-[0-9]{4}-[0-9]{2}')",
           note=("Counts feeds whose bounding box contains the city centre; it does not "
                 "confirm that routes or stops serve the city, or how often.")),
    Metric("rail_segments_5km", "m", "rail_segments_5km",
           "Rail track segments within 5 km", "segments", "higher",
           "getting_around", OVERTURE_TRANSPORT_SOURCE, OVERTURE_TRANSPORT_PERIOD),
    Metric("internet_download_mbps", "b", "median_down_mbps",
           "Tests-weighted median of Ookla tile download speeds within 5 km", "Mbps", "higher",
           "internet", "b.source", "b.observed_period",
           value_sql=BROADBAND_VALUE.format(col="b.median_down_mbps"), sample_sql="b.total_tests",
           note=BROADBAND_NOTE),
    Metric("internet_upload_mbps", "b", "median_up_mbps",
           "Tests-weighted median of Ookla tile upload speeds within 5 km", "Mbps", "higher",
           "internet", "b.source", "b.observed_period",
           value_sql=BROADBAND_VALUE.format(col="b.median_up_mbps"), sample_sql="b.total_tests",
           note=BROADBAND_NOTE),
    Metric("internet_latency_ms", "b", "avg_latency_ms",
           "Tests-weighted mean of Ookla tile latency within 5 km", "ms", "lower",
           "internet", "b.source", "b.observed_period",
           value_sql=BROADBAND_VALUE.format(col="b.avg_latency_ms"), sample_sql="b.total_tests",
           note=BROADBAND_NOTE),
    Metric("tourist_nights_per_resident", "t", "nights_per_resident",
           "Tourist nights per resident in the surrounding region", "nights/resident",
           "lower", "crowding", "t.source", "t.observed_period",
           note="Eurostat NUTS 2 region value (EU/EFTA only), shared by every city in the region."),
    Metric("short_let_monthly_local", "h", "median_monthly_local",
           "Median 30-night short-let price (Inside Airbnb, local currency)",
           "local currency / month", "lower", "cost", "h.source", "h.observed_period",
           country_only=True,
           note="Short-let listings, not long-term rent; compared only within the country."),
)

METRIC_GROUP_LABELS = {
    "basics": "Basics",
    "things_to_do": "Things to do",
    "food": "Food",
    "getting_around": "Getting around",
    "internet": "Internet",
    "crowding": "Tourist crowding",
    "cost": "Cost",
    "walkability_ghsci": "Walkability (GHSCI)",
    "environment": "Air and environment",
}

NOT_IN_DATABASE = [
    "Long-term residential rent (no global city-level open source loaded)",
    "City-level crime or safety (only the national homicide rate is loaded)",
    "Visa and residence rules (only an EU/Schengen 90-day flag is used in ranking)",
    "Rainy days and hot days per month (Open-Meteo/ERA5 enrichment not run)",
    "City-level air quality (only the national PM2.5 exposure is loaded)",
    "Neighbourhood-level walkability, sidewalk quality and crossing safety "
    "(the published score covers the city centre, not a neighbourhood)",
    "GHSCI scoring for cities without a completed run (Trento and the 24 "
    "published 25-city study cities only)",
    "Street-network form (intersections, dead ends) is stored for analysis but "
    "deliberately not shown as walkability",
    "Beaches, parks, healthcare and schools",
]

HOW_TO_READ = (
    "percentile_global: 0-100 among all loaded cities that have this value; 100 is "
    "best (most for 'higher' metrics, least for 'lower' metrics; for population, "
    "largest), with ties placed mid-range. band_* puts it in words; reading_* "
    "counts only places with a strictly worse value. "
    "percentile_in_country is the same within the country. status 'not_measured' "
    "means the source has no value for this city; 'none_listed' means the map "
    "source lists none, which usually reflects missing coverage rather than zero. "
    "cohort names what the global percentile is among (for example, only the "
    "8-country Overture sample or only scored cities). "
    "Country indicators are national values, not city measurements; each country's "
    "latest available year is used, so years can differ between countries -- quote them."
)
CLIMATE_NOTE = "WorldClim 2.1 1970-2000 averages; recent years are typically warmer."


def _existing_tables(cur: Any, names: Iterable[str]) -> set[str]:
    names = list(names)
    cur.execute(
        "SELECT t AS name FROM unnest(%s::text[]) AS t WHERE to_regclass(t) IS NOT NULL",
        (names,),
    )
    return {row["name"] for row in cur.fetchall()}


def build_metric_sql(available_tables: set[str]) -> str:
    """One query: every metric value plus global and in-country percentiles.

    Placeholder: destination_id array. Percentiles are computed over all
    destinations (not only the requested ones) and ignore NULL values.
    """
    present = {alias for alias, table in TABLES.items() if table in available_tables}
    joins = "".join(
        f"\n    LEFT JOIN {TABLES[alias]} {alias} ON {alias}.destination_id = d.destination_id"
        for alias in sorted(present)
    )
    base_cols: list[str] = []
    ranked_cols: list[str] = []
    for metric in METRICS:
        usable = metric.table == "d" or metric.table in present
        raw = metric.value_sql or f"{metric.table}.{metric.column}"
        raw = f"({raw})::double precision" if usable else "NULL::double precision"
        value = f"NULLIF({raw}, 0)" if metric.zero_is_gap else raw
        source = metric.source_expr if usable else "NULL"
        period = metric.period_expr if usable else "NULL"
        url = metric.source_url_expr if (usable and metric.source_url_expr) else "NULL"
        k = metric.key
        sample = f"({metric.sample_sql})::double precision" if (usable and metric.sample_sql) else "NULL::double precision"
        base_cols += [
            f"{value} AS {k}",
            f"{raw} AS {k}__raw",
            f"{sample} AS {k}__sample",
            f"({source})::text AS {k}__source",
            f"({period})::text AS {k}__period",
            f"({url})::text AS {k}__url",
        ]
        order = "DESC" if metric.better == "lower" else "ASC"
        def mid_rank(partition: str) -> str:
            # Tied values share the middle of their rank range, so a value
            # shared by many cities is not labelled "worst".
            window = f"OVER (PARTITION BY {partition} ORDER BY {k} {order})"
            n = f"count(*) OVER (PARTITION BY {partition})"
            return (
                f"CASE WHEN {k} IS NULL THEN NULL WHEN {n} < 2 THEN 0.5 ELSE "
                f"(percent_rank() {window} + (cume_dist() {window} * {n} - 1) "
                f"/ ({n} - 1)) / 2.0 END"
            )

        global_pct = (
            "NULL::double precision" if metric.country_only else mid_rank(f"({k} IS NULL)")
        )
        strict = (
            "NULL::double precision" if metric.country_only else
            f"CASE WHEN {k} IS NULL THEN NULL ELSE percent_rank() OVER "
            f"(PARTITION BY ({k} IS NULL) ORDER BY {k} {order}) END"
        )
        ranked_cols += [
            k, f"{k}__raw", f"{k}__sample", f"{k}__source", f"{k}__period", f"{k}__url",
            f"{strict} AS {k}__beats_global",
            f"{global_pct} AS {k}__pct_global",
            f"{mid_rank(f'country_iso2, ({k} IS NULL)')} AS {k}__pct_country",
            f"count({k}) OVER () AS {k}__n_global",
            f"count({k}) OVER (PARTITION BY country_iso2) AS {k}__n_country",
        ]
    return (
        "WITH base AS (\n  SELECT d.destination_id, d.country_iso2,\n    "
        + ",\n    ".join(base_cols)
        + "\n  FROM destinations d" + joins
        + "\n), ranked AS (\n  SELECT destination_id, country_iso2,\n    "
        + ",\n    ".join(ranked_cols)
        + "\n  FROM base\n)\nSELECT * FROM ranked WHERE destination_id = ANY(%s)"
    )


PRICE_BANDS = ("cheapest 10%", "cheapest 25%", "cheaper than most",
               "pricier than most", "priciest 25%")
PRICE_KEYS = {"price_level_vs_usd", "short_let_monthly_local"}


def percentile_band(pct: float | None, better: str | None = "higher",
                    key: str | None = None) -> str | None:
    """Plain label for a 0-100 percentile where 100 is best (or largest)."""
    if pct is None:
        return None
    if key in PRICE_KEYS:
        labels = PRICE_BANDS
    elif better is None:
        labels = ("largest 10%", "largest 25%", "larger than most",
                  "smaller than most", "smallest 25%")
    else:
        labels = ("best 10%", "best 25%", "better than most",
                  "worse than most", "worst 25%")
    for threshold, label in zip((90, 75, 50, 25), labels):
        if pct >= threshold:
            return label
    return labels[-1]


def percentile_reading(beats: float | None, better: str | None, n: int, of: str,
                       key: str | None = None) -> str | None:
    """Plain words from the share of *other* places with a strictly worse value.

    ``beats`` is 0-100, unrounded: the percentage of the other n-1 places this
    one strictly beats (ties are not counted as beaten). Only exactly 100 / 0
    produce "the highest" / "the lowest".
    """
    if beats is None or n < 2:
        return None
    if key in PRICE_KEYS:
        word, top, bottom = "cheaper", "cheapest", "priciest"
    elif better == "lower":
        word, top, bottom = "lower", "lowest", "highest"
    elif better == "higher":
        word, top, bottom = "higher", "highest", "lowest"
    else:
        word, top, bottom = "larger", "largest", "smallest"
    others = n - 1
    if beats >= 100:
        return f"the {top} of the {n:,} {of} with data"
    if beats <= 0:
        return f"the {bottom} (or tied for {bottom}) of the {n:,} {of} with data"
    shown = "<1" if beats < 1 else (">99" if beats > 99 else str(math.floor(beats)))
    return f"{word} than {shown}% of the other {others:,} {of} with data"


def _raw_pct(value: Any) -> float | None:
    return None if value is None else float(value) * 100.0


def _pct(value: Any) -> float | None:
    return None if value is None else round(float(value) * 100.0, 1)


def metric_entry(metric: Metric, row: dict[str, Any]) -> dict[str, Any]:
    k = metric.key
    value = row.get(k)
    entry: dict[str, Any] = {
        "key": k,
        "label": metric.label,
        "unit": metric.unit,
        "better": metric.better,
        "value": None if value is None else round(float(value), 3),
        "status": "not_measured" if value is None else "measured",
        "cohort": metric.cohort,
    }
    if value is None and int(row.get(f"{k}__n_global") or 0) == 0 and row:
        entry["status"] = "not_loaded"
    if value is None and metric.zero_is_gap and row.get(f"{k}__raw") == 0:
        entry.update({
            "value": 0,
            "status": "none_listed",
            "source": row.get(f"{k}__source"),
            "observed_period": row.get(f"{k}__period"),
        })
        if row.get(f"{k}__url"):
            entry["source_url"] = row[f"{k}__url"]
    if value is not None:
        pct_global = _pct(row.get(f"{k}__pct_global"))
        pct_country = _pct(row.get(f"{k}__pct_country"))
        n_global = int(row.get(f"{k}__n_global") or 0)
        n_country = int(row.get(f"{k}__n_country") or 0)
        beats_global = _raw_pct(row.get(f"{k}__beats_global"))
        band = percentile_band(pct_global, metric.better, metric.key)
        if beats_global is not None and n_global > 1 and metric.key not in PRICE_KEYS:
            if beats_global >= 100:
                band = "highest"
            elif beats_global <= 0:
                band = "lowest"
        entry.update({
            "cities_with_data_global": n_global,
            "cities_with_data_in_country": n_country,
            "source": row.get(f"{k}__source"),
            "observed_period": row.get(f"{k}__period"),
        })
        if n_global > 1:
            entry.update({
                "percentile_global": pct_global,
                "band_global": band,
                "reading_global": percentile_reading(
                    beats_global, metric.better, n_global, metric.cohort,
                    metric.key),
            })
        entry["percentile_in_country"] = pct_country if n_country > 1 else None
        if row.get(f"{k}__url"):
            entry["source_url"] = row[f"{k}__url"]
    if metric.sample_sql and row.get(f"{k}__sample") is not None:
        entry["sample_size"] = int(row[f"{k}__sample"])
    if metric.note:
        entry["note"] = metric.note
    return entry


def fetch_metric_rows(cur: Any, destination_ids: list[str]) -> dict[str, dict[str, Any]]:
    available = _existing_tables(cur, TABLES.values())
    cur.execute(build_metric_sql(available), (destination_ids,))
    return {str(row["destination_id"]): dict(row) for row in cur.fetchall()}


def climate_summary(months: list[dict[str, Any]]) -> dict[str, Any]:
    """Short, plain-language summary of a 12-month climatology."""
    rows = [m for m in months if m.get("median_temp_max_c") is not None]
    if not rows:
        return {"status": "not_measured"}

    def pick(key: str, reverse: bool) -> int | None:
        valid = [m for m in rows if m.get(key) is not None]
        if not valid:
            return None
        return int(sorted(valid, key=lambda m: float(m[key]), reverse=reverse)[0]["month"])

    mild = [
        int(m["month"]) for m in rows
        if 18.0 <= float(m["median_temp_max_c"]) <= 28.0
    ]
    precip = [float(m["precip_mm"]) for m in rows if m.get("precip_mm") is not None]
    return {
        "warmest_month": pick("median_temp_max_c", True),
        "coolest_month": pick("median_temp_min_c", False),
        "wettest_month": pick("precip_mm", True),
        "driest_month": pick("precip_mm", False),
        "months_with_daily_high_18_to_28c": mild,
        "annual_precip_mm": round(sum(precip), 1) if len(precip) == 12 else None,
        "note": CLIMATE_NOTE,
    }


def fetch_climate(cur: Any, destination_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    cur.execute(
        """
        SELECT destination_id, month, median_temp_max_c AS avg_daily_high_c,
               median_temp_min_c AS avg_daily_low_c, precip_mm,
               avg_rainy_days, avg_hot_days_30c, source, observed_period
        FROM climate_monthly
        WHERE destination_id = ANY(%s)
        ORDER BY destination_id, month
        """,
        (destination_ids,),
    )
    out: dict[str, list[dict[str, Any]]] = {i: [] for i in destination_ids}
    for row in cur.fetchall():
        row = dict(row)
        out.setdefault(str(row.pop("destination_id")), []).append(row)
    return out


def fetch_recent_climate(cur: Any, destination_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Recent monthly normals (ERA5 via Open-Meteo), if that table is loaded."""
    if "climate_recent" not in _existing_tables(cur, ["climate_recent"]):
        return {}
    cur.execute(
        """
        SELECT destination_id, month, avg_high_c, avg_low_c, precip_mm,
               rainy_days, hot_days_30c, warm_nights, years, source,
               observed_period
        FROM climate_recent
        WHERE destination_id = ANY(%s)
        ORDER BY destination_id, month
        """,
        (destination_ids,),
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        row = dict(row)
        out.setdefault(str(row.pop("destination_id")), []).append(row)
    return out


def recent_climate_summary(months: list[dict[str, Any]]) -> dict[str, Any]:
    if not months:
        return {"status": "not_loaded"}
    wet = max((m for m in months if m.get("precip_mm") is not None),
              key=lambda m: m["precip_mm"], default=None)
    hot = max((m for m in months if m.get("hot_days_30c") is not None),
              key=lambda m: m["hot_days_30c"], default=None)
    return {
        "wettest_month": wet["month"] if wet else None,
        "hottest_month": hot["month"] if hot else None,
        "annual_precip_mm": round(sum(
            float(m["precip_mm"]) for m in months if m.get("precip_mm") is not None
        ), 1),
        "annual_rainy_days": round(sum(
            float(m["rainy_days"]) for m in months if m.get("rainy_days") is not None
        ), 1),
        "annual_hot_days_30c": round(sum(
            float(m["hot_days_30c"]) for m in months if m.get("hot_days_30c") is not None
        ), 1),
        "annual_warm_nights": round(sum(
            float(m["warm_nights"]) for m in months if m.get("warm_nights") is not None
        ), 1),
        "note": "Recent monthly normals from ERA5; separate from the 1970-2000 WorldClim baseline.",
    }


def _climate_for_summary(months: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "month": m["month"],
            "median_temp_max_c": m.get("avg_daily_high_c"),
            "median_temp_min_c": m.get("avg_daily_low_c"),
            "precip_mm": m.get("precip_mm"),
        }
        for m in months
    ]


def fetch_country_context(cur: Any, country_codes: list[str]) -> dict[str, dict[str, Any]]:
    """Country facts and indicators (with world percentiles across countries)."""
    available = _existing_tables(cur, ["country_facts", "country_indicators"])
    out: dict[str, dict[str, Any]] = {
        code: {"granularity": "country", "status": "not_loaded"} for code in country_codes
    }
    if "country_facts" not in available:
        return out
    cur.execute(
        """
        SELECT country_iso2, name, capital, continent, currency_code, currency_name,
               languages, phone_prefix, neighbours, source, observed_period
        FROM country_facts WHERE country_iso2 = ANY(%s)
        """,
        (country_codes,),
    )
    for row in cur.fetchall():
        row = dict(row)
        code = str(row.pop("country_iso2"))
        out[code] = {
            "granularity": "country",
            "status": "loaded",
            "facts": row,
            "indicators": [],
            "note": ("No World Bank indicators are published for this country or "
                     "territory; safety, price and air-quality context is unavailable."),
        }
    indicators_loaded = False
    if "country_indicators" in available:
        cur.execute("SELECT EXISTS (SELECT 1 FROM country_indicators) AS any")
        indicators_loaded = bool(cur.fetchone()["any"])
    if not indicators_loaded:
        for ctx in out.values():
            if "note" in ctx:
                ctx["note"] = ("Country indicators (safety, prices, air quality) are not "
                               "loaded in this database; run bootstrap_places.py.")
    if indicators_loaded:
        cur.execute(
            """
            WITH r AS (
                SELECT country_iso2, indicator, value, year, label, unit, better,
                       source, source_indicator,
                       CASE WHEN count(*) OVER (PARTITION BY indicator) < 2 THEN 0.5
                            WHEN better = 'lower' THEN
                              (percent_rank() OVER (PARTITION BY indicator ORDER BY value DESC)
                               + (cume_dist() OVER (PARTITION BY indicator ORDER BY value DESC)
                                  * count(*) OVER (PARTITION BY indicator) - 1)
                                 / (count(*) OVER (PARTITION BY indicator) - 1)) / 2.0
                            ELSE
                              (percent_rank() OVER (PARTITION BY indicator ORDER BY value)
                               + (cume_dist() OVER (PARTITION BY indicator ORDER BY value)
                                  * count(*) OVER (PARTITION BY indicator) - 1)
                                 / (count(*) OVER (PARTITION BY indicator) - 1)) / 2.0
                       END AS pct,
                       CASE WHEN better = 'lower'
                            THEN percent_rank() OVER (PARTITION BY indicator ORDER BY value DESC)
                            ELSE percent_rank() OVER (PARTITION BY indicator ORDER BY value)
                       END AS beats,
                       count(*) OVER (PARTITION BY indicator) AS n
                FROM country_indicators
            )
            SELECT * FROM r WHERE country_iso2 = ANY(%s) ORDER BY indicator
            """,
            (country_codes,),
        )
        indicator_rows = cur.fetchall()
        cur.execute("SELECT DISTINCT indicator, label FROM country_indicators ORDER BY indicator")
        all_indicators = {r["indicator"]: r["label"] for r in cur.fetchall()}
        for row in indicator_rows:
            code = str(row["country_iso2"])
            if code not in out or "indicators" not in out[code]:
                continue
            pct = _pct(row["pct"])
            out[code].pop("note", None)
            out[code]["indicators"].append({
                "key": row["indicator"],
                "label": row["label"],
                "unit": row["unit"],
                "better": row["better"],
                "value": round(float(row["value"]), 3),
                "year": int(row["year"]),
                "is_stale": (date.today().year - int(row["year"])) > 5,
                "percentile_among_countries": pct,
                "band_among_countries": percentile_band(pct, row["better"], row["indicator"]),
                "reading_among_countries": percentile_reading(
                    _raw_pct(row["beats"]), row["better"], int(row["n"]), "countries",
                    row["indicator"]),
                "countries_with_data": int(row["n"]),
                "source": row["source"],
                "source_indicator": row["source_indicator"],
            })
        for ctx in out.values():
            if "indicators" in ctx:
                present = {ind["key"] for ind in ctx["indicators"]}
                ctx["indicators_missing"] = [
                    label for key, label in all_indicators.items() if key not in present
                ]
    return out


COUNTRY_SYNONYMS = {
    "uk": "GB", "u k": "GB", "great britain": "GB", "britain": "GB", "usa": "US", "u s a": "US", "u s": "US",
    "america": "US", "united states of america": "US", "holland": "NL",
    "czechia": "CZ", "south korea": "KR", "korea": "KR", "russia": "RU",
    "turkiye": "TR", "uae": "AE", "ivory coast": "CI", "vietnam": "VN",
}
OFFICIAL_NAME_WEIGHT = 10  # an exact official-name match beats an alternate name
                           # unless the alternate-name city is > 10x larger


def split_qualifiers(query: str) -> tuple[str, list[str]]:
    """'Austin, Texas, USA' -> ('Austin', ['Texas', 'USA'])."""
    parts = [part.strip() for part in (query or "").split(",")]
    if len(parts) < 2 or not parts[0] or any(not p or len(p) > 60 for p in parts[1:]):
        return (query or "").strip(), []
    return parts[0], parts[1:]


def split_qualifier(query: str) -> tuple[str, str | None]:
    """'Porto, Portugal' -> ('Porto', 'Portugal'); only the last qualifier."""
    head, qualifiers = split_qualifiers(query)
    if not qualifiers:
        return head, None
    if len(qualifiers) > 1:
        return ", ".join([head, *qualifiers[:-1]]), qualifiers[-1]
    return head, qualifiers[0]


def split_country_suffix(query: str) -> tuple[str, str | None]:
    """Backward-compatible helper: split only a trailing two-letter code."""
    head, tail = split_qualifier(query)
    if tail and len(tail) == 2 and tail.isalpha():
        return head, tail.upper()
    return (query or "").strip(), None


# GeoNames uses numeric admin1 codes for these countries; map the postal
# abbreviations people actually type to the admin1 names.
REGION_ABBREVIATIONS = {
    "AB": "alberta", "BC": "british columbia", "MB": "manitoba", "NB": "new brunswick",
    "NL": "newfoundland and labrador", "NS": "nova scotia", "ON": "ontario",
    "PE": "prince edward island", "QC": "quebec", "SK": "saskatchewan",
    "NSW": "new south wales", "VIC": "victoria", "QLD": "queensland",
    "WA": "western australia", "SA": "south australia", "TAS": "tasmania",
    "ACT": "australian capital territory", "NT": "northern territory",
}
REGION_DOMINANCE_RATIO = 3  # a city 3x larger than the rest of its region stands for it
SIMILAR_SIZE_RATIO = 0.5  # rival exact match at >= half the weighted size -> ask
SAME_COUNTRY_RATIO = 0.10  # a same-country namesake at >= 10% of the size -> ask


def _country_lookup(cur: Any, available: set[str]) -> tuple[set[str], dict[str, str]]:
    known: set[str] = set()
    names: dict[str, str] = {}
    if "country_facts" in available:
        cur.execute("SELECT country_iso2, name FROM country_facts")
        for row in cur.fetchall():
            known.add(str(row["country_iso2"]))
            names[normalize_name(row["name"])] = str(row["country_iso2"])
    return known, names


def _qualifier_filter(
    cur: Any, qualifier: str, available: set[str], index: int = 0,
    restrict_country: str | None = None,
) -> tuple[str, dict[str, Any], list[str], bool]:
    """SQL filter for one ', <country or region>' qualifier, the countries it
    names, and whether it names anything known at all.

    Raw two/three-letter region codes are only matched for the US (state
    postal codes); elsewhere GeoNames codes collide with ISO country codes
    (Serbia's "SE", Switzerland's "BE"). Canadian/Australian abbreviations go
    through REGION_ABBREVIATIONS. ``restrict_country`` limits the region
    reading to one country (``country_iso2`` given alongside "Salem, IN").
    """
    q_norm = normalize_name(qualifier)
    q_code = re.sub(r"[^A-Za-z]", "", qualifier).upper()
    known_countries, names = _country_lookup(cur, available)
    countries: set[str] = set()
    if q_norm in COUNTRY_SYNONYMS:
        countries.add(COUNTRY_SYNONYMS[q_norm])
    if q_norm in names:
        countries.add(names[q_norm])
    if len(q_code) == 2 and len(qualifier.strip()) <= 3:
        code = "GB" if q_code == "UK" else q_code
        if not known_countries or code in known_countries:
            countries.add(code)
    region_norms = sorted({q_norm, *([REGION_ABBREVIATIONS[q_code]]
                                     if q_code in REGION_ABBREVIATIONS else [])})
    us_code = q_code if 2 <= len(q_code) <= 3 else ""
    sfx = f"_{index}"
    params: dict[str, Any] = {f"q_countries{sfx}": sorted(countries)}
    clauses = [] if restrict_country else [f"d.country_iso2 = ANY(%(q_countries{sfx})s)"]
    region_known = False
    if "destination_admin1" in available:
        region_sql = (
            f"SELECT r.destination_id FROM destination_admin1 r "
            f"JOIN destinations rd ON rd.destination_id = r.destination_id "
            f"WHERE (r.admin1_norm = ANY(%(q_norms{sfx})s) "
            f"OR (%(q_code{sfx})s <> '' AND rd.country_iso2 = 'US' AND r.admin1_code = %(q_code{sfx})s))"
            + (f" AND rd.country_iso2 = %(q_restrict{sfx})s" if restrict_country else "")
        )
        params |= {f"q_norms{sfx}": region_norms, f"q_code{sfx}": us_code,
                   f"q_restrict{sfx}": restrict_country}
        cur.execute(
            "SELECT EXISTS (" + region_sql.replace(f"%(q_norms{sfx})s", "%(n)s")
            .replace(f"%(q_code{sfx})s", "%(c)s").replace(f"%(q_restrict{sfx})s", "%(r)s")
            + ") AS hit",
            {"n": region_norms, "c": us_code, "r": restrict_country},
        )
        region_known = bool(cur.fetchone()["hit"])
        clauses.append(f"d.destination_id IN ({region_sql})")
    if not clauses:
        return "AND FALSE", params, [], False
    return ("AND (" + " OR ".join(clauses) + ")", params, sorted(countries),
            bool(countries) or region_known)


def _normalize_country(cur: Any, value: str, available: set[str]) -> str | None:
    """'UK' / 'United Kingdom' / 'gb' -> 'GB'; None when not a known country."""
    known, names = _country_lookup(cur, available)
    norm = normalize_name(value)
    if norm in COUNTRY_SYNONYMS:
        return COUNTRY_SYNONYMS[norm]
    if norm in names:
        return names[norm]
    code = value.strip().upper()
    if len(code) == 2 and code.isalpha() and (not known or code in known):
        return code
    return None


def _combine_filters(
    cur: Any, qualifiers: list[str], available: set[str],
    restrict_country: str | None = None,
) -> tuple[str, dict[str, Any], list[str], bool]:
    """AND together one filter per qualifier ("Texas" AND "USA")."""
    sql, params, countries, recognized = "", {}, [], True
    for index, qualifier in enumerate(qualifiers):
        part_sql, part_params, part_countries, part_known = _qualifier_filter(
            cur, qualifier, available, index, restrict_country)
        sql += " " + part_sql
        params |= part_params
        countries = part_countries or countries
        recognized = recognized and part_known
    return sql, params, countries, recognized


def _candidate_rows_by_id(cur: Any, destination_id: str, available: set[str]) -> list[dict[str, Any]]:
    has_regions = "destination_admin1" in available
    cur.execute(
        f"""
        SELECT d.destination_id, d.name, d.country_iso2, d.population, d.timezone,
               d.lat, d.lon, {"r.admin1_name" if has_regions else "NULL::text"} AS region
        FROM destinations d
        {"LEFT JOIN destination_admin1 r ON r.destination_id = d.destination_id" if has_regions else ""}
        WHERE d.destination_id = %s
        """,
        (destination_id,),
    )
    return [dict(r) | {"match": "id", "matched_alias": None, "similarity": 1.0}
            for r in cur.fetchall()]


def _looks_like_code(alias: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{3,4}", alias or ""))


def _candidate_rows(cur: Any, text: str, norm: str, filter_sql: str,
                    filter_params: dict[str, Any], available: set[str]) -> list[dict[str, Any]]:
    region_join = (
        "LEFT JOIN destination_admin1 r ON r.destination_id = d.destination_id"
        if "destination_admin1" in available else ""
    )
    region_cols = (
        "r.admin1_name AS region, r.admin1_code AS region_code"
        if region_join else "NULL::text AS region, NULL::text AS region_code"
    )
    # An all-caps 3-4 letter alternate name is usually an airport/station code
    # ("SAN", "GOA"): it only counts as exact when the user typed it in capitals.
    typed_code = _looks_like_code(text.strip())
    params = {"norm": norm, "prefix": norm.replace("%", "") + "%",
              "min_sim": FUZZY_MIN_SIMILARITY, "typed_code": typed_code} | filter_params
    rows: list[dict[str, Any]] = []
    if "destination_aliases" in available:
        cur.execute(
            f"""
            WITH hits AS (
                SELECT a.destination_id, a.alias,
                       CASE WHEN a.alias_norm = %(norm)s AND a.kind <> 'alternate' THEN 4
                            WHEN a.alias_norm = %(norm)s
                                 AND (%(typed_code)s OR a.alias !~ '^[A-Z]{{3,4}}$') THEN 3
                            WHEN a.alias_norm LIKE %(prefix)s THEN 2
                            ELSE 1 END AS tier,
                       similarity(a.alias_norm, %(norm)s) AS sim
                FROM destination_aliases a
                WHERE a.alias_norm = %(norm)s
                   OR a.alias_norm LIKE %(prefix)s
                   OR a.alias_norm %% %(norm)s
            ), best AS (
                SELECT DISTINCT ON (destination_id) destination_id, alias, tier, sim
                FROM hits
                ORDER BY destination_id, tier DESC, sim DESC
            )
            SELECT d.destination_id, d.name, d.country_iso2, d.population,
                   d.timezone, d.lat, d.lon, {region_cols},
                   b.alias AS matched_alias, b.tier, b.sim
            FROM best b JOIN destinations d USING (destination_id) {region_join}
            WHERE (b.tier > 1 OR b.sim >= %(min_sim)s) {filter_sql}
            ORDER BY b.tier DESC,
                     CASE WHEN b.tier = 2 THEN d.population END DESC NULLS LAST,
                     b.sim DESC, d.population DESC
            LIMIT 50
            """,
            params,
        )
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        # No alias table, an empty one (--skip-aliases), or no alias hit:
        # fall back to the official names on destinations.
        cur.execute(
            f"""
            SELECT d.destination_id, d.name, d.country_iso2, d.population,
                   d.timezone, d.lat, d.lon, {region_cols},
                   d.name AS matched_alias,
                   CASE WHEN lower(d.name) = lower(%(raw)s) OR lower(d.ascii_name) = lower(%(raw)s)
                        THEN 4 ELSE 2 END AS tier,
                   1.0 AS sim
            FROM destinations d {region_join}
            WHERE (d.name ILIKE %(raw_prefix)s OR d.ascii_name ILIKE %(raw_prefix)s) {filter_sql}
            ORDER BY tier DESC, d.population DESC
            LIMIT 50
            """,
            params | {"raw": text, "raw_prefix": text.replace("%", "") + "%"},
        )
        rows = [dict(r) for r in cur.fetchall()]
    exact = [r for r in rows if int(r["tier"]) >= 3]
    if exact:
        # An exact name match hides weaker fuzzy noise ("new york" ~ "York").
        # Official names win unless the alternate-name city is much larger
        # ("Porto" -> Porto, not Bordeaux; "St Petersburg" -> Russia, not Florida).
        for r in exact:
            r["weight"] = int(r["population"] or 0) * (
                OFFICIAL_NAME_WEIGHT if int(r["tier"]) == 4 else 1)
        rows = sorted(exact, key=lambda r: r["weight"], reverse=True)
    return rows


def search_places(
    cur: Any,
    query: str,
    country_iso2: str | None = None,
    limit: int = 5,
    include_weight: bool = False,
) -> dict[str, Any]:
    """Resolve a free-text place name to ranked destination candidates.

    A trailing ", <qualifier>" is read as a country (code, name or common
    synonym) or a first-level region (US state code, Canadian/Australian
    abbreviation, region name). It takes priority over ``country_iso2``. If no
    loaded city matches with it, the name is searched alone and
    ``qualifier.applied`` is false.
    """
    text, qualifiers = split_qualifiers(query)
    limit = max(1, min(25, int(limit)))
    norm = normalize_name(text)
    if not norm:
        raise ValueError("query must contain a place name.")
    available = _existing_tables(
        cur, ["destination_aliases", "destination_admin1", "country_facts"]
    )

    if DESTINATION_ID_RE.match(text.strip()):
        rows = _candidate_rows_by_id(cur, text.strip(), available)
        return {"query": query, "qualifier": None, "matches": rows}

    arg_text = (country_iso2 or "").strip() or None
    arg_country = _normalize_country(cur, arg_text, available) if arg_text else None
    country_sql = "AND d.country_iso2 = %(arg_country)s"
    qualifier_info: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    if qualifiers:
        filter_sql, filter_params, countries, recognized = _combine_filters(
            cur, qualifiers, available)
        if arg_country:
            # "Richmond, CA" with country_iso2="CA": both readings of CA are
            # allowed, but the city must be in the requested country.
            rows = _candidate_rows(cur, text, norm, filter_sql + " " + country_sql,
                                   filter_params | {"arg_country": arg_country}, available)
        if not rows:
            rows = _candidate_rows(cur, text, norm, filter_sql, filter_params, available)
        qualifier_info = {"text": ", ".join(qualifiers), "country_codes": countries,
                          "recognized": recognized, "applied": bool(rows)}
        if arg_text and rows and all(r["country_iso2"] != arg_country for r in rows):
            qualifier_info["conflicts_with_country_iso2"] = arg_country or arg_text
    elif arg_text:
        # The country argument only ever means a country ("CA" is Canada).
        if arg_country:
            rows = _candidate_rows(cur, text, norm, country_sql,
                                   {"arg_country": arg_country}, available)
        qualifier_info = {"text": arg_text, "country_codes": [arg_country] if arg_country else [],
                          "recognized": bool(arg_country), "applied": bool(rows)}
    if not rows:
        # No qualifier, or nothing matched with it: search the name alone, and
        # let resolve_place ask before using a result from elsewhere.
        rows = _candidate_rows(cur, text, norm, "", {}, available)

    tier_names = {4: "exact", 3: "exact_alternate_name", 2: "prefix", 1: "fuzzy"}
    matches = [
        {
            "destination_id": r["destination_id"],
            "name": r["name"],
            "region": r.get("region"),
            "country_iso2": r["country_iso2"],
            "population": r["population"],
            "timezone": r["timezone"],
            "lat": round(float(r["lat"]), 4),
            "lon": round(float(r["lon"]), 4),
            "matched_alias": r["matched_alias"],
            "match": tier_names[int(r["tier"])],
            "similarity": round(float(r["sim"]), 2),
            **({"weight": r.get("weight")} if include_weight else {}),
        }
        for r in rows[:limit]
    ]
    return {"query": query, "qualifier": qualifier_info, "matches": matches}


def choice_reason(chosen: dict[str, Any], others: list[dict[str, Any]],
                  qualifier: dict[str, Any] | None,
                  query: str | None = None) -> str | None:
    """Why the best match is not safe to present as *the* city, or None."""
    if qualifier and qualifier.get("conflicts_with_country_iso2"):
        return (f"{qualifier['text']!r} matched outside the requested country "
                f"{qualifier['conflicts_with_country_iso2']}.")
    if qualifier and not qualifier.get("applied"):
        return (f"No loaded city (population >= 40,000) matches the name in "
                f"{qualifier['text']!r}; these matches are elsewhere.")
    if chosen["match"] == "fuzzy":
        return "The name only approximately matches a loaded city."
    if chosen["match"] == "prefix":
        return "The name is only the start of a loaded city name, not a full match."
    if chosen["match"] == "exact_alternate_name":
        official = [o for o in others if o["match"] == "exact"]
        if official:
            return (f"{official[0]['name']!r} ({official[0]['country_iso2']}) has this "
                    "exact official name; the best match only uses it as an alternate name.")
        if query and normalize_name(query) not in normalize_name(chosen.get("name")):
            return ("No loaded city has this as its exact official name; this match "
                    "uses only an alternate name.")
    if chosen["match"] == "exact":
        top = chosen.get("weight") or 0
        same_country = [o for o in others
                        if o["match"] == "exact"
                        and o.get("country_iso2") == chosen.get("country_iso2")
                        and top and (o.get("weight") or 0) >= SAME_COUNTRY_RATIO * top]
        if same_country:
            return ("Another loaded city in the same country has this exact name; "
                    "add a region or country to choose.")
    if chosen["match"] in ("exact", "exact_alternate_name"):
        top = chosen.get("weight") or 0
        rivals = [o for o in others
                  if o["match"] in ("exact", "exact_alternate_name")
                  and top and (o.get("weight") or 0) >= SIMILAR_SIZE_RATIO * top]
        if rivals:
            return "Several loaded cities of similar size share this name."
    return None


def _country_name_cities(
    cur: Any, place: str, chosen: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """If the bare name is a country ("Egypt", "Grenada", "usa"), return it and
    its largest loaded cities -- unless the match is a city whose official name
    is exactly that country's name (Singapore, Luxembourg)."""
    text, qualifiers = split_qualifiers(place)
    if qualifiers:
        return None
    available = _existing_tables(cur, ["country_facts"])
    norm = normalize_name(text)
    code = COUNTRY_SYNONYMS.get(norm) or _country_lookup(cur, available)[1].get(norm)
    if not code:
        return None
    if chosen.get("match") == "exact" and chosen.get("country_iso2") == code:
        return None
    cur.execute(
        "SELECT destination_id, name, country_iso2, population, timezone, lat, lon "
        "FROM destinations WHERE country_iso2 = %s ORDER BY population DESC LIMIT 3",
        (code,),
    )
    cities = [
        dict(r) | {"region": None, "lat": round(float(r["lat"]), 4),
                   "lon": round(float(r["lon"]), 4), "matched_alias": None,
                   "match": "in_country", "similarity": None}
        for r in cur.fetchall()
    ]
    return {"name": text, "country_iso2": code}, cities


def _same_name_region_cities(
    cur: Any, place: str, chosen: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """If the bare name is a region ("Bali") that does not contain the chosen
    city and whose largest loaded city is at least as big, return the region
    and its largest loaded cities. A big city that merely shares its name with
    a smaller region (Taipei vs. the "Taipei" region of New Taipei) is kept."""
    chosen_id = chosen["destination_id"]
    text, _qualifiers = split_qualifiers(place)
    if "destination_admin1" not in _existing_tables(cur, ["destination_admin1"]):
        return None
    cur.execute(
        """
        SELECT d.destination_id, d.name, r.admin1_name AS region, d.country_iso2,
               d.population, d.timezone, d.lat, d.lon
        FROM destination_admin1 r JOIN destinations d USING (destination_id)
        WHERE r.admin1_norm = %s
        ORDER BY d.population DESC
        """,
        (normalize_name(text),),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return None
    if chosen.get("match") == "exact":
        if _qualifiers:
            return None  # "Victoria, BC": the user already said which one
        # The typed name is this city's official name. A same-name region in
        # its own country is fine (Washington, Taipei); one elsewhere with a
        # bigger city is not (Bali, Montana).
        rows = [r for r in rows if r["country_iso2"] != chosen.get("country_iso2")]
        if not rows or any(r["destination_id"] == chosen_id for r in rows):
            return None
        if int(rows[0]["population"] or 0) < int(chosen.get("population") or 0):
            return None
    else:
        # The name reached this city only through an alternate name or a
        # partial match while it is the name of a region (Goa, Missouri,
        # Henan): the user most likely means the region -- unless the city
        # dominates that region ("New York" -> New York City).
        in_region = [r for r in rows if r["destination_id"] == chosen_id]
        rivals = [r for r in rows if r["destination_id"] != chosen_id]
        if in_region and (not rivals or int(chosen.get("population") or 0)
                          >= REGION_DOMINANCE_RATIO * int(rivals[0]["population"] or 0)):
            return None
    cities = [
        r | {"lat": round(float(r["lat"]), 4), "lon": round(float(r["lon"]), 4),
             "matched_alias": None, "match": "in_region", "similarity": None}
        for r in rows[:3]
    ]
    return {"name": rows[0]["region"], "country_iso2": rows[0]["country_iso2"]}, cities


def resolve_place(cur: Any, place: str, country_iso2: str | None = None) -> dict[str, Any]:
    """Pick one destination for ``place`` and report the alternatives honestly."""
    result = search_places(cur, place, country_iso2=country_iso2, limit=6, include_weight=True)
    matches = result["matches"]
    qualifier = result.get("qualifier")
    if not matches:
        where = f" in {qualifier['text']}" if qualifier and qualifier.get("applied") else ""
        raise ValueError(
            f"No loaded city matches {place!r}{where}"
            + ". Only cities with population >= 40,000 are loaded; try search_places."
        )
    chosen = matches[0]
    others = matches[1:]
    exact_kinds = {"exact", "exact_alternate_name", "id"}
    ambiguous = bool(others) and (
        chosen["match"] not in exact_kinds
        or any(o["match"] in exact_kinds for o in others)
    )
    reason = choice_reason(chosen, others, qualifier, query=place)
    country_cities = _country_name_cities(cur, place, chosen)
    if country_cities and reason is None:
        country, cities = country_cities
        reason = (f"{country['name']!r} is a country, not a city; ask which city "
                  "in it (largest loaded cities listed).")
        known = {m["destination_id"] for m in matches}
        others = others + [c for c in cities if c["destination_id"] not in known]
    region_cities = _same_name_region_cities(cur, place, chosen)
    if region_cities and reason is None:
        region, cities = region_cities
        reason = (f"{region['name']!r} is also a region ({region['country_iso2']}); "
                  f"the best city match is not in it.")
        known = {m["destination_id"] for m in matches}
        others = others + [c for c in cities if c["destination_id"] not in known]
    for match in matches:
        match.pop("weight", None)
    return {
        "destination_id": chosen["destination_id"],
        "resolved": chosen,
        "ambiguous": ambiguous or reason is not None,
        "needs_choice": reason is not None,
        "choice_reason": reason,
        "qualifier": qualifier,
        "other_matches": others if (ambiguous or reason) else [],
    }


def fetch_basics(cur: Any, destination_ids: list[str]) -> dict[str, dict[str, Any]]:
    has_regions = "destination_admin1" in _existing_tables(cur, ["destination_admin1"])
    region_col = "r.admin1_name" if has_regions else "NULL::text"
    region_join = (
        "LEFT JOIN destination_admin1 r ON r.destination_id = d.destination_id"
        if has_regions else ""
    )
    cur.execute(
        f"""
        SELECT d.destination_id, d.name, d.ascii_name, {region_col} AS region,
               d.country_iso2, d.timezone, d.lat, d.lon, d.population, d.iata_codes
        FROM destinations d {region_join} WHERE d.destination_id = ANY(%s)
        """,
        (destination_ids,),
    )
    out = {str(r["destination_id"]): dict(r) for r in cur.fetchall()}
    if "destination_metro" in _existing_tables(cur, ["destination_metro"]):
        cur.execute(
            """
            SELECT destination_id, urban_centre_id, urban_centre_name,
                   urban_centre_country, match_kind, match_distance_km,
                   centre_area_km2, centre_population
            FROM destination_metro
            WHERE destination_id = ANY(%s)
            """,
            (destination_ids,),
        )
        for row in cur.fetchall():
            row = dict(row)
            out[str(row.pop("destination_id"))]["urban_centre"] = row
    elif "street_network_metrics" in _existing_tables(cur, ["street_network_metrics"]):
        cur.execute(
            """
            SELECT destination_id, urban_centre_id, urban_centre_name,
                   match_kind, match_distance_km
            FROM street_network_metrics
            WHERE destination_id = ANY(%s)
            """,
            (destination_ids,),
        )
        for row in cur.fetchall():
            row = dict(row)
            out[str(row.pop("destination_id"))]["urban_centre"] = row
    return out


def build_profile(cur: Any, destination_id: str) -> dict[str, Any]:
    basics = fetch_basics(cur, [destination_id]).get(destination_id)
    if basics is None:
        raise ValueError(f"Destination {destination_id!r} not found.")
    metric_row = fetch_metric_rows(cur, [destination_id]).get(destination_id, {})
    climate = fetch_climate(cur, [destination_id]).get(destination_id, [])
    recent_climate = fetch_recent_climate(cur, [destination_id]).get(destination_id, [])
    country = fetch_country_context(cur, [basics["country_iso2"]]).get(basics["country_iso2"], {})

    groups: dict[str, list[dict[str, Any]]] = {}
    not_measured: list[str] = []
    not_loaded: list[str] = []
    has_ghsci = metric_row.get("ghsci_walkability_index") is not None
    for metric in METRICS:
        if metric.group == "walkability_ghsci" and not has_ghsci:
            continue
        entry = metric_entry(metric, metric_row)
        groups.setdefault(metric.group, []).append(entry)
        if entry["status"] == "not_loaded":
            not_loaded.append(metric.label + " (dataset not loaded for any city)")
        elif entry["status"] == "not_measured":
            not_measured.append(metric.label)
        elif entry["status"] == "none_listed":
            not_measured.append(metric.label + " (none listed; likely a map-coverage gap)")
    basics["nearest_airports_iata"] = basics.pop("iata_codes") or []
    return {
        "place": basics,
        "metrics": [
            {"group": group, "label": METRIC_GROUP_LABELS.get(group, group), "metrics": entries}
            for group, entries in groups.items()
        ],
        "climate": {
            "summary": climate_summary(_climate_for_summary(climate)),
            "months": climate,
        },
        "recent_climate": {
            "status": "loaded" if recent_climate else "not_loaded",
            "source": recent_climate[0].get("source") if recent_climate else None,
            "observed_period": recent_climate[0].get("observed_period") if recent_climate else None,
            "summary": recent_climate_summary(recent_climate),
            "months": recent_climate,
        },
        "country": country,
        "not_measured_for_this_city": not_measured,
        "not_in_database_for_any_city": NOT_IN_DATABASE + not_loaded,
        "how_to_read": HOW_TO_READ,
    }


def build_comparison(cur: Any, destination_ids: list[str]) -> dict[str, Any]:
    basics = fetch_basics(cur, destination_ids)
    ids = [i for i in destination_ids if i in basics]
    metric_rows = fetch_metric_rows(cur, ids)
    climate = fetch_climate(cur, ids)
    countries = sorted({basics[i]["country_iso2"] for i in ids})
    country_context = fetch_country_context(cur, countries)

    metrics_out: list[dict[str, Any]] = []
    not_loaded: list[str] = []
    for metric in METRICS:
        if metric.group == "walkability_ghsci" and not any(
            metric_rows.get(i, {}).get(metric.key) is not None for i in ids
        ):
            continue
        values: dict[str, Any] = {}
        comparable: dict[str, float] = {}
        for i in ids:
            entry = metric_entry(metric, metric_rows.get(i, {}))
            values[i] = {
                key: entry.get(key)
                for key in ("value", "status", "percentile_global", "band_global",
                            "reading_global", "percentile_in_country", "source",
                            "observed_period", "sample_size", "cohort",
                            "cities_with_data_global", "cities_with_data_in_country",
                            "source_url")
            }
            if entry["status"] == "measured":
                comparable[i] = float(entry["value"])
            if entry["status"] == "not_loaded" and metric.label not in not_loaded:
                not_loaded.append(metric.label)
        best = None
        # Only name a winner when every compared city has a value.
        if (metric.better and len(comparable) == len(ids) and len(ids) >= 2
                and not metric.country_only):
            pick = max if metric.better == "higher" else min
            best_value = pick(comparable.values())
            ordered = sorted(comparable.values(), reverse=metric.better == "higher")
            runner_up = ordered[1]
            # Within 5% of the runner-up is too close to call a winner.
            close = abs(best_value - runner_up) <= 0.05 * max(abs(best_value), abs(runner_up), 1e-9)
            winners = [i for i, v in comparable.items() if v == best_value]
            best = winners[0] if len(winners) == 1 and not close else None
            best_reason = (
                "measured_all" if best else "tie_or_within_5_percent"
            )
        elif not metric.better or metric.country_only:
            best_reason = "not_comparable"
        else:
            best_reason = "missing_data"
        metrics_out.append({
            "key": metric.key,
            "label": metric.label,
            "unit": metric.unit,
            "better": metric.better,
            "values": values,
            "best": best,
            "best_reason": best_reason,
            **({"note": metric.note} if metric.note else {}),
        })

    country_rows: dict[str, dict[str, Any]] = {}
    for code, ctx in country_context.items():
        country_rows[code] = {
            "name": (ctx.get("facts") or {}).get("name"),
            "currency_code": (ctx.get("facts") or {}).get("currency_code"),
            "granularity": "country",
            "indicators": {
                ind["key"]: {
                    "label": ind["label"], "value": ind["value"], "year": ind["year"],
                    "unit": ind["unit"], "better": ind["better"],
                    "percentile_among_countries": ind["percentile_among_countries"],
                    "reading_among_countries": ind["reading_among_countries"],
                    "source": ind["source"],
                }
                for ind in ctx.get("indicators", [])
            },
        }
    return {
        "places": [
            {
                "destination_id": i,
                "name": basics[i]["name"],
                "region": basics[i].get("region"),
                "country_iso2": basics[i]["country_iso2"],
                "population": basics[i]["population"],
                "nearest_airports_iata": basics[i]["iata_codes"] or [],
                "urban_centre": basics[i].get("urban_centre"),
            }
            for i in ids
        ],
        "metrics": metrics_out,
        "climate_by_month": {
            i: [
                {"month": m["month"], "avg_daily_high_c": m["avg_daily_high_c"],
                 "avg_daily_low_c": m["avg_daily_low_c"], "precip_mm": m["precip_mm"]}
                for m in climate.get(i, [])
            ]
            for i in ids
        },
        "climate_note": CLIMATE_NOTE,
        "country_context": country_rows,
        "not_in_database_for_any_city": NOT_IN_DATABASE
        + [label + " (dataset not loaded for any city)" for label in not_loaded],
        "how_to_read": HOW_TO_READ
        + " 'best' is null when values tie or are within 5%, any compared city lacks "
        "data, or the metric is in local currency.",
    }
