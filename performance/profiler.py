"""
Performance Engine - Profilers & Asset Dependency Manager
==========================================================

Roadmap Phase 13 - "Memory profiler", "GPU profiler", "CPU profiler",
"Asset dependency manager".

`MemoryProfiler`/`CPUProfiler`: stdlib `tracemalloc`/`time` tabanli, gercek
olcum yapan basit profiler'lar (roadmap dokumaninin talep ettigi gibi).
`GPUProfiler`: gercek bir GPU sürücüsü/API'si (Vulkan/DirectX/Metal) bu
ortamda mevcut olmadigindan, gercek donanim olcumu YAPAMAZ - bunun yerine
bir render backend'in `frame_start()/frame_end()/draw_call()` gibi
cagrilarini enstrumante edebilecek bir **arayuz + yazilim-tarafi sayac**
saglar (draw call sayisi, tahmini frame suresi). Gercek GPU zaman damgasi
(timestamp query) entegrasyonu, secilen render backend'ine ozel bir
gelistirme gerektirir ve bu modulun kapsami disindadir - bu, yorum
satirinda acikca belirtilir.

`AssetDependencyManager`: DAG (yonlu-dongusuz-graf) tabanli asset bagimlilik
takibi + topolojik sirali yukleme sirasi (Kahn algoritmasi) + dongu tespiti.
"""

from __future__ import annotations

import time
import tracemalloc
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


# ============================================================================ #
# CPU / Memory Profiler
# ============================================================================ #

@dataclass(slots=True)
class ProfileSample:
    label: str
    duration_s: float
    extra: dict = field(default_factory=dict)


