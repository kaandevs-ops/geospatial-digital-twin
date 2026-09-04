"""
Kalıcı Disk Backend (SQLite)
============================

`data_engine.cache.ObjectCache`/`SceneCache` bellek-içi çalışır; uygulama
kapandığında içerik kaybolur. `ProjectDatabase`, aynı "anahtar -> nesne"
modelini **diske** (tek bir `.hproj` SQLite dosyasına) kalıcı olarak yazar.

Şema:
    meta      (key TEXT PRIMARY KEY, value TEXT)
    objects   (key TEXT PRIMARY KEY, kind TEXT, data TEXT, updated_at REAL)
    history   (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, op TEXT,
               key TEXT, kind TEXT)   -- append-only değişiklik günlüğü

`data` sütunu JSON metni olarak saklanır (stdlib `json`); çağıran taraf
(genelde `data_engine`/`digital_twin`/`export` nesneleri) serileştirmeyi
kendi `to_dict()`/`from_dict()` sözleşmesiyle yapar — bu katman formatı
zorlamaz, yalnızca metin olarak taşır.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project_format import (
    FORMAT_VERSION,
    ProjectFormatError,
    ProjectManifest,
    migrate_schema,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS objects (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_kind ON objects(kind);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    op TEXT NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ObjectRecord:
    """`objects` tablosundan okunan tek bir satır (deserialize edilmiş)."""

    key: str
    kind: str
    data: Any
    updated_at: float


class ProjectDatabase:
    """Tek bir `.hproj` SQLite dosyasını saran kalıcılık katmanı.

    Kullanım:
        db = ProjectDatabase.create("sehir.hproj", ProjectManifest(name="Şehir", project_id="p1"))
        db.save_object("bina_42", "mesh3d", {"vertices": [...]})
        db.close()

        db2 = ProjectDatabase.open("sehir.hproj")
        rec = db2.load_object("bina_42")
    """

    def __init__(self, path: Path, conn: sqlite3.Connection) -> None:
        self.path = path
        self._conn = conn
        # Faz 21 sertleştirmesi: bkz. project_manager.py ProjectManager.__init__
        # docstring'i — aynı gerekçeyle bu bağlantı da çoklu-thread erişime
        # (ThreadingHTTPServer) karşı kilitle korunuyor.
        self._lock = threading.RLock()

    # -- yaşam döngüsü -----------------------------------------------

    @classmethod
    def create(cls, path: str | Path, manifest: ProjectManifest) -> ProjectDatabase:
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"Proje dosyası zaten var: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.executescript(_SCHEMA)
        db = cls(path, conn)
        db._write_manifest(manifest)
        conn.commit()
        return db

    @classmethod
    def open(cls, path: str | Path, *, auto_migrate: bool = True) -> ProjectDatabase:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Proje dosyası bulunamadı: {path}")
        conn = sqlite3.connect(str(path), check_same_thread=False)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'")
        if cur.fetchone() is None:
            raise ProjectFormatError(f"Geçersiz/tanınmayan .hproj dosyası: {path}")
        db = cls(path, conn)
        manifest = db._read_manifest()
        if manifest.format_version != FORMAT_VERSION:
            if not auto_migrate:
                raise ProjectFormatError(
                    f"Proje dosyası v{manifest.format_version}, beklenen v{FORMAT_VERSION} "
                    "(auto_migrate=False)"
                )
            migrate_schema(conn, manifest.format_version, FORMAT_VERSION)
            manifest.format_version = FORMAT_VERSION
            db._write_manifest(manifest)
            conn.commit()
        return db

    def close(self) -> None:
        with self._lock:
            self._conn.commit()
            self._conn.close()

    def __enter__(self) -> ProjectDatabase:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- manifest ------------------------------------------------------

    def _write_manifest(self, manifest: ProjectManifest) -> None:
        rows = manifest.to_dict().items()
        with self._lock:
            self._conn.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                list(rows),
            )

    def _read_manifest(self) -> ProjectManifest:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM meta").fetchall()
        if not rows:
            raise ProjectFormatError("Proje dosyasında meta verisi yok")
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

    def save_object(self, key: str, kind: str, data: Any) -> None:
        now = time.time()
        payload = json.dumps(data, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "INSERT INTO objects(key, kind, data, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, data=excluded.data, "
                "updated_at=excluded.updated_at",
                (key, kind, payload, now),
            )
            self._conn.execute(
                "INSERT INTO history(ts, op, key, kind) VALUES (?, 'save', ?, ?)",
                (now, key, kind),
            )
            self._conn.commit()

    def save_many(self, records: Iterable[tuple[str, str, Any]]) -> int:
        """Toplu kayıt: büyük sahneler için tek transaction'da yazar.

        `records`: (key, kind, data) üçlüleri. Dönüş: yazılan kayıt sayısı.
        """
        now = time.time()
        rows = [(k, kind, json.dumps(data, ensure_ascii=False), now) for k, kind, data in records]
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO objects(key, kind, data, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET kind=excluded.kind, data=excluded.data, "
                "updated_at=excluded.updated_at",
                rows,
            )
            self._conn.executemany(
                "INSERT INTO history(ts, op, key, kind) VALUES (?, 'save', ?, ?)",
                [(now, r[0], r[1]) for r in rows],
            )
            self._conn.commit()
        return len(rows)

    def load_object(self, key: str) -> ObjectRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT key, kind, data, updated_at FROM objects WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        k, kind, data, updated_at = row
        return ObjectRecord(key=k, kind=kind, data=json.loads(data), updated_at=updated_at)

    def delete_object(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("SELECT kind FROM objects WHERE key = ?", (key,))
            row = cur.fetchone()
            if row is None:
                return False
            kind = row[0]
            self._conn.execute("DELETE FROM objects WHERE key = ?", (key,))
            self._conn.execute(
                "INSERT INTO history(ts, op, key, kind) VALUES (?, 'delete', ?, ?)",
                (time.time(), key, kind),
            )
            self._conn.commit()
            return True

    def list_objects(self, kind: str | None = None) -> list[str]:
        with self._lock:
            if kind is None:
                rows = self._conn.execute("SELECT key FROM objects ORDER BY key").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT key FROM objects WHERE kind = ? ORDER BY key", (kind,)
                ).fetchall()
        return [r[0] for r in rows]

    def count_objects(self, kind: str | None = None) -> int:
        with self._lock:
            if kind is None:
                row = self._conn.execute("SELECT COUNT(*) FROM objects").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) FROM objects WHERE kind = ?", (kind,)
                ).fetchone()
        return int(row[0])

    def iter_history(self, limit: int = 100) -> Iterator[tuple[float, str, str, str]]:
        """En yeni değişikliklerden başlayarak (ts, op, key, kind) döndürür."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, op, key, kind FROM history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return iter(rows)

    def vacuum(self) -> None:
        """Silinen kayıtların bıraktığı boş alanı geri kazanır (bakım işlemi)."""
        with self._lock:
            self._conn.commit()
            self._conn.execute("VACUUM")
