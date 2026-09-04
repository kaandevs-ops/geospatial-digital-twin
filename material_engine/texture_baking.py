"""
Texture Baking
==============

Roadmap V3 - Faz D9 ("Material Engine: Gerçek Texture-Baking").

Mevcut kısıt (D9 öncesi): `material_engine` yalnızca sabit skaler PBR
değerleri (albedo/roughness/metallic) tutuyordu; gerçek bir AO (ambient
occlusion) / normal map üretim hattı (texture-baking) yoktu.

Bu modül üç bağımsız parça sağlar:

1. `AOBaker` - bir `Mesh3D`'nin UV uzayını rasterize ederek her texel için
   3D karşılığını (barycentric enterpolasyon) bulur ve o noktadan yarım-küre
   (cosine-weighted) örnekleme ile kendi geometrisine ray atarak (Phase 10
   `BVH` üzerinden hızlandırılmış, verilmezse doğrusal tarama) ambient
   occlusion hesaplar - Möller-Trumbore ray-triangle kesişimi
   `analysis_engine.visibility`/`lighting` ile aynı algoritmadır.
2. `NormalMapBaker` - yüksek-poli bir kaynak mesh'in yüzey normalini,
   düşük-poli (örn. Faz D1 QEM ile üretilmiş LOD) hedef mesh'in UV
   uzayına ray-cast ile aktarır ("bake").
3. `TextureMapCodec` - stdlib'de PNG/JPEG encoder olmadığı için, üretilen
   ham piksel verisini `zlib` (DEFLATE) ile sıkıştırıp base64'e çevirerek
   `Scene` JSON'una gömülebilir, bağımlılıksız bir format sağlar.

İlke korunuyor: tamamen stdlib (`zlib`, `base64`, `math`), opsiyonel
bağımlılık yok. Mevcut `PBRMaterial`/`MaterialCache`/`ProceduralMaterials`
API'lerine dokunulmadı (geriye uyumlu ek).
"""

from __future__ import annotations

import base64
import math
import zlib
from dataclasses import dataclass, field

from ..mesh_engine import Mesh3D, Vertex3D

Vec3 = tuple[float, float, float]

# Bu modül `data_engine.spatial_index.BVH` ve
# `analysis_engine.visibility.RayCasting`'e isteğe bağlı bağlanır - import
# döngüsünü önlemek için TYPE_CHECKING dışında lazy referans kullanılır
# (fonksiyon parametresi olarak enjekte edilir, burada import edilmez).


# ============================================================================ #
# Texture Map - ortak veri taşıyıcı + sıkıştırılmış kodlama
# ============================================================================ #

@dataclass(slots=True)
class TextureMap:
    """Tek kanallı (AO) veya 3 kanallı (normal) bir raster texture.

    `channels=1` -> AO map (0.0-1.0 float, 0=tam gölgeli, 1=tam açık).
    `channels=3` -> normal map (tanjant-uzayı yerine burada **dünya-uzayı**
    birim normal vektörleri, her bileşen [-1, 1] aralığından [0, 255]'e
    ölçeklenmiş 8-bit unsigned).
    """

    width: int
    height: int
    channels: int
    pixels: bytes  # row-major, channels-interleaved, uint8

    def texel(self, x: int, y: int) -> tuple[int, ...]:
        idx = (y * self.width + x) * self.channels
        return tuple(self.pixels[idx: idx + self.channels])

    def ao_value(self, x: int, y: int) -> float:
        if self.channels != 1:
            raise ValueError("ao_value yalnızca tek-kanallı (AO) map için geçerlidir.")
        return self.texel(x, y)[0] / 255.0


