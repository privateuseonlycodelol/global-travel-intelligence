"""Offline tests for the GHSL UCDB urban form loader."""

import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_urban_form as uf  # noqa: E402


def ghsl_row(**overrides):
    row = {
        "ID_UC_G0": "35",
        "GC_UCN_MAI_2025": "Testville",
        "GC_UCA_KM2_2025": "35",
        "GC_POP_TOT_2025": "60041.65661",
        "GH_BUS_TOT_2025": "3902673",
        "GH_BUH_AVG_2020": "4.991132538",
    }
    row.update(overrides)
    return row


def infrastructure_row(**overrides):
    row = {
        "ID_UC_G0": "35",
        "IN_ROA_LEN_2024": "230124.2493",
        "IN_ROA_DEN_2024": "0.006574979",
    }
    row.update(overrides)
    return row


class NumberParsingTests(unittest.TestCase):
    def test_number_accepts_numeric_strings_and_floats(self):
        self.assertEqual(uf.number("0.42"), 0.42)
        self.assertEqual(uf.number("  -3.5  "), -3.5)
        self.assertEqual(uf.number("1e3"), 1000.0)
        self.assertEqual(uf.number(7), 7.0)

    def test_number_rejects_missing_and_non_numeric(self):
        for value in ("", "-", "--", "NA", "n/a", "N/A", "n.a.", "null",
                      "None", "nan", "#N/A", "not a number", None):
            with self.subTest(value=value):
                self.assertIsNone(uf.number(value))

    def test_number_rejects_infinite(self):
        self.assertIsNone(uf.number("inf"))
        self.assertIsNone(uf.number("-inf"))
        self.assertIsNone(uf.number("NaN"))

    def test_integer_rounds_and_rejects_missing(self):
        self.assertEqual(uf.integer("7"), 7)
        self.assertEqual(uf.integer("7.6"), 8)
        self.assertIsNone(uf.integer("NA"))
        self.assertIsNone(uf.integer(None))


class InterpolateTests(unittest.TestCase):
    anchors = ((0.0, 0.0), (10.0, 50.0), (20.0, 100.0))

    def test_none_stays_none(self):
        self.assertIsNone(uf.interpolate(None, self.anchors))

    def test_clamps_below_and_above(self):
        self.assertEqual(uf.interpolate(-5.0, self.anchors), 0.0)
        self.assertEqual(uf.interpolate(99.0, self.anchors), 100.0)

    def test_linear_midpoint(self):
        self.assertEqual(uf.interpolate(5.0, self.anchors), 25.0)
        self.assertEqual(uf.interpolate(15.0, self.anchors), 75.0)

    def test_exact_anchor(self):
        self.assertEqual(uf.interpolate(10.0, self.anchors), 50.0)


class DensityCurveTests(unittest.TestCase):
    def test_peak_band_is_five_to_eighteen_thousand(self):
        for density in (5000, 8000, 12000, 18000):
            with self.subTest(density=density):
                self.assertEqual(uf.density_score(density), 100.0)

    def test_auto_sprawl_is_penalised(self):
        self.assertEqual(uf.density_score(500), 0.0)
        self.assertEqual(uf.density_score(750), 0.0)
        self.assertEqual(uf.density_score(1000), 5.0)
        self.assertLess(uf.density_score(1000), uf.density_score(6000))

    def test_hyper_congestion_is_penalised(self):
        self.assertEqual(uf.density_score(35000), 40.0)
        self.assertEqual(uf.density_score(50000), 15.0)
        self.assertEqual(uf.density_score(80000), 0.0)
        self.assertLess(uf.density_score(50000), uf.density_score(12000))

    def test_curve_rises_then_falls(self):
        rising = [uf.density_score(x) for x in
                  (0, 750, 1500, 2500, 3500, 5000)]
        for left, right in zip(rising, rising[1:]):
            self.assertLessEqual(left, right)
        falling = [uf.density_score(x) for x in
                   (18000, 25000, 35000, 50000, 80000)]
        for left, right in zip(falling, falling[1:]):
            self.assertGreaterEqual(left, right)

    def test_scores_stay_in_bounds(self):
        for density in range(0, 100001, 250):
            score = uf.density_score(float(density))
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)


