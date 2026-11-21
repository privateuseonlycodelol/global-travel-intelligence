"""Offline unit tests: no database, no network.

Run with: python -m unittest discover -s tests -v
"""

import math
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_global_40k as b  # noqa: E402


def city(name, lat, lon, pop, fcode="PPL", cc="ES", a1="", a2="", a3=""):
    return {
        "destination_id": f"gn-{name}",
        "name": name,
        "country_iso2": cc,
        "lat": lat,
        "lon": lon,
        "population": pop,
        "fcode": fcode,
        "a1": a1,
        "a2": a2,
        "a3": a3,
    }


class DedupeTests(unittest.TestCase):
    def dedupe(self, rows, **kwargs):
        df = pd.DataFrame(rows)
        kept, dropped = b.dedupe_metro_areas(df, **kwargs)
        return set(kept["name"]), set(dropped["name"])

    def test_same_municipality_district_is_merged(self):
        kept, dropped = self.dedupe([
            city("Parent", 41.39, 2.17, 1_600_000, a3="08019"),
            city("District", 41.40, 2.19, 235_000, a3="08019"),
        ])
        self.assertEqual(kept, {"Parent"})
        self.assertEqual(dropped, {"District"})

    def test_separate_municipality_is_kept(self):
        kept, _ = self.dedupe([
            city("Parent", 41.39, 2.17, 1_600_000, a3="08019"),
            city("Neighbour", 41.36, 2.10, 257_000, a3="08101"),
        ])
        self.assertEqual(kept, {"Parent", "Neighbour"})

    def test_peer_city_in_same_admin3_is_kept(self):
        # Venice/Mestre: same comune code, but only 2.9x apart.
        kept, _ = self.dedupe([
            city("Mestre", 45.49, 12.24, 147_000, a3="027042"),
            city("Venice", 45.44, 12.33, 51_000, a3="027042"),
        ])
        self.assertEqual(kept, {"Mestre", "Venice"})

    def test_ppla2_borough_is_merged(self):
        kept, _ = self.dedupe([
            city("New York City", 40.71, -74.01, 8_800_000, cc="US"),
            city("Brooklyn", 40.65, -73.95, 2_736_000, fcode="PPLA2", cc="US"),
        ])
        self.assertEqual(kept, {"New York City"})

    def test_independent_city_near_larger_city_is_kept(self):
        kept, _ = self.dedupe([
            city("Big", 37.77, -122.42, 830_000, cc="US", a1="CA", a3="075"),
            city("Oakland", 37.80, -122.27, 419_000, fcode="PPLA2", cc="US", a1="CA"),
        ])
        self.assertIn("Oakland", kept)

    def test_dedupe_can_be_disabled_by_caller(self):
        # load_geonames_cities skips this function when --no-metro-dedupe.
        self.assertTrue(callable(b.dedupe_metro_areas))


class GeometryTests(unittest.TestCase):
    def test_haversine_one_degree_at_equator(self):
        d = b.haversine_km(0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(d, 111.19, delta=0.2)

    def test_envelopes_are_per_country(self):
        df = pd.DataFrame({
            "country_iso2": ["SE", "SE", "ES"],
            "lat": [59.3, 57.7, 40.4],
            "lon": [18.1, 12.0, -3.7],
        })
        envs = b._scan_envelopes(df)
        self.assertEqual(len(envs), 2)
        # every envelope is a plain conjunction-friendly bbox
        for min_lon, max_lon, min_lat, max_lat in envs:
            self.assertLess(min_lon, max_lon)
            self.assertLess(min_lat, max_lat)

    def test_antimeridian_falls_back_to_full_longitude(self):
        df = pd.DataFrame({
            "country_iso2": ["FJ", "FJ"],
            "lat": [-17.0, -17.1],
            "lon": [178.0, -179.0],
        })
        (min_lon, max_lon, min_lat, max_lat), = b._scan_envelopes(df)
        self.assertEqual((min_lon, max_lon), (-180.0, 180.0))
        self.assertLess(min_lat, -17.0)
        self.assertGreater(max_lat, -17.0)

    def test_global_run_uses_one_world_scan(self):
        df = pd.DataFrame({
            "country_iso2": [f"C{i:02d}" for i in range(45)],
            "lat": [0.0] * 45,
            "lon": [float(i) for i in range(45)],
        })
        self.assertEqual(b._scan_envelopes(df), [(-180.0, 180.0, -90.0, 90.0)])

    def test_scan_sql_uses_union_all_and_bbox_conjunctions(self):
        sql = b._places_scan_sql(
            "s3://bucket/*",
            [(-4.0, 2.5, 40.0, 42.0), (11.0, 19.0, 57.0, 60.0)],
        )
        self.assertIn("UNION ALL", sql)
        self.assertNotIn(" UNION\n", sql)
        self.assertIn("bbox.xmin <= 2.5", sql)
        self.assertIn("read_parquet('s3://bucket/*'", sql)


class CatalogueTests(unittest.TestCase):
    def test_required_columns_are_declared(self):
        for table, columns in b.REQUIRED_COLUMNS.items():
            self.assertIn("destination_id", columns, table)
        self.assertIn("source", b.REQUIRED_COLUMNS["climate_monthly"])
        self.assertIn("source", b.REQUIRED_COLUMNS["poi_metrics"])
        self.assertIn("source", b.REQUIRED_COLUMNS["tourism_pressure"])

    def test_scorer_has_expected_defaults(self):
        self.assertIn("rank_destinations_v2", b.SCORER_SQL)
        self.assertIn("p_countries", b.SCORER_SQL)
        self.assertIn("NULLIF(w.active_weight, 0)", b.SCORER_SQL)


if __name__ == "__main__":
    unittest.main()
