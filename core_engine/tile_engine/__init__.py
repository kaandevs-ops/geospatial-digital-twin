"""
Tile Engine
============

Roadmap Phase 1 - "Harita Motoru":
    Tile Engine, Multi Zoom Engine, Infinite Scroll, Coordinate Converter,
    Projection Engine, GPS Coordinate Engine, GeoJSON Parser, Vector/Raster
    Tile Reader, Custom/Offline Tile Cache, Dynamic Tile Loader, Memory/Disk
    Cache, Async Tile Loading, Multi-thread Tile Renderer.

Bu modül, standart Slippy Map (Web Mercator, XYZ) tile şemasını temel alır
ve tamamen bağımsız (harici kütüphanesiz) çalışır.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..coordinate_systems import GeoPoint


# ======================================================================== #
# Tile Coordinate ve dönüşümler (Multi Zoom Engine)
# ======================================================================== #

@dataclass(frozen=True, slots=True)
class TileCoordinate:
    """Standart XYZ / Slippy Map tile koordinatı."""

    z: int
    x: int
    y: int

    def key(self) -> str:
        return f"{self.z}/{self.x}/{self.y}"

    @staticmethod
    def from_geopoint(point: GeoPoint, zoom: int) -> "TileCoordinate":
        lat_rad = math.radians(point.lat)
        n = 2 ** zoom
        x = int((point.lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        x = max(0, min(n - 1, x))
        y = max(0, min(n - 1, y))
        return TileCoordinate(z=zoom, x=x, y=y)

    def to_bounds(self) -> tuple[GeoPoint, GeoPoint]:
        """Tile'ın kapsadığı coğrafi sınır kutusu (NW, SE köşeleri)."""
        n = 2 ** self.z

        def _lat(y_tile: int) -> float:
            yy = math.pi * (1 - 2 * y_tile / n)
            return math.degrees(math.atan(math.sinh(yy)))

        lon_west = self.x / n * 360.0 - 180.0
        lon_east = (self.x + 1) / n * 360.0 - 180.0
        lat_north = _lat(self.y)
        lat_south = _lat(self.y + 1)
        return GeoPoint(lat_north, lon_west), GeoPoint(lat_south, lon_east)

    def children(self) -> list["TileCoordinate"]:
        """Bir üst zoom seviyesindeki 4 alt tile (quadtree)."""
        return [
            TileCoordinate(self.z + 1, self.x * 2, self.y * 2),
            TileCoordinate(self.z + 1, self.x * 2 + 1, self.y * 2),
            TileCoordinate(self.z + 1, self.x * 2, self.y * 2 + 1),
            TileCoordinate(self.z + 1, self.x * 2 + 1, self.y * 2 + 1),
        ]

    def parent(self) -> Optional["TileCoordinate"]:
        if self.z == 0:
            return None
        return TileCoordinate(self.z - 1, self.x // 2, self.y // 2)

    def neighbors(self) -> list["TileCoordinate"]:
        """Infinite Scroll için 8 komşu tile."""
        n = 2 ** self.z
        result = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = (self.x + dx) % n, self.y + dy
                if 0 <= ny < n:
                    result.append(TileCoordinate(self.z, nx, ny))
        return result


@dataclass(frozen=True, slots=True)
class TileData:
    coord: TileCoordinate
    content: bytes
    content_type: str  # "vector" | "raster"
    fetched_at: float = field(default_factory=time.time)


# ======================================================================== #
# Cache katmanları: Memory / Disk / Offline
# ======================================================================== #

class MemoryCache:
    """LRU tabanlı bellek içi tile önbelleği."""

    def __init__(self, capacity: int = 512):
        self.capacity = capacity
        self._store: "dict[str, TileData]" = {}
        self._order: list[str] = []

    def get(self, coord: TileCoordinate) -> TileData | None:
        key = coord.key()
        if key in self._store:
            self._order.remove(key)
            self._order.append(key)
            return self._store[key]
        return None

    def put(self, tile: TileData) -> None:
        key = tile.coord.key()
        if key in self._store:
            self._order.remove(key)
        elif len(self._store) >= self.capacity:
            oldest = self._order.pop(0)
            del self._store[oldest]
        self._store[key] = tile
        self._order.append(key)

    def __len__(self) -> int:
        return len(self._store)


class DiskCache:
    """Dosya sistemi tabanlı kalıcı tile önbelleği (offline kullanım için)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, coord: TileCoordinate) -> Path:
        h = hashlib.sha1(coord.key().encode()).hexdigest()[:2]
        d = self.root / str(coord.z) / h
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{coord.x}_{coord.y}.tile"

    def get(self, coord: TileCoordinate) -> TileData | None:
        path = self._path_for(coord)
        meta_path = path.with_suffix(".meta")
        if not path.exists() or not meta_path.exists():
            return None
        content = path.read_bytes()
        meta = json.loads(meta_path.read_text())
        return TileData(coord=coord, content=content,
                         content_type=meta["content_type"],
                         fetched_at=meta["fetched_at"])

    def put(self, tile: TileData) -> None:
        path = self._path_for(tile.coord)
        path.write_bytes(tile.content)
        path.with_suffix(".meta").write_text(json.dumps({
            "content_type": tile.content_type,
            "fetched_at": tile.fetched_at,
        }))

    def exists(self, coord: TileCoordinate) -> bool:
        return self._path_for(coord).exists()


class TileCache:
    """Memory + Disk cache'i birleştiren, "Custom Tile Cache" / "Offline Tile
    Cache" gereksinimini karşılayan birleşik katman."""

    def __init__(self, memory_capacity: int = 512, disk_root: str | Path | None = None):
        self.memory = MemoryCache(memory_capacity)
        self.disk = DiskCache(disk_root) if disk_root else None

    def get(self, coord: TileCoordinate) -> TileData | None:
        tile = self.memory.get(coord)
        if tile is not None:
            return tile
        if self.disk is not None:
            tile = self.disk.get(coord)
            if tile is not None:
                self.memory.put(tile)
                return tile
        return None

    def put(self, tile: TileData) -> None:
        self.memory.put(tile)
        if self.disk is not None:
            self.disk.put(tile)


# ======================================================================== #
# Tile Loader (Async + Multi-thread) ve Tile Engine
# ======================================================================== #

TileFetchFn = Callable[[TileCoordinate], bytes]


class TileEngine:
    """
    Ana harita motoru: verilen bir "fetch" fonksiyonu (ağdan/dosyadan tile
    indiren herhangi bir callable) etrafında cache, async yükleme ve
    multi-thread render pipeline'ı sağlar.

    `fetch_fn` enjekte edilir; böylece tile_engine, gerçek bir tile
    sağlayıcısına (OSM, Cadastre, yerel .mbtiles vb.) bağımlı olmadan test
    edilebilir ve farklı kaynaklara kolayca bağlanabilir.
    """

    def __init__(
        self,
        fetch_fn: TileFetchFn,
        content_type: str = "vector",
        cache: TileCache | None = None,
        max_workers: int = 8,
    ):
        self.fetch_fn = fetch_fn
        self.content_type = content_type
        self.cache = cache or TileCache()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    # -- senkron -- #
    def get_tile(self, coord: TileCoordinate) -> TileData:
        cached = self.cache.get(coord)
        if cached is not None:
            return cached
        content = self.fetch_fn(coord)
        tile = TileData(coord=coord, content=content, content_type=self.content_type)
        self.cache.put(tile)
        return tile

    # -- multi-thread render pipeline -- #
    def get_tiles(self, coords: list[TileCoordinate]) -> list[TileData]:
        """Birden fazla tile'ı thread pool üzerinden paralel getirir
        (Multi-thread Tile Renderer)."""
        futures = [self._executor.submit(self.get_tile, c) for c in coords]
        return [f.result() for f in futures]

    # -- async -- #
    async def get_tile_async(self, coord: TileCoordinate) -> TileData:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self.get_tile, coord)

    async def get_tiles_async(self, coords: list[TileCoordinate]) -> list[TileData]:
        return await asyncio.gather(*(self.get_tile_async(c) for c in coords))

    # -- viewport / infinite scroll yardımcıları -- #
    def tiles_in_viewport(
        self, center: GeoPoint, zoom: int, viewport_tiles_radius: int = 2
    ) -> list[TileCoordinate]:
        """
        Ekranın merkezi ve zoom seviyesine göre, "sonsuz kaydırma" hissi
        veren bir tile penceresi üretir (merkez + N halka komşu tile).
        """
        center_tile = TileCoordinate.from_geopoint(center, zoom)
        n = 2 ** zoom
        result = []
        for dx in range(-viewport_tiles_radius, viewport_tiles_radius + 1):
            for dy in range(-viewport_tiles_radius, viewport_tiles_radius + 1):
                x = (center_tile.x + dx) % n
                y = center_tile.y + dy
                if 0 <= y < n:
                    result.append(TileCoordinate(zoom, x, y))
        return result

    def preload_offline_region(
        self, north_west: GeoPoint, south_east: GeoPoint, zoom_levels: list[int]
    ) -> int:
        """
        Belirtilen bölgeyi ve zoom seviyelerini önceden indirip diske
        (offline cache) yazar. Kaç tile indirildiğini döndürür.
        """
        count = 0
        for z in zoom_levels:
            t_nw = TileCoordinate.from_geopoint(north_west, z)
            t_se = TileCoordinate.from_geopoint(south_east, z)
            x_min, x_max = sorted((t_nw.x, t_se.x))
            y_min, y_max = sorted((t_nw.y, t_se.y))
            coords = [
                TileCoordinate(z, x, y)
                for x in range(x_min, x_max + 1)
                for y in range(y_min, y_max + 1)
            ]
            self.get_tiles(coords)
            count += len(coords)
        return count

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
