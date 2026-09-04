"""
Prosedürel Malzeme Dokuları
============================

ROADMAP_V7 Faz C4'ün devamı — önceki oturumda kapatılan "Kalan" maddesi
şuydu: "mevcut mesh üretim yollarının (bina cephesi, yol, su...) çoğu
henüz kendi UV koordinatlarını üretmiyor - bu yüzden gerçek doku şu an
yalnızca impostor'larda görünür olacak". Bu modül, o notun ikinci
yarısını kapatır: `ProceduralMaterials`'ın 8 cephe malzemesi ön ayarı
(cam/beton/tuğla/metal/kompozit/taş/ahşap/endüstriyel) için **gerçek,
kategoriye özgü** dokular üretir (düz renk değil) — `tugla` için mevcut
`ProceduralMaterials.procedural_brick_pattern` boolean maskesi gerçek bir
tuğla-harç deseni piksel verisine çevrilir; diğerleri için malzemenin
kendi albedo'sundan türetilen hafif bir doku varyasyonu (tam düz renk,
GPU'da tekdüze/plastik görünmeyi önlemek için) üretilir.

`material_engine.texture_baking.TextureMapCodec.to_data_uri` (bu oturumda
eklenen genel amaçlı kodek metodu) ile aynı `data:harita-texture-v1`
şemasını kullanır - viewer'daki (`app_shell/web/index.html`)
`decodeHaritaTextureDataURI` bunu zaten anlıyor (impostor dokuları için
yazılmıştı, şema/format aynı olduğu için ek bir viewer değişikliği
gerekmiyor).

Tamamen stdlib-only, harici görüntü kütüphanesi yok (proje ilkesiyle
tutarlı).
"""

from __future__ import annotations

from . import ProceduralMaterials
from .texture_baking import TextureMap, TextureMapCodec

__all__ = [
    "procedural_material_texture",
    "procedural_material_texture_data_uri",
]


def _clamp255(v: float) -> int:
    return max(0, min(255, int(round(v * 255))))


def _brick_texture(albedo: tuple[float, float, float], size: int) -> TextureMap:
    """`ProceduralMaterials.procedural_brick_pattern`'in boolean maskesini
    (tuğla=True / harç=False) gerçek RGB piksel verisine çevirir - tuğla
    rengi malzemenin kendi albedo'su, harç rengi standart gri-bej harç
    tonudur (gerçek bir doku dosyası değil ama düz renkten belirgin
    şekilde ayrışan, tanınabilir bir tuğla-harç deseni)."""
    mask = ProceduralMaterials.procedural_brick_pattern(
        size, size, brick_w=max(4, size // 4), brick_h=max(2, size // 8)
    )
    r, g, b = albedo
    brick = (_clamp255(r), _clamp255(g), _clamp255(b))
    mortar = (_clamp255(0.72), _clamp255(0.68), _clamp255(0.62))
    pixels = bytearray(size * size * 3)
    for y in range(size):
        row = mask[y]
        for x in range(size):
            color = brick if row[x] else mortar
            idx = (y * size + x) * 3
            pixels[idx : idx + 3] = bytes(color)
    return TextureMap(width=size, height=size, channels=3, pixels=bytes(pixels))


def _checker_texture(albedo: tuple[float, float, float], size: int) -> TextureMap:
    """Tuğla dışındaki malzemeler için: albedo'nun açık/koyu tonlarından
    ince bir dama-tahtası varyasyonu (`performance.scene_lod.
    build_cross_billboard_impostor`'ın impostor'lar için kullandığı
    teknikle aynı ilke - düz renkten görsel olarak ayrışan, stdlib-only
    bir doku)."""
    r, g, b = albedo
    dark = (_clamp255(r * 0.82), _clamp255(g * 0.82), _clamp255(b * 0.82))
    light = (
        _clamp255(min(1.0, r * 1.08)),
        _clamp255(min(1.0, g * 1.08)),
        _clamp255(min(1.0, b * 1.08)),
    )
    pixels = bytearray(size * size * 3)
    cell = max(1, size // 8)
    for y in range(size):
        for x in range(size):
            checker = ((x // cell) + (y // cell)) % 2 == 0
            color = light if checker else dark
            idx = (y * size + x) * 3
            pixels[idx : idx + 3] = bytes(color)
    return TextureMap(width=size, height=size, channels=3, pixels=bytes(pixels))


def procedural_material_texture(material_type: str, size: int = 32) -> TextureMap:
    """`material_type` (`ProceduralMaterials.available_types()`'tan biri)
    için gerçek bir `TextureMap` üretir. Bilinmeyen tip için `ValueError`
    (aynı disiplin: `ProceduralMaterials.create`)."""
    material_type = material_type.lower()
    if material_type not in ProceduralMaterials.available_types():
        raise ValueError(
            f"Bilinmeyen malzeme tipi: '{material_type}'. "
            f"Seçenekler: {ProceduralMaterials.available_types()}"
        )
    preset = ProceduralMaterials._PRESETS[material_type]
    albedo = preset["albedo"]
    if material_type == "tugla":
        return _brick_texture(albedo, size)
    return _checker_texture(albedo, size)


def procedural_material_texture_data_uri(material_type: str, size: int = 32) -> str:
    """`procedural_material_texture` + `TextureMapCodec.to_data_uri` -
    doğrudan `PBRMaterial.albedo_map`'e atanabilir `str`."""
    return TextureMapCodec.to_data_uri(procedural_material_texture(material_type, size))
