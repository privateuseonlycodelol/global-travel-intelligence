"""Offline tests for the all-city quality ranking exports."""

import csv
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reports import generate_quality_rankings_all as gq  # noqa: E402


def city(
    destination_id: str,
    name: str,
    *,
    urban_centre_id: int | None,
    population: int,
    completeness: float | None,
    quality: float | None,
    country: str = "FR",
    continent: str | None = "EU",
    **overrides,
) -> dict:
    row = {
        "destination_id": destination_id,
        "name": name,
        "country_iso2": country,
        "continent": continent,
        "city_population": population,
        "centre_population": population,
        "urban_centre_id": urban_centre_id,
        "ghsl_match_status": (
            "matched" if urban_centre_id is not None else "unmatched"
        ),
        "ghsl_centre": f"Centre {urban_centre_id}"
        if urban_centre_id is not None
        else None,
        "quality_score": quality,
        "environment_score": 70.0,
        "climate_score": 80.0,
        "safety_health_score": 90.0,
        "affordability_score": 60.0,
        "connectivity_score": 75.0,
        "data_completeness": completeness,
        "uncertainty_tier": "low" if completeness and completeness >= 0.95
        else "medium",
        "uncertainty_flags": "",
        "missing_dimensions": "" if completeness == 1.0 else "climate",
        "ghsl_ndvi_score": 55.0,
        "ghsl_heat_stress_days": 12.0,
        "ghsl_hazard_events": 2,
        "ghsl_enhanced_score": 65.0,
        "provenance_summary": "Core: test. Supplementary: test. Experimental: test.",
        "walkability_score": 88.0,
        "public_transport_score": 77.0,
        "transit_stops_5km": 120,
        "transit_observation_status": "observed",
        "culture_food_score": 66.0,
        "poi_observation_status": "observed",
        "park_score": 44.0,
        "park_observation_status": "observed",
        "physical_green_score": 33.0,
    }
    row.update(overrides)
    return row


def sample_rows() -> list[dict]:
    return [
        city("paris", "Paris", urban_centre_id=1, population=2_100_000,
             completeness=1.0, quality=80.0, continent="EU"),
        city("paris-small", "Paris Small", urban_centre_id=1,
             population=100_000, completeness=1.0, quality=70.0,
             continent="EU"),
        city("berlin", "Berlin", urban_centre_id=2, population=3_600_000,
             completeness=1.0, quality=85.0, country="DE", continent="EU"),
        city("berlin-small", "Berlin Small", urban_centre_id=2,
             population=50_000, completeness=0.95, quality=50.0,
             country="DE", continent="EU"),
        city("tokyo", "Tokyo", urban_centre_id=4, population=37_000_000,
             completeness=1.0, quality=90.0, country="JP", continent="AS"),
        city("lima", "Lima", urban_centre_id=3, population=9_000_000,
             completeness=0.95, quality=78.0, country="PE", continent="SA"),
        city("delta", "Delta", urban_centre_id=None, population=500_000,
             completeness=0.80, quality=60.0, country="US", continent="NA"),
        city("echo", "Echo", urban_centre_id=None, population=200_000,
             completeness=0.85, quality=None, country="US", continent="NA"),
    ]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class PrincipalCityTests(unittest.TestCase):
    def test_keeps_largest_destination_per_urban_centre(self):
        rows = gq.principal_cities(sample_rows())
        names = [row["name"] for row in rows]
        self.assertEqual(len(rows), 6)
        self.assertIn("Paris", names)
        self.assertNotIn("Paris Small", names)
        self.assertIn("Berlin", names)
        self.assertNotIn("Berlin Small", names)
        self.assertIn("Delta", names)
        self.assertIn("Echo", names)

    def test_unmatched_destinations_are_their_own_principal_city(self):
        rows = gq.principal_cities(sample_rows())
        unmatched = [row for row in rows if row["urban_centre_id"] is None]
        self.assertEqual({row["destination_id"] for row in unmatched},
                         {"delta", "echo"})

    def test_dedup_is_input_order_independent(self):
        first = [row["destination_id"] for row in gq.principal_cities(sample_rows())]
        reversed_rows = list(reversed(sample_rows()))
        second = [
            row["destination_id"]
            for row in gq.principal_cities(reversed_rows)
        ]
        self.assertEqual(set(first), set(second))


