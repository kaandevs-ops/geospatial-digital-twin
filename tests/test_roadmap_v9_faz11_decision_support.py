"""ROADMAP_V9 Faz XI — Karar Destek Motoru (Katman 9, nihai teslimat) testleri.

Kapsam: senaryo karşılaştırma (madde 1), otomatik öneri motoru (madde 2),
duyarlılık analizi (madde 3), what-if yeniden koşum (madde 5),
erişilebilirlik uyarı motoru (madde 6, saf `RecommendationEngine` kısmı).
"""

from __future__ import annotations

import unittest

from harita.analysis_engine.decision_support import (
    INDICATIVE_DISCLAIMER,
    Recommendation,
    RecommendationEngine,
    ScenarioComparison,
    SensitivityAnalyzer,
    compare_capacity_reports,
    compare_evacuation_results,
    what_if_close_exit_and_rerun,
)
from harita.core_engine.geometry_engine import Point2D
from harita.mobility.crowd_simulation import (
    Agent, EvacuationResult, SocialForceModel, spawn_random_agents,
)
from harita.mobility.crowd_simulation.capacity_analysis import (
    CapacityAnalysisReport, CapacityRunResult,
)
from harita.mobility.pathfinding import NavGraph


def _make_result(evac_time: float, evacuated: int, bottleneck: int | None = None) -> EvacuationResult:
    return EvacuationResult(
        total_agents=evacuated, evacuated_count=evacuated, evacuation_time_s=evac_time,
        per_agent_time_s={}, timed_out=False, bottleneck_peak_count=bottleneck,
    )


def _make_capacity_run(count: int, evac_time: float, within: bool | None, timed_out: bool = False) -> CapacityRunResult:
    return CapacityRunResult(
        agent_count=count, evacuation_time_s=evac_time, evacuated_count=count,
        total_agents=count, timed_out=timed_out, bottleneck_cell=None,
        bottleneck_count=None, threshold_s=120.0, within_threshold=within,
    )


def _make_capacity_report(runs: list[CapacityRunResult]) -> CapacityAnalysisReport:
    return CapacityAnalysisReport(
        building_type="residential", exit_width_m=1.2,
        regulation_profile_name="default", runs=runs,
    )


class TestScenarioComparison(unittest.TestCase):
    def test_delta_and_pct_change(self):
        c = ScenarioComparison(label="x", metric_name="evacuation_time_s", before_value=100.0, after_value=80.0)
        self.assertAlmostEqual(c.delta, -20.0)
        self.assertAlmostEqual(c.pct_change, -20.0)

    def test_pct_change_none_when_before_zero(self):
        c = ScenarioComparison(label="x", metric_name="m", before_value=0.0, after_value=5.0)
        self.assertIsNone(c.pct_change)

    def test_to_dict_rounds_values(self):
        c = ScenarioComparison(label="x", metric_name="m", before_value=10.0, after_value=12.3456)
        d = c.to_dict()
        self.assertEqual(d["after_value"], 12.346)

    def test_compare_evacuation_results_basic_fields(self):
        before = _make_result(120.0, 40, bottleneck=10)
        after = _make_result(90.0, 40, bottleneck=5)
        comparisons = compare_evacuation_results(before, after, label="senaryo A")
        names = {c.metric_name for c in comparisons}
        self.assertIn("evacuation_time_s", names)
        self.assertIn("evacuated_count", names)
        self.assertIn("bottleneck_peak_count", names)
        time_cmp = next(c for c in comparisons if c.metric_name == "evacuation_time_s")
        self.assertAlmostEqual(time_cmp.delta, -30.0)

    def test_compare_evacuation_results_without_bottleneck(self):
        before = _make_result(120.0, 40)
        after = _make_result(90.0, 40)
        comparisons = compare_evacuation_results(before, after)
        names = {c.metric_name for c in comparisons}
        self.assertNotIn("bottleneck_peak_count", names)

    def test_compare_capacity_reports_matches_common_agent_counts(self):
        before = _make_capacity_report([
            _make_capacity_run(20, 50.0, True), _make_capacity_run(40, 95.0, True),
        ])
        after = _make_capacity_report([
            _make_capacity_run(20, 40.0, True), _make_capacity_run(60, 200.0, False),
        ])
        comparisons = compare_capacity_reports(before, after)
        self.assertEqual(len(comparisons), 1)
        self.assertIn("20 kişi", comparisons[0].label)
        self.assertAlmostEqual(comparisons[0].delta, -10.0)


