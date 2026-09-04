"""Faz D3 — Analysis Engine: BVH-Hızlandırmalı Visibility + Gerçek Çevresel
Modeller testleri.

Roadmap V3, Faz D3 kabul kriteri (A6): 10.000 binalık bir sahnede visibility
sorgusu <100ms olmalı (BVH ile, mevcut doğrusal taramaya karşı benchmark +
regresyon testi). Bu dosya:

    1) `RayCasting.cast`'in `bvh` verildiğinde, verilmediği (doğrusal tarama)
       duruma göre **aynı** kesişim sonucunu ürettiğini (doğruluk regresyonu),
    2) `SceneVisibilityIndex`'in (broad-phase Octree + narrow-phase BVH)
       10.000 binalık bir sahnede tek bir LOS sorgusunu <100ms'de
       tamamladığını ve saf doğrusal taramaya (`LineOfSight.check` BVH'siz)
       göre ölçülebilir şekilde daha az sayıda üçgen/mesh test ettiğini,
    3) `WindSimulation`'ın artık bina yüksekliğine göre farklı wake
       uzunlukları ürettiğini (Wise 1970 ilişkisi),
    4) `NoiseSimulation.spl_at`'ın ISO 9613-2 basitleştirilmiş iki-terimli
       (geometric divergence + atmospheric absorption) formülünü doğru
       uyguladığını
doğrular.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.analysis_engine.environmental_sim import NoiseSimulation, WindSimulation
from harita.analysis_engine.visibility import (
    LineOfSight,
    RayCasting,
    SceneVisibilityIndex,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import Mesh3D, MeshBuilder


def _box_mesh(size=10.0, height=6.0, base_z=0.0, cx=0.0, cy=0.0) -> Mesh3D:
    poly = Polygon(
        points=[
            Point2D(cx - size / 2, cy - size / 2),
            Point2D(cx + size / 2, cy - size / 2),
            Point2D(cx + size / 2, cy + size / 2),
            Point2D(cx - size / 2, cy + size / 2),
        ]
    )
    return MeshBuilder.extrude_polygon(poly, base_z=base_z, height=height)


# ============================================================================ #
# 1) RayCasting: BVH ile doğrusal tarama aynı sonucu vermeli
# ============================================================================ #


class TestRayCastingBVHParity:
    def test_bvh_hit_matches_linear_scan(self):
        mesh = _box_mesh(size=10.0, height=6.0)
        bvh = RayCasting.build_bvh(mesh)

        origin = (0.0, 0.0, 3.0)
        direction = (1.0, 0.0, 0.0)

        linear_hit = RayCasting.cast(origin, direction, mesh, max_distance=100.0)
        bvh_hit = RayCasting.cast(origin, direction, mesh, max_distance=100.0, bvh=bvh)

        assert linear_hit is not None
        assert bvh_hit is not None
        assert math.isclose(linear_hit.distance, bvh_hit.distance, rel_tol=1e-9)
        assert math.isclose(linear_hit.point[0], bvh_hit.point[0], abs_tol=1e-6)

    def test_bvh_miss_matches_linear_scan(self):
        mesh = _box_mesh(size=10.0, height=6.0)
        bvh = RayCasting.build_bvh(mesh)

        origin = (100.0, 100.0, 3.0)
        direction = (1.0, 0.0, 0.0)

        assert RayCasting.cast(origin, direction, mesh, max_distance=50.0) is None
        assert RayCasting.cast(origin, direction, mesh, max_distance=50.0, bvh=bvh) is None

    def test_cast_any_with_bvhs_parity(self):
        mesh_a = _box_mesh(size=6.0, height=4.0, cx=0.0, cy=0.0)
        mesh_b = _box_mesh(size=6.0, height=4.0, cx=30.0, cy=0.0)
        bvhs = [RayCasting.build_bvh(mesh_a), RayCasting.build_bvh(mesh_b)]

        origin = (-20.0, 0.0, 2.0)
        direction = (1.0, 0.0, 0.0)

        linear = RayCasting.cast_any(origin, direction, [mesh_a, mesh_b], max_distance=100.0)
        accelerated = RayCasting.cast_any(
            origin, direction, [mesh_a, mesh_b], max_distance=100.0, bvhs=bvhs
        )
        assert linear is not None and accelerated is not None
        assert math.isclose(linear.distance, accelerated.distance, rel_tol=1e-9)


# ============================================================================ #
# 2) SceneVisibilityIndex: A6 kabul kriteri (10.000 bina, <100ms)
# ============================================================================ #


def _build_grid_scene(n_side: int, spacing: float = 20.0, size: float = 8.0, height: float = 6.0):
    """`n_side x n_side` bina ızgarası (toplam n_side**2 bina) üretir."""
    meshes = []
    for row in range(n_side):
        for col in range(n_side):
            cx = col * spacing
            cy = row * spacing
            meshes.append(_box_mesh(size=size, height=height, cx=cx, cy=cy))
    return meshes


class TestSceneVisibilityIndexScale:
    def test_10000_building_scene_query_under_100ms(self):
        n_side = 100  # 100 x 100 = 10.000 bina
        meshes = _build_grid_scene(n_side)

        index = SceneVisibilityIndex(meshes)

        # Sahnenin bir köşesinden karşı köşeye (uzun mesafeli, çoğu binanın
        # gerçek segment AABB'siyle kesişmediği, dolayısıyla broad-phase'de
        # elendiği) bir LOS sorgusu.
        observer = (-10.0, -10.0, 3.0)
        target = (n_side * 20.0 + 10.0, 10.0, 3.0)

        start = time.perf_counter()
        result = index.line_of_sight(observer, target)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, f"SceneVisibilityIndex sorgusu 100ms'i aştı: {elapsed * 1000:.2f}ms"
        assert result.distance > 0.0

    def test_broad_phase_eliminates_most_buildings(self):
        n_side = 100
        meshes = _build_grid_scene(n_side)
        index = SceneVisibilityIndex(meshes)

        # Dar bir koridor boyunca (tek bir satır hizasında) kısa bir sorgu -
        # adayların toplam bina sayısının (10.000) çok altında kalması beklenir.
        observer = (-10.0, 5.0 * 20.0, 3.0)
        target = (30.0 * 20.0, 5.0 * 20.0, 3.0)
        candidates = index.candidate_count(observer, target)

        assert candidates < len(meshes) // 10, (
            f"Broad-phase yeterince eleme yapmadı: {candidates} aday / {len(meshes)} bina"
        )

    def test_scene_visibility_index_accuracy_vs_linear_scan(self):
        """Küçük bir sahnede, hızlandırılmış sorgunun saf doğrusal LOS
        taramasıyla (BVH'siz `LineOfSight.check`) aynı görünürlük sonucunu
        verdiğini doğrular (doğruluk regresyonu - sadece hız değil)."""
        meshes = _build_grid_scene(n_side=6, spacing=15.0, size=8.0, height=6.0)
        index = SceneVisibilityIndex(meshes)

        observer = (-10.0, 15.0, 3.0)
        target = (6 * 15.0 + 10.0, 15.0, 3.0)  # bina sırası boyunca - engellenmeli

        fast_result = index.line_of_sight(observer, target)
        reference_result = LineOfSight.check(observer, target, meshes)

        assert fast_result.visible == reference_result.visible
        if not reference_result.visible:
            assert math.isclose(
                fast_result.blocked_at[0], reference_result.blocked_at[0], abs_tol=1e-6
            )


# ============================================================================ #
# 3) WindSimulation: Wise (1970) bina-yüksekliği bağımlı wake modeli
# ============================================================================ #


class TestWindSimulationWakeModel:
    def test_taller_building_produces_longer_wake(self):
        short_poly = Polygon(
            points=[
                Point2D(20, 20),
                Point2D(28, 20),
                Point2D(28, 28),
                Point2D(20, 28),
            ]
        )
        tall_poly = Polygon(
            points=[
                Point2D(20, 20),
                Point2D(28, 20),
                Point2D(28, 28),
                Point2D(20, 28),
            ]
        )

        field_short = WindSimulation.simulate(
            width=40,
            height=40,
            cell_size_m=2.0,
            obstacles=[short_poly],
            free_stream_speed=5.0,
            free_stream_direction_deg=0.0,
            heights_m=[3.0],
        )
        field_tall = WindSimulation.simulate(
            width=40,
            height=40,
            cell_size_m=2.0,
            obstacles=[tall_poly],
            free_stream_speed=5.0,
            free_stream_direction_deg=0.0,
            heights_m=[30.0],
        )

        # Rüzgar +y yönünde estiği için downstream, obstacle'ın "arkasında"
        # (daha yüksek row) kalan hücrelerdeki toplam hız-eksikliğini karşılaştır.
        def wake_deficit_sum(field):
            total = 0.0
            for row in range(20, 40):
                for col in range(10, 20):
                    speed, _ = field.at(col, row)
                    total += 5.0 - speed
            return total

        assert wake_deficit_sum(field_tall) > wake_deficit_sum(field_short)

    def test_default_height_backward_compatible_when_heights_omitted(self):
        poly = Polygon(points=[Point2D(20, 20), Point2D(28, 20), Point2D(28, 28), Point2D(20, 28)])
        field = WindSimulation.simulate(
            width=40,
            height=40,
            cell_size_m=2.0,
            obstacles=[poly],
            free_stream_speed=5.0,
            free_stream_direction_deg=0.0,
        )
        assert field.width == 40 and field.height == 40


# ============================================================================ #
# 4) NoiseSimulation: ISO 9613-2 basitleştirilmiş iki-terimli sönümleme
# ============================================================================ #


class TestNoiseSimulationISO9613:
    def test_matches_manual_iso_formula(self):
        source_db = 90.0
        source = (0.0, 0.0, 0.0)
        receiver = (100.0, 0.0, 0.0)
        alpha = 2.0  # dB/km

        result = NoiseSimulation.spl_at(
            source_db,
            source,
            receiver,
            atmospheric_absorption_db_per_km=alpha,
        )

        distance = 100.0
        a_div = 20.0 * math.log10(distance / 1.0)
        a_atm = alpha * (distance / 1000.0)
        expected = source_db - a_div - a_atm

        assert math.isclose(result.spl_db, expected, rel_tol=1e-9)

    def test_atmospheric_absorption_increases_with_distance(self):
        near = NoiseSimulation.spl_at(90.0, (0, 0, 0), (50.0, 0, 0))
        far = NoiseSimulation.spl_at(90.0, (0, 0, 0), (500.0, 0, 0))
        assert far.spl_db < near.spl_db

    def test_default_atmospheric_absorption_is_documented_constant(self):
        assert math.isclose(
            NoiseSimulation.DEFAULT_ATMOSPHERIC_ABSORPTION_DB_PER_KM, 1.5, rel_tol=1e-9
        )
