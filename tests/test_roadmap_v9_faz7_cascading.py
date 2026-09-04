"""
Roadmap V9 / Faz VII — Kriz Modunun Şehir Geneline Yayılması (Cascading).

Kapsam: Katman 7.1 (HazardEvent ortak soyutlaması), 7.4 (Cascade Engine),
7.5 (Acil Müdahale Simülasyonu), 7.6 (Toparlanma Zaman Çizelgesi).
"""
from __future__ import annotations

import unittest

from harita.core_engine.geometry_engine import Point2D
from harita.extensibility.city_events import CityEventType, emit_city_event
from harita.extensibility.event_system import EventSystem
from harita.hazard_data.hazard_event import HazardEvent, HazardRegistry, HazardType
from harita.hazard_data.cascade_rules import CascadeEngine, CascadeRule, DEFAULT_CASCADE_RULES
from harita.hazard_data.resilience_timeline import (
    SYSTEM_EVENT_PAIRS,
    build_resilience_report,
)
from harita.mobility.emergency_response import (
    DispatchResult,
    EmergencyDispatcher,
    EmergencyStation,
    EmergencyUnitType,
    dispatch_nearest_unit,
    emergency_signal_priority_params,
)
from harita.mobility.adaptive_signal import AdaptiveSignalParams
from harita.mobility.pathfinding import NavGraph


class TestHazardEvent(unittest.TestCase):
    def test_static_hazard_affects_point_within_radius(self):
        hz = HazardEvent(
            hazard_type=HazardType.EARTHQUAKE, epicenter=Point2D(0, 0),
            radius_m=1000.0, severity=0.8, started_at=0.0,
        )
        self.assertTrue(hz.affects_point(Point2D(500, 0)))
        self.assertFalse(hz.affects_point(Point2D(5000, 0)))

    def test_is_active_respects_started_and_ended(self):
        hz = HazardEvent(
            hazard_type=HazardType.FIRE, epicenter=Point2D(0, 0),
            radius_m=100.0, severity=0.5, started_at=10.0, ended_at=20.0,
        )
        self.assertFalse(hz.is_active(5.0))
        self.assertTrue(hz.is_active(15.0))
        self.assertFalse(hz.is_active(25.0))

    def test_spread_fn_grows_radius_over_time(self):
        hz = HazardEvent(
            hazard_type=HazardType.FIRE, epicenter=Point2D(0, 0),
            radius_m=10.0, severity=0.5, started_at=0.0,
            spread_fn=lambda elapsed: 10.0 + elapsed * 2.0,
        )
        self.assertAlmostEqual(hz.current_radius_m(0.0), 10.0)
        self.assertAlmostEqual(hz.current_radius_m(5.0), 20.0)
        self.assertTrue(hz.affects_point(Point2D(15, 0), now=5.0))
        self.assertFalse(hz.affects_point(Point2D(15, 0), now=0.0))

    def test_registry_active_and_affecting_point(self):
        reg = HazardRegistry()
        hz1 = reg.register(HazardEvent(
            hazard_type=HazardType.EARTHQUAKE, epicenter=Point2D(0, 0),
            radius_m=500.0, severity=0.9, started_at=0.0,
        ))
        reg.register(HazardEvent(
            hazard_type=HazardType.FLOOD, epicenter=Point2D(10000, 10000),
            radius_m=200.0, severity=0.3, started_at=0.0,
        ))
        active = reg.active_at(1.0)
        self.assertEqual(len(active), 2)
        affecting = reg.affecting_point(Point2D(100, 0), 1.0)
        self.assertEqual(affecting, [hz1])
        eq_only = reg.active_of_type(HazardType.EARTHQUAKE, 1.0)
        self.assertEqual(eq_only, [hz1])

    def test_registry_end_deactivates(self):
        reg = HazardRegistry()
        hz = reg.register(HazardEvent(
            hazard_type=HazardType.HEATWAVE, epicenter=Point2D(0, 0),
            radius_m=100.0, severity=0.2, started_at=0.0,
        ))
        self.assertTrue(hz.is_active(1.0))
        reg.end(hz, now=1.0)
        self.assertFalse(hz.is_active(1.0))


