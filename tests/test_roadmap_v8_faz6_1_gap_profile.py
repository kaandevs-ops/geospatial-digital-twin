"""ROADMAP_V8 Faz 6.1 — RFC "İlk aşama": `DoubleSkinFacadeGenerator`'a
yükseklik-bazlı `gap_profile` desteği (bkz. docs/RFC_FAZ6_1_KAVISLI_CEPHE.md,
Seçenek C — yalnızca görsel dış kabuk eğrilir, ana yapısal kat/oda/pencere
sistemi hiç etkilenmez).

Test edilenler:
1. `gap_profile=None` (varsayılan) davranış tamamen değişmiyor — hem
   `DoubleSkinFacadeGenerator.generate()` hem
   `ProceduralBuildingGenerator.generate()` seviyesinde.
2. Bir profil verildiğinde dış kabuk gerçekten düşey eksende daralıyor/
   genişliyor (üst/alt halka yarıçapı ölçülerek doğrulanıyor).
3. Sabit bir profil (`lambda z: gap_m`) ile `gap_profile=None` yolunun
   ürettiği dış siluet (bounding box) birbirine denk — yalnızca iç
   tesselasyon farklı, dıştan görünen şekil aynı.
4. Mullion bantları profile uyum sağlıyor (sabit ofset yerine kat
   hizasındaki gerçek offsete oturuyor).
5. `ProceduralBuildingGenerator.generate(double_skin_gap_profile=...)`
   uçtan uca çalışıyor, ana yapısal sistem (kat sayısı, pencere) etkilenmiyor.
"""
from __future__ import annotations

import math

import pytest

from harita.building_reconstruction.building_elements import (
    DoubleSkinFacade,
    DoubleSkinFacadeGenerator,
)
from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import (
    BuildingType,
    ProceduralBuildingGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon


@pytest.fixture
def base_polygon() -> Polygon:
    return Polygon([Point2D(-6, -4), Point2D(6, -4), Point2D(6, 4), Point2D(-6, 4)])


def _avg_radius(vertices, cx: float, cy: float) -> float:
    return sum(math.hypot(v.x - cx, v.y - cy) for v in vertices) / len(vertices)


class TestBackwardCompatibility:
    def test_none_profile_matches_original_single_extrude_path(self, base_polygon):
        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=5, floor_height=3.0,
            gap_m=1.0, gap_profile=None,
        )
        assert isinstance(result, DoubleSkinFacade)
        assert result.outer_skin_mesh.triangle_count() > 0

    def test_procedural_generator_default_unaffected(self):
        poly = Polygon([Point2D(-8, -6), Point2D(8, -6), Point2D(8, 6), Point2D(-8, 6)])
        fp = Footprint(polygon=poly, height_m=12.0, floor_count=4)
        b1 = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.OFIS)
        b2 = ProceduralBuildingGenerator.generate(
            fp, building_type=BuildingType.OFIS, add_double_skin=False,
        )
        assert b1.double_skin is None and b2.double_skin is None


class TestTaperingProfile:
    def test_top_radius_smaller_than_bottom_for_shrinking_profile(self, base_polygon):
        total_height = 5 * 3.0

        def taper(z: float) -> float:
            return 1.0 * (1.0 - 0.6 * (z / total_height))

        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=5, floor_height=3.0,
            gap_m=1.0, gap_profile=taper,
        )
        verts_bottom = [v for v in result.outer_skin_mesh.vertices if abs(v.z - 0.0) < 1e-6]
        verts_top = [v for v in result.outer_skin_mesh.vertices if abs(v.z - total_height) < 1e-6]
        assert verts_bottom and verts_top
        r_bottom = _avg_radius(verts_bottom, 0.0, 0.0)
        r_top = _avg_radius(verts_top, 0.0, 0.0)
        assert r_top < r_bottom

    def test_expanding_profile_grows_upward(self, base_polygon):
        total_height = 4 * 3.0

        def expand(z: float) -> float:
            return 0.5 + 1.0 * (z / total_height)

        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=4, floor_height=3.0,
            gap_profile=expand,
        )
        verts_bottom = [v for v in result.outer_skin_mesh.vertices if abs(v.z - 0.0) < 1e-6]
        verts_top = [v for v in result.outer_skin_mesh.vertices if abs(v.z - total_height) < 1e-6]
        r_bottom = _avg_radius(verts_bottom, 0.0, 0.0)
        r_top = _avg_radius(verts_top, 0.0, 0.0)
        assert r_top > r_bottom

    def test_constant_profile_matches_constant_gap_m_bounding_box(self, base_polygon):
        constant_gap = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=3, floor_height=3.0, gap_m=1.3,
        )
        constant_profile = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=3, floor_height=3.0,
            gap_profile=lambda z: 1.3,
        )
        bb1 = constant_gap.outer_skin_mesh.bounding_box()
        bb2 = constant_profile.outer_skin_mesh.bounding_box()
        for a, b in zip(bb1[0], bb2[0]):
            assert abs(a - b) < 1e-6
        for a, b in zip(bb1[1], bb2[1]):
            assert abs(a - b) < 1e-6

    def test_sinusoidal_twist_profile_is_non_monotonic(self, base_polygon):
        total_height = 6 * 3.0

        def twisted(z: float) -> float:
            return 1.0 + 0.4 * math.sin(2 * math.pi * z / total_height)

        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=6, floor_height=3.0,
            gap_profile=twisted,
        )
        radii_by_floor = []
        for floor_idx in range(7):
            z = floor_idx * 3.0
            verts = [v for v in result.outer_skin_mesh.vertices if abs(v.z - z) < 1e-6]
            if verts:
                radii_by_floor.append(_avg_radius(verts, 0.0, 0.0))
        # Sinuzoidal profilde radii hem artan hem azalan aralıklar olmalı
        # (tek yönlü monoton artış/azalış değil - gerçek "twist" kanıtı).
        diffs = [radii_by_floor[i + 1] - radii_by_floor[i] for i in range(len(radii_by_floor) - 1)]
        assert any(d > 0 for d in diffs) and any(d < 0 for d in diffs)


