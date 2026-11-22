"""Offline tests for the published walkability-score loader."""

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_walkability as bw  # noqa: E402
import place_profiles as pp  # noqa: E402


def destination(destination_id, name, iso2, lat, lon):
    return bw.Destination(destination_id, name, iso2, lat, lon)


def score_row(city, country, score=75, lat=0.0, lon=0.0, rank=100):
    return bw.ScoreRow(
        city=city,
        region=None,
        country=country,
        score=score,
        centre_score=score,
        rank_position=rank,
        population=1_000_000,
        url=None,
        centre_lat=lat,
        centre_lon=lon,
    )


class ReadScoresTests(unittest.TestCase):
    def test_reads_and_skips_incomplete_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cities.csv"
            with path.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "rank", "city", "region", "country", "population", "score",
                    "centre_score", "centre_lat", "centre_lon", "url",
                ])
                writer.writerow([1, "Paris", "Île-de-France", "France", 2_000_000, 99, 100, 48.86, 2.35, "u"])
                writer.writerow([2, "Nowhere", "", "France", "", "", "", "", "", ""])
            rows = bw.read_scores(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].city, "Paris")
        self.assertEqual(rows[0].score, 99)
        self.assertEqual(rows[0].centre_score, 100)


class MatchTests(unittest.TestCase):
    COUNTRIES = {"united states": "US", "germany": "DE", "france": "FR"}

    def test_exact_name_and_country(self):
        rows = [score_row("Houston", "United States", 79, 29.7555, -95.3736)]
        matches, unmatched = bw.match_scores(
            rows,
            [destination("gn-hou", "Houston", "US", 29.7604, -95.3698)],
            {},
            self.COUNTRIES,
        )
        self.assertEqual(matches["gn-hou"].row.score, 79)
        self.assertEqual(matches["gn-hou"].match_kind, "name")
        self.assertEqual(unmatched, [])

    def test_alias_resolves_a_translated_name(self):
        rows = [score_row("Cologne", "Germany", 83, 50.94, 6.96)]
        cologne = destination("gn-koeln", "Köln", "DE", 50.9375, 6.9603)
        matches, _ = bw.match_scores(
            rows, [cologne], {("cologne", "DE"): [cologne]}, self.COUNTRIES
        )
        self.assertEqual(matches["gn-koeln"].match_kind, "alias")

    def test_country_must_agree(self):
        rows = [score_row("Paris", "United States", 50, 33.0, -95.0)]
        matches, unmatched = bw.match_scores(
            rows,
            [destination("gn-paris-fr", "Paris", "FR", 48.85, 2.35)],
            {},
            self.COUNTRIES,
        )
        self.assertEqual(matches, {})
        self.assertEqual(len(unmatched), 1)

    def test_duplicate_name_picks_the_nearest(self):
        rows = [score_row("Springfield", "United States", 60, 37.2, -93.3)]
        east = destination("gn-east", "Springfield", "US", 42.1, -72.5)
        west = destination("gn-west", "Springfield", "US", 37.2, -93.3)
        matches, _ = bw.match_scores(rows, [east, west], {}, self.COUNTRIES)
        self.assertIn("gn-west", matches)
        self.assertNotIn("gn-east", matches)

    def test_far_name_match_is_rejected(self):
        rows = [score_row("Berlin", "Germany", 90, 52.52, 13.40)]
        far = destination("gn-far", "Berlin", "DE", 40.0, 13.0)
        matches, unmatched = bw.match_scores(rows, [far], {}, self.COUNTRIES)
        self.assertEqual(matches, {})
        self.assertEqual(len(unmatched), 1)


class MetricWiringTests(unittest.TestCase):
    def test_metric_is_in_the_profile_query(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("LEFT JOIN walkability_scores v", sql)
        self.assertIn("AS walkability_score__pct_global", sql)

    def test_direction_and_unit(self):
        metric = next(m for m in pp.METRICS if m.key == "walkability_score")
        self.assertEqual(metric.better, "higher")
        self.assertEqual(metric.unit, "score 0-100")

    def test_street_grid_proxy_is_no_longer_exposed(self):
        keys = {metric.key for metric in pp.METRICS}
        self.assertNotIn("street_intersections_per_km2", keys)
        self.assertNotIn("dead_end_street_share", keys)


if __name__ == "__main__":
    unittest.main()
