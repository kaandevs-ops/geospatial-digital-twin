"""
ROADMAP_V7.md Faz C3 (2. dilim) — OSM roads/waterway/water_area köprüsü
==========================================================================

`editor.osm_bridge` için birim testler: yol (LineString) -> `Road` ->
şerit `Mesh3D`, dere (LineString, waterway) -> gömülü şerit, göl/su alanı
(Polygon) -> düz su yüzeyi prizması, ve uçtan uca
`generate_infrastructure_for_collection`.
"""

from __future__ import annotations

import unittest

from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.editor import Road
from harita.editor.osm_bridge import (
    DEFAULT_ROAD_WIDTH_FALLBACK_M,
    DEFAULT_WATER_DEPTH_OFFSET_M,
    DEFAULT_WATERWAY_WIDTH_FALLBACK_M,
    generate_infrastructure_for_collection,
    mesh_for_road,
    mesh_for_water_area,
    road_from_linestring_feature,
    snap_water_area_to_coastline,
    waterway_from_linestring_feature,
)


def _road_feature(highway="residential", width=None, coords=None):
    tags = {"highway": highway, "osm_id": 101}
    if width is not None:
        tags["width"] = width
    return GeoFeature(
        geometry_type="LineString",
        coordinates=coords or [(0.0, 0.0), (10.0, 0.0), (20.0, 5.0)],
        properties={**tags, "__category__": "roads"},
    )


def _waterway_feature(waterway="river", coords=None):
    return GeoFeature(
        geometry_type="LineString",
        coordinates=coords or [(0.0, 0.0), (5.0, 0.0), (10.0, 2.0)],
        properties={"waterway": waterway, "osm_id": 202, "__category__": "waterway"},
    )


def _water_area_feature(coords=None):
    ring = coords or [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0), (0.0, 0.0)]
    return GeoFeature(
        geometry_type="Polygon",
        coordinates=[ring],
        properties={"natural": "water", "osm_id": 303, "__category__": "water_area"},
    )


class TestRoadFromLinestring(unittest.TestCase):
    def test_known_highway_type_uses_table_width(self) -> None:
        road = road_from_linestring_feature(_road_feature(highway="motorway"))
        self.assertAlmostEqual(road.width_m, 11.0)

    def test_unknown_highway_type_uses_fallback_width(self) -> None:
        road = road_from_linestring_feature(_road_feature(highway="unknown_type"))
        self.assertAlmostEqual(road.width_m, DEFAULT_ROAD_WIDTH_FALLBACK_M)

    def test_explicit_width_tag_overrides_table(self) -> None:
        road = road_from_linestring_feature(_road_feature(highway="residential", width="7.5 m"))
        self.assertAlmostEqual(road.width_m, 7.5)

    def test_control_points_match_osm_coordinates(self) -> None:
        road = road_from_linestring_feature(_road_feature())
        self.assertEqual(len(road.control_points), 3)
        self.assertEqual((road.control_points[0].x, road.control_points[0].y), (0.0, 0.0))

    def test_is_a_road_instance(self) -> None:
        road = road_from_linestring_feature(_road_feature())
        self.assertIsInstance(road, Road)

    def test_rejects_non_linestring(self) -> None:
        bad = _water_area_feature()
        with self.assertRaises(ValueError):
            road_from_linestring_feature(bad)


class TestMeshForRoad(unittest.TestCase):
    def test_produces_nondegenerate_ribbon_mesh(self) -> None:
        road = road_from_linestring_feature(_road_feature())
        mesh = mesh_for_road(road)
        self.assertGreater(mesh.vertex_count(), 0)
        self.assertGreater(mesh.triangle_count(), 0)
        # 3 merkez nokta -> 3 sol + 3 sağ kenar vertex'i
        self.assertEqual(mesh.vertex_count(), 6)

    def test_flat_at_ground_elevation(self) -> None:
        road = road_from_linestring_feature(_road_feature())
        mesh = mesh_for_road(road)
        zs = {round(v.z, 6) for v in mesh.vertices}
        self.assertEqual(zs, {0.0})


class TestWaterwayFromLinestring(unittest.TestCase):
    def test_river_uses_table_width(self) -> None:
        waterway = waterway_from_linestring_feature(_waterway_feature(waterway="river"))
        self.assertAlmostEqual(waterway.width_m, 12.0)

    def test_unknown_waterway_type_uses_fallback(self) -> None:
        waterway = waterway_from_linestring_feature(_waterway_feature(waterway="obscure"))
        self.assertAlmostEqual(waterway.width_m, DEFAULT_WATERWAY_WIDTH_FALLBACK_M)

    def test_embedded_below_ground_level(self) -> None:
        waterway = waterway_from_linestring_feature(_waterway_feature())
        self.assertAlmostEqual(waterway.elevation_z, -DEFAULT_WATER_DEPTH_OFFSET_M)

    def test_mesh_is_flat_at_embedded_elevation(self) -> None:
        waterway = waterway_from_linestring_feature(_waterway_feature())
        mesh = mesh_for_road(waterway)
        zs = {round(v.z, 6) for v in mesh.vertices}
        self.assertEqual(zs, {round(-DEFAULT_WATER_DEPTH_OFFSET_M, 6)})


