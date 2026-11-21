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

import re
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
    zero_is_gap: bool = False     # 0 usually means "no map coverage": not ranked


POI_NOTE = (
    "Counts what Overture Maps lists; coverage is thinner outside North America "
    "and Europe, so a low or zero count can mean missing map data."
)

TABLES = {
    "p": "poi_metrics",
    "m": "mobility_metrics",
    "b": "broadband_metrics",
    "t": "tourism_pressure",
    "h": "housing_monthly",
}

METRICS: tuple[Metric, ...] = (
    Metric("population", "d", "population", "City population (city proper, not metro)",
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
           value_sql="CASE WHEN p.restaurant_count > 0 THEN p.cuisine_entropy END"),
    Metric("pedestrian_street_share", "m", "pedestrian_ratio_2km",
           "Share of street segments within 2 km that are pedestrian/cycle paths",
           "ratio 0-1", "higher", "getting_around", "m.source", "m.observed_period",
           note="Overture Transportation; loaded only for some countries."),
    Metric("transit_stations_5km", "m", "transit_stops_5km",
           "Bus, rail and metro stations within 5 km", "stations", "higher",
           "getting_around", "m.transit_stops_source", "m.transit_stops_period",
           note="Station/facility count, not service frequency."),
    Metric("transit_timetable_feeds", "m", "gtfs_active_feeds",
           "Public-transport timetable (GTFS) feeds covering the city", "feeds",
           "higher", "getting_around", "m.source", "m.observed_period",
           note="Feed coverage, not service quality; regional feeds count too."),
    Metric("rail_segments_5km", "m", "rail_segments_5km",
           "Rail track segments within 5 km", "segments", "higher",
           "getting_around", "m.source", "m.observed_period"),
    Metric("internet_download_mbps", "b", "median_down_mbps",
           "Typical fixed-broadband download speed", "Mbps", "higher",
           "internet", "b.source", "b.observed_period"),
    Metric("internet_upload_mbps", "b", "median_up_mbps",
           "Typical fixed-broadband upload speed", "Mbps", "higher",
           "internet", "b.source", "b.observed_period"),
    Metric("internet_latency_ms", "b", "avg_latency_ms",
           "Typical fixed-broadband latency", "ms", "lower",
           "internet", "b.source", "b.observed_period"),
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
}

NOT_IN_DATABASE = [
    "Long-term residential rent (no global city-level open source loaded)",
    "City-level crime or safety (only the national homicide rate is loaded)",
    "Visa and residence rules (only an EU/Schengen 90-day flag is used in ranking)",
    "Rainy days and hot days per month (Open-Meteo/ERA5 enrichment not run)",
    "City-level air quality (only the national PM2.5 exposure is loaded)",
    "Beaches, parks, healthcare and schools",
]

