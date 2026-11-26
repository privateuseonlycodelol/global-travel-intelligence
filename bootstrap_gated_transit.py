#!/usr/bin/env python3
"""
Gated public transport (Council pillar 10, 6%) and Taiwan national indicators.

Public transport is deliberately *gated*, not imputed:

* **Tier 1 — local GTFS frequency (preferred).** A destination receives a
  score only when it has a weekday 07:00-09:00 GTFS headway from a local
  operator with at least 50 mapped stops. Intercity coach agencies are
  excluded with :data:`INTERCITY_COACH_RE`; their sparse long-distance
  timetables are not a city bus network. The score curve is fixed:
  100 at <=5 minutes, linear 100 -> 50 between 5 and 15 minutes, linear
  50 -> 0 between 15 and 45 minutes, 0 at >=45 minutes.

* **Tier 2 — road-gated guideway/access fallback.** Only GHSL centres with
  at least 300,000 residents may use it. The country must pass the
  road-completeness gate from ``urban_centre_form.road_km_per_built_km2``
  (country median >= :data:`ROAD_COMPLETENESS_MIN_KM_PER_BUILT_KM2`). When
  that table is not installed yet, the gate falls back to requiring an
  observed ``mobility_metrics.rail_segments_5km`` value. At least one of
  ``rail_segments_5km`` / ``transit_stops_5km`` must be observed. The score
  uses fixed absolute log anchors (:data:`RAIL_ANCHOR_SEGMENTS`,
  :data:`TRANSIT_STOP_ANCHOR`), never a cohort percentile.

* **No imputation.** If the centre is smaller than 300,000, the gate fails,
  or mobility is unobserved, ``transit_score`` stays NULL and the row is
  labelled ``unobserved_small_centre`` or ``unobserved_gate_failed``.

The same script also supplies official Taiwan (TW) national statistics to
``country_indicators``. The World Bank/WHO series omit Taiwan, which left all
39 Taiwan destinations without homicide, WASH, or GDP-per-capita values.
These are published national figures, not World Bank/UNODC/WHO estimates;
their ``source`` and ``source_indicator`` make that distinction explicit.

Usage
-----
    python bootstrap_gated_transit.py --db-url "$SUPABASE_DB_URL"
    python bootstrap_gated_transit.py --taiwan-only
    python bootstrap_gated_transit.py --report

Requires ``psycopg`` and an installed ``destinations`` table. All SQL is
read-only until the final replace of ``destination_gated_transit``; the lead
runs this loader after integration.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import psycopg
from psycopg.rows import dict_row

TABLE = "destination_gated_transit"

MIN_LOCAL_GTFS_STOPS = 50
TIER2_MIN_CENTRE_POPULATION = 300_000

# Fixed absolute anchors (no cohort percentiles).
HEADWAY_BEST_MIN = 5.0
HEADWAY_GOOD_MIN = 15.0
HEADWAY_WORST_MIN = 45.0
RAIL_ANCHOR_SEGMENTS = 500.0
TRANSIT_STOP_ANCHOR = 150.0
RAIL_ANCHOR_WEIGHT = 0.70
TRANSIT_STOP_ANCHOR_WEIGHT = 0.30

# A country median below this many road-km per built km2 is treated as an
# incomplete OSM road network, so its rail/stop proxies are not trusted.
ROAD_COMPLETENESS_MIN_KM_PER_BUILT_KM2 = 5.0

TRANSIT_METHOD_GTFS = "gtfs_local_headway"
TRANSIT_METHOD_GUIDEWAY = "road_gated_guideway_anchor"
TRANSIT_METHOD_GATE_FAILED = "unobserved_gate_failed"
TRANSIT_METHOD_SMALL_CENTRE = "unobserved_small_centre"

GTFS_SOURCE = (
    "Mobility Database GTFS weekday peak headway "
    "(intercity coaches excluded; >=50 mapped stops)"
)
GUIDEWAY_SOURCE = (
    "Overture Maps rail segments + public-transport stops "
    "(road-gated fallback; no local GTFS headway)"
)
GATE_FAILED_SOURCE = (
    "No local GTFS headway; road-completeness gate or mobility observation missing"
)
SMALL_CENTRE_SOURCE = (
    "No local GTFS headway; GHSL centre below 300,000 population"
)

# Intercity coach agencies whose long-distance timetables must never be read
# as local city service. Word boundaries keep "ALSA" from matching "Alsace"
# (Mulhouse Alsace Agglomération is a legitimate local network).
INTERCITY_COACH_RE = re.compile(
    r"""
    \bflix(?:bus)?\b
    | \bblabla\s*car\b
    | \bblablabus\b
    | \bgreyhound\b
    | \beurolines\b
    | \bmegabus\b
    | \bnational\s+express\b
    | \bouibus\b
    | \bregiojet\b
    | \bsindbad\b
    | \balsa\b
    | \bbolt\s*bus\b
    | \bflixtrain\b
    | \bstudent\s+agency\b
    | \bpeter\s+pan\b
    | \btrailways\b
    | \bourbus\b
    | \bredcoach\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS destination_gated_transit (
    destination_id               TEXT PRIMARY KEY
                                 REFERENCES destinations(destination_id) ON DELETE CASCADE,
    urban_centre_id              BIGINT,
    local_gtfs_headway_min       DOUBLE PRECISION,
    local_gtfs_stops             INT,
    rail_segments_5km            INT,
    transit_stops_5km            INT,
    country_road_completeness_ok BOOLEAN,
    transit_score                DOUBLE PRECISION,
    transit_method               TEXT NOT NULL,
    source                       TEXT NOT NULL,
    observed_period              TEXT NOT NULL,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_destination_gated_transit_uc
    ON destination_gated_transit (urban_centre_id);
"""

INSERT_SQL = """
INSERT INTO destination_gated_transit (
    destination_id, urban_centre_id, local_gtfs_headway_min, local_gtfs_stops,
    rail_segments_5km, transit_stops_5km, country_road_completeness_ok,
    transit_score, transit_method, source, observed_period, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
"""

TAIWAN_UPSERT_SQL = """
INSERT INTO country_indicators (
    country_iso2, indicator, value, year, label, unit, better,
    source, source_indicator
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (country_iso2, indicator) DO UPDATE SET
    value = EXCLUDED.value,
    year = EXCLUDED.year,
    label = EXCLUDED.label,
    unit = EXCLUDED.unit,
    better = EXCLUDED.better,
    source = EXCLUDED.source,
    source_indicator = EXCLUDED.source_indicator,
    updated_at = NOW()
"""


@dataclass(frozen=True)
class TaiwanIndicator:
    indicator: str
    value: float
    year: int
    label: str
    unit: str
    better: str
    source: str
    source_indicator: str


# Official Taiwan R.O.C. national statistics. Values are national, not a
# World Bank/WHO estimate, so their source and source_indicator are explicit.
TAIWAN_INDICATORS: tuple[TaiwanIndicator, ...] = (
    TaiwanIndicator(
        indicator="homicide_per_100k",
        value=0.54,
        year=2023,
        label="Intentional homicides per 100,000 people (Taiwan national statistics)",
        unit="per 100k people",
        better="lower",
        source="Taiwan Ministry of the Interior / National Police Agency",
        source_indicator="2023 recorded intentional homicide rate",
    ),
    TaiwanIndicator(
        indicator="safely_managed_water_pct",
        value=95.3,
        year=2023,
        label="People using safely managed drinking water services (Taiwan national statistics)",
        unit="% of population",
        better="higher",
        source="Taiwan Water Corporation / Taiwan EPA",
        source_indicator="2023 tap water supply coverage",
    ),
    TaiwanIndicator(
        indicator="safely_managed_sanitation_pct",
        value=72.1,
        year=2023,
        label="People using safely managed sanitation services (Taiwan national statistics)",
        unit="% of population",
        better="higher",
        source="Taiwan Construction and Planning Agency / NLMA",
        source_indicator="2023 sanitary sewerage and treatment rate",
    ),
    TaiwanIndicator(
        indicator="gdp_per_capita_ppp",
        value=73344.0,
        year=2023,
        label="GDP per capita, PPP (Taiwan national statistics)",
        unit="current international $",
        better="higher",
        source="IMF WEO / Taiwan DGBAS",
        source_indicator="2023 GDP per capita, purchasing power parity",
    ),
)


def log(message: str) -> None:
    print(message, flush=True)


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def is_intercity_coach(provider: str | None, feed_name: str | None = None) -> bool:
    """Return True for intercity coach agencies excluded from local service."""
    haystack = " ".join(part for part in (provider, feed_name) if part)
    return bool(INTERCITY_COACH_RE.search(haystack))


def headway_transit_score(headway_min: float | None) -> float | None:
    """Fixed headway curve: 100 <=5 min, 50 @15 min, 0 >=45 min.

    The curve is continuous and linear in headway inside each segment:
    100 -> 50 over 5-15 minutes and 50 -> 0 over 15-45 minutes.
    """
    if headway_min is None:
        return None
    try:
        headway = float(headway_min)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(headway) or headway < 0:
        return None
    if headway <= HEADWAY_BEST_MIN:
        return 100.0
    if headway <= HEADWAY_GOOD_MIN:
        return _clamp(100.0 - (headway - HEADWAY_BEST_MIN) * 5.0)
    if headway < HEADWAY_WORST_MIN:
        return _clamp(
            50.0
            - (headway - HEADWAY_GOOD_MIN)
            * (50.0 / (HEADWAY_WORST_MIN - HEADWAY_GOOD_MIN))
        )
    return 0.0


def _log_anchor(value: float | int | None, anchor: float) -> float | None:
    """Map a non-negative count to 0-100 with a fixed logarithmic cap."""
    if value is None:
        return None
    try:
        count = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(count) or count < 0:
        return None
    return _clamp(100.0 * math.log1p(count) / math.log1p(anchor))


def guideway_access_score(
    rail_segments_5km: float | int | None,
    transit_stops_5km: float | int | None,
) -> float | None:
    """Fixed absolute log anchor for road-gated rail guideway and stop access.

    Rail guideway is weighted 70% and stop access 30%. If only one metric is
    observed, its available weight is renormalised to 100%; missing metrics
    are never imputed. Observed zeros score 0, not NULL.
    """
    parts: list[tuple[float, float]] = []
    rail_score = _log_anchor(rail_segments_5km, RAIL_ANCHOR_SEGMENTS)
    if rail_score is not None:
        parts.append((rail_score, RAIL_ANCHOR_WEIGHT))
    stop_score = _log_anchor(transit_stops_5km, TRANSIT_STOP_ANCHOR)
    if stop_score is not None:
        parts.append((stop_score, TRANSIT_STOP_ANCHOR_WEIGHT))
    if not parts:
        return None
    total_weight = sum(weight for _, weight in parts)
    return sum(score * weight for score, weight in parts) / total_weight


def compute_country_road_gate(
    rows: Iterable[Mapping[str, Any]],
    threshold: float = ROAD_COMPLETENESS_MIN_KM_PER_BUILT_KM2,
) -> dict[int, bool]:
    """Map every urban centre id to whether its country clears the road gate.

    ``rows`` need ``urban_centre_id``, ``country`` and
    ``road_km_per_built_km2``. Country medians use only non-null road values;
    centres with a null road value still inherit their country's gate result.
    """
    values_by_country: dict[str, list[float]] = defaultdict(list)
    centre_country: dict[int, str] = {}
    for row in rows:
        centre_id = row.get("urban_centre_id")
        country = row.get("country")
        if centre_id is None or country is None:
            continue
        country_key = str(country)
        centre_country[int(centre_id)] = country_key
        value = row.get("road_km_per_built_km2")
        if value is None:
            continue
        try:
            values_by_country[country_key].append(float(value))
        except (TypeError, ValueError):
            continue
    medians = {
        country: statistics.median(values)
        for country, values in values_by_country.items()
        if values
    }
    return {
        centre_id: medians[country] >= threshold
        for centre_id, country in centre_country.items()
        if country in medians
    }


def road_gate_status(
    urban_centre_id: int | None,
    rail_segments_5km: int | None,
    road_gate_map: Mapping[int, bool],
) -> bool:
    """Resolve the country road-completeness gate for one destination.

    When ``urban_centre_form`` is not loaded (empty map), the documented
    fallback applies: an observed OSM rail guideway is evidence that the road
    network is good enough to trust the mobility proxy.
    """
    if road_gate_map:
        if urban_centre_id is None:
            return False
        return bool(road_gate_map.get(int(urban_centre_id), False))
    return rail_segments_5km is not None


def tier2_transit_score(
    centre_population: int | None,
    rail_segments_5km: int | None,
    transit_stops_5km: int | None,
    country_road_gate_ok: bool | None,
) -> tuple[float | None, str]:
    """Return (score, method) for the gated guideway/access fallback.

    The fallback is restricted to GHSL centres >=300,000 population. It never
    imputes: a failed gate, missing population, or absent mobility observation
    all return a NULL score with an explicit unobserved method.
    """
    if (
        centre_population is None
        or int(centre_population) < TIER2_MIN_CENTRE_POPULATION
    ):
        return None, TRANSIT_METHOD_SMALL_CENTRE
    has_mobility = (
        rail_segments_5km is not None or transit_stops_5km is not None
    )
    if not country_road_gate_ok or not has_mobility:
        return None, TRANSIT_METHOD_GATE_FAILED
    score = guideway_access_score(rail_segments_5km, transit_stops_5km)
    if score is None:
        return None, TRANSIT_METHOD_GATE_FAILED
    return score, TRANSIT_METHOD_GUIDEWAY


def select_local_headway(
    rows: Iterable[Mapping[str, Any]],
    min_stops: int = MIN_LOCAL_GTFS_STOPS,
) -> dict[str, Any] | None:
    """Pick the best-covered non-coach weekday peak GTFS row for one city.

    Coach agencies are excluded before the stop threshold is applied. Like
    the existing ``transit_city_service`` view, the best-covered feed wins
    (most mapped stops), with the urban-centre geography band preferred and
    the lowest headway used as a tie-break.
    """
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if is_intercity_coach(row.get("provider"), row.get("feed_name")):
            continue
        stops = row.get("stops_mapped")
        if stops is None:
            stops = row.get("stops")
        if stops is None or int(stops) < min_stops:
            continue
        headway = row.get("median_headway_min")
        if headway is None:
            continue
        candidates.append({
            "destination_id": row.get("destination_id"),
            "feed_id": row.get("feed_id"),
            "radius_rule": row.get("radius_rule"),
            "local_gtfs_headway_min": float(headway),
            "local_gtfs_stops": int(stops),
            "service_date": row.get("service_date"),
        })
    if not candidates:
        return None
    candidates.sort(
        key=lambda candidate: (
            0 if candidate["radius_rule"] == "urban_centre" else 1,
            -candidate["local_gtfs_stops"],
            candidate["local_gtfs_headway_min"],
            str(candidate["feed_id"] or ""),
        )
    )
    return candidates[0]


def build_transit_row(
    destination_id: str,
    centre: Mapping[str, Any] | None,
    gtfs_row: Mapping[str, Any] | None,
    mobility: Mapping[str, Any] | None,
    road_gate_map: Mapping[int, bool],
) -> dict[str, Any] | None:
    """Build one destination row without touching the database.

    Returns None when the destination is neither a GHSL centre nor covered by
    local GTFS, i.e. it is outside the gated-transit universe.
    """
    urban_centre_id = centre.get("urban_centre_id") if centre else None
    centre_population = centre.get("centre_population") if centre else None
    rail_segments = mobility.get("rail_segments_5km") if mobility else None
    transit_stops = mobility.get("transit_stops_5km") if mobility else None

    if gtfs_row is None and urban_centre_id is None:
        return None

    gate_ok: bool | None
    if urban_centre_id is None:
        gate_ok = None
    else:
        gate_ok = road_gate_status(
            urban_centre_id, rail_segments, road_gate_map
        )

    if gtfs_row is not None:
        score = headway_transit_score(gtfs_row.get("local_gtfs_headway_min"))
        method = TRANSIT_METHOD_GTFS
        headway = gtfs_row.get("local_gtfs_headway_min")
        local_stops = gtfs_row.get("local_gtfs_stops")
        source = GTFS_SOURCE
        service_date = gtfs_row.get("service_date")
        observed_period = (
            f"gtfs-{service_date}" if service_date is not None else "gtfs"
        )
    else:
        score, method = tier2_transit_score(
            centre_population, rail_segments, transit_stops, gate_ok
        )
        headway = None
        local_stops = None
        source = {
            TRANSIT_METHOD_GUIDEWAY: GUIDEWAY_SOURCE,
            TRANSIT_METHOD_GATE_FAILED: GATE_FAILED_SOURCE,
            TRANSIT_METHOD_SMALL_CENTRE: SMALL_CENTRE_SOURCE,
        }[method]
        observed_period = (
            str(mobility.get("observed_period"))
            if mobility and mobility.get("observed_period")
            else "not observed"
        )

    return {
        "destination_id": destination_id,
        "urban_centre_id": urban_centre_id,
        "local_gtfs_headway_min": headway,
        "local_gtfs_stops": local_stops,
        "rail_segments_5km": rail_segments,
        "transit_stops_5km": transit_stops,
        "country_road_completeness_ok": gate_ok,
        "transit_score": score,
        "transit_method": method,
        "source": source,
        "observed_period": observed_period,
    }


def build_transit_rows(
    centres: Mapping[str, Mapping[str, Any]],
    gtfs_rows: Iterable[Mapping[str, Any]],
    mobility_by_destination: Mapping[str, Mapping[str, Any]],
    road_gate_map: Mapping[int, bool],
) -> list[dict[str, Any]]:
    """Build deterministic rows for every destination in the gated universe."""
    gtfs_by_destination: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in gtfs_rows:
        destination_id = row.get("destination_id")
        if destination_id is not None:
            gtfs_by_destination[str(destination_id)].append(row)

    output: list[dict[str, Any]] = []
    for destination_id in sorted(centres):
        gtfs_row = select_local_headway(
            gtfs_by_destination.get(destination_id, ())
        )
        row = build_transit_row(
            destination_id,
            centres[destination_id],
            gtfs_row,
            mobility_by_destination.get(destination_id),
            road_gate_map,
        )
        if row is not None:
            output.append(row)
    return output


def _fetch_centres(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.destination_id, m.urban_centre_id, m.centre_population
            FROM destinations d
            LEFT JOIN destination_metro m USING (destination_id)
            """
        )
        return {
            str(row["destination_id"]): {
                "urban_centre_id": row["urban_centre_id"],
                "centre_population": row["centre_population"],
            }
            for row in cur.fetchall()
        }


def _fetch_gtfs_rows(conn: psycopg.Connection) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT th.destination_id,
                   th.feed_id,
                   th.radius_rule,
                   th.median_headway_min,
                   th.stops,
                   th.service_date,
                   gf.provider,
                   gf.feed_name,
                   gfp.stops_mapped
            FROM transit_headway th
            JOIN gtfs_feed gf ON gf.feed_id = th.feed_id
            LEFT JOIN gtfs_feed_place gfp
              ON gfp.feed_id = th.feed_id
             AND gfp.destination_id = th.destination_id
            WHERE th.day_type = 'weekday'
              AND th.time_band = 'peak'
              AND th.median_headway_min IS NOT NULL
            """
        )
        return [dict(row) for row in cur.fetchall()]


def _fetch_mobility(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT destination_id, rail_segments_5km, transit_stops_5km,
                   source, observed_period
            FROM mobility_metrics
            """
        )
        return {
            str(row["destination_id"]): dict(row)
            for row in cur.fetchall()
        }


def load_road_gate_map(
    conn: psycopg.Connection,
    threshold: float = ROAD_COMPLETENESS_MIN_KM_PER_BUILT_KM2,
) -> dict[int, bool]:
    """Read ``urban_centre_form`` if present and compute the country gate.

    Returns an empty map when the table (or its road column) is not installed;
    callers then use the observed-rail fallback. An installed-but-empty table
    also returns an empty map so a loader-order race never fails every city.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.urban_centre_form') AS relation")
        if cur.fetchone()["relation"] is None:
            return {}
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'urban_centre_form'
            """
        )
        columns = {row["column_name"] for row in cur.fetchall()}
        if (
            "road_km_per_built_km2" not in columns
            or "urban_centre_id" not in columns
        ):
            return {}
        if "country_iso2" in columns:
            country_expression = "f.country_iso2"
            join_sql = ""
        elif "country" in columns:
            country_expression = "f.country"
            join_sql = ""
        else:
            country_expression = "uc.country"
            join_sql = (
                "JOIN ghsl_urban_centres uc "
                "ON uc.urban_centre_id = f.urban_centre_id"
            )
        cur.execute(
            f"""
            SELECT f.urban_centre_id, {country_expression} AS country,
                   f.road_km_per_built_km2
            FROM urban_centre_form f
            {join_sql}
            WHERE {country_expression} IS NOT NULL
            """
        )
        rows = [dict(row) for row in cur.fetchall()]
    return compute_country_road_gate(rows, threshold)


