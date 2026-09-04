"""
ROADMAP V5 - Track M / M1: Mesh Optimizasyon Katmanı testleri.

M1.1 (LOD zinciri + hysteresis), M1.2 (static batching + instancing),
M1.3 (topoloji onarımı - auto_repair) için kabul kriteri testleri.
"""

from __future__ import annotations

from harita.mesh_engine import Mesh3D, MeshRepair, Vertex3D
from harita.mesh_engine.batching import (
    DrawCallEstimator,
    InstanceMeshBaker,
    InstanceTransform,
    StaticMeshBatcher,
)
from harita.mesh_engine.lod import (
    LODChainBuilder,
    LODLevel,
    LODSelector,
)


def _pyramid(n_base: int = 12) -> Mesh3D:
    """n_base kenarli bir piramit (taban + tepe) - basit ama yeterince
    ucgen iceren test mesh'i."""
    import math

    verts = [
        Vertex3D(
            math.cos(2 * math.pi * i / n_base) * 5, math.sin(2 * math.pi * i / n_base) * 5, 0.0
        )
        for i in range(n_base)
    ]
    verts.append(Vertex3D(0.0, 0.0, 8.0))  # apex
    apex = n_base
    tris = []
    for i in range(n_base):
        tris.append((i, (i + 1) % n_base, apex))
    # taban (fan triangulation)
    for i in range(1, n_base - 1):
        tris.append((0, i, i + 1))
    return Mesh3D(vertices=verts, triangles=tris, name="pyramid")


# --------------------------------------------------------------------- #
# M1.3 - Topoloji onarımı
# --------------------------------------------------------------------- #


def test_m13_auto_repair_removes_degenerate_and_duplicate_vertices():
    verts = [
        Vertex3D(0, 0, 0),
        Vertex3D(1, 0, 0),
        Vertex3D(1, 1, 0),
        Vertex3D(0, 1, 0),
        Vertex3D(0.0000001, 0.0000001, 0.0),  # (0,0,0)'a çok yakın - weld ile birleşmeli
    ]
    tris = [(0, 1, 2), (0, 2, 3), (0, 0, 1)]  # son üçgen dejenere (0==0)
    m = Mesh3D(vertices=verts, triangles=tris, name="t")
    repaired = MeshRepair.auto_repair(m, fill_holes=False)
    assert len(repaired.triangles) == 2
    for tri in repaired.triangles:
        assert len(set(tri)) == 3


def test_m13_auto_repair_produces_watertight_pyramid():
    p = _pyramid()
    repaired = MeshRepair.auto_repair(p)
    assert MeshRepair.is_manifold(repaired)
    assert MeshRepair.find_boundary_edges(repaired) == []


def test_m13_fix_non_manifold_edges_splits_shared_edge():
    # Bir kenari 3 ucgenin paylastigi bozuk (non-manifold) mesh.
    verts = [
        Vertex3D(0, 0, 0),
        Vertex3D(1, 0, 0),
        Vertex3D(0.5, 1, 0),
        Vertex3D(0.5, -1, 0),
        Vertex3D(0.5, 0, 1),
    ]
    tris = [(0, 1, 2), (0, 1, 3), (0, 1, 4)]  # (0,1) kenari 3 kez kullanildi
    m = Mesh3D(vertices=verts, triangles=tris, name="nm")
    assert not MeshRepair.is_manifold(m)
    fixed = MeshRepair.fix_non_manifold_edges(m)
    assert MeshRepair.is_manifold(fixed)
    assert len(fixed.triangles) == 3


# --------------------------------------------------------------------- #
# M1.1 - LOD zinciri
# --------------------------------------------------------------------- #


def test_m11_lod_chain_reduces_triangle_count_progressively():
    mesh = _pyramid(n_base=40)
    chain = LODChainBuilder.build_from_lod0(mesh)
    counts = chain.triangle_counts
    assert counts[LODLevel.LOD0] == len(mesh.triangles)
    assert counts[LODLevel.LOD1] < counts[LODLevel.LOD0]
    assert counts[LODLevel.LOD2] <= counts[LODLevel.LOD1]
    assert counts[LODLevel.LOD3] <= counts[LODLevel.LOD2]
    # Kabul kriteri: uzak LOD'da (LOD3) en az %90 azalma.
    assert chain.triangle_reduction_ratio(LODLevel.LOD3) >= 0.90


def test_m11_lod_selector_hysteresis_prevents_popping_at_threshold():
    sel = LODSelector(thresholds={LODLevel.LOD0: 0.0, LODLevel.LOD1: 60.0}, hysteresis_band=0.10)
    assert sel.select(59.0) == LODLevel.LOD0
    assert sel.select(60.5) == LODLevel.LOD1
    # Eşiğin hemen altına (60m'nin biraz altına) dönmek LOD0'a geri
    # DÜŞÜRMEMELİ (hysteresis bandı: retreat point = 60*0.9=54).
    assert sel.select(55.0) == LODLevel.LOD1
    # Gerçekten geri çekilme noktasının altına inince LOD0'a döner.
    assert sel.select(50.0) == LODLevel.LOD0


def test_m11_lod_chain_from_variants_fills_missing_levels():
    mesh = _pyramid()
    custom_lod1 = mesh.clone()
    chain = LODChainBuilder.build_from_variants(lod0=mesh, lod1=custom_lod1)
    assert chain.meshes[LODLevel.LOD1] is custom_lod1
    assert LODLevel.LOD2 in chain.meshes
    assert LODLevel.LOD3 in chain.meshes


# --------------------------------------------------------------------- #
# M1.2 - Batching / Instancing
# --------------------------------------------------------------------- #


def test_m12_static_batching_reduces_draw_calls_by_at_least_60_percent():
    mesh = _pyramid(n_base=6)
    groups = {"beton": [mesh.clone() for _ in range(10)], "cam": [mesh.clone() for _ in range(5)]}
    reduction = StaticMeshBatcher.reduction_ratio(groups)
    assert reduction >= 0.60
    batched = StaticMeshBatcher.batch_by_material(groups)
    assert len(batched) == 2
    assert len(batched["beton"].triangles) == len(mesh.triangles) * 10


def test_m12_instance_baker_transforms_geometry_correctly():
    base = Mesh3D(
        vertices=[Vertex3D(0, 0, 0), Vertex3D(1, 0, 0), Vertex3D(0, 1, 0)],
        triangles=[(0, 1, 2)],
        name="tri",
    )
    baked = InstanceMeshBaker.bake_merged(base, [InstanceTransform((10.0, 0.0, 0.0))])
    assert baked.vertices[0].x == 10.0
    instances = InstanceMeshBaker.bake_instances(
        base, [InstanceTransform((0, 0, 0)), InstanceTransform((5, 0, 0))]
    )
    assert len(instances) == 2
    assert instances[1].vertices[0].x == 5.0


def test_m12_draw_call_estimator_matches_batching_math():
    pairs = [("beton", 40), ("cam", 20), ("metal", 40)]
    assert DrawCallEstimator.estimate(pairs) == 100
    assert DrawCallEstimator.estimate_after_batching(pairs) == 3
    assert DrawCallEstimator.reduction_percent(pairs) > 60.0
