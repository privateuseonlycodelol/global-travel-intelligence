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
        self.assertEqual(pp.split_country_suffix("Porto, Portugal"), ("Porto, Portugal", None))
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

    def test_price_level_uses_published_index_scaled_to_ratio(self):
        indicator, _, _, unit, better, scale = bp.WORLD_BANK_INDICATORS["price_level_vs_usd"]
        self.assertEqual(indicator, "PA.NUS.GDP.PLI")
        self.assertEqual((unit, better, scale), ("ratio to US", "lower", 0.01))
        self.assertFalse(hasattr(bp, "price_level_values"))


class Admin1Tests(unittest.TestCase):
    def test_parse_and_build(self):
        names = bp.parse_admin1("US.TX\tTexas\tTexas\t4736286\nCA.08\tOntario\tOntario\t6093943\n")
        self.assertEqual(names, {"US.TX": "Texas", "CA.08": "Ontario"})
        line = "\t".join(["4671654", "Austin", "Austin", "", "30.2", "-97.7", "P", "PPLA",
                           "US", "", "TX", "453", "", "", "961855"]) + "\n"
        rows = bp.build_admin1_rows({4671654: "gn-4671654"}, [line], names)
        self.assertEqual(rows, [("gn-4671654", "TX", "Texas", "texas")])


