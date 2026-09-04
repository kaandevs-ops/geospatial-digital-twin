from __future__ import annotations

import unittest

from harita.core_engine.geometry_engine import Point2D
from harita.digital_twin import DigitalTwin, DigitalTwinRegistry
from harita.digital_twin.hierarchy import TwinHierarchy
from harita.extensibility.city_events import CityEventType
from harita.extensibility.event_system import EventSystem
from harita.mobility.pathfinding import NavGraph

from harita.power_infrastructure.demand_model import (
    BuildingDemandProfile,
    BuildingDemandRegistry,
    BuildingDemandType,
    HOURS_PER_YEAR,
    aggregate_city_demand_kw,
    peak_hour,
)
from harita.power_infrastructure.outage_propagation import (
    DARK_CORRIDOR_SPEED_MULTIPLIER,
    OutagePropagationEngine,
    build_power_network_graph,
)
from harita.climate_data.microclimate import (
    UrbanFabricSample,
    estimate_heat_island_index,
    fabric_from_footprint,
)
from harita.climate_data.open_meteo_client import HourlyClimateSample
from harita.climate_data.air_quality_estimate import (
    RoadSegmentTraffic,
    estimate_network_air_quality,
    estimate_segment_air_quality,
)
from harita.climate_data.noise_estimate import (
    crowd_noise_db,
    estimate_noise,
    traffic_noise_db,
)


class DemandModelTests(unittest.TestCase):
    def test_hourly_curve_conserves_annual_average(self):
        profile = BuildingDemandProfile("b1", annual_kwh=8760.0,
                                         demand_type=BuildingDemandType.RESIDENTIAL)
        curve = profile.daily_curve_kw()
        self.assertEqual(len(curve), 24)
        # ortalama guc == 1.0 kW olmali (8760 kWh / 8760 saat)
        self.assertAlmostEqual(sum(curve) / 24.0, 1.0, places=6)

    def test_residential_evening_peak_higher_than_night(self):
        profile = BuildingDemandProfile("b1", annual_kwh=8760.0,
                                         demand_type=BuildingDemandType.RESIDENTIAL)
        self.assertGreater(profile.hourly_load_kw(19), profile.hourly_load_kw(3))

    def test_commercial_daytime_peak_higher_than_night(self):
        profile = BuildingDemandProfile("b2", annual_kwh=8760.0,
                                         demand_type=BuildingDemandType.COMMERCIAL)
        self.assertGreater(profile.hourly_load_kw(13), profile.hourly_load_kw(3))

    def test_invalid_hour_raises(self):
        profile = BuildingDemandProfile("b1", annual_kwh=1000.0)
        with self.assertRaises(ValueError):
            profile.hourly_load_kw(24)

    def test_peak_hour_matches_max_of_curve(self):
        profile = BuildingDemandProfile("b1", annual_kwh=8760.0,
                                         demand_type=BuildingDemandType.RESIDENTIAL)
        registry = BuildingDemandRegistry()
        registry.register(profile)
        hour, kw = peak_hour(registry, "b1")
        self.assertEqual(kw, max(profile.daily_curve_kw()))
        self.assertEqual(hour, profile.daily_curve_kw().index(kw))

    def test_peak_hour_unknown_building_raises(self):
        registry = BuildingDemandRegistry()
        with self.assertRaises(KeyError):
            peak_hour(registry, "does-not-exist")

    def test_hierarchy_aggregation_sums_leaf_buildings(self):
        # mahalle -> 2 bina (mevcut digital_twin.hierarchy deseni)
        hierarchy = TwinHierarchy()
        hierarchy.add("neighborhood")
        hierarchy.add("bld_a", parent_id="neighborhood")
        hierarchy.add("bld_b", parent_id="neighborhood")

        registry = DigitalTwinRegistry()
        registry.create(twin_id="bld_a")
        registry.create(twin_id="bld_b")

        demand_registry = BuildingDemandRegistry()
        demand_registry.register(BuildingDemandProfile("bld_a", annual_kwh=8760.0))
        demand_registry.register(BuildingDemandProfile("bld_b", annual_kwh=17520.0))

        total = aggregate_city_demand_kw(hierarchy, registry, demand_registry, "neighborhood", hour=3)
        direct = (
            demand_registry.get("bld_a").hourly_load_kw(3)
            + demand_registry.get("bld_b").hourly_load_kw(3)
        )
        self.assertAlmostEqual(total, direct, places=6)

    def test_hierarchy_aggregation_zero_for_untracked_leaf(self):
        hierarchy = TwinHierarchy()
        hierarchy.add("block")
        hierarchy.add("bld_untracked", parent_id="block")
        registry = DigitalTwinRegistry()
        registry.create(twin_id="bld_untracked")
        demand_registry = BuildingDemandRegistry()  # boş - hiç kayıt yok
        total = aggregate_city_demand_kw(hierarchy, registry, demand_registry, "block", hour=10)
        self.assertEqual(total, 0.0)


