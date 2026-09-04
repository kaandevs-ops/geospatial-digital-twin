"""
Sıcak/Soğuk Veri Katmanı (Tiered Cache)
========================================

ROADMAP_V3 — Faz D15.

`data_engine.cache.ObjectCache` bellek-içi (sıcak, hızlı, sınırlı kapasiteli)
çalışır; `persistence.db_backend.ProjectDatabase` diske (soğuk, sınırsız
boyutlu ama daha yavaş) kalıcı yazar. Bugüne kadar bu iki katman **elle**
kullanılıyordu — bir nesneye erişmek isteyen kod, önce sıcak katmana mı
soğuk katmana mı bakacağına kendisi karar vermek zorundaydı.

`TieredCache`, bu iki katmanı tek bir "anahtar -> nesne" arayüzü altında
birleştirir:

- `get(key)`  : önce sıcak katmana bakar (O(1)); yoksa soğuk katmandan
  (`ProjectDatabase.load_object`) okur ve bulunursa **sıcağa terfi ettirir**
  (promotion) — bir sonraki erişim artık hızlı olur.
- `put(key, kind, value)` : hem sıcağa hem soğuğa yazar (write-through) —
  böylece süreç çökse bile veri kaybolmaz; sıcak katman kapasiteyi aşarsa
  LRU tahliye edilen nesne zaten soğukta durduğu için veri kaybı olmaz
  (deport/eviction sessiz ve güvenlidir).
- `evict_cold(key)` yalnızca sıcaktan siler (soğukta kalır) — bellek
  baskısını azaltmak için elle tetiklenebilir.

Bu, roadmap'in "otomatik sıcak/soğuk ayrımı" hedefini karşılar: çağıran
kod yalnızca `TieredCache` ile konuşur, hangi katmanın kullanıldığını asla
bilmek zorunda değildir.

Değerler `ProjectDatabase.save_object` sözleşmesiyle uyumlu olmalı, yani
stdlib `json.dumps` ile serileştirilebilir olmalıdır (dict/list/str/int/
float/bool/None kombinasyonları). Bu katman formatı zorlamaz; yalnızca
taşır (`db_backend.py` ile aynı ilke).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cache import ObjectCache

try:  # pragma: no cover - sadece import yolu farklılığı
    from ..persistence.db_backend import ProjectDatabase
except ImportError:  # pragma: no cover
    ProjectDatabase = Any  # type: ignore[assignment,misc]


@dataclass
class TieredCacheStats:
    """Gözlemlenebilirlik için basit sayaçlar (benchmark/regresyon testleri
    bu sayaçları okuyarak sıcak/soğuk oranını doğrular)."""

    hot_hits: int = 0
    cold_hits: int = 0
    misses: int = 0
    promotions: int = 0
    cold_writes: int = 0

    @property
    def total_reads(self) -> int:
        return self.hot_hits + self.cold_hits + self.misses

    @property
    def hot_hit_ratio(self) -> float:
        total = self.total_reads
        return self.hot_hits / total if total else 0.0


@dataclass
class TieredCacheEntry:
    """`get()`'ten dönen zarf: değer + hangi katmandan geldiği bilgisi."""

    key: str
    kind: str
    value: Any
    source: str  # "hot" | "cold"


class TieredCache:
    """Sıcak (bellek, LRU) + soğuk (disk, `ProjectDatabase`) iki katmanlı
    önbellek.

    `db` verilmezse yalnızca sıcak katman gibi davranır (soğuk katman
    yoksa terfi/write-through devre dışı kalır, mevcut davranışa sessizce
    düşer) — `ObjectCache`'in tek başına kullanımıyla tam geriye uyumlu.
    """

    def __init__(self, db: ProjectDatabase | None = None, hot_capacity: int = 256) -> None:
        self._hot: ObjectCache[TieredCacheEntry] = ObjectCache(capacity=hot_capacity)
        self._db = db
        self.stats = TieredCacheStats()
        # soğuktan silinen ama henüz sıcağa da yazılmamış anahtarları
        # ayırt etmek için (tombstone yerine basitçe db'den de sil).
        self._known_kinds: dict[str, str] = {}

    # -- yazma ------------------------------------------------------- #

    def put(self, key: str, kind: str, value: Any) -> None:
        """Write-through: hem sıcağa hem (varsa) soğuğa yazar."""
        entry = TieredCacheEntry(key=key, kind=kind, value=value, source="hot")
        self._hot.put(key, entry)
        self._known_kinds[key] = kind
        if self._db is not None:
            self._db.save_object(key, kind, value)
            self.stats.cold_writes += 1

    # -- okuma --------------------------------------------------------- #

    def get(self, key: str) -> TieredCacheEntry | None:
        cached = self._hot.get(key)
        if cached is not None:
            self.stats.hot_hits += 1
            return TieredCacheEntry(
                key=cached.key, kind=cached.kind, value=cached.value, source="hot"
            )

        if self._db is None:
            self.stats.misses += 1
            return None

        record = self._db.load_object(key)
        if record is None:
            self.stats.misses += 1
            return None

        # terfi (promotion): soğuktan okunan nesne artık sıcakta.
        entry = TieredCacheEntry(key=key, kind=record.kind, value=record.data, source="cold")
        self._hot.put(key, entry)
        self._known_kinds[key] = record.kind
        self.stats.cold_hits += 1
        self.stats.promotions += 1
        return entry

    # -- silme / bakım -------------------------------------------------- #

    def evict_cold(self, key: str) -> bool:
        """Yalnızca sıcak katmandan çıkarır; soğukta veri kalmaya devam
        eder (bir sonraki `get()` yeniden terfi ettirir)."""
        return self._hot.invalidate(key)

    def delete(self, key: str) -> bool:
        """Hem sıcaktan hem soğuktan siler (kalıcı silme)."""
        hot_deleted = self._hot.invalidate(key)
        self._known_kinds.pop(key, None)
        cold_deleted = False
        if self._db is not None:
            cold_deleted = self._db.delete_object(key)
        return hot_deleted or cold_deleted

    def hot_size(self) -> int:
        return len(self._hot._store)  # noqa: SLF001 - aynı paket içi iç durum okuması

    def __contains__(self, key: str) -> bool:
        if key in self._hot._store:  # noqa: SLF001
            return True
        if self._db is None:
            return False
        return self._db.load_object(key) is not None


__all__ = ["TieredCache", "TieredCacheEntry", "TieredCacheStats"]