class QualifierTests(unittest.TestCase):
    def test_choice_reason(self):
        exact = {"match": "exact", "weight": 1000}
        self.assertIsNone(pp.choice_reason(exact, [], None))
        self.assertIsNotNone(pp.choice_reason(exact, [], {"text": "TX", "applied": False}))
        self.assertIsNotNone(pp.choice_reason({"match": "fuzzy"}, [], None))
        self.assertIsNotNone(pp.choice_reason({"match": "prefix"}, [], None))
        self.assertIsNotNone(pp.choice_reason({"match": "prefix"}, [{"match": "prefix"}], None))
        self.assertIsNotNone(pp.choice_reason(exact, [{"match": "exact", "weight": 600}], None))
        # A same-country namesake at 10% of the chosen city's size does ask.
        self.assertIsNotNone(pp.choice_reason(exact, [{"match": "exact", "weight": 100}], None))
        self.assertIsNone(pp.choice_reason(exact, [{"match": "exact", "weight": 10}], None))

    def test_split_qualifier(self):
        self.assertEqual(pp.split_qualifier("Porto, Portugal"), ("Porto", "Portugal"))
        self.assertEqual(pp.split_qualifier("Austin, TX"), ("Austin", "TX"))
        self.assertEqual(pp.split_qualifier("Washington, D.C."), ("Washington", "D.C."))
        self.assertEqual(pp.split_qualifier("Lisbon"), ("Lisbon", None))
        self.assertEqual(pp.split_qualifiers("Austin, Texas, USA"), ("Austin", ["Texas", "USA"]))
        self.assertEqual(pp.split_qualifiers("Lisbon"), ("Lisbon", []))
        self.assertEqual(pp.split_qualifier(", PT"), (", PT", None))


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

    def test_ties_use_mid_rank(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("cume_dist()", sql)
        self.assertIn("/ 2.0", sql)

    def test_broadband_needs_minimum_tests(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("b.total_tests >= 10 AND b.avg_latency_ms > 0", sql)
        self.assertIn("AS internet_latency_ms__sample", sql)

    def test_poi_zero_is_excluded_from_ranking(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("NULLIF((p.restaurant_count)::double precision, 0) AS restaurant_count", sql)
        self.assertIn("CASE WHEN p.restaurant_count > 0 THEN p.cuisine_entropy END", sql)

    def test_local_currency_has_no_global_percentile(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("NULL::double precision AS short_let_monthly_local__pct_global", sql)

    def test_walkability_centre_and_attribution_are_exposed(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("AS walkability_centre_score__pct_global", sql)
        self.assertIn("AS walkability_score__url", sql)


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
        self.assertEqual(entry["band_global"], "best 10%")
        self.assertEqual(entry["percentile_in_country"], 100.0)

    def test_not_measured_is_not_zero(self):
        entry = pp.metric_entry(self.metric, {})
        self.assertEqual(entry["status"], "not_measured")
        self.assertIsNone(entry["value"])
        self.assertNotIn("percentile_global", entry)

    def test_dataset_missing_everywhere_is_not_loaded(self):
        metric = next(x for x in pp.METRICS if x.key == "short_let_monthly_local")
        entry = pp.metric_entry(metric, {"short_let_monthly_local__n_global": 0,
                                         "destination_id": "gn-1"})
        self.assertEqual(entry["status"], "not_loaded")

    def test_single_city_country_has_no_country_percentile(self):
        entry = pp.metric_entry(self.metric, {
            "restaurant_count": 5, "restaurant_count__pct_country": 0.0,
            "restaurant_count__n_country": 1,
        })
        self.assertIsNone(entry["percentile_in_country"])

    def test_bands_carry_direction(self):
        self.assertEqual(pp.percentile_band(95, "higher"), "best 10%")
        self.assertEqual(pp.percentile_band(80, "lower"), "best 25%")
        self.assertEqual(pp.percentile_band(50, "higher"), "better than most")
        self.assertEqual(pp.percentile_band(30, "lower"), "worse than most")
        self.assertEqual(pp.percentile_band(10, "higher"), "worst 25%")
        self.assertEqual(pp.percentile_band(95, None), "largest 10%")
        self.assertEqual(pp.percentile_band(10, None), "smallest 25%")
        self.assertIsNone(pp.percentile_band(None, "higher"))

    def test_price_bands_use_price_words(self):
        self.assertEqual(pp.percentile_band(30, "lower", "price_level_vs_usd"), "pricier than most")
        self.assertEqual(pp.percentile_band(95, "lower", "price_level_vs_usd"), "cheapest 10%")
        self.assertEqual(pp.percentile_reading(29.6, "lower", 202, "countries", "price_level_vs_usd"),
                         "cheaper than 29% of the other 201 countries with data")
        self.assertEqual(pp.percentile_reading(100, "lower", 202, "countries", "price_level_vs_usd"),
                         "the cheapest of the 202 countries with data")

    def test_reading_counts_only_strictly_beaten_others(self):
        self.assertEqual(pp.percentile_reading(29.6, "lower", 202, "countries"),
                         "lower than 29% of the other 201 countries with data")
        self.assertEqual(pp.percentile_reading(98.5, None, 13101, "cities"),
                         "larger than 98% of the other 13,100 cities with data")
        self.assertEqual(pp.percentile_reading(100.0, "higher", 10, "cities"),
                         "the highest of the 10 cities with data")
        self.assertEqual(pp.percentile_reading(0.0, "higher", 4000, "cities"),
                         "the lowest (or tied for lowest) of the 4,000 cities with data")
        self.assertIsNone(pp.percentile_reading(50, "higher", 1, "cities"))
        # Near-top / near-bottom values are never rounded into superlatives.
        self.assertEqual(pp.percentile_reading(99.992, None, 13101, "cities"),
                         "larger than >99% of the other 13,100 cities with data")
        self.assertEqual(pp.percentile_reading(0.4, "lower", 202, "countries", "price_level_vs_usd"),
                         "cheaper than <1% of the other 201 countries with data")

    def test_walkability_entry_carries_cohort_and_url(self):
        metric = next(m for m in pp.METRICS if m.key == "walkability_score")
        entry = pp.metric_entry(metric, {
            "walkability_score": 90, "walkability_score__pct_global": 0.95,
            "walkability_score__n_global": 662, "walkability_score__source": "How Walkable",
            "walkability_score__period": "2026-10-22",
            "walkability_score__url": "https://howwalkable.com/cities/germany/berlin",
        })
        self.assertEqual(entry["cohort"], "scored cities (300k+, 95 countries)")
        self.assertEqual(entry["source_url"], "https://howwalkable.com/cities/germany/berlin")

    def test_zero_poi_count_is_none_listed_not_ranked(self):
        entry = pp.metric_entry(self.metric, {
            "restaurant_count": None, "restaurant_count__raw": 0.0,
            "restaurant_count__source": "Overture",
        })
        self.assertEqual(entry["status"], "none_listed")
        self.assertEqual(entry["value"], 0)
        self.assertNotIn("percentile_global", entry)


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

    def test_verified_matches_rank_ahead_of_unverified(self):
        rows = [{"destination_id": "a", "final_score": 0.5},
                {"destination_id": "b", "final_score": 0.5},
                {"destination_id": "c", "final_score": 0.5}]
        quotes = {"a": self._quote(400, 120), "b": self._quote(700, 120)}
        out = m._apply_stage2_rescore(rows, quotes, 0.25, 0.15, 800, None)
        self.assertEqual([r["destination_id"] for r in out], ["a", "b", "c"])
        self.assertFalse(out[-1]["constraints_verified"])

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
                                    ("Kiev", "UA"), ("Sao Paulo", "BR"),
                                    ("St Petersburg", "RU")):
                result = pp.resolve_place(cur, query)
                self.assertEqual(result["resolved"]["country_iso2"], expected, query)

    def test_qualifiers_resolve_country_and_region(self):
        with self.conn.cursor() as cur:
            for query, expected_name, expected_country in (
                ("Austin, TX", "Austin", "US"), ("San Diego, CA", "San Diego", "US"),
                ("Porto, Portugal", "Porto", "PT"), ("Valencia, Spain", "Valencia", "ES"),
                ("London, Ontario", "London", "CA"), ("London, UK", "London", "GB"),
                ("Portland, ME", "Portland", "US"), ("Washington, DC", "Washington", "US"),
                ("London, ON", "London", "CA"), ("Victoria, BC", "Victoria", "CA"),
                ("Sydney, NSW", "Sydney", "AU"), ("Perth, WA", "Perth", "AU"),
                ("Austin, Texas, USA", "Austin", "US"), ("Portland, Oregon, USA", "Portland", "US"),
                ("Belgrade, RS", "Belgrade", "RS"), ("Glasgow, Scotland", "Glasgow", "GB"),
            ):
                result = pp.resolve_place(cur, query)
                resolved = result["resolved"]
                self.assertEqual((resolved["name"], resolved["country_iso2"]),
                                 (expected_name, expected_country), query)
                self.assertFalse(result["needs_choice"], query)
            item_wins = pp.resolve_place(cur, "Madrid, ES", country_iso2="PT")
            self.assertEqual(item_wins["resolved"]["country_iso2"], "ES")
            salem = pp.resolve_place(cur, "Salem, OR", country_iso2="US")
            self.assertEqual(salem["resolved"]["country_iso2"], "US")
            uk = pp.resolve_place(cur, "London", country_iso2="UK")
            self.assertEqual(uk["resolved"]["country_iso2"], "GB")

    def test_unsafe_guesses_need_choice(self):
        with self.conn.cursor() as cur:
            for query in ("Lisbn", "Paris, TX", "Naples, FL", "Portland, UK", "Bath",
                          "Barca", "Springfield", "Richmond, CA", "Aspen", "Bali",
                          "Portland", "London, Scotland", "Belgrade, SE"):
                self.assertTrue(pp.resolve_place(cur, query)["needs_choice"], query)
            self.assertTrue(pp.resolve_place(cur, "Salem, IN", country_iso2="US")["needs_choice"])
            self.assertEqual(pp.resolve_place(cur, "Cancun")["resolved"]["country_iso2"], "MX")
            for query in ("Lisbon", "Paris", "London", "Porto", "Madrid", "Taipei"):
                self.assertFalse(pp.resolve_place(cur, query)["needs_choice"], query)

    def test_region_name_offers_cities_in_region(self):
        with self.conn.cursor() as cur:
            bali = pp.resolve_place(cur, "Bali")
        self.assertTrue(any(m.get("match") == "in_region" and m["country_iso2"] == "ID"
                            for m in bali["other_matches"]))

    def test_round_five_regressions(self):
        with self.conn.cursor() as cur:
            for query, country in (("Dili", None), ("Puno", None), ("Montana", None),
                                   ("Bolivar", None), ("San Diego", "CA"),
                                   ("Salem, IN", "US"), ("Lisbon", "XX")):
                self.assertTrue(pp.resolve_place(cur, query, country)["needs_choice"],
                                (query, country))
            for query, country, expected in (("London, UK", "UK", "GB"),
                                             ("Glasgow, Scotland", "UK", "GB"),
                                             ("Austin, TX", "USA", "US"),
                                             ("Richmond, CA", "CA", "CA"),
                                             ("Lisbon", "Portugal", "PT")):
                result = pp.resolve_place(cur, query, country)
                self.assertEqual(result["resolved"]["country_iso2"], expected, (query, country))
                self.assertFalse(result["needs_choice"], (query, country))
            profile = pp.build_profile(cur, pp.resolve_place(cur, "Lisbon")["destination_id"])
        latency = next(m for g in profile["metrics"] for m in g["metrics"]
                       if m["key"] == "internet_latency_ms")
        self.assertGreaterEqual(latency["sample_size"], pp.MIN_BROADBAND_TESTS)

    def test_country_and_region_names_ask(self):
        with self.conn.cursor() as cur:
            for query in ("usa", "Egypt", "Grenada", "Laos", "Missouri", "Henan",
                          "Goa", "Goa, India"):
                self.assertTrue(pp.resolve_place(cur, query)["needs_choice"], query)
            for query in ("Singapore", "Luxembourg", "Djibouti", "New York",
                          "Washington", "Mexico City"):
                self.assertFalse(pp.resolve_place(cur, query)["needs_choice"], query)
            profile = pp.build_profile(cur, pp.resolve_place(cur, "Bangkok")["destination_id"])
        self.assertIn("indicators_missing", profile["country"])

    def test_airport_codes_do_not_beat_city_names(self):
        with self.conn.cursor() as cur:
            self.assertEqual(pp.resolve_place(cur, "San")["resolved"]["country_iso2"], "ML")
            self.assertEqual(pp.resolve_place(cur, "LIS")["resolved"]["name"], "Lisbon")

    def test_candidates_carry_region(self):
        with self.conn.cursor() as cur:
            regions = {m["region"] for m in pp.search_places(cur, "Portland")["matches"]}
        self.assertTrue({"Oregon", "Maine"} <= regions)

    def test_price_levels_are_plausible(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT country_iso2, value FROM country_indicators "
                        "WHERE indicator = 'price_level_vs_usd'")
            values = {r["country_iso2"]: r["value"] for r in cur.fetchall()}
        self.assertAlmostEqual(values["US"], 1.0, places=3)
        for code in ("IR", "LR", "BG"):
            self.assertTrue(0.1 < values[code] < 0.8, (code, values[code]))

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
            statuses = [v["status"] for v in metric["values"].values()]
            if metric["best"] is not None:
                self.assertTrue(all(s == "measured" for s in statuses), metric["key"])
            for value in metric["values"].values():
                pct = value["percentile_global"]
                self.assertTrue(pct is None or 0 <= pct <= 100)


if __name__ == "__main__":
    unittest.main()
