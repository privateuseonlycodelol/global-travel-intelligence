"""Offline tests for the GHSCI result importer."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_ghsci as bg  # noqa: E402


class ReferenceIndexTests(unittest.TestCase):
    ROW = {
        "local_nh_population_density": "4796.72615043832",
        "local_nh_intersection_density": "153.64089417556124",
        "local_daily_living": "2.001279498249032",
    }

    def test_trento_index(self):
        self.assertEqual(bg.reference_index(self.ROW), 2.0955)

    def test_missing_input_is_not_imputed(self):
        row = dict(self.ROW)
        row["local_daily_living"] = ""
        self.assertIsNone(bg.reference_index(row))

    def test_schema_has_the_queue_and_results(self):
        self.assertIn("ghsci_city_indicators", bg.SCHEMA_SQL)
        self.assertIn("ghsci_jobs", bg.SCHEMA_SQL)
        self.assertIn("low_confidence", bg.SCHEMA_SQL)


if __name__ == "__main__":
    unittest.main()