def load_transit_scores(conn: psycopg.Connection) -> int:
    """Replace ``destination_gated_transit`` with a freshly gated score set."""
    centres = _fetch_centres(conn)
    gtfs_rows = _fetch_gtfs_rows(conn)
    mobility = _fetch_mobility(conn)
    road_gate_map = load_road_gate_map(conn)
    rows = build_transit_rows(centres, gtfs_rows, mobility, road_gate_map)

    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TABLE}")
        cur.executemany(
            INSERT_SQL,
            [
                (
                    row["destination_id"],
                    row["urban_centre_id"],
                    row["local_gtfs_headway_min"],
                    row["local_gtfs_stops"],
                    row["rail_segments_5km"],
                    row["transit_stops_5km"],
                    row["country_road_completeness_ok"],
                    row["transit_score"],
                    row["transit_method"],
                    row["source"],
                    row["observed_period"],
                )
                for row in rows
            ],
        )
    conn.commit()
    log(f"  wrote {len(rows):,} rows to {TABLE}.")
    return len(rows)


def taiwan_indicator_rows() -> list[tuple[Any, ...]]:
    """Return ``country_indicators`` tuples for the four Taiwan statistics."""
    return [
        (
            "TW",
            item.indicator,
            item.value,
            item.year,
            item.label,
            item.unit,
            item.better,
            item.source,
            item.source_indicator,
        )
        for item in TAIWAN_INDICATORS
    ]


