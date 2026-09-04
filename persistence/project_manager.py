"""
Proje Yöneticisi
================

`ProjectDatabase` tek bir `.hproj` dosyasını yönetir; `ProjectManager` ise
**birden çok** projeyi (aç/kaydet/oto-kaydet/son kullanılanlar) tek bir
kullanıcı/uygulama oturumu seviyesinde yönetir.

- Bir "registry" (kendi de küçük bir SQLite dosyası, varsayılan
  `~/.harita/registry.hprojreg` benzeri bir yol; testlerde geçici dizin
  kullanılır) tüm bilinen projelerin yolunu + son açılma zamanını tutar.
- `ProjectHandle`, açık bir projeyi (manifest + `ProjectDatabase`) sarar ve
  oto-kaydet (autosave) zamanlamasını (harici bir olay döngüsüne bağlı
  olmadan, `maybe_autosave()` çağrısıyla tetiklenen basit bir zaman
  damgası karşılaştırması) takip eder.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db_backend import ProjectDatabase
from .project_format import ProjectManifest

#: ROADMAP_V9 Faz X / Katman 8 madde 2 — dallanan (branched) bir projenin
#: manifest `tags` alanında taşıdığı, ebeveyn proje kimliğini kodlayan
#: etiket öneki. Yeni bir tablo/şema icat edilmedi (roadmap ilkesi #2) —
#: mevcut `ProjectManifest.tags` (zaten var, `meta` tablosunda saklanan bir
#: string tuple) yeniden kullanıldı.
_BRANCH_TAG_PREFIX = "branched_from:"


class BranchNotFoundError(Exception):
    """İstenen dal (branch) registry'de bulunamadı."""


class ProjectNotFoundError(Exception):
    """Registry'de veya diskte istenen proje bulunamadı."""


class ProjectAlreadyExistsError(Exception):
    """Aynı yolda/isimde proje zaten var."""


@dataclass
class ProjectHandle:
    """Açık (bellekte tutulan) bir projenin tanıtıcısı."""

    db: ProjectDatabase
    manifest: ProjectManifest
    autosave_interval_s: float = 60.0
    _last_saved_at: float = field(default_factory=time.time)
    _dirty: bool = False

    @property
    def path(self) -> Path:
        return self.db.path

    def mark_dirty(self) -> None:
        """Bellekteki bir değişiklik yapıldığında (obje eklendi/silindi vs.)
        çağrılır; `maybe_autosave` bunu görene kadar oto-kaydetmeyi tetikler.
        """
        self._dirty = True

    def save_now(self) -> None:
        """Manifest güncelleme zamanını yeniler ve durumu 'temiz' işaretler.

        Nesne verisi zaten `ProjectDatabase.save_object` her çağrıldığında
        diske yazılır (autocommit); bu metod yalnızca proje meta bilgisini
        (updated_at) tazeler ve autosave sayaç durumunu sıfırlar.
        """
        self.db.update_manifest(updated_at=time.time())
        self.manifest = self.db.read_manifest()
        self._last_saved_at = time.time()
        self._dirty = False

    def maybe_autosave(self, now: float | None = None) -> bool:
        """Autosave aralığı dolmuşsa ve değişiklik varsa kaydeder.

        Dönüş: kaydetme gerçekleştiyse `True`.
        """
        now = time.time() if now is None else now
        if self._dirty and (now - self._last_saved_at) >= self.autosave_interval_s:
            self.save_now()
            return True
        return False

    def close(self) -> None:
        if self._dirty:
            self.save_now()
        self.db.close()


