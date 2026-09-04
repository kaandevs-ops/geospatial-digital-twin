"""
Sun Simulation
==============

Roadmap Phase 6 - "Sun Simulation": Saat, Tarih, Mevsim, Solar Angle,
Solar Exposure, Roof Irradiance, Shadow Projection.

`SolarPosition`/`SolarPositionCalculator` Phase 2 `lighting` modülünden
yeniden kullanılır (tek doğruluk kaynağı ilkesi). Bu modül onun üzerine
saatlik/mevsimsel tarama, maruz kalma (exposure) süresi, çatı için basit
clear-sky irradiance modeli ve zemine gölge izdüşümü ekler.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ...climate_data import ClimateError, HourlyClimateSample, OpenMeteoClient
from ...core_engine.coordinate_systems import GeoPoint
from ...core_engine.geometry_engine import Point2D, Polygon
from ...lighting import SolarPosition, SolarPositionCalculator, SunLight
from ...mesh_engine import Mesh3D

# Yeniden dışa aktarım - Phase 6 kullanıcıları `sun_simulation` içinden de
# erişebilsin (tek import noktası kolaylığı).
__all__ = [
    "SolarPosition",
    "SolarPositionCalculator",
    "SeasonalSunPath",
    "SolarExposureResult",
    "SolarExposure",
    "RoofIrradiance",
    "IrradianceResult",
    "ShadowProjection",
]


# ============================================================================ #
# Seasonal Sun Path (Saat / Tarih / Mevsim / Solar Angle)
# ============================================================================ #


@dataclass(slots=True)
class SunPathSample:
    when_utc: datetime
    position: SolarPosition


class SeasonalSunPath:
    """Roadmap: 'Saat', 'Tarih', 'Mevsim', 'Solar Angle'. Belirli bir gün
    boyunca (veya mevsim temsilcisi 4 gün: gündönümü/ekinoks) güneş
    pozisyonunu örnekler."""

    SEASON_REPRESENTATIVE_MONTHS_DAYS = {
        "kis_gunumu": (12, 21),
        "ilkbahar_ekinoksu": (3, 20),
        "yaz_gunumu": (6, 21),
        "sonbahar_ekinoksu": (9, 22),
    }

    @staticmethod
    def sample_day(
        location: GeoPoint, date: datetime, step_minutes: int = 30
    ) -> list[SunPathSample]:
        samples: list[SunPathSample] = []
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        steps = (24 * 60) // step_minutes
        for i in range(steps):
            when = day_start + timedelta(minutes=i * step_minutes)
            pos = SolarPositionCalculator.compute(location, when)
            samples.append(SunPathSample(when_utc=when, position=pos))
        return samples

    @staticmethod
    def sample_seasons(
        location: GeoPoint, year: int, hour_utc: int = 12
    ) -> dict[str, SolarPosition]:
        result: dict[str, SolarPosition] = {}
        for season, (month, day) in SeasonalSunPath.SEASON_REPRESENTATIVE_MONTHS_DAYS.items():
            when = datetime(year, month, day, hour_utc, tzinfo=timezone.utc)
            result[season] = SolarPositionCalculator.compute(location, when)
        return result

    @staticmethod
    def solar_noon(location: GeoPoint, date: datetime) -> SunPathSample:
        """Günün en yüksek güneş yüksekliğinin (elevation) yaklaşık olarak
        oluştuğu saat - saatlik taramayla bulunur (basit ama sağlam)."""
        best: SunPathSample | None = None
        for sample in SeasonalSunPath.sample_day(location, date, step_minutes=10):
            if best is None or sample.position.elevation_deg > best.position.elevation_deg:
                best = sample
        assert best is not None
        return best


# ============================================================================ #
# Solar Exposure
# ============================================================================ #


@dataclass(slots=True)
class SolarExposureResult:
    point: tuple[float, float, float]
    daylight_hours: float
    direct_sun_hours: float
    shaded_hours: float

    @property
    def exposure_ratio(self) -> float:
        if self.daylight_hours <= 0:
            return 0.0
        return self.direct_sun_hours / self.daylight_hours


class SolarExposure:
    """Roadmap: 'Solar Exposure'. Bir noktanın bir gün boyunca doğrudan
    güneşe maruz kaldığı süreyi (engelleyici mesh'lere göre) hesaplar."""

    @staticmethod
    def compute(
        point: tuple[float, float, float],
        location: GeoPoint,
        date: datetime,
        occluders: list[Mesh3D],
        step_minutes: int = 30,
    ) -> SolarExposureResult:
        # Döngüsel import'tan kaçınmak için lazy import (visibility -> lighting
        # zaten import ediyor; sun_simulation -> visibility burada tek yönlü).
        from ..visibility import ShadowAnalysis

        daylight = 0
        direct = 0
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        steps = (24 * 60) // step_minutes
        for i in range(steps):
            when = day_start + timedelta(minutes=i * step_minutes)
            result = ShadowAnalysis.evaluate(point, location, when, occluders)
            if result.sun_elevation_deg > 0:
                daylight += 1
                if not result.in_shadow:
                    direct += 1
        hours_per_step = step_minutes / 60.0
        daylight_hours = daylight * hours_per_step
        direct_hours = direct * hours_per_step
        return SolarExposureResult(
            point=point,
            daylight_hours=daylight_hours,
            direct_sun_hours=direct_hours,
            shaded_hours=daylight_hours - direct_hours,
        )


# ============================================================================ #
# Roof Irradiance
# ============================================================================ #


@dataclass(slots=True)
class IrradianceResult:
    watts_per_m2: float
    sun_elevation_deg: float
    incidence_angle_deg: float


class RoofIrradiance:
    """Roadmap: 'Roof Irradiance'. Clear-sky (bulutsuz/temiz gökyüzü)
    irradiance modeli — GERÇEK meteorolojik (bulut, nem, aerosol) veriler
    olmadan bir *üst-sınır tahmini*dir (gerçek gün ışığı bundan daha
    düşük olur); roadmap'in "AI Değerlendirmeler / enerji potansiyeli"
    özelliğinin temelidir.

    Önceki sürümden farkı (daha rigor'lu hale getirildi):
        1. Hava kütlesi (air mass) artık basitleştirilmiş `1/sin(h)` yerine
           Kasten & Young (1989)'un yayınlanmış, düşük güneş açılarında da
           doğru kalan formülüyle hesaplanır:
               AM = 1 / [sin(h) + 0.50572 * (h_deg + 6.07995)^-1.6364]
           (h: güneş yükseklik açısı). `1/sin(h)` düşük elevation'da
           (gün doğumu/batımı) sonsuza ıraksar ve gerçekçi değildir;
           Kasten-Young bu bölgede de sınırlı/gerçekçi kalır.
        2. Işınım artık sadece DOĞRUDAN (direct/beam) bileşenden değil,
           standart ÜÇ BİLEŞENLİ eğimli-yüzey modelinden oluşur (Liu &
           Jordan, 1963 izotropik gökyüzü modelinin yapısı):
               POA = Doğrudan (beam) + İzotropik gökyüzü diffüzü + Zeminden yansıma
           Önceki sürüm yalnızca doğrudan bileşeni (Lambert kosinüs kuralı)
           hesaplıyordu — bu, açık havada bile gerçek değerin belirgin
           şekilde altında bir kestirim üretiyordu (diffüz bileşen açık
           gökyüzünde bile toplam ışınımın kayda değer bir kısmıdır).

    DÜRÜST SINIRLAMA: bu hâlâ bir CLEAR-SKY modelidir — bulutluluk oranı,
    nem, aerosol optik derinliği gibi gerçek meteorolojik girdileri
    (örn. `climate_data.open_meteo_client`'tan alınabilecek) içermez.
    Diffüz oranı (`DIFFUSE_FRACTION`) ve zemin albedosu sabit/temsili
    değerlerdir; gerçek bir enerji verimi (yield) hesabı için TMY
    (Typical Meteorological Year) verisi ve PVGIS/NASA POWER gibi
    kalibre edilmiş bir kaynakla karşılaştırılmalıdır.
    """

    SOLAR_CONSTANT = 1361.0  # W/m^2, atmosfer dışı (yeryüzü ort. mesafesinde)
    ATMOSPHERIC_TRANSMITTANCE = 0.75  # açık/temiz gökyüzü yaklaşık değeri (tek-bant basitleştirme)
    #: Açık gökyüzünde diffüz (dağınık gökyüzü) ışınımının, yatay düzlemdeki
    #: doğrudan bileşene oranı — temsili bir sabit (gerçekte güneş
    #: yüksekliğine ve atmosfer berraklığına göre değişir; örn. Liu-Jordan/
    #: Perez modellerinde bu ilişki açı-bağımlıdır, burada basitleştirilmiştir).
    DIFFUSE_FRACTION = 0.15
    #: Zemin albedosu — tipik şehir/kentsel yüzey (kaba çim/beton karışımı
    #: için literatürde sık kullanılan mertebe; siyah asfalt ~0.05, taze
    #: kar ~0.8 gibi uç değerlerden belirgin şekilde farklı olabilir).
    GROUND_ALBEDO = 0.20

    @classmethod
    def _air_mass_kasten_young(cls, elevation_deg: float) -> float:
        """Kasten & Young (1989): 'Revised optical air mass tables and
        approximation formula', Applied Optics 28(22). Düşük güneş
        açılarında (gün doğumu/batımı) fiziksel olarak sınırlı kalan,
        yaygın kullanılan bir air-mass yaklaşımıdır."""
        h = max(elevation_deg, 0.0)
        denom = math.sin(math.radians(h)) + 0.50572 * (h + 6.07995) ** -1.6364
        return 1.0 / max(denom, 1e-6)

    @classmethod
    def compute(
        cls, sun: SolarPosition, roof_tilt_deg: float = 0.0, roof_azimuth_deg: float = 180.0
    ) -> IrradianceResult:
        if not sun.is_daylight:
            return IrradianceResult(0.0, sun.elevation_deg, 90.0)

        air_mass = cls._air_mass_kasten_young(sun.elevation_deg)
        transmittance = cls.ATMOSPHERIC_TRANSMITTANCE ** min(air_mass, 38.0)
        direct_normal_irradiance = cls.SOLAR_CONSTANT * transmittance

        # Panel normali ile güneş yönü arasındaki açı (incidence angle).
        sun_dir = sun.direction_vector()
        tilt_rad = math.radians(roof_tilt_deg)
        az_rad = math.radians(roof_azimuth_deg)
        panel_normal = (
            math.sin(tilt_rad) * math.sin(az_rad),
            math.sin(tilt_rad) * math.cos(az_rad),
            math.cos(tilt_rad),
        )
        cos_incidence = (
            sun_dir[0] * panel_normal[0]
            + sun_dir[1] * panel_normal[1]
            + sun_dir[2] * panel_normal[2]
        )
        cos_incidence = max(0.0, cos_incidence)
        incidence_deg = math.degrees(math.acos(max(-1.0, min(1.0, cos_incidence))))

        elevation_rad = math.radians(max(sun.elevation_deg, 0.0))
        sin_elevation = max(math.sin(elevation_rad), 0.0)

        # Yatay düzlemde diffüz ve toplam (GHI) — üç-bileşenli modelin girdisi.
        diffuse_horizontal = cls.DIFFUSE_FRACTION * direct_normal_irradiance * sin_elevation
        global_horizontal = direct_normal_irradiance * sin_elevation + diffuse_horizontal

        # 1) Doğrudan (beam) bileşen — panel normaline göre.
        beam_component = direct_normal_irradiance * cos_incidence

        # 2) İzotropik gökyüzü diffüzü — panelin gökyüzünü "gördüğü" oran
        #    (1+cos(tilt))/2 (Liu & Jordan, 1963 izotropik model).
        sky_view_factor = (1.0 + math.cos(tilt_rad)) / 2.0
        diffuse_component = diffuse_horizontal * sky_view_factor

        # 3) Zeminden yansıyan bileşen — panelin zemini "gördüğü" oran
        #    (1-cos(tilt))/2, zemin albedosuyla çarpılır.
        ground_view_factor = (1.0 - math.cos(tilt_rad)) / 2.0
        ground_reflected_component = global_horizontal * cls.GROUND_ALBEDO * ground_view_factor

        watts = beam_component + diffuse_component + ground_reflected_component
        return IrradianceResult(
            watts_per_m2=watts,
            sun_elevation_deg=sun.elevation_deg,
            incidence_angle_deg=incidence_deg,
        )

    @classmethod
    def daily_energy_kwh_per_m2(
        cls,
        location: GeoPoint,
        date: datetime,
        roof_tilt_deg: float = 0.0,
        roof_azimuth_deg: float = 180.0,
        step_minutes: int = 30,
    ) -> float:
        """Bir günlük toplam enerji (kWh/m^2) - roadmap'in 'çatı solar
        potansiyeli' değerlendirmesinin sayısal temeli.

        DÜRÜST SINIRLAMA: bu hâlâ CLEAR-SKY (bulutsuz varsayım) bir üst-sınır
        tahminidir. Gerçek bulutluluğa göre düzeltilmiş sonuç için
        `daily_energy_kwh_per_m2_with_real_climate()`'i kullanın (ağ erişimi
        ve `climate_data.OpenMeteoClient` gerektirir)."""
        total_wh = 0.0
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        steps = (24 * 60) // step_minutes
        hours_per_step = step_minutes / 60.0
        for i in range(steps):
            when = day_start + timedelta(minutes=i * step_minutes)
            sun = SolarPositionCalculator.compute(location, when)
            result = cls.compute(sun, roof_tilt_deg, roof_azimuth_deg)
            total_wh += result.watts_per_m2 * hours_per_step
        return total_wh / 1000.0

    #: Kasten & Czeplak (1980), "Solar and terrestrial radiation dependent
    #: on the amount and type of cloud": açık-gökyüzü ışınım indeksi
    #: k_c = 1 - 0.75*(N/8)^3.4, N = okta cinsinden bulut örtüsü (0-8).
    #: Meteoroloji/güneş enerjisi literatüründe yaygın kullanılan, ampirik
    #: ama YAYINLANMIŞ (hakemli) bir bulut-azaltım (cloud attenuation) modeli
    #: — burada uydurulmuş bir katsayı DEĞİL.
    @staticmethod
    def _kasten_czeplak_clear_sky_index(cloud_cover_pct: float) -> float:
        octas = max(0.0, min(100.0, cloud_cover_pct)) / 100.0 * 8.0
        return 1.0 - 0.75 * (octas / 8.0) ** 3.4

    @classmethod
    def compute_with_real_climate(
        cls,
        sun: SolarPosition,
        climate_sample: HourlyClimateSample,
        roof_tilt_deg: float = 0.0,
        roof_azimuth_deg: float = 180.0,
    ) -> IrradianceResult:
        """`compute()`'un clear-sky çıktısını, Open-Meteo'dan gelen GERÇEK
        bulutluluk (`climate_sample.cloud_cover_pct`) ile Kasten-Czeplak
        modeliyle ölçeklendirir. `cloud_cover_pct` yoksa (None), ölçeklendirme
        yapılmadan clear-sky sonucu döner (bunu gizlemez; çağıran
        `climate_sample.cloud_cover_pct is None` ile ayırt edebilir)."""
        clear_sky = cls.compute(sun, roof_tilt_deg, roof_azimuth_deg)
        if climate_sample.cloud_cover_pct is None:
            return clear_sky
        clear_sky_index = cls._kasten_czeplak_clear_sky_index(climate_sample.cloud_cover_pct)
        return IrradianceResult(
            watts_per_m2=clear_sky.watts_per_m2 * clear_sky_index,
            sun_elevation_deg=clear_sky.sun_elevation_deg,
            incidence_angle_deg=clear_sky.incidence_angle_deg,
        )

    @classmethod
    def daily_energy_kwh_per_m2_with_real_climate(
        cls,
        location: GeoPoint,
        date: datetime,
        roof_tilt_deg: float = 0.0,
        roof_azimuth_deg: float = 180.0,
        step_minutes: int = 60,
        climate_client: OpenMeteoClient | None = None,
    ) -> dict:
        """`daily_energy_kwh_per_m2`'nin GERÇEK bulutluluk verisiyle
        düzeltilmiş hali. `climate_data.OpenMeteoClient` üzerinden o gün
        için gerçek saatlik bulut örtüsü verisi çeker (bugünden >92 gün
        eskiyse otomatik olarak arşiv/ERA5 uç noktasına düşer) ve her saatin
        clear-sky ışınımını Kasten-Czeplak (1980) modeliyle ölçeklendirir.

        Ağa ulaşılamazsa (`ClimateNetworkError`) VEYA yanıt beklenen şemaya
        uymazsa (`ClimateParseError`) bu metod bunu YUKARI FIRLATIR —
        sessizce clear-sky'a düşmez (roadmap ilkesi: sessiz sahte-başarı
        yok). Clear-sky sonucunu ayrıca istiyorsanız `daily_energy_kwh_per_m2`'yi
        ayrıca çağırın.

        `step_minutes` varsayılan 60'tır (Open-Meteo saatlik veri verir;
        daha ince adımlarda ardışık saatler arasında en-yakın-saat
        enterpolasyonu YAPILMAZ — sabit tutulur, bu da dürüstçe
        `interpolation: "nearest_hour"` olarak sonuçta belirtilir).
        """
        client = climate_client or OpenMeteoClient()
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        today = datetime.now(timezone.utc).date()
        use_archive = (today - day_start.date()).days > 92

        samples = client.fetch_hourly(
            latitude=location.lat,
            longitude=location.lon,
            start_date=day_start.date(),
            end_date=day_start.date(),
            use_archive=use_archive,
        )
        by_hour: dict[int, HourlyClimateSample] = {}
        for s in samples:
            try:
                hour = datetime.fromisoformat(s.time_iso).hour
            except ValueError:
                continue
            by_hour[hour] = s
        if not by_hour:
            raise ClimateError(
                f"Open-Meteo {day_start.date().isoformat()} için hiç saatlik örnek döndürmedi "
                "(boş yanıt) — gerçek bulut verisi olmadan bu hesap yapılamaz."
            )

        total_wh_real = 0.0
        total_wh_clear = 0.0
        steps = (24 * 60) // step_minutes
        hours_per_step = step_minutes / 60.0
        missing_hours = 0
        for i in range(steps):
            when = day_start + timedelta(minutes=i * step_minutes)
            sun = SolarPositionCalculator.compute(location, when)
            clear = cls.compute(sun, roof_tilt_deg, roof_azimuth_deg)
            total_wh_clear += clear.watts_per_m2 * hours_per_step
            sample = by_hour.get(when.hour)
            if sample is None or sample.cloud_cover_pct is None:
                missing_hours += 1
                total_wh_real += clear.watts_per_m2 * hours_per_step
                continue
            index = cls._kasten_czeplak_clear_sky_index(sample.cloud_cover_pct)
            total_wh_real += clear.watts_per_m2 * index * hours_per_step

        return {
            "kwh_per_m2_real_climate": round(total_wh_real / 1000.0, 4),
            "kwh_per_m2_clear_sky": round(total_wh_clear / 1000.0, 4),
            "date": day_start.date().isoformat(),
            "used_archive_endpoint": use_archive,
            "interpolation": "nearest_hour",
            "hours_missing_cloud_data": missing_hours,
            "source": "open-meteo.com (cloud_cover_pct) + Kasten-Czeplak (1980) açık-gökyüzü indeksi",
        }


# ============================================================================ #
# Shadow Projection
# ============================================================================ #


class ShadowProjection:
    """Roadmap: 'Shadow Projection'. Bir bina ayak izini (footprint), belirli
    bir güneş yönü için zemine (z=ground_z) izdüşürerek gölge poligonunu
    üretir (basit dikdörtgen-olmayan poligonlar için de çalışan, extrusion
    + zemine projeksiyon yaklaşımı)."""

    @staticmethod
    def project_footprint(
        footprint: Polygon, building_height_m: float, sun: SolarPosition, ground_z: float = 0.0
    ) -> Polygon | None:
        if not sun.is_daylight:
            return None  # gece - gölge tanımsız (tam karanlık)

        dx, dy, dz = sun.direction_vector()
        if dz <= 1e-6:
            return None

        # Güneşten yere doğru: ışık yönünün tersi. Bina tepe noktası (x, y,
        # height) -> zemine kadar ışık izini takip ettiğinde düşen nokta.
        # Yükseklik farkı = building_height_m, yatay kayma = height * (dx,dy)/dz
        shift_x = building_height_m * (dx / dz)
        shift_y = building_height_m * (dy / dz)

        shadow_points = list(footprint.points)
        # Işığın geldiği yönün tersine (güneşe bakan taraftan uzağa) kayar.
        projected = [Point2D(p.x - shift_x, p.y - shift_y) for p in footprint.points]

        from ...core_engine.geometry_engine import GeometryEngine

        all_points = shadow_points + projected
        return GeometryEngine.convex_hull(all_points)
