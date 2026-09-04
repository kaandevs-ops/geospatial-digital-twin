"""ROADMAP_V5 - M2.2 (kalan madde): kavisli/yuvarlatılmış footprint +
çift kabuk cephe (double-skin façade) testleri."""

from __future__ import annotations

import math

import pytest

from harita.building_reconstruction.curved_facade import CurvedFootprintGenerator
from harita.building_reconstruction.building_elements import (
    DoubleSkinFacade,
    DoubleSkinFacadeGenerator,
)
from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import (
    ProceduralBuildingGenerator,
    BuildingType,
)
from harita.core_engine.geometry_engine import Point2D, Polygon


# ---------------------------------------------------------------------- #
# CurvedFootprintGenerator
# ---------------------------------------------------------------------- #

class TestCircularFootprint:
    def test_vertex_count_matches_segments(self):
        poly = CurvedFootprintGenerator.circular_footprint(Point2D(0, 0), 10.0, segments=24)
        assert len(poly.closed_ring()) - 1 == 24

    def test_minimum_segments_enforced(self):
        poly = CurvedFootprintGenerator.circular_footprint(Point2D(0, 0), 5.0, segments=2)
        assert len(poly.closed_ring()) - 1 >= 8

    def test_all_points_on_radius(self):
        center = Point2D(5.0, -3.0)
        radius = 12.0
        poly = CurvedFootprintGenerator.circular_footprint(center, radius, segments=16)
        for p in poly.closed_ring()[:-1]:
            d = math.hypot(p.x - center.x, p.y - center.y)
            assert abs(d - radius) < 1e-9

    def test_no_self_intersection(self):
        poly = CurvedFootprintGenerator.circular_footprint(Point2D(0, 0), 8.0, segments=32)
        assert not CurvedFootprintGenerator.is_self_intersecting(poly)


class TestEllipticalFootprint:
    def test_produces_valid_polygon(self):
        poly = CurvedFootprintGenerator.elliptical_footprint(
            Point2D(0, 0), radius_x=10.0, radius_y=5.0, segments=20,
        )
        assert len(poly.closed_ring()) - 1 == 20
        assert not CurvedFootprintGenerator.is_self_intersecting(poly)

    def test_rotation_changes_orientation(self):
        base = CurvedFootprintGenerator.elliptical_footprint(
            Point2D(0, 0), radius_x=10.0, radius_y=4.0, rotation_deg=0.0, segments=16,
        )
        rotated = CurvedFootprintGenerator.elliptical_footprint(
            Point2D(0, 0), radius_x=10.0, radius_y=4.0, rotation_deg=45.0, segments=16,
        )
        base_pts = [(round(p.x, 6), round(p.y, 6)) for p in base.closed_ring()[:-1]]
        rot_pts = [(round(p.x, 6), round(p.y, 6)) for p in rotated.closed_ring()[:-1]]
        assert base_pts != rot_pts


class TestRoundedRectangleFootprint:
    @pytest.mark.parametrize("w,d,r", [
        (20.0, 12.0, 3.0), (10.0, 10.0, 4.9), (30.0, 8.0, 3.9), (15.0, 15.0, 0.5),
    ])
    def test_no_self_intersection_across_sizes(self, w, d, r):
        poly = CurvedFootprintGenerator.rounded_rectangle_footprint(w, d, corner_radius=r)
        assert not CurvedFootprintGenerator.is_self_intersecting(poly)

    def test_corner_radius_clamped_to_half_min_dimension(self):
        # width=10, depth=6 -> min(w,d)/2 = 3.0; verilen 100 kırpılmalı.
        poly = CurvedFootprintGenerator.rounded_rectangle_footprint(10.0, 6.0, corner_radius=100.0)
        assert not CurvedFootprintGenerator.is_self_intersecting(poly)
        # Kırpma sonrası poligon hâlâ makul bir kutu alanına yakın olmalı
        # (dejenere/sıfır alan değil).
        ring = poly.closed_ring()[:-1]
        xs = [p.x for p in ring]
        ys = [p.y for p in ring]
        assert (max(xs) - min(xs)) > 5.0
        assert (max(ys) - min(ys)) > 3.0

    def test_zero_radius_falls_back_to_sharp_rectangle(self):
        poly = CurvedFootprintGenerator.rounded_rectangle_footprint(10.0, 6.0, corner_radius=0.0)
        assert len(poly.closed_ring()) - 1 == 4

    def test_segments_per_corner_increases_vertex_count(self):
        low = CurvedFootprintGenerator.rounded_rectangle_footprint(20, 12, corner_radius=3.0, segments_per_corner=2)
        high = CurvedFootprintGenerator.rounded_rectangle_footprint(20, 12, corner_radius=3.0, segments_per_corner=10)
        n_low = len(low.closed_ring()) - 1
        n_high = len(high.closed_ring()) - 1
        assert n_high > n_low


