"""
Roadmap V9 / Faz VI — kalan maddeler testleri.

Kapsam: Katman 3.1 madde 3 (açık-alan NavGraph), Katman 3.1 madde 4 +
3.2 madde 3 (heatmap overlay), Katman 3.3 (çok-modlu talep / adaptif
sinyalizasyon / ROAD_CLOSED / hava durumu->IDM).
"""

from __future__ import annotations

import unittest

from harita.climate_data.open_meteo_client import HourlyClimateSample
from harita.core_engine.geometry_engine import Point2D
from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.extensibility.city_events import CityEventType, emit_city_event
from harita.extensibility.event_system import EventSystem
from harita.mobility.adaptive_signal import (
    AdaptiveSignalController,
    AdaptiveSignalParams,
    adapt_signal,
    queued_vehicle_count,
)
from harita.mobility.crowd_simulation import Agent
from harita.mobility.mode_choice import ModeChoiceModel, TravelMode
from harita.mobility.open_area_navgraph import (
    OpenAreaNavGraphBuilder,
    edge_cost_multiplier_from_properties,
    find_nearest_node,
)
from harita.mobility.osm_demand_bridge import ResolvedDemand
from harita.mobility.pathfinding import NavGraph
from harita.mobility.road_closure import (
    RoadClosureSubscriber,
    emit_road_closed,
    emit_road_reopened,
)
from harita.mobility.traffic_simulation import (
    VEHICLE_IDM_DEFAULTS,
    TrafficAgent,
    TrafficSignalPhase,
    TrafficSimulator,
    VehicleType,
)
from harita.mobility.weather_traffic_effect import apply_weather_effect, weather_severity
from harita.population.activity_model import ActivityType
from harita.visualization.heatmap_overlay import (
    crowd_heatmap_overlay,
    crowd_heatmap_overlay_from_agents,
    station_heatmap_overlay,
)


class TestOpenAreaNavGraph(unittest.TestCase):
    def _collection(self):
        return GeoFeatureCollection(
            features=[
                GeoFeature(
                    "LineString", [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)], {"category": "roads"}
                ),
                GeoFeature(
                    "Polygon",
                    [[(20.0, 0.0), (20.0, 10.0), (30.0, 10.0), (30.0, 0.0)]],
                    {"category": "park"},
                ),
                GeoFeature(
                    "LineString", [(0.0, 100.0), (5.0, 100.0)], {"category": "waterway"}
                ),  # ilgisiz
            ]
        )

    def test_build_creates_connected_graph(self):
        graph = OpenAreaNavGraphBuilder(snap_precision=0.5).build(self._collection())
        self.assertGreater(len(graph.positions), 0)
        # LineString + Polygon toplamda >=3 farklı düğüm üretmeli
        self.assertGreaterEqual(len(graph.positions), 3)

    def test_irrelevant_categories_ignored(self):
        graph = OpenAreaNavGraphBuilder().build(self._collection())
        # waterway kategorisi eklenmemeli -> y=100 civarında düğüm olmamalı
        self.assertFalse(any(p.y > 50 for p in graph.positions.values()))

    def test_snap_merges_close_endpoints(self):
        coll = GeoFeatureCollection(
            features=[
                GeoFeature("LineString", [(0.0, 0.0), (10.0, 0.0)], {"category": "roads"}),
                GeoFeature("LineString", [(10.001, 0.0), (20.0, 0.0)], {"category": "roads"}),
            ]
        )
        graph = OpenAreaNavGraphBuilder(snap_precision=0.5).build(coll)
        # iki segment aynı (yaklaşık) noktada birleşmeli -> 3 düğüm (0,10,20), 4 değil
        self.assertEqual(len(graph.positions), 3)

    def test_edge_cost_multiplier_narrow_and_steep(self):
        base = edge_cost_multiplier_from_properties({})
        narrow = edge_cost_multiplier_from_properties({"width": 1.0})
        steep = edge_cost_multiplier_from_properties({"incline": "10%"})
        self.assertEqual(base, 1.0)
        self.assertGreater(narrow, 1.0)
        self.assertGreater(steep, 1.0)

    def test_edge_cost_multiplier_invalid_values_fallback_to_default(self):
        result = edge_cost_multiplier_from_properties({"width": "not-a-number"}, default=2.0)
        self.assertEqual(result, 2.0)

    def test_find_nearest_node(self):
        graph = OpenAreaNavGraphBuilder().build(self._collection())
        nearest = find_nearest_node(graph, Point2D(1.0, 0.5))
        self.assertIsNotNone(nearest)

    def test_find_nearest_node_empty_graph_returns_none(self):
        self.assertIsNone(find_nearest_node(NavGraph(), Point2D(0, 0)))


