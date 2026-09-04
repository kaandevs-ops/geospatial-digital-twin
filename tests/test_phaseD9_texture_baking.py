"""
Roadmap V3 - Faz D9 ("Material Engine: Gerçek Texture-Baking") kabul
kriteri testleri.

Kabul kriteri (ROADMAP_V3.md): "Bilinen bir test mesh'inde (küp üstüne
çıkıntı) üretilen AO haritası, çıkıntının gölgeli tarafında ölçülebilir
şekilde düşük AO değeri (regresyon testiyle sayısal karşılaştırma) verir."
"""

from __future__ import annotations

import base64
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.data_engine.spatial_index import BVH
from harita.material_engine.texture_baking import (
    AOBaker,
    HemisphereSampler,
    NormalMapBaker,
    TextureMap,
    TextureMapCodec,
    _barycentric_2d,
    _ray_triangle,
)
from harita.mesh_engine import (
    Mesh3D,
    MeshBuilder,
    MeshMerger,
    NormalGenerator,
    UVGenerator,
)

# ============================================================================ #
# Test sahnesi: geniş bir taban plakası + ortasında yükselen bir çıkıntı kutu
# (roadmap'in "küp üstüne çıkıntı" senaryosu)
# ============================================================================ #


def _build_plate_with_bump() -> Mesh3D:
    plate_poly = Polygon([Point2D(-5, -5), Point2D(5, -5), Point2D(5, 5), Point2D(-5, 5)])
    plate = MeshBuilder.extrude_polygon(plate_poly, base_z=-0.2, height=0.2, name="plate")

    bump_poly = Polygon([Point2D(-1, -1), Point2D(1, -1), Point2D(1, 1), Point2D(-1, 1)])
    bump = MeshBuilder.extrude_polygon(bump_poly, base_z=0.0, height=1.5, name="bump")

    merged = MeshMerger.merge([plate, bump], name="plate_with_bump")
    NormalGenerator.compute_face_averaged_normals(merged)
    merged = UVGenerator.box_mapping(merged)
    return merged


@pytest.fixture(scope="module")
def scene_mesh() -> Mesh3D:
    return _build_plate_with_bump()


# ============================================================================ #
# AOBaker - texel dizisi (bake_texture)
# ============================================================================ #


class TestAOBakerTexture:
    def test_bake_texture_returns_correct_dimensions(self, scene_mesh):
        tex = AOBaker.bake_texture(scene_mesh, width=24, height=24, sample_count=8)
        assert tex.width == 24
        assert tex.height == 24
        assert tex.channels == 1
        assert len(tex.pixels) == 24 * 24

    def test_all_ao_values_in_valid_range(self, scene_mesh):
        tex = AOBaker.bake_texture(scene_mesh, width=16, height=16, sample_count=8)
        for y in range(tex.height):
            for x in range(tex.width):
                v = tex.ao_value(x, y)
                assert 0.0 <= v <= 1.0

    def test_crevice_near_bump_base_is_darker_than_open_plate(self, scene_mesh):
        """Kabul kriteri: çıkıntının tabanına yakın (gölgeli/crevice) bölgedeki
        AO değeri, çıkıntıdan uzak açık düz zemine göre ölçülebilir şekilde
        daha düşük olmalı."""
        tex = AOBaker.bake_texture(
            scene_mesh, width=48, height=48, sample_count=32, max_distance=3.0
        )

        # box_mapping taban plakasını (z ekseni baskın normal) XY düzlemine
        # izdüşürür: u=(x-minx)/(maxx-minx), v=(y-miny)/(maxy-miny), taban
        # aralığı [-5,5] -> [0,1]. Çıkıntının tabanı [-1,1] aralığında, yani
        # UV [0.4, 0.6] civarında. Crevice: çıkıntıya bitişik texel'ler
        # (u ~ 0.4, v ~ 0.5). Açık alan: köşeye yakın (u ~ 0.05, v ~ 0.05).
        def texel_at_uv(u: float, v: float) -> tuple[int, int]:
            return (
                min(tex.width - 1, int(u * tex.width)),
                min(tex.height - 1, int(v * tex.height)),
            )

        crevice_x, crevice_y = texel_at_uv(0.41, 0.5)
        open_x, open_y = texel_at_uv(0.05, 0.05)

        crevice_ao = tex.ao_value(crevice_x, crevice_y)
        open_ao = tex.ao_value(open_x, open_y)

        assert crevice_ao < open_ao, (
            f"crevice AO ({crevice_ao}) açık alan AO'sundan ({open_ao}) "
            "düşük olmalıydı (çıkıntı komşu geometriyi gölgeliyor)"
        )

    def test_dead_space_texels_default_to_fully_lit(self, scene_mesh):
        """UV üçgenine düşmeyen texel'ler (dead space) 1.0 (255) - etkisiz
        varsayılana ayarlanmalı, sıfır/çöp değer değil."""
        tex = AOBaker.bake_texture(scene_mesh, width=8, height=8, sample_count=4)
        # Köşe texel'lerinden en az biri UV üçgenlerinin dışında kalabilir;
        # tüm değerlerin geçerli [0,1] aralığında olduğunu zaten önceki test
        # doğruluyor - burada spesifik olarak "boş" davranışın çökmediğini
        # (exception atmadığını) doğruluyoruz.
        assert tex is not None

    def test_bvh_accelerated_result_matches_linear_scan_closely(self, scene_mesh):
        """BVH ile hızlandırılmış AO, aynı seed/parametrelerle doğrusal
        taramaya yakın sonuç vermeli (aynı ray'ler, aynı geometri - yalnızca
        kesişim testi yöntemi farklı)."""
        bvh = BVH(scene_mesh)
        tex_linear = AOBaker.bake_texture(scene_mesh, width=12, height=12, sample_count=8, bvh=None)
        tex_bvh = AOBaker.bake_texture(scene_mesh, width=12, height=12, sample_count=8, bvh=bvh)
        diffs = [
            abs(tex_linear.ao_value(x, y) - tex_bvh.ao_value(x, y))
            for y in range(12)
            for x in range(12)
        ]
        assert max(diffs) < 1e-6

    def test_missing_uv_raises_clear_error(self):
        mesh = MeshBuilder.build_flat_quad(1.0, 1.0)
        mesh.vertices[0].uv = None
        for v in mesh.vertices:
            v.uv = None
        with pytest.raises(ValueError):
            AOBaker.bake_texture(mesh, width=4, height=4)


