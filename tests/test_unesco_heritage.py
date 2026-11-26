"""Offline unit tests for bootstrap_unesco_heritage.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bootstrap_unesco_heritage as uh


class HeritageScoreTests(unittest.TestCase):
    def test_saturating_anchor_curve(self):
        self.assertEqual(uh.compute_heritage_score(0), 40.0)
        self.assertEqual(uh.compute_heritage_score(1), 70.0)
        self.assertEqual(uh.compute_heritage_score(2), 85.0)
        self.assertEqual(uh.compute_heritage_score(3), 100.0)
        self.assertEqual(uh.compute_heritage_score(12), 100.0)
        self.assertEqual(uh.compute_heritage_score(-1), 40.0)


class CsvParsingTests(unittest.TestCase):
    def test_parses_cultural_and_mixed_skips_natural_and_expands_components(self):
        csv_text = (
            "id_no;name_en;category;date_inscribed;iso_codes;components_list;geo_point_2d\n"
            '100;Historic Centre of Rome;Cultural;1980;IT;"[{""latitude"": 41.89, ""longitude"": 12.49}, {""latitude"": 41.90, ""longitude"": 12.45}]";41.89, 12.49\n'
            "200;Great Barrier Reef;Natural;1981;AU;;-18.28, 147.69\n"
            "300;Machu Picchu;Mixed;1983;PE;;-13.16, -72.54\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "whc.csv"
            p.write_text(csv_text, encoding="utf-8")
            pts = uh.parse_unesco_csv(p)
        self.assertEqual(len(pts), 3)
        self.assertEqual([p["site_id"] for p in pts], ["100", "100", "300"])
        self.assertEqual(pts[2]["category"], "Mixed")
        self.assertAlmostEqual(pts[2]["lat"], -13.16)
        self.assertAlmostEqual(pts[2]["lon"], -72.54)


if __name__ == "__main__":
    unittest.main()