class TestHeatmapOverlay(unittest.TestCase):
    def test_crowd_heatmap_overlay_color_gradient(self):
        heatmap = {(0, 0): 10, (1, 0): 5, (2, 0): 1}
        cells = crowd_heatmap_overlay(heatmap, cell_size=2.0)
        self.assertEqual(len(cells), 3)
        densest = max(cells, key=lambda c: c.raw_count)
        self.assertEqual(densest.density_ratio, 1.0)
        self.assertAlmostEqual(densest.color_rgb[0], 1.0)  # kırmızı = yoğun

    def test_crowd_heatmap_overlay_empty(self):
        self.assertEqual(crowd_heatmap_overlay({}), [])

    def test_crowd_heatmap_overlay_from_agents(self):
        agents = [
            Agent(agent_id=i, position=Point2D(0.2 * i, 0.0), goal=Point2D(10, 10))
            for i in range(5)
        ]
        cells = crowd_heatmap_overlay_from_agents(agents, cell_size=1.0)
        self.assertGreater(len(cells), 0)

    def test_station_heatmap_overlay(self):
        report = {
            8: {"avg_occupancy_ratio": 0.9, "is_dense": True},
            14: {"avg_occupancy_ratio": 0.1, "is_dense": False},
        }
        cells = station_heatmap_overlay(Point2D(0, 0), report)
        self.assertEqual(len(cells), 2)
        self.assertTrue(cells[0].hour < cells[1].hour)
        self.assertTrue(cells[0].is_dense)


class TestModeChoice(unittest.TestCase):
    def _demand(self, dest=Point2D(500, 0)):
        return ResolvedDemand(
            individual_id="i1",
            departure_hour=8.0,
            origin_activity=ActivityType.HOME,
            destination_activity=ActivityType.WORK,
            destination_position=dest,
        )

    def test_short_distance_prefers_walk(self):
        model = ModeChoiceModel(seed=1)
        result = model.choose(
            self._demand(Point2D(200, 0)), origin_position=Point2D(0, 0), has_vehicle_access=False
        )
        self.assertEqual(result.chosen_mode, TravelMode.WALK)

    def test_long_distance_with_vehicle_prefers_vehicle(self):
        model = ModeChoiceModel(seed=1)
        result = model.choose(
            self._demand(Point2D(10000, 0)), origin_position=Point2D(0, 0), has_vehicle_access=True
        )
        self.assertEqual(result.chosen_mode, TravelMode.VEHICLE)

    def test_bad_weather_reduces_walk_score(self):
        model = ModeChoiceModel(seed=1)
        good = model.choose(
            self._demand(Point2D(300, 0)),
            origin_position=Point2D(0, 0),
            has_vehicle_access=False,
            bad_weather=False,
        )
        bad = model.choose(
            self._demand(Point2D(300, 0)),
            origin_position=Point2D(0, 0),
            has_vehicle_access=False,
            bad_weather=True,
        )
        self.assertLess(bad.scores[TravelMode.WALK.value], good.scores[TravelMode.WALK.value])

    def test_choose_batch_and_mode_share(self):
        model = ModeChoiceModel(seed=2)
        demands = [self._demand(Point2D(100 * i, 0)) for i in range(1, 6)]
        results = model.choose_batch(demands)
        self.assertEqual(len(results), 5)
        share = model.mode_share(results)
        self.assertAlmostEqual(sum(share.values()), 1.0, places=6)


class TestAdaptiveSignal(unittest.TestCase):
    def _make_simulator_with_queue(
        self, queued_count: int
    ) -> tuple[TrafficSimulator, TrafficSignalPhase]:
        sim = TrafficSimulator()
        signal = TrafficSignalPhase(
            stop_line_distance=100.0, green_duration_s=20.0, red_duration_s=20.0
        )
        for i in range(queued_count):
            agent = TrafficAgent(
                agent_id=i,
                vehicle_type=VehicleType.ARAC,
                route_nodes=[],
                distance_along_route=50.0 + i,
                speed=0.0,
            )
            sim.add_agent(agent, "route1")
        sim.add_signal("route1", signal)
        return sim, signal

    def test_queued_vehicle_count(self):
        sim, signal = self._make_simulator_with_queue(7)
        count = queued_vehicle_count(sim, "route1", signal)
        self.assertEqual(count, 7)

    def test_adapt_signal_extends_green_when_congested(self):
        sim, signal = self._make_simulator_with_queue(7)
        params = AdaptiveSignalParams(queue_length_threshold=5, green_extension_s=10.0)
        new_signal = adapt_signal(sim, "route1", signal, params=params)
        self.assertGreater(new_signal.green_duration_s, signal.green_duration_s)
        self.assertEqual(new_signal.red_duration_s, signal.red_duration_s)

    def test_adapt_signal_shrinks_green_when_not_congested(self):
        sim, signal = self._make_simulator_with_queue(1)
        params = AdaptiveSignalParams(queue_length_threshold=5, green_extension_s=10.0)
        new_signal = adapt_signal(sim, "route1", signal, params=params)
        self.assertLessEqual(new_signal.green_duration_s, signal.green_duration_s)

    def test_controller_installs_and_refreshes(self):
        sim, signal = self._make_simulator_with_queue(7)
        controller = AdaptiveSignalController(
            sim,
            "route1",
            signal,
            params=AdaptiveSignalParams(queue_length_threshold=5, green_extension_s=10.0),
            refresh_interval_s=5.0,
        )
        self.assertIn(controller.current_signal, sim._signals["route1"])
        controller.on_step(2.0)  # henüz yenileme zamanı gelmedi
        self.assertEqual(controller.current_signal.green_duration_s, signal.green_duration_s)
        controller.on_step(4.0)  # toplam 6s >= refresh_interval
        self.assertGreater(controller.current_signal.green_duration_s, signal.green_duration_s)


