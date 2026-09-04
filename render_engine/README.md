# render_engine (Faz 15 — ROADMAP V2) — ✅ Uygulandı

Roadmap V2 Track B, Faz 15 — **"Render Engine"**. Platformun en büyük
boşluğunu kapatır: önceki 14 fazın ürettiği veri/geometriyi gerçek zamanlı
olarak **görünür** kılmak.

## Neden bu şekilde tasarlandı

Python tarafında (proje ilkesi gereği: stdlib-only, harici GPU/oyun motoru
yok) gerçek bir GPU render pipeline'ı kurmak mümkün değil. Bu yüzden Faz 15
iki parçaya bölündü:

1. **`scene_bridge.py`** (Python, stdlib-only) — Faz 2 `Mesh3D`/`PBRMaterial`
   ve Faz 2 `lighting` (`SunLight`/`AmbientLight`) nesnelerini, kayıpsız,
   düz (flat) bir JSON `Scene` temsiline çevirir. Hiçbir çizim yapmaz —
   sadece veriyi GPU-dostu hale getirir.
2. **`viewer/index.html`** (bağımsız, tek dosya, kurulum gerektirmez) —
   WebGL2 tabanlı gerçek zamanlı 3D görüntüleyici. `Scene` JSON'unu okuyup
   orbit-kamera + Lambert/Blinn-Phong (PBR-lite) shading ile ekrana çizer.

Bu ayrım, roadmap'in "harici bağımlılık yok" ilkesini bozmadan gerçek bir
görselleştirme sağlar: tarayıcı zaten her makinede var, ekstra kurulum yok.

## Kullanım

```python
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction import Footprint, ProceduralBuildingGenerator, BuildingType
from harita.material_engine import PBRMaterial
from harita.render_engine import Scene

footprint = Footprint(
    polygon=Polygon([Point2D(0, 0), Point2D(20, 0), Point2D(20, 15), Point2D(0, 15)]),
    building_type="apartman",
    floor_count=5,
    height_m=15.0,
)
building = ProceduralBuildingGenerator.generate(
    footprint, building_type=BuildingType.APARTMAN, seed=11
)

scene = Scene(name="mahalle")
scene.add_mesh(building.full_mesh(), material=PBRMaterial(name="beton", albedo=(0.68, 0.66, 0.62)))
scene.write("scene.json")
```

Sonra `render_engine/viewer/index.html` dosyasını bir tarayıcıda açın,
soldaki "Sahne dosyası" alanından `scene.json`'u seçin — bina 60 FPS'de
orbit-kamerayla incelenebilir. Kurulum/sunucu gerekmez (`file://` ile
doğrudan açılabilir; WebGL2 destekleyen her modern tarayıcı yeterli).

Uçtan uca gerçek bir demo için:

```bash
python -m harita.render_engine.demo_export scene.json
```

Bu komut Faz 3 (Building Reconstruction) ile 3 farklı bina tipini
(apartman/ofis/villa) prosedürel olarak üretir, Faz 15 köprüsünden
geçirir ve `scene.json` olarak yazar.

## Sahne JSON şeması (v1.0)

```jsonc
{
  "schema_version": "1.0",
  "name": "...",
  "camera": { "target": [x,y,z], "distance": ..., "yaw_deg": ..., "pitch_deg": ..., "fov_deg": ..., "near": ..., "far": ... },
  "materials": { "isim": { "albedo":[r,g,b], "roughness":..., "metallic":..., "emissive":[r,g,b], "opacity":... } },
  "lights": [ { "kind": "ambient|directional", "color":[r,g,b], "intensity":..., "direction":[x,y,z]|null } ],
  "nodes": [
    {
      "name": "...", "material": "isim|null",
      "translation":[x,y,z], "rotation_deg":[x,y,z], "scale":[x,y,z],
      "positions": [...], "normals": [...]|null, "uvs": [...]|null,
      "indices": [...], "vertex_count": N, "triangle_count": M
    }
  ]
}
```

`normals` `null` ise viewer, indexed triangle'lardan flat-shading normalleri
kendisi hesaplar (bkz. `computeFlatNormals` — Faz 2 `NormalGenerator`'ın
JS tarafındaki minimal karşılığı).

## Kapsam ve sınırlamalar (bilinçli, belgelenmiş)

- Gölge haritası (shadow mapping) artık **var** — bkz. aşağıdaki
  "ROADMAP_V4 — Faz E1" bölümü. Bu bölümdeki eski not (ROADMAP_V2 A2/A9
  döneminden kalma) Faz E1 ile kapatıldı; güncel durum aşağıda anlatılıyor.
- Texture/materyal haritaları (`albedo_map`, `normal_map` vb.) henüz
  viewer'a taşınmadı — şu an yalnızca düz renk (`albedo`) + roughness/
  metallic kullanılıyor.
- Büyük sahnelerde Faz 13 `performance` (culling/LOD/streaming) artık
  **kısmen** entegre — bkz. aşağıdaki "ROADMAP_V3 Faz D2" bölümü. Tam
  gerçek-zamanlı frustum/occlusion culling (her karede JS tarafında) ve
  gerçek streaming/asset-eviction (Faz D8) hâlâ ROADMAP_V3 kapsamında
  bekleyen bir sonraki adım.

