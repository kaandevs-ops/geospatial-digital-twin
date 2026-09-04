"""ROADMAP_V6 FAZ S6 — Saha -> Dijital İkiz uçtan uca orkestrasyon testleri.

Kabul kriteri (ROADMAP_V6.md, FAZ S6): "Gerçek bir uçtan uca senaryo —
RTK-GNSS kontrol noktaları + Total Station detay ölçümü + drone fotogrametri
aynı projede birleştirilip tek bir tutarlı, doğruluğu raporlanmış dijital
ikiz üretilecek."

Bu test dosyası, `run_field_survey_pipeline`'ın S1(session)+S2(checkpoint/
closure)+S3(pointcloud)+S4(linework)+S5(QC+surface comparison) fazlarının
TÜMÜNÜ tek bir çağrıda, gerçek (sahte olmayan) ara hesaplamalarla birleştirip
tutarlı ve denetlenebilir (audit trail) bir sonuç ürettiğini kanıtlar.
"""

from __future__ import annotations

import random

import pytest

from harita.feature_survey.codes import FeatureCode
from harita.feature_survey.field_point import FieldPoint, FieldSurveySession
from harita.feature_survey.geodetic_engine.gnss_adjustment import ControlPointComparison
from harita.feature_survey.geodetic_engine.traverse import AngularClosure, LinearClosure
from harita.feature_survey.orchestration.pipeline import (
    OrchestrationError,
    run_field_survey_pipeline,
)
from harita.mesh_engine import Mesh3D, Vertex3D


def _flat_plane_mesh(size: float = 10.0) -> Mesh3D:
    verts = [
        Vertex3D(0.0, 0.0, 0.0),
        Vertex3D(size, 0.0, 0.0),
        Vertex3D(size, size, 0.0),
        Vertex3D(0.0, size, 0.0),
    ]
    tris = [(0, 1, 2), (0, 2, 3)]
    return Mesh3D(vertices=verts, triangles=tris, name="plane")


def _building_session() -> FieldSurveySession:
    """4 köşeli basit bir bina dış hattı (Total Station detay ölçümü)."""
    pts = [
        FieldPoint("1", 0.0, 0.0, 100.0, FeatureCode.BUILDING_CORNER, string_id="BLD01"),
        FieldPoint("2", 10.0, 0.0, 100.0, FeatureCode.BUILDING_CORNER, string_id="BLD01"),
        FieldPoint("3", 10.0, 10.0, 100.0, FeatureCode.BUILDING_CORNER, string_id="BLD01"),
        FieldPoint("4", 0.0, 10.0, 100.0, FeatureCode.BUILDING_CORNER, string_id="BLD01"),
    ]
    return FieldSurveySession(name="S6 E2E Bina", points=pts, crs="local")


