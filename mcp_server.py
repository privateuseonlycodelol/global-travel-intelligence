"""Phase 4 travel-ranking MCP server.

Exposes these tools over Streamable HTTP:

* ``search_places`` -- resolve a city name (any language, typos, "City, CC").
* ``place_profile`` -- one city's facts in plain terms: values, units, global and
  in-country percentiles, climate summary, country context and data gaps.
* ``compare_places`` -- two to five cities side by side.
* ``rank_destinations`` -- the Phase 3 static scorer, optionally re-ranked with
  live flight and hotel quotes.
* ``inspect_destination`` -- the raw evidence rows for one city.
* ``refresh_live_quotes`` -- force-refresh the split quote caches.

Run with ``python mcp_server.py``. Requires ``SUPABASE_DB_URL``; ``SERPAPI_API_KEY``
is optional and only enables live pricing.
"""

from __future__ import annotations

import math
import os
import statistics
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID

import psycopg
import requests
from fastmcp import FastMCP
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
from starlette.requests import Request
from starlette.responses import JSONResponse

import place_profiles


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"WARNING: {name}={raw!r} is not a number; using {default}.", file=sys.stderr)
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"WARNING: {name}={raw!r} is not an integer; using {default}.", file=sys.stderr)
        return default


DB_URL: str | None = (os.environ.get("SUPABASE_DB_URL") or "").strip() or None
SERPAPI_API_KEY: str | None = (os.environ.get("SERPAPI_API_KEY") or "").strip() or None
QUOTE_TTL_HOURS: float = max(0.0, _env_float("QUOTE_TTL_HOURS", 12.0))
PORT: int = _env_int("PORT", 8000)
MCP_PATH: str = os.environ.get("MCP_PATH") or "/mcp"
if not MCP_PATH.startswith("/"):
    MCP_PATH = "/" + MCP_PATH

SERPAPI_URL = "https://serpapi.com/search"
SERPAPI_TIMEOUT_SECONDS = 30
FLIGHT_SOURCE = "SerpApi Google Flights"
HOTEL_SOURCE = "SerpApi Google Hotels"
DATA_SOURCES_DOC = "docs/DATA_SOURCES.md"
UNAVAILABLE_DIMENSIONS = [
    "safety_crime",
    "global_city_level_long_term_rent",
    "visa_and_residence_rules",
    "rainy_and_hot_days",
]

FLIGHT_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS flight_quote_cache (
    origin_iata        CHAR(3),
    destination_iata   CHAR(3),
    start_date         DATE,
    end_date           DATE,
    lowest_price_usd   DOUBLE PRECISION,
    duration_min       INT,
    stops              INT,
    price_level        TEXT,
    source             TEXT NOT NULL DEFAULT 'SerpApi Google Flights',
    fetched_at         TIMESTAMPTZ DEFAULT NOW(),
    expires_at         TIMESTAMPTZ,
    PRIMARY KEY (origin_iata, destination_iata, start_date, end_date)
);
"""

HOTEL_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS hotel_quote_cache (
    destination_id        TEXT REFERENCES destinations(destination_id) ON DELETE CASCADE,
    start_date            DATE,
    end_date              DATE,
    median_nightly_usd    DOUBLE PRECISION,
    sample_properties     INT,
    source                TEXT NOT NULL DEFAULT 'SerpApi Google Hotels',
    fetched_at            TIMESTAMPTZ DEFAULT NOW(),
    expires_at            TIMESTAMPTZ,
    PRIMARY KEY (destination_id, start_date, end_date)
);
"""

FLIGHT_CACHE_SELECT = """
SELECT lowest_price_usd, duration_min, stops, price_level, fetched_at
FROM flight_quote_cache
WHERE origin_iata = %s
  AND destination_iata = %s
  AND start_date = %s
  AND end_date = %s
  AND expires_at > NOW()
"""

HOTEL_CACHE_SELECT = """
SELECT median_nightly_usd, sample_properties, fetched_at
FROM hotel_quote_cache
WHERE destination_id = %s
  AND start_date = %s
  AND end_date = %s
  AND expires_at > NOW()
"""

FLIGHT_CACHE_UPSERT = """
INSERT INTO flight_quote_cache (
    origin_iata, destination_iata, start_date, end_date,
    lowest_price_usd, duration_min, stops, price_level,
    source, fetched_at, expires_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (origin_iata, destination_iata, start_date, end_date)
DO UPDATE SET
    lowest_price_usd = EXCLUDED.lowest_price_usd,
    duration_min     = EXCLUDED.duration_min,
    stops            = EXCLUDED.stops,
    price_level      = EXCLUDED.price_level,
    source           = EXCLUDED.source,
    fetched_at       = EXCLUDED.fetched_at,
    expires_at       = EXCLUDED.expires_at
"""

HOTEL_CACHE_UPSERT = """
INSERT INTO hotel_quote_cache (
    destination_id, start_date, end_date, median_nightly_usd,
    sample_properties, source, fetched_at, expires_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (destination_id, start_date, end_date)
DO UPDATE SET
    median_nightly_usd = EXCLUDED.median_nightly_usd,
    sample_properties  = EXCLUDED.sample_properties,
    source             = EXCLUDED.source,
    fetched_at         = EXCLUDED.fetched_at,
    expires_at         = EXCLUDED.expires_at
"""

