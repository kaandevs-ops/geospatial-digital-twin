# material_engine — ✅ Uygulandı (Phase 2) + Roadmap V3 Faz D9 ✅

`PBRMaterial`, `TextureLoader` (Pillow, opsiyonel), `MaterialCache`,
`ProceduralMaterials` (cam/beton/tuğla/metal/kompozit/taş/ahşap/endüstriyel).

Kod: [`__init__.py`](./__init__.py) · Testler: [`../tests/test_phase2_3d_world_engine.py`](../tests/test_phase2_3d_world_engine.py)
Teknik spesifikasyon (tasarım referansı): [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)

## Roadmap V3 — Faz D9: Gerçek Texture-Baking — ✅ Tamamlandı

**Önceki kısıt:** PBR malzemeler yalnızca sabit skaler değerler
(albedo/roughness/metallic) taşıyordu; gerçek AO/normal **map** üretimi
(texture-baking pipeline) yoktu — yüzeyler viewer'da düz/tek renk
görünüyordu.

**Uygulanan çözüm** ([`texture_baking.py`](./texture_baking.py)):

- **`AOBaker.bake_texture`**: mesh'in UV uzayını rasterize eder (her texel
  merkezinin hangi UV üçgenine düştüğünü barycentric enterpolasyonla bulur),
  texel'in 3D karşılığından kosinüs-ağırlıklı yarım-küre (`HemisphereSampler`,
  Malley yöntemi) örnekleme ile ambient occlusion hesaplar. Kesişim testi
  Möller-Trumbore (`lighting`/`analysis_engine.visibility` ile aynı
  algoritma, bağımsız kopya); isteğe bağlı `data_engine.spatial_index.BVH`
  ile hızlandırılabilir (`bvh=` parametresi — verilmezse doğrusal tarama,
  geriye uyumlu davranış deseni). `AOBaker.bake_vertex_ao` UV gerektirmeyen
  daha ucuz per-vertex varyant.
- **`NormalMapBaker.bake_texture`**: düşük-poli (örn. Faz D1 QEM LOD) hedefin
  UV uzayından, yüksek-poli kaynağa ±normal yönünde ray-cast ile normal
  detayı aktarır ("bake"); kesişim bulunamazsa düşük-poli'nin kendi
  enterpole normaline düşer (asla boş/sıfır çıktı üretmez).
- **`TextureMap`/`TextureMapCodec`**: stdlib'de PNG/JPEG encoder olmadığı
  için, ham piksel verisi `zlib` (DEFLATE) ile sıkıştırılıp base64'e
  çevrilerek `Scene` JSON'una gömülebilir bağımlılıksız bir format sağlar
  (`encode`/`decode`, boyut uyuşmazlığında `ValueError`).

**Kabul kriteri:** "Küp üstüne çıkıntı" test sahnesinde (`plate_with_bump`
— geniş taban plakası + ortasında yükselen kutu), çıkıntının tabanına
bitişik (crevice/gölgeli) texel'lerin AO değeri, çıkıntıdan uzak açık
düz zemine göre **ölçülebilir şekilde düşük** — bkz.
[`tests/test_phaseD9_texture_baking.py`](../tests/test_phaseD9_texture_baking.py)
(`TestAOBakerTexture::test_crevice_near_bump_base_is_darker_than_open_plate`).
24 yeni test (AO texture/vertex, normal-map bake, codec round-trip,
hemisphere sampler determinizmi, geometri yardımcıları), tamamı yeşil;
mevcut testlerin hiçbiri kırılmadı.
