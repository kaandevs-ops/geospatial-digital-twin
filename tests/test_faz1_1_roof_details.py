"""FAZ 1.1 — roof_generator gerçek eksik/tamam tablosu ve kapatılan
boşluklar için testler: baca, çatı penceresi, iklim bazlı pitch önerisi."""

from __future__ import annotations

import pytest

from harita.building_reconstruction.roof_generator import RoofDetailGenerator, RoofGenerator
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine.quality_metrics import MeshQualityAnalyzer


def _rect_polygon(w: float = 10.0, d: float = 8.0) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


class TestChimney:
    def test_chimney_adds_geometry_to_flat_roof(self):
        roof = RoofGenerator.flat(_rect_polygon(), base_z=10.0)
        base_v, base_t = roof.vertex_count(), roof.triangle_count()
        with_chimney = RoofDetailGenerator.add_chimney(
            roof, Point2D(5.0, 4.0), roof_base_z=10.4, width=0.6, depth=0.6, height=1.2,
        )
        assert with_chimney.vertex_count() > base_v
        assert with_chimney.triangle_count() > base_t

    def test_chimney_geometry_is_clean(self):
        roof = RoofGenerator.flat(_rect_polygon(), base_z=0.0)
        with_chimney = RoofDetailGenerator.add_chimney(roof, Point2D(5.0, 4.0), roof_base_z=0.4)
        report = MeshQualityAnalyzer.analyze(with_chimney)
        assert report.degenerate_triangle_count == 0

    def test_chimney_top_is_above_roof_surface(self):
        roof = RoofGenerator.flat(_rect_polygon(), base_z=0.0)
        with_chimney = RoofDetailGenerator.add_chimney(
            roof, Point2D(5.0, 4.0), roof_base_z=0.4, height=1.2,
        )
        max_z = max(v.z for v in with_chimney.vertices)
        assert max_z == pytest.approx(0.4 + 1.2)


class TestSkylight:
    def test_skylight_adds_geometry(self):
        roof = RoofGenerator.flat(_rect_polygon(), base_z=0.0)
        base_t = roof.triangle_count()
        with_sky = RoofDetailGenerator.add_skylight(roof, Point2D(3.0, 3.0), roof_surface_z=0.4)
        assert with_sky.triangle_count() > base_t

    def test_multiple_skylights_can_be_chained(self):
        roof = RoofGenerator.flat(_rect_polygon(), base_z=0.0)
        roof = RoofDetailGenerator.add_skylight(roof, Point2D(2.0, 2.0), roof_surface_z=0.4)
        roof = RoofDetailGenerator.add_skylight(roof, Point2D(7.0, 6.0), roof_surface_z=0.4)
        report = MeshQualityAnalyzer.analyze(roof)
        assert report.non_manifold_edge_count == 0


class TestClimatePitchSuggestion:
    @pytest.mark.parametrize("zone,expected_min", [
        ("kutup", 40.0),
        ("daglik_karli", 35.0),
        ("kurak", 5.0),
    ])
    def test_snow_heavy_zones_get_steeper_pitch_than_arid(self, zone, expected_min):
        assert RoofDetailGenerator.suggest_pitch_deg(zone) >= expected_min

    def test_snowy_zone_pitch_exceeds_arid_zone_pitch(self):
        assert RoofDetailGenerator.suggest_pitch_deg("kutup") > RoofDetailGenerator.suggest_pitch_deg("kurak")

    def test_unknown_zone_falls_back_to_default(self):
        assert RoofDetailGenerator.suggest_pitch_deg("bilinmeyen_bolge") == 25.0

    def test_suggested_pitch_can_feed_directly_into_generate(self):
        pitch = RoofDetailGenerator.suggest_pitch_deg("dagsi_karli".replace("dagsi", "daglik"))
        mesh = RoofGenerator.generate(_rect_polygon(), base_z=0.0, roof_type="gable", pitch_deg=pitch)
        assert mesh.triangle_count() > 0
