#!/usr/bin/env python3
"""
Authoritative metro (urban-centre) assignment from GHSL UCDB boundaries.

``street_network_metrics`` originally attached a destination to a GHSL urban
centre by name or by distance to the centre's published centroid. That produced
false merges: 18 Hanoi districts were attributed to ``Quoc Oai``, a separate
town 19 km away, because the name lookup missed and the 12 km centroid radius
won. This loader is the authoritative grouping instead:

* it loads the 11,422 GHSL UCDB R2024A urban-centre polygons from the GeoPackage
  already cached by ``bootstrap_street_network.py``;
* a destination whose point falls inside a polygon belongs to that centre
  (smallest polygon wins if boundaries ever overlap);
* a destination just outside every polygon (a coastal centroid, for example)
  may attach to the nearest polygon within ``MAX_EDGE_KM``, country name
  agreeing, and is labelled ``nearby`` with the edge distance;
* everything else stays unmatched rather than borrowing a distant city.

The table is display/grouping evidence. It does not replace the street-form
indicators, and it is not a walkability score.

Source: GHSL UCDB R2024A geography theme, European Commission JRC, CC BY 4.0.
https://human-settlement.emergency.copernicus.eu/

Usage
-----
    python bootstrap_metro.py
    python bootstrap_metro.py --report
    python bootstrap_metro.py --max-edge-km 3
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import struct
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bootstrap_street_network import CENTROIDS_GPKG_CACHE, mollweide_to_lonlat

LAYER = "GHSL_UCDB_THEME_GEOGRAPHY_GLOBE_R2024A"
SOURCE = "GHSL UCDB R2024A urban-centre boundary (European Commission JRC)"
SOURCE_URL = "https://human-settlement.emergency.copernicus.eu/"
PERIOD = "GHSL UCDB R2024A"
DEFAULT_MAX_EDGE_KM = 5.0

# GHSL/UCDB country labels that name the same country differently.
COUNTRY_ALIASES = {
    "México": "Mexico",
    "Netherlands": "The Netherlands",
    "Côte d'Ivoire": "Ivory Coast",
    "Palestine": "Palestinian Territory",
    "Réunion": "Reunion",
    "Curaçao": "Curacao",
    "Swaziland": "Eswatini",
    "Timor-Leste": "Timor Leste",
    "São Tomé and Príncipe": "Sao Tome and Principe",
}

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS ghsl_urban_centres (
    urban_centre_id  BIGINT PRIMARY KEY,
    name             TEXT NOT NULL,
    country          TEXT NOT NULL,
    area_km2         DOUBLE PRECISION,
    population       BIGINT,
    elevation_m      DOUBLE PRECISION,
    geom             geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ghsl_urban_centres_geom
    ON ghsl_urban_centres USING GIST (geom);

CREATE TABLE IF NOT EXISTS destination_metro (
    destination_id        TEXT PRIMARY KEY REFERENCES destinations(destination_id) ON DELETE CASCADE,
    urban_centre_id       BIGINT NOT NULL,
    urban_centre_name     TEXT NOT NULL,
    urban_centre_country  TEXT NOT NULL,
    match_kind            TEXT NOT NULL CHECK (match_kind IN ('contained', 'nearby')),
    match_distance_km     DOUBLE PRECISION NOT NULL,
    centre_area_km2       DOUBLE PRECISION,
    centre_population     BIGINT,
    centre_elevation_m    DOUBLE PRECISION,
    source                TEXT NOT NULL,
    source_url            TEXT NOT NULL,
    observed_period       TEXT NOT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_destination_metro_uc
    ON destination_metro (urban_centre_id);
"""


def log(message: str) -> None:
    print(message, flush=True)


def fix_mojibake(value: str | None) -> str | None:
    """Repair double-encoded UTF-8 in a few GHSL country labels."""
    if not value or ("Ã" not in value and "Â" not in value):
        return value
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if repaired else value


def _gpkg_geometry(blob: bytes) -> bytes:
    if len(blob) < 8 or blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry")
    flags = blob[3]
    envelope = (flags >> 1) & 0x07
    header_length = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(envelope, 0)
    return blob[header_length:]