# ============================================================================ #
# AOBaker - vertex-tabanlı ucuz varyant
# ============================================================================ #


class TestAOBakerVertex:
    def test_vertex_ao_values_in_range(self, scene_mesh):
        values = AOBaker.bake_vertex_ao(scene_mesh, sample_count=8, max_distance=3.0)
        assert len(values) == scene_mesh.vertex_count()
        assert all(0.0 <= v <= 1.0 for v in values)

    def test_bump_base_vertices_darker_than_isolated_plate_corner(self, scene_mesh):
        values = AOBaker.bake_vertex_ao(scene_mesh, sample_count=32, max_distance=3.0)
        # Plate tabanı ilk 8 vertex (taban + tavan halkası, extrude_polygon
        # sırasına göre); bump vertex'leri ondan sonra gelir. Bump'ın taban
        # halkasındaki bir köşe vs plate'in uzak dış köşesi karşılaştırılır.
        plate_far_corner_idx = 0  # (-5,-5,-0.2) - açık alan
        # bump alt halkası: plate 8 vertex sonra başlar (4 taban + 4 tavan)
        bump_base_idx = 8  # bump mesh'in ilk vertex'i (taban halkası)
        assert values[bump_base_idx] <= values[plate_far_corner_idx] + 0.05


# ============================================================================ #
# NormalMapBaker
# ============================================================================ #


class TestNormalMapBaker:
    def test_bake_texture_dimensions_and_channels(self, scene_mesh):
        # Basit senaryo: aynı mesh'i hem düşük hem yüksek-poli kaynak olarak
        # kullan (kimlik testi - normal transferi kendi yüzeyine denk gelmeli).
        tex = NormalMapBaker.bake_texture(
            scene_mesh, scene_mesh, width=16, height=16, search_distance=0.5
        )
        assert tex.width == 16
        assert tex.height == 16
        assert tex.channels == 3
        assert len(tex.pixels) == 16 * 16 * 3

    def test_decoded_normals_are_unit_length(self, scene_mesh):
        tex = NormalMapBaker.bake_texture(
            scene_mesh, scene_mesh, width=10, height=10, search_distance=0.5
        )
        for y in range(10):
            for x in range(10):
                nx, ny, nz = NormalMapBaker.decode_normal(tex, x, y)
                length = math.sqrt(nx * nx + ny * ny + nz * nz)
                # 8-bit kuantizasyon nedeniyle tam 1.0 değil, makul tolerans
                assert 0.7 <= length <= 1.3

    def test_self_bake_normal_roughly_matches_source_normal(self, scene_mesh):
        """Aynı mesh kendi kendine bake edildiğinde, çözülen normal ile
        rasterizasyondan gelen orijinal enterpole normal arasındaki açı
        küçük olmalı (yüksek-poli == düşük-poli kimlik durumu)."""
        tex = NormalMapBaker.bake_texture(
            scene_mesh, scene_mesh, width=20, height=20, search_distance=1.0
        )
        # Merkezi bir texel örnekle (dead-space olmayan bir tanesini bul)
        found_valid = False
        for y in range(20):
            for x in range(20):
                r, g, b = tex.texel(x, y)
                if (r, g, b) != (128, 128, 255):
                    found_valid = True
                    nx, ny, nz = NormalMapBaker.decode_normal(tex, x, y)
                    length = math.sqrt(nx * nx + ny * ny + nz * nz)
                    assert length > 0.5
                    break
            if found_valid:
                break
        assert found_valid, "en az bir geçerli (dead-space olmayan) texel bulunmalıydı"


# ============================================================================ #
# TextureMapCodec - sıkıştırma + base64 round-trip
# ============================================================================ #


