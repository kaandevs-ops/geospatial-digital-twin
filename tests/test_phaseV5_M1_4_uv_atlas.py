"""ROADMAP_V5 - M1.4 (son açık madde): dünya-ölçeği tutarlı UV
(`WorldScaleUVMapper`) + mesh-seviyeli doku atlası (`MeshUVAtlasBaker`)
testleri.

Kabul kriteri (ROADMAP_V5.md, M1.4): "Farklı boyutlarda 10 test
binasında doku tekrar sıklığı (cm/tekrar) sabit kalmalı" + M1.2'nin
"doku atlas'ı: küçük dokuların tek büyük doku sayfasında birleştirilmesi"
maddesinin gerçek UV-yeniden-yazma ile karşılanması (yalnız layout değil).
"""

from __future__ import annotations

import pytest
from harita.mesh_engine import Mesh3D, MeshBuilder
from harita.mesh_engine.uv_atlas import (
    AtlasBakeResult,
    MeshUVAtlasBaker,
    WorldScaleUVMapper,
)

# ---------------------------------------------------------------------- #
# WorldScaleUVMapper
# ---------------------------------------------------------------------- #


class TestWorldScaleUVMapper:
    @pytest.mark.parametrize(
        "width,depth,height",
        [
            (1, 1, 1),
            (2, 2, 3),
            (5, 5, 8),
            (10, 10, 15),
            (20, 20, 30),
            (35, 35, 50),
            (50, 50, 70),
            (75, 75, 100),
            (100, 100, 140),
            (150, 150, 200),
        ],
    )
    def test_texel_density_constant_across_building_sizes(self, width, depth, height):
        """Kabul kriteri: 10 farklı boyuttaki binada doku tekrar sıklığı
        (metre/UV-birimi) `texture_size_m` ile birebir eşleşmeli - bina
        büyüklüğünden bağımsız sabit kalmalı."""
        mesh = MeshBuilder.build_box(width, depth, height, name=f"b_{width}x{height}")
        mapped = WorldScaleUVMapper.box_mapping_world_scale(mesh, texture_size_m=1.0)
        density = WorldScaleUVMapper.measure_texel_density(mapped, texture_size_m=1.0)
        assert density == pytest.approx(1.0, abs=1e-6)

    def test_different_texture_size_m_scales_density_accordingly(self):
        mesh = MeshBuilder.build_box(10, 10, 12, name="b")
        mapped = WorldScaleUVMapper.box_mapping_world_scale(mesh, texture_size_m=2.5)
        density = WorldScaleUVMapper.measure_texel_density(mapped, texture_size_m=2.5)
        assert density == pytest.approx(2.5, abs=1e-6)

    def test_small_and_large_building_share_identical_density(self):
        """Roadmap'in tam olarak şikayet ettiği regresyon: küçük ve büyük
        bina eskiden aynı [0,1] UV aralığına normalize edildiği için farklı
        tekrar sıklığı görüyordu. Artık ikisi de aynı olmalı."""
        small = MeshBuilder.build_box(1, 1, 2, name="small")
        big = MeshBuilder.build_box(100, 100, 140, name="big")
        small_uv = WorldScaleUVMapper.box_mapping_world_scale(small, texture_size_m=1.0)
        big_uv = WorldScaleUVMapper.box_mapping_world_scale(big, texture_size_m=1.0)
        d_small = WorldScaleUVMapper.measure_texel_density(small_uv, 1.0)
        d_big = WorldScaleUVMapper.measure_texel_density(big_uv, 1.0)
        assert d_small == pytest.approx(d_big, abs=1e-6)

    def test_uv_values_exceed_unit_range_for_large_buildings(self):
        """Dünya-ölçeği mapping normalize etmediği için büyük binalarda UV
        [0,1] dışına taşmalı (GPU repeat/tiling ile doğru davranış)."""
        mesh = MeshBuilder.build_box(50, 50, 30, name="big")
        mapped = WorldScaleUVMapper.box_mapping_world_scale(mesh, texture_size_m=1.0)
        max_uv = max(max(abs(v.uv[0]), abs(v.uv[1])) for v in mapped.vertices if v.uv)
        assert max_uv > 1.0

    def test_invalid_texture_size_raises(self):
        mesh = MeshBuilder.build_box(5, 5, 5, name="b")
        with pytest.raises(ValueError):
            WorldScaleUVMapper.box_mapping_world_scale(mesh, texture_size_m=0.0)
        with pytest.raises(ValueError):
            WorldScaleUVMapper.box_mapping_world_scale(mesh, texture_size_m=-1.0)

    def test_computes_normals_when_missing(self):
        mesh = MeshBuilder.build_box(4, 4, 6, name="b")
        for v in mesh.vertices:
            v.normal = None
        mapped = WorldScaleUVMapper.box_mapping_world_scale(mesh, texture_size_m=1.0)
        assert all(v.uv is not None for v in mapped.vertices)

    def test_does_not_mutate_input_mesh(self):
        mesh = MeshBuilder.build_box(3, 3, 4, name="b")
        original_uvs = [v.uv for v in mesh.vertices]
        WorldScaleUVMapper.box_mapping_world_scale(mesh, texture_size_m=1.0)
        assert [v.uv for v in mesh.vertices] == original_uvs

    def test_measure_texel_density_empty_mesh_returns_zero(self):
        empty = Mesh3D(vertices=[], triangles=[], name="empty")
        assert WorldScaleUVMapper.measure_texel_density(empty, 1.0) == 0.0