def test_end_to_end_full_pipeline_all_phases():
    random.seed(42)
    session = _building_session()

    # S2 - RTK-GNSS kontrol noktası karşılaştırması (gerçek RMSE)
    checkpoint_comparisons = [
        ControlPointComparison(
            delta_easting_m=0.01,
            delta_northing_m=-0.02,
            delta_elevation_m=0.015,
            rmse_2d_m=0.022,
            rmse_3d_m=0.027,
        )
    ]

    # S2 - poligon kapatma (gerçek açısal/doğrusal kapatma değerleri)
    angular = AngularClosure(
        n_points=4,
        measured_sum_gon=399.9,
        theoretical_sum_gon=400.0,
        closure_error_gon=0.1,
    )
    linear = LinearClosure(
        delta_easting_sum_m=0.02,
        delta_northing_sum_m=-0.01,
        closure_distance_m=0.0223,
        perimeter_m=40.0,
        relative_precision=40.0 / 0.0223,
    )

    # S3 - LiDAR/nokta bulutu (düzlem + küçük gürültü)
    pointcloud = [
        (float(i), float(j), random.gauss(0.0, 0.01))
        for i in range(11)
        for j in range(11)
    ]
    reference_mesh = _flat_plane_mesh(10.0)

    result = run_field_survey_pipeline(
        project_name="S6 E2E Test Projesi",
        session=session,
        checkpoint_comparisons=checkpoint_comparisons,
        checkpoint_tolerance_horizontal_m=0.05,
        checkpoint_tolerance_vertical_m=0.05,
        checkpoint_standard_reference="ASPRS Positional Accuracy Standards (test)",
        angular_closure=angular,
        linear_closure=linear,
        angular_tolerance_gon=0.15,
        max_relative_precision=5000.0,
        closure_standard_reference="TUJJB Topografik Ölçüm Yönetmeliği (test)",
        pointcloud_points=pointcloud,
        reference_mesh=reference_mesh,
    )

    # S4: linework gerçekten üretildi (kapanan bina poligonu)
    assert result.linework is not None
    assert len(result.linework) == 1

    # S5: birleşik QC raporu üretildi ve geçer (gerçek toleranslarla)
    assert result.quality_report is not None
    assert result.quality_report.overall_passed is True

    # S3: zemin sınıflandırma ve kalite raporu üretildi
    assert result.ground_classification is not None
    assert result.pointcloud_quality is not None
    assert result.pointcloud_quality.density.density_points_per_m2 > 0

    # S5 (S3'e bağımlı parça): yüzey karşılaştırması üretildi, gürültü
    # seviyesiyle tutarlı küçük bir RMS mesafesi rapor edildi (sıfır değil,
    # sahte de değil — gerçek gürültüden geliyor).
    assert result.surface_comparison is not None
    assert 0.0 < result.surface_comparison.rms_distance_m < 0.05

    # Audit trail: her faz denetlenebilir şekilde kaydedildi.
    phases = [entry.phase for entry in result.audit_trail]
    for expected in ["S4", "S5-checkpoint", "S5-closure", "S5-combined", "S3-ground", "S3-quality", "S5-surface-comparison"]:
        assert expected in phases

    # Proje manifestosu payload'ı JSON-serileştirilebilir ve tutarlı.
    payload = result.to_project_manifest_payload()
    assert payload["project_name"] == "S6 E2E Test Projesi"
    assert payload["qc_overall_passed"] is True
    assert payload["linework_feature_count"] == 1
    assert "surface_comparison" in payload
    assert len(payload["audit_trail"]) == len(result.audit_trail)


def test_no_input_raises_orchestration_error():
    with pytest.raises(OrchestrationError):
        run_field_survey_pipeline(project_name="Boş Proje")


def test_empty_project_name_rejected():
    with pytest.raises(OrchestrationError):
        run_field_survey_pipeline(project_name="   ", session=_building_session())


def test_checkpoint_without_tolerance_rejected():
    """S5 uydurma tolerans kabul etmez ilkesi S6 seviyesinde de korunmalı."""
    with pytest.raises(OrchestrationError):
        run_field_survey_pipeline(
            project_name="Toleranssız Proje",
            checkpoint_comparisons=[
                ControlPointComparison(0.01, -0.02, 0.015, 0.022, 0.027)
            ],
        )


def test_negative_scenario_bad_closure_reports_failure_not_silently():
    """Kasıtlı olarak toleransı aşan kapatma -> pipeline sessizce 'başarılı'
    demiyor, gerçekten overall_passed=False üretiyor (S5 negatif test
    ilkesinin S6 seviyesinde de geçerli olduğunun kanıtı)."""
    angular = AngularClosure(
        n_points=4,
        measured_sum_gon=395.0,
        theoretical_sum_gon=400.0,
        closure_error_gon=5.0,  # kasıtlı olarak çok büyük
    )
    linear = LinearClosure(
        delta_easting_sum_m=2.0,
        delta_northing_sum_m=2.0,
        closure_distance_m=2.83,
        perimeter_m=40.0,
        relative_precision=40.0 / 2.83,
    )
    result = run_field_survey_pipeline(
        project_name="Kasıtlı Bozuk Poligon",
        angular_closure=angular,
        linear_closure=linear,
        angular_tolerance_gon=0.15,
        max_relative_precision=5000.0,
        closure_standard_reference="TUJJB Topografik Ölçüm Yönetmeliği (test)",
    )
    assert result.quality_report is not None
    assert result.quality_report.overall_passed is False
