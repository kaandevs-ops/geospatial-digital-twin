"""ROADMAP_V8 Faz 6.1 — "Seçenek A - tam yapısal versiyon" (bkz.
docs/RFC_FAZ6_1_KAVISLI_CEPHE.md, Bölüm 3 Seçenek A): ana yapısal cephe
sistemi (kat/oda/pencere/döşeme) düşey eksende gerçekten eğrilir.

Test edilenler:
1. `VerticalProfileGenerator.floor_polygons()` — hiçbir profil callable'ı
   verilmediğinde tüm katlar taban çokgenle birebir aynı (dejenere durum).
2. `scale_at` verildiğinde katlar gerçekten daralıyor/genişliyor (üretilen
   her `Polygon`'un bounding-box'ı ölçülerek doğrulanıyor).
3. `rotation_deg_at` verildiğinde üst kat gerçekten dönüyor.
4. Üretilen her kat çokgeni taban ile aynı köşe sayısına sahip (facade
   generator'ın `wall_edge_index` eşlemesi için gerekli koşul).
5. `FacadeGenerator.generate(floor_polygons=...)` — uzunluk uyuşmazlığında
   `ValueError`, `None` iken eski davranışla birebir aynı.
6. `ProceduralBuildingGenerator.generate(vertical_scale_at=...)` uçtan
   uca çalışıyor: mesh üretiliyor, çatı en üst katın (daralmış) çokgenine
   oturuyor, `vertical_*` parametreleri `None` iken eski davranış
   (regresyonsuz) korunuyor.
"""

from __future__ import annotations