# ---------------------------------------------------------------------- #
# MeshUVAtlasBaker
# ---------------------------------------------------------------------- #


class TestMeshUVAtlasBaker:
    def test_bake_merges_multiple_parts_into_single_mesh(self):
        parts = [
            ("small", MeshBuilder.build_box(2, 2, 3, name="small"), 128, 128),
            ("medium", MeshBuilder.build_box(6, 6, 8, name="medium"), 256, 256),
            ("large", MeshBuilder.build_box(20, 20, 25, name="large"), 512, 512),
        ]
        result = MeshUVAtlasBaker.bake(parts, atlas_width=2048, atlas_height=2048)
        assert isinstance(result, AtlasBakeResult)
        expected_vertex_count = sum(len(mesh.vertices) for _, mesh, _, _ in parts)
        assert len(result.merged_mesh.vertices) == expected_vertex_count
        assert set(result.entries.keys()) == {"small", "medium", "large"}

    def test_all_remapped_uvs_stay_within_unit_range(self):
        """Draw-call azaltma hedefi: tek doku sayfası ile çizilebilmesi için
        tüm UV'ler [0,1] atlas alanı içinde kalmalı."""
        parts = [
            ("a", MeshBuilder.build_box(3, 3, 4, name="a"), 200, 200),
            ("b", MeshBuilder.build_box(9, 9, 11, name="b"), 300, 150),
        ]
        result = MeshUVAtlasBaker.bake(parts, atlas_width=1024, atlas_height=1024)
        for v in result.merged_mesh.vertices:
            assert v.uv is not None
            assert -1e-9 <= v.uv[0] <= 1.0 + 1e-9
            assert -1e-9 <= v.uv[1] <= 1.0 + 1e-9

    def test_world_scale_uvs_are_wrapped_before_atlas_remap(self):
        """World-scale UV'ler [0,1] dışına taşabiliyor (tiling) - atlas'a
        koyulmadan önce modulo ile sarılmalı, aksi halde atlas dışına
        taşıp komşu doku alt-bölgelerine bulaşır."""
        big = MeshBuilder.build_box(50, 50, 60, name="big")
        world_uv = WorldScaleUVMapper.box_mapping_world_scale(big, texture_size_m=1.0)
        parts = [("big", world_uv, 256, 256)]
        result = MeshUVAtlasBaker.bake(parts, atlas_width=2048, atlas_height=2048)
        entry = result.entries["big"]
        u0, v0 = entry.x / 2048, entry.y / 2048
        u1, v1 = (entry.x + entry.width) / 2048, (entry.y + entry.height) / 2048
        for v in result.merged_mesh.vertices:
            assert u0 - 1e-9 <= v.uv[0] <= u1 + 1e-9
            assert v0 - 1e-9 <= v.uv[1] <= v1 + 1e-9

    def test_vertex_without_uv_falls_back_to_subregion_origin(self):
        mesh = MeshBuilder.build_box(4, 4, 5, name="no_uv")
        for v in mesh.vertices:
            v.uv = None
        parts = [("no_uv", mesh, 128, 128)]
        result = MeshUVAtlasBaker.bake(parts, atlas_width=1024, atlas_height=1024)
        entry = result.entries["no_uv"]
        expected_u0 = entry.x / 1024
        expected_v0 = entry.y / 1024
        for v in result.merged_mesh.vertices:
            assert v.uv == (expected_u0, expected_v0)

    def test_atlas_occupancy_ratio_reported(self):
        parts = [
            ("a", MeshBuilder.build_box(2, 2, 2, name="a"), 100, 100),
            ("b", MeshBuilder.build_box(2, 2, 2, name="b"), 100, 100),
        ]
        result = MeshUVAtlasBaker.bake(parts, atlas_width=1024, atlas_height=1024)
        assert 0.0 < result.atlas.occupancy_ratio() <= 1.0

    def test_empty_parts_list_raises(self):
        with pytest.raises(ValueError):
            MeshUVAtlasBaker.bake([], atlas_width=1024, atlas_height=1024)

    def test_bake_result_merged_mesh_is_valid_mesh3d(self):
        parts = [("a", MeshBuilder.build_box(5, 5, 5, name="a"), 64, 64)]
        result = MeshUVAtlasBaker.bake(parts)
        assert isinstance(result.merged_mesh, Mesh3D)
        assert len(result.merged_mesh.triangles) == len(parts[0][1].triangles)

    def test_ten_varied_size_parts_bake_into_single_atlas(self):
        """M1.4 kabul kriterinin doğrudan karşılığı: 10 farklı boyuttaki
        bina dokusu tek atlas sayfasında birleşebilmeli."""
        parts = []
        for i in range(10):
            size = 1 + i * 12
            mesh = MeshBuilder.build_box(size, size, size * 1.3, name=f"bldg_{i}")
            uv_mesh = WorldScaleUVMapper.box_mapping_world_scale(mesh, texture_size_m=1.0)
            parts.append((f"bldg_{i}", uv_mesh, 64 + i * 16, 64 + i * 16))
        result = MeshUVAtlasBaker.bake(parts, atlas_width=4096, atlas_height=4096)
        assert len(result.entries) == 10
        assert len(result.merged_mesh.vertices) == sum(len(m.vertices) for _, m, _, _ in parts)
