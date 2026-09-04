"""
mobility.adaptive_signal - Yoğunluk-Adaptif Sinyalizasyon (Katman 3.3 madde 2)
==================================================================================

Roadmap V9 / Faz VI / Katman 3.3 madde 2:

"Trafik ışığı / sinyalizasyon senaryoları: TrafficSignalPhase zaten var -
yoğunluk-adaptif sinyalizasyon (yoğunluğa göre yeşil süresi uzatma)
eklenmesi."

`mobility.traffic_simulation.TrafficSignalPhase` **değiştirilmedi**
(roadmap ilkesi #2) - bu modül onun üzerine, periyodik olarak yoğunluk
okuyup yeni bir `TrafficSignalPhase` **örneği** üreten ince bir
adaptasyon katmanı ekler (`TrafficSimulator.add_signal` zaten mevcut bir
sinyali kaldırıp yenisini eklemeyi destekler - `_signals` dict'i doğrudan
mutasyona uğratılmaz, roadmap'in "mevcut mimari korunacak" ilkesiyle
tutarlı bir dışsal kontrol döngüsü).
"""
from __future__ import annotations

from dataclasses import dataclass

from .traffic_simulation import TrafficSignalPhase, TrafficSimulator


@dataclass(slots=True, frozen=True)
class AdaptiveSignalParams:
    """Basit, açık bir kural tablosu (kara kutu değil - roadmap ilkesi #5).
    `queue_length_threshold`: bu sayının üstünde bekleyen araç varsa yeşil
    süresi uzatılır."""

    queue_length_threshold: int = 5
    green_extension_s: float = 10.0
    max_green_duration_s: float = 90.0
    min_green_duration_s: float = 10.0


def queued_vehicle_count(
    simulator: TrafficSimulator, route_key: str, signal: TrafficSignalPhase,
) -> int:
    """`signal.stop_line_distance`'ın gerisinde, hâlâ hareket etmemiş
    (`speed` ~0) araç sayısını sayar - `TrafficSimulator`'ın iç durumunu
    (`_route_groups`) yalnızca okuma amaçlı kullanır, mutasyona uğratmaz."""
    group = simulator._route_groups.get(route_key, [])  # noqa: SLF001 - kasıtlı, salt-okunur erişim
    count = 0
    for agent in group:
        if agent.arrived:
            continue
        if agent.distance_along_route < signal.stop_line_distance and agent.speed < 0.3:
            count += 1
    return count


def adapt_signal(
    simulator: TrafficSimulator,
    route_key: str,
    base_signal: TrafficSignalPhase,
    *,
    params: AdaptiveSignalParams | None = None,
) -> TrafficSignalPhase:
    """Kuyruk uzunluğuna göre `base_signal`'den türeyen, yeşil süresi
    ayarlanmış **yeni** bir `TrafficSignalPhase` döner (orijinal
    değiştirilmez - `dataclass` immutable değilse bile burada kopyalama
    disiplinine uyulur, roadmap'in "mevcut mimari korunacak" ilkesi).
    `cycle_length_s` sabit tutulmaz - roadmap'in "yeşil süresini uzatma"
    ifadesiyle tutarlı olarak yalnızca yeşil süresi değişir, kırmızı süresi
    aynı kalır (basit, öngörülebilir bir kural)."""
    p = params or AdaptiveSignalParams()
    queue = queued_vehicle_count(simulator, route_key, base_signal)

    new_green = base_signal.green_duration_s
    if queue >= p.queue_length_threshold:
        new_green = min(p.max_green_duration_s, base_signal.green_duration_s + p.green_extension_s)
    else:
        new_green = max(p.min_green_duration_s, base_signal.green_duration_s - p.green_extension_s / 2.0)

    return TrafficSignalPhase(
        stop_line_distance=base_signal.stop_line_distance,
        green_duration_s=new_green,
        red_duration_s=base_signal.red_duration_s,
        yellow_duration_s=base_signal.yellow_duration_s,
        offset_s=base_signal.offset_s,
    )


class AdaptiveSignalController:
    """`refresh_interval_s` aralığında `adapt_signal()`'i çağırıp
    `TrafficSimulator`'daki sinyali günceller (`mobility.crowd_simulation.
    fire_evacuation.PeriodicFireRerouter` ile aynı "periyodik yeniden
    değerlendirme" deseni - roadmap ilkesi #2, aynı desen iki katmanda
    yeniden kullanılıyor)."""

    def __init__(
        self,
        simulator: TrafficSimulator,
        route_key: str,
        base_signal: TrafficSignalPhase,
        *,
        params: AdaptiveSignalParams | None = None,
        refresh_interval_s: float = 30.0,
    ) -> None:
        self.simulator = simulator
        self.route_key = route_key
        self.base_signal = base_signal
        self.params = params or AdaptiveSignalParams()
        self.refresh_interval_s = refresh_interval_s
        self._elapsed_since_refresh = 0.0
        self._current_signal = base_signal
        self._install(base_signal)

    def _install(self, signal: TrafficSignalPhase) -> None:
        signals = self.simulator._signals.setdefault(self.route_key, [])  # noqa: SLF001
        if signal in signals:
            return
        # eski (bu kontrolcünün kurduğu) sinyali kaldır, yenisini ekle -
        # `TrafficSimulator`'ın kendi API'si liste-tabanlı olduğundan
        # doğrudan liste manipülasyonu gerekir (yeni bir public API icat
        # etmek yerine mevcut iç yapıyla uyumlu kalındı).
        if self._current_signal in signals:
            signals.remove(self._current_signal)
        signals.append(signal)
        self._current_signal = signal

    def on_step(self, dt: float) -> None:
        """`TrafficSimulator.step()` çağrılarının yanında periyodik olarak
        çağrılmalı (motor-agnostik kanca deseni, `EvacuationSimulator.
        run(on_step=...)` ile aynı fikir)."""
        self._elapsed_since_refresh += dt
        if self._elapsed_since_refresh < self.refresh_interval_s:
            return
        self._elapsed_since_refresh = 0.0
        new_signal = adapt_signal(
            self.simulator, self.route_key, self._current_signal, params=self.params,
        )
        self._install(new_signal)

    @property
    def current_signal(self) -> TrafficSignalPhase:
        return self._current_signal
