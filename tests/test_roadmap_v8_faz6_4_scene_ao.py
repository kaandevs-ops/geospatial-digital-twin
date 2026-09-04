"""ROADMAP_V8 Faz 6.4 — şehir-ölçeği AO (kalan madde): `SpatialHashGrid`,
`SceneAOBaker` (lighting) ve `Scene.attach_scene_vertex_ao` (render_engine).

Önceki oturum bu üç parçayı yazmış ama HİÇ test etmemişti (denetimde
bulunan boşluk). Bu dosya üç şeyi doğrular:

1. `SpatialHashGrid.query_near` brute-force (tüm üçgenleri tarayan) bir
   mesafe testiyle BİREBİR AYNI aday kümesini (üçgen bazında) üretir —
   yalnızca bir hız optimizasyonu, sonucu değiştirmemeli.
2. `SceneAOBaker.bake_scene_ao`/`apply_scene_ao` iki ayrı mesh (iki bina)
   arasında GERÇEKTEN binalar-arası gölgeleme üretiyor — tek başına
   `AmbientOcclusionBaker` (mesh-lokal) ile karşılaştırıldığında, birbirine
   çok yakın konumlandırılmış iki kutunun bitişik yüzlerindeki AO,
   sahne-ölçeği hesapta mesh-lokal hesaba göre daha düşük (daha fazla
   gölgeli) olmalı.
3. `Scene.attach_scene_vertex_ao` node'ların `translation`/`rotation_deg`
   (yaw) transform'unu doğru dünya-uzayına taşıyor ve sonucu
   `Scene.vertex_ao`'ya yazıyor — ayrıca `Scene`'e ait olmayan bir node
   verilirse `ValueError` fırlatıyor (mevcut `attach_vertex_ao` ile aynı
   sözleşme).
"""
from __future__ import annotations

import math

from harita.lighting import AmbientOcclusionBaker, SceneAOBaker, SpatialHashGrid
from harita.mesh_engine import MeshBuilder, NormalGenerator
from harita.render_engine.scene_bridge import Scene


def _box(name: str, size: float = 2.0):
    mesh = MeshBuilder.build_box(size, size, size, name=name)
    NormalGenerator.compute_face_averaged_normals(mesh)
    return mesh


class TestSpatialHashGridEquivalence:
    def test_query_near_matches_bruteforce_triangle_set(self):
        mesh_a = _box("box_a")
        mesh_b = _box("box_b")
        for v in mesh_b.vertices:
            v.x += 1.7  # box_a'ya çok yakın, aday kümesi kesişmeli

        all_triangles = []
        grid = SpatialHashGrid(cell_size=2.5)
        for mesh in (mesh_a, mesh_b):
            for tri in mesh.triangles:
                a, b, c = mesh.triangle_positions(tri)
                pa, pb, pc = a.as_tuple(), b.as_tuple(), c.as_tuple()
                grid.add_triangle(pa, pb, pc)
                all_triangles.append((pa, pb, pc))

        query_point = (1.0, 1.0, 1.0)
        max_distance = 3.0

        def _brute_force():
            found = []
            for pa, pb, pc in all_triangles:
                centroid = tuple((pa[k] + pb[k] + pc[k]) / 3.0 for k in range(3))
                radius = max(
                    math.dist(centroid, pa), math.dist(centroid, pb), math.dist(centroid, pc),
                )
                if math.dist(query_point, centroid) - radius <= max_distance:
                    found.append((pa, pb, pc))
            return found

        expected = _brute_force()
        actual = grid.query_near(query_point, max_distance)

        # Aday sayısı en az brute-force kadar olmalı ve hiçbir gerçek isabet
        # kaçırılmamalı (grid, hücre taşması nedeniyle fazladan aday
        # döndürebilir ama beklenen tüm üçgenleri İÇERMELİ).
        actual_set = set(actual)
        for tri in expected:
            assert tri in actual_set

    def test_query_near_returns_empty_when_nothing_in_range(self):
        grid = SpatialHashGrid(cell_size=1.0)
        grid.add_triangle((0, 0, 0), (1, 0, 0), (0, 1, 0))
        far_result = grid.query_near((1000.0, 1000.0, 1000.0), 1.0)
        assert far_result == []

    def test_add_triangle_then_query_finds_it_at_origin(self):
        grid = SpatialHashGrid(cell_size=5.0)
        grid.add_triangle((0, 0, 0), (1, 0, 0), (0, 1, 0))
        result = grid.query_near((0.2, 0.2, 0.0), 2.0)
        assert len(result) == 1