HOW_TO_READ = (
    "percentile_global: 0-100 among all loaded cities that have this value; 100 is "
    "best (most for 'higher' metrics, least for 'lower' metrics; for population, "
    "largest). band_* and reading_* say the same in words, with direction. "
    "percentile_in_country is the same within the country. status 'not_measured' "
    "means the source has no value for this city; 'none_listed' means the map "
    "source lists none, which usually reflects missing coverage rather than zero. "
    "Country indicators are national values, not city measurements; quote their year."
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
        k = metric.key
        base_cols += [
            f"{value} AS {k}",
            f"{raw} AS {k}__raw",
            f"({source})::text AS {k}__source",
            f"({period})::text AS {k}__period",
        ]
        order = "DESC" if metric.better == "lower" else "ASC"
        global_pct = (
            "NULL::double precision" if metric.country_only else
            f"CASE WHEN {k} IS NULL THEN NULL ELSE percent_rank() OVER "
            f"(PARTITION BY ({k} IS NULL) ORDER BY {k} {order}) END"
        )
        ranked_cols += [
            k, f"{k}__raw", f"{k}__source", f"{k}__period",
            f"{global_pct} AS {k}__pct_global",
            f"CASE WHEN {k} IS NULL THEN NULL ELSE percent_rank() OVER "
            f"(PARTITION BY country_iso2, ({k} IS NULL) ORDER BY {k} {order}) END AS {k}__pct_country",
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


def percentile_band(pct: float | None, better: str | None = "higher") -> str | None:
    """Plain label for a 0-100 percentile where 100 is best (or largest)."""
    if pct is None:
        return None
    if better is None:
        labels = ("largest 10%", "largest 25%", "larger than most",
                  "smaller than most", "smallest 25%")
    else:
        labels = ("best 10%", "best 25%", "better than most",
                  "worse than most", "worst 25%")
    for threshold, label in zip((90, 75, 50, 25), labels):
        if pct >= threshold:
            return label
    return labels[-1]


def percentile_reading(pct: float | None, better: str | None, n: int, of: str) -> str | None:
    """'lower than 70% of 200 countries with data' -- direction spelled out."""
    if pct is None or n < 2:
        return None
    word = {"higher": "higher", "lower": "lower"}.get(better or "", "larger")
    return f"{word} than {pct:.0f}% of the {n:,} {of} with data"


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
    }
    if value is None and metric.zero_is_gap and row.get(f"{k}__raw") == 0:
        entry.update({
            "value": 0,
            "status": "none_listed",
            "source": row.get(f"{k}__source"),
            "observed_period": row.get(f"{k}__period"),
        })
    if value is not None:
        pct_global = _pct(row.get(f"{k}__pct_global"))
        pct_country = _pct(row.get(f"{k}__pct_country"))
        n_global = int(row.get(f"{k}__n_global") or 0)
        n_country = int(row.get(f"{k}__n_country") or 0)
        entry.update({
            "percentile_global": pct_global,
            "band_global": percentile_band(pct_global, metric.better),
            "reading_global": percentile_reading(pct_global, metric.better, n_global, "cities"),
            "cities_with_data_global": n_global,
            "percentile_in_country": pct_country if n_country > 1 else None,
            "cities_with_data_in_country": n_country,
            "source": row.get(f"{k}__source"),
            "observed_period": row.get(f"{k}__period"),
        })
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
        }
    if "country_indicators" in available:
        cur.execute(
            """
            WITH r AS (
                SELECT country_iso2, indicator, value, year, label, unit, better,
                       source, source_indicator,
                       CASE WHEN better = 'lower'
                            THEN percent_rank() OVER (PARTITION BY indicator ORDER BY value DESC)
                            ELSE percent_rank() OVER (PARTITION BY indicator ORDER BY value)
                       END AS pct,
                       count(*) OVER (PARTITION BY indicator) AS n
                FROM country_indicators
            )
            SELECT * FROM r WHERE country_iso2 = ANY(%s) ORDER BY indicator
            """,
            (country_codes,),
        )
        for row in cur.fetchall():
            code = str(row["country_iso2"])
            if code not in out or "indicators" not in out[code]:
                continue
            pct = _pct(row["pct"])
            out[code]["indicators"].append({
                "key": row["indicator"],
                "label": row["label"],
                "unit": row["unit"],
                "better": row["better"],
                "value": round(float(row["value"]), 3),
                "year": int(row["year"]),
                "percentile_among_countries": pct,
                "band_among_countries": percentile_band(pct, row["better"]),
                "reading_among_countries": percentile_reading(
                    pct, row["better"], int(row["n"]), "countries"),
                "countries_with_data": int(row["n"]),
                "source": row["source"],
                "source_indicator": row["source_indicator"],
            })
    return out


COUNTRY_SYNONYMS = {
    "uk": "GB", "u k": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "great britain": "GB", "britain": "GB", "usa": "US", "u s a": "US", "u s": "US",
    "america": "US", "united states of america": "US", "holland": "NL",
    "czechia": "CZ", "south korea": "KR", "korea": "KR", "russia": "RU",
    "turkiye": "TR", "uae": "AE", "ivory coast": "CI", "vietnam": "VN",
}
OFFICIAL_NAME_WEIGHT = 5  # an exact official-name match beats an alternate name
                          # unless the alternate-name city is > 5x larger


def split_qualifier(query: str) -> tuple[str, str | None]:
    """'Porto, Portugal' -> ('Porto', 'Portugal'); 'Austin, TX' -> ('Austin', 'TX')."""
    text = (query or "").strip()
    if "," not in text:
        return text, None
    head, _, tail = text.rpartition(",")
    head, tail = head.strip(), tail.strip()
    if not head or not tail or len(tail) > 60:
        return text, None
    return head, tail


def split_country_suffix(query: str) -> tuple[str, str | None]:
    """Backward-compatible helper: split only a trailing two-letter code."""
    head, tail = split_qualifier(query)
    if tail and len(tail) == 2 and tail.isalpha():
        return head, tail.upper()
    return (query or "").strip(), None


