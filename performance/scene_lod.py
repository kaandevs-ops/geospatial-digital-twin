"""
performance.scene_lod - Faz C4 (LOD/instancing/performans), 2. dilim:
mesafe bazlı LOD seçiminin `scene_instancing` gruplarına bağlanması
================================================================================

ROADMAP_V7.md Bölüm B3: "LOD (level of detail): uzak mesafede ağaç/direk
gibi küçük nesneler basit billboard/sprite'a düşsün, yakınlaşınca tam 3D
mesh." ve "Frustum/mesafe culling: görünmeyen/çok uzak nesneler render
edilmesin."

C4'ün 1. diliminde (`scene_instancing.py`) her OSM nokta-prop kategorisi
için origin-merkezli şablon + world-space transform listesi üretilmişti,
ama bu transformların hepsi mesafeden bağımsız olarak "tam detay" kabul
ediliyordu. Bu modül, zaten var olan `performance.culling.LODManager`
(Faz 13, **değiştirilmedi**) üzerine ince bir köprü kurarak, bir kamera
konumuna göre her `InstanceGroup`'un transformlarını üç kovaya ayırır:

- ``full``    : tam detay - `InstanceGroup.base_mesh` olduğu gibi kullanılır.
- ``impostor``: orta mesafe - üçgen sayısı `IMPOSTOR_TRIANGLE_FACTOR` ile
  yaklaşık olarak indirgenmiş kabul edilir (bkz. aşağıdaki dürüstlük notu).
- ``culled``  : çok uzak - hiç render edilmez (B3 "çok uzak nesneler render
  edilmesin").

Dürüstlük notu (güncellendi — bu oturumda kısmen kapatıldı): önceki
dilim gerçek bir ikinci (düşük-poly/billboard) mesh üretmiyordu, yalnızca
`IMPOSTOR_TRIANGLE_FACTOR` ile üçgen sayısını yaklaşık temsil ediyordu.
Bu oturumda `build_cross_billboard_impostor` eklendi: her şablonun
`base_mesh`'inden bounding-box tabanlı, gerçek 4 üçgenlik bir
cross-billboard mesh üretir (`LODInstanceGroup.build_impostor_mesh()`).
Maliyet muhasebesi (`rendered_triangle_count`, `IMPOSTOR_TRIANGLE_FACTOR`)
bilinçli olarak **değiştirilmedi** — gerçek impostor mesh'in kendi üçgen
sayısı (4) şablona göre değişkendir ve mevcut testler/ölçümler sabit
katsayıya dayanıyor; ikisini birleştirmek (dinamik katsayı) ayrı bir
kalibrasyon çalışması gerektirir ve kapsam dışı bırakıldı.

Faz C4 dürüstlük notundaki (a) ve (b) maddeleri sonraki oturumlarda
kapatıldı: (a) doku/materyal ataması `impostor_material_for`/
`build_impostor_texture` ile (bkz. aşağıda), (b) render-engine'e
(gerçek mimari: projenin kendi ham WebGL2 motoru, Three.js DEĞİL)
uçtan uca bağlanma `app_shell/web/index.html` içindeki
`decodeHaritaTextureDataURI`/`loadMaterialTextures` ile.

ROADMAP_V7.md'nin daha önce açık bıraktığı `OcclusionCulling` entegrasyonu
bu oturumda kapatıldı. Önceki not, "hangi nesnelerin occluder sayılacağı"
sorusunun roadmap metninde tanımlanmadığını, bu yüzden tahminle
doldurulmayacağını söylüyordu - bu hâlâ doğru, ama proje ilerledikçe
cevap artık tahmin değil, sahnenin kendi verisinden çıkan açık bir karar
haline geldi: **B3'ün "1000+ karışık feature" senaryosunda sahnedeki tek
büyük, static, opak hacimler binalardır** (yollar/su düz, ince şeritler -
anlamlı occluder değiller; ağaç/direk/bank gibi nokta prop'lar zaten kendi
LOD kovalarında ve çoğu diğerlerinden küçük - occluder olarak
kullanılmaları hem ucuz kazanç sağlamaz hem de yanlış-pozitif risklidir).
Bu yüzden **occlusion politikası: yalnızca bina (building) mesh'lerinin
bounding box'ları occluder sayılır** - `building_occluder_aabbs()` bu
politikayı somutlaştırır. `apply_lod_to_group`/`apply_scene_lod`'a
`frustum` ile birebir aynı desende opsiyonel bir `occlusion:
OcclusionCulling | None` parametresi eklendi; sonuç yeni, ayrı bir
`OCCLUDED` kovasına düşer (ne `CULLED` ne `FRUSTUM_CULLED` ile
karıştırılır - istatistiklerde "neden render edilmiyor" sorusu üç yönlü
dürüstçe ayırt edilebilir kalır). `performance.culling.OcclusionCulling`
(Faz 13) **değiştirilmedi**, yalnızca tüketildi.
"""

