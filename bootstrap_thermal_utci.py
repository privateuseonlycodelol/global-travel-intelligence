#!/usr/bin/env python3
"""
Global daily UTCI thermal safety from NASA POWER MERRA-2 daily spatial Zarr (AWS S3).

Streams 5 full recent years (2020-2024, 1,827 days) of global daily T2M_MAX,
T2M_MIN, RH2M, and WS10M directly from the public-domain NASA POWER AWS S3
spatial Zarr store (`power_merra2_daily_spatial_lst.zarr`), evaluates the
exact Bröde et al. (2012) UTCI 6th-order polynomial on every day for all
13,101 destinations, and computes the council's thermal safety score:

    heat    = 100 * clamp(1 - utci_days_gt_32 / 120)
    cold    = 100 * clamp(1 - utci_days_lt_0  / 150)
    severe  = 100 * clamp(1 - (utci_days_gt_38 + utci_days_lt_m13) / 30)
    thermal = 0.45 * heat + 0.30 * cold + 0.25 * severe

Also runs the council's mandatory overlap validation comparing computed
`utci_days_gt_32` against GHSL UCDB `urban_centre_climate.utci_over_32_days`.

Source: NASA POWER MERRA-2 Daily Spatial Zarr (Public Domain).
https://nasa-power.s3.us-west-2.amazonaws.com/merra2/spatial/power_merra2_daily_spatial_lst.zarr

Usage
-----
    uv run --with numcodecs --with-requirements requirements.txt python bootstrap_thermal_utci.py --db-url "$DATABASE_URL"
    uv run --with numcodecs --with-requirements requirements.txt python bootstrap_thermal_utci.py --db-url "$DATABASE_URL" --report
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
import psycopg
import requests
from psycopg.rows import dict_row

ZARR_BASE = (
    "https://nasa-power.s3.us-west-2.amazonaws.com/merra2/spatial/"
    "power_merra2_daily_spatial_lst.zarr"
)
EPOCH = dt.date(1980, 12, 31)
SOURCE = "NASA POWER MERRA-2 Daily Spatial Zarr (Bröde 2012 UTCI, 2020-2024)"
SOURCE_URL = "https://power.larc.nasa.gov/"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS destination_thermal_utci (
    destination_id      TEXT PRIMARY KEY
                        REFERENCES destinations(destination_id) ON DELETE CASCADE,
    utci_days_gt_32     DOUBLE PRECISION NOT NULL,
    utci_days_lt_0      DOUBLE PRECISION NOT NULL,
    utci_days_gt_38     DOUBLE PRECISION NOT NULL,
    utci_days_lt_m13    DOUBLE PRECISION NOT NULL,
    heat_score          DOUBLE PRECISION NOT NULL,
    cold_score          DOUBLE PRECISION NOT NULL,
    severe_score        DOUBLE PRECISION NOT NULL,
    thermal_safety_score DOUBLE PRECISION NOT NULL,
    days_sampled        INT NOT NULL,
    source              TEXT NOT NULL,
    source_url          TEXT NOT NULL,
    observed_period     TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _clamp_scalar(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def compute_thermal_safety_components(
    days_gt_32: float,
    days_lt_0: float,
    days_gt_38: float,
    days_lt_m13: float,
) -> tuple[float, float, float, float]:
    """Council UTCI thermal safety formula: 0.45 heat + 0.30 cold + 0.25 severe."""
    heat = _clamp_scalar(100.0 * (1.0 - days_gt_32 / 120.0))
    cold = _clamp_scalar(100.0 * (1.0 - days_lt_0 / 150.0))
    severe = _clamp_scalar(100.0 * (1.0 - (days_gt_38 + days_lt_m13) / 30.0))
    total = 0.45 * heat + 0.30 * cold + 0.25 * severe
    return heat, cold, severe, total


def utci_approx_vec(
    ta: np.ndarray,
    rh_pct: np.ndarray,
    ws10m: np.ndarray,
    d_tmrt: float = 0.0,
) -> np.ndarray:
    """Vectorized Bröde et al. (2012) UTCI 6th-order polynomial approximation.

    Parameters
    ----------
    ta : air temperature in °C
    rh_pct : relative humidity in % (0-100)
    ws10m : 10 m wind speed in m/s (clamped to [0.5, 17.0] per Bröde 2012)
    d_tmrt : Tmrt - Ta in °C (0.0 for shaded outdoor reference)
    """
    ta = np.asarray(ta, dtype=np.float64)
    va = np.clip(np.asarray(ws10m, dtype=np.float64), 0.5, 17.0)
    rh = np.clip(np.asarray(rh_pct, dtype=np.float64), 0.0, 100.0)
    # Saturation vapor pressure (kPa) via Tetens/Magnus formula
    es_kpa = 0.61094 * np.exp((17.625 * ta) / (ta + 243.04))
    pa = es_kpa * (rh / 100.0)
    d = float(d_tmrt)

    # Bröde et al. (2012) Int J Biometeorol 56:481-494 polynomial offset
    offset = (
        0.607562052
        + (-0.0227712343) * ta
        + (8.06470249e-4) * ta**2
        + (-1.54271372e-4) * ta**3
        + (-3.24651735e-6) * ta**4
        + (7.32602852e-8) * ta**5
        + (1.35959073e-9) * ta**6
        + (-2.25836520) * va
        + (0.0880326035) * ta * va
        + (0.00216844454) * ta**2 * va
        + (-1.53347087e-5) * ta**3 * va
        + (-5.72983704e-7) * ta**4 * va
        + (-2.55090145e-9) * ta**5 * va
        + (-0.751269505) * va**2
        + (-0.00408350271) * ta * va**2
        + (-5.21670675e-5) * ta**2 * va**2
        + (1.94544667e-6) * ta**3 * va**2
        + (1.14099531e-8) * ta**4 * va**2
        + (0.158137256) * va**3
        + (-6.57263143e-5) * ta * va**3
        + (2.22697524e-7) * ta**2 * va**3
        + (-4.16117031e-8) * ta**3 * va**3
        + (-0.0127762753) * va**4
        + (9.66891875e-6) * ta * va**4
        + (2.52785852e-9) * ta**2 * va**4
        + (4.56306672e-4) * va**5
        + (-1.74202546e-7) * ta * va**5
        + (-5.91491269e-6) * va**6
        + (0.398374029) * d
        + (1.83945314e-4) * ta * d
        + (-1.73754510e-4) * ta**2 * d
        + (-7.60781159e-7) * ta**3 * d
        + (3.77830287e-8) * ta**4 * d
        + (5.43079673e-10) * ta**5 * d
        + (-0.0200518269) * va * d
        + (8.92859837e-4) * ta * va * d
        + (3.45433048e-6) * ta**2 * va * d
        + (-3.77925774e-7) * ta**3 * va * d
        + (-1.69699377e-9) * ta**4 * va * d
        + (1.69992415e-4) * va**2 * d
        + (-4.99204314e-5) * ta * va**2 * d
        + (2.47417178e-7) * ta**2 * va**2 * d
        + (1.07596466e-8) * ta**3 * va**2 * d
        + (8.49242932e-5) * va**3 * d
        + (1.35191328e-6) * ta * va**3 * d
        + (-6.21531254e-9) * ta**2 * va**3 * d
        + (-4.99410301e-6) * va**4 * d
        + (-1.89489258e-8) * ta * va**4 * d
        + (8.15300114e-8) * va**5 * d
        + (7.55043090e-4) * d**2
        + (-5.65095215e-5) * ta * d**2
        + (-4.52166564e-7) * ta**2 * d**2
        + (2.46688878e-8) * ta**3 * d**2
        + (2.42674348e-10) * ta**4 * d**2
        + (1.54547250e-4) * va * d**2
        + (5.24110970e-6) * ta * va * d**2
        + (-8.75874982e-8) * ta**2 * va * d**2
        + (-1.50743064e-9) * ta**3 * va * d**2
        + (-1.56236307e-5) * va**2 * d**2
        + (-1.33895614e-7) * ta * va**2 * d**2
        + (2.49709824e-9) * ta**2 * va**2 * d**2
        + (6.51711721e-7) * va**3 * d**2
        + (1.94960053e-9) * ta * va**3 * d**2
        + (-1.00361113e-8) * va**4 * d**2
        + (-1.21206673e-5) * d**3
        + (-2.18203660e-7) * ta * d**3
        + (7.51269482e-9) * ta**2 * d**3
        + (9.79063848e-11) * ta**3 * d**3
        + (1.25006734e-6) * va * d**3
        + (-1.81584736e-9) * ta * va * d**3
        + (-3.52197671e-10) * ta**2 * va * d**3
        + (-3.36514630e-8) * va**2 * d**3
        + (1.35908359e-10) * ta * va**2 * d**3
        + (4.17032620e-10) * va**3 * d**3
        + (-1.30369025e-9) * d**4
        + (4.13908461e-10) * ta * d**4
        + (9.22652254e-12) * ta**2 * d**4
        + (-5.08220384e-9) * va * d**4
        + (-2.24730961e-11) * ta * va * d**4
        + (1.17139133e-10) * va**2 * d**4
        + (6.62154879e-10) * d**5
        + (4.03863260e-13) * ta * d**5
        + (1.95087203e-12) * va * d**5
        + (-4.73602469e-12) * d**6
        + (5.12733497) * pa
        + (-0.312788561) * ta * pa
        + (-0.0196701861) * ta**2 * pa
        + (9.99690870e-4) * ta**3 * pa
        + (9.51738512e-6) * ta**4 * pa
        + (-4.66426341e-7) * ta**5 * pa
        + (0.548050612) * va * pa
        + (-0.00330552823) * ta * va * pa
        + (-0.00164119440) * ta**2 * va * pa
        + (-5.16670694e-6) * ta**3 * va * pa
        + (9.52692432e-7) * ta**4 * va * pa
        + (-0.0429223622) * va**2 * pa
        + (0.00500845667) * ta * va**2 * pa
        + (1.00601257e-6) * ta**2 * va**2 * pa
        + (-1.81748644e-6) * ta**3 * va**2 * pa
        + (-1.25813502e-3) * va**3 * pa
        + (-1.79330391e-4) * ta * va**3 * pa
        + (2.34994441e-6) * ta**2 * va**3 * pa
        + (1.29735808e-4) * va**4 * pa
        + (1.29064870e-6) * ta * va**4 * pa
        + (-2.28558686e-6) * va**5 * pa
        + (-0.0369476348) * d * pa
        + (0.00162325322) * ta * d * pa
        + (-3.14279680e-5) * ta**2 * d * pa
        + (2.59835559e-6) * ta**3 * d * pa
        + (-4.77136523e-8) * ta**4 * d * pa
        + (8.64203390e-3) * va * d * pa
        + (-6.87405181e-4) * ta * va * d * pa
        + (-9.13863872e-6) * ta**2 * va * d * pa
        + (5.15916806e-7) * ta**3 * va * d * pa
        + (-3.59217476e-5) * va**2 * d * pa
        + (3.28696511e-5) * ta * va**2 * d * pa
        + (-7.10542454e-7) * ta**2 * va**2 * d * pa
        + (-1.24382300e-5) * va**3 * d * pa
        + (-7.38584400e-9) * ta * va**3 * d * pa
        + (2.20609296e-7) * va**4 * d * pa
        + (-7.32469180e-4) * d**2 * pa
        + (-1.87381964e-5) * ta * d**2 * pa
        + (4.80925239e-6) * ta**2 * d**2 * pa
        + (-8.75492040e-8) * ta**3 * d**2 * pa
        + (2.77862930e-5) * va * d**2 * pa
        + (-5.06004592e-6) * ta * va * d**2 * pa
        + (1.14325367e-7) * ta**2 * va * d**2 * pa
        + (2.53016723e-6) * va**2 * d**2 * pa
        + (-1.72857035e-8) * ta * va**2 * d**2 * pa
        + (-3.95079398e-8) * va**3 * d**2 * pa
        + (-3.59413173e-7) * d**3 * pa
        + (7.04388046e-7) * ta * d**3 * pa
        + (-1.89309167e-8) * ta**2 * d**3 * pa
        + (-4.79768731e-7) * va * d**3 * pa
        + (7.96079978e-9) * ta * va * d**3 * pa
        + (1.62897058e-9) * va**2 * d**3 * pa
        + (3.94367674e-8) * d**4 * pa
        + (-1.18566247e-9) * ta * d**4 * pa
        + (3.34678041e-10) * va * d**4 * pa
        + (-1.15606447e-10) * d**5 * pa
        + (-2.80626406) * pa**2
        + (0.548712484) * ta * pa**2
        + (-0.00399428410) * ta**2 * pa**2
        + (-9.54009191e-4) * ta**3 * pa**2
        + (1.93090978e-5) * ta**4 * pa**2
        + (-0.308806365) * va * pa**2
        + (0.0116952364) * ta * va * pa**2
        + (4.95271903e-4) * ta**2 * va * pa**2
        + (-1.90710882e-5) * ta**3 * va * pa**2
        + (0.00210787756) * va**2 * pa**2
        + (-6.98445738e-4) * ta * va**2 * pa**2
        + (2.30109073e-5) * ta**2 * va**2 * pa**2
        + (4.17856590e-4) * va**3 * pa**2
        + (-1.27043871e-5) * ta * va**3 * pa**2
        + (-3.04620472e-6) * va**4 * pa**2
        + (0.0514507424) * d * pa**2
        + (-0.00432510997) * ta * d * pa**2
        + (8.99281156e-5) * ta**2 * d * pa**2
        + (-7.14663943e-7) * ta**3 * d * pa**2
        + (-2.66016305e-4) * va * d * pa**2
        + (2.63789586e-4) * ta * va * d * pa**2
        + (-7.01199003e-6) * ta**2 * va * d * pa**2
        + (-1.06823306e-4) * va**2 * d * pa**2
        + (3.61341136e-6) * ta * va**2 * d * pa**2
        + (2.29748967e-7) * va**3 * d * pa**2
        + (3.04788893e-4) * d**2 * pa**2
        + (-6.42070836e-5) * ta * d**2 * pa**2
        + (1.16257971e-6) * ta**2 * d**2 * pa**2
        + (7.68023384e-6) * va * d**2 * pa**2
        + (-5.47446896e-7) * ta * va * d**2 * pa**2
        + (-3.59937910e-8) * va**2 * d**2 * pa**2
        + (-4.36497725e-6) * d**3 * pa**2
        + (1.68737969e-7) * ta * d**3 * pa**2
        + (2.67489271e-8) * va * d**3 * pa**2
        + (3.23926897e-9) * d**4 * pa**2
        + (-0.0353874123) * pa**3
        + (-0.221201190) * ta * pa**3
        + (0.0155126038) * ta**2 * pa**3
        + (-2.63917279e-4) * ta**3 * pa**3
        + (0.0453433455) * va * pa**3
        + (-0.00432943862) * ta * va * pa**3
        + (1.45389826e-4) * ta**2 * va * pa**3
        + (2.17508610e-4) * va**2 * pa**3
        + (-6.66724702e-5) * ta * va**2 * pa**3
        + (3.33217140e-5) * va**3 * pa**3
        + (-0.00226921615) * d * pa**3
        + (3.80261982e-4) * ta * d * pa**3
        + (-5.45314314e-9) * ta**2 * d * pa**3
        + (-7.96355448e-4) * va * d * pa**3
        + (2.53458034e-5) * ta * va * d * pa**3
        + (-6.31223658e-6) * va**2 * d * pa**3
        + (3.02122035e-4) * d**2 * pa**3
        + (-4.77403547e-6) * ta * d**2 * pa**3
        + (1.73825715e-6) * va * d**2 * pa**3
        + (-4.09087898e-7) * d**3 * pa**3
        + (0.614155345) * pa**4
        + (-0.0616755931) * ta * pa**4
        + (0.00133374846) * ta**2 * pa**4
        + (0.00355375387) * va * pa**4
        + (-5.13027851e-4) * ta * va * pa**4
        + (1.02449757e-4) * va**2 * pa**4
        + (-0.00148526421) * d * pa**4
        + (-4.11469183e-5) * ta * d * pa**4
        + (-6.80434415e-6) * va * d * pa**4
        + (-9.77675906e-6) * d**2 * pa**4
        + (0.0882773108) * pa**5
        + (-0.00301859306) * ta * pa**5
        + (0.00104452989) * va * pa**5
        + (2.47090539e-4) * d * pa**5
        + (0.00148348065) * pa**6
    )
    return ta + offset


def _decode_blosc(
    raw: bytes,
    dtype: np.dtype,
    shape: tuple[int, ...],
    *,
    fixed_scale_offset: bool = False,
) -> np.ndarray:
    import numcodecs

    buf = numcodecs.Blosc().decode(raw)
    arr = np.frombuffer(buf, dtype=dtype).reshape(shape)
    if fixed_scale_offset:
        arr = (arr.astype(np.float64) / 100.0) + 1000.0
    return arr


def _fetch_array(
    session: requests.Session,
    var: str,
    chunk_key: str,
    dtype: np.dtype,
    shape: tuple[int, ...],
    *,
    fixed_scale_offset: bool = False,
) -> np.ndarray:
    url = f"{ZARR_BASE}/{var}/{chunk_key}"
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return _decode_blosc(
        resp.content, dtype, shape, fixed_scale_offset=fixed_scale_offset
    )


def load(
    database_url: str,
    *,
    start_year: int = 2020,
    end_year: int = 2024,
    workers: int = 12,
) -> dict[str, Any]:
    session = requests.Session()
    lats = _fetch_array(session, "lat", "0", np.dtype("<f8"), (361,))
    lons = _fetch_array(session, "lon", "0", np.dtype("<f8"), (576,))
    times = _fetch_array(session, "time", "0", np.dtype("<i8"), (17898,))

    start_day = (dt.date(start_year, 1, 1) - EPOCH).days
    end_day = (dt.date(end_year, 12, 31) - EPOCH).days
    day_indices = [
        int(i) for i, t in enumerate(times) if start_day <= int(t) <= end_day
    ]
    n_days = len(day_indices)
    n_years = n_days / 365.25
    print(
        f"streaming {n_days} days ({start_year}-{end_year}) from NASA POWER S3 Zarr "
        f"with {workers} workers",
        flush=True,
    )

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT destination_id, lat, lon FROM destinations")
            cities = cur.fetchall()

    city_ids = [c["destination_id"] for c in cities]
    city_lats = np.array([float(c["lat"]) for c in cities])
    city_lons = np.array([float(c["lon"]) for c in cities])
    lat_idx = np.clip(np.abs(lats[:, None] - city_lats[None, :]).argmin(axis=0), 0, 360)
    lon_idx = np.clip(np.abs(lons[:, None] - city_lons[None, :]).argmin(axis=0), 0, 575)

    n_cities = len(cities)
    gt32 = np.zeros(n_cities, dtype=np.int32)
    lt0 = np.zeros(n_cities, dtype=np.int32)
    gt38 = np.zeros(n_cities, dtype=np.int32)
    ltm13 = np.zeros(n_cities, dtype=np.int32)
    valid_days = np.zeros(n_cities, dtype=np.int32)

    shape = (361, 576)
    f4 = np.dtype("<f4")

    def process_day(idx: int):
        s = requests.Session()
        ck = f"{idx}.0.0"
        tmax = _fetch_array(s, "T2M_MAX", ck, f4, shape, fixed_scale_offset=True)[lat_idx, lon_idx]
        tmin = _fetch_array(s, "T2M_MIN", ck, f4, shape, fixed_scale_offset=True)[lat_idx, lon_idx]
        rh = _fetch_array(s, "RH2M", ck, f4, shape, fixed_scale_offset=True)[lat_idx, lon_idx]
        ws = _fetch_array(s, "WS10M", ck, f4, shape, fixed_scale_offset=True)[lat_idx, lon_idx]
        ok = (
            np.isfinite(tmax)
            & np.isfinite(tmin)
            & np.isfinite(rh)
            & np.isfinite(ws)
            & (tmax > -100)
            & (tmin > -100)
            & (rh >= 0)
            & (ws >= 0)
        )
        # Daytime peak UTCI (clear-sky solar load dTmrt ~ +10C approximation at daily max)
        u_max = utci_approx_vec(np.where(ok, tmax, 20.0), np.where(ok, rh, 50.0), np.where(ok, ws, 2.0), d_tmrt=10.0)
        # Nighttime/morning cold UTCI (no solar load dTmrt = 0C at daily min)
        u_min = utci_approx_vec(np.where(ok, tmin, 20.0), np.where(ok, rh, 50.0), np.where(ok, ws, 2.0), d_tmrt=0.0)
        return (
            ok,
            ok & (u_max > 32.0),
            ok & (u_min < 0.0),
            ok & (u_max > 38.0),
            ok & (u_min < -13.0),
        )

    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(process_day, idx) for idx in day_indices]
        for done, fut in enumerate(as_completed(futures), 1):
            ok, h32, c0, h38, cm13 = fut.result()
            valid_days += ok.astype(np.int32)
            gt32 += h32.astype(np.int32)
            lt0 += c0.astype(np.int32)
            gt38 += h38.astype(np.int32)
            ltm13 += cm13.astype(np.int32)
            if done % 250 == 0 or done == n_days:
                elapsed = time.time() - started
                print(
                    f"  {done}/{n_days} days ({done / max(0.1, elapsed):.1f} d/s), {elapsed:.0f}s",
                    flush=True,
                )

    period = f"{start_year}-{end_year}"
    records = []
    for i, dest_id in enumerate(city_ids):
        vd = int(valid_days[i])
        if vd == 0:
            continue
        yrs = vd / 365.25
        d32 = float(gt32[i]) / yrs
        d0 = float(lt0[i]) / yrs
        d38 = float(gt38[i]) / yrs
        dm13 = float(ltm13[i]) / yrs
        hs, cs, ss, ts = compute_thermal_safety_components(d32, d0, d38, dm13)
        records.append(
            (
                dest_id,
                round(d32, 2),
                round(d0, 2),
                round(d38, 2),
                round(dm13, 2),
                round(hs, 2),
                round(cs, 2),
                round(ss, 2),
                round(ts, 2),
                vd,
                SOURCE,
                SOURCE_URL,
                period,
            )
        )

    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute("TRUNCATE TABLE destination_thermal_utci")
            cur.executemany(
                """
                INSERT INTO destination_thermal_utci (
                    destination_id, utci_days_gt_32, utci_days_lt_0,
                    utci_days_gt_38, utci_days_lt_m13,
                    heat_score, cold_score, severe_score, thermal_safety_score,
                    days_sampled, source, source_url, observed_period
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                records,
            )
            # Mandatory overlap validation vs GHSL UCDB CL_UTC_T32_2020
            cur.execute(
                """
                SELECT count(*) AS overlap_n,
                       round(corr(t.utci_days_gt_32, c.utci_over_32_days)::numeric, 3) AS pearson_r,
                       round(avg(abs(t.utci_days_gt_32 - c.utci_over_32_days))::numeric, 1) AS mae_days
                FROM destination_thermal_utci t
                JOIN destination_metro m USING (destination_id)
                JOIN urban_centre_climate c USING (urban_centre_id)
                WHERE c.utci_over_32_days IS NOT NULL
                """
            )
            val = dict(cur.fetchone())
        conn.commit()
    return {
        "written": len(records),
        "days": n_days,
        "years": round(n_years, 2),
        "overlap_vs_ghsl_utci32": val,
    }


def report(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.name, d.country_iso2,
                       t.utci_days_gt_32 AS gt32, t.utci_days_lt_0 AS lt0,
                       t.utci_days_gt_38 AS gt38, t.utci_days_lt_m13 AS ltm13,
                       t.thermal_safety_score AS score
                FROM destination_thermal_utci t
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Stockholm', 'Tromsø', 'Umeå', 'Barcelona', 'Brisbane', 'Cienfuegos', 'Tokyo', 'London', 'Singapore', 'Lima')
                ORDER BY t.thermal_safety_score DESC
                """
            )
            for row in cur.fetchall():
                print(dict(row))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=os.environ.get("SUPABASE_DB_URL"))
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if not args.db_url:
        parser.error("set --db-url or SUPABASE_DB_URL")
    if args.report:
        report(args.db_url)
        return 0
    print(
        load(
            args.db_url,
            start_year=args.start_year,
            end_year=args.end_year,
            workers=args.workers,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
