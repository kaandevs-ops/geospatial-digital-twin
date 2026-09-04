"""ROADMAP_V3 - Faz D11 (Digital Twin: Gerçek IoT Akış Modeli + Twin
Hiyerarşisi) için kabul kriteri testleri.

Kapsam:
    - `TwinHierarchy`: parent/child ilişkisi, agregasyon doğruluğu (kök
      seviyesinden yapılan sorgu, yaprakların doğrudan toplamıyla eşleşir),
      3 seviyeli (mahalle -> blok -> bina) 1000 twin'lik hiyerarşide
      "iş miktarı" davranışı (bir yaprak değiştiğinde yalnızca kök'e giden
      yol kadar yeniden hesaplama yapılır - toplam yaprak sayısından
      bağımsız), invalidation/dirty-tracking doğruluğu.
    - `generate_sensor_timeseries`: günlük+mevsimsel periyodiklik + gürültü
      modeli - determinizm (aynı seed -> aynı sonuç), günlük periyodiklik
      varlığı (FFT gerektirmeden basit tepe-konumu kontrolü), gürültüsüz
      halin periyodik bileşenlerle tutarlılığı.
    - `apply_timeseries_to_sensor`: `SensorBinding`'e uygulama + event log
      şişirmeme davranışı.
"""

import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.digital_twin import DigitalTwin, DigitalTwinRegistry, SensorBinding
from harita.digital_twin.hierarchy import (
    TwinHierarchy,
    SensorSeriesConfig,
    generate_sensor_timeseries,
    apply_timeseries_to_sensor,
)


# ========================================================================== #
# TwinHierarchy - yapı ve temel sorgular
# ========================================================================== #

def test_hierarchy_add_and_parent_child_queries():
    h = TwinHierarchy()
    h.add("neighborhood-1")
    h.add("block-1", parent_id="neighborhood-1")
    h.add("block-2", parent_id="neighborhood-1")
    h.add("bldg-1", parent_id="block-1")
    h.add("bldg-2", parent_id="block-1")
    h.add("bldg-3", parent_id="block-2")

    assert h.parent_of("bldg-1") == "block-1"
    assert h.parent_of("neighborhood-1") is None
    assert set(h.children_of("neighborhood-1")) == {"block-1", "block-2"}
    assert set(h.children_of("block-1")) == {"bldg-1", "bldg-2"}
    assert h.is_leaf("bldg-1")
    assert not h.is_leaf("block-1")
    assert h.roots() == ["neighborhood-1"]
    assert set(h.descendants("neighborhood-1")) == {
        "block-1", "block-2", "bldg-1", "bldg-2", "bldg-3",
    }
    assert h.depth_of("bldg-1") == 2
    assert h.ancestors("bldg-1") == ["block-1", "neighborhood-1"]


def test_hierarchy_reparenting_updates_children_sets():
    h = TwinHierarchy()
    h.add("block-1")
    h.add("block-2")
    h.add("bldg-1", parent_id="block-1")
    assert "bldg-1" in h.children_of("block-1")

    h.set_parent("bldg-1", "block-2")
    assert "bldg-1" not in h.children_of("block-1")
    assert "bldg-1" in h.children_of("block-2")
    assert h.parent_of("bldg-1") == "block-2"


# ========================================================================== #
# Agregasyon doğruluğu
# ========================================================================== #

def _energy_metric(twin):
    if twin is None:
        return 0.0
    return float(twin.metadata.get("energy_kwh", 0.0))


def _build_three_level_hierarchy(registry, n_blocks=4, buildings_per_block=8):
    """3 seviyeli (root -> blok -> bina) bir hiyerarşi + registry kaydı
    oluşturur. Her bina'ya deterministik bir enerji değeri atanır."""
    h = TwinHierarchy()
    root_id = "neighborhood-root"
    h.add(root_id)
    counter = 0
    for b in range(n_blocks):
        block_id = f"block-{b}"
        h.add(block_id, parent_id=root_id)
        for i in range(buildings_per_block):
            bldg_id = f"bldg-{b}-{i}"
            h.add(bldg_id, parent_id=block_id)
            twin = registry.create(bldg_id)
            twin.set_metadata("energy_kwh", float(counter % 17) + 1.0)
            registry.save(twin)
            counter += 1
    return h, root_id


