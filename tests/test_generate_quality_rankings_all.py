"""Offline unit tests for Council Phase 1 rankings & release diagnostics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reports import generate_quality_rankings_all as gq


def make_centre(**overrides):
    row = {
        "name": "Bilbao",
        "country_iso2": "ES",
        "continent": "EU",
        "city_population": 350_000,
        "urban_centre_id": 10,
        "urban_centre_name": "Bilbao",
        "centre_population": 750_000,
        "settlement_class": "ghsl_centre",
        "gdp_per_capita_ppp": 48_000.0,
        "quality_score": 84.5,
        "biophysical_board_score": 82.0,
        "institutional_board_score": 97.0,
        "services_board_score": 78.0,
        "air_score": 88.0,
        "waterfront_score": 90.0,
        "thermal_heat_score": 88.3,
        "ghsl_heat_stress_days": 14.0,
        "worldcover_green_score": None,
        "green_method": "ghsl_ndvi_diagnostic_only",
        "ghsl_ndvi_score": 48.0,
        "safety_prior_score": 100.0,
        "wash_prior_score": 94.0,
        "broadband_score": 78.0,
        "construct_coverage": 0.54,
        "data_completeness": 1.0,
        "tier": "A",
        "uncertainty_tier": "medium",
        "uncertainty_flags": "pm25_point_sample",
        "missing_dimensions": "thermal_cold_severe,urban_form",
        "provenance_summary": "Council Phase 1",
        "walkability_score": 85.0,
        "transit_stops_5km": 300,
        "transit_observation_status": "observed",
        "museum_culture_count": 20,
        "restaurant_count": 400,
        "cuisine_entropy": 2.4,
        "poi_observation_status": "observed",
        "park_count_5km": 40,
        "park_observation_status": "observed",
        "price_level_vs_usd": 0.85,
        "out_of_pocket_pct": 21.0,
        "airport_nearest_km": 8.0,
        "ghsl_hazard_events": 1,
    }
    row.update(overrides)
    return row


class RankingDiagnosticsTests(unittest.TestCase):
    def test_gdp_diagnostics_computes_correlations(self):
        rows = [
            make_centre(name="A", gdp_per_capita_ppp=10_000.0, quality_score=60.0, biophysical_board_score=65.0, institutional_board_score=50.0, broadband_score=40.0),
            make_centre(name="B", gdp_per_capita_ppp=25_000.0, quality_score=72.0, biophysical_board_score=70.0, institutional_board_score=75.0, broadband_score=65.0),
            make_centre(name="C", gdp_per_capita_ppp=55_000.0, quality_score=85.0, biophysical_board_score=75.0, institutional_board_score=95.0, broadband_score=90.0),
        ]
        diag = gq.compute_gdp_diagnostics(rows)
        self.assertGreater(diag["quality_score"], 0.9)
        self.assertGreater(diag["institutional_board"], 0.9)

    def test_pathological_checks_detect_clean_state(self):
        c1 = make_centre(urban_centre_id=1, ghsl_heat_stress_days=14.0, thermal_heat_score=88.3)
        c2 = make_centre(
            urban_centre_id=2,
            country_iso2="CN",
            ghsl_heat_stress_days=180.0,
            thermal_heat_score=0.0,
            restaurant_count=0,
            poi_observation_status="unobserved",
            quality_score=50.0,
        )
        checks = gq.compute_pathological_checks([c1, c2], [c1, c2])
        self.assertEqual(checks["hot_ge150d_in_top_decile"], 0)
        self.assertEqual(checks["hot_ge150d_with_thermal_ge50"], 0)
        self.assertEqual(checks["china_zero_pois_marked_observed"], 0)
        self.assertEqual(checks["unique_ghsl_centres_in_top100"], 2)

    def test_markdown_includes_continent_base_rates_and_diagnostics(self):
        c1 = make_centre(urban_centre_id=1, continent="EU", quality_score=85.0)
        c2 = make_centre(urban_centre_id=2, name="Lagos", country_iso2="NG", continent="AF", tier="C", quality_score=None)
        md = gq.render_markdown(
            [c1, c2], [c1], suppressed_satellites=120, unmatched_towns=2500
        )
        self.assertIn("Continent distribution vs GHSL centre base rates", md)
        self.assertIn("Mandatory release diagnostics", md)
        self.assertIn("suppressed 15 km satellites: **120**", md)
        self.assertIn("Bilbao", md)

    def test_write_csv_separates_centre_and_experimental_columns(self):
        row = make_centre(_rank=1)
        with tempfile.TemporaryDirectory() as tmp:
            p_main = Path(tmp) / "main.csv"
            p_exp = Path(tmp) / "exp.csv"
            gq.write_csv(p_main, [row], gq.CENTRE_FIELDS)
            gq.write_csv(p_exp, [row], gq.EXPERIMENTAL_FIELDS)
            main_header = p_main.read_text().splitlines()[0]
            exp_header = p_exp.read_text().splitlines()[0]
            self.assertNotIn("price_level_vs_usd", main_header)
            self.assertNotIn("walkability_score", main_header)
            self.assertIn("price_level_vs_usd", exp_header)
            self.assertIn("poi_observation_status", exp_header)


if __name__ == "__main__":
    unittest.main()
