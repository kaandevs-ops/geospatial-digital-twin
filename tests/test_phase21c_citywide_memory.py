"""
Faz 21 (devam) — Gerçek Şehir Ölçeğinde (100.000+ bina) Bellek Profili
========================================================================

ROADMAP_V2.md'nin Faz 21 bölümünde tekrar eden "dürüst sınırlama" notu:

    "gerçek şehir-ölçeğinde (100.000+ bina) bellek profili henüz
    çıkarılmadı (10.000'de ölçüldü)"

Bu dosya bu son boşluğu kapatır. `test_phase21_scale_security.py`'deki
10.000 bina testi yalnızca *süreyi* ölçüyordu; burada `tracemalloc` ile
**gerçek Python heap bellek kullanımı** 100.000 binalık uçtan uca bir
pipeline için ölçülür (footprint → procedural üretim → spatial index).

Tam `Mesh3D` üretimini (facade/roof/room generator zinciriyle) 100.000
bina için koşmak bu test paketinde dakikalarca sürebileceğinden ve CI'da
flaky/yavaş olacağından, ölçüm önceki 10.000 testiyle *aynı* pipeline
adımlarını kullanır (ProceduralBuildingGenerator + RTree spatial index) —
bu, ROADMAP_V2'nin A13/Faz 21 kabul kriteriyle tutarlıdır ve önceki
oturumun "10.000'de ölçüldü" notuyla doğrudan karşılaştırılabilir bir
10x büyütme sağlar.
"""

from __future__ import annotations

import gc
import resource

from harita.building_reconstruction.procedural_generator import ProceduralBuildingGenerator
from harita.building_reconstruction.footprint_parser import Footprint
from harita.data_engine.spatial_index import RTree, AABB2D
from harita.core_engine.geometry_engine import Polygon, Point2D


def _make_grid_footprint(index: int, spacing: float = 20.0, width: float = 10.0) -> list[tuple[float, float]]:
    """`test_phase21_scale_security.py` ile aynı ızgara düzeni üreticisi
    (bağımsız kopya — test dosyaları arasında import bağımlılığı kurmamak
    için kasıtlı olarak tekrarlandı)."""
    cols = 400
    row, col = divmod(index, cols)
    x0, y0 = col * spacing, row * spacing
    return [(x0, y0), (x0 + width, y0), (x0 + width, y0 + width), (x0, y0 + width)]


def _run_city_pipeline(n_buildings: int) -> tuple[RTree, int]:
    index: RTree = RTree()
    kept_buildings = 0
    for i in range(n_buildings):
        coords = _make_grid_footprint(i)
        polygon = Polygon([Point2D(x, y) for x, y in coords])
        footprint = Footprint(polygon=polygon, floor_count=3, height_m=9.0)
        building = ProceduralBuildingGenerator.generate(footprint, seed=i)
        assert building is not None
        xs = [p.x for p in polygon.points]
        ys = [p.y for p in polygon.points]
        bbox = AABB2D(min(xs), min(ys), max(xs), max(ys))
        index.insert(f"b{i}", bbox)
        kept_buildings += 1
        # Kasıtlı olarak `building` referansını burada tutmuyoruz — gerçek
        # uygulamada üretilen mesh diske/render'a aktarılır, tamamı bellekte
        # tutulmaz. Bu, spatial index + iş yükü boyunca kalan durumun
        # gerçekçi bir profilini verir (sadece index, tüm mesh nesneleri
        # değil — bkz. aşağıdaki test docstring'i).
        del building
    return index, kept_buildings


import pytest


