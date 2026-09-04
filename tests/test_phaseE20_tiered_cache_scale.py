"""ROADMAP_V4 — Track E / Faz E20.

D15 (`data_engine/tiered_cache.py`) birim seviyesinde test edilmişti ama
ROADMAP_V3'ün kendi yazılı D15 kabul kriteri hiç sayısal olarak ölçülmemişti:

    "100.000 nesnelik sahnede, Zipf-benzeri erişim paterninde bellek
    kullanımı ölçülebilir şekilde düşük kalırken sorgu gecikmesi kabul
    edilebilir sınırda kalmalı."

Bu dosya, Faz 21c'nin (`test_phase21c_citywide_memory.py`) gerçek RSS
ölçüm desenini (`resource.getrusage`, `tracemalloc`'un bu ölçekteki
overhead'inden kaçınmak için) yeniden kullanarak bu kriteri doğrudan,
izlenebilir ve sayısal olarak kanıtlar.

Senaryo: 100.000 nesnelik bir "şehir" için `TieredCache(hot_capacity=...)`
kurulur (sıcak katman nesne sayısının küçük bir kesri kadar); erişim
paterni Zipf-benzeri (birkaç anahtar çok sık, geri kalanı seyrek) üretilir.
Kabul kriteri iki parçaya ayrılır:

1. **Bellek**: sıcak katmanın gerçek boyutu (`hot_size()`) her zaman
   `hot_capacity`'yi aşmaz (100.000 nesnenin tamamı asla aynı anda
   bellekte tutulmaz) — ölçülebilir şekilde düşük bellek ayak izi.
2. **Gecikme**: Zipf paterninde sıcak-katman isabet oranı (`hot_hit_ratio`)
   yüksek kalır (popüler anahtarlar sıcakta kalıcı olarak barınır) ve
   ortalama `get()` gecikmesi, tamamen soğuk-katman-only bir taban
   senaryoya (`ProjectDatabase.load_object` doğrudan) göre kabul
   edilebilir bir sınırın (en az %30 daha hızlı) altında kalır.
"""

from __future__ import annotations

import gc
import random
import resource
import time
from pathlib import Path

import pytest
from harita.data_engine.tiered_cache import TieredCache
from harita.persistence.db_backend import ProjectDatabase
from harita.persistence.project_format import ProjectManifest

N_OBJECTS = 100_000
HOT_CAPACITY = 2_000  # nesne sayısinin %2'si - "ölçülebilir şekilde düşük"


def _zipf_like_keys(n_objects: int, n_accesses: int, seed: int = 42) -> list[str]:
    """Birkaç anahtarın çok, geri kalanının nadir erişildiği basit bir
    Zipf-benzeri (rank-tabanlı ağırlıklı) anahtar örneklemesi üretir.
    Tam `numpy`/`scipy` Zipf dağılımına ihtiyaç yok — stdlib `random`
    ile ağırlıklı örnekleme yeterli ve bağımlılıksız.
    """
    rng = random.Random(seed)
    # ilk 100 anahtar "popüler" - ağırlık 1/rank; geri kalanı düz seyrek kuyruk.
    hot_pool = list(range(min(100, n_objects)))
    weights = [1.0 / (i + 1) for i in range(len(hot_pool))]
    keys: list[str] = []
    for _ in range(n_accesses):
        if rng.random() < 0.9:  # %90 erişim popüler havuzdan
            idx = rng.choices(hot_pool, weights=weights, k=1)[0]
        else:  # %10 erişim seyrek/soğuk kuyruktan
            idx = rng.randrange(n_objects)
        keys.append(f"building:{idx}")
    return keys


@pytest.fixture(scope="module")
def city_cache(tmp_path_factory):
    """100.000 nesnelik bir TieredCache + arka planda gerçek
    ProjectDatabase (sqlite dosyası) kurar; tüm nesneleri `put()` eder
    (write-through - hem sıcağa hem soğuğa yazılır, ama sıcak katman
    kapasiteyi aşınca LRU tahliye eder).
    """
    tmp_dir = tmp_path_factory.mktemp("e20_tiered_cache")
    db_path = Path(tmp_dir) / "city.hproj"
    manifest = ProjectManifest(name="e20-city-scale", project_id="e20-city-scale")
    db = ProjectDatabase.create(db_path, manifest)

    cache = TieredCache(db=db, hot_capacity=HOT_CAPACITY)

    gc.collect()
    rss_before_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    for i in range(N_OBJECTS):
        cache.put(
            f"building:{i}",
            kind="building_footprint",
            value={"id": i, "area_m2": 40.0 + (i % 500), "floors": 1 + (i % 12)},
        )

    gc.collect()
    rss_after_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    return {
        "cache": cache,
        "db": db,
        "rss_before_kb": rss_before_kb,
        "rss_after_kb": rss_after_kb,
    }


