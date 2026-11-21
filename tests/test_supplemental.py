"""Offline tests for the supplemental Eurostat loader helpers."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_supplemental as s  # noqa: E402


def payload(size_freq=1, values=None):
    return {
        "label": "test",
        "id": ["freq", "geo"],
        "size": [size_freq, 2],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}}},
            "geo": {"category": {"index": {"ES51": 0, "ES52": 1}}},
        },
        "value": values if values is not None else {"0": 10, "1": 20},
    }


class JsonStatTests(unittest.TestCase):
    def test_index_to_code(self):
        self.assertEqual(
            s._index_to_code(payload(), "geo"),
            {0: "ES51", 1: "ES52"},
        )

    def test_values_by_geo(self):
        self.assertEqual(
            s._values_by_geo(payload()),
            {"ES51": 10.0, "ES52": 20.0},
        )

    def test_skips_null_values(self):
        self.assertEqual(
            s._values_by_geo(payload(values={"0": 10, "1": None})),
            {"ES51": 10.0},
        )

    def test_rejects_non_single_non_geo_dimensions(self):
        with self.assertRaises(ValueError):
            s._values_by_geo(payload(size_freq=2))

    def test_empty_values(self):
        self.assertEqual(s._values_by_geo(payload(values={})), {})


class ConstantsTests(unittest.TestCase):
    def test_official_endpoints(self):
        self.assertIn("ec.europa.eu", s.EUROSTAT_BASE)
        self.assertEqual(s.TOURISM_DATASET, "tour_occ_nin2m")
        self.assertEqual(s.POPULATION_DATASET, "demo_r_pjangrp3")
        self.assertIn("NUTS_RG_01M_2024", s.NUTS2_GEOJSON_URL)


if __name__ == "__main__":
    unittest.main()