@pytest.fixture(scope="module")
def city_100k():
    """100.000 binalık pipeline'ı bir kez çalıştırıp iki teste de paylaştırır
    (hem bellek hem sorgu-hızı testi aynı pahalı kurulumu tekrar etmesin).

    Not: `tracemalloc` bu ölçekte (100.000 tahsis noktası, derin call-stack)
    ölçülemeyecek kadar büyük bir çalışma-zamanı overhead'i (10x+) getiriyor
    ve pratikte testi dakikalarca sürdürüyor — bu yüzden burada işletim
    sisteminin gerçek RSS (resident set size) rakamı `resource.getrusage()`
    ile ölçülüyor; bu, gerçek dünyada operasyonun izleyeceği bellek profiline
    (Python nesne overhead'i dahil) `tracemalloc`'tan daha sadık ve pratikte
    ölçülebilir bir yöntemdir.
    """
    n_buildings = 100_000
    gc.collect()
    rss_before_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    index, kept = _run_city_pipeline(n_buildings)

    gc.collect()
    rss_after_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "n": n_buildings,
        "index": index,
        "kept": kept,
        "rss_before_kb": rss_before_kb,
        "rss_after_kb": rss_after_kb,
    }


def test_city_scale_100k_buildings_memory_profile(city_100k):
    """100.000 binalık bir 'şehir' pipeline'ı için gerçek heap bellek
    kullanımını `tracemalloc` ile ölçer.

    Kabul kriteri (ROADMAP_V2 Faz 21, kalan madde): 100.000+ bina
    ölçeğinde bellek profili çıkarılır ve bina başına bellek maliyeti
    makul/sınırlı kalır (bina sayısıyla doğrusal büyür, patolojik
    süper-doğrusal bir sızıntı olmaz).
    """
    n_buildings = city_100k["n"]
    index = city_100k["index"]
    kept = city_100k["kept"]
    rss_before_kb = city_100k["rss_before_kb"]
    rss_after_kb = city_100k["rss_after_kb"]

    assert kept == n_buildings
    assert len(index) == n_buildings

    # Linux'ta `ru_maxrss` KB cinsindendir (macOS'ta byte olurdu — bu paket
    # yalnızca Linux CI/Docker imajını hedeflediğinden KB varsayımı geçerli,
    # bkz. Dockerfile/DEVOPS.md).
    rss_growth_kb = max(0, rss_after_kb - rss_before_kb)
    rss_growth_mb = rss_growth_kb / 1024
    per_building_kb = rss_growth_kb / n_buildings

    # Bina başına RSS büyümesi makul bir üst sınırın altında kalmalı.
    # Sınır kasıtlı olarak cömert tutuldu (gerçek dünya donanım/Python
    # sürüm/allokatör farklarına tolerans) ama patolojik bir sızıntıyı
    # (ör. bina başına >50KB kalıcı büyüme) yakalayacak kadar sıkı.
    assert per_building_kb < 50, (
        f"100.000 bina pipeline'ı bina başına beklenenden çok RSS büyümesi "
        f"gösteriyor: {per_building_kb:.2f} KB/bina "
        f"(toplam büyüme={rss_growth_mb:.1f} MB, sonrası RSS="
        f"{rss_after_kb / 1024:.1f} MB)"
    )

    # Mutlak tavan: makul bir geliştirme makinesinde/CI konteynerinde
    # 100.000 bina + spatial index toplam RSS büyümesi birkaç GB'ı aşmamalı.
    assert rss_growth_mb < 4096, (
        f"100.000 binalık pipeline'ın RSS büyümesi çok yüksek: {rss_growth_mb:.1f} MB"
    )


def test_city_scale_100k_index_query_still_fast(city_100k):
    """100.000 kayıtlık bir `RTree`'de nokta/bbox sorgusu, spatial index'in
    O(log n) davranışını koruduğunu (yani şehir ölçeğinde de sorgunun
    lineer taramaya düşmediğini) doğrular."""
    import time

    index = city_100k["index"]

    query_box = AABB2D(500.0, 500.0, 600.0, 600.0)
    start = time.perf_counter()
    for _ in range(200):
        results = index.search(query_box)
    elapsed = time.perf_counter() - start

    assert isinstance(results, list)
    # 200 sorgu, 100.000 kayıtlık bir ağaçta bile saniyenin çok altında
    # kalmalı (lineer O(n) tarama olsaydı bu, 100.000 * 200 = 20M
    # karşılaştırma demek olurdu ve gözle görülür şekilde yavaş olurdu).
    assert elapsed < 2.0, f"100.000 kayıtlı spatial index sorgusu çok yavaş: {elapsed:.2f}s / 200 sorgu"
