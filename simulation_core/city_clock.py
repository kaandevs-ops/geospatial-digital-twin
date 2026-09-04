"""
City Clock — Küresel Şehir Saati
=================================

Roadmap V9 / OMURGA / O.1 — "Simülasyon Zaman Ekseni + Küresel Şehir Saati".

Sorun (roadmap'te tespit edilen): her katman kendi zaman kavramıyla
çalışıyordu — `crowd_simulation` sabit `dt=0.1` sn adımıyla, `climate_data`
saatlik veriyle. Bu modül, tüm katmanların abone olabileceği **tek** bir
zaman otoritesi sağlar: gerçek zaman hızlandırılmış/yavaşlatılmış akabilir
(1x, 60x, 3600x — "1 günü 1 dakikada izle") ve her katman kendi `tick(t)`'ini
bu saatten tetikler.

Tasarım ilkeleri (ROADMAP_V9.md "Kritik Tasarım İlkeleri" ile tutarlı):
- Tekrar yazma yok: mevcut `extensibility/event_system.py`'deki `EventSystem`
  pub/sub'ı üzerine kurulur, yeni bir olay veri yolu icat edilmez.
- Saf stdlib, dış bağımlılık yok (`crowd_simulation` ve diğer motorlarla
  aynı disiplin).
- Katmanlar birbirini doğrudan çağırmaz; `CityClock` tick olayını yayınlar,
  dinleyen katman kendi `tick(t, dt)` mantığını çalıştırır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from ..extensibility.event_system import Event, EventSystem

__all__ = [
    "ClockEventType",
    "ClockState",
    "CityClock",
]


class ClockEventType(str, Enum):
    """Şehir saatinin yaydığı olay isimleri (`EventSystem` pattern'leriyle
    uyumlu — `city_clock.*` glob'uyla toptan dinlenebilir)."""

    TICK = "city_clock.tick"
    STARTED = "city_clock.started"
    PAUSED = "city_clock.paused"
    RESUMED = "city_clock.resumed"
    RESET = "city_clock.reset"
    SPEED_CHANGED = "city_clock.speed_changed"


@dataclass(slots=True)
class ClockState:
    """Bir `tick` anında yayınlanan anlık saat durumu (event payload'ı)."""

    sim_time_s: float
    dt_s: float
    speed_multiplier: float
    tick_index: int
    running: bool


class CityClock:
    """Tüm şehir katmanlarının abone olduğu tek zaman otoritesi.

    Kullanım:
        clock = CityClock(event_bus=my_bus)
        clock.subscribe(lambda state: traffic_engine.tick(state.sim_time_s, state.dt_s))
        clock.set_speed(60.0)   # "1 dakikayı 1 saniyede izle"
        for _ in range(600):
            clock.advance(real_dt_s=1.0)  # render/oyun döngüsü her kare çağırır

    `advance()` *gerçek* geçen saniyeyi alır, `speed_multiplier` ile çarpıp
    simülasyon zamanını ilerletir ve bunu sabit `base_dt_s` adımlarına bölerek
    her katmanın öngörülebilir, sabit boyutlu adımlarla tick almasını sağlar
    (değişken kare süresi nedeniyle fizik/sosyal-kuvvet modellerinin
    kararsızlaşmasını önler — `SocialForceModel` gibi motorlar sabit `dt`
    varsayımıyla doğrulanmıştır, bkz. `EvacuationBenchmark`).
    """

    def __init__(
        self,
        event_bus: Optional[EventSystem] = None,
        base_dt_s: float = 0.1,
        speed_multiplier: float = 1.0,
        max_ticks_per_advance: int = 10_000,
    ) -> None:
        if base_dt_s <= 0:
            raise ValueError("base_dt_s pozitif olmalı")
        if speed_multiplier <= 0:
            raise ValueError("speed_multiplier pozitif olmalı")

        self.event_bus = event_bus if event_bus is not None else EventSystem()
        self.base_dt_s = base_dt_s
        self.speed_multiplier = speed_multiplier
        self.max_ticks_per_advance = max_ticks_per_advance

        self.sim_time_s: float = 0.0
        self.tick_index: int = 0
        self.running: bool = False
        self._accumulator_s: float = 0.0

        # Doğrudan Python callback aboneliği (event_bus'a ek olarak, düşük
        # ek-yük gerektiren sıcak-döngü katmanları için — trafik/kalabalık
        # motorları her tick'te event dispatch overhead'inden kaçınabilir).
        self._direct_subscribers: list[Callable[[ClockState], None]] = []

    # -- Yaşam döngüsü ------------------------------------------------- #

    def start(self) -> None:
        self.running = True
        self.event_bus.emit(ClockEventType.STARTED.value, self._state(), source="city_clock")

    def pause(self) -> None:
        self.running = False
        self.event_bus.emit(ClockEventType.PAUSED.value, self._state(), source="city_clock")

    def resume(self) -> None:
        self.running = True
        self.event_bus.emit(ClockEventType.RESUMED.value, self._state(), source="city_clock")

    def reset(self) -> None:
        self.sim_time_s = 0.0
        self.tick_index = 0
        self._accumulator_s = 0.0
        self.event_bus.emit(ClockEventType.RESET.value, self._state(), source="city_clock")

    def set_speed(self, multiplier: float) -> None:
        """Simülasyon hız çarpanını değiştirir (1x=gerçek zaman, 60x, 3600x...)."""
        if multiplier <= 0:
            raise ValueError("speed_multiplier pozitif olmalı")
        self.speed_multiplier = multiplier
        self.event_bus.emit(
            ClockEventType.SPEED_CHANGED.value, self._state(), source="city_clock"
        )

    # -- Abonelik -------------------------------------------------------- #

    def subscribe(self, callback: Callable[[ClockState], None]) -> Callable[[], None]:
        """Her `tick` adımında doğrudan çağrılacak bir callback ekler.
        Geriye aboneliği iptal eden bir fonksiyon döner."""
        self._direct_subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._direct_subscribers:
                self._direct_subscribers.remove(callback)

        return _unsubscribe

    # -- İlerletme --------------------------------------------------------- #

    def advance(self, real_dt_s: float) -> int:
        """Gerçek geçen süreyi (saniye) alır, hız çarpanıyla ölçekleyip
        `base_dt_s` genişliğinde sabit adımlara böler ve her adım için
        `TICK` olayını + doğrudan abonelikleri tetikler.

        Döner: bu çağrıda kaç tick işlendiği (0 olabilir, `running=False`
        ise veya çok kısa bir `real_dt_s` verilmişse birikim yeterli oluşana
        kadar hiçbir tick tetiklenmez).
        """
        if not self.running or real_dt_s <= 0:
            return 0

        self._accumulator_s += real_dt_s * self.speed_multiplier
        ticks_done = 0
        # Kayan nokta birikim hatasını tolere etmek için küçük epsilon:
        # `base_dt_s` tam katları olan girdilerde (ör. 600 × 0.1 == 60.0)
        # tekrarlanan çıkarma işlemi 59.999999... gibi bir kalıntı bırakıp
        # son tick'i sessizce kaybettirebilir.
        epsilon = self.base_dt_s * 1e-9

        while self._accumulator_s >= self.base_dt_s - epsilon and ticks_done < self.max_ticks_per_advance:
            self.sim_time_s += self.base_dt_s
            self.tick_index += 1
            self._accumulator_s -= self.base_dt_s
            ticks_done += 1

            state = self._state()
            for cb in list(self._direct_subscribers):
                cb(state)
            self.event_bus.emit(ClockEventType.TICK.value, state, source="city_clock")

        return ticks_done

    def advance_ticks(self, n: int) -> None:
        """Gerçek-zamandan bağımsız, `n` adet sabit `base_dt_s` tick'i
        doğrudan işler — batch/offline senaryo koşumları (ör. kapasite
        batch-runner, Faz III) için `advance()`'in gerçek-zaman sarmalayıcısı
        olmadan kullanılır."""
        for _ in range(n):
            self.sim_time_s += self.base_dt_s
            self.tick_index += 1
            state = self._state()
            for cb in list(self._direct_subscribers):
                cb(state)
            self.event_bus.emit(ClockEventType.TICK.value, state, source="city_clock")

    def _state(self) -> ClockState:
        return ClockState(
            sim_time_s=self.sim_time_s,
            dt_s=self.base_dt_s,
            speed_multiplier=self.speed_multiplier,
            tick_index=self.tick_index,
            running=self.running,
        )
