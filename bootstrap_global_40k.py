#!/usr/bin/env python3
"""
Bootstrap a global destinations database (cities >= 40k population) in PostGIS.

Pipeline:
  1. PostGIS schema + rank_destinations_v2() SQL scorer
  2. GeoNames cities15000  -> destinations (bulk COPY + upsert)
  3. OurAirports airports.csv -> nearest IATA codes (PostGIS spatial join)
  4. WorldClim 2.1 5m monthly tmax/tmin/prec -> climate_monthly (local rasterio)
  5. Overture Maps Places (S3 Parquet) -> poi_metrics (single DuckDB grid-hash join)

Designed for Supabase Postgres. See README.md for setup, Supabase specifics and
the data-quality caveats.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import psycopg
import rasterio
import requests

CACHE_DIR = Path("./data_cache")

GEONAMES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
WORLDCLIM_BASE = "https://geodata.ucdavis.edu/climate/worldclim/2_1/base"

# Verified to exist on 2026-11-21. Override with --overture-release.
DEFAULT_OVERTURE_RELEASE = "2026-11-18.0"
OVERTURE_S3_ROOT = "s3://overturemaps-us-west-2/release"

# GeoNames feature codes that describe a subdivision of a larger city rather
# than an independent destination. PPLA2 covers NYC's boroughs (which are not
# PPLX, as often assumed); PPLX is excluded outright in load_geonames_cities.
BOROUGH_FCODES = ("PPLA2",)

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS destinations (
    destination_id   TEXT PRIMARY KEY,              -- 'gn-<geonameid>'
    geoname_id       INT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    ascii_name       TEXT NOT NULL,
    country_iso2     CHAR(2) NOT NULL,
    timezone         TEXT,
    centroid         GEOGRAPHY(Point, 4326) NOT NULL,
    lat              DOUBLE PRECISION NOT NULL,
    lon              DOUBLE PRECISION NOT NULL,
    population       INT NOT NULL,
    nuts3_code       TEXT,
    iata_codes       TEXT[] NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_destinations_centroid ON destinations USING GIST (centroid);
CREATE INDEX IF NOT EXISTS idx_destinations_country  ON destinations (country_iso2);
CREATE INDEX IF NOT EXISTS idx_destinations_pop      ON destinations (population DESC);

CREATE TABLE IF NOT EXISTS climate_monthly (
    destination_id        TEXT REFERENCES destinations(destination_id) ON DELETE CASCADE,
    month                 SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    median_temp_max_c     DOUBLE PRECISION,         -- WorldClim monthly mean daily max
    median_temp_min_c     DOUBLE PRECISION,         -- WorldClim monthly mean daily min
    precip_mm             DOUBLE PRECISION,         -- WorldClim monthly precipitation (mm)
    avg_rainy_days        DOUBLE PRECISION,         -- Optional Open-Meteo ERA5 upgrade
    avg_hot_days_30c      DOUBLE PRECISION,         -- Optional Open-Meteo ERA5 upgrade
    source                TEXT NOT NULL,
    observed_period       TEXT NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (destination_id, month)
);

CREATE TABLE IF NOT EXISTS poi_metrics (
    destination_id        TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    radius_km             DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    museum_culture_count  INT,
    restaurant_count      INT,
    cuisine_entropy       DOUBLE PRECISION,
    source                TEXT NOT NULL,
    observed_period       TEXT NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tourism_pressure (
    destination_id        TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    tourist_nights_annual BIGINT,
    nights_per_resident   DOUBLE PRECISION,
    source                TEXT NOT NULL,
    observed_period       TEXT NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

SCORER_SQL = """
CREATE OR REPLACE FUNCTION rank_destinations_v2(
    p_months           INT[],
    p_ideal_temp_min   DOUBLE PRECISION DEFAULT 20.0,
    p_ideal_temp_max   DOUBLE PRECISION DEFAULT 26.0,
    p_max_temp_c       DOUBLE PRECISION DEFAULT 31.0,  -- hard cap on avg monthly max temp
    p_min_population   INT DEFAULT 40000,
    p_countries        TEXT[] DEFAULT NULL,            -- optional ISO-2 filter, e.g. ARRAY['ES','IT']
    p_require_airport  BOOLEAN DEFAULT FALSE,
    p_w_climate        DOUBLE PRECISION DEFAULT 0.35,
    p_w_culture        DOUBLE PRECISION DEFAULT 0.30,
    p_w_food           DOUBLE PRECISION DEFAULT 0.25,
    p_w_low_crowding   DOUBLE PRECISION DEFAULT 0.10,
    p_limit            INT DEFAULT 15
)
RETURNS TABLE (
    destination_id     TEXT,
    name               TEXT,
    country_iso2       CHAR(2),
    population         INT,
    iata_codes         TEXT[],
    final_score        DOUBLE PRECISION,
    data_completeness  DOUBLE PRECISION,
    metrics            JSONB,
    provenance         JSONB
)
LANGUAGE sql STABLE AS $$
WITH window_climate AS (
    SELECT
        c.destination_id,
        AVG(c.median_temp_max_c) AS avg_max_c,
        MAX(c.median_temp_max_c) AS peak_month_max_c,
        AVG(c.precip_mm)         AS avg_precip_mm,
        AVG(c.avg_rainy_days)    AS avg_rainy_days_mo,
        MIN(c.source)            AS climate_source,
        MIN(c.observed_period)   AS climate_period
    FROM climate_monthly c
    WHERE c.month = ANY(p_months)
    GROUP BY c.destination_id
),
filtered AS (
    SELECT
        d.destination_id,
        d.name,
        d.country_iso2,
        d.population,
        d.iata_codes,
        wc.avg_max_c,
        wc.peak_month_max_c,
        wc.avg_precip_mm,
        wc.avg_rainy_days_mo,
        p.museum_culture_count,
        p.restaurant_count,
        p.cuisine_entropy,
        t.nights_per_resident,
        wc.climate_source,
        wc.climate_period,
        p.source                 AS poi_source,
        p.observed_period        AS poi_period,
        t.source                 AS crowding_source,
        t.observed_period        AS crowding_period
    FROM destinations d
    LEFT JOIN window_climate wc  ON wc.destination_id = d.destination_id
    LEFT JOIN poi_metrics p      ON p.destination_id  = d.destination_id
    LEFT JOIN tourism_pressure t ON t.destination_id  = d.destination_id
    WHERE d.population >= p_min_population
      AND (p_countries IS NULL OR d.country_iso2 = ANY(p_countries))
      AND (NOT p_require_airport OR cardinality(d.iata_codes) > 0)
      AND (wc.peak_month_max_c IS NULL OR wc.peak_month_max_c <= p_max_temp_c)
),
culture_ranks AS (
    SELECT destination_id,
           PERCENT_RANK() OVER (ORDER BY LN(1 + museum_culture_count)) AS s_culture
    FROM filtered
    WHERE museum_culture_count IS NOT NULL
),
food_ranks AS (
    SELECT destination_id,
           0.50 * PERCENT_RANK() OVER (ORDER BY LN(1 + restaurant_count))
         + 0.50 * PERCENT_RANK() OVER (ORDER BY COALESCE(cuisine_entropy, 0)) AS s_food
    FROM filtered
    WHERE restaurant_count IS NOT NULL
),
crowding_ranks AS (
    SELECT destination_id,
           1.0 - PERCENT_RANK() OVER (ORDER BY nights_per_resident) AS s_low_crowding
    FROM filtered
    WHERE nights_per_resident IS NOT NULL
),
scored AS (
    SELECT
        f.*,
        CASE WHEN f.avg_max_c IS NULL THEN NULL ELSE
            0.70 * GREATEST(0.0, 1.0 - 0.10 * GREATEST(
                p_ideal_temp_min - f.avg_max_c,
                f.avg_max_c - p_ideal_temp_max,
                0.0
            ))
            + 0.30 * CASE
                WHEN f.avg_rainy_days_mo IS NOT NULL THEN
                    GREATEST(0.0, 1.0 - f.avg_rainy_days_mo / 20.0)
                WHEN f.avg_precip_mm IS NOT NULL THEN
                    GREATEST(0.0, 1.0 - f.avg_precip_mm / 180.0)
                ELSE 0.5
            END
        END AS s_climate,
        cr.s_culture,
        fr.s_food,
        tr.s_low_crowding
    FROM filtered f
    LEFT JOIN culture_ranks cr  ON cr.destination_id = f.destination_id
    LEFT JOIN food_ranks fr     ON fr.destination_id = f.destination_id
    LEFT JOIN crowding_ranks tr ON tr.destination_id = f.destination_id
),
weighted AS (
    SELECT
        s.*,
        (CASE WHEN s.s_climate      IS NOT NULL THEN p_w_climate      ELSE 0 END
       + CASE WHEN s.s_culture      IS NOT NULL THEN p_w_culture      ELSE 0 END
       + CASE WHEN s.s_food         IS NOT NULL THEN p_w_food         ELSE 0 END
       + CASE WHEN s.s_low_crowding IS NOT NULL THEN p_w_low_crowding ELSE 0 END) AS active_weight,
        (COALESCE(s.s_climate      * p_w_climate,      0)
       + COALESCE(s.s_culture      * p_w_culture,      0)
       + COALESCE(s.s_food         * p_w_food,         0)
       + COALESCE(s.s_low_crowding * p_w_low_crowding, 0)) AS raw_weighted_sum
    FROM scored s
)
SELECT
    w.destination_id,
    w.name,
    w.country_iso2,
    w.population,
    w.iata_codes,
    ROUND((w.raw_weighted_sum / NULLIF(w.active_weight, 0))::NUMERIC, 4)::DOUBLE PRECISION AS final_score,
    ROUND((w.active_weight / NULLIF(p_w_climate + p_w_culture + p_w_food + p_w_low_crowding, 0))::NUMERIC, 2)::DOUBLE PRECISION AS data_completeness,
    jsonb_build_object(
        'avg_max_c', ROUND(w.avg_max_c::NUMERIC, 1),
        'peak_month_max_c', ROUND(w.peak_month_max_c::NUMERIC, 1),
        'avg_precip_mm', ROUND(w.avg_precip_mm::NUMERIC, 1),
        'avg_rainy_days_mo', ROUND(w.avg_rainy_days_mo::NUMERIC, 2),
        'museum_culture_count_5km', w.museum_culture_count,
        'restaurant_count_5km', w.restaurant_count,
        'cuisine_entropy', ROUND(w.cuisine_entropy::NUMERIC, 3),
        'nights_per_resident', ROUND(w.nights_per_resident::NUMERIC, 2)
    ) AS metrics,
    jsonb_build_object(
        'climate',  jsonb_build_object('source', w.climate_source,  'period', w.climate_period),
        'poi',      jsonb_build_object('source', w.poi_source,      'period', w.poi_period),
        'crowding', jsonb_build_object('source', w.crowding_source, 'period', w.crowding_period)
    ) AS provenance
