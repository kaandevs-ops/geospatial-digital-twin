# export

Roadmap Phase 11 — **Export**. Tam teknik spesifikasyon:
[`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)

## Durum: ✅ Uygulandı (çalışan kod + testler, 27 test) — Roadmap V3 Faz D7 ile genişletildi (16 test) — ROADMAP_V4 Faz E5 ile genişletildi (bkz. aşağı, 18 yeni test)

## Modüller

- **`geometry_3d.py`** — 3D exporterlar:
  - `OBJExporter` — Wavefront OBJ (+ `.mtl` materyal dosyası, normal/UV varsa yazar)
  - `STLExporter` — ASCII ve binary STL (yüzey normalleri hesaplanır)
  - `PLYExporter` — Stanford PLY (ASCII), normal/UV vertex özellikleri opsiyonel
  - `GLTFExporter` — glTF 2.0 (`export_gltf`: JSON+.bin çifti, `export_glb`: tek dosya binary konteyner), POSITION/NORMAL/TEXCOORD_0 + indexed triangle
  - `GLTFImporter` — glTF/GLB **okuyucu** (Roadmap V2, A1): `import_gltf`/`import_glb`, `GLTFExporter` çıktısıyla round-trip uyumlu; magic/chunk/accessor doğrulaması yapar, bozuk girdide sessizce yanlış veri üretmek yerine `GLTFParseError` fırlatır
  - `DXFExporter` — DXF R12 ASCII, her üçgen bir `3DFACE` varlığı
  - `DWGExporter`, `FBXExporter`, `USDExporter.export` — kapalı/karmaşık formatlar (DWG, FBX, binary USD/USDZ) için açık `UnsupportedFormatError`; `USDExporter.export_usda` ile bağımlılıksız ASCII USD alternatifi
- **`vector_2d.py`** — 2D/rapor exporterları:
  - `SVGCanvas` — bağımlılıksız SVG oluşturucu (line/polygon/polyline/circle/text)
  - `FloorPlanSVGExporter` — oda listesinden (`.polygon`/`.name` duck-typing) 2D kat planı SVG'si (roadmap: "2D Kat Planları")
  - `PNGExporter.export_footprint` — Pillow varsa üstten-görünüm rasterizasyonu; yoksa `UnsupportedFormatError`
  - `PDFExporter.export_text_report` — reportlab varsa zengin PDF; yoksa bağımlılıksız minimal PDF yazıcı (xref tablosu dahil, her zaman çalışır)
- **`reports.py`** — Rapor exporterları:
  - `JSONReportExporter`, `CSVReportExporter`, `XMLReportExporter`, `MarkdownReportExporter`
  - `ReportSection` / `ReportBuilder` — tek veri modelinden JSON/XML/Markdown/CSV/PDF'in hepsini üretir

## Roadmap V3 — Faz D7: 3D Tiles + IFC (BIM) — ✅ Tamamlandı

- **`tiles_3d.py`** — `Tiles3DExporter.export(scene, out_dir)`: bir Faz 15
  `Scene`'i (`render_engine.scene_bridge`) OGC 3D Tiles 1.0 olarak yazar —
  her `SceneNode` bağımsız bir `.b3dm` (Batched 3D Model) tile'ı, kök
  `tileset.json` bunları tek seviyeli bir listede toplar (world-space AABB
  `union()`'ı ile hesaplanan kök `boundingVolume.box`). `.b3dm` gövdesi,
  `GLTFExporter._build_buffers` yeniden kullanılarak üretilen geçerli bir
  GLB'dir — `GLTFImporter.import_glb` ile round-trip test edilir. Şema,
  `validate_tileset()` ile (asset.version, boundingVolume.box uzunluğu,
  refine değeri, her child'ın content.uri'si) kendi kendine doğrulanır;
  `read_b3dm_header()` üretilen `.b3dm` header'ının (magic/byteLength)
  dosya boyutuyla tutarlılığını kontrol eder.
- **`ifc_export.py`** — `IFCExporter.export(model, path)`: minimal IFC4
  STEP (ISO 10303-21) yazıcı. `IFCBuildingModel` (framework-bağımsız basit
  veri modeli: `IFCRoom`/`IFCWall`, `core_engine.geometry_engine.Polygon`
  tabanlı) girdi alır; `IfcProject → IfcSite → IfcBuilding →
  IfcBuildingStorey → (IfcSpace | IfcWallStandardCase)` hiyerarşisini,
  taban poligonundan dikey ekstrüzyonla (`IfcExtrudedAreaSolid`) üretir.
  IFC'nin sıkıştırılmış GUID formatı stdlib `uuid`+özel taban-64 kodlayıcı
  ile üretilir (`_ifc_guid`, 22 karakter, çakışmasız). `validate_step()`
  header/footer varlığını ve **her `#id` referansının tanımlı bir
  entity'ye işaret ettiğini** (dangling reference kontrolü) doğrular —
  sessizce bozuk/açılamayan bir dosya üretmek yerine.
- **Kapsam dışı (bilinçli):** `FBXExporter`/`USDExporter.export`'takiyle
  aynı ilke — malzeme katmanları (`IfcMaterialLayerSet`), IFC2x3, property
  set şemaları (`Pset_*`) ve quantity take-off tam IFC şeması gerektirir;
  roadmap D7'nin "minimal alt küme" hedefiyle kapsam dışı bırakılmıştır.

**Kabul kriteri testi:**
[`tests/test_phaseD7_3dtiles_ifc_export.py`](../tests/test_phaseD7_3dtiles_ifc_export.py)
— 16 yeni test: `tileset.json` şema doğrulaması + çoklu-tile kök
bounding-box birleşimi + `.b3dm` header/GLB round-trip (3D Tiles), IFC
STEP söz dizimi doğrulaması + varlık hiyerarşisi + GUID benzersizliği +
dangling-reference reddi (IFC). Mevcut 494 testin tamamı hâlâ geçiyor,
hiçbiri kırılmadı (toplam 510).

## ROADMAP_V4 — Faz E5: CityGML + CityJSON (şehir-ölçeği semantik 3D) — ✅ Tamamlandı

- **`cityjson_export.py`** — `CityBuilding`/`CityModel` (D7'nin somut
  sınıflarına sıkı bağımlı olmayan, yalnızca taban poligonu + yükseklik +
  opsiyonel gerçek çatı `Mesh3D` alan format-agnostik veri modeli) +
  `CityJSONExporter.export()`: CityJSON 1.1 (`CityObjects`/`vertices`/
  `geometry`/`semantics`) — LOD1 her zaman (düz ekstrüzyon), LOD2
  opsiyonel (`roof_mesh` verilirse, her mesh üçgeni ayrı `RoofSurface`).
  `validate_structure()` ile temel yapısal doğrulama (tam JSON-şema
  değil, harici bağımlılık gerektirmeden zorunlu alan/tip kontrolü).
- **`citygml_export.py`** — `CityGMLExporter.export()`: aynı `CityModel`'i
  OGC CityGML 2.0 (`bldg:Building`, `gml:Solid`/`CompositeSurface`,
  LOD2'de `bldg:GroundSurface`/`WallSurface`/`RoofSurface` semantik
  `boundedBy` yüzeyleri) olarak, stdlib `xml.etree`-only yazar.
  `validate_well_formed()` ile XML well-formedness + zorunlu namespace
  (`bldg`, `gml`) kontrolü.
- Testler: `tests/test_phaseE5_citygml_cityjson.py` — LOD1/LOD2 yapısal
  doğrulama, hata yolları (geçersiz footprint/yükseklik, bozuk XML,
  eksik namespace), iki format arası yükseklik tutarlılığı — 18 test.

## Tasarım İlkesi

Bağımlılıksız yazılabilen formatlar (OBJ, STL, PLY, GLTF/GLB, DXF, SVG, JSON,
CSV, XML, Markdown, minimal PDF, CityJSON, CityGML) **tam olarak**
uygulanmıştır — stdlib dışında hiçbir paket gerektirmez. Resmi kapalı/karmaşık
SDK gerektiren formatlar (FBX, binary USD/USDZ, DWG) için sessizce bozuk
dosya üretmek yerine açık `UnsupportedFormatError` fırlatılır ve
bağımlılıksız alternatif önerilir.