def test_aggregate_matches_direct_leaf_sum():
    registry = DigitalTwinRegistry()
    h, root_id = _build_three_level_hierarchy(registry, n_blocks=5, buildings_per_block=10)

    aggregated = h.aggregate_leaf_sum(registry, root_id, _energy_metric)
    direct = h.direct_leaf_sum(registry, root_id, _energy_metric)

    assert aggregated == direct
    assert aggregated > 0.0

    # Alt seviye (tek blok) için de tutarlı olmalı
    block_agg = h.aggregate_leaf_sum(registry, "block-2", _energy_metric)
    block_direct = h.direct_leaf_sum(registry, "block-2", _energy_metric)
    assert block_agg == block_direct


def test_aggregate_reflects_update_after_invalidate():
    registry = DigitalTwinRegistry()
    h, root_id = _build_three_level_hierarchy(registry, n_blocks=3, buildings_per_block=4)

    before = h.aggregate_leaf_sum(registry, root_id, _energy_metric)

    twin = registry.get("bldg-1-2")
    twin.set_metadata("energy_kwh", twin.metadata["energy_kwh"] + 1000.0)
    registry.save(twin)
    h.invalidate("bldg-1-2")

    after = h.aggregate_leaf_sum(registry, root_id, _energy_metric)
    assert after == before + 1000.0


def test_reduce_fn_can_be_customized_eg_max():
    registry = DigitalTwinRegistry()
    h, root_id = _build_three_level_hierarchy(registry, n_blocks=2, buildings_per_block=5)

    max_energy = h.aggregate(registry, root_id, _energy_metric, reduce_fn=lambda vs: max(vs))
    all_leaves = [_energy_metric(registry.get(leaf))
                  for leaf in h.descendants(root_id) if h.is_leaf(leaf)]
    assert max_energy == max(all_leaves)


# ========================================================================== #
# A5 kabul kriteri: 1000 twin'lik hiyerarşi, O(log n) davranışı
# ========================================================================== #

class _CountingHierarchy(TwinHierarchy):
    """Test amaçlı: `metric_fn` çağrı sayısını sayar (aggregate() üretim
    kodundaki normal recursive davranışına dokunmadan - metric_fn yalnızca
    ağacın en dışındaki çağrıda bir kez sarmalanır, üretim kodu bunu
    recursion boyunca aynı referansla iletir)."""

    def __init__(self):
        super().__init__()
        self.metric_calls = 0

    def aggregate_leaf_sum(self, registry, twin_id, metric_fn, cache_key="default"):
        def counting_metric(twin):
            self.metric_calls += 1
            return metric_fn(twin)
        return self.aggregate(registry, twin_id, counting_metric, sum, cache_key)


def test_1000_twin_hierarchy_query_after_single_leaf_change_is_sublinear():
    """3 seviyeli (mahalle -> blok -> bina), toplam ~1000 bina'lık bir
    hiyerarşide: (1) ilk sorgu tüm yaprakları tarar (ısınma), (2) tek bir
    yaprak değiştikten sonraki sorgu, toplam yaprak sayısından çok daha az
    iş yapar (yalnızca değişen dalın kök'e giden yolu kadar) - roadmap'in
    "O(log n) korunmalı" kabul kriteri."""
    registry = DigitalTwinRegistry()
    n_blocks = 20
    buildings_per_block = 50  # 20 * 50 = 1000 bina
    h = _CountingHierarchy()
    root_id = "neighborhood-root"
    h.add(root_id)
    for b in range(n_blocks):
        block_id = f"block-{b}"
        h.add(block_id, parent_id=root_id)
        for i in range(buildings_per_block):
            bldg_id = f"bldg-{b}-{i}"
            h.add(bldg_id, parent_id=block_id)
            twin = registry.create(bldg_id)
            twin.set_metadata("energy_kwh", 1.0)
            registry.save(twin)

    total_leaves = n_blocks * buildings_per_block
    assert total_leaves == 1000

    # İlk (soğuk) sorgu - tüm yaprakları tarar
    h.aggregate_leaf_sum(registry, root_id, _energy_metric)
    assert h.metric_calls == total_leaves

    # Tek bir yaprağı değiştir + invalidate et
    h.metric_calls = 0
    twin = registry.get("bldg-10-25")
    twin.set_metadata("energy_kwh", 2.0)
    registry.save(twin)
    h.invalidate("bldg-10-25")

    result = h.aggregate_leaf_sum(registry, root_id, _energy_metric)

    # Yalnızca değişen dal metric_fn'i yeniden çağırmalı: kendisi (1) +
    # aynı bloktaki kardeşlerinin cache'ten okunması metric_fn çağırmaz.
    # Beklenen üst sınır: değişen yaprak kadar (>= 1) ama kesinlikle
    # toplam yaprak sayısının çok altında (sub-linear kanıtı).
    assert h.metric_calls >= 1
    assert h.metric_calls <= 5  # tek yaprak + güvenlik payı
    assert h.metric_calls < total_leaves * 0.05

    expected = (total_leaves - 1) * 1.0 + 2.0
    assert result == expected