from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass, field

from ..data_engine.spatial_index import AABB3D
from ..material_engine import PBRMaterial, TextureMap, TextureMapCodec
from ..mesh_engine import Mesh3D, Vertex3D
from ..mesh_engine.batching import InstanceTransform
from ..visualization.camera_rig import Camera
from .culling import FrustumCulling, LODLevel, LODManager, OcclusionCulling
from .scene_instancing import InstanceGroup, SceneInstancingResult

Vec3 = tuple[float, float, float]

#: `impostor` kovasındaki bir instance'ın gerçek üçgen maliyetinin,
#: `full` kovasındaki aynı şablona göre tahmini oranı (bkz. modül
#: docstring'indeki dürüstlük notu). `culled` kovası için bu oran 0'dır
#: (hiç render edilmez).
IMPOSTOR_TRIANGLE_FACTOR = 0.15

#: B3'ün "uzak mesafe" / "çok uzak mesafe" eşikleri (metre). Terrain LOD
#: (Faz 2) ile aynı büyüklük mertebesinde, nokta-prop'lar (ağaç/direk/bank)
#: binalardan küçük olduğu için biraz daha yakın eşiklerle.
DEFAULT_FULL_DETAIL_DISTANCE_M = 60.0
DEFAULT_IMPOSTOR_DISTANCE_M = 250.0

FULL = "full"
IMPOSTOR = "impostor"
CULLED = "culled"
#: Faz 13 (`performance.culling.FrustumCulling`) ile bu modülün mesafe
#: tabanlı LOD kovalarının birleştirilmesi — roadmap'in ROADMAP_V7.md'de
#: en son "Kalan" olarak işaretlediği madde: "FrustumCulling/
#: OcclusionCulling ile LOD kovalarının birleşimi". `CULLED`'dan ayrı
#: tutulur ki mesafeden mi yoksa görüş konisi dışında olmaktan mı
#: elendiği istatistiklerde dürüstçe ayırt edilebilsin (bkz.
#: `apply_lod_to_group` docstring'i).
FRUSTUM_CULLED = "frustum_culled"
#: Occlusion-politikası kovası (bkz. modül docstring'i): kamera->hedef
#: ışını bir bina occluder AABB'siyle kesişiyorsa ve kesişim hedeften
#: daha yakınsa instance burada biriktirilir. `FRUSTUM_CULLED`'dan ayrı
#: tutulur - biri "görüş konisi dışında", diğeri "konide ama bir bina
#: tarafından gizlenmiş" anlamına gelir, istatistiksel olarak farklı
#: nedenler.
OCCLUDED = "occluded"


def default_instance_lod_manager(
    full_distance_m: float = DEFAULT_FULL_DETAIL_DISTANCE_M,
    impostor_distance_m: float = DEFAULT_IMPOSTOR_DISTANCE_M,
) -> LODManager:
    """B3'ün üç seviyeli (tam detay / impostor / culled) LOD isteğini
    mevcut `LODManager` (Faz 13, değiştirilmedi) üzerinden kurar."""
    return LODManager(
        [
            LODLevel(max_distance=full_distance_m, mesh_key=FULL),
            LODLevel(max_distance=impostor_distance_m, mesh_key=IMPOSTOR),
            LODLevel(max_distance=math.inf, mesh_key=CULLED),
        ]
    )