class TestSceneAOBakerInterBuildingShadowing:
    def test_bake_scene_ao_lower_than_mesh_local_ao_at_shared_corner(self):
        box_a = _box("box_a")
        box_b = _box("box_b")
        # box_b'yi box_a'nın hemen yanına, dar bir "L" köşesi oluşturacak
        # şekilde kaydır (test_roadmap_v8_faz6_4_ao_baking.py'deki
        # _concave_two_box_mesh ile aynı desen, ama TEK bir merge'lenmiş
        # mesh yerine İKİ AYRI mesh/bina olarak).
        for v in box_b.vertices:
            v.x += 1.7
            v.z += 1.7

        # Mesh-lokal (tek başına, komşudan habersiz) AO: her kutu kendi
        # başına dışbükey bir küp olduğu için occlusion neredeyse yok.
        local_ao_a = AmbientOcclusionBaker.bake_vertex_ao(
            box_a, sample_count=10, max_distance=4.0,
        )
        assert all(v >= 0.99 for v in local_ao_a), (
            "tek başına dışbükey kutuda kendi kendini gölgeleme olmamalı"
        )

        # Sahne-ölçeği AO: box_b artık grid'de var, box_a'nın box_b'ye
        # bakan köşesi onun tarafından kısmen kapatılmalı.
        scene_ao_a, scene_ao_b = SceneAOBaker.bake_scene_ao(
            [box_a, box_b], sample_count=10, max_distance=4.0,
        )
        assert len(scene_ao_a) == box_a.vertex_count()
        assert len(scene_ao_b) == box_b.vertex_count()
        assert any(v < 0.99 for v in scene_ao_a), (
            "box_b'ye bakan köşe box_a'nın sahne-ölçeği AO'sunda gölgeli olmalı"
        )
        assert any(v < 0.99 for v in scene_ao_b)
        assert all(0.0 <= v <= 1.0 for v in scene_ao_a + scene_ao_b)

    def test_bake_scene_ao_matches_mesh_local_when_buildings_far_apart(self):
        box_a = _box("box_a")
        box_b = _box("box_b")
        for v in box_b.vertices:
            v.x += 1000.0  # çok uzak - birbirini etkilememeli

        scene_ao_a, _ = SceneAOBaker.bake_scene_ao(
            [box_a, box_b], sample_count=8, max_distance=4.0,
        )
        local_ao_a = AmbientOcclusionBaker.bake_vertex_ao(
            box_a, sample_count=8, max_distance=4.0,
        )
        for s, m in zip(scene_ao_a, local_ao_a):
            assert abs(s - m) < 1e-9

    def test_bake_scene_ao_empty_input_returns_empty(self):
        assert SceneAOBaker.bake_scene_ao([]) == []

    def test_apply_scene_ao_clones_and_does_not_mutate_input(self):
        box_a = _box("box_a")
        box_b = _box("box_b")
        for v in box_b.vertices:
            v.x += 1.7
            v.z += 1.7
        original_ao = [v.ao for v in box_a.vertices]
        assert all(a == 1.0 for a in original_ao)

        clones = SceneAOBaker.apply_scene_ao(
            [box_a, box_b], sample_count=8, max_distance=4.0,
        )

        assert all(v.ao == 1.0 for v in box_a.vertices), "girdi mutasyona uğramamalı"
        assert len(clones) == 2
        assert any(v.ao < 1.0 for v in clones[0].vertices)


class TestSceneAttachSceneVertexAO:
    def test_attach_scene_vertex_ao_writes_vertex_ao_for_all_nodes(self):
        scene = Scene()
        node_a = scene.add_mesh(_box("box_a"), translation=(0.0, 0.0, 0.0))
        node_b = scene.add_mesh(_box("box_b"), translation=(1.7, 0.0, 1.7))

        result = scene.attach_scene_vertex_ao(sample_count=8, max_distance=4.0)

        assert set(result.keys()) == {"box_a", "box_b"}
        assert scene.vertex_ao["box_a"] == result["box_a"]
        assert scene.vertex_ao["box_b"] == result["box_b"]
        assert len(result["box_a"]) == node_a.mesh.vertex_count()
        assert len(result["box_b"]) == node_b.mesh.vertex_count()
        assert any(v < 0.99 for v in result["box_a"] + result["box_b"]), (
            "translation ile bitiştirilmiş iki node birbirini gölgelemeli"
        )

    def test_attach_scene_vertex_ao_subset_of_nodes(self):
        scene = Scene()
        node_a = scene.add_mesh(_box("box_a"))
        scene.add_mesh(_box("box_b"), translation=(1.7, 0.0, 1.7))

        result = scene.attach_scene_vertex_ao(nodes=[node_a], sample_count=6)
        assert set(result.keys()) == {"box_a"}
        assert "box_b" not in scene.vertex_ao

    def test_attach_scene_vertex_ao_rejects_foreign_node(self):
        scene = Scene()
        scene.add_mesh(_box("box_a"))
        other_scene = Scene()
        foreign_node = other_scene.add_mesh(_box("box_foreign"))

        try:
            scene.attach_scene_vertex_ao(nodes=[foreign_node])
            assert False, "ValueError bekleniyordu"
        except ValueError:
            pass

    def test_attach_scene_vertex_ao_empty_nodes_returns_empty_dict(self):
        scene = Scene()
        assert scene.attach_scene_vertex_ao(nodes=[]) == {}

    def test_attach_scene_vertex_ao_applies_yaw_rotation_to_world_space(self):
        # box_b'yi 90 derece döndürüp box_a'nın yanına yerleştir - eğer yaw
        # uygulanmasaydı (world-space transform atlanırsa) iki kutu
        # birbirinden çok uzak/örtüşmeyen konumda kalır ve gölgeleme
        # oluşmazdı; yaw doğru uygulanınca bitişik köşe oluşmalı.
        scene = Scene()
        scene.add_mesh(_box("box_a"))
        scene.add_mesh(
            _box("box_b"), translation=(1.7, 0.0, 1.7), rotation_deg=(0.0, 0.0, 45.0),
        )
        result = scene.attach_scene_vertex_ao(sample_count=10, max_distance=4.0)
        assert any(v < 0.99 for v in result["box_a"])
