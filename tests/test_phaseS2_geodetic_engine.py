"""ROADMAP_V6 FAZ S2 — jeodezik hesap motoru birim testleri."""

from __future__ import annotations

import math

import pytest
from harita.feature_survey.geodetic_engine.datum_transform import (
    geocentric_to_geodetic,
    geodetic_to_geocentric,
    orthometric_height,
)
from harita.feature_survey.geodetic_engine.gnss_adjustment import (
    compare_to_control_point,
    rmse_from_differences,
)
from harita.feature_survey.geodetic_engine.reduction import (
    InsufficientDataError,
    ReducedObservation,
    accumulate_bearing,
    reduce_observation,
)
from harita.feature_survey.geodetic_engine.traverse import (
    bowditch_adjustment,
    compute_angular_closure,
    compute_linear_closure,
    transit_adjustment,
)

WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563


def test_reduce_observation_flat_sight():
    # Zenit açısı 100 gon = 90 derece (yatay gözlem) -> tüm mesafe yataydır.
    obs = reduce_observation(
        "P1",
        slope_distance_m=100.0,
        zenith_angle_gon=100.0,
        horizontal_angle_gon=0.0,
        backsight_bearing_gon=0.0,
        instrument_height_m=1.5,
        target_height_m=1.5,
    )
    assert obs.horizontal_distance_m == pytest.approx(100.0)
    assert obs.delta_elevation_m == pytest.approx(0.0, abs=1e-9)
    assert obs.delta_northing_m == pytest.approx(100.0)
    assert obs.delta_easting_m == pytest.approx(0.0, abs=1e-9)


def test_reduce_observation_missing_data_raises():
    with pytest.raises(InsufficientDataError):
        reduce_observation(
            "P1",
            slope_distance_m=None,
            zenith_angle_gon=100.0,
            horizontal_angle_gon=0.0,
            backsight_bearing_gon=0.0,
            instrument_height_m=1.5,
            target_height_m=1.5,
        )


def test_angular_closure_perfect_square():
    # Kapalı bir dörtgenin iç açıları toplamı = (4-2)*200 = 400 gon.
    closure = compute_angular_closure([100.0, 100.0, 100.0, 100.0])
    assert closure.closure_error_gon == pytest.approx(0.0)


def test_angular_closure_with_error():
    closure = compute_angular_closure([100.0, 100.0, 100.0, 100.5])
    assert closure.closure_error_gon == pytest.approx(0.5)


def test_bowditch_closes_to_zero():
    legs = [
        ReducedObservation("P1", 100.0, 0.0, 100.0, 0.001, 0.0),
        ReducedObservation("P2", 100.0, 0.0, 0.0, -100.0, 100.0),
        ReducedObservation("P3", 100.0, 0.0, -100.0, 0.0, 200.0),
        ReducedObservation("P4", 100.0, 0.0, 0.0, 99.999, 300.0),
    ]
    closure = compute_linear_closure(legs)
    assert closure.closure_distance_m > 0  # kasıtlı küçük hata var
    balanced = bowditch_adjustment(legs, closure)
    total_e = math.fsum(b.balanced_delta_easting_m for b in balanced)
    total_n = math.fsum(b.balanced_delta_northing_m for b in balanced)
    assert total_e == pytest.approx(0.0, abs=1e-9)
    assert total_n == pytest.approx(0.0, abs=1e-9)


def test_transit_adjustment_closes_to_zero():
    legs = [
        ReducedObservation("P1", 100.0, 0.0, 100.0, 0.002, 0.0),
        ReducedObservation("P2", 141.42, 0.0, 0.0, -141.42, 100.0),
        ReducedObservation("P3", 100.0, 0.0, -100.0, 0.0, 200.0),
        ReducedObservation("P4", 141.42, 0.0, 0.0, 141.418, 300.0),
    ]
    closure = compute_linear_closure(legs)
    balanced = transit_adjustment(legs, closure)
    total_e = math.fsum(b.balanced_delta_easting_m for b in balanced)
    total_n = math.fsum(b.balanced_delta_northing_m for b in balanced)
    assert total_e == pytest.approx(0.0, abs=1e-6)
    assert total_n == pytest.approx(0.0, abs=1e-6)


def test_control_point_rmse_zero_when_perfect():
    comparisons = [compare_to_control_point(0, 0, 0, 0, 0, 0) for _ in range(3)]
    report = rmse_from_differences(comparisons)
    assert report.rmse_2d_m == pytest.approx(0.0)
    # n=3 (df=2) artık gerçek Student t-tablosundan (t_critical_95) tam
    # kritik değer kullanılıyor -> normal yaklaşımına DÜŞÜLMEDİ.
    assert report.used_normal_approximation is False


def test_geodetic_geocentric_round_trip():
    coord = geodetic_to_geocentric(39.925, 32.837, 900.0, WGS84_A, WGS84_F)
    lat, lon, h = geocentric_to_geodetic(coord, WGS84_A, WGS84_F)
    assert lat == pytest.approx(39.925, abs=1e-8)
    assert lon == pytest.approx(32.837, abs=1e-8)
    assert h == pytest.approx(900.0, abs=1e-3)


def test_orthometric_height_requires_geoid_undulation():
    with pytest.raises(Exception):
        orthometric_height(900.0, None)
    assert orthometric_height(900.0, 30.0) == pytest.approx(870.0)


def test_accumulate_bearing_basic():
    # Önceki yöney 0 gon, iç açı 100 gon (dik açı) -> yeni yöney = 0+200-100=100 gon
    new_bearing = accumulate_bearing(0.0, 100.0)
    assert new_bearing == pytest.approx(100.0)
