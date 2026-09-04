"""Roadmap V2 - A10 - Data Engine ölçek: RTree O(log n) düzeltmesi ve
spatial-index yapılarının benchmark regresyon testleri."""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.data_engine.benchmark import (
    benchmark_kdtree,
    benchmark_octree,
    benchmark_quadtree,
    benchmark_rtree,
    format_report,
    run_all,
)
from harita.data_engine.spatial_index import AABB2D, RTree

# ---------------------------------------------------------------------------
# RTree doğruluğu - küçük/orta ölçekte kaba kuvvetle karşılaştırma
# ---------------------------------------------------------------------------


def test_rtree_search_matches_brute_force_at_scale():
    rng = random.Random(7)
    n = 4000
    world = 2000.0
    tree: RTree[int] = RTree(max_entries=8)
    boxes: list[AABB2D] = []
    for i in range(n):
        x, y = rng.uniform(0, world), rng.uniform(0, world)
        b = AABB2D(x, y, x + 1.0, y + 1.0)
        boxes.append(b)
        tree.insert(i, b)

    assert len(tree) == n

    for _ in range(15):
        x, y = rng.uniform(0, world - 100), rng.uniform(0, world - 100)
        qb = AABB2D(x, y, x + 100.0, y + 100.0)
        got = set(tree.search(qb))
        brute = {i for i, b in enumerate(boxes) if b.intersects(qb)}
        assert got == brute


def test_rtree_bounds_consistent_after_many_splits():
    """Her düğümün MBR'i, kendi alt-ağacındaki tüm girişleri kapsamalı
    (parent-pointer tabanlı kısmi güncellemenin tüm ağacı tutarlı
    bıraktığını doğrular - regresyon: eski O(n) `_recompute_all` yerine
    yalnızca kökten-yaprağa yol güncelleniyor).
    """
    rng = random.Random(3)
    tree: RTree[int] = RTree(max_entries=4)
    boxes = []
    for i in range(1500):
        x, y = rng.uniform(0, 500), rng.uniform(0, 500)
        b = AABB2D(x, y, x + 2.0, y + 2.0)
        boxes.append(b)
        tree.insert(i, b)

    def check(node) -> AABB2D:
        assert node.bounds is not None
        for eb, child_or_item in node.entries:
            if node.is_leaf:
                assert node.bounds.contains(eb)
            else:
                child_bounds = check(child_or_item)
                assert eb == child_bounds
                assert node.bounds.contains(eb)
        return node.bounds

    check(tree._root)  # iç tutarlılık kontrolü (whitebox)


def test_rtree_parent_pointers_point_to_correct_parent():
    rng = random.Random(11)
    tree: RTree[int] = RTree(max_entries=4)
    for i in range(800):
        x, y = rng.uniform(0, 300), rng.uniform(0, 300)
        tree.insert(i, AABB2D(x, y, x + 1.0, y + 1.0))

    def check(node, expected_parent) -> None:
        assert node.parent is expected_parent
        if not node.is_leaf:
            for _, child in node.entries:
                check(child, node)

    check(tree._root, None)


# ---------------------------------------------------------------------------
# Benchmark fonksiyonlarının kendisi (sonuç şekli, temel makullük)
# ---------------------------------------------------------------------------


def test_benchmark_rtree_returns_sane_result():
    result = benchmark_rtree(n=2000, n_queries=50, seed=1)
    assert result.structure == "RTree"
    assert result.n == 2000
    assert result.build_total_s >= 0.0
    assert result.query_p99_ms >= result.query_p50_ms >= 0.0


def test_benchmark_quadtree_and_octree_and_kdtree_return_sane_results():
    for fn in (benchmark_quadtree, benchmark_octree, benchmark_kdtree):
        result = fn(n=1000, n_queries=30, seed=2)
        assert result.n == 1000
        assert result.build_total_s >= 0.0
        assert result.query_p99_ms >= 0.0


def test_run_all_and_format_report():
    results = run_all(n=500, n_queries=20, seed=5)
    assert len(results) == 4
    report = format_report(results)
    assert "RTree" in report
    assert "QuadTree" in report
    assert "Octree" in report
    assert "KDTree" in report


# ---------------------------------------------------------------------------
# Kabul kriteri (küçültülmüş regresyon): sorgu p99 < 10ms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bench_fn", [benchmark_rtree, benchmark_quadtree, benchmark_octree, benchmark_kdtree]
)
def test_query_p99_under_10ms_at_moderate_scale(bench_fn):
    """Roadmap kabul kriteri: '1.000.000 nesnelik sahnede insert/query
    <10ms p99'. CI'da her PR'da 1M nesne üretip ölçmek pahalı olduğundan,
    burada 20.000 nesnelik (RTree/QuadTree/Octree) veya eşdeğer (KDTree)
    bir ölçekte regresyon testi çalıştırılır; tam 1M ölçekli manuel koşum
    için bkz. `data_engine/BENCHMARK.md`.
    """
    result = bench_fn(n=20_000, n_queries=150, seed=99)
    assert result.query_p99_ms < 10.0, (
        f"{result.structure}: p99={result.query_p99_ms:.3f}ms >= 10ms eşiği"
    )


def test_rtree_insert_mean_is_fast_after_optimization():
    """A10 optimizasyonu öncesi (`_recompute_all` her insert'te tüm ağacı
    O(n) yeniden hesaplıyordu) 20.000 nesnede ortalama insert süresi
    milisaniyeler mertebesindeydi ve n arttıkça ikinci dereceden (O(n^2))
    büyüyordu. Parent-pointer tabanlı kısmi güncelleme sonrası ortalama
    insert süresi n'den (hemen hemen) bağımsız, alt-milisaniye mertebesinde
    kalmalı.
    """
    result = benchmark_rtree(n=20_000, n_queries=10, seed=7)
    assert result.build_mean_ms < 1.0, (
        f"RTree insert ortalaması beklenenden yavaş: {result.build_mean_ms:.4f} ms/op"
    )
