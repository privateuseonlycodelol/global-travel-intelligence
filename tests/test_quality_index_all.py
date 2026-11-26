"""Offline unit tests for Council Phase 1 Intrinsic City Quality Index."""

from __future__ import annotations

import unittest

import bootstrap_quality_index_all as bq


def sample_row(**overrides):
    base = {
        "destination_id": "gn-1",
        "name": "Testville",
        "country_iso2": "ES",
        "urban_centre_id": 1001,
        "within_15km_centre": False,
        "centre_population": 500_000,
        "gdp_per_capita_ppp": 45_000.0,
        "pm25_ugm3": 8.0,
        "coast_distance_km": 2.0,
        "large_lake_distance_km": 50.0,
        "major_river_distance_km": 20.0,
        "ghsl_heat_stress_days": 15.0,
        "utci_thermal_safety_score": 88.0,
        "wc_tree_fraction": 0.25,
        "wc_shrub_fraction": 0.05,
        "wc_water_fraction": 0.05,
        "ghsl_ndvi_score": 45.0,
        "ghsl_canopy_m": 6.0,
        "ghsl_hazard_events": 1,
        "unesco_heritage_score": 70.0,
        "homicide_per_100k": 0.8,
        "safely_managed_water_pct": 99.0,
        "safely_managed_sanitation_pct": 97.0,
        "median_down_mbps": 120.0,
        "median_up_mbps": 40.0,
        "latency_ms": 15.0,
        "broadband_total_tests": 120,
        "broadband_tile_count": 10,
        "walkability_score": 90.0,
        "transit_stops_5km": 200,
        "museum_culture_count": 30,
        "restaurant_count": 500,
        "cuisine_entropy": 2.5,
        "park_count_5km": 50,
        "price_level_vs_usd": 0.85,
        "out_of_pocket_health_pct": 20.0,
        "airport_nearest_km": 12.0,
    }
    base.update(overrides)
    return base


class AnchorFormulaTests(unittest.TestCase):
    def test_reference_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(bq.REFERENCE_WEIGHTS.values()), 1.0, places=6)

    def test_air_score_bounds_and_log_scale(self):
        self.assertEqual(bq.compute_air_score(4.0), 100.0)
        self.assertEqual(bq.compute_air_score(5.0), 100.0)
        self.assertEqual(bq.compute_air_score(75.0), 0.0)
        self.assertEqual(bq.compute_air_score(120.0), 0.0)
        mid = bq.compute_air_score(15.0)
        self.assertGreater(mid, 50.0)
        self.assertLess(mid, 70.0)

    def test_waterfront_1_to_15km_decay(self):
        s0, d0 = bq.compute_waterfront_score(0.5, 40.0, 30.0)
        self.assertEqual(s0, 100.0)
        self.assertEqual(d0, 0.5)
        s15, d15 = bq.compute_waterfront_score(15.0, 40.0, None)
        self.assertEqual(s15, 0.0)
        self.assertEqual(d15, 15.0)
        s8, _ = bq.compute_waterfront_score(8.0, None, None)
        self.assertAlmostEqual(s8, 50.0)

    def test_thermal_heat_penalizes_cienfuegos(self):
        self.assertEqual(bq.compute_thermal_heat_score(0.0), 100.0)
        self.assertAlmostEqual(bq.compute_thermal_heat_score(60.0), 50.0)
        self.assertEqual(bq.compute_thermal_heat_score(120.0), 0.0)
        self.assertEqual(bq.compute_thermal_heat_score(295.0), 0.0)

    def test_worldcover_green_with_canopy_multiplier(self):
        # 40% tree+shrub on 100% land, 8m canopy -> 100 * 1.0 = 100
        self.assertAlmostEqual(
            bq.compute_worldcover_green_score(0.30, 0.10, 0.0, 8.0), 100.0
        )
        # 0m canopy -> 0.85 multiplier
        self.assertAlmostEqual(
            bq.compute_worldcover_green_score(0.40, 0.0, 0.0, 0.0), 85.0
        )
        # Water >= 80% -> None
        self.assertIsNone(bq.compute_worldcover_green_score(0.1, 0.0, 0.85, 5.0))

    def test_homicide_piecewise_anchors(self):
        self.assertEqual(bq.compute_homicide_score(0.5), 100.0)
        self.assertEqual(bq.compute_homicide_score(3.0), 80.0)
        self.assertEqual(bq.compute_homicide_score(10.0), 50.0)
        self.assertEqual(bq.compute_homicide_score(25.0), 20.0)
        self.assertEqual(bq.compute_homicide_score(60.0), 0.0)

    def test_wash_requires_both_safely_managed_series(self):
        self.assertAlmostEqual(bq.compute_wash_score(90.0, 80.0), 85.0)
        self.assertIsNone(bq.compute_wash_score(90.0, None))
        self.assertIsNone(bq.compute_wash_score(None, 80.0))

    def test_broadband_absolute_thresholds_and_gates(self):
        # Perfect scores
        self.assertAlmostEqual(
            bq.compute_broadband_score(200.0, 60.0, 8.0, 50, 5), 100.0
        )
        # Below min tests or tiles -> None
        self.assertIsNone(bq.compute_broadband_score(200.0, 60.0, 8.0, 9, 5))
        self.assertIsNone(bq.compute_broadband_score(200.0, 60.0, 8.0, 100, 1))
        # 10-49 tests with >=2 tiles is scored
        self.assertIsNotNone(bq.compute_broadband_score(80.0, 25.0, 30.0, 20, 3))


