"""Tests for the global GTFS scheduled-service loader."""

import os
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_gtfs as bg  # noqa: E402
import place_profiles as pp  # noqa: E402


class TimeTests(unittest.TestCase):
    def test_gtfs_time(self):
        self.assertEqual(bg.parse_gtfs_time("07:05:00"), 425)
        self.assertEqual(bg.parse_gtfs_time("25:10:00"), 1510)
        self.assertIsNone(bg.parse_gtfs_time(""))
        self.assertIsNone(bg.parse_gtfs_time("bad"))

    def test_month_day(self):
        self.assertEqual(bg.month_day("20261125"), date(2026, 11, 25))
        self.assertIsNone(bg.month_day("2026-11-25"))
        self.assertIsNone(bg.month_day(None))

    def test_next_weekday(self):
        # 2026-11-25 is a Wednesday.
        self.assertEqual(bg.next_weekday(date(2026, 11, 25), 2), date(2026, 11, 25))
        self.assertEqual(bg.next_weekday(date(2026, 11, 25), 6), date(2026, 11, 29))


class ServiceTests(unittest.TestCase):
    def test_calendar_and_exception(self):
        calendar = pd.DataFrame([{
            "service_id": "A", "monday": "1", "tuesday": "1", "wednesday": "1",
            "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0",
            "start_date": "20260101", "end_date": "20261231",
        }])
        exceptions = pd.DataFrame([
            {"service_id": "A", "date": "20261125", "exception_type": "2"},
            {"service_id": "B", "date": "20261125", "exception_type": "1"},
        ])
        self.assertEqual(
            bg.active_service_ids(calendar, exceptions, date(2026, 11, 25)),
            {"B"},
        )
        self.assertEqual(
            bg.active_service_ids(calendar, exceptions, date(2026, 11, 26)),
            {"A"},
        )

    def test_no_calendar_uses_exceptions(self):
        exceptions = pd.DataFrame([
            {"service_id": "X", "date": "20261129", "exception_type": "1"},
        ])
        self.assertEqual(
            bg.active_service_ids(None, exceptions, date(2026, 11, 29)), {"X"}
        )


class SummaryTests(unittest.TestCase):
    ROW = {
        "day_type": "weekday", "time_band": "peak", "service_date": "2026-11-25",
        "radius_rule": "urban_centre", "stops": 100,
        "median_headway_min": 12.0,
        "pct_stops_headway_le_10": 0.4, "pct_stops_headway_le_15": 0.7,
        "pct_stops_headway_le_30": 0.95, "first_departure_min": 300,
        "last_departure_min": 1465, "departures_at_busiest_stop": 800,
        "route_type_counts": {"0": 2, "3": 40},
        "wheelchair_coded_pct": 0.8, "wheelchair_boardable_pct": 0.6,
        "feed_id": "test", "provider": "Test", "license_url": None,
    }

    def test_transit_summary_percentages_and_times(self):
        summary = pp.transit_summary([self.ROW])
        peak = summary["weekday_peak"]
        self.assertEqual(peak["median_headway_min"], 12.0)
        self.assertEqual(peak["pct_stops_within_15_min"], 70.0)
        self.assertEqual(peak["first_departure"], "05:00")
        self.assertEqual(peak["last_departure"], "24:25")
        self.assertEqual(peak["route_types"], {"Tram": 2, "Bus": 40})

    def test_missing_rows(self):
        self.assertEqual(pp.transit_summary([])["status"], "not_loaded")

    def test_thin_feed_warns(self):
        row = dict(self.ROW)
        row["stops"] = 4
        summary = pp.transit_summary([row])
        self.assertIsNotNone(summary["coverage_warning"])
        self.assertEqual(summary["weekday_peak"]["coverage"], "low")


@unittest.skipUnless(os.environ.get("TRAVEL_TEST_DB_URL"), "TRAVEL_TEST_DB_URL not set")
class LiveGtfsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.conn = psycopg.connect(os.environ["TRAVEL_TEST_DB_URL"], row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_tables_are_populated(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM gtfs_feed")
            self.assertGreater(cur.fetchone()["n"], 0)
            cur.execute("SELECT count(*) n FROM transit_headway")
            self.assertGreater(cur.fetchone()["n"], 0)

    def test_trento_and_paris_headways(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.name, t.day_type, t.time_band, t.stops,
                       t.median_headway_min
                FROM transit_city_service t
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Trento', 'Paris')
                """
            )
            rows = {(r["name"], r["day_type"], r["time_band"]): r
                    for r in cur.fetchall()}
        if not rows:
            self.skipTest("GTFS feeds not loaded yet")
        trento = rows.get(("Trento", "weekday", "peak"))
        paris = rows.get(("Paris", "weekday", "peak"))
        self.assertIsNotNone(trento)
        self.assertIsNotNone(paris)
        self.assertTrue(5 <= float(trento["median_headway_min"]) <= 30)
        self.assertTrue(5 <= float(paris["median_headway_min"]) <= 20)
        self.assertGreater(trento["stops"], 50)
        self.assertGreater(paris["stops"], 1_000)

    def test_feed_place_uses_mapped_stops(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT stops_in_centre, stops_mapped
                FROM gtfs_feed_place
                WHERE feed_id IN ('mdb-978', 'tdg-80921')
                """
            )
            rows = cur.fetchall()
        if not rows:
            self.skipTest("Trento/Paris feeds not loaded yet")
        for row in rows:
            self.assertGreater(row["stops_mapped"], 0)
            self.assertLessEqual(row["stops_in_centre"], row["stops_mapped"])


if __name__ == "__main__":
    unittest.main()