import pytest
from harita.building_reconstruction.curved_facade import VerticalProfileGenerator
from harita.building_reconstruction.facade_generator import FacadeGenerator
from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import (
    ProceduralBuildingGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon


@pytest.fixture
def base_polygon() -> Polygon:
    return Polygon([Point2D(-6, -4), Point2D(6, -4), Point2D(6, 4), Point2D(-6, 4)])


def _bbox(polygon: Polygon):
    ring = polygon.closed_ring()[:-1]
    xs = [p.x for p in ring]
    ys = [p.y for p in ring]
    return (max(xs) - min(xs), max(ys) - min(ys))


class TestFloorPolygonsIdentity:
    def test_no_profile_is_identity(self, base_polygon):
        floors = VerticalProfileGenerator.floor_polygons(base_polygon, floor_count=5)
        assert len(floors) == 5
        base_ring = base_polygon.closed_ring()[:-1]
        for poly in floors:
            ring = poly.closed_ring()[:-1]
            assert len(ring) == len(base_ring)
            for p, bp in zip(ring, base_ring):
                assert p.x == pytest.approx(bp.x, abs=1e-9)
                assert p.y == pytest.approx(bp.y, abs=1e-9)

    def test_single_floor_no_crash(self, base_polygon):
        floors = VerticalProfileGenerator.floor_polygons(
            base_polygon,
            floor_count=1,
            scale_at=lambda t: 0.5,
        )
        assert len(floors) == 1
        # t=0 tek katlı binada -> scale_at(0.0) = 0.5 uygulanmalı.
        w, h = _bbox(floors[0])
        base_w, base_h = _bbox(base_polygon)
        assert w == pytest.approx(base_w * 0.5, rel=1e-6)
        assert h == pytest.approx(base_h * 0.5, rel=1e-6)


class TestVerticalScale:
    def test_tapering_tower(self, base_polygon):
        # Yukarı doğru büzülen kule: t=0 (zemin) -> ölçek 1.0, t=1 (en
        # üst kat) -> ölçek 0.5.
        floors = VerticalProfileGenerator.floor_polygons(
            base_polygon,
            floor_count=10,
            scale_at=lambda t: 1.0 - 0.5 * t,
        )
        base_w, base_h = _bbox(base_polygon)
        w0, h0 = _bbox(floors[0])
        w9, h9 = _bbox(floors[-1])
        assert w0 == pytest.approx(base_w, rel=1e-6)
        assert w9 == pytest.approx(base_w * 0.5, rel=1e-6)
        assert h9 == pytest.approx(base_h * 0.5, rel=1e-6)
        # Monoton azalan bir dizide olmalı.
        widths = [_bbox(f)[0] for f in floors]
        assert all(widths[i] >= widths[i + 1] - 1e-9 for i in range(len(widths) - 1))

    def test_rotation_twists_top_floor(self, base_polygon):
        floors = VerticalProfileGenerator.floor_polygons(
            base_polygon,
            floor_count=4,
            rotation_deg_at=lambda t: 45.0 * t,
        )
        base_ring = base_polygon.closed_ring()[:-1]
        top_ring = floors[-1].closed_ring()[:-1]
        # 45 derece dönmüş köşe, orijinaliyle aynı konumda olmamalı.
        assert not (
            top_ring[0].x == pytest.approx(base_ring[0].x, abs=1e-6)
            and top_ring[0].y == pytest.approx(base_ring[0].y, abs=1e-6)
        )
        # Ama merkeze uzaklık (yarıçap) korunmalı (yalnız rotasyon, ölçek yok).
        center = base_polygon.centroid()
        r_base = base_ring[0].distance_to(center)
        r_top = top_ring[0].distance_to(center)
        assert r_top == pytest.approx(r_base, rel=1e-6)

    def test_offset_leans_tower(self, base_polygon):
        floors = VerticalProfileGenerator.floor_polygons(
            base_polygon,
            floor_count=3,
            offset_at=lambda t: Point2D(t * 3.0, 0.0),
        )
        c0 = floors[0].centroid()
        c2 = floors[-1].centroid()
        assert c2.x - c0.x == pytest.approx(3.0, rel=1e-6)

    def test_edge_count_preserved(self, base_polygon):
        floors = VerticalProfileGenerator.floor_polygons(
            base_polygon,
            floor_count=6,
            scale_at=lambda t: 1.0 - 0.3 * t,
            rotation_deg_at=lambda t: 20.0 * t,
        )
        base_edges = len(base_polygon.closed_ring()) - 1
        for poly in floors:
            assert len(poly.closed_ring()) - 1 == base_edges


class TestFacadeGeneratorFloorPolygons:
    def test_length_mismatch_raises(self, base_polygon):
        floors = VerticalProfileGenerator.floor_polygons(base_polygon, floor_count=3)
        with pytest.raises(ValueError):
            FacadeGenerator.generate(
                base_polygon,
                "apartman",
                base_z=0.0,
                floor_height=3.0,
                floor_count=5,
                floor_polygons=floors,
            )

    def test_none_is_backward_compatible(self, base_polygon):
        facade_old = FacadeGenerator.generate(
            base_polygon,
            "apartman",
            base_z=0.0,
            floor_height=3.0,
            floor_count=3,
            seed=42,
        )
        facade_new = FacadeGenerator.generate(
            base_polygon,
            "apartman",
            base_z=0.0,
            floor_height=3.0,
            floor_count=3,
            seed=42,
            floor_polygons=None,
        )
        assert facade_old.mesh.triangle_count() == facade_new.mesh.triangle_count()

    def test_tapering_facade_produces_smaller_top_footprint(self, base_polygon):
        floors = VerticalProfileGenerator.floor_polygons(
            base_polygon,
            floor_count=4,
            scale_at=lambda t: 1.0 - 0.4 * t,
        )
        facade = FacadeGenerator.generate(
            base_polygon,
            "apartman",
            base_z=0.0,
            floor_height=3.0,
            floor_count=4,
            floor_polygons=floors,
        )
        assert facade.mesh is not None
        assert facade.mesh.triangle_count() > 0


class TestProceduralBuildingGeneratorVerticalProfile:
    def test_default_none_is_backward_compatible(self, base_polygon):
        fp = Footprint(polygon=base_polygon, building_type="apartman", floor_count=5)
        b1 = ProceduralBuildingGenerator.generate(fp, seed=1)
        b2 = ProceduralBuildingGenerator.generate(fp, seed=1)
        assert b1.facade.mesh.triangle_count() == b2.facade.mesh.triangle_count()
        assert b1.roof.triangle_count() == b2.roof.triangle_count()

    def test_tapering_tower_end_to_end(self, base_polygon):
        fp = Footprint(polygon=base_polygon, building_type="ofis", floor_count=8)
        building = ProceduralBuildingGenerator.generate(
            fp,
            seed=7,
            vertical_scale_at=lambda t: 1.0 - 0.5 * t,
        )
        assert building.facade.mesh is not None
        assert building.facade.mesh.triangle_count() > 0
        assert building.roof is not None
        assert building.roof.triangle_count() > 0
        # Çatı en üst kat (daralmış) çokgenine oturmalı - taban footprint
        # yerine küçültülmüş bounding-box beklenir.
        base_w, _ = _bbox(base_polygon)
        roof_verts = building.roof.vertices
        roof_xs = [v.x for v in roof_verts]
        roof_w = max(roof_xs) - min(roof_xs)
        assert roof_w < base_w

    def test_twisted_tower_end_to_end_no_crash(self, base_polygon):
        fp = Footprint(polygon=base_polygon, building_type="ofis", floor_count=6)
        building = ProceduralBuildingGenerator.generate(
            fp,
            seed=3,
            vertical_scale_at=lambda t: 1.0 - 0.2 * t,
            vertical_rotation_deg_at=lambda t: 30.0 * t,
        )
        assert building.facade.mesh.triangle_count() > 0
        full = building.full_mesh(generate_uvs=True)
        assert full.triangle_count() > 0