class TestRecommendationEngine(unittest.TestCase):
    def test_flags_over_threshold_run_as_critical(self):
        report = _make_capacity_report([_make_capacity_run(80, 150.0, False)])
        recs = RecommendationEngine.from_capacity_report(report)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].severity, "critical")

    def test_flags_timed_out_run_as_warning(self):
        report = _make_capacity_report([_make_capacity_run(80, 600.0, None, timed_out=True)])
        recs = RecommendationEngine.from_capacity_report(report)
        self.assertEqual(recs[0].severity, "warning")

    def test_all_within_threshold_gives_info(self):
        report = _make_capacity_report([_make_capacity_run(20, 40.0, True)])
        recs = RecommendationEngine.from_capacity_report(report)
        self.assertEqual(recs[0].severity, "info")

    def test_accessibility_impact_no_room_graph(self):
        recs = RecommendationEngine.from_accessibility_impact(0, room_graph_available=False)
        self.assertEqual(recs[0].severity, "warning")

    def test_accessibility_impact_unreachable_rooms_critical(self):
        recs = RecommendationEngine.from_accessibility_impact(3, room_graph_available=True)
        self.assertEqual(recs[0].severity, "critical")
        self.assertGreaterEqual(len(recs[0].suggestions), 1)

    def test_accessibility_impact_all_reachable_info(self):
        recs = RecommendationEngine.from_accessibility_impact(0, room_graph_available=True)
        self.assertEqual(recs[0].severity, "info")


class TestSensitivityAnalyzer(unittest.TestCase):
    def test_scan_parameter_calls_run_fn_per_value(self):
        calls = []

        def run_fn(v):
            calls.append(v)
            return v * 2

        result = SensitivityAnalyzer.scan_parameter("agent_count", [10.0, 20.0, 30.0], run_fn)
        self.assertEqual(calls, [10.0, 20.0, 30.0])
        self.assertEqual(result.outcomes, [20.0, 40.0, 60.0])
        self.assertAlmostEqual(result.outcome_range(), 40.0)

    def test_rank_parameters_orders_by_range_desc(self):
        low = SensitivityAnalyzer.scan_parameter("p1", [1.0, 2.0], lambda v: v)
        high = SensitivityAnalyzer.scan_parameter("p2", [1.0, 100.0], lambda v: v)
        ranked = SensitivityAnalyzer.rank_parameters([low, high])
        self.assertEqual(ranked[0].parameter_name, "p2")

    def test_empty_outcomes_range_is_zero(self):
        result = SensitivityAnalyzer.scan_parameter("p", [], lambda v: v)
        self.assertEqual(result.outcome_range(), 0.0)


class TestWhatIfCloseExitAndRerun(unittest.TestCase):
    def test_closing_exit_reroutes_and_still_completes(self):
        graph = NavGraph()
        # Basit koridor: start -> mid -> exit_a / exit_b
        graph.add_node("start", Point2D(0.0, 0.0))
        graph.add_node("mid", Point2D(5.0, 0.0))
        graph.add_node("exit_a", Point2D(10.0, 0.0))
        graph.add_node("exit_b", Point2D(5.0, 5.0))
        graph.add_edge("start", "mid", cost=5.0)
        graph.add_edge("mid", "start", cost=5.0)
        graph.add_edge("mid", "exit_a", cost=5.0)
        graph.add_edge("exit_a", "mid", cost=5.0)
        graph.add_edge("mid", "exit_b", cost=5.0)
        graph.add_edge("exit_b", "mid", cost=5.0)

        agents = spawn_random_agents(3, area_min=Point2D(-0.5, -0.5), area_max=Point2D(0.5, 0.5), goal=Point2D(10.0, 0.0), seed=1)
        node_of_agent = lambda a: "start"  # noqa: E731 - test yardımcı fonksiyonu

        result = what_if_close_exit_and_rerun(
            graph=graph, exits=["exit_a", "exit_b"], exit_to_close="exit_a",
            agents=agents, node_of_agent=node_of_agent, max_time_s=60.0,
        )
        self.assertIsInstance(result, EvacuationResult)
        self.assertEqual(result.total_agents, 3)
        # exit_a'ya giden kenarlar kapatıldığından kalan tek çıkış exit_b'dir.
        self.assertTrue(graph.has_node("exit_b"))


class TestDisclaimer(unittest.TestCase):
    def test_disclaimer_present_and_nonempty(self):
        self.assertTrue(INDICATIVE_DISCLAIMER)
        self.assertIn("gösterge", INDICATIVE_DISCLAIMER)


if __name__ == "__main__":
    unittest.main()
