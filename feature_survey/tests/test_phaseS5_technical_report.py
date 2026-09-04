"""ROADMAP_V6 FAZ S5 — teknik rapor şablonu (PDF/JSON) birim testleri."""

from __future__ import annotations

import json

import pytest
from harita.feature_survey.codes import FeatureCode
from harita.feature_survey.field_point import FieldPoint, FieldSurveySession
from harita.feature_survey.geodetic_engine.gnss_adjustment import compare_to_control_point
from harita.feature_survey.qc.checkpoint_report import generate_checkpoint_report
from harita.feature_survey.qc.survey_quality_report import build_survey_quality_report
from harita.feature_survey.qc.technical_report import (
    InsufficientDataError,
    TechnicalReportMeta,
    build_report_lines,
    export_technical_report_json,
    export_technical_report_pdf,
)


def _passing_report():
    comparisons = [
        compare_to_control_point(0, 0, 0, 0.01, -0.01, 0.02),
        compare_to_control_point(100, 100, 10, 100.02, 99.99, 10.01),
    ]
    checkpoint = generate_checkpoint_report(comparisons, 0.05, 0.05, "Test standardı ±5cm")
    return build_survey_quality_report(checkpoint=checkpoint, closure=None)


def _failing_report():
    comparisons = [
        compare_to_control_point(0, 0, 0, 0.01, -0.01, 0.02),
        compare_to_control_point(100, 100, 10, 100.50, 99.40, 10.01),
    ]
    checkpoint = generate_checkpoint_report(comparisons, 0.05, 0.05, "Test standardı ±5cm")
    return build_survey_quality_report(checkpoint=checkpoint, closure=None)


def _meta() -> TechnicalReportMeta:
    return TechnicalReportMeta(
        project_name="Test Projesi",
        surveyor="Test Mühendis",
        crs="EPSG:32636",
        standard_reference="Test standardı ±5cm",
    )


def test_build_report_lines_reports_success():
    lines = build_report_lines(_meta(), _passing_report())
    assert any("BAŞARILI" in l for l in lines)
    assert any("Test Projesi" in l for l in lines)


def test_build_report_lines_reports_failure_explicitly():
    """NEGATİF TEST: rapor toleransı aşan durumda sessizce 'başarılı' demez."""
    lines = build_report_lines(_meta(), _failing_report())
    assert any("BAŞARISIZ" in l for l in lines)
    assert any("BAŞARISIZLIK ÖZETİ" in l for l in lines)


def test_report_requires_project_name():
    meta = TechnicalReportMeta(
        project_name="   ", surveyor="x", crs="local", standard_reference="y"
    )
    with pytest.raises(InsufficientDataError):
        build_report_lines(meta, _passing_report())


def test_export_technical_report_pdf(tmp_path):
    path = tmp_path / "report.pdf"
    result = export_technical_report_pdf(_meta(), _passing_report(), str(path))
    assert path.exists()
    assert path.stat().st_size > 0
    assert result.format in ("pdf", "pdf-minimal")


def test_export_technical_report_json_roundtrip(tmp_path):
    path = tmp_path / "report.json"
    session = FieldSurveySession(name="Oturum1", crs="EPSG:32636")
    session.add(FieldPoint("P1", 10.0, 20.0, 5.0, FeatureCode.CONTROL_POINT))
    export_technical_report_json(_meta(), _passing_report(), str(path), session=session)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["overall_passed"] is True
    assert data["session"]["point_count"] == 1
    assert "checkpoint" in data
