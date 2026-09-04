"""ROADMAP_V6 FAZ S2.2 (ikinci alt-faz) — en küçük kareler ağ dengelemesi
birim testleri."""

from __future__ import annotations

import math

import pytest

from harita.feature_survey.geodetic_engine.gnss_adjustment import (
    InsufficientDataError as GnssInsufficientDataError,
    t_critical_95,
)
from harita.feature_survey.geodetic_engine.least_squares import (
    AzimuthObservation,
    DistanceObservation,
    FixedPoint,
    InsufficientDataError,
    SingularNormalEquationsError,
    UnknownPoint,
    adjust_network,
)


def test_resection_exact_observations_recovers_true_position():
    """Üç sabit noktadan (A, B, C) hatasız mesafe gözlemiyle bilinmeyen P
    noktasının gerçek konumuna (10,10) yakınsaması gerekir; artıklar ~0."""

    true_e, true_n = 10.0, 10.0
    fixed = [
        FixedPoint("A", 0.0, 0.0),
        FixedPoint("B", 20.0, 0.0),
        FixedPoint("C", 0.0, 20.0),
    ]

    def dist(a, b):
        return math.hypot(a.easting_m - b[0], a.northing_m - b[1])

    obs = [
        DistanceObservation("A", "P", math.hypot(true_e - 0, true_n - 0), 0.005),
        DistanceObservation("B", "P", math.hypot(true_e - 20, true_n - 0), 0.005),
        DistanceObservation("C", "P", math.hypot(true_e - 0, true_n - 20), 0.005),
    ]
    unknowns = [UnknownPoint("P", approx_easting_m=9.0, approx_northing_m=9.0)]

    result = adjust_network(unknowns, fixed, obs, [])

    assert result.converged
    p = result.points["P"]
    assert p.easting_m == pytest.approx(true_e, abs=1e-6)
    assert p.northing_m == pytest.approx(true_n, abs=1e-6)
    assert result.reference_variance == pytest.approx(0.0, abs=1e-6)
    assert result.redundancy == 1  # 3 gözlem - 2 bilinmeyen


def test_insufficient_redundancy_raises():
    fixed = [FixedPoint("A", 0.0, 0.0), FixedPoint("B", 10.0, 0.0)]
    unknowns = [UnknownPoint("P", 5.0, 5.0)]
    obs = [DistanceObservation("A", "P", 7.07, 0.01)]
    with pytest.raises(InsufficientDataError):
        adjust_network(unknowns, fixed, obs, [])


def test_zero_or_negative_sigma_rejected():
    fixed = [FixedPoint("A", 0.0, 0.0), FixedPoint("B", 10.0, 0.0), FixedPoint("C", 0.0, 10.0)]
    unknowns = [UnknownPoint("P", 5.0, 5.0)]
    obs = [
        DistanceObservation("A", "P", 7.07, 0.0),
        DistanceObservation("B", "P", 7.07, 0.01),
        DistanceObservation("C", "P", 7.07, 0.01),
    ]
    with pytest.raises(InsufficientDataError):
        adjust_network(unknowns, fixed, obs, [])


def test_azimuth_observation_contributes_to_adjustment():
    # True P = (10, 0). A=(0,0) -> P: mesafe 10, azimut 100 gon (tam doğu).
    # B=(0,10) -> P: mesafe = hypot(10, -10) = 14.142135623730951.
    fixed = [FixedPoint("A", 0.0, 0.0), FixedPoint("B", 0.0, 10.0)]
    unknowns = [UnknownPoint("P", approx_easting_m=9.5, approx_northing_m=0.5)]
    obs_dist = [
        DistanceObservation("A", "P", 10.0, 0.01),
        DistanceObservation("B", "P", math.hypot(10.0, -10.0), 0.01),
    ]
    obs_az = [AzimuthObservation("A", "P", 100.0, 0.01)]  # 100 gon = tam doğu (90°)

    result = adjust_network(unknowns, fixed, obs_dist, obs_az)
    p = result.points["P"]
    assert p.easting_m == pytest.approx(10.0, abs=1e-3)
    assert p.northing_m == pytest.approx(0.0, abs=1e-3)
    assert result.redundancy == 1  # 3 gözlem (2 mesafe + 1 azimut) - 2 bilinmeyen


def test_covariance_and_error_ellipse_are_positive():
    fixed = [
        FixedPoint("A", 0.0, 0.0),
        FixedPoint("B", 20.0, 0.0),
        FixedPoint("C", 0.0, 20.0),
        FixedPoint("D", 20.0, 20.0),
    ]
    unknowns = [UnknownPoint("P", 9.5, 9.5)]
    obs = [
        DistanceObservation("A", "P", math.hypot(10, 10), 0.01),
        DistanceObservation("B", "P", math.hypot(10, 10), 0.01),
        DistanceObservation("C", "P", math.hypot(10, 10), 0.01),
        DistanceObservation("D", "P", math.hypot(10, 10), 0.01),
    ]
    result = adjust_network(unknowns, fixed, obs, [])
    p = result.points["P"]
    assert p.std_easting_m > 0
    assert p.std_northing_m > 0
    assert p.error_ellipse.semi_major_m >= p.error_ellipse.semi_minor_m > 0


def test_singular_network_raises_without_silent_fallback():
    """Datum eksik (hiç sabit nokta yok, sadece iki bilinmeyen arası tek
    mesafe) -> normal denklemler tekil olmalı, sessizce çözülmemeli."""

    unknowns = [
        UnknownPoint("P1", 0.0, 0.0),
        UnknownPoint("P2", 10.0, 0.0),
    ]
    obs = [
        DistanceObservation("P1", "P2", 10.0, 0.01),
        DistanceObservation("P1", "P2", 10.01, 0.01),
    ]
    with pytest.raises((InsufficientDataError, SingularNormalEquationsError)):
        adjust_network(unknowns, [], obs, [])


def test_t_critical_95_small_sample_matches_table():
    value, exact = t_critical_95(9)  # n=10 -> df=9
    assert exact
    assert value == pytest.approx(2.262)


def test_t_critical_95_large_sample_uses_normal_approx():
    value, exact = t_critical_95(500)
    assert not exact
    assert value == pytest.approx(1.95996)


def test_t_critical_95_invalid_dof_raises():
    with pytest.raises(GnssInsufficientDataError):
        t_critical_95(0)
