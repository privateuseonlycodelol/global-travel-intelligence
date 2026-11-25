"""Tests for the nature typology layers."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_nature as bn  # noqa: E402
import place_profiles as pp  # noqa: E402


class ClassificationTests(unittest.TestCase):
    def test_relief_classes(self):
        self.assertEqual(bn.relief_class(3), "low relief")
        self.assertEqual(bn.relief_class(25), "rolling or hilly")
        self.assertEqual(bn.relief_class(60), "high relief")
        self.assertIsNone(bn.relief_class(None))

    def test_position_classes(self):
        self.assertEqual(bn.position_class(-1.2), "valley or low position")
        self.assertEqual(bn.position_class(0.0), "slope or neutral")
        self.assertEqual(bn.position_class(0.9), "ridge or high position")

    def test_nature_summary(self):
        row = {
            "landscape": {
                "biome_name": "Temperate Conifer Forests",
                "ecoregion_name": "Alps conifer and mixed forests",
                "realm": "Palearctic",
                "source": "RESOLVE",
                "observed_period": "2017",
            },
            "terrain": {
                "radius_m": 10000,
                "elevation_median_m": 357.0,
                "elevation_sd_m": 250.0,
                "tri_median": 32.0,
                "tpi_median": -1.0,
                "relief_class": "rolling or hilly",
                "position_class": "valley or low position",
                "source": "Amatulli",
                "observed_period": "2018",
            },
        }
        summary = pp.nature_summary(row)
        self.assertEqual(summary["landscape"]["biome"], "Temperate Conifer Forests")
        self.assertEqual(summary["terrain"]["radius_km"], 10)
        self.assertEqual(summary["terrain"]["position_class"], "valley or low position")


@unittest.skipUnless(os.environ.get("TRAVEL_TEST_DB_URL"), "TRAVEL_TEST_DB_URL not set")
class LiveNatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.conn = psycopg.connect(os.environ["TRAVEL_TEST_DB_URL"], row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_coverage(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM destination_landscape")
            self.assertGreater(cur.fetchone()["n"], 12_000)
            cur.execute("SELECT count(*) n FROM destination_terrain")
            self.assertEqual(cur.fetchone()["n"], 13_101)

    def test_land_cover_fractions(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM destination_land_cover")
            if cur.fetchone()["n"] == 0:
                self.skipTest("land cover not loaded yet")
            cur.execute(
                """
                SELECT tree_fraction, shrub_fraction, grassland_fraction,
                       cropland_fraction, built_up_fraction, bare_fraction,
                       snow_fraction, water_fraction, wetland_fraction,
                       mangrove_fraction, moss_fraction
                FROM destination_land_cover
                WHERE radius_m = 1000
                LIMIT 20
                """
            )
            rows = cur.fetchall()
        for row in rows:
            values = [v for v in row.values() if v is not None]
            if values:
                self.assertAlmostEqual(sum(values), 1.0, delta=0.05)

    def test_green_score(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) n FROM destination_green_score")
            if cur.fetchone()["n"] == 0:
                self.skipTest("green score not loaded yet")
            cur.execute(
                "SELECT min(green_score_0_100) lo, max(green_score_0_100) hi "
                "FROM destination_green_score"
            )
            row = cur.fetchone()
            self.assertGreaterEqual(row["lo"], 0)
            self.assertLessEqual(row["hi"], 100)
            trento = pp.build_profile(cur, "gn-3165243")
            paris = pp.build_profile(cur, "gn-2988507")
        self.assertGreater(
            trento["nature"]["green_score"]["score_0_100"],
            paris["nature"]["green_score"]["score_0_100"],
        )

    def test_greenery_and_environment_scores(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n, count(greenery_score_0_100) scored "
                "FROM destination_greenery_score"
            )
            row = cur.fetchone()
            if row["n"] == 0:
                self.skipTest("greenery score not built")
            self.assertGreaterEqual(row["scored"], 3_000)
            trento = pp.build_profile(cur, "gn-3165243")
            paris = pp.build_profile(cur, "gn-2988507")
        greenery = trento["nature"]["greenery_score"]
        self.assertGreater(greenery["score_0_100"], 0)
        self.assertLessEqual(greenery["score_0_100"], 100)
        self.assertEqual(
            set(greenery["components_0_100"]),
            {"local_tree", "local_other_vegetation",
             "hinterland_tree_shrub", "parks"},
        )
        self.assertGreater(
            trento["nature"]["greenery_score"]["score_0_100"],
            paris["nature"]["greenery_score"]["score_0_100"],
        )
        environment = trento["nature"]["environment_score"]
        self.assertEqual(
            set(environment["components_0_100"]),
            {"greenery", "air", "water"},
        )
        self.assertGreater(
            paris["nature"]["environment_score"]["components_0_100"]["air"],
            trento["nature"]["environment_score"]["components_0_100"]["air"],
        )

    def test_all_green_score(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n, count(all_green_score_0_100) scored "
                "FROM destination_all_green_score"
            )
            row = cur.fetchone()
            if row["n"] == 0:
                self.skipTest("all-green score not built")
            self.assertGreaterEqual(row["scored"], 3_000)
            trento = pp.build_profile(cur, "gn-3165243")
            paris = pp.build_profile(cur, "gn-2988507")
        full = trento["nature"]["all_green_score"]
        self.assertEqual(
            set(full["components_0_100"]),
            {"vegetation", "parks", "air", "water"},
        )
        self.assertAlmostEqual(
            float(full["score_0_100"]),
            0.40 * float(full["components_0_100"]["vegetation"])
            + 0.20 * float(full["components_0_100"]["parks"])
            + 0.20 * float(full["components_0_100"]["air"])
            + 0.20 * float(full["components_0_100"]["water"]),
            places=4,
        )
        self.assertGreater(
            trento["nature"]["all_green_score"]["score_0_100"],
            paris["nature"]["all_green_score"]["score_0_100"],
        )

    def test_combined_score(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n, count(combined_score_0_100) scored "
                "FROM destination_combined_score"
            )
            row = cur.fetchone()
            if row["n"] == 0:
                self.skipTest("combined score not built")
            self.assertGreaterEqual(row["scored"], 3_000)
            trento = pp.build_profile(cur, "gn-3165243")
        combined = trento["nature"]["combined_score"]
        self.assertAlmostEqual(
            combined["score_0_100"],
            0.5 * combined["components_0_100"]["physical_greenery"]
            + 0.5 * combined["components_0_100"]["environmental_quality"],
            places=4,
        )

    def test_urban_greenery(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n, count(urban_green_score_0_100) scored "
                "FROM destination_urban_greenery"
            )
            row = cur.fetchone()
            if row["n"] == 0:
                self.skipTest("urban greenery not built")
            self.assertGreaterEqual(row["scored"], 400)
            cur.execute(
                """
                SELECT urban_tree_score_0_100, built_up_fraction
                FROM destination_urban_greenery
                WHERE destination_id = 'gn-6077243'
                """
            )
            montreal = cur.fetchone()
            profile = pp.build_profile(cur, "gn-6077243")
        self.assertIsNotNone(montreal)
        self.assertGreater(montreal["urban_tree_score_0_100"], 20)
        self.assertLess(montreal["urban_tree_score_0_100"], 35)
        urban = profile["nature"]["urban_greenery"]
        self.assertGreater(urban["score_0_100"], 0)

    def test_ghsl_greenery(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n, count(physical_green_score) physical, "
                "count(nature_access_score) nature, "
                "count(environment_score) environment "
                "FROM destination_ghsl_greenery"
            )
            row = cur.fetchone()
            if row["n"] == 0:
                self.skipTest("GHSL greenery not built")
            self.assertGreaterEqual(row["physical"], 450)
            self.assertEqual(row["nature"], 0)
            self.assertGreaterEqual(row["environment"], 450)
            cur.execute(
                """
                SELECT physical_green_score, nature_access_score,
                       everyday_greenery_available,
                       public_green_access_available
                FROM destination_ghsl_greenery
                WHERE name = 'Montréal'
                """
            )
            montreal = cur.fetchone()
        self.assertIsNotNone(montreal)
        self.assertGreater(montreal["physical_green_score"], 20)
        self.assertIsNone(montreal["nature_access_score"])
        self.assertFalse(montreal["everyday_greenery_available"])
        self.assertFalse(montreal["public_green_access_available"])

    def test_surrounding_nature(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n, count(surrounding_nature_score_0_100) scored "
                "FROM destination_surrounding_nature"
            )
            row = cur.fetchone()
            if row["n"] == 0:
                self.skipTest("surrounding nature not built")
            self.assertGreaterEqual(row["scored"], 4_000)
            profile = pp.build_profile(cur, "gn-6077243")
        self.assertIsNotNone(profile["nature"]["surrounding_nature"])

    def test_land_only_vegetation(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT hinterland_water_fraction, hinterland_tree_share
                FROM destination_green_score
                WHERE destination_id = 'gn-1880252'
                """
            )
            row = cur.fetchone()
        if row is None:
            self.skipTest("Singapore land cover not loaded")
        self.assertGreater(row["hinterland_water_fraction"], 0.4)
        self.assertGreater(row["hinterland_tree_share"], 0.25)

    def test_river_access(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT distance_km, river_order
                FROM destination_river_access
                WHERE destination_id = 'gn-2950159'
                """
            )
            row = cur.fetchone()
        if row is None:
            self.skipTest("river access not loaded")
        self.assertLess(row["distance_km"], 2.0)
        self.assertGreaterEqual(row["river_order"], 4)

    def test_inland_water(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) n FROM destination_inland_water"
            )
            if cur.fetchone()["n"] == 0:
                self.skipTest("inland water not loaded yet")
            profile = pp.build_profile(cur, "gn-3037543")  # Annecy
        water = profile["nature"]["inland_water"]
        self.assertLess(water["nearest_lake_km"], 2.0)
        self.assertGreater(water["lake_area_km2"], 20)

    def test_trento_and_paris_nature(self):
        with self.conn.cursor() as cur:
            trento = pp.build_profile(cur, "gn-3165243")
            paris = pp.build_profile(cur, "gn-2988507")
        self.assertEqual(trento["nature"]["landscape"]["biome"], "Temperate Conifer Forests")
        self.assertEqual(trento["nature"]["climate"]["koppen_code"], "Cfa")
        self.assertEqual(paris["nature"]["climate"]["koppen_code"], "Cfb")
        self.assertEqual(trento["nature"]["terrain"]["position_class"], "valley or low position")
        self.assertGreater(trento["nature"]["terrain"]["elevation_sd_m"], 100)
        self.assertEqual(paris["nature"]["landscape"]["biome"], "Temperate Broadleaf & Mixed Forests")
        self.assertEqual(paris["nature"]["terrain"]["relief_class"], "low relief")
        if trento["nature"].get("land_cover"):
            self.assertGreater(
                trento["nature"]["land_cover"]["fifteen_km"]["classes"]["tree_fraction"]["percent"],
                50,
            )
            self.assertGreater(
                paris["nature"]["land_cover"]["one_km"]["classes"]["built_up_fraction"]["percent"],
                70,
            )


if __name__ == "__main__":
    unittest.main()
