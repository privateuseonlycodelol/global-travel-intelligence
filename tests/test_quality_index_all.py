"""Offline unit tests for the global City Quality Index."""

import math
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_quality_index_all as bq  # noqa: E402


class FakeCursor:
    """Minimal offline cursor for the optional-table discovery queries."""

    def __init__(self, tables: dict[str, dict]):
        self.tables = tables
        self._one = None
        self._rows: list[dict] = []

    def execute(self, sql: str, params=None):
        text = " ".join(sql.split())
        if text.startswith("SELECT to_regclass"):
            table = params[0].split(".", 1)[1]
            self._one = {
                "name": object() if table in self.tables else None
            }
            self._rows = []
        elif "information_schema.columns" in text:
            table = params[0]
            self._one = None
            self._rows = [
                {"column_name": column}
                for column in self.tables.get(table, {}).get("columns", [])
            ]
        elif text.startswith("SELECT urban_centre_id"):
            match = re.search(r"FROM public\.(\w+)", text)
            self._one = None
            self._rows = list(
                self.tables.get(match.group(1), {}).get("rows", [])
            )
        else:
            raise AssertionError(f"unexpected SQL: {text}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


def months(
    tmax: float = 22.0, tmin: float = 12.0, precip: float = 100.0
) -> list[dict]:
    return [
        {
            "month": month,
            "median_temp_max_c": tmax,
            "median_temp_min_c": tmin,
            "precip_mm": precip,
        }
        for month in range(1, 13)
    ]


def core_row(**overrides) -> dict:
    row = {
        "destination_id": "dest-a",
        "name": "Alpha",
        "country_iso2": "AA",
        "centre_population": 1_000_000,
        "urban_centre_id": 1,
        "pm25_ugm3": 5.0,
        "coast_distance_km": 0.0,
        "large_lake_distance_km": None,
        "river_distance_km": None,
        "climate_months": months(),
        "recent_avg_high": 22.0,
        "recent_rainy_days": 120.0,
        "homicide_per_100k": 1.0,
        "road_deaths_per_100k": 2.0,
        "life_expectancy_years": 85.0,
        "physicians_per_1000": 5.0,
        "price_level_vs_usd": 50.0,
        "out_of_pocket_pct": 5.0,
        "median_down_mbps": 100.0,
        "median_up_mbps": 50.0,
        "latency_ms": 10.0,
        "broadband_total_tests": 500,
        "airport_nearest_distance_km": 5.0,
        "airport_large_100km": 2,
        "airport_medium_100km": 3,
        "museum_culture_count": 10,
        "restaurant_count": 100,
        "cuisine_entropy": 2.0,
        "park_count_5km": 10,
        "transit_stops_5km": 100,
        "walkability_score": 90.0,
        "physical_green_score": 80.0,
        "ghsl_ndvi_score": 80.0,
        "ghsl_heat_stress_days": 12.0,
        "ghsl_hazard_events": 3,
    }
    row.update(overrides)
    return row


class AirScoreTests(unittest.TestCase):
    def test_boundaries_and_clamping(self):
        self.assertEqual(bq.compute_air_score(None), None)
        self.assertEqual(bq.compute_air_score(2.0), 100.0)
        self.assertEqual(bq.compute_air_score(5.0), 100.0)
        self.assertEqual(bq.compute_air_score(75.0), 0.0)
        self.assertEqual(bq.compute_air_score(120.0), 0.0)

    def test_log_scale_midpoint(self):
        expected = 100.0 * (math.log(75.0) - math.log(15.0)) / (
            math.log(75.0) - math.log(5.0)
        )
        self.assertAlmostEqual(bq.compute_air_score(15.0), expected)
        self.assertGreater(bq.compute_air_score(10.0), bq.compute_air_score(30.0))

    def test_matches_green_score_formula(self):
        # Same fixed scale documented in bootstrap_green_score.py.
        self.assertAlmostEqual(bq.compute_air_score(5.0), 100.0)
        self.assertAlmostEqual(bq.compute_air_score(75.0), 0.0)


class WaterScoreTests(unittest.TestCase):
    def test_missing_and_zero(self):
        self.assertIsNone(bq.compute_water_score(None, None, None))
        self.assertEqual(bq.compute_water_score(0.0, None, None), 100.0)

    def test_linear_decay_to_fifty_km(self):
        self.assertEqual(bq.compute_water_score(25.0, None, None), 50.0)
        self.assertEqual(bq.compute_water_score(50.0, None, None), 0.0)
        self.assertEqual(bq.compute_water_score(100.0, None, None), 0.0)

    def test_nearest_feature_wins(self):
        self.assertAlmostEqual(
            bq.compute_water_score(10.0, 5.0, 20.0), 90.0
        )
        self.assertAlmostEqual(
            bq.compute_water_score(None, None, 12.5), 75.0
        )


class ClimateScoreTests(unittest.TestCase):
    def test_ideal_year_scores_one_hundred(self):
        self.assertAlmostEqual(bq.compute_climate_score(months()), 100.0)

    def test_missing_or_partial_year_is_null(self):
        self.assertIsNone(bq.compute_climate_score(None))
        self.assertIsNone(bq.compute_climate_score([]))
        self.assertIsNone(bq.compute_climate_score(months()[:11]))

    def test_missing_month_value_is_null(self):
        rows = months()
        rows[6]["precip_mm"] = None
        self.assertIsNone(bq.compute_climate_score(rows))

    def test_atacama_hyper_aridity_is_penalised(self):
        arid = bq.compute_climate_score(months(precip=0.0))
        dry = bq.compute_climate_score(months(precip=100.0 / 12.0))
        ideal = bq.compute_climate_score(months())
        self.assertAlmostEqual(arid, 70.0)
        self.assertAlmostEqual(dry, 72.0)
        self.assertLess(arid, ideal)
        self.assertLess(dry, ideal)

    def test_extreme_heat_is_penalised(self):
        score = bq.compute_climate_score(months(tmax=40.0, tmin=30.0))
        # Thermal comfort collapses to zero; precipitation is ideal (1200 mm).
        self.assertAlmostEqual(score, 30.0)

    def test_cold_is_penalised(self):
        score = bq.compute_climate_score(months(tmax=0.0, tmin=-10.0))
        self.assertAlmostEqual(score, 30.0)

    def test_monsoon_wetness_is_penalised(self):
        score = bq.compute_climate_score(months(precip=4000.0))
        self.assertAlmostEqual(score, 70.0)

    def test_precipitation_comfort_curve(self):
        self.assertEqual(bq.compute_precip_comfort(500.0), 100.0)
        self.assertEqual(bq.compute_precip_comfort(1400.0), 100.0)
        self.assertEqual(bq.compute_precip_comfort(0.0), 0.0)
        self.assertAlmostEqual(bq.compute_precip_comfort(300.0), 20.0)
        self.assertAlmostEqual(bq.compute_precip_comfort(2200.0), 40.0)
        self.assertEqual(bq.compute_precip_comfort(3500.0), 0.0)
        self.assertLess(
            bq.compute_precip_comfort(200.0),
            bq.compute_precip_comfort(500.0),
        )
        self.assertLess(
            bq.compute_precip_comfort(3000.0),
            bq.compute_precip_comfort(1400.0),
        )


class AirportScoreTests(unittest.TestCase):
    def test_distance_decay(self):
        self.assertAlmostEqual(
            bq.compute_airport_score(0.0, 0, 0), 75.0
        )
        self.assertAlmostEqual(
            bq.compute_airport_score(137.5, 0, 0), 37.5
        )
        self.assertAlmostEqual(
            bq.compute_airport_score(250.0, 0, 0), 0.0
        )

    def test_close_and_broad_is_full_marks(self):
        self.assertEqual(bq.compute_airport_score(10.0, 20, 0), 100.0)

    def test_breadth_blend_and_missing(self):
        broad = bq.compute_airport_score(25.0, 2, 3)
        narrow = bq.compute_airport_score(25.0, 0, 0)
        self.assertGreater(broad, narrow)
        self.assertIsNone(bq.compute_airport_score(None, None, None))

    def test_counts_known_but_distance_missing(self):
        self.assertAlmostEqual(
            bq.compute_airport_score(None, 0, 0), 0.0
        )


class UncertaintyTests(unittest.TestCase):
    def test_low_requires_completeness_broadband_and_safety(self):
        self.assertEqual(
            bq.classify_uncertainty(
                1.0,
                broadband_present=True,
                broadband_total_tests=10,
                safety_subindicators_present=True,
            ),
            "low",
        )
        self.assertEqual(
            bq.classify_uncertainty(
                1.0,
                broadband_present=True,
                broadband_total_tests=9,
                safety_subindicators_present=True,
            ),
            "medium",
        )
        self.assertEqual(
            bq.classify_uncertainty(
                1.0,
                broadband_present=True,
                broadband_total_tests=10,
                safety_subindicators_present=False,
            ),
            "medium",
        )

    def test_medium_and_high_thresholds(self):
        self.assertEqual(
            bq.classify_uncertainty(
                0.95,
                broadband_present=False,
                safety_subindicators_present=True,
            ),
            "medium",
        )
        self.assertEqual(bq.classify_uncertainty(0.90), "medium")
        self.assertEqual(bq.classify_uncertainty(0.899), "high")
        self.assertEqual(bq.classify_uncertainty(None), "high")


class PercentileTests(unittest.TestCase):
    def test_percentile_map(self):
        ranks = bq.percentile_map([0.0, 10.0, 20.0])
        self.assertEqual(ranks[0.0], 0.0)
        self.assertEqual(ranks[10.0], 50.0)
        self.assertEqual(ranks[20.0], 100.0)
        self.assertEqual(bq.percentile_map([5.0]), {5.0: 50.0})
        self.assertEqual(bq.percentile_map([None, None]), {})

    def test_weighted_available_renormalises(self):
        self.assertIsNone(bq.weighted_available([(None, 0.5), (None, 0.5)]))
        self.assertAlmostEqual(
            bq.weighted_available([(100.0, 0.7), (None, 0.3)]), 100.0
        )
        self.assertAlmostEqual(
            bq.weighted_available([(100.0, 0.7), (50.0, 0.3)]), 85.0
        )

    def test_pick_column(self):
        available = {"NDVI_Score_0_100", "canopy_height_m"}
        self.assertEqual(
            bq.pick_column(available, bq.GREENNESS_NDVI_SCORE_CANDIDATES),
            "NDVI_Score_0_100",
        )
        self.assertIsNone(bq.pick_column(available, ("missing",)))

    def test_ndvi_score_formula(self):
        self.assertIsNone(bq.compute_ndvi_score(None))
        self.assertEqual(bq.compute_ndvi_score(0.0), 0.0)
        self.assertEqual(bq.compute_ndvi_score(0.05), 0.0)
        self.assertEqual(bq.compute_ndvi_score(0.325), 50.0)
        self.assertEqual(bq.compute_ndvi_score(0.6), 100.0)
        self.assertEqual(bq.compute_ndvi_score(0.9), 100.0)


class SupplementaryLoadTests(unittest.TestCase):
    def test_missing_optional_tables_are_skipped(self):
        self.assertEqual(bq.load_supplementary(FakeCursor({})), {})

    def test_score_columns_and_hazard_are_read(self):
        cursor = FakeCursor(
            {
                "urban_centre_greenness": {
                    "columns": ["urban_centre_id", "ndvi_score_0_100"],
                    "rows": [{"urban_centre_id": 7, "value": 61.5}],
                },
                "urban_centre_climate": {
                    "columns": ["urban_centre_id", "heat_stress_days"],
                    "rows": [{"urban_centre_id": 7, "value": 21}],
                },
                "urban_centre_hazard": {
                    "columns": ["urban_centre_id", "events_2015"],
                    "rows": [{"urban_centre_id": 7, "events_2015": 4}],
                },
            }
        )
        values = bq.load_supplementary(cursor)
        self.assertEqual(
            values[7],
            {
                "ghsl_ndvi_score": 61.5,
                "ghsl_heat_stress_days": 21,
                "ghsl_hazard_events": 4,
            },
        )

    def test_raw_ndvi_mean_is_converted(self):
        cursor = FakeCursor(
            {
                "urban_centre_greenness": {
                    "columns": ["urban_centre_id", "ndvi_mean"],
                    "rows": [
                        {"urban_centre_id": 3, "value": 0.325},
                        {"urban_centre_id": 4, "value": None},
                    ],
                }
            }
        )
        values = bq.load_supplementary(cursor)
        self.assertEqual(values[3]["ghsl_ndvi_score"], 50.0)
        self.assertIsNone(values[4]["ghsl_ndvi_score"])

    def test_table_without_known_columns_is_skipped(self):
        cursor = FakeCursor(
            {
                "urban_centre_climate": {
                    "columns": ["urban_centre_id", "koppen_class"],
                    "rows": [{"urban_centre_id": 1, "value": "Cfb"}],
                }
            }
        )
        self.assertEqual(bq.load_supplementary(cursor), {})


class SchemaTests(unittest.TestCase):
    def test_core_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(bq.CORE_WEIGHTS.values()), 1.0)
        self.assertEqual(
            set(bq.CORE_WEIGHTS),
            {
                "environment",
                "climate",
                "safety_health",
                "affordability",
                "connectivity",
            },
        )

    def test_schema_has_layer_and_uncertainty_columns(self):
        for column in (
            "quality_score",
            "data_completeness",
            "missing_dimensions",
            "uncertainty_tier",
            "uncertainty_flags",
            "provenance_summary",
            "urban_centre_id",
            "ghsl_match_status",
            "ghsl_ndvi_score",
            "ghsl_heat_stress_days",
            "ghsl_hazard_events",
            "ghsl_enhanced_score",
            "walkability_score",
            "public_transport_score",
            "culture_food_score",
            "park_score",
            "physical_green_score",
            "poi_observation_status",
            "transit_observation_status",
            "park_observation_status",
        ):
            self.assertIn(column, bq.SCHEMA_SQL)

    def test_insert_columns_match_schema(self):
        self.assertEqual(len(bq.INSERT_COLUMNS), len(set(bq.INSERT_COLUMNS)))
        for column in bq.INSERT_COLUMNS:
            self.assertIn(column, bq.SCHEMA_SQL)
        self.assertEqual(
            bq.INSERT_SQL.count("%s"), len(bq.INSERT_COLUMNS)
        )

    def test_experimental_columns_never_in_core_weights(self):
        for name in (
            "walkability",
            "public_transport",
            "culture_food",
            "park",
            "physical_green",
        ):
            self.assertNotIn(name, bq.CORE_WEIGHTS)