class TextureMapCodec:
    """Roadmap: 'PNG değil - ham bytes + genişlik/yükseklik meta verisiyle
    Scene JSON'una gömülebilir format (base64 + basit sıkıştırma, stdlib
    zlib)'."""

    FORMAT_VERSION = 1

    @staticmethod
    def encode(tex: TextureMap) -> dict:
        """`TextureMap` -> JSON-serileştirilebilir sözlük. `zlib.compress`
        DEFLATE kullanır (roadmap'in bahsettiği "basit RLE" yerine, stdlib'in
        kendi genel amaçlı - ve RLE'den daha etkin - sıkıştırma algoritması;
        çıktı formatı aynı ilkeye hizmet eder: bağımlılıksız, kayıpsız,
        base64 ile JSON-gömülebilir)."""
        compressed = zlib.compress(tex.pixels, level=9)
        return {
            "format_version": TextureMapCodec.FORMAT_VERSION,
            "width": tex.width,
            "height": tex.height,
            "channels": tex.channels,
            "encoding": "zlib+base64",
            "data": base64.b64encode(compressed).decode("ascii"),
            "raw_byte_count": len(tex.pixels),
            "compressed_byte_count": len(compressed),
        }

    @staticmethod
    def decode(payload: dict) -> TextureMap:
        raw = zlib.decompress(base64.b64decode(payload["data"]))
        expected = payload["width"] * payload["height"] * payload["channels"]
        if len(raw) != expected:
            raise ValueError(
                f"TextureMapCodec.decode: beklenen {expected} byte, "
                f"çözülen {len(raw)} byte - veri bozuk."
            )
        return TextureMap(
            width=payload["width"],
            height=payload["height"],
            channels=payload["channels"],
            pixels=raw,
        )

    # -- ROADMAP_V7 Faz C4 devamı: `performance.scene_lod`'un impostor'lar
    # için özel olarak yazdığı `data:harita-texture-v1;base64,...` şemasını
    # (viewer'ın `decodeHaritaTextureDataURI`'sinin zaten anladığı format)
    # genel amaçlı, herkese açık bir kodek metoduna taşıyoruz - böylece
    # yalnızca impostor'lar değil, `PBRMaterial.albedo_map` kullanan her
    # tüketici (bkz. `material_engine.texture_presets`) aynı şemayı,
    # kopyalamadan, tek bir yerden kullanabilir. `scene_lod`'un kendi
    # (özel, test edilmiş) `_texture_to_data_uri`/`decode_impostor_texture_data_uri`
    # fonksiyonlarına dokunulmadı - geriye dönük tam uyumlu, aynı `data:`
    # önekini üretir/tüketir. --
    DATA_URI_PREFIX = "data:harita-texture-v1;base64,"

    @staticmethod
    def to_data_uri(tex: TextureMap) -> str:
        """`TextureMap` -> `PBRMaterial.albedo_map`'in beklediği kendi
        kendine yeten `str` referansı (dosya yolu gerektirmez)."""
        import json as _json
        encoded = TextureMapCodec.encode(tex)
        payload = base64.b64encode(_json.dumps(encoded).encode("ascii")).decode("ascii")
        return f"{TextureMapCodec.DATA_URI_PREFIX}{payload}"

    @staticmethod
    def from_data_uri(data_uri: str) -> TextureMap:
        """`to_data_uri`'nin tersi."""
        import json as _json
        if not data_uri.startswith(TextureMapCodec.DATA_URI_PREFIX):
            raise ValueError("TextureMapCodec.from_data_uri: beklenmeyen şema/önek.")
        payload = _json.loads(base64.b64decode(data_uri[len(TextureMapCodec.DATA_URI_PREFIX):]).decode("ascii"))
        return TextureMapCodec.decode(payload)


# ============================================================================ #
# Ortak geometri yardımcıları (Möller-Trumbore, barycentric UV rasterizasyon)
# ============================================================================ #

