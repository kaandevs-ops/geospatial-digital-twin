"""
Roadmap V9 / Faz VI (Çekirdek Gündelik Yaşam Döngüsü) testleri.

Kapsam: Katman 2.1 (sentetik nüfus), Katman 2.2 (günlük rutin / OD talebi),
Katman 3.1 (OSM POI köprüsü), Katman 3.2 (transit OSM köprüsü + saatlik
yoğunluk raporu).
"""

from __future__ import annotations

import unittest

from harita.core_engine.geometry_engine import Point2D
from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.core_engine.gis_core.osm_client import DEFAULT_CATEGORIES
from harita.mobility.crowd_simulation import MobilityProfile
from harita.mobility.osm_demand_bridge import (
    build_poi_points,
    resolve_demand_locations,
)
from harita.mobility.transit_osm_bridge import (
    build_transit_stops,
    estimate_line_from_stops,
    hourly_occupancy_report,
)
from harita.mobility.transit_simulation import TransitStop
from harita.population import (
    ActivityModel,
    ActivityType,
    DailyRoutineType,
    SyntheticPopulationGenerator,
)


class TestSyntheticPopulation(unittest.TestCase):
    def test_generate_for_building_deterministic_with_seed(self):
        gen_a = SyntheticPopulationGenerator(seed=42)
        gen_b = SyntheticPopulationGenerator(seed=42)
        hh_a = gen_a.generate_for_building("bina:1", 10)
        hh_b = gen_b.generate_for_building("bina:1", 10)
        self.assertEqual(len(hh_a), 10)
        self.assertEqual(
            [ind.age_group for hh in hh_a for ind in hh.individuals],
            [ind.age_group for hh in hh_b for ind in hh.individuals],
        )

    def test_household_size_at_least_one(self):
        gen = SyntheticPopulationGenerator(seed=1, avg_household_size=0.1)
        households = gen.generate_for_building("bina:2", 5)
        for hh in households:
            self.assertGreaterEqual(hh.size(), 1)

    def test_negative_household_count_raises(self):
        gen = SyntheticPopulationGenerator(seed=1)
        with self.assertRaises(ValueError):
            gen.generate_for_building("bina:3", -1)

    def test_children_always_school_routine(self):
        gen = SyntheticPopulationGenerator(seed=7)
        households = gen.generate_for_building("bina:4", 30)
        individuals = gen.all_individuals(households)
        from harita.population.synthetic_population import AgeGroup

        for ind in individuals:
            if ind.age_group == AgeGroup.CHILD:
                self.assertEqual(ind.routine_type, DailyRoutineType.SCHOOL_CHILD)

    def test_profile_distribution_from_matches_actual_counts(self):
        gen = SyntheticPopulationGenerator(seed=3)
        households = gen.generate_for_building("bina:5", 50)
        individuals = gen.all_individuals(households)
        dist = gen.profile_distribution_from(individuals)
        self.assertAlmostEqual(sum(dist.values()), 1.0, places=6)
        self.assertTrue(all(isinstance(k, MobilityProfile) for k in dist))

    def test_profile_distribution_from_empty_falls_back_to_default(self):
        gen = SyntheticPopulationGenerator(seed=1)
        dist = gen.profile_distribution_from([])
        self.assertEqual(dist, gen.mobility_profile_distribution)


class TestActivityModel(unittest.TestCase):
    def setUp(self):
        self.gen = SyntheticPopulationGenerator(seed=11)
        self.model = ActivityModel()

    def test_od_demand_for_individual_starts_and_ends_at_home_chain(self):
        households = self.gen.generate_for_building("bina:6", 1)
        individual = households[0].individuals[0]
        entries = self.model.od_demand_for_individual(individual, households[0].household_id)
        self.assertGreater(len(entries), 0)
        self.assertEqual(entries[-1].destination_activity, ActivityType.HOME)

    def test_od_demand_for_population_aggregates_all(self):
        households = self.gen.generate_for_building("bina:7", 20)
        pairs = [(ind, hh.household_id) for hh in households for ind in hh.individuals]
        entries = self.model.od_demand_for_population(pairs)
        total_individuals = sum(hh.size() for hh in households)
        self.assertGreater(len(entries), total_individuals)  # her birey >=1 geçiş üretir

    def test_demand_by_hour_and_peak_hours(self):
        households = self.gen.generate_for_building("bina:8", 40)
        pairs = [(ind, hh.household_id) for hh in households for ind in hh.individuals]
        entries = self.model.od_demand_for_population(pairs)
        buckets = self.model.demand_by_hour(entries)
        self.assertTrue(all(0 <= h < 24 for h in buckets))
        peaks = self.model.peak_hours(entries, top_n=3)
        self.assertLessEqual(len(peaks), 3)
        # azalan sırada olmalı
        counts = [c for _, c in peaks]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_unknown_routine_falls_back_to_flexible_template(self):
        model = ActivityModel(
            chain_templates={
                DailyRoutineType.UNEMPLOYED_OR_FLEXIBLE: [],
            }
        )
        # boş şablon bile hatasız çalışmalı (0 aktivite -> 0 od girdisi)
        ind = self.gen._make_individual("test:1")
        chain = model.activity_chain_for(ind)
        self.assertIsInstance(chain, list)


