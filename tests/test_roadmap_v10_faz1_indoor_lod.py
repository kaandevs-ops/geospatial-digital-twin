"""
Roadmap V10 / Faz 1.2, 1.3, 1.4 — regresyon testleri.

Kapsanan roadmap maddeleri:
- 1.2 — `SocialForceModel` <-> `BuildingNavGraph` gerçek entegrasyonu:
  spawn = kat+oda, rota = A* ile merdiven/asansör düğümlerinden geçen
  gerçek graf yolu; `floor_index`/`room_id`/`z_m` rota ilerledikçe
  otomatik güncellenir.
- 1.3 — `MobilityProfile` gerçek rota kısıtı: tekerlekli sandalye profili
  merdiven kenarlarını hiç kullanamaz; asansör de kapalıysa gerçekten
  sıkışır (rota bulunamaz).
- 1.4 — LOD ile bağlanma: uzak ajan ucuz kinematik yaklaşımla, yakın ajan
  tam `SocialForceModel` ile güncellenir; çok uzak (CULLED) ajan hiç
  güncellenmez.
"""

from __future__ import annotations

from harita.building_reconstruction.building_elements import ElevatorCore, Stair
from harita.building_reconstruction.room_generator import Room, RoomType
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mobility.crowd_simulation import (
    Agent,
    EvacuationSimulator,
    MobilityProfile,
)
from harita.mobility.indoor_navigation import Floor, IndoorNavigationBuilder
from harita.performance.simulation_lod import (
    SimulationLODLevel,
    SimulationLODManager,
    SimulationLODMode,
)


def _rect_room(room_id, x0, y0, x1, y1, room_type=RoomType.OFIS, neighbors=None):
    poly = Polygon(points=[Point2D(x0, y0), Point2D(x1, y0), Point2D(x1, y1), Point2D(x0, y1)])
    return Room(polygon=poly, room_type=room_type.value, room_id=room_id, neighbors=neighbors or [])


def _two_floor_building():
    """Zemin kat (oda 1 = merdiven tarafı, oda 3 = asansör tarafı) + 1. kat
    (oda 2 = merdiven tarafı, oda 4 = asansör tarafı) - merdiven ve
    asansör **farklı** oda çiftlerini bağlar (gerçekçi bina yerleşimi: iki
    ayrı dikey çekirdek), böylece Faz 1.3 testleri stair/elevator
    kenarlarını birbirinden ayrıştırabilir (aksi halde tek odalı bir kat
    modelinde ikisi de aynı graf kenarını üretir ve ayrım anlamsızlaşır)."""
    r1 = _rect_room(1, 0, 0, 4, 4, neighbors=[3])
    r3 = _rect_room(3, 10, 0, 14, 4, neighbors=[1])
    stair = Stair(
        position=Point2D(2, 2),
        width=1.2,
        run_length=3.0,
        step_count=15,
        step_height=0.18,
        step_depth=0.28,
    )
    elevator = ElevatorCore(
        position=Point2D(12, 2), width=2.0, depth=2.0, shaft_top_z=3.0, shaft_bottom_z=0.0
    )
    floor0 = Floor(floor_index=0, rooms=[r1, r3], stairs=[stair], elevators=[elevator])

    r2 = _rect_room(2, 0, 0, 4, 4, neighbors=[4])
    r4 = _rect_room(4, 10, 0, 14, 4, neighbors=[2])
    floor1 = Floor(floor_index=1, rooms=[r2, r4])
    return IndoorNavigationBuilder.build([floor0, floor1], floor_height=3.0)