def _qualifier_filter(cur: Any, qualifier: str, available: set[str]) -> tuple[str, dict[str, Any], list[str]]:
    """SQL filter for a ', <country or region>' qualifier, plus matched country codes."""
    q_norm = normalize_name(qualifier)
    q_code = re.sub(r"[^A-Za-z]", "", qualifier).upper()
    countries: set[str] = set()
    if q_norm in COUNTRY_SYNONYMS:
        countries.add(COUNTRY_SYNONYMS[q_norm])
    if len(q_code) == 2 and len(qualifier.strip()) <= 3:
        countries.add("GB" if q_code == "UK" else q_code)
    if "country_facts" in available:
        cur.execute("SELECT country_iso2, name FROM country_facts")
        for row in cur.fetchall():
            if normalize_name(row["name"]) == q_norm:
                countries.add(str(row["country_iso2"]))
    clauses = ["d.country_iso2 = ANY(%(q_countries)s)"]
    params: dict[str, Any] = {"q_countries": sorted(countries)}
    if "destination_admin1" in available:
        clauses.append(
            "d.destination_id IN (SELECT destination_id FROM destination_admin1 "
            "WHERE admin1_norm = %(q_norm)s OR (%(q_code)s <> '' AND admin1_code = %(q_code)s))"
        )
        params |= {"q_norm": q_norm, "q_code": q_code if 2 <= len(q_code) <= 3 else ""}
    return "AND (" + " OR ".join(clauses) + ")", params, sorted(countries)


