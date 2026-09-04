"""Kalıcı Disk Backend (PostgreSQL + PostGIS) — Roadmap Faz 5.2

    "üretimde PostgreSQL + PostGIS (coğrafi sorgular için endüstri
    standardı) önerilir."

`db_backend.ProjectDatabase` (SQLite) tek-dosya/tek-kullanıcı senaryosu
için hâlâ birincil ve varsayılan backend'dir — `persistence.project_manager.
ProjectManager` şu an SADECE onu kullanır (bu dürüstçe belirtilir, bu
modül `ProjectManager`'a otomatik olarak bağlanmaz). Bu modül, roadmap'in
istediği PostgreSQL+PostGIS'e API-uyumlu (aynı public metod yüzeyi:
`create`/`open`/`close`/`save_object`/`save_many`/`load_object`/
`delete_object`/`list_objects`/`count_objects`/`iter_history`/`vacuum`/
`read_manifest`/`update_manifest`) bir alternatif sağlar, artı SQLite'ın
yapamadığı gerçek coğrafi sorguyu (`query_bbox`) PostGIS `geometry`
sütunuyla ekler.

Opsiyonel bağımlılık
---------------------
`psycopg` (v3) yoksa import zamanında DEĞİL, ilk gerçek kullanımda açık
`PostgresUnavailable` fırlatılır (`extensibility/script_api.py`'deki Lua/JS
motorları ile aynı "opsiyonel bağımlılık" deseni).

Bu ortamda (network egress yalnızca pypi/npm/github alan adlarına izin
veriyor, gerçek bir Postgres sunucusu yok) canlı bağlantı testleri
`tests/test_faz5_2_postgres_backend.py` içinde açıkça `skip` edilir; şema/
SQL üretimi ve PostGIS WKT dönüşümü ağ/DB gerektirmeden offline test edilir.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Optional

from .project_format import FORMAT_VERSION, ProjectManifest, ProjectFormatError
from .db_backend import ObjectRecord

try:  # pragma: no cover - opsiyonel bağımlılık, ortama göre değişir
    import psycopg  # type: ignore
    _PSYCOPG_AVAILABLE = True
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore
    _PSYCOPG_AVAILABLE = False


class PostgresUnavailable(RuntimeError):
    """`psycopg` kurulu değil — `pip install harita-modelleme[postgres]`."""


class PostGISExtensionMissing(RuntimeError):
    """Bağlanılan veritabanında `CREATE EXTENSION postgis` çalıştırılmamış."""


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS harita_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harita_objects (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    data JSONB NOT NULL,
    footprint GEOMETRY(Polygon, 4326),
    updated_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_harita_objects_kind ON harita_objects(kind);
CREATE INDEX IF NOT EXISTS idx_harita_objects_footprint
    ON harita_objects USING GIST(footprint);
CREATE TABLE IF NOT EXISTS harita_history (
    id BIGSERIAL PRIMARY KEY,
    ts DOUBLE PRECISION NOT NULL,
    op TEXT NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL
);
"""


def _require_psycopg() -> None:
    if not _PSYCOPG_AVAILABLE:
        raise PostgresUnavailable(
            "psycopg (v3) kurulu değil. Kurmak için: "
            "pip install harita-modelleme[postgres]"
        )


def footprint_to_wkt(footprint: Optional[Iterable[tuple[float, float]]]) -> Optional[str]:
    """Bir (lon, lat) halka listesini PostGIS `POLYGON(...)` WKT'sine çevirir.

    `None` verilirse `None` döner (footprint opsiyoneldir — her nesnenin
    coğrafi konumu olmak zorunda değil, örn. bir malzeme/tema kaydı).
    Halka otomatik kapatılır (ilk nokta sonda tekrarlanmıyorsa eklenir).
    """
    if footprint is None:
        return None
    points = list(footprint)
    if len(points) < 3:
        raise ValueError("Bir poligon en az 3 nokta gerektirir")
    if points[0] != points[-1]:
        points = points + [points[0]]
    coords = ", ".join(f"{lon} {lat}" for lon, lat in points)
    return f"POLYGON(({coords}))"