class CompactnessTests(unittest.TestCase):
    def test_sparse_footprint_scores_below_contiguous_footprint(self):
        self.assertLess(
            uf.compactness_score(0.01), uf.compactness_score(0.20)
        )

    def test_high_fraction_is_not_penalised(self):
        self.assertEqual(uf.compactness_score(0.35), 100.0)
        self.assertEqual(uf.compactness_score(0.45), 100.0)

    def test_none_stays_none(self):
        self.assertIsNone(uf.compactness_score(None))

    def test_scores_stay_in_bounds(self):
        for hundredths in range(0, 51):
            score = uf.compactness_score(hundredths / 100.0)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)


class VerticalityTests(unittest.TestCase):
    def test_mid_rise_peak(self):
        for height in (8.0, 9.0, 11.0):
            with self.subTest(height=height):
                self.assertGreaterEqual(uf.verticality_score(height), 98.0)

    def test_single_storey_sprawl_is_penalised(self):
        self.assertEqual(uf.verticality_score(2.5), 15.0)
        self.assertLess(uf.verticality_score(2.5), uf.verticality_score(8.0))

    def test_tower_extremes_are_penalised(self):
        self.assertEqual(uf.verticality_score(18.0), 65.0)
        self.assertEqual(uf.verticality_score(25.0), 35.0)
        self.assertEqual(uf.verticality_score(40.0), 10.0)

    def test_none_stays_none(self):
        self.assertIsNone(uf.verticality_score(None))

    def test_scores_stay_in_bounds(self):
        for tenths in range(0, 500):
            score = uf.verticality_score(tenths / 10.0)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)


class UrbanFormScoreTests(unittest.TestCase):
    def test_fixed_anchor_example(self):
        density = 60041.65661 / 35.0
        fraction = 3902673 / (35.0 * 1_000_000.0)
        height = 4.991132538
        expected = round(
            0.5 * uf.density_score(density)
            + 0.25 * uf.compactness_score(fraction)
            + 0.25 * uf.verticality_score(height),
            2,
        )
        self.assertEqual(
            uf.urban_form_score(density, fraction, height), expected
        )
        self.assertEqual(uf.urban_form_score(density, fraction, height), 41.67)

    def test_missing_density_has_no_score(self):
        self.assertIsNone(uf.urban_form_score(None, 0.2, 9.0))

    def test_missing_height_renormalises_remaining_weights(self):
        density = 6000.0
        fraction = 0.2
        expected = round(
            (0.5 * uf.density_score(density)
             + 0.25 * uf.compactness_score(fraction)) / 0.75,
            2,
        )
        self.assertEqual(
            uf.urban_form_score(density, fraction, None), expected
        )

    def test_density_only_falls_back_to_density_curve(self):
        self.assertEqual(uf.urban_form_score(6000.0, None, None), 100.0)

    def test_score_is_rounded_to_two_decimals(self):
        score = uf.urban_form_score(1715.475903142857, 0.11150494, 4.99113)
        self.assertEqual(score, round(score, 2))

    def test_scores_stay_in_bounds(self):
        for density in (0, 500, 1500, 6000, 35000, 90000):
            for fraction in (0.0, 0.05, 0.2, 0.45):
                for height in (0.0, 2.5, 8.0, 40.0):
                    score = uf.urban_form_score(density, fraction, height)
                    self.assertGreaterEqual(score, 0.0)
                    self.assertLessEqual(score, 100.0)