RANK_SQL = """
SELECT destination_id, name, country_iso2, population, iata_codes,
       final_score, data_completeness, metrics, provenance
FROM rank_destinations_v3(
    %s::INT[], %s::DOUBLE PRECISION, %s::DOUBLE PRECISION,
    %s::DOUBLE PRECISION, %s::INT, %s::TEXT[], %s::BOOLEAN,
    %s::BOOLEAN, %s::DOUBLE PRECISION, %s::BOOLEAN,
    %s::DOUBLE PRECISION, %s::DOUBLE PRECISION,
    %s::DOUBLE PRECISION, %s::DOUBLE PRECISION,
    %s::DOUBLE PRECISION, %s::DOUBLE PRECISION,
    %s::DOUBLE PRECISION, %s::DOUBLE PRECISION, %s::INT
)
"""

DESTINATION_SELECT = """
SELECT destination_id, geoname_id, name, ascii_name, country_iso2,
       timezone, lat, lon, population, nuts3_code, iata_codes
FROM destinations
"""

DESTINATION_COLUMNS = """
destination_id, name, country_iso2, iata_codes
"""

STATIC_WEIGHT_KEYS = (
    "climate",
    "culture",
    "food",
    "walkability",
    "transit",
    "broadband",
    "low_crowding",
    "housing_cost",
)

REGIME_WEIGHTS: dict[str, tuple[float, ...]] = {
    "extended_stay": (0.20, 0.10, 0.10, 0.20, 0.15, 0.15, 0.05, 0.05),
    "short_stay": (0.25, 0.25, 0.20, 0.15, 0.10, 0.00, 0.05, 0.00),
}

mcp = FastMCP(
    name="travel-ranking",
    instructions=(
        "Answer questions about ~13k cities (population >= 40k) for trips, long "
        "stays and moves. Start with search_places or place_profile for one city, "
        "compare_places for 2-5 cities, and rank_destinations to discover places. "
        "Rank global cities for a trip, inspect a city's climate, POI, mobility, "
        "broadband, housing and crowding evidence, and refresh live flight/hotel "
        "quotes cached in Postgres. All non-derived observations are third-party; "
        "read docs/DATA_SOURCES.md for source, licence and coverage. Missing data "
        "is NULL and must be reported as unavailable; a neutral 0.5 score component "
        "is ranking arithmetic, not an observed value. Tourism pressure is EU NUTS 2 "
        "only; walkability/rail coverage depends on the last Overture load."
    ),
)

pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _jsonable(value: Any) -> Any:
    """Recursively convert psycopg values into JSON-serializable types."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            key if isinstance(key, str) else str(key): _jsonable(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _as_float(value: Any) -> float | None:
    """Best-effort numeric conversion for SerpApi's mixed string/number values."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
        if not value:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(number)


