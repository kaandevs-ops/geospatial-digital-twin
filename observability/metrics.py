"""
MetricsRegistry — Counter / Gauge / Histogram + Prometheus Text Export
=========================================================================

ROADMAP_V4 Faz E15. Projede metrik toplama altyapısı hiç yoktu — bu modül
üç temel metrik türünü (Prometheus'un kendi veri modeliyle bire bir
uyumlu) ve stdlib-only bir Prometheus text-format (`text/plain;
version=0.0.4`) render'ını sağlar. Faz 13 `performance.profiler`
(`CPUProfiler`/`MemoryProfiler`) ile entegredir: `MetricsRegistry.
collect_process_metrics()` bir `MemoryProfiler` anlık görüntüsünü gauge
metriklerine döker.

Etiketler (labels), Prometheus konvansiyonuyla aynı şekilde metrik
adının yanında `{key="value", ...}` olarak render edilir; aynı isim +
farklı etiket kombinasyonu ayrı bir zaman serisi sayılır.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

#: Histogram varsayılan bucket sınırları (saniye) — HTTP gecikme
#: ölçümleri için makul bir aralık (1ms - 10s).
DEFAULT_HISTOGRAM_BUCKETS: tuple[float, ...] = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _label_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


def _render_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    parts = ",".join(f'{k}="{v}"' for k, v in labels)
    return "{" + parts + "}"


@dataclass
class _CounterSeries:
    value: float = 0.0


@dataclass
class _GaugeSeries:
    value: float = 0.0


@dataclass
class _HistogramSeries:
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total_count: int = 0
    total_sum: float = 0.0
    observations: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        self.total_count += 1
        self.total_sum += value
        self.observations.append(value)
        for i, upper_bound in enumerate(self.buckets):
            if value <= upper_bound:
                self.counts[i] += 1


class MetricsRegistry:
    """Counter/Gauge/Histogram tutan, Prometheus text-format export eden
    merkezi metrik kaydı.

    Her metrik adı bir "help" metnine ve tipe (counter/gauge/histogram)
    sahiptir; aynı ad farklı türlerle yeniden tanımlanmaya çalışılırsa
    `ValueError` fırlatılır (Prometheus'un kendi tutarlılık kuralıyla
    aynı).
    """

    def __init__(self) -> None:
        self._help: dict[str, str] = {}
        self._types: dict[str, str] = {}
        self._counters: dict[str, dict[tuple, _CounterSeries]] = {}
        self._gauges: dict[str, dict[tuple, _GaugeSeries]] = {}
        self._histograms: dict[str, dict[tuple, _HistogramSeries]] = {}

    # -- tip kaydı / tutarlılık -------------------------------------------
    def _register(self, name: str, kind: str, help_text: str) -> None:
        existing_kind = self._types.get(name)
        if existing_kind is not None and existing_kind != kind:
            raise ValueError(
                f"'{name}' metriği zaten '{existing_kind}' tipiyle kayıtlı, "
                f"'{kind}' olarak yeniden tanımlanamaz."
            )
        self._types[name] = kind
        if help_text:
            self._help[name] = help_text
        elif name not in self._help:
            self._help[name] = ""

    # -- Counter ------------------------------------------------------------
    def inc_counter(
        self,
        name: str,
        value: float = 1.0,
        *,
        help_text: str = "",
        labels: dict[str, str] | None = None,
    ) -> None:
        if value < 0:
            raise ValueError("Counter değeri negatif olamaz (yalnızca artar).")
        self._register(name, "counter", help_text)
        series_map = self._counters.setdefault(name, {})
        key = _label_key(labels)
        series = series_map.setdefault(key, _CounterSeries())
        series.value += value

    def counter_value(self, name: str, labels: dict[str, str] | None = None) -> float:
        series_map = self._counters.get(name, {})
        series = series_map.get(_label_key(labels))
        return series.value if series else 0.0

    # -- Gauge --------------------------------------------------------------
    def set_gauge(
        self,
        name: str,
        value: float,
        *,
        help_text: str = "",
        labels: dict[str, str] | None = None,
    ) -> None:
        self._register(name, "gauge", help_text)
        series_map = self._gauges.setdefault(name, {})
        key = _label_key(labels)
        series_map.setdefault(key, _GaugeSeries()).value = value

    def gauge_value(self, name: str, labels: dict[str, str] | None = None) -> float:
        series_map = self._gauges.get(name, {})
        series = series_map.get(_label_key(labels))
        return series.value if series else 0.0

    # -- Histogram ------------------------------------------------------------
    def observe_histogram(
        self,
        name: str,
        value: float,
        *,
        help_text: str = "",
        labels: dict[str, str] | None = None,
        buckets: tuple[float, ...] = DEFAULT_HISTOGRAM_BUCKETS,
    ) -> None:
        self._register(name, "histogram", help_text)
        series_map = self._histograms.setdefault(name, {})
        key = _label_key(labels)
        series = series_map.setdefault(key, _HistogramSeries(buckets=buckets))
        series.observe(value)

    def histogram_summary(
        self,
        name: str,
        labels: dict[str, str] | None = None,
    ) -> dict[str, float] | None:
        series_map = self._histograms.get(name, {})
        series = series_map.get(_label_key(labels))
        if series is None or series.total_count == 0:
            return None
        return {
            "count": series.total_count,
            "sum": series.total_sum,
            "mean": series.total_sum / series.total_count,
            "p50": statistics.median(series.observations),
            "p99": (sorted(series.observations)[max(0, int(len(series.observations) * 0.99) - 1)]),
        }

    # -- Faz 13 performance.profiler entegrasyonu ------------------------
    def collect_process_metrics(self, memory_profiler: Any = None) -> None:
        """Bir `performance.profiler.MemoryProfiler` anlık görüntüsünü
        (`current_usage_bytes() -> (current, peak)`) gauge metriklerine
        döker. `memory_profiler=None` ise no-op (opsiyonel entegrasyon)."""
        if memory_profiler is None:
            return
        current_bytes, peak_bytes = memory_profiler.current_usage_bytes()
        self.set_gauge(
            "process_memory_current_bytes",
            float(current_bytes),
            help_text="tracemalloc ile ölçülen anlık Python bellek kullanımı (byte)",
        )
        self.set_gauge(
            "process_memory_peak_bytes",
            float(peak_bytes),
            help_text="tracemalloc ile ölçülen tepe Python bellek kullanımı (byte)",
        )

    # -- Prometheus text-format export -------------------------------------
    def render_prometheus(self) -> str:
        """Prometheus `text/plain; version=0.0.4` formatında tüm metrikleri
        döner (stdlib-only, harici `prometheus_client` bağımlılığı yok)."""
        lines: list[str] = []

        for name, series_map in self._counters.items():
            lines.append(f"# HELP {name} {self._help.get(name, '')}".rstrip())
            lines.append(f"# TYPE {name} counter")
            for label_key, series in series_map.items():
                lines.append(f"{name}{_render_labels(label_key)} {series.value}")

        for name, series_map in self._gauges.items():
            lines.append(f"# HELP {name} {self._help.get(name, '')}".rstrip())
            lines.append(f"# TYPE {name} gauge")
            for label_key, series in series_map.items():
                lines.append(f"{name}{_render_labels(label_key)} {series.value}")

        for name, series_map in self._histograms.items():
            lines.append(f"# HELP {name} {self._help.get(name, '')}".rstrip())
            lines.append(f"# TYPE {name} histogram")
            for label_key, series in series_map.items():
                base_labels = dict(label_key)
                cumulative = 0
                for bound, count in zip(series.buckets, series.counts):
                    cumulative = count  # counts zaten kümülatif tutuluyor (bkz. observe)
                    bucket_labels = {**base_labels, "le": str(bound)}
                    lines.append(
                        f"{name}_bucket{_render_labels(_label_key(bucket_labels))} {cumulative}"
                    )
                inf_labels = {**base_labels, "le": "+Inf"}
                lines.append(
                    f"{name}_bucket{_render_labels(_label_key(inf_labels))} {series.total_count}"
                )
                lines.append(f"{name}_sum{_render_labels(label_key)} {series.total_sum}")
                lines.append(f"{name}_count{_render_labels(label_key)} {series.total_count}")

        return "\n".join(lines) + ("\n" if lines else "")

    def metric_names(self) -> list[str]:
        """Kayıtlı tüm benzersiz metrik adları (kabul-testi doğrulaması için)."""
        return sorted(self._types.keys())