FROM weighted w
WHERE w.active_weight > 0
ORDER BY final_score DESC, w.population DESC
LIMIT p_limit;
$$;
"""

# Columns each table must have for this script to work. CREATE TABLE IF NOT
# EXISTS will silently keep an older, incompatible Phase-1 table, so check.
REQUIRED_COLUMNS = {
    "destinations": {
        "destination_id", "geoname_id", "name", "ascii_name", "country_iso2",
        "timezone", "centroid", "lat", "lon", "population", "nuts3_code", "iata_codes",
    },
    "climate_monthly": {
        "destination_id", "month", "median_temp_max_c", "median_temp_min_c",
        "precip_mm", "avg_rainy_days", "avg_hot_days_30c", "source",
        "observed_period", "updated_at",
    },
    "poi_metrics": {
        "destination_id", "radius_km", "museum_culture_count", "restaurant_count",
        "cuisine_entropy", "source", "observed_period", "updated_at",
    },
    "tourism_pressure": {
        "destination_id", "tourist_nights_annual", "nights_per_resident",
        "source", "observed_period", "updated_at",
    },
}


def log(msg: str) -> None:
    print(msg, flush=True)


def timed(name: str):
    class _T:
        def __enter__(self):
            self.t0 = time.time()
            return self

        def __exit__(self, *exc):
            log(f"  ({name}: {time.time() - self.t0:.1f}s)")

    return _T()


def _execute_with_retries(
    duck: duckdb.DuckDBPyConnection, sql: str, attempts: int = 5, base_wait: int = 20
) -> pd.DataFrame:
    """Retry long S3 scans; transient DNS/HTTP errors are common."""
    for attempt in range(1, attempts + 1):
        try:
            return duck.execute(sql).fetchdf()
        except duckdb.Error as exc:
            if attempt == attempts:
                raise
            wait = base_wait * attempt
            log(f"  DuckDB/S3 error ({exc}); retrying in {wait}s "
                f"(attempt {attempt + 1}/{attempts}) ...")
            time.sleep(wait)
    raise AssertionError("unreachable")


def download_cached(url: str, dest: Path, retries: int = 3) -> Path:
    """Download once into the cache, with a .part file and retry on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest

    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, retries + 1):
        try:
            log(f"  downloading {url} -> {dest.name} (attempt {attempt}/{retries}) ...")
            with requests.get(url, stream=True, timeout=(30, 300)) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            if dest.suffix == ".zip" and not zipfile.is_zipfile(tmp):
                raise ValueError("downloaded file is not a valid zip")
            tmp.replace(dest)
            return dest
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            if attempt == retries:
                raise
            log(f"  download failed ({exc}); retrying ...")
            time.sleep(2 * attempt)
    return dest