class CPUProfiler:
    """`time.perf_counter()` tabanli, isimlendirilmis blok sureleri."""

    def __init__(self) -> None:
        self._samples: list[ProfileSample] = []

    @contextmanager
    def measure(self, label: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self._samples.append(ProfileSample(label, time.perf_counter() - start))

    @property
    def samples(self) -> list[ProfileSample]:
        return list(self._samples)

    def total_for(self, label: str) -> float:
        return sum(s.duration_s for s in self._samples if s.label == label)

    def summary(self) -> dict[str, dict[str, float]]:
        totals: dict[str, list[float]] = defaultdict(list)
        for s in self._samples:
            totals[s.label].append(s.duration_s)
        return {
            label: {
                "count": len(durations),
                "total_s": sum(durations),
                "avg_s": sum(durations) / len(durations),
                "max_s": max(durations),
            }
            for label, durations in totals.items()
        }

    def reset(self) -> None:
        self._samples.clear()


class MemoryProfiler:
    """`tracemalloc` tabanli bellek profilleyici - gercek Python-heap
    tahsisatlarini olcer (roadmap'in "Memory profiler" istegi)."""

    def __init__(self) -> None:
        self._started_here = False
        self._snapshots: dict[str, tracemalloc.Snapshot] = {}

    def start(self) -> None:
        if not tracemalloc.is_tracing():
            tracemalloc.start()
            self._started_here = True

    def stop(self) -> None:
        if self._started_here and tracemalloc.is_tracing():
            tracemalloc.stop()
            self._started_here = False

    def snapshot(self, label: str) -> None:
        if not tracemalloc.is_tracing():
            self.start()
        self._snapshots[label] = tracemalloc.take_snapshot()

    def current_usage_bytes(self) -> tuple[int, int]:
        """(current, peak) - `tracemalloc.get_traced_memory()`."""
        if not tracemalloc.is_tracing():
            self.start()
        return tracemalloc.get_traced_memory()

    def diff(self, label_a: str, label_b: str, top_n: int = 5) -> list[str]:
        """İki snapshot arasindaki en buyuk bellek artislarini okunabilir
        satirlar olarak dondurur (`tracemalloc.StatisticDiff`)."""
        if label_a not in self._snapshots or label_b not in self._snapshots:
            raise KeyError("Once her iki etiket icin de snapshot() cagrilmali")
        stats = self._snapshots[label_b].compare_to(self._snapshots[label_a], "lineno")
        return [str(stat) for stat in stats[:top_n]]


# ============================================================================ #
# GPU Profiler (sayac tabanli - bkz. modul docstring'i)
# ============================================================================ #

@dataclass(slots=True)
class FrameStats:
    draw_calls: int = 0
    triangles: int = 0
    duration_s: float = 0.0
    # ROADMAP_V4 - Faz E10: EXT_disjoint_timer_query_webgl2 (veya baska bir
    # gercek GPU zaman damgasi kaynagi) uzerinden gelen gercek donanim
    # olcumu. `gpu_timing_supported=False` iken `duration_s` yazilim-tarafi
    # simulasyonu tek kaynak olarak kullanilir (geriye uyumlu, regresyon yok).
    gpu_time_ms: float | None = None
    gpu_timing_supported: bool = False


class GPUProfiler:
    """Yazilim-tarafi GPU aktivite sayaci (gercek GPU zamanlama degil -
    bkz. modul docstring'i). Bir render backend her `draw_call()`
    cagrisinda ucgen sayisini bildirir; `end_frame()` frame suresini ve
    toplam istatistigi kapatir."""

    def __init__(self) -> None:
        self._current: FrameStats | None = None
        self._frame_start: float = 0.0
        self._history: list[FrameStats] = []

    def begin_frame(self) -> None:
        self._current = FrameStats()
        self._frame_start = time.perf_counter()

    def draw_call(self, triangle_count: int = 0) -> None:
        if self._current is None:
            raise RuntimeError("draw_call() once begin_frame() cagrilmali")
        self._current.draw_calls += 1
        self._current.triangles += triangle_count

    def end_frame(self) -> FrameStats:
        if self._current is None:
            raise RuntimeError("end_frame() once begin_frame() cagrilmali")
        self._current.duration_s = time.perf_counter() - self._frame_start
        finished = self._current
        self._history.append(finished)
        self._current = None
        return finished

    @property
    def history(self) -> list[FrameStats]:
        return list(self._history)

    def average_fps(self, last_n: int | None = None) -> float:
        frames = self._history[-last_n:] if last_n else self._history
        durations = [f.duration_s for f in frames if f.duration_s > 0]
        if not durations:
            return 0.0
        avg_duration = sum(durations) / len(durations)
        return 1.0 / avg_duration if avg_duration > 0 else 0.0

    # ------------------------------------------------------------------ #
    # ROADMAP_V4 - Faz E10: gercek GPU donanim zamanlamasi kopru noktasi
    # ------------------------------------------------------------------ #
    def record_gpu_timing(self, gpu_time_ms: float, supported: bool = True) -> None:
        """Viewer'daki `EXT_disjoint_timer_query_webgl2` uzantisindan (veya
        baska bir gercek GPU zaman damgasi kaynagindan) REST/WebSocket
        uzerinden gelen olcumu son frame'e isler.

        `supported=False` ise (tarayici uzantiyi desteklemiyor) hicbir sey
        yapilmaz ve mevcut yazilim-tarafi `duration_s` simulasyonu tek
        kaynak olarak kalir - regresyon yok, sessiz dusme.
        """
        if not supported or self._current is None:
            return
        self._current.gpu_time_ms = gpu_time_ms
        self._current.gpu_timing_supported = True

    def average_gpu_time_ms(self, last_n: int | None = None) -> float | None:
        """Gercek GPU zamanlamasi raporlanmis frame'lerin ortalamasi.
        Hic frame gercek GPU zamanlamasi icermiyorsa None doner (yazilim
        simulasyonuna sessizce dusulur - caller `duration_s` kullanmaya
        devam eder)."""
        frames = self._history[-last_n:] if last_n else self._history
        timings = [f.gpu_time_ms for f in frames if f.gpu_timing_supported and f.gpu_time_ms is not None]
        if not timings:
            return None
        return sum(timings) / len(timings)


# ============================================================================ #
# Asset Dependency Manager
# ============================================================================ #

class CyclicDependencyError(Exception):
    """Asset bagimlilik grafinde dongu tespit edildiginde firlatilir."""


class AssetDependencyManager:
    """Asset -> bagimliliklari (DAG) takibi. `load_order()` topolojik
    siralama (Kahn algoritmasi) dondurur; bagimliliklar bagimli olan
    asset'ten once yuklenir. Dongu varsa `CyclicDependencyError`."""

    def __init__(self) -> None:
        self._deps: dict[str, set[str]] = defaultdict(set)
        self._all_nodes: set[str] = set()

    def add_asset(self, key: str, depends_on: list[str] | None = None) -> None:
        self._all_nodes.add(key)
        for dep in depends_on or []:
            self._all_nodes.add(dep)
            self._deps[key].add(dep)

    def dependencies_of(self, key: str) -> set[str]:
        return set(self._deps.get(key, set()))

    def load_order(self) -> list[str]:
        """Kahn algoritmasi: once bagimliliklari olmayan (veya hepsi zaten
        siralanmis) dugumler cikartilir. Sonuc, her asset'in tum
        bagimliliklarindan SONRA gorunecegi bir siradir."""
        in_degree: dict[str, int] = {n: 0 for n in self._all_nodes}
        # kenar: dep -> key (dep once yuklenmeli, yani key, dep'e "bagli")
        adjacency: dict[str, list[str]] = defaultdict(list)
        for key, deps in self._deps.items():
            for dep in deps:
                adjacency[dep].append(key)
                in_degree[key] += 1

        queue: deque[str] = deque(sorted(n for n, d in in_degree.items() if d == 0))
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in sorted(adjacency.get(node, [])):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(order) != len(self._all_nodes):
            remaining = self._all_nodes - set(order)
            raise CyclicDependencyError(f"Dongusel asset bagimliligi tespit edildi: {sorted(remaining)}")
        return order