class TestFaz1_2IndoorRouteIntegration:
    def test_agent_gets_real_graph_path_across_floors(self):
        building_graph = _two_floor_building()
        agent = Agent(agent_id=1, position=Point2D(1.0, 1.0), goal=Point2D(0, 0), floor_index=0)
        EvacuationSimulator.assign_building_exit_paths([agent], building_graph, exits=[(1, 2)])

        assert agent.path_nodes is not None
        assert agent.path_nodes[0] == (0, 1)
        assert agent.path_nodes[-1] == (1, 2)
        assert agent.path[-1] == building_graph.graph.positions[(1, 2)]

    def test_floor_room_z_sync_as_agent_advances(self):
        building_graph = _two_floor_building()
        agent = Agent(agent_id=1, position=Point2D(1.0, 1.0), goal=Point2D(0, 0), floor_index=0)
        EvacuationSimulator.assign_building_exit_paths([agent], building_graph, exits=[(1, 2)])
        assert agent.floor_index == 0
        assert agent.room_id == 1
        assert agent.z_m == 0.0

        # Rotada ilerlet (elle path_index'i son düğüme taşı) ve senkron
        # fonksiyonunun gerçekten okuduğunu doğrula.
        agent.path_index = len(agent.path_nodes) - 1
        agent.sync_vertical_state_from_path(building_graph)
        assert agent.floor_index == 1
        assert agent.room_id == 2
        assert agent.z_m == 3.0

    def test_run_with_building_graph_keeps_floor_state_current(self):
        building_graph = _two_floor_building()
        agent = Agent(
            agent_id=1,
            position=Point2D(1.0, 1.0),
            goal=Point2D(0, 0),
            floor_index=0,
            desired_speed=5.0,
        )
        EvacuationSimulator.assign_building_exit_paths([agent], building_graph, exits=[(1, 2)])

        sim = EvacuationSimulator()
        sim.run([agent], dt=0.1, max_time_s=30.0, building_graph=building_graph)

        # Ajan sonunda üst kattaki hedefe ulaşmış olmalı ve floor_index bunu
        # yansıtmalı (spawn anında bir kere yazılıp sonra bayatlamamalı).
        assert agent.evacuated
        assert agent.floor_index == 1
        assert agent.room_id == 2

    def test_2d_scenario_unaffected_when_no_building_graph(self):
        """`path_nodes=None` olan eski/2D senaryolarda hiçbir şey bozulmaz."""
        agent = Agent(agent_id=1, position=Point2D(0.0, 0.0), goal=Point2D(5.0, 0.0))
        assert agent.path_nodes is None
        agent.sync_vertical_state_from_path(building_graph=None)  # no-op, hata fırlatmamalı
        assert agent.floor_index == 0
        assert agent.room_id is None


class TestFaz1_3MobilityProfileRouteConstraint:
    def test_wheelchair_never_uses_stairs(self):
        building_graph = _two_floor_building()
        agent = Agent(
            agent_id=1,
            position=Point2D(1.0, 1.0),
            goal=Point2D(0, 0),
            floor_index=0,
            mobility_profile=MobilityProfile.WHEELCHAIR,
        )
        # Hem merdiven tarafına (1,2) hem asansör tarafına (1,4) çıkış
        # tanımlı - normal (yürüyen) bir agent merdiven tarafına daha
        # yakın olduğu için (1,2)'yi seçerdi; tekerlekli sandalye
        # profilinin bunu seçMEmesi (merdiven kullanamaması) gerçek kısıtı
        # kanıtlar.
        EvacuationSimulator.assign_building_exit_paths(
            [agent], building_graph, exits=[(1, 2), (1, 4)]
        )

        assert agent.path_nodes[-1] == (1, 4)
        # Rota, dikey geçiş için asansör kenarını kullanmalı, merdiven
        # kenarını asla kullanmamalı.
        used_edges = list(zip(agent.path_nodes, agent.path_nodes[1:]))
        assert ((0, 3), (1, 4)) in used_edges
        assert ((0, 1), (1, 2)) not in used_edges
        # block/unblock çağrıları simetrik olmalı - graf kalıcı olarak
        # bloke kalmamalı (bir sonraki normal-profil agent'ı etkilenmemeli).
        assert building_graph.stairs_blocked is False

    def test_wheelchair_gets_stuck_when_elevator_also_disabled(self):
        building_graph = _two_floor_building()
        building_graph.disable_elevators()  # ör. deprem senaryosu
        agent = Agent(
            agent_id=1,
            position=Point2D(1.0, 1.0),
            goal=Point2D(0, 0),
            floor_index=0,
            mobility_profile=MobilityProfile.WHEELCHAIR,
        )
        original_path = list(agent.path)
        EvacuationSimulator.assign_building_exit_paths([agent], building_graph, exits=[(1, 2)])

        # Roadmap kabul kriteri: "tekerlekli sandalye profili gerçekten
        # sıkışıyor" - sessizce merdivene geri düşmemeli, path boş/eski
        # halinde kalmalı.
        assert agent.path == original_path
        assert agent.path_nodes is None

    def test_walking_agent_still_uses_stairs_when_cheaper(self):
        building_graph = _two_floor_building()
        building_graph.disable_elevators()
        agent = Agent(
            agent_id=1,
            position=Point2D(1.0, 1.0),
            goal=Point2D(0, 0),
            floor_index=0,
            mobility_profile=MobilityProfile.WALKING,
        )
        EvacuationSimulator.assign_building_exit_paths([agent], building_graph, exits=[(1, 2)])

        # Yürüyen profil asansör kapalıyken bile merdivenle sıkışmadan
        # rota bulabilmeli.
        assert agent.path_nodes is not None
        assert agent.path_nodes[-1] == (1, 2)


