"""FAZ 1.1 (açık bulgu #2 kapanışı) — çatıya monte edilmiş gerçek güneş
paneli geometrisi (`RoofDetailGenerator.add_solar_panels`).

Önceki tur yalnızca `RoofGenerator.solar()` ile çatı eğimini üretmişti;
panelin kendi mesh'i yoktu. Bu testler panellerin gerçekten çatı
yüzeyine (eğime uyarak) oturduğunu, ayrı geometri olarak eklendiğini ve
temiz (dejenere üçgensiz) olduğunu doğrular.
"""

from __future__ import annotations

import math

import pytest

from harita.building_reconstruction.roof_generator import RoofDetailGenerator, RoofGenerator
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine.quality_metrics import MeshQualityAnalyzer


def _rect_polygon(w: float = 10.0, d: float = 8.0) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


class TestSolarPanels:
    def test_panels_add_geometry(self):
        polygon = _rect_polygon()
        roof = RoofGenerator.solar(polygon, base_z=10.0, pitch_deg=15.0)
        base_v, base_t = roof.vertex_count(), roof.triangle_count()
        with_panels = RoofDetailGenerator.add_solar_panels(
            roof, polygon, base_z=10.0, pitch_deg=15.0,
        )
        assert with_panels.vertex_count() > base_v
        assert with_panels.triangle_count() > base_t

    def test_panels_are_clean_geometry(self):
        """Panel geometrisi kendi başına dejenere üçgen içermemeli.
        (Not: taban `roof_solar` mesh'inin - `_mono_pitch` çıktısının -
        kendi 4 dejenere üçgeni zaten var (dar üçgen tabanlı taban/tavan
        üçgenlemesinden kaynaklı, bu testin kapsamı dışında ve panel
        eklemeden bağımsız); bu yüzden yalnızca panel eklenmesinin YENİ
        dejenere üçgen getirmediğini doğruluyoruz.)"""
        polygon = _rect_polygon()
        roof = RoofGenerator.solar(polygon, base_z=0.0, pitch_deg=15.0)
        base_report = MeshQualityAnalyzer.analyze(roof)
        with_panels = RoofDetailGenerator.add_solar_panels(
            roof, polygon, base_z=0.0, pitch_deg=15.0,
        )
        report = MeshQualityAnalyzer.analyze(with_panels)
        assert report.degenerate_triangle_count == base_report.degenerate_triangle_count

    def test_panels_follow_roof_slope(self):
        """Panel merkezleri çatı yüzeyi düzleminin üstünde (kalınlık
        kadar), _mono_pitch ile aynı z(x) doğrusuna yakın olmalı."""
        polygon = _rect_polygon(w=12.0, d=10.0)
        base_z = 5.0
        pitch_deg = 20.0
        roof = RoofGenerator.solar(polygon, base_z=base_z, pitch_deg=pitch_deg)
        with_panels = RoofDetailGenerator.add_solar_panels(
            roof, polygon, base_z=base_z, pitch_deg=pitch_deg,
            panel_w=1.0, panel_d=1.6, thickness=0.04,
        )
        # Sadece panel vertex'lerini (roof'tan sonra eklenenler) al
        roof_only = RoofGenerator.solar(polygon, base_z=base_z, pitch_deg=pitch_deg)
        panel_verts = with_panels.vertices[roof_only.vertex_count():]
        assert len(panel_verts) > 0

        span = 12.0
        rise = span * math.tan(math.radians(pitch_deg))
        for v in panel_verts:
            expected_surface_z = base_z + (v.x - 0.0) / span * rise
            # Panel kalınlığı (0.04) + geometrik tilt payı içinde olmalı
            assert v.z >= expected_surface_z - 0.5
            assert v.z <= expected_surface_z + 0.5

    def test_no_panels_on_too_small_roof(self):
        """Kenar boşluğu (margin) çatıdan büyükse panel eklenmemeli,
        fonksiyon çatı mesh'ini olduğu gibi döndürmeli (hata fırlatmamalı)."""
        polygon = _rect_polygon(w=1.0, d=1.0)
        roof = RoofGenerator.solar(polygon, base_z=0.0, pitch_deg=15.0)
        result = RoofDetailGenerator.add_solar_panels(
            roof, polygon, base_z=0.0, pitch_deg=15.0, margin=0.5,
        )
        assert result.vertex_count() == roof.vertex_count()

    def test_panels_scale_with_available_area(self):
        """Daha büyük çatıda daha fazla panel üretilmeli."""
        small_polygon = _rect_polygon(w=6.0, d=6.0)
        big_polygon = _rect_polygon(w=20.0, d=16.0)

        small_roof = RoofGenerator.solar(small_polygon, base_z=0.0, pitch_deg=15.0)
        big_roof = RoofGenerator.solar(big_polygon, base_z=0.0, pitch_deg=15.0)

        small_with_panels = RoofDetailGenerator.add_solar_panels(
            small_roof, small_polygon, base_z=0.0, pitch_deg=15.0,
        )
        big_with_panels = RoofDetailGenerator.add_solar_panels(
            big_roof, big_polygon, base_z=0.0, pitch_deg=15.0,
        )

        small_panel_verts = small_with_panels.vertex_count() - small_roof.vertex_count()
        big_panel_verts = big_with_panels.vertex_count() - big_roof.vertex_count()
        assert big_panel_verts > small_panel_verts