class CouncilArchitectureTests(unittest.TestCase):
    def test_cost_airports_and_experimental_do_not_change_quality_score(self):
        a = sample_row(
            destination_id="a",
            price_level_vs_usd=0.20,
            out_of_pocket_health_pct=5.0,
            airport_nearest_km=1.0,
            walkability_score=99.0,
            restaurant_count=5000,
            transit_stops_5km=1000,
        )
        b = sample_row(
            destination_id="b",
            price_level_vs_usd=1.80,
            out_of_pocket_health_pct=75.0,
            airport_nearest_km=400.0,
            walkability_score=None,
            museum_culture_count=0,
            restaurant_count=0,
            transit_stops_5km=0,
        )
        rows = bq.score_rows([a, b])
        self.assertAlmostEqual(rows[0]["quality_score"], rows[1]["quality_score"])
        self.assertEqual(rows[0]["construct_coverage"], rows[1]["construct_coverage"])
        self.assertEqual(rows[0]["tier"], "A")
        self.assertEqual(rows[1]["poi_observation_status"], "unobserved")
        self.assertEqual(rows[1]["transit_observation_status"], "unobserved")

    def test_no_silent_renormalization_on_missing_pillar(self):
        missing_wash = sample_row(safely_managed_water_pct=None)
        row = bq.score_rows([missing_wash])[0]
        self.assertIsNone(row["wash_prior_score"])
        self.assertIsNone(row["institutional_board_score"])
        self.assertIsNone(row["quality_score"])
        self.assertEqual(row["tier"], "C")
        self.assertAlmostEqual(row["construct_coverage"], 0.68, places=4)

    def test_broadband_10_to_49_tests_capped_at_tier_b(self):
        row = bq.score_rows([sample_row(broadband_total_tests=25, broadband_tile_count=3)])[0]
        self.assertIsNotNone(row["quality_score"])
        self.assertEqual(row["tier"], "B")
        self.assertIn("ookla_tier_b_sample", row["uncertainty_flags"])

    def test_satellite_suppression_and_unmatched_towns_excluded_from_centre_rank(self):
        sat = sample_row(urban_centre_id=None, within_15km_centre=True)
        town = sample_row(urban_centre_id=None, within_15km_centre=False)
        r_sat, r_town = bq.score_rows([sat, town])
        self.assertEqual(r_sat["settlement_class"], "satellite_suppressed")
        self.assertIsNone(r_sat["quality_score"])
        self.assertEqual(r_sat["tier"], "C")
        self.assertEqual(r_town["settlement_class"], "unmatched_town")
        self.assertIsNone(r_town["quality_score"])
        self.assertEqual(r_town["tier"], "C")

    def test_true_construct_coverage_reflects_named_holes(self):
        row = bq.score_rows([sample_row()])[0]
        self.assertAlmostEqual(row["construct_coverage"], 0.74, places=4)
        self.assertAlmostEqual(row["data_completeness"], 1.0, places=4)
        self.assertIsNotNone(row["thermal_safety_score"])
        self.assertIsNotNone(row["heritage_score"])
        self.assertIsNotNone(row["worldcover_green_score"])
        self.assertIsNone(row["urban_form_score"])
        self.assertIsNone(row["care_access_score"])
        self.assertIsNone(row["hazard_score"])
        self.assertIsNone(row["transit_score"])


if __name__ == "__main__":
    unittest.main()