def _coerce_date(value: str | date | datetime) -> date:
    """Parse an ISO date or pass through a date/datetime."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _expand_months(start_month: int, duration_days: int) -> list[int]:
    """Return the 1..12 months touched by a stay, wrapping across the year."""
    start = int(start_month)
    if not 1 <= start <= 12:
        raise ValueError("start_month must be between 1 and 12.")
    count = max(1, min(12, math.ceil(max(0, int(duration_days)) / 30)))
    return [((start - 1 + offset) % 12) + 1 for offset in range(count)]


def _months_between(start: date, end: date) -> list[int]:
    """Calendar months touched by the nights of a stay [start, end)."""
    last_night = end - timedelta(days=1) if end > start else start
    months: list[int] = []
    year, month = start.year, start.month
    while (year, month) <= (last_night.year, last_night.month) and len(months) < 12:
        months.append(month)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _normalize_weights(weights: Iterable[float]) -> list[float]:
    """Clamp negatives, normalize to sum 1, or fall back to equal weights."""
    values = [max(0.0, float(weight)) for weight in weights]
    if not values:
        return []
    total = sum(values)
    if total <= 0.0:
        equal = round(1.0 / len(values), 6)
        return [equal for _ in values]
    return [round(value / total, 6) for value in values]


def _connection_kwargs(db_url: str) -> dict[str, Any]:
    """Connection settings; Supabase poolers cannot use prepared statements."""
    kwargs: dict[str, Any] = {"row_factory": dict_row}
    if "pooler.supabase.com" in db_url or ":6543" in db_url:
        kwargs["prepare_threshold"] = None
    return kwargs


def _get_pool() -> ConnectionPool:
    """Return the process-wide pool, creating it lazily if necessary."""
    global pool, DB_URL
    if not DB_URL:
        DB_URL = (os.environ.get("SUPABASE_DB_URL") or "").strip() or None
    if pool is None:
        if not DB_URL:
            raise RuntimeError("SUPABASE_DB_URL is not set.")
        with _pool_lock:
            if pool is None:
                pool = ConnectionPool(
                    conninfo=DB_URL,
                    min_size=1,
                    max_size=8,
                    kwargs=_connection_kwargs(DB_URL),
                    open=True,
                )
    return pool


def _ensure_cache_tables() -> None:
    """Create the split quote caches; fail clearly if ``destinations`` is absent."""
    try:
        with _get_pool().connection() as conn:
            conn.execute(FLIGHT_CACHE_DDL)
            conn.execute(HOTEL_CACHE_DDL)
    except (psycopg.Error, PoolTimeout) as exc:
        message = str(exc).strip() or exc.__class__.__name__
        print(f"ERROR: could not create quote cache tables: {message}", file=sys.stderr)
        if isinstance(exc, psycopg.errors.UndefinedTable) or "destinations" in message:
            print(
                "ERROR: hotel_quote_cache references destinations(destination_id); "
                "run the Phase 1 bootstrap before starting this server.",
                file=sys.stderr,
            )
        raise SystemExit(1) from exc


def _fetch_serpapi_flight(
    origin_iata: str,
    dest_iata: str,
    start_date: str | date,
    end_date: str | date,
) -> dict[str, Any]:
    """Fetch the cheapest round-trip flight option from SerpApi Google Flights."""
    if not SERPAPI_API_KEY:
        raise RuntimeError("SERPAPI_API_KEY is not set.")

    params = {
        "engine": "google_flights",
        "departure_id": str(origin_iata).upper(),
        "arrival_id": str(dest_iata).upper(),
        "outbound_date": _coerce_date(start_date).isoformat(),
        "return_date": _coerce_date(end_date).isoformat(),
        "currency": "USD",
        "hl": "en",
        "api_key": SERPAPI_API_KEY,
    }
    response = requests.get(SERPAPI_URL, params=params, timeout=SERPAPI_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"SerpApi flight error: {payload['error']}")

    best = payload.get("best_flights") or []
    other = payload.get("other_flights") or []
    candidates = [
        option
        for option in [*best, *other]
        if _as_float(option.get("price")) is not None
    ]

    price_insights = payload.get("price_insights") or {}
    price_level = price_insights.get("price_level")

    if not candidates:
        return {
            "lowest_price_usd": None,
            "duration_min": None,
            "stops": None,
            "price_level": price_level,
        }

    def sort_key(option: dict[str, Any]) -> tuple[float, float]:
        price = _as_float(option.get("price"))
        duration = _as_float(option.get("total_duration"))
        return (
            price if price is not None else math.inf,
            duration if duration is not None else math.inf,
        )

    chosen = min(candidates, key=sort_key)
    segments = chosen.get("flights") or []
    duration = _as_float(chosen.get("total_duration"))
    if duration is None and segments:
        segment_durations = [_as_float(segment.get("duration")) for segment in segments]
        if all(item is not None for item in segment_durations):
            duration = sum(item for item in segment_durations if item is not None)

    return {
        "lowest_price_usd": _as_float(chosen.get("price")),
        "duration_min": _as_int(duration),
        "stops": max(0, len(segments) - 1),
        "price_level": price_level,
    }


def _fetch_serpapi_hotel(
    city_name: str,
    country_iso2: str,
    start_date: str | date,
    end_date: str | date,
) -> dict[str, Any]:
    """Fetch the median nightly hotel rate from SerpApi Google Hotels."""
    if not SERPAPI_API_KEY:
        raise RuntimeError("SERPAPI_API_KEY is not set.")

    country = (country_iso2 or "").strip().upper()
    query = f"hotels in {city_name} {country}".strip()
    params = {
        "engine": "google_hotels",
        "q": query,
        "check_in_date": _coerce_date(start_date).isoformat(),
        "check_out_date": _coerce_date(end_date).isoformat(),
        "adults": 1,
        "rating": 8,
        "currency": "USD",
        "hl": "en",
        "api_key": SERPAPI_API_KEY,
    }
    response = requests.get(SERPAPI_URL, params=params, timeout=SERPAPI_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"SerpApi hotel error: {payload['error']}")

    rates: list[float] = []
    for prop in payload.get("properties") or []:
        rate = prop.get("rate_per_night") or {}
        value = _as_float(rate.get("extracted_lowest"))
        if value is not None and value > 0:
            rates.append(value)

    if not rates:
        return {"median_nightly_usd": None, "sample_properties": 0}

    return {
        "median_nightly_usd": float(statistics.median(rates)),
        "sample_properties": len(rates),
    }


def _empty_quote(
    row: dict[str, Any],
    origin_iata: str,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Build an all-null quote so callers always see the same shape."""
    codes = row.get("iata_codes") or []
    dest_iata = next((str(code).upper() for code in codes if code), None)
    return {
        "destination_id": str(row.get("destination_id")),
        "dest_iata": dest_iata,
        "flight_usd": None,
        "flight_duration_min": None,
        "flight_stops": None,
        "flight_price_level": None,
        "flight_cache_hit": False,
        "hotel_median_nightly_usd": None,
        "hotel_sample_properties": 0,
        "hotel_cache_hit": False,
        "nights": max(0, (end_date - start_date).days),
        "estimated_total_trip_usd": None,
        "fetched_at": None,
    }