class TestCascadeEngine(unittest.TestCase):
    def test_deterministic_rule_always_triggers(self):
        bus = EventSystem()
        rule = CascadeRule(
            name="test_kural", trigger_type=CityEventType.FIRE_IGNITED,
            probability=1.0, effect_type=CityEventType.EVACUATION_STARTED,
        )
        engine = CascadeEngine(bus=bus, rules=(rule,), seed=1)
        engine.start()
        emit_city_event(bus, CityEventType.FIRE_IGNITED)
        self.assertEqual(len(engine.triggered_log), 1)
        history = bus.history(str(CityEventType.EVACUATION_STARTED.value))
        self.assertEqual(len(history), 1)

    def test_zero_probability_never_triggers(self):
        bus = EventSystem()
        rule = CascadeRule(
            name="test_kural", trigger_type=CityEventType.FIRE_IGNITED,
            probability=0.0, effect_type=CityEventType.EVACUATION_STARTED,
        )
        engine = CascadeEngine(bus=bus, rules=(rule,), seed=1)
        engine.start()
        for _ in range(20):
            emit_city_event(bus, CityEventType.FIRE_IGNITED)
        self.assertEqual(len(engine.triggered_log), 0)
        self.assertEqual(len(engine.skipped_log), 20)

    def test_condition_filters_out_event(self):
        bus = EventSystem()
        rule = CascadeRule(
            name="buyuk_deprem_kurali", trigger_type=CityEventType.HAZARD_STARTED,
            probability=1.0, effect_type=CityEventType.POWER_OUTAGE,
            condition=lambda e: (e.payload or {}).get("magnitude", 0) >= 6.0,
        )
        engine = CascadeEngine(bus=bus, rules=(rule,), seed=1)
        engine.start()
        emit_city_event(bus, CityEventType.HAZARD_STARTED, magnitude=4.0)
        self.assertEqual(len(engine.triggered_log), 0)
        emit_city_event(bus, CityEventType.HAZARD_STARTED, magnitude=6.5)
        self.assertEqual(len(engine.triggered_log), 1)

    def test_cooldown_prevents_immediate_retrigger(self):
        bus = EventSystem()
        rule = CascadeRule(
            name="cooldownlu_kural", trigger_type=CityEventType.FIRE_IGNITED,
            probability=1.0, effect_type=CityEventType.EVACUATION_STARTED,
            cooldown_s=100.0,
        )
        clock = {"t": 0.0}
        engine = CascadeEngine(bus=bus, rules=(rule,), seed=1)
        engine.set_clock(lambda: clock["t"])
        engine.start()
        emit_city_event(bus, CityEventType.FIRE_IGNITED, source="bina_1")
        clock["t"] = 10.0
        emit_city_event(bus, CityEventType.FIRE_IGNITED, source="bina_1")
        self.assertEqual(len(engine.triggered_log), 1)
        clock["t"] = 200.0
        emit_city_event(bus, CityEventType.FIRE_IGNITED, source="bina_1")
        self.assertEqual(len(engine.triggered_log), 2)

    def test_default_rules_load_without_error(self):
        bus = EventSystem()
        engine = CascadeEngine(bus=bus, rules=DEFAULT_CASCADE_RULES, seed=42)
        engine.start()
        emit_city_event(bus, CityEventType.HAZARD_STARTED, magnitude=7.0, epicenter=(0, 0))
        # deterministik değil ama en azından hata fırlatmadan çalışmalı ve
        # bir şeyler (tetiklenen ya da atlanan) loglanmalı.
        self.assertGreater(len(engine.triggered_log) + len(engine.skipped_log), 0)