class TestSelfIntersectionDetector:
    def test_simple_square_not_self_intersecting(self):
        poly = Polygon([Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)])
        assert not CurvedFootprintGenerator.is_self_intersecting(poly)

    def test_bowtie_polygon_detected_as_self_intersecting(self):
        # Kravat (bowtie) şekli: köşegen olarak çaprazlanan kenarlar.
        poly = Polygon([Point2D(0, 0), Point2D(10, 10), Point2D(10, 0), Point2D(0, 10)])
        assert CurvedFootprintGenerator.is_self_intersecting(poly)


# ---------------------------------------------------------------------- #
# DoubleSkinFacadeGenerator
# ---------------------------------------------------------------------- #

class TestDoubleSkinFacadeGenerator:
    @pytest.fixture
    def base_polygon(self) -> Polygon:
        return Polygon([Point2D(-6, -4), Point2D(6, -4), Point2D(6, 4), Point2D(-6, 4)])

    def test_returns_double_skin_facade_dataclass(self, base_polygon):
        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=5, floor_height=3.2, gap_m=0.9,
        )
        assert isinstance(result, DoubleSkinFacade)
        assert result.outer_skin_mesh.triangle_count() > 0
        assert result.gap_m == 0.9

    def test_outer_skin_is_outside_primary_footprint(self, base_polygon):
        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=3, floor_height=3.0, gap_m=1.2,
        )
        bbox_min, bbox_max = result.outer_skin_mesh.bounding_box()
        base_ring = base_polygon.closed_ring()[:-1]
        base_xs = [p.x for p in base_ring]
        base_ys = [p.y for p in base_ring]
        assert bbox_min[0] < min(base_xs)
        assert bbox_max[0] > max(base_xs)
        assert bbox_min[1] < min(base_ys)
        assert bbox_max[1] > max(base_ys)

    def test_shading_fins_optional(self, base_polygon):
        with_fins = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=4, floor_height=3.0, add_shading_fins=True,
        )
        without_fins = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=4, floor_height=3.0, add_shading_fins=False,
        )
        assert with_fins.shading_fin_mesh is not None
        assert with_fins.fin_count > 0
        assert without_fins.shading_fin_mesh is None
        assert without_fins.fin_count == 0

    def test_gap_zero_returns_same_footprint_shell(self, base_polygon):
        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=2, floor_height=3.0, gap_m=0.0,
        )
        bbox_min, bbox_max = result.outer_skin_mesh.bounding_box()
        base_ring = base_polygon.closed_ring()[:-1]
        assert abs(bbox_min[0] - min(p.x for p in base_ring)) < 0.1
        assert abs(bbox_max[0] - max(p.x for p in base_ring)) < 0.1

    def test_total_height_covers_all_floors(self, base_polygon):
        floor_count, floor_height = 6, 3.1
        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=10.0, floor_count=floor_count, floor_height=floor_height,
        )
        bbox_min, bbox_max = result.outer_skin_mesh.bounding_box()
        assert abs(bbox_min[2] - 10.0) < 0.1
        assert abs(bbox_max[2] - (10.0 + floor_count * floor_height)) < 0.1


# ---------------------------------------------------------------------- #
# ProceduralBuildingGenerator integration (opt-in, backward compatible)
# ---------------------------------------------------------------------- #

class TestProceduralGeneratorDoubleSkinIntegration:
    def test_default_behavior_unchanged_without_opt_in(self):
        poly = Polygon([Point2D(-8, -6), Point2D(8, -6), Point2D(8, 6), Point2D(-8, 6)])
        fp = Footprint(polygon=poly, height_m=12.0, floor_count=4)
        building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.OFIS)
        assert building.double_skin is None

    def test_opt_in_produces_double_skin_and_extends_full_mesh(self):
        poly = Polygon([Point2D(-8, -6), Point2D(8, -6), Point2D(8, 6), Point2D(-8, 6)])
        fp = Footprint(polygon=poly, height_m=12.0, floor_count=4)

        without = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.OFIS)
        with_ds = ProceduralBuildingGenerator.generate(
            fp, building_type=BuildingType.OFIS, add_double_skin=True, double_skin_gap_m=0.8,
        )

        assert with_ds.double_skin is not None
        assert with_ds.double_skin.outer_skin_mesh.triangle_count() > 0
        assert with_ds.full_mesh().triangle_count() > without.full_mesh().triangle_count()

    def test_curved_footprint_generates_valid_building(self):
        poly = CurvedFootprintGenerator.rounded_rectangle_footprint(20, 14, corner_radius=3.0)
        fp = Footprint(polygon=poly, height_m=15.0, floor_count=5)
        building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.OFIS)
        assert len(building.floors) == 5
        assert building.full_mesh().triangle_count() > 0

    def test_circular_footprint_generates_valid_building(self):
        poly = CurvedFootprintGenerator.circular_footprint(Point2D(0, 0), 12.0, segments=24)
        fp = Footprint(polygon=poly, height_m=30.0, floor_count=10)
        building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.OFIS)
        assert len(building.floors) == 10
        assert building.full_mesh().triangle_count() > 0
