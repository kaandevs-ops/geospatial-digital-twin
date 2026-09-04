"""
Tile Sources — WMTS/WMS Katman Tüketimi
=========================================

ROADMAP_V2 Faz 17 — "Gerçek uydu/ortofoto katman desteği (WMTS/WMS tile
server tüketimi)".

Bu modül, standart WMTS (RESTful/KVP) ve WMS (GetMap) tile server'larına
karşı **doğru URL üretimini** ve tile içeriğini Faz 1 `TileEngine`
önbellek katmanına (`MemoryCache`/`DiskCache`) besleyen bir tüketim
katmanını sağlar.

Ağ bağımlılığı yok / bağımsız test edilebilir
-----------------------------------------------
Gerçek bir HTTP isteği atmak `urllib.request` ile `UrllibFetcher`
sınıfında uygulanmıştır (stdlib-only), ancak bu modülün testleri **hiçbir
zaman gerçek ağa çıkmaz** — `TileFetcher` bir `Protocol`'dür ve testler
`FakeFetcher` gibi sahte bir implementasyon enjekte eder. Bu, ağsız CI/test
ortamlarında (bu oturum dahil) modülün URL üretimi ve önbellek/hata
davranışının tam olarak doğrulanmasını sağlar; gerçek bir WMTS/WMS
sunucusuna karşı uçtan uca doğrulama (ör. bir kamu kurumunun ortofoto
servisi) kapsam dışıdır — bkz. ROADMAP_V2.md Faz 17 "kalan" notu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlencode

from ..tile_engine import TileCoordinate


class TileSourceError(Exception):
    """Tile kaynağı yapılandırması veya URL üretimi hatası."""


class TileFetchError(Exception):
    """Bir tile'ın getirilmesi (fetch) sırasında oluşan hata."""


# ======================================================================== #
# URL şablonları
# ======================================================================== #


@dataclass(frozen=True, slots=True)
class XYZTileSource:
    """
    Standart Slippy Map / XYZ tile şeması (Faz 1 `TileEngine` ile aynı
    şema). `{z}`/`{x}`/`{y}` yer tutucularını içeren bir URL şablonu alır.
    Örnek: "https://tile.example.com/{z}/{x}/{y}.png"
    """

    name: str
    url_template: str
    attribution: str = ""
    max_zoom: int = 19
    subdomains: tuple[str, ...] = ()

    def build_url(self, coord: TileCoordinate, subdomain_index: int = 0) -> str:
        if not (0 <= coord.z <= self.max_zoom):
            raise TileSourceError(
                f"zoom {coord.z}, '{self.name}' kaynağının max_zoom={self.max_zoom} sınırını aşıyor"
            )
        template = self.url_template
        if "{s}" in template:
            if not self.subdomains:
                raise TileSourceError(
                    f"'{self.name}' şablonu '{{s}}' içeriyor ama subdomains tanımlı değil"
                )
            sub = self.subdomains[subdomain_index % len(self.subdomains)]
            template = template.replace("{s}", sub)
        return template.format(z=coord.z, x=coord.x, y=coord.y)


@dataclass(frozen=True, slots=True)
class WMTSTileSource:
    """
    OGC WMTS — RESTful (Google Maps uyumlu XYZ benzeri) veya KVP
    (Key-Value-Pair, GetTile) modunda URL üretir.

    RESTful: base_url zaten {TileMatrix}/{TileCol}/{TileRow} yer
    tutucularını barındırır (WMTS Capabilities'ten alınan
    ResourceURL şablonu).
    KVP: base_url yalnızca sunucu endpoint'idir, sorgu parametreleri
    bu sınıf tarafından RFC 3986 uyumlu şekilde eklenir.
    """

    name: str
    base_url: str
    layer: str
    tile_matrix_set: str
    style: str = "default"
    image_format: str = "image/png"
    kvp: bool = True
    tile_matrix_prefix: str = ""  # ör. "EPSG:3857:" bazı sunucularda gerekir

    def build_url(self, coord: TileCoordinate) -> str:
        matrix = f"{self.tile_matrix_prefix}{coord.z}"
        if not self.kvp:
            return self.base_url.format(
                TileMatrix=matrix,
                TileCol=coord.x,
                TileRow=coord.y,
                z=coord.z,
                x=coord.x,
                y=coord.y,
            )
        params = {
            "SERVICE": "WMTS",
            "REQUEST": "GetTile",
            "VERSION": "1.0.0",
            "LAYER": self.layer,
            "STYLE": self.style,
            "TILEMATRIXSET": self.tile_matrix_set,
            "TILEMATRIX": matrix,
            "TILEROW": str(coord.y),
            "TILECOL": str(coord.x),
            "FORMAT": self.image_format,
        }
        sep = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{sep}{urlencode(params)}"