@dataclass
class PostgresProjectDatabase:
    """`db_backend.ProjectDatabase` ile API-uyumlu PostgreSQL+PostGIS backend.

    Kullanım (gerçek bir Postgres sunucusuna sahip bir ortamda):
        db = PostgresProjectDatabase.create(
            "postgresql://harita:secret@localhost/harita",
            ProjectManifest(name="Şehir", project_id="p1"),
        )
        db.save_object("bina_42", "building", {"floors": 5},
                        footprint=[(28.97, 41.00), (28.98, 41.00), (28.98, 41.01)])
        nearby = db.query_bbox(min_lon=28.9, min_lat=40.9, max_lon=29.1, max_lat=41.1)
    """

    dsn: str
    _conn: Any
    _lock: threading.RLock

    # -- yaşam döngüsü -----------------------------------------------

    @classmethod
    def create(cls, dsn: str, manifest: ProjectManifest) -> "PostgresProjectDatabase":
        _require_psycopg()
        conn = psycopg.connect(dsn, autocommit=False)
        db = cls(dsn=dsn, _conn=conn, _lock=threading.RLock())
        db._ensure_postgis()
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
        conn.commit()
        db._write_manifest(manifest)
        conn.commit()
        return db

    @classmethod
    def open(cls, dsn: str, *, auto_migrate: bool = True) -> "PostgresProjectDatabase":
        _require_psycopg()
        conn = psycopg.connect(dsn, autocommit=False)
        db = cls(dsn=dsn, _conn=conn, _lock=threading.RLock())
        manifest = db._read_manifest()
        if manifest.format_version != FORMAT_VERSION:
            # DÜRÜST SINIRLAMA: `db_backend.migrate_schema` yalnızca SQLite
            # `Connection` alır (sqlite'a özgü SQL kullanır). Postgres için
            # ayrı bir migrasyon zinciri henüz yazılmadı — FORMAT_VERSION
            # şu an 1 olduğundan (tek sürüm yayınlandı) bu dal pratikte hiç
            # tetiklenmez; ileride v2 çıkarsa burada gerçek bir Postgres
            # migrasyon fonksiyonu eklenmelidir. Sessizce "migrate edildi"
            # gibi davranmak yerine açıkça hata verir.
            raise ProjectFormatError(
                f"Proje v{manifest.format_version}, beklenen v{FORMAT_VERSION} — "
                "Postgres backend için otomatik migrasyon henüz uygulanmadı."
            )
        return db

    def _ensure_postgis(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()

    def __enter__(self) -> "PostgresProjectDatabase":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(sql, params)

    # -- manifest ------------------------------------------------------

    def _write_manifest(self, manifest: ProjectManifest) -> None:
        rows = list(manifest.to_dict().items())
        with self._lock, self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO harita_meta(key, value) VALUES (%s, %s) "
                "ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
                rows,
            )

    def _read_manifest(self) -> ProjectManifest:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("SELECT key, value FROM harita_meta")
            rows = cur.fetchall()
        if not rows:
            raise ProjectFormatError("Proje veritabanında meta verisi yok")
        return ProjectManifest.from_dict(dict(rows))

    def read_manifest(self) -> ProjectManifest:
        return self._read_manifest()

    def update_manifest(self, **fields: Any) -> ProjectManifest:
        with self._lock:
            manifest = self._read_manifest()
            for key, value in fields.items():
                setattr(manifest, key, value)
            manifest.updated_at = time.time()
            self._write_manifest(manifest)
            self._conn.commit()
            return manifest

    # -- nesne CRUD ------------------------------------------------------

    def save_object(
        self, key: str, kind: str, data: Any,
        *, footprint: Optional[Iterable[tuple[float, float]]] = None,
    ) -> None:
        now = time.time()
        payload = json.dumps(data, ensure_ascii=False)
        wkt = footprint_to_wkt(footprint)
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO harita_objects(key, kind, data, footprint, updated_at) "
                "VALUES (%s, %s, %s::jsonb, "
                + ("ST_GeomFromText(%s, 4326)" if wkt is not None else "NULL") + ", %s) "
                "ON CONFLICT(key) DO UPDATE SET kind=EXCLUDED.kind, data=EXCLUDED.data, "
                "footprint=EXCLUDED.footprint, updated_at=EXCLUDED.updated_at",
                (key, kind, payload, *( (wkt,) if wkt is not None else () ), now),
            )
            cur.execute(
                "INSERT INTO harita_history(ts, op, key, kind) VALUES (%s, 'save', %s, %s)",
                (now, key, kind),
            )
            self._conn.commit()

    def save_many(self, records: Iterable[tuple[str, str, Any]]) -> int:
        rows = list(records)
        if not rows:
            return 0
        for key, kind, data in rows:
            self.save_object(key, kind, data)
        return len(rows)

    def load_object(self, key: str) -> Optional[ObjectRecord]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                "SELECT key, kind, data, updated_at FROM harita_objects WHERE key = %s",
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        k, kind, data, updated_at = row
        return ObjectRecord(key=k, kind=kind, data=data, updated_at=updated_at)

    def delete_object(self, key: str) -> bool:
        with self._lock, self._conn.cursor() as cur:
            cur.execute("SELECT kind FROM harita_objects WHERE key = %s", (key,))
            row = cur.fetchone()
            if row is None:
                return False
            kind = row[0]
            cur.execute("DELETE FROM harita_objects WHERE key = %s", (key,))
            cur.execute(
                "INSERT INTO harita_history(ts, op, key, kind) VALUES (%s, 'delete', %s, %s)",
                (time.time(), key, kind),
            )
            self._conn.commit()
            return True

    def list_objects(self, kind: Optional[str] = None) -> list[str]:
        with self._lock, self._conn.cursor() as cur:
            if kind is None:
                cur.execute("SELECT key FROM harita_objects ORDER BY key")
            else:
                cur.execute(
                    "SELECT key FROM harita_objects WHERE kind = %s ORDER BY key", (kind,)
                )
            return [r[0] for r in cur.fetchall()]

    def count_objects(self, kind: Optional[str] = None) -> int:
        with self._lock, self._conn.cursor() as cur:
            if kind is None:
                cur.execute("SELECT COUNT(*) FROM harita_objects")
            else:
                cur.execute("SELECT COUNT(*) FROM harita_objects WHERE kind = %s", (kind,))
            return int(cur.fetchone()[0])

    def iter_history(self, limit: int = 100) -> Iterator[tuple[float, str, str, str]]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                "SELECT ts, op, key, kind FROM harita_history ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            return iter(cur.fetchall())

    def vacuum(self) -> None:
        with self._lock, self._conn.cursor() as cur:
            self._conn.commit()
            old_autocommit = self._conn.autocommit
            self._conn.autocommit = True
            try:
                cur.execute("VACUUM")
            finally:
                self._conn.autocommit = old_autocommit

    # -- PostGIS'e özgü: gerçek coğrafi sorgu (roadmap Faz 5.2'nin özü) ---

    def query_bbox(
        self, *, min_lon: float, min_lat: float, max_lon: float, max_lat: float,
        kind: Optional[str] = None,
    ) -> list[ObjectRecord]:
        """Bir WGS84 bbox'ıyla kesişen tüm nesneleri PostGIS `&&` (bbox
        overlap) operatörüyle döndürür — SQLite backend'de bu sorgu türü
        mevcut değil (indekslenmiş coğrafi sorgu, roadmap'in PostGIS'i
        özellikle istemesinin nedeni)."""
        bbox_wkt = (
            f"POLYGON(({min_lon} {min_lat}, {max_lon} {min_lat}, "
            f"{max_lon} {max_lat}, {min_lon} {max_lat}, {min_lon} {min_lat}))"
        )
        sql = (
            "SELECT key, kind, data, updated_at FROM harita_objects "
            "WHERE footprint && ST_GeomFromText(%s, 4326)"
        )
        params: list[Any] = [bbox_wkt]
        if kind is not None:
            sql += " AND kind = %s"
            params.append(kind)
        with self._lock, self._conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [ObjectRecord(key=k, kind=kd, data=d, updated_at=u) for k, kd, d, u in rows]