def _read_wkb_polygons(wkb: bytes) -> list[list[list[tuple[float, float]]]]:
    if len(wkb) < 5:
        return []

    def read_geometry(offset: int) -> tuple[str, Any, int]:
        endian = "<" if wkb[offset] == 0x01 else ">"
        geometry_type = struct.unpack(endian + "I", wkb[offset + 1:offset + 5])[0]
        offset += 5
        # 2D Polygon (3) or MultiPolygon (6); reject Z/M variants.
        if geometry_type == 3:
            rings = []
            ring_count = struct.unpack(endian + "I", wkb[offset:offset + 4])[0]
            offset += 4
            for _ in range(ring_count):
                count = struct.unpack(endian + "I", wkb[offset:offset + 4])[0]
                offset += 4
                ring = []
                for _ in range(count):
                    x, y = struct.unpack(endian + "dd", wkb[offset:offset + 16])
                    offset += 16
                    ring.append((x, y))
                rings.append(ring)
            return "polygon", rings, offset
        if geometry_type == 6:
            polygons = []
            count = struct.unpack(endian + "I", wkb[offset:offset + 4])[0]
            offset += 4
            for _ in range(count):
                kind, rings, offset = read_geometry(offset)
                if kind == "polygon":
                    polygons.append(rings)
            return "multipolygon", polygons, offset
        raise ValueError(f"unsupported WKB geometry type {geometry_type}")

    kind, value, _ = read_geometry(0)
    if kind == "polygon":
        return [value]
    return value


def _wkb_multipolygon(polygons: list[list[list[tuple[float, float]]]]) -> bytes:
    parts = [b"\x01", struct.pack("<I", 6), struct.pack("<I", len(polygons))]
    for rings in polygons:
        parts.append(b"\x01")
        parts.append(struct.pack("<I", 3))
        parts.append(struct.pack("<I", len(rings)))
        for ring in rings:
            parts.append(struct.pack("<I", len(ring)))
            for lon, lat in ring:
                parts.append(struct.pack("<dd", lon, lat))
    return b"".join(parts)


def read_gpkg_polygons(
    path: Path, layer: str = LAYER
) -> list[dict[str, Any]]:
    """Read UCDB polygons and convert Mollweide metres to WGS84."""
    out: list[dict[str, Any]] = []
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT ID_UC_G0, GC_UCN_MAI_2025, GC_CNT_GAD_2025,
                   GC_UCA_KM2_2025, GC_POP_TOT_2025, GE_ELV_AVG_2025, geom
            FROM "{layer}"
            """
        )
        for uc_id, name, country, area, population, elevation, blob in rows:
            if not blob:
                continue
            try:
                polygons = _read_wkb_polygons(_gpkg_geometry(blob))
            except (ValueError, struct.error):
                continue
            converted = [
                [[mollweide_to_lonlat(x, y) for x, y in ring] for ring in rings]
                for rings in polygons
            ]
            out.append({
                "urban_centre_id": int(uc_id),
                "name": fix_mojibake(name) or "unnamed",
                "country": COUNTRY_ALIASES.get(
                    fix_mojibake(country) or "", fix_mojibake(country) or "Unknown"
                ),
                "area_km2": float(area) if area is not None else None,
                "population": int(population) if population is not None else None,
                "elevation_m": float(elevation) if elevation is not None else None,
                "geom": _wkb_multipolygon(converted),
            })
    return out


MATCH_SQL = """
WITH pts AS (
    SELECT d.destination_id,
           c.name AS country_name,
           ST_SetSRID(ST_MakePoint(d.lon, d.lat), 4326) AS geom
    FROM destinations d
    LEFT JOIN country_facts c ON c.country_iso2 = d.country_iso2
),
contained AS (
    SELECT DISTINCT ON (p.destination_id)
           p.destination_id, g.urban_centre_id, g.name, g.country,
           0.0::double precision AS distance_km,
           g.area_km2, g.population, g.elevation_m,
           'contained'::text AS match_kind
    FROM pts p
    JOIN ghsl_urban_centres g
      ON ST_Within(p.geom, g.geom)
     AND (
         p.country_name IS NULL
         OR g.country = p.country_name
         OR (p.country_name, g.country) IN (
             ('Hong Kong', 'China'), ('Macao', 'China')
         )
     )
    ORDER BY p.destination_id, ST_Area(g.geom)
),
rest AS (
    SELECT p.* FROM pts p
    WHERE NOT EXISTS (
        SELECT 1 FROM contained c WHERE c.destination_id = p.destination_id
    )
),
nearby AS (
    SELECT DISTINCT ON (p.destination_id)
           p.destination_id, g.urban_centre_id, g.name, g.country,
           ST_Distance(p.geom::geography, g.geom::geography) / 1000.0 AS distance_km,
           g.area_km2, g.population, g.elevation_m,
           'nearby'::text AS match_kind
    FROM rest p
    JOIN LATERAL (
        SELECT * FROM ghsl_urban_centres g2
        WHERE ST_DWithin(p.geom::geography, g2.geom::geography, %(max_edge_m)s)
          AND (
              p.country_name IS NULL
              OR g2.country = p.country_name
              OR (p.country_name, g2.country) IN (
                  ('Hong Kong', 'China'), ('Macao', 'China')
              )
          )
        ORDER BY g2.geom <-> p.geom
        LIMIT 1
    ) g ON TRUE
    ORDER BY p.destination_id
)
INSERT INTO destination_metro (
    destination_id, urban_centre_id, urban_centre_name, urban_centre_country,
    match_kind, match_distance_km, centre_area_km2, centre_population,
    centre_elevation_m, source, source_url, observed_period
)
SELECT destination_id, urban_centre_id, name, country, match_kind, distance_km,
       area_km2, population, elevation_m, %(source)s, %(source_url)s, %(period)s
