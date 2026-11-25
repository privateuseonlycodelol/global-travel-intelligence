"""Tests for the Eurostat city-rent loader."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_rent as br  # noqa: E402
import bootstrap_rent_uk as bru  # noqa: E402


def payload(value):
    return {
        "id": ["freq", "building", "currency", "geo", "time"],
        "size": [1, 2, 2, 1, 2],
        "dimension": {
            "freq": {"category": {"index": {"A": 0}, "label": {"A": "Annual"}}},
            "building": {
                "category": {
                    "index": {"FLAT1": 0, "FLAT2": 1},
                    "label": {"FLAT1": "1-bedroom flat", "FLAT2": "2-bedroom flat"},
                }
            },
            "currency": {
                "category": {"index": {"EUR": 0, "NAC": 1}, "label": {}}
            },
            "geo": {"category": {"index": {"FR_CAP": 0}, "label": {"FR_CAP": "Paris"}}},
            "time": {
                "category": {"index": {"2024": 0, "2025": 1}, "label": {}}
            },
        },
        "value": value,
    }


class UkParseTests(unittest.TestCase):
    def test_number_handles_commas_and_placeholders(self):
        self.assertEqual(bru.number("1,234"), 1234.0)
        self.assertIsNone(bru.number("[x]"))
        self.assertIsNone(bru.number(None))

    def test_area_name_tokens_can_find_a_city(self):
        candidates = [name for _score, name in bru.candidate_names("Kingston upon Hull, City of")]
        self.assertIn("hull", candidates)

    def test_house_type_keys_are_present(self):
        keys = {key for key, _bedrooms, _column in bru.TYPES}
        self.assertIn("FLAT2", keys)
        self.assertIn("HOUSE_TERRACE", keys)
        self.assertIn("ALL", keys)


class ParseTests(unittest.TestCase):
    def test_latest_national_currency_value_wins(self):
        # Flat index order is freq, building, currency, geo, time.
        values = {
            str((((0 * 2 + 0) * 2 + 1) * 1 + 0) * 2 + 0): 1000.0,  # FLAT1 NAC 2024
            str((((0 * 2 + 0) * 2 + 1) * 1 + 0) * 2 + 1): 1100.0,  # FLAT1 NAC 2025
            str((((0 * 2 + 0) * 2 + 0) * 1 + 0) * 2 + 1): 1200.0,  # FLAT1 EUR 2025
            str((((0 * 2 + 1) * 2 + 1) * 1 + 0) * 2 + 1): 2000.0,  # FLAT2 NAC 2025
        }
        parsed = br.parse(payload(values))
        self.assertIn("FR_CAP", parsed)
        self.assertEqual(parsed["FR_CAP"]["FLAT1"]["rent_local"], 1100.0)
        self.assertEqual(parsed["FR_CAP"]["FLAT1"]["year"], 2025)
        self.assertEqual(parsed["FR_CAP"]["FLAT2"]["bedrooms"], 2)
        self.assertEqual(parsed["FR_CAP"]["FLAT2"]["rent_local"], 2000.0)

    def test_unknown_geography_is_ignored(self):
        self.assertEqual(br.parse(payload({})), {})

    def test_currency_map_keeps_pre_euro_year(self):
        self.assertEqual(br.CURRENCY_BY_ISO2["BG"], "BGN")
        self.assertEqual(br.CURRENCY_BY_ISO2["FR"], "EUR")


@unittest.skipUnless(os.environ.get("TRAVEL_TEST_DB_URL"), "TRAVEL_TEST_DB_URL not set")
class LiveRentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.conn = psycopg.connect(os.environ["TRAVEL_TEST_DB_URL"], row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_coverage_and_currencies(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(DISTINCT destination_id) cities, count(*) rows FROM rent_monthly"
            )
            row = cur.fetchone()
            self.assertGreaterEqual(row["cities"], 150)
            self.assertGreaterEqual(row["rows"], 1_200)
            cur.execute(
                "SELECT count(DISTINCT destination_id) n FROM rent_monthly "
                "WHERE source LIKE 'Eurostat%'"
            )
            self.assertGreaterEqual(cur.fetchone()["n"], 40)
            cur.execute(
                "SELECT count(DISTINCT destination_id) n FROM rent_monthly "
                "WHERE source LIKE 'ONS%'"
            )
            self.assertGreaterEqual(cur.fetchone()["n"], 100)
            cur.execute("SELECT DISTINCT currency_code FROM rent_monthly")
            currencies = {r["currency_code"] for r in cur.fetchall()}
        self.assertIn("EUR", currencies)
        self.assertIn("GBP", currencies)
        self.assertIn("RON", currencies)
        self.assertIn("GBP", currencies)

    def test_profile_uses_local_currency(self):
        import place_profiles as pp
        with self.conn.cursor() as cur:
            paris = pp.build_profile(cur, "gn-2988507")
            london = pp.build_profile(cur, "gn-2643743")
        self.assertEqual(paris["housing"]["currency_code"], "EUR")
        self.assertEqual(paris["housing"]["headline"]["rent_local"], 2500.0)
        self.assertEqual(london["housing"]["currency_code"], "GBP")
        self.assertEqual(london["housing"]["headline"]["dwelling_type"], "FLAT2")
        self.assertEqual(london["housing"]["headline"]["area_name"], "London")


if __name__ == "__main__":
    unittest.main()
