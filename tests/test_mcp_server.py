"""Offline unit tests for the Phase 4 MCP server helpers.

No database or network is touched: importing ``mcp_server`` only defines the
pool lazily and registers the FastMCP tools.

Run with: python -m unittest tests.test_mcp_server -v
"""

import inspect
import sys
import unittest
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server as m  # noqa: E402


class RankSqlContractTests(unittest.TestCase):
    def test_data_sources_documentation_is_declared(self):
        self.assertEqual(m.DATA_SOURCES_DOC, "docs/DATA_SOURCES.md")
        self.assertIn("safety_crime", m.UNAVAILABLE_DIMENSIONS)

    def test_rank_sql_has_nineteen_placeholders(self):
        self.assertEqual(m.RANK_SQL.count("%s"), 19)

    def test_rank_sql_calls_v3_with_all_filters(self):
        for token in (
            "rank_destinations_v3",
            "excluded_for_no_evidence",
            "%s::INT[]",
            "%s::TEXT[]",
            "%s::BOOLEAN",
        ):
            self.assertIn(token, m.RANK_SQL)

    def test_tool_signatures_expose_stage2_arguments(self):
        rank = inspect.signature(m.rank_destinations)
        for name in (
            "origin_iata",
            "start_date",
            "end_date",
            "live_candidate_pool",
            "w_trip_cost",
            "max_total_trip_usd",
            "dedupe_metro",
        ):
            self.assertIn(name, rank.parameters)

        refresh = inspect.signature(m.refresh_live_quotes)
        for name in ("destination_ids", "origin_iata", "start_date", "end_date"):
            self.assertIn(name, refresh.parameters)

    def test_cache_ddl_has_split_primary_keys(self):
        self.assertIn(
            "PRIMARY KEY (origin_iata, destination_iata, start_date, end_date)",
            m.FLIGHT_CACHE_DDL,
        )
        self.assertIn(
            "PRIMARY KEY (destination_id, start_date, end_date)",
            m.HOTEL_CACHE_DDL,
        )


class MonthExpansionTests(unittest.TestCase):
    def test_one_month(self):
        self.assertEqual(m._expand_months(6, 4), [6])

    def test_wraps_across_new_year(self):
        self.assertEqual(m._expand_months(11, 60), [11, 12])
        self.assertEqual(m._expand_months(11, 61), [11, 12, 1])

    def test_caps_at_twelve_months(self):
        self.assertEqual(len(m._expand_months(1, 500)), 12)

    def test_rejects_invalid_month(self):
        with self.assertRaises(ValueError):
            m._expand_months(13, 10)


class NormalizeWeightsTests(unittest.TestCase):
    def test_normalizes(self):
        self.assertEqual(m._normalize_weights([1, 1, 2]), [0.25, 0.25, 0.5])

    def test_clamps_negative(self):
        self.assertEqual(m._normalize_weights([-1, 1, 1]), [0.0, 0.5, 0.5])

    def test_zero_total_falls_back_to_equal(self):
        self.assertEqual(m._normalize_weights([0, 0, 0, 0]), [0.25] * 4)


class JsonableTests(unittest.TestCase):
    def test_converts_decimal_date_datetime_time_and_uuid(self):
        value = {
            "price": Decimal("12.50"),
            "day": date(2027, 6, 10),
            "moment": datetime(2027, 6, 10, 12, 30),
            "clock": time(12, 30),
            "id": UUID("12345678-1234-5678-1234-567812345678"),
            "nested": [Decimal("1.5"), {"x": date(2027, 1, 1)}],
        }
        out = m._jsonable(value)
        self.assertEqual(out["price"], 12.5)
        self.assertEqual(out["day"], "2027-06-10")
        self.assertEqual(out["moment"], "2027-06-10T12:30:00")
        self.assertEqual(out["clock"], "12:30:00")
        self.assertEqual(out["id"], "12345678-1234-5678-1234-567812345678")
        self.assertEqual(out["nested"][0], 1.5)
        self.assertEqual(out["nested"][1]["x"], "2027-01-01")

    def test_passes_through_plain_values(self):
        self.assertEqual(m._jsonable({"a": 1, "b": None}), {"a": 1, "b": None})


class DateCoercionTests(unittest.TestCase):
    def test_iso_string(self):
        self.assertEqual(m._coerce_date("2027-06-10"), date(2027, 6, 10))

    def test_date_and_datetime(self):
        self.assertEqual(m._coerce_date(date(2027, 6, 10)), date(2027, 6, 10))
        self.assertEqual(
            m._coerce_date(datetime(2027, 6, 10, 23, 59)), date(2027, 6, 10)
        )


class InvertRankTests(unittest.TestCase):
    def test_lower_cost_scores_higher(self):
        scores = m._invert_rank_scores({"a": 100.0, "b": 200.0, "c": 300.0})
        self.assertGreater(scores["a"], scores["b"])
        self.assertGreater(scores["b"], scores["c"])
        self.assertEqual(scores["a"], 1.0)
        self.assertEqual(scores["c"], 0.0)

    def test_ties_average_their_positions(self):
        scores = m._invert_rank_scores({"a": 100.0, "b": 100.0, "c": 300.0})
        self.assertEqual(scores["a"], scores["b"])
        self.assertGreater(scores["a"], scores["c"])

    def test_single_value_is_one(self):
        self.assertEqual(m._invert_rank_scores({"a": 10.0}), {"a": 1.0})

    def test_empty(self):
        self.assertEqual(m._invert_rank_scores({}), {})


class EmptyQuoteTests(unittest.TestCase):
    def test_shape_matches_cached_quote(self):
        quote = m._empty_quote(
            {"destination_id": "gn-1", "iata_codes": ["LUX"]},
            "CPH",
            date(2027, 6, 10),
            date(2027, 6, 14),
        )
        self.assertEqual(quote["destination_id"], "gn-1")
        self.assertEqual(quote["dest_iata"], "LUX")
        self.assertEqual(quote["nights"], 4)
        self.assertIsNone(quote["estimated_total_trip_usd"])
        self.assertFalse(quote["flight_cache_hit"])
        self.assertFalse(quote["hotel_cache_hit"])


if __name__ == "__main__":
    unittest.main()