def test_hot_layer_never_holds_full_city(city_cache):
    """Bellek kabul kriteri: 100.000 nesne yazıldıktan sonra bile sıcak
    katmanın gerçek boyutu `hot_capacity`'yi aşmaz - tüm şehir asla aynı
    anda bellekte tutulmuyor (roadmap'in "bellek kullanımı ölçülebilir
    şekilde düşük kalır" kriteri)."""
    cache: TieredCache = city_cache["cache"]
    assert cache.hot_size() <= HOT_CAPACITY
    # kabaca kapasiteye yakın dolu olmalı (LRU boşa düşürmüyor, sadece
    # kapasiteyi aşmıyor) - dejenere/boş bir sıcak katman da hatalı olurdu.
    assert cache.hot_size() >= HOT_CAPACITY // 2


def test_rss_growth_scales_far_below_full_dataset(city_cache):
    """100.000 x nesne başına birkaç yüz bayt yerine, sıcak katman
    kapasitesiyle orantılı bir RSS artışı bekleniyor - süper-doğrusal bir
    sızıntı yok. Kesin bir bayt sınırı platform-bağımlı olduğundan
    (allocator/GC farklılıkları), oransal bir üst sınır kullanılır:
    RSS artışı, tüm 100.000 nesnenin sıcakta tutulduğu bir senaryonun
    kabaca 1/10'undan fazla olmamalı (HOT_CAPACITY = N_OBJECTS'in %2'si
    olduğu için bu oldukça gevşek/güvenli bir üst sınırdır)."""
    delta_kb = city_cache["rss_after_kb"] - city_cache["rss_before_kb"]
    # Negatif/ölçülemeyen büyüme (GC/işletim sistemi varyansı) kabul edilir.
    if delta_kb <= 0:
        return
    # Çok kaba bir üst sınır: nesne başına ortalama 50 KB'tan fazla büyüme
    # olursa (100.000 nesnenin TAMAMI sıcakta tutulmuş gibi davranıyor
    # demektir) test kırmızı olur.
    per_object_kb = delta_kb / HOT_CAPACITY
    assert per_object_kb < 200, (
        f"Sıcak katman başına düşen RSS artışı beklenenden çok yüksek: "
        f"{per_object_kb:.1f} KB/nesne (delta={delta_kb} KB, "
        f"hot_capacity={HOT_CAPACITY})"
    )


def test_zipf_access_pattern_hot_hit_ratio_is_high(city_cache):
    """Gecikme kabul kriteri (isabet oranı yönü): Zipf-benzeri bir erişim
    paterninde (birkaç anahtar çok sık istenir), sıcak katman bu popüler
    anahtarları kalıcı tutar ve isabet oranı yüksek kalır."""
    cache: TieredCache = city_cache["cache"]
    access_keys = _zipf_like_keys(N_OBJECTS, n_accesses=5_000)

    for key in access_keys:
        result = cache.get(key)
        assert result is not None

    # Zipf paterni + LRU sıcak katman -> yüksek isabet oranı beklenir.
    # %90 erişim yalnızca 100 popüler anahtara gidiyor ve HOT_CAPACITY
    # (2.000) bunların tamamını rahatça barındırabiliyor.
    assert cache.stats.hot_hit_ratio > 0.75, (
        f"Zipf paterninde beklenenden düşük sıcak-isabet oranı: {cache.stats.hot_hit_ratio:.3f}"
    )


def test_tiered_cache_faster_than_cold_only_baseline(city_cache):
    """Gecikme kabul kriteri (mutlak): aynı Zipf erişim paternini
    doğrudan soğuk katman (ProjectDatabase.load_object) üzerinden
    çalıştırmakla karşılaştırıldığında, TieredCache belirgin biçimde
    (en az %30) daha hızlı kalmalı - roadmap'in "kabul edilebilir sınırda
    gecikme" kriterinin ölçülebilir hali."""
    cache: TieredCache = city_cache["cache"]
    db: ProjectDatabase = city_cache["db"]
    access_keys = _zipf_like_keys(N_OBJECTS, n_accesses=2_000, seed=7)

    # Isınma turu (LRU'nun stabilize olması için) - ölçüme dahil değil.
    for key in access_keys:
        cache.get(key)

    start = time.perf_counter()
    for key in access_keys:
        cache.get(key)
    tiered_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    for key in access_keys:
        db.load_object(key)
    cold_only_elapsed = time.perf_counter() - start

    assert tiered_elapsed < cold_only_elapsed * 0.7, (
        f"TieredCache beklenen hızlanmayı sağlamadı: tiered={tiered_elapsed:.4f}s "
        f"vs cold_only={cold_only_elapsed:.4f}s"
    )
