# data_engine — Phase 10: Data Engine

Roadmap: *"Spatial Database"*, *"Object Cache"*, *"Scene Cache"*, *"Undo"*,
*"Redo"*, *"History"*, *"Versioning"*.

Tam teknik spesifikasyon için bkz: [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)
(Phase 10 bölümü). Bu dosya, uygulamanın kendisinin özeti.

## İçerik

| Dosya | Sınıflar | Roadmap karşılığı |
|---|---|---|
| `spatial_index.py` | `AABB2D`, `AABB3D`, `QuadTree`, `Octree`, `KDTree`, `BVH`, `RTree`, `RayHit` | QuadTree, Octree, KDTree, BVH, RTree |
| `cache.py` | `ObjectCache`, `SceneCache` | Object Cache, Scene Cache |
| `history.py` | `History`, `HistoryEntry`, `Versioning`, `VersionSnapshot` | History, Versioning (+ Undo/Redo — Phase 8'den re-export) |

## Tasarım notları

- **QuadTree / Octree**: Phase 1 `core_engine.geometry_engine.PointIndex`'in
  (grid tabanlı basit nokta indeksi) genelleştirilmiş, hiyerarşik
  versiyonlarıdır. Nokta değil, `AABB2D`/`AABB3D` (bounding box) kabul
  ederler — bina ayak izleri, sahne nesneleri, mesh parçaları gibi alan
  kaplayan öğeler için. Sınır çizgisine denk gelen öğeler birden fazla
  alt-düğüme eklenebilir (loose-tree yaklaşımı); `count()`/`query()` bunu
  `id()` bazlı tekilleştirme ile telafi eder.

- **KDTree**: Statik nokta bulutları için medyan-split ile dengeli inşa
  edilen k-boyutlu arama ağacı. `nearest()`/`nearest_k()`/`range_search()`
  destekler; `PointIndex.nearest_search`'ten farklı olarak grid
  yaklaşıklığı değil, kesin (exact) sonuç verir.

- **BVH**: `mesh_engine.Mesh3D` üzerinde üçgen bazlı Bounding Volume
  Hierarchy. Möller-Trumbore ray-triangle kesişim testi + slab-yöntemi
  AABB-ray testi ile çalışır. Phase 6 (`analysis_engine.visibility`) ray
  casting/LOS hesaplarını ve Phase 9 `xray.occlusion_ratio`'yu
  hızlandırmak için kullanılabilir.

- **RTree**: Guttman'ın klasik quadratic-split R-tree'si. `QuadTree`'den
  farkı: öğeler yalnızca yaprak düğümlerde tutulur, iç düğümler MBR
  (minimum bounding rectangle) hiyerarşisi kurar — çok sayıda örtüşen
  dikdörtgen (bina ayak izleri gibi) için tipik olarak daha az düğüm
  ziyaret eder. **(Roadmap V2, A10)** Her düğüm artık bir `parent`
  işaretçisi tutar; bir `insert` sonrası MBR güncellemesi/bölünme yalnızca
  kökten yaprağa giden yol üzerinde yapılır (O(log n) amortize). Önceki
  sürüm her `insert`'te `_recompute_all()` ile **tüm ağacı** yeniden
  hesaplıyordu (O(n)/ekleme, O(n²) toplam) — büyük sahnelerde (10.000+
  nesne) pratikte kullanılamaz hale geliyordu. Detaylar ve ölçüm sonuçları
  için bkz. [`BENCHMARK.md`](BENCHMARK.md).

- **`benchmark.py`** *(Roadmap V2, A10)*: `spatial_index` yapılarının
  (RTree/QuadTree/Octree/KDTree) insert/build ve query sürelerini ölçen
  bağımlılıksız yardımcı fonksiyonlar (`benchmark_rtree`,
  `benchmark_quadtree`, `benchmark_octree`, `benchmark_kdtree`, `run_all`,
  `format_report`). Deterministik `seed` ile tekrar üretilebilir; CI'da
  küçültülmüş ölçekte (20.000 nesne) regresyon testi olarak, manuel olarak
  ise gerçek 1M ölçekte (`run_all(n=1_000_000)`) çalıştırılabilir.

- **ObjectCache / SceneCache**: Phase 1 `tile_engine.MemoryCache` ile aynı
  LRU deseni, genel amaçlı nesnelere (procedural mesh, `DigitalTwin`,
  `NavGraph` ...) uygulanmış hali. `SceneCache`, isimlendirilmiş
  `ObjectCache` "bucket"larını (`mesh`, `digital_twin`, ...) yönetir.

- **History / Versioning**: `editor.commands.UndoRedoStack` (Phase 8)
  üzerine inşa edilir, onu değiştirmez. `History`, stack'teki her
  do/undo/redo olayını zaman damgasıyla kalıcı olarak günlükler (stack'in
  kendi undo/redo yığını silinse/tükense bile günlük kalır — denetim
  amaçlı). `Versioning` ise adım-bazlı değil, **tam-durum** (full-state,
  deep-copy) tabanlı anlık görüntü/geri-yükleme sağlar; büyük nesnelerin
  (`DigitalTwin.to_dict()` gibi) periyodik checkpoint'leri için uygundur.

## Testler

`harita/tests/test_phase10_data_engine.py` — 45 test, tamamı geçiyor.
`harita/tests/test_phase10b_scale_benchmark.py` *(Roadmap V2, A10)* — 11 test:
RTree doğruluğu (kaba kuvvetle karşılaştırma, whitebox MBR/parent-pointer
tutarlılığı) + dört yapının benchmark regresyonu (p99 < 10ms @ 20.000 nesne).

## Örnek kullanım

```python
from harita import RTree, AABB2D, ObjectCache, History, Versioning
from harita.editor.commands import FunctionCommand

# Bina ayak izlerini R-tree'de indeksle
rt = RTree(max_entries=8)
rt.insert("bina_1", AABB2D(0, 0, 20, 15))
rt.insert("bina_2", AABB2D(25, 0, 40, 12))
overlapping = rt.search(AABB2D(0, 0, 45, 15))  # ["bina_1", "bina_2"]

# Ağır mesh üretimini önbelleğe al
mesh_cache: ObjectCache = ObjectCache(capacity=128)
mesh = mesh_cache.get_or_create("bina_1", lambda: build_expensive_mesh("bina_1"))

# Undo/redo + kalıcı denetim günlüğü
history = History()
history.execute(FunctionCommand(do_fn, undo_fn, label="kat_ekle"))
history.undo()
print([e.label for e in history.entries()])  # ["kat_ekle"]

# Tam-durum sürümleme
versioning = Versioning()
versioning.snapshot(digital_twin.to_dict(), label="v1")
old_state = versioning.restore(0)
```
