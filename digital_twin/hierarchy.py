"""
Digital Twin - Hierarchy & Sensor Time-Series
==============================================

ROADMAP_V3 - Faz D11 (Digital Twin: Gerçek IoT Akış Modeli + Twin Hiyerarşisi).

Bu modül, Phase 5 `DigitalTwin`/`DigitalTwinRegistry`'nin iki eksiğini kapatır:

1. **Twin hiyerarşisi** (`TwinHierarchy`): twin'ler arası parent/child
   ilişkisi (örn. mahalle -> blok -> bina) ve üst seviyeden yapılan
   agregasyon sorguları (bir bloktaki tüm binaların toplam enerji
   tüketimi gibi). Sorgu performansı, değişmeyen alt-ağaçların önbelleğe
   alınmış (memoized) sonuçlarını yeniden kullanarak, yalnızca değişen
   yaprak-den-köke yol kadar (ağaç dengeliyse O(log n)) iş yapar - her
   sorguda tüm yaprakların taranmasına gerek kalmaz.

2. **Gerçekçi sensör zaman-serisi üretimi** (`generate_sensor_timeseries`):
   tamamen rastgele değerler yerine, literatürde bilinen basit bir model -
   günlük periyodiklik (sinüzoidal) + mevsimsel periyodiklik (sinüzoidal,
   daha uzun periyot) + Gauss gürültüsü. Bu, "IoT akışı yalnızca simülasyon"
   kısıtını, adı konmuş ve tekrarlanabilir (seed'li) bir modelle değiştirir.

Bağımlılık: yalnızca stdlib (`math`, `random`, `collections`).
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import DigitalTwin, DigitalTwinRegistry

# ========================================================================== #
# TwinHierarchy
# ========================================================================== #


class TwinHierarchy:
    """Twin'ler arası parent/child ilişkisini tutan hafif ağaç yapısı.

    Not: bu sınıf `DigitalTwin` nesnelerinin kendisini değil, yalnızca
    id'ler arası ilişkiyi tutar - `DigitalTwinRegistry` değiştirilmeden
    (roadmap ilkesi: mevcut modüllere dokunmadan üzerine inşa etme) ayrı
    bir katman olarak çalışır. Bir `twin_id`, registry'de kayıtlı olmak
    zorunda değildir (ör. saf organizasyonel bir "mahalle" düğümü hiçbir
    zaman bir `DigitalTwin` olarak var olmayabilir); `aggregate()` yaprak
    düğümlerde registry'den okuma yapar, iç düğümlerde yalnızca alt
    toplamları birleştirir.
    """

    def __init__(self) -> None:
        self._parent: dict = {}
        self._children: dict = defaultdict(set)
        # (cache_key, twin_id) -> agregasyon sonucu
        self._cache: dict = {}
        # bir twin_id "kirli" (yeniden hesaplanmalı) işaretliyse burada
        self._dirty: set = set()

    # -- yapı ----------------------------------------------------------- #

    def add(self, twin_id: str, parent_id: str | None = None) -> None:
        """Bir düğümü hiyerarşiye ekler (yoksa) ve varsa parent'a bağlar."""
        if twin_id not in self._parent:
            self._parent[twin_id] = None
        if parent_id is not None:
            self.set_parent(twin_id, parent_id)
        else:
            self._mark_dirty(twin_id)

    def set_parent(self, child_id: str, parent_id: str) -> None:
        old_parent = self._parent.get(child_id)
        if old_parent is not None and old_parent in self._children:
            self._children[old_parent].discard(child_id)
            self._mark_dirty(old_parent)
        self._parent[child_id] = parent_id
        self._children[parent_id].add(child_id)
        if parent_id not in self._parent:
            self._parent[parent_id] = None
        self._mark_dirty(child_id)

    def parent_of(self, twin_id: str) -> str | None:
        return self._parent.get(twin_id)

    def children_of(self, twin_id: str) -> list:
        return sorted(self._children.get(twin_id, ()))

    def is_leaf(self, twin_id: str) -> bool:
        return len(self._children.get(twin_id, ())) == 0

    def roots(self) -> list:
        return sorted(tid for tid, p in self._parent.items() if p is None)

    def descendants(self, twin_id: str) -> list:
        """Bir düğümün tüm alt-ağacındaki (kendisi hariç) id listesi."""
        result = []
        stack = list(self.children_of(twin_id))
        while stack:
            node = stack.pop()
            result.append(node)
            stack.extend(self.children_of(node))
        return result

    def depth_of(self, twin_id: str) -> int:
        depth = 0
        node = self._parent.get(twin_id)
        while node is not None:
            depth += 1
            node = self._parent.get(node)
        return depth

    def ancestors(self, twin_id: str) -> list:
        """Kökten en yakına doğru değil, yapraktan köke sıralı ata listesi."""
        result = []
        node = self._parent.get(twin_id)
        while node is not None:
            result.append(node)
            node = self._parent.get(node)
        return result

    # -- kirlilik/önbellek ------------------------------------------------ #

    def _mark_dirty(self, twin_id: str) -> None:
        """`twin_id`'yi ve tüm atalarını (köke kadar) kirli işaretler.

        Bu, bir yaprak değiştiğinde çağrılır: yalnızca değişen dalın
        kök'e giden yolu (ağaç dengeliyse O(log n) düğüm) kirli olur,
        kardeş alt-ağaçlar dokunulmamış (temiz/önbellekten okunabilir)
        kalır.
        """
        node: str | None = twin_id
        seen = set()
        while node is not None and node not in seen:
            seen.add(node)
            self._dirty.add(node)
            node = self._parent.get(node)

    def invalidate(self, twin_id: str) -> None:
        """Bir yaprağın (veya herhangi bir düğümün) altındaki verinin
        registry tarafında değiştiğini bildirir - agregasyon önbelleğini
        o düğümden köke kadar geçersiz kılar. Sensör değeri güncellendiğinde
        veya twin kaydedildiğinde çağrılmalıdır."""
        self._mark_dirty(twin_id)

    def clear_cache(self) -> None:
        self._cache.clear()
        self._dirty.clear()

    # -- agregasyon --------------------------------------------------------- #

    def aggregate(
        self,
        registry: DigitalTwinRegistry,
        twin_id: str,
        metric_fn: Callable[[DigitalTwin | None], float],
        reduce_fn: Callable[[list], float] = sum,
        cache_key: str = "default",
    ) -> float:
        """`twin_id` alt-ağacı için `metric_fn`'in `reduce_fn` ile
        birleştirilmiş sonucunu döndürür.

        Yaprak düğümlerde `metric_fn(registry.get(twin_id))` çağrılır
        (twin registry'de yoksa `metric_fn(None)` - çağıran 0.0 gibi bir
        varsayılan döndürmelidir). İç düğümlerde çocukların agregasyonları
        `reduce_fn` ile birleştirilir.

        Performans: yalnızca `_dirty` kümesindeki düğümler yeniden
        hesaplanır; temiz alt-ağaçlar önbellekten O(1) okunur. Bir yaprak
        değiştikten sonra yapılan sorgu, o yapraktan köke kadar olan yol
        kadar (dengeli ağaçta O(log n)) iş yapar - toplam yaprak sayısından
        bağımsız.
        """
        key = (cache_key, twin_id)
        if twin_id not in self._dirty and key in self._cache:
            return self._cache[key]

        children = self.children_of(twin_id)
        if children:
            values = [
                self.aggregate(registry, c, metric_fn, reduce_fn, cache_key) for c in children
            ]
            result = reduce_fn(values)
        else:
            twin = registry.get(twin_id) if registry.exists(twin_id) else None
            result = metric_fn(twin)

        self._cache[key] = result
        self._dirty.discard(twin_id)
        return result

    def aggregate_leaf_sum(
        self,
        registry: DigitalTwinRegistry,
        twin_id: str,
        metric_fn: Callable[[DigitalTwin | None], float],
        cache_key: str = "default",
    ) -> float:
        """`aggregate()` için kısayol: toplam (sum) reduce fonksiyonu."""
        return self.aggregate(registry, twin_id, metric_fn, sum, cache_key)

    # -- doğrulama yardımcı: direkt yaprak toplamı (test/regresyon için) --- #

    def direct_leaf_sum(
        self,
        registry: DigitalTwinRegistry,
        twin_id: str,
        metric_fn: Callable[[DigitalTwin | None], float],
    ) -> float:
        """Önbellek kullanmadan, tüm yaprakları doğrudan tarayarak toplam
        hesaplar. `aggregate()` sonucunun doğruluğunu test etmek için
        referans olarak kullanılır (kasıtlı olarak O(n))."""
        leaves = [d for d in ([twin_id] + self.descendants(twin_id)) if self.is_leaf(d)]
        total = 0.0
        for leaf in leaves:
            twin = registry.get(leaf) if registry.exists(leaf) else None
            total += metric_fn(twin)
        return total

    # -- serialization ------------------------------------------------------- #

    def to_dict(self) -> dict:
        return {"parent": dict(self._parent)}

    @staticmethod
    def from_dict(data: dict) -> TwinHierarchy:
        h = TwinHierarchy()
        for child_id, parent_id in data.get("parent", {}).items():
            h.add(child_id, parent_id)
        return h


