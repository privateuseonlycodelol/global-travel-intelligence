"""Offline unit tests for Phase 3: no database, no network.

Run with: python -m unittest tests.test_phase3 -v
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_phase3 as b  # noqa: E402

try:
    import duckdb
except ImportError:  # pragma: no cover - duckdb is a hard dep of the script
    duckdb = None


def cities(rows):
    return pd.DataFrame(rows, columns=["country_iso2", "lat", "lon"])


class SchengenTests(unittest.TestCase):
    def test_exactly_29_unique_iso2_codes(self):
        self.assertEqual(len(b.SCHENGEN_ISO2), 29)
        self.assertEqual(len(set(b.SCHENGEN_ISO2)), 29)

    def test_codes_are_two_uppercase_letters(self):
        for code in b.SCHENGEN_ISO2:
            self.assertIsInstance(code, str)
            self.assertEqual(len(code), 2, code)
            self.assertEqual(code, code.upper(), code)
            self.assertTrue(code.isalpha(), code)

    def test_expected_members_and_non_members(self):
        members = set(b.SCHENGEN_ISO2)
        for code in ("AT", "BE", "CH", "ES", "SE"):
            self.assertIn(code, members)
        for code in ("GB", "IE", "US"):
            self.assertNotIn(code, members)


class OoklaUrlTests(unittest.TestCase):
    def test_2026_q2_url(self):
        url = b.OOKLA_URL_TEMPLATE.format(year=2026, quarter=2, month="04")
        self.assertEqual(
            url,
            "https://ookla-open-data.s3.amazonaws.com/parquet/performance/"
            "type=fixed/year=2026/quarter=2/"
            "2026-04-01_performance_fixed_tiles.parquet",
        )

    def test_url_has_no_unfilled_placeholders(self):
        url = b.OOKLA_URL_TEMPLATE.format(year=2026, quarter=2, month="04")
        self.assertNotIn("{", url)
        self.assertNotIn("}", url)


class HaversineTests(unittest.TestCase):
    LUX = (49.6117, 6.1300)
    BRU = (50.8503, 4.3517)

    def test_luxembourg_to_brussels_is_roughly_190_km(self):
        d = b.haversine_km(*self.LUX, *self.BRU)
        self.assertGreaterEqual(d, 180.0)
        self.assertLessEqual(d, 200.0)

    def test_same_point_is_zero(self):
        self.assertAlmostEqual(b.haversine_km(49.6117, 6.13, 49.6117, 6.13), 0.0)

    def test_symmetric(self):
        forward = b.haversine_km(*self.LUX, *self.BRU)
        backward = b.haversine_km(*self.BRU, *self.LUX)
        self.assertAlmostEqual(forward, backward, places=9)

    def test_zero_on_equator_one_degree(self):
        self.assertAlmostEqual(b.haversine_km(0.0, 0.0, 0.0, 1.0), 111.19, delta=0.2)


class ScanEnvelopeTests(unittest.TestCase):
    def test_two_countries_return_two_envelopes(self):
        df = cities([
            ("SE", 59.3, 18.1),
            ("SE", 57.7, 12.0),
            ("ES", 40.4, -3.7),
            ("ES", 41.4, 2.2),
        ])
        envs = b._scan_envelopes(df)
        self.assertEqual(len(envs), 2)
        for min_lon, max_lon, min_lat, max_lat in envs:
            self.assertIsInstance(min_lon, float)
            self.assertIsInstance(max_lon, float)
            self.assertIsInstance(min_lat, float)
            self.assertIsInstance(max_lat, float)
            self.assertLess(min_lon, max_lon)
            self.assertLess(min_lat, max_lat)
            self.assertGreaterEqual(min_lon, -180.0)
            self.assertLessEqual(max_lon, 180.0)
            self.assertGreaterEqual(min_lat, -90.0)
            self.assertLessEqual(max_lat, 90.0)

    def test_envelope_covers_points_plus_pad(self):
        df = cities([("LU", 49.6, 6.1), ("LU", 49.9, 6.4)])
        (min_lon, max_lon, min_lat, max_lat), = b._scan_envelopes(df, pad=0.25)
        self.assertAlmostEqual(min_lon, 5.85, places=6)
        self.assertAlmostEqual(max_lon, 6.65, places=6)
        self.assertAlmostEqual(min_lat, 49.35, places=6)
        self.assertAlmostEqual(max_lat, 50.15, places=6)

    def test_antimeridian_country_uses_world_longitude(self):
        df = cities([("FJ", -17.0, -179.9), ("FJ", -17.1, 179.9)])
        envelopes = b._scan_envelopes(df)
        self.assertEqual(len(envelopes), 1)
        min_lon, max_lon, min_lat, max_lat = envelopes[0]
        self.assertEqual((min_lon, max_lon), (-180.0, 180.0))
        self.assertLess(min_lat, -17.0)
        self.assertGreater(max_lat, -17.0)

    def test_more_than_40_countries_are_grouped_not_world(self):
        df = cities([(f"C{i:02d}", 0.0, float(i)) for i in range(41)])
        envelopes = b._scan_envelopes(df)
        self.assertGreaterEqual(len(envelopes), 1)
        self.assertLessEqual(len(envelopes), 8)
        for min_lon, max_lon, min_lat, max_lat in envelopes:
            self.assertGreaterEqual(min_lon, -180.0)
            self.assertLessEqual(max_lon, 180.0)
        self.assertTrue(
            any(
                min_lon <= 0.0 <= max_lon
                for min_lon, max_lon, _, _ in envelopes
            )
        )

    def test_true_global_uses_one_world_scan(self):
        df = cities([(f"C{i:03d}", 0.0, float(i % 180)) for i in range(101)])
        self.assertEqual(
            b._scan_envelopes(df),
            [(-180.0, 180.0, -90.0, 90.0)],
        )

    def test_near_dateline_city_gets_opposite_edge_band(self):
        df = cities([("FJ", -17.0, 179.99)])
        envelopes = b._scan_envelopes(df)
        self.assertTrue(any(min_lon == -180.0 for min_lon, _, _, _ in envelopes))
        self.assertTrue(any(max_lon == 180.0 for _, max_lon, _, _ in envelopes))

    def test_radius_pad_widens_longitude_at_high_latitude(self):
        df = cities([("NO", 78.0, 15.0)])
        envelopes = b._scan_envelopes(df, pad=0.15, radius_km=10.0)
        min_lon, max_lon, _, _ = envelopes[0]
        self.assertLess(min_lon, 14.6)
        self.assertGreater(max_lon, 15.4)

    def test_exactly_40_countries_stays_per_country(self):
        df = cities([(f"C{i:02d}", 0.0, float(i)) for i in range(40)])
        self.assertEqual(len(b._scan_envelopes(df)), 40)

    def test_empty_returns_empty_list(self):
        self.assertEqual(b._scan_envelopes(cities([])), [])


class PgTypeTests(unittest.TestCase):
    """Every column the Phase 3 upserts bind must have a Postgres type."""

    EXPECTED = {
        # broadband_metrics
        "destination_id": "TEXT",
        "radius_km": "DOUBLE PRECISION",
        "median_down_mbps": "DOUBLE PRECISION",
        "median_up_mbps": "DOUBLE PRECISION",
        "avg_latency_ms": "DOUBLE PRECISION",
        "total_tests": "BIGINT",
        "tile_count": "INT",
        "source": "TEXT",
        "observed_period": "TEXT",
        "updated_at": "TEXT",
        # mobility_metrics
        "pedestrian_segments_2km": "INT",
        "total_road_segments_2km": "INT",
        "pedestrian_ratio_2km": "DOUBLE PRECISION",
        "rail_segments_5km": "INT",
        "gtfs_active_feeds": "INT",
        "transit_stops_5km": "INT",
        "transit_stops_source": "TEXT",
        "transit_stops_period": "TEXT",
        # housing_monthly
        "median_monthly_local": "DOUBLE PRECISION",
        "sample_listings": "INT",
    }

    def test_mapping_for_each_phase3_column(self):
        for column, expected in self.EXPECTED.items():
            self.assertEqual(b._pg_type(column), expected, column)

    def test_covers_every_declared_phase3_column(self):
        for table in ("broadband_metrics", "mobility_metrics", "housing_monthly"):
            for column in b.REQUIRED_COLUMNS[table]:
                self.assertIn(column, self.EXPECTED, f"{table}.{column}")

    def test_only_known_types(self):
        allowed = {"BIGINT", "DOUBLE PRECISION", "INT", "TEXT"}
        for column in self.EXPECTED:
            self.assertIn(b._pg_type(column), allowed, column)


class RetryClassificationTests(unittest.TestCase):
    def test_network_errors_are_retried(self):
        for message in (
            "Could not resolve hostname",
            "HTTP 503",
            "connection reset by peer",
        ):
            self.assertTrue(b._is_transient_duckdb_error(RuntimeError(message)), message)

    def test_deterministic_errors_are_not_retried(self):
        for message in (
            "Out of Memory Error: could not allocate block; see https://duckdb.org",
            "Permission denied",
            "Failed to create directory .tmp",
            "Binder Error: referenced column not found",
        ):
            self.assertFalse(b._is_transient_duckdb_error(RuntimeError(message)), message)


class PedestrianClassTests(unittest.TestCase):
    def test_expected_members(self):
        classes = set(b.PEDESTRIAN_CLASSES)
        for name in ("footway", "pedestrian", "living_street", "steps", "cycleway"):
            self.assertIn(name, classes)
        for name in ("motorway", "residential"):
            self.assertNotIn(name, classes)

    def test_no_duplicates(self):
        self.assertEqual(len(b.PEDESTRIAN_CLASSES), len(set(b.PEDESTRIAN_CLASSES)))


@unittest.skipIf(duckdb is None, "duckdb is not installed")
class WeightedMedianTests(unittest.TestCase):
    """The Ookla median is a test-weighted median, not a plain median."""

    def setUp(self):
        self.duck = duckdb.connect()
        self.addCleanup(self.duck.close)
        self.duck.execute(
            """
            CREATE TABLE tiles (
                destination_id TEXT,
                d_mbps         DOUBLE,
                tests          BIGINT
            )
            """
        )

    def test_weighted_median_picks_crossing_tile(self):
        self.duck.executemany(
            "INSERT INTO tiles VALUES (?, ?, ?)",
            [("a", 10.0, 1), ("a", 50.0, 3), ("a", 100.0, 1)],
        )
        median = self.duck.execute(
            """
            WITH ordered AS (
                SELECT *,
                       SUM(tests) OVER (
                           PARTITION BY destination_id ORDER BY d_mbps
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                       ) AS cum_tests,
                       SUM(tests) OVER (PARTITION BY destination_id) AS total_tests
                FROM tiles
            )
            SELECT MIN(d_mbps)
            FROM ordered
            WHERE cum_tests >= total_tests / 2.0
            GROUP BY destination_id
            """
        ).fetchone()[0]
        self.assertEqual(median, 50.0)

    def test_unweighted_median_would_differ(self):
        # Plain median of (10, 50, 100) is 50 here, so use a skewed case:
        # tile 20 Mbps carries 9 of 10 tests; the weighted median is 20.
        self.duck.executemany(
            "INSERT INTO tiles VALUES (?, ?, ?)",
            [("b", 20.0, 9), ("b", 1000.0, 1)],
        )
        median = self.duck.execute(
            """
            WITH ordered AS (
                SELECT *,
                       SUM(tests) OVER (
                           PARTITION BY destination_id ORDER BY d_mbps
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                       ) AS cum_tests,
                       SUM(tests) OVER (PARTITION BY destination_id) AS total_tests
                FROM tiles
            )
            SELECT MIN(d_mbps)
            FROM ordered
            WHERE cum_tests >= total_tests / 2.0
            GROUP BY destination_id
            """
        ).fetchone()[0]
        self.assertEqual(median, 20.0)


class CityCellTests(unittest.TestCase):
    @unittest.skipIf(duckdb is None, "duckdb is not installed")
    def test_default_halo_is_seven_by_seven(self):
        duck = duckdb.connect()
        self.addCleanup(duck.close)
        df = pd.DataFrame(
            [("gn-1", 49.6, 6.1)], columns=["destination_id", "lat", "lon"]
        )
        b._register_city_tables(duck, df, halo=3)
        count = duck.execute("SELECT COUNT(*) FROM city_cells").fetchone()[0]
        self.assertEqual(count, 49)

    @unittest.skipIf(duckdb is None, "duckdb is not installed")
    def test_airbnb_halo_is_wide_enough_for_ten_km(self):
        duck = duckdb.connect()
        self.addCleanup(duck.close)
        df = pd.DataFrame(
            [("gn-1", 78.0, 15.0)], columns=["destination_id", "lat", "lon"]
        )
        b._register_city_tables(duck, df, halo=5)
        count = duck.execute("SELECT COUNT(*) FROM city_cells").fetchone()[0]
        self.assertEqual(count, 121)


class ScorerContractTests(unittest.TestCase):
    def test_phase3_scorer_declares_all_parameters(self):
        for param in (
            "p_exclude_schengen",
            "p_min_broadband_mbps",
            "p_require_gtfs",
            "p_w_broadband",
            "p_w_housing",
        ):
            self.assertIn(param, b.PHASE3_SCORER_SQL)

    def test_scorer_reads_phase3_tables(self):
        for table in ("broadband_metrics", "mobility_metrics", "housing_monthly"):
            self.assertIn(table, b.PHASE3_SCORER_SQL)
        self.assertIn("transit_stops_5km", b.PHASE3_SCORER_SQL)

    def test_scorer_uses_non_degenerate_rank_scale(self):
        self.assertIn("0.05 + 0.90 * PERCENT_RANK()", b.PHASE3_SCORER_SQL)
        self.assertIn("THEN 0.5", b.PHASE3_SCORER_SQL)

    def test_walk_rank_uses_published_score(self):
        self.assertIn("walk_values", b.PHASE3_SCORER_SQL)
        self.assertNotIn("0.60 * CASE", b.PHASE3_SCORER_SQL)
        self.assertIn("pedestrian_segments_2km", b.PHASE3_SCORER_SQL)
        self.assertIn("'walkability_score', w.walkability_score_100", b.PHASE3_SCORER_SQL)
        self.assertIn("walkability_score_100 AS score_value", b.PHASE3_SCORER_SQL)
        self.assertIn("CASE WHEN p.restaurant_count > 0", b.PHASE3_SCORER_SQL)
        self.assertIn("CASE WHEN b.total_tests >= 10", b.PHASE3_SCORER_SQL)
        self.assertIn("excluded_for_no_evidence", b.PHASE3_SCORER_SQL)


if __name__ == "__main__":
    unittest.main()
