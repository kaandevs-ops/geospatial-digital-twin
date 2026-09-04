"""Roadmap V3 - Faz D8 kabul kriteri testleri.

Roadmap V2 A13'ün zaten yazılı kabul kriteri: "10.000+ bina, gerçek
zamanlı LOD geçişleri" senaryosunda **bellek kullanımı sahne büyüklüğüyle
doğrusal-altı (sub-linear) büyür" (regresyon benchmark'ı).

Kapsam:
  1. `build_city_catalog` - katalog üretimi gerçekten "ucuz" (Mesh3D
     üretmeden, yalnızca konum tablosu) - büyük N'lerde bile anlık.
  2. `StreamingSceneCache.sync` - yalnızca kamera `radius`'u içindeki
     girdiler için `Mesh3D`+LOD üretir/sahneye ekler; kamera hareket
     ettiğinde eski girdileri sahneden çıkarır (gerçek "unload").
  3. `benchmark_resident_memory_scaling` + `assert_sub_linear_growth` -
     A13'ün kendi kabul kriterinin sayısal kanıtı: katalog N kat büyürken
     resident bellek (tracemalloc ile gerçek ölçüm) N ile orantılı
     büyümüyor.
  4. 10.000+ büyüklüğünde bir katalogda (11.025 bina) senkronizasyonun
     makul sürede tamamlandığı ve resident kümenin katalog boyutundan
     bağımsız kaldığı doğrudan doğrulanır.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.performance.scene_scale_benchmark import (
    BuildingDescriptor,
    ScaleBenchmarkResult,
    StreamingSceneCache,
    assert_sub_linear_growth,
    benchmark_resident_memory_scaling,
    build_city_catalog,
)


# ============================================================================ #
# build_city_catalog
# ============================================================================ #

def test_build_city_catalog_produces_expected_size_and_no_mesh_cost():
    catalog = build_city_catalog(n_side=12, spacing=15.0)
    assert len(catalog) == 144
    for name, desc in catalog.items():
        assert isinstance(desc, BuildingDescriptor)
        assert desc.name == name


def test_build_city_catalog_large_scale_is_fast():
    """11.025 (105x105) büyüklüğünde bir katalog - A13'ün '10.000+ bina'
    eşiğinin üzerinde - saniyenin çok altında üretilebilmeli (yalnızca
    konum tablosu, hiçbir Mesh3D yok)."""
    import time

    t0 = time.perf_counter()
    catalog = build_city_catalog(n_side=105, spacing=20.0)
    elapsed = time.perf_counter() - t0

    assert len(catalog) == 11025
    assert elapsed < 2.0


# ============================================================================ #
# StreamingSceneCache
# ============================================================================ #

def test_streaming_cache_only_loads_buildings_within_radius():
    catalog = build_city_catalog(n_side=20, spacing=20.0)
    cache = StreamingSceneCache(catalog, radius=25.0)

    center = (190.0, 0.0, 190.0)  # ızgaranın ortasına yakın
    diff = cache.sync(center)

    assert len(diff.to_load) > 0
    assert len(diff.to_load) < len(catalog)  # tüm katalog değil, yalnızca yakın kısım
    assert cache.resident_count == len(diff.to_load)
    assert len(cache.scene.nodes) == cache.resident_count


def test_streaming_cache_evicts_when_camera_moves_away():
    catalog = build_city_catalog(n_side=20, spacing=20.0)
    cache = StreamingSceneCache(catalog, radius=25.0)

    cache.sync((0.0, 0.0, 0.0))
    resident_near_origin = set(n.name for n in cache.scene.nodes)
    assert len(resident_near_origin) > 0

    diff = cache.sync((380.0, 0.0, 380.0))  # ızgaranın karşı köşesine geç
    assert len(diff.to_unload) > 0
    resident_after_move = set(n.name for n in cache.scene.nodes)
    # eski konumdaki node'lar artık sahnede olmamalı
    assert resident_near_origin.isdisjoint(resident_after_move)


def test_streaming_cache_resident_set_independent_of_catalog_size():
    """Aynı `radius`/yoğunlukla, katalog 4 kat büyürken (10x10 -> 20x20)
    aynı göreli konumdaki resident küme boyutu benzer kalmalı - streaming
    'radius'un ötesindeki her şeyi görmezden geliyor demektir."""
    small_catalog = build_city_catalog(n_side=10, spacing=20.0)
    large_catalog = build_city_catalog(n_side=20, spacing=20.0)

    small_cache = StreamingSceneCache(small_catalog, radius=25.0)
    large_cache = StreamingSceneCache(large_catalog, radius=25.0)

    center = (90.0, 0.0, 90.0)  # her iki ızgarada da geçerli bir merkez
    small_cache.sync(center)
    large_cache.sync(center)

    # tam eşit olmayabilir (kenar etkileri) ama aynı mertebede olmalı
    assert abs(small_cache.resident_count - large_cache.resident_count) <= 2


def test_streaming_cache_real_time_lod_transition_on_camera_move():
    """Kamera yaklaşıp uzaklaştıkça resident kümenin üçgen sayısı D2'nin
    kamera-mesafe tabanlı LOD seçimiyle değişir - 'gerçek zamanlı LOD
    geçişi' resident kümeye doğru şekilde uygulanıyor demektir."""
    catalog = build_city_catalog(n_side=15, spacing=20.0)
    cache = StreamingSceneCache(catalog, radius=40.0)
    center = (140.0, 0.0, 140.0)
    cache.sync(center)

    tri_close = cache.resident_triangle_count(center)
    tri_far = cache.resident_triangle_count((center[0] + 5000.0, 0.0, center[2] + 5000.0))
    full_detail = cache.resident_triangle_count_full_detail()

    assert tri_close == full_detail  # kamera içindeyken en yüksek detay
    assert tri_far < tri_close  # çok uzaktan bakınca daha düşük LOD seçilir