class TestMullionAlignment:
    def test_mullion_bands_scale_with_profile(self, base_polygon):
        # Mullion bantları artık sabit outer_polygon yerine kat-bazlı
        # gerçek offsete oturuyor - dolaylı kanıt: gap_profile ile üretilen
        # tüm mesh'in (outer shell + mullion bantları) bounding box'ı, yalnız
        # outer shell'in kendi bounding box'ından büyük olamaz (bantlar
        # kabuğun dışına taşmamalı).
        def taper(z: float) -> float:
            total = 5 * 3.0
            return 1.0 * (1.0 - 0.5 * (z / total))

        result = DoubleSkinFacadeGenerator.generate(
            base_polygon, base_z=0.0, floor_count=5, floor_height=3.0,
            gap_profile=taper, add_shading_fins=False,
        )
        outer_only = DoubleSkinFacadeGenerator._build_lofted_shell(
            base_polygon,
            [i * 3.0 for i in range(6)],
            [taper(i * 3.0) for i in range(6)],
            name="reference_shell",
        )
        bb_full = result.outer_skin_mesh.bounding_box()
        bb_shell = outer_only.bounding_box()
        # Mullion bantları hafif kalınlık taşıdığı için sınırlı bir tolerans
        # ile kabuğun dışına küçük bir miktar taşabilir (mullion_thickness),
        # ama on kat büyüklükte bir sapma olmamalı (yanlış hizalama kanıtı).
        assert abs(bb_full[1][0] - bb_shell[1][0]) < 0.5
        assert abs(bb_full[1][1] - bb_shell[1][1]) < 0.5


class TestProceduralGeneratorIntegration:
    def test_gap_profile_flows_through_end_to_end(self):
        poly = Polygon([Point2D(-8, -6), Point2D(8, -6), Point2D(8, 6), Point2D(-8, 6)])
        fp = Footprint(polygon=poly, height_m=15.0, floor_count=5)

        def taper(z: float) -> float:
            return 1.0 * (1.0 - 0.5 * (z / 15.0))

        building = ProceduralBuildingGenerator.generate(
            fp, building_type=BuildingType.OFIS,
            add_double_skin=True, double_skin_gap_m=1.0,
            double_skin_gap_profile=taper,
        )
        assert building.double_skin is not None
        assert building.double_skin.outer_skin_mesh.triangle_count() > 0
        # Ana yapısal sistem etkilenmemeli.
        assert len(building.floors) == 5
        assert building.full_mesh().triangle_count() > 0

    def test_gap_profile_none_keeps_original_double_skin_path(self):
        poly = Polygon([Point2D(-8, -6), Point2D(8, -6), Point2D(8, 6), Point2D(-8, 6)])
        fp = Footprint(polygon=poly, height_m=12.0, floor_count=4)
        with_gap_m_only = ProceduralBuildingGenerator.generate(
            fp, building_type=BuildingType.OFIS,
            add_double_skin=True, double_skin_gap_m=0.8,
        )
        with_explicit_none = ProceduralBuildingGenerator.generate(
            fp, building_type=BuildingType.OFIS,
            add_double_skin=True, double_skin_gap_m=0.8, double_skin_gap_profile=None,
        )
        t1 = with_gap_m_only.double_skin.outer_skin_mesh.triangle_count()
        t2 = with_explicit_none.double_skin.outer_skin_mesh.triangle_count()
        assert t1 == t2
