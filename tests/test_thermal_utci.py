"""Offline unit tests for bootstrap_thermal_utci.py."""

from __future__ import annotations

import unittest
import numpy as np

import bootstrap_thermal_utci as tu


class UtciPolynomialTests(unittest.TestCase):
    def test_temperate_reference_near_air_temp(self):
        # At Ta=20C, RH=50%, va=0.5 m/s, dTmrt=0 -> UTCI should be ~20C
        u = tu.utci_approx_vec(np.array([20.0]), np.array([50.0]), np.array([0.5]), d_tmrt=0.0)[0]
        self.assertGreater(u, 18.0)
        self.assertLess(u, 22.0)

    def test_wind_chill_lowers_cold_utci(self):
        calm = tu.utci_approx_vec(np.array([-5.0]), np.array([70.0]), np.array([0.5]), d_tmrt=0.0)[0]
        windy = tu.utci_approx_vec(np.array([-5.0]), np.array([70.0]), np.array([10.0]), d_tmrt=0.0)[0]
        self.assertLess(windy, calm - 5.0)

    def test_humidity_and_sun_raise_hot_utci(self):
        dry_shade = tu.utci_approx_vec(np.array([34.0]), np.array([25.0]), np.array([2.0]), d_tmrt=0.0)[0]
        humid_sun = tu.utci_approx_vec(np.array([34.0]), np.array([75.0]), np.array([2.0]), d_tmrt=10.0)[0]
        self.assertGreater(humid_sun, dry_shade + 4.0)


class ThermalSafetyScoreTests(unittest.TestCase):
    def test_mild_city_scores_high_and_extremes_penalized(self):
        _, _, _, mild = tu.compute_thermal_safety_components(10, 5, 0, 0)
        _, _, _, arctic = tu.compute_thermal_safety_components(0, 210, 0, 45)
        _, _, _, tropical_hot = tu.compute_thermal_safety_components(250, 0, 40, 0)
        self.assertGreater(mild, 90.0)
        self.assertLess(arctic, 50.0)
        self.assertLess(tropical_hot, 35.0)


if __name__ == "__main__":
    unittest.main()
