from __future__ import annotations

import unittest

from harita.commerce_props import CommercePropType
from harita.commerce_props.city_event_simulation import (
    CityEventCategory,
    CityEventProfile,
    CityEventSimulator,
    attendance_curve,
)
from harita.commerce_props.economic_resilience import (
    CommercialAreaResilience,
    build_resilience_curve,
    closure_days_for_risk_level,
    complete_commercial_recovery,
    start_commercial_disruption,
)
from harita.commerce_props.footfall_model import (
    FOOTFALL_HOURLY_WEIGHTS,
    FootfallProfile,
    demand_multiplier_by_hour_from_od,
)
from harita.extensibility.city_events import CityEventType
from harita.extensibility.event_system import EventSystem
from harita.hazard_data.resilience_timeline import SYSTEM_EVENT_PAIRS, build_resilience_report
from harita.hazard_data.risk_scoring import RiskLevel
from harita.population.activity_model import ActivityType, ODDemandEntry


class FootfallModelTests(unittest.TestCase):
    def test_all_prop_types_have_weight_tables(self):
        for prop_type in CommercePropType:
            self.assertIn(prop_type, FOOTFALL_HOURLY_WEIGHTS)
            self.assertEqual(len(FOOTFALL_HOURLY_WEIGHTS[prop_type]), 24)

    def test_daily_curve_conserves_average(self):
        profile = FootfallProfile(
            "shop1", CommercePropType.SUPERMARKET, baseline_daily_visits=2400.0
        )
        curve = profile.daily_curve()
        self.assertAlmostEqual(sum(curve) / 24.0, 100.0, places=6)

    def test_mall_evening_peak_higher_than_early_morning(self):
        profile = FootfallProfile(
            "mall1", CommercePropType.SHOPPING_MALL, baseline_daily_visits=4800.0
        )
        self.assertGreater(profile.hourly_visits(18), profile.hourly_visits(3))

    def test_market_morning_peak_higher_than_evening(self):
        profile = FootfallProfile(
            "mkt1", CommercePropType.MARKET_STALL, baseline_daily_visits=1200.0
        )
        self.assertGreater(profile.hourly_visits(9), profile.hourly_visits(21))

    def test_invalid_hour_raises(self):
        profile = FootfallProfile("s1", CommercePropType.MARKET_STALL, baseline_daily_visits=100.0)
        with self.assertRaises(ValueError):
            profile.hourly_visits(24)

    def test_peak_hour_matches_curve_max(self):
        profile = FootfallProfile(
            "s1", CommercePropType.OUTDOOR_SEATING, baseline_daily_visits=500.0
        )
        hour, visits = profile.peak_hour()
        curve = profile.daily_curve()
        self.assertEqual(visits, max(curve))
        self.assertEqual(hour, curve.index(visits))

    def test_od_demand_multiplier_empty_entries_is_neutral(self):
        multipliers = demand_multiplier_by_hour_from_od([])
        self.assertEqual(set(multipliers.keys()), set(range(24)))
        self.assertTrue(all(v == 1.0 for v in multipliers.values()))

    def test_od_demand_multiplier_reflects_concentration(self):
        entries = [
            ODDemandEntry("ind1", "hh1", 17.0, ActivityType.HOME, ActivityType.LOCAL_ERRAND),
            ODDemandEntry("ind2", "hh1", 17.0, ActivityType.HOME, ActivityType.SOCIAL_EVENING),
            ODDemandEntry(
                "ind3", "hh2", 3.0, ActivityType.HOME, ActivityType.WORK
            ),  # ilgisiz, sayılmaz
        ]
        multipliers = demand_multiplier_by_hour_from_od(entries)
        self.assertGreater(multipliers[17], multipliers[3])

    def test_demand_multiplier_applied_to_curve(self):
        profile = FootfallProfile("s1", CommercePropType.SUPERMARKET, baseline_daily_visits=2400.0)
        curve_flat = profile.daily_curve()
        curve_boosted = profile.daily_curve({12: 3.0})
        self.assertAlmostEqual(curve_boosted[12], curve_flat[12] * 3.0, places=6)
        self.assertAlmostEqual(curve_boosted[5], curve_flat[5], places=6)


