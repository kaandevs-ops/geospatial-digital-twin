"""
Material Engine
=================

Roadmap Phase 2 - "Material Engine".

Kapsam: PBR Materials, Texture Loader, Material Cache, Procedural Materials.

Bağımlılık notu: `TextureLoader` gerçek PNG/JPG decode için Pillow'a ihtiyaç
duyar (roadmap'in tek istisnası - "sadece performans kritik yerlerde hafif
bağımlılık" ilkesine uygun, opsiyonel/lazy import). Pillow yoksa
`TextureLoader` net bir hata ile bunu bildirir; `ProceduralMaterials` ve
`PBRMaterial`/`MaterialCache` tamamen bağımlılıksız çalışır.

Roadmap V3 - Faz D9 ("Material Engine: Gerçek Texture-Baking"): gerçek
AO/normal map texture-baking pipeline'ı `texture_baking.py` alt modülünde
(`AOBaker`, `NormalMapBaker`, `TextureMap`, `TextureMapCodec`) - bkz. o
modülün docstring'i. Burada re-export edilir.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from .texture_baking import (  # noqa: F401 - re-export
    TextureMap,
    TextureMapCodec,
    HemisphereSampler,
    AOBaker,
    NormalMapBaker,
)


# ======================================================================== #
# PBR Material
# ======================================================================== #

@dataclass(slots=True)
class PBRMaterial:
    """Metallic/Roughness PBR iş akışı (glTF/Unity/Unreal ile uyumlu şema)."""

    name: str
    albedo: tuple[float, float, float] = (0.8, 0.8, 0.8)
    albedo_map: str | None = None
    roughness: float = 0.5
    roughness_map: str | None = None
    metallic: float = 0.0
    metallic_map: str | None = None
    normal_map: str | None = None
    ao_map: str | None = None
    emissive: tuple[float, float, float] = (0.0, 0.0, 0.0)
    opacity: float = 1.0

    def __post_init__(self) -> None:
        self.roughness = min(1.0, max(0.0, self.roughness))
        self.metallic = min(1.0, max(0.0, self.metallic))
        self.opacity = min(1.0, max(0.0, self.opacity))

    def content_hash(self) -> str:
        """`MaterialCache` için tekilleştirme anahtarı."""
        payload = "|".join(str(x) for x in (
            self.albedo, self.albedo_map, self.roughness, self.roughness_map,
            self.metallic, self.metallic_map, self.normal_map, self.ao_map,
            self.emissive, self.opacity,
        ))
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# ======================================================================== #
# Texture Loader
# ======================================================================== #

@dataclass(slots=True)
class TextureData:
    width: int
    height: int
    channels: int
    pixels: bytes  # row-major, channels-interleaved


class TextureLoader:
    """Roadmap: 'Texture Loader'. PNG/JPG decode - Pillow tek harici
    bağımlılık (opsiyonel/lazy import; yalnızca gerçekten çağrıldığında
    aranır, böylece modülün geri kalanı Pillow kurulu olmasa da import
    edilebilir)."""

    _cache: dict[str, TextureData] = {}

    @classmethod
    def load(cls, path: str | Path) -> TextureData:
        key = str(path)
        if key in cls._cache:
            return cls._cache[key]
        try:
            from PIL import Image  # lazy import
        except ImportError as exc:  # pragma: no cover - ortama bağlı
            raise ImportError(
                "TextureLoader gerçek görüntü decode için Pillow gerektirir. "
                "Kurulum: pip install Pillow"
            ) from exc

        with Image.open(path) as img:
            img = img.convert("RGBA")
            data = TextureData(width=img.width, height=img.height, channels=4, pixels=img.tobytes())
        cls._cache[key] = data
        return data

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()


# ======================================================================== #
# Material Cache
# ======================================================================== #

class MaterialCache:
    """Roadmap: 'Material Cache'. `content_hash()` bazlı tekilleştirme -
    aynı içerikli malzemenin sahne genelinde tek bir örneği paylaşılır
    (bellek/GPU state-change optimizasyonu)."""

    def __init__(self) -> None:
        self._by_hash: dict[str, PBRMaterial] = {}

    def get_or_add(self, material: PBRMaterial) -> PBRMaterial:
        h = material.content_hash()
        if h not in self._by_hash:
            self._by_hash[h] = material
        return self._by_hash[h]

    def __len__(self) -> int:
        return len(self._by_hash)

    def all_materials(self) -> list[PBRMaterial]:
        return list(self._by_hash.values())


# ======================================================================== #
# Procedural Materials
# ======================================================================== #

class ProceduralMaterials:
    """Roadmap: 'Procedural Materials'. Bina cephesi için parametrik,
    doku-dosyasız `PBRMaterial` üreticileri (roadmap Phase 3 Facade
    Generator: Cam/Beton/Tuğla/Metal/Kompozit/Taş/Ahşap/Endüstriyel)."""

    #: ROADMAP_V8 Faz 5.1 — "8 preset yetersiz, en az 20+ olmalı" kabul
    #: kriteri. Orijinal 8 preset (cam..endustriyel) DEĞİŞTİRİLMEDEN
    #: korundu (geriye dönük uyumluluk — mevcut çağıran kodlar/testler
    #: aynı isimlerle çalışmaya devam eder); aşağıya B1'in yeni
    #: kategorileriyle (su, çim/suni çim, dini yapı taşı/mermer, anıt
    #: bronz/bakır, çatı malzemeleri, yol/kaldırım) eşleşen 15 yeni preset
    #: eklendi. Ayrıca `tugla`'nın tek tonluk sınırlaması, alt-varyant
    #: presetleriyle (tugla_sari/tugla_koyu) gerçek dünyadaki ton
    #: çeşitliliğine yaklaştırıldı (roadmap: "onlarca tonu var, hepsi tek
    #: RGB'ye indirgeniyor" eleştirisine doğrudan yanıt).
    _PRESETS: dict[str, dict] = {
        # --- Orijinal 8 (değiştirilmedi) ---
        "cam": dict(albedo=(0.6, 0.75, 0.85), roughness=0.05, metallic=0.0, opacity=0.35),
        "beton": dict(albedo=(0.65, 0.64, 0.6), roughness=0.85, metallic=0.0, opacity=1.0),
        "tugla": dict(albedo=(0.55, 0.27, 0.2), roughness=0.75, metallic=0.0, opacity=1.0),
        "metal": dict(albedo=(0.7, 0.7, 0.72), roughness=0.3, metallic=0.9, opacity=1.0),
        "kompozit": dict(albedo=(0.5, 0.5, 0.5), roughness=0.5, metallic=0.2, opacity=1.0),
        "tas": dict(albedo=(0.58, 0.56, 0.52), roughness=0.9, metallic=0.0, opacity=1.0),
        "ahsap": dict(albedo=(0.45, 0.3, 0.18), roughness=0.7, metallic=0.0, opacity=1.0),
        "endustriyel": dict(albedo=(0.4, 0.4, 0.42), roughness=0.6, metallic=0.5, opacity=1.0),

        # --- ROADMAP_V8 Faz 5.1 eklentisi: tuğla alt-varyantları ---
        "tugla_sari": dict(albedo=(0.78, 0.68, 0.4), roughness=0.72, metallic=0.0, opacity=1.0),
        "tugla_koyu": dict(albedo=(0.35, 0.16, 0.13), roughness=0.8, metallic=0.0, opacity=1.0),

        # --- Doğal taş / anıt-dini yapı ---
        "mermer": dict(albedo=(0.88, 0.87, 0.84), roughness=0.25, metallic=0.0, opacity=1.0),
        "granit": dict(albedo=(0.42, 0.4, 0.4), roughness=0.55, metallic=0.0, opacity=1.0),
        "kalker": dict(albedo=(0.72, 0.68, 0.58), roughness=0.8, metallic=0.0, opacity=1.0),

        # --- Metal alt-varyantları (anıt bronzu, dini yapı bakır kubbe) ---
        "bronz": dict(albedo=(0.55, 0.38, 0.2), roughness=0.4, metallic=0.85, opacity=1.0),
        "bakir_oksitli": dict(albedo=(0.3, 0.55, 0.48), roughness=0.6, metallic=0.4, opacity=1.0),

        # --- Su / doğal zemin (B1 "düz renk placeholder" eleştirisine yanıt) ---
        "su": dict(albedo=(0.1, 0.25, 0.35), roughness=0.05, metallic=0.0, opacity=0.85),
        "cim": dict(albedo=(0.25, 0.45, 0.18), roughness=0.95, metallic=0.0, opacity=1.0),
        "suni_cim": dict(albedo=(0.2, 0.55, 0.22), roughness=0.7, metallic=0.0, opacity=1.0),
        "kum": dict(albedo=(0.76, 0.68, 0.5), roughness=0.9, metallic=0.0, opacity=1.0),
        "toprak": dict(albedo=(0.4, 0.28, 0.18), roughness=0.95, metallic=0.0, opacity=1.0),

        # --- Yol / kaldırım (B1 peyzaj mobilyası ile eşleşir) ---
        "asfalt": dict(albedo=(0.12, 0.12, 0.13), roughness=0.8, metallic=0.0, opacity=1.0),
        "beton_parke": dict(albedo=(0.68, 0.67, 0.63), roughness=0.7, metallic=0.0, opacity=1.0),

        # --- Çatı malzemeleri (roof_generator ile eşleşir) ---
        "cati_kiremit": dict(albedo=(0.6, 0.28, 0.18), roughness=0.65, metallic=0.0, opacity=1.0),
        "cati_metal": dict(albedo=(0.55, 0.56, 0.58), roughness=0.35, metallic=0.7, opacity=1.0),

        # --- Renkli/vitray cam (dini yapı pencereleri) ---
        "vitray": dict(albedo=(0.5, 0.2, 0.4), roughness=0.1, metallic=0.0, opacity=0.55),
    }

    @classmethod
    def available_types(cls) -> list[str]:
        return list(cls._PRESETS.keys())

    @classmethod
    def create(cls, material_type: str, variation_seed: int | None = None) -> PBRMaterial:
        material_type = material_type.lower()
        if material_type not in cls._PRESETS:
            raise ValueError(
                f"Bilinmeyen malzeme tipi: '{material_type}'. "
                f"Seçenekler: {cls.available_types()}"
            )
        params = dict(cls._PRESETS[material_type])
        if variation_seed is not None:
            params = cls._apply_variation(params, variation_seed)
        return PBRMaterial(name=material_type, **params)

    @staticmethod
    def _apply_variation(params: dict, seed: int) -> dict:
        """Deterministik, hafif renk/pürüzlülük varyasyonu - her binada aynı
        malzeme tipinin tekdüze görünmemesi için (seed tabanlı, tekrarlanabilir)."""
        rng_state = seed
        def _next() -> float:
            nonlocal rng_state
            rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
            return rng_state / 0x7FFFFFFF

        r, g, b = params["albedo"]
        jitter = lambda v: min(1.0, max(0.0, v + (_next() - 0.5) * 0.08))
        params = dict(params)
        params["albedo"] = (jitter(r), jitter(g), jitter(b))
        params["roughness"] = min(1.0, max(0.0, params["roughness"] + (_next() - 0.5) * 0.1))
        # ROADMAP_V8 Faz 5.1 — önceden yalnızca albedo/roughness varyasyon
        # görüyordu; metalik yüzeylerde (metal/bronz/bakır/çatı metali)
        # metallic sabit kalınca varyasyon "görünmüyordu" (roadmap kabul
        # kriteri: "her preset gerçek varyasyon üretiyor"). Küçük (±0.06)
        # bir sapma eklendi — sıfır-metalik (beton/tuğla/vb.) preset'lerde
        # de görünür kalsın diye clamp negatif tarafa gitmiyor.
        params["metallic"] = min(1.0, max(0.0, params["metallic"] + (_next() - 0.5) * 0.06))
        return params

    @staticmethod
    def procedural_brick_pattern(width_px: int, height_px: int, brick_w: int = 32, brick_h: int = 16
                                  ) -> list[list[bool]]:
        """Basit prosedürel tuğla deseni (offset-row) - gerçek doku pikseli
        yerine boolean mask üretir; `TextureLoader`'a ihtiyaç duymadan
        `FacadeGenerator`'ın (Phase 3) desen kararları için kullanılabilir."""
        mask = []
        for y in range(height_px):
            row_index = y // brick_h
            offset = (brick_w // 2) if row_index % 2 == 1 else 0
            row = []
            for x in range(width_px):
                local_x = (x + offset) % brick_w
                local_y = y % brick_h
                is_mortar = local_x == 0 or local_y == 0
                row.append(not is_mortar)
            mask.append(row)
        return mask


# ======================================================================== #
# Malzeme Yaşlandırma / Kirlenme (Weathering) - yeni_roadmap.md Faz 1.4
# ======================================================================== #

class MaterialWeathering:
    """Roadmap 1.4: 'Yaşlandırma/kirlenme (weathering) efekti - opsiyonel
    gerçekçilik katmanı'.

    Gerçek bir kirlenme simülasyonu (akış tabanlı streak/patina) yerine,
    stdlib-only ve deterministik bir yaklaşım kullanılır: yaş (yıl) ve
    iklim maruziyeti (0-1, yağış/nem/kirlilik vekili) parametrik olarak
    albedo'yu koyulaştırır/desatüre eder ve pürüzlülüğü artırır - gerçek
    malzemelerin yaşlanma davranışına kabaca uyan, yönlendirilebilir bir
    yaklaşımdır (agresif fiziksel doğruluk iddiası taşımaz).
    """

    @staticmethod
    def apply(
        material: PBRMaterial,
        age_years: float,
        climate_exposure: float = 0.5,
        max_darkening: float = 0.35,
        max_roughness_increase: float = 0.3,
    ) -> PBRMaterial:
        """`material`i değiştirmeden, yaşlanmış bir kopyasını döndürür.

        - `age_years`: 0 -> hiç etki yok; ~50+ yılda etki doygunlaşır
          (üstel doygunluk eğrisi - ilk yıllarda hızlı, sonra yavaşlayan
          değişim, gerçek hava koşullarına maruz malzemelerin genel
          davranışına uygun).
        - `climate_exposure`: 0 (korunaklı/iç mekan benzeri) - 1 (tam
          maruz, yüksek yağış/nem/kirlilik) arası çarpan.
        """
        age_years = max(0.0, age_years)
        climate_exposure = min(1.0, max(0.0, climate_exposure))
        # Doygunlaşan üstel eğri: 1 - e^(-yaş/tau), tau=25 yıl.
        age_factor = (1.0 - math.exp(-age_years / 25.0)) * climate_exposure

        r, g, b = material.albedo
        darken = 1.0 - max_darkening * age_factor
        # Hafif desatürasyon: renk kanallarını ortalamaya doğru çek.
        avg = (r + g + b) / 3.0
        desat = 0.15 * age_factor

        def _weather(c: float) -> float:
            c = c * (1 - desat) + avg * desat
            return min(1.0, max(0.0, c * darken))

        new_albedo = (_weather(r), _weather(g), _weather(b))
        new_roughness = min(1.0, material.roughness + max_roughness_increase * age_factor)

        return PBRMaterial(
            name=f"{material.name}_weathered_{int(age_years)}y",
            albedo=new_albedo, albedo_map=material.albedo_map,
            roughness=new_roughness, roughness_map=material.roughness_map,
            metallic=material.metallic, metallic_map=material.metallic_map,
            normal_map=material.normal_map, ao_map=material.ao_map,
            emissive=material.emissive, opacity=material.opacity,
        )


# ROADMAP_V7 Faz C4 devamı: `texture_presets` bu modülün kendi
# `ProceduralMaterials` sınıfını tüketir - döngüsel import'u önlemek için
# (aynı desen: `street_furniture/__init__.py`'nin `osm_bridge`'i dosya
# sonunda içe alması) burada, sınıf tanımlandıktan *sonra* import edilir.
from .texture_presets import (  # noqa: F401,E402 - re-export, döngüsel import çözümü
    procedural_material_texture,
    procedural_material_texture_data_uri,
)
