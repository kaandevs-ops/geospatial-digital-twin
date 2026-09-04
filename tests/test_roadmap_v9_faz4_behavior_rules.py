"""
Roadmap V9 / Faz IV testleri — Davranış Kuralları (Katman 2.4 madde 7)
==========================================================================

Kapsam:
  - `NavGraph.update_edge_cost` / `NavGraph.edges()` (yeni, mutable ağırlık
    altyapısı - Faz V'in de kullanacağı ortak mekanizma).
  - `AgentBehavior` genişlemesi (AVOID_CROWDED_EXIT / AVOID_SMOKE /
    SEEK_ALTERNATIVE_EXIT) - hız çarpanını bozmadığının doğrulanması.
  - `CongestionAwareRouter` — yoğunluk cezası, baseline'dan (birikmeli
    olmayan) yeniden hesaplama, `reset()`.
  - `refresh_congestion_aware_routes` — kalabalık çıkıştan kaçınma uçtan
    uca (A* üzerinden rota değişimi + etiketleme).
  - `close_exit_and_seek_alternative` — kapalı ana çıkış → alternatif.
  - `EvacuationSimulator.run(on_step=...)` — periyodik rota yenileme kancası.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.core_engine.geometry_engine import Point2D
from harita.mobility.crowd_simulation import (
    Agent,
    AgentBehavior,
    EvacuationSimulator,
    SocialForceModel,
    spawn_random_agents,
)
from harita.mobility.crowd_simulation.behavior_rules import (
    CongestionAwareRouter,
    close_exit_and_seek_alternative,
    refresh_congestion_aware_routes,
)
from harita.mobility.pathfinding import NavGraph

# ----------------------------------------------------------------------- #
# NavGraph mutable edge weight altyapısı
# ----------------------------------------------------------------------- #


def _simple_graph() -> NavGraph:
    g = NavGraph()
    g.add_node("a", Point2D(0, 0))
    g.add_node("b", Point2D(5, 0))
    g.add_edge("a", "b")
    return g


def test_navgraph_edges_lists_all_directed_edges():
    g = _simple_graph()
    edges = sorted(g.edges())
    assert edges == [("a", "b", 5.0), ("b", "a", 5.0)]


def test_navgraph_update_edge_cost_directional():
    g = _simple_graph()
    g.update_edge_cost("a", "b", 99.0, bidirectional=False)
    assert g.edge_cost("a", "b") == 99.0
    assert g.edge_cost("b", "a") == 5.0  # ters yön etkilenmedi


def test_navgraph_update_edge_cost_bidirectional_default():
    g = _simple_graph()
    g.update_edge_cost("a", "b", 42.0)
    assert g.edge_cost("a", "b") == 42.0
    assert g.edge_cost("b", "a") == 42.0


def test_navgraph_update_edge_cost_does_not_delete_edge():
    """Roadmap ilkesi: kenar silinmez, yalnızca maliyeti değişir."""
    g = _simple_graph()
    g.update_edge_cost("a", "b", 1000.0)
    assert g.has_node("a") and g.has_node("b")
    assert g.edge_count() == 2


def test_navgraph_update_edge_cost_unknown_node_raises():
    g = _simple_graph()
    with pytest.raises(KeyError):
        g.update_edge_cost("a", "unknown", 1.0)


# ----------------------------------------------------------------------- #
# AgentBehavior genişlemesi
# ----------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "tag",
    [
        AgentBehavior.AVOID_CROWDED_EXIT,
        AgentBehavior.AVOID_SMOKE,
        AgentBehavior.SEEK_ALTERNATIVE_EXIT,
    ],
)
def test_new_behavior_tags_do_not_break_speed_multiplier(tag):
    normal = Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(1, 0))
    tagged = Agent(agent_id=1, position=Point2D(0, 0), goal=Point2D(1, 0), behavior=tag)
    # Roadmap notu: rota-karar etiketleri hızı NORMAL ile aynı bırakır.
    assert tagged.effective_desired_speed() == normal.effective_desired_speed()


def test_new_behavior_tags_do_not_trigger_panic_repulsion_multiplier():
    """`SocialForceModel._agent_repulsion` yalnızca PANIC için özel
    çarpan uygular - yeni etiketler bunu tetiklememeli (adım çökmemeli)."""
    agents = [
        Agent(
            agent_id=0,
            position=Point2D(0, 0),
            goal=Point2D(5, 0),
            behavior=AgentBehavior.AVOID_CROWDED_EXIT,
        ),
        Agent(
            agent_id=1,
            position=Point2D(0.3, 0),
            goal=Point2D(5, 0),
            behavior=AgentBehavior.SEEK_ALTERNATIVE_EXIT,
        ),
    ]
    model = SocialForceModel()
    model.step(agents, dt=0.1)  # exception fırlatmamalı


# ----------------------------------------------------------------------- #
# CongestionAwareRouter
# ----------------------------------------------------------------------- #


def _exit_graph() -> NavGraph:
    g = NavGraph()
    g.add_node("start", Point2D(10, 0))
    g.add_node("exitA", Point2D(0, 0))
    g.add_node("exitB", Point2D(20, 0))
    g.add_edge("start", "exitA", cost=10.0)
    g.add_edge("start", "exitB", cost=10.0)
    return g


def test_congestion_router_captures_baseline_on_init():
    g = _exit_graph()
    router = CongestionAwareRouter(graph=g, cell_size=1.0)
    assert router._base_costs[("start", "exitA")] == 10.0
    assert router._base_costs[("start", "exitB")] == 10.0


def test_congestion_router_penalizes_crowded_exit_more():
    g = _exit_graph()
    router = CongestionAwareRouter(graph=g, cell_size=1.0, congestion_coefficient=1.0)
    crowd_near_b = spawn_random_agents(
        40, Point2D(19.5, 0), Point2D(20.5, 1), Point2D(0, 0), seed=1
    )
    router.apply_density_penalty(crowd_near_b)
    assert g.edge_cost("start", "exitB") > g.edge_cost("start", "exitA")


def test_congestion_router_penalty_is_not_cumulative_across_refreshes():
    """Roadmap ilkesi: her `refresh` baseline'dan hesaplanır, üst üste
    katlanıp sonsuza gitmemeli."""
    g = _exit_graph()
    router = CongestionAwareRouter(graph=g, cell_size=1.0, congestion_coefficient=1.0)
    crowd_near_b = spawn_random_agents(
        40, Point2D(19.5, 0), Point2D(20.5, 1), Point2D(0, 0), seed=1
    )
    router.apply_density_penalty(crowd_near_b)
    first_cost = g.edge_cost("start", "exitB")
    router.apply_density_penalty(crowd_near_b)  # aynı yoğunlukla tekrar
    second_cost = g.edge_cost("start", "exitB")
    assert first_cost == pytest.approx(second_cost)


def test_congestion_router_reset_restores_baseline():
    g = _exit_graph()
    router = CongestionAwareRouter(graph=g, cell_size=1.0, congestion_coefficient=2.0)
    crowd = spawn_random_agents(40, Point2D(19.5, 0), Point2D(20.5, 1), Point2D(0, 0), seed=1)
    router.apply_density_penalty(crowd)
    assert g.edge_cost("start", "exitB") != 10.0
    router.reset()
    assert g.edge_cost("start", "exitB") == 10.0
    assert g.edge_cost("start", "exitA") == 10.0


# ----------------------------------------------------------------------- #
# refresh_congestion_aware_routes — uçtan uca kalabalık-kaçınma
# ----------------------------------------------------------------------- #


def test_refresh_congestion_aware_routes_steers_agents_to_emptier_exit():
    g = _exit_graph()
    router = CongestionAwareRouter(graph=g, cell_size=1.0, congestion_coefficient=1.0)
    agents = spawn_random_agents(50, Point2D(19.5, 0), Point2D(20.5, 1), Point2D(999, 999), seed=3)

    def node_of_agent(_agent):
        return "start"

    refresh_congestion_aware_routes(agents, router, ["exitA", "exitB"], node_of_agent)

    # Kalabalık kendi konumuna (exitB) yakın olsa da, yoğunluk cezası
    # yüzünden A* artık daha ucuz olan exitA'yı seçmeli.
    assert all(agent.goal.x == 0 for agent in agents if not agent.evacuated)
    assert any(agent.behavior == AgentBehavior.AVOID_CROWDED_EXIT for agent in agents)


def test_refresh_congestion_aware_routes_no_tag_when_disabled():
    g = _exit_graph()
    router = CongestionAwareRouter(graph=g, cell_size=1.0, congestion_coefficient=1.0)
    agents = spawn_random_agents(50, Point2D(19.5, 0), Point2D(20.5, 1), Point2D(0, 0), seed=3)

    def node_of_agent(_agent):
        return "start"

    refresh_congestion_aware_routes(
        agents,
        router,
        ["exitA", "exitB"],
        node_of_agent,
        tag_avoiding_agents=False,
    )
    assert all(agent.behavior == AgentBehavior.NORMAL for agent in agents)


# ----------------------------------------------------------------------- #
# close_exit_and_seek_alternative
# ----------------------------------------------------------------------- #


def test_close_exit_and_seek_alternative_reroutes_and_tags():
    g = _exit_graph()
    agents = spawn_random_agents(10, Point2D(9, -1), Point2D(11, 1), Point2D(0, 0), seed=5)

    def node_of_agent(_agent):
        return "start"

    close_exit_and_seek_alternative(
        agents,
        g,
        closed_exit_edges=[("start", "exitA")],
        remaining_exits=["exitB"],
        node_of_agent=node_of_agent,
    )
    assert all(agent.goal.x == 20 for agent in agents)
    assert all(agent.behavior == AgentBehavior.SEEK_ALTERNATIVE_EXIT for agent in agents)
    # Kenar silinmedi, yalnızca bloke edildi (geri açılabilir).
    assert g.has_node("exitA")
    assert g.edge_cost("start", "exitA") is None  # bloke -> None döner
    g.set_blocked("start", "exitA", blocked=False)
    assert g.edge_cost("start", "exitA") is not None  # geri açıldı


# ----------------------------------------------------------------------- #
# EvacuationSimulator.run(on_step=...) — periyodik kanca
# ----------------------------------------------------------------------- #


def test_evacuation_simulator_on_step_called_each_tick():
    agents = [Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(3, 0))]
    calls: list[float] = []

    def on_step(elapsed, _agents):
        calls.append(elapsed)

    sim = EvacuationSimulator(SocialForceModel())
    result = sim.run(agents, dt=0.1, max_time_s=30.0, on_step=on_step)
    assert result.evacuated_count == 1
    assert len(calls) > 0
    assert calls[-1] == pytest.approx(result.evacuation_time_s)


def test_evacuation_simulator_backward_compatible_without_on_step():
    agents = [Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(3, 0))]
    sim = EvacuationSimulator(SocialForceModel())
    result = sim.run(agents, dt=0.1, max_time_s=30.0)
    assert result.evacuated_count == 1


def test_evacuation_simulator_on_step_periodic_congestion_reroute_integration():
    """Uçtan uca: `on_step` kancasıyla periyodik olarak
    `refresh_congestion_aware_routes` çağrılan bir tahliye, kalabalık bir
    çıkışı zamanla terk edip alternatife yönelmelidir - roadmap'in
    "periyodik olarak yeniden çağrılmalı" notunun somut entegrasyonu."""
    g = _exit_graph()
    router = CongestionAwareRouter(graph=g, cell_size=1.0, congestion_coefficient=1.0)
    agents = spawn_random_agents(30, Point2D(19.5, 0), Point2D(20.5, 1), Point2D(0, 0), seed=9)

    def node_of_agent(_agent):
        return "start"

    # Başlangıçta agent'lar exitB'ye (0 maliyet farkı ile ilk seçilen) yakın
    # spawn edildi; on_step her adımda yeniden değerlendirsin.
    state = {"last_reroute": -999.0}

    def on_step(elapsed, current_agents):
        if elapsed - state["last_reroute"] >= 1.0:
            refresh_congestion_aware_routes(
                current_agents, router, ["exitA", "exitB"], node_of_agent
            )
            state["last_reroute"] = elapsed

    sim = EvacuationSimulator(SocialForceModel())
    result = sim.run(agents, dt=0.1, max_time_s=60.0, on_step=on_step)
    assert result.total_agents == 30
    # Yoğunluk cezası uygulandıktan sonra rotalar exitA'ya kaymış olmalı.
    assert any(a.goal.x == 0 for a in agents)
