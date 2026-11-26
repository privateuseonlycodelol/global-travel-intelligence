"""Offline tests for the GHSL UCDB hazard-exposure loader."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_ucdb_hazards_exposure as hz  # noqa: E402


def exposure_row(**overrides):
    row = {
        "ID_UC_G0": "1",
        "GC_UCN_MAI_2025": "Apia",
        "GC_POP_TOT_2025": "100000",
        "EX_100_SHP_2025": "10",
        "EX_CF1_SHP_2025": "0",
        "EX_100_S1P_2025": "25000",
        "EX_E01_POP_2025": "60000",
        "EX_E02_POP_2025": "20000",
        "EX_E04_POP_2025": "10000",
        "EX_E05_POP_2025": "5000",
        "EX_E06_POP_2025": "3000",
        "EX_E07_POP_2025": "1500",
        "EX_E08_POP_2025": "400",
        "EX_E09_POP_2025": "100",
        "EX_E10_POP_2025": "0",
    }
    row.update(overrides)
    return row


class NumberParsingTests(unittest.TestCase):
    def test_number_accepts_numeric_strings_and_floats(self):
        self.assertEqual(hz.number("0.42"), 0.42)
        self.assertEqual(hz.number("  -3.5  "), -3.5)
        self.assertEqual(hz.number("1e3"), 1000.0)
        self.assertEqual(hz.number(7), 7.0)

    def test_number_rejects_missing_and_non_numeric(self):
        for value in ("", "-", "--", "NA", "n/a", "N/A", "n.a.", "null",
                      "None", "nan", "#N/A", "not a number", None):
            with self.subTest(value=value):
                self.assertIsNone(hz.number(value))

    def test_number_rejects_infinite(self):
        self.assertIsNone(hz.number("inf"))
        self.assertIsNone(hz.number("-inf"))
        self.assertIsNone(hz.number("NaN"))

    def test_integer_rounds_and_rejects_missing(self):
        self.assertEqual(hz.integer("7"), 7)
        self.assertEqual(hz.integer("7.6"), 8)
        self.assertIsNone(hz.integer("NA"))
        self.assertIsNone(hz.integer(None))


class ShareAnchorTests(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(hz.share_safety_score(None))
        self.assertIsNone(hz.flood_safety_score(None))
        self.assertIsNone(hz.cyclone_safety_score(None))

    def test_flood_anchor_points(self):
        self.assertEqual(hz.flood_safety_score(0), 100.0)
        self.assertEqual(hz.flood_safety_score(10), 80.0)
        self.assertEqual(hz.flood_safety_score(25), 50.0)
        self.assertEqual(hz.flood_safety_score(50), 0.0)
        self.assertEqual(hz.flood_safety_score(75), 0.0)
        self.assertEqual(hz.flood_safety_score(100), 0.0)

    def test_cyclone_anchor_matches_flood_anchor(self):
        for share in (0, 5, 12.5, 25, 50, 100):
            with self.subTest(share=share):
                self.assertEqual(
                    hz.cyclone_safety_score(share),
                    hz.flood_safety_score(share),
                )

    def test_scores_are_clamped_outside_zero_to_hundred(self):
        self.assertEqual(hz.flood_safety_score(-20), 100.0)
        self.assertEqual(hz.flood_safety_score(120), 0.0)

    def test_share_scores_are_bounded_and_monotonic(self):
        previous = None
        for tenths in range(-100, 1001):
            score = hz.share_safety_score(tenths / 10.0)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)
            if previous is not None:
                self.assertLessEqual(score, previous)
            previous = score

    def test_share_score_is_rounded_to_two_decimals(self):
        self.assertEqual(hz.flood_safety_score(33.333), 33.33)


class SeismicAnchorTests(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(hz.seismic_safety_score(None))

    def test_mmi_anchor_points(self):
        self.assertEqual(hz.seismic_safety_score(0), 100.0)
        self.assertEqual(hz.seismic_safety_score(4), 100.0)
        self.assertEqual(hz.seismic_safety_score(4.5), 90.0)
        self.assertEqual(hz.seismic_safety_score(5), 80.0)
        self.assertEqual(hz.seismic_safety_score(5.5), 65.0)
        self.assertEqual(hz.seismic_safety_score(6), 50.0)
        self.assertEqual(hz.seismic_safety_score(6.5), 35.0)
        self.assertEqual(hz.seismic_safety_score(7), 20.0)
        self.assertEqual(hz.seismic_safety_score(7.5), 10.0)
        self.assertEqual(hz.seismic_safety_score(8), 0.0)
        self.assertEqual(hz.seismic_safety_score(10), 0.0)
        self.assertEqual(hz.seismic_safety_score(12), 0.0)
        self.assertEqual(hz.seismic_safety_score(-1), 100.0)

    def test_seismic_scores_are_bounded_and_monotonic(self):
        previous = None
        for hundredths in range(-100, 1201):
            score = hz.seismic_safety_score(hundredths / 100.0)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)
            if previous is not None:
                self.assertLessEqual(score, previous)
            previous = score

    def test_seismic_score_is_rounded_to_two_decimals(self):
        self.assertEqual(hz.seismic_safety_score(5.333), 70.01)


class HazardCompositeTests(unittest.TestCase):
    def test_mean_only_when_all_three_present(self):
        self.assertEqual(hz.hazard_safety_score(80, 100, 50), 76.67)
        self.assertIsNone(hz.hazard_safety_score(None, 100, 50))
        self.assertIsNone(hz.hazard_safety_score(80, None, 50))
        self.assertIsNone(hz.hazard_safety_score(80, 100, None))

    def test_composite_stays_bounded(self):
        for flood in (0, 50, 100):
            for seismic in (0, 50, 100):
                for cyclone in (0, 50, 100):
                    score = hz.hazard_safety_score(flood, seismic, cyclone)
                    self.assertGreaterEqual(score, 0.0)
                    self.assertLessEqual(score, 100.0)


class LatestColumnTests(unittest.TestCase):
    def test_prefers_2025_over_2030_projection(self):
        column, year = hz.latest_column_at_most(
            ["ID_UC_G0", "EX_100_SHP_2020", "EX_100_SHP_2025",
             "EX_100_SHP_2030"],
            hz.FLOOD_RIVER_PREFIX,
        )
        self.assertEqual(column, "EX_100_SHP_2025")
        self.assertEqual(year, 2025)

    def test_ignores_years_after_the_cap(self):
        self.assertEqual(
            hz.latest_column_at_most(["EX_100_SHP_2030"], "EX_100_SHP_"),
            (None, None),
        )

    def test_falls_back_to_an_older_year(self):
        column, year = hz.latest_column_at_most(
            ["EX_100_SHP_2015", "EX_100_SHP_2020"], "EX_100_SHP_"
        )
        self.assertEqual(column, "EX_100_SHP_2020")
        self.assertEqual(year, 2020)

    def test_ignores_placeholder_year_suffix(self):
        column, year = hz.latest_column_at_most(
            ["EX_100_SHP_XXXX", "EX_100_SHP_2025"], "EX_100_SHP_"
        )
        self.assertEqual(column, "EX_100_SHP_2025")
        self.assertEqual(year, 2025)

    def test_missing_prefix_returns_none(self):
        self.assertEqual(
            hz.latest_column_at_most(["ID_UC_G0"], "EX_100_SHP_"),
            (None, None),
        )

    def test_custom_cap_can_accept_2030(self):
        column, year = hz.latest_column_at_most(
            ["EX_100_SHP_2025", "EX_100_SHP_2030"],
            "EX_100_SHP_",
            max_year=2030,
        )
        self.assertEqual(column, "EX_100_SHP_2030")
        self.assertEqual(year, 2030)


class ReadRowsTests(unittest.TestCase):
    def test_reads_bom_header_and_pads_missing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "theme.csv"
            path.write_bytes(
                b"\xef\xbb\xbfID_UC_G0,EX_100_SHP_2025,GC_CNT_GAD_2025\r\n"
                b"1,10.5,M\xe9xico\r\n"
                b"\r\n"
                b"2\r\n"
            )
            rows = hz.read_rows(path)
        self.assertEqual(len(rows), 2)
        self.assertIn("ID_UC_G0", rows[0])
        self.assertEqual(rows[0]["GC_CNT_GAD_2025"], "México")
        self.assertEqual(rows[1]["EX_100_SHP_2025"], "")
        self.assertEqual(rows[1]["GC_CNT_GAD_2025"], "")

    def test_empty_file_returns_no_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "theme.csv"
            path.write_text("ID_UC_G0,EX_100_SHP_2025\n", encoding="latin-1")
            self.assertEqual(hz.read_rows(path), [])

    def test_keep_prefixes_projects_to_id_and_hazard_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "theme.csv"
            path.write_text(
                "ID_UC_G0,EX_100_SHP_2025,GR_AVG_GRN_2025\n"
                "1,10,0.5\n",
                encoding="latin-1",
            )
            rows = hz.read_rows(
                path, keep_prefixes=(hz.FLOOD_RIVER_PREFIX,)
            )
        self.assertEqual(
            set(rows[0]), {"ID_UC_G0", "EX_100_SHP_2025"}
        )
        self.assertEqual(rows[0]["EX_100_SHP_2025"], "10")

    def test_hazard_prefixes_cover_every_selected_family(self):
        for prefix in (
            hz.FLOOD_RIVER_PREFIX,
            hz.FLOOD_COASTAL_PREFIX,
            hz.CYCLONE_POP_PREFIX,
            hz.POPULATION_PREFIX,
            *(prefix for prefix, _ in hz.MMI_CLASSES),
        ):
            with self.subTest(prefix=prefix):
                self.assertIn(prefix, hz.HAZARD_PREFIXES)


class DedupeTests(unittest.TestCase):
    def test_last_duplicate_wins(self):
        records = [
            {"urban_centre_id": 1, "value": "first"},
            {"urban_centre_id": 2, "value": "other"},
            {"urban_centre_id": 1, "value": "last"},
        ]
        deduped, duplicates = hz.dedupe_by_id(records)
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["value"], "last")


class ParseExposureTests(unittest.TestCase):
    def test_parses_all_three_dimensions_and_scores(self):
        records, meta = hz.parse_exposure([exposure_row()])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["urban_centre_id"], 1)
        self.assertEqual(record["flood_exposure_raw"], 10.0)
        self.assertEqual(record["seismic_exposure_raw"], 2.076)
        self.assertEqual(record["cyclone_exposure_raw"], 25.0)
        self.assertEqual(record["flood_safety_score"], 80.0)
        self.assertEqual(record["seismic_safety_score"], 100.0)
        self.assertEqual(record["cyclone_safety_score"], 50.0)
        self.assertEqual(record["hazard_safety_score"], 76.67)
        self.assertIn("CC BY 4.0", record["source"])
        self.assertTrue(record["source_url"].endswith("_V1_2.zip"))
        self.assertEqual(record["observed_period"], "2025")
        self.assertEqual(meta["duplicates"], 0)
        self.assertEqual(meta["seismic_null"], 0)

    def test_metadata_records_the_exact_2025_columns(self):
        _, meta = hz.parse_exposure([exposure_row()])
        self.assertEqual(meta["columns"]["flood_river"], "EX_100_SHP_2025")
        self.assertEqual(
            meta["columns"]["flood_coastal"], "EX_CF1_SHP_2025"
        )
        self.assertEqual(
            meta["columns"]["cyclone_population"], "EX_100_S1P_2025"
        )
        self.assertEqual(meta["columns"]["population"], "GC_POP_TOT_2025")
        self.assertEqual(meta["columns"]["mmi"][7.0], "EX_E07_POP_2025")
        self.assertEqual(meta["years"]["flood_river"], 2025)
        self.assertEqual(meta["years"]["mmi"][7.0], 2025)

    def test_flood_uses_worse_of_river_and_coastal(self):
        records, _ = hz.parse_exposure([
            exposure_row(**{
                "EX_100_SHP_2025": "10",
                "EX_CF1_SHP_2025": "20",
            })
        ])
        self.assertEqual(records[0]["flood_exposure_raw"], 20.0)
        self.assertEqual(records[0]["flood_safety_score"], 60.0)

    def test_cyclone_share_is_capped_at_100(self):
        records, _ = hz.parse_exposure([
            exposure_row(**{
                "GC_POP_TOT_2025": "100000",
                "EX_100_S1P_2025": "150000",
            })
        ])
        self.assertEqual(records[0]["cyclone_exposure_raw"], 100.0)
        self.assertEqual(records[0]["cyclone_safety_score"], 0.0)

    def test_cyclone_share_is_zero_when_no_population_exposed(self):
        records, _ = hz.parse_exposure([
            exposure_row(**{"EX_100_S1P_2025": "0"})
        ])
        self.assertEqual(records[0]["cyclone_exposure_raw"], 0.0)
        self.assertEqual(records[0]["cyclone_safety_score"], 100.0)

    def test_valid_zero_exposures_score_100(self):
        records, _ = hz.parse_exposure([
            exposure_row(**{
                "EX_100_SHP_2025": "0",
                "EX_CF1_SHP_2025": "0",
                "EX_100_S1P_2025": "0",
                "EX_E01_POP_2025": "100000",
                "EX_E02_POP_2025": "0",
                "EX_E04_POP_2025": "0",
                "EX_E05_POP_2025": "0",
                "EX_E06_POP_2025": "0",
                "EX_E07_POP_2025": "0",
                "EX_E08_POP_2025": "0",
                "EX_E09_POP_2025": "0",
                "EX_E10_POP_2025": "0",
            })
        ])
        record = records[0]
        self.assertEqual(record["flood_safety_score"], 100.0)
        self.assertEqual(record["seismic_safety_score"], 100.0)
        self.assertEqual(record["cyclone_safety_score"], 100.0)
        self.assertEqual(record["hazard_safety_score"], 100.0)

    def test_na_values_become_null_without_dropping_the_row(self):
        records, _ = hz.parse_exposure([
            exposure_row(**{
                "EX_100_SHP_2025": "NA",
                "EX_CF1_SHP_2025": "-",
                "EX_100_S1P_2025": "",
                "EX_E01_POP_2025": "NA",
                "EX_E02_POP_2025": "NA",
                "EX_E04_POP_2025": "NA",
                "EX_E05_POP_2025": "NA",
                "EX_E06_POP_2025": "NA",
                "EX_E07_POP_2025": "NA",
                "EX_E08_POP_2025": "NA",
                "EX_E09_POP_2025": "NA",
                "EX_E10_POP_2025": "NA",
            })
        ])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertIsNone(record["flood_exposure_raw"])
        self.assertIsNone(record["seismic_exposure_raw"])
        self.assertIsNone(record["cyclone_exposure_raw"])
        self.assertIsNone(record["flood_safety_score"])
        self.assertIsNone(record["seismic_safety_score"])
        self.assertIsNone(record["cyclone_safety_score"])
        self.assertIsNone(record["hazard_safety_score"])

    def test_missing_earthquake_classes_stay_null(self):
        records, meta = hz.parse_exposure([
            exposure_row(**{
                "EX_E01_POP_2025": "0",
                "EX_E02_POP_2025": "0",
                "EX_E04_POP_2025": "0",
                "EX_E05_POP_2025": "0",
                "EX_E06_POP_2025": "0",
                "EX_E07_POP_2025": "0",
                "EX_E08_POP_2025": "0",
                "EX_E09_POP_2025": "0",
                "EX_E10_POP_2025": "0",
            })
        ])
        record = records[0]
        self.assertIsNone(record["seismic_exposure_raw"])
        self.assertIsNone(record["seismic_safety_score"])
        self.assertIsNone(record["hazard_safety_score"])
        self.assertEqual(record["flood_safety_score"], 80.0)
        self.assertEqual(record["cyclone_safety_score"], 50.0)
        self.assertEqual(meta["seismic_null"], 1)

    def test_mmi_uses_class_midpoints(self):
        records, _ = hz.parse_exposure([
            exposure_row(**{
                "EX_E01_POP_2025": "0",
                "EX_E02_POP_2025": "0",
                "EX_E04_POP_2025": "0",
                "EX_E05_POP_2025": "0",
                "EX_E06_POP_2025": "50000",
                "EX_E07_POP_2025": "0",
                "EX_E08_POP_2025": "50000",
                "EX_E09_POP_2025": "0",
                "EX_E10_POP_2025": "0",
            })
        ])
        self.assertEqual(records[0]["seismic_exposure_raw"], 7.0)
        self.assertEqual(records[0]["seismic_safety_score"], 20.0)

    def test_missing_id_rows_are_skipped(self):
        records, _ = hz.parse_exposure([
            exposure_row(**{"ID_UC_G0": ""}),
            exposure_row(**{"ID_UC_G0": "3"}),
        ])
        self.assertEqual(
            [record["urban_centre_id"] for record in records], [3]
        )

    def test_duplicate_urban_centre_ids_are_collapsed(self):
        records, meta = hz.parse_exposure([
            exposure_row(**{"EX_100_SHP_2025": "10"}),
            exposure_row(**{"EX_100_SHP_2025": "40"}),
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["flood_exposure_raw"], 40.0)
        self.assertEqual(meta["duplicates"], 1)

    def test_uses_2025_values_and_ignores_2030(self):
        rows = [exposure_row(**{
            "EX_100_SHP_2025": "10",
            "EX_100_SHP_2030": "90",
        })]
        records, meta = hz.parse_exposure(rows)
        self.assertEqual(meta["columns"]["flood_river"], "EX_100_SHP_2025")
        self.assertEqual(records[0]["flood_exposure_raw"], 10.0)

    def test_missing_required_column_raises(self):
        row = exposure_row()
        del row["EX_100_SHP_2025"]
        with self.assertRaises(ValueError) as caught:
            hz.parse_exposure([row])
        self.assertIn("flood_river", str(caught.exception))

    def test_missing_mmi_class_column_raises(self):
        row = exposure_row()
        del row["EX_E07_POP_2025"]
        with self.assertRaises(ValueError) as caught:
            hz.parse_exposure([row])
        self.assertIn("mmi_7", str(caught.exception))

    def test_empty_input_has_empty_metadata(self):
        records, meta = hz.parse_exposure([])
        self.assertEqual(records, [])
        self.assertEqual(meta["duplicates"], 0)
        self.assertEqual(meta["seismic_null"], 0)
        self.assertEqual(meta["columns"], {})

    def test_provenance_marks_the_non_heat_scope(self):
        records, _ = hz.parse_exposure([exposure_row()])
        source = records[0]["source"]
        for token in ("GHS-UCDB R2024A", "CC BY 4.0", "flood",
                      "earthquake", "cyclone"):
            with self.subTest(token=token):
                self.assertIn(token, source)


class SchemaTests(unittest.TestCase):
    def test_table_and_primary_key_are_declared(self):
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS urban_centre_hazard_exposure",
            hz.SCHEMA_SQL,
        )
        self.assertEqual(hz.SCHEMA_SQL.count("BIGINT PRIMARY KEY"), 1)
        self.assertIn("urban_centre_hazard_exposure_score_idx", hz.SCHEMA_SQL)

    def test_all_required_columns_are_declared(self):
        for column in (
            "urban_centre_id",
            "flood_exposure_raw",
            "seismic_exposure_raw",
            "cyclone_exposure_raw",
            "flood_safety_score",
            "seismic_safety_score",
            "cyclone_safety_score",
            "hazard_safety_score",
            "source",
            "source_url",
            "observed_period",
            "updated_at",
        ):
            with self.subTest(column=column):
                self.assertIn(column, hz.SCHEMA_SQL)

    def test_insert_statement_matches_schema(self):
        self.assertEqual(hz.INSERT_SQL.count("%s"), 11)
        self.assertIn("urban_centre_hazard_exposure", hz.INSERT_SQL)

    def test_heat_and_conflict_columns_are_not_loaded(self):
        for sql in (hz.SCHEMA_SQL, hz.INSERT_SQL):
            for token in ("heat", "heatwave", "drought", "cold_wave",
                          "wildfire", "volcanic", "ACLED", "conflict"):
                with self.subTest(token=token):
                    self.assertNotIn(token, sql.lower())

    def test_selected_flood_family_is_the_100_year_extent(self):
        self.assertEqual(hz.FLOOD_RIVER_PREFIX, "EX_100_SHP_")
        self.assertEqual(hz.FLOOD_COASTAL_PREFIX, "EX_CF1_SHP_")
        self.assertNotEqual(hz.FLOOD_RIVER_PREFIX, "EX_010_SHP_")


class ThemeConfigTests(unittest.TestCase):
    def test_theme_url_is_the_v1_2_exposure_archive(self):
        self.assertTrue(hz.THEME_URL.endswith("_V1_2.zip"))
        self.assertIn("GHS_UCDB_THEME_EXPOSURE_GLOBE_R2024A", hz.THEME_URL)
        self.assertEqual(
            hz.CSV_NAME, "GHS_UCDB_THEME_EXPOSURE_GLOBE_R2024A.csv"
        )

    def test_ensure_csv_reuses_cached_file_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            expected = Path(folder) / hz.THEME_FOLDER / hz.CSV_NAME
            expected.parent.mkdir(parents=True)
            expected.write_text("ID_UC_G0\n", encoding="latin-1")
            self.assertEqual(hz.ensure_csv(folder), expected)

    def test_ensure_csv_extracts_a_local_archive_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            theme_folder = Path(folder) / hz.THEME_FOLDER
            theme_folder.mkdir(parents=True)
            archive_path = theme_folder / f"{hz.THEME_FOLDER}.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(hz.CSV_NAME, "ID_UC_G0\n1\n")
            path = hz.ensure_csv(folder)
            self.assertEqual(path, theme_folder / hz.CSV_NAME)
            self.assertEqual(
                path.read_text(encoding="latin-1"), "ID_UC_G0\n1\n"
            )


if __name__ == "__main__":
    unittest.main()
