"""
ai_reconstruction.material_bridge - kullanıcı isteğiyle işlenen kapsam dışı
kalem: "gerçek doku ataması" (bina tarafı)
================================================================================

Önceki oturumlarda OSM nokta-prop köprüleri (ağaç, sokak mobilyası, dini
yapı...) için `performance.scene_lod.impostor_material_for` gerçek gömülü
doku üreten bir `PBRMaterial` döndürüyordu - ama bu yalnızca *prosedürel*
(dama deseni) bir dokuydu, roadmap'in "gerçek PBR doku kütüphanesi"
isteğini karşılamıyordu.

Bu proje aslında bu isteği zaten ele alan bir modül barındırıyor:
`material_engine.external_library.PBRMaterialLibrary` (ambientCG CC0
kütüphanesine bağlanan, ağ yoksa/başarısızsa sessizce `ProceduralMaterials`
placeholder'ına düşen bir istemci) - ama bu modül **hiçbir yerden
tüketilmiyordu**. `ai_reconstruction.material_predictor.AIMaterialPredictor`
bir bina için hangi `FacadeMaterial` (beton/tuğla/cam/metal/taş/ahşap/
kompozit/endüstriyel) kullanılacağını tahmin ediyordu, ama bu tahmin hiçbir
zaman gerçek bir `PBRMaterial` nesnesine (dolayısıyla `render_engine.
scene_bridge.Scene`'e, dolayısıyla viewer'a) çevrilmiyordu.

Bu modül o eksik köprüyü kurar: `MaterialPrediction` (tahmin) ->
`PBRMaterial` (gerçek/prosedürel doku) -> `Scene.add_mesh(mesh,
material=...)` ile doğrudan tüketilebilir sözlük.

Dürüstlük notu (external_library.py'nin kendi notuyla aynı): bu sandbox'ın
ağ erişimi ambientcg.com'a izin vermiyor, bu yüzden gerçek doku indirme
burada test edilemiyor - `PBRMaterialLibrary.get()` bu durumda (`source`
alanı `"procedural"` olarak işaretlenerek) sessizce `ProceduralMaterials`
placeholder'ına düşer; ağ erişimi olan bir ortamda değişiklik yapmadan
gerçek ambientCG dokuları devreye girer. Bu bilinçli bir tasarım, eksik
değil - `PBRMaterialLibrary` zaten bunun için var.
"""
from __future__ import annotations

from pathlib import Path

from ..material_engine import PBRMaterial
from ..material_engine.external_library import PBRMaterialLibrary
from .material_predictor import MaterialPrediction, SurfaceClass

#: Roadmap'in `~/.harita/` yerleşim yeri konvansiyonuyla tutarlı
#: (bkz. `app_shell/server.py` - registry de aynı kökte).
DEFAULT_MATERIAL_CACHE_DIR = Path.home() / ".harita" / "material_cache"


def resolve_pbr_material(
    prediction: MaterialPrediction,
    cache_dir: str | Path = DEFAULT_MATERIAL_CACHE_DIR,
    variation_seed: int | None = None,
    library: PBRMaterialLibrary | None = None,
) -> tuple[PBRMaterial, str]:
    """Bir `MaterialPrediction`'ı gerçek `(PBRMaterial, source)` çiftine
    çevirir. `source` ∈ {"cache", "network", "procedural"} -
    `PBRMaterialLibrary.get()`'in kendi sözleşmesi, olduğu gibi iletilir.

    `library` verilmezse `cache_dir`'den yeni bir `PBRMaterialLibrary`
    kurulur (varsayılan: `~/.harita/material_cache`) - tekrarlı çağrılarda
    aynı `library`'yi paylaşmak (aynı önbellek index'ini tekrar tekrar
    diskten okumamak için) önerilir, bkz. `resolve_building_materials`.
    """
    lib = library or PBRMaterialLibrary(cache_dir)
    return lib.get(prediction.material.value, variation_seed=variation_seed)


def resolve_building_materials(
    predictions: dict[SurfaceClass, MaterialPrediction],
    cache_dir: str | Path = DEFAULT_MATERIAL_CACHE_DIR,
    variation_seed: int | None = None,
) -> dict[str, PBRMaterial]:
    """`AIMaterialPredictor.predict_all_surfaces()` çıktısını (yüzey ->
    tahmin sözlüğü) `render_engine.scene_bridge.Scene`'in `materials`
    alanına doğrudan uygun bir sözlüğe (yüzey adı -> gerçek `PBRMaterial`)
    çevirir. Boş/eksik tahmin sözlüğünde boş sözlük döner - hata
    fırlatmaz (B4 "eksik veri sahneyi bozmasın" ilkesiyle tutarlı).

    Tek bir `PBRMaterialLibrary` örneği tüm yüzeyler için paylaşılır -
    bir binanın 5 yüzeyi için index.json 5 kez değil 1 kez okunur/yazılır
    (gereksiz disk I/O yok)."""
    if not predictions:
        return {}
    library = PBRMaterialLibrary(cache_dir)
    result: dict[str, PBRMaterial] = {}
    for surface_class, prediction in predictions.items():
        material, _source = resolve_pbr_material(
            prediction, variation_seed=variation_seed, library=library,
        )
        result[surface_class.value] = material
    return result


__all__ = [
    "DEFAULT_MATERIAL_CACHE_DIR",
    "resolve_pbr_material",
    "resolve_building_materials",
]
