"""
Davranış Kuralları (Behavior Rules)
=====================================

Roadmap V9 / Katman 2.4 madde 7 ("İnsan davranışı — kural tabanlı karar
katmanı", eski "madde 7"):

    "Bu madde ayrı bir modül değil, mevcut `SocialForceModel`/
    `EvacuationSimulator`'a eklenecek bir karar katmanı ... her agent'ın
    periyodik rota yenilemesinde şu öncelik listesi uygulanır:
      - En yakın çıkışı seç → zaten `assign_nearest_exit_paths` bunu yapıyor.
      - Kalabalık çıkıştan kaçın → `OccupancyHeatmap`'ten çıkış önündeki
        yoğunluğu okuyup A* maliyetine 'yoğunluk cezası' ekleme (kenar
        maliyeti = mesafe × (1 + yoğunluk_katsayısı)).
      - Dumanlı bölgeye girme → Katman 7'deki yangın dinamik
        ağırlıklandırmasının doğal sonucu.
      - Ana çıkış kapalıysa alternatif ara → A*'ın zaten yaptığı iş,
        yalnızca kapalı kenarın graf'tan çıkarılması yeterli."

Metodolojik disiplin (roadmap'in kendi notu, birebir korunuyor): "Bu
kurallar 'gerçek insan psikolojisi' iddiası taşımaz - literatürdeki
basit, adı konmuş kurallardan (nearest-exit, congestion-avoidance) öteye
geçmez."
"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

from ..pathfinding import NavGraph
from . import Agent, AgentBehavior, OccupancyHeatmap

NodeId = Hashable


@dataclass
class CongestionAwareRouter:
    """`NavGraph` üzerinde "kalabalık çıkıştan kaçın" kuralını uygular:
    çıkış önündeki hücre yoğunluğunu okuyup, o hücreye yakın düğümlere
    bağlanan kenarların maliyetine bir "yoğunluk cezası" ekler (roadmap'in
    formülü: `kenar_maliyeti = mesafe * (1 + yoğunluk_katsayısı)`).

    Baseline maliyetleri (`_base_costs`) ilk kurulumda `graph.edges()`
    üzerinden bir kez okunur ve saklanır - her `refresh()` çağrısında
    **birikmeli** ceza uygulanmaz (üzerine tekrar tekrar katlanıp
    büyümez); her seferinde baseline'dan yeniden hesaplanır. Bu, `Faz V`
    (yangın dinamik ağırlıklandırma) ile aynı `update_edge_cost()`
    mekanizmasını kullanır ama farklı bir ceza kaynağı (yoğunluk vs.
    duman) uygular - iki katmanda aynı teknik yeniden kullanılır.
    """

    graph: NavGraph
    cell_size: float = 1.0
    congestion_coefficient: float = 0.5
    _base_costs: dict[tuple[NodeId, NodeId], float] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._capture_baseline()

    def _capture_baseline(self) -> None:
        self._base_costs = {(a, b): cost for a, b, cost in self.graph.edges()}

    def reset(self) -> None:
        """Tüm kenarları baseline maliyetlerine geri döndürür (ceza
        temizlenir) - hazard/kalabalık geçtiğinde kullanılabilir."""
        for (a, b), cost in self._base_costs.items():
            self.graph.update_edge_cost(a, b, cost, bidirectional=False)

    def apply_density_penalty(self, agents: list[Agent]) -> dict[tuple[int, int], int]:
        """`OccupancyHeatmap.compute()` ile güncel yoğunluğu okur, her
        kenarın **hedef düğümünün** bulunduğu hücredeki agent sayısına
        göre (roadmap formülü) maliyeti günceller. Baseline'dan
        hesaplanır (birikmeli değil). Kullanılan heatmap'i (raporlama/test
        amaçlı) döner."""
        heatmap = OccupancyHeatmap.compute(agents, cell_size=self.cell_size)
        for (a, b), base_cost in self._base_costs.items():
            pos = self.graph.positions.get(b)
            if pos is None:
                continue
            cell = (int(pos.x // self.cell_size), int(pos.y // self.cell_size))
            density = heatmap.get(cell, 0)
            penalized_cost = base_cost * (1.0 + self.congestion_coefficient * density)
            self.graph.update_edge_cost(a, b, penalized_cost, bidirectional=False)
        return heatmap


def refresh_congestion_aware_routes(
    agents: list[Agent],
    router: CongestionAwareRouter,
    exits: list[NodeId],
    node_of_agent: Callable[[Agent], NodeId],
    *,
    tag_avoiding_agents: bool = True,
) -> dict[tuple[int, int], int]:
    """Roadmap'in öncelik listesinin ilk iki maddesini birleştiren
    kolaylık fonksiyonu: yoğunluk cezasını uygula (`router`), ardından
    `EvacuationSimulator.assign_nearest_exit_paths`'i (cezalı graf
    üzerinde) yeniden çağırarak her agent'ın rotasını günceller - A*
    zaten en düşük maliyetli (artık yoğunluk-cezalı) rotayı seçer, ayrı
    bir "kalabalıktan kaç" algoritması icat edilmedi.

    `tag_avoiding_agents=True` ise (varsayılan), rotası önceki karardan
    farklılaşan (yani gerçekten bir kalabalığı görüp güzergah değiştiren)
    agent'lar `AgentBehavior.AVOID_CROWDED_EXIT` ile etiketlenir - yalnızca
    raporlama/görselleştirme amaçlı, hızı etkilemez (bkz. AgentBehavior
    tanımındaki not)."""
    # Geç import - döngüsel bağımlılığı önlemek için (crowd_simulation ->
    # behavior_rules -> crowd_simulation).
    from . import EvacuationSimulator

    previous_goals = {a.agent_id: a.goal for a in agents}
    heatmap = router.apply_density_penalty(agents)
    EvacuationSimulator.assign_nearest_exit_paths(agents, router.graph, exits, node_of_agent)

    if tag_avoiding_agents:
        for agent in agents:
            if agent.evacuated:
                continue
            previous_goal = previous_goals.get(agent.agent_id)
            if previous_goal is not None and (
                previous_goal.x != agent.goal.x or previous_goal.y != agent.goal.y
            ):
                agent.behavior = AgentBehavior.AVOID_CROWDED_EXIT

    return heatmap


def close_exit_and_seek_alternative(
    agents: list[Agent],
    graph: NavGraph,
    closed_exit_edges: list[tuple[NodeId, NodeId]],
    remaining_exits: list[NodeId],
    node_of_agent: Callable[[Agent], NodeId],
) -> None:
    """Roadmap'in üçüncü kuralı: "Ana çıkış kapalıysa alternatif ara -
    A*'ın zaten yaptığı iş, sadece kapalı kenarın graf'tan çıkarılması
    yeterli." Kenar **silinmez** (`NavGraph`'ın kendi ilkesi - geri
    açılabilir), yalnızca `set_blocked(True)` ile A*/Dijkstra'nın
    atlayacağı şekilde işaretlenir; ardından kalan çıkışlara göre rotalar
    yeniden hesaplanır ve etkilenen agent'lar
    `AgentBehavior.SEEK_ALTERNATIVE_EXIT` ile etiketlenir."""
    from . import EvacuationSimulator

    for a, b in closed_exit_edges:
        graph.set_blocked(a, b, blocked=True)
        graph.set_blocked(b, a, blocked=True)

    EvacuationSimulator.assign_nearest_exit_paths(agents, graph, remaining_exits, node_of_agent)
    for agent in agents:
        if not agent.evacuated:
            agent.behavior = AgentBehavior.SEEK_ALTERNATIVE_EXIT


__all__ = [
    "CongestionAwareRouter",
    "refresh_congestion_aware_routes",
    "close_exit_and_seek_alternative",
]
