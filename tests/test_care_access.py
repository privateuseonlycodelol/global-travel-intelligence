"""Offline unit tests for bootstrap_care_access.py."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np
import rasterio
from rasterio.transform import from_origin

import bootstrap_care_access as ca

NODATA = -9999.0


def write_raster(
    path: Path,
    values: np.ndarray,
    *,
    left: float = -1.0,
    top: float = 1.0,
    res: float = 0.01,
) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(left, top, res, res),
        nodata=NODATA,
    ) as dataset:
        dataset.write(values.astype("float32"), 1)


def polygon(minx: float, miny: float, maxx: float, maxy: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [minx, miny],
                [maxx, miny],
                [maxx, maxy],
                [minx, maxy],
                [minx, miny],
            ]
        ],
    }


class CareAccessAnchorTests(unittest.TestCase):
    def test_council_anchor_points(self):
        self.assertAlmostEqual(ca.compute_care_access_score(10.0), 100.0)
        self.assertAlmostEqual(ca.compute_care_access_score(35.0), 50.0)
        self.assertAlmostEqual(ca.compute_care_access_score(60.0), 0.0)

    def test_linear_between_anchors(self):
        self.assertAlmostEqual(ca.compute_care_access_score(20.0), 80.0)
        self.assertAlmostEqual(ca.compute_care_access_score(45.0), 30.0)

    def test_saturates_at_each_end(self):
        for minutes in (0.0, 1.0, 5.0, 9.9, 10.0):
            self.assertEqual(ca.compute_care_access_score(minutes), 100.0)
        for minutes in (60.0, 60.1, 90.0, 500.0, 20800.0):
            self.assertEqual(ca.compute_care_access_score(minutes), 0.0)

    def test_monotonic_non_increasing(self):
        scores = [ca.compute_care_access_score(m / 2.0) for m in range(0, 161)]
        for left, right in zip(scores, scores[1:]):
            self.assertGreaterEqual(left, right)

    def test_invalid_values_return_none(self):
        for value in (None, float("nan"), float("inf"), -0.1, -1.0, -9999.0):
            self.assertIsNone(ca.compute_care_access_score(value))


class TravelTimeValidityTests(unittest.TestCase):
    def test_nodata_and_negative_are_ignored(self):
        values = np.array([[5.0, -9999.0], [-1.0, 10.0], [60.0, np.nan]])
        self.assertEqual(ca.median_travel_time(values, NODATA), 10.0)

    def test_negative_values_dropped_even_without_nodata_argument(self):
        self.assertEqual(ca.median_travel_time([-9999.0, 5.0, 7.0]), 6.0)

    def test_zero_and_positive_values_count(self):
        self.assertEqual(ca.median_travel_time([0.0, 0.0, 30.0], NODATA), 0.0)

    def test_all_invalid_returns_none(self):
        values = np.array([-9999.0, -1.0, np.nan])
        self.assertIsNone(ca.median_travel_time(values, NODATA))
        self.assertIsNone(ca.median_travel_time([], NODATA))


class ArchiveExtractionTests(unittest.TestCase):
    def test_zip_archive_extracts_geotiff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tif = root / "source.tif"
            values = np.arange(16, dtype="float32").reshape(4, 4)
            write_raster(tif, values)
            archive = root / "weiss.download"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
                handle.write(tif, "nested/weiss.geotiff")
                handle.writestr("readme.txt", "metadata")
            target = root / "out" / "weiss.tif"
            self.assertEqual(ca.materialize_geotiff(archive, target), target)
            with rasterio.open(target) as dataset:
                np.testing.assert_allclose(dataset.read(1), values)

    def test_direct_geotiff_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tif = root / "direct.tif"
            values = np.full((3, 3), 12.0, dtype="float32")
            write_raster(tif, values)
            target = root / "nested" / "out.tif"
            self.assertEqual(ca.materialize_geotiff(tif, target), target)
            with rasterio.open(target) as dataset:
                np.testing.assert_allclose(dataset.read(1), values)

    def test_direct_geotiff_with_download_extension_is_accepted(self):
        # The server may return a bare GeoTIFF instead of a zip; the cached
        # file name has no .tif suffix, so detection must use magic bytes.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "weiss.download"
            values = np.full((2, 2), 9.0, dtype="float32")
            write_raster(source, values)
            target = root / "out.tif"
            ca.materialize_geotiff(source, target)
            with rasterio.open(target) as dataset:
                np.testing.assert_allclose(dataset.read(1), values)

    def test_largest_geotiff_member_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = root / "small.tif"
            large = root / "large.tif"
            write_raster(small, np.zeros((1, 1), dtype="float32"))
            write_raster(large, np.ones((2, 3), dtype="float32"))
            archive = root / "pack.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.write(small, "small.tif")
                handle.write(large, "large.tif")
            target = root / "out.tif"
            ca.materialize_geotiff(archive, target)
            with rasterio.open(target) as dataset:
                self.assertEqual(dataset.shape, (2, 3))

    def test_archive_without_geotiff_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "pack.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("readme.txt", "no raster here")
            with self.assertRaises(ValueError):
                ca.materialize_geotiff(archive, root / "out.tif")

    def test_unknown_file_type_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.txt"
            path.write_text("hello", encoding="utf-8")
            with self.assertRaises(ValueError):
                ca.materialize_geotiff(path, Path(tmp) / "out.tif")

    def test_ensure_geotiff_uses_cached_tif_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / ca.GEOTIFF_NAME
            write_raster(target, np.ones((2, 2), dtype="float32"))
            with mock.patch.object(
                ca, "_download_file", side_effect=AssertionError("network used")
            ):
                out = ca.ensure_geotiff(root, url="https://invalid.example")
            self.assertEqual(out, target)

    def test_ensure_geotiff_extracts_cached_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tif = root / "inside.tif"
            write_raster(tif, np.full((2, 2), 7.0, dtype="float32"))
            archive = root / ca.ARCHIVE_NAME
            with zipfile.ZipFile(archive, "w") as handle:
                handle.write(tif, "layer.tif")
            with mock.patch.object(
                ca, "_download_file", side_effect=AssertionError("network used")
            ):
                out = ca.ensure_geotiff(root)
            self.assertEqual(out, root / ca.GEOTIFF_NAME)
            with rasterio.open(out) as dataset:
                self.assertEqual(float(dataset.read(1)[0, 0]), 7.0)

    def test_ensure_geotiff_handles_direct_tiff_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            download = root / ca.ARCHIVE_NAME
            write_raster(download, np.full((2, 2), 4.0, dtype="float32"))
            with mock.patch.object(
                ca, "_download_file", side_effect=AssertionError("network used")
            ):
                out = ca.ensure_geotiff(root)
            self.assertEqual(out, root / ca.GEOTIFF_NAME)
            with rasterio.open(out) as dataset:
                self.assertEqual(float(dataset.read(1)[0, 0]), 4.0)


class RasterSamplingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "base.tif"
        columns = np.array([5, 10, 15, 20, 25, 30, 35, 40], dtype="float32")
        self.values = np.tile(columns, (8, 1))
        write_raster(self.path, self.values)

    def tearDown(self):
        self._tmp.cleanup()

    def test_point_sample_uses_3x3_median(self):
        with rasterio.open(self.path) as dataset:
            self.assertEqual(ca.sample_point(dataset, 0.975, -0.975), 15.0)

    def test_point_sample_handles_nodata_and_negative(self):
        nodata_path = self.root / "nodata.tif"
        write_raster(nodata_path, np.full((4, 4), NODATA))
        with rasterio.open(nodata_path) as dataset:
            self.assertIsNone(ca.sample_point(dataset, 0.985, -0.985))
        negative_path = self.root / "negative.tif"
        write_raster(negative_path, np.full((4, 4), -1.0))
        with rasterio.open(negative_path) as dataset:
            self.assertIsNone(ca.sample_point(dataset, 0.985, -0.985))

    def test_point_outside_raster_is_none(self):
        with rasterio.open(self.path) as dataset:
            self.assertIsNone(ca.sample_point(dataset, 46.0, 10.0))

    def test_polygon_median_uses_pixels_inside_only(self):
        geom = polygon(-0.980, 0.950, -0.950, 0.980)
        with rasterio.open(self.path) as dataset:
            self.assertEqual(ca.median_travel_time_in_geometry(dataset, geom), 20.0)

    def test_polygon_median_ignores_nodata_and_negative(self):
        values = self.values.copy()
        values[:, 3] = NODATA
        values[:, 4] = NODATA
        geom = polygon(-0.980, 0.950, -0.950, 0.980)
        nodata_path = self.root / "poly_nodata.tif"
        write_raster(nodata_path, values)
        with rasterio.open(nodata_path) as dataset:
            self.assertEqual(ca.median_travel_time_in_geometry(dataset, geom), 15.0)

        values[:, 2] = -1.0
        negative_path = self.root / "poly_negative.tif"
        write_raster(negative_path, values)
        with rasterio.open(negative_path) as dataset:
            self.assertIsNone(ca.median_travel_time_in_geometry(dataset, geom))

    def test_tiny_polygon_falls_back_to_point(self):
        geom = polygon(-0.974, 0.972, -0.972, 0.974)
        with rasterio.open(self.path) as dataset:
            self.assertIsNone(ca.median_travel_time_in_geometry(dataset, geom))
            median, method = ca.sample_urban_centre(
                dataset, geom, 0.975, -0.975
            )
        self.assertEqual(method, "point_fallback")
        self.assertEqual(median, 15.0)

    def test_missing_geometry_uses_point_sample(self):
        with rasterio.open(self.path) as dataset:
            median, method = ca.sample_urban_centre(
                dataset, None, 0.975, -0.975
            )
        self.assertEqual(method, "point_3x3")
        self.assertEqual(median, 15.0)

    def test_polygon_outside_raster_is_none(self):
        geom = polygon(10.0, 10.0, 11.0, 11.0)
        with rasterio.open(self.path) as dataset:
            self.assertIsNone(ca.median_travel_time_in_geometry(dataset, geom))

    def test_geometry_bounds_multipolygon(self):
        geom = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[1.0, 2.0], [3.0, 2.0], [3.0, 4.0], [1.0, 4.0], [1.0, 2.0]]],
                [
                    [
                        [-1.0, -2.0],
                        [-0.5, -2.0],
                        [-0.5, -1.5],
                        [-1.0, -1.5],
                        [-1.0, -2.0],
                    ]
                ],
            ],
        }
        self.assertEqual(ca.geometry_bounds(geom), (-1.0, -2.0, 3.0, 4.0))


class SchemaTests(unittest.TestCase):
    def test_schema_has_table_primary_key_and_reference(self):
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS destination_care_access", ca.SCHEMA_SQL
        )
        self.assertIn("PRIMARY KEY", ca.SCHEMA_SQL)
        self.assertIn("REFERENCES destinations(destination_id)", ca.SCHEMA_SQL)

    def test_schema_has_all_required_columns(self):
        for column in (
            "destination_id",
            "urban_centre_id",
            "median_travel_min",
            "care_access_score",
            "sample_method",
            "source",
            "source_url",
            "observed_period",
            "updated_at",
        ):
            self.assertIn(column, ca.SCHEMA_SQL)

    def test_insert_matches_schema_columns(self):
        for column in (
            "destination_id",
            "urban_centre_id",
            "median_travel_min",
            "care_access_score",
            "sample_method",
            "source",
            "source_url",
            "observed_period",
        ):
            self.assertIn(column, ca.INSERT_SQL)

    def test_source_metadata(self):
        self.assertIn("malariaatlas.org", ca.SOURCE_URL)
        self.assertIn(
            "2020_motorized_travel_time_to_healthcare", ca.SOURCE_URL
        )
        self.assertEqual(ca.OBSERVED_PERIOD, "2020")
        self.assertTrue(ca.SOURCE)


if __name__ == "__main__":
    unittest.main()
