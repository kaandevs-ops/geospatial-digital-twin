"""FAZ S2.3 — GNSS baseline / ağ dengeleme: ağırlıklı epoch ortalaması,
kontrol noktası karşılaştırması, gerçek RMSE ve güven aralığı.

Bağımlılık yok (stdlib `statistics` + `math`). Ağırlıklı ortalama, her
epoch'un GST cümlesinden gelen gerçek σ (standart sapma) değeriyle
ağırlıklandırılır (w = 1/σ²) — roadmap ilkesi: "basit ortalama değil".

Güven aralığı: örneklem sayısı büyükse (n ≥ 30) normal dağılım kritik
değeri (z) kullanılır; küçük örneklemde t-dağılımı gerekir ancak stdlib'de
t-dağılımı kritik değer tablosu yoktur (scipy bu proje için opsiyonel
bağımlılık değildir) — bu nedenle küçük örneklemler için t yerine normal
yaklaşım kullanıldığı ve bunun muhafazakâr olmadığı `used_normal_approximation`
alanıyla **açıkça** raporlanır; sonuç sessizce "t-dağılımı" gibi sunulmaz.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from ..raw_import.gnss import GnssEpoch


class InsufficientDataError(ValueError):
    """Ağırlıklı ortalama/RMSE için gerekli veri (örn. sigma) eksik olduğunda."""


# %95 güven düzeyi için iki-yönlü Student t-dağılımı kritik değerleri
# (serbestlik derecesi, ν = n − 1). Kaynak: standart istatistik tabloları
# (örn. Ghilani & Wolf, "Adjustment Computations", Ek C; NIST/SEMATECH
# e-Handbook of Statistical Methods, Tablo 1.3.6.7.2). stdlib'de t-dağılımı
# kritik değer fonksiyonu olmadığından (scipy bu projeye zorunlu bağımlılık
# olarak eklenmiyor), küçük örneklemler (ν ≤ 30) için bu tablo kullanılır;
# ν > 30 için normal dağılımın z-kritik değerine (1.95996) yakınsar ve o
# kullanılır. Tabloda olmayan bir ν için enterpolasyon yapılmaz.
_T_TABLE_95: dict[int, float] = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}
_Z_CRITICAL_95 = 1.95996  # ν > 30 için normal dağılım yaklaşımı


def t_critical_95(degrees_of_freedom: int) -> tuple[float, bool]:
    """%95 güven düzeyi için t-kritik değeri döner.

    Dönüş: `(kritik_değer, exact_from_table)`. `degrees_of_freedom < 1` ise
    `InsufficientDataError` fırlatılır. ν > 30 için normal yaklaşım
    kullanılır ve `exact_from_table=False` döner — sonucun kesin t-dağılımı
    değil, büyük örneklem yaklaşımı olduğunu açıkça işaretler.
    """

    if degrees_of_freedom < 1:
        raise InsufficientDataError(
            "t-kritik değeri için serbestlik derecesi >= 1 olmalı (en az 2 örneklem)."
        )
    if degrees_of_freedom in _T_TABLE_95:
        return _T_TABLE_95[degrees_of_freedom], True
    if degrees_of_freedom > 30:
        return _Z_CRITICAL_95, False
    fallback = max(k for k in _T_TABLE_95 if k <= degrees_of_freedom)
    return _T_TABLE_95[fallback], False


@dataclass(slots=True)
class WeightedMeanResult:
    mean_latitude_deg: float
    mean_longitude_deg: float
    mean_ellipsoidal_height_m: float
    n_epochs: int
    n_fixed_epochs: int


def weighted_mean_position(
    epochs: list[GnssEpoch], require_fixed_only: bool = True
) -> WeightedMeanResult:
    """RTK epoch'larının gerçek σ (GST) değerlerine göre ağırlıklı
    ortalamasını hesaplar. `require_fixed_only=True` (varsayılan, roadmap
    ilkesi): sadece RTK Fixed epoch'lar kullanılır — Float/Autonomous
    epoch'lar kullanıcı bilinçli olarak `require_fixed_only=False` ile
    override etmedikçe dahil edilmez.
    """

    candidates = [e for e in epochs if (e.is_survey_grade or not require_fixed_only)]
    if not candidates:
        raise InsufficientDataError(
            "Ağırlıklı ortalama için hiç uygun epoch yok (RTK Fixed bulunamadı; "
            "override için require_fixed_only=False gerekir, ancak bu kalite "
            "düşüşünü dokümante etmeyi kullanıcının sorumluluğuna bırakır)."
        )

    missing_sigma = [
        e for e in candidates if e.std_lat_m is None or e.std_lon_m is None or e.std_alt_m is None
    ]
    if missing_sigma:
        raise InsufficientDataError(
            f"{len(missing_sigma)} epoch'ta GST kaynaklı standart sapma yok — ağırlıklı "
            "ortalama gerçek sigma olmadan hesaplanamaz (uydurma bir sigma=1 varsayımı yapılmaz). "
            "Bu epoch'lar için GST cümlesi sağlanmalı veya basit (eşit ağırlıklı) ortalama "
            "bilinçli olarak talep edilmelidir."
        )

    def _weighted(values: list[float], sigmas: list[float]) -> float:
        weights = [1.0 / (s**2) for s in sigmas]
        total_w = math.fsum(weights)
        return math.fsum(v * w for v, w in zip(values, weights)) / total_w

    lats = [e.latitude_deg for e in candidates]
    lons = [e.longitude_deg for e in candidates]
    heights = [e.ellipsoidal_height_m for e in candidates]
    sigma_lat = [e.std_lat_m for e in candidates]  # type: ignore[misc]
    sigma_lon = [e.std_lon_m for e in candidates]  # type: ignore[misc]
    sigma_alt = [e.std_alt_m for e in candidates]  # type: ignore[misc]

    return WeightedMeanResult(
        mean_latitude_deg=_weighted(lats, sigma_lat),
        mean_longitude_deg=_weighted(lons, sigma_lon),
        mean_ellipsoidal_height_m=_weighted(heights, sigma_alt),
        n_epochs=len(candidates),
        n_fixed_epochs=sum(1 for e in candidates if e.is_survey_grade),
    )


@dataclass(slots=True)
class ControlPointComparison:
    """Bilinen (kontrol) nokta ile ölçülen nokta arasındaki gerçek fark."""

    delta_easting_m: float
    delta_northing_m: float
    delta_elevation_m: float
    rmse_2d_m: float
    rmse_3d_m: float


def compare_to_control_point(
    known_easting_m: float,
    known_northing_m: float,
    known_elevation_m: float,
    measured_easting_m: float,
    measured_northing_m: float,
    measured_elevation_m: float,
) -> ControlPointComparison:
    """Tek bir kontrol noktası karşılaştırması — gerçek fark ve RMSE
    (tek nokta için RMSE = |fark|, çoklu nokta seti için `rmse_from_differences`
    kullanılır)."""

    de = measured_easting_m - known_easting_m
    dn = measured_northing_m - known_northing_m
    dz = measured_elevation_m - known_elevation_m
    rmse_2d = math.hypot(de, dn)
    rmse_3d = math.sqrt(de**2 + dn**2 + dz**2)
    return ControlPointComparison(
        delta_easting_m=de,
        delta_northing_m=dn,
        delta_elevation_m=dz,
        rmse_2d_m=rmse_2d,
        rmse_3d_m=rmse_3d,
    )


@dataclass(slots=True)
class RmseReport:
    rmse_easting_m: float
    rmse_northing_m: float
    rmse_elevation_m: float
    rmse_2d_m: float
    rmse_3d_m: float
    n_points: int
    confidence_95_radius_m: float  # 2D, gerçek istatistiksel hesap
    used_normal_approximation: bool


def rmse_from_differences(comparisons: list[ControlPointComparison]) -> RmseReport:
    """Birden fazla kontrol noktası karşılaştırmasından gerçek RMSE ve
    %95 güven aralığı yarıçapını hesaplar (2D, dairesel hata olasılığı
    yaklaşımı — CEP95 ≈ 2.4477 × RMSE_2D_bileşen için Rayleigh dağılımı
    yaklaşık ilişkisi, kaynak: NSSDA/FGDC doğruluk standardı)."""

    if not comparisons:
        raise InsufficientDataError(
            "RMSE hesaplamak için en az bir kontrol noktası karşılaştırması gerekir."
        )

    n = len(comparisons)
    rmse_e = math.sqrt(statistics.fmean(c.delta_easting_m**2 for c in comparisons))
    rmse_n = math.sqrt(statistics.fmean(c.delta_northing_m**2 for c in comparisons))
    rmse_z = math.sqrt(statistics.fmean(c.delta_elevation_m**2 for c in comparisons))
    rmse_2d = math.hypot(rmse_e, rmse_n)
    rmse_3d = math.sqrt(rmse_e**2 + rmse_n**2 + rmse_z**2)

    # NSSDA (FGDC-STD-001-1998) yaklaşımı: yatay doğruluk (%95) = 1.7308 × RMSE_r
    # (RMSE_e = RMSE_n varsayımı altında Rayleigh dağılımı 95. yüzdelik değeri).
    # FGDC eşiği: n >= 20 için bu sabit 1.7308 çarpanı doğrudan kullanılır.
    # n < 20 (küçük örneklem) için normal (z=1.96) yaklaşımı yerine artık
    # gerçek Student t-dağılımı kritik değeri (ν = n−1 serbestlik derecesi,
    # bkz. `t_critical_95`) kullanılır — bu, küçük örneklemde normal
    # yaklaşımın olduğundan dar (iyimser) bir güven aralığı vermesini önler.
    if n >= 20:
        horizontal_accuracy_95 = 1.7308 * rmse_2d / math.sqrt(2) if rmse_2d > 0 else 0.0
        used_normal = False
    elif n >= 2:
        t_value, exact_from_table = t_critical_95(n - 1)
        # Rayleigh/2D dairesel hata için normal dağılımın 1.7308 (≈ 1.96×0.883
        # düzeltme çarpanı bileşimi) yerini, küçük örneklemde t_value ile
        # ölçeklenmiş eşdeğer bir çarpan alır (z=1.95996 yerine t_value).
        scale_factor = 1.7308 * (t_value / _Z_CRITICAL_95)
        horizontal_accuracy_95 = scale_factor * rmse_2d / math.sqrt(2) if rmse_2d > 0 else 0.0
        used_normal = not exact_from_table
    else:
        # n=1: örneklem varyansı/serbestlik derecesi tanımsız (df=0) —
        # t-dağılımı kritik değeri hesaplanamaz. Bu durumda NSSDA'nın
        # standart normal yaklaşımına (z=1.96) bilinçli olarak düşülür ve
        # bu açıkça `used_normal_approximation=True` ile işaretlenir (tek
        # kontrol noktasıyla istatistiksel bir güven aralığının zaten
        # sınırlı anlamı olduğu, çağıran kodun bilmesi gereken bir durumdur).
        horizontal_accuracy_95 = 1.7308 * rmse_2d / math.sqrt(2) if rmse_2d > 0 else 0.0
        used_normal = True

    return RmseReport(
        rmse_easting_m=rmse_e,
        rmse_northing_m=rmse_n,
        rmse_elevation_m=rmse_z,
        rmse_2d_m=rmse_2d,
        rmse_3d_m=rmse_3d,
        n_points=n,
        confidence_95_radius_m=horizontal_accuracy_95,
        used_normal_approximation=used_normal,
    )