def init_db(conn: psycopg.Connection) -> None:
    log("[1/5] Applying PostGIS schema and v2 SQL scorer ...")
    with conn.cursor() as cur:
        try:
            cur.execute(SCHEMA_SQL)
        except psycopg.errors.UndefinedFile as exc:
            raise SystemExit(
                "PostGIS is not available on this database. On Supabase enable it "
                "under Database -> Extensions -> postgis, then re-run."
            ) from exc
        cur.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            """,
            (list(REQUIRED_COLUMNS),),
        )
        found: dict[str, set[str]] = defaultdict(set)
        for table, column in cur.fetchall():
            found[table].add(column)
    conn.commit()

    # Check before creating the scorer: a LANGUAGE sql function body is
    # validated at creation and would otherwise fail with a column-level
    # Postgres error instead of this message.
    problems = []
    for table, required in REQUIRED_COLUMNS.items():
        missing = required - found.get(table, set())
        if missing:
            problems.append(f"  {table}: missing {', '.join(sorted(missing))}")
    if problems:
        raise SystemExit(
            "Existing tables are not compatible with this schema:\n"
            + "\n".join(problems)
            + "\nUse a fresh database/schema, or migrate the Phase-1 tables first."
        )

    with conn.cursor() as cur:
        cur.execute(SCORER_SQL)
    conn.commit()


def load_geonames_cities(
    conn: psycopg.Connection,
    min_pop: int,
    countries: list[str] | None,
    metro_dedupe: bool = True,
    dedupe_radius_km: float = 20.0,
    dedupe_pop_ratio: float = 2.0,
    municipality_radius_km: float = 15.0,
    municipality_pop_ratio: float = 5.0,
    prune: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    log(f"[2/5] Loading GeoNames cities (population >= {min_pop:,}) ...")
    zip_path = download_cached(GEONAMES_URL, CACHE_DIR / "cities15000.zip")

    cols = [
        "geoname_id", "name", "ascii_name", "alt", "lat", "lon",
        "fclass", "fcode", "country_iso2", "cc2", "a1", "a2", "a3", "a4",
        "population", "elev", "dem", "timezone", "mod",
    ]
    with zipfile.ZipFile(zip_path) as zf, zf.open("cities15000.txt") as f:
        df = pd.read_csv(
            f, sep="\t", header=None, names=cols,
            usecols=[
                "geoname_id", "name", "ascii_name", "lat", "lon", "fcode",
                "country_iso2", "population", "timezone", "a1", "a2", "a3",
            ],
            dtype={
                "country_iso2": str, "fcode": str, "timezone": str,
                "a1": str, "a2": str, "a3": str,
                "geoname_id": "int64", "population": "int64",
            },
            keep_default_na=False,
        )

    df = df[
        (df["population"] >= min_pop)
        & (df["fcode"] != "PPLX")            # sub-city sections (e.g. Tokyo wards)
        & (df["country_iso2"].str.len() == 2)
    ].copy()

    if countries:
        df = df[df["country_iso2"].isin(countries)].copy()

    df["timezone"] = df["timezone"].replace("", None)
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)

    dropped = pd.DataFrame(columns=[
        "name", "country_iso2", "population", "fcode", "near_name", "near_population", "distance_km"
    ])
    if metro_dedupe and len(df):
        df, dropped = dedupe_metro_areas(
            df, dedupe_radius_km, dedupe_pop_ratio,
            municipality_radius_km, municipality_pop_ratio,
        )

    if df.empty:
        raise SystemExit(
            f"No GeoNames cities matched (min-pop={min_pop:,}, countries={countries})."
        )

    df["destination_id"] = "gn-" + df["geoname_id"].astype(str)
    log(
        f"  selected {len(df):,} cities in {df['country_iso2'].nunique()} countries"
        + (f"; dropped {len(dropped):,} metro subdivisions" if len(dropped) else "")
        + "."
    )
    if len(dropped):
        examples = dropped.sort_values("population", ascending=False).head(5)
        log("  largest merged subdivisions: " + ", ".join(
            f"{r.name}->{r.near_name}" for r in examples.itertuples()
        ))

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_dest (
                destination_id TEXT, geoname_id INT, name TEXT, ascii_name TEXT,
                country_iso2 CHAR(2), timezone TEXT, lat DOUBLE PRECISION,
                lon DOUBLE PRECISION, population INT
            ) ON COMMIT DROP;
        """)
        with cur.copy("COPY tmp_dest FROM STDIN") as copy:
            for row in df[[
                "destination_id", "geoname_id", "name", "ascii_name",
                "country_iso2", "timezone", "lat", "lon", "population",
            ]].itertuples(index=False):
                copy.write_row(row)

        cur.execute("""
            INSERT INTO destinations (
                destination_id, geoname_id, name, ascii_name, country_iso2,
                timezone, centroid, lat, lon, population
            )
            SELECT
                destination_id, geoname_id, name, ascii_name, country_iso2,
                timezone, ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography,
                lat, lon, population
            FROM tmp_dest
            ON CONFLICT (destination_id) DO UPDATE SET
                name        = EXCLUDED.name,
                ascii_name  = EXCLUDED.ascii_name,
                population  = EXCLUDED.population,
                timezone    = EXCLUDED.timezone,
                lat         = EXCLUDED.lat,
                lon         = EXCLUDED.lon,
                centroid    = EXCLUDED.centroid;
        """)

        if prune:
            # Authoritative sync for the selected scope: remove rows that this
            # run no longer selects (dedupe changes, population threshold
            # changes, or cities removed from GeoNames). FKs cascade.
            cur.execute(
                """
                DELETE FROM destinations d
                WHERE (%s::text[] IS NULL OR d.country_iso2 = ANY(%s::text[]))
                  AND NOT EXISTS (
                      SELECT 1 FROM tmp_dest t
                      WHERE t.destination_id = d.destination_id
                  )
                """,
                (countries, countries),
            )
            if cur.rowcount:
                log(f"  pruned {cur.rowcount:,} destinations outside this run.")
    conn.commit()

    return (
        df[["destination_id", "name", "country_iso2", "lat", "lon", "population"]].copy(),
        dropped,
    )


