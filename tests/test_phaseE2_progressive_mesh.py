"""
Faz E2 — Mesh Engine: Progressive Mesh ve Gerçek Zamanlı LOD Geçiş Animasyonu
==============================================================================

Roadmap V4 Track E, Faz E2. `mesh_engine.progressive_mesh.ProgressiveMesh`'in
D1'in QEM edge-collapse sırasını doğru sakladığını ve iki LOD seviyesi
arasında ani "pop" yerine sürekli (geomorph) bir ara-mesh üretebildiğini
doğrular. Kabul kriteri (ROADMAP_V4.md, Faz E2): "ardışık iki LOD seviyesi
arası üretilen ara-mesh'in üçgen sayısı, iki uç seviye arasında monoton
artar/azalır". Ayrıca `render_engine.scene_bridge.select_lod_mesh_geomorph`
köprüsünün geriye uyumluluğunu (progressive olmayan node'larda ani-pop
davranışının değişmediğini) doğrular.
"""

from __future__ import annotations

import pytest

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import MeshBuilder, MeshSimplifier
from harita.mesh_engine.progressive_mesh import ProgressiveMesh
from harita.render_engine.scene_bridge import (
    Scene,
    select_lod_for_distance,
    select_lod_mesh_geomorph,
)


def _building_mesh():
    poly = Polygon(points=[
        Point2D(0, 0), Point2D(12, 0), Point2D(12, 8), Point2D(6, 12), Point2D(0, 8),
    ])
    return MeshBuilder.extrude_polygon(poly, base_z=0.0, height=6.0, name="test_building")


class TestProgressiveMeshHistory:
    def test_build_produces_monotonic_non_increasing_triangle_counts(self):
        mesh = _building_mesh()
        pm = ProgressiveMesh.build(mesh, min_triangle_ratio=0.2)
        counts = [pm.triangle_count_at_step(k) for k in range(len(pm.steps) + 1)]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] == len(mesh.triangles)
        assert counts[-1] <= max(1, int(len(mesh.triangles) * 0.2))

    def test_mesh_at_step_zero_equals_base(self):
        mesh = _building_mesh()
        pm = ProgressiveMesh.build(mesh, min_triangle_ratio=0.3)
        m0 = pm.mesh_at_step(0)
        assert len(m0.triangles) == len(mesh.triangles)
        assert len(m0.vertices) == len(mesh.vertices)

    def test_mesh_at_step_full_matches_direct_qem_triangle_count(self):
        """Progressive mesh geçmişinin sonu, D1'in doğrudan `MeshSimplifier.
        simplify()` çağrısıyla aynı üçgen sayısına ulaşmalı (aynı QEM
        sırasının yeniden kullanıldığının kanıtı)."""
        mesh = _building_mesh()
        ratio = 0.3
        pm = ProgressiveMesh.build(mesh, min_triangle_ratio=ratio)
        direct = MeshSimplifier.simplify(mesh, ratio)
        coarsest = pm.mesh_at_step(len(pm.steps))
        assert len(coarsest.triangles) == len(direct.triangles)

    def test_interpolate_keeps_topology_constant_mid_step(self):
        """Geomorph sırasında (t in (0,1)) topoloji SABİT kalmalı - yalnızca
        t=1'e ulaşınca ayrık üçgen sayısı düşmeli (klasik geomorph tekniği)."""
        mesh = _building_mesh()
        pm = ProgressiveMesh.build(mesh, min_triangle_ratio=0.2)
        step0_before = pm.steps[0].triangles_before
        for t in (0.0, 0.25, 0.5, 0.75, 0.99):
            inter = pm.interpolate(0, t)
            assert len(inter.triangles) == step0_before

    def test_interpolate_between_counts_is_monotonic_across_full_range(self):
        """Kabul kriteri: iki uç LOD seviyesi arasında üretilen tüm ara-mesh
        üçgen sayıları, t arttıkça monoton azalır (ya da eşit kalır)."""
        mesh = _building_mesh()
        pm = ProgressiveMesh.build(mesh, min_triangle_ratio=0.2)
        fine_count = pm.triangle_count_at_step(0)
        coarse_count = pm.triangle_count_at_step(len(pm.steps))

        counts = []
        for i in range(21):
            t = i / 20.0
            m = pm.interpolate_between_counts(fine_count, coarse_count, t)
            counts.append(len(m.triangles))

        assert counts[0] == fine_count
        assert counts[-1] == coarse_count
        for a, b in zip(counts, counts[1:]):
            assert b <= a, "üçgen sayısı monoton azalmalı (pop olmadan)"

    def test_invalid_ratio_raises(self):
        mesh = _building_mesh()
        with pytest.raises(ValueError):
            ProgressiveMesh.build(mesh, min_triangle_ratio=0.0)
        with pytest.raises(ValueError):
            ProgressiveMesh.build(mesh, min_triangle_ratio=1.5)


class TestSceneBridgeGeomorphIntegration:
    def test_add_mesh_with_lod_default_has_no_progressive_backward_compat(self):
        mesh = _building_mesh()
        scene = Scene()
        node = scene.add_mesh_with_lod(mesh)
        assert node.progressive is None
        # use_progressive=False iken select_lod_mesh_geomorph, eski ani-pop
        # davranışıyla (select_lod_for_distance) birebir aynı sonucu vermeli.
        for d in (0.0, 60.0, 200.0, 1000.0):
            assert select_lod_mesh_geomorph(node, d) is select_lod_for_distance(node, d)

    def test_add_mesh_with_lod_progressive_transition_is_smooth(self):
        mesh = _building_mesh()
        scene = Scene()
        node = scene.add_mesh_with_lod(
            mesh,
            lod_ratios=(1.0, 0.5, 0.25),
            lod_distances=(50.0, 150.0, float("inf")),
            use_progressive=True,
        )
        assert node.progressive is not None

        distances = [d for d in range(0, 260, 5)]
        counts = [len(select_lod_mesh_geomorph(node, d).triangles) for d in distances]
        for a, b in zip(counts, counts[1:]):
            assert b <= a, "kamera uzaklaştıkça üçgen sayısı asla artmamalı"

    def test_progressive_node_matches_discrete_lod_at_thresholds(self):
        """Bant sınırlarında (tam eşik mesafesinde), geomorph sonucu ayrık
        LOD seviyesiyle aynı üçgen sayısına sahip olmalı (süreklilik garantisi)."""
        mesh = _building_mesh()
        scene = Scene()
        node = scene.add_mesh_with_lod(
            mesh,
            lod_ratios=(1.0, 0.5, 0.25),
            lod_distances=(50.0, 150.0, float("inf")),
            use_progressive=True,
        )
        for max_dist, discrete_mesh in node.lod_levels:
            if max_dist == float("inf"):
                continue
            smooth_mesh = select_lod_mesh_geomorph(node, max_dist)
            assert len(smooth_mesh.triangles) == len(discrete_mesh.triangles)
