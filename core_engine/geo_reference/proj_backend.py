"""
PROJ Backend — Opsiyonel Gerçek Datum Dönüşümü
=================================================

ROADMAP_V2 Faz 17'nin son kalan maddesi: "Tam PROJ-uyumlu CRS dönüşüm
kütüphanesi entegrasyonu".

`core_engine.coordinate_systems.CoordinateConverter` stdlib-only, sıfırdan
yazılmış WGS84<->UTM/Web Mercator formülleri sağlar (bkz. o modülün
docstring'i). Bunlar **tek bir datum (WGS84) içinde** doğru dönüşümlerdir,
ama gerçek dünyada farklı ülkeler farklı datum'lar kullanır (örn. ED50,
NAD27, yerel grid'ler) ve bu datum'lar arasında geçiş 7-parametreli Helmert
dönüşümü veya NTv2/grid-shift dosyaları gerektirir — bunlar stdlib ile
pratik olarak uygulanamaz, resmi PROJ veritabanı gerekir.

Bu modül, projenin ana ilkesini (mevcut koda dokunmadan, opsiyonel bağımlılık
olarak üstüne inşa) koruyarak `pyproj` (PROJ'un Python sarmalayıcısı) varsa
gerçek, üretim-kalitesinde datum dönüşümü sağlar; yoksa **sessizce yanlış
sonuç üretmek yerine** açık `ProjBackendUnavailable` fırlatır — çağıran kod
isterse stdlib `CoordinateConverter`'a bilinçli olarak düşebilir.

Tasarım kararları
------------------
- `pyproj` yoksa import zamanında patlamaz (`is_available()` ile kontrol
  edilir) — mevcut 684 testin hiçbiri bu modüle bağımlı değil, dolayısıyla
  `pyproj` kurulu olmayan bir ortamda da tüm paket sorunsuz çalışmaya
  devam eder.
- API, EPSG kodlarıyla çalışır (`CoordinateConverter.transform_epsg()` ile
  aynı imza felsefesi) — çağıran kod stdlib/pyproj arasında kolayca geçiş
  yapabilir.
- Sonuçlar `always_xy=True` ile üretilir (lon,lat sırası — GIS endüstri
  standardı), stdlib tarafındaki `GeoPoint(lat, lon)` sırasına köprü
  fonksiyonlarında açıkça çevrilir.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..coordinate_systems import GeoPoint

try:
    import pyproj  # type: ignore[import-untyped]

    _PYPROJ_AVAILABLE = True
    _PYPROJ_VERSION = getattr(pyproj, "__proj_version__", None) or pyproj.__version__
except ImportError:  # pragma: no cover - ortam bağımlı
    pyproj = None  # type: ignore[assignment]
    _PYPROJ_AVAILABLE = False
    _PYPROJ_VERSION = None


class ProjBackendUnavailable(RuntimeError):
    """`pyproj` kurulu değilken gerçek datum dönüşümü istendiğinde fırlatılır."""


def is_available() -> bool:
    """`pyproj` bu ortamda kurulu mu?"""
    return _PYPROJ_AVAILABLE


def proj_version() -> str | None:
    """Kurulu PROJ/pyproj sürümü, yoksa None."""
    return _PYPROJ_VERSION


@dataclass(frozen=True, slots=True)
class DatumTransformResult:
    """Gerçek PROJ pipeline'ından dönen sonuç + tanılama bilgisi."""

    lat: float
    lon: float
    elevation: float
    source_epsg: int
    target_epsg: int
    # PROJ'un seçtiği pipeline'ın "accuracy" tahmini (metre, bilinmiyorsa None).
    # `Transformer.transform()` çağrısı başına değişebilir; en doğru pipeline
    # PROJ tarafından otomatik seçilir (area-of-use'a göre).
    accuracy_m: float | None