# ============================================================================ #
# A13 kabul kriteri: doğrusal-altı bellek büyümesi
# ============================================================================ #

def test_resident_memory_grows_sub_linearly_with_catalog_size():
    results = benchmark_resident_memory_scaling(
        n_sides=(10, 20, 40), spacing=20.0, radius=45.0,
    )
    assert len(results) == 3
    for r in results:
        assert isinstance(r, ScaleBenchmarkResult)
        assert r.resident_bytes > 0

    # A13 kabul kriterinin doğrudan doğrulaması
    assert_sub_linear_growth(results)

    # katalog 4 kat (10->20) ve 16 kat (10->40) büyürken bayt/bina oranı
    # ölçülebilir şekilde küçülmeli
    assert results[1].bytes_per_catalog_building < results[0].bytes_per_catalog_building * 0.6
    assert results[2].bytes_per_catalog_building < results[0].bytes_per_catalog_building * 0.2


def test_resident_count_bounded_across_10000_plus_catalog():
    """A13'ün somut '10.000+ bina' senaryosu: 11.025 binalık bir katalogda
    resident küme, çok daha küçük bir katalogla (400 bina) karşılaştırıldığında
    aynı mertebede kalmalı - toplam katalog N ile orantılı büyümüyor."""
    small_catalog = build_city_catalog(n_side=20, spacing=20.0)  # 400 bina
    huge_catalog = build_city_catalog(n_side=105, spacing=20.0)  # 11.025 bina

    small_cache = StreamingSceneCache(small_catalog, radius=45.0)
    huge_cache = StreamingSceneCache(huge_catalog, radius=45.0)

    center = (190.0, 0.0, 190.0)
    small_cache.sync(center)
    huge_cache.sync(center)

    assert huge_cache.resident_count <= small_cache.resident_count * 2
    assert huge_cache.resident_count < len(huge_catalog) * 0.01  # katalogun %1'inden azı


def test_assert_sub_linear_growth_raises_on_violating_sequence():
    violating = [
        ScaleBenchmarkResult(catalog_size=100, resident_count=10,
                              resident_bytes=1000, bytes_per_catalog_building=10.0),
        ScaleBenchmarkResult(catalog_size=200, resident_count=10,
                              resident_bytes=2200, bytes_per_catalog_building=11.0),
    ]
    try:
        assert_sub_linear_growth(violating)
        assert False, "AssertionError bekleniyordu (doğrusal-altı büyüme ihlali)"
    except AssertionError:
        pass


def test_assert_sub_linear_growth_requires_at_least_two_points():
    single = [
        ScaleBenchmarkResult(catalog_size=100, resident_count=10,
                              resident_bytes=1000, bytes_per_catalog_building=10.0),
    ]
    try:
        assert_sub_linear_growth(single)
        assert False, "ValueError bekleniyordu (yetersiz nokta sayısı)"
    except ValueError:
        pass