class ScoreRowsTests(unittest.TestCase):
    def test_complete_row_has_full_completeness(self):
        rows = bq.score_rows([core_row()])
        row = rows[0]
        self.assertAlmostEqual(row["data_completeness"], 1.0)
        self.assertEqual(row["missing_dimensions"], "")
        self.assertAlmostEqual(row["environment_score"], 100.0)
        self.assertAlmostEqual(row["climate_score"], 100.0)
        self.assertIsNotNone(row["quality_score"])
        self.assertGreaterEqual(row["quality_score"], 0.0)
        self.assertLessEqual(row["quality_score"], 100.0)

    def test_missing_core_dimension_is_renormalised(self):
        rows = bq.score_rows([core_row(climate_months=None)])
        row = rows[0]
        self.assertAlmostEqual(row["data_completeness"], 0.80)
        self.assertEqual(row["missing_dimensions"], "climate")
        self.assertAlmostEqual(
            row["quality_score"],
            (
                0.30 * row["environment_score"]
                + 0.25 * row["safety_health_score"]
                + 0.15 * row["affordability_score"]
                + 0.10 * row["connectivity_score"]
            )
            / 0.80,
        )
        self.assertEqual(row["uncertainty_tier"], "high")

    def test_safety_and_affordability_use_percentiles(self):
        best = core_row(destination_id="best", homicide_per_100k=1.0,
                        road_deaths_per_100k=2.0, life_expectancy_years=85.0,
                        physicians_per_1000=5.0,
                        price_level_vs_usd=50.0, out_of_pocket_pct=5.0)
        worst = core_row(destination_id="worst", homicide_per_100k=20.0,
                         road_deaths_per_100k=30.0, life_expectancy_years=55.0,
                         physicians_per_1000=0.5,
                         price_level_vs_usd=150.0, out_of_pocket_pct=60.0)
        rows = bq.score_rows([best, worst])
        self.assertAlmostEqual(rows[0]["safety_health_score"], 100.0)
        self.assertAlmostEqual(rows[1]["safety_health_score"], 0.0)
        self.assertAlmostEqual(rows[0]["affordability_score"], 100.0)
        self.assertAlmostEqual(rows[1]["affordability_score"], 0.0)
        self.assertIn("country_level_safety", rows[0]["uncertainty_flags"])
        self.assertIn(
            "country_level_affordability", rows[0]["uncertainty_flags"]
        )

    def test_connectivity_falls_back_to_airport_without_ookla(self):
        connected = core_row(destination_id="connected")
        remote = core_row(
            destination_id="remote",
            median_down_mbps=None,
            median_up_mbps=None,
            latency_ms=None,
            broadband_total_tests=None,
            airport_nearest_distance_km=300.0,
            airport_large_100km=0,
            airport_medium_100km=0,
        )
        rows = bq.score_rows([connected, remote])
        self.assertAlmostEqual(
            rows[0]["connectivity_score"],
            0.70 * rows[0]["broadband_score"]
            + 0.30 * rows[0]["airport_access_score"],
        )
        self.assertIsNone(rows[1]["broadband_score"])
        self.assertEqual(
            rows[1]["connectivity_score"], rows[1]["airport_access_score"]
        )
        self.assertIn("ookla_missing", rows[1]["uncertainty_flags"])
        self.assertEqual(rows[1]["uncertainty_tier"], "medium")

    def test_low_sample_ookla_is_flagged_and_excluded(self):
        row = bq.score_rows([core_row(broadband_total_tests=4)])[0]
        self.assertIsNone(row["broadband_score"])
        self.assertEqual(
            row["connectivity_score"], row["airport_access_score"]
        )
        self.assertIn("ookla_low_sample", row["uncertainty_flags"])
        self.assertEqual(row["uncertainty_tier"], "medium")

    def test_low_sample_cannot_inherit_a_valid_percentile(self):
        valid = core_row(destination_id="valid")
        low_sample = core_row(
            destination_id="low-sample", broadband_total_tests=5
        )
        rows = bq.score_rows([valid, low_sample])
        self.assertIsNotNone(rows[0]["broadband_score"])
        self.assertIsNone(rows[1]["broadband_score"])
        self.assertEqual(
            rows[1]["connectivity_score"], rows[1]["airport_access_score"]
        )

    def test_missing_homicide_flag_and_tier(self):
        row = bq.score_rows([core_row(homicide_per_100k=None)])[0]
        self.assertIn("homicide_missing", row["uncertainty_flags"])
        self.assertEqual(row["uncertainty_tier"], "medium")
        self.assertAlmostEqual(row["data_completeness"], 1.0)
        self.assertIsNotNone(row["safety_health_score"])

    def test_supplementary_layer_does_not_change_core_score(self):
        with_ghsl = core_row(destination_id="with", ghsl_ndvi_score=80.0)
        without_ghsl = core_row(
            destination_id="without", ghsl_ndvi_score=None
        )
        rows = bq.score_rows([with_ghsl, without_ghsl])
        self.assertEqual(rows[0]["ghsl_match_status"], "matched")
        self.assertAlmostEqual(
            rows[0]["ghsl_enhanced_score"],
            0.40 * 80.0 + 0.36 * rows[0]["air_score"]
            + 0.24 * rows[0]["water_score"],
        )
        self.assertIsNone(rows[1]["ghsl_enhanced_score"])
        self.assertAlmostEqual(
            rows[0]["quality_score"], rows[1]["quality_score"]
        )
        self.assertEqual(
            rows[0]["data_completeness"], rows[1]["data_completeness"]
        )

    def test_unmatched_city_flagged(self):
        row = bq.score_rows([core_row(urban_centre_id=None)])[0]
        self.assertEqual(row["ghsl_match_status"], "unmatched")
        self.assertIsNone(row["ghsl_enhanced_score"])
        self.assertIn("no_ghsl_polygon", row["uncertainty_flags"])

    def test_experimental_layer_does_not_change_core_score(self):
        rich = core_row(destination_id="rich")
        sparse = core_row(
            destination_id="sparse",
            walkability_score=None,
            public_transport_score=None,
            transit_stops_5km=0,
            restaurant_count=0,
            museum_culture_count=0,
            cuisine_entropy=None,
            park_count_5km=0,
            physical_green_score=None,
        )
        rows = bq.score_rows([rich, sparse])
        self.assertAlmostEqual(
            rows[0]["quality_score"], rows[1]["quality_score"]
        )
        self.assertAlmostEqual(
            rows[0]["data_completeness"], rows[1]["data_completeness"]
        )
        self.assertEqual(
            rows[1]["poi_observation_status"], "none_listed_or_unmapped"
        )
        self.assertEqual(
            rows[1]["transit_observation_status"], "unobserved_or_unmapped"
        )
        self.assertEqual(rows[1]["park_observation_status"], "none_listed")
        self.assertIsNone(rows[1]["park_score"])
        self.assertEqual(rows[0]["poi_observation_status"], "observed")
        self.assertEqual(rows[0]["transit_observation_status"], "observed")
        self.assertEqual(rows[0]["park_observation_status"], "observed")

    def test_provenance_and_source_are_populated(self):
        row = bq.score_rows([core_row()])[0]
        self.assertEqual(row["provenance_summary"], bq.PROVENANCE_SUMMARY)
        self.assertIn("Core:", row["provenance_summary"])
        self.assertIn("Supplementary:", row["provenance_summary"])
        self.assertIn("Experimental:", row["provenance_summary"])
        self.assertEqual(row["source"], bq.SOURCE)
        self.assertEqual(row["observed_period"], bq.OBSERVED_PERIOD)

    def test_empty_input(self):
        self.assertEqual(bq.score_rows([]), [])


if __name__ == "__main__":
    unittest.main()