class OutagePropagationTests(unittest.TestCase):
    def _simple_network(self) -> NavGraph:
        # sub1 -- sub2 (ayrık, aralarında hat yok)
        # sub1 -> bld1
        # sub2 -> bld2
        return build_power_network_graph(
            substation_ids=["sub1", "sub2"],
            building_ids=["bld1", "bld2"],
            lines=[("sub1", "bld1"), ("sub2", "bld2")],
        )

    def test_isolated_substation_only_affects_its_own_buildings(self):
        graph = self._simple_network()
        engine = OutagePropagationEngine(graph, ["sub1", "sub2"], ["bld1", "bld2"])
        report = engine.propagate(["sub1"])
        self.assertEqual(report.affected_building_ids, ["bld1"])
        self.assertEqual(report.still_powered_building_ids, ["bld2"])
        self.assertTrue(report.hazard_rules.disable_elevators)
        self.assertEqual(report.walk_speed_multiplier, DARK_CORRIDOR_SPEED_MULTIPLIER)

    def test_redundant_connection_prevents_outage(self):
        graph = build_power_network_graph(
            substation_ids=["sub1", "sub2"],
            building_ids=["bld1"],
            lines=[("sub1", "bld1"), ("sub2", "bld1")],
        )
        engine = OutagePropagationEngine(graph, ["sub1", "sub2"], ["bld1"])
        report = engine.propagate(["sub1"])
        self.assertEqual(report.affected_building_ids, [])
        self.assertEqual(report.still_powered_building_ids, ["bld1"])
        self.assertFalse(report.hazard_rules.disable_elevators)
        self.assertEqual(report.walk_speed_multiplier, 1.0)

    def test_no_failures_no_impact(self):
        graph = self._simple_network()
        engine = OutagePropagationEngine(graph, ["sub1", "sub2"], ["bld1", "bld2"])
        report = engine.propagate([])
        self.assertEqual(report.affected_building_ids, [])

    def test_event_bus_emits_power_outage(self):
        graph = self._simple_network()
        bus = EventSystem()
        received = []
        bus.subscribe(str(CityEventType.POWER_OUTAGE.value), lambda e: received.append(e))
        engine = OutagePropagationEngine(graph, ["sub1", "sub2"], ["bld1", "bld2"], bus=bus)
        engine.propagate(["sub1"], source="test")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload["affected_building_ids"], ["bld1"])

    def test_restore_emits_power_restored(self):
        graph = self._simple_network()
        bus = EventSystem()
        received = []
        bus.subscribe(str(CityEventType.POWER_RESTORED.value), lambda e: received.append(e))
        engine = OutagePropagationEngine(graph, ["sub1", "sub2"], ["bld1", "bld2"], bus=bus)
        engine.restore(["sub1"])
        self.assertEqual(len(received), 1)

    def test_missing_nodes_in_lines_are_skipped_not_raised(self):
        graph = build_power_network_graph(
            substation_ids=["sub1"], building_ids=["bld1"],
            lines=[("sub1", "bld1"), ("sub1", "ghost")],
        )
        self.assertFalse(graph.has_node("ghost"))