#: B3'ün "doku/materyal ataması" maddesi için — şablon anahtarının önekine
#: (`tree:`, `furniture:`, `religious:`, `commerce:`, `power:`, `sport:`)
#: göre kategori-uygun düz-renk `PBRMaterial` (glTF metallic/roughness
#: uyumlu). `material_engine.ProceduralMaterials`'ın mevcut ön ayarları
#: (`cam`/`beton`/`tugla`/...) bina cephesi içindir - bu impostor'lar için
#: kategori uyumsuzluğu var (bir ağaç "beton" değildir), bu yüzden mevcut
#: ön ayar sözlüğü **değiştirilmeden**, ayrı ve küçük bir eşleme burada
#: tutulur (roadmap'in "mevcut mimari korunacak" ilkesi - var olan API'ye
#: yeni bir önek eklemek yerine, ayrı bir sorumluluk olarak izole edildi).
_IMPOSTOR_MATERIAL_PRESETS: dict[str, dict] = {
    "tree": dict(albedo=(0.20, 0.45, 0.18), roughness=0.85, metallic=0.0, opacity=0.9),
    "furniture": dict(albedo=(0.42, 0.30, 0.20), roughness=0.7, metallic=0.0, opacity=1.0),
    "religious": dict(albedo=(0.75, 0.72, 0.65), roughness=0.6, metallic=0.0, opacity=1.0),
    "commerce": dict(albedo=(0.55, 0.5, 0.45), roughness=0.65, metallic=0.05, opacity=1.0),
    "power": dict(albedo=(0.5, 0.5, 0.52), roughness=0.4, metallic=0.7, opacity=1.0),
    "sport": dict(albedo=(0.65, 0.55, 0.25), roughness=0.6, metallic=0.0, opacity=1.0),
}
#: Yukarıdaki kategori tablosunda karşılığı olmayan (bilinmeyen/gelecekte
#: eklenecek) şablon önekleri için güvenli, nötr varsayılan.
_IMPOSTOR_MATERIAL_DEFAULT = dict(albedo=(0.6, 0.6, 0.6), roughness=0.6, metallic=0.0, opacity=1.0)


