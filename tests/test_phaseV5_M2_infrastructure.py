"""ROADMAP_V5 - M2.5 (kalan madde): köprü, su yüzeyi, peyzaj detayları
testleri."""

from __future__ import annotations

import math

import pytest

from harita.street_furniture.infrastructure import (
    BridgeGenerator,
    BridgeSpec,
    WaterSurfaceGenerator,
    LandscapeDetailGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon


# ---------------------------------------------------------------------- #
# BridgeGenerator
# ---------------------------------------------------------------------- #

class TestBridgeGenerator:
    def test_straight_bridge_produces_mesh(self):
        path = [Point2D(0, 0), Point2D(50, 0)]
        spec = BridgeSpec(path=path, deck_width_m=8.0, deck_z=5.0, pier_ground_z=-3.0)
        mesh = BridgeGenerator.generate(spec)
        assert mesh.triangle_count() > 0

    def test_curved_path_bridge_produces_mesh(self):
        path = [Point2D(0, 0), Point2D(20, 5), Point2D(40, 8), Point2D(60, 6)]
        spec = BridgeSpec(path=path, deck_width_m=6.0, deck_z=4.0, pier_ground_z=-2.0)
        mesh = BridgeGenerator.generate(spec)
        assert mesh.triangle_count() > 0

    def test_deck_footprint_width_is_correct(self):
        path = [Point2D(0, 0), Point2D(30, 0)]
        footprint = BridgeGenerator._deck_footprint(path, width_m=10.0)
        ring = footprint.closed_ring()[:-1]
        ys = [p.y for p in ring]
        assert abs((max(ys) - min(ys)) - 10.0) < 1e-6

    def test_deck_requires_at_least_two_points(self):
        with pytest.raises(ValueError):
            BridgeGenerator._deck_footprint([Point2D(0, 0)], width_m=5.0)

    def test_deck_bbox_spans_deck_z_thickness(self):
        path = [Point2D(0, 0), Point2D(50, 0)]
        spec = BridgeSpec(path=path, deck_width_m=8.0, deck_thickness_m=0.6, deck_z=5.0, pier_ground_z=-3.0)
        mesh = BridgeGenerator.generate(spec)
        bbox_min, bbox_max = mesh.bounding_box()
        # Tabliye üst yüzeyi deck_z civarında olmalı (korkuluk/ayak alt
        # sınırı çeker ama üst sınır tabliyeyi aşmamalı).
        assert bbox_max[2] >= spec.deck_z
        assert bbox_min[2] <= spec.pier_ground_z

    def test_more_piers_for_longer_bridge(self):
        short_path = [Point2D(0, 0), Point2D(10, 0)]
        long_path = [Point2D(0, 0), Point2D(100, 0)]
        short_spec = BridgeSpec(path=short_path, pier_spacing_m=15.0, deck_z=5.0, pier_ground_z=-3.0)
        long_spec = BridgeSpec(path=long_path, pier_spacing_m=15.0, deck_z=5.0, pier_ground_z=-3.0)
        short_mesh = BridgeGenerator.generate(short_spec)
        long_mesh = BridgeGenerator.generate(long_spec)
        # Uzun köprü daha fazla ayak (dolayısıyla daha fazla üçgen) içermeli.
        assert long_mesh.triangle_count() > short_mesh.triangle_count()

    def test_no_self_intersection_in_deck_footprint(self):
        path = [Point2D(0, 0), Point2D(20, 10), Point2D(40, 5)]
        footprint = BridgeGenerator._deck_footprint(path, width_m=8.0)
        # Basit sürekli bir şerit olmalı - kendi kendini kesmemeli.
        from harita.building_reconstruction.curved_facade import CurvedFootprintGenerator
        assert not CurvedFootprintGenerator.is_self_intersecting(footprint)


# ---------------------------------------------------------------------- #
# WaterSurfaceGenerator
# ---------------------------------------------------------------------- #

class TestWaterSurfaceGenerator:
    def test_rectangular_surface_has_correct_bbox(self):
        mesh = WaterSurfaceGenerator.rectangular_surface(Point2D(0, 0), width_m=100.0, depth_m=80.0, z=0.0)
        bbox_min, bbox_max = mesh.bounding_box()
        assert abs((bbox_max[0] - bbox_min[0]) - 100.0) < 1e-6
        assert abs((bbox_max[1] - bbox_min[1]) - 80.0) < 1e-6

    def test_surface_is_thin(self):
        mesh = WaterSurfaceGenerator.rectangular_surface(Point2D(0, 0), 50.0, 50.0, z=2.0)
        bbox_min, bbox_max = mesh.bounding_box()
        assert (bbox_max[2] - bbox_min[2]) < 0.1

    def test_polygon_surface_arbitrary_shape(self):
        poly = Polygon([Point2D(0, 0), Point2D(10, 0), Point2D(10, 5), Point2D(5, 8), Point2D(0, 5)])
        mesh = WaterSurfaceGenerator.polygon_surface(poly, z=-1.0)
        assert mesh.triangle_count() > 0

    def test_z_level_positions_surface_correctly(self):
        mesh = WaterSurfaceGenerator.rectangular_surface(Point2D(0, 0), 20.0, 20.0, z=-5.0)
        bbox_min, bbox_max = mesh.bounding_box()
        assert bbox_min[2] < -5.0 < bbox_max[2] or abs(bbox_min[2] - (-5.0)) < 0.1


# ---------------------------------------------------------------------- #
# LandscapeDetailGenerator
# ---------------------------------------------------------------------- #

class TestLandscapeDetailGenerator:
    def test_sidewalk_strip_produces_mesh(self):
        path = [Point2D(0, 0), Point2D(30, 0)]
        mesh = LandscapeDetailGenerator.sidewalk_strip(path, width_m=2.0)
        assert mesh.triangle_count() > 0

    def test_sidewalk_offset_shifts_strip(self):
        path = [Point2D(0, 0), Point2D(30, 0)]
        no_offset = LandscapeDetailGenerator.sidewalk_strip(path, width_m=2.0, offset_from_road_m=0.0)
        with_offset = LandscapeDetailGenerator.sidewalk_strip(path, width_m=2.0, offset_from_road_m=5.0)
        _, no_offset_max = no_offset.bounding_box()
        _, offset_max = with_offset.bounding_box()
        assert offset_max[1] > no_offset_max[1]

    def test_curb_strip_produces_mesh(self):
        path = [Point2D(0, 0), Point2D(30, 0)]
        mesh = LandscapeDetailGenerator.curb_strip(path)
        assert mesh.triangle_count() > 0

    def test_crosswalk_stripe_count_matches_request(self):
        mesh_6 = LandscapeDetailGenerator.crosswalk_stripes(
            Point2D(10, 0), direction_deg=0.0, road_width_m=8.0, stripe_count=6,
        )
        mesh_10 = LandscapeDetailGenerator.crosswalk_stripes(
            Point2D(10, 0), direction_deg=0.0, road_width_m=8.0, stripe_count=10,
        )
        assert mesh_10.triangle_count() > mesh_6.triangle_count()

    def test_crosswalk_rotates_with_direction(self):
        mesh_0 = LandscapeDetailGenerator.crosswalk_stripes(Point2D(0, 0), direction_deg=0.0, road_width_m=8.0)
        mesh_90 = LandscapeDetailGenerator.crosswalk_stripes(Point2D(0, 0), direction_deg=90.0, road_width_m=8.0)
        bbox0_min, bbox0_max = mesh_0.bounding_box()
        bbox90_min, bbox90_max = mesh_90.bounding_box()
        # 0 derecede genişlik y-ekseninde daha büyük, 90 derecede x-ekseninde.
        span0_x = bbox0_max[0] - bbox0_min[0]
        span0_y = bbox0_max[1] - bbox0_min[1]
        span90_x = bbox90_max[0] - bbox90_min[0]
        span90_y = bbox90_max[1] - bbox90_min[1]
        assert span0_y > span0_x
        assert span90_x > span90_y

    def test_parking_lines_count_matches_stalls_plus_one(self):
        mesh_3 = LandscapeDetailGenerator.parking_lines(Point2D(0, 0), direction_deg=0.0, stall_count=3)
        mesh_5 = LandscapeDetailGenerator.parking_lines(Point2D(0, 0), direction_deg=0.0, stall_count=5)
        # 5 stall -> 6 çizgi, 3 stall -> 4 çizgi: daha fazla stall daha
        # fazla üçgen üretmeli.
        assert mesh_5.triangle_count() > mesh_3.triangle_count()


# ---------------------------------------------------------------------- #
# M2.5 kabul kriteri: "en az 4 kategori (yol/köprü/su/mobilya) otomatik
# üretilip sahneye yerleştirilmeli" - entegrasyon testi.
# ---------------------------------------------------------------------- #

def test_m25_four_categories_integration():
    from harita.street_furniture import StreetFurnitureGenerator, StreetFurnitureItem, StreetFurnitureType
    from harita.mesh_engine import MeshMerger

    road_path = [Point2D(0, 0), Point2D(50, 0)]
    sidewalk = LandscapeDetailGenerator.sidewalk_strip(road_path, width_m=2.0, offset_from_road_m=5.0)
    bridge = BridgeGenerator.generate(BridgeSpec(path=[Point2D(50, 0), Point2D(80, 0)], deck_z=3.0, pier_ground_z=-2.0))
    water = WaterSurfaceGenerator.rectangular_surface(Point2D(65, 0), 40.0, 20.0, z=-2.0)
    furniture = StreetFurnitureGenerator.generate(
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(10, 6))
    )

    scene = MeshMerger.merge([sidewalk, bridge, water, furniture], name="m25_scene")
    assert scene.triangle_count() > 0
    # Her kategori kendi üçgenlerini katkıda bulunmuş olmalı.
    assert sidewalk.triangle_count() > 0
    assert bridge.triangle_count() > 0
    assert water.triangle_count() > 0
    assert furniture.triangle_count() > 0


