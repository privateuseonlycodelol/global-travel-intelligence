"""Tests for place search/profile helpers, the places loader and MCP fixes.

Offline by default. Set TRAVEL_TEST_DB_URL to a loaded database to also run
the integration checks against real data.
"""

import os
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_places as bp  # noqa: E402
import mcp_server as m  # noqa: E402
import place_profiles as pp  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_accents_case_and_punctuation(self):
        self.assertEqual(pp.normalize_name("São Paulo"), "sao paulo")
        self.assertEqual(pp.normalize_name("sao-paulo"), "sao paulo")
        self.assertEqual(pp.normalize_name("  SAO   PAULO "), "sao paulo")
        self.assertEqual(pp.normalize_name("Köln"), "koln")
        self.assertEqual(pp.normalize_name("Straße"), "strasse")

    def test_non_latin_scripts_survive(self):
        self.assertEqual(pp.normalize_name("東京"), "東京")

    def test_empty(self):
        self.assertEqual(pp.normalize_name(None), "")
        self.assertEqual(pp.normalize_name("  ,, "), "")

    def test_country_suffix(self):
        self.assertEqual(pp.split_country_suffix("Porto, pt"), ("Porto", "PT"))
        self.assertEqual(pp.split_country_suffix("Washington, D.C."), ("Washington, D.C.", None))
        self.assertEqual(pp.split_country_suffix("Lisbon"), ("Lisbon", None))


class AliasRowTests(unittest.TestCase):
    def test_builds_deduplicated_aliases_for_loaded_cities_only(self):
        lines = [
            "2267057\tLisbon\tLisbon\tLisboa,Lisbonne,LIS,https://x.example,Lisbon\t38.7\t-9.1\n",
            "999\tNot Loaded\tNot Loaded\tNope\t0\t0\n",
        ]
        rows = bp.build_alias_rows({2267057: "gn-2267057"}, lines)
        norms = [r[2] for r in rows]
        self.assertEqual(norms, ["lisbon", "lisboa", "lisbonne", "lis"])
        self.assertEqual(rows[0][3], "name")
        self.assertTrue(all(r[0] == "gn-2267057" for r in rows))

    def test_skips_malformed_lines(self):
        self.assertEqual(bp.build_alias_rows({1: "gn-1"}, ["junk\n", "\n"]), [])


class CountryInfoTests(unittest.TestCase):
    SAMPLE = (
        "# comment line\n"
        "PT\tPRT\t620\tPO\tPortugal\tLisbon\t92391.0\t10281762\tEU\t.pt\tEUR\tEuro\t351"
        "\t####-###\t^(\\d{7})$\tpt-PT,mwl\t2264397\tES\t\n"
    )

    def test_parse(self):
        rows = bp.parse_country_info(self.SAMPLE)
        self.assertEqual(len(rows), 1)
        pt = rows[0]
        self.assertEqual(pt["country_iso2"], "PT")
        self.assertEqual(pt["currency_code"], "EUR")
        self.assertEqual(pt["languages"], ["pt-PT", "mwl"])
        self.assertEqual(pt["neighbours"], ["ES"])
        self.assertEqual(pt["population"], 10281762)


def _wb(iso2, year, value):
    return {"country": {"id": iso2}, "date": str(year), "value": value}


class WorldBankParsingTests(unittest.TestCase):
    def test_latest_non_null_year_wins(self):
        records = [_wb("PT", 2021, 1.0), _wb("PT", 2023, None), _wb("PT", 2022, 2.0),
                   _wb("JP", 2020, 3.0), {"country": {"id": "PT"}, "date": "x", "value": 5}]
        # Aggregates such as "1W" are dropped later against country_facts.
        self.assertEqual(bp.latest_values(records), {"PT": (2.0, 2022), "JP": (3.0, 2020)})

    def test_price_level_uses_same_year_ratio(self):
        ppp = [_wb("JP", 2025, 97.0), _wb("JP", 2024, 100.0), _wb("CH", 2025, 0.93)]
        fx = [_wb("JP", 2024, 150.0), _wb("CH", 2025, 0.83)]
        out = bp.price_level_values(ppp, fx)
        self.assertAlmostEqual(out["JP"][0], 100.0 / 150.0)
        self.assertEqual(out["JP"][1], 2024)
        self.assertAlmostEqual(out["CH"][0], 0.93 / 0.83)


