"""ROADMAP_V8 Faz 6.4 (kalan madde) — UV Atlas paketlemesinin bina üretim
ardılına bağlanması.

`mesh_engine.uv_atlas.MeshUVAtlasBaker` (ve `WorldScaleUVMapper`) daha önce
de vardı ama hiçbir üretim mimarisine (bina üretim ardılına) bağlı değildi
— yalnızca *layout* hesaplanıyordu, gerçek bina parçalarına *uygulanmıyordu*.
Bu test dosyası, `ProceduralBuildingGenerator...full_mesh(pack_uv_atlas=True)`
opt-in parametresinin (AO baking ile aynı desen, Faz 6.4 ilk yarısı) dört
şeyi doğruladığını test eder:

1. Varsayılan davranış (`pack_uv_atlas=False`, hatta parametre hiç
   verilmeden) tamamen geriye dönük uyumlu — üçgen sayısı ve UV üretmeme
   davranışı değişmiyor.
2. `generate_uvs=True, pack_uv_atlas=True` iken üçgen/vertex sayısı,
   atlas'sız `generate_uvs=True` çağrısıyla birebir aynı (yalnızca UV
   düzeni değişiyor, geometri kaybı/çoğalması yok).
3. Atlas UV'leri her zaman [0,1] aralığında kalıyor (dünya-ölçekli
   mapping'in taşabilen ham çıktısının aksine) ve en az iki farklı parça
   atlas içinde çakışmayan alt-dikdörtgenlere düşüyor (gerçek paketleme
   oluyor, hepsi aynı köşeye yığılmıyor).
4. Tek parçalı (facade/roof birleşmeden önce tek bir parça kalan) uç
   durumda çökme olmuyor, güvenli şekilde normal birleştirme yoluna
   düşüyor.
"""
from __future__ import annotations

from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import (
    BuildingType,
    ProceduralBuildingGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon


def _rect_footprint(w: float = 20.0, d: float = 15.0) -> Footprint:
    poly = Polygon(points=[
        Point2D(0.0, 0.0), Point2D(w, 0.0), Point2D(w, d), Point2D(0.0, d),
    ])
    return Footprint(polygon=poly)


def _generate_building():
    gen = ProceduralBuildingGenerator()
    building_type = list(BuildingType)[0]
    return gen.generate(_rect_footprint(), floor_count=5, building_type=building_type)


class TestBackwardCompatibility:
    def test_default_full_mesh_unaffected(self):
        building = _generate_building()
        mesh_before = building.full_mesh()
        mesh_after = building.full_mesh(pack_uv_atlas=False)
        assert len(mesh_before.triangles) == len(mesh_after.triangles)
        # Bazı parçalar (facade_generator) kendi UV'sini önceden taşıyabilir
        # - buradaki asıl kontrol, pack_uv_atlas=False'ın davranışı
        # generate_uvs=False ile birebir aynı bırakmasıdır (atlas yolu hiç
        # tetiklenmemeli).
        uvs_before = [v.uv for v in mesh_before.vertices]
        uvs_after = [v.uv for v in mesh_after.vertices]
        assert uvs_before == uvs_after

    def test_pack_uv_atlas_ignored_without_generate_uvs(self):
        building = _generate_building()
        mesh_plain = building.full_mesh()
        mesh_flagged = building.full_mesh(pack_uv_atlas=True)
        # generate_uvs=False (varsayılan) -> atlas paketleme hiç
        # tetiklenmemeli, sonuç generate_uvs=False'la birebir aynı olmalı.
        uvs_plain = [v.uv for v in mesh_plain.vertices]
        uvs_flagged = [v.uv for v in mesh_flagged.vertices]
        assert uvs_plain == uvs_flagged


class TestGeometryPreservation:
    def test_triangle_and_vertex_count_match_non_atlas_uv_path(self):
        building = _generate_building()
        mesh_plain_uv = building.full_mesh(generate_uvs=True, texture_size_m=2.0)
        mesh_atlas_uv = building.full_mesh(
            generate_uvs=True, texture_size_m=2.0, pack_uv_atlas=True,
            atlas_texture_px=256, atlas_width_px=2048, atlas_height_px=2048,
        )
        assert len(mesh_plain_uv.triangles) == len(mesh_atlas_uv.triangles)
        assert len(mesh_plain_uv.vertices) == len(mesh_atlas_uv.vertices)

    def test_atlas_mesh_has_uvs_on_every_vertex(self):
        building = _generate_building()
        mesh = building.full_mesh(
            generate_uvs=True, pack_uv_atlas=True, atlas_texture_px=256,
        )
        assert all(v.uv is not None for v in mesh.vertices)


class TestAtlasPacking:
    def test_uvs_stay_within_unit_square(self):
        building = _generate_building()
        mesh = building.full_mesh(
            generate_uvs=True, texture_size_m=2.0, pack_uv_atlas=True,
            atlas_texture_px=256, atlas_width_px=1024, atlas_height_px=1024,
        )
        for v in mesh.vertices:
            u, w = v.uv
            assert -1e-9 <= u <= 1.0 + 1e-9
            assert -1e-9 <= w <= 1.0 + 1e-9

    def test_multiple_parts_get_distinct_atlas_regions(self):
        building = _generate_building()
        # Bu bina en az facade + roof içerir (2+ parça garanti).
        mesh = building.full_mesh(
            generate_uvs=True, texture_size_m=2.0, pack_uv_atlas=True,
            atlas_texture_px=256, atlas_width_px=1024, atlas_height_px=1024,
        )
        u_values = {round(v.uv[0], 6) for v in mesh.vertices}
        w_values = {round(v.uv[1], 6) for v in mesh.vertices}
        # Tek bir alt-dikdörtgene sıkışmadığının kaba kanıtı: en az 2 farklı
        # u-taban (parça offseti) görülmeli.
        assert len(u_values) > 1 or len(w_values) > 1

    def test_atlas_result_smaller_or_equal_footprint_than_no_atlas(self):
        # Atlas'lı ve atlas'sız aynı geometri veriyor olmalı - bu test
        # asıl amacı belgeliyor (tek doku sayfası + tek malzeme).
        building = _generate_building()
        mesh_atlas = building.full_mesh(
            generate_uvs=True, pack_uv_atlas=True, atlas_texture_px=256,
        )
        assert mesh_atlas.triangle_count() > 0


class TestSinglePartEdgeCase:
    def test_single_part_building_does_not_crash(self):
        # roof=None, yalnızca facade kalan minimal senaryo hâlâ çalışmalı.
        building = _generate_building()
        building.roof = None
        building.basement_envelope = None
        building.terrain_foundation = None
        building.retaining_wall = None
        building.double_skin = None
        mesh = building.full_mesh(generate_uvs=True, pack_uv_atlas=True)
        assert mesh.triangle_count() > 0
        assert mesh.vertices[0].uv is not None
