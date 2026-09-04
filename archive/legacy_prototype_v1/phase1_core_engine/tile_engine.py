"""
harita_modelleme/phase1_core_engine/tile_engine.py
=====================================================
FAZ 1 — Harita Motoru (Tile Engine)

Roadmap kapsamı:
    - Tile Engine / Multi Zoom Engine / Infinite Scroll (tile matematiği)
    - Dynamic Tile Loader / Async Tile Loading / Multi-thread Tile Renderer
    - Memory Cache / Disk Cache / Custom Tile Cache / Offline Tile Cache

Standart Slippy Map (XYZ / Web Mercator) tile şemasını uygular:
https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

TILE_SIZE_PX = 256
MAX_ZOOM = 24
MIN_ZOOM = 0


class TileEngineError(ValueError):
    pass


# ============================================================================
# TILE COORDINATE MATEMATİĞİ
# ============================================================================


@dataclass(frozen=True)
class TileCoordinate:
    x: int
    y: int
    z: int

    def __post_init__(self) -> None:
        if not (MIN_ZOOM <= self.z <= MAX_ZOOM):
            raise TileEngineError(f"zoom [{MIN_ZOOM},{MAX_ZOOM}] dışında: {self.z}")
        n = 2**self.z
        if not (0 <= self.x < n) or not (0 <= self.y < n):
            raise TileEngineError(f"tile x/y zoom={self.z} sınırı dışında: ({self.x},{self.y})")

    def key(self) -> str:
        return f"{self.z}/{self.x}/{self.y}"

    def parent(self) -> TileCoordinate:
        if self.z == MIN_ZOOM:
            raise TileEngineError("kök tile'ın üst tile'ı yok")
        return TileCoordinate(self.x // 2, self.y // 2, self.z - 1)

    def children(self) -> tuple[TileCoordinate, TileCoordinate, TileCoordinate, TileCoordinate]:
        if self.z == MAX_ZOOM:
            raise TileEngineError("maksimum zoom'da alt tile yok")
        return (
            TileCoordinate(self.x * 2, self.y * 2, self.z + 1),
            TileCoordinate(self.x * 2 + 1, self.y * 2, self.z + 1),
            TileCoordinate(self.x * 2, self.y * 2 + 1, self.z + 1),
            TileCoordinate(self.x * 2 + 1, self.y * 2 + 1, self.z + 1),
        )


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> TileCoordinate:
    """WGS84 lon/lat -> XYZ tile koordinatı (Web Mercator projeksiyonu)."""
    if not (-180.0 <= lon <= 180.0) or not (-85.05112878 <= lat <= 85.05112878):
        raise TileEngineError(f"lon/lat tile şeması sınırı dışında: ({lon},{lat})")
    lat_rad = math.radians(lat)
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return TileCoordinate(x=x, y=y, z=zoom)


def tile_to_lonlat_bounds(tile: TileCoordinate) -> tuple[float, float, float, float]:
    """Tile -> (west, south, east, north) derece cinsinden sınır kutusu."""
    n = 2**tile.z

    def _lon(x: int) -> float:
        return x / n * 360.0 - 180.0

    def _lat(y: int) -> float:
        rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        return math.degrees(rad)

    west = _lon(tile.x)
    east = _lon(tile.x + 1)
    north = _lat(tile.y)
    south = _lat(tile.y + 1)
    return west, south, east, north


def lonlat_to_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    """WGS84 -> global piksel koordinatı (verilen zoom'da, tüm dünya)."""
    lat_rad = math.radians(lat)
    n = 2**zoom
    world_px = n * TILE_SIZE_PX
    x = (lon + 180.0) / 360.0 * world_px
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * world_px
    return x, y


# ============================================================================
# MEMORY CACHE (LRU)
# ============================================================================


class MemoryTileCache:
    """Basit thread-safe LRU bellek içi tile cache'i."""

    def __init__(self, max_items: int = 512):
        if max_items <= 0:
            raise TileEngineError("max_items > 0 olmalı")
        self.max_items = max_items
        self._store: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, tile: TileCoordinate) -> bytes | None:
        key = tile.key()
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self.hits += 1
                return self._store[key]
            self.misses += 1
            return None

    def put(self, tile: TileCoordinate, data: bytes) -> None:
        key = tile.key()
        with self._lock:
            self._store[key] = data
            self._store.move_to_end(key)
            if len(self._store) > self.max_items:
                self._store.popitem(last=False)  # en eski girdiyi at (LRU evict)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"items": len(self._store), "hits": self.hits, "misses": self.misses}


# ============================================================================
# DISK CACHE
# ============================================================================


