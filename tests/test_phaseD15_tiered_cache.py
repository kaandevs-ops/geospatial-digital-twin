"""
ROADMAP_V3 — Faz D15: Data Engine sıcak/soğuk veri ayrımı testleri.

Kabul kriteri: büyük bir nesne kümesinde yalnızca sık erişilenler sıcak
katmanda (bellekte) tutulurken, nadir erişilenler soğuk katmanda (disk,
`ProjectDatabase`) kalır; erişildiğinde otomatik terfi eder ve toplam
sıcak-katman boyutu (bellek ayak izi) tüm veri kümesinin boyutundan
bağımsız, `hot_capacity` ile sınırlı kalır.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from harita.data_engine.tiered_cache import TieredCache
from harita.persistence.db_backend import ProjectDatabase
from harita.persistence.project_format import ProjectManifest


@pytest.fixture()
def db(tmp_path: Path) -> ProjectDatabase:
    path = tmp_path / "test.hproj"
    manifest = ProjectManifest(name="Test", project_id="p1")
    database = ProjectDatabase.create(path, manifest)
    yield database
    database.close()


class TestBasicHotOnly:
    def test_hot_only_mode_without_db(self) -> None:
        cache = TieredCache(db=None, hot_capacity=4)
        cache.put("a", "mesh3d", {"v": 1})
        entry = cache.get("a")
        assert entry is not None
        assert entry.value == {"v": 1}
        assert entry.source == "hot"

    def test_hot_only_miss(self) -> None:
        cache = TieredCache(db=None, hot_capacity=4)
        assert cache.get("missing") is None
        assert cache.stats.misses == 1


class TestWriteThrough:
    def test_put_writes_to_both_layers(self, db: ProjectDatabase) -> None:
        cache = TieredCache(db=db, hot_capacity=4)
        cache.put("bina_1", "mesh3d", {"h": 12.0})
        # doğrudan db'den okununca da veri orada olmalı (write-through)
        record = db.load_object("bina_1")
        assert record is not None
        assert record.data == {"h": 12.0}

    def test_get_prefers_hot_layer(self, db: ProjectDatabase) -> None:
        cache = TieredCache(db=db, hot_capacity=4)
        cache.put("bina_1", "mesh3d", {"h": 12.0})
        entry = cache.get("bina_1")
        assert entry is not None
        assert entry.source == "hot"
        assert cache.stats.hot_hits == 1
        assert cache.stats.cold_hits == 0


class TestPromotion:
    def test_eviction_then_cold_read_promotes(self, db: ProjectDatabase) -> None:
        cache = TieredCache(db=db, hot_capacity=4)
        cache.put("bina_1", "mesh3d", {"h": 1.0})
        # sıcaktan manuel çıkar (bellek baskısı simülasyonu); soğukta kalmalı
        assert cache.evict_cold("bina_1") is True
        assert cache.hot_size() == 0

        entry = cache.get("bina_1")
        assert entry is not None
        assert entry.source == "cold"
        assert cache.stats.promotions == 1

        # ikinci erişim artık sıcaktan gelmeli (terfi kalıcı)
        entry2 = cache.get("bina_1")
        assert entry2 is not None
        assert entry2.source == "hot"

    def test_lru_eviction_keeps_cold_copy_safe(self, db: ProjectDatabase) -> None:
        cache = TieredCache(db=db, hot_capacity=2)
        cache.put("a", "mesh3d", {"i": 1})
        cache.put("b", "mesh3d", {"i": 2})
        cache.put("c", "mesh3d", {"i": 3})  # kapasiteyi aşar, "a" LRU tahliye edilir
        assert cache.hot_size() <= 2

        # "a" hâlâ soğukta olmalı (write-through sayesinde veri kaybı yok)
        entry = cache.get("a")
        assert entry is not None
        assert entry.value == {"i": 1}


class TestDeleteAndContains:
    def test_delete_removes_from_both_layers(self, db: ProjectDatabase) -> None:
        cache = TieredCache(db=db, hot_capacity=4)
        cache.put("a", "mesh3d", {"i": 1})
        assert cache.delete("a") is True
        assert cache.get("a") is None
        assert db.load_object("a") is None

    def test_contains_checks_cold_layer_too(self, db: ProjectDatabase) -> None:
        cache = TieredCache(db=db, hot_capacity=4)
        cache.put("a", "mesh3d", {"i": 1})
        cache.evict_cold("a")
        assert "a" in cache


class TestScaleAcceptance:
    def test_hot_layer_bounded_regardless_of_dataset_size(self, db: ProjectDatabase) -> None:
        """Kabul kriteri: 2.000 nesnelik bir kümede yalnızca %10'una sık
        erişilen (Zipf-benzeri) bir erişim paterninde, sıcak katman
        boyutu `hot_capacity` ile sınırlı kalır — tüm veri kümesinin
        boyutundan bağımsız."""
        n = 2000
        hot_capacity = 100
        cache = TieredCache(db=db, hot_capacity=hot_capacity)

        for i in range(n):
            cache.put(f"obj_{i}", "mesh3d", {"i": i})

        # sıcak katman kapasiteyi hiçbir zaman aşmamalı
        assert cache.hot_size() <= hot_capacity

        # ama tüm veri soğukta (write-through) korunmuş olmalı
        assert db.count_objects() == n

        rng = random.Random(42)
        hot_ids = [rng.randrange(0, n // 10) for _ in range(500)]
        for i in hot_ids:
            entry = cache.get(f"obj_{i}")
            assert entry is not None

        # sıcak katman büyüklüğü hâlâ dataset boyutundan bağımsız kalmalı
        assert cache.hot_size() <= hot_capacity

    def test_promotion_counter_reflects_cold_reads(self, db: ProjectDatabase) -> None:
        cache = TieredCache(db=db, hot_capacity=2)
        for key in ("a", "b", "c"):
            cache.put(key, "mesh3d", {"k": key})
        # "a" artık kapasite aşıldığı için sıcaktan tahliye edilmiş olmalı
        assert cache.stats.promotions == 0
        entry = cache.get("a")
        assert entry is not None
        assert cache.stats.promotions == 1