def test_invalidate_without_query_does_not_recompute_unrelated_branches():
    registry = DigitalTwinRegistry()
    h = _CountingHierarchy()
    h.add("root")
    h.add("block-a", parent_id="root")
    h.add("block-b", parent_id="root")
    for i in range(50):
        h.add(f"a-{i}", parent_id="block-a")
        t = registry.create(f"a-{i}")
        t.set_metadata("energy_kwh", 1.0)
        registry.save(t)
    for i in range(50):
        h.add(f"b-{i}", parent_id="block-b")
        t = registry.create(f"b-{i}")
        t.set_metadata("energy_kwh", 1.0)
        registry.save(t)

    h.aggregate_leaf_sum(registry, "root", _energy_metric)  # ısınma
    h.metric_calls = 0

    # yalnızca block-a altında bir değişiklik
    t = registry.get("a-0")
    t.set_metadata("energy_kwh", 5.0)
    registry.save(t)
    h.invalidate("a-0")

    h.aggregate_leaf_sum(registry, "root", _energy_metric)
    # block-b'nin 50 yaprağı yeniden hesaplanmamalı
    assert h.metric_calls <= 2


# ========================================================================== #
# Sensör zaman-serisi üretimi
# ========================================================================== #

def test_generate_sensor_timeseries_is_deterministic_for_same_seed():
    cfg = SensorSeriesConfig(seed=42, noise_std=1.0)
    s1 = generate_sensor_timeseries(0.0, 200, cfg)
    s2 = generate_sensor_timeseries(0.0, 200, cfg)
    assert s1 == s2


def test_generate_sensor_timeseries_different_seed_differs():
    s1 = generate_sensor_timeseries(0.0, 50, SensorSeriesConfig(seed=1, noise_std=2.0))
    s2 = generate_sensor_timeseries(0.0, 50, SensorSeriesConfig(seed=2, noise_std=2.0))
    values1 = [v for _, v in s1]
    values2 = [v for _, v in s2]
    assert values1 != values2


def test_generate_sensor_timeseries_daily_cycle_peak_and_trough():
    """Gürültüsüz (noise_std=0) bir seride, günlük sinüzoidal bileşenin
    beklenen tepe (t_of_day = day/4) ve çukur (t_of_day = 3*day/4)
    noktalarında beklenen değerlere ulaştığını doğrular."""
    day = 86400.0
    cfg = SensorSeriesConfig(
        base_value=10.0, daily_amplitude=5.0, seasonal_amplitude=0.0,
        noise_std=0.0, interval_seconds=day / 4, seed=0, day_seconds=day,
    )
    series = generate_sensor_timeseries(0.0, 4, cfg)
    values = [v for _, v in series]
    # t=0 -> sin(0)=0 -> base
    assert math.isclose(values[0], 10.0, abs_tol=1e-9)
    # t=day/4 -> sin(pi/2)=1 -> base + amplitude (tepe)
    assert math.isclose(values[1], 15.0, abs_tol=1e-9)
    # t=day/2 -> sin(pi)=0 -> base
    assert math.isclose(values[2], 10.0, abs_tol=1e-9)
    # t=3day/4 -> sin(3pi/2)=-1 -> base - amplitude (çukur)
    assert math.isclose(values[3], 5.0, abs_tol=1e-9)