class RoadCompletenessTests(unittest.TestCase):
    def test_fixture_ratio(self):
        ratio = uf.road_km_per_built_km2(
            230124.2493 / 1000.0, 3902673.0
        )
        self.assertAlmostEqual(ratio, 58.965803514668025)

    def test_one_km_road_per_built_km2(self):
        self.assertEqual(uf.road_km_per_built_km2(1.0, 1_000_000.0), 1.0)

    def test_missing_or_zero_inputs_stay_none(self):
        self.assertIsNone(uf.road_km_per_built_km2(None, 1_000_000.0))
        self.assertIsNone(uf.road_km_per_built_km2(1.0, None))
        self.assertIsNone(uf.road_km_per_built_km2(1.0, 0.0))
        self.assertIsNone(uf.road_km_per_built_km2(1.0, -1.0))


class ReadRowsTests(unittest.TestCase):
    def test_reads_bom_header_and_pads_missing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "theme.csv"
            path.write_bytes(
                b"\xef\xbb\xbfID_UC_G0,IN_ROA_LEN_2024\r\n"
                b"1,230.5\r\n"
                b"\r\n"
                b"2\r\n"
            )
            rows = uf.read_rows(path)
        self.assertEqual(len(rows), 2)
        self.assertIn("ID_UC_G0", rows[0])
        self.assertEqual(rows[0]["IN_ROA_LEN_2024"], "230.5")
        self.assertEqual(rows[1]["IN_ROA_LEN_2024"], "")

    def test_empty_file_returns_no_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "theme.csv"
            path.write_text("ID_UC_G0,IN_ROA_LEN_2024\n", encoding="latin-1")
            self.assertEqual(uf.read_rows(path), [])


class EnsureCsvTests(unittest.TestCase):
    def test_reuses_cached_csv_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            info = uf.THEMES["GHSL"]
            expected = Path(folder) / "GHSL" / info["csv"]
            expected.parent.mkdir(parents=True)
            expected.write_text("ID_UC_G0\n", encoding="latin-1")
            self.assertEqual(uf.ensure_csv("GHSL", folder), expected)


class ParseFormTests(unittest.TestCase):
    def test_joins_themes_and_computes_metrics(self):
        records, meta = uf.parse_form(
            [ghsl_row()], [infrastructure_row()]
        )
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["urban_centre_id"], 35)
        self.assertAlmostEqual(record["pop_density_km2"], 1715.475903142857)
        self.assertAlmostEqual(record["built_up_fraction"], 0.111504942857)
        self.assertAlmostEqual(record["mean_building_height_m"], 4.991132538)
        self.assertAlmostEqual(record["road_length_km"], 230.1242493)
        self.assertAlmostEqual(
            record["road_km_per_built_km2"], 58.965803514668025
        )
        self.assertEqual(record["urban_form_score"], 41.67)
        self.assertEqual(record["source"], uf.SOURCE)
        self.assertEqual(record["source_url"], uf.SOURCE_URL)
        self.assertEqual(record["observed_period"], uf.OBSERVED_PERIOD)
        self.assertEqual(meta["rows"], 1)
        self.assertEqual(meta["ghsl_rows"], 1)
        self.assertEqual(meta["infrastructure_rows"], 1)

    def test_missing_infrastructure_row_keeps_form_score(self):
        records, _ = uf.parse_form([ghsl_row()], [])
        record = records[0]
        self.assertIsNone(record["road_length_km"])
        self.assertIsNone(record["road_km_per_built_km2"])
        self.assertIsNotNone(record["urban_form_score"])

    def test_missing_area_removes_density_and_score(self):
        records, _ = uf.parse_form(
            [ghsl_row(**{"GC_UCA_KM2_2025": "NA"})],
            [infrastructure_row()],
        )
        record = records[0]
        self.assertIsNone(record["pop_density_km2"])
        self.assertIsNone(record["built_up_fraction"])
        self.assertIsNone(record["urban_form_score"])
        self.assertIsNotNone(record["road_length_km"])

    def test_na_height_renormalises_score(self):
        records, _ = uf.parse_form(
            [ghsl_row(**{"GH_BUH_AVG_2020": "-"})],
            [infrastructure_row()],
        )
        record = records[0]
        self.assertIsNone(record["mean_building_height_m"])
        self.assertIsNotNone(record["urban_form_score"])

    def test_missing_ids_are_skipped(self):
        records, _ = uf.parse_form(
            [ghsl_row(**{"ID_UC_G0": ""}), ghsl_row(**{"ID_UC_G0": "7"})],
            [infrastructure_row(**{"ID_UC_G0": "NA"})],
        )
        self.assertEqual(
            [record["urban_centre_id"] for record in records], [7]
        )

    def test_duplicate_ghsl_rows_collapse_with_last_value(self):
        records, meta = uf.parse_form(
            [
                ghsl_row(**{"GC_POP_TOT_2025": "35000"}),
                ghsl_row(**{"GC_POP_TOT_2025": "70000"}),
            ],
            [infrastructure_row()],
        )
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["pop_density_km2"], 2000.0)
        self.assertEqual(meta["duplicates"], 1)

    def test_duplicate_infrastructure_rows_collapse_with_last_value(self):
        records, meta = uf.parse_form(
            [ghsl_row()],
            [
                infrastructure_row(**{"IN_ROA_LEN_2024": "1000"}),
                infrastructure_row(**{"IN_ROA_LEN_2024": "35000"}),
            ],
        )
        self.assertEqual(records[0]["road_length_km"], 35.0)
        self.assertEqual(meta["duplicates"], 1)

    def test_empty_input_has_empty_metadata(self):
        records, meta = uf.parse_form([], [])
        self.assertEqual(records, [])
        self.assertEqual(meta["rows"], 0)
        self.assertEqual(meta["duplicates"], 0)


