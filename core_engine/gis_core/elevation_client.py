"""
Gerçek Yükseklik/Arazi Verisi (Open-Elevation / SRTM) — yeni_roadmap.md Faz 2.4
================================================================================

"Yükseklik/arazi verisi: Copernicus DEM veya SRTM (30m çözünürlük,
ücretsiz) — arazi eğimi, zemin kotu."

Bu modül `core_engine.gis_core.osm_client` ile aynı dürüst desende yazıldı:

* Open-Elevation (https://api.open-elevation.com) — ücretsiz, key
  gerektirmeyen, SRTM/ASTER GDEM tabanlı açık kaynak yükseklik API'si.
  Toplu (`POST /api/v1/lookup`, çoklu nokta) sorgu destekler — bir bbox'ı
  düzenli bir ızgaraya bölüp tek istekte tüm noktaların yüksekliğini
  çekebiliriz (rate-limit'e karşı tek istek = daha az risk).
* **Dürüst kısıtlama**: bu sandbox'ın ağ erişimi `api.open-elevation.com`'a
  izin vermiyor (yalnızca paket kayıt defterleri açık) — bu istemci gerçek
  ağda **hiç doğrulanmadı**. Kod, Open-Elevation'ın belgelenmiş genel API
  şemasına göre yazıldı; testler `urlopen` mock'layarak şema-parse +
  `HeightmapGrid` dönüşümünü doğruluyor.
* Ağ başarısızsa (veya bu ortamdaki gibi engelliyse) `ElevationClient`
  açık bir `ElevationNetworkError` fırlatır — `terrain_engine.DEMImporter`
  tarafındaki mevcut `synthetic_hills`/`flat_terrain` fallback'lerine
  sessizce düşmek çağıranın sorumluluğunda (bkz. `fetch_terrain_or_flat`
  yardımcı fonksiyonu — sessiz fallback isteyen çağıranlar için).
* stdlib-only (`urllib.request`, `json`), harici bağımlılık yok.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from ...terrain_engine import HeightmapGrid
from ..coordinate_systems import GeoPoint

DEFAULT_ELEVATION_ENDPOINT = "https://api.open-elevation.com/api/v1/lookup"
DEFAULT_USER_AGENT = "harita-modelleme-platformu/0.17 (roadmap-2.4-elevation)"


class ElevationError(RuntimeError):
    """Yükseklik verisiyle ilgili genel hata."""


class ElevationNetworkError(ElevationError):
    """Open-Elevation'a ulaşılamadı (ağ/HTTP/zaman aşımı hatası)."""


@dataclass(slots=True)
class ElevationSample:
    lat: float
    lon: float
    elevation_m: float


