"""FAZ S2.4 — Datum ve projeksiyon dönüşümleri: 7-parametreli Helmert
dönüşümü + jeoit ondülasyonu (elipsoidal → ortometrik yükseklik).

`core_engine/geo_reference/proj_backend.py` üzerine inşa edilir — bu modül
projeksiyon (harita düzlemi) dönüşümünü değil, **datum** (referans elipsoid/
çerçeve) dönüşümünü ele alır (roadmap S2.4).

7-parametreli Helmert (Bursa-Wolf) dönüşümü — gerçek, standart formül
(kaynak: IOGP Geomatics Guidance Note 7-2, "Coordinate Conversions and
Transformations including Formulas"):

    [X]   [X]       [ 1   -rz   ry ] [X]
    [Y] = [Y]  + (1+s)[ rz   1  -rx ] [Y]   (küçük açı yaklaşımı, ppm ölçek)
    [Z]_t [Z]_0       [-ry   rx   1 ] [Z]_s

Dönüşüm parametreleri (tx, ty, tz, rx, ry, rz, s) **kod içine gömülmez** —
roadmap ilkesi gereği kaynağı belgelenmiş, güncellenebilir bir parametre
kümesi olarak `DatumTransformParameters` ile dışarıdan verilir (örn.
HGK/TUSAGA-Aktif resmi parametre setlerinden okunarak). Bu modül parametre
kaynağını icat ETMEZ — sadece matematiği uygular.

Jeoit ondülasyonu: N değeri de aynı şekilde dışarıdan (gerçek bir jeoit grid
dosyasından enterpolasyonla) sağlanır; bu modül sadece H = h − N formülünü
uygular ve N sağlanmadan ortometrik yükseklik hesaplamayı reddeder.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


class InsufficientDataError(ValueError):
    """Dönüşüm parametreleri veya jeoit ondülasyonu sağlanmadığında —
    varsayılan/uydurma parametre KULLANILMAZ."""


@dataclass(slots=True)
class DatumTransformParameters:
    """7 parametreli Helmert dönüşüm parametreleri. `source` alanı, bu
    parametrelerin hangi resmi kaynaktan geldiğini belgeler (roadmap
    ilkesi: "gerçek sayılar kod içine gömülmeyecek, kaynak dosyadan/
    README'de referanslı şekilde yüklenecek")."""

    tx_m: float
    ty_m: float
    tz_m: float
    rx_arcsec: float
    ry_arcsec: float
    rz_arcsec: float
    scale_ppm: float
    source: str  # örn. "HGK TUSAGA-Aktif resmi parametre seti, yayın tarihi X"


@dataclass(slots=True)
class GeocentricCoordinate:
    x_m: float
    y_m: float
    z_m: float


def _arcsec_to_rad(arcsec: float) -> float:
    return arcsec * (math.pi / (180.0 * 3600.0))


def geodetic_to_geocentric(
    latitude_deg: float, longitude_deg: float, ellipsoidal_height_m: float,
    semi_major_axis_m: float, flattening: float,
) -> GeocentricCoordinate:
    """Jeodezik (φ, λ, h) → Kartezyen jeosentrik (X, Y, Z). Standart
    formül (herhangi bir jeodezi ders kitabı); elipsoid parametreleri
    (a, f) çağıran koddan gelir — varsayılan WGS84 burada ZORLANMAZ,
    çağıran açıkça belirtmelidir (farklı datum'lar farklı elipsoid
    kullanabilir)."""

    lat_rad = math.radians(latitude_deg)
    lon_rad = math.radians(longitude_deg)
    e2 = 2 * flattening - flattening ** 2  # birinci dış merkezlik karesi
    sin_lat = math.sin(lat_rad)
    n_radius = semi_major_axis_m / math.sqrt(1 - e2 * sin_lat ** 2)

    x = (n_radius + ellipsoidal_height_m) * math.cos(lat_rad) * math.cos(lon_rad)
    y = (n_radius + ellipsoidal_height_m) * math.cos(lat_rad) * math.sin(lon_rad)
    z = (n_radius * (1 - e2) + ellipsoidal_height_m) * sin_lat
    return GeocentricCoordinate(x_m=x, y_m=y, z_m=z)


def apply_helmert_transform(
    coord: GeocentricCoordinate, params: DatumTransformParameters
) -> GeocentricCoordinate:
    """7-parametreli Helmert (Bursa-Wolf) dönüşümünü, küçük açı
    yaklaşımıyla (rotasyonlar yay saniyesi mertebesinde olduğundan bu
    yaklaşım jeodezik pratikte standarttır) uygular."""

    rx = _arcsec_to_rad(params.rx_arcsec)
    ry = _arcsec_to_rad(params.ry_arcsec)
    rz = _arcsec_to_rad(params.rz_arcsec)
    scale = 1.0 + params.scale_ppm * 1e-6

    x, y, z = coord.x_m, coord.y_m, coord.z_m
    x_t = params.tx_m + scale * (x - rz * y + ry * z)
    y_t = params.ty_m + scale * (rz * x + y - rx * z)
    z_t = params.tz_m + scale * (-ry * x + rx * y + z)

    return GeocentricCoordinate(x_m=x_t, y_m=y_t, z_m=z_t)


def geocentric_to_geodetic(
    coord: GeocentricCoordinate, semi_major_axis_m: float, flattening: float,
    tolerance_m: float = 1e-9, max_iterations: int = 20,
) -> tuple[float, float, float]:
    """Kartezyen jeosentrik (X, Y, Z) → jeodezik (φ, λ, h). Bowring'in
    yinelemeli (iterative) yöntemi — kapalı formül yerine yakınsama
    kriterine kadar gerçek yineleme yapılır (kaynak: standart jeodezi
    pratiği; kapalı-form yaklaşık formüller yerine tercih edilir çünkü
    yakınsama toleransı açıkça kontrol edilebilir)."""

    x, y, z = coord.x_m, coord.y_m, coord.z_m
    lon_rad = math.atan2(y, x)
    e2 = 2 * flattening - flattening ** 2
    p = math.hypot(x, y)

    lat_rad = math.atan2(z, p * (1 - e2))  # ilk tahmin
    for _ in range(max_iterations):
        sin_lat = math.sin(lat_rad)
        n_radius = semi_major_axis_m / math.sqrt(1 - e2 * sin_lat ** 2)
        h = p / math.cos(lat_rad) - n_radius
        new_lat = math.atan2(z, p * (1 - e2 * n_radius / (n_radius + h)))
        if abs(new_lat - lat_rad) < tolerance_m / semi_major_axis_m:
            lat_rad = new_lat
            break
        lat_rad = new_lat
    else:
        raise InsufficientDataError(
            f"Jeodezik koordinat yinelemesi {max_iterations} adımda yakınsamadı "
            "(tolerance_m artırılabilir veya girdi koordinatları kontrol edilmelidir)."
        )

    sin_lat = math.sin(lat_rad)
    n_radius = semi_major_axis_m / math.sqrt(1 - e2 * sin_lat ** 2)
    h = p / math.cos(lat_rad) - n_radius

    return math.degrees(lat_rad), math.degrees(lon_rad), h


def orthometric_height(ellipsoidal_height_m: float, geoid_undulation_m: float | None) -> float:
    """H (ortometrik yükseklik) = h (elipsoidal) − N (jeoit ondülasyonu).
    N sağlanmadan (None) hesaplama YAPILMAZ — roadmap ilkesi: "elipsoidal
    yükseklikten ortometrik yüksekliğe gerçek dönüşüm", uydurma N=0
    varsayımı kabul edilemez (bu, düz bir jeoit varsayımına eşdeğerdir ve
    gerçek arazi için yanlıştır)."""

    if geoid_undulation_m is None:
        raise InsufficientDataError(
            "Jeoit ondülasyonu (N) sağlanmadan ortometrik yükseklik hesaplanamaz. "
            "N=0 varsayımı YAPILMAZ — gerçek bir jeoit modelinden (örn. TG-03/EGM2008 "
            "grid dosyası) enterpolasyon gerekir."
        )
    return ellipsoidal_height_m - geoid_undulation_m
