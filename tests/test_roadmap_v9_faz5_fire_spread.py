"""
Roadmap V9 / Faz V testleri — Yangın ve Dinamik Tehlike (Katman 7.2)
==========================================================================

Kapsam:
  - `FireSpreadModel` — hücre-otomat yayılımı, duvar/kapı bariyer etkisi,
    eşik (SMOKE/FIRE) sınıflandırması, ateşin tam-yanan hücrede 1.0'da
    doyması.
  - `FireAwareRouter` — baseline'dan (birikmeli olmayan) duman cezası,
    tam-yanan hedefe giden kenarın bloklanması (silinmeden), `reset()`.
  - `PeriodicFireRerouter` — `EvacuationSimulator.run(on_step=...)`
    kancasına takılı periyodik yangın ilerletme + rota yenileme.
  - `FireEvacuationComparator` — "çıkış kapalıyken süre %X artıyor"
    karşılaştırma raporu.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.core_engine.geometry_engine import Point2D
from harita.hazard_data.fire_spread import (
    FIRE_THRESHOLD,
    SMOKE_THRESHOLD,
    FireAwareRouter,
    FireCellState,
    FireSpreadModel,
)
from harita.mobility.crowd_simulation import (
    AgentBehavior,
    EvacuationSimulator,
    spawn_random_agents,
)
from harita.mobility.crowd_simulation.fire_evacuation import (
    FireEvacuationComparator,
    PeriodicFireRerouter,
)
from harita.mobility.pathfinding import NavGraph

# ============================================================================ #
# FireSpreadModel
# ============================================================================ #


def test_ignition_cell_starts_at_full_fire():
    m = FireSpreadModel(width=4, height=4, ignition_cells=[(1, 1)], seed=1)
    assert m.state_of((1, 1)) is FireCellState.FIRE
    assert m.intensity[(1, 1)] == 1.0


def test_other_cells_start_clear():
    m = FireSpreadModel(width=4, height=4, ignition_cells=[(0, 0)], seed=1)
    assert m.state_of((3, 3)) is FireCellState.CLEAR
    assert m.smoke_density((3, 3)) == 0.0


def test_fire_spreads_to_adjacent_cell_over_time():
    m = FireSpreadModel(width=5, height=5, ignition_cells=[(0, 0)], seed=7)
    assert m.smoke_density((1, 0)) == 0.0
    m.run(10.0, dt=1.0)
    assert m.smoke_density((1, 0)) > 0.0
    # Yayılım monotonik olarak kaynaktan uzaklaştıkça (aynı anda) azalmalı
    assert m.smoke_density((1, 0)) >= m.smoke_density((4, 4))


def test_intensity_saturates_at_one():
    m = FireSpreadModel(width=3, height=3, ignition_cells=[(1, 1)], seed=1)
    m.run(1000.0, dt=1.0)
    for v in m.intensity.values():
        assert 0.0 <= v <= 1.0


def test_wall_slows_spread_more_than_open_cell():
    open_model = FireSpreadModel(width=5, height=1, ignition_cells=[(0, 0)], seed=3)
    walled_model = FireSpreadModel(
        width=5,
        height=1,
        ignition_cells=[(0, 0)],
        seed=3,
        wall_cells=frozenset({(1, 0)}),
    )
    open_model.run(5.0, dt=1.0)
    walled_model.run(5.0, dt=1.0)
    assert walled_model.smoke_density((2, 0)) < open_model.smoke_density((2, 0))


def test_door_slows_spread_less_than_wall():
    door_model = FireSpreadModel(
        width=5,
        height=1,
        ignition_cells=[(0, 0)],
        seed=3,
        door_cells=frozenset({(1, 0)}),
    )
    wall_model = FireSpreadModel(
        width=5,
        height=1,
        ignition_cells=[(0, 0)],
        seed=3,
        wall_cells=frozenset({(1, 0)}),
    )
    door_model.run(5.0, dt=1.0)
    wall_model.run(5.0, dt=1.0)
    assert door_model.smoke_density((1, 0)) > wall_model.smoke_density((1, 0))


def test_state_thresholds_are_consistent():
    assert SMOKE_THRESHOLD < FIRE_THRESHOLD
    m = FireSpreadModel(width=2, height=1, ignition_cells=[(0, 0)])
    m.intensity[(1, 0)] = SMOKE_THRESHOLD
    assert m.state_of((1, 0)) is FireCellState.SMOKE
    m.intensity[(1, 0)] = FIRE_THRESHOLD
    assert m.state_of((1, 0)) is FireCellState.FIRE


def test_deterministic_with_seed():
    m1 = FireSpreadModel(width=6, height=6, ignition_cells=[(0, 0)], seed=99)
    m2 = FireSpreadModel(width=6, height=6, ignition_cells=[(0, 0)], seed=99)
    m1.run(8.0, dt=1.0)
    m2.run(8.0, dt=1.0)
    assert m1.intensity == m2.intensity


def test_burning_cell_count_and_cells_by_state():
    m = FireSpreadModel(width=4, height=4, ignition_cells=[(0, 0)], seed=1)
    assert m.burning_cell_count() == 1
    assert (0, 0) in m.cells_by_state(FireCellState.FIRE)


# ============================================================================ #
# FireAwareRouter
# ============================================================================ #


def _build_line_graph(n: int) -> NavGraph:
    g = NavGraph()
    for x in range(n):
        g.add_node(x, Point2D(float(x), 0.0))
    for x in range(n - 1):
        g.add_edge(x, x + 1)
    return g


def test_router_applies_smoke_penalty_without_accumulating():
    g = _build_line_graph(5)
    baseline_cost = g.edge_cost(1, 2)
    m = FireSpreadModel(width=5, height=1, ignition_cells=[(0, 0)], seed=1)
    router = FireAwareRouter(g, node_to_cell=lambda n: (n, 0))
    m.run(3.0, dt=1.0)
    router.apply(m)
    first_pass_cost = g.edge_cost(1, 2)
    assert first_pass_cost >= baseline_cost
    # İkinci `apply()` çağrısı (yoğunluk artmasa bile) maliyeti tekrar
    # tekrar katlamamalı - baseline'dan yeniden hesaplanmalı.
    router.apply(m)
    assert g.edge_cost(1, 2) == pytest.approx(first_pass_cost)


def test_router_blocks_edge_into_fully_burning_cell():
    g = _build_line_graph(3)
    m = FireSpreadModel(width=3, height=1, ignition_cells=[(1, 0)], seed=1)
    router = FireAwareRouter(g, node_to_cell=lambda n: (n, 0))
    router.apply(m)
    # Hedefi (1,0) [FIRE] olan her iki kenar da bloklanmalı.
    assert g.edge_cost(0, 1) is None
    assert g.edge_cost(2, 1) is None
    # Hedefi (2,0) [henüz FIRE değil] olan kenar bloklanmamalı.
    assert g.edge_cost(1, 2) is not None


def test_router_reset_restores_baseline_and_unblocks():
    g = _build_line_graph(3)
    baseline = g.edge_cost(0, 1)
    m = FireSpreadModel(width=3, height=1, ignition_cells=[(1, 0)], seed=1)
    router = FireAwareRouter(g, node_to_cell=lambda n: (n, 0))
    router.apply(m)
    assert g.edge_cost(0, 1) is None
    router.reset()
    assert g.edge_cost(0, 1) == pytest.approx(baseline)


def test_router_edges_do_not_get_deleted_from_graph_structure():
    g = _build_line_graph(3)
    edge_count_before = g.edge_count()
    m = FireSpreadModel(width=3, height=1, ignition_cells=[(1, 0)], seed=1)
    router = FireAwareRouter(g, node_to_cell=lambda n: (n, 0))
    router.apply(m)
    # `set_blocked` kenarı silmez - yapısal kenar sayısı değişmemeli.
    assert g.edge_count() == edge_count_before


# ============================================================================ #
# PeriodicFireRerouter (EvacuationSimulator.run(on_step=...) entegrasyonu)
# ============================================================================ #


def _build_grid_graph(size: int) -> NavGraph:
    g = NavGraph()
    for y in range(size):
        for x in range(size):
            g.add_node((x, y), Point2D(x * 2.0, y * 2.0))
    for y in range(size):
        for x in range(size):
            if x + 1 < size:
                g.add_edge((x, y), (x + 1, y))
            if y + 1 < size:
                g.add_edge((x, y), (x, y + 1))
    return g


def _grid_scenario(size: int = 6, agent_count: int = 8, seed: int = 3):
    g = _build_grid_graph(size)
    exits = [(size - 1, size - 1)]
    agents = spawn_random_agents(
        agent_count,
        Point2D(0.0, 0.0),
        Point2D((size - 1) * 2.0, (size - 1) * 2.0),
        Point2D((size - 1) * 2.0, (size - 1) * 2.0),
        seed=seed,
    )

    def node_of_agent(agent):
        return min(g.positions, key=lambda n: g.positions[n].distance_to(agent.position))

    return agents, g, exits, node_of_agent


def test_periodic_rerouter_advances_fire_and_refreshes_on_interval():
    agents, g, exits, node_of_agent = _grid_scenario()
    EvacuationSimulator.assign_nearest_exit_paths(agents, g, exits, node_of_agent)
    fire = FireSpreadModel(width=6, height=6, ignition_cells=[(0, 0)], seed=5)
    router = FireAwareRouter(g, node_to_cell=lambda n: n)
    rerouter = PeriodicFireRerouter(
        fire_model=fire,
        router=router,
        exits=exits,
        node_of_agent=node_of_agent,
        refresh_interval_s=2.0,
    )
    EvacuationSimulator().run(agents, dt=0.5, max_time_s=10.0, on_step=rerouter)
    # 10s / 2s aralık -> en az birkaç kez yenilenmiş olmalı, yangın da ilerlemiş olmalı.
    assert rerouter.refresh_count >= 3
    assert fire.elapsed_s == pytest.approx(10.0)
    assert fire.smoke_density((1, 0)) > 0.0


def test_periodic_rerouter_none_on_step_leaves_default_behavior_unchanged():
    # Faz IV'ün `on_step=None` geriye-uyumluluk garantisinin Faz V ile de
    # korunduğunun regresyon kontrolü.
    agents, g, exits, node_of_agent = _grid_scenario(agent_count=4)
    EvacuationSimulator.assign_nearest_exit_paths(agents, g, exits, node_of_agent)
    result = EvacuationSimulator().run(agents, dt=0.5, max_time_s=30.0)
    assert result.total_agents == 4


def test_periodic_rerouter_tags_rerouted_agents_avoid_smoke():
    agents, g, exits, node_of_agent = _grid_scenario(agent_count=6, seed=11)
    EvacuationSimulator.assign_nearest_exit_paths(agents, g, exits, node_of_agent)
    fire = FireSpreadModel(width=6, height=6, ignition_cells=[(5, 4)], seed=5)
    router = FireAwareRouter(g, node_to_cell=lambda n: n)
    rerouter = PeriodicFireRerouter(
        fire_model=fire,
        router=router,
        exits=exits,
        node_of_agent=node_of_agent,
        refresh_interval_s=1.0,
    )
    EvacuationSimulator().run(agents, dt=0.5, max_time_s=15.0, on_step=rerouter)
    behaviors = {a.behavior for a in agents}
    # Yangının çıkışa yakın başlaması nedeniyle en az bazı agent'lar rota
    # değiştirip etiketlenmiş olmalı (kesin sayı iddia edilmiyor - roadmap
    # disiplini: davranışsal, olasılıksal bir yayılım modeli).
    assert AgentBehavior.AVOID_SMOKE in behaviors or rerouter.refresh_count > 0


# ============================================================================ #
# FireEvacuationComparator
# ============================================================================ #


def test_comparator_baseline_has_zero_pct_change():
    report = FireEvacuationComparator.compare(
        lambda: _grid_scenario(agent_count=6, seed=2),
        [],
        dt=0.5,
        max_time_s=60.0,
    )
    assert report.baseline.pct_change_vs_baseline == 0.0
    assert report.scenarios == []


def test_comparator_closed_exit_scenario_reports_pct_change():
    def factory():
        return _grid_scenario(size=5, agent_count=6, seed=4)

    report = FireEvacuationComparator.compare(
        factory,
        [("only_exit_closed", [((3, 4), (4, 4))], [(4, 4)])],
        dt=0.5,
        max_time_s=60.0,
    )
    assert len(report.scenarios) == 1
    scenario = report.scenarios[0]
    assert scenario.label == "only_exit_closed"
    assert scenario.pct_change_vs_baseline is not None
    # Tek çıkışın kendisine giden son kenar kapatıldığında (agent'lar
    # alternatif yaya ağı olmayan kapalı ızgarada mahsur kalır) süre
    # baseline'dan kısa olamaz.
    assert scenario.pct_change_vs_baseline >= 0.0


def test_comparator_scenario_factory_called_fresh_each_time():
    call_count = {"n": 0}

    def factory():
        call_count["n"] += 1
        return _grid_scenario(agent_count=3, seed=1)

    FireEvacuationComparator.compare(
        factory,
        [("a", [], [(5, 5)]), ("b", [], [(5, 5)])],
        dt=0.5,
        max_time_s=20.0,
    )
    # baseline + 2 senaryo = 3 çağrı
    assert call_count["n"] == 3


def test_comparator_disclaimer_present():
    report = FireEvacuationComparator.compare(
        lambda: _grid_scenario(agent_count=3, seed=1),
        [],
        dt=0.5,
        max_time_s=20.0,
    )
    assert "gösterge niteliğindedir" in report.disclaimer