## ROADMAP_V3 — Faz D2: Render Engine ↔ Performance Köprüsü (Tamamlandı)

Faz 15'in ilk sürümünde Faz 13 (`performance`) LOD/culling sınıfları hiçbir
yerde `Scene`'e bağlanmıyordu — viewer her zaman sahnenin tamamını tek
seferde çiziyordu. Faz D2 bunu iki şekilde kapattı:

1. **LOD zinciri:** `Scene.add_mesh_with_lod()` D1'in gerçek QEM
   `MeshSimplifier.simplify()`'ını kullanarak her node için varsayılan
   3 seviyeli bir LOD zinciri üretir: `ratio=1.0` (0-50 birim, orijinal
   mesh), `ratio=0.5` (50-150 birim), `ratio=0.25` (150+ birim). Bu üç
   seviye `to_dict()` ile `lod_groups` alanına (schema `1.0` → `1.1`,
   geriye uyumlu ek alan) gömülür.
2. **Viewer entegrasyonu:** `viewer/index.html`, `lod_groups` içeren her
   node için seviye başına ayrı bir VAO/index-buffer kurar
   (`buildGpuLevel`) ve her karede kamera-node dünya-uzayı mesafesine göre
   hangi seviyenin çizileceğine karar verir (`pickLodLevel`, Python
   tarafındaki `select_lod_for_distance()` ile birebir aynı mantık).
   Çizilen toplam üçgen sayısı sidebar'da "Üçgen (çizilen, LOD)" olarak
   canlı gösterilir — "Üçgen (tam)" (LOD'suz referans) ile karşılaştırılıp
   kamera uzaklaştıkça düştüğü doğrudan gözlemlenebilir.
3. **Frustum-culling köprüsü:** `visible_node_names()`, Faz 13
   `FrustumCulling`'i `Scene` node'larının world-space AABB'lerine
   uygulayan bir köprü fonksiyonudur — Python tarafında hazırlanmış
   statik/başlangıç görünürlük listesi üretir (tam gerçek-zamanlı culling
   viewer'da JS ile, kamera her hareket ettiğinde yapılır — bu D2 kapsamı
   dışında kalan bir sonraki adım).

`lod_groups` olmayan (eski/LOD'suz) node'lar eskisi gibi tek seviyeyle
çizilir — geriye dönük uyumluluk tam korunur.

## ROADMAP_V4 — Faz E1: Gerçek Gölge Haritası, IBL ve Post-Processing Pipeline (Tamamlandı)

Faz 15/D2'nin ürettiği sahne ve LOD verisi ekrana geliyordu, ama Faz 2
`lighting`'in `ShadowMapPass`/`AmbientOcclusionBaker` çıktıları ve Faz 9
`RenderPipeline`'ın PBR/HDR/tonemap/FXAA "reçetesi" viewer'da hiç
kullanılmıyordu — veri üretiliyor ama ekrana yansımıyordu. E1 bunu kapattı:

1. **Gerçek zamanlı shadow-map pass'i:** `viewer/index.html`'de ayrı bir
   depth-only framebuffer (`shadowFBO`/`shadowDepthTex`) sahneyi güneşin
   bakış açısından render eder; ana shader bu derinlik dokusunu 3×3 PCF
   (percentage-closer filtering) ile örnekleyip yumuşak bir gölge faktörü
   üretir. Işık-uzayı projeksiyon matrisi (`M4.computeLightSpaceMatrix`,
   JS) ve onun Python referans karşılığı (`scene_bridge.compute_light_space_matrix`)
   **birebir aynı matematiği** uygular; güneş yönü (Faz 2 `SunLight`/D4 SPA)
   değiştiğinde gölgenin yönü/uzunluğu gerçek zamanlı değişir.
2. **D9 AO köprüsü:** `Scene.attach_vertex_ao()`, D9 `AmbientOcclusionBaker`
   çıktısını (daha önce yalnızca JSON'a gömülü, çizilmeyen bir veri)
   gerçekten vertex-attribute olarak GPU'ya taşır; fragment shader bunu
   diffuse terimle çarpar.
3. **HDR ara-tampon + post-processing:** Sahne önce bir HDR renk
   framebuffer'ına (`hdrFBO`/`hdrColorTex`) çizilir, ardından ayrı bir
   fullscreen-quad pass'i ACES-yaklaşık tonemap + basitleştirilmiş 3×3
   FXAA uygular — Faz 9 `RenderPipeline.default_pbr_pipeline()` sırasının
   ilk gerçek GPU karşılığı.
4. **HDRSky gökyüzü render'ı:** Faz 2 `HDRSky` veri modeli
   `hdr_sky_to_scene_sky()` ile `Scene.sky`'a (JSON) taşınır; `SKY_VS/FS`
   bunu derinlik yazmadan, sahnenin en arkasında ufuk/zenit gradyanı olarak
   çizer (equirect HDRI dosyası yoksa `HDRSky`'nin kendi analitik
   fallback'iyle tutarlı).

**Neden bu şekilde doğrulandı:** Bu ortamda gerçek bir tarayıcı/GPU
bulunmuyor, dolayısıyla roadmap'in önerdiği "headless canvas-pixel-
örnekleme" testi burada çalıştırılamıyor. Bunun yerine iki bağımsız
doğrulama katmanı kullanıldı:

- **Python tarafı (pytest, CI'da otomatik):**
  [`../tests/test_phaseE1_shadow_render_pipeline.py`](../tests/test_phaseE1_shadow_render_pipeline.py)
  — ışık-uzayı projeksiyonunun, tamamen bağımsız bir algoritma olan
  ray-triangle tabanlı `ShadowCalculator.point_in_shadow` (Faz 6) ile
  **aynı** gölge/aydınlık kararına vardığını çapraz kontrol eder; ayrıca
  D9 AO köprüsünü ve `HDRSky`→`SceneSky` dönüşümünü doğrular (12 test).
- **JS↔Python sayısal parite (manuel/CI script):**
  [`../scripts/check_e1_light_space_parity.mjs`](../scripts/check_e1_light_space_parity.mjs)
  — `index.html`'deki gerçek `M4.computeLightSpaceMatrix` JS kodunu
  (kopyalamadan, doğrudan kaynak dosyadan regex ile çekip) gerçek Node.js
  üzerinde çalıştırır ve çıktıyı Python referans implementasyonuyla
  karşılaştırmaya izin verir — böylece JS ve Python tarafı gerçekten
  aynı matrisi ürettiği, birinin diğerinden "kopyalanmış ama sonradan
  ıraksamış" olmadığı kanıtlanır. CI'da `ci.yml`'e eklenen ayrı bir adımla
  otomatik çalışır (bkz. aşağıdaki not).

**Dokunulan dosyalar:** `render_engine/viewer/index.html`,
`render_engine/scene_bridge.py`, `render_engine/README.md` (bu bölüm).

## İçerik

| Dosya | Sorumluluk |
|---|---|
| `scene_bridge.py` | `Scene`/`SceneNode`/`SceneLight` veri modeli, `Mesh3D`→JSON dönüşümü, kamera/bounding-box hesabı, `SunLight`/`AmbientLight` köprüleri, Faz D2 LOD üretimi (`add_mesh_with_lod`) ve frustum-culling köprüsü (`visible_node_names`) |
| `demo_export.py` | Faz 3 ile gerçek bina üretip `scene.json` yazan uçtan uca demo |
| `viewer/index.html` | WebGL2 renderer: orbit-kamera, Lambert+Blinn-Phong shading, grid, dosya yükleme, canlı istatistik paneli, Faz D2 kamera-mesafeli LOD seçimi |

Testler:
- [`../tests/test_phase15_render_engine.py`](../tests/test_phase15_render_engine.py)
  (21 test — sahne inşası, bounding box/kamera, serileştirme, ışık köprüsü,
  çok-node sahneler).
- [`../tests/test_phaseD2_render_performance_bridge.py`](../tests/test_phaseD2_render_performance_bridge.py)
  (13 test — LOD zinciri üretimi, `lod_groups` serileştirme, mesafeye göre
  LOD seçimi, 1024 binalık sahnede kamera-mesafeli üçgen sayısı azalması,
  frustum-culling köprüsü).
- [`../tests/test_phaseE1_shadow_render_pipeline.py`](../tests/test_phaseE1_shadow_render_pipeline.py)
  (12 test — ışık-uzayı projeksiyonunun ray-triangle `ShadowCalculator` ile
  çapraz doğrulaması, güneş yönü değiştiğinde projeksiyonun değişmesi, D9
  AO köprüsü, `HDRSky`→`SceneSky` dönüşümü) + `../scripts/check_e1_light_space_parity.mjs`
  (JS↔Python ışık-uzayı matrisi sayısal parite kontrolü, CI'da otomatik).

---

## ROADMAP_V4 — Faz E3: Hidroloji Görselleştirme Köprüsü (Tamamlandı)

`Scene.attach_flow_network(node, grid, accumulation_threshold=4.0)`
eklendi — `terrain_engine.FlowAccumulation`'ın D8 akış-yönü/akümülasyon
çıktısını, ilgili terrain mesh'iyle aynı dünya-uzayı dönüşümünü
kullanarak world-space çizgi-segmentlerine (`start`/`end`/`intensity`)
çevirir ve `Scene.flow_lines[node.name]`'e yazar. `to_dict()` çıktısına
yeni üst-seviye `flow_lines` anahtarı eklendi (`SCENE_SCHEMA_VERSION`
`1.2`→`1.3`); boş sözlük varsayılan olduğundan mevcut viewer/JSON
tüketicileri değişmeden çalışmaya devam eder — geriye uyumlu. Ayrıntı ve
kapanış notu için bkz. `terrain_engine/README.md` "ROADMAP_V4 — Faz E3"
bölümü ve `tests/test_phaseE3_terrain_hydrology.py`.
