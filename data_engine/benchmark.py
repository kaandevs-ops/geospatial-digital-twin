"""
Ölçek Benchmark'ı (Roadmap V2, A10 — Data Engine ölçek)
========================================================

`spatial_index` yapılarının (özellikle `RTree`, `QuadTree`, `Octree`,
`KDTree`) büyük nesne sayılarında (roadmap hedefi: 1M+ nesne) insert/query
performansını ölçmek için bağımlılıksız yardımcı fonksiyonlar.

Bu modül CI'da her PR'da koşan **doğrulama testleri** için değil, tekrar
üretilebilir (deterministik `seed`) benchmark koşuları için tasarlandı.
`tests/test_phase10b_scale_benchmark.py`, burada tanımlı fonksiyonları
küçültülmüş bir N (örn. 20.000–50.000) ile regresyon testi olarak kullanır;
gerçek 1M ölçekli koşum için `run_all(n=1_000_000)` doğrudan çağrılabilir
(bkz. `data_engine/BENCHMARK.md`).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .spatial_index import AABB2D, AABB3D, KDTree, Octree, QuadTree, RTree


@dataclass(frozen=True)
class BenchmarkResult:
    structure: str
    n: int
    build_total_s: float
    build_mean_ms: float
    query_mean_ms: float
    query_p50_ms: float
    query_p99_ms: float
    n_queries: int


def _percentile(sorted_ms: list[float], p: float) -> float:
    if not sorted_ms:
        return 0.0
    idx = min(len(sorted_ms) - 1, int(len(sorted_ms) * p))
    return sorted_ms[idx]


def benchmark_rtree(
    n: int = 20_000,
    n_queries: int = 200,
    seed: int = 42,
    world: float = 10_000.0,
    query_size: float = 50.0,
    max_entries: int = 8,
) -> BenchmarkResult:
    rng = random.Random(seed)
    tree: RTree[int] = RTree(max_entries=max_entries)

    t0 = time.perf_counter()
    for i in range(n):
        x, y = rng.uniform(0, world), rng.uniform(0, world)
        tree.insert(i, AABB2D(x, y, x + 1.0, y + 1.0))
    build_total = time.perf_counter() - t0

    q_times_ms: list[float] = []
    for _ in range(n_queries):
        x = rng.uniform(0, max(0.0, world - query_size))
        y = rng.uniform(0, max(0.0, world - query_size))
        s = time.perf_counter()
        tree.search(AABB2D(x, y, x + query_size, y + query_size))
        q_times_ms.append((time.perf_counter() - s) * 1000.0)
    q_times_ms.sort()

    return BenchmarkResult(
        structure="RTree",
        n=n,
        build_total_s=build_total,
        build_mean_ms=(build_total / n) * 1000.0 if n else 0.0,
        query_mean_ms=sum(q_times_ms) / len(q_times_ms) if q_times_ms else 0.0,
        query_p50_ms=_percentile(q_times_ms, 0.50),
        query_p99_ms=_percentile(q_times_ms, 0.99),
        n_queries=n_queries,
    )


def benchmark_quadtree(
    n: int = 20_000,
    n_queries: int = 200,
    seed: int = 42,
    world: float = 10_000.0,
    query_size: float = 50.0,
    capacity: int = 8,
) -> BenchmarkResult:
    rng = random.Random(seed)
    boundary = AABB2D(0.0, 0.0, world, world)
    tree: QuadTree[int] = QuadTree(boundary, capacity=capacity)

    t0 = time.perf_counter()
    for i in range(n):
        x, y = rng.uniform(0, world), rng.uniform(0, world)
        tree.insert(i, AABB2D(x, y, x + 1.0, y + 1.0))
    build_total = time.perf_counter() - t0

    q_times_ms: list[float] = []
    for _ in range(n_queries):
        x = rng.uniform(0, max(0.0, world - query_size))
        y = rng.uniform(0, max(0.0, world - query_size))
        s = time.perf_counter()
        tree.query(AABB2D(x, y, x + query_size, y + query_size))
        q_times_ms.append((time.perf_counter() - s) * 1000.0)
    q_times_ms.sort()

    return BenchmarkResult(
        structure="QuadTree",
        n=n,
        build_total_s=build_total,
        build_mean_ms=(build_total / n) * 1000.0 if n else 0.0,
        query_mean_ms=sum(q_times_ms) / len(q_times_ms) if q_times_ms else 0.0,
        query_p50_ms=_percentile(q_times_ms, 0.50),
        query_p99_ms=_percentile(q_times_ms, 0.99),
        n_queries=n_queries,
    )


def benchmark_octree(
    n: int = 20_000,
    n_queries: int = 200,
    seed: int = 42,
    world: float = 10_000.0,
    query_size: float = 50.0,
    capacity: int = 8,
) -> BenchmarkResult:
    rng = random.Random(seed)
    boundary = AABB3D(0.0, 0.0, 0.0, world, world, world)
    tree: Octree[int] = Octree(boundary, capacity=capacity)

    t0 = time.perf_counter()
    for i in range(n):
        x, y, z = rng.uniform(0, world), rng.uniform(0, world), rng.uniform(0, world)
        tree.insert(i, AABB3D(x, y, z, x + 1.0, y + 1.0, z + 1.0))
    build_total = time.perf_counter() - t0

    q_times_ms: list[float] = []
    for _ in range(n_queries):
        x = rng.uniform(0, max(0.0, world - query_size))
        y = rng.uniform(0, max(0.0, world - query_size))
        z = rng.uniform(0, max(0.0, world - query_size))
        s = time.perf_counter()
        tree.query(AABB3D(x, y, z, x + query_size, y + query_size, z + query_size))
        q_times_ms.append((time.perf_counter() - s) * 1000.0)
    q_times_ms.sort()

    return BenchmarkResult(
        structure="Octree",
        n=n,
        build_total_s=build_total,
        build_mean_ms=(build_total / n) * 1000.0 if n else 0.0,
        query_mean_ms=sum(q_times_ms) / len(q_times_ms) if q_times_ms else 0.0,
        query_p50_ms=_percentile(q_times_ms, 0.50),
        query_p99_ms=_percentile(q_times_ms, 0.99),
        n_queries=n_queries,
    )


def benchmark_kdtree(
    n: int = 20_000,
    n_queries: int = 200,
    seed: int = 42,
    world: float = 10_000.0,
) -> BenchmarkResult:
    """KDTree statik (bulk-build) bir yapıdır; "insert" yerine tek seferlik
    inşa süresi ölçülür, sorgu olarak `nearest()` (en-yakın-komşu) kullanılır.
    """
    rng = random.Random(seed)
    points = [(rng.uniform(0, world), rng.uniform(0, world)) for _ in range(n)]

    t0 = time.perf_counter()
    tree = KDTree(points)
    build_total = time.perf_counter() - t0

    q_times_ms: list[float] = []
    for _ in range(n_queries):
        target = (rng.uniform(0, world), rng.uniform(0, world))
        s = time.perf_counter()
        tree.nearest(target)
        q_times_ms.append((time.perf_counter() - s) * 1000.0)
    q_times_ms.sort()

    return BenchmarkResult(
        structure="KDTree",
        n=n,
        build_total_s=build_total,
        build_mean_ms=(build_total / n) * 1000.0 if n else 0.0,
        query_mean_ms=sum(q_times_ms) / len(q_times_ms) if q_times_ms else 0.0,
        query_p50_ms=_percentile(q_times_ms, 0.50),
        query_p99_ms=_percentile(q_times_ms, 0.99),
        n_queries=n_queries,
    )


def run_all(n: int = 20_000, n_queries: int = 200, seed: int = 42) -> list[BenchmarkResult]:
    """Dört yapının hepsini aynı `n`/`seed` ile koşturur; sonuçları listeler.

    Gerçek 1M ölçekli manuel koşum için: `run_all(n=1_000_000)`.
    """
    return [
        benchmark_rtree(n=n, n_queries=n_queries, seed=seed),
        benchmark_quadtree(n=n, n_queries=n_queries, seed=seed),
        benchmark_octree(n=n, n_queries=n_queries, seed=seed),
        benchmark_kdtree(n=n, n_queries=n_queries, seed=seed),
    ]


def format_report(results: list[BenchmarkResult]) -> str:
    """Sonuçları okunabilir bir tablo (metin) olarak biçimlendirir."""
    lines = [
        f"{'Yapı':<10} {'n':>10} {'build_s':>10} {'build_ms/op':>12} "
        f"{'q_mean_ms':>10} {'q_p50_ms':>10} {'q_p99_ms':>10}"
    ]
    for r in results:
        lines.append(
            f"{r.structure:<10} {r.n:>10} {r.build_total_s:>10.3f} "
            f"{r.build_mean_ms:>12.4f} {r.query_mean_ms:>10.4f} "
            f"{r.query_p50_ms:>10.4f} {r.query_p99_ms:>10.4f}"
        )
    return "\n".join(lines)
