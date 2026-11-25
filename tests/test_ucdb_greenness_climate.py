"""Offline tests for the GHSL UCDB greenness and climate loader."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_ucdb_greenness_climate as uc  # noqa: E402


def greenness_row(**overrides):
    row = {
        "ID_UC_G0": "1",
        "GC_UCN_MAI_2025": "Apia",
        "GR_AVG_GRN_2025": "0.325",
        "GR_CTH_AVG_2020": "12.5",
    }
    row.update(overrides)
    return row


def climate_row(**overrides):
    row = {
        "ID_UC_G0": "1",
        "GC_UCN_MAI_2025": "Apia",
        "CL_B01_CUR_2010": "26.56",
        "CL_B12_CUR_2010": "1905.158",
        "CL_UTC_T32_2020": "101",
        "CL_KOP_CUR_2025": "1",
    }
    row.update(overrides)
    return row


class NumberParsingTests(unittest.TestCase):
    def test_number_accepts_numeric_strings_and_floats(self):
        self.assertEqual(uc.number("0.42"), 0.42)
        self.assertEqual(uc.number("  -3.5  "), -3.5)
        self.assertEqual(uc.number("1e3"), 1000.0)
        self.assertEqual(uc.number(7), 7.0)

    def test_number_rejects_missing_and_non_numeric(self):
        for value in ("", "-", "--", "NA", "n/a", "N/A", "n.a.", "null",
                      "None", "nan", "#N/A", "not a number", None):
            with self.subTest(value=value):
                self.assertIsNone(uc.number(value))

    def test_number_rejects_infinite(self):
        self.assertIsNone(uc.number("inf"))
        self.assertIsNone(uc.number("-inf"))
        self.assertIsNone(uc.number("NaN"))

    def test_integer_rounds_and_rejects_missing(self):
        self.assertEqual(uc.integer("7"), 7)
        self.assertEqual(uc.integer("7.6"), 8)
        self.assertIsNone(uc.integer("NA"))
        self.assertIsNone(uc.integer(None))


class NdviScoreTests(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(uc.ndvi_score_0_100(None))

    def test_floor_and_ceiling(self):
        self.assertEqual(uc.ndvi_score_0_100(-0.5), 0.0)
        self.assertEqual(uc.ndvi_score_0_100(0.0), 0.0)
        self.assertEqual(uc.ndvi_score_0_100(0.05), 0.0)
        self.assertEqual(uc.ndvi_score_0_100(0.325), 50.0)
        self.assertEqual(uc.ndvi_score_0_100(0.6), 100.0)
        self.assertEqual(uc.ndvi_score_0_100(0.79), 100.0)

    def test_scores_stay_in_bounds(self):
        for hundredths in range(-100, 101):
            ndvi = hundredths / 100.0
            score = uc.ndvi_score_0_100(ndvi)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

    def test_scores_are_monotonic(self):
        values = [-0.2, 0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0]
        scores = [uc.ndvi_score_0_100(value) for value in values]
        for left, right in zip(scores, scores[1:]):
            self.assertLessEqual(left, right)

    def test_score_is_rounded_to_two_decimals(self):
        expected = round(100.0 * ((0.333 - uc.NDVI_FLOOR) / uc.NDVI_SPAN), 2)
        self.assertEqual(uc.ndvi_score_0_100(0.333), expected)


class LatestColumnTests(unittest.TestCase):
    def test_picks_latest_year(self):
        column, year = uc.latest_column(
            ["ID_UC_G0", "GR_AVG_GRN_2020", "GR_AVG_GRN_2025"],
            uc.NDVI_PREFIXES,
        )
        self.assertEqual(column, "GR_AVG_GRN_2025")
        self.assertEqual(year, 2025)

    def test_prefers_ndv_spelling_over_grn_for_same_year(self):
        column, year = uc.latest_column(
            ["GR_AVG_GRN_2025", "GR_AVG_NDV_2025"], uc.NDVI_PREFIXES
        )
        self.assertEqual(column, "GR_AVG_NDV_2025")
        self.assertEqual(year, 2025)

    def test_ignores_inconsistent_area_columns(self):
        column, year = uc.latest_column(
            ["GR_SQM_GRN_2025", "GR_SHB_GRN_2025", "GR_AVG_GRN_2025"],
            uc.NDVI_PREFIXES,
        )
        self.assertEqual(column, "GR_AVG_GRN_2025")
        self.assertEqual(year, 2025)

    def test_ignores_placeholder_year_suffix(self):
        column, year = uc.latest_column(
            ["CL_B01_CUR_XXXX", "CL_B01_CUR_2010"],
            uc.TEMPERATURE_PREFIXES,
        )
        self.assertEqual(column, "CL_B01_CUR_2010")
        self.assertEqual(year, 2010)

    def test_missing_prefix_returns_none(self):
        self.assertEqual(
            uc.latest_column(["ID_UC_G0"], uc.NDVI_PREFIXES), (None, None)
        )

    def test_canopy_prefix_is_independent_of_ndvi(self):
        column, year = uc.latest_column(
            ["GR_CTH_AVG_2020", "GR_AVG_GRN_2025"], uc.CANOPY_PREFIXES
        )
        self.assertEqual(column, "GR_CTH_AVG_2020")
        self.assertEqual(year, 2020)


class ReadRowsTests(unittest.TestCase):
    def test_reads_bom_header_and_pads_missing_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "theme.csv"
            path.write_bytes(
                b"\xef\xbb\xbfID_UC_G0,GR_AVG_GRN_2025,GC_CNT_GAD_2025\r\n"
                b"1,0.42,M\xe9xico\r\n"
                b"\r\n"
                b"2\r\n"
            )
            rows = uc.read_rows(path)
        self.assertEqual(len(rows), 2)
        self.assertIn("ID_UC_G0", rows[0])
        self.assertEqual(rows[0]["GC_CNT_GAD_2025"], "México")
        self.assertEqual(rows[1]["GR_AVG_GRN_2025"], "")
        self.assertEqual(rows[1]["GC_CNT_GAD_2025"], "")

    def test_empty_file_returns_no_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "theme.csv"
            path.write_text("ID_UC_G0,GR_AVG_GRN_2025\n", encoding="latin-1")
            self.assertEqual(uc.read_rows(path), [])


class DedupeTests(unittest.TestCase):
    def test_last_duplicate_wins(self):
        records = [
            {"urban_centre_id": 1, "value": "first"},
            {"urban_centre_id": 2, "value": "other"},
            {"urban_centre_id": 1, "value": "last"},
        ]
        deduped, duplicates = uc.dedupe_by_id(records)
        self.assertEqual(duplicates, 1)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["value"], "last")


class GreennessParsingTests(unittest.TestCase):
    def test_parses_ndvi_score_canopy_and_provenance(self):
        records, meta = uc.parse_greenness([greenness_row()])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["urban_centre_id"], 1)
        self.assertEqual(record["ndvi"], 0.325)
        self.assertEqual(record["ndvi_score_0_100"], 50.0)
        self.assertEqual(record["canopy_height_m"], 12.5)
        self.assertIn("NDVI 2025", record["observed_period"])
        self.assertIn("canopy height 2020", record["observed_period"])
        self.assertIn("CC BY 4.0", record["source"])
        self.assertTrue(record["source_url"].startswith("https://"))
        self.assertEqual(meta["ndvi_column"], "GR_AVG_GRN_2025")
        self.assertEqual(meta["canopy_column"], "GR_CTH_AVG_2020")

    def test_na_and_non_numeric_values_become_null(self):
        records, _ = uc.parse_greenness([
            greenness_row(**{
                "ID_UC_G0": "2",
                "GR_AVG_GRN_2025": "NA",
                "GR_CTH_AVG_2020": "not-a-number",
            })
        ])
        record = records[0]
        self.assertIsNone(record["ndvi"])
        self.assertIsNone(record["ndvi_score_0_100"])
        self.assertIsNone(record["canopy_height_m"])

    def test_negative_ndvi_is_clamped_to_zero(self):
        records, _ = uc.parse_greenness([
            greenness_row(**{"GR_AVG_GRN_2025": "-0.0407"})
        ])
        self.assertEqual(records[0]["ndvi_score_0_100"], 0.0)

    def test_missing_id_rows_are_skipped(self):
        records, _ = uc.parse_greenness([
            greenness_row(**{"ID_UC_G0": ""}),
            greenness_row(**{"ID_UC_G0": "3"}),
        ])
        self.assertEqual([row["urban_centre_id"] for row in records], [3])

    def test_duplicate_urban_centre_ids_are_collapsed(self):
        records, meta = uc.parse_greenness([
            greenness_row(**{"GR_AVG_GRN_2025": "0.1"}),
            greenness_row(**{"GR_AVG_GRN_2025": "0.5"}),
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["ndvi"], 0.5)
        self.assertEqual(meta["duplicates"], 1)

    def test_latest_ndvi_column_is_used(self):
        rows = [{
            "ID_UC_G0": "4",
            "GR_AVG_GRN_2020": "0.1",
            "GR_AVG_GRN_2025": "0.6",
            "GR_CTH_AVG_2020": "8.0",
        }]
        records, meta = uc.parse_greenness(rows)
        self.assertEqual(meta["ndvi_year"], 2025)
        self.assertEqual(records[0]["ndvi"], 0.6)
        self.assertEqual(records[0]["ndvi_score_0_100"], 100.0)

    def test_empty_input_has_empty_metadata(self):
        records, meta = uc.parse_greenness([])
        self.assertEqual(records, [])
        self.assertEqual(meta["duplicates"], 0)
        self.assertIsNone(meta["ndvi_column"])


class KoppenTests(unittest.TestCase):
    def test_known_codes(self):
        self.assertEqual(uc.koppen_class("1"), ("Af", "Tropical, rainforest"))
        self.assertEqual(
            uc.koppen_class("15"),
            ("Cfb", "Temperate, no dry season, warm summer"),
        )
        self.assertEqual(uc.koppen_class("29"), ("ET", "Polar, tundra"))

    def test_missing_and_unknown_codes(self):
        self.assertEqual(uc.koppen_class("NA"), (None, None))
        self.assertEqual(uc.koppen_class(""), (None, None))
        self.assertEqual(uc.koppen_class("99"), (None, None))
        self.assertEqual(uc.koppen_class(None), (None, None))


class ClimateParsingTests(unittest.TestCase):
    def test_parses_bioclimatic_utci_and_koppen(self):
        records, meta = uc.parse_climate([climate_row()])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["mean_annual_temp_c"], 26.56)
        self.assertEqual(record["annual_precip_mm"], 1905.158)
        self.assertEqual(record["utci_over_32_days"], 101.0)
        self.assertEqual(record["koppen_code"], "Af")
        self.assertEqual(record["koppen_label"], "Tropical, rainforest")
        self.assertIn("BIO1/BIO12 2010", record["observed_period"])
        self.assertIn("UTCI 2020", record["observed_period"])
        self.assertIn("Koppen-Geiger 2025", record["observed_period"])
        self.assertIn("CC BY 4.0", record["source"])
        self.assertEqual(meta["temperature_column"], "CL_B01_CUR_2010")
        self.assertEqual(meta["precipitation_column"], "CL_B12_CUR_2010")
        self.assertEqual(meta["utci_column"], "CL_UTC_T32_2020")
        self.assertEqual(meta["koppen_column"], "CL_KOP_CUR_2025")

    def test_na_values_become_null_without_dropping_the_row(self):
        records, _ = uc.parse_climate([
            climate_row(**{
                "CL_B01_CUR_2010": "NA",
                "CL_B12_CUR_2010": "-",
                "CL_UTC_T32_2020": "",
                "CL_KOP_CUR_2025": "-",
            })
        ])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertIsNone(record["mean_annual_temp_c"])
        self.assertIsNone(record["annual_precip_mm"])
        self.assertIsNone(record["utci_over_32_days"])
        self.assertIsNone(record["koppen_code"])
        self.assertIsNone(record["koppen_label"])

    def test_duplicate_urban_centre_ids_are_collapsed(self):
        records, meta = uc.parse_climate([
            climate_row(**{"CL_B01_CUR_2010": "10"}),
            climate_row(**{"CL_B01_CUR_2010": "20"}),
        ])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["mean_annual_temp_c"], 20.0)
        self.assertEqual(meta["duplicates"], 1)

    def test_missing_id_rows_are_skipped(self):
        records, _ = uc.parse_climate([
            climate_row(**{"ID_UC_G0": "NA"}),
            climate_row(**{"ID_UC_G0": "9"}),
        ])
        self.assertEqual([row["urban_centre_id"] for row in records], [9])

    def test_latest_historical_epoch_is_used(self):
        rows = [{
            "ID_UC_G0": "10",
            "CL_B01_CUR_2000": "9.0",
            "CL_B01_CUR_2010": "10.0",
            "CL_B12_CUR_2000": "700",
            "CL_B12_CUR_2010": "800",
            "CL_UTC_T32_2010": "1",
            "CL_UTC_T32_2020": "2",
            "CL_KOP_CUR_2025": "15",
        }]
        records, meta = uc.parse_climate(rows)
        self.assertEqual(meta["temperature_year"], 2010)
        self.assertEqual(meta["utci_year"], 2020)
        self.assertEqual(records[0]["mean_annual_temp_c"], 10.0)
        self.assertEqual(records[0]["annual_precip_mm"], 800.0)
        self.assertEqual(records[0]["utci_over_32_days"], 2.0)
        self.assertEqual(records[0]["koppen_code"], "Cfb")

    def test_empty_input_has_empty_metadata(self):
        records, meta = uc.parse_climate([])
        self.assertEqual(records, [])
        self.assertEqual(meta["duplicates"], 0)
        self.assertIsNone(meta["temperature_column"])


class SchemaTests(unittest.TestCase):
    def test_both_tables_and_keys_are_declared(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS urban_centre_greenness", uc.SCHEMA_SQL)
        self.assertIn("CREATE TABLE IF NOT EXISTS urban_centre_climate", uc.SCHEMA_SQL)
        self.assertEqual(uc.SCHEMA_SQL.count("BIGINT PRIMARY KEY"), 2)

    def test_greenness_columns_and_provenance(self):
        for column in (
            "ndvi",
            "ndvi_score_0_100",
            "canopy_height_m",
            "source",
            "source_url",
            "observed_period",
            "updated_at",
        ):
            with self.subTest(column=column):
                self.assertIn(column, uc.SCHEMA_SQL)

    def test_climate_columns_and_provenance(self):
        for column in (
            "mean_annual_temp_c",
            "annual_precip_mm",
            "utci_over_32_days",
            "koppen_code",
            "koppen_label",
            "source",
            "source_url",
            "observed_period",
            "updated_at",
        ):
            with self.subTest(column=column):
                self.assertIn(column, uc.SCHEMA_SQL)

    def test_insert_statements_match_schema(self):
        self.assertEqual(uc.GREENNESS_INSERT_SQL.count("%s"), 7)
        self.assertEqual(uc.CLIMATE_INSERT_SQL.count("%s"), 9)

    def test_inconsistent_area_columns_are_not_loaded(self):
        for sql in (uc.GREENNESS_INSERT_SQL, uc.CLIMATE_INSERT_SQL):
            self.assertNotIn("GR_SQM", sql)
            self.assertNotIn("GR_SHB", sql)


class ThemeConfigTests(unittest.TestCase):
    def test_theme_urls_point_at_v1_2_archives(self):
        for theme, info in uc.THEMES.items():
            with self.subTest(theme=theme):
                self.assertIn("GHS_UCDB_THEME_", info["url"])
                self.assertTrue(info["url"].endswith("_V1_2.zip"))
                self.assertTrue(info["csv"].endswith(".csv"))

    def test_ensure_csv_reuses_cached_file_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            info = uc.THEMES["GREENNESS"]
            expected = Path(folder) / "GREENNESS" / info["csv"]
            expected.parent.mkdir(parents=True)
            expected.write_text("ID_UC_G0\n", encoding="latin-1")
            self.assertEqual(uc.ensure_csv("GREENNESS", folder), expected)


if __name__ == "__main__":
    unittest.main()