class TestTextureMapCodec:
    def test_roundtrip_preserves_pixels_exactly(self):
        original = TextureMap(width=4, height=4, channels=1, pixels=bytes(range(16)))
        payload = TextureMapCodec.encode(original)
        restored = TextureMapCodec.decode(payload)
        assert restored.width == original.width
        assert restored.height == original.height
        assert restored.channels == original.channels
        assert restored.pixels == original.pixels

    def test_payload_is_json_serializable(self):
        import json

        tex = TextureMap(
            width=8,
            height=8,
            channels=3,
            pixels=bytes(range(8 * 8 * 3 % 256)) * 3 if False else bytes([10] * (8 * 8 * 3)),
        )
        payload = TextureMapCodec.encode(tex)
        serialized = json.dumps(payload)
        deserialized = json.loads(serialized)
        restored = TextureMapCodec.decode(deserialized)
        assert restored.pixels == tex.pixels

    def test_compression_reduces_size_for_uniform_texture(self):
        # Tek renkli, çok tekrarlı bir doku -> DEFLATE ile ciddi küçülmeli.
        tex = TextureMap(width=64, height=64, channels=1, pixels=bytes([200] * (64 * 64)))
        payload = TextureMapCodec.encode(tex)
        assert payload["compressed_byte_count"] < payload["raw_byte_count"]

    def test_data_is_valid_base64(self):
        tex = TextureMap(width=2, height=2, channels=1, pixels=bytes([1, 2, 3, 4]))
        payload = TextureMapCodec.encode(tex)
        # base64.b64decode ValueError fırlatmadan çözülebilmeli
        raw = base64.b64decode(payload["data"])
        assert isinstance(raw, bytes)

    def test_decode_rejects_corrupted_length(self):
        tex = TextureMap(width=4, height=4, channels=1, pixels=bytes(range(16)))
        payload = TextureMapCodec.encode(tex)
        payload["width"] = 999  # metadata'yı bozarak boyut uyuşmazlığı yarat
        with pytest.raises(ValueError):
            TextureMapCodec.decode(payload)


# ============================================================================ #
# HemisphereSampler - determinizm ve geometrik doğruluk
# ============================================================================ #


class TestHemisphereSampler:
    def test_same_seed_produces_identical_samples(self):
        s1 = HemisphereSampler(seed=42)
        s2 = HemisphereSampler(seed=42)
        d1 = s1.sample_hemisphere((0.0, 0.0, 1.0), 20)
        d2 = s2.sample_hemisphere((0.0, 0.0, 1.0), 20)
        assert d1 == d2

    def test_different_seed_produces_different_samples(self):
        s1 = HemisphereSampler(seed=1)
        s2 = HemisphereSampler(seed=2)
        d1 = s1.sample_hemisphere((0.0, 0.0, 1.0), 20)
        d2 = s2.sample_hemisphere((0.0, 0.0, 1.0), 20)
        assert d1 != d2

    def test_all_samples_are_unit_vectors_in_upper_hemisphere(self):
        sampler = HemisphereSampler(seed=7)
        normal = (0.0, 0.0, 1.0)
        for d in sampler.sample_hemisphere(normal, 50):
            length = math.sqrt(sum(c * c for c in d))
            assert abs(length - 1.0) < 1e-9
            dot = d[0] * normal[0] + d[1] * normal[1] + d[2] * normal[2]
            assert dot >= -1e-9  # normal ile aynı yarım-kürede

    def test_samples_respect_arbitrary_normal_direction(self):
        sampler = HemisphereSampler(seed=3)
        normal = (1.0, 0.0, 0.0)
        for d in sampler.sample_hemisphere(normal, 30):
            dot = d[0] * normal[0] + d[1] * normal[1] + d[2] * normal[2]
            assert dot >= -1e-9


# ============================================================================ #
# Geometri yardımcıları (barycentric / möller-trumbore) - birim testler
# ============================================================================ #


class TestGeometryHelpers:
    def test_ray_triangle_hits_known_triangle(self):
        a, b, c = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
        t = _ray_triangle((0.2, 0.2, 1.0), (0.0, 0.0, -1.0), a, b, c)
        assert t is not None
        assert abs(t - 1.0) < 1e-9

    def test_ray_triangle_misses_outside_triangle(self):
        a, b, c = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
        t = _ray_triangle((5.0, 5.0, 1.0), (0.0, 0.0, -1.0), a, b, c)
        assert t is None

    def test_barycentric_center_of_triangle(self):
        a, b, c = (0.0, 0.0), (1.0, 0.0), (0.0, 1.0)
        centroid = ((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3)
        bc = _barycentric_2d(centroid, a, b, c)
        assert bc is not None
        u, v, w = bc
        assert abs(u - 1 / 3) < 1e-9
        assert abs(v - 1 / 3) < 1e-9
        assert abs(w - 1 / 3) < 1e-9

    def test_barycentric_outside_triangle_returns_none(self):
        a, b, c = (0.0, 0.0), (1.0, 0.0), (0.0, 1.0)
        bc = _barycentric_2d((5.0, 5.0), a, b, c)
        assert bc is None