def build_impostor_texture(template_key: str, size: int = 8) -> TextureMap:
    """B3'ün "doku/materyal ataması" maddesinin önceki oturumda açık
    bırakılan gerçek doku kısmı: kategoriye göre iki tonlu, deterministik
    bir dama-tahtası (checkerboard) deseni üretir ve `TextureMap` (3
    kanallı, RGB) olarak döner.

    Roadmap'in stdlib-only ilkesiyle tutarlı: PIL/numpy gibi bir
    bağımlılık yerine, `_IMPOSTOR_MATERIAL_PRESETS`'teki albedo rengiyle
    aynı temel tonun açık/koyu varyasyonlarını `texture_baking.TextureMap`
    ham piksel formatına (row-major, RGB, uint8) doğrudan yazar. Bu
    fotogerçekçi bir doku DEĞİLDİR (bkz. modül docstring'i) - amaç
    `TextureMapCodec`/`PBRMaterial.albedo_map` boru hattının uçtan uca
    gerçekten çalıştığını (yalnızca bir metaveri alanı olarak değil)
    kanıtlamak, billboard/sprite'a tek-düze bir yüzeyden daha fazla görsel
    ayrışım (doku hissi) kazandırmaktır.
    """
    prefix = template_key.split(":", 1)[0].lower()
    params = _IMPOSTOR_MATERIAL_PRESETS.get(prefix, _IMPOSTOR_MATERIAL_DEFAULT)
    r, g, b = params["albedo"]

    def _clamp255(v: float) -> int:
        return max(0, min(255, int(round(v * 255))))

    dark = (_clamp255(r * 0.65), _clamp255(g * 0.65), _clamp255(b * 0.65))
    light = (
        _clamp255(min(1.0, r * 1.15)),
        _clamp255(min(1.0, g * 1.15)),
        _clamp255(min(1.0, b * 1.15)),
    )

    pixels = bytearray(size * size * 3)
    cell = max(1, size // 4)
    for y in range(size):
        for x in range(size):
            checker = ((x // cell) + (y // cell)) % 2 == 0
            color = light if checker else dark
            idx = (y * size + x) * 3
            pixels[idx : idx + 3] = bytes(color)
    return TextureMap(width=size, height=size, channels=3, pixels=bytes(pixels))


def _texture_to_data_uri(tex: TextureMap) -> str:
    """`TextureMap`'i, `PBRMaterial.albedo_map`'in beklediği `str` tipine
    uyan, kendi kendine yeten bir referansa çevirir. Gerçek bir dosya
    yoluna ihtiyaç duymaz (roadmap'in "bağımlılıksız, JSON-gömülebilir"
    ilkesi, `TextureMapCodec` docstring'iyle aynı) - `TextureMapCodec.
    encode` çıktısını (zaten base64) tek bir `data:` URI string'ine
    paketler; `TextureMapCodec.decode`'un beklediği sözlüğü yeniden
    üretmek için `harita-texture-v1` şema etiketiyle JSON olarak gömer.
    """
    encoded = TextureMapCodec.encode(tex)
    payload = base64.b64encode(json.dumps(encoded).encode("ascii")).decode("ascii")
    return f"data:harita-texture-v1;base64,{payload}"


def decode_impostor_texture_data_uri(data_uri: str) -> TextureMap:
    """`_texture_to_data_uri`'nin tersi - tüketici tarafın (render-engine
    köprüsü, test) gömülü dokuyu geri `TextureMap`'e çevirmesi için."""
    prefix = "data:harita-texture-v1;base64,"
    if not data_uri.startswith(prefix):
        raise ValueError("decode_impostor_texture_data_uri: beklenmeyen şema/önek.")
    payload = json.loads(base64.b64decode(data_uri[len(prefix) :]).decode("ascii"))
    return TextureMapCodec.decode(payload)


def impostor_material_for(template_key: str, with_texture: bool = True) -> PBRMaterial:
    """B3'ün "doku/materyal ataması" maddesi: `template_key`'in önekine
    (`:`'dan önceki kısım, örn. `"tree:conifer:8"` -> `"tree"`) göre
    kategori-uygun bir `PBRMaterial` döner.

    `with_texture=True` (varsayılan): `build_impostor_texture` ile
    üretilen gerçek dama-tahtası dokusu `albedo_map`'e gömülü bir `data:`
    URI olarak atanır - `render_engine.scene_bridge`'in materials_json
    çıktısında artık gerçek bir doku referansı taşınır (bu oturumda
    `scene_bridge.py`'ye eklenen serileştirme desteğiyle birlikte, bkz.
    o modülün değişiklik notu). `with_texture=False`: yalnızca düz renk
    (önceki dilimin davranışı, geriye dönük uyumluluk/performans için -
    her çağrıda doku üretmek istemeyen tüketiciler için opsiyonel).
    """
    prefix = template_key.split(":", 1)[0].lower()
    params = dict(_IMPOSTOR_MATERIAL_PRESETS.get(prefix, _IMPOSTOR_MATERIAL_DEFAULT))
    albedo_map = None
    if with_texture:
        albedo_map = _texture_to_data_uri(build_impostor_texture(template_key))
    return PBRMaterial(name=f"impostor_{prefix}", albedo_map=albedo_map, **params)


def build_cross_billboard_impostor(base_mesh: Mesh3D, name_suffix: str = "_impostor") -> Mesh3D:
    """B3'ün "uzak mesafede basit billboard/sprite'a düşsün" maddesinin
    gerçek karşılığı: `base_mesh`'in bounding box'ından, iki dik açılı
    (çapraz) quad'dan oluşan 4 üçgenlik gerçek bir geometri üretir
    (oyun motorlarında yaygın "cross-billboard" impostor tekniği - iki
    quad tek eksenli bir billboard'dan farklı olarak her açıdan bir
    siluet verir). `IMPOSTOR_TRIANGLE_FACTOR` (0.15) ile önceki dilimin
    yalnızca ÜÇGEN SAYISI yaklaşıklığı yerine artık gerçek, render
    edilebilir bir ikinci mesh var - modülün eski dürüstlük notundaki
    eksik burada kapatıldı. Maliyet muhasebesi
    (`LODInstanceGroup.rendered_triangle_count`) **değiştirilmedi**;
    bu fonksiyon ayrı, opsiyonel bir geometri üretici olarak eklendi.

    Boş/dejenere mesh (0 vertex) için boş bir `Mesh3D` döner (güvenli
    varsayılan - çağıran taraf `triangle_count() == 0` ile kontrol
    edebilir).
    """
    if not base_mesh.vertices:
        return Mesh3D(name=f"{base_mesh.name}{name_suffix}")
    (min_x, min_y, min_z), (max_x, max_y, max_z) = base_mesh.bounding_box()
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    half_w = max((max_x - min_x), (max_y - min_y)) / 2.0
    if half_w <= 0.0:
        half_w = 0.5
    z0, z1 = min_z, max_z if max_z > min_z else min_z + 1.0

    # Quad A: X ekseni boyunca (cy sabit), Quad B: Y ekseni boyunca (cx sabit).
    verts = [
        Vertex3D(cx - half_w, cy, z0, uv=(0.0, 0.0)),
        Vertex3D(cx + half_w, cy, z0, uv=(1.0, 0.0)),
        Vertex3D(cx + half_w, cy, z1, uv=(1.0, 1.0)),
        Vertex3D(cx - half_w, cy, z1, uv=(0.0, 1.0)),
        Vertex3D(cx, cy - half_w, z0, uv=(0.0, 0.0)),
        Vertex3D(cx, cy + half_w, z0, uv=(1.0, 0.0)),
        Vertex3D(cx, cy + half_w, z1, uv=(1.0, 1.0)),
        Vertex3D(cx, cy - half_w, z1, uv=(0.0, 1.0)),
    ]
    triangles: list[Triangle] = [
        (0, 1, 2),
        (0, 2, 3),  # Quad A (2 uzgen)
        (4, 5, 6),
        (4, 6, 7),  # Quad B (2 uzgen)
    ]
    return Mesh3D(vertices=verts, triangles=triangles, name=f"{base_mesh.name}{name_suffix}")


def _instance_world_aabb(base_mesh: Mesh3D, transform: InstanceTransform) -> AABB3D | None:
    """`FrustumCulling.is_visible`'ın beklediği dünya-uzayı `AABB3D`'yi bir
    `InstanceTransform`'dan üretir. `base_mesh` boşsa (0 vertex) `None`
    döner - çağıran taraf bu durumda frustum testini atlayıp mesafe
    tabanlı kovayı olduğu gibi korur (güvenli varsayılan, yanlışlıkla
    eleme yok).

    `rotation_deg_z` göz ardı edilir: AABB, XY düzleminde yarım-köşegen
    yarıçapı kadar bir kare olarak genişletilir (döndürmeden bağımsız,
    her açıda kutuyu içine alan en küçük eksene-hizalı kare) - bu,
    `FrustumCulling`'in kendi modül docstring'indeki "açı-tabanlı
    yaklaşıklık" ilkesiyle tutarlı, muhafazakâr (yanlış-negatif değil,
    olsa olsa yanlış-pozitif verir) bir basitleştirmedir.
    """
    if not base_mesh.vertices:
        return None
    (min_x, min_y, min_z), (max_x, max_y, max_z) = base_mesh.bounding_box()
    scale = transform.scale
    half_x = (max_x - min_x) / 2.0 * scale
    half_y = (max_y - min_y) / 2.0 * scale
    half_diag = math.hypot(half_x, half_y) or 0.5
    tx, ty, tz = transform.translation
    z0 = tz + min_z * scale
    z1 = tz + max_z * scale
    if z1 < z0:
        z0, z1 = z1, z0
    if z1 == z0:
        z1 = z0 + max(0.5, scale)
    return AABB3D(
        min_x=tx - half_diag,
        min_y=ty - half_diag,
        min_z=z0,
        max_x=tx + half_diag,
        max_y=ty + half_diag,
        max_z=z1,
    )


def _mesh_to_aabb(mesh: Mesh3D) -> AABB3D | None:
    """Tek bir mesh'i `AABB3D`'ye çevirir; boş/vertexsiz mesh için `None`
    döner (savunmacı - bozuk/kısmi veri sessizce atlanır, B4 ilkesi)."""
    if not mesh.vertices:
        return None
    (min_x, min_y, min_z), (max_x, max_y, max_z) = mesh.bounding_box()
    return AABB3D(min_x=min_x, min_y=min_y, min_z=min_z, max_x=max_x, max_y=max_y, max_z=max_z)


def building_occluder_aabbs(building_meshes: list[Mesh3D]) -> list[AABB3D]:
    """Occlusion politikasının ESKİ, yalnızca-bina girişi (geriye dönük tam
    uyumlu - hiçbir mevcut çağıran/kabul kriteri bozulmaz). Yeni kod
    `occluder_aabbs_from_scene()`'i tercih etmeli (bkz. aşağıdaki dürüstlük
    notu - denetimde "sadece statik bina AABB'lerini occluder sayıyor"
    maddesi burada genişletildi)."""
    result: list[AABB3D] = []
    for mesh in building_meshes:
        aabb = _mesh_to_aabb(mesh)
        if aabb is not None:
            result.append(aabb)
    return result


#: Occluder'a kabul edilecek asgari yükseklik (m) - bu eşiğin altındaki
#: nesneler (ör. bir kaldırım kenar taşı, alçak bir bordür) ışın-kesişim
#: testinde hedefi neredeyse hiç gizlemez ama `OcclusionCulling.is_occluded`
#: içinde her occluder için bir ray-cast maliyeti ekler - yanlış-pozitif
#: gizleme + gereksiz CPU maliyeti riskini birlikte azaltmak için filtrelenir.
DEFAULT_MIN_OCCLUDER_HEIGHT_M = 2.0

#: Occluder'a kabul edilecek asgari taban alanı (m^2, XY izdüşümü) - ince/
#: uzun tek-nokta prop'ların (direk, ağaç gövdesi) AABB'si dar olsa da
#: yüksek olabilir; yalnızca yükseklik eşiği bunları elemeyebilir, bu yüzden
#: taban alanı eşiği de ayrıca uygulanır (ikisi birlikte "gerçekten hacimli/
#: duvar-gibi bir engel mi" testini oluşturur).
DEFAULT_MIN_OCCLUDER_FOOTPRINT_M2 = 4.0


def _passes_occluder_size_filter(
    aabb: AABB3D,
    *,
    min_height_m: float,
    min_footprint_m2: float,
) -> bool:
    height = aabb.max_z - aabb.min_z
    footprint = (aabb.max_x - aabb.min_x) * (aabb.max_y - aabb.min_y)
    return height >= min_height_m and footprint >= min_footprint_m2


def occluder_aabbs_from_scene(
    building_meshes: list[Mesh3D],
    *,
    road_meshes: list[Mesh3D] | None = None,
    water_meshes: list[Mesh3D] | None = None,
    prop_meshes: list[Mesh3D] | None = None,
    min_height_m: float = DEFAULT_MIN_OCCLUDER_HEIGHT_M,
    min_footprint_m2: float = DEFAULT_MIN_OCCLUDER_FOOTPRINT_M2,
) -> list[AABB3D]:
    """Denetim maddesi (a) - "OcclusionCulling occluder listesi şu an
    sadece statik bina AABB'lerini occluder sayıyor (yol/su/prop'lar
    dahil değil)" - burada kapatılır.

    Genişletilmiş politika: binalar KOŞULSUZ occluder'dır (önceki
    davranış birebir korunur - `min_height_m`/`min_footprint_m2` bina
    listesine UYGULANMAZ, geriye dönük uyumluluk ve mevcut testler için).
    `road_meshes`/`water_meshes`/`prop_meshes` ise OPSİYONELDİR (`None` ->
    önceki davranışla birebir aynı sonuç, hiçbir mevcut çağıran bozulmaz)
    ve verildiğinde `min_height_m`/`min_footprint_m2` boyut filtresinden
    geçen mesh'ler occluder listesine eklenir.

    Boyut filtresinin gerekçesi: bir yol şeridi neredeyse düz (yükseklik
    ~0), bir ağaç/direk ise ince ama yüksek olabilir - ikisi de "duvar
    gibi" bir görsel engel oluşturmaz ve occluder sayılırsa yanlış-pozitif
    gizleme (görünmesi gereken bir nesnenin gizli sayılması) üretir. Bu
    yüzden yol/su/prop mesh'leri KOŞULSUZ değil, boyut eşiğinden GEÇENLER
    eklenir - ör. bir köprü gövdesi, bir istinat duvarı, büyük bir su
    yapısı (baraj/rıhtım) occluder olur; düz bir yol şeridi veya bir sokak
    lambası olmaz. Eşikler kasıtlı olarak parametrik bırakıldı - roadmap'in
    "bilinçli tasarım kararı" notuyla tutarlı, ama artık genişletilebilir."""
    result = building_occluder_aabbs(building_meshes)
    for mesh_list in (road_meshes, water_meshes, prop_meshes):
        if not mesh_list:
            continue
        for mesh in mesh_list:
            aabb = _mesh_to_aabb(mesh)
            if aabb is None:
                continue
            if _passes_occluder_size_filter(
                aabb,
                min_height_m=min_height_m,
                min_footprint_m2=min_footprint_m2,
            ):
                result.append(aabb)
    return result


@dataclass(slots=True)
class LODInstanceGroup:
    """Bir `InstanceGroup`'un, bir kamera konumuna göre üç LOD kovasına
    ayrılmış hali. `base_mesh`, orijinal `InstanceGroup`'tan değişmeden
    referans alınır (kopyalanmaz - bellek israfı yok)."""

    template_key: str
    base_mesh_triangle_count: int
    buckets: dict[str, list[InstanceTransform]] = field(default_factory=dict)
    base_mesh: Mesh3D | None = None

    def instance_count(self, bucket: str) -> int:
        return len(self.buckets.get(bucket, ()))

    def total_instance_count(self) -> int:
        return sum(len(v) for v in self.buckets.values())

    def rendered_triangle_count(self) -> int:
        """Bu grubun LOD sonrası TAHMİNİ render üçgen maliyeti - `full`
        tam ağırlık, `impostor` `IMPOSTOR_TRIANGLE_FACTOR` ağırlık,
        `culled` sıfır (bkz. modül docstring'indeki dürüstlük notu)."""
        full_n = self.instance_count(FULL)
        impostor_n = self.instance_count(IMPOSTOR)
        base = self.base_mesh_triangle_count
        return int(base * full_n + base * impostor_n * IMPOSTOR_TRIANGLE_FACTOR)

    def build_impostor_mesh(self) -> Mesh3D | None:
        """Bu grup için gerçek cross-billboard impostor mesh'i üretir
        (bkz. `build_cross_billboard_impostor`). `base_mesh` bilinmiyorsa
        (örn. eski bir `apply_lod_to_group` çağrısından `base_mesh`
        verilmeden oluşturulmuş bir grup) `None` döner - geriye dönük
        uyumluluk için opsiyonel."""
        if self.base_mesh is None:
            return None
        return build_cross_billboard_impostor(self.base_mesh)

    def build_impostor_material(self, with_texture: bool = True) -> PBRMaterial:
        """Bu grubun `template_key`'ine göre kategori-uygun `PBRMaterial`
        (bkz. `impostor_material_for`, artık gerçek gömülü doku dahil).
        `base_mesh` gerektirmez - her zaman bir sonuç döner (bilinmeyen
        kategoriler için nötr varsayılan)."""
        return impostor_material_for(self.template_key, with_texture=with_texture)


@dataclass(slots=True)
class LODAwareSceneResult:
    """Bir `SceneInstancingResult`'ın tüm gruplarına LOD uygulanmış hali -
    B3'ün "1000+ karışık feature kabul edilebilir FPS'te render edilmeli"
    kabul kriterine giren ölçülebilir çıktı."""

    groups: dict[str, LODInstanceGroup] = field(default_factory=dict)

    def total_instance_count(self, bucket: str | None = None) -> int:
        if bucket is None:
            return sum(g.total_instance_count() for g in self.groups.values())
        return sum(g.instance_count(bucket) for g in self.groups.values())

    def total_rendered_triangle_count(self) -> int:
        return sum(g.rendered_triangle_count() for g in self.groups.values())

    def culled_ratio(self) -> float:
        """Toplam instance sayısına oranla render edilmeyen (mesafe
        tabanlı `culled`, görüş konisi dışı `frustum_culled` VEYA bir
        bina tarafından gizlenmiş `occluded`) instance oranı (0..1) -
        B3'ün "görünmeyen/çok uzak nesneler render edilmesin" kriterinin
        doğrudan kanıtı. `frustum`/`occlusion` hiç kullanılmadıysa
        (ilgili kova boş/yok) bu, önceki davranışla birebir aynıdır."""
        total = self.total_instance_count()
        if total == 0:
            return 0.0
        not_rendered = (
            self.total_instance_count(CULLED)
            + self.total_instance_count(FRUSTUM_CULLED)
            + self.total_instance_count(OCCLUDED)
        )
        return not_rendered / total

    def frustum_culled_ratio(self) -> float:
        """Yalnızca görüş konisi dışı kalarak elenen instance oranı
        (0..1) - mesafe tabanlı elemeden ayrı, `FrustumCulling`
        entegrasyonunun kendi başına ne kadar etkili olduğunun kanıtı."""
        total = self.total_instance_count()
        if total == 0:
            return 0.0
        return self.total_instance_count(FRUSTUM_CULLED) / total

    def occluded_ratio(self) -> float:
        """Yalnızca bir bina occluder'ı tarafından gizlenerek elenen
        instance oranı (0..1) - occlusion politikasının (bkz. modül
        docstring'i, `building_occluder_aabbs`) kendi başına ne kadar
        ek kazanç sağladığının kanıtı, frustum/mesafe elemeden ayrı."""
        total = self.total_instance_count()
        if total == 0:
            return 0.0
        return self.total_instance_count(OCCLUDED) / total


def apply_lod_to_group(
    group: InstanceGroup,
    camera: Camera,
    lod_manager: LODManager | None = None,
    frustum: FrustumCulling | None = None,
    occlusion: OcclusionCulling | None = None,
) -> LODInstanceGroup:
    """Tek bir `InstanceGroup`'un transformlarını, kamera konumuna olan
    mesafeye göre `full`/`impostor`/`culled` kovalarına ayırır.

    `frustum` (opsiyonel, varsayılan `None` - geriye dönük tam uyumlu):
    verilirse, önce mesafe tabanlı kova belirlenir (mevcut davranış,
    değişmedi); `full`/`impostor` kovasına düşen ve `base_mesh`'i bilinen
    (`group.base_mesh is not None`) her instance için `FrustumCulling.
    is_visible` ile kamera görüş konisi testi yapılır - kapsam dışıysa
    instance `frustum_culled` kovasına taşınır.

    `occlusion` (opsiyonel, varsayılan `None` - geriye dönük tam uyumlu):
    verilirse, frustum testinden SONRA hâlâ `full`/`impostor` kovasında
    kalan her instance için `OcclusionCulling.is_occluded` ile kamera->
    instance ışını occluder listesine (bkz. `building_occluder_aabbs`,
    modül docstring'indeki politika) karşı test edilir - gizliyse instance
    `occluded` kovasına taşınır. Sıralama bilinçli: önce ucuz/kaba testler
    (mesafe, frustum), en sona en pahalı test (occluder listesi taraması)
    - gereksiz hesaplama yapılmaz.

    Her iki parametre de `None` olduğunda davranış birebir eskisiyle
    aynı kalır (geriye dönük tam uyumluluk)."""
    manager = lod_manager or default_instance_lod_manager()
    buckets: dict[str, list[InstanceTransform]] = {FULL: [], IMPOSTOR: [], CULLED: []}
    if frustum is not None:
        buckets[FRUSTUM_CULLED] = []
    if occlusion is not None:
        buckets[OCCLUDED] = []
    for transform in group.transforms:
        bucket = manager.select_mesh_key(camera.position, transform.translation)
        if frustum is not None and bucket in (FULL, IMPOSTOR) and group.base_mesh is not None:
            world_aabb = _instance_world_aabb(group.base_mesh, transform)
            if world_aabb is not None and not frustum.is_visible(camera, world_aabb):
                bucket = FRUSTUM_CULLED
        if occlusion is not None and bucket in (FULL, IMPOSTOR):
            if occlusion.is_occluded(camera.position, transform.translation):
                bucket = OCCLUDED
        buckets.setdefault(bucket, []).append(transform)
    return LODInstanceGroup(
        template_key=group.template_key,
        base_mesh_triangle_count=group.base_triangle_count(),
        buckets=buckets,
        base_mesh=group.base_mesh,
    )


def apply_scene_lod(
    result: SceneInstancingResult,
    camera: Camera,
    lod_manager: LODManager | None = None,
    frustum: FrustumCulling | None = None,
    occlusion: OcclusionCulling | None = None,
) -> LODAwareSceneResult:
    """`build_scene_instancing_result` (C4/1. dilim) çıktısındaki TÜM
    kategorilere tek çağrıda LOD uygular - B3'ün nihai kabul kriteri
    için giriş noktası. `frustum`/`occlusion` parametreleri
    `apply_lod_to_group`'a olduğu gibi iletilir (bkz. o fonksiyonun
    docstring'i). Tipik kullanım: `occlusion=OcclusionCulling(
    building_occluder_aabbs(building_meshes))`."""
    manager = lod_manager or default_instance_lod_manager()
    groups = {
        key: apply_lod_to_group(group, camera, manager, frustum=frustum, occlusion=occlusion)
        for key, group in result.groups.items()
    }
    return LODAwareSceneResult(groups=groups)


__all__ = [
    "IMPOSTOR_TRIANGLE_FACTOR",
    "DEFAULT_FULL_DETAIL_DISTANCE_M",
    "DEFAULT_IMPOSTOR_DISTANCE_M",
    "FULL",
    "IMPOSTOR",
    "CULLED",
    "FRUSTUM_CULLED",
    "OCCLUDED",
    "building_occluder_aabbs",
    "occluder_aabbs_from_scene",
    "DEFAULT_MIN_OCCLUDER_HEIGHT_M",
    "DEFAULT_MIN_OCCLUDER_FOOTPRINT_M2",
    "default_instance_lod_manager",
    "build_cross_billboard_impostor",
    "build_impostor_texture",
    "decode_impostor_texture_data_uri",
    "impostor_material_for",
    "LODInstanceGroup",
    "LODAwareSceneResult",
    "apply_lod_to_group",
    "apply_scene_lod",
]