class TestMeshForWaterArea(unittest.TestCase):
    def test_produces_closed_prism_mesh(self) -> None:
        mesh = mesh_for_water_area(_water_area_feature())
        # kare taban -> 4 + 4 vertex (taban + tavan)
        self.assertEqual(mesh.vertex_count(), 8)
        self.assertGreater(mesh.triangle_count(), 0)

    def test_thin_slab_thickness_respected(self) -> None:
        mesh = mesh_for_water_area(_water_area_feature(), thickness_m=0.5)
        zs = sorted({round(v.z, 6) for v in mesh.vertices})
        self.assertEqual(len(zs), 2)
        self.assertAlmostEqual(zs[1] - zs[0], 0.5)

    def test_rejects_non_polygon(self) -> None:
        with self.assertRaises(ValueError):
            mesh_for_water_area(_road_feature())


class TestSnapWaterAreaToCoastline(unittest.TestCase):
    """ROADMAP_V8 Faz 5.5 — kıyı şeridi hizalama."""

    def _water(self):
        return GeoFeature(
            geometry_type="Polygon",
            coordinates=[[(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 18.0), (0.0, 0.0)]],
            properties={"__category__": "water_area", "osm_id": 501},
        )

    def _coast(self, coords=((0.0, 20.0), (20.0, 20.0))):
        return GeoFeature(
            geometry_type="LineString",
            coordinates=list(coords),
            properties={"__category__": "coastline", "osm_id": 502},
        )

    def test_within_tolerance_snaps_to_segment(self) -> None:
        water = self._water()
        snapped = snap_water_area_to_coastline(water, [self._coast()], tolerance_m=5.0)
        self.assertEqual(snapped.coordinates[0][2], (20.0, 20.0))
        self.assertEqual(snapped.coordinates[0][3], (0.0, 20.0))

    def test_original_feature_not_mutated(self) -> None:
        water = self._water()
        snap_water_area_to_coastline(water, [self._coast()], tolerance_m=5.0)
        self.assertEqual(water.coordinates[0][3], (0.0, 18.0))

    def test_out_of_tolerance_corner_unchanged(self) -> None:
        water = self._water()
        far_coast = self._coast(((1000.0, 1000.0), (1020.0, 1000.0)))
        snapped = snap_water_area_to_coastline(water, [far_coast], tolerance_m=5.0)
        self.assertEqual(snapped.coordinates[0], water.coordinates[0])

    def test_no_coastline_features_returns_same_feature(self) -> None:
        water = self._water()
        snapped = snap_water_area_to_coastline(water, [], tolerance_m=5.0)
        self.assertIs(snapped, water)

    def test_rejects_non_polygon(self) -> None:
        with self.assertRaises(ValueError):
            snap_water_area_to_coastline(_road_feature(), [self._coast()])

    def test_mesh_for_water_area_backward_compatible_without_coastline(self) -> None:
        mesh_default = mesh_for_water_area(self._water())
        mesh_explicit_none = mesh_for_water_area(self._water(), coastline_features=None)
        self.assertEqual(mesh_default.vertex_count(), mesh_explicit_none.vertex_count())

    def test_mesh_for_water_area_aligns_with_coastline(self) -> None:
        mesh = mesh_for_water_area(self._water(), coastline_features=[self._coast()])
        ys = {round(v.y, 6) for v in mesh.vertices}
        # Hizalama sonrası tüm köşeler y=0 veya y=20 üzerinde olmalı (18 kaybolmalı).
        self.assertNotIn(18.0, ys)

    def test_end_to_end_collection_order_independent(self) -> None:
        """Su alanı, koleksiyonda kıyı şeridinden ÖNCE gelse bile hizalanmalı."""
        water = self._water()
        coast = self._coast()
        collection = GeoFeatureCollection([water, coast], crs="EPSG:32636")
        result = generate_infrastructure_for_collection(collection)
        self.assertEqual(len(result.water_area_meshes), 1)
        self.assertEqual(len(result.coastline_meshes), 1)
        ys = {round(v.y, 6) for v in result.water_area_meshes[0].vertices}
        self.assertNotIn(18.0, ys)


class TestGenerateInfrastructureForCollection(unittest.TestCase):
    def test_end_to_end_mixed_collection(self) -> None:
        collection = GeoFeatureCollection(
            [_road_feature(), _waterway_feature(), _water_area_feature()],
            crs="EPSG:32636",
        )
        result = generate_infrastructure_for_collection(collection)
        self.assertEqual(len(result.road_meshes), 1)
        self.assertEqual(len(result.waterway_meshes), 1)
        self.assertEqual(len(result.water_area_meshes), 1)
        self.assertEqual(
            result.counts(),
            {
                "roads": 1,
                "waterway": 1,
                "water_area": 1,
                "railway": 0,
                "coastline": 0,
                "area": 0,
                "administrative_boundary": 0,
                "lane_marking": 0,
                "crosswalk": 0,
            },
        )

    def test_ignores_vegetation_categories(self) -> None:
        tree = GeoFeature(
            geometry_type="Point",
            coordinates=[1.0, 1.0],
            properties={"natural": "tree", "__category__": "trees"},
        )
        collection = GeoFeatureCollection([tree], crs="EPSG:32636")
        result = generate_infrastructure_for_collection(collection)
        self.assertEqual(
            result.counts(),
            {
                "roads": 0,
                "waterway": 0,
                "water_area": 0,
                "railway": 0,
                "coastline": 0,
                "area": 0,
                "administrative_boundary": 0,
                "lane_marking": 0,
                "crosswalk": 0,
            },
        )

    def test_skips_degenerate_linestring(self) -> None:
        degenerate = _road_feature(coords=[(0.0, 0.0)])
        collection = GeoFeatureCollection([degenerate], crs="EPSG:32636")
        result = generate_infrastructure_for_collection(collection)
        self.assertEqual(len(result.road_meshes), 0)


if __name__ == "__main__":
    unittest.main()