def dedupe_metro_areas(
    df: pd.DataFrame,
    radius_km: float = 20.0,
    pop_ratio: float = 2.0,
    municipality_radius_km: float = 15.0,
    municipality_pop_ratio: float = 5.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Merge sub-city GeoNames records into the larger city they belong to.

    Two rules, both requiring a nearby city at least `pop_ratio` times larger:
      1. PPLA2 "borough" records (NYC's boroughs are PPLA2, not PPLX) within
         `radius_km`.
      2. Any record that shares country/admin1/admin2/admin3 with a city at
         least `municipality_pop_ratio` times larger within
         `municipality_radius_km`; this catches districts such as Barcelona's
         Sant Martí, Madrid's Chamartín or the Paris arrondissements without
         merging peers such as Venice/Mestre or Grevenbroich/Neuss.

    Independent municipalities in the same metro (L'Hospitalet, Badalona,
    Cambridge, Oakland) are kept. Dropped rows stay in the spatial grid so
    chains resolve transitively (a district of a borough still merges).
    """
    work = df.sort_values("population", ascending=False)
    cell = max(radius_km, municipality_radius_km) / 111.0
    cell = max(cell, 0.05)
    y_cells = int(math.ceil(180.0 / cell)) + 1
    x_cells = int(math.ceil(360.0 / cell)) + 1
    grid: dict[tuple[int, int], list] = defaultdict(list)

    kept_rows = []
    dropped_rows = []
    min_ratio = min(pop_ratio, municipality_pop_ratio)

    for row in work.itertuples(index=False):
        gy = int(math.floor((row.lat + 90.0) / cell)) % y_cells
        gx = int(math.floor((row.lon + 180.0) / cell)) % x_cells
        row_admin = (row.a1, row.a2, row.a3)
        match = None

        for dy in (-1, 0, 1):
            if match:
                break
            for dx in (-1, 0, 1):
                for near in grid[((gy + dy) % y_cells, (gx + dx) % x_cells)]:
                    if near.country_iso2 != row.country_iso2:
                        continue
                    if near.population < min_ratio * row.population:
                        continue
                    dist = haversine_km(row.lat, row.lon, near.lat, near.lon)
                    if row.fcode in BOROUGH_FCODES and dist <= radius_km:
                        match = (near, dist, "borough")
                        break
                    if (
                        row.a3
                        and row_admin == (near.a1, near.a2, near.a3)
                        and near.population >= municipality_pop_ratio * row.population
                        and dist <= municipality_radius_km
                    ):
                        match = (near, dist, "municipality")
                        break
                if match:
                    break

        if match:
            near, dist, reason = match
            dropped_rows.append({
                "name": row.name,
                "country_iso2": row.country_iso2,
                "population": row.population,
                "fcode": row.fcode,
                "near_name": near.name,
                "near_population": near.population,
                "distance_km": round(dist, 1),
                "reason": reason,
            })
        else:
            kept_rows.append(tuple(row))

        # Keep dropped rows in the grid: a district of a merged borough must
        # still find that borough as its parent.
        grid[(gy, gx)].append(row)

    kept = pd.DataFrame(kept_rows, columns=list(df.columns))
    dropped = pd.DataFrame(
        dropped_rows,
        columns=[
            "name", "country_iso2", "population", "fcode",
            "near_name", "near_population", "distance_km", "reason",
        ],
    )
    return kept, dropped


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(a)))


def attach_nearest_airports(
    conn: psycopg.Connection, cities_df: pd.DataFrame, radius_km: float = 100.0
) -> None:
    log(f"[3/5] Attaching commercial IATA airports within {radius_km:.0f} km ...")
    csv_path = download_cached(OURAIRPORTS_URL, CACHE_DIR / "airports.csv")
    adf = pd.read_csv(
        csv_path,
        usecols=["type", "latitude_deg", "longitude_deg", "scheduled_service", "iata_code"],
        dtype=str,
    )
    adf = adf[
        adf["type"].isin(["large_airport", "medium_airport"])
        & (adf["scheduled_service"] == "yes")
        & adf["iata_code"].notna()
        & (adf["iata_code"].str.len() == 3)
    ].copy()
    adf["lat"] = adf["latitude_deg"].astype(float)
    adf["lon"] = adf["longitude_deg"].astype(float)
    adf = adf.drop_duplicates("iata_code")

    with conn.cursor() as cur:
        # Restrict updates to this run's cities so a --countries run cannot
        # clear airports belonging to a previously loaded global dataset.
        cur.execute("CREATE TEMP TABLE tmp_city_ids (destination_id TEXT PRIMARY KEY) ON COMMIT DROP;")
        with cur.copy("COPY tmp_city_ids FROM STDIN") as copy:
            for (destination_id,) in cities_df[["destination_id"]].itertuples(index=False):
                copy.write_row((destination_id,))

        cur.execute("""
            CREATE TEMP TABLE tmp_airports (
                iata TEXT, type TEXT, lat DOUBLE PRECISION, lon DOUBLE PRECISION,
                geom GEOGRAPHY(Point, 4326)
            ) ON COMMIT DROP;
        """)
        with cur.copy("COPY tmp_airports (iata, type, lat, lon) FROM STDIN") as copy:
            for row in adf[["iata_code", "type", "lat", "lon"]].itertuples(index=False):
                copy.write_row(row)

        cur.execute("""
            UPDATE tmp_airports
            SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography
        """)
        cur.execute("CREATE INDEX ON tmp_airports USING GIST (geom);")

        # Reset only this run's codes, then recompute.
        cur.execute("""
            UPDATE destinations d
            SET iata_codes = '{}'
            FROM tmp_city_ids t
            WHERE d.destination_id = t.destination_id;
        """)
        cur.execute(
            """
            WITH matched AS (
                SELECT
                    d.destination_id,
                    -- Prefer large (international) airports over nearby GA
                    -- fields, then distance. Otherwise Teterboro outranks JFK
                    -- and San Carlos outranks San Jose for NYC/SF.
                    ARRAY_AGG(
                        a.iata
                        ORDER BY (a.type <> 'large_airport'),
                                 ST_Distance(d.centroid, a.geom)
                    ) AS codes
                FROM destinations d
                JOIN tmp_city_ids t ON t.destination_id = d.destination_id
                JOIN tmp_airports a ON ST_DWithin(d.centroid, a.geom, %s)
                GROUP BY d.destination_id
            )
            UPDATE destinations d
            SET iata_codes = m.codes[1:3]
            FROM matched m
            WHERE d.destination_id = m.destination_id;
            """,
            (radius_km * 1000.0,),
        )
        matched = cur.rowcount

        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE cardinality(iata_codes) > 0), COUNT(*)
            FROM destinations d
            JOIN tmp_city_ids t ON t.destination_id = d.destination_id;
        """)
        with_airport, total = cur.fetchone()
        log(f"  matched airports for {matched:,}/{total:,} cities "
            f"({with_airport:,} have at least one IATA code).")
    conn.commit()


def _sample_worldclim_zip(
    zip_path: Path, coords: list[tuple[float, float]]
) -> dict[int, np.ndarray]:
    """Sample 12 monthly rasters at (lon, lat) pairs, returning values in °C/mm."""
    out: dict[int, np.ndarray] = {}
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()

    for month in range(1, 13):
        suffix = f"_{month:02d}.tif"
        member = next(n for n in names if n.endswith(suffix))
        vsi_path = f"/vsizip/{zip_path.resolve()}/{member}"
        with rasterio.open(vsi_path) as src:
            vals = np.array([v[0] for v in src.sample(coords)], dtype=np.float64)
            nodata = src.nodata
            invalid = ~np.isfinite(vals) | (np.abs(vals) > 1e10)
            if nodata is not None:
                invalid |= np.isclose(vals, nodata)
            vals[invalid] = np.nan
            # WorldClim 5m rasters are float32 °C/mm (scale 1), but 10m/2.5m
            # products are sometimes scaled integers; apply metadata if present.
            scale = float(src.scales[0]) if src.scales else 1.0
            offset = float(src.offsets[0]) if src.offsets else 0.0
            if scale != 1.0 or offset != 0.0:
                vals = vals * scale + offset
            out[month] = vals
    return out


def ingest_worldclim_climate(conn: psycopg.Connection, cities_df: pd.DataFrame) -> None:
    log("[4/5] Sampling WorldClim 2.1 (5m) monthly climate for all cities ...")
    tmax_zip = download_cached(f"{WORLDCLIM_BASE}/wc2.1_5m_tmax.zip", CACHE_DIR / "wc2.1_5m_tmax.zip")
    tmin_zip = download_cached(f"{WORLDCLIM_BASE}/wc2.1_5m_tmin.zip", CACHE_DIR / "wc2.1_5m_tmin.zip")
    prec_zip = download_cached(f"{WORLDCLIM_BASE}/wc2.1_5m_prec.zip", CACHE_DIR / "wc2.1_5m_prec.zip")

    coords = list(zip(cities_df["lon"].tolist(), cities_df["lat"].tolist()))
    dest_ids = cities_df["destination_id"].tolist()

    with timed("raster sampling"):
        tmax = _sample_worldclim_zip(tmax_zip, coords)
        tmin = _sample_worldclim_zip(tmin_zip, coords)
        prec = _sample_worldclim_zip(prec_zip, coords)

    log("  writing city-month rows via COPY ...")
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_clim (
                destination_id TEXT, month SMALLINT,
                median_temp_max_c DOUBLE PRECISION,
                median_temp_min_c DOUBLE PRECISION,
                precip_mm DOUBLE PRECISION,
                source TEXT, observed_period TEXT
            ) ON COMMIT DROP;
        """)
        with cur.copy("COPY tmp_clim FROM STDIN") as copy:
            for month in range(1, 13):
                tx, tn, pr = tmax[month], tmin[month], prec[month]
                for i, cid in enumerate(dest_ids):
                    v_tx = None if np.isnan(tx[i]) else round(float(tx[i]), 2)
                    v_tn = None if np.isnan(tn[i]) else round(float(tn[i]), 2)
                    v_pr = None if (np.isnan(pr[i]) or pr[i] < 0) else round(float(pr[i]), 1)
                    if v_tx is None and v_tn is None and v_pr is None:
                        continue
                    copy.write_row((
                        cid, month, v_tx, v_tn, v_pr,
                        "WorldClim 2.1 (5m)", "1970-2000",
                    ))

        cur.execute("""
            INSERT INTO climate_monthly (
                destination_id, month, median_temp_max_c, median_temp_min_c,
                precip_mm, source, observed_period
            )
            SELECT * FROM tmp_clim
            ON CONFLICT (destination_id, month) DO UPDATE SET
                median_temp_max_c = COALESCE(
                    climate_monthly.median_temp_max_c, EXCLUDED.median_temp_max_c),
                median_temp_min_c = COALESCE(
                    climate_monthly.median_temp_min_c, EXCLUDED.median_temp_min_c),
                precip_mm  = EXCLUDED.precip_mm,
                updated_at = NOW();
        """)
        log(f"  upserted {cur.rowcount:,} city-month climate rows.")
    conn.commit()


def _scan_envelopes(
    cities_df: pd.DataFrame, pad: float = 0.25, max_countries: int = 40
) -> list[tuple[float, float, float, float]]:
    """
    Return (min_lon, max_lon, min_lat, max_lat) envelopes to scan.

    One envelope per country lets each Parquet scan prune to its own row
    groups. A single OR-of-envelopes filter would be correct but blocks
    DuckDB's row-group pruning and turns every --countries run into a global
    scan. Countries whose cities straddle the antimeridian fall back to the
    whole world (correct, rare); a full global run is one world scan.
    """
    if cities_df.empty:
        return []

    if cities_df["country_iso2"].nunique() > max_countries:
        return [(-180.0, 180.0, -90.0, 90.0)]

    grouped = cities_df.groupby("country_iso2", dropna=False).agg(
        min_lat=("lat", "min"), max_lat=("lat", "max"),
        min_lon=("lon", "min"), max_lon=("lon", "max"),
    )

    envelopes = []
    for row in grouped.itertuples(index=False):
        min_lon = float(row.min_lon)
        max_lon = float(row.max_lon)
        min_lat = max(-90.0, float(row.min_lat) - pad)
        max_lat = min(90.0, float(row.max_lat) + pad)
        if max_lon - min_lon > 180.0:
            envelopes.append((-180.0, 180.0, min_lat, max_lat))
        else:
            envelopes.append((
                max(-180.0, min_lon - pad),
                min(180.0, max_lon + pad),
                min_lat, max_lat,
            ))
    return envelopes


def _places_scan_sql(
    places_s3: str, envelopes: list[tuple[float, float, float, float]]
) -> str:
    selects = []
    for min_lon, max_lon, min_lat, max_lat in envelopes:
        selects.append(f"""
            SELECT
                id,
                (bbox.ymin + bbox.ymax) / 2.0 AS plat,
                (bbox.xmin + bbox.xmax) / 2.0 AS plon,
                CAST(FLOOR(((bbox.ymin + bbox.ymax) / 2.0) * 10) AS INT) AS gy,
                ((CAST(FLOOR(((bbox.xmin + bbox.xmax) / 2.0) * 10) AS INT)
                    % 3600) + 3600) % 3600 AS gx,
                COALESCE(list_contains(taxonomy.hierarchy, 'restaurant'), FALSE) AS is_rest,
                (
                    COALESCE(list_contains(taxonomy.hierarchy, 'cultural_and_historic'), FALSE)
                    OR COALESCE(list_contains(taxonomy.hierarchy, 'arts_and_entertainment'), FALSE)
                ) AS is_cult,
                -- Most specific cuisine label; generic "restaurant" is
                -- excluded so entropy measures cuisine mix, not the
                -- restaurant/no-cuisine split.
                CASE
                    WHEN taxonomy.primary IS NULL THEN NULL
                    WHEN taxonomy.primary = 'restaurant' THEN NULL
                    ELSE taxonomy.primary
                END AS cuisine_cat
            FROM read_parquet('{places_s3}', hive_partitioning=1)
            WHERE bbox.xmin <= {max_lon!r}
              AND bbox.xmax >= {min_lon!r}
              AND bbox.ymin <= {max_lat!r}
              AND bbox.ymax >= {min_lat!r}
              AND confidence >= 0.6
              AND COALESCE(operating_status, 'open') <> 'permanently_closed'
              AND (
                  COALESCE(list_contains(taxonomy.hierarchy, 'restaurant'), FALSE)
                  OR COALESCE(list_contains(taxonomy.hierarchy, 'cultural_and_historic'), FALSE)
                  OR COALESCE(list_contains(taxonomy.hierarchy, 'arts_and_entertainment'), FALSE)
              )
        """)

    # UNION ALL keeps the scans streaming. Overlapping country envelopes are
    # de-duplicated per (destination, place id) in `matched` below.
    return "\n            UNION ALL\n".join(selects)


def ingest_overture_pois_batch(
    conn: psycopg.Connection,
    cities_df: pd.DataFrame,
    release: str = DEFAULT_OVERTURE_RELEASE,
) -> None:
    log(f"[5/5] Running single-pass DuckDB spatial join over Overture Maps ({release}) ...")
    duck = duckdb.connect()
    try:
        duck.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
        # Hard cap: DuckDB defaults to 80% of host RAM, and the container has no
        # memory limit, so an uncapped global scan took down the whole Unraid host
        # on 2026-11-21. Spill to disk instead of growing past this.
        duck.execute(
            f"SET memory_limit='{os.environ.get('DUCKDB_MEMORY_LIMIT', '6GB')}'; "
            "SET temp_directory='/workspace/travel/.duckdb_tmp'; "
            "SET preserve_insertion_order=false;"
        )
        try:
            # Fewer concurrent S3 clients make resolver failures less likely.
            # Not every DuckDB build exposes these settings.
            duck.execute("SET threads=8; SET http_client_pool_capacity=16;")
        except duckdb.Error as exc:
            log(f"  (optional DuckDB HTTP tuning unavailable: {exc})")
        duck.register("cities_df", cities_df[["destination_id", "lat", "lon"]])

        places_s3 = f"{OVERTURE_S3_ROOT}/{release}/theme=places/type=place/*"
        envelopes = _scan_envelopes(cities_df)
        places_sql = _places_scan_sql(places_s3, envelopes)
        # Overlapping country envelopes can yield the same place twice; a
        # single global scan cannot, so skip the DISTINCT there.
        distinct = "DISTINCT " if len(envelopes) > 1 else ""

        # 0.1° grid hash join with a 5x3 halo (5 km guaranteed up to 78°N),
        # then exact Haversine. Grid indices wrap at the antimeridian.
        poi_df = _execute_with_retries(
            duck,
            f"""
            WITH city_cells AS (
                SELECT
                    c.destination_id,
                    c.lat AS clat,
                    c.lon AS clon,
                    (CAST(FLOOR(c.lat * 10) AS INT) + dy.d) AS gy,
                    ((CAST(FLOOR(c.lon * 10) AS INT) + dx.d) % 3600 + 3600) % 3600 AS gx
                FROM cities_df c
                CROSS JOIN (VALUES (-1), (0), (1)) AS dy(d)
                CROSS JOIN (VALUES (-2), (-1), (0), (1), (2)) AS dx(d)
            ),
            places AS (
                {places_sql}
            ),
            matched AS (
                SELECT {distinct}
                    cc.destination_id,
                    p.id,
                    p.is_rest,
                    p.is_cult,
                    p.cuisine_cat
                FROM places p
                JOIN city_cells cc ON cc.gy = p.gy AND cc.gx = p.gx
                WHERE 6371.0 * 2.0 * ASIN(SQRT(
                    POWER(SIN(RADIANS(p.plat - cc.clat) / 2.0), 2)
                    + COS(RADIANS(cc.clat)) * COS(RADIANS(p.plat))
                    * POWER(SIN(RADIANS(p.plon - cc.clon) / 2.0), 2)
                )) <= 5.0
            ),
            rest_count AS (
                SELECT destination_id, COUNT(*) AS restaurant_count
                FROM matched
                WHERE is_rest
                GROUP BY destination_id
            ),
            rest_cat_counts AS (
                SELECT destination_id, cuisine_cat, COUNT(*) AS cnt
                FROM matched
                WHERE is_rest AND cuisine_cat IS NOT NULL
                GROUP BY 1, 2
            ),
            rest_entropy AS (
                SELECT
                    destination_id,
                    -SUM((cnt::DOUBLE / total_cnt) * LN(cnt::DOUBLE / total_cnt)) AS cuisine_entropy
                FROM (
                    SELECT destination_id, cnt,
                           SUM(cnt) OVER (PARTITION BY destination_id) AS total_cnt
                    FROM rest_cat_counts
                )
                GROUP BY destination_id
            ),
            cult_counts AS (
                SELECT destination_id, COUNT(*) AS museum_culture_count
                FROM matched
                WHERE is_cult
                GROUP BY destination_id
            )
            SELECT
                c.destination_id,
                COALESCE(cu.museum_culture_count, 0) AS museum_culture_count,
                COALESCE(rc.restaurant_count, 0)     AS restaurant_count,
                COALESCE(re.cuisine_entropy, 0.0)    AS cuisine_entropy
            FROM cities_df c
            LEFT JOIN cult_counts cu  ON cu.destination_id = c.destination_id
            LEFT JOIN rest_count  rc  ON rc.destination_id = c.destination_id
            LEFT JOIN rest_entropy re ON re.destination_id = c.destination_id
            """,
        )
    finally:
        duck.close()

    if len(poi_df) != len(cities_df):
        raise RuntimeError(
            f"POI join returned {len(poi_df):,} rows for {len(cities_df):,} cities"
        )

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE tmp_poi (
                destination_id TEXT, museum_culture_count INT,
                restaurant_count INT, cuisine_entropy DOUBLE PRECISION,
                source TEXT, observed_period TEXT
            ) ON COMMIT DROP;
        """)
        with cur.copy("COPY tmp_poi FROM STDIN") as copy:
            for row in poi_df.itertuples(index=False):
                copy.write_row((
                    row.destination_id, int(row.museum_culture_count),
                    int(row.restaurant_count), round(float(row.cuisine_entropy), 4),
                    "Overture Maps Places", f"overture-{release}",
                ))
        cur.execute("""
            INSERT INTO poi_metrics (
                destination_id, radius_km, museum_culture_count, restaurant_count,
                cuisine_entropy, source, observed_period
            )
            SELECT destination_id, 5.0, museum_culture_count, restaurant_count,
                   cuisine_entropy, source, observed_period
            FROM tmp_poi
            ON CONFLICT (destination_id) DO UPDATE SET
                radius_km            = EXCLUDED.radius_km,
                museum_culture_count = EXCLUDED.museum_culture_count,
                restaurant_count     = EXCLUDED.restaurant_count,
                cuisine_entropy      = EXCLUDED.cuisine_entropy,
                source               = EXCLUDED.source,
                observed_period      = EXCLUDED.observed_period,
                updated_at           = NOW();
        """)
        log(f"  upserted POI metrics for {cur.rowcount:,} cities.")
    conn.commit()