def _candidate_rows(cur: Any, text: str, norm: str, filter_sql: str,
                    filter_params: dict[str, Any], has_aliases: bool) -> list[dict[str, Any]]:
    params = {"norm": norm, "prefix": norm.replace("%", "") + "%",
              "min_sim": FUZZY_MIN_SIMILARITY} | filter_params
    if has_aliases:
        cur.execute(
            f"""
            WITH hits AS (
                SELECT a.destination_id, a.alias,
                       CASE WHEN a.alias_norm = %(norm)s AND a.kind <> 'alternate' THEN 4
                            WHEN a.alias_norm = %(norm)s THEN 3
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
                   d.timezone, d.lat, d.lon,
                   b.alias AS matched_alias, b.tier, b.sim
            FROM best b JOIN destinations d USING (destination_id)
            WHERE (b.tier > 1 OR b.sim >= %(min_sim)s) {filter_sql}
            ORDER BY b.tier DESC, b.sim DESC, d.population DESC
            LIMIT 50
            """,
            params,
        )
    else:
        cur.execute(
            f"""
            SELECT d.destination_id, d.name, d.country_iso2, d.population,
                   d.timezone, d.lat, d.lon,
                   d.name AS matched_alias,
                   CASE WHEN lower(d.name) = lower(%(raw)s) OR lower(d.ascii_name) = lower(%(raw)s)
                        THEN 4 ELSE 2 END AS tier,
                   1.0 AS sim
            FROM destinations d
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
        rows = sorted(
            exact,
            key=lambda r: (int(r["population"] or 0)
                           * (OFFICIAL_NAME_WEIGHT if int(r["tier"]) == 4 else 1)),
            reverse=True,
        )
    return rows


def search_places(
    cur: Any,
    query: str,
    country_iso2: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Resolve a free-text place name to ranked destination candidates.

    A trailing ", <qualifier>" is read as a country (code, name or common
    synonym) or a first-level region (US state code, province name). It takes
    priority over ``country_iso2``. If the qualifier matches nothing, the
    search is retried without it and ``qualifier.applied`` is false.
    """
    text, qualifier = split_qualifier(query)
    limit = max(1, min(25, int(limit)))
    norm = normalize_name(text)
    if not norm:
        raise ValueError("query must contain a place name.")

    if DESTINATION_ID_RE.match(text.strip()):
        cur.execute(
            "SELECT destination_id, name, country_iso2, population, timezone, lat, lon "
            "FROM destinations WHERE destination_id = %s",
            (text.strip(),),
        )
        rows = [dict(r) | {"match": "id", "matched_alias": None, "similarity": 1.0}
                for r in cur.fetchall()]
        return {"query": query, "qualifier": None, "matches": rows}

    available = _existing_tables(
        cur, ["destination_aliases", "destination_admin1", "country_facts"]
    )
    has_aliases = "destination_aliases" in available
    qualifier_info: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    if qualifier:
        filter_sql, filter_params, countries = _qualifier_filter(cur, qualifier, available)
        rows = _candidate_rows(cur, text, norm, filter_sql, filter_params, has_aliases)
        qualifier_info = {"text": qualifier, "country_codes": countries, "applied": bool(rows)}
        if not rows:
            # Not a country/region we know, or no city there: search the name alone.
            rows = _candidate_rows(cur, text, norm, "", {}, has_aliases)
    else:
        country = (country_iso2 or "").strip().upper() or None
        country_sql = "AND d.country_iso2 = %(country)s" if country else ""
        rows = _candidate_rows(cur, text, norm, country_sql, {"country": country}, has_aliases)
        if country:
            qualifier_info = {"text": country, "country_codes": [country], "applied": True}

    tier_names = {4: "exact", 3: "exact_alternate_name", 2: "prefix", 1: "fuzzy"}
    matches = [
        {
            "destination_id": r["destination_id"],
            "name": r["name"],
            "country_iso2": r["country_iso2"],
            "population": r["population"],
            "timezone": r["timezone"],
            "lat": round(float(r["lat"]), 4),
            "lon": round(float(r["lon"]), 4),
            "matched_alias": r["matched_alias"],
            "match": tier_names[int(r["tier"])],
            "similarity": round(float(r["sim"]), 2),
        }
        for r in rows[:limit]
    ]
    return {"query": query, "qualifier": qualifier_info, "matches": matches}


def resolve_place(cur: Any, place: str, country_iso2: str | None = None) -> dict[str, Any]:
    """Pick one destination for ``place`` and report the alternatives honestly."""
    result = search_places(cur, place, country_iso2=country_iso2, limit=6)
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
    # A best guess that is only a fuzzy match is not safe to describe as
    # "the" city: callers must ask the user to confirm or pick.
    needs_choice = chosen["match"] == "fuzzy"
    return {
        "destination_id": chosen["destination_id"],
        "resolved": chosen,
        "ambiguous": ambiguous,
        "needs_choice": needs_choice,
        "qualifier": qualifier,
        "other_matches": others if ambiguous else [],
    }


def fetch_basics(cur: Any, destination_ids: list[str]) -> dict[str, dict[str, Any]]:
    cur.execute(
        """
        SELECT destination_id, name, ascii_name, country_iso2, timezone, lat, lon,
               population, iata_codes
        FROM destinations WHERE destination_id = ANY(%s)
        """,
        (destination_ids,),
    )
    return {str(r["destination_id"]): dict(r) for r in cur.fetchall()}


def build_profile(cur: Any, destination_id: str) -> dict[str, Any]:
    basics = fetch_basics(cur, [destination_id]).get(destination_id)
    if basics is None:
        raise ValueError(f"Destination {destination_id!r} not found.")
    metric_row = fetch_metric_rows(cur, [destination_id]).get(destination_id, {})
    climate = fetch_climate(cur, [destination_id]).get(destination_id, [])
    country = fetch_country_context(cur, [basics["country_iso2"]]).get(basics["country_iso2"], {})

    groups: dict[str, list[dict[str, Any]]] = {}
    not_measured: list[str] = []
    for metric in METRICS:
        entry = metric_entry(metric, metric_row)
        groups.setdefault(metric.group, []).append(entry)
        if entry["status"] == "not_measured":
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
        "country": country,
        "not_measured_for_this_city": not_measured,
        "not_in_database_for_any_city": NOT_IN_DATABASE,
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
    for metric in METRICS:
        values: dict[str, Any] = {}
        comparable: dict[str, float] = {}
        for i in ids:
            entry = metric_entry(metric, metric_rows.get(i, {}))
            values[i] = {
                "value": entry["value"],
                "status": entry["status"],
                "percentile_global": entry.get("percentile_global"),
                "percentile_in_country": entry.get("percentile_in_country"),
            }
            if entry["status"] == "measured":
                comparable[i] = float(entry["value"])
        best = None
        # Only name a winner when every compared city has a value.
        if (metric.better and len(comparable) == len(ids) and len(ids) >= 2
                and not metric.country_only):
            pick = max if metric.better == "higher" else min
            best_value = pick(comparable.values())
            winners = [i for i, v in comparable.items() if v == best_value]
            best = winners[0] if len(winners) == 1 else None
        metrics_out.append({
            "key": metric.key,
            "label": metric.label,
            "unit": metric.unit,
            "better": metric.better,
            "values": values,
            "best": best,
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
                "country_iso2": basics[i]["country_iso2"],
                "population": basics[i]["population"],
                "nearest_airports_iata": basics[i]["iata_codes"] or [],
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
        "not_in_database_for_any_city": NOT_IN_DATABASE,
        "how_to_read": HOW_TO_READ
        + " 'best' is null when values tie, any compared city lacks data, or the "
        "metric is in local currency.",
    }