class TestRoadClosure(unittest.TestCase):
    def test_road_closed_blocks_edge(self):
        graph = NavGraph()
        graph.add_node("A", Point2D(0, 0))
        graph.add_node("B", Point2D(10, 0))
        graph.add_edge("A", "B")
        bus = EventSystem()
        sub = RoadClosureSubscriber()
        sub.register_graph(graph)
        sub.subscribe(bus)

        emit_road_closed(bus, "A", "B")
        self.assertIn(("A", "B"), graph._blocked_edges)  # noqa: SLF001
        self.assertIn((None, "A", "B"), sub.closed_edges)

    def test_road_reopened_unblocks_edge(self):
        graph = NavGraph()
        graph.add_node("A", Point2D(0, 0))
        graph.add_node("B", Point2D(10, 0))
        graph.add_edge("A", "B")
        bus = EventSystem()
        sub = RoadClosureSubscriber()
        sub.register_graph(graph)
        sub.subscribe(bus)

        emit_road_closed(bus, "A", "B")
        emit_road_reopened(bus, "A", "B")
        self.assertNotIn(("A", "B"), graph._blocked_edges)  # noqa: SLF001

    def test_unknown_nodes_recorded_as_unresolved(self):
        graph = NavGraph()
        graph.add_node("A", Point2D(0, 0))
        bus = EventSystem()
        sub = RoadClosureSubscriber()
        sub.register_graph(graph)
        sub.subscribe(bus)

        emit_road_closed(bus, "A", "UNKNOWN")
        self.assertEqual(len(sub.unresolved_events), 1)

    def test_generic_hazard_started_event_not_consumed(self):
        # ROAD_CLOSED dışındaki olaylar bu abonelik tarafından işlenmemeli.
        graph = NavGraph()
        graph.add_node("A", Point2D(0, 0))
        graph.add_node("B", Point2D(10, 0))
        graph.add_edge("A", "B")
        bus = EventSystem()
        sub = RoadClosureSubscriber()
        sub.register_graph(graph)
        sub.subscribe(bus)
        emit_city_event(bus, CityEventType.HAZARD_STARTED, hazard_type="earthquake")
        self.assertNotIn(("A", "B"), graph._blocked_edges)  # noqa: SLF001


class TestWeatherTrafficEffect(unittest.TestCase):
    def _sample(self, precip=None, visibility=None):
        return HourlyClimateSample(
            time_iso="2026-01-01T08:00",
            temperature_c=10.0,
            cloud_cover_pct=80.0,
            shortwave_radiation_wm2=100.0,
            direct_radiation_wm2=50.0,
            diffuse_radiation_wm2=50.0,
            precipitation_mm=precip,
            visibility_m=visibility,
        )

    def test_no_data_returns_unchanged_params(self):
        sample = self._sample()
        base = VEHICLE_IDM_DEFAULTS[VehicleType.ARAC]
        result = apply_weather_effect(base, sample)
        self.assertEqual(result, base)

    def test_heavy_rain_reduces_speed_and_increases_headway(self):
        sample = self._sample(precip=15.0, visibility=300.0)
        base = VEHICLE_IDM_DEFAULTS[VehicleType.ARAC]
        result = apply_weather_effect(base, sample)
        self.assertLess(result.desired_speed, base.desired_speed)
        self.assertGreater(result.safe_time_headway, base.safe_time_headway)

    def test_weather_severity_bounds(self):
        clear = weather_severity(self._sample())
        severe = weather_severity(self._sample(precip=50.0, visibility=50.0))
        self.assertEqual(clear, 0.0)
        self.assertLessEqual(severe, 1.0)
        self.assertGreater(severe, 0.5)


if __name__ == "__main__":
    unittest.main()
