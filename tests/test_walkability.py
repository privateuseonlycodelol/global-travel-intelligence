"""Offline tests for the global street-network (walkability) loader."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap_walkability as bw  # noqa: E402
import place_profiles as pp  # noqa: E402


def centre(uc_id, name, iso3, lat, lon, **kwargs):
    names = kwargs.pop("names", {name})
    return bw.UrbanCentre(
        uc_id=uc_id,
        name=name,
        country_iso3=iso3,
        lat=lat,
        lon=lon,
        names=frozenset(names),
        core_name=name,
        built_up_area_km2=kwargs.pop("built_up_area_km2", 100.0),
        intersection_count=kwargs.pop("intersection_count", 10_000),
        intersections_per_km2=kwargs.pop("intersections_per_km2", 100.0),
        dead_end_share=kwargs.pop("dead_end_share", 0.1),
        four_way_share=kwargs.pop("four_way_share", 0.3),
        street_length_km=kwargs.pop("street_length_km", 1000.0),
        resident_population=kwargs.pop("resident_population", 500_000),
    )


class ProjectionTests(unittest.TestCase):
    def test_mollweide_inverse_matches_known_city(self):
        # Berlin's UCDB centroid in EPSG:54009.
        lon, lat = bw.mollweide_to_lonlat(984727.5438, 6132900.704)
        self.assertAlmostEqual(lat, 52.57, delta=0.1)
        self.assertAlmostEqual(lon, 13.43, delta=0.1)

    def test_haversine(self):
        self.assertAlmostEqual(bw.haversine_km(52.52, 13.405, 48.8566, 2.3522), 878, delta=10)


class BuildCentreTests(unittest.TestCase):
    ROW = {
        "uc_id": "7",
        "core_city": "new_york_city",
        "uc_names": "New York; Newark; Jersey City",
        "country_iso": "USA",
        "built_up_area_m2": "200000000",
        "intersect_count_clean_topo": "40000",
        "prop_deadend": "0.12",
        "prop_4way": "0.31",
        "length_total": "5000000",
        "resident_pop": "18000000",
    }

    def test_density_and_names(self):
        centres = bw.build_centres({7: self.ROW}, {7: (-74.0, 40.7)})
        c = centres[7]
        self.assertEqual(c.built_up_area_km2, 200.0)
        self.assertEqual(c.intersections_per_km2, 200.0)
        self.assertEqual(c.street_length_km, 5000.0)
        self.assertIn("new york city", c.names)
        self.assertIn("newark", c.names)
        self.assertEqual(c.country_iso3, "USA")

    def test_zero_built_up_area_has_no_density(self):
        row = dict(self.ROW, built_up_area_m2="0")
        c = bw.build_centres({7: row}, {7: (-74.0, 40.7)})[7]
        self.assertIsNone(c.intersections_per_km2)


class MatchingTests(unittest.TestCase):
    ISO3 = {"DE": "DEU", "US": "USA"}

    def test_name_match_beats_a_nearer_unnamed_centre(self):
        near = centre(1, "potsdam", "DEU", 52.40, 13.06)
        named = centre(2, "berlin", "DEU", 52.57, 13.43)
        matches = bw.match_destinations(
            [("gn-1", "Berlin", "DE", 52.52, 13.405)], {1: near, 2: named}, self.ISO3
        )
        self.assertEqual(matches["gn-1"].centre.uc_id, 2)
        self.assertEqual(matches["gn-1"].kind, "name")

    def test_nearby_match_inside_twelve_km(self):
        city = centre(1, "berlin", "DEU", 52.57, 13.43)
        matches = bw.match_destinations(
            [("gn-1", "Neukölln", "DE", 52.48, 13.44)], {1: city}, self.ISO3
        )
        self.assertEqual(matches["gn-1"].kind, "nearby")

    def test_far_unnamed_city_is_not_matched(self):
        city = centre(1, "berlin", "DEU", 52.57, 13.43)
        matches = bw.match_destinations(
            [("gn-1", "A Separate Town", "DE", 52.30, 13.05)], {1: city}, self.ISO3
        )
        self.assertEqual(matches, {})

    def test_country_must_agree(self):
        city = centre(1, "berlin", "DEU", 52.57, 13.43)
        matches = bw.match_destinations(
            [("gn-1", "Berlin", "US", 52.52, 13.405)], {1: city}, self.ISO3
        )
        self.assertEqual(matches, {})

    def test_missing_country_only_accepts_a_name_match(self):
        city = centre(1, "berlin", "DEU", 52.57, 13.43)
        matches = bw.match_destinations(
            [("gn-1", "Berlin", "XK", 52.52, 13.405)], {1: city}, {}
        )
        self.assertEqual(matches["gn-1"].kind, "name")

    def test_search_radius(self):
        city = centre(1, "berlin", "DEU", 52.57, 13.43)
        matches = bw.match_destinations(
            [("gn-1", "Berlin", "DE", 51.0, 13.0)], {1: city}, self.ISO3
        )
        self.assertEqual(matches, {})

    def test_name_match_survives_a_distant_urban_centre_centroid(self):
        # Phoenix's GHSL centroid is ~26 km from the GeoNames point, beyond the
        # nearby-match search radius.
        city = centre(1, "phoenix", "USA", 33.59, -112.30)
        matches = bw.match_destinations(
            [("gn-1", "Phoenix", "US", 33.448, -112.074)], {1: city}, self.ISO3
        )
        self.assertEqual(matches["gn-1"].centre.uc_id, 1)
        self.assertEqual(matches["gn-1"].kind, "name")


class MetricWiringTests(unittest.TestCase):
    def test_metrics_are_in_the_profile_query(self):
        sql = pp.build_metric_sql(set(pp.TABLES.values()))
        self.assertIn("LEFT JOIN street_network_metrics w", sql)
        for key in ("street_intersections_per_km2", "dead_end_street_share"):
            self.assertIn(f"AS {key}__pct_global", sql)
        self.assertIn("ORDER BY dead_end_street_share DESC", sql)

    def test_metric_directions(self):
        by_key = {metric.key: metric for metric in pp.METRICS}
        # Connectivity is context, not a good/bad judgement: large blocks can
        # keep it low in a very walkable city.
        self.assertIsNone(by_key["street_intersections_per_km2"].better)
        self.assertEqual(by_key["dead_end_street_share"].better, "lower")


if __name__ == "__main__":
    unittest.main()
