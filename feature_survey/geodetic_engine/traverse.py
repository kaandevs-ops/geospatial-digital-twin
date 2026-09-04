"""FAZ S2.2 — Poligon (traverse) dengeleme: açısal + doğrusal kapatma,
Bowditch (compass rule) dengeleme.

Kaynak formüller (Ghilani & Wolf, "Elementary Surveying"; TUJJB/HKMO
topografik ölçüm yönetmeliği tolerans yaklaşımıyla tutarlı genel pratik):

    Açısal kapatma hatası = Σ(ölçülen iç açılar) − (n−2)×200 gon
        (n−2)×180° = (n−2)×200 gon (gon biriminde teorik iç açı toplamı)
    Doğrusal kapatma hatası (kapanma vektörü) = (ΣΔE, ΣΔN) — kapalı bir
        poligonda teorik olarak (0, 0) olmalıdır.
    Bağıl hata (relative precision) = kapanma_mesafesi / toplam_çevre
    Bowditch (compass rule): her kenara, o kenarın uzunluğu / toplam çevre
        oranında düzeltme dağıtılır — mesafe ölçüm hassasiyeti açı
        hassasiyetine yakın olduğunda uygundur (dokümante edilmiştir).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .reduction import GON_PER_CIRCLE, ReducedObservation


class InsufficientDataError(ValueError):
    """Dengeleme için gerekli veri (açı sayısı, kenar listesi vb.) eksik/
    tutarsız olduğunda fırlatılır."""


@dataclass(slots=True)
class AngularClosure:
    n_points: int
    measured_sum_gon: float
    theoretical_sum_gon: float
    closure_error_gon: float  # ölçülen - teorik


@dataclass(slots=True)
class LinearClosure:
    """Doğrusal kapatma — gerçek kapanma vektöründen hesaplanır."""

    delta_easting_sum_m: float
    delta_northing_sum_m: float
    closure_distance_m: float  # sqrt(ΔE² + ΔN²)
    perimeter_m: float
    relative_precision: float  # closure_distance / perimeter (örn. 1/5000 biçiminde raporlanır)


@dataclass(slots=True)
class BalancedLeg:
    point_id: str
    raw_delta_easting_m: float
    raw_delta_northing_m: float
    correction_easting_m: float
    correction_northing_m: float
    balanced_delta_easting_m: float
    balanced_delta_northing_m: float


def compute_angular_closure(interior_angles_gon: list[float]) -> AngularClosure:
    """Kapalı bir poligonun iç açılarından açısal kapatma hatasını
    hesaplar. n < 3 ise poligon tanımsızdır → hata fırlatılır."""

    n = len(interior_angles_gon)
    if n < 3:
        raise InsufficientDataError(
            f"Kapalı bir poligon en az 3 kenar/açı gerektirir, verilen: {n}"
        )
    measured_sum = math.fsum(interior_angles_gon)
    theoretical_sum = (n - 2) * (GON_PER_CIRCLE / 2.0)
    return AngularClosure(
        n_points=n,
        measured_sum_gon=measured_sum,
        theoretical_sum_gon=theoretical_sum,
        closure_error_gon=measured_sum - theoretical_sum,
    )


def distribute_angular_correction(
    interior_angles_gon: list[float], closure: AngularClosure
) -> list[float]:
    """Açısal kapatma hatasını, kenarlar arasında eşit olarak dağıtır
    (standart pratik — tüm açılar genelde benzer hassasiyette ölçülür).
    Düzeltilmiş açılar = ölçülen − (hata / n)."""

    n = closure.n_points
    correction_per_angle = closure.closure_error_gon / n
    return [angle - correction_per_angle for angle in interior_angles_gon]


def compute_linear_closure(legs: list[ReducedObservation]) -> LinearClosure:
    """Poligon kenarlarının (indirgenmiş ΔE/ΔN) toplamından gerçek
    kapanma vektörünü ve bağıl hatayı hesaplar."""

    if not legs:
        raise InsufficientDataError("Doğrusal kapatma için en az bir kenar gerekir.")

    sum_de = math.fsum(leg.delta_easting_m for leg in legs)
    sum_dn = math.fsum(leg.delta_northing_m for leg in legs)
    perimeter = math.fsum(leg.horizontal_distance_m for leg in legs)
    if perimeter <= 0:
        raise InsufficientDataError("Poligon çevresi sıfır/negatif — geçersiz kenar verisi.")

    closure_distance = math.hypot(sum_de, sum_dn)
    return LinearClosure(
        delta_easting_sum_m=sum_de,
        delta_northing_sum_m=sum_dn,
        closure_distance_m=closure_distance,
        perimeter_m=perimeter,
        relative_precision=closure_distance / perimeter,
    )


def bowditch_adjustment(
    legs: list[ReducedObservation], closure: LinearClosure
) -> list[BalancedLeg]:
    """Bowditch (compass rule) dengeleme: her kenara, kendi uzunluğunun
    çevreye oranında ters işaretli düzeltme uygulanır — böylece dengelenmiş
    ΔE/ΔN toplamları tam olarak sıfıra iner."""

    balanced: list[BalancedLeg] = []
    for leg in legs:
        ratio = leg.horizontal_distance_m / closure.perimeter_m
        corr_e = -closure.delta_easting_sum_m * ratio
        corr_n = -closure.delta_northing_sum_m * ratio
        balanced.append(
            BalancedLeg(
                point_id=leg.point_id,
                raw_delta_easting_m=leg.delta_easting_m,
                raw_delta_northing_m=leg.delta_northing_m,
                correction_easting_m=corr_e,
                correction_northing_m=corr_n,
                balanced_delta_easting_m=leg.delta_easting_m + corr_e,
                balanced_delta_northing_m=leg.delta_northing_m + corr_n,
            )
        )
    return balanced


def transit_adjustment(legs: list[ReducedObservation], closure: LinearClosure) -> list[BalancedLeg]:
    """Transit dengeleme: düzeltme, kenarın |ΔE|/Σ|ΔE| ve |ΔN|/Σ|ΔN|
    oranlarına göre ayrı ayrı dağıtılır (açı ölçüm hassasiyeti mesafe
    hassasiyetinden daha iyi olduğunda uygundur — Bowditch'in aksine mesafeyle
    orantılı değil, bileşen büyüklüğüyle orantılı dağıtım yapar)."""

    sum_abs_de = math.fsum(abs(leg.delta_easting_m) for leg in legs)
    sum_abs_dn = math.fsum(abs(leg.delta_northing_m) for leg in legs)
    if sum_abs_de == 0 or sum_abs_dn == 0:
        raise InsufficientDataError(
            "Transit dengeleme için ΔE ve ΔN bileşenlerinin toplamı sıfırdan farklı olmalı "
            "(dejenere/doğrusal poligon transit yöntemiyle dengelenemez, Bowditch kullanın)."
        )

    balanced: list[BalancedLeg] = []
    for leg in legs:
        corr_e = -closure.delta_easting_sum_m * (abs(leg.delta_easting_m) / sum_abs_de)
        corr_n = -closure.delta_northing_sum_m * (abs(leg.delta_northing_m) / sum_abs_dn)
        balanced.append(
            BalancedLeg(
                point_id=leg.point_id,
                raw_delta_easting_m=leg.delta_easting_m,
                raw_delta_northing_m=leg.delta_northing_m,
                correction_easting_m=corr_e,
                correction_northing_m=corr_n,
                balanced_delta_easting_m=leg.delta_easting_m + corr_e,
                balanced_delta_northing_m=leg.delta_northing_m + corr_n,
            )
        )
    return balanced


def build_traverse_coordinates(
    start_easting_m: float, start_northing_m: float, balanced_legs: list[BalancedLeg]
) -> list[tuple[str, float, float]]:
    """Dengelenmiş ΔE/ΔN'lerden, başlangıç istasyonundan itibaren gerçek
    mutlak koordinatları kümülatif olarak üretir."""

    coords: list[tuple[str, float, float]] = []
    e, n = start_easting_m, start_northing_m
    for leg in balanced_legs:
        e += leg.balanced_delta_easting_m
        n += leg.balanced_delta_northing_m
        coords.append((leg.point_id, e, n))
    return coords
