"""Offline tests for the Overture transit-stop loader."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_transit_stops as t  # noqa: E402


class TransitCategoryTests(unittest.TestCase):
    def test_has_core_public_transport_categories(self):
        for category in (
            "bus_station",
            "train_station",
            "metro_station",
            "public_transit_facility_or_service",
            "light_rail_and_subway_station",
        ):
            self.assertIn(category, t.TRANSIT_CATEGORIES)

    def test_generic_travel_category_is_excluded(self):
        self.assertNotIn("travel_and_transportation", t.TRANSIT_CATEGORIES)

    def test_source_is_labelled(self):
        self.assertIn("Overture Maps Places", t.TRANSIT_SOURCE)
        self.assertIn("transit", t.TRANSIT_SOURCE.lower())


if __name__ == "__main__":
    unittest.main()