# ---------------------------------------------------------------------- #
# ROADMAP_V7.md UV-unwrap kapanışı: bridge/water/landscape artık gerçek
# UV atıyor (daha önce hiç atamıyordu, bkz. UVGenerator wiring)
# ---------------------------------------------------------------------- #

class TestInfrastructureUVCoverage:
    def test_bridge_mesh_has_uv_on_every_vertex(self):
        path = [Point2D(0, 0), Point2D(50, 0)]
        spec = BridgeSpec(path=path, deck_width_m=8.0, deck_z=5.0, pier_ground_z=-3.0)
        mesh = BridgeGenerator.generate(spec)
        assert mesh.vertex_count() > 0
        assert all(v.uv is not None for v in mesh.vertices)

    def test_water_polygon_surface_has_uv(self):
        ring = [Point2D(0, 0), Point2D(20, 0), Point2D(20, 15), Point2D(0, 15)]
        mesh = WaterSurfaceGenerator.polygon_surface(Polygon(ring), z=0.0)
        assert mesh.vertex_count() > 0
        assert all(v.uv is not None for v in mesh.vertices)

    def test_water_rectangular_surface_has_uv(self):
        mesh = WaterSurfaceGenerator.rectangular_surface(Point2D(0, 0), width_m=30, depth_m=20)
        assert all(v.uv is not None for v in mesh.vertices)

    def test_sidewalk_strip_has_uv(self):
        path = [Point2D(0, 0), Point2D(30, 0)]
        mesh = LandscapeDetailGenerator.sidewalk_strip(path)
        assert mesh.vertex_count() > 0
        assert all(v.uv is not None for v in mesh.vertices)

    def test_curb_strip_has_uv(self):
        path = [Point2D(0, 0), Point2D(30, 0)]
        mesh = LandscapeDetailGenerator.curb_strip(path)
        assert all(v.uv is not None for v in mesh.vertices)

    def test_crosswalk_stripes_have_uv(self):
        mesh = LandscapeDetailGenerator.crosswalk_stripes(
            center=Point2D(10, 10), direction_deg=0.0, road_width_m=8.0,
        )
        assert mesh.vertex_count() > 0
        assert all(v.uv is not None for v in mesh.vertices)

    def test_parking_lines_have_uv(self):
        mesh = LandscapeDetailGenerator.parking_lines(
            origin=Point2D(0, 0), direction_deg=0.0, stall_count=5,
        )
        assert mesh.vertex_count() > 0
        assert all(v.uv is not None for v in mesh.vertices)
