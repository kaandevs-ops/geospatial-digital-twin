"""
Thermal Comfort (Termal Konfor Endeksi)
========================================

Roadmap V4 - Faz E11: "Analysis Engine: Gerçek CFD-Lite Rüzgar ve Termal
Konfor Endeksi".

Kapanmadan önceki durum (denetim maddesi): D3 (`WindSimulation` - wake-etkili
"yaklaşık CFD" rüzgar modeli, Wise 1970) ve D20 (`GaussianPlumeSimulation`)
ile açık-hava çevresel simülasyonlar güçlendirildi, ayrıca `sun_simulation`
paketinde D4 SPA tabanlı `RoofIrradiance` (clear-sky ışınım) ve `visibility`
paketinde `ShadowAnalysis` (gerçek gölge/occluder testi) zaten mevcuttu -
ama bu üç alt sistem (güneş ışınımı + rüzgar + gölge) hiçbir yerde **tek bir
açık-hava termal konfor endeksinde** birleştirilmiyordu.

Bu modül, roadmap'in hedeflediği "basitleştirilmiş UTCI/PET benzeri" endeksi
uygular. ÖNEMLİ - dürüst kapsam sınırlaması: burada hesaplanan endeks gerçek
UTCI (Bröde et al. 2012, 6 değişkenli çok yüksek dereceden polinom regresyon)
ya da PET (Höppe 1999, tam insan-enerji-dengesi modeli, nem/giysi/metabolizma
dahil) DEĞİLDİR - roadmap maddesinin kendisi de bunu "basitleştirilmiş...
benzeri bir endeks" olarak tanımlar. Burada uygulanan, literatürde adı konmuş
iki gerçek fiziksel ilişkiye dayanan, stdlib-only, deterministik bir
yaklaşıklamadır:

1. **Ortalama Işınım Sıcaklığı (Tmrt) - radyatif denge:**
   `Tmrt^4 = Tair^4 + (a_k * I_direct) / (eps * sigma)`
   İnsan vücudunun doğrudan kısa-dalga güneş ışınımı altındaki radyatif
   sıcaklık artışının basitleştirilmiş hali (yalnızca doğrudan bileşen;
   difüz/yansıyan gökyüzü ışınımı ve uzun-dalga alışverişi ihmal edilir).
   Katsayılar VDI 3787 Part 2 / Jendritzky et al. (2012, "UTCI - Why
   another thermal index?") çalışmasında kullanılan tipik insan-vücudu
   soğurma/yayınım katsayılarıdır (a_k≈0.7, eps≈0.97).
2. **Rüzgar soğutma etkisi:** Tmrt'nin sıcaklığa katkısı rüzgar hızıyla
   üstel olarak söner (zorlanmış konveksiyonun artan ısı transfer
   katsayısının niteliksel yansıması) + doğrudan orantılı bir ek soğuma
   terimi (Newton soğuma kanununun basitleştirilmiş hali - tam NWS/JAG-TI
   rüzgar-serinliği regresyonu değil, ama aynı yönde/mertebede davranır).

`ShadowAnalysis` (Faz 6/`visibility`) gölgede olup olmadığını belirler -
gölgedeyse doğrudan ışınım sıfırlanır (`RoofIrradiance` çağrılmaz).
`WindSimulation`'ın (D3, `environmental_sim` - bu paketin kendi modülü)
ürettiği bir `WindField`'dan doğrudan örnekleme yapan
`evaluate_with_wind_field()` köprüsü, D3 ile bu modülü birleştirir.

Bağımlılık: yalnızca stdlib (math, dataclasses, datetime) + projenin kendi
`lighting`/`sun_simulation`/`visibility`/`mesh_engine`/`core_engine` API'leri.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ...core_engine.coordinate_systems import GeoPoint
from ...mesh_engine import Mesh3D
from ...lighting import SolarPositionCalculator
from ..sun_simulation import RoofIrradiance, IrradianceResult
from ..visibility import ShadowAnalysis

if TYPE_CHECKING:
    # Aynı paket içinde tanımlı (`environmental_sim/__init__.py`) - döngüsel
    # import'tan kaçınmak için yalnızca tip kontrolü sırasında import edilir;
    # çalışma zamanında `from __future__ import annotations` sayesinde bu
    # anotasyon hiç değerlendirilmez (bkz. `evaluate_with_wind_field`).
    from . import WindField

Vec3 = tuple[float, float, float]


@dataclass(slots=True)
class ThermalComfortResult:
    """Tek bir nokta/an için hesaplanan termal konfor değerlendirmesi."""

    apparent_temperature_c: float
    mean_radiant_temp_c: float
    air_temp_c: float
    wind_speed_mps: float
    in_shadow: bool
    irradiance_watts_per_m2: float
    category: str


class ThermalComfort:
    """Roadmap: 'Termal Konfor Endeksi' - `RoofIrradiance` (D4 SPA tabanlı
    clear-sky ışınım) + `WindSimulation` (D3 wake-etkili rüzgar) +
    `ShadowAnalysis` (gerçek gölge testi) birleşimi, basitleştirilmiş
    UTCI-benzeri bir "hissedilen sıcaklık" (apparent temperature) ve
    kategorik ısı-stresi sınıflandırması üretir.

    Modül üstü docstring'te açıklanan varsayımlar/kaynaklar geçerlidir.
    """

    #: Stefan-Boltzmann sabiti, W/(m^2*K^4).
    STEFAN_BOLTZMANN = 5.670374e-8
    #: İnsan vücudu/giysisinin uzun-dalga yayınım katsayısı (VDI 3787).
    BODY_EMISSIVITY = 0.97
    #: İnsan vücudunun doğrudan kısa-dalga (güneş) soğurma katsayısı (VDI 3787).
    ABSORPTION_COEFFICIENT = 0.7
    #: Rüzgarın radyatif kazancı sönümleme hızı (m/s cinsinden karakteristik
    #: ölçek) - büyük değer daha yavaş sönüm demektir.
    WIND_DECAY_MPS = 3.0
    #: Doğrudan rüzgar-soğutma terimi, °C azalma / (m/s).
    WIND_COOLING_COEFF = 0.8

    # UTCI'nin GERÇEK ısı-stresi kategori sınırları (Bröde et al. 2012,
    # "Deriving the operational procedure for the Universal Thermal Climate
    # Index (UTCI)"). Burada hesaplanan endeks tam UTCI değildir (bkz. modül
    # üstü docstring) - ama okunabilirlik ve endüstri-standart isimlendirme
    # için aynı eşik/etiketler kullanılır ("basitleştirilmiş UTCI-benzeri").
    _CATEGORIES: tuple[tuple[float, str], ...] = (
        (-40.0, "aşırı soğuk stresi"),
        (-27.0, "çok güçlü soğuk stresi"),
        (-13.0, "güçlü soğuk stresi"),
        (0.0, "orta soğuk stresi"),
        (9.0, "hafif soğuk stresi"),
        (26.0, "termal konfor"),
        (32.0, "orta sıcak stresi"),
        (38.0, "güçlü sıcak stresi"),
        (46.0, "çok güçlü sıcak stresi"),
        (math.inf, "aşırı sıcak stresi"),
    )

    @classmethod
    def _categorize(cls, apparent_temp_c: float) -> str:
        for upper, label in cls._CATEGORIES:
            if apparent_temp_c < upper:
                return label
        return cls._CATEGORIES[-1][1]

    @classmethod
    def mean_radiant_temperature(cls, air_temp_c: float,
                                  irradiance_watts_per_m2: float) -> float:
        """Basitleştirilmiş radyatif denge (bkz. modül üstü docstring #1):

            Tmrt^4 = Tair^4 + (a_k * I_direct) / (eps * sigma)

        Sıcaklıklar Kelvin'e çevrilip hesaplanır, sonuç tekrar °C'ye
        döndürülür. `irradiance_watts_per_m2 <= 0` ise (gece/gölge) Tmrt
        hava sıcaklığına eşittir.
        """
        t_air_k = air_temp_c + 273.15
        i_direct = max(irradiance_watts_per_m2, 0.0)
        t_mrt_k4 = t_air_k ** 4 + (cls.ABSORPTION_COEFFICIENT * i_direct) / (
            cls.BODY_EMISSIVITY * cls.STEFAN_BOLTZMANN
        )
        t_mrt_k = t_mrt_k4 ** 0.25
        return t_mrt_k - 273.15

    @classmethod
    def apparent_temperature(cls, air_temp_c: float, mean_radiant_temp_c: float,
                              wind_speed_mps: float) -> float:
        """Basitleştirilmiş 'hissedilen sıcaklık' (bkz. modül üstü docstring
        #2): radyan kazanç (Tmrt - Tair, negatifse 0) rüzgarla üstel olarak
        söner, ayrıca doğrudan orantılı bir rüzgar-soğutma terimi eklenir."""
        radiant_gain = max(mean_radiant_temp_c - air_temp_c, 0.0)
        wind_speed_mps = max(wind_speed_mps, 0.0)
        damped_gain = radiant_gain * math.exp(-wind_speed_mps / cls.WIND_DECAY_MPS)
        wind_cooling = cls.WIND_COOLING_COEFF * wind_speed_mps
        return air_temp_c + damped_gain - wind_cooling

    @classmethod
    def evaluate(cls, point: Vec3, location: GeoPoint, when_utc: datetime,
                 occluders: list[Mesh3D], air_temp_c: float,
                 wind_speed_mps: float, roof_tilt_deg: float = 90.0,
                 roof_azimuth_deg: float = 180.0) -> ThermalComfortResult:
        """Belirli bir nokta/an için termal konforu değerlendirir.

        `roof_tilt_deg=90.0` (dikey panel) varsayılanı, `RoofIrradiance`'ın
        yatay bir çatı YERİNE ayakta duran bir insanın gövdesine gelen
        doğrudan ışınımı yaklaşık temsil etmesi içindir (dikey yüzey normali
        ufka paralel - Lambert kosinüs kuralı ayakta duran bir insan için
        yataydan çok daha gerçekçi bir yaklaşıklamadır).
        """
        sun = SolarPositionCalculator.compute(location, when_utc)
        shadow = ShadowAnalysis.evaluate(point, location, when_utc, occluders)

        if shadow.in_shadow or not sun.is_daylight:
            irradiance = IrradianceResult(
                watts_per_m2=0.0, sun_elevation_deg=sun.elevation_deg,
                incidence_angle_deg=90.0,
            )
        else:
            irradiance = RoofIrradiance.compute(
                sun, roof_tilt_deg=roof_tilt_deg, roof_azimuth_deg=roof_azimuth_deg,
            )

        tmrt = cls.mean_radiant_temperature(air_temp_c, irradiance.watts_per_m2)
        apparent = cls.apparent_temperature(air_temp_c, tmrt, wind_speed_mps)
        category = cls._categorize(apparent)

        return ThermalComfortResult(
            apparent_temperature_c=apparent,
            mean_radiant_temp_c=tmrt,
            air_temp_c=air_temp_c,
            wind_speed_mps=wind_speed_mps,
            in_shadow=shadow.in_shadow,
            irradiance_watts_per_m2=irradiance.watts_per_m2,
            category=category,
        )

    @classmethod
    def evaluate_with_wind_field(cls, point: Vec3, location: GeoPoint,
                                  when_utc: datetime, occluders: list[Mesh3D],
                                  air_temp_c: float, wind_field: "WindField",
                                  grid_origin: tuple[float, float] = (0.0, 0.0),
                                  **kwargs) -> ThermalComfortResult:
        """Faz E11 asıl köprüsü: D3 `WindSimulation.simulate()` çıktısı bir
        `WindField`'dan, `point`'in (x, y) dünya-uzayı konumuna en yakın
        hücredeki rüzgar hızını örnekleyip `evaluate()`'e besler - böylece
        wake-etkili (bina arkasında yavaşlayan) gerçek rüzgar alanı, termal
        konfor hesabına doğrudan yansır (roadmap'in "CFD-lite rüzgar ve
        termal konfor" hedefinin birleşim noktası).
        """
        ox, oy = grid_origin
        col = round((point[0] - ox) / wind_field.cell_size_m)
        row = round((point[1] - oy) / wind_field.cell_size_m)
        col = max(0, min(wind_field.width - 1, col))
        row = max(0, min(wind_field.height - 1, row))
        speed, _direction = wind_field.at(col, row)
        return cls.evaluate(point, location, when_utc, occluders, air_temp_c,
                             speed, **kwargs)
