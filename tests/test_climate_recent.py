"""Offline tests for the recent-climate loader."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_climate_recent as bc  # noqa: E402


class MonthlyNormalTests(unittest.TestCase):
    def test_monthly_aggregation(self):
        payload = {
            "daily": {
                "time": ["2015-01-01", "2015-01-02", "2016-01-01", "2016-01-02"],
                "temperature_2m_max": [10.0, 12.0, 14.0, 16.0],
                "temperature_2m_min": [2.0, 4.0, 6.0, 8.0],
                "precipitation_sum": [0.0, 2.0, 0.0, 4.0],
            }
        }
        rows = bc.monthly_normals(payload, 2)
        jan = rows[0]
        self.assertEqual(jan["month"], 1)
        self.assertEqual(jan["avg_high_c"], 13.0)
        self.assertEqual(jan["avg_low_c"], 5.0)
        self.assertEqual(jan["precip_mm"], 3.0)
        self.assertEqual(jan["rainy_days"], 1.0)
        self.assertEqual(jan["hot_days_30c"], 0.0)

    def test_nasa_dates_and_missing_values(self):
        payload = {
            "daily": {
                "time": ["20150101", "20150102"],
                "temperature_2m_max": [30.0, -999.0],
                "temperature_2m_min": [20.0, -999.0],
                "precipitation_sum": [1.5, -999.0],
            }
        }
        rows = bc.monthly_normals(payload, 1)
        self.assertEqual(rows[0]["hot_days_30c"], 1.0)
        self.assertEqual(rows[0]["warm_nights"], 1.0)
        self.assertEqual(rows[0]["precip_mm"], 1.5)


if __name__ == "__main__":
    unittest.main()