def _require_pyproj() -> None:
    if not _PYPROJ_AVAILABLE:
        raise ProjBackendUnavailable(
            "pyproj kurulu değil. Gerçek PROJ-uyumlu datum dönüşümü için "
            "`pip install harita-modelleme[geo]` (veya doğrudan `pip install "
            "pyproj>=3.0`) gerekir. Alternatif: "
            "`core_engine.coordinate_systems.CoordinateConverter` tek-datum "
            "(WGS84) dönüşümlerine bilinçli olarak düşün."
        )


def transform_datum(
    point: GeoPoint,
    source_epsg: int,
    target_epsg: int,
) -> DatumTransformResult:
    """
    Gerçek PROJ pipeline'ı ile `source_epsg` datumundaki bir noktayı
    `target_epsg` datumuna dönüştürür (örn. ED50 (EPSG:4230) -> WGS84
    (EPSG:4326)). Grid-shift/NTv2 dosyaları varsa PROJ otomatik kullanır.

    `point.lat`/`point.lon`, `source_epsg` CRS'i içindeki koordinatlar
    olarak yorumlanır (yalnızca WGS84 kaynak için `GeoPoint`'in kendi
    doğrulaması -90..90/-180..180 anlamlı olur; farklı datum'larda da
    aynı aralık pratikte geçerlidir çünkü hepsi coğrafi (enlem/boylam)
    CRS'lerdir — projected/metre-tabanlı kaynak CRS'ler için bu fonksiyon
    uygun değildir, `transform_projected_to_wgs84` kullanın).

    Raises:
        ProjBackendUnavailable: `pyproj` kurulu değilse.
    """
    _require_pyproj()
    transformer = pyproj.Transformer.from_crs(
        f"EPSG:{source_epsg}", f"EPSG:{target_epsg}", always_xy=True
    )
    lon2, lat2 = transformer.transform(point.lon, point.lat)
    accuracy = None
    try:
        # pyproj >= 2.x: transformer.transformer.accuracy (dahili, garantisiz)
        pipeline = pyproj.Transformer.from_crs(
            f"EPSG:{source_epsg}",
            f"EPSG:{target_epsg}",
            always_xy=True,
            accuracy=None,
        )
        accuracy = getattr(pipeline, "accuracy", None)
    except Exception:  # pragma: no cover - tanılama, kritik değil
        accuracy = None
    return DatumTransformResult(
        lat=lat2,
        lon=lon2,
        elevation=point.elevation,
        source_epsg=source_epsg,
        target_epsg=target_epsg,
        accuracy_m=accuracy,
    )


def round_trip_error_m(point: GeoPoint, via_epsg: int, wgs84_epsg: int = 4326) -> float:
    """
    `point` (WGS84) -> `via_epsg` -> geri WGS84 round-trip'inin haversine
    hatasını metre cinsinden döndürür. `CoordinateConverter` tarafındaki
    stdlib round-trip testleriyle aynı doğrulama mantığı, ama gerçek PROJ
    pipeline'ı üzerinden.
    """
    _require_pyproj()
    from ..coordinate_systems import CoordinateConverter

    forward = transform_datum(point, wgs84_epsg, via_epsg)
    back_point = (
        GeoPoint(lat=forward.lat, lon=forward.lon, elevation=point.elevation)
        if via_epsg == wgs84_epsg
        else None
    )
    if back_point is None:
        # via_epsg'den geri WGS84'e
        intermediate = pyproj.Transformer.from_crs(
            f"EPSG:{wgs84_epsg}", f"EPSG:{via_epsg}", always_xy=True
        )
        lon_v, lat_v = intermediate.transform(point.lon, point.lat)
        back = pyproj.Transformer.from_crs(f"EPSG:{via_epsg}", f"EPSG:{wgs84_epsg}", always_xy=True)
        lon_b, lat_b = back.transform(lon_v, lat_v)
        back_point = GeoPoint(lat=lat_b, lon=lon_b)
    return CoordinateConverter.haversine_distance(point, back_point)


__all__ = [
    "ProjBackendUnavailable",
    "DatumTransformResult",
    "is_available",
    "proj_version",
    "transform_datum",
    "round_trip_error_m",
]
