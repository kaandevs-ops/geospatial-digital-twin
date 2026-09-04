"""ROADMAP_V8 Faz 6.4 — UV Atlas + AO Baking (kısa-vade genişletme).

`lighting.AmbientOcclusionBaker.bake_vertex_ao` daha önce de vardı ama hiçbir
üretim mimarisine (bina üretim ardılına) bağlı değildi ve büyük mesh'lerde
brute-force O(vertex*sample*triangle) maliyeti pratik değildi. Bu test
dosyası üç şeyi doğrular:

1. `spatial_prune=True` (yeni varsayılan) `spatial_prune=False` ile
   BİREBİR AYNI AO değerlerini üretir (yalnızca hız optimizasyonu, sonucu
   değiştirmez).
2. `AmbientOcclusionBaker.apply_vertex_ao` gerçek bir mesh'in
   `Vertex3D.ao` alanlarını doldurur, girdi mesh'i değiştirmez (clone).
3. `ProceduralBuildingGenerator...full_mesh(bake_ao=True)` uçtan uca gerçek
   bir binada çalışır, varsayılan `bake_ao=False` davranışını bozmaz
   (geriye dönük tam uyumlu).
"""

from __future__ import annotations

from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import (
    ProceduralBuildingGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.lighting import AmbientOcclusionBaker
from harita.mesh_engine import Mesh3D, MeshBuilder, NormalGenerator, Vertex3D


def _cube_mesh() -> Mesh3D:
    mesh = MeshBuilder.build_box(2.0, 2.0, 2.0, name="ao_test_cube")
    NormalGenerator.compute_face_averaged_normals(mesh)
    return mesh


def _concave_two_box_mesh() -> Mesh3D:
    """Tek başına dışbükey bir kutuda kendi kendini gölgeleyecek hiçbir şey
    yoktur (tüm hemisphere ışınları boşluğa gider) - AO testinde gerçek bir
    occlusion görmek için iki kutuyu birbirine çok yakın (aralarında dar bir
    boşluk/köşe oluşturacak şekilde) birleştiriyoruz."""
    from harita.mesh_engine import MeshMerger

    box_a = MeshBuilder.build_box(2.0, 2.0, 2.0, name="box_a")
    box_b = MeshBuilder.build_box(2.0, 2.0, 2.0, name="box_b")
    # box_b'yi box_a'nın hemen yanına, dar bir "L" köşesi oluşturacak şekilde
    # kaydır (0.3m çakışma/yakınlık - içbükey köşe garanti).
    for v in box_b.vertices:
        v.x += 1.7
        v.z += 1.7
    merged = MeshMerger.merge([box_a, box_b], name="concave_l")
    NormalGenerator.compute_face_averaged_normals(merged)
    return merged


def _l_shaped_footprint() -> Footprint:
    poly = Polygon(
        [
            Point2D(0, 0),
            Point2D(10, 0),
            Point2D(10, 4),
            Point2D(5, 4),
            Point2D(5, 8),
            Point2D(0, 8),
        ]
    )
    return Footprint(polygon=poly)


class TestSpatialPruneEquivalence:
    def test_pruned_and_bruteforce_give_identical_ao(self):
        mesh = _concave_two_box_mesh()
        pruned = AmbientOcclusionBaker.bake_vertex_ao(
            mesh,
            sample_count=6,
            max_distance=4.0,
            spatial_prune=True,
        )
        brute = AmbientOcclusionBaker.bake_vertex_ao(
            mesh,
            sample_count=6,
            max_distance=4.0,
            spatial_prune=False,
        )
        assert len(pruned) == len(brute) == mesh.vertex_count()
        for p, b in zip(pruned, brute):
            assert abs(p - b) < 1e-9
        # En az bir occlusion algılanmalı (aksi halde eşitlik testi anlamsız
        # bir şekilde iki tarafın da "hep 1.0" döndürmesiyle geçebilirdi).
        assert any(p < 1.0 for p in pruned)

    def test_pruned_is_not_slower_in_result_quality_all_in_range(self):
        mesh = _cube_mesh()
        values = AmbientOcclusionBaker.bake_vertex_ao(mesh, sample_count=8)
        assert all(0.0 <= v <= 1.0 for v in values)


class TestApplyVertexAO:
    def test_apply_sets_ao_field_on_clone(self):
        mesh = _concave_two_box_mesh()
        original_ao = [v.ao for v in mesh.vertices]
        assert all(a == 1.0 for a in original_ao), "varsayılan ao 1.0 olmalı"

        result = AmbientOcclusionBaker.apply_vertex_ao(
            mesh,
            sample_count=8,
            max_distance=3.0,
        )

        # Girdi mesh değişmedi (clone üzerinde çalışıldı).
        assert all(v.ao == 1.0 for v in mesh.vertices)
        # İki kutunun birleştiği içbükey köşede en az bir vertex'in AO'su
        # 1.0'ın altına düşmeli (komşu geometri onu kısmen kapatıyor).
        assert any(v.ao < 1.0 for v in result.vertices)
        assert all(0.0 <= v.ao <= 1.0 for v in result.vertices)

    def test_apply_computes_normals_if_missing(self):
        mesh = MeshBuilder.build_box(1.0, 1.0, 1.0, name="no_normals")
        for v in mesh.vertices:
            v.normal = None
        result = AmbientOcclusionBaker.apply_vertex_ao(mesh, sample_count=4)
        assert result.vertices[0].normal is not None


class TestVertex3DAOFieldBackwardCompatible:
    def test_default_ao_is_one(self):
        v = Vertex3D(0.0, 0.0, 0.0)
        assert v.ao == 1.0

    def test_positional_construction_still_works_without_ao(self):
        v = Vertex3D(1.0, 2.0, 3.0, (0, 0, 1), None, (0.5, 0.5))
        assert v.ao == 1.0
        assert v.uv == (0.5, 0.5)


class TestProceduralGeneratorAOIntegration:
    def test_bake_ao_false_is_default_and_unchanged_behaviour(self):
        building = ProceduralBuildingGenerator.generate(
            _l_shaped_footprint(),
            floor_count=2,
            seed=7,
        )
        mesh = building.full_mesh()
        assert mesh.triangles
        assert all(v.ao == 1.0 for v in mesh.vertices)

    def test_bake_ao_true_produces_varying_ao_on_real_building(self):
        building = ProceduralBuildingGenerator.generate(
            _l_shaped_footprint(),
            floor_count=2,
            seed=7,
        )
        mesh = building.full_mesh(
            generate_uvs=True,
            bake_ao=True,
            ao_sample_count=4,
            ao_max_distance=3.0,
        )
        assert mesh.triangles
        ao_values = [v.ao for v in mesh.vertices]
        assert all(0.0 <= a <= 1.0 for a in ao_values)
        # Gerçek bir L-binada iç köşe/girinti olduğu için tüm AO'ların
        # 1.0'da sabit kalması (yani hiç occlusion algılanmaması) regresyon
        # sayılır.
        assert any(a < 1.0 for a in ao_values)
        # UV de aynı çağrıda üretilmiş olmalı (iki opt-in özellik birlikte
        # çalışabilmeli).
        assert all(v.uv is not None for v in mesh.vertices)

    def test_bake_ao_does_not_mutate_facade_or_roof_source_meshes(self):
        building = ProceduralBuildingGenerator.generate(
            _l_shaped_footprint(),
            floor_count=2,
            seed=7,
        )
        facade_ao_before = [v.ao for v in building.facade.mesh.vertices]
        building.full_mesh(bake_ao=True, ao_sample_count=4)
        facade_ao_after = [v.ao for v in building.facade.mesh.vertices]
        assert facade_ao_before == facade_ao_after == [1.0] * len(facade_ao_before)
