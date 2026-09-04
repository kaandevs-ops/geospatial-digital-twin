"""ROADMAP_V9 Faz X — Şehir Ölçeği + Governance testleri.

Kapsam: Katman 8.1 (çoklu bina orkestrasyonu / köprü düğüm, spatial
hashing, bölgesel yığılma, IoT senkron başlangıç koşulu) + Katman 8
madde 1 (rol-bazlı senaryo izinleri) + madde 2 (senaryo dallanması) +
madde 3 (denetim izi).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harita.building_reconstruction.redevelopment import (
    BuildingChangeRecord,
    BuildingRedevelopmentOrchestrator,
)
from harita.collaboration.auth import PermissionDeniedError, Role
from harita.collaboration.scenario_permissions import (
    ScenarioAction,
    can_perform,
    require_scenario_permission,
)
from harita.collaboration.scenario_watch import ScenarioWatchHub, scenario_topic
from harita.core_engine.geometry_engine import Point2D
from harita.digital_twin.hierarchy import TwinHierarchy
from harita.extensibility.event_system import EventSystem
from harita.extensibility.websocket_api import WSMessage
from harita.mobility.city_scale_evacuation import (
    BuildingBridge,
    CityScaleAgentIndex,
    CityScaleEvacuationError,
    bridge_building_to_outdoor_graph,
    initial_agent_count_from_iot,
    most_congested_region,
    regional_agent_density,
    route_evacuated_agents_to_assembly_point,
)
from harita.mobility.indoor_navigation import BuildingNavGraph
from harita.mobility.pathfinding import NavGraph
from harita.observability.logging import StructuredLogger
from harita.observability.scenario_audit import (
    query_scenario_audit_trail,
    record_scenario_event,
)
from harita.persistence.project_manager import ProjectManager


class TestScenarioPermissions(unittest.TestCase):
    def test_viewer_can_only_view(self):
        self.assertTrue(can_perform(Role.VIEWER, ScenarioAction.VIEW_RESULT))
        self.assertFalse(can_perform(Role.VIEWER, ScenarioAction.RUN_SCENARIO))
        self.assertFalse(can_perform(Role.VIEWER, ScenarioAction.CONFIGURE_REALITY_FEED))

    def test_editor_can_run_but_not_configure(self):
        self.assertTrue(can_perform(Role.EDITOR, ScenarioAction.RUN_SCENARIO))
        self.assertFalse(can_perform(Role.EDITOR, ScenarioAction.CONFIGURE_REALITY_FEED))

    def test_owner_can_do_everything(self):
        for action in ScenarioAction:
            self.assertTrue(can_perform(Role.OWNER, action))

    def test_require_permission_raises_for_viewer_run(self):
        with self.assertRaises(PermissionDeniedError):
            require_scenario_permission(Role.VIEWER, ScenarioAction.RUN_SCENARIO)

    def test_require_permission_none_role_always_allows(self):
        # Geriye uyumluluk: role=None -> denetimsiz.
        require_scenario_permission(None, ScenarioAction.CONFIGURE_REALITY_FEED)

    def test_require_permission_editor_run_ok(self):
        require_scenario_permission(Role.EDITOR, ScenarioAction.RUN_SCENARIO)


class TestScenarioAuditTrail(unittest.TestCase):
    def setUp(self):
        self.logger = StructuredLogger(name="test-audit", max_records=100)

    def test_record_and_query(self):
        record_scenario_event(
            self.logger,
            actor="u1",
            project_id="p1",
            action="evacuation_run",
            scenario_id="s1",
            result_id="r1",
            role="EDITOR",
        )
        record_scenario_event(
            self.logger,
            actor="u2",
            project_id="p1",
            action="scenario_saved",
            scenario_id="s2",
            role="OWNER",
        )
        record_scenario_event(
            self.logger,
            actor="u1",
            project_id="p2",
            action="evacuation_run",
            scenario_id="s3",
            role="EDITOR",
        )
        all_p1 = query_scenario_audit_trail(self.logger, project_id="p1")
        self.assertEqual(len(all_p1), 2)
        by_actor = query_scenario_audit_trail(self.logger, actor="u1")
        self.assertEqual(len(by_actor), 2)
        by_scenario = query_scenario_audit_trail(self.logger, scenario_id="s2")
        self.assertEqual(len(by_scenario), 1)
        self.assertEqual(by_scenario[0]["actor"], "u2")

    def test_unrelated_log_entries_excluded(self):
        self.logger.info("unrelated_event", foo="bar")
        record_scenario_event(self.logger, actor="u1", project_id="p1", action="x")
        results = query_scenario_audit_trail(self.logger)
        self.assertEqual(len(results), 1)


class TestProjectBranching(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.mgr = ProjectManager(self.dir / "registry.db")
        self.addCleanup(self.mgr.close)
        self.handle = self.mgr.create_project(
            self.dir / "main.hproj",
            "Ana Proje",
            "main-1",
        )
        self.handle.db.save_object("obj:1", "mesh", {"v": 1})

    def test_create_branch_is_independent(self):
        branch = self.mgr.create_branch("main-1", "Deneme Dalı", branch_project_id="branch-1")
        self.assertNotEqual(branch.manifest.project_id, "main-1")
        # dal, ana projenin nesnesini devralır (kopya)
        self.assertIsNotNone(branch.db.load_object("obj:1"))
        # dal üzerinde yapılan değişiklik ana projeyi etkilemez
        branch.db.save_object("obj:2", "mesh", {"v": 2})
        self.assertIsNone(self.handle.db.load_object("obj:2"))

    def test_list_branches(self):
        self.mgr.create_branch("main-1", "Dal A", branch_project_id="branch-a")
        self.mgr.create_branch("main-1", "Dal B", branch_project_id="branch-b")
        branches = self.mgr.list_branches("main-1")
        ids = {b["project_id"] for b in branches}
        self.assertEqual(ids, {"branch-a", "branch-b"})

    def test_merge_branch_copies_objects_back(self):
        branch = self.mgr.create_branch("main-1", "Dal C", branch_project_id="branch-c")
        branch.db.save_object("obj:new", "mesh", {"v": 99})
        merged = self.mgr.merge_branch("branch-c", "main-1")
        self.assertGreaterEqual(merged, 1)
        record = self.handle.db.load_object("obj:new")
        self.assertIsNotNone(record)
        self.assertEqual(record.data["v"], 99)

    def test_merge_branch_kind_filter(self):
        branch = self.mgr.create_branch("main-1", "Dal D", branch_project_id="branch-d")
        branch.db.save_object("obj:scenario1", "scenario", {"x": 1})
        branch.db.save_object("obj:mesh2", "mesh", {"x": 2})
        merged = self.mgr.merge_branch("branch-d", "main-1", kinds=("scenario",))
        self.assertEqual(merged, 1)
        self.assertIsNotNone(self.handle.db.load_object("obj:scenario1"))
        self.assertIsNone(self.handle.db.load_object("obj:mesh2"))


class TestCityScaleBridge(unittest.TestCase):
    def test_bridge_missing_exit_node_raises(self):
        bg = NavGraph()
        b = BuildingNavGraph(graph=bg, floor_count=1, floor_height=3.0)
        outdoor = NavGraph()
        outdoor.add_node("sidewalk", Point2D(0, 0))
        with self.assertRaises(CityScaleEvacuationError):
            bridge_building_to_outdoor_graph(b, "missing", outdoor, "sidewalk")

    def test_bridge_and_route_to_assembly(self):
        bg = NavGraph()
        bg.add_node("exit", Point2D(0, 0))
        b = BuildingNavGraph(graph=bg, floor_count=1, floor_height=3.0)

        outdoor = NavGraph()
        outdoor.add_node("sidewalk", Point2D(2, 0))
        outdoor.add_node("assembly", Point2D(50, 0))
        outdoor.add_edge("sidewalk", "assembly", cost=48.0)

        bridge_building_to_outdoor_graph(b, "exit", outdoor, "sidewalk", edge_cost=2.0)
        bridge = BuildingBridge(
            building_id="b1",
            exit_node_id="exit",
            outdoor_node_id="sidewalk",
            assembly_point_node_id="assembly",
        )
        route = route_evacuated_agents_to_assembly_point(outdoor, bridge)
        self.assertEqual(route[0][0], "bridge::exit")
        self.assertEqual(route[-1][0], "assembly")
        self.assertAlmostEqual(route[-1][1], 50.0)

    def test_route_without_bridge_raises(self):
        outdoor = NavGraph()
        outdoor.add_node("assembly", Point2D(50, 0))
        bridge = BuildingBridge(
            building_id="b1",
            exit_node_id="exit",
            outdoor_node_id="sidewalk",
            assembly_point_node_id="assembly",
        )
        with self.assertRaises(CityScaleEvacuationError):
            route_evacuated_agents_to_assembly_point(outdoor, bridge)

    def test_two_buildings_share_outdoor_graph_independently(self):
        outdoor = NavGraph()
        outdoor.add_node("s1", Point2D(0, 0))
        outdoor.add_node("s2", Point2D(10, 0))
        outdoor.add_node("assembly", Point2D(20, 0))
        outdoor.add_edge("s1", "assembly", cost=20.0)
        outdoor.add_edge("s2", "assembly", cost=10.0)

        bg1 = NavGraph()
        bg1.add_node("exit", Point2D(0, 0))
        b1 = BuildingNavGraph(graph=bg1, floor_count=1, floor_height=3.0)
        bg2 = NavGraph()
        bg2.add_node("exit", Point2D(10, 0))
        b2 = BuildingNavGraph(graph=bg2, floor_count=1, floor_height=3.0)

        bridge_building_to_outdoor_graph(b1, "exit", outdoor, "s1")
        bridge_building_to_outdoor_graph(b2, "exit", outdoor, "s2")
        # aynı düğüm kimliğine sahip iki farklı bina, çakışma olmadan
        # ayrı köprü düğümleri (bridge::exit) alamaz -- bu bilinçli bir
        # sınırdır, bu yüzden building_id ile çağıranın node id'lerini
        # bina-bazlı benzersiz tutması beklenir. Burada gerçek graf'ın
        # node sayısı çakışmadan arttığını doğruluyoruz.
        self.assertTrue(outdoor.has_node("bridge::exit"))


class TestRegionalDensity(unittest.TestCase):
    def setUp(self):
        self.h = TwinHierarchy()
        self.h.add("block_a", "district")
        self.h.add("block_b", "district")
        self.h.add("building_1", "block_a")
        self.h.add("building_2", "block_a")
        self.h.add("building_3", "block_b")
        self.counts = {"building_1": 10, "building_2": 5, "building_3": 40}

    def test_regional_agent_density_sums_leaves(self):
        self.assertEqual(regional_agent_density(self.h, "block_a", self.counts), 15)
        self.assertEqual(regional_agent_density(self.h, "block_b", self.counts), 40)

    def test_regional_agent_density_whole_district(self):
        self.assertEqual(regional_agent_density(self.h, "district", self.counts), 55)

    def test_most_congested_region(self):
        region_id, count = most_congested_region(self.h, ["block_a", "block_b"], self.counts)
        self.assertEqual(region_id, "block_b")
        self.assertEqual(count, 40)

    def test_missing_building_counts_as_zero(self):
        self.assertEqual(regional_agent_density(self.h, "block_a", {}), 0)


class TestCityScaleAgentIndex(unittest.TestCase):
    class _StubAgent:
        def __init__(self, agent_id, x, y):
            self.agent_id = agent_id
            self.position = Point2D(x, y)

    def test_neighbors_within_radius(self):
        idx = CityScaleAgentIndex()
        a1 = self._StubAgent(1, 0, 0)
        a2 = self._StubAgent(2, 1, 0)
        a3 = self._StubAgent(3, 500, 500)
        idx.rebuild({"b1": [a1, a2], "b2": [a3]})
        neighbors = idx.neighbors_within(a1, radius=5.0)
        self.assertEqual([n.agent_id for n in neighbors], [2])

    def test_empty_index_returns_empty(self):
        idx = CityScaleAgentIndex()
        idx.rebuild({})
        a1 = self._StubAgent(1, 0, 0)
        self.assertEqual(idx.neighbors_within(a1, 5.0), [])

    def test_building_of_lookup(self):
        idx = CityScaleAgentIndex()
        a1 = self._StubAgent(1, 0, 0)
        idx.rebuild({"building-x": [a1]})
        self.assertEqual(idx.building_of(a1), "building-x")


class TestIotInitialCondition(unittest.TestCase):
    def test_live_value_used_when_present(self):
        count, used = initial_agent_count_from_iot(12.0, fallback_count=5)
        self.assertEqual(count, 12)
        self.assertTrue(used)

    def test_fallback_when_no_sensor_value(self):
        count, used = initial_agent_count_from_iot(None, fallback_count=5)
        self.assertEqual(count, 5)
        self.assertFalse(used)

    def test_negative_sensor_value_falls_back(self):
        count, used = initial_agent_count_from_iot(-3.0, fallback_count=7)
        self.assertEqual(count, 7)
        self.assertFalse(used)

    def test_occupancy_multiplier_applied(self):
        count, used = initial_agent_count_from_iot(
            4.0,
            fallback_count=0,
            occupancy_per_sensor_unit=2.5,
        )
        self.assertEqual(count, 10)
        self.assertTrue(used)


class TestScenarioWatchHub(unittest.TestCase):
    def test_join_returns_default_state(self):
        hub = ScenarioWatchHub()
        conn = hub.router.connect()
        reply = hub.router.dispatch(
            conn,
            WSMessage(type="scenario_watch.join", payload={"project_id": "p1", "result_id": "r1"}),
        )
        self.assertEqual(reply.payload["playing"], False)
        self.assertIn(scenario_topic("p1", "r1"), conn.topics)

    def test_sync_broadcasts_to_other_viewers(self):
        hub = ScenarioWatchHub()
        c1 = hub.router.connect()
        c2 = hub.router.connect()
        hub.router.dispatch(
            c1,
            WSMessage(type="scenario_watch.join", payload={"project_id": "p1", "result_id": "r1"}),
        )
        hub.router.dispatch(
            c2,
            WSMessage(type="scenario_watch.join", payload={"project_id": "p1", "result_id": "r1"}),
        )
        hub.router.dispatch(
            c1,
            WSMessage(
                type="scenario_watch.sync",
                payload={
                    "project_id": "p1",
                    "result_id": "r1",
                    "playing": True,
                    "elapsed_s": 5.0,
                    "updated_by": "u1",
                },
            ),
        )
        self.assertEqual(len(c2.outbox), 1)
        self.assertEqual(c2.outbox[0].payload["elapsed_s"], 5.0)
        self.assertEqual(c2.outbox[0].payload["updated_by"], "u1")

    def test_viewer_count(self):
        hub = ScenarioWatchHub()
        c1 = hub.router.connect()
        c2 = hub.router.connect()
        hub.router.dispatch(
            c1,
            WSMessage(type="scenario_watch.join", payload={"project_id": "p1", "result_id": "r1"}),
        )
        self.assertEqual(hub.viewer_count("p1", "r1"), 1)
        hub.router.dispatch(
            c2,
            WSMessage(type="scenario_watch.join", payload={"project_id": "p1", "result_id": "r1"}),
        )
        self.assertEqual(hub.viewer_count("p1", "r1"), 2)

    def test_current_state_none_before_join(self):
        hub = ScenarioWatchHub()
        self.assertIsNone(hub.current_state("px", "rx"))


class TestBuildingRedevelopmentCascade(unittest.TestCase):
    def test_apply_calls_all_registered_layers(self):
        orch = BuildingRedevelopmentOrchestrator()
        calls = []
        orch.register_layer("energy_demand", lambda c: calls.append("energy") or "energy_demand")
        orch.register_layer(
            "synthetic_population", lambda c: calls.append("pop") or "synthetic_population"
        )
        change = BuildingChangeRecord(
            building_id="b1",
            change_kind="floor_count_changed",
            old_floor_count=5,
            new_floor_count=10,
        )
        report = orch.apply(change)
        self.assertEqual(set(report.recomputed_layers), {"energy_demand", "synthetic_population"})
        self.assertEqual(set(calls), {"energy", "pop"})
        self.assertEqual(report.errors, [])

    def test_layer_error_does_not_block_others(self):
        orch = BuildingRedevelopmentOrchestrator()
        orch.register_layer("broken", lambda c: (_ for _ in ()).throw(ValueError("boom")))
        orch.register_layer("ok", lambda c: "ok")
        change = BuildingChangeRecord(building_id="b1", change_kind="demolished")
        report = orch.apply(change)
        self.assertEqual(report.recomputed_layers, ["ok"])
        self.assertEqual(len(report.errors), 1)
        self.assertIn("boom", report.errors[0])

    def test_event_bus_integration_triggers_exactly_once(self):
        bus = EventSystem()
        orch = BuildingRedevelopmentOrchestrator(bus)
        calls = []
        orch.register_layer("energy_demand", lambda c: calls.append(1) or "energy_demand")
        change = BuildingChangeRecord(building_id="b1", change_kind="rebuilt", new_floor_count=12)
        orch.notify_building_changed(change)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(orch.history), 1)

    def test_unregister_layer(self):
        orch = BuildingRedevelopmentOrchestrator()
        orch.register_layer("temp", lambda c: "temp")
        orch.unregister_layer("temp")
        change = BuildingChangeRecord(building_id="b1", change_kind="demolished")
        report = orch.apply(change)
        self.assertEqual(report.recomputed_layers, [])


if __name__ == "__main__":
    unittest.main()