# ========================================================================== #
# Sensör zaman-serisi üretici
# ========================================================================== #


@dataclass(slots=True)
class SensorSeriesConfig:
    """`generate_sensor_timeseries` parametre kümesi (tekrar kullanılabilir
    profil olarak - örn. 'sıcaklık sensörü' vs 'enerji sayacı')."""

    base_value: float = 20.0
    daily_amplitude: float = 5.0
    seasonal_amplitude: float = 8.0
    noise_std: float = 0.5
    interval_seconds: float = 3600.0
    seed: int = 0
    day_seconds: float = 86400.0
    year_seconds: float = 365.25 * 86400.0
    min_value: float | None = None
    max_value: float | None = None


def generate_sensor_timeseries(
    start_timestamp: float,
    count: int,
    config: SensorSeriesConfig | None = None,
    **overrides: Any,
) -> list:
    """Gerçekçi bir sensör zaman serisi üretir.

    Model: ``value(t) = base + daily_amplitude * sin(2*pi*t_of_day/day) +
    seasonal_amplitude * sin(2*pi*t_of_year/year) + N(0, noise_std)``

    Tamamen rastgele değerler yerine adı konmuş, deterministik (seed'li)
    ve tekrarlanabilir bir model - literatürde bina enerji/iklim
    sensörlerinde yaygın kullanılan günlük+mevsimsel sinüzoidal + gürültü
    yaklaşımının basit bir versiyonu.

    Returns:
        list[tuple[float, float]] - (timestamp, value) çiftleri, `count`
        eleman, `interval_seconds` aralıklarla.
    """
    cfg = config or SensorSeriesConfig()
    if overrides:
        import dataclasses

        current = {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)}
        cfg = SensorSeriesConfig(**{**current, **overrides})

    rng = random.Random(cfg.seed)
    series = []
    for i in range(count):
        t = start_timestamp + i * cfg.interval_seconds
        daily = cfg.daily_amplitude * math.sin(
            2 * math.pi * (t % cfg.day_seconds) / cfg.day_seconds
        )
        seasonal = cfg.seasonal_amplitude * math.sin(
            2 * math.pi * (t % cfg.year_seconds) / cfg.year_seconds
        )
        noise = rng.gauss(0.0, cfg.noise_std) if cfg.noise_std > 0 else 0.0
        value = cfg.base_value + daily + seasonal + noise
        if cfg.min_value is not None:
            value = max(cfg.min_value, value)
        if cfg.max_value is not None:
            value = min(cfg.max_value, value)
        series.append((t, value))
    return series


