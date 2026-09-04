"""Roadmap Faz 5.2 — PostgreSQL + PostGIS kalıcılık backend'i.

Gerçek bir Postgres sunucusu bu sandbox'ta yok (izin verilen ağ alan-adı
listesi bir DB sunucusunu kapsamıyor) — bu yüzden CRUD/şema mantığı,
psycopg'nin kendi arayüzünü taklit eden hafif bir sahte (fake) bağlantı
üzerinden offline test edilir (gerçek SQL string'leri + parametre
sırası doğrulanır). Ayrıca `psycopg` paketinin gerçekten import
edilebildiği (bu ortamda kurulu) doğrulanır. Canlı bağlantı testi ağ/DB
yoksa açıkça skip edilir.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.persistence.db_backend import ObjectRecord
from harita.persistence.postgres_backend import (
    _PSYCOPG_AVAILABLE,
    PostgresProjectDatabase,
    PostgresUnavailable,
    footprint_to_wkt,
)
from harita.persistence.project_format import FORMAT_VERSION, ProjectManifest


class TestPsycopgAvailability:
    def test_psycopg_import_flag_matches_real_import(self):
        try:
            import psycopg  # noqa: F401

            really_available = True
        except ImportError:
            really_available = False
        assert _PSYCOPG_AVAILABLE == really_available


class TestFootprintToWKT:
    def test_none_returns_none(self):
        assert footprint_to_wkt(None) is None

    def test_triangle_produces_valid_polygon_wkt(self):
        wkt = footprint_to_wkt([(28.97, 41.00), (28.98, 41.00), (28.98, 41.01)])
        assert wkt.startswith("POLYGON((")
        assert wkt.endswith("))")
        # Halka otomatik kapatılmalı: ilk nokta sonda tekrar etmeli.
        assert wkt.count("28.97 41.0") >= 2

    def test_already_closed_ring_not_duplicated(self):
        ring = [(0, 0), (1, 0), (1, 1), (0, 0)]
        wkt = footprint_to_wkt(ring)
        assert wkt.count("0 0") == 2  # başta ve sonda - üçüncü kez eklenmemeli

    def test_too_few_points_raises(self):
        with pytest.raises(ValueError):
            footprint_to_wkt([(0, 0), (1, 1)])


# ---------------------------------------------------------------------------
# Sahte (fake) psycopg bağlantısı — gerçek SQL yürütmeden CRUD akışını
# doğrular. `db_backend.ProjectDatabase` ile aynı public API sözleşmesini
# (create/open/save_object/load_object/delete_object/list_objects/
# count_objects/query_bbox) uçtan uca egzersiz eder.
# ---------------------------------------------------------------------------

import json


def _maybe_loads(value):
    """psycopg, jsonb sütunlarını otomatik olarak Python nesnesine çözer;
    bu sahte cursor JSON'u ham metin olarak sakladığı için burada taklit
    edilir."""
    if isinstance(value, str):
        return json.loads(value)
    return value


class _FakeCursor:
    def __init__(self, store: dict):
        self.store = store
        self._last_result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        sql_norm = " ".join(sql.split())
        if (
            "CREATE EXTENSION" in sql_norm
            or sql_norm.startswith("CREATE TABLE")
            or "CREATE INDEX" in sql_norm
        ):
            return
        if sql_norm.startswith("INSERT INTO harita_meta"):
            key, value = params
            self.store.setdefault("meta", {})[key] = value
        elif sql_norm.startswith("SELECT key, value FROM harita_meta"):
            self._last_result = list(self.store.get("meta", {}).items())
        elif sql_norm.startswith("INSERT INTO harita_objects"):
            objects = self.store.setdefault("objects", {})
            key, kind, data = params[0], params[1], params[2]
            objects[key] = {"kind": kind, "data": data, "footprint": None, "updated_at": params[-1]}
        elif sql_norm.startswith("INSERT INTO harita_history"):
            op = "save" if "'save'" in sql_norm else "delete"
            ts, key, kind = params
            self.store.setdefault("history", []).append((ts, op, key, kind))
        elif sql_norm.startswith(
            "SELECT key, kind, data, updated_at FROM harita_objects WHERE key"
        ):
            key = params[0]
            row = self.store.get("objects", {}).get(key)
            self._last_result = (
                None
                if row is None
                else [(key, row["kind"], _maybe_loads(row["data"]), row["updated_at"])]
            )
        elif sql_norm.startswith("SELECT kind FROM harita_objects WHERE key"):
            key = params[0]
            row = self.store.get("objects", {}).get(key)
            self._last_result = None if row is None else [(row["kind"],)]
        elif sql_norm.startswith("DELETE FROM harita_objects"):
            self.store.get("objects", {}).pop(params[0], None)
        elif sql_norm.startswith("SELECT key FROM harita_objects"):
            objects = self.store.get("objects", {})
            if len(params) == 1:
                keys = [k for k, v in objects.items() if v["kind"] == params[0]]
            else:
                keys = list(objects.keys())
            self._last_result = [(k,) for k in sorted(keys)]
        elif sql_norm.startswith("SELECT COUNT(*) FROM harita_objects"):
            objects = self.store.get("objects", {})
            if params:
                n = sum(1 for v in objects.values() if v["kind"] == params[0])
            else:
                n = len(objects)
            self._last_result = (n,)
        elif sql_norm.startswith("SELECT ts, op, key, kind FROM harita_history"):
            self._last_result = list(reversed(self.store.get("history", [])))[: params[0]]
        else:
            raise AssertionError(f"Fake cursor unhandled SQL: {sql_norm[:80]}")

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)

    def fetchone(self):
        if isinstance(self._last_result, tuple):
            return self._last_result
        if not self._last_result:
            return None
        return self._last_result[0]

    def fetchall(self):
        return self._last_result or []


class _FakeConnection:
    def __init__(self):
        self.store = {}
        self.autocommit = False

    def cursor(self):
        return _FakeCursor(self.store)

    def commit(self):
        pass

    def close(self):
        pass


def _fake_db(manifest: ProjectManifest) -> PostgresProjectDatabase:
    import threading

    conn = _FakeConnection()
    db = PostgresProjectDatabase(dsn="postgresql://fake/db", _conn=conn, _lock=threading.RLock())
    with conn.cursor() as cur:
        pass  # schema "creation" no-op on fake
    db._write_manifest(manifest)
    return db


class TestPostgresBackendCRUDWithFakeConnection:
    def _manifest(self) -> ProjectManifest:
        return ProjectManifest(name="Test Proje", project_id="p-test")

    def test_manifest_roundtrip(self):
        db = _fake_db(self._manifest())
        manifest = db.read_manifest()
        assert manifest.name == "Test Proje"
        assert manifest.project_id == "p-test"
        assert manifest.format_version == FORMAT_VERSION

    def test_save_and_load_object(self):
        db = _fake_db(self._manifest())
        db.save_object("bina_1", "building", {"floors": 5})
        rec = db.load_object("bina_1")
        assert isinstance(rec, ObjectRecord)
        assert rec.data == {"floors": 5}
        assert rec.kind == "building"

    def test_load_missing_object_returns_none(self):
        db = _fake_db(self._manifest())
        assert db.load_object("nope") is None

    def test_delete_object(self):
        db = _fake_db(self._manifest())
        db.save_object("bina_1", "building", {"floors": 5})
        assert db.delete_object("bina_1") is True
        assert db.load_object("bina_1") is None
        assert db.delete_object("bina_1") is False

    def test_list_and_count_objects_by_kind(self):
        db = _fake_db(self._manifest())
        db.save_object("b1", "building", {})
        db.save_object("b2", "building", {})
        db.save_object("t1", "tree", {})
        assert sorted(db.list_objects("building")) == ["b1", "b2"]
        assert db.count_objects("building") == 2
        assert db.count_objects() == 3

    def test_save_many(self):
        db = _fake_db(self._manifest())
        n = db.save_many([("a", "x", 1), ("b", "x", 2)])
        assert n == 2
        assert db.count_objects() == 2

    def test_history_recorded_on_save_and_delete(self):
        db = _fake_db(self._manifest())
        db.save_object("k", "kind", {})
        db.delete_object("k")
        history = list(db.iter_history(limit=10))
        ops = [h[1] for h in history]
        assert "save" in ops and "delete" in ops


# ---------------------------------------------------------------------------
# Canlı bağlantı — yalnızca gerçek erişilebilir bir Postgres varsa çalışır.
# ---------------------------------------------------------------------------


def _postgres_reachable() -> bool:
    if not _PSYCOPG_AVAILABLE:
        return False
    import os

    dsn = os.environ.get("HARITA_TEST_POSTGRES_DSN")
    if not dsn:
        return False
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _postgres_reachable(),
    reason=(
        "Bu ortamda erişilebilir bir PostgreSQL yok (HARITA_TEST_POSTGRES_DSN "
        "ortam değişkeni tanımlı değil ya da bağlantı başarısız). Gerçek bir "
        "Postgres+PostGIS sunucusu olan bir makinede bu test gerçekten çalışır."
    ),
)
class TestLivePostgres:
    def test_live_create_save_query_bbox(self):
        import os

        dsn = os.environ["HARITA_TEST_POSTGRES_DSN"]
        manifest = ProjectManifest(name="Canlı Test", project_id="live-1")
        db = PostgresProjectDatabase.create(dsn, manifest)
        try:
            db.save_object(
                "bina_ist",
                "building",
                {"floors": 6},
                footprint=[(28.97, 41.00), (28.98, 41.00), (28.98, 41.01), (28.97, 41.01)],
            )
            hits = db.query_bbox(min_lon=28.9, min_lat=40.9, max_lon=29.1, max_lat=41.1)
            assert any(h.key == "bina_ist" for h in hits)
        finally:
            db.close()


def test_postgres_unavailable_error_has_install_hint_when_missing():
    if _PSYCOPG_AVAILABLE:
        pytest.skip("psycopg bu ortamda kurulu - unavailable dalı test edilemez")
    with pytest.raises(PostgresUnavailable, match="postgres"):
        PostgresProjectDatabase.create(
            "postgresql://x/y", ProjectManifest(name="x", project_id="x")
        )