def test_generate_sensor_timeseries_seasonal_component():
    year = 365.25 * 86400.0
    cfg = SensorSeriesConfig(
        base_value=0.0, daily_amplitude=0.0, seasonal_amplitude=10.0,
        noise_std=0.0, interval_seconds=year / 4, seed=0, year_seconds=year,
    )
    series = generate_sensor_timeseries(0.0, 4, cfg)
    values = [v for _, v in series]
    assert math.isclose(values[0], 0.0, abs_tol=1e-6)
    assert math.isclose(values[1], 10.0, abs_tol=1e-6)


def test_generate_sensor_timeseries_respects_min_max_clamp():
    cfg = SensorSeriesConfig(
        base_value=0.0, daily_amplitude=100.0, seasonal_amplitude=0.0,
        noise_std=0.0, min_value=-1.0, max_value=1.0,
    )
    series = generate_sensor_timeseries(0.0, 20, cfg)
    for _, v in series:
        assert -1.0 <= v <= 1.0


def test_generate_sensor_timeseries_overrides_kwargs():
    series = generate_sensor_timeseries(0.0, 10, base_value=100.0, noise_std=0.0,
                                          daily_amplitude=0.0, seasonal_amplitude=0.0)
    for _, v in series:
        assert math.isclose(v, 100.0, abs_tol=1e-9)


# ========================================================================== #
# apply_timeseries_to_sensor
# ========================================================================== #

def test_apply_timeseries_updates_sensor_and_logs_single_event():
    twin = DigitalTwin(id="bldg-x")
    sensor = SensorBinding(sensor_id="temp-1", sensor_type="temperature", target_ref="roof")
    twin.bind_sensor(sensor)
    history_before = len(twin.history)

    series = generate_sensor_timeseries(0.0, 24, SensorSeriesConfig(seed=7, noise_std=0.1))
    applied = apply_timeseries_to_sensor(twin, "temp-1", series)

    assert applied == 24
    bound = twin.sensors_for_target("roof")[0]
    assert bound.last_value == series[-1][1]
    assert bound.last_updated == series[-1][0]
    # yalnızca 1 yeni event eklenmeli (şişirme yok)
    assert len(twin.history) == history_before + 1
    assert twin.history[-1].event_type == "sensor_timeseries_applied"


def test_apply_timeseries_with_log_each_update_creates_full_history():
    twin = DigitalTwin(id="bldg-y")
    sensor = SensorBinding(sensor_id="temp-2", sensor_type="temperature", target_ref="roof")
    twin.bind_sensor(sensor)
    history_before = len(twin.history)

    series = generate_sensor_timeseries(0.0, 5, SensorSeriesConfig(seed=1, noise_std=0.0))
    applied = apply_timeseries_to_sensor(twin, "temp-2", series, log_each_update=True)

    assert applied == 5
    assert len(twin.history) == history_before + 5


def test_apply_timeseries_unknown_sensor_returns_zero():
    twin = DigitalTwin(id="bldg-z")
    series = generate_sensor_timeseries(0.0, 5, SensorSeriesConfig(seed=1))
    applied = apply_timeseries_to_sensor(twin, "nonexistent", series)
    assert applied == 0


def test_apply_timeseries_empty_series_is_noop():
    twin = DigitalTwin(id="bldg-w")
    sensor = SensorBinding(sensor_id="temp-3", sensor_type="temperature", target_ref="roof")
    twin.bind_sensor(sensor)
    assert apply_timeseries_to_sensor(twin, "temp-3", []) == 0


# ========================================================================== #
# Serialization
# ========================================================================== #

def test_hierarchy_to_dict_from_dict_roundtrip():
    h = TwinHierarchy()
    h.add("root")
    h.add("block-1", parent_id="root")
    h.add("bldg-1", parent_id="block-1")

    data = h.to_dict()
    h2 = TwinHierarchy.from_dict(data)

    assert h2.parent_of("bldg-1") == "block-1"
    assert h2.parent_of("block-1") == "root"
    assert set(h2.children_of("root")) == {"block-1"}


# ========================================================================== #
# Üst seviye harita paketinden erişilebilirlik (re-export)
# ========================================================================== #

def test_top_level_harita_package_reexports_d11_symbols():
    import harita

    assert harita.TwinHierarchy is TwinHierarchy
    assert harita.generate_sensor_timeseries is generate_sensor_timeseries
    assert harita.apply_timeseries_to_sensor is apply_timeseries_to_sensor
    assert harita.SensorSeriesConfig is SensorSeriesConfig