class SpearmanTests(unittest.TestCase):
    def test_perfect_positive_and_negative(self):
        self.assertEqual(
            uf.spearman_correlation([1, 2, 3], [10, 20, 30])[0], 1.0
        )
        self.assertEqual(
            uf.spearman_correlation([1, 2, 3], [30, 20, 10])[0], -1.0
        )

    def test_ties_use_average_ranks(self):
        rho, count = uf.spearman_correlation([1, 2, 2, 3], [1, 2, 3, 4])
        self.assertEqual(count, 4)
        self.assertAlmostEqual(rho, 0.9486832980505138)

    def test_null_pairs_are_dropped(self):
        rho, count = uf.spearman_correlation(
            [1, None, 3, 4], [10, 20, None, 40]
        )
        self.assertEqual(count, 2)
        self.assertIsNone(rho)

    def test_too_few_pairs_stay_none(self):
        self.assertEqual(uf.spearman_correlation([], []), (None, 0))
        self.assertEqual(
            uf.spearman_correlation([1, 2], [1, 2]), (None, 2)
        )

    def test_symmetric(self):
        left = uf.spearman_correlation([1, 2, 3, 4], [4, 3, 2, 1])[0]
        right = uf.spearman_correlation([4, 3, 2, 1], [1, 2, 3, 4])[0]
        self.assertEqual(left, right)


