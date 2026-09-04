"""ROADMAP_V5 - Track Q (Q1-Q3): CI'ya bağlı görsel regresyon +
kalite dashboard + performans eşiği otomasyonu testleri.

Bu dosya `scripts/quality_dashboard.py` (Q2) ve
`scripts/perf_regression_gate.py` (Q3) script'lerinin **saf mantığını**
doğrular - `.github/workflows/quality_ci.yml` (Q1) dosyasının kendisi bir
GitHub Actions runner'ı olmadan bu sandbox'ta tetiklenemez (bkz. dosyanın
başındaki "DÜRÜST NOT"), ama içindeki her adımın çağırdığı script burada
gerçek girdilerle test ediliyor.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml  # type: ignore

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import quality_dashboard as qd  # noqa: E402
import perf_regression_gate as prg  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "quality_ci.yml"


# ---------------------------------------------------------------------- #
# Q2 - quality_dashboard.py
# ---------------------------------------------------------------------- #

class TestQualityDashboard:
    def test_snapshot_covers_all_demo_cases(self):
        snapshot = qd.run_dashboard_snapshot()
        assert snapshot["case_count"] == 5  # visual_regression._DEMO_CASES ile aynı sayı
        names = {c["name"] for c in snapshot["cases"]}
        assert names == {
            "dikdortgen_apartman", "l_sekli_ofis", "u_sekli_okul",
            "duzensiz_villa", "kare_depo",
        }

    def test_snapshot_is_json_serializable(self):
        snapshot = qd.run_dashboard_snapshot()
        # ensure_ascii=False ile Türkçe karakterler de dahil sorunsuz serileşmeli
        serialized = json.dumps(snapshot, ensure_ascii=False)
        assert json.loads(serialized) == snapshot

    def test_watertight_ratio_between_zero_and_one(self):
        snapshot = qd.run_dashboard_snapshot()
        assert 0.0 <= snapshot["watertight_ratio"] <= 1.0

    def test_history_append_and_load_roundtrip(self, tmp_path, monkeypatch):
        history_path = tmp_path / "history.jsonl"
        monkeypatch.setattr(qd, "HISTORY_PATH", history_path)
        assert qd.load_history() == []
        snap1 = {"timestamp": "t1", "total_triangles": 100}
        snap2 = {"timestamp": "t2", "total_triangles": 120}
        qd.append_history(snap1)
        qd.append_history(snap2)
        history = qd.load_history()
        assert history == [snap1, snap2]

    def test_sparkline_svg_handles_empty_and_single_value(self):
        assert "<svg" in qd._sparkline_svg([])
        assert "<svg" in qd._sparkline_svg([42.0])
        assert "<svg" in qd._sparkline_svg([1.0, 5.0, 3.0, 9.0])

    def test_render_html_contains_key_metrics_and_table_rows(self):
        snapshot = qd.run_dashboard_snapshot()
        html = qd.render_html(snapshot, history=[snapshot])
        assert "Mesh Kalite Dashboard" in html
        for case in snapshot["cases"]:
            assert case["name"] in html
        assert str(snapshot["total_triangles"]) in html

    def test_main_writes_dashboard_html_without_logging(self, tmp_path, monkeypatch):
        history_path = tmp_path / "history.jsonl"
        html_path = tmp_path / "dashboard.html"
        monkeypatch.setattr(qd, "HISTORY_PATH", history_path)
        monkeypatch.setattr(qd, "DASHBOARD_HTML_PATH", html_path)
        monkeypatch.setattr(sys, "argv", ["quality_dashboard.py", "--no-log"])
        exit_code = qd.main()
        assert exit_code == 0
        assert html_path.exists()
        assert not history_path.exists()  # --no-log ile geçmişe eklenmemeli


# ---------------------------------------------------------------------- #
# Q3 - perf_regression_gate.py
# ---------------------------------------------------------------------- #

class TestPerfRegressionGate:
    def test_measure_current_returns_expected_keys(self):
        current = prg.measure_current()
        assert set(current) == {"lod_reduction_ratio", "draw_call_reduction_percent"}
        assert 0.0 <= current["lod_reduction_ratio"] <= 1.0
        assert 0.0 <= current["draw_call_reduction_percent"] <= 100.0

    def test_lod_reduction_meets_m1_1_threshold(self):
        """M1.1 kabul kriteri: LOD0->LOD3 üçgen azalması >= %90."""
        ratio = prg._measure_lod_reduction()
        assert ratio >= prg.LOD_REDUCTION_MIN_RATIO

    def test_draw_call_reduction_meets_m1_2_threshold(self):
        """M1.2 kabul kriteri: draw call azalması >= %60."""
        percent = prg._measure_draw_call_reduction()
        assert percent >= prg.DRAW_CALL_REDUCTION_MIN_PERCENT

    def test_evaluate_no_baseline_only_checks_absolute_thresholds(self):
        good = {"lod_reduction_ratio": 0.95, "draw_call_reduction_percent": 70.0}
        assert prg.evaluate(good, baseline=None) == []
        bad = {"lod_reduction_ratio": 0.5, "draw_call_reduction_percent": 70.0}
        problems = prg.evaluate(bad, baseline=None)
        assert len(problems) == 1
        assert "M1.1" in problems[0]

    def test_evaluate_flags_both_absolute_thresholds(self):
        bad = {"lod_reduction_ratio": 0.1, "draw_call_reduction_percent": 10.0}
        problems = prg.evaluate(bad, baseline=None)
        assert len(problems) == 2

    def test_evaluate_flags_regression_vs_baseline_even_if_above_threshold(self):
        baseline = {"lod_reduction_ratio": 0.98, "draw_call_reduction_percent": 90.0}
        # eşiği geçiyor (>=0.90, >=60.0) ama baseline'a göre %10'dan fazla düştü
        current = {"lod_reduction_ratio": 0.91, "draw_call_reduction_percent": 90.0}
        problems = prg.evaluate(current, baseline)
        assert any("Regresyon" in p for p in problems)

    def test_evaluate_allows_small_fluctuation_vs_baseline(self):
        baseline = {"lod_reduction_ratio": 0.95, "draw_call_reduction_percent": 80.0}
        current = {"lod_reduction_ratio": 0.94, "draw_call_reduction_percent": 79.5}
        assert prg.evaluate(current, baseline) == []

    def test_save_and_load_baseline_roundtrip(self, tmp_path, monkeypatch):
        baseline_path = tmp_path / "baseline.json"
        monkeypatch.setattr(prg, "BASELINE_PATH", baseline_path)
        assert prg.load_baseline() is None
        metrics = {"lod_reduction_ratio": 0.93, "draw_call_reduction_percent": 75.0}
        prg.save_baseline(metrics)
        assert prg.load_baseline() == metrics

    def test_main_update_baseline_returns_zero(self, tmp_path, monkeypatch):
        baseline_path = tmp_path / "baseline.json"
        monkeypatch.setattr(prg, "BASELINE_PATH", baseline_path)
        monkeypatch.setattr(sys, "argv", ["perf_regression_gate.py", "--update-baseline"])
        assert prg.main() == 0
        assert baseline_path.exists()

    def test_main_check_mode_returns_zero_when_thresholds_met(self, tmp_path, monkeypatch):
        baseline_path = tmp_path / "baseline.json"
        monkeypatch.setattr(prg, "BASELINE_PATH", baseline_path)
        monkeypatch.setattr(sys, "argv", ["perf_regression_gate.py"])
        assert prg.main() == 0  # baseline yok, yalnız mutlak eşikler - mevcut kod bunları geçiyor


# ---------------------------------------------------------------------- #
# Q1 - CI workflow tanımı
# ---------------------------------------------------------------------- #

class TestQualityCIWorkflow:
    def test_workflow_file_exists(self):
        assert _WORKFLOW_PATH.exists()

    def test_workflow_is_valid_yaml_with_expected_jobs(self):
        pytest.importorskip("yaml")
        content = yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))
        jobs = content["jobs"]
        assert "full-test-suite" in jobs
        assert "visual-regression-gate" in jobs
        assert "perf-regression-gate" in jobs
        assert "quality-dashboard" in jobs

    def test_workflow_runs_all_three_scripts(self):
        text = _WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "scripts/visual_regression.py" in text
        assert "scripts/perf_regression_gate.py" in text
        assert "scripts/quality_dashboard.py" in text
