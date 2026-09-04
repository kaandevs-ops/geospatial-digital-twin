"""
offline_cache - Faz C5 (offline mod, A4)
================================================================================

ROADMAP_V7.md Bölüm A4: "Offline mod (ayrı bir 'çevrimdışı' anahtar/toggle
olarak)":

    "Tile önbellekleme: kullanıcı bir bölgeyi 'offline için indir' dediğinde,
    seçili bbox + zoom aralığı için tile'lar indirilip yerel bir klasöre/DB'ye
    (ör. MBTiles formatı veya basit `tiles/{z}/{x}/{y}.png` dosya yapısı)
    kaydedilir."
    "Offline modda Leaflet, uzak tile URL'i yerine yerel bir HTTP
    endpoint'ten (`app_shell` sunucusu üzerinden servis edilen) tile'ları
    çeker."

Bu modül **stdlib-only** (`urllib.request`, `json`, `pathlib`) bir tile
önbellek yöneticisi sağlar: standart "slippy map" tile matematiği
(`deg2tile`/`tile2deg`, OSM Wiki'nin referans formülü) + bir bbox/zoom
aralığı için gereken tüm `(z, x, y)` tile anahtarlarının hesaplanması +
bunların basit `tiles/{z}/{x}/{y}.<ext>` dosya yapısında diske
kaydedilmesi/okunması. A4'ün önerdiği iki seçenekten (MBTiles vs. düz
dosya yapısı) **düz dosya yapısı** seçildi: stdlib'de MBTiles (SQLite
tabanlı) için hazır bir yazıcı yoktur ve düz dosya yapısı hem daha basit
hem de `app_shell/server.py`'nin zaten sahip olduğu statik dosya servis
mekanizmasıyla (path-traversal korumalı, bkz. `server.py._serve_static`)
doğrudan uyumludur.

`TileDownloader.fetch` gerçek bir HTTP GET yapar (`urllib.request` ile,
harici bağımlılık yok) - testlerde bu, roadmap'in "ağ gerektirmeyen test"
ilkesiyle tutarlı olarak sahte (fake) bir `opener` ile değiştirilir
(bkz. `tests/test_c5_offline_tile_cache.py`).
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

#: A4'ün "kaba" politeness sınırı: tek bir indirme çağrısında en fazla bu
#: kadar tile istenir (kullanıcı yanlışlıkla dünya çapında bir bbox +
#: yüksek zoom seçerse sunucuları/ağı boğmayı önler).
DEFAULT_MAX_TILES_PER_DOWNLOAD = 4000

#: OSM kullanım politikasının istediği tanınabilir User-Agent (A4'ün OSM
#: ODbL/atıf disiplinine, bkz. B6, paralel bir "iyi vatandaşlık" kuralı).
DEFAULT_USER_AGENT = "harita-offline-cache/1.0 (+https://example.invalid/harita)"

TileKey = tuple[int, int, int]  # (z, x, y)


# ============================================================================ #
# Slippy-map tile matematiği (OSM Wiki referans formülü)
# ============================================================================ #


def deg2tile(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    """Enlem/boylam (WGS84) -> `(x, y)` tile indeksi (belirli bir `zoom`
    seviyesinde). Standart Web Mercator slippy-map formülü."""
    lat_rad = math.radians(lat_deg)
    n = 2.0**zoom
    x = int((lon_deg + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    x = max(0, min(int(n) - 1, x))
    y = max(0, min(int(n) - 1, y))
    return x, y


def tile2deg(x: int, y: int, zoom: int) -> tuple[float, float]:
    """`(x, y, zoom)` tile indeksi -> tile'ın kuzeybatı köşesinin
    enlem/boylamı (ters formül, `deg2tile`'ın tersine eşleneni)."""
    n = 2.0**zoom
    lon_deg = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def tiles_for_bbox(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    zoom: int,
) -> list[TileKey]:
    """Verilen bbox'ı (WGS84, [min_lat, min_lon, max_lat, max_lon]) belirli
    bir `zoom` seviyesinde kaplayan tüm `(z, x, y)` tile anahtarlarını
    döner (kuzey-batı/güney-doğu köşe tile'ları dahil, aralarındaki tüm
    tile'lar da eklenir)."""
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon
    x_min, y_min = deg2tile(max_lat, min_lon, zoom)  # kuzey-batı
    x_max, y_max = deg2tile(min_lat, max_lon, zoom)  # güney-doğu
    if x_min > x_max:
        x_min, x_max = x_max, x_min
    if y_min > y_max:
        y_min, y_max = y_max, y_min
    return [(zoom, x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]


def tiles_for_bbox_zoom_range(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    zoom_min: int,
    zoom_max: int,
) -> list[TileKey]:
    """`tiles_for_bbox`'un birden çok zoom seviyesi için birleşimi - A4'ün
    "seçili bbox + zoom aralığı" ifadesine karşılık gelir."""
    if zoom_min > zoom_max:
        zoom_min, zoom_max = zoom_max, zoom_min
    result: list[TileKey] = []
    for z in range(zoom_min, zoom_max + 1):
        result.extend(tiles_for_bbox(min_lat, min_lon, max_lat, max_lon, z))
    return result


# ============================================================================ #
# Diskteki tile depolama - basit `tiles/{z}/{x}/{y}.<ext>` yapısı
# ============================================================================ #


class TileCache:
    """`cache_dir` altında `tiles/{z}/{x}/{y}.<ext>` yapısında ham tile
    byte'larını okuyup yazan ince bir depolama katmanı - A4'ün "yerel bir
    klasöre... kaydedilir" isteğinin en basit karşılığı."""

    def __init__(self, cache_dir: str | Path, extension: str = "png") -> None:
        self.cache_dir = Path(cache_dir)
        self.extension = extension.lstrip(".")
        (self.cache_dir / "tiles").mkdir(parents=True, exist_ok=True)

    def tile_path(self, z: int, x: int, y: int) -> Path:
        return self.cache_dir / "tiles" / str(z) / str(x) / f"{y}.{self.extension}"

    def has_tile(self, z: int, x: int, y: int) -> bool:
        return self.tile_path(z, x, y).is_file()

    def read_tile(self, z: int, x: int, y: int) -> bytes | None:
        path = self.tile_path(z, x, y)
        if not path.is_file():
            return None
        return path.read_bytes()

    def write_tile(self, z: int, x: int, y: int, data: bytes) -> None:
        path = self.tile_path(z, x, y)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def tile_count(self) -> int:
        return sum(1 for _ in (self.cache_dir / "tiles").rglob(f"*.{self.extension}"))

    def total_bytes(self) -> int:
        return sum(
            p.stat().st_size for p in (self.cache_dir / "tiles").rglob(f"*.{self.extension}")
        )

    # -- manifest (indirilen bölgelerin defteri, A4 "offline paket") ---- #

    def _manifest_path(self) -> Path:
        return self.cache_dir / "manifest.json"

    def load_manifest(self) -> list[dict]:
        path = self._manifest_path()
        if not path.is_file():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def record_region(
        self,
        *,
        name: str,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        zoom_min: int,
        zoom_max: int,
        tile_count: int,
    ) -> None:
        """A4'ün "önceden 'offline paket' olarak indirilmiş bir bölge"
        kabul kriterine giriş: hangi bölgelerin ne zaman/ne kapsamda
        indirildiğinin kalıcı bir defteri."""
        regions = self.load_manifest()
        regions.append(
            {
                "name": name,
                "bbox": [min_lat, min_lon, max_lat, max_lon],
                "zoom_range": [zoom_min, zoom_max],
                "tile_count": tile_count,
                "downloaded_at": time.time(),
            }
        )
        self._manifest_path().write_text(
            json.dumps(regions, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ============================================================================ #
# İndirme - urllib.request tabanlı, testlerde degistirilebilir `fetcher`
# ============================================================================ #

Fetcher = Callable[[str], bytes]


def _default_fetcher(
    url: str, timeout: float = 10.0, user_agent: str = DEFAULT_USER_AGENT
) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - kullanıcı tarafından verilen tile sunucusu
        return response.read()


@dataclass(slots=True)
class TileDownloadResult:
    """A4'ün "offline için indir" işleminin sonuç raporu."""

    requested: int = 0
    already_cached: int = 0
    downloaded: int = 0
    failed: int = 0
    failed_tiles: list[TileKey] = field(default_factory=list)

    def success_ratio(self) -> float:
        if self.requested == 0:
            return 1.0
        return (self.already_cached + self.downloaded) / self.requested


def download_tiles(
    cache: TileCache,
    tiles: Iterable[TileKey],
    url_template: str,
    *,
    fetcher: Fetcher | None = None,
    max_tiles: int = DEFAULT_MAX_TILES_PER_DOWNLOAD,
    skip_existing: bool = True,
) -> TileDownloadResult:
    """`tiles` listesindeki her `(z, x, y)` için `url_template`'i (ör.
    ``"https://tile.openstreetmap.org/{z}/{x}/{y}.png"``) doldurup indirir
    ve `cache`'e yazar. `fetcher` verilmezse `urllib.request` kullanılır
    (testlerde sahte bir fonksiyon geçirilebilir - ağ gerektirmez).

    `max_tiles` A4'ün örtük "politeness" ilkesi: `DEFAULT_MAX_TILES_PER_
    DOWNLOAD`'ı aşan istekler sessizce kırpılır (kullanıcı arayüzü bunu
    önceden "N tile indirilecek" özetiyle göstermeli, B5'in "istatistik
    özeti" ilkesiyle tutarlı)."""
    fetch = fetcher or _default_fetcher
    tile_list = list(tiles)[:max_tiles]
    result = TileDownloadResult(requested=len(tile_list))
    for z, x, y in tile_list:
        if skip_existing and cache.has_tile(z, x, y):
            result.already_cached += 1
            continue
        url = url_template.format(z=z, x=x, y=y)
        try:
            data = fetch(url)
        except Exception:  # noqa: BLE001 - tek bir tile hatası tüm indirmeyi durdurmamalı
            result.failed += 1
            result.failed_tiles.append((z, x, y))
            continue
        cache.write_tile(z, x, y, data)
        result.downloaded += 1
    return result


def download_bbox(
    cache: TileCache,
    *,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    zoom_min: int,
    zoom_max: int,
    url_template: str,
    region_name: str = "offline_region",
    fetcher: Fetcher | None = None,
    max_tiles: int = DEFAULT_MAX_TILES_PER_DOWNLOAD,
) -> TileDownloadResult:
    """`tiles_for_bbox_zoom_range` + `download_tiles` + `record_region`'ı
    tek çağrıda birleştiren, A4'ün "bir bölgeyi offline için indir"
    kabul kriterine doğrudan karşılık gelen üst seviye giriş noktası."""
    tiles = tiles_for_bbox_zoom_range(min_lat, min_lon, max_lat, max_lon, zoom_min, zoom_max)
    result = download_tiles(cache, tiles, url_template, fetcher=fetcher, max_tiles=max_tiles)
    cache.record_region(
        name=region_name,
        min_lat=min_lat,
        min_lon=min_lon,
        max_lat=max_lat,
        max_lon=max_lon,
        zoom_min=zoom_min,
        zoom_max=zoom_max,
        tile_count=result.downloaded + result.already_cached,
    )
    return result


__all__ = [
    "DEFAULT_MAX_TILES_PER_DOWNLOAD",
    "DEFAULT_USER_AGENT",
    "TileKey",
    "deg2tile",
    "tile2deg",
    "tiles_for_bbox",
    "tiles_for_bbox_zoom_range",
    "TileCache",
    "TileDownloadResult",
    "download_tiles",
    "download_bbox",
]
