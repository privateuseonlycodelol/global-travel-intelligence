"""Tests for geography, facilities and airport context layers."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import place_profiles as pp  # noqa: E402


class OfflineTests(unittest.TestCase):
    def test_daylight_at_equator_is_twelve_hours(self):
        for month in (1, 6, 12):
            self.assertAlmostEqual(pp.daylight_hours(0.0, month), 12.0, delta=0.1)

    def test_daylight_at_high_latitude_has_a_large_seasonal_swing(self):
        june = pp.daylight_hours(60.0, 6)
        december = pp.daylight_hours(60.0, 12)
        self.assertGreater(june, 18.0)
        self.assertLess(december, 6.0)

    def test_zero_facility_count_is_none_listed(self):
        summary = pp.facility_summary({
            "hospital_count_5km": 0,
            "pharmacy_count_5km": 3,
            "school_count_5km": 0,
            "grocery_count_5km": 0,
            "park_count_5km": 0,
            "beach_count_5km": 0,
            "source": "test",
            "observed_period": "test",
        })
        self.assertEqual(summary["counts"]["hospital"]["status"], "none_listed")
        self.assertEqual(summary["counts"]["pharmacy"]["status"], "measured")
        self.assertEqual(summary["counts"]["pharmacy"]["count"], 3)

    def test_hazard_summary_counts(self):
        summary = pp.hazard_summary({
            "events_2015": 10, "hazards_2015": 3,
            "max_hazards_per_event_2015": 2, "flood_2015": 4,
            "heatwave_2015": 2,
        })
        self.assertEqual(summary["events"], 10)
        self.assertEqual(summary["by_type"]["flood"]["events"], 4)
        self.assertEqual(summary["by_type"]["earthquake"]["events"], 0)

    def test_health_access_summary(self):
        summary = pp.health_access_summary({
            "hospitals_count": 12, "pharmacies_count": 6,
            "hospital_access_share_pct": 41.4, "pharmacy_access_share_pct": 20.0,
        })
        self.assertEqual(summary["hospitals_count"], 12)
        self.assertEqual(summary["hospital_access_share_pct"], 41.4)

    def test_tourism_seasonality_peak_and_ratio(self):
        rows = [
            {"month": 1, "tourist_nights": 100, "nights_per_resident": 1.0},
            {"month": 8, "tourist_nights": 400, "nights_per_resident": 4.0},
        ]
        summary = pp.tourism_seasonality(rows)
        self.assertEqual(summary["peak_month"], 8)
        self.assertEqual(summary["quietest_month"], 1)
        self.assertEqual(summary["peak_to_quiet_ratio"], 4.0)

    def test_missing_layers_report_not_loaded(self):
        self.assertEqual(pp.facility_summary(None)["status"], "not_loaded")
        self.assertEqual(pp.airport_summary(None)["status"], "not_loaded")
        self.assertEqual(
            pp.geography_summary(None, 0.0)["status"], "not_loaded"
        )


@unittest.skipUnless(os.environ.get("TRAVEL_TEST_DB_URL"), "TRAVEL_TEST_DB_URL not set")
class LiveContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.conn = psycopg.connect(os.environ["TRAVEL_TEST_DB_URL"], row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_facility_coverage(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM facility_metrics")
            self.assertEqual(cur.fetchone()["n"], 13_101)

    def test_airport_coverage(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n, count(nearest_iata) iata FROM airport_access"
            )
            row = cur.fetchone()
        if row["n"] == 0:
            self.skipTest("destination_geography is still loading")
        self.assertEqual(row["n"], 13_101)
        self.assertGreater(row["iata"], 12_000)

    def test_lisbon_profile_has_facilities_and_airport(self):
        with self.conn.cursor() as cur:
            profile = pp.build_profile(cur, "gn-2267057")
        self.assertGreater(
            profile["facilities"]["counts"]["hospital"]["count"], 0
        )
        self.assertEqual(profile["airport_access"]["nearest"]["iata"], "LIS")
        self.assertEqual(profile["airport_access"]["nearest"]["type"], "large_airport")

    def test_ucdb_hazard_and_health_coverage(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM urban_centre_hazard")
            self.assertEqual(cur.fetchone()["n"], 11_422)
            cur.execute("SELECT count(*) n FROM urban_centre_health")
            self.assertEqual(cur.fetchone()["n"], 11_422)
            profile = pp.build_profile(cur, "gn-1701668")  # Manila
        self.assertEqual(profile["hazard_exposure"]["status"], "loaded")
        self.assertGreater(profile["hazard_exposure"]["events"], 0)
        self.assertEqual(profile["health_access"]["status"], "loaded")

    def test_tourism_monthly_coverage(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) rows, count(DISTINCT destination_id) cities "
                "FROM tourism_monthly"
            )
            row = cur.fetchone()
        self.assertEqual(row["rows"], 16_860)
        self.assertEqual(row["cities"], 1_405)

    def test_geography_when_loaded(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT to_regclass('destination_geography') AS present")
            if not cur.fetchone()["present"]:
                self.skipTest("destination_geography not loaded")
            cur.execute(
                """
                SELECT count(*) n, count(elevation_m) elevation,
                       count(coast_distance_km) coast
                FROM destination_geography
                """
            )
            row = cur.fetchone()
        if row["n"] == 0:
            self.skipTest("destination_geography is still loading")
        self.assertEqual(row["n"], 13_101)
        self.assertGreater(row["elevation"], 13_000)
        self.assertEqual(row["coast"], 13_101)


if __name__ == "__main__":
    unittest.main()