class CityEventSimulationTests(unittest.TestCase):
    def test_attendance_curve_ramps_up_and_down(self):
        profile = CityEventProfile(
            event_id="ev1",
            category=CityEventCategory.CONCERT,
            location_ref="stadium1",
            expected_attendance=10000,
            start_hour=19.0,
            duration_h=3.0,
            ramp_fraction=0.2,
        )
        curve = attendance_curve(profile, samples=11)
        self.assertEqual(curve[0].attendance, 0)
        self.assertEqual(curve[-1].attendance, 0)
        self.assertEqual(max(p.attendance for p in curve), 10000)

    def test_attendance_curve_requires_min_samples(self):
        profile = CityEventProfile("ev1", CityEventCategory.FESTIVAL, "square1", 500, 10.0, 5.0)
        with self.assertRaises(ValueError):
            attendance_curve(profile, samples=1)

    def test_simulator_emits_crowd_surge(self):
        bus = EventSystem()
        received = []
        bus.subscribe(str(CityEventType.CROWD_SURGE.value), lambda e: received.append(e))
        simulator = CityEventSimulator(bus)
        profile = CityEventProfile(
            "ev1", CityEventCategory.SPORTS_MATCH, "stadium1", 30000, 20.0, 2.0
        )
        simulator.trigger(profile)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload["expected_attendance"], 30000)
        self.assertEqual(received[0].payload["category"], "sports_match")

    def test_simulator_logs_triggered_events(self):
        simulator = CityEventSimulator()
        profile = CityEventProfile("ev1", CityEventCategory.MARKET_FAIR, "market1", 2000, 9.0, 4.0)
        simulator.trigger(profile)
        self.assertEqual(len(simulator.triggered_log), 1)
        self.assertEqual(simulator.triggered_log[0].event_id, "ev1")

    def test_no_bus_does_not_raise(self):
        simulator = CityEventSimulator(bus=None)
        profile = CityEventProfile("ev1", CityEventCategory.CONCERT, "hall1", 500, 21.0, 2.0)
        curve = simulator.trigger(profile)
        self.assertTrue(len(curve) > 0)


class EconomicResilienceTests(unittest.TestCase):
    def test_higher_risk_level_has_longer_closure(self):
        self.assertLess(
            closure_days_for_risk_level(RiskLevel.LOW),
            closure_days_for_risk_level(RiskLevel.VERY_HIGH),
        )

    def test_operational_fraction_starts_low_ends_high(self):
        area = CommercialAreaResilience(
            "mall_a", RiskLevel.MODERATE, closure_days=7.0, disrupted_at=0.0
        )
        start_fraction = area.operational_fraction_at(0.0)
        end_fraction = area.operational_fraction_at(30.0 * 86400.0)
        self.assertLess(start_fraction, 0.5)
        self.assertGreater(end_fraction, 0.9)

    def test_before_disruption_fully_operational(self):
        area = CommercialAreaResilience(
            "mall_a", RiskLevel.HIGH, closure_days=30.0, disrupted_at=1000.0
        )
        self.assertEqual(area.operational_fraction_at(500.0), 1.0)

    def test_is_recovered_at_threshold(self):
        area = CommercialAreaResilience("mall_a", RiskLevel.LOW, closure_days=1.0, disrupted_at=0.0)
        self.assertTrue(area.is_recovered_at(10.0 * 86400.0))
        self.assertFalse(area.is_recovered_at(0.0))

    def test_build_resilience_curve_monotonic_increasing(self):
        area = CommercialAreaResilience(
            "mall_a", RiskLevel.MODERATE, closure_days=7.0, disrupted_at=0.0
        )
        report = build_resilience_curve(area, sample_count=10)
        fractions = [f for _, f in report.samples]
        self.assertEqual(fractions, sorted(fractions))

    def test_start_and_complete_disruption_emits_events_and_resilience_report_reads_them(self):
        bus = EventSystem()
        area = start_commercial_disruption(bus, "bazaar_1", RiskLevel.HIGH, source="test")
        self.assertEqual(area.risk_level, RiskLevel.HIGH)
        self.assertEqual(area.closure_days, closure_days_for_risk_level(RiskLevel.HIGH))
        complete_commercial_recovery(bus, area, source="test")

        report = build_resilience_report(bus, window_start=0.0)
        commerce_records = report.by_system("ticaret")
        self.assertEqual(len(commerce_records), 1)
        self.assertTrue(commerce_records[0].recovered)

    def test_ticaret_row_present_in_system_event_pairs(self):
        names = [pair.system_name for pair in SYSTEM_EVENT_PAIRS]
        self.assertIn("ticaret", names)


if __name__ == "__main__":
    unittest.main()
