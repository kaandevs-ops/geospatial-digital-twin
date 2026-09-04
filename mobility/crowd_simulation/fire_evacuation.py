"""
Yangın Tahliyesi - Periyodik Rota Yenileme + Çıkış Karşılaştırması
=====================================================================

Roadmap V9 / Katman 7.2 ("Yangın Tahliye Simülasyonu"), madde 2-4:

    "2. Dinamik graf ağırlıklandırma ... 3. Agent'ların yeniden rota
    alması: ... periyodik olarak (örn. her 5 sn) yeniden çağrılmalı - bu,
    Katman 2'deki `behavior_rules.py`'nin 'dumanlı bölgeye girme'
    kuralıyla doğrudan kesişir. 4. Çıkış performans karşılaştırması:
    Aynı senaryoyu farklı 'ana çıkış kapalı' varsayımlarıyla art arda
    koşturup ... sonuçları karşılaştıran `FireEvacuationComparator` -
    'A çıkışı kullanılamazsa süre %X artıyor.'"

Bu modül **yeni bir hareket motoru yazmaz** (roadmap'in kendi disiplini,
`capacity_analysis.py` ile aynı ilke): `PeriodicFireRerouter`,
`EvacuationSimulator.run()`'ın Faz IV'te eklenen `on_step` kancasına
takılan ince bir orkestratördür; `FireEvacuationComparator`,
`EvacuationSimulator`/`behavior_rules.close_exit_and_seek_alternative`'i
tekrar tekrar çağıran bir sarmalayıcıdır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Hashable

from ...hazard_data.fire_spread import FireAwareRouter, FireSpreadModel
from ..pathfinding import NavGraph
from . import Agent, AgentBehavior, EvacuationResult, EvacuationSimulator

NodeId = Hashable


# ============================================================================ #
# Periyodik duman-kaçınma rota yenilemesi (madde 2 + 3)
# ============================================================================ #

@dataclass(slots=True)
class PeriodicFireRerouter:
    """`EvacuationSimulator.run(..., on_step=...)` kancasına takılan
    durum-tutan (stateful) yardımcı: her `refresh_interval_s` saniyede
    bir (Roadmap'in "örn. her 5 sn" notu, varsayılan değeri) `fire_model`'i
    ilerletir (`FireSpreadModel.step`) ve `FireAwareRouter.apply()` ile
    graf ağırlıklarını/blokajlarını günceller, ardından etkilenen
    agent'ların rotasını (`assign_nearest_exit_paths`) yeniler.

    `on_step(elapsed, agents)` imzasıyla doğrudan `EvacuationSimulator.run`'a
    verilebilir - motor kendisi hiçbir yangın kavramına bağımlı kalmaz
    (roadmap'in "motor genel kalır, kural katmanı dışarıdan enjekte edilir"
    ilkesiyle tutarlı, Faz IV'teki `on_step` notunun aynısı)."""

    fire_model: FireSpreadModel
    router: FireAwareRouter
    exits: list[NodeId]
    node_of_agent: Callable[[Agent], NodeId]
    refresh_interval_s: float = 5.0
    fire_step_dt_s: float = 1.0
    _last_refresh_s: float = field(init=False, default=0.0)
    _last_elapsed_s: float = field(init=False, default=0.0)
    refresh_count: int = field(init=False, default=0)

    def __call__(self, elapsed: float, agents: list[Agent]) -> None:
        # Yangın modeli, simülasyon zamanına bağlı olarak (dt sim adımı
        # ile aynı değil - `fire_step_dt_s` kendi çözünürlüğü) ilerletilir;
        # burada yalnızca "ne kadar sim-zamanı geçti" izlenir, motorun
        # kendi `dt`'sine sıkı bağımlı olunmaz (genel kancaya uygun).
        step_elapsed = elapsed - self._last_elapsed_s
        self._last_elapsed_s = elapsed
        if step_elapsed > 0.0:
            self.fire_model.run(step_elapsed, dt=self.fire_step_dt_s)

        if elapsed - self._last_refresh_s < self.refresh_interval_s:
            return
        self._last_refresh_s = elapsed
        self.refresh_count += 1

        self.router.apply(self.fire_model)
        previous_goals = {a.agent_id: a.goal for a in agents if not a.evacuated}
        EvacuationSimulator.assign_nearest_exit_paths(
            agents, self.router.graph, self.exits, self.node_of_agent)
        for agent in agents:
            if agent.evacuated:
                continue
            previous_goal = previous_goals.get(agent.agent_id)
            if previous_goal is not None and (
                previous_goal.x != agent.goal.x or previous_goal.y != agent.goal.y
            ):
                agent.behavior = AgentBehavior.AVOID_SMOKE


# ============================================================================ #
# Çıkış performans karşılaştırması (madde 4)
# ============================================================================ #

@dataclass(slots=True)
class ExitScenarioResult:
    """Tek bir kapalı-çıkış varsayımının sonucu - roadmap'in
    "A çıkışı kullanılamazsa süre %X artıyor" cümlesinin veri karşılığı."""

    label: str
    closed_exit_edges: tuple
    result: EvacuationResult
    pct_change_vs_baseline: float | None   # baseline için 0.0, hesaplanamıyorsa None


@dataclass(slots=True)
class FireEvacuationComparisonReport:
    baseline: ExitScenarioResult
    scenarios: list[ExitScenarioResult]
    # Roadmap'in gösterge disiplini (risk_scoring.py ile aynı dil) -
    # bu bir kesin mühendislik/yönetmelik raporu değildir.
    disclaimer: str = (
        "Bu karşılaştırma gösterge niteliğindedir; kesin bir mühendislik "
        "veya yönetmelik uygunluk raporu yerine geçmez."
    )


class FireEvacuationComparator:
    """`EvacuationBenchmark`/`CapacityAnalyzer` ile aynı desende: mevcut
    motoru (`EvacuationSimulator` + `behavior_rules.
    close_exit_and_seek_alternative`) tekrar tekrar çağırıp karşılaştırmalı
    rapor üretir - yeni bir simülasyon motoru yazılmadı."""

    @staticmethod
    def compare(
        scenario_factory: Callable[[], tuple[list[Agent], NavGraph, list[NodeId],
                                              Callable[[Agent], NodeId]]],
        closure_scenarios: list[tuple[str, list[tuple[NodeId, NodeId]], list[NodeId]]],
        *, dt: float = 0.1, max_time_s: float = 600.0,
    ) -> FireEvacuationComparisonReport:
        """`scenario_factory()` her çağrıda **taze** `(agents, graph, exits,
        node_of_agent)` üretmelidir (graf/agent durumu her koşumda
        mutasyona uğradığından - kapalı kenar/tahliye durumu yeniden
        kullanılamaz). `closure_scenarios` - her biri
        `(etiket, kapatılacak_çıkış_kenarları, kalan_çıkışlar)` üçlüsü.
        """
        # Geç import - döngüsel bağımlılığı önlemek için (crowd_simulation
        # -> fire_evacuation -> behavior_rules -> crowd_simulation).
        from .behavior_rules import close_exit_and_seek_alternative

        base_agents, base_graph, base_exits, base_node_of_agent = scenario_factory()
        EvacuationSimulator.assign_nearest_exit_paths(
            base_agents, base_graph, base_exits, base_node_of_agent)
        baseline_result = EvacuationSimulator().run(base_agents, dt=dt, max_time_s=max_time_s)
        baseline = ExitScenarioResult(
            label="baseline", closed_exit_edges=(), result=baseline_result,
            pct_change_vs_baseline=0.0,
        )

        scenarios_out: list[ExitScenarioResult] = []
        for label, closed_edges, remaining_exits in closure_scenarios:
            agents, graph, _exits, node_of_agent = scenario_factory()
            close_exit_and_seek_alternative(
                agents, graph, closed_edges, remaining_exits, node_of_agent)
            result = EvacuationSimulator().run(agents, dt=dt, max_time_s=max_time_s)

            pct_change = None
            if baseline_result.evacuation_time_s > 0:
                pct_change = (
                    (result.evacuation_time_s - baseline_result.evacuation_time_s)
                    / baseline_result.evacuation_time_s * 100.0
                )
            scenarios_out.append(ExitScenarioResult(
                label=label, closed_exit_edges=tuple(closed_edges),
                result=result, pct_change_vs_baseline=pct_change,
            ))

        return FireEvacuationComparisonReport(baseline=baseline, scenarios=scenarios_out)


__all__ = [
    "PeriodicFireRerouter",
    "ExitScenarioResult",
    "FireEvacuationComparisonReport",
    "FireEvacuationComparator",
]
