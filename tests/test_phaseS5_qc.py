"""ROADMAP_V6 FAZ S5 — kalite kontrol & doğruluk raporu birim testleri.

Roadmap S5 kabul kriteri: "QC modülü, kasıtlı olarak bozuk/toleransı aşan
bir test veri setinde gerçekten hata/uyarı üretmeli" — bu dosyadaki
`test_*_fails_when_tolerance_exceeded` testleri bu kriteri doğrudan kanıtlar.
"""

from __future__ import annotations

import pytest

from harita.feature_survey.geodetic_engine.gnss_adjustment import (
    InsufficientDataError as GnssInsufficientDataError,
    compare_to_control_point,
)
from harita.feature_survey.geodetic_engine.traverse import (
    InsufficientDataError as TraverseInsufficientDataError,
    compute_angular_closure,
    compute_linear_closure,
)
from harita.feature_survey.geodetic_engine.reduction import ReducedObservation
from harita.feature_survey.qc.checkpoint_report import generate_checkpoint_report
from harita.feature_survey.qc.closure_report import generate_closure_report
from harita.feature_survey.qc.survey_quality_report import (
    InsufficientDataError as CombinedInsufficientDataError,
    build_survey_quality_report,
)


# --- Checkpoint accuracy report ---------------------------------------------

def test_checkpoint_report_passes_within_tolerance():
    comparisons = [
        compare_to_control_point(0, 0, 0, 0.01, -0.01, 0.02),
        compare_to_control_point(100, 100, 10, 100.02, 99.99, 10.01),
    ]
    report = generate_checkpoint_report(
        comparisons,
        tolerance_horizontal_m=0.05,
        tolerance_vertical_m=0.05,
        standard_reference="Test standardı: ±5cm yatay/düşey (birim test amaçlı)",
    )
    assert report.all_passed
    assert report.n_failed == 0
    assert all(p.passed for p in report.per_point)


def test_checkpoint_report_fails_when_tolerance_exceeded():
    """NEGATİF TEST: bir nokta kasıtlı olarak toleransı aşıyor -> rapor
    bunu gerçekten yakalamalı, sessizce 'başarılı' dememelidir."""
    comparisons = [
        compare_to_control_point(0, 0, 0, 0.01, -0.01, 0.02),  # tolerans içinde
        compare_to_control_point(100, 100, 10, 100.50, 99.40, 10.01),  # kasıtlı büyük hata
    ]
    report = generate_checkpoint_report(
        comparisons,
        tolerance_horizontal_m=0.05,
        tolerance_vertical_m=0.05,
        standard_reference="Test standardı: ±5cm yatay/düşey (birim test amaçlı)",
    )
    assert report.all_passed is False
    assert report.n_failed == 1
    assert report.per_point[0].passed is True
    assert report.per_point[1].passed is False
    assert report.per_point[1].horizontal_pass is False


def test_checkpoint_report_requires_positive_tolerance():
    comparisons = [compare_to_control_point(0, 0, 0, 0, 0, 0)]
    with pytest.raises(GnssInsufficientDataError):
        generate_checkpoint_report(comparisons, 0.0, 0.05, "kaynak")


def test_checkpoint_report_requires_standard_reference():
    comparisons = [compare_to_control_point(0, 0, 0, 0, 0, 0)]
    with pytest.raises(GnssInsufficientDataError):
        generate_checkpoint_report(comparisons, 0.05, 0.05, "   ")


# --- Closure QC report -------------------------------------------------------

def _legs(n: int, side_m: float = 100.0) -> list[ReducedObservation]:
    """Kapalı bir n-kenarlı düzgün poligonun (yaklaşık) kenarlarını üretir."""
    import math

    legs = []
    for i in range(n):
        angle_gon = i * (400.0 / n)
        rad = angle_gon * (math.pi / 200.0)
        legs.append(
            ReducedObservation(
                point_id=f"P{i}",
                horizontal_distance_m=side_m,
                delta_elevation_m=0.0,
                delta_easting_m=side_m * math.sin(rad),
                delta_northing_m=side_m * math.cos(rad),
                bearing_gon=angle_gon,
            )
        )
    return legs


def test_closure_report_passes_within_tolerance():
    angles = [100.0] * 4  # kapalı dörtgen için teorik iç açı: (4-2)*200/... aslında gon bazında (n-2)*200
    angular = compute_angular_closure(angles)
    linear = compute_linear_closure(_legs(4))
    report = generate_closure_report(
        angular, linear,
        angular_tolerance_gon=0.01,
        max_relative_precision=1 / 1000,
        standard_reference="Test standardı: poligon tolerans (birim test amaçlı)",
    )
    assert report.angular_pass
    assert report.linear_pass
    assert report.all_passed


def test_closure_report_fails_when_angular_tolerance_exceeded():
    """NEGATİF TEST: açısal kapatma hatası kasıtlı olarak büyük -> tolerans
    aşılmalı, rapor False dönmeli."""
    angles = [100.0, 100.0, 100.0, 105.0]  # kasıtlı 5 gon'luk hata
    angular = compute_angular_closure(angles)
    linear = compute_linear_closure(_legs(4))
    report = generate_closure_report(
        angular, linear,
        angular_tolerance_gon=0.01,
        max_relative_precision=1 / 1000,
        standard_reference="Test standardı",
    )
    assert report.angular_pass is False
    assert report.all_passed is False


def test_closure_report_fails_when_linear_tolerance_exceeded():
    """NEGATİF TEST: kenarlardan biri kasıtlı olarak kapanmayan bir poligon
    oluşturuyor -> bağıl hassasiyet toleransı aşılmalı."""
    legs = _legs(4)
    # Son kenara büyük bir kapanmama hatası ekle (dejenere/bozuk ölçüm simülasyonu).
    bad_leg = ReducedObservation(
        point_id=legs[-1].point_id,
        horizontal_distance_m=legs[-1].horizontal_distance_m,
        delta_elevation_m=0.0,
        delta_easting_m=legs[-1].delta_easting_m + 10.0,  # 10m kasıtlı hata
        delta_northing_m=legs[-1].delta_northing_m,
        bearing_gon=legs[-1].bearing_gon,
    )
    legs[-1] = bad_leg
    angular = compute_angular_closure([100.0] * 4)
    linear = compute_linear_closure(legs)
    report = generate_closure_report(
        angular, linear,
        angular_tolerance_gon=0.01,
        max_relative_precision=1 / 5000,
        standard_reference="Test standardı",
    )
    assert report.linear_pass is False
    assert report.all_passed is False


def test_closure_report_requires_documented_standard():
    angular = compute_angular_closure([100.0] * 4)
    linear = compute_linear_closure(_legs(4))
    with pytest.raises(TraverseInsufficientDataError):
        generate_closure_report(angular, linear, 0.01, 1 / 1000, "")


# --- Combined survey quality report ------------------------------------------

def test_combined_report_requires_at_least_one_subreport():
    with pytest.raises(CombinedInsufficientDataError):
        build_survey_quality_report(None, None)


def test_combined_report_overall_passed_reflects_failure():
    comparisons = [compare_to_control_point(0, 0, 0, 5.0, 5.0, 5.0)]  # büyük kasıtlı hata
    checkpoint = generate_checkpoint_report(comparisons, 0.05, 0.05, "Test standardı")
    combined = build_survey_quality_report(checkpoint, None)
    assert combined.overall_passed is False
    assert len(combined.failure_summary) == 1
