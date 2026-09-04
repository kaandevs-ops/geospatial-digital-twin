"""
Geo Reference — Coğrafi Doğruluk Regresyon Katmanı
====================================================

ROADMAP_V2 Faz 17 — "Coğrafi Doğruluk & Büyük Ölçek Haritalama".

Bu modül, `core_engine.coordinate_systems.CoordinateConverter` içindeki
WGS84 <-> Web Mercator <-> UTM dönüşümlerini **gerçek, yayınlanmış referans
koordinatlara** karşı doğrulayan bir regresyon test paketi sağlar. Amaç:
dönüşüm formüllerinde ileride yapılacak bir değişikliğin sessizce hatalı
sonuç üretmesini (regresyon) yakalamak.

Kapsam ve dürüst sınırlama
---------------------------
Bu paket **tam PROJ-uyumlu bir CRS kütüphanesi değildir** ve öyle iddia
etmez. `pyproj`/PROJ grid-shift/datum dönüşümü (ör. ED50->WGS84 7 parametreli
Helmert + NTv2 grid shift) burada yoktur — bu iş, opsiyonel `pyproj`
bağımlılığı gerektiren, kapsam dışı bırakılmış bir gelecek adımdır (bkz.
ROADMAP_V2.md Faz 17). Burada doğrulanan şey: (a) uygulanan Transverse
Mercator / Web Mercator formüllerinin **kendi içinde tutarlı** (round-trip)
olduğu, (b) UTM zon ataması ve enlem/boylam referans noktalarının gerçek
dünya verisiyle **doğru** eşleştiği, (c) haversine mesafe/yön hesaplarının
bağımsız, yayınlanmış referans değerlerle (uçuş/harita mesafesi) makul bir
tolerans içinde örtüştüğü.

Referans veri kaynakları (her noktanın yanında belirtilmiştir):
    - Enlem/boylam: Wikipedia / OpenStreetMap tabanlı genel bilgi
      (coğrafi bilgi kutuları) ve Himmera mesafe hesaplayıcısının GPS
      koordinat tablosu.
    - Referans mesafeler: Himmera "as the crow flies" (great-circle)
      değerleri — Vincenty/WGS84 elipsoid tabanlı, dolayısıyla saf küresel
      haversine formülümüzle karşılaştırıldığında ~%0.5 mertebesinde bir
      sapma beklenir; tolerans buna göre belirlenmiştir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..coordinate_systems import CoordinateConverter, GeoPoint


# ======================================================================== #
# Referans konum veri seti
# ======================================================================== #

@dataclass(frozen=True, slots=True)
class ReferenceLocation:
    """Yayınlanmış enlem/boylamı bilinen gerçek bir referans nokta."""

    name: str
    lat: float
    lon: float
    expected_utm_zone: int
    source: str

    def point(self) -> GeoPoint:
        return GeoPoint(lat=self.lat, lon=self.lon)


# Koordinatlar: Himmera mesafe hesaplayıcısının GPS tablosu (WGS84, ondalık
# derece) ve genel coğrafi referanslar. UTM zon beklentisi
# `CoordinateConverter.utm_zone_for()` ile bağımsız olarak (boylam/6 + 31
# kuralı) çapraz kontrol edilir.
REFERENCE_LOCATIONS: tuple[ReferenceLocation, ...] = (
    ReferenceLocation(
        name="Ankara (Kızılay)",
        lat=39.92077,
        lon=32.85411,
        expected_utm_zone=36,
        source="Himmera distance-calculator GPS tablosu",
    ),
    ReferenceLocation(
        name="İstanbul",
        lat=41.00527,
        lon=28.97696,
        expected_utm_zone=35,
        source="Himmera distance-calculator GPS tablosu",
    ),
    ReferenceLocation(
        name="Londra (Greenwich yakını)",
        lat=51.4769,
        lon=-0.0005,
        expected_utm_zone=30,
        source="Genel coğrafi referans (Royal Observatory Greenwich)",
    ),
    ReferenceLocation(
        name="New York",
        lat=40.7128,
        lon=-74.0060,
        expected_utm_zone=18,
        source="Genel coğrafi referans",
    ),
    ReferenceLocation(
        name="Sidney",
        lat=-33.8688,
        lon=151.2093,
        expected_utm_zone=56,
        source="Genel coğrafi referans",
    ),
    ReferenceLocation(
        name="Ekvator/Greenwich kesişimi",
        lat=0.0,
        lon=0.0,
        expected_utm_zone=31,
        source="Tanım gereği (0,0)",
    ),
)


@dataclass(frozen=True, slots=True)
class ReferenceDistance:
    """İki referans nokta arası yayınlanmış kuş-uçuşu (great-circle) mesafe."""

    a: str
    b: str
    published_km: float
    tolerance_km: float
    source: str


# "a"/"b" alanları REFERENCE_LOCATIONS içindeki `name` alanına karşılık gelir.
REFERENCE_DISTANCES: tuple[ReferenceDistance, ...] = (
    ReferenceDistance(
        a="Ankara (Kızılay)",
        b="İstanbul",
        published_km=349.0,
        # Vincenty (ellipsoid) referansına karşı küresel haversine sapması
        # + iki şehrin "temsili" tek noktaları arasındaki belirsizlik.
        tolerance_km=5.0,
        source="Himmera 'as the crow flies' değeri (349 km)",
    ),
)


# ======================================================================== #
# Doğruluk raporu
# ======================================================================== #

@dataclass(slots=True)
class AccuracyCheck:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True)
class GeodeticAccuracyReport:
    """`run_geodetic_accuracy_suite()` çıktısı."""

    checks: list[AccuracyCheck] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed(self) -> list[AccuracyCheck]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        total = len(self.checks)
        ok = sum(1 for c in self.checks if c.passed)
        lines = [f"Geodetic accuracy: {ok}/{total} kontrol geçti."]
        for c in self.failed:
            lines.append(f"  FAIL: {c.name} — {c.detail}")
        return "\n".join(lines)


def _round_trip_utm_error_m(point: GeoPoint) -> float:
    projected = CoordinateConverter.wgs84_to_utm(point)
    back = CoordinateConverter.utm_to_wgs84(projected, northern_hemisphere=point.lat >= 0)
    return CoordinateConverter.haversine_distance(point, back)


def _round_trip_web_mercator_error_m(point: GeoPoint) -> float:
    projected = CoordinateConverter.wgs84_to_web_mercator(point)
    back = CoordinateConverter.web_mercator_to_wgs84(projected)
    return CoordinateConverter.haversine_distance(point, back)


def run_geodetic_accuracy_suite(
    *,
    round_trip_tolerance_m: float = 0.001,
) -> GeodeticAccuracyReport:
    """
    ROADMAP_V2 Faz 17 kabul kriterinin ölçülebilir alt kümesi: tüm referans
    noktalarda UTM/Web Mercator round-trip hatası ve UTM zon ataması
    doğruluğu, artı yayınlanmış şehir-arası mesafelerle çapraz kontrol.

    Tam "gerçek bir şehrin OSM verisiyle ±1m örtüşmesi" kriteri (ROADMAP_V2)
    gerçek OSM veri seti erişimi gerektirdiğinden bu ortamda kapsam dışıdır;
    burada uygulanan, aynı doğruluk iddiasının bağımsız olarak
    doğrulanabilir bir alt kümesidir.
    """
    report = GeodeticAccuracyReport()

    for loc in REFERENCE_LOCATIONS:
        point = loc.point()

        computed_zone = CoordinateConverter.utm_zone_for(loc.lon)
        report.checks.append(
            AccuracyCheck(
                name=f"UTM zone — {loc.name}",
                passed=computed_zone == loc.expected_utm_zone,
                detail=(
                    f"beklenen={loc.expected_utm_zone} hesaplanan={computed_zone} "
                    f"(kaynak: {loc.source})"
                ),
            )
        )

        utm_err = _round_trip_utm_error_m(point)
        report.checks.append(
            AccuracyCheck(
                name=f"UTM round-trip — {loc.name}",
                passed=utm_err <= round_trip_tolerance_m,
                detail=f"hata={utm_err * 1000:.4f} mm (tolerans={round_trip_tolerance_m * 1000:.1f} mm)",
            )
        )

        merc_err = _round_trip_web_mercator_error_m(point)
        report.checks.append(
            AccuracyCheck(
                name=f"Web Mercator round-trip — {loc.name}",
                passed=merc_err <= round_trip_tolerance_m,
                detail=f"hata={merc_err * 1000:.4f} mm (tolerans={round_trip_tolerance_m * 1000:.1f} mm)",
            )
        )

    by_name = {loc.name: loc for loc in REFERENCE_LOCATIONS}
    for ref in REFERENCE_DISTANCES:
        a, b = by_name[ref.a], by_name[ref.b]
        computed_km = CoordinateConverter.haversine_distance(a.point(), b.point()) / 1000.0
        err = abs(computed_km - ref.published_km)
        report.checks.append(
            AccuracyCheck(
                name=f"Mesafe — {ref.a} <-> {ref.b}",
                passed=err <= ref.tolerance_km,
                detail=(
                    f"hesaplanan={computed_km:.2f} km yayınlanan={ref.published_km:.2f} km "
                    f"fark={err:.2f} km (tolerans={ref.tolerance_km} km, kaynak: {ref.source})"
                ),
            )
        )

    return report


__all__ = [
    "ReferenceLocation",
    "ReferenceDistance",
    "REFERENCE_LOCATIONS",
    "REFERENCE_DISTANCES",
    "AccuracyCheck",
    "GeodeticAccuracyReport",
    "run_geodetic_accuracy_suite",
]
