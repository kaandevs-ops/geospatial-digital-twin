"""FAZ S2.1 — Trigonometrik indirgeme: Total Station ham gözlemden E/N/Z.

Gerçek, standart topografya formülleri (kaynak: herhangi bir jeodezi/
topografya ders kitabı, örn. Ghilani & Wolf, "Elementary Surveying"):

    yatay_mesafe = eğik_mesafe × sin(zenit_açısı)
    yükseklik_farkı = eğik_mesafe × cos(zenit_açısı) + alet_yüksekliği − hedef_yüksekliği
    ΔE = yatay_mesafe × sin(yöney)
    ΔN = yatay_mesafe × cos(yöney)

Açılar bu modülde **gon** (grad, 0-400) biriminde tutulur (GSI ham veri
modülüyle tutarlı); dış API'ler derece/radyan istiyorsa `gon_to_radians`
ile dönüştürülür.

Küresellik ve refraksiyon düzeltmesi: kısa mesafelerde (roadmap: "ihmal
edilebilir kısa mesafelerde belgelenip") ihmal edilir; `apply_curvature_refraction`
fonksiyonu açıkça çağrılmadıkça uygulanmaz (sessiz/otomatik düzeltme yok —
mühendis hangi düzeltmenin uygulandığını bilmeli).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class InsufficientDataError(ValueError):
    """İndirgeme için gerekli bir gözlem alanı (mesafe/açı/yükseklik)
    eksik olduğunda fırlatılır — asla varsayılan (0 vb.) değer kullanılmaz."""


GON_PER_CIRCLE = 400.0
_EARTH_RADIUS_M = 6_371_000.0  # ortalama Dünya yarıçapı (küresellik düzeltmesi için)
_REFRACTION_COEFFICIENT = 0.13  # tipik atmosferik refraksiyon katsayısı (k), literatür değeri


def gon_to_radians(gon: float) -> float:
    return gon * (math.pi / (GON_PER_CIRCLE / 2.0))


def radians_to_gon(rad: float) -> float:
    return rad * (GON_PER_CIRCLE / 2.0) / math.pi


@dataclass(slots=True)
class ReducedObservation:
    """Bir total station gözleminden hesaplanan gerçek E/N/Z farkları
    (istasyon koordinatına göre bağıl — mutlak koordinat için `apply_traverse`
    ile istasyon koordinatına eklenir)."""

    point_id: str
    horizontal_distance_m: float
    delta_elevation_m: float
    delta_easting_m: float
    delta_northing_m: float
    bearing_gon: float


def apply_curvature_refraction(horizontal_distance_m: float) -> float:
    """Küresellik + refraksiyon düzeltmesi (metre, yükseklik farkına
    eklenir). Formül: c-r = (1 - k) × D² / (2R), D=yatay mesafe, k=refraksiyon
    katsayısı, R=Dünya yarıçapı. Sadece açıkça çağrıldığında uygulanır."""

    d = horizontal_distance_m
    return (1.0 - _REFRACTION_COEFFICIENT) * (d**2) / (2.0 * _EARTH_RADIUS_M)


def reduce_observation(
    point_id: str,
    slope_distance_m: float | None,
    zenith_angle_gon: float | None,
    horizontal_angle_gon: float | None,
    backsight_bearing_gon: float,
    instrument_height_m: float | None,
    target_height_m: float | None,
    apply_earth_curvature: bool = False,
) -> ReducedObservation:
    """Ham total station gözlemini gerçek E/N/Z farklarına indirger.

    `backsight_bearing_gon`: geriden okuma (backsight) doğrultusunun bilinen
    yöneyi — poligon dengelemesinde `traverse.accumulate_bearing` ile
    hesaplanır, burada dışarıdan parametre olarak alınır (tek sorumluluk).
    """

    if slope_distance_m is None:
        raise InsufficientDataError(f"{point_id}: eğik mesafe eksik — indirgeme yapılamaz.")
    if zenith_angle_gon is None:
        raise InsufficientDataError(f"{point_id}: zenit açısı eksik — indirgeme yapılamaz.")
    if horizontal_angle_gon is None:
        raise InsufficientDataError(f"{point_id}: yatay açı eksik — indirgeme yapılamaz.")
    if instrument_height_m is None:
        raise InsufficientDataError(
            f"{point_id}: alet yüksekliği (HI) eksik — indirgeme yapılamaz."
        )
    if target_height_m is None:
        raise InsufficientDataError(
            f"{point_id}: hedef/prizma yüksekliği (HT) eksik — indirgeme yapılamaz."
        )

    zenith_rad = gon_to_radians(zenith_angle_gon)
    horizontal_distance = slope_distance_m * math.sin(zenith_rad)
    delta_elevation = (
        slope_distance_m * math.cos(zenith_rad) + instrument_height_m - target_height_m
    )
    if apply_earth_curvature:
        delta_elevation += apply_curvature_refraction(horizontal_distance)

    bearing_gon = (backsight_bearing_gon + horizontal_angle_gon) % GON_PER_CIRCLE
    bearing_rad = gon_to_radians(bearing_gon)

    delta_easting = horizontal_distance * math.sin(bearing_rad)
    delta_northing = horizontal_distance * math.cos(bearing_rad)

    return ReducedObservation(
        point_id=point_id,
        horizontal_distance_m=horizontal_distance,
        delta_elevation_m=delta_elevation,
        delta_easting_m=delta_easting,
        delta_northing_m=delta_northing,
        bearing_gon=bearing_gon,
    )


def accumulate_bearing(
    previous_bearing_gon: float, measured_angle_gon: float, angle_is_deflection: bool = False
) -> float:
    """Yöney biriktirme (angle accumulation): bir önceki kenarın yöneyine
    ölçülen açı eklenerek yeni kenarın yöneyi hesaplanır.

    `angle_is_deflection=False` (varsayılan): iç açı ölçümü, yeni yöney =
    önceki yöney + 200 gon (ters yön) − iç açı (standart poligon konvansiyonu,
    saat yönü açı ölçümü varsayılır — mühendislik pratiğinde en yaygın).
    `angle_is_deflection=True`: sapma açısı doğrudan eklenir.
    """

    if angle_is_deflection:
        return (previous_bearing_gon + measured_angle_gon) % GON_PER_CIRCLE
    return (previous_bearing_gon + GON_PER_CIRCLE / 2.0 - measured_angle_gon) % GON_PER_CIRCLE