def apply_timeseries_to_sensor(
    twin: DigitalTwin,
    sensor_id: str,
    series: list,
    log_each_update: bool = False,
) -> int:
    """Üretilen bir zaman serisini bir `SensorBinding`'e sırayla uygular.

    `log_each_update=False` (varsayılan) iken yalnızca son değer
    `SensorBinding.update()` ile uygulanır ve tek bir toplu `TwinEvent`
    (`sensor_timeseries_applied`) loglanır - `count` uzunluğunda bir seri
    için `count` adet ayrı history kaydı oluşturup event log'unu şişirmemek
    için. `log_each_update=True` verilirse (küçük seriler için) her nokta
    ayrı ayrı `update_sensor()` ile (tam history ile) uygulanır.

    Returns:
        Uygulanan nokta sayısı.
    """
    if not series:
        return 0

    if log_each_update:
        applied = 0
        for ts, value in series:
            if twin.update_sensor(sensor_id, value, ts):
                applied += 1
        return applied

    last_ts, last_value = series[-1]
    target = None
    for s in twin.sensors:
        if s.sensor_id == sensor_id:
            target = s
            break
    if target is None:
        return 0

    target.update(last_value, last_ts)
    twin.log_event(
        "sensor_timeseries_applied",
        {
            "sensor_id": sensor_id,
            "point_count": len(series),
            "first_ts": series[0][0],
            "last_ts": last_ts,
            "last_value": last_value,
        },
        "sensor",
    )
    return len(series)


__all__ = [
    "TwinHierarchy",
    "SensorSeriesConfig",
    "generate_sensor_timeseries",
    "apply_timeseries_to_sensor",
]
