"""
Object / Scene Cache
=====================

Roadmap Phase 10 - "Object Cache", "Scene Cache".

`core_engine.tile_engine.MemoryCache`/`DiskCache`/`TileCache` ile aynı LRU
desenini (Phase 1'de tile verisi için kurulmuştu) genel amaçlı nesnelere
(procedural olarak üretilmiş `Mesh3D`, `DigitalTwin`, `Floor`, herhangi bir
ağır hesaplanmış nesne) uygular.

- `ObjectCache[T]`  : anahtar -> nesne, LRU tahliyeli, generic tip.
- `SceneCache`      : birden çok `ObjectCache` bucket'ını isimle yöneten,
  bir "sahne"deki farklı nesne türlerini (mesh, digital_twin, nav_graph, ...)
  ayrı kapasitelerle önbelleğe alan üst katman.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class ObjectCache(Generic[T]):
    """LRU (Least Recently Used) tabanlı genel amaçlı nesne önbelleği.

    `core_engine.tile_engine.MemoryCache` ile aynı tahliye stratejisi
    (`_order` listesi + `capacity` sınırı), ancak `TileCoordinate` yerine
    herhangi bir `Hashable` anahtar ve herhangi bir tip (`T`) nesne kabul
    eder. `factory` verilirse `get_or_create` ile "yoksa üret, varsa
    getir" deseni desteklenir (procedural generation önbelleklemesi için
    tipik kullanım: bina mesh'i ilk erişimde üretilir, sonrakinde önbellekten
    döner).
    """

    def __init__(self, capacity: int = 256) -> None:
        if capacity < 1:
            raise ValueError("ObjectCache: capacity >= 1 olmalı")
        self.capacity = capacity
        self._store: dict[object, T] = {}
        self._order: list[object] = []
        self.hits = 0
        self.misses = 0

    def get(self, key: object) -> T | None:
        if key in self._store:
            self._order.remove(key)
            self._order.append(key)
            self.hits += 1
            return self._store[key]
        self.misses += 1
        return None

    def put(self, key: object, value: T) -> None:
        if key in self._store:
            self._order.remove(key)
        elif len(self._store) >= self.capacity:
            oldest = self._order.pop(0)
            del self._store[oldest]
        self._store[key] = value
        self._order.append(key)

    def get_or_create(self, key: object, factory: Callable[[], T]) -> T:
        """Anahtar önbellekte varsa döndürür; yoksa `factory()` ile üretir,
        önbelleğe yazar ve döndürür."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = factory()
        self.put(key, value)
        return value

    def invalidate(self, key: object) -> bool:
        if key in self._store:
            del self._store[key]
            self._order.remove(key)
            return True
        return False

    def clear(self) -> None:
        self._store.clear()
        self._order.clear()

    def __contains__(self, key: object) -> bool:
        return key in self._store

    def __len__(self) -> int:
        return len(self._store)

    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class SceneCache:
    """Bir sahnedeki farklı nesne türleri için ayrı `ObjectCache` bucket'ları
    yöneten üst katman.

    Roadmap: "Scene Cache". Örnek kullanım::

        cache = SceneCache()
        cache.bucket("mesh", capacity=128)
        cache.bucket("digital_twin", capacity=64)

        mesh = cache.get_or_create("mesh", building_id, lambda: build_mesh(...))
    """

    def __init__(self) -> None:
        self._buckets: dict[str, ObjectCache] = {}

    def bucket(self, name: str, capacity: int = 256) -> ObjectCache:
        """`name` bucket'ını döndürür; yoksa `capacity` ile oluşturur.
        Bucket zaten varsa `capacity` parametresi yok sayılır (mevcut
        bucket olduğu gibi döner)."""
        if name not in self._buckets:
            self._buckets[name] = ObjectCache(capacity)
        return self._buckets[name]

    def get(self, bucket: str, key: object) -> object | None:
        if bucket not in self._buckets:
            return None
        return self._buckets[bucket].get(key)

    def put(self, bucket: str, key: object, value: object) -> None:
        self.bucket(bucket).put(key, value)

    def get_or_create(self, bucket: str, key: object, factory: Callable[[], object]) -> object:
        return self.bucket(bucket).get_or_create(key, factory)

    def invalidate(self, bucket: str, key: object) -> bool:
        if bucket not in self._buckets:
            return False
        return self._buckets[bucket].invalidate(key)

    def invalidate_bucket(self, bucket: str) -> None:
        if bucket in self._buckets:
            self._buckets[bucket].clear()

    def clear_all(self) -> None:
        for b in self._buckets.values():
            b.clear()

    def bucket_names(self) -> list[str]:
        return list(self._buckets.keys())

    def total_size(self) -> int:
        return sum(len(b) for b in self._buckets.values())

    def stats(self) -> dict[str, dict[str, float | int]]:
        """Her bucket için `{size, capacity, hits, misses, hit_rate}`."""
        return {
            name: {
                "size": len(b),
                "capacity": b.capacity,
                "hits": b.hits,
                "misses": b.misses,
                "hit_rate": b.hit_rate(),
            }
            for name, b in self._buckets.items()
        }