def _get_or_fetch_quotes_for_city(
    row: dict[str, Any],
    origin_iata: str,
    start_date: str | date,
    end_date: str | date,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return cached or live flight and hotel quotes for one ranked city."""
    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    nights = max(0, (end - start).days)
    destination_id = str(row["destination_id"])
    origin = str(origin_iata).upper()

    codes = row.get("iata_codes") or []
    dest_iata = next((str(code).upper() for code in codes if code), None)

    flight_cache: dict[str, Any] | None = None
    hotel_cache: dict[str, Any] | None = None
    if not force_refresh:
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                if dest_iata:
                    cur.execute(
                        FLIGHT_CACHE_SELECT,
                        (origin, dest_iata, start, end),
                    )
                    flight_cache = cur.fetchone()
                cur.execute(
                    HOTEL_CACHE_SELECT,
                    (destination_id, start, end),
                )
                hotel_cache = cur.fetchone()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=QUOTE_TTL_HOURS)

    flight_usd: float | None = None
    flight_duration: int | None = None
    flight_stops: int | None = None
    flight_price_level: str | None = None
    hotel_nightly: float | None = None
    hotel_samples: int | None = None
    flight_fetched = False
    hotel_fetched = False

    if flight_cache is not None:
        flight_usd = _as_float(flight_cache.get("lowest_price_usd"))
        flight_duration = _as_int(flight_cache.get("duration_min"))
        flight_stops = _as_int(flight_cache.get("stops"))
        flight_price_level = flight_cache.get("price_level")
    elif dest_iata and SERPAPI_API_KEY:
        try:
            fetched = _fetch_serpapi_flight(origin, dest_iata, start, end)
            if fetched["lowest_price_usd"] is not None:
                flight_usd = fetched["lowest_price_usd"]
                flight_duration = fetched["duration_min"]
                flight_stops = fetched["stops"]
                flight_price_level = fetched["price_level"]
                flight_fetched = True
        except Exception as exc:  # one bad quote must not sink the whole ranking
            print(
                f"WARNING: flight quote failed for {origin}->{dest_iata}: {exc}",
                file=sys.stderr,
            )

    if hotel_cache is not None:
        hotel_nightly = _as_float(hotel_cache.get("median_nightly_usd"))
        hotel_samples = _as_int(hotel_cache.get("sample_properties"))
    elif SERPAPI_API_KEY:
        try:
            fetched = _fetch_serpapi_hotel(
                str(row.get("name") or ""),
                str(row.get("country_iso2") or ""),
                start,
                end,
            )
            if fetched["median_nightly_usd"] is not None:
                hotel_nightly = fetched["median_nightly_usd"]
                hotel_samples = fetched["sample_properties"]
                hotel_fetched = True
        except Exception as exc:
            print(
                f"WARNING: hotel quote failed for {destination_id}: {exc}",
                file=sys.stderr,
            )

    if flight_fetched or hotel_fetched:
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                if flight_fetched and dest_iata and flight_usd is not None:
                    cur.execute(
                        FLIGHT_CACHE_UPSERT,
                        (
                            origin,
                            dest_iata,
                            start,
                            end,
                            flight_usd,
                            flight_duration,
                            flight_stops,
                            flight_price_level,
                            FLIGHT_SOURCE,
                            now,
                            expires_at,
                        ),
                    )
                if hotel_fetched and hotel_nightly is not None:
                    cur.execute(
                        HOTEL_CACHE_UPSERT,
                        (
                            destination_id,
                            start,
                            end,
                            hotel_nightly,
                            hotel_samples,
                            HOTEL_SOURCE,
                            now,
                            expires_at,
                        ),
                    )

    estimated_total: float | None = None
    if flight_usd is not None and hotel_nightly is not None:
        estimated_total = round(flight_usd + hotel_nightly * nights, 2)

    timestamps = []
    if flight_cache is not None:
        timestamps.append(flight_cache.get("fetched_at"))
    if hotel_cache is not None:
        timestamps.append(hotel_cache.get("fetched_at"))
    if flight_fetched or hotel_fetched:
        timestamps.append(now)
    timestamps = [stamp for stamp in timestamps if stamp is not None]
    fetched_at = max(timestamps) if timestamps else None

    return {
        "destination_id": destination_id,
        "dest_iata": dest_iata,
        "flight_usd": flight_usd,
        "flight_duration_min": flight_duration,
        "flight_stops": flight_stops,
        "flight_price_level": flight_price_level,
        "flight_cache_hit": flight_cache is not None,
        "hotel_median_nightly_usd": hotel_nightly,
        "hotel_sample_properties": hotel_samples if hotel_samples is not None else 0,
        "hotel_cache_hit": hotel_cache is not None,
        "nights": nights,
        "estimated_total_trip_usd": estimated_total,
        "fetched_at": fetched_at,
    }


def _invert_rank_scores(values: dict[str, float]) -> dict[str, float]:
    """Map lower-is-better values to 1.0..0.0 scores, averaging tied ranks."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    count = len(ordered)
    if count == 1:
        return {ordered[0][0]: 1.0}

    scores: dict[str, float] = {}
    index = 0
    while index < count:
        end = index
        while end + 1 < count and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        average_position = (index + end) / 2.0
        score = 1.0 - (average_position / (count - 1))
        for position in range(index, end + 1):
            scores[ordered[position][0]] = score
        index = end + 1
    return scores


def _apply_stage2_rescore(
    rows: list[dict[str, Any]],
    quotes_by_id: dict[str, dict[str, Any]],
    w_trip_cost: float,
    w_flight_time: float,
    max_total_trip_usd: float | None,
    max_flight_duration_min: int | None,
) -> list[dict[str, Any]]:
    """Hard-filter stage 1 rows, then blend live cost/duration ranks with the static score."""
    w_cost = max(0.0, float(w_trip_cost))
    w_time = max(0.0, float(w_flight_time))

    candidates: list[dict[str, Any]] = []
    for row in rows:
        destination_id = str(row.get("destination_id"))
        quote = quotes_by_id.get(destination_id)
        total = quote.get("estimated_total_trip_usd") if quote else None
        duration = quote.get("flight_duration_min") if quote else None

        checks: dict[str, str] = {}
        if max_total_trip_usd is not None:
            if total is None:
                checks["max_total_trip_usd"] = "unverified_no_quote"
            elif float(total) > float(max_total_trip_usd):
                continue
            else:
                checks["max_total_trip_usd"] = "pass"
        if max_flight_duration_min is not None:
            if duration is None:
                checks["max_flight_duration_min"] = "unverified_no_quote"
            elif int(duration) > int(max_flight_duration_min):
                continue
            else:
                checks["max_flight_duration_min"] = "pass"

        live_row = dict(row)
        live_row["stage1_score"] = row.get("final_score")
        live_row["live_quotes"] = quote
        if checks:
            live_row["constraint_checks"] = checks

        provenance = row.get("provenance")
        provenance = dict(provenance) if isinstance(provenance, dict) else {}
        if quote is None:
            provenance["live_pricing"] = {"status": "unavailable"}
        else:
            has_flight = quote.get("flight_usd") is not None
            has_hotel = quote.get("hotel_median_nightly_usd") is not None
            if has_flight and has_hotel:
                live_status = "ok"
            elif has_flight or has_hotel:
                live_status = "partial"
            else:
                live_status = "unavailable"
            provenance["live_pricing"] = {
                "status": live_status,
                "destination_id": destination_id,
                "destination_iata": quote.get("dest_iata"),
                "flight_usd": quote.get("flight_usd"),
                "flight_duration_min": quote.get("flight_duration_min"),
                "flight_stops": quote.get("flight_stops"),
                "flight_price_level": quote.get("flight_price_level"),
                "hotel_median_nightly_usd": quote.get("hotel_median_nightly_usd"),
                "hotel_sample_properties": quote.get("hotel_sample_properties"),
                "nights": quote.get("nights"),
                "estimated_total_trip_usd": quote.get("estimated_total_trip_usd"),
                "flight_cache_hit": quote.get("flight_cache_hit"),
                "hotel_cache_hit": quote.get("hotel_cache_hit"),
                "fetched_at": quote.get("fetched_at"),
                "sources": [FLIGHT_SOURCE, HOTEL_SOURCE],
            }
        live_row["provenance"] = provenance
        candidates.append(live_row)

    cost_values: dict[str, float] = {}
    duration_values: dict[str, float] = {}
    for row in candidates:
        quote = row.get("live_quotes")
        if not quote:
            continue
        destination_id = str(row.get("destination_id"))
        total = quote.get("estimated_total_trip_usd")
        duration = quote.get("flight_duration_min")
        if total is not None:
            cost_values[destination_id] = float(total)
        if duration is not None:
            duration_values[destination_id] = float(duration)

    cost_scores = _invert_rank_scores(cost_values)
    duration_scores = _invert_rank_scores(duration_values)

    w_live_total = min(0.60, w_cost + w_time)
    w_static = max(0.40, 1.0 - w_live_total)

    for row in candidates:
        destination_id = str(row.get("destination_id"))
        stage1 = row.get("stage1_score")
        stage1_score = float(stage1) if stage1 is not None else 0.0

        live_weight = 0.0
        live_sum = 0.0
        # A missing quote is neutral (0.5), like other missing dimensions, so a
        # city cannot outrank quoted cities just because its quote failed.
        live_sum += cost_scores.get(destination_id, 0.5) * w_cost
        live_weight += w_cost
        live_sum += duration_scores.get(destination_id, 0.5) * w_time
        live_weight += w_time

        if live_weight > 0:
            live_component = live_sum / live_weight
            final_score = w_static * stage1_score + w_live_total * live_component
        else:
            final_score = stage1_score

        row["final_score"] = round(final_score, 4)
        checks = row.get("constraint_checks") or {}
        row["constraints_verified"] = all(v == "pass" for v in checks.values())

    # Under a budget/duration cap, confirmed matches rank ahead of unverified ones.
    candidates.sort(
        key=lambda row: (
            bool(row.get("constraints_verified")),
            row.get("final_score")
            if row.get("final_score") is not None
            else float("-inf"),
            row.get("population") or 0,
        ),
        reverse=True,
    )
    return candidates


@mcp.tool
def rank_destinations(
    start_month: int | None = None,
    duration_days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    origin_iata: str | None = None,
    ideal_temp_min_c: float = 20.0,
    ideal_temp_max_c: float = 26.0,
    max_peak_temp_c: float | None = None,
    min_population: int = 40000,
    countries: list[str] | None = None,
    require_airport: bool = False,
    passport_eu_eea: bool = True,
    min_broadband_mbps: float | None = None,
    require_gtfs: bool = False,
    max_total_trip_usd: float | None = None,
    max_flight_duration_min: int | None = None,
    w_climate: float | None = None,
    w_culture: float | None = None,
    w_food: float | None = None,
    w_walkability: float | None = None,
    w_transit: float | None = None,
    w_broadband: float | None = None,
    w_low_crowding: float | None = None,
    w_housing_cost: float | None = None,
    w_trip_cost: float = 0.25,
    w_flight_time: float = 0.15,
    live_candidate_pool: int = 8,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank destinations by static evidence and, when available, live trip cost."""
    if (start_date is None) != (end_date is None):
        raise ValueError("Provide both start_date and end_date, or neither.")

    quote_start: date | None = None
    quote_end: date | None = None
    if start_date is not None and end_date is not None:
        quote_start = _coerce_date(start_date)
        quote_end = _coerce_date(end_date)
        if quote_end <= quote_start:
            raise ValueError("end_date must be after start_date.")
        eff_start_month = quote_start.month
        eff_duration = (quote_end - quote_start).days
    else:
        if start_month is None:
            raise ValueError(
                "start_month is required when start_date/end_date are not provided."
            )
        eff_start_month = int(start_month)
        if not 1 <= eff_start_month <= 12:
            raise ValueError("start_month must be between 1 and 12.")
        eff_duration = int(duration_days) if duration_days is not None else 7
        if eff_duration <= 0:
            raise ValueError("duration_days must be positive.")

    if quote_start is not None and quote_end is not None:
        months = _months_between(quote_start, quote_end)
    else:
        months = _expand_months(eff_start_month, eff_duration)
    stay_profile = "extended_stay" if eff_duration >= 30 else "short_stay"
    exclude_schengen = eff_duration > 90 and not passport_eu_eea
    # The default heat cap never sits below the caller's own ideal range.
    eff_max_temp = (
        float(max_peak_temp_c)
        if max_peak_temp_c is not None
        else max(
            29.0 if stay_profile == "extended_stay" else 32.0,
            float(ideal_temp_max_c) + 3.0,
        )
    )

    regime = REGIME_WEIGHTS[stay_profile]
    supplied_weights = [
        w_climate,
        w_culture,
        w_food,
        w_walkability,
        w_transit,
        w_broadband,
        w_low_crowding,
        w_housing_cost,
    ]
    raw_weights = [
        regime[index] if weight is None else float(weight)
        for index, weight in enumerate(supplied_weights)
    ]
    normalized_weights = _normalize_weights(raw_weights)

    origin = (origin_iata or "").strip().upper() or None
    run_stage2 = bool(
        stay_profile == "short_stay"
        and origin
        and quote_start is not None
        and quote_end is not None
        and SERPAPI_API_KEY
    )

    clamped_limit = max(1, min(25, int(limit) if limit is not None else 10))
    if run_stage2:
        pool_size = (
            int(live_candidate_pool) if live_candidate_pool is not None else 8
        )
        # Never spend SerpApi credits on more than 15 finalists.
        stage1_limit = min(15, max(clamped_limit, min(15, max(1, pool_size))))
    else:
        stage1_limit = clamped_limit

    norm_countries = (
        [
            str(country).strip().upper()
            for country in countries
            if str(country).strip()
        ]
        if countries
        else None
    )
    if norm_countries == []:
        norm_countries = None
    eff_require_airport = bool(require_airport or run_stage2)
    eff_min_population = max(
        40000,
        int(min_population) if min_population is not None else 40000,
    )

    args = (
        months,
        float(ideal_temp_min_c),
        float(ideal_temp_max_c),
        eff_max_temp,
        eff_min_population,
        norm_countries,
        eff_require_airport,
        exclude_schengen,
        min_broadband_mbps,
        require_gtfs,
        normalized_weights[0],
        normalized_weights[1],
        normalized_weights[2],
        normalized_weights[3],
        normalized_weights[4],
        normalized_weights[5],
        normalized_weights[6],
        normalized_weights[7],
        stage1_limit,
    )

    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(RANK_SQL, args)
            rows = [dict(row) for row in cur.fetchall()]

    if run_stage2 and rows and quote_start is not None and quote_end is not None:
        quotes_by_id: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    _get_or_fetch_quotes_for_city,
                    row,
                    origin,
                    quote_start,
                    quote_end,
                ): row
                for row in rows
            }
            for future in as_completed(futures):
                row = futures[future]
                try:
                    quote = future.result()
                except Exception as exc:
                    print(
                        f"WARNING: live quote worker failed for "
                        f"{row.get('destination_id')}: {exc}",
                        file=sys.stderr,
                    )
                    quote = _empty_quote(row, origin, quote_start, quote_end)
                quotes_by_id[str(row.get("destination_id"))] = quote

        rows = _apply_stage2_rescore(
            rows=rows,
            quotes_by_id=quotes_by_id,
            w_trip_cost=float(w_trip_cost) if w_trip_cost is not None else 0.25,
            w_flight_time=(
                float(w_flight_time) if w_flight_time is not None else 0.15
            ),
            max_total_trip_usd=max_total_trip_usd,
            max_flight_duration_min=max_flight_duration_min,
        )
        rows = rows[:clamped_limit]

    if run_stage2:
        live_pricing_status = "live"
    elif stay_profile != "short_stay":
        live_pricing_status = "disabled_extended_stay"
    elif quote_start is None or quote_end is None or not origin:
        live_pricing_status = "disabled_missing_origin_or_dates"
    elif not SERPAPI_API_KEY:
        live_pricing_status = "disabled_missing_serpapi_key"
    else:
        live_pricing_status = "disabled"

    ignored_constraints: list[str] = []
    if not run_stage2:
        if max_total_trip_usd is not None:
            ignored_constraints.append("max_total_trip_usd")
        if max_flight_duration_min is not None:
            ignored_constraints.append("max_flight_duration_min")

    payload = {
        "query_context": {
            "stay_profile": stay_profile,
            "ignored_constraints": ignored_constraints,
            "effective_max_peak_temp_c": eff_max_temp,
            "evaluated_months": months,
            "duration_days": eff_duration,
            "exclude_schengen": exclude_schengen,
            "live_pricing_status": live_pricing_status,
            "normalized_static_weights": dict(
                zip(STATIC_WEIGHT_KEYS, normalized_weights)
            ),
            "coverage_notes": [
                "tourism_pressure is Eurostat NUTS 2 only (EU/EFTA); not global city-level",
                "walkability/rail may be absent if Overture Transportation was not loaded",
                "transit_stops_5km is an Overture Places facility count, not service frequency",
                "housing_monthly, when present, is in local currency (no FX conversion)",
                "missing dimensions are NULL; see docs/DATA_SOURCES.md",
            ],
        },
        "result_count": len(rows),
        "results": rows,
        "unavailable_dimensions": UNAVAILABLE_DIMENSIONS,
        "data_sources_documentation": DATA_SOURCES_DOC,
    }
    return _jsonable(payload)


@mcp.tool
def inspect_destination(
    destination_id: str | None = None,
    city_name: str | None = None,
    country_iso2: str | None = None,
) -> dict[str, Any]:
    """Return a destination's full climate, POI, mobility, broadband, housing and crowding evidence."""
    if not destination_id and not city_name:
        raise ValueError("Provide destination_id or city_name.")

    resolution: dict[str, Any] | None = None
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            if not destination_id:
                try:
                    resolution = place_profiles.resolve_place(
                        cur, str(city_name), country_iso2
                    )
                except ValueError:
                    resolution = None
                if resolution and resolution["needs_choice"]:
                    return _jsonable(_choice_payload(str(city_name), resolution))
                destination_id = resolution["destination_id"] if resolution else None
            cur.execute(
                DESTINATION_SELECT + " WHERE destination_id = %s",
                (destination_id,),
            )
            destination = cur.fetchone()

            if destination is None:
                raise ValueError(
                    "Destination not found for "
                    f"destination_id={destination_id!r}, city_name={city_name!r}, "
                    f"country_iso2={country_iso2!r}."
                )

            resolved_id = destination["destination_id"]

            cur.execute(
                """
                SELECT month, median_temp_max_c, median_temp_min_c, precip_mm,
                       avg_rainy_days, avg_hot_days_30c, source,
                       observed_period, updated_at
                FROM climate_monthly
                WHERE destination_id = %s
                ORDER BY month
                """,
                (resolved_id,),
            )
            climate = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT radius_km, museum_culture_count, restaurant_count,
                       cuisine_entropy, source, observed_period, updated_at
                FROM poi_metrics
                WHERE destination_id = %s
                """,
                (resolved_id,),
            )
            poi_metrics = cur.fetchone()

            cur.execute(
                """
                SELECT radius_km, pedestrian_segments_2km,
                       total_road_segments_2km, pedestrian_ratio_2km,
                       rail_segments_5km, gtfs_active_feeds,
                       transit_stops_5km, transit_stops_source, transit_stops_period,
                       source, observed_period, updated_at
                FROM mobility_metrics
                WHERE destination_id = %s
                """,
                (resolved_id,),
            )
            mobility_metrics = cur.fetchone()

            cur.execute(
                """
                SELECT radius_km, median_down_mbps, median_up_mbps,
                       avg_latency_ms, total_tests, tile_count, source,
                       observed_period, updated_at
                FROM broadband_metrics
                WHERE destination_id = %s
                """,
                (resolved_id,),
            )
            broadband_metrics = cur.fetchone()

            cur.execute(
                """
                SELECT median_monthly_local, sample_listings, source,
                       observed_period, updated_at
                FROM housing_monthly
                WHERE destination_id = %s
                """,
                (resolved_id,),
            )
            housing_monthly = cur.fetchone()

            cur.execute(
                """
                SELECT tourist_nights_annual, nights_per_resident, source,
                       observed_period, updated_at
                FROM tourism_pressure
                WHERE destination_id = %s
                """,
                (resolved_id,),
            )
            tourism_pressure = cur.fetchone()

    payload = {
        "destination": destination,
        "name_resolution": resolution,
        "climate": climate,
        "poi_metrics": poi_metrics,
        "mobility_metrics": mobility_metrics,
        "broadband_metrics": broadband_metrics,
        "housing_monthly": housing_monthly,
        "tourism_pressure": tourism_pressure,
        "data_sources_documentation": DATA_SOURCES_DOC,
    }
    return _jsonable(payload)


def _choice_payload(place: str, resolution: dict[str, Any]) -> dict[str, Any]:
    """Response for a name that only fuzzily matches several cities."""
    return {
        "needs_choice": True,
        "query": place,
        "message": (
            f"{place!r} does not exactly match a loaded city. Ask the user which "
            "of these they mean, then call again with its destination_id."
        ),
        "candidates": [resolution["resolved"], *resolution["other_matches"]],
    }


def _resolve_many(
    cur: Any, places: list[str], country_iso2: str | None
) -> tuple[list[str], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    ids: list[str] = []
    resolutions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    choices: list[dict[str, Any]] = []
    for place in places:
        try:
            resolution = place_profiles.resolve_place(cur, place, country_iso2)
        except ValueError:
            unresolved.append(place)
            continue
        if resolution["needs_choice"]:
            unresolved.append(place)
            choices.append(_choice_payload(place, resolution))
            continue
        if resolution["destination_id"] not in ids:
            ids.append(resolution["destination_id"])
            resolutions.append({"query": place, **resolution})
    return ids, resolutions, unresolved, choices


@mcp.tool
def search_places(
    query: str,
    country_iso2: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Find cities by name in any language or spelling ("Cologne", "Köln", "Porto, PT").

    Returns candidates with destination_id, country and population, best first.
    Use the destination_id with place_profile or compare_places. Only cities with
    population >= 40,000 are loaded.
    """
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            result = place_profiles.search_places(cur, query, country_iso2, limit)
    return _jsonable(result)


@mcp.tool
def place_profile(
    place: str,
    country_iso2: str | None = None,
) -> dict[str, Any]:
    """Everything the database knows about one city, in plain terms.

    ``place`` is a name ("Lisbon", "Porto, Portugal", "Austin, TX") or a
    destination_id ("gn-2267057"). Each metric has a label, unit, value, global and
    in-country percentile (100 = best among cities with data) with a plain-words
    ``reading_global``, source and period, or status "not_measured"/"none_listed".
    Also returns a 12-month climate table with a summary, country context
    (currency, languages, price level, homicide rate, PM2.5, life expectancy,
    political stability -- national values), and a list of what is not measured.
    If the name is ambiguous, ``match.other_matches`` lists the alternatives; if it
    is only a fuzzy guess, the result has ``needs_choice: true`` and candidates
    instead of a profile.
    """
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            resolution = place_profiles.resolve_place(cur, place, country_iso2)
            if resolution["needs_choice"]:
                return _jsonable(_choice_payload(place, resolution))
            profile = place_profiles.build_profile(cur, resolution["destination_id"])
    profile = {"match": resolution, **profile, "data_sources_documentation": DATA_SOURCES_DOC}
    return _jsonable(profile)


@mcp.tool
def compare_places(
    places: list[str],
    country_iso2: str | None = None,
) -> dict[str, Any]:
    """Compare two to five cities side by side.

    ``places`` are names or destination_ids. For each metric the result lists
    every city's value and percentile and names the ``best`` city (null on ties,
    missing data or local-currency values). Also returns monthly climate for each
    city and national context for each country.
    """
    requested = [str(p).strip() for p in (places or []) if str(p).strip()]
    if not 2 <= len(requested) <= place_profiles.MAX_COMPARE:
        raise ValueError(
            f"places must contain between 2 and {place_profiles.MAX_COMPARE} names or ids."
        )
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            ids, resolutions, unresolved, choices = _resolve_many(cur, requested, country_iso2)
            if len(ids) < 2:
                if choices:
                    return _jsonable({
                        "needs_choice": True,
                        "matches": resolutions,
                        "choices": choices,
                        "unresolved": unresolved,
                    })
                if unresolved:
                    raise ValueError(
                        "Need at least two distinct matching cities; unresolved: "
                        + ", ".join(unresolved)
                    )
                raise ValueError(
                    "Need at least two distinct matching cities; inputs resolved to the same city: "
                    + ", ".join(requested)
                )
            comparison = place_profiles.build_comparison(cur, ids)
    payload = {
        "matches": resolutions,
        "unresolved": unresolved,
        "choices": choices,
        **comparison,
        "data_sources_documentation": DATA_SOURCES_DOC,
    }
    return _jsonable(payload)


@mcp.tool
def refresh_live_quotes(
    destination_ids: list[str],
    origin_iata: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Force-refresh live flight and hotel quotes for one to five destinations."""
    if not SERPAPI_API_KEY:
        raise ValueError(
            "SERPAPI_API_KEY is not set; live quote refresh is unavailable."
        )

    ids: list[str] = []
    for destination_id in destination_ids or []:
        value = str(destination_id).strip()
        if value and value not in ids:
            ids.append(value)
    if not 1 <= len(ids) <= 5:
        raise ValueError("destination_ids must contain between 1 and 5 ids.")

    origin = str(origin_iata or "").strip().upper()
    if not origin:
        raise ValueError("origin_iata is required.")

    start = _coerce_date(start_date)
    end = _coerce_date(end_date)
    if end <= start:
        raise ValueError("end_date must be after start_date.")

    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT " + DESTINATION_COLUMNS + " FROM destinations "
                "WHERE destination_id = ANY(%s)",
                (ids,),
            )
            found = {
                str(row["destination_id"]): row for row in cur.fetchall()
            }

    missing = [destination_id for destination_id in ids if destination_id not in found]
    rows = [found[destination_id] for destination_id in ids if destination_id in found]
    if not rows:
        raise ValueError(f"No destinations found for ids: {', '.join(ids)}")

    quotes: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(
                _get_or_fetch_quotes_for_city,
                row,
                origin,
                start,
                end,
                True,
            ): row
            for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            try:
                quotes.append(future.result())
            except Exception as exc:
                print(
                    f"WARNING: quote refresh failed for "
                    f"{row.get('destination_id')}: {exc}",
                    file=sys.stderr,
                )
                quotes.append(_empty_quote(row, origin, start, end))

    quotes.sort(key=lambda quote: ids.index(str(quote["destination_id"])))

    payload = {
        "origin_iata": origin,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "requested_count": len(ids),
        "quote_count": len(quotes),
        "quotes": quotes,
        "missing_destination_ids": missing,
        "data_sources_documentation": DATA_SOURCES_DOC,
    }
    return _jsonable(payload)


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """Liveness/readiness probe for the MCP server and its database pool."""
    body: dict[str, Any] = {
        "status": "ok",
        "database": "unknown",
        "cities_loaded": None,
        "serpapi_configured": bool(SERPAPI_API_KEY),
        "live_pricing_enabled": bool(SERPAPI_API_KEY),
        "quote_ttl_hours": QUOTE_TTL_HOURS,
        "mcp_path": MCP_PATH,
        "data_sources_documentation": DATA_SOURCES_DOC,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with _get_pool().connection(timeout=3) as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM destinations").fetchone()
        body["database"] = "ok"
        body["cities_loaded"] = int(row["n"]) if row else 0
    except Exception as exc:
        body["status"] = "degraded"
        body["database"] = "unavailable"
        body["error"] = str(exc).strip() or exc.__class__.__name__
        return JSONResponse(body, status_code=503)
    return JSONResponse(body)


def main() -> None:
    """Validate configuration, create cache tables and serve Streamable HTTP."""
    global DB_URL, SERPAPI_API_KEY, pool
    DB_URL = (os.environ.get("SUPABASE_DB_URL") or "").strip() or None
    SERPAPI_API_KEY = (os.environ.get("SERPAPI_API_KEY") or "").strip() or None

    if not DB_URL:
        print(
            "ERROR: SUPABASE_DB_URL is not set. Export the Postgres connection "
            "string before starting the MCP server.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    _ensure_cache_tables()
    print(
        f"Starting travel-ranking MCP server on 0.0.0.0:{PORT}{MCP_PATH} "
        "(Streamable HTTP, stateless).",
        flush=True,
    )
    try:
        mcp.run(
            transport="http",
            host="0.0.0.0",
            port=PORT,
            path=MCP_PATH,
            stateless_http=True,
        )
    finally:
        if pool is not None:
            pool.close()


if __name__ == "__main__":
    main()