class MicroclimateTests(unittest.TestCase):
    def test_dense_high_rise_has_higher_index_than_open_low_rise(self):
        dense = UrbanFabricSample(average_building_height_m=40.0, average_street_width_m=8.0,
                                   building_footprint_ratio=0.7)
        open_area = UrbanFabricSample(average_building_height_m=6.0, average_street_width_m=20.0,
                                       building_footprint_ratio=0.1)
        dense_report = estimate_heat_island_index(dense)
        open_report = estimate_heat_island_index(open_area)
        self.assertGreater(dense_report.heat_island_index_c, open_report.heat_island_index_c)

    def test_canopy_coverage_reduces_index(self):
        base = UrbanFabricSample(average_building_height_m=20.0, average_street_width_m=10.0,
                                  building_footprint_ratio=0.5, canopy_coverage_ratio=0.0)
        shaded = UrbanFabricSample(average_building_height_m=20.0, average_street_width_m=10.0,
                                    building_footprint_ratio=0.5, canopy_coverage_ratio=0.6)
        self.assertLess(
            estimate_heat_island_index(shaded).heat_island_index_c,
            estimate_heat_island_index(base).heat_island_index_c,
        )

    def test_index_never_negative_or_above_cap(self):
        extreme = UrbanFabricSample(average_building_height_m=200.0, average_street_width_m=1.0,
                                     building_footprint_ratio=1.0, canopy_coverage_ratio=0.0)
        report = estimate_heat_island_index(extreme)
        self.assertGreaterEqual(report.heat_island_index_c, 0.0)
        self.assertLessEqual(report.heat_island_index_c, 6.0)

    def test_baseline_temperature_propagates_to_local_estimate(self):
        fabric = UrbanFabricSample(average_building_height_m=10.0, average_street_width_m=10.0,
                                    building_footprint_ratio=0.3)
        baseline = HourlyClimateSample(
            time_iso="2024-06-01T12:00", temperature_c=30.0, cloud_cover_pct=10.0,
            shortwave_radiation_wm2=800.0, direct_radiation_wm2=600.0, diffuse_radiation_wm2=200.0,
        )
        report = estimate_heat_island_index(fabric, baseline=baseline)
        self.assertIsNotNone(report.estimated_local_temperature_c)
        self.assertAlmostEqual(
            report.estimated_local_temperature_c, 30.0 + report.heat_island_index_c, places=6,
        )

    def test_no_baseline_leaves_local_estimate_none(self):
        fabric = UrbanFabricSample(average_building_height_m=10.0, average_street_width_m=10.0,
                                    building_footprint_ratio=0.3)
        report = estimate_heat_island_index(fabric)
        self.assertIsNone(report.estimated_local_temperature_c)

    def test_fabric_from_footprint_helper(self):
        fabric = fabric_from_footprint(
            building_heights_m=[10.0, 20.0, 30.0], cell_area_m2=1000.0,
            building_footprint_area_m2=400.0, average_street_width_m=12.0,
        )
        self.assertAlmostEqual(fabric.average_building_height_m, 20.0)
        self.assertAlmostEqual(fabric.building_footprint_ratio, 0.4)


class AirQualityTests(unittest.TestCase):
    def test_more_traffic_yields_higher_index(self):
        light = RoadSegmentTraffic("seg1", length_m=1000.0, vehicles_per_hour=100.0)
        heavy = RoadSegmentTraffic("seg1", length_m=1000.0, vehicles_per_hour=2000.0)
        self.assertGreater(
            estimate_segment_air_quality(heavy).indicative_index_0_100,
            estimate_segment_air_quality(light).indicative_index_0_100,
        )

    def test_zero_traffic_zero_emissions(self):
        report = estimate_segment_air_quality(RoadSegmentTraffic("seg1", 500.0, 0.0))
        self.assertEqual(report.vehicle_km_per_hour, 0.0)
        self.assertEqual(report.indicative_index_0_100, 0.0)

    def test_index_capped_at_100(self):
        extreme = RoadSegmentTraffic("seg1", length_m=100000.0, vehicles_per_hour=100000.0)
        report = estimate_segment_air_quality(extreme)
        self.assertLessEqual(report.indicative_index_0_100, 100.0)

    def test_network_batch_matches_individual_calls(self):
        segments = [
            RoadSegmentTraffic("a", 800.0, 300.0),
            RoadSegmentTraffic("b", 1200.0, 900.0),
        ]
        batch = estimate_network_air_quality(segments)
        individual = [estimate_segment_air_quality(s) for s in segments]
        self.assertEqual(
            [r.indicative_index_0_100 for r in batch],
            [r.indicative_index_0_100 for r in individual],
        )


class NoiseEstimateTests(unittest.TestCase):
    def test_traffic_noise_increases_with_flow(self):
        self.assertGreater(traffic_noise_db(2000.0), traffic_noise_db(100.0))

    def test_zero_traffic_zero_db(self):
        self.assertEqual(traffic_noise_db(0.0), 0.0)
        self.assertEqual(traffic_noise_db(-5.0), 0.0)

    def test_crowd_noise_increases_with_density(self):
        self.assertGreater(crowd_noise_db(3.0), crowd_noise_db(0.2))

    def test_combined_db_at_least_as_loud_as_louder_source(self):
        report = estimate_noise(vehicles_per_hour=1000.0, density_people_per_m2=1.0)
        louder = max(report.traffic_db, report.crowd_db)
        self.assertGreaterEqual(report.combined_db, louder - 1e-6)

    def test_only_traffic_given_crowd_none(self):
        report = estimate_noise(vehicles_per_hour=500.0)
        self.assertIsNone(report.crowd_db)
        self.assertIsNotNone(report.traffic_db)
        self.assertAlmostEqual(report.combined_db, report.traffic_db, places=6)

    def test_no_inputs_all_none(self):
        report = estimate_noise()
        self.assertIsNone(report.traffic_db)
        self.assertIsNone(report.crowd_db)
        self.assertIsNone(report.combined_db)


if __name__ == "__main__":
    unittest.main()
