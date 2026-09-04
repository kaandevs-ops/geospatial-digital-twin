"""
Yangın Yayılım Modeli (Fire / Smoke Spread)
=============================================

Roadmap V9 / Katman 7.2 ("Yangın Tahliye Simülasyonu — eski 'madde 4'"),
madde 1 ("Duman/ateş yayılım modeli"):

    "hazard_data/fire_spread.py — hücre-otomat (cellular automaton)
    tabanlı duman yayılımı (her adımda komşu hücrelere yayılma olasılığı,
    kapı/duvarların yayılımı yavaşlatması). Tam CFD gerekmez - bu ölçek
    için aşırı mühendislik olur; hücre-otomat yeterli gerçekçilik/
    performans dengesi verir."

Bu modül **tam CFD değildir** ve öyle olduğu iddia edilmez (roadmap'in
gösterge disiplini, `risk_scoring.py` ile aynı ilke): ızgara üzerindeki her
hücre 0.0 (temiz) - 1.0 (tam yanma) arası bir "yoğunluk" (intensity)
taşır; her adımda komşu hücrelere, duvar/kapı gibi engellerin yayılımı
yavaşlattığı basit, deterministik-seed'li olasılıksal bir kural ile yayılır.

Ağ (NavGraph) entegrasyonu — `FireAwareRouter` — Faz IV'ün
`behavior_rules.CongestionAwareRouter` ile **aynı teknik**: kenar maliyeti
baseline'dan yeniden hesaplanır (birikmeli değil), `NavGraph.
update_edge_cost()` (Faz IV'te eklenen mutable-weight altyapısı) kullanılır.
Tam yanan (FIRE eşiği üstü) hücreye giden kenarlar `set_blocked()` ile
(silinmeden) kapatılır — Katman 3.3'teki `ROAD_CLOSED` mekanizmasıyla aynı
teknik, roadmap'in kendi notunda işaret ettiği "iki farklı katmanda yeniden
kullanım" ilkesi.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Hashable, Iterable

from ..mobility.pathfinding import NavGraph

NodeId = Hashable
CellId = tuple[int, int]

# Gösterge niteliğindeki eşikler (roadmap'in kendi disiplini: kesin bir
# mühendislik/CFD sonucu değil, basit ve ayarlanabilir bir yaklaşım).
SMOKE_THRESHOLD = 0.15
FIRE_THRESHOLD = 0.75


class FireCellState(str, Enum):
    """Bir hücrenin yoğunluk (intensity) değerinden türetilen okunabilir
    durumu - yalnızca raporlama/görselleştirme için; iç hesaplama sürekli
    (continuous) `intensity` üzerinden yürür."""

    CLEAR = "clear"
    SMOKE = "smoke"
    FIRE = "fire"


def _state_of(intensity: float) -> FireCellState:
    if intensity >= FIRE_THRESHOLD:
        return FireCellState.FIRE
    if intensity >= SMOKE_THRESHOLD:
        return FireCellState.SMOKE
    return FireCellState.CLEAR


@dataclass(slots=True)
class FireSpreadModel:
    """Dikdörtgen ızgara üzerinde hücre-otomat tabanlı duman/ateş yayılımı.

    `wall_cells` - yayılımı ciddi ölçüde yavaşlatan (ör. taşıyıcı duvar,
    yangın kapısı kapalı) hücreler; `door_cells` - kısmen yavaşlatan (ör.
    açık kapı, ince bölme) hücreler. İkisi de graf/oda verisinden türetilir,
    burada yeniden icat edilmez - çağıran taraf (`building_reconstruction`
    oda/duvar verisinden) bu kümeleri doldurur.
    """

    width: int
    height: int
    cell_size: float = 1.0
    ignition_cells: Iterable[CellId] = ()
    wall_cells: frozenset[CellId] = field(default_factory=frozenset)
    door_cells: frozenset[CellId] = field(default_factory=frozenset)
    spread_rate_per_s: float = 0.35
    wall_barrier_factor: float = 0.05     # duvar: yayılım ~20 kat yavaşlar
    door_barrier_factor: float = 0.45     # kapı: yayılım ~2 kat yavaşlar
    seed: int | None = 42

    elapsed_s: float = field(init=False, default=0.0)
    intensity: dict[CellId, float] = field(init=False)
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self.intensity = {
            (x, y): 0.0
            for y in range(self.height)
            for x in range(self.width)
        }
        for cell in self.ignition_cells:
            if cell in self.intensity:
                self.intensity[cell] = 1.0
        self._rng = random.Random(self.seed)

    # -- hücre yardımcıları --------------------------------------------- #

    def _in_bounds(self, cell: CellId) -> bool:
        x, y = cell
        return 0 <= x < self.width and 0 <= y < self.height

    def _neighbors(self, cell: CellId) -> Iterable[CellId]:
        x, y = cell
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (x + dx, y + dy)
            if self._in_bounds(nb):
                yield nb

    def _barrier_factor(self, cell: CellId) -> float:
        """Hedef hücreye yayılımı yavaşlatan çarpan (1.0 = engel yok)."""
        if cell in self.wall_cells:
            return self.wall_barrier_factor
        if cell in self.door_cells:
            return self.door_barrier_factor
        return 1.0

    # -- simülasyon adımı -------------------------------------------------#

    def step(self, dt: float) -> None:
        """Hücre-otomat tek adımı: her hücrenin yeni yoğunluğu, komşularının
        mevcut yoğunluğundan (bu adımın başındaki, "eski" durumdan - eş
        zamanlı güncelleme, sıra-bağımlılığı yaratmamak için) beslenir.
        Deterministik yayılım oranına küçük bir olasılıksal gürültü
        (`_rng`, sabit seed ile tekrarlanabilir) eklenir - gerçek yangının
        pürüzlü/eşit-olmayan ilerleyişini kaba biçimde temsil eder."""
        old = dict(self.intensity)
        new = dict(old)
        for cell, current in old.items():
            if current >= 1.0:
                continue
            inflow = 0.0
            for nb in self._neighbors(cell):
                nb_intensity = old[nb]
                if nb_intensity <= current:
                    continue
                barrier = self._barrier_factor(cell)
                noise = 0.85 + 0.3 * self._rng.random()  # ±%15 gürültü
                inflow += (nb_intensity - current) * self.spread_rate_per_s * barrier * noise
            if inflow > 0.0:
                new[cell] = min(1.0, current + inflow * dt)
        self.intensity = new
        self.elapsed_s += dt

    def run(self, duration_s: float, dt: float = 1.0) -> None:
        """`duration_s` kadar zamanı `dt` büyüklüğünde adımlarla ilerletir.
        Son adım `duration_s`'e tam bölünmezse (veya `duration_s < dt`
        ise) kalan süre kadar kısaltılır - toplam ilerleme her zaman tam
        `duration_s` olur (önceki sürümde `duration_s < dt` durumunda tam
        bir `dt` adımı zorlanıyor ve zaman fazladan ilerliyordu)."""
        remaining = duration_s
        epsilon = 1e-9
        while remaining > epsilon:
            step_dt = min(dt, remaining)
            self.step(step_dt)
            remaining -= step_dt

    # -- sorgular ---------------------------------------------------------#

    def state_of(self, cell: CellId) -> FireCellState:
        return _state_of(self.intensity.get(cell, 0.0))

    def smoke_density(self, cell: CellId) -> float:
        """0.0 (temiz) - 1.0 (tam yanma) arası yoğunluk - bilinmeyen/ızgara
        dışı hücreler için 0.0 (temiz varsayılır, sessizce hata vermez)."""
        return self.intensity.get(cell, 0.0)

    def is_impassable(self, cell: CellId) -> bool:
        return self.state_of(cell) is FireCellState.FIRE

    def burning_cell_count(self) -> int:
        return sum(1 for v in self.intensity.values() if v >= FIRE_THRESHOLD)

    def cells_by_state(self, state: FireCellState) -> list[CellId]:
        return [c for c, v in self.intensity.items() if _state_of(v) is state]


@dataclass(slots=True)
class FireAwareRouter:
    """`NavGraph` üzerinde yangının dinamik ağırlıklandırma etkisini
    uygular - roadmap'in "Faz IV'ün mutable-weight altyapısı, iki katmanda
    (kalabalık burada, duman/ateş orada) yeniden kullanılır" notunun somut
    karşılığı (bkz. `behavior_rules.CongestionAwareRouter`, aynı desen).

    `node_to_cell` - graf düğümünü `FireSpreadModel` ızgara hücresine
    eşleyen callable (indoor/açık-alan entegrasyon noktası, burada icat
    edilmez).
    """

    graph: NavGraph
    node_to_cell: Callable[[NodeId], CellId]
    smoke_penalty_coefficient: float = 2.0
    _base_costs: dict[tuple[NodeId, NodeId], float] = field(default_factory=dict, init=False)
    _fire_blocked_edges: set[tuple[NodeId, NodeId]] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self._base_costs = {(a, b): cost for a, b, cost in self.graph.edges()}

    def reset(self) -> None:
        """Tüm kenarları baseline maliyetine döndürür ve yangın kaynaklı
        blokajları kaldırır (CongestionAwareRouter.reset ile aynı ilke)."""
        for (a, b), cost in self._base_costs.items():
            self.graph.update_edge_cost(a, b, cost, bidirectional=False)
        for a, b in self._fire_blocked_edges:
            self.graph.set_blocked(a, b, blocked=False)
        self._fire_blocked_edges.clear()

    def apply(self, fire_model: FireSpreadModel) -> dict[CellId, FireCellState]:
        """Roadmap formülüne benzer biçimde (kalabalık cezasıyla aynı
        desen): `kenar_maliyeti = baseline * (1 + katsayı * duman_yoğunluğu)`.
        Hedef düğümün hücresi tam yanıyorsa (`FIRE`), kenar **silinmeden**
        `set_blocked(True)` ile kapatılır (geri açılabilir - `reset()`).
        Baseline'dan yeniden hesaplanır, birikmeli ceza yaratılmaz.
        Dönen sözlük (raporlama/test amaçlı): ziyaret edilen hedef
        hücrelerin durumu."""
        visited_states: dict[CellId, FireCellState] = {}
        # Önce bu turda artık yangın-bloklu olmayan kenarları serbest bırak
        # (blokaj kalıcı değil - hücre koşumun ilerleyen bir anında sönmüş
        # olabilir; gerçekçi olmasa da API tutarlılığı için desteklenir).
        still_blocked: set[tuple[NodeId, NodeId]] = set()
        for (a, b), base_cost in self._base_costs.items():
            cell = self.node_to_cell(b)
            state = fire_model.state_of(cell)
            visited_states[cell] = state
            if state is FireCellState.FIRE:
                self.graph.set_blocked(a, b, blocked=True)
                still_blocked.add((a, b))
                continue
            density = fire_model.smoke_density(cell)
            penalized_cost = base_cost * (1.0 + self.smoke_penalty_coefficient * density)
            self.graph.update_edge_cost(a, b, penalized_cost, bidirectional=False)
        for a, b in self._fire_blocked_edges - still_blocked:
            self.graph.set_blocked(a, b, blocked=False)
        self._fire_blocked_edges = still_blocked
        return visited_states


__all__ = [
    "SMOKE_THRESHOLD",
    "FIRE_THRESHOLD",
    "FireCellState",
    "FireSpreadModel",
    "FireAwareRouter",
]
