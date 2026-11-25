"""Tests for the official travel-advisory loader."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_advisories as ba  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_us_title_level_and_country(self):
        title = "Mexico - Level 2: Exercise Increased Caution"
        self.assertEqual(ba.us_level(title), 2)
        self.assertEqual(ba.us_country_name(title), "Mexico")

    def test_us_unknown_level_is_none(self):
        self.assertIsNone(ba.us_level("No level here"))

    def test_uk_labels(self):
        self.assertEqual(
            ba.uk_label("avoid_all_travel_to_whole_country"),
            "Advise against all travel",
        )
        self.assertEqual(
            ba.uk_label(None), "No blanket advisory against travel"
        )

    def test_advisory_name_aliases(self):
        lookup = {
            "burma": "MM",
            "cote d ivoire": "CI",
            "netherlands": "NL",
        }
        self.assertEqual(ba.resolve_country("Burma", lookup), "MM")
        self.assertEqual(ba.resolve_country("Côte d'Ivoire", lookup), "CI")


@unittest.skipUnless(os.environ.get("TRAVEL_TEST_DB_URL"), "TRAVEL_TEST_DB_URL not set")
class LiveAdvisoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.conn = psycopg.connect(os.environ["TRAVEL_TEST_DB_URL"], row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_both_sources_loaded(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT source, count(*) n FROM travel_advisory GROUP BY source"
            )
            counts = {r["source"]: r["n"] for r in cur.fetchall()}
        self.assertGreater(counts.get(ba.US_SOURCE, 0), 200)
        self.assertGreater(counts.get(ba.UK_SOURCE, 0), 200)

    def test_mexico_keeps_the_two_scales_separate(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, level, status_code FROM travel_advisory
                WHERE country_iso2 = 'MX'
                """
            )
            rows = {r["source"]: r for r in cur.fetchall()}
        self.assertEqual(rows[ba.US_SOURCE]["level"], 2)
        self.assertEqual(
            rows[ba.UK_SOURCE]["status_code"],
            "avoid_all_but_essential_travel_to_parts",
        )

    def test_profile_country_block_includes_advisories(self):
        import place_profiles as pp
        with self.conn.cursor() as cur:
            profile = pp.build_profile(cur, "gn-3530597")  # Mexico City
        advisories = profile["country"]["advisories"]
        self.assertEqual({a["source"] for a in advisories}, {ba.US_SOURCE, ba.UK_SOURCE})
        self.assertIn("not averaged", profile["country"]["advisory_note"])


if __name__ == "__main__":
    unittest.main()