class ProjectManager:
    """Birden çok `.hproj` projesini yöneten üst katman.

    Registry, kendi SQLite dosyasında (`registry_path`) şu tabloyu tutar:
        projects(project_id TEXT PRIMARY KEY, name TEXT, path TEXT,
                 last_opened_at REAL)
    """

    def __init__(self, registry_path: str | Path) -> None:
        self.registry_path = Path(registry_path)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        # Faz 21 güvenlik/ölçek sertleştirmesi: `app_shell.server`
        # `ThreadingHTTPServer` kullanır (her istek kendi thread'inde
        # işlenir), ama bu bağlantı `ProjectManager`/`AppSession`
        # oluşturulduğu (ana) thread'de açılır. sqlite3'ün varsayılan
        # `check_same_thread=True` davranışıyla, başka bir thread'den gelen
        # ilk yazma isteği "SQLite objects created in a thread can only be
        # used in that same thread" hatasıyla patlar — bu gerçek bir
        # regresyondu (bkz. `tests/test_phase21_scale_security.py`), REST
        # API üzerinden hiçbir proje-değiştiren istek çalışmıyordu.
        # `check_same_thread=False` + `_lock` (bkz. aşağıda) ile düzeltildi.
        self._reg = sqlite3.connect(str(self.registry_path), check_same_thread=False)
        self._lock = threading.RLock()
        self._reg.execute(
            "CREATE TABLE IF NOT EXISTS projects ("
            "project_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "path TEXT NOT NULL, last_opened_at REAL NOT NULL)"
        )
        self._reg.commit()
        self._open_handles: dict[str, ProjectHandle] = {}

    def close(self) -> None:
        for handle in list(self._open_handles.values()):
            handle.close()
        self._open_handles.clear()
        with self._lock:
            self._reg.commit()
            self._reg.close()

    def __enter__(self) -> ProjectManager:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- proje oluşturma / açma ------------------------------------------

    def create_project(
        self, path: str | Path, name: str, project_id: str, **manifest_fields: Any
    ) -> ProjectHandle:
        path = Path(path)
        with self._lock:
            existing = self._reg.execute(
                "SELECT project_id FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if existing is not None or path.exists():
                raise ProjectAlreadyExistsError(
                    f"Proje zaten var: project_id={project_id} path={path}"
                )
            manifest = ProjectManifest(name=name, project_id=project_id, **manifest_fields)
            db = ProjectDatabase.create(path, manifest)
            self._register(manifest, path)
            handle = ProjectHandle(db=db, manifest=manifest)
            self._open_handles[project_id] = handle
            return handle

    def open_project(
        self, project_id: str | None = None, *, path: str | Path | None = None
    ) -> ProjectHandle:
        """`project_id` (registry üzerinden) veya doğrudan `path` ile açar."""
        if project_id is not None and project_id in self._open_handles:
            return self._open_handles[project_id]

        if path is None:
            if project_id is None:
                raise ValueError("project_id veya path verilmeli")
            with self._lock:
                row = self._reg.execute(
                    "SELECT path FROM projects WHERE project_id = ?", (project_id,)
                ).fetchone()
            if row is None:
                raise ProjectNotFoundError(f"Registry'de bulunamadı: {project_id}")
            path = row[0]

        db = ProjectDatabase.open(path)
        manifest = db.read_manifest()
        self._register(manifest, Path(path))
        handle = ProjectHandle(db=db, manifest=manifest)
        self._open_handles[manifest.project_id] = handle
        return handle

    def close_project(self, project_id: str) -> None:
        handle = self._open_handles.pop(project_id, None)
        if handle is not None:
            handle.close()

    # -- registry sorguları ------------------------------------------

    def _register(self, manifest: ProjectManifest, path: Path) -> None:
        with self._lock:
            self._reg.execute(
                "INSERT INTO projects(project_id, name, path, last_opened_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET "
                "name=excluded.name, path=excluded.path, last_opened_at=excluded.last_opened_at",
                (manifest.project_id, manifest.name, str(path), time.time()),
            )
            self._reg.commit()

    def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._reg.execute(
                "SELECT project_id, name, path, last_opened_at FROM projects "
                "ORDER BY last_opened_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"project_id": r[0], "name": r[1], "path": r[2], "last_opened_at": r[3]} for r in rows
        ]

    def forget_project(self, project_id: str) -> bool:
        """Registry kaydını siler (diskteki `.hproj` dosyasına dokunmaz)."""
        self.close_project(project_id)
        with self._lock:
            cur = self._reg.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            self._reg.commit()
            return cur.rowcount > 0

    # -- senaryo dallanması (branching) ---------------------------------- #
    # ROADMAP_V9 Faz X / Katman 8 madde 2: "Bu mahallede 3 kat daha yüksek
    # bina yapılırsa senaryosu ana ikizi bozmadan bir dal olarak çalıştırılır,
    # sonuçlar karşılaştırılır, istenirse ana hatta birleştirilir (git benzeri
    # mantık)". Yeni bir versiyonlama motoru yazılmadı (roadmap ilkesi #2) —
    # `.hproj` zaten bağımsız bir SQLite dosyası olduğundan, bir dal basitçe
    # dosyanın diskte kopyalanmasıdır; kopya kendi `history`/versiyonlama
    # günlüğüyle (`db_backend.iter_history`) bağımsız yaşamaya devam eder.

    def create_branch(
        self,
        source_project_id: str,
        branch_name: str,
        *,
        branch_project_id: str | None = None,
        branch_dir: str | Path | None = None,
    ) -> ProjectHandle:
        """`source_project_id` projesinin bağımsız bir dalını (kopyasını)
        oluşturur. Ana proje bu işlemden **etkilenmez** — dal ayrı bir
        `.hproj` dosyasıdır, ayrı bir `project_id` taşır.

        Dönüş: yeni dalın açık `ProjectHandle`'ı.
        """
        source_handle = self.open_project(source_project_id)
        source_path = source_handle.path
        # Kapanmamış bağlantıdan tutarlı bir kopya almak için önce diske
        # yazılmamış değişiklikler autosave ile boşaltılır.
        source_handle.save_now()

        new_project_id = branch_project_id or f"{source_project_id}-branch-{uuid.uuid4().hex[:8]}"
        target_dir = Path(branch_dir) if branch_dir is not None else source_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{new_project_id}.hproj"
        if target_path.exists():
            raise ProjectAlreadyExistsError(f"Dal yolu zaten var: {target_path}")

        shutil.copyfile(source_path, target_path)

        branch_db = ProjectDatabase.open(target_path)
        parent_manifest = source_handle.manifest
        branch_db.update_manifest(
            name=branch_name,
            project_id=new_project_id,
            tags=tuple(parent_manifest.tags) + (f"{_BRANCH_TAG_PREFIX}{source_project_id}",),
        )
        # `project_id` `meta` tablosunda tutuluyor ama şu ana kadar açılan
        # `ProjectDatabase._read_manifest`'in kendi PRIMARY KEY'i `key`
        # sütunudur — `update_manifest` zaten doğru satırı günceller,
        # burada yalnızca döndürülen manifesti registry'ye kaydediyoruz.
        branch_manifest = branch_db.read_manifest()
        self._register(branch_manifest, target_path)
        handle = ProjectHandle(db=branch_db, manifest=branch_manifest)
        self._open_handles[new_project_id] = handle
        return handle

    def list_branches(self, source_project_id: str) -> list[dict[str, Any]]:
        """`source_project_id`'den türetilmiş tüm dalları (registry'deki tüm
        projeler taranarak, `tags` alanındaki `branched_from:` etiketi
        eşleştirilerek) döndürür."""
        tag = f"{_BRANCH_TAG_PREFIX}{source_project_id}"
        results: list[dict[str, Any]] = []
        for row in self.list_recent(limit=10_000):
            pid = row["project_id"]
            if pid == source_project_id:
                continue
            try:
                handle = self.open_project(pid)
            except ProjectNotFoundError:
                continue
            if tag in handle.manifest.tags:
                results.append(row)
        return results

    def merge_branch(
        self,
        branch_project_id: str,
        into_project_id: str,
        *,
        kinds: tuple[str, ...] | None = None,
    ) -> int:
        """Bir dalın nesnelerini ana hatta (veya başka bir dala) birleştirir.

        Roadmap'in "istenirse ana hatta birleştirilir" maddesi — basit,
        şeffaf bir strateji: dal içindeki (istenirse `kinds` ile
        filtrelenmiş) nesneler `save_object` ile hedefe **son-yazan-kazanır**
        (last-write-wins) mantığıyla kopyalanır; hedefteki ilgisiz nesnelere
        dokunulmaz, silme işlemi yayılmaz (bilinçli, geri alınabilir kapsam
        sınırı — tam CRDT-stili üç-yönlü birleştirme `collaboration/crdt.py`
        alanına girer ve burada yeniden icat edilmedi).
        """
        branch_handle = self.open_project(branch_project_id)
        target_handle = self.open_project(into_project_id)

        merged = 0
        for key in branch_handle.db.list_objects():
            record = branch_handle.db.load_object(key)
            if record is None:
                continue
            if kinds is not None and record.kind not in kinds:
                continue
            target_handle.db.save_object(key, record.kind, record.data)
            merged += 1
        if merged:
            target_handle.mark_dirty()
            target_handle.save_now()
        return merged