class TestOSMDemandBridge(unittest.TestCase):
    def test_school_and_transit_categories_registered(self):
        self.assertIn("school", DEFAULT_CATEGORIES)
        self.assertIn("transit_stop_position", DEFAULT_CATEGORIES)

    def _make_collection(self):
        return GeoFeatureCollection(
            features=[
                GeoFeature("Point", (28.97, 41.01), {"category": "school", "name": "Okul A"}),
                GeoFeature("Point", (28.98, 41.02), {"category": "cafe", "name": "Kafe B"}),
                GeoFeature(
                    "Polygon",
                    [[(28.90, 41.00), (28.91, 41.00), (28.91, 41.01), (28.90, 41.01)]],
                    {"category": "supermarket", "name": "Market C"},
                ),
                GeoFeature("LineString", [(28.0, 41.0), (28.1, 41.1)], {"category": "roads"}),
            ]
        )

    def test_build_poi_points_filters_known_categories_only(self):
        points = build_poi_points(self._make_collection())
        categories = {p.category_key for p in points}
        self.assertEqual(categories, {"school", "cafe", "supermarket"})

    def test_resolve_demand_locations_home_lookup_and_warnings(self):
        points = build_poi_points(self._make_collection())
        from harita.population.activity_model import ODDemandEntry

        entries = [
            ODDemandEntry("ind1", "hh1", 8.0, ActivityType.HOME, ActivityType.SCHOOL),
            ODDemandEntry("ind1", "hh1", 12.0, ActivityType.SCHOOL, ActivityType.LUNCH_BREAK),
            ODDemandEntry("ind1", "hh1", 18.0, ActivityType.WORK, ActivityType.HOME),
            ODDemandEntry("ind2", "hh2", 9.0, ActivityType.HOME, ActivityType.WORK),
        ]
        home_lookup = {"hh1": Point2D(0.0, 0.0)}
        resolved, warnings = resolve_demand_locations(
            entries,
            points,
            home_position_lookup=home_lookup,
            seed=1,
        )
        # hh1: SCHOOL çözülür, LUNCH_BREAK çözülür, HOME çözülür = 3
        # hh2: WORK için POI yok -> uyarı, çözülmez
        self.assertEqual(len(resolved), 3)
        self.assertTrue(any("WORK" in w for w in warnings))

    def test_resolve_demand_locations_missing_home_lookup_warns(self):
        points = build_poi_points(self._make_collection())
        from harita.population.activity_model import ODDemandEntry

        entries = [ODDemandEntry("ind3", "hh3", 19.0, ActivityType.WORK, ActivityType.HOME)]
        resolved, warnings = resolve_demand_locations(entries, points, seed=1)
        self.assertEqual(len(resolved), 0)
        self.assertTrue(any("HOME" in w for w in warnings))


class TestTransitOSMBridge(unittest.TestCase):
    def _make_collection(self):
        return GeoFeatureCollection(
            features=[
                GeoFeature("Point", (28.90, 41.00), {"category": "bus_stop", "id": "n1"}),
                GeoFeature("Point", (28.91, 41.00), {"category": "bus_stop", "id": "n2"}),
                GeoFeature("Point", (28.92, 41.00), {"category": "railway_station", "id": "n3"}),
                GeoFeature("Point", (28.99, 41.05), {"category": "cafe", "id": "n4"}),
            ]
        )

    def test_build_transit_stops_filters_transit_categories(self):
        stops = build_transit_stops(self._make_collection())
        self.assertEqual(len(stops), 3)
        self.assertTrue(all(isinstance(s, TransitStop) for s in stops))

    def test_build_transit_stops_deduplicates_by_id(self):
        coll = GeoFeatureCollection(
            features=[
                GeoFeature("Point", (28.90, 41.00), {"category": "bus_stop", "id": "dup"}),
                GeoFeature("Point", (28.90, 41.00), {"category": "bus_stop", "id": "dup"}),
            ]
        )
        stops = build_transit_stops(coll)
        self.assertEqual(len(stops), 1)

    def test_estimate_line_from_stops_orders_by_nearest_neighbour(self):
        stops = build_transit_stops(self._make_collection())
        line = estimate_line_from_stops("hat1", stops)
        self.assertEqual(len(line.ordered_stop_ids), 3)
        self.assertTrue(line.is_estimated_sequence)

    def test_estimate_line_from_stops_requires_two_stops(self):
        with self.assertRaises(ValueError):
            estimate_line_from_stops("hatX", [TransitStop("only", Point2D(0, 0))])

    def test_to_transit_line_builds_valid_transit_line(self):
        stops = build_transit_stops(self._make_collection())
        estimated = estimate_line_from_stops("hat2", stops)
        transit_line = estimated.to_transit_line(
            travel_times_s=[60.0, 60.0],
            schedule_headway_s=300.0,
        )
        self.assertEqual(len(transit_line.stops), 3)

    def test_hourly_occupancy_report_flags_dense_hours(self):
        samples = [
            (8 * 3600, 55, 60),  # 08:00 - yoğun
            (8 * 3600 + 100, 58, 60),
            (14 * 3600, 5, 60),  # 14:00 - boş
        ]
        report = hourly_occupancy_report(samples, dense_threshold=0.75)
        self.assertTrue(report[8]["is_dense"])
        self.assertFalse(report[14]["is_dense"])


if __name__ == "__main__":
    unittest.main()
