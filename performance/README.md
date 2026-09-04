# performance

Roadmap Phase 13 — **Uygulandı** (çalışan kod + testler).

Profesyonel seviyeye çıkmak için kritik olan performans katmanı: görev
zamanlama, culling/LOD, streaming ve profiling.

- **`task_scheduler.py`**: `TaskScheduler` — Phase 1 `TileEngine`'in
  `ThreadPoolExecutor` deseninin genelleştirilmiş hali; `heapq` tabanlı
  öncelik kuyruğu + thread pool. `AsyncAssetLoader` — aynı anahtar için
  çakışan yükleme isteklerini birleştiren (de-dup), cache'li asenkron asset
  yükleyici. **Önemli düzeltme**: ilk implementasyonda bir yarış durumu
  (race condition) vardı — çok hızlı biten işlerde `invalidate()` sonrası
  yeniden yükleme eski sonucu döndürebiliyordu. Bu, placeholder `Future`'ın
  iş zamanlanmadan *önce* kilit altında kaydedilmesiyle giderildi (bkz. kod
  içi yorum). Ayrıca `add_done_callback` bir `Future` zaten tamamlanmışsa
  callback'i senkron çalıştırdığı için `threading.Lock` yerine
  `threading.RLock` kullanıldı (aksi halde kendi kendini kilitleyen bir
  deadlock oluşuyordu).
- **`culling.py`**: `FrustumCulling` (açı-tabanlı yaklaşık view-frustum
  testi — 6-plane tam testin daha ucuz ama daha az kesin bir alternatifi,
  sınırlaması kod içi yorumda açıkça belirtilir), `OcclusionCulling`
  (occluder-listesi + ray-cast), `LODManager` (Phase 2 Terrain LOD
  deseninin genellemesi), `SpatialPartitioning` (Phase 10 `Octree`
  üzerine ince cephe).
- **`streaming.py`**: `IncrementalMeshGenerator` (büyük mesh'i sabit
  boyutlu chunk'lar halinde generator olarak üretir), `GeometryStreaming`/
  `SceneStreaming` (Phase 2 Terrain Streaming ile aynı desen — kamera
  konumuna göre yüklenecek/boşaltılacak chunk farkı), `TextureAtlas`
  (shelf-packing algoritması ile UV atlas düzeni), `InstancingBatch`/
  `DynamicBatcher` (CPU-tarafı gruplama — gerçek GPU instancing/batching
  çağrısı render backend'ine özeldir).
- **`profiler.py`**: `CPUProfiler` (`time.perf_counter` tabanlı, gerçek
  ölçüm), `MemoryProfiler` (`tracemalloc` tabanlı, gerçek Python-heap
  ölçümü), `GPUProfiler` (gerçek GPU donanımı bu ortamda mevcut olmadığı
  için yazılım-tarafı sayaç: draw call/üçgen sayısı + frame süresi —
  gerçek GPU timestamp query'si render backend'ine özel bir geliştirme
  gerektirir, bu açıkça belirtilir), `AssetDependencyManager` (Kahn
  algoritmasıyla topolojik yükleme sırası + döngü tespiti).

Tüm alt modüller `harita/performance/__init__.py` ve üst seviye
`harita/__init__.py` üzerinden re-export edilir
(`from harita import TaskScheduler, FrustumCulling, ...`).
Testler: `harita/tests/test_phase13_performance.py` (33 test, tamamı
geçiyor — çoklu-thread testler flakiness için 3 kez ardışık çalıştırılıp
doğrulandı).

## Roadmap V3 — Faz D8: Gerçek Streaming/LOD Ölçek Testi — ✅ Tamamlandı

- **`scene_scale_benchmark.py`** — Roadmap V2 A13'ün "10.000+ bina, gerçek
  zamanlı LOD geçişi" kabul kriterinin (bellek kullanımı sahne
  büyüklüğüyle **doğrusal-altı** büyür) doğrudan ölçümü. D1 (gerçek QEM)
  ve D2 (`Scene.add_mesh_with_lod`) tamamlandıktan sonra anlamlı hale
  gelen köprü:
  - `BuildingDescriptor`/`build_city_catalog` — ucuz "katalog" girdileri
    (yalnızca konum/boyut); 11.025 (105×105) büyüklüğünde bir katalog bile
    hiçbir `Mesh3D` üretmeden < 1 saniyede oluşturuluyor.
  - `StreamingSceneCache` — Faz 13 `SceneStreaming` (kamera-mesafe tabanlı
    "desired set") ile Faz D2 `Scene.add_mesh_with_lod` arasında köprü:
    `sync(camera_position)` yalnızca `radius` içindeki katalog girdileri
    için gerçekten `Mesh3D`+LOD zinciri üretip sahneye ekler; kamera
    uzaklaştığında karşılık gelen `SceneNode`'ları sahneden **gerçekten
    çıkarır** (bellekten düşürür) — katalogun tamamı asla aynı anda
    belleğe alınmaz.
  - `benchmark_resident_memory_scaling`/`assert_sub_linear_growth` —
    `tracemalloc` (gerçek Python-heap ölçümü) ile katalog 100→400→1600
    büyürken resident bellek/bina oranının ölçülebilir şekilde küçüldüğünü
    kanıtlıyor (ölçülen: 1488→360→90 B/bina — 16 kat katalog büyümesinde
    resident küme sabit kaldığı için oran ~16 kat küçülüyor).
  - 11.025 binalık bir katalogda resident küme, 400 binalık bir katalogla
    aynı mertebede kalıyor (`< %1` oranında) — A13'ün somut "10.000+ bina"
    senaryosunun kanıtı.

**Kabul kriteri testi:**
[`tests/test_phaseD8_streaming_scale.py`](../tests/test_phaseD8_streaming_scale.py)
— 10 yeni test: katalog üretim maliyeti, radius-sınırlı yükleme, kamera
hareketinde gerçek eviction, katalog boyutundan bağımsız resident küme,
kamera mesafesine göre gerçek zamanlı LOD geçişi, ve `assert_sub_linear_growth`'un
kendisinin ihlal durumunda doğru şekilde hata verdiği dahil. Mevcut 510
test hiç kırılmadı (toplam 520).

---

## ROADMAP_V4 — Faz E10 (Gerçek GPU Donanım Zamanlama Köprüsü)

`GPUProfiler`'a `record_gpu_timing(gpu_time_ms, supported)` ve
`average_gpu_time_ms()` eklendi. `render_engine/viewer/index.html`, WebGL2
`EXT_disjoint_timer_query_webgl2` uzantısını algılıyorsa gerçek GPU
zamanlamasını `beginGpuTimer()/endGpuTimer()/pollGpuTimer()` ile ölçüp
`/api/performance/gpu-timing` REST ucuna raporluyor; uzantı yoksa mevcut
yazılım-tarafı `duration_s` simülasyonuna sessizce düşülüyor (regresyon
yok). `app_shell/api.py::build_app_router(..., gpu_profiler=...)` ile
opsiyonel olarak bağlanır — verilmezse route hiç kayıtlı olmaz (geriye
uyumlu).

### Testler
[`../tests/test_phaseE10_gpu_profiling_bridge.py`](../tests/test_phaseE10_gpu_profiling_bridge.py)
— 8 test, hepsi geçti.
