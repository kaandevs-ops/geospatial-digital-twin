"""
Coordinate Systems
===================

Roadmap Phase 1 - "Coordinate Systems" bölümü.

Desteklenen sistemler:
    - WGS84            (lat/lon, EPSG:4326)
    - Web Mercator      (EPSG:3857) - tile engine'in native projeksiyonu
    - UTM               (zone bazlı, metre cinsinden düzlem koordinat)
    - Local Coordinate  (bir orijin noktasına göre metre cinsinden düzlem,
                          küçük ölçekli bina/sahne modellemesi için)

Sıfırdan yazılmıştır (pyproj/GDAL bağımlılığı yoktur), böylece harita modülü
hafif ve bağımsız kalır. Formüller WGS84 elipsoidine göre standart
Web Mercator / UTM (Transverse Mercator) dönüşümleridir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum, auto


class CoordinateSystem(Enum):
    """Desteklenen koordinat sistemleri."""

    WGS84 = auto()
    WEB_MERCATOR = auto()
    UTM = auto()
    LOCAL = auto()


# --- WGS84 elipsoid sabitleri ---
_WGS84_A = 6378137.0  # semi-major axis (m)
_WGS84_F = 1 / 298.257223563  # flattening
_WGS84_E2 = _WGS84_F * (2 - _WGS84_F)  # eccentricity squared
_K0 = 0.9996  # UTM scale factor


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """WGS84 coğrafi koordinat (derece cinsinden lat/lon), opsiyonel yükseklik (m)."""

    lat: float
    lon: float
    elevation: float = 0.0

    def __post_init__(self) -> None:
        if not (-90.0 <= self.lat <= 90.0):
            raise ValueError(f"Geçersiz enlem (lat): {self.lat}")
        if not (-180.0 <= self.lon <= 180.0):
            raise ValueError(f"Geçersiz boylam (lon): {self.lon}")


@dataclass(frozen=True, slots=True)
class ProjectedPoint:
    """Düzlemsel (projected) koordinat - metre veya piksel biriminde x/y."""

    x: float
    y: float
    system: CoordinateSystem
    zone: int | None = None  # yalnızca UTM için anlamlı
    elevation: float = 0.0


class CoordinateConverter:
    """
    Roadmap: "Coordinate Converter", "Projection Engine", "GPS Coordinate Engine".

    Tüm dönüşümler stateless static metotlardır; büyük veri setleri için
    vektörize edilebilir (numpy) şekilde de kolayca genişletilebilir.
    """

    # ------------------------------------------------------------------ #
    # WGS84 <-> Web Mercator
    # ------------------------------------------------------------------ #
    @staticmethod
    def wgs84_to_web_mercator(point: GeoPoint) -> ProjectedPoint:
        """EPSG:4326 -> EPSG:3857"""
        x = math.radians(point.lon) * _WGS84_A
        y = _WGS84_A * math.log(
            math.tan(math.pi / 4 + math.radians(point.lat) / 2)
        )
        return ProjectedPoint(x=x, y=y, system=CoordinateSystem.WEB_MERCATOR,
                               elevation=point.elevation)

    @staticmethod
    def web_mercator_to_wgs84(point: ProjectedPoint) -> GeoPoint:
        """EPSG:3857 -> EPSG:4326"""
        lon = math.degrees(point.x / _WGS84_A)
        lat = math.degrees(
            2 * math.atan(math.exp(point.y / _WGS84_A)) - math.pi / 2
        )
        return GeoPoint(lat=lat, lon=lon, elevation=point.elevation)

    # ------------------------------------------------------------------ #
    # WGS84 <-> UTM (Transverse Mercator, standart formülasyon)
    # ------------------------------------------------------------------ #
    @staticmethod
    def utm_zone_for(lon: float) -> int:
        """Boylamdan UTM dilimini (1-60) hesaplar."""
        return int((lon + 180) / 6) + 1

    @staticmethod
    def wgs84_to_utm(point: GeoPoint, zone: int | None = None) -> ProjectedPoint:
        zone = zone or CoordinateConverter.utm_zone_for(point.lon)
        lon0 = math.radians(-183 + zone * 6)
        lat = math.radians(point.lat)
        lon = math.radians(point.lon)

        e2 = _WGS84_E2
        ep2 = e2 / (1 - e2)
        n = _WGS84_A / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        t = math.tan(lat) ** 2
        c = ep2 * math.cos(lat) ** 2
        a = math.cos(lat) * (lon - lon0)

        m = _WGS84_A * (
            (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * lat
            - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * lat)
            + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * lat)
            - (35 * e2 ** 3 / 3072) * math.sin(6 * lat)
        )

        easting = _K0 * n * (
            a + (1 - t + c) * a ** 3 / 6
            + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * a ** 5 / 120
        ) + 500000.0

        northing = _K0 * (
            m + n * math.tan(lat) * (
                a ** 2 / 2
                + (5 - t + 9 * c + 4 * c ** 2) * a ** 4 / 24
                + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * a ** 6 / 720
            )
        )
        if point.lat < 0:
            northing += 10_000_000.0  # güney yarımküre offseti

        return ProjectedPoint(x=easting, y=northing, system=CoordinateSystem.UTM,
                               zone=zone, elevation=point.elevation)

    @staticmethod
    def utm_to_wgs84(point: ProjectedPoint, northern_hemisphere: bool = True) -> GeoPoint:
        if point.zone is None:
            raise ValueError("UTM->WGS84 dönüşümü için 'zone' zorunludur.")

        e2 = _WGS84_E2
        e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
        ep2 = e2 / (1 - e2)

        x = point.x - 500000.0
        y = point.y if northern_hemisphere else point.y - 10_000_000.0

        m = y / _K0
        mu = m / (_WGS84_A * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))

        phi1 = (
            mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        )

        n1 = _WGS84_A / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
        t1 = math.tan(phi1) ** 2
        c1 = ep2 * math.cos(phi1) ** 2
        r1 = _WGS84_A * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
        d = x / (n1 * _K0)

        lat = phi1 - (n1 * math.tan(phi1) / r1) * (
            d ** 2 / 2
            - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
            + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720
        )
        lon0 = math.radians(-183 + point.zone * 6)
        lon = lon0 + (
            d
            - (1 + 2 * t1 + c1) * d ** 3 / 6
            + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
        ) / math.cos(phi1)

        return GeoPoint(lat=math.degrees(lat), lon=math.degrees(lon),
                         elevation=point.elevation)

    # ------------------------------------------------------------------ #
    # Local coordinate system (küçük ölçek - bina/sahne modellemesi)
    # ------------------------------------------------------------------ #
    @staticmethod
    def wgs84_to_local(point: GeoPoint, origin: GeoPoint) -> ProjectedPoint:
        """
        Basit düzlemsel yaklaşıklama (equirectangular). Orijine göre metre
        cinsinden x (doğu), y (kuzey). Bina ölçeğinde (birkaç km) hata payı
        ihmal edilebilir düzeydedir.
        """
        lat0 = math.radians(origin.lat)
        dx = math.radians(point.lon - origin.lon) * _WGS84_A * math.cos(lat0)
        dy = math.radians(point.lat - origin.lat) * _WGS84_A
        return ProjectedPoint(x=dx, y=dy, system=CoordinateSystem.LOCAL,
                               elevation=point.elevation - origin.elevation)

    @staticmethod
    def local_to_wgs84(point: ProjectedPoint, origin: GeoPoint) -> GeoPoint:
        lat0 = math.radians(origin.lat)
        lat = origin.lat + math.degrees(point.y / _WGS84_A)
        lon = origin.lon + math.degrees(point.x / (_WGS84_A * math.cos(lat0)))
        return GeoPoint(lat=lat, lon=lon, elevation=origin.elevation + point.elevation)

    # ------------------------------------------------------------------ #
    # Yardımcı: iki GeoPoint arası haversine mesafe (m) - GPS Coordinate Engine
    # ------------------------------------------------------------------ #
    @staticmethod
    def haversine_distance(a: GeoPoint, b: GeoPoint) -> float:
        r = 6371000.0  # ortalama dünya yarıçapı (m)
        phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
        dphi = math.radians(b.lat - a.lat)
        dlambda = math.radians(b.lon - a.lon)
        h = (math.sin(dphi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
        return 2 * r * math.asin(math.sqrt(h))

    @staticmethod
    def bearing(a: GeoPoint, b: GeoPoint) -> float:
        """A'dan B'ye yön açısı (derece, 0-360, kuzeyden saat yönünde)."""
        phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
        dlambda = math.radians(b.lon - a.lon)
        x = math.sin(dlambda) * math.cos(phi2)
        y = (math.cos(phi1) * math.sin(phi2)
             - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda))
        return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0

    # ------------------------------------------------------------------ #
    # EPSG kayıt katmanı (Roadmap V2, A1 - "gerçek CRS dönüşüm katmanı")
    # ------------------------------------------------------------------ #
    #
    # Tam bir PROJ veritabanı değildir (bkz. ROADMAP_V2 Faz 17 - "Tam
    # PROJ-uyumlu CRS kütüphanesi entegrasyonu"). Bu katman, yukarıda
    # sıfırdan uygulanmış üç CRS ailesini (WGS84, Web Mercator, UTM kuzey/
    # güney 60 zon) EPSG kodlarıyla eşler ve aralarında genel bir
    # `transform_epsg()` sağlar. Kayıtlı olmayan bir EPSG kodu istenirse
    # sessizce yanlış dönüşüm yapmak yerine `ValueError` fırlatılır.

    EPSG_WGS84 = 4326
    EPSG_WEB_MERCATOR = 3857

    @staticmethod
    def _epsg_to_utm_zone(epsg: int) -> tuple[int, bool]:
        """EPSG -> (zone, northern_hemisphere). Bilinmiyorsa ValueError."""
        if 32601 <= epsg <= 32660:
            return epsg - 32600, True
        if 32701 <= epsg <= 32760:
            return epsg - 32700, False
        raise ValueError(f"EPSG:{epsg} bilinen bir UTM zonu değil (32601-32660 / 32701-32760)")

    @staticmethod
    def utm_zone_to_epsg(zone: int, northern_hemisphere: bool = True) -> int:
        """UTM zone/hemisphere -> EPSG kodu."""
        if not (1 <= zone <= 60):
            raise ValueError(f"Geçersiz UTM zone: {zone} (1-60 arası olmalı)")
        return (32600 if northern_hemisphere else 32700) + zone

    @staticmethod
    def is_known_epsg(epsg: int) -> bool:
        """Bu kayıt katmanının dönüşüm yapabildiği bir EPSG kodu mu?"""
        return (
            epsg == CoordinateConverter.EPSG_WGS84
            or epsg == CoordinateConverter.EPSG_WEB_MERCATOR
            or 32601 <= epsg <= 32660
            or 32701 <= epsg <= 32760
        )

    @staticmethod
    def epsg_description(epsg: int) -> str:
        """Kısa, insan-okunur EPSG açıklaması (bilinmiyorsa ValueError)."""
        if epsg == CoordinateConverter.EPSG_WGS84:
            return "WGS 84 - coğrafi (lat/lon, derece)"
        if epsg == CoordinateConverter.EPSG_WEB_MERCATOR:
            return "WGS 84 / Pseudo-Mercator (Web Mercator)"
        zone, north = CoordinateConverter._epsg_to_utm_zone(epsg)
        return f"WGS 84 / UTM zone {zone}{'N' if north else 'S'}"

    @staticmethod
    def to_wgs84_epsg(point: "ProjectedPoint | GeoPoint", source_epsg: int) -> GeoPoint:
        """Herhangi bir kayıtlı EPSG kaynağından WGS84'e (EPSG:4326) dönüşüm."""
        if source_epsg == CoordinateConverter.EPSG_WGS84:
            if not isinstance(point, GeoPoint):
                raise ValueError("EPSG:4326 kaynağı için GeoPoint bekleniyor")
            return point
        if source_epsg == CoordinateConverter.EPSG_WEB_MERCATOR:
            if not isinstance(point, ProjectedPoint):
                raise ValueError("EPSG:3857 kaynağı için ProjectedPoint bekleniyor")
            return CoordinateConverter.web_mercator_to_wgs84(point)
        zone, north = CoordinateConverter._epsg_to_utm_zone(source_epsg)
        if not isinstance(point, ProjectedPoint):
            raise ValueError(f"EPSG:{source_epsg} kaynağı için ProjectedPoint bekleniyor")
        if point.zone != zone:
            raise ValueError(
                f"ProjectedPoint.zone ({point.zone}) EPSG:{source_epsg} "
                f"(zone {zone}) ile uyuşmuyor"
            )
        return CoordinateConverter.utm_to_wgs84(point, northern_hemisphere=north)

    @staticmethod
    def from_wgs84_epsg(point: GeoPoint, target_epsg: int) -> "ProjectedPoint | GeoPoint":
        """WGS84'ten (EPSG:4326) herhangi bir kayıtlı hedef EPSG'ye dönüşüm."""
        if target_epsg == CoordinateConverter.EPSG_WGS84:
            return point
        if target_epsg == CoordinateConverter.EPSG_WEB_MERCATOR:
            return CoordinateConverter.wgs84_to_web_mercator(point)
        zone, north = CoordinateConverter._epsg_to_utm_zone(target_epsg)
        result = CoordinateConverter.wgs84_to_utm(point, zone=zone)
        if north != (point.lat >= 0):
            raise ValueError(
                f"Nokta EPSG:{target_epsg}'in yarımküresiyle uyuşmuyor "
                f"(lat={point.lat}, hedef {'kuzey' if north else 'güney'})"
            )
        return result

    @staticmethod
    def transform_epsg(
        point: "ProjectedPoint | GeoPoint", source_epsg: int, target_epsg: int
    ) -> "ProjectedPoint | GeoPoint":
        """Genel EPSG->EPSG dönüşümü (WGS84 kayıtlı EPSG ailesi <-> Web
        Mercator <-> UTM). Her zaman WGS84 ara adımından geçer; bilinmeyen
        bir EPSG kodu ile karşılaşılırsa `ValueError` fırlatılır (sessiz/
        yanlış dönüşüm üretilmez). Tam jeodezik grid-shift/datum dönüşümleri
        (PROJ-uyumlu) kapsam dışıdır - bkz. ROADMAP_V2 Faz 17."""
        if source_epsg == target_epsg:
            return point
        wgs = CoordinateConverter.to_wgs84_epsg(point, source_epsg)
        return CoordinateConverter.from_wgs84_epsg(wgs, target_epsg)