FROM (
    SELECT * FROM contained
    UNION ALL
    SELECT * FROM nearby
) matched
ON CONFLICT (destination_id) DO UPDATE SET
    urban_centre_id = EXCLUDED.urban_centre_id,
    urban_centre_name = EXCLUDED.urban_centre_name,
    urban_centre_country = EXCLUDED.urban_centre_country,
    match_kind = EXCLUDED.match_kind,
    match_distance_km = EXCLUDED.match_distance_km,
    centre_area_km2 = EXCLUDED.centre_area_km2,
    centre_population = EXCLUDED.centre_population,
    centre_elevation_m = EXCLUDED.centre_elevation_m,
    source = EXCLUDED.source,
    source_url = EXCLUDED.source_url,
    observed_period = EXCLUDED.observed_period,
    updated_at = NOW()
"""


def load(database_url: str, max_edge_km: float) -> dict[str, Any]:
    path = CENTROIDS_GPKG_CACHE
    if not path.exists():
        raise SystemExit(
            f"{path} is missing; run bootstrap_street_network.py once to cache it."
        )
    centres = read_gpkg_polygons(path)
    log(f"read {len(centres):,} urban-centre polygons")
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("TRUNCATE ghsl_urban_centres")
            for centre in centres:
                cur.execute(
                    """
                    INSERT INTO ghsl_urban_centres (
                        urban_centre_id, name, country, area_km2, population,
                        elevation_m, geom
                    ) VALUES (
                        %(urban_centre_id)s, %(name)s, %(country)s, %(area_km2)s,
                        %(population)s, %(elevation_m)s,
                        ST_Multi(ST_GeomFromWKB(%(geom)s::bytea, 4326))
                    )
                    """,
                    centre,
                )
            cur.execute("DELETE FROM destination_metro")
            cur.execute(
                MATCH_SQL,
                {
                    "max_edge_m": max_edge_km * 1000.0,
                    "source": SOURCE,
                    "source_url": SOURCE_URL,
                    "period": PERIOD,
                },
            )
            cur.execute(
                """
                SELECT count(*) AS matched,
                       count(*) FILTER (WHERE match_kind = 'contained') AS contained,
                       count(*) FILTER (WHERE match_kind = 'nearby') AS nearby,
                       count(DISTINCT urban_centre_id) AS centres
                FROM destination_metro
                """
            )
            stats = dict(cur.fetchone())
            cur.execute("SELECT count(*) FROM destinations")
            total = cur.fetchone()["count"]
            cur.execute(
                """
                SELECT count(*) FROM destination_metro m
                JOIN destinations d USING (destination_id)
                LEFT JOIN country_facts c ON c.country_iso2 = d.country_iso2
                WHERE c.name IS DISTINCT FROM m.urban_centre_country
                  AND (c.name, m.urban_centre_country) NOT IN (
                      ('Hong Kong', 'China'), ('Macao', 'China')
                  )
                """
            )
            mismatches = cur.fetchone()["count"]
        conn.commit()
    stats["destinations"] = total
    stats["unmatched"] = total - stats["matched"]
    stats["country_mismatches"] = mismatches
    stats["max_edge_km"] = max_edge_km
    return stats


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS rows,
                       count(*) FILTER (WHERE match_kind = 'contained') AS contained,
                       count(*) FILTER (WHERE match_kind = 'nearby') AS nearby,
                       count(DISTINCT urban_centre_id) AS centres
                FROM destination_metro
                """
            )
            print(dict(cur.fetchone()))
            cur.execute(
                """
                SELECT urban_centre_name, count(*) AS destinations,
                       sum(d.population) AS city_population,
                       max(centre_population) AS centre_population
                FROM destination_metro m
                JOIN destinations d USING (destination_id)
                GROUP BY urban_centre_name, urban_centre_id
                ORDER BY count(*) DESC, sum(d.population) DESC
                LIMIT 15
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--max-edge-km", type=float, default=DEFAULT_MAX_EDGE_KM)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(load(args.db_url, args.max_edge_km))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
