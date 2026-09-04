"""
harita_modelleme/phase1_core_engine/coordinate_systems.py
============================================================
FAZ 1 — Coordinate Systems

Roadmap kapsamı:
    - WGS84
    - UTM
    - Web Mercator
    - Local Coordinate System

Harici bağımlılık yok (pyproj kullanılmıyor). Tüm dönüşümler klasik
jeodezik formüllerle (Snyder, "Map Projections — A Working Manual", USGS
1395) sıfırdan uygulanmıştır.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

# ============================================================================
# SABİTLER (WGS84 elipsoidi)
# ============================================================================

WGS84_A = 6378137.0                 # semi-major axis (m)
WGS84_F = 1 / 298.257223563         # flattening
WGS84_B = WGS84_A * (1 - WGS84_F)   # semi-minor axis (m)
WGS84_E2 = WGS84_F * (2 - WGS84_F)  # first eccentricity squared
WGS84_E = math.sqrt(WGS84_E2)

EARTH_RADIUS_MEAN_M = 6371008.8     # ortalama yarıçap (haversine için)

K0_UTM = 0.9996                     # UTM ölçek faktörü


class CoordinateSystemError(ValueError):
    """Koordinat dönüşümlerinde geçersiz girdi/aralık hatası."""


# ============================================================================
# WGS84 — coğrafi koordinat (lon/lat/alt)
# ============================================================================

@dataclass(frozen=True)
class WGS84:
    """WGS84 coğrafi koordinat (derece cinsinden lon/lat)."""

    lon: float
    lat: float
    alt: float = 0.0

    def __post_init__(self) -> None:
        if not (-180.0 <= self.lon <= 180.0):
            raise CoordinateSystemError(f"lon [-180,180] dışında: {self.lon}")
        if not (-90.0 <= self.lat <= 90.0):
            raise CoordinateSystemError(f"lat [-90,90] dışında: {self.lat}")

    def as_radians(self) -> Tuple[float, float]:
        return math.radians(self.lon), math.radians(self.lat)


def haversine_distance_m(a: WGS84, b: WGS84) -> float:
    """İki WGS84 nokta arası büyük daire (great-circle) mesafesi (metre)."""
    lon1, lat1 = a.as_radians()
    lon2, lat2 = b.as_radians()
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MEAN_M * math.asin(min(1.0, math.sqrt(h)))


def initial_bearing_deg(a: WGS84, b: WGS84) -> float:
    """A'dan B'ye başlangıç yön açısı (0-360, kuzeyden saat yönünde)."""
    lon1, lat1 = a.as_radians()
    lon2, lat2 = b.as_radians()
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360.0) % 360.0


# ============================================================================
# WEB MERCATOR (EPSG:3857) <-> WGS84
# ============================================================================

@dataclass(frozen=True)
class WebMercatorCoordinate:
    x: float
    y: float


def wgs84_to_web_mercator(coord: WGS84) -> WebMercatorCoordinate:
    """WGS84 -> EPSG:3857 (Web Mercator, metre)."""
    if not (-85.05112878 <= coord.lat <= 85.05112878):
        raise CoordinateSystemError(
            "Web Mercator lat aralığı [-85.0511, 85.0511] ile sınırlıdır: "
            f"{coord.lat}"
        )
    lon_rad, lat_rad = coord.as_radians()
    x = WGS84_A * lon_rad
    y = WGS84_A * math.log(math.tan(math.pi / 4 + lat_rad / 2))
    return WebMercatorCoordinate(x=x, y=y)


def web_mercator_to_wgs84(coord: WebMercatorCoordinate) -> WGS84:
    """EPSG:3857 -> WGS84."""
    lon = math.degrees(coord.x / WGS84_A)
    lat = math.degrees(2 * math.atan(math.exp(coord.y / WGS84_A)) - math.pi / 2)
    return WGS84(lon=lon, lat=lat)


# ============================================================================
# UTM <-> WGS84  (Transverse Mercator, Snyder serisi açılım)
# ============================================================================

@dataclass(frozen=True)
class UTMCoordinate:
    easting: float
    northing: float
    zone: int
    hemisphere: str  # "N" veya "S"


def _utm_zone_from_lon(lon: float) -> int:
    return int(math.floor((lon + 180.0) / 6.0)) + 1


def wgs84_to_utm(coord: WGS84, zone: int | None = None) -> UTMCoordinate:
    """WGS84 -> UTM. `zone` verilmezse otomatik hesaplanır."""
    lat = coord.lat
    lon = coord.lon
    if zone is None:
        zone = _utm_zone_from_lon(lon)

    lon0 = math.radians((zone - 1) * 6 - 180 + 3)  # zone central meridian
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)

    e2 = WGS84_E2
    ep2 = e2 / (1 - e2)
    N = WGS84_A / math.sqrt(1 - e2 * math.sin(lat_rad) ** 2)
    T = math.tan(lat_rad) ** 2
    C = ep2 * math.cos(lat_rad) ** 2
    A = math.cos(lat_rad) * (lon_rad - lon0)

    M = WGS84_A * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * lat_rad)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * lat_rad)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * lat_rad)
    )

    easting = (
        K0_UTM
        * N
        * (
            A
            + (1 - T + C) * A ** 3 / 6
            + (5 - 18 * T + T ** 2 + 72 * C - 58 * ep2) * A ** 5 / 120
        )
        + 500000.0
    )

    northing = K0_UTM * (
        M
        + N
        * math.tan(lat_rad)
        * (
            A ** 2 / 2
            + (5 - T + 9 * C + 4 * C ** 2) * A ** 4 / 24
            + (61 - 58 * T + T ** 2 + 600 * C - 330 * ep2) * A ** 6 / 720
        )
    )

    hemisphere = "N" if lat >= 0 else "S"
    if hemisphere == "S":
        northing += 10000000.0

    return UTMCoordinate(easting=easting, northing=northing, zone=zone, hemisphere=hemisphere)


def utm_to_wgs84(coord: UTMCoordinate) -> WGS84:
    """UTM -> WGS84."""
    e2 = WGS84_E2
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))

    x = coord.easting - 500000.0
    y = coord.northing
    if coord.hemisphere == "S":
        y -= 10000000.0

    M = y / K0_UTM
    mu = M / (
        WGS84_A
        * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256)
    )

    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )

    N1 = WGS84_A / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    T1 = math.tan(phi1) ** 2
    C1 = ep2 * math.cos(phi1) ** 2
    R1 = WGS84_A * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    D = x / (N1 * K0_UTM)

    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
        D ** 2 / 2
        - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720
    )

    lon0 = math.radians((coord.zone - 1) * 6 - 180 + 3)
    lon = lon0 + (
        D
        - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120
    ) / math.cos(phi1)

    return WGS84(lon=math.degrees(lon), lat=math.degrees(lat))


# ============================================================================
# LOCAL COORDINATE SYSTEM
# ============================================================================

@dataclass
class LocalCoordinateSystem:
    """
    Bir referans noktasına (origin) göre yerel düzlem koordinat sistemi.
    Küçük alanlar (tek bina / tek parsel) için equirectangular yaklaşımla
    lon/lat'ı metre cinsine indirger (Web Mercator distorsiyonundan kaçınmak
    için özellikle bina ölçeğinde tercih edilir).
    """

    origin: WGS84

    def __post_init__(self) -> None:
        self._lat0_rad = math.radians(self.origin.lat)
        self._m_per_deg_lat = (
            111132.92
            - 559.82 * math.cos(2 * self._lat0_rad)
            + 1.175 * math.cos(4 * self._lat0_rad)
            - 0.0023 * math.cos(6 * self._lat0_rad)
        )
        self._m_per_deg_lon = (
            111412.84 * math.cos(self._lat0_rad)
            - 93.5 * math.cos(3 * self._lat0_rad)
            + 0.118 * math.cos(5 * self._lat0_rad)
        )

    def to_local(self, coord: WGS84) -> Tuple[float, float, float]:
        """WGS84 -> yerel (x=doğu, y=kuzey, z=yükseklik) metre."""
        x = (coord.lon - self.origin.lon) * self._m_per_deg_lon
        y = (coord.lat - self.origin.lat) * self._m_per_deg_lat
        z = coord.alt - self.origin.alt
        return x, y, z

    def to_wgs84(self, x: float, y: float, z: float = 0.0) -> WGS84:
        """Yerel (x=doğu, y=kuzey, z=yükseklik) -> WGS84."""
        lon = self.origin.lon + x / self._m_per_deg_lon
        lat = self.origin.lat + y / self._m_per_deg_lat
        alt = self.origin.alt + z
        return WGS84(lon=lon, lat=lat, alt=alt)


# ============================================================================
# EPSG KAYIT KATMANI (A1 — genel CRS dönüşüm katmanı)
# ============================================================================
#
# Tam bir PROJ veritabanı değildir (bkz. ROADMAP_V2 Faz 17: "Tam PROJ-uyumlu
# CRS kütüphanesi"). Bu kayıt, üç bağımlılıksız-uygulanmış CRS ailesini
# (WGS84 coğrafi, Web Mercator, UTM kuzey/güney 60 zon) EPSG kodlarıyla
# eşler ve aralarında genel bir `transform()` fonksiyonu sağlar. Kayıtlı
# olmayan bir EPSG kodu istenirse (örn. yerel bir ulusal projeksiyon)
# `CoordinateSystemError` fırlatılır — sessizce yanlış/uydurma dönüşüm
# yapılmaz.

EPSG_WGS84 = 4326
EPSG_WEB_MERCATOR = 3857


def _epsg_utm_zone(zone: int, hemisphere: str) -> int:
    """UTM zone/hemisphere -> EPSG kodu (32601-32660 kuzey, 32701-32760 güney)."""
    base = 32600 if hemisphere == "N" else 32700
    return base + zone


def _utm_zone_from_epsg(epsg: int) -> Tuple[int, str]:
    if 32601 <= epsg <= 32660:
        return epsg - 32600, "N"
    if 32701 <= epsg <= 32760:
        return epsg - 32700, "S"
    raise CoordinateSystemError(f"EPSG:{epsg} bilinen bir UTM zonu değil")


def is_known_epsg(epsg: int) -> bool:
    """Bu kayıt katmanının dönüşüm yapabildiği bir EPSG kodu mu?"""
    return (
        epsg == EPSG_WGS84
        or epsg == EPSG_WEB_MERCATOR
        or 32601 <= epsg <= 32660
        or 32701 <= epsg <= 32760
    )


def epsg_description(epsg: int) -> str:
    """Kısa, insan-okunur EPSG açıklaması (bilinmiyorsa hata fırlatır)."""
    if epsg == EPSG_WGS84:
        return "WGS 84 — coğrafi (lon/lat, derece)"
    if epsg == EPSG_WEB_MERCATOR:
        return "WGS 84 / Pseudo-Mercator (Web Mercator)"
    if 32601 <= epsg <= 32660 or 32701 <= epsg <= 32760:
        zone, hemi = _utm_zone_from_epsg(epsg)
        return f"WGS 84 / UTM zone {zone}{hemi}"
    raise CoordinateSystemError(f"EPSG:{epsg} kayıt katmanında yok")


def _to_wgs84_generic(coord, epsg: int) -> WGS84:
    if epsg == EPSG_WGS84:
        if not isinstance(coord, WGS84):
            raise CoordinateSystemError("EPSG:4326 için WGS84 nesnesi bekleniyor")
        return coord
    if epsg == EPSG_WEB_MERCATOR:
        if not isinstance(coord, WebMercatorCoordinate):
            raise CoordinateSystemError(
                "EPSG:3857 için WebMercatorCoordinate nesnesi bekleniyor"
            )
        return web_mercator_to_wgs84(coord)
    if is_known_epsg(epsg):
        zone, hemi = _utm_zone_from_epsg(epsg)
        if not isinstance(coord, UTMCoordinate):
            raise CoordinateSystemError("UTM EPSG kodu için UTMCoordinate nesnesi bekleniyor")
        if coord.zone != zone or coord.hemisphere != hemi:
            raise CoordinateSystemError(
                f"UTMCoordinate zone/hemisphere ({coord.zone}{coord.hemisphere}) "
                f"EPSG:{epsg} ile uyuşmuyor"
            )
        return utm_to_wgs84(coord)
    raise CoordinateSystemError(f"EPSG:{epsg} kayıt katmanında yok")


def _from_wgs84_generic(wgs: WGS84, epsg: int):
    if epsg == EPSG_WGS84:
        return wgs
    if epsg == EPSG_WEB_MERCATOR:
        return wgs84_to_web_mercator(wgs)
    if is_known_epsg(epsg):
        zone, hemi = _utm_zone_from_epsg(epsg)
        result = wgs84_to_utm(wgs, zone=zone)
        return result
    raise CoordinateSystemError(f"EPSG:{epsg} kayıt katmanında yok")


def transform(coord, source_epsg: int, target_epsg: int):
    """Genel EPSG->EPSG dönüşümü. `coord`, `source_epsg`'e uygun tipte
    olmalıdır (WGS84 / WebMercatorCoordinate / UTMCoordinate). Her zaman
    WGS84 ara adımından geçer (roadmap kapsamındaki 3 aile için yeterli
    hassasiyet; tam jeodezik grid-shift dönüşümleri Faz 17 kapsamındadır).
    """
    if source_epsg == target_epsg:
        return coord
    wgs = _to_wgs84_generic(coord, source_epsg)
    return _from_wgs84_generic(wgs, target_epsg)