class MetricSqlTests(unittest.TestCase):
    def test_all_tables_present(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        for metric in pp.METRICS:
            self.assertIn(f"AS {metric.key}__pct_global", sql)
            self.assertIn(f"AS {metric.key}__pct_country", sql)
        self.assertIn("LEFT JOIN mobility_metrics m", sql)
        self.assertEqual(sql.count("%s"), 1)

    def test_missing_tables_become_nulls(self):
        sql = pp.build_metric_sql({"poi_metrics"})
        self.assertNotIn("broadband_metrics", sql)
        self.assertIn("NULL::double precision AS internet_download_mbps", sql)

    def test_lower_is_better_sorts_descending(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("ORDER BY internet_latency_ms DESC", sql)
        self.assertIn("ORDER BY restaurant_count ASC", sql)

    def test_local_currency_has_no_global_percentile(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("NULL::double precision AS short_let_monthly_local__pct_global", sql)


class MetricEntryTests(unittest.TestCase):
    metric = next(x for x in pp.METRICS if x.key == "restaurant_count")

    def test_measured(self):
        entry = pp.metric_entry(self.metric, {
            "restaurant_count": 3445, "restaurant_count__pct_global": 0.993,
            "restaurant_count__pct_country": 1.0, "restaurant_count__n_global": 13101,
            "restaurant_count__n_country": 31, "restaurant_count__source": "Overture",
            "restaurant_count__period": "2026-11",
        })
        self.assertEqual(entry["status"], "measured")
        self.assertEqual(entry["percentile_global"], 99.3)
        self.assertEqual(entry["band_global"], "top 10%")
        self.assertEqual(entry["percentile_in_country"], 100.0)

    def test_not_measured_is_not_zero(self):
        entry = pp.metric_entry(self.metric, {})
        self.assertEqual(entry["status"], "not_measured")
        self.assertIsNone(entry["value"])
        self.assertNotIn("percentile_global", entry)

    def test_single_city_country_has_no_country_percentile(self):
        entry = pp.metric_entry(self.metric, {
            "restaurant_count": 5, "restaurant_count__pct_country": 0.0,
            "restaurant_count__n_country": 1,
        })
        self.assertIsNone(entry["percentile_in_country"])

    def test_bands(self):
        self.assertEqual(pp.percentile_band(95), "top 10%")
        self.assertEqual(pp.percentile_band(80), "top 25%")
        self.assertEqual(pp.percentile_band(50), "above median")
        self.assertEqual(pp.percentile_band(30), "below median")
        self.assertEqual(pp.percentile_band(10), "bottom 25%")
        self.assertIsNone(pp.percentile_band(None))


class ClimateSummaryTests(unittest.TestCase):
    def test_summary(self):
        months = [
            {"month": i, "median_temp_max_c": 10 + i, "median_temp_min_c": i, "precip_mm": 100 - i}
            for i in range(1, 13)
        ]
        summary = pp.climate_summary(months)
        self.assertEqual(summary["warmest_month"], 12)
        self.assertEqual(summary["coolest_month"], 1)
        self.assertEqual(summary["wettest_month"], 1)
        self.assertEqual(summary["driest_month"], 12)
        self.assertEqual(summary["months_with_daily_high_18_to_28c"], [8, 9, 10, 11, 12])
        self.assertEqual(summary["annual_precip_mm"], sum(100 - i for i in range(1, 13)))

    def test_empty(self):
        self.assertEqual(pp.climate_summary([]), {"status": "not_measured"})


class MonthsBetweenTests(unittest.TestCase):
    def test_trip_crossing_month_boundary(self):
        self.assertEqual(m._months_between(date(2027, 1, 25), date(2027, 2, 10)), [1, 2])

    def test_checkout_on_first_of_month_does_not_count(self):
        self.assertEqual(m._months_between(date(2027, 1, 25), date(2027, 2, 1)), [1])

    def test_year_wrap_and_cap(self):
        self.assertEqual(m._months_between(date(2026, 12, 20), date(2027, 1, 5)), [12, 1])
        self.assertEqual(len(m._months_between(date(2026, 1, 1), date(2028, 1, 1))), 12)


class Stage2MissingQuoteTests(unittest.TestCase):
    @staticmethod
    def _quote(total, duration):
        return {
            "estimated_total_trip_usd": total, "flight_duration_min": duration,
            "flight_usd": total, "hotel_median_nightly_usd": None,
        }

    def test_missing_quote_is_neutral_not_rewarded(self):
        rows = [
            {"destination_id": "a", "final_score": 0.8},
            {"destination_id": "b", "final_score": 0.8},
            {"destination_id": "c", "final_score": 0.8},
        ]
        quotes = {"a": self._quote(500, 120), "b": self._quote(900, 300)}
        out = {r["destination_id"]: r for r in m._apply_stage2_rescore(
            rows, quotes, 0.25, 0.15, None, None)}
        # c has no quote: it lands between the cheapest and priciest quoted cities.
        self.assertGreater(out["a"]["final_score"], out["c"]["final_score"])
        self.assertGreater(out["c"]["final_score"], out["b"]["final_score"])

    def test_caps_mark_unknowns_unverified(self):
        rows = [{"destination_id": "a", "final_score": 0.5},
                {"destination_id": "b", "final_score": 0.5},
                {"destination_id": "c", "final_score": 0.5}]
        quotes = {"a": self._quote(500, 120), "b": self._quote(2000, 120)}
        out = {r["destination_id"]: r for r in m._apply_stage2_rescore(
            rows, quotes, 0.25, 0.15, 1000, None)}
        self.assertNotIn("b", out)
        self.assertEqual(out["a"]["constraint_checks"]["max_total_trip_usd"], "pass")
        self.assertEqual(out["c"]["constraint_checks"]["max_total_trip_usd"], "unverified_no_quote")


class ConnectionKwargsTests(unittest.TestCase):
    def test_pooler_disables_prepared_statements(self):
        kwargs = m._connection_kwargs("postgresql://u:p@aws-0-eu.pooler.supabase.com:5432/postgres")
        self.assertIsNone(kwargs["prepare_threshold"])
        self.assertNotIn("prepare_threshold", m._connection_kwargs("postgresql:///travel"))


class ToolRegistrationTests(unittest.TestCase):
    def test_new_tools_have_expected_parameters(self):
        import inspect
        fn = lambda tool: getattr(tool, "fn", tool)  # noqa: E731
        self.assertIn("query", inspect.signature(fn(m.search_places)).parameters)
        self.assertIn("place", inspect.signature(fn(m.place_profile)).parameters)
        self.assertIn("places", inspect.signature(fn(m.compare_places)).parameters)


@unittest.skipUnless(os.environ.get("TRAVEL_TEST_DB_URL"), "TRAVEL_TEST_DB_URL not set")
class LiveDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row
        cls.conn = psycopg.connect(os.environ["TRAVEL_TEST_DB_URL"], row_factory=dict_row)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_alternate_and_translated_names_resolve(self):
        with self.conn.cursor() as cur:
            for query, expected in (("Cologne", "DE"), ("Lisboa", "PT"), ("Porto", "PT"),
                                    ("Kiev", "UA"), ("Sao Paulo", "BR")):
                result = pp.resolve_place(cur, query)
                self.assertEqual(result["resolved"]["country_iso2"], expected, query)

    def test_ambiguous_name_reports_alternatives(self):
        with self.conn.cursor() as cur:
            result = pp.resolve_place(cur, "Portland")
        self.assertTrue(result["ambiguous"])
        self.assertTrue(result["other_matches"])

    def test_profile_and_comparison_shapes(self):
        with self.conn.cursor() as cur:
            lisbon = pp.resolve_place(cur, "Lisbon")["destination_id"]
            porto = pp.resolve_place(cur, "Porto, PT")["destination_id"]
            profile = pp.build_profile(cur, lisbon)
            comparison = pp.build_comparison(cur, [lisbon, porto])
        self.assertEqual(profile["place"]["country_iso2"], "PT")
        self.assertEqual(len(profile["climate"]["months"]), 12)
        self.assertEqual(len(comparison["places"]), 2)
        for metric in comparison["metrics"]:
            for value in metric["values"].values():
                pct = value["percentile_global"]
                self.assertTrue(pct is None or 0 <= pct <= 100)


if __name__ == "__main__":
    unittest.main()
