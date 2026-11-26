"""Offline unit tests for the gated public transport loader."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_gated_transit as gt  # noqa: E402


class _FakeCursor:
    def __init__(self, taiwan_exists: bool = True):
        self.taiwan_exists = taiwan_exists
        self.executed: list[tuple[str, object]] = []
        self.upsert_sql: str | None = None
        self.upsert_rows: list[tuple] | None = None
        self._fetchone = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "FROM country_facts" in sql:
            self._fetchone = (1,) if self.taiwan_exists else None

    def fetchone(self):
        return self._fetchone

    def executemany(self, sql, rows):
        self.upsert_sql = sql
        self.upsert_rows = list(rows)


class _FakeConn:
    def __init__(self, taiwan_exists: bool = True):
        self.cur = _FakeCursor(taiwan_exists)
        self.committed = False

    def cursor(self):
        return self.cur

    def commit(self):
        self.committed = True


def gtfs_row(**overrides):
    base = {
        "destination_id": "gn-1",
        "feed_id": "local-1",
        "radius_rule": "urban_centre",
        "median_headway_min": 10.0,
        "stops": 120,
        "stops_mapped": 100,
        "service_date": "2026-11-25",
        "provider": "Trentino Trasporti",
        "feed_name": "Urban GTFS",
    }
    base.update(overrides)
    return base


class CoachExclusionTests(unittest.TestCase):
    def test_named_intercity_coach_agencies_are_excluded(self):
        for provider in (
            "Flixbus France",
            "FlixBus",
            "BlaBlaCar Bus",
            "BlaBlaBus",
            "Greyhound Lines",
            "Eurolines",
            "Megabus",
            "National Express",
            "Ouibus",
            "RegioJet",
            "Sindbad",
            "ALSA",
            "Bolt Bus",
            "FlixTrain",
            "Student Agency",
        ):
            with self.subTest(provider=provider):
                self.assertTrue(gt.is_intercity_coach(provider))

    def test_feed_name_is_also_checked(self):
        self.assertTrue(
            gt.is_intercity_coach(
                "Neutral Transport Co",
                "Réseau européen FlixBus et FlixTrain",
            )
        )

    def test_alsace_is_not_alsa(self):
        # Regression: a naive "alsa" substring match would exclude Mulhouse's
        # legitimate local network.
        self.assertFalse(
            gt.is_intercity_coach(
                "Mulhouse Alsace Agglomération", "Réseau urbain Soléa"
            )
        )

    def test_local_agencies_are_allowed(self):
        for provider in (
            "Trentino Trasporti",
            "Île-de-France Mobilités",
            "Azienda Trasporti Milanesi (ATM)",
            "Autolinee Toscane - Urbano Pisa",
        ):
            with self.subTest(provider=provider):
                self.assertFalse(gt.is_intercity_coach(provider))

    def test_missing_values_are_not_coaches(self):
        self.assertFalse(gt.is_intercity_coach(None, None))
        self.assertFalse(gt.is_intercity_coach("", ""))


class HeadwayScoreTests(unittest.TestCase):
    def test_exact_anchor_points(self):
        self.assertEqual(gt.headway_transit_score(3.0), 100.0)
        self.assertEqual(gt.headway_transit_score(5.0), 100.0)
        self.assertAlmostEqual(gt.headway_transit_score(10.0), 75.0)
        self.assertAlmostEqual(gt.headway_transit_score(15.0), 50.0)
        self.assertAlmostEqual(gt.headway_transit_score(30.0), 25.0)
        self.assertEqual(gt.headway_transit_score(45.0), 0.0)
        self.assertEqual(gt.headway_transit_score(60.0), 0.0)

    def test_missing_and_invalid_values_are_null(self):
        for value in (None, "bad", -1.0, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(gt.headway_transit_score(value))

    def test_curve_is_monotonic_non_increasing(self):
        previous = 100.0
        value = 0.0
        while value <= 60.0:
            score = gt.headway_transit_score(value)
            self.assertIsNotNone(score)
            self.assertLessEqual(score, previous + 1e-12)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)
            previous = score
            value += 0.5

    def test_slower_than_15_minutes_falls_to_zero(self):
        self.assertGreater(gt.headway_transit_score(15.1), 45.0)
        self.assertLess(gt.headway_transit_score(15.1), 50.0)
        self.assertLess(gt.headway_transit_score(44.9), 1.0)
        self.assertEqual(gt.headway_transit_score(45.0), 0.0)


class LocalHeadwaySelectionTests(unittest.TestCase):
    def test_requires_50_mapped_stops(self):
        self.assertIsNone(
            gt.select_local_headway([gtfs_row(stops_mapped=49)])
        )
        chosen = gt.select_local_headway([gtfs_row(stops_mapped=50)])
        self.assertEqual(chosen["local_gtfs_stops"], 50)

    def test_stops_falls_back_when_stops_mapped_is_missing(self):
        chosen = gt.select_local_headway(
            [gtfs_row(stops_mapped=None, stops=75)]
        )
        self.assertEqual(chosen["local_gtfs_stops"], 75)

    def test_coach_feed_is_excluded_even_when_best(self):
        rows = [
            gtfs_row(
                feed_id="coach",
                provider="Flixbus France",
                feed_name="Réseau européen FlixBus",
                median_headway_min=2.0,
                stops_mapped=500,
            ),
            gtfs_row(
                feed_id="local",
                provider="Trentino Trasporti",
                median_headway_min=12.0,
                stops_mapped=80,
            ),
        ]
        chosen = gt.select_local_headway(rows)
        self.assertEqual(chosen["feed_id"], "local")

    def test_best_covered_feed_wins_before_headway(self):
        rows = [
            gtfs_row(feed_id="small", median_headway_min=6.0, stops_mapped=60),
            gtfs_row(feed_id="large", median_headway_min=20.0, stops_mapped=200),
        ]
        chosen = gt.select_local_headway(rows)
        self.assertEqual(chosen["feed_id"], "large")

    def test_urban_centre_band_is_the_first_tiebreak(self):
        rows = [
            gtfs_row(
                feed_id="nearby",
                radius_rule="nearest_5km",
                median_headway_min=8.0,
                stops_mapped=100,
            ),
            gtfs_row(
                feed_id="centre",
                radius_rule="urban_centre",
                median_headway_min=8.0,
                stops_mapped=100,
            ),
        ]
        chosen = gt.select_local_headway(rows)
        self.assertEqual(chosen["feed_id"], "centre")

    def test_no_candidates_returns_none(self):
        self.assertIsNone(gt.select_local_headway([]))
        self.assertIsNone(
            gt.select_local_headway([gtfs_row(median_headway_min=None)])
        )


class GuidewayAnchorTests(unittest.TestCase):
    def test_missing_both_is_null(self):
        self.assertIsNone(gt.guideway_access_score(None, None))

    def test_observed_zeros_score_zero_not_null(self):
        self.assertEqual(gt.guideway_access_score(0, 0), 0.0)

    def test_caps_reach_100(self):
        self.assertEqual(gt.guideway_access_score(500, None), 100.0)
        self.assertEqual(gt.guideway_access_score(None, 150), 100.0)
        self.assertEqual(gt.guideway_access_score(500, 150), 100.0)

    def test_single_metric_renormalises_available_weight(self):
        self.assertAlmostEqual(
            gt.guideway_access_score(500, None),
            gt.guideway_access_score(500, 150),
        )

    def test_blend_uses_seventy_thirty_weights(self):
        # rail 500 -> 100, stops 0 -> 0 => 70% of 100.
        self.assertAlmostEqual(gt.guideway_access_score(500, 0), 70.0)
        self.assertAlmostEqual(gt.guideway_access_score(0, 150), 30.0)

    def test_bounds_and_monotonicity(self):
        previous = -1.0
        for rail in (0, 1, 10, 50, 100, 250, 500, 1000):
            score = gt.guideway_access_score(rail, 10)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)
            self.assertGreaterEqual(score, previous)
            previous = score

    def test_invalid_values_are_ignored(self):
        self.assertIsNone(gt.guideway_access_score(-1, None))
        self.assertIsNone(gt.guideway_access_score(None, float("nan")))


class RoadGateTests(unittest.TestCase):
    def test_country_medians_and_centre_mapping(self):
        rows = [
            {"urban_centre_id": 1, "country": "A", "road_km_per_built_km2": 10.0},
            {"urban_centre_id": 2, "country": "A", "road_km_per_built_km2": 12.0},
            {"urban_centre_id": 3, "country": "A", "road_km_per_built_km2": 14.0},
            {"urban_centre_id": 4, "country": "B", "road_km_per_built_km2": 1.0},
            {"urban_centre_id": 5, "country": "B", "road_km_per_built_km2": 2.0},
            {"urban_centre_id": 6, "country": "B", "road_km_per_built_km2": 3.0},
            {"urban_centre_id": 7, "country": "A", "road_km_per_built_km2": None},
        ]
        gate = gt.compute_country_road_gate(rows, threshold=5.0)
        self.assertTrue(gate[1])
        self.assertTrue(gate[2])
        self.assertTrue(gate[3])
        self.assertTrue(gate[7])
        self.assertFalse(gate[4])
        self.assertFalse(gate[5])
        self.assertFalse(gate[6])

    def test_median_must_reach_threshold(self):
        rows = [
            {"urban_centre_id": 1, "country": "A", "road_km_per_built_km2": 9.0},
            {"urban_centre_id": 2, "country": "A", "road_km_per_built_km2": 10.0},
            {"urban_centre_id": 3, "country": "A", "road_km_per_built_km2": 11.0},
        ]
        self.assertTrue(gt.compute_country_road_gate(rows, threshold=10.0)[1])
        self.assertFalse(gt.compute_country_road_gate(rows, threshold=10.1)[1])

    def test_empty_rows_return_empty_gate(self):
        self.assertEqual(gt.compute_country_road_gate([]), {})

    def test_missing_table_falls_back_to_observed_rail(self):
        self.assertTrue(gt.road_gate_status(1, 0, {}))
        self.assertFalse(gt.road_gate_status(1, None, {}))

    def test_loaded_table_uses_country_result(self):
        gate = {1: True, 2: False}
        self.assertTrue(gt.road_gate_status(1, 100, gate))
        self.assertFalse(gt.road_gate_status(2, 100, gate))
        self.assertFalse(gt.road_gate_status(99, 100, gate))
        self.assertFalse(gt.road_gate_status(None, 100, gate))


class Tier2Tests(unittest.TestCase):
    def test_centre_below_300k_is_unobserved(self):
        score, method = gt.tier2_transit_score(
            299_999, 500, 200, country_road_gate_ok=True
        )
        self.assertIsNone(score)
        self.assertEqual(method, gt.TRANSIT_METHOD_SMALL_CENTRE)

    def test_missing_population_is_unobserved(self):
        score, method = gt.tier2_transit_score(
            None, 500, 200, country_road_gate_ok=True
        )
        self.assertIsNone(score)
        self.assertEqual(method, gt.TRANSIT_METHOD_SMALL_CENTRE)

    def test_failed_road_gate_never_imputes(self):
        score, method = gt.tier2_transit_score(
            300_000, 500, 200, country_road_gate_ok=False
        )
        self.assertIsNone(score)
        self.assertEqual(method, gt.TRANSIT_METHOD_GATE_FAILED)

    def test_missing_observation_never_imputes(self):
        score, method = gt.tier2_transit_score(
            300_000, None, None, country_road_gate_ok=True
        )
        self.assertIsNone(score)
        self.assertEqual(method, gt.TRANSIT_METHOD_GATE_FAILED)

    def test_observed_zero_scores_zero(self):
        score, method = gt.tier2_transit_score(
            300_000, 0, 0, country_road_gate_ok=True
        )
        self.assertEqual(score, 0.0)
        self.assertEqual(method, gt.TRANSIT_METHOD_GUIDEWAY)

    def test_gate_pass_scores_on_fixed_anchor(self):
        score, method = gt.tier2_transit_score(
            300_000, 500, 150, country_road_gate_ok=True
        )
        self.assertEqual(score, 100.0)
        self.assertEqual(method, gt.TRANSIT_METHOD_GUIDEWAY)


class RowBuilderTests(unittest.TestCase):
    def test_tier1_scores_even_a_small_centre(self):
        row = gt.build_transit_row(
            "gn-1",
            {"urban_centre_id": 1, "centre_population": 100_000},
            gt.select_local_headway([gtfs_row(median_headway_min=10.0)]),
            {"rail_segments_5km": None, "transit_stops_5km": 40},
            road_gate_map={1: False},
        )
        self.assertEqual(row["transit_score"], 75.0)
        self.assertEqual(row["transit_method"], gt.TRANSIT_METHOD_GTFS)
        self.assertEqual(row["local_gtfs_headway_min"], 10.0)
        self.assertEqual(row["local_gtfs_stops"], 100)
        self.assertFalse(row["country_road_completeness_ok"])
        self.assertIn("2026-11-25", row["observed_period"])

    def test_unmatched_city_with_gtfs_is_scored(self):
        row = gt.build_transit_row(
            "gn-1",
            {"urban_centre_id": None, "centre_population": None},
            gt.select_local_headway([gtfs_row()]),
            None,
            road_gate_map={},
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["transit_method"], gt.TRANSIT_METHOD_GTFS)
        self.assertIsNone(row["country_road_completeness_ok"])

    def test_tier2_gate_failure_is_explicit_null(self):
        row = gt.build_transit_row(
            "gn-1",
            {"urban_centre_id": 1, "centre_population": 500_000},
            None,
            {"rail_segments_5km": None, "transit_stops_5km": None},
            road_gate_map={1: True},
        )
        self.assertIsNone(row["transit_score"])
        self.assertEqual(row["transit_method"], gt.TRANSIT_METHOD_GATE_FAILED)
        self.assertEqual(row["source"], gt.GATE_FAILED_SOURCE)
        self.assertEqual(row["observed_period"], "not observed")

    def test_tier2_success_uses_mobility_provenance(self):
        row = gt.build_transit_row(
            "gn-1",
            {"urban_centre_id": 1, "centre_population": 500_000},
            None,
            {
                "rail_segments_5km": 500,
                "transit_stops_5km": 150,
                "observed_period": "overture-2026-11-18.0",
            },
            road_gate_map={1: True},
        )
        self.assertEqual(row["transit_score"], 100.0)
        self.assertEqual(row["transit_method"], gt.TRANSIT_METHOD_GUIDEWAY)
        self.assertEqual(row["observed_period"], "overture-2026-11-18.0")
        self.assertTrue(row["country_road_completeness_ok"])

    def test_non_centre_without_gtfs_is_skipped(self):
        self.assertIsNone(
            gt.build_transit_row(
                "gn-1",
                {"urban_centre_id": None, "centre_population": None},
                None,
                {"rail_segments_5km": 500, "transit_stops_5km": 150},
                road_gate_map={},
            )
        )

    def test_build_rows_selects_tier_per_destination(self):
        centres = {
            "big": {"urban_centre_id": 1, "centre_population": 500_000},
            "small": {"urban_centre_id": 2, "centre_population": 100_000},
            "town": {"urban_centre_id": None, "centre_population": None},
        }
        gtfs_rows = [
            gtfs_row(
                destination_id="big",
                provider="Flixbus France",
                stops_mapped=500,
                median_headway_min=2.0,
            ),
            gtfs_row(
                destination_id="small",
                provider="Local Bus",
                stops_mapped=80,
                median_headway_min=10.0,
            ),
        ]
        mobility = {
            "big": {
                "rail_segments_5km": 250,
                "transit_stops_5km": 50,
                "observed_period": "2026",
            }
        }
        rows = gt.build_transit_rows(
            centres, gtfs_rows, mobility, road_gate_map={1: True, 2: False}
        )
        by_id = {row["destination_id"]: row for row in rows}
        self.assertEqual(set(by_id), {"big", "small"})
        self.assertEqual(
            by_id["big"]["transit_method"], gt.TRANSIT_METHOD_GUIDEWAY
        )
        self.assertIsNotNone(by_id["big"]["transit_score"])
        self.assertEqual(
            by_id["small"]["transit_method"], gt.TRANSIT_METHOD_GTFS
        )
        self.assertEqual(by_id["small"]["transit_score"], 75.0)


class SchemaTests(unittest.TestCase):
    def test_schema_has_every_required_column(self):
        for column in (
            "destination_id",
            "urban_centre_id",
            "local_gtfs_headway_min",
            "local_gtfs_stops",
            "rail_segments_5km",
            "transit_stops_5km",
            "country_road_completeness_ok",
            "transit_score",
            "transit_method",
            "source",
            "observed_period",
            "updated_at",
        ):
            self.assertIn(column, gt.SCHEMA_SQL)

    def test_primary_key_and_index(self):
        self.assertIn("PRIMARY KEY", gt.SCHEMA_SQL)
        self.assertIn("idx_destination_gated_transit_uc", gt.SCHEMA_SQL)

    def test_insert_has_one_placeholder_per_parameter(self):
        self.assertEqual(gt.INSERT_SQL.count("%s"), 11)

    def test_taiwan_upsert_is_conflict_update(self):
        self.assertIn("ON CONFLICT (country_iso2, indicator)", gt.TAIWAN_UPSERT_SQL)
        self.assertIn("DO UPDATE", gt.TAIWAN_UPSERT_SQL)


class TaiwanIndicatorTests(unittest.TestCase):
    EXPECTED = {
        "homicide_per_100k": (0.54, "lower"),
        "safely_managed_water_pct": (95.3, "higher"),
        "safely_managed_sanitation_pct": (72.1, "higher"),
        "gdp_per_capita_ppp": (73344.0, "higher"),
    }

    def test_four_unique_indicators(self):
        self.assertEqual(len(gt.TAIWAN_INDICATORS), 4)
        self.assertEqual(
            len({item.indicator for item in gt.TAIWAN_INDICATORS}), 4
        )

    def test_values_better_and_year(self):
        by_indicator = {item.indicator: item for item in gt.TAIWAN_INDICATORS}
        for indicator, (value, better) in self.EXPECTED.items():
            with self.subTest(indicator=indicator):
                item = by_indicator[indicator]
                self.assertEqual(item.value, value)
                self.assertEqual(item.better, better)
                self.assertEqual(item.year, 2023)
                self.assertIn("Taiwan", item.source)
                self.assertTrue(item.label)
                self.assertTrue(item.unit)
                self.assertTrue(item.source_indicator)

    def test_rows_use_tw_and_sql_order(self):
        rows = gt.taiwan_indicator_rows()
        self.assertEqual(len(rows), 4)
        for row in rows:
            self.assertEqual(row[0], "TW")
            self.assertEqual(len(row), 9)
        self.assertEqual(
            {row[1] for row in rows}, set(self.EXPECTED)
        )

    def test_loader_upserts_when_taiwan_exists(self):
        conn = _FakeConn(taiwan_exists=True)
        count = gt.load_taiwan_indicators(conn)
        self.assertEqual(count, 4)
        self.assertTrue(conn.committed)
        self.assertIsNotNone(conn.cur.upsert_sql)
        self.assertEqual(len(conn.cur.upsert_rows), 4)
        self.assertEqual(conn.cur.executed[0][1], ("TW",))

    def test_loader_skips_when_taiwan_is_absent(self):
        conn = _FakeConn(taiwan_exists=False)
        count = gt.load_taiwan_indicators(conn)
        self.assertEqual(count, 0)
        self.assertFalse(conn.committed)
        self.assertIsNone(conn.cur.upsert_rows)


if __name__ == "__main__":
    unittest.main()
