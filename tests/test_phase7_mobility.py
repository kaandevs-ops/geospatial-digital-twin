"""Phase 7 (Mobility) için birim testleri."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction.room_generator import Room, RoomType
from harita.building_reconstruction.building_elements import Stair, ElevatorCore

from harita.mobility.pathfinding import (
    AStar, Dijkstra, JumpPointSearch, NavGraph, ThetaStar,
)
from harita.mobility.indoor_navigation import Floor, IndoorNavigationBuilder
from harita.mobility.crowd_simulation import (
    Agent, AgentBehavior, EvacuationSimulator, OccupancyHeatmap,
    SocialForceModel, spawn_random_agents,
)
from harita.mobility.traffic_simulation import (
    IDMModel, TrafficAgent, TrafficSimulator, VehicleType, build_route,
)


# ============================================================================ #
# NavGraph
# ============================================================================ #

def test_navgraph_basic_add_and_neighbors():
    g = NavGraph()
    g.add_node("a", Point2D(0, 0))
    g.add_node("b", Point2D(1, 0))
    g.add_edge("a", "b")
    neighbors = dict(g.neighbors("a"))
    assert "b" in neighbors
    assert math.isclose(neighbors["b"], 1.0)
    assert g.node_count() == 2
    assert g.edge_count() == 2  # bidirectional


def test_navgraph_missing_node_raises():
    g = NavGraph()
    g.add_node("a", Point2D(0, 0))
    try:
        g.add_edge("a", "b")
        assert False, "beklenen KeyError fırlatılmadı"
    except KeyError:
        pass


def test_navgraph_blocked_edge_excluded():
    g = NavGraph()
    g.add_node("a", Point2D(0, 0))
    g.add_node("b", Point2D(1, 0))
    g.add_edge("a", "b")
    g.set_blocked("a", "b")
    assert dict(g.neighbors("a")) == {}
    g.set_blocked("a", "b", blocked=False)
    assert "b" in dict(g.neighbors("a"))


def test_navgraph_from_grid_open():
    g = NavGraph.from_grid(4, 4)
    assert g.node_count() == 16
    result = AStar.find_path(g, (0, 0), (3, 3))
    assert result.found
    assert result.path[0] == (0, 0)
    assert result.path[-1] == (3, 3)


def test_navgraph_from_grid_with_blocked_wall():
    blocked = {(x, 2) for x in range(3)}  # y=2 satırının bir kısmı duvar
    g = NavGraph.from_grid(5, 5, blocked_cells=blocked, diagonal=False)
    result = AStar.find_path(g, (0, 0), (0, 4))
    assert result.found
    # doğrudan geçemez, dolanmalı
    assert result.cost > 4.0


# ============================================================================ #
# A* / Dijkstra / ThetaStar
# ============================================================================ #

def test_astar_finds_optimal_path_on_simple_line():
    g = NavGraph()
    for i in range(5):
        g.add_node(i, Point2D(float(i), 0.0))
    for i in range(4):
        g.add_edge(i, i + 1)
    result = AStar.find_path(g, 0, 4)
    assert result.found
    assert result.path == [0, 1, 2, 3, 4]
    assert math.isclose(result.cost, 4.0)


def test_astar_no_path_returns_not_found():
    g = NavGraph()
    g.add_node("a", Point2D(0, 0))
    g.add_node("b", Point2D(5, 5))
    result = AStar.find_path(g, "a", "b")
    assert not result.found
    assert result.path == []
    assert result.cost == math.inf


def test_astar_same_start_goal():
    g = NavGraph()
    g.add_node("a", Point2D(0, 0))
    result = AStar.find_path(g, "a", "a")
    assert result.found
    assert result.path == ["a"]
    assert result.cost == 0.0


def test_dijkstra_matches_astar_cost():
    g = NavGraph.from_grid(6, 6)
    a = AStar.find_path(g, (0, 0), (5, 5))
    d = Dijkstra.find_path(g, (0, 0), (5, 5))
    assert a.found and d.found
    assert math.isclose(a.cost, d.cost, rel_tol=1e-9)


def test_dijkstra_shortest_paths_from():
    g = NavGraph()
    for i in range(4):
        g.add_node(i, Point2D(float(i), 0.0))
    for i in range(3):
        g.add_edge(i, i + 1)
    dist = Dijkstra.shortest_paths_from(g, 0)
    assert dist[0] == 0.0
    assert math.isclose(dist[3], 3.0)


def test_jump_point_search_matches_astar_cost_on_open_grid():
    g = NavGraph.from_grid(10, 10)
    a = AStar.find_path(g, (0, 0), (9, 9))
    j = JumpPointSearch.find_path(g, (0, 0), (9, 9))
    assert a.found and j.found
    assert math.isclose(a.cost, j.cost, rel_tol=1e-6)
    # JPS aynı grid üzerinde eşit veya daha az düğüm genişletmeli
    assert j.expanded_nodes <= a.expanded_nodes


def test_jump_point_search_fallback_on_non_grid_graph():
    g = NavGraph()
    g.add_node("a", Point2D(0, 0))
    g.add_node("b", Point2D(1, 0))
    g.add_edge("a", "b")
    result = JumpPointSearch.find_path(g, "a", "b")
    assert result.found
    assert result.path == ["a", "b"]


def test_theta_star_produces_valid_and_not_longer_path():
    g = NavGraph.from_grid(8, 8)
    a = AStar.find_path(g, (0, 0), (7, 7))
    t = ThetaStar.find_path(g, (0, 0), (7, 7))
    assert a.found and t.found
    assert t.path[0] == (0, 0)
    assert t.path[-1] == (7, 7)
    # any-angle kısaltma A*'dan daha uzun olmamalı (üst sınır toleransıyla)
    assert t.cost <= a.cost + 1e-6


# ============================================================================ #
# Indoor Navigation
# ============================================================================ #

def _rect_room(room_id, x0, y0, x1, y1, room_type=RoomType.OFIS, neighbors=None):
    poly = Polygon(points=[Point2D(x0, y0), Point2D(x1, y0), Point2D(x1, y1), Point2D(x0, y1)])
    return Room(polygon=poly, room_type=room_type.value, room_id=room_id,
                neighbors=neighbors or [])


def test_floor_to_nav_graph_respects_adjacency():
    r1 = _rect_room(1, 0, 0, 4, 4, neighbors=[2])
    r2 = _rect_room(2, 4, 0, 8, 4, neighbors=[1])
    r3 = _rect_room(3, 8, 0, 12, 4, neighbors=[])  # izole oda
    floor = Floor(floor_index=0, rooms=[r1, r2, r3])
    graph = floor.to_nav_graph()
    assert graph.node_count() == 3
    assert 2 in dict(graph.neighbors(1))
    assert dict(graph.neighbors(3)) == {}


def test_indoor_navigation_connects_two_floors_via_stair():
    r1 = _rect_room(1, 0, 0, 4, 4)
    floor0 = Floor(floor_index=0, rooms=[r1], stairs=[Stair(
        position=Point2D(2, 2), width=1.2, run_length=3.0, step_count=15,
        step_height=0.18, step_depth=0.28)])
    r2 = _rect_room(2, 0, 0, 4, 4)
    floor1 = Floor(floor_index=1, rooms=[r2])

    result = IndoorNavigationBuilder.build([floor0, floor1], floor_height=3.0)
    assert result.floor_count == 2
    path = AStar.find_path(result.graph, (0, 1), (1, 2))
    assert path.found
    assert path.path == [(0, 1), (1, 2)]
    assert path.cost > 3.0  # merdiven maliyeti çarpanı uygulanmış olmalı


def test_indoor_navigation_elevator_cheaper_than_stair():
    r1 = _rect_room(1, 0, 0, 4, 4)
    r2 = _rect_room(2, 0, 0, 4, 4)
    stair = Stair(position=Point2D(2, 2), width=1.2, run_length=3.0,
                   step_count=15, step_height=0.18, step_depth=0.28)
    elevator = ElevatorCore(position=Point2D(2, 2), width=2.0, depth=2.0,
                              shaft_top_z=3.0, shaft_bottom_z=0.0)

    floor_stair = Floor(floor_index=0, rooms=[r1], stairs=[stair])
    floor_target = Floor(floor_index=1, rooms=[r2])
    stair_only = IndoorNavigationBuilder.build([floor_stair, floor_target], floor_height=3.0)
    stair_cost = AStar.find_path(stair_only.graph, (0, 1), (1, 2)).cost

    floor_elevator = Floor(floor_index=0, rooms=[r1], elevators=[elevator])
    elevator_only = IndoorNavigationBuilder.build([floor_elevator, floor_target], floor_height=3.0)
    elevator_cost = AStar.find_path(elevator_only.graph, (0, 1), (1, 2)).cost

    assert elevator_cost < stair_cost


def test_indoor_navigation_nearest_node():
    r1 = _rect_room(1, 0, 0, 4, 4)
    floor = Floor(floor_index=0, rooms=[r1])
    result = IndoorNavigationBuilder.build([floor])
    node = IndoorNavigationBuilder.nearest_node(result, 0, Point2D(1, 1))
    assert node == (0, 1)
    assert IndoorNavigationBuilder.nearest_node(result, 5, Point2D(0, 0)) is None


# ============================================================================ #
# Crowd Simulation
# ============================================================================ #

def test_agent_moves_toward_goal_under_social_force():
    agent = Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(10, 0))
    model = SocialForceModel()
    initial_dist = agent.position.distance_to(agent.goal)
    for _ in range(50):
        model.step([agent], dt=0.1)
    assert agent.position.distance_to(agent.goal) < initial_dist


def test_agents_repel_each_other_and_dont_overlap():
    a = Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(0, 0), radius_m=0.3)
    b = Agent(agent_id=1, position=Point2D(0.1, 0.0), goal=Point2D(0.1, 0.0), radius_m=0.3)
    model = SocialForceModel()
    for _ in range(30):
        model.step([a, b], dt=0.05)
    dist = a.position.distance_to(b.position)
    assert dist > 0.1  # itilmiş olmalılar (başlangıçtan uzaklaşmış)


def test_panic_behavior_has_higher_effective_speed():
    calm = Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(1, 0), behavior=AgentBehavior.NORMAL)
    panicked = Agent(agent_id=1, position=Point2D(0, 0), goal=Point2D(1, 0), behavior=AgentBehavior.PANIC)
    assert panicked.effective_desired_speed() > calm.effective_desired_speed()


def test_evacuation_simulator_evacuates_agents_through_graph():
    g = NavGraph()
    for i in range(6):
        g.add_node(i, Point2D(float(i) * 2.0, 0.0))
    for i in range(5):
        g.add_edge(i, i + 1)

    agents = [Agent(agent_id=i, position=Point2D(0.0, 0.0), goal=Point2D(0, 0))
              for i in range(3)]

    def node_of_agent(agent):
        return 0

    EvacuationSimulator.assign_nearest_exit_paths(agents, g, exits=[5], node_of_agent=node_of_agent)
    for agent in agents:
        assert agent.path
        assert agent.goal == Point2D(10.0, 0.0)

    sim = EvacuationSimulator()
    result = sim.run(agents, dt=0.2, max_time_s=120.0)
    assert result.total_agents == 3
    assert result.evacuated_count == 3
    assert not result.timed_out
    assert result.evacuation_time_s > 0


def test_occupancy_heatmap_counts_agents_per_cell():
    agents = [Agent(agent_id=0, position=Point2D(0.5, 0.5), goal=Point2D(0, 0)),
              Agent(agent_id=1, position=Point2D(0.6, 0.6), goal=Point2D(0, 0)),
              Agent(agent_id=2, position=Point2D(5.5, 5.5), goal=Point2D(0, 0))]
    heatmap = OccupancyHeatmap.compute(agents, cell_size=1.0)
    assert heatmap[(0, 0)] == 2
    assert heatmap[(5, 5)] == 1
    top_cell, count = OccupancyHeatmap.max_density_cell(heatmap)
    assert top_cell == (0, 0)
    assert count == 2


def test_spawn_random_agents_deterministic_with_seed():
    a1 = spawn_random_agents(5, Point2D(0, 0), Point2D(10, 10), Point2D(5, 5), seed=42)
    a2 = spawn_random_agents(5, Point2D(0, 0), Point2D(10, 10), Point2D(5, 5), seed=42)
    assert [p.position for p in a1] == [p.position for p in a2]
    assert len(a1) == 5


# ============================================================================ #
# Traffic Simulation
# ============================================================================ #

def _straight_road_graph(length_km_nodes=10):
    g = NavGraph()
    for i in range(length_km_nodes):
        g.add_node(i, Point2D(float(i) * 20.0, 0.0))  # 20m aralıklı düğümler
    for i in range(length_km_nodes - 1):
        g.add_edge(i, i + 1)
    return g


def test_build_route_creates_traffic_agent():
    g = _straight_road_graph()
    agent = build_route(g, 0, 9, VehicleType.ARAC, agent_id=1)
    assert agent is not None
    assert agent.route_nodes == list(range(10))
    assert math.isclose(agent.route_length(), 180.0)


def test_build_route_returns_none_when_unreachable():
    g = NavGraph()
    g.add_node("a", Point2D(0, 0))
    g.add_node("b", Point2D(5, 5))
    agent = build_route(g, "a", "b", VehicleType.ARAC, agent_id=1)
    assert agent is None


def test_idm_single_vehicle_accelerates_to_desired_speed():
    g = _straight_road_graph()
    agent = build_route(g, 0, 9, VehicleType.ARAC, agent_id=1)
    for _ in range(300):
        IDMModel.step([agent], dt=0.1)
        if agent.arrived:
            break
    assert agent.arrived
    assert agent.distance_along_route > 0


def test_idm_follower_keeps_safe_distance_from_leader():
    g = _straight_road_graph(length_km_nodes=30)
    leader = build_route(g, 0, 29, VehicleType.ARAC, agent_id=1)
    follower = build_route(g, 0, 29, VehicleType.ARAC, agent_id=2)
    leader.distance_along_route = 10.0
    follower.distance_along_route = 0.0

    for _ in range(200):
        IDMModel.step([leader, follower], dt=0.1)

    gap = leader.distance_along_route - follower.distance_along_route
    assert gap > 0  # takip eden hiçbir zaman öndekini geçmemeli


def test_traffic_simulator_arrived_count():
    g = _straight_road_graph(length_km_nodes=5)
    sim = TrafficSimulator()
    for i in range(3):
        agent = build_route(g, 0, 4, VehicleType.YAYA, agent_id=i)
        sim.add_agent(agent, route_key="main")

    for _ in range(2000):
        sim.step(dt=0.1)
        if sim.arrived_count() == 3:
            break

    assert sim.arrived_count() == 3


def test_vehicle_types_have_distinct_idm_defaults():
    from harita.mobility.traffic_simulation import VEHICLE_IDM_DEFAULTS
    assert VEHICLE_IDM_DEFAULTS[VehicleType.OTOBUS].vehicle_length > \
           VEHICLE_IDM_DEFAULTS[VehicleType.BISIKLET].vehicle_length
    assert VEHICLE_IDM_DEFAULTS[VehicleType.YAYA].desired_speed < \
           VEHICLE_IDM_DEFAULTS[VehicleType.ARAC].desired_speed