def print_coverage(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM destinations),
                (SELECT COUNT(*) FROM destinations
                  WHERE cardinality(iata_codes) > 0),
                (SELECT COUNT(*) FROM destinations d
                  WHERE EXISTS (SELECT 1 FROM climate_monthly c
                                WHERE c.destination_id = d.destination_id)),
                (SELECT COUNT(*) FROM destinations d
                  WHERE EXISTS (SELECT 1 FROM poi_metrics p
                                WHERE p.destination_id = d.destination_id));
        """)
        cities, airports, climate, pois = cur.fetchone()
    log(
        f"\nCoverage: {cities:,} cities | {airports:,} with IATA | "
        f"{climate:,} with climate | {pois:,} with POIs"
    )


def run_verification(
    conn: psycopg.Connection, countries: list[str] | None = None
) -> None:
    print_coverage(conn)
    scope = f" ({', '.join(countries)})" if countries else " Global"
    queries = [
        (f"Top 10{scope} (1-month trip in June, airport required)",
         """SELECT name, country_iso2, population, iata_codes, final_score,
                   data_completeness, metrics
            FROM rank_destinations_v2(
                p_months => ARRAY[6], p_ideal_temp_min => 20.0,
                p_ideal_temp_max => 26.0, p_max_temp_c => 31.0,
                p_min_population => 40000, p_countries => %s::text[],
                p_require_airport => TRUE, p_w_climate => 0.35,
                p_w_culture => 0.30, p_w_food => 0.25,
                p_w_low_crowding => 0.10, p_limit => 10);"""),
        (f"Top 10{scope} (4-month stay June-Sept, peak month <= 28C)",
         """SELECT name, country_iso2, population, iata_codes, final_score,
                   data_completeness, metrics
            FROM rank_destinations_v2(
                p_months => ARRAY[6,7,8,9], p_ideal_temp_min => 20.0,
                p_ideal_temp_max => 26.0, p_max_temp_c => 28.0,
                p_min_population => 40000, p_countries => %s::text[],
                p_require_airport => TRUE, p_w_climate => 0.35,
                p_w_culture => 0.30, p_w_food => 0.25,
                p_w_low_crowding => 0.10, p_limit => 10);"""),
    ]
    for title, sql in queries:
        log(f"\n=== {title} ===")
        with conn.cursor() as cur:
            cur.execute(sql, (countries,))
            rows = cur.fetchall()
        if not rows:
            log("  (no results)")
        for rank, (name, cc, pop, iatas, score, comp, m) in enumerate(rows, 1):
            log(
                f"{rank:2d}. {name:18s} ({cc}) pop={pop:>8,} | "
                f"iata={','.join((iatas or [])[:2]):7s} | "
                f"score={score:.4f} | maxC={m.get('avg_max_c')} | "
                f"mm={m.get('avg_precip_mm')} | cult={m.get('museum_culture_count_5km')} | "
                f"rest={m.get('restaurant_count_5km')} | H={m.get('cuisine_entropy')}"
            )


def connect_db(db_url: str) -> psycopg.Connection:
    kwargs: dict = {
        "connect_timeout": 30,
        "application_name": "bootstrap_global_40k",
    }
    # Supabase's transaction pooler (6543) and session pooler do not support
    # client-side prepared statements reliably.
    if "pooler.supabase.com" in db_url or ":6543" in db_url:
        kwargs["prepare_threshold"] = None
    conn = psycopg.connect(db_url, **kwargs)
    conn.execute("SET TIME ZONE 'UTC'")
    # Long COPY/upsert steps should not be cut off by a low session default.
    conn.execute("SET statement_timeout = 0")
    return conn


def main() -> None:
    global CACHE_DIR
    parser = argparse.ArgumentParser(
        description="Global >=40k Cities Travel DB Bootstrap"
    )
    parser.add_argument("--min-pop", type=int, default=40000)
    parser.add_argument(
        "--countries", type=str, default=None,
        help="Comma-separated ISO-2 codes for fast testing, e.g. SE,ES",
    )
    parser.add_argument("--skip-pois", action="store_true", help="Skip Overture Maps Parquet scan")
    parser.add_argument("--skip-climate", action="store_true", help="Skip WorldClim sampling")
    parser.add_argument("--skip-airports", action="store_true", help="Skip airport matching")
    parser.add_argument("--query-only", action="store_true", help="Run verification queries only")
    parser.add_argument(
        "--overture-release", default=DEFAULT_OVERTURE_RELEASE,
        help=f"Overture release, default {DEFAULT_OVERTURE_RELEASE}",
    )
    parser.add_argument(
        "--no-metro-dedupe", action="store_true",
        help="Keep PPLA2 boroughs and same-municipality districts "
             "(PPLX sections are always excluded)",
    )
    parser.add_argument(
        "--dedupe-radius-km", type=float, default=20.0,
        help="merge PPLA2 boroughs within this distance of a >=2x larger city",
    )
    parser.add_argument(
        "--municipality-radius-km", type=float, default=15.0,
        help="merge same-municipality districts within this distance",
    )
    parser.add_argument(
        "--dedupe-pop-ratio", type=float, default=2.0,
        help="PPLA2 borough must be this many times smaller than its parent",
    )
    parser.add_argument(
        "--municipality-pop-ratio", type=float, default=5.0,
        help="same-municipality district must be this many times smaller",
    )
    parser.add_argument(
        "--no-prune", action="store_true",
        help="keep destinations in scope that this run no longer selects",
    )
    parser.add_argument("--airport-km", type=float, default=100.0)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--db-url", default=None, help="Overrides SUPABASE_DB_URL")
    args = parser.parse_args()

    CACHE_DIR = args.cache_dir

    db_url = args.db_url or os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        log(
            "Error: set SUPABASE_DB_URL (or pass --db-url). Example:\n"
            "  export SUPABASE_DB_URL='postgresql://postgres:<PASSWORD>@"
            "db.<PROJECT_REF>.supabase.co:5432/postgres'",
        )
        sys.exit(1)

    countries = (
        [c.strip().upper() for c in args.countries.split(",") if c.strip()]
        if args.countries else None
    )

    conn = connect_db(db_url)
    try:
        if not args.query_only:
            init_db(conn)
            cities_df, _ = load_geonames_cities(
                conn, args.min_pop, countries,
                metro_dedupe=not args.no_metro_dedupe,
                dedupe_radius_km=args.dedupe_radius_km,
                dedupe_pop_ratio=args.dedupe_pop_ratio,
                municipality_radius_km=args.municipality_radius_km,
                municipality_pop_ratio=args.municipality_pop_ratio,
                prune=not args.no_prune,
            )
            if not args.skip_airports:
                attach_nearest_airports(conn, cities_df, args.airport_km)
            if not args.skip_climate:
                ingest_worldclim_climate(conn, cities_df)
            if not args.skip_pois:
                ingest_overture_pois_batch(conn, cities_df, args.overture_release)
        run_verification(conn, countries)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