class ElevationClient:
    """Open-Elevation genel REST API'sine (SRTM/ASTER GDEM tabanlı) ince
    bir HTTP istemcisi. Tek istekte çoklu nokta sorgusu destekler."""

    def __init__(self, endpoint: str = DEFAULT_ELEVATION_ENDPOINT, timeout: float = 15.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout

    def lookup(self, points: list[tuple[float, float]]) -> list[ElevationSample]:
        """`points`: `[(lat, lon), ...]` -> her nokta için `ElevationSample`.

        Open-Elevation şeması: `POST {"locations": [{"latitude": ..,
        "longitude": ..}, ...]}` -> `{"results": [{"latitude": ..,
        "longitude": .., "elevation": ..}, ...]}`.
        """
        if not points:
            return []
        payload = json.dumps(
            {"locations": [{"latitude": lat, "longitude": lon} for lat, lon in points]}
        ).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": DEFAULT_USER_AGENT},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ElevationNetworkError(f"Open-Elevation isteği başarısız: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ElevationError(f"Open-Elevation yanıtı parse edilemedi: {exc}") from exc

        results = data.get("results", [])
        samples: list[ElevationSample] = []
        for item in results:
            try:
                samples.append(
                    ElevationSample(
                        lat=float(item["latitude"]),
                        lon=float(item["longitude"]),
                        elevation_m=float(item["elevation"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ElevationError(
                    f"Open-Elevation sonuç kaydı beklenmedik biçimde: {item}"
                ) from exc
        return samples


def fetch_heightmap_grid(
    client: ElevationClient,
    south: float,
    west: float,
    north: float,
    east: float,
    grid_size: int = 16,
    resolution_hint_m: float = 30.0,
) -> HeightmapGrid:
    """Verilen bbox'ı `grid_size x grid_size` düzenli ızgaraya bölüp
    Open-Elevation'dan gerçek (SRTM tabanlı) yükseklikleri çeker ve
    mevcut `terrain_engine.HeightmapGrid`'e (roadmap Faz 1 terrain
    pipeline'ıyla doğrudan uyumlu) dönüştürür.

    `resolution_hint_m`: SRTM'in yerel çözünürlüğü (~30m); ızgara
    hücreleri arası gerçek mesafe bundan bağımsız (bbox/grid_size'a
    göre hesaplanır) - bu yalnızca `HeightmapGrid.resolution_m` alanına
    yazılan bir etiket/varsayım, roadmap 2.4'ün "30m çözünürlük" notuyla
    tutarlılık için.
    """
    if grid_size < 2:
        raise ValueError("grid_size en az 2 olmalı (kenar noktaları için)")

    lat_step = (north - south) / (grid_size - 1)
    lon_step = (east - west) / (grid_size - 1)
    points: list[tuple[float, float]] = []
    for row in range(grid_size):
        lat = south + row * lat_step
        for col in range(grid_size):
            lon = west + col * lon_step
            points.append((lat, lon))

    samples = client.lookup(points)
    if len(samples) != grid_size * grid_size:
        raise ElevationError(
            f"Open-Elevation beklenen {grid_size * grid_size} nokta yerine "
            f"{len(samples)} sonuç döndürdü"
        )

    matrix: list[list[float]] = []
    for row in range(grid_size):
        row_values = [samples[row * grid_size + col].elevation_m for col in range(grid_size)]
        matrix.append(row_values)

    # Izgaranın metre cinsinden hücre boyutu (yaklaşık, ekvator-yakını
    # düzeltmesiz basit derece->metre dönüşümü - roadmap Faz 2.4'ün
    # "arazi eğimi" analizleri için yeterli hassasiyette).
    avg_lat_rad = ((south + north) / 2.0) * 3.141592653589793 / 180.0
    meters_per_deg_lat = 111_320.0
    meters_per_deg_lon = 111_320.0 * max(0.01, abs(__import__("math").cos(avg_lat_rad)))
    cell_size_m = min(abs(lat_step) * meters_per_deg_lat, abs(lon_step) * meters_per_deg_lon)
    if cell_size_m <= 0:
        cell_size_m = resolution_hint_m

    origin = GeoPoint(lat=south, lon=west, elevation=matrix[0][0])
    return HeightmapGrid(
        width=grid_size,
        height=grid_size,
        resolution_m=cell_size_m,
        elevations=matrix,
        origin=origin,
    )


def fetch_terrain_or_flat(
    client: ElevationClient,
    south: float,
    west: float,
    north: float,
    east: float,
    grid_size: int = 16,
    flat_elevation: float = 0.0,
) -> tuple[HeightmapGrid, str]:
    """`fetch_heightmap_grid`'i dener; ağ/parse hatasında **sessizce**
    düz (flat) araziye düşer - offline/sandbox senaryoların hiçbir zaman
    kırılmaması için (`external_library.PBRMaterialLibrary` ile aynı
    fallback ilkesi). `(HeightmapGrid, source)` döner, `source` ∈
    {"open-elevation", "flat-fallback"}.
    """
    try:
        grid = fetch_heightmap_grid(client, south, west, north, east, grid_size=grid_size)
        return grid, "open-elevation"
    except ElevationError:
        from ...terrain_engine import DEMImporter

        origin = GeoPoint(lat=south, lon=west, elevation=flat_elevation)
        grid = DEMImporter.flat_terrain(
            width=grid_size,
            height=grid_size,
            resolution_m=30.0,
            elevation=flat_elevation,
            origin=origin,
        )
        return grid, "flat-fallback"