class TestEmergencyResponse(unittest.TestCase):
    def _linear_graph(self) -> NavGraph:
        graph = NavGraph()
        graph.add_node("station", Point2D(0, 0))
        graph.add_node("mid", Point2D(500, 0))
        graph.add_node("incident", Point2D(1000, 0))
        graph.add_edge("station", "mid", 500.0)
        graph.add_edge("mid", "incident", 500.0)
        return graph

    def test_dispatch_nearest_unit_finds_route_and_eta(self):
        graph = self._linear_graph()
        station = EmergencyStation(
            station_id="itfaiye_1", node_id="station", position=Point2D(0, 0),
            unit_types=frozenset({EmergencyUnitType.FIRE_TRUCK}),
        )
        result = dispatch_nearest_unit(
            [station], "incident", graph, unit_type=EmergencyUnitType.FIRE_TRUCK,
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.found)
        self.assertEqual(result.path_result.path[0], "station")
        self.assertEqual(result.path_result.path[-1], "incident")
        self.assertGreater(result.estimated_response_seconds, 0.0)

    def test_dispatch_picks_closer_station_by_graph_cost(self):
        graph = NavGraph()
        graph.add_node("near", Point2D(0, 0))
        graph.add_node("far", Point2D(0, 0))
        graph.add_node("incident", Point2D(1000, 0))
        graph.add_edge("near", "incident", 100.0)
        graph.add_edge("far", "incident", 5000.0)
        near_station = EmergencyStation(
            station_id="near", node_id="near", position=Point2D(0, 0),
            unit_types=frozenset({EmergencyUnitType.AMBULANCE}),
        )
        far_station = EmergencyStation(
            station_id="far", node_id="far", position=Point2D(0, 0),
            unit_types=frozenset({EmergencyUnitType.AMBULANCE}),
        )
        result = dispatch_nearest_unit(
            [far_station, near_station], "incident", graph,
            unit_type=EmergencyUnitType.AMBULANCE,
        )
        self.assertEqual(result.station.station_id, "near")

    def test_dispatch_no_matching_unit_type_returns_none(self):
        graph = self._linear_graph()
        station = EmergencyStation(
            station_id="polis_1", node_id="station", position=Point2D(0, 0),
            unit_types=frozenset({EmergencyUnitType.POLICE}),
        )
        result = dispatch_nearest_unit(
            [station], "incident", graph, unit_type=EmergencyUnitType.AMBULANCE,
        )
        self.assertIsNone(result)

    def test_dispatch_unreachable_incident_returns_none(self):
        graph = NavGraph()
        graph.add_node("station", Point2D(0, 0))
        graph.add_node("incident", Point2D(1000, 0))  # kenar yok -> erişilemez
        station = EmergencyStation(
            station_id="s1", node_id="station", position=Point2D(0, 0),
            unit_types=frozenset({EmergencyUnitType.FIRE_TRUCK}),
        )
        result = dispatch_nearest_unit(
            [station], "incident", graph, unit_type=EmergencyUnitType.FIRE_TRUCK,
        )
        self.assertIsNone(result)

    def test_emergency_dispatcher_logs_dispatches(self):
        graph = self._linear_graph()
        dispatcher = EmergencyDispatcher(graph=graph)
        dispatcher.register_station(EmergencyStation(
            station_id="s1", node_id="station", position=Point2D(0, 0),
            unit_types=frozenset({EmergencyUnitType.AMBULANCE}),
        ))
        result = dispatcher.dispatch("incident", unit_type=EmergencyUnitType.AMBULANCE)
        self.assertIsNotNone(result)
        self.assertEqual(len(dispatcher.log), 1)

    def test_emergency_dispatcher_without_graph_raises(self):
        dispatcher = EmergencyDispatcher()
        with self.assertRaises(ValueError):
            dispatcher.dispatch("incident", unit_type=EmergencyUnitType.POLICE)

    def test_emergency_signal_priority_more_aggressive_than_base(self):
        base = AdaptiveSignalParams(queue_length_threshold=5, green_extension_s=10.0)
        priority = emergency_signal_priority_params(base)
        self.assertLess(priority.queue_length_threshold, base.queue_length_threshold)
        self.assertGreater(priority.green_extension_s, base.green_extension_s)


class TestResilienceTimeline(unittest.TestCase):
    def test_recovered_system_reports_duration(self):
        bus = EventSystem()
        emit_city_event(bus, CityEventType.POWER_OUTAGE)
        emit_city_event(bus, CityEventType.POWER_RESTORED)
        report = build_resilience_report(bus)
        power_records = report.by_system("elektrik")
        self.assertEqual(len(power_records), 1)
        self.assertTrue(power_records[0].recovered)
        self.assertIsNotNone(power_records[0].recovery_seconds)
        self.assertGreaterEqual(power_records[0].recovery_seconds, 0.0)

    def test_unrecovered_system_reported_as_such(self):
        bus = EventSystem()
        emit_city_event(bus, CityEventType.ROAD_CLOSED)
        report = build_resilience_report(bus)
        self.assertIn("yollar", report.unrecovered_systems())
        road_records = report.by_system("yollar")
        self.assertFalse(road_records[0].recovered)
        self.assertIsNone(road_records[0].recovery_seconds)

    def test_no_disruption_no_record(self):
        bus = EventSystem()
        report = build_resilience_report(bus)
        self.assertEqual(report.records, [])

    def test_slowest_system_picks_max_duration(self):
        bus = EventSystem()
        emit_city_event(bus, CityEventType.POWER_OUTAGE)
        emit_city_event(bus, CityEventType.POWER_RESTORED)
        emit_city_event(bus, CityEventType.TRANSIT_DISRUPTED)
        emit_city_event(bus, CityEventType.TRANSIT_RESTORED)
        report = build_resilience_report(bus)
        slowest = report.slowest_system()
        self.assertIsNotNone(slowest)
        self.assertIn(slowest.system_name, ("elektrik", "toplu_tasima"))

    def test_all_system_event_pairs_are_distinct(self):
        names = [p.system_name for p in SYSTEM_EVENT_PAIRS]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