def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _length(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _normalize(a: Vec3) -> Vec3:
    length = _length(a)
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def _ray_triangle(origin: Vec3, direction: Vec3, a: Vec3, b: Vec3, c: Vec3) -> float | None:
    """Möller-Trumbore ray-triangle kesişimi (t parametresi veya None).
    `lighting`/`analysis_engine.visibility` ile aynı algoritma - burada
    bağımsız kopya, modülün BVH olmadan da çalışabilmesi için."""
    edge1 = _sub(b, a)
    edge2 = _sub(c, a)
    pvec = _cross(direction, edge2)
    det = _dot(edge1, pvec)
    if -1e-12 < det < 1e-12:
        return None
    inv_det = 1.0 / det
    tvec = _sub(origin, a)
    u = _dot(tvec, pvec) * inv_det
    if u < 0.0 or u > 1.0:
        return None
    qvec = _cross(tvec, edge1)
    v = _dot(direction, qvec) * inv_det
    if v < 0.0 or u + v > 1.0:
        return None
    t = _dot(edge2, qvec) * inv_det
    return t


def _barycentric_2d(p: tuple[float, float], a: tuple[float, float],
                     b: tuple[float, float], c: tuple[float, float]) -> Vec3 | None:
    """2D (UV-uzayı) barycentric koordinatlar; nokta üçgen dışındaysa None."""
    v0 = (b[0] - a[0], b[1] - a[1])
    v1 = (c[0] - a[0], c[1] - a[1])
    v2 = (p[0] - a[0], p[1] - a[1])
    d00 = v0[0] * v0[0] + v0[1] * v0[1]
    d01 = v0[0] * v1[0] + v0[1] * v1[1]
    d11 = v1[0] * v1[0] + v1[1] * v1[1]
    d20 = v2[0] * v0[0] + v2[1] * v0[1]
    d21 = v2[0] * v1[0] + v2[1] * v1[1]
    denom = d00 * d11 - d01 * d01
    if abs(denom) < 1e-14:
        return None
    v = (d11 * d20 - d01 * d21) / denom
    w = (d00 * d21 - d01 * d20) / denom
    u = 1.0 - v - w
    eps = 1e-6
    if u < -eps or v < -eps or w < -eps:
        return None
    return (u, v, w)


@dataclass(slots=True)
class _UVTexel:
    """Bir texel'in 3D karşılığı: UV-uzayı rasterizasyonundan çıkan ara sonuç."""
    world_pos: Vec3
    world_normal: Vec3
    triangle_index: int


def _rasterize_uv_space(mesh: Mesh3D, width: int, height: int) -> list[_UVTexel | None]:
    """Mesh'in UV üçgenlerini `width x height` bir grid'e rasterize eder;
    her texel merkezinin hangi üçgenin UV-alanına düştüğünü bulur ve o
    noktadaki 3D pozisyon/normali barycentric enterpolasyonla hesaplar.
    Hiçbir üçgene düşmeyen texel'ler `None` kalır (dead space - PBR
    tarayıcılar/viewer'lar bu texel'leri örneklemez)."""
    if not mesh.vertices or mesh.vertices[0].uv is None:
        raise ValueError(
            "AOBaker/NormalMapBaker için mesh vertex'lerinde UV koordinatı "
            "gerekli (önce mesh_engine.UVGenerator ile UV üret)."
        )

    result: list[_UVTexel | None] = [None] * (width * height)

    for tri_idx, tri in enumerate(mesh.triangles):
        v0, v1, v2 = mesh.triangle_positions(tri)
        if v0.uv is None or v1.uv is None or v2.uv is None:
            continue
        uv0, uv1, uv2 = v0.uv, v1.uv, v2.uv

        # UV üçgeninin piksel-uzayı bounding box'ı - yalnızca bu aralığı tara.
        px0, px1 = uv0[0] * width, uv0[1] * height
        px2, px3 = uv1[0] * width, uv1[1] * height
        px4, px5 = uv2[0] * width, uv2[1] * height
        min_x = max(0, int(math.floor(min(px0, px2, px4))))
        max_x = min(width - 1, int(math.ceil(max(px0, px2, px4))))
        min_y = max(0, int(math.floor(min(px1, px3, px5))))
        max_y = min(height - 1, int(math.ceil(max(px1, px3, px5))))

        n0 = v0.normal or (0.0, 0.0, 1.0)
        n1 = v1.normal or (0.0, 0.0, 1.0)
        n2 = v2.normal or (0.0, 0.0, 1.0)

        for py in range(min_y, max_y + 1):
            for px in range(min_x, max_x + 1):
                # Texel merkezi -> UV koordinatı
                u = (px + 0.5) / width
                v = (py + 0.5) / height
                bc = _barycentric_2d((u, v), uv0, uv1, uv2)
                if bc is None:
                    continue
                a, b, c = bc
                pos = (
                    a * v0.x + b * v1.x + c * v2.x,
                    a * v0.y + b * v1.y + c * v2.y,
                    a * v0.z + b * v1.z + c * v2.z,
                )
                normal = _normalize((
                    a * n0[0] + b * n1[0] + c * n2[0],
                    a * n0[1] + b * n1[1] + c * n2[1],
                    a * n0[2] + b * n1[2] + c * n2[2],
                ))
                result[py * width + px] = _UVTexel(pos, normal, tri_idx)

    return result


# ============================================================================ #
# Hemisphere sampling - deterministik, seed'li (test edilebilir/tekrarlanabilir)
# ============================================================================ #

class HemisphereSampler:
    """Kosinüs-ağırlıklı yarım-küre örnekleme (Malley yöntemi: disk üzerinde
    tekdüze örnek + yukarı izdüşüm). Deterministik LCG (`random` modülüne
    bağımlı değil) - aynı seed her zaman aynı örnek kümesini üretir, bu da
    regresyon testlerini tekrarlanabilir kılar."""

    def __init__(self, seed: int = 12345) -> None:
        self._state = seed & 0x7FFFFFFF

    def _next(self) -> float:
        self._state = (self._state * 1103515245 + 12345) & 0x7FFFFFFF
        return self._state / 0x7FFFFFFF

    def sample_hemisphere(self, normal: Vec3, count: int) -> list[Vec3]:
        """`normal` etrafında `count` adet kosinüs-ağırlıklı yön üretir
        (dünya-uzayında, tanjant çerçevesinden dönüştürülmüş)."""
        # Tanjant çerçevesi (normal'e dik iki eksen)
        n = _normalize(normal)
        helper = (0.0, 1.0, 0.0) if abs(n[1]) < 0.99 else (1.0, 0.0, 0.0)
        tangent = _normalize(_cross(helper, n))
        bitangent = _cross(n, tangent)

        dirs: list[Vec3] = []
        for _ in range(count):
            r1 = self._next()
            r2 = self._next()
            r = math.sqrt(r1)
            theta = 2.0 * math.pi * r2
            x = r * math.cos(theta)
            y = r * math.sin(theta)
            z = math.sqrt(max(0.0, 1.0 - r1))
            world = (
                x * tangent[0] + y * bitangent[0] + z * n[0],
                x * tangent[1] + y * bitangent[1] + z * n[1],
                x * tangent[2] + y * bitangent[2] + z * n[2],
            )
            dirs.append(_normalize(world))
        return dirs


# ============================================================================ #
# AO Baker
# ============================================================================ #

class AOBaker:
    """Roadmap: 'material_engine/texture_baking.py: mesh geometrisinden
    stdlib-only bir AO haritası (basit ray-sampling: her texel için
    yarım-küre örnekleme, komşu üçgenlerle kesişim testi) üretimi'."""

    @staticmethod
    def _occlusion_at(pos: Vec3, normal: Vec3, mesh: Mesh3D, sample_count: int,
                       max_distance: float, bias: float, seed: int,
                       bvh: "object | None" = None) -> float:
        sampler = HemisphereSampler(seed=seed)
        dirs = sampler.sample_hemisphere(normal, sample_count)
        origin = (
            pos[0] + normal[0] * bias,
            pos[1] + normal[1] * bias,
            pos[2] + normal[2] * bias,
        )
        occluded = 0
        for d in dirs:
            if bvh is not None:
                hit = bvh.intersect_ray(origin, d)
                if hit is not None and hit.t <= max_distance:
                    occluded += 1
                continue
            hit_found = False
            for tri in mesh.triangles:
                a, b, c = mesh.triangle_positions(tri)
                t = _ray_triangle(origin, d, a.as_tuple(), b.as_tuple(), c.as_tuple())
                if t is not None and 1e-6 < t <= max_distance:
                    hit_found = True
                    break
            if hit_found:
                occluded += 1
        return 1.0 - (occluded / sample_count)

    @classmethod
    def bake_texture(cls, mesh: Mesh3D, width: int = 64, height: int = 64,
                      sample_count: int = 16, max_distance: float = 5.0,
                      bias: float = 1e-3, bvh: "object | None" = None) -> TextureMap:
        """Mesh'in UV uzayını rasterize edip her texel'de yarım-küre AO
        örneklemesi yapar. `bvh` verilirse (`data_engine.spatial_index.BVH`
        örneği - aynı mesh üzerinden inşa edilmiş), doğrusal üçgen
        taraması yerine O(log n) BVH gezintisi kullanılır (büyük mesh'lerde
        önerilir; küçük test mesh'lerinde opsiyoneldir)."""
        texels = _rasterize_uv_space(mesh, width, height)
        pixels = bytearray(width * height)
        for i, texel in enumerate(texels):
            if texel is None:
                pixels[i] = 255  # dead-space: tam açık (etkisiz) varsayılan
                continue
            ao = cls._occlusion_at(
                texel.world_pos, texel.world_normal, mesh,
                sample_count=sample_count, max_distance=max_distance,
                bias=bias, seed=12345 + i, bvh=bvh,
            )
            pixels[i] = max(0, min(255, int(round(ao * 255))))
        return TextureMap(width=width, height=height, channels=1, pixels=bytes(pixels))

    @classmethod
    def bake_vertex_ao(cls, mesh: Mesh3D, sample_count: int = 24,
                        max_distance: float = 5.0, bias: float = 1e-3,
                        bvh: "object | None" = None) -> list[float]:
        """Texel-rasterizasyonu gerektirmeyen, doğrudan her vertex için AO
        değeri döndüren daha ucuz varyant (UV olmayan mesh'lerde veya
        vertex-renk tabanlı AO iş akışlarında kullanılabilir)."""
        result: list[float] = []
        for i, v in enumerate(mesh.vertices):
            normal = v.normal or (0.0, 0.0, 1.0)
            ao = cls._occlusion_at(
                v.as_tuple(), normal, mesh,
                sample_count=sample_count, max_distance=max_distance,
                bias=bias, seed=999 + i, bvh=bvh,
            )
            result.append(ao)
        return result


# ============================================================================ #
# Normal Map Baker (yüksek-poli -> düşük-poli)
# ============================================================================ #

class NormalMapBaker:
    """Roadmap: 'Normal map: yüksek-poli mesh'ten düşük-poli hedefe (D1'in
    ürettiği LOD) normal detayı aktarımı (basit ray-cast tabanlı bake)'."""

    @staticmethod
    def _closest_hit_normal(origin: Vec3, direction: Vec3, high_mesh: Mesh3D,
                             search_distance: float,
                             bvh_high: "object | None" = None) -> Vec3 | None:
        if bvh_high is not None:
            hit = bvh_high.intersect_ray(origin, direction)
            if hit is not None and hit.t <= search_distance:
                tri = high_mesh.triangles[hit.triangle_index]
                a, b, c = high_mesh.triangle_positions(tri)
                return _normalize(_cross(_sub(b.as_tuple(), a.as_tuple()),
                                          _sub(c.as_tuple(), a.as_tuple())))
            return None

        best_t = None
        best_normal = None
        for tri in high_mesh.triangles:
            a, b, c = high_mesh.triangle_positions(tri)
            t = _ray_triangle(origin, direction, a.as_tuple(), b.as_tuple(), c.as_tuple())
            if t is not None and 1e-6 < t <= search_distance:
                if best_t is None or t < best_t:
                    best_t = t
                    best_normal = _normalize(_cross(
                        _sub(b.as_tuple(), a.as_tuple()), _sub(c.as_tuple(), a.as_tuple())
                    ))
        return best_normal

    @classmethod
    def bake_texture(cls, low_mesh: Mesh3D, high_mesh: Mesh3D,
                      width: int = 64, height: int = 64,
                      search_distance: float = 2.0,
                      bvh_high: "object | None" = None) -> TextureMap:
        """Düşük-poli hedefin UV uzayını rasterize eder; her texel için
        düşük-poli yüzeyden hem +normal hem -normal yönünde `search_distance`
        içinde yüksek-poli mesh'e ray atar (en yakın kesişimi alır - iki
        yönün de denenmesi, yüksek-poli yüzeyin düşük-poliye göre içeride
        veya dışarıda olabileceği genel durumu kapsar). Kesişim
        bulunamazsa düşük-poli'nin kendi enterpole normali kullanılır
        (fallback - hiçbir zaman boş/sıfır normal üretmez)."""
        texels = _rasterize_uv_space(low_mesh, width, height)
        pixels = bytearray(width * height * 3)
        for i, texel in enumerate(texels):
            if texel is None:
                # dead-space: (0,0,1) -> encode edilmiş (128,128,255)
                pixels[i * 3: i * 3 + 3] = bytes((128, 128, 255))
                continue
            n_low = texel.world_normal
            hit_normal = cls._closest_hit_normal(
                texel.world_pos, n_low, high_mesh, search_distance, bvh_high
            )
            if hit_normal is None:
                inv = (-n_low[0], -n_low[1], -n_low[2])
                hit_normal = cls._closest_hit_normal(
                    texel.world_pos, inv, high_mesh, search_distance, bvh_high
                )
            final_normal = hit_normal if hit_normal is not None else n_low
            pixels[i * 3: i * 3 + 3] = bytes(
                max(0, min(255, int(round((c + 1.0) * 0.5 * 255)))) for c in final_normal
            )
        return TextureMap(width=width, height=height, channels=3, pixels=bytes(pixels))

    @staticmethod
    def decode_normal(tex: TextureMap, x: int, y: int) -> Vec3:
        """`bake_texture` çıktısındaki [0,255] kodlu bir texel'i tekrar
        [-1, 1] birim normal vektörüne çözer."""
        r, g, b = tex.texel(x, y)
        return (r / 255.0 * 2.0 - 1.0, g / 255.0 * 2.0 - 1.0, b / 255.0 * 2.0 - 1.0)
