"""Tests for the polygon-based metro assignment."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_metro as bm  # noqa: E402


class GpkgTests(unittest.TestCase):
    def test_mojibake_repair(self):
        self.assertEqual(bm.fix_mojibake("MÃ©xico"), "México")
        self.assertEqual(bm.fix_mojibake("CÃ´te d'Ivoire"), "Côte d'Ivoire")
        self.assertEqual(bm.fix_mojibake("China"), "China")
        self.assertIsNone(bm.fix_mojibake(None))

    @unittest.skipUnless(bm.CENTROIDS_GPKG_CACHE.exists(), "UCDB GeoPackage not cached")
    def test_reads_all_polygons_in_wgs84(self):
        rows = bm.read_gpkg_polygons(bm.CENTROIDS_GPKG_CACHE)
        self.assertEqual(len(rows), 11_422)
        hanoi = next(row for row in rows if row["name"] == "Hanoi")
        self.assertEqual(hanoi["country"], "Vietnam")
        self.assertGreater(hanoi["population"], 1_000_000)
        self.assertEqual(hanoi["geom"][:5], b"\x01\x06\x00\x00\x00")
        self.assertGreater(len(hanoi["geom"]), 100)

    def test_wkb_header_is_multipolygon(self):
        polygons = [[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]]]
        wkb = bm._wkb_multipolygon(polygons)
        self.assertEqual(wkb[:5], b"\x01\x06\x00\x00\x00")


@unittest.skipUnless(os.environ.get("TRAVEL_TEST_DB_URL"), "TRAVEL_TEST_DB_URL not set")
class LiveMetroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.conn = psycopg.connect(os.environ["TRAVEL_TEST_DB_URL"], row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_polygon_coverage(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS matched,
                       count(*) FILTER (WHERE match_kind = 'contained') AS contained,
                       count(*) FILTER (WHERE match_kind = 'nearby') AS nearby
                FROM destination_metro
                """
            )
            row = cur.fetchone()
        self.assertGreater(row["matched"], 9_000)
        self.assertGreater(row["contained"], 8_900)
        self.assertEqual(row["contained"] + row["nearby"], row["matched"])

    def test_no_cross_border_containment(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) AS n
                FROM destination_metro m
                JOIN destinations d USING (destination_id)
                LEFT JOIN country_facts c ON c.country_iso2 = d.country_iso2
                WHERE c.name IS DISTINCT FROM m.urban_centre_country
                  AND (c.name, m.urban_centre_country) NOT IN (
                      ('Hong Kong', 'China'), ('Macao', 'China')
                  )
                """
            )
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_hanoi_districts_share_the_hanoi_centre(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.name, m.urban_centre_id, m.urban_centre_name
                FROM destination_metro m
                JOIN destinations d USING (destination_id)
                WHERE d.name IN ('Hanoi', 'Ba Dinh', 'Tương Mai', 'Nghĩa Đô', 'Mai Dịch')
                """
            )
            rows = cur.fetchall()
        self.assertEqual(len(rows), 5)
        self.assertEqual({row["urban_centre_name"] for row in rows}, {"Hanoi"})

    def test_profile_exposes_metro_population_and_area(self):
        import place_profiles as pp
        with self.conn.cursor() as cur:
            profile = pp.build_profile(cur, "gn-1581130")  # Hanoi
        centre = profile["place"]["urban_centre"]
        self.assertEqual(centre["urban_centre_name"], "Hanoi")
        self.assertEqual(centre["match_kind"], "contained")
        self.assertEqual(centre["match_distance_km"], 0.0)
        self.assertGreater(centre["centre_population"], 4_000_000)
        self.assertGreater(centre["centre_area_km2"], 500)


if __name__ == "__main__":
    unittest.main()