class TestFaz1_4SimulationLODWiring:
    def test_culled_agent_never_moves(self):
        levels = (
            SimulationLODLevel(max_distance=5.0, mode=SimulationLODMode.FULL),
            SimulationLODLevel(max_distance=10.0, mode=SimulationLODMode.AGGREGATE),
            SimulationLODLevel(max_distance=float("inf"), mode=SimulationLODMode.CULLED),
        )
        lod_manager = SimulationLODManager(levels=levels)
        far_agent = Agent(
            agent_id=1,
            position=Point2D(1000.0, 1000.0),
            goal=Point2D(1010.0, 1000.0),
            desired_speed=5.0,
        )
        start_pos = far_agent.position

        sim = EvacuationSimulator()
        sim.run(
            [far_agent], dt=0.1, max_time_s=2.0, lod_manager=lod_manager, camera_position=(0.0, 0.0)
        )

        assert far_agent.position == start_pos
        assert not far_agent.evacuated

    def test_aggregate_agent_moves_cheaply_toward_goal(self):
        levels = (
            SimulationLODLevel(max_distance=5.0, mode=SimulationLODMode.FULL),
            SimulationLODLevel(max_distance=1000.0, mode=SimulationLODMode.AGGREGATE),
        )
        lod_manager = SimulationLODManager(levels=levels)
        agent = Agent(
            agent_id=1, position=Point2D(100.0, 0.0), goal=Point2D(105.0, 0.0), desired_speed=5.0
        )

        sim = EvacuationSimulator()
        result = sim.run(
            [agent], dt=0.1, max_time_s=10.0, lod_manager=lod_manager, camera_position=(0.0, 0.0)
        )

        assert result.evacuated_count == 1
        assert agent.evacuated

    def test_full_mode_matches_default_social_force_behavior(self):
        """Kamera agent'a çok yakınsa (`FULL`), sonuç LOD kullanılmayan
        varsayılan koşuyla aynı olmalı (regresyon: LOD bağlanması eski
        davranışı bozmamalı)."""
        levels = (SimulationLODLevel(max_distance=float("inf"), mode=SimulationLODMode.FULL),)
        lod_manager = SimulationLODManager(levels=levels)

        agent_a = Agent(agent_id=1, position=Point2D(0.0, 0.0), goal=Point2D(5.0, 0.0))
        agent_b = Agent(agent_id=1, position=Point2D(0.0, 0.0), goal=Point2D(5.0, 0.0))

        sim = EvacuationSimulator()
        sim.run([agent_a], dt=0.1, max_time_s=10.0)
        sim.run(
            [agent_b], dt=0.1, max_time_s=10.0, lod_manager=lod_manager, camera_position=(0.0, 0.0)
        )

        assert agent_a.position.x == agent_b.position.x
        assert agent_a.position.y == agent_b.position.y
        assert agent_a.evacuated == agent_b.evacuated

    def test_no_lod_manager_keeps_old_behavior(self):
        """`lod_manager`/`camera_position` verilmezse davranış birebir eski
        (tüm agent'lar her zaman FULL) kalmalı."""
        agent = Agent(agent_id=1, position=Point2D(0.0, 0.0), goal=Point2D(3.0, 0.0))
        sim = EvacuationSimulator()
        result = sim.run([agent], dt=0.1, max_time_s=10.0)
        assert result.evacuated_count == 1