class RankingTests(unittest.TestCase):
    def test_rank_only_applies_to_complete_core_rows(self):
        rows = gq.rank_principal_cities(gq.principal_cities(sample_rows()))
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["Tokyo"]["_rank"], 1)
        self.assertEqual(by_name["Berlin"]["_rank"], 2)
        self.assertEqual(by_name["Paris"]["_rank"], 3)
        self.assertEqual(by_name["Lima"]["_rank"], 4)
        self.assertIsNone(by_name["Delta"]["_rank"])
        self.assertIsNone(by_name["Echo"]["_rank"])

    def test_quality_score_orders_ranks(self):
        rows = gq.rank_principal_cities(gq.principal_cities(sample_rows()))
        ranked = [row for row in rows if row["_rank"] is not None]
        scores = [row["quality_score"] for row in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_null_quality_never_gets_a_rank(self):
        rows = gq.rank_principal_cities(
            [city("x", "X", urban_centre_id=None, population=1,
                  completeness=1.0, quality=None)]
        )
        self.assertIsNone(rows[0]["_rank"])

    def test_low_completeness_includes_null(self):
        rows = gq.rank_principal_cities(gq.principal_cities(sample_rows()))
        low = gq.low_completeness(rows)
        self.assertEqual({row["name"] for row in low}, {"Delta", "Echo"})


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.out = Path(self.tempdir.name)
        self.rows = sample_rows()
        self.stats = gq.write_exports(self.rows, self.out)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_all_export_files_are_written(self):
        for name in (
            "quality_all_cities.csv",
            "quality_all_cities_top100.csv",
            "quality_all_cities_top100.md",
            "quality_all_cities_low_completeness.csv",
            "quality_experimental_indicators.csv",
        ):
            self.assertTrue((self.out / name).exists(), name)

    def test_all_cities_csv_has_every_principal_city(self):
        rows = read_csv(self.out / "quality_all_cities.csv")
        self.assertEqual(len(rows), 6)
        self.assertEqual(
            [field for field, _ in gq.QUALITY_FIELDS],
            list(rows[0].keys()),
        )
        by_city = {row["city"]: row for row in rows}
        self.assertEqual(by_city["Tokyo"]["rank"], "1")
        self.assertEqual(by_city["Delta"]["rank"], "")
        self.assertEqual(by_city["Delta"]["data_completeness"], "0.8")

    def test_top100_csv_contains_only_ranked_rows(self):
        rows = read_csv(self.out / "quality_all_cities_top100.csv")
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row["rank"] for row in rows))
        self.assertEqual(rows[0]["city"], "Tokyo")

    def test_low_completeness_csv_is_separate(self):
        rows = read_csv(
            self.out / "quality_all_cities_low_completeness.csv"
        )
        self.assertEqual({row["city"] for row in rows}, {"Delta", "Echo"})
        self.assertTrue(all(float(row["data_completeness"]) < 0.90
                            for row in rows))

    def test_experimental_export_is_separate_from_core(self):
        rows = read_csv(self.out / "quality_experimental_indicators.csv")
        self.assertEqual(len(rows), 6)
        header = list(rows[0].keys())
        self.assertEqual(header, [field for field, _ in gq.EXPERIMENTAL_FIELDS])
        self.assertNotIn("quality_score", header)
        self.assertNotIn("rank", header)
        self.assertIn("walkability_score", header)
        self.assertIn("poi_observation_status", header)
        self.assertEqual(rows[0]["city"], "Tokyo")

    def test_stats(self):
        self.assertEqual(self.stats["principal_cities"], 6)
        self.assertEqual(self.stats["ranked_completeness_ge_0_90"], 4)
        self.assertEqual(self.stats["low_completeness"], 2)
        self.assertEqual(self.stats["experimental_rows"], 6)
        self.assertEqual(self.stats["top"], "Tokyo")
        self.assertEqual(self.stats["continent_count"], 3)

    def test_decimal_values_are_normalised(self):
        rows = [
            city("decimal", "Decimal City", urban_centre_id=None,
                 population=1, completeness=Decimal("1.000"),
                 quality=Decimal("88.5"))
        ]
        normalised = gq.normalise_rows(rows)
        self.assertIsInstance(normalised[0]["quality_score"], float)
        self.assertEqual(normalised[0]["quality_score"], 88.5)


class MarkdownTests(unittest.TestCase):
    def test_continent_table_precedes_top100_table(self):
        rows = gq.rank_principal_cities(gq.principal_cities(sample_rows()))
        ranked = [row for row in rows if row["_rank"] is not None]
        text = gq.markdown(ranked)
        self.assertTrue(text.startswith("# Global City Quality Index"))
        summary_at = text.index("## Continent distribution (top 100)")
        top_at = text.index("## Top 100")
        self.assertLess(summary_at, top_at)
        self.assertIn("| Europe (EU) | 2 | 50.0% |", text)
        self.assertIn("| Asia (AS) | 1 | 25.0% |", text)
        self.assertIn("| Tokyo | JP | Asia (AS) | 90.0 |", text)
        self.assertIn("| 1 | Tokyo", text)
        self.assertIn("| 4 | Lima", text)

    def test_markdown_limits_to_one_hundred_rows(self):
        rows = []
        for index in range(105):
            rows.append(
                city(
                    f"dest-{index}",
                    f"City {index:03d}",
                    urban_centre_id=None,
                    population=100_000 - index,
                    completeness=1.0,
                    quality=100.0 - index * 0.5,
                )
            )
        ranked = gq.rank_principal_cities(gq.principal_cities(rows))
        text = gq.markdown(ranked)
        self.assertIn("| 100 | City 099", text)
        self.assertNotIn("| 101 |", text)


class ContinentTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(gq.continent_label("EU"), "Europe (EU)")
        self.assertEqual(gq.continent_label("ZZ"), "ZZ")
        self.assertEqual(gq.continent_label(None), "Unknown")

    def test_summary_counts_and_tie_order(self):
        rows = [
            {"continent": "EU"},
            {"continent": "EU"},
            {"continent": "AS"},
            {"continent": "SA"},
            {"continent": None},
        ]
        summary = gq.continent_summary(rows)
        self.assertEqual(summary[0][0], "Europe (EU)")
        self.assertEqual(summary[0][1], 2)
        names = [name for name, _, _ in summary]
        self.assertLess(names.index("Asia (AS)"), names.index("South America (SA)"))
        self.assertIn("Unknown", names)

    def test_empty_summary(self):
        self.assertEqual(gq.continent_summary([]), [])


if __name__ == "__main__":
    unittest.main()