class ValidationSummaryTests(unittest.TestCase):
    def rows(self):
        return [
            {"continent": "EU", "walkability_score": 70,
             "urban_form_score": 60, "restaurant_count": 100},
            {"continent": "EU", "walkability_score": 75,
             "urban_form_score": 70, "restaurant_count": 200},
            {"continent": "EU", "walkability_score": 80,
             "urban_form_score": 80, "restaurant_count": 300},
            {"continent": "EU", "walkability_score": 85,
             "urban_form_score": 90, "restaurant_count": 400},
            {"continent": "NA", "walkability_score": 80,
             "urban_form_score": 70, "restaurant_count": 50},
            {"continent": "NA", "walkability_score": 70,
             "urban_form_score": 80, "restaurant_count": 60},
            {"continent": "NA", "walkability_score": 60,
             "urban_form_score": 90, "restaurant_count": 70},
        ]

    def test_reports_overlap_and_both_guards(self):
        summary = uf.summarize_validation(
            self.rows(), walkability_rows=662, with_metro=660
        )
        rows = self.rows()
        expected, _ = uf.spearman_correlation(
            [row["walkability_score"] for row in rows],
            [row["urban_form_score"] for row in rows],
        )
        self.assertEqual(summary["overlap"]["walkability_rows"], 662)
        self.assertEqual(summary["overlap"]["walkability_with_metro"], 660)
        self.assertEqual(summary["overlap"]["validated_rows"], 7)
        self.assertEqual(
            summary["walkability"]["overall"]["rho"], round(expected, 3)
        )
        self.assertEqual(summary["walkability"]["overall"]["n"], 7)
        self.assertEqual(
            summary["walkability"]["by_continent"]["EU"]["rho"], 1.0
        )
        self.assertEqual(
            summary["walkability"]["by_continent"]["NA"]["rho"], -1.0
        )
        self.assertIn("restaurant_count", summary)
        self.assertEqual(summary["restaurant_count"]["overall"]["n"], 7)

    def test_restaurant_guard_uses_log1p(self):
        rows = self.rows()
        summary = uf.summarize_validation(rows)
        expected, count = uf.spearman_correlation(
            [math.log1p(row["restaurant_count"]) for row in rows],
            [row["urban_form_score"] for row in rows],
        )
        self.assertEqual(
            summary["restaurant_count"]["overall"]["rho"], round(expected, 3)
        )
        self.assertEqual(summary["restaurant_count"]["overall"]["n"], count)

    def test_missing_scores_reduce_the_pair_count(self):
        rows = self.rows()
        rows[0]["urban_form_score"] = None
        rows[1]["restaurant_count"] = None
        summary = uf.summarize_validation(rows)
        self.assertEqual(summary["walkability"]["overall"]["n"], 6)
        self.assertEqual(summary["restaurant_count"]["overall"]["n"], 5)

    def test_unknown_continent_is_grouped_explicitly(self):
        rows = self.rows()
        rows[0]["continent"] = None
        summary = uf.summarize_validation(rows)
        self.assertIn("UNKNOWN", summary["walkability"]["by_continent"])


class SchemaTests(unittest.TestCase):
    def test_table_and_primary_key(self):
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS urban_centre_form", uf.SCHEMA_SQL
        )
        self.assertEqual(uf.SCHEMA_SQL.count("BIGINT PRIMARY KEY"), 1)

    def test_required_columns_and_provenance(self):
        for column in (
            "urban_centre_id",
            "pop_density_km2",
            "built_up_fraction",
            "mean_building_height_m",
            "road_length_km",
            "road_km_per_built_km2",
            "urban_form_score",
            "source",
            "source_url",
            "observed_period",
            "updated_at",
        ):
            with self.subTest(column=column):
                self.assertIn(column, uf.SCHEMA_SQL)

    def test_insert_statement_matches_schema(self):
        self.assertEqual(uf.INSERT_SQL.count("%s"), 10)

    def test_validation_sql_reads_both_guards(self):
        self.assertIn("walkability_scores", uf.VALIDATION_SQL)
        self.assertIn("poi_metrics", uf.VALIDATION_SQL)
        self.assertIn("restaurant_count", uf.VALIDATION_SQL)
        self.assertIn("urban_centre_form", uf.VALIDATION_SQL)
        self.assertIn("destination_metro", uf.VALIDATION_SQL)


class ThemeConfigTests(unittest.TestCase):
    def test_both_themes_point_at_v1_2_archives(self):
        self.assertEqual(set(uf.THEMES), {"GHSL", "INFRASTRUCTURES"})
        for theme, info in uf.THEMES.items():
            with self.subTest(theme=theme):
                self.assertIn("GHS_UCDB_THEME_", info["url"])
                self.assertTrue(info["url"].endswith("_V1_2.zip"))
                self.assertTrue(info["csv"].endswith(".csv"))

    def test_provenance_and_observed_period(self):
        self.assertIn("CC BY 4.0", uf.SOURCE)
        self.assertTrue(uf.SOURCE_URL.startswith("https://"))
        for year in ("2025", "2020", "2024"):
            self.assertIn(year, uf.OBSERVED_PERIOD)


if __name__ == "__main__":
    unittest.main()
