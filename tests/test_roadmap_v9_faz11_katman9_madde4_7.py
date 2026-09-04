"""ROADMAP_V9 Katman 9 madde 4 (rapor anlatıcı entegrasyonu) + madde 7
(video/GIF export) — bu iki madde ilk Faz XI teslimatında eksik kalmıştı,
bu test dosyası kapatılan boşluğu doğrular.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from harita.analysis_engine.decision_support import ScenarioComparison
from harita.analysis_engine.result_narrator import (
    narrate_capacity_report,
    narrate_evacuation_result,
    narrate_scenario_comparison,
)
from harita.mobility.crowd_simulation import EvacuationResult
from harita.mobility.crowd_simulation.capacity_analysis import (
    CapacityAnalysisReport, CapacityRunResult,
)
from harita.mobility.simulation_recorder import AgentFrameState, AgentSnapshot, Keyframe, SimulationRecorder
from harita.render_engine.animation_export import (
    AnimationExportError,
    compute_export_bounds,
    export_keyframes_gif,
    export_keyframes_svg_sequence,
    render_keyframe_svg,
)


def _sample_keyframes() -> list[Keyframe]:
    return [
        Keyframe(t=0.0, agents=[
            AgentSnapshot(agent_id=1, x=0.0, y=0.0, state=AgentFrameState.MOVING),
            AgentSnapshot(agent_id=2, x=1.0, y=1.0, state=AgentFrameState.WAITING),
        ]),
        Keyframe(t=0.5, agents=[
            AgentSnapshot(agent_id=1, x=1.0, y=0.5, state=AgentFrameState.MOVING),
            AgentSnapshot(agent_id=2, x=1.0, y=1.0, state=AgentFrameState.EVACUATED),
        ]),
        Keyframe(t=1.0, agents=[
            AgentSnapshot(agent_id=1, x=2.0, y=1.0, state=AgentFrameState.PANIC),
        ]),
    ]


class TestNarrateEvacuationResult(unittest.TestCase):
    def test_basic_summary_contains_key_numbers(self):
        result = EvacuationResult(
            total_agents=87, evacuated_count=87, evacuation_time_s=142.0,
            per_agent_time_s={}, timed_out=False,
        )
        text = narrate_evacuation_result(result)
        self.assertIn("87", text)
        self.assertIn("142", text)

    def test_timed_out_mentioned(self):
        result = EvacuationResult(
            total_agents=50, evacuated_count=30, evacuation_time_s=600.0,
            per_agent_time_s={}, timed_out=True,
        )
        text = narrate_evacuation_result(result)
        self.assertIn("tamamlanamadı", text)

    def test_bottleneck_mentioned_when_present(self):
        result = EvacuationResult(
            total_agents=50, evacuated_count=50, evacuation_time_s=90.0,
            per_agent_time_s={}, timed_out=False, bottleneck_peak_count=12,
            bottleneck_peak_time_s=45.0,
        )
        text = narrate_evacuation_result(result)
        self.assertIn("darboğaz", text)
        self.assertIn("12", text)

    def test_never_raises_never_empty(self):
        text = narrate_evacuation_result(EvacuationResult(
            total_agents=0, evacuated_count=0, evacuation_time_s=0.0,
            per_agent_time_s={}, timed_out=False,
        ))
        self.assertTrue(text)


class TestNarrateCapacityReport(unittest.TestCase):
    def test_flags_over_threshold_scenarios(self):
        report = CapacityAnalysisReport(
            building_type="residential", exit_width_m=1.2, regulation_profile_name="default",
            runs=[
                CapacityRunResult(agent_count=20, evacuation_time_s=40.0, evacuated_count=20,
                                   total_agents=20, timed_out=False, bottleneck_cell=None,
                                   bottleneck_count=None, threshold_s=120.0, within_threshold=True),
                CapacityRunResult(agent_count=80, evacuation_time_s=150.0, evacuated_count=80,
                                   total_agents=80, timed_out=False, bottleneck_cell=None,
                                   bottleneck_count=None, threshold_s=120.0, within_threshold=False),
            ],
        )
        text = narrate_capacity_report(report)
        self.assertIn("80", text)
        self.assertIn("eşiğini aşıyor", text)

    def test_empty_runs_does_not_raise(self):
        report = CapacityAnalysisReport(
            building_type="residential", exit_width_m=1.2, regulation_profile_name="default",
            runs=[],
        )
        text = narrate_capacity_report(report)
        self.assertTrue(text)


class TestNarrateScenarioComparison(unittest.TestCase):
    def test_reports_direction_of_change(self):
        comparisons = [
            ScenarioComparison(label="x", metric_name="tahliye süresi", before_value=100.0, after_value=85.0),
            ScenarioComparison(label="x", metric_name="enerji talebi", before_value=100.0, after_value=108.0),
        ]
        text = narrate_scenario_comparison(comparisons)
        self.assertIn("azaldı", text)
        self.assertIn("arttı", text)

    def test_empty_list_handled(self):
        text = narrate_scenario_comparison([])
        self.assertTrue(text)


class TestAnimationExportSvg(unittest.TestCase):
    def test_compute_export_bounds_covers_all_agents(self):
        bounds = compute_export_bounds(_sample_keyframes(), margin=0.0)
        self.assertLessEqual(bounds.min_x, 0.0)
        self.assertGreaterEqual(bounds.max_x, 2.0)

    def test_render_keyframe_svg_contains_circles(self):
        keyframes = _sample_keyframes()
        bounds = compute_export_bounds(keyframes)
        svg = render_keyframe_svg(keyframes[0], bounds)
        self.assertIn("<svg", svg)
        self.assertEqual(svg.count("<circle"), 2)

    def test_export_svg_sequence_writes_one_file_per_keyframe(self):
        with TemporaryDirectory() as tmp:
            paths = export_keyframes_svg_sequence(_sample_keyframes(), tmp)
            self.assertEqual(len(paths), 3)
            for p in paths:
                self.assertTrue(p.exists())
                self.assertIn("<svg", p.read_text(encoding="utf-8"))

    def test_export_accepts_simulation_recorder_directly(self):
        recorder = SimulationRecorder(keyframe_interval_s=0.5)
        for kf in _sample_keyframes():
            recorder._keyframes.append(kf)  # test yardımcı - iç durumu doğrudan besler
        with TemporaryDirectory() as tmp:
            paths = export_keyframes_svg_sequence(recorder, tmp)
            self.assertEqual(len(paths), 3)

    def test_empty_keyframes_raises_clear_error(self):
        with self.assertRaises(AnimationExportError):
            export_keyframes_svg_sequence([], "/tmp/should_not_be_created")


class TestAnimationExportGif(unittest.TestCase):
    def test_export_gif_creates_valid_animated_file(self):
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "evac.gif"
            result_path = export_keyframes_gif(_sample_keyframes(), out)
            self.assertTrue(result_path.exists())
            self.assertGreater(result_path.stat().st_size, 0)
            try:
                from PIL import Image
                with Image.open(result_path) as img:
                    self.assertEqual(img.format, "GIF")
                    frame_count = 0
                    try:
                        while True:
                            img.seek(frame_count)
                            frame_count += 1
                    except EOFError:
                        pass
                    self.assertEqual(frame_count, 3)
            except ImportError:
                pass  # Pillow yoksa yalnızca dosyanın varlığı doğrulanır


if __name__ == "__main__":
    unittest.main()