class DiskTileCache:
    """
    Dosya sistemi tabanlı kalıcı (offline) tile cache'i.
    Dosya yapısı: {root}/{z}/{x}/{y}.tile
    """

    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        os.makedirs(self.root_dir, exist_ok=True)
        self._lock = threading.Lock()

    def _path_for(self, tile: TileCoordinate) -> str:
        return os.path.join(self.root_dir, str(tile.z), str(tile.x), f"{tile.y}.tile")

    def get(self, tile: TileCoordinate) -> bytes | None:
        path = self._path_for(tile)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def put(self, tile: TileCoordinate, data: bytes) -> None:
        path = self._path_for(tile)
        with self._lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = path + f".tmp-{threading.get_ident()}"
            with open(tmp_path, "wb") as f:
                f.write(data)
            os.replace(tmp_path, path)  # atomik yazma

    def has(self, tile: TileCoordinate) -> bool:
        return os.path.isfile(self._path_for(tile))

    def checksum(self, tile: TileCoordinate) -> str | None:
        data = self.get(tile)
        if data is None:
            return None
        return hashlib.sha256(data).hexdigest()

    def clear(self) -> None:
        import shutil

        with self._lock:
            if os.path.isdir(self.root_dir):
                shutil.rmtree(self.root_dir)
            os.makedirs(self.root_dir, exist_ok=True)


# ============================================================================
# TILE ENGINE — orkestrasyon (memory -> disk -> loader zinciri)
# ============================================================================

TileLoaderFn = Callable[[TileCoordinate], Awaitable[bytes]]


@dataclass
class TileEngineStats:
    memory_hits: int = 0
    disk_hits: int = 0
    loader_calls: int = 0
    errors: int = 0


class TileEngine:
    """
    Dynamic Tile Loader + Async/Multi-thread Tile Renderer orkestratörü.

    Çözüm sırası: memory cache -> disk cache -> `loader_fn` (kullanıcı
    tarafından enjekte edilen, örn. bir HTTP tile sağlayıcısına bağlanan
    async fonksiyon; offline kullanım için None bırakılabilir).
    """

    def __init__(
        self,
        loader_fn: TileLoaderFn | None = None,
        memory_cache: MemoryTileCache | None = None,
        disk_cache: DiskTileCache | None = None,
        max_concurrent_loads: int = 8,
    ):
        self.loader_fn = loader_fn
        self.memory_cache = memory_cache or MemoryTileCache()
        self.disk_cache = disk_cache
        self._semaphore = asyncio.Semaphore(max_concurrent_loads)
        self._inflight: dict[str, asyncio.Future[bytes]] = {}
        self._inflight_lock = threading.Lock()
        self.stats = TileEngineStats()

    async def get_tile(self, tile: TileCoordinate) -> bytes:
        """Bir tile'ı memory -> disk -> loader zinciriyle async olarak getirir."""
        cached = self.memory_cache.get(tile)
        if cached is not None:
            self.stats.memory_hits += 1
            return cached

        if self.disk_cache is not None:
            disk_data = self.disk_cache.get(tile)
            if disk_data is not None:
                self.stats.disk_hits += 1
                self.memory_cache.put(tile, disk_data)
                return disk_data

        if self.loader_fn is None:
            raise TileEngineError(
                f"Tile {tile.key()} cache'lerde yok ve loader_fn tanımlı değil "
                "(offline mod — bu tile önceden indirilmemiş)."
            )

        # Aynı tile için eşzamanlı çağrıları tekilleştir (request coalescing)
        key = tile.key()
        with self._inflight_lock:
            future = self._inflight.get(key)
            is_owner = future is None
            if is_owner:
                future = asyncio.get_event_loop().create_future()
                self._inflight[key] = future

        if not is_owner:
            return await future  # type: ignore[return-value]

        try:
            async with self._semaphore:
                self.stats.loader_calls += 1
                data = await self.loader_fn(tile)
            self.memory_cache.put(tile, data)
            if self.disk_cache is not None:
                self.disk_cache.put(tile, data)
            future.set_result(data)  # type: ignore[union-attr]
            return data
        except Exception as exc:  # noqa: BLE001 - loader hatasını future'a taşı
            self.stats.errors += 1
            future.set_exception(exc)  # type: ignore[union-attr]
            raise
        finally:
            with self._inflight_lock:
                self._inflight.pop(key, None)

    async def get_tiles(self, tiles: list[TileCoordinate]) -> list[bytes]:
        """Birden çok tile'ı eşzamanlı (concurrent) yükler — infinite-scroll pan/zoom senaryosu."""
        return await asyncio.gather(*(self.get_tile(t) for t in tiles))

    def visible_tiles(
        self, west: float, south: float, east: float, north: float, zoom: int
    ) -> list[TileCoordinate]:
        """
        Verilen görünür alan (viewport bbox) ve zoom seviyesi için gereken
        tile listesini üretir. "Infinite Scroll" ve "Multi Zoom Engine" için
        temel taşı.
        """
        top_left = lonlat_to_tile(west, north, zoom)
        bottom_right = lonlat_to_tile(east, south, zoom)
        tiles = []
        for x in range(top_left.x, bottom_right.x + 1):
            for y in range(top_left.y, bottom_right.y + 1):
                tiles.append(TileCoordinate(x=x, y=y, z=zoom))
        return tiles
