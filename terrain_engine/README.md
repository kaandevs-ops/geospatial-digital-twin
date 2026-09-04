# terrain_engine — ✅ Uygulandı (Phase 2) + Roadmap V3 Faz D10 ✅

`HeightmapGrid`, `DEMImporter`, `TerrainMeshGenerator`, `AdaptiveTerrain`
(quadtree LOD), `TerrainLOD`, `TerrainChunking`, `TerrainStreaming`.

Kod: [`__init__.py`](./__init__.py) · Testler: [`../tests/test_phase2_3d_world_engine.py`](../tests/test_phase2_3d_world_engine.py)
Teknik spesifikasyon (tasarım referansı): [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)

## Roadmap V3 — Faz D10: Erozyon/Hidroloji Simülasyonu — ✅ Tamamlandı

**Önceki kısıt:** Arazi tamamen "ham" DEM verisinden üretiliyordu — gerçekçi
vadi/tepe aşınması, su birikim yolları yoktu.

**Uygulanan çözüm** (`__init__.py` sonunda, aynı dosyada — roadmap
spesifikasyonunun belirttiği dosya):

- **`ErosionSimulator.thermal_erosion`**: her hücrenin en dik alçalan
  komşusuyla eğim farkı, dinlenme açısını (`talus_angle_deg`) aştığında
  fazla malzemenin bir kısmı komşuya aktarılır (Musgrave et al. 1989
  "thermal weathering" modelinin basitleştirilmiş iteratif versiyonu).
- **`ErosionSimulator.hydraulic_erosion`**: damla-tabanlı (droplet-based)
  simülasyon — her damla bilinear-enterpolasyonlu gradyan yönünde akar,
  hız/su-hacmine bağlı taşıma kapasitesi (Hjulström-benzeri basitleştirilmiş
  eşik; Beyer 2015 uygulamasıyla aynı prensip) aşıldığında sediment
  bırakır, altında kaldığında zeminden malzeme alır; su her adımda
  buharlaşır. Deterministik LCG seed — aynı seed her zaman aynı sonucu
  üretir (regresyon testine uygun).
- **`ErosionSimulator.simulate`**: hidrolik (büyük ölçekli vadi oyma) +
  thermal (keskin kenar yumuşatma) birleşik pipeline.
- **`FlowAccumulation`**: D8 tekil-akış-yönü algoritması (O'Callaghan &
  Mark, 1984) — her hücrenin en dik alçalan komşusu akış yönü olarak
  seçilir, hücreler yükseklik azalan sırada işlenerek akümülasyon
  hesaplanır (`analysis_engine.environmental_sim`/D20 ile paylaşılabilir
  ortak ara veri yapısı).

**Kabul kriteri:** Tek-tepe (koni) sentetik yükseklik haritasında, tam
erozyon pipeline'ı (`simulate`) sonrası (1) yükseklik varyansı ölçülebilir
şekilde azalıyor, (2) tepe zirvesine yakın eşiğin altındaki hücre sayısı
artıyor (tepe alanı erozyonla küçülüp yayılıyor) — bkz.
[`tests/test_phaseD10_erosion_hydrology.py`](../tests/test_phaseD10_erosion_hydrology.py)
(`TestErosionAcceptanceCriterion`). 18 yeni test (thermal/hydraulic/
pipeline/flow-accumulation), tamamı yeşil; mevcut testlerin hiçbiri
kırılmadı.

---

## ROADMAP_V4 — Faz E3: Hidroloji Görselleştirmesi ve Gerçek DEM Entegrasyonu ✅ TAMAMLANDI

**Kapanmadan önceki durum:** D10'un `FlowAccumulation` çıktısı (nehir/dere
ağı) yalnızca sayısal bir grid olarak kalıyordu — `render_engine`/viewer'a
hiç bağlanmamıştı; ayrıca D14'ün gerçek DEFLATE-GeoTIFF okuyucusuyla
üretilen gerçek yükseklik verisinin uçtan uca `TerrainMeshGenerator`'a
beslendiği bir entegrasyon testi yoktu.

**Uygulanan çözüm:**

- Bu modülde (`terrain_engine/__init__.py`) kod değişikliği **yok** —
  `FlowAccumulation`/`ErosionSimulator`/`TerrainMeshGenerator` zaten D10/
  temel fazlardan beri doğru çalışıyordu; eksik olan tek şey bu çıktıların
  **başka bir modüle (render_engine) bağlanmasıydı**.
- Köprü `render_engine/scene_bridge.py`'ye eklendi:
  `Scene.attach_flow_network(node, grid, accumulation_threshold=4.0)` —
  `FlowAccumulation.flow_directions()`/`accumulate()`'i çağırıp,
  `TerrainMeshGenerator.generate()` ile **birebir aynı** dünya-uzayı
  dönüşümünü (x=col*resolution_m, y=row*resolution_m, z=elevation)
  kullanarak world-space çizgi-segmentleri üretir ve `Scene.flow_lines`'e
  yazar (`to_dict()` üzerinden viewer'a JSON olarak taşınır — bkz.
  `render_engine/README.md`).
- Uçtan uca entegrasyon testi eklendi:
  [`tests/test_phaseE3_terrain_hydrology.py`](../tests/test_phaseE3_terrain_hydrology.py) —
  D14'ün minimal-ama-gerçek DEFLATE-GeoTIFF yazıcısını yeniden kullanarak
  bilinen bir "V" vadi profilli gerçek bir GeoTIFF üretir, `HeightmapParser.
  parse_file()` ile okur, `TerrainMeshGenerator`/`ErosionSimulator`/
  `FlowAccumulation`/`Scene.attach_flow_network()` zincirinin tamamından
  geçirir. 8 test, tamamı yeşil; mevcut sentetik-DEM testleri (D10, D14)
  değişmeden geçmeye devam ediyor.

**Kabul kriteri (roadmap'in kendi ölçütü):** Gerçek/gerçekçi bir DEM
dosyası uçtan uca işlenip viewer'da görüntülenebilir bir arazi mesh'i +
üzerinde işaretli akış ağı üretir — `test_attach_flow_network_produces_
world_space_segments_matching_mesh` bunu sayısal olarak (segment uçlarının
mesh vertex'leriyle piksel-hassasiyetinde örtüşmesi) doğrular; regresyon
testleri mevcut sentetik-DEM davranışını bozmadığını teyit eder.

**Dokunulan dosyalar:** `render_engine/scene_bridge.py` (yeni
`attach_flow_network()`, `flow_lines` alanı, schema `1.2`→`1.3`),
`tests/test_phaseE3_terrain_hydrology.py` (yeni), bu README, `ROADMAP_V4.md`.