@dataclass(frozen=True, slots=True)
class WMSTileSource:
    """
    OGC WMS — GetMap. Slippy-map tile'ını, o tile'ın coğrafi sınır
    kutusuna (`TileCoordinate.to_bounds()`) karşılık gelen bir GetMap
    isteğine çevirir (EPSG:4326 bbox varsayılan; `crs` ile değiştirilebilir
    — dönüşüm çağıranın sorumluluğundadır, bu sınıf yalnızca URL üretir).
    """

    name: str
    base_url: str
    layers: str
    crs: str = "EPSG:4326"
    image_format: str = "image/png"
    tile_size: int = 256
    version: str = "1.3.0"
    transparent: bool = True

    def build_url(self, coord: TileCoordinate) -> str:
        nw, se = coord.to_bounds()
        if self.crs.upper() == "EPSG:4326" and self.version == "1.3.0":
            # WMS 1.3.0 + EPSG:4326: eksen sırası (lat, lon) — CRS84 değil.
            bbox = f"{se.lat},{nw.lon},{nw.lat},{se.lon}"
        else:
            bbox = f"{nw.lon},{se.lat},{se.lon},{nw.lat}"
        params = {
            "SERVICE": "WMS",
            "REQUEST": "GetMap",
            "VERSION": self.version,
            "LAYERS": self.layers,
            "STYLES": "",
            "CRS" if self.version == "1.3.0" else "SRS": self.crs,
            "BBOX": bbox,
            "WIDTH": str(self.tile_size),
            "HEIGHT": str(self.tile_size),
            "FORMAT": self.image_format,
            "TRANSPARENT": "TRUE" if self.transparent else "FALSE",
        }
        sep = "&" if "?" in self.base_url else "?"
        return f"{self.base_url}{sep}{urlencode(params)}"


TileSource = XYZTileSource | WMTSTileSource | WMSTileSource


# ======================================================================== #
# Fetch soyutlaması (ağ-agnostik, test edilebilir)
# ======================================================================== #


class TileFetcher(Protocol):
    """Bir URL'den ham tile byte'larını getiren herhangi bir çağrılabilir."""

    def __call__(self, url: str) -> bytes: ...


class UrllibFetcher:
    """
    `urllib.request` tabanlı stdlib-only gerçek HTTP fetcher.
    Bu ortamda ağ erişimi olmadığından uçtan uca test edilmemiştir;
    arayüz `TileFetcher` Protocol'üne uyar ve `FakeFetcher` ile aynı
    şekilde `TileSourceConsumer`'a enjekte edilebilir.
    """

    def __init__(self, timeout_s: float = 10.0, user_agent: str = "harita-modelleme/1.0"):
        self.timeout_s = timeout_s
        self.user_agent = user_agent

    def __call__(self, url: str) -> bytes:
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - tek bir TileFetchError'a normalize edilir
            raise TileFetchError(f"{url} getirilemedi: {exc}") from exc


@dataclass
class TileSourceConsumer:
    """
    Bir `TileSource` + `TileFetcher` çiftini, Faz 1 `MemoryCache`/`DiskCache`
    ile aynı `TileData` sözleşmesine bağlayan tüketici. `core_engine.
    tile_engine.TileCache` (memory+disk) doğrudan besleyicisi olarak
    kullanılabilir.
    """

    source: TileSource
    fetcher: TileFetcher
    _stats: dict[str, int] = field(default_factory=lambda: {"requests": 0, "errors": 0})

    def fetch_tile(self, coord: TileCoordinate) -> bytes:
        url = self.source.build_url(coord)
        self._stats["requests"] += 1
        try:
            return self.fetcher(url)
        except TileFetchError:
            self._stats["errors"] += 1
            raise
        except Exception as exc:  # noqa: BLE001
            self._stats["errors"] += 1
            raise TileFetchError(f"{url} getirilirken beklenmeyen hata: {exc}") from exc

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)


__all__ = [
    "TileSourceError",
    "TileFetchError",
    "XYZTileSource",
    "WMTSTileSource",
    "WMSTileSource",
    "TileSource",
    "TileFetcher",
    "UrllibFetcher",
    "TileSourceConsumer",
]
