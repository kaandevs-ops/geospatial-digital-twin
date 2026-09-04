"""FAZ S2 — Jeodezik Hesap Motoru.

Roadmap V6, FAZ S2: platformun kendi matematiğini yazdığı yer — dış araca
bağımlı değil, standart jeodezi/topografya formülleri stdlib ile uygulanır.

Alt modüller:
    - `reduction`: Trigonometrik indirgeme, Total Station → E/N/Z (S2.1).
    - `traverse`: Poligon (traverse) dengeleme — açısal/doğrusal kapatma,
      Bowditch ve Transit yöntemleri (S2.2).
    - `gnss_adjustment`: GNSS epoch ağırlıklı ortalaması, kontrol noktası
      karşılaştırması, gerçek RMSE/güven aralığı (S2.3).
    - `datum_transform`: 7-parametreli Helmert dönüşümü, jeodezik↔jeosentrik
      koordinat dönüşümü, jeoit ondülasyonu uygulaması (S2.4).
    - `least_squares`: en küçük kareler (parametrik/Gauss-Markov) ağ
      dengelemesi — mesafe/azimut gözlem denklemleri, ağırlıklı normal
      denklemler, varyans-kovaryans matrisi, hata elipsleri (S2.2, ikinci
      alt-faz).
    - `datum_params_loader`: S2.4'ün resmi HGK/TUSAGA-Aktif parametre
      setini dış JSON dosyasından güvenli biçimde yükler (kod içine
      gömülmüş/uydurma sayı yok; bkz. `datum_params/README.md`).
"""

from __future__ import annotations

from .reduction import (
    InsufficientDataError as ReductionInsufficientDataError,
    ReducedObservation,
    accumulate_bearing,
    apply_curvature_refraction,
    gon_to_radians,
    radians_to_gon,
    reduce_observation,
)
from .traverse import (
    AngularClosure,
    BalancedLeg,
    InsufficientDataError as TraverseInsufficientDataError,
    LinearClosure,
    bowditch_adjustment,
    build_traverse_coordinates,
    compute_angular_closure,
    compute_linear_closure,
    distribute_angular_correction,
    transit_adjustment,
)
from .gnss_adjustment import (
    ControlPointComparison,
    InsufficientDataError as GnssAdjustmentInsufficientDataError,
    RmseReport,
    WeightedMeanResult,
    compare_to_control_point,
    rmse_from_differences,
    weighted_mean_position,
)
from .datum_transform import (
    DatumTransformParameters,
    GeocentricCoordinate,
    InsufficientDataError as DatumTransformInsufficientDataError,
    apply_helmert_transform,
    geocentric_to_geodetic,
    geodetic_to_geocentric,
    orthometric_height,
)
from .least_squares import (
    AdjustedPoint,
    AzimuthObservation,
    DistanceObservation,
    ErrorEllipse,
    FixedPoint,
    InsufficientDataError as LeastSquaresInsufficientDataError,
    NetworkAdjustmentResult,
    SingularNormalEquationsError,
    UnknownPoint,
    adjust_network,
)
from .datum_params_loader import load_datum_transform_params

__all__ = [
    "ReducedObservation",
    "ReductionInsufficientDataError",
    "accumulate_bearing",
    "apply_curvature_refraction",
    "gon_to_radians",
    "radians_to_gon",
    "reduce_observation",
    "AngularClosure",
    "BalancedLeg",
    "TraverseInsufficientDataError",
    "LinearClosure",
    "bowditch_adjustment",
    "build_traverse_coordinates",
    "compute_angular_closure",
    "compute_linear_closure",
    "distribute_angular_correction",
    "transit_adjustment",
    "ControlPointComparison",
    "GnssAdjustmentInsufficientDataError",
    "RmseReport",
    "WeightedMeanResult",
    "compare_to_control_point",
    "rmse_from_differences",
    "weighted_mean_position",
    "DatumTransformParameters",
    "GeocentricCoordinate",
    "DatumTransformInsufficientDataError",
    "apply_helmert_transform",
    "geocentric_to_geodetic",
    "geodetic_to_geocentric",
    "orthometric_height",
    "AdjustedPoint",
    "AzimuthObservation",
    "DistanceObservation",
    "ErrorEllipse",
    "FixedPoint",
    "LeastSquaresInsufficientDataError",
    "NetworkAdjustmentResult",
    "SingularNormalEquationsError",
    "UnknownPoint",
    "adjust_network",
    "load_datum_transform_params",
]