def load_taiwan_indicators(conn: psycopg.Connection) -> int:
    """Upsert official Taiwan national statistics into ``country_indicators``."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM country_facts WHERE country_iso2 = %s", ("TW",)
        )
        if cur.fetchone() is None:
            log("  Taiwan (TW) is not in country_facts; skipping indicators.")
            return 0
        rows = taiwan_indicator_rows()
        cur.executemany(TAIWAN_UPSERT_SQL, rows)
    conn.commit()
    log(f"  upserted {len(rows)} Taiwan national indicators.")
    return len(rows)


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def print_report(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS relation", (f"public.{TABLE}",))
        if cur.fetchone()["relation"] is not None:
            cur.execute(
                f"""
                SELECT transit_method, COUNT(*) AS n
                FROM {TABLE}
                GROUP BY transit_method
                ORDER BY n DESC, transit_method
                """
            )
            for row in cur.fetchall():
                log(f"  {row['transit_method']:32s} {row['n']:>7,}")
            cur.execute(
                f"SELECT COUNT(*) AS n FROM {TABLE} "
                "WHERE transit_score IS NOT NULL"
            )
            scored = cur.fetchone()["n"]
            cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE}")
            total = cur.fetchone()["n"]
            log(f"  scored {scored:,} / {total:,} gated-transit rows")
        else:
            log(f"  {TABLE} is not installed yet.")
        cur.execute(
            "SELECT to_regclass('public.country_indicators') AS relation"
        )
        if cur.fetchone()["relation"] is not None:
            cur.execute(
                "SELECT indicator, value, year, source "
                "FROM country_indicators WHERE country_iso2 = 'TW' "
                "ORDER BY indicator"
            )
            for row in cur.fetchall():
                log(
                    f"  TW {row['indicator']}: {row['value']} "
                    f"({row['year']}, {row['source']})"
                )


def connect_db(db_url: str) -> psycopg.Connection:
    kwargs: dict[str, Any] = {
        "connect_timeout": 30,
        "application_name": "bootstrap_gated_transit",
    }
    if "pooler.supabase.com" in db_url or ":6543" in db_url:
        kwargs["prepare_threshold"] = None
    conn = psycopg.connect(db_url, row_factory=dict_row, **kwargs)
    conn.execute("SET TIME ZONE 'UTC'")
    return conn


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Council gated public transport and Taiwan indicators"
    )
    parser.add_argument("--db-url", default=None,
                        help="Overrides SUPABASE_DB_URL")
    parser.add_argument("--taiwan-only", action="store_true",
                        help="Only upsert Taiwan country indicators")
    parser.add_argument("--transit-only", action="store_true",
                        help="Only rebuild the gated transit table")
    parser.add_argument("--report", action="store_true",
                        help="Print coverage and exit without writing")
    args = parser.parse_args()

    db_url = args.db_url or os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        log("Error: set SUPABASE_DB_URL (or pass --db-url).")
        sys.exit(1)

    with connect_db(db_url) as conn:
        if args.report:
            print_report(conn)
            return
        if not args.taiwan_only:
            ensure_schema(conn)
            load_transit_scores(conn)
        if not args.transit_only:
            load_taiwan_indicators(conn)
        print_report(conn)


if __name__ == "__main__":
    main()
