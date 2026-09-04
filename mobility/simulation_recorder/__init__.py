"""
Simulation Recorder
====================

Roadmap V9 / OMURGA / O.1 — "Kayıt katmanı".

Sorun (roadmap'te tespit edilen): `EvacuationSimulator.run()` bir sonuç
(`EvacuationResult`) döndürüyor ama ara kareleri saklamıyor — animasyon için
veri üretmiyor.

Çözüm: `SimulationRecorder`, bir simülasyon koşumu sırasında `record_frame()`
çağrılarını **keyframe + interpolasyon** stratejisiyle biriktirir: tam
`dt=0.1` sn çözünürlükte her kareyi saklamak (`crowd_simulation`'ın iç adımı)
bellek patlamasına yol açar (60.000 tick × binlerce agent gibi şehir ölçeği
senaryolarda) — bunun yerine yalnızca `keyframe_interval_s` aralığıyla
("örn. her 0.5 sn'de bir kare") anlık pozisyon kaydedilir; aradaki hareket
render tarafında `interpolate_at()` ile lineer interpolasyonla tamamlanır.

Bu modül `crowd_simulation`'ın kendisini değiştirmez — `EvacuationSimulator`
ve benzeri motorlar recorder'ı isteğe bağlı (opsiyonel) bir parametre olarak
alır ve her adımda `record_frame()` çağırır (bkz.
`mobility/crowd_simulation/__init__.py`'deki `EvacuationSimulator.run()`
güncellemesi).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

__all__ = [
    "AgentFrameState",
    "AgentSnapshot",
    "Keyframe",
    "RunMetadata",
    "SimulationRecorder",
]


class AgentFrameState(str, Enum):
    """Render tarafının renk kodlaması için (O.2 / Katman 2.4 animasyon
    notuyla uyumlu: "yeşil=hareket ediyor, kırmızı=beklemede/panik")."""

    MOVING = "moving"
    WAITING = "waiting"
    PANIC = "panic"
    EVACUATED = "evacuated"


@dataclass(slots=True)
class AgentSnapshot:
    """Bir keyframe anındaki tek bir agent'ın durumu.

    Roadmap V10 / Faz 1.1 ("Ortak Omurga — Veri + Navigasyon Köprüsü"):
    `floor_index`, `z_m`, `room_id` eklendi. Bu üç alan olmadan render
    tarafı (Faz 1 + 1.6 + 1.7 kabul kriteri: "ajan gerçekten binanın
    içinde, gerçek bir kattan/odadan geçiyor mu?") bir ajanın hangi katta
    ve hangi odada olduğunu bilemez, yalnızca 2D (x, y) düzlemsel pozisyon
    görür.

    Geriye dönük uyumluluk: üç alan da varsayılan değerlere sahiptir
    (`floor_index=0`, `room_id=None`, `z_m=0.0`) - `AgentSnapshot(agent_id=...,
    x=..., y=..., state=...)` şeklindeki eski çağrılar/testler değişmeden
    çalışmaya devam eder.
    """

    agent_id: int
    x: float
    y: float
    state: AgentFrameState
    floor_index: int = 0
    room_id: Optional[int] = None
    z_m: float = 0.0


@dataclass(slots=True)
class Keyframe:
    """`t` anındaki tüm agent'ların anlık görüntüsü."""

    t: float
    agents: list[AgentSnapshot] = field(default_factory=list)


@dataclass(slots=True)
class RunMetadata:
    """Roadmap V10 / Faz 1.5 ("Deterministik replay & seed sistemi").

    Bir koşunun **tekrar oynatılabilir** (replayable) olduğunu iddia
    edebilmek için, yalnızca kare verisini değil, o koşuyu tekrar
    üretebilecek tüm parametreleri de kaydetmek gerekir. Bunsuz
    `interpolate_at()` çıktısı "güzel bir animasyon"dur ama "aynı seed
    ile bit-bit aynı sonucu üretir" iddiası doğrulanamaz - Faz 1.6/1.7'nin
    determinizm testi bu alanlara dayanır.
    """

    seed: int | None = None
    dt: float | None = None
    max_time_s: float | None = None
    agent_count: int | None = None
    scenario_id: str | None = None
    extra_params: dict = field(default_factory=dict)


class SimulationRecorder:
    """Bir simülasyon koşumunun agent pozisyonlarını zaman-damgalı
    keyframe'ler halinde kaydeder ve render tarafı için interpolasyonlu
    sorgu arayüzü sağlar.

    Kullanım (bkz. `EvacuationSimulator.run(..., recorder=recorder)`):
        recorder = SimulationRecorder(keyframe_interval_s=0.5)
        while elapsed < max_time_s:
            model.step(agents, dt=dt)
            elapsed += dt
            recorder.maybe_record(elapsed, agents)
        # Render tarafı, herhangi bir t anındaki (interpole) pozisyonları ister:
        frame = recorder.interpolate_at(t=12.3)
    """

    def __init__(self, keyframe_interval_s: float = 0.5, max_keyframes: int = 20_000) -> None:
        if keyframe_interval_s <= 0:
            raise ValueError("keyframe_interval_s pozitif olmalı")
        self.keyframe_interval_s = keyframe_interval_s
        self.max_keyframes = max_keyframes
        self._keyframes: list[Keyframe] = []
        self._last_recorded_t: Optional[float] = None
        # Roadmap V10 / Faz 1.5: koşu meta verisi (seed + parametreler).
        self.run_metadata: RunMetadata = RunMetadata()

    @property
    def keyframes(self) -> list[Keyframe]:
        return self._keyframes

    def set_run_metadata(self, *, seed: int | None = None, dt: float | None = None,
                          max_time_s: float | None = None, agent_count: int | None = None,
                          scenario_id: str | None = None, **extra_params) -> RunMetadata:
        """Bu koşunun seed + parametrelerini kaydeder (Faz 1.5).

        `EvacuationSimulator.run(..., recorder=recorder, seed=42)` bu
        metodu otomatik çağırır; harici kullanım da mümkündür (ör. şehir
        ölçeği koşularda `scenario_id` ile etiketleme).
        """
        self.run_metadata = RunMetadata(
            seed=seed, dt=dt, max_time_s=max_time_s,
            agent_count=agent_count, scenario_id=scenario_id,
            extra_params=dict(extra_params),
        )
        return self.run_metadata

    def reset(self) -> None:
        self._keyframes.clear()
        self._last_recorded_t = None
        self.run_metadata = RunMetadata()

    @staticmethod
    def _snapshot_state(agent) -> AgentFrameState:
        """`crowd_simulation.Agent`'tan render durumu türetir. `agent`'ı
        gevşek tipte (duck-typing) alır — `mobility.crowd_simulation.Agent`
        veya uyumlu bir arayüze sahip başka bir agent tipiyle çalışır (ör.
        gelecekteki `traffic_simulation` araç agent'ları)."""
        if getattr(agent, "evacuated", False):
            return AgentFrameState.EVACUATED
        behavior = getattr(agent, "behavior", None)
        if behavior is not None and getattr(behavior, "value", behavior) == "panic":
            return AgentFrameState.PANIC
        if getattr(agent, "waiting", False):
            return AgentFrameState.WAITING
        return AgentFrameState.MOVING

    def record_frame(self, t: float, agents: list) -> Keyframe:
        """Verilen `t` anı için (koşulsuz) bir keyframe kaydeder.

        Roadmap V10 / Faz 1.1: `floor_index` / `room_id` / `z_m`, agent
        nesnesinden `getattr(..., default)` ile duck-typing üzerinden
        okunur - `Agent` dataclass'ı bu alanları zaten taşıyor (varsayılan
        0/None/0.0), ama recorder gelecekteki uyumlu-arayüzlü agent
        tiplerini (ör. `traffic_simulation` araçları, katı olmayan
        nesneler) de bozmadan kabul etmeye devam eder.
        """
        snapshot = [
            AgentSnapshot(
                agent_id=a.agent_id,
                x=a.position.x,
                y=a.position.y,
                state=self._snapshot_state(a),
                floor_index=getattr(a, "floor_index", 0),
                room_id=getattr(a, "room_id", None),
                z_m=getattr(a, "z_m", 0.0),
            )
            for a in agents
        ]
        kf = Keyframe(t=t, agents=snapshot)
        self._keyframes.append(kf)
        self._last_recorded_t = t
        if len(self._keyframes) > self.max_keyframes:
            # Bellek üst sınırı: en eski kareleri budayarak sabit üst sınır
            # koru (şehir ölçeği uzun-koşum senaryoları için güvenlik ağı).
            overflow = len(self._keyframes) - self.max_keyframes
            del self._keyframes[:overflow]
        return kf

    def maybe_record(self, t: float, agents: list) -> Optional[Keyframe]:
        """Yalnızca son kayıttan bu yana `keyframe_interval_s` kadar zaman
        geçtiyse kaydeder (asıl bellek-tasarrufu mekanizması). İlk çağrıda
        (t=0 civarı) her zaman kaydeder."""
        if self._last_recorded_t is None or (t - self._last_recorded_t) >= self.keyframe_interval_s:
            return self.record_frame(t, agents)
        return None

    def interpolate_at(self, t: float) -> dict[int, AgentSnapshot]:
        """`t` anındaki (varsa iki keyframe arasında lineer interpole
        edilmiş) agent pozisyonlarını `agent_id -> AgentSnapshot` olarak
        döner. `t`, kayıt aralığının dışındaysa en yakın uç keyframe'e
        clamp edilir (aynen `EvacuationBenchmark`'ın sınır-durum ilkesiyle
        tutarlı — sessizce yanlış sonuç üretmek yerine öngörülebilir davran)."""
        if not self._keyframes:
            return {}

        times = [kf.t for kf in self._keyframes]
        if t <= times[0]:
            return {s.agent_id: s for s in self._keyframes[0].agents}
        if t >= times[-1]:
            return {s.agent_id: s for s in self._keyframes[-1].agents}

        idx = bisect.bisect_left(times, t)
        kf_after = self._keyframes[idx]
        kf_before = self._keyframes[idx - 1]

        span = kf_after.t - kf_before.t
        ratio = 0.0 if span <= 0 else (t - kf_before.t) / span

        before_by_id = {s.agent_id: s for s in kf_before.agents}
        after_by_id = {s.agent_id: s for s in kf_after.agents}

        result: dict[int, AgentSnapshot] = {}
        for agent_id, snap_before in before_by_id.items():
            snap_after = after_by_id.get(agent_id)
            if snap_after is None:
                # Agent bu aralıkta ortadan kalktı (ör. tahliye edildi ve
                # artık kaydedilmiyor) — son bilinen durumunu koru.
                result[agent_id] = snap_before
                continue
            result[agent_id] = AgentSnapshot(
                agent_id=agent_id,
                x=snap_before.x + (snap_after.x - snap_before.x) * ratio,
                y=snap_before.y + (snap_after.y - snap_before.y) * ratio,
                # Durum (state) interpole edilmez — hedef karedeki (after)
                # durumu, ratio >= 0.5 olduğunda erken yansıt, aksi halde
                # önceki durumu koru (ani state-flicker'ı azaltmak için).
                state=snap_after.state if ratio >= 0.5 else snap_before.state,
            )
        # Aralıkta yeni beliren (before'da olmayan) agent'lar (nadiren, ör.
        # dinamik spawn) — after durumunu doğrudan kullan.
        for agent_id, snap_after in after_by_id.items():
            if agent_id not in result:
                result[agent_id] = snap_after
        return result

    def bottleneck_over_time(self, cell_size: float = 1.0) -> list[tuple[float, tuple[int, int], int]]:
        """Her keyframe için en yoğun hücreyi hesaplar — Katman 2.4 madde 3
        ("Darboğaz tespiti") burada karşılığını bulur: `OccupancyHeatmap`
        şu an yalnızca final durumda çalışıyordu, bu metod zaman serisi
        üzerinden `(t, cell, count)` listesi üretir. Mevcut
        `OccupancyHeatmap`'in ızgara mantığıyla tutarlı (aynı `cell_size`
        yaklaşımı) ama tekrar kod yazmamak için burada bağımsız, hafif bir
        implementasyon kullanılır (yalnızca (x, y) çiftlerine ihtiyaç var,
        tam `Agent` nesnesine değil)."""
        results: list[tuple[float, tuple[int, int], int]] = []
        for kf in self._keyframes:
            grid: dict[tuple[int, int], int] = {}
            for snap in kf.agents:
                if snap.state == AgentFrameState.EVACUATED:
                    continue
                cell = (int(snap.x // cell_size), int(snap.y // cell_size))
                grid[cell] = grid.get(cell, 0) + 1
            if grid:
                peak_cell, peak_count = max(grid.items(), key=lambda kv: kv[1])
                results.append((kf.t, peak_cell, peak_count))
        return results

    def peak_bottleneck(self, cell_size: float = 1.0) -> Optional[tuple[float, tuple[int, int], int]]:
        """Tüm koşum boyunca en yoğun anı/hücreyi döner —
        `EvacuationResult.bottleneck_location` / `bottleneck_peak_time_s`
        alanlarını doldurmak için kullanılır (bkz. `crowd_simulation`
        güncellemesi)."""
        series = self.bottleneck_over_time(cell_size=cell_size)
        if not series:
            return None
        return max(series, key=lambda row: row[2])
