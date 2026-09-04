"""Roadmap V2 - Faz 16 - Persistence & Proje Yönetimi testleri."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import sqlite3
import time

import pytest
from harita.persistence import (
    FORMAT_VERSION,
    MigrationError,
    ProjectAlreadyExistsError,
    ProjectDatabase,
    ProjectFormatError,
    ProjectManager,
    ProjectManifest,
    ProjectNotFoundError,
    migrate_schema,
)

# ---------------------------------------------------------------------------
# ProjectManifest
# ---------------------------------------------------------------------------


def test_manifest_roundtrip_dict():
    m = ProjectManifest(name="Ankara Kızılay", project_id="p1", description="test", tags=("a", "b"))
    d = m.to_dict()
    m2 = ProjectManifest.from_dict(d)
    assert m2.name == m.name
    assert m2.project_id == m.project_id
    assert m2.tags == ("a", "b")
    assert m2.format_version == FORMAT_VERSION


def test_manifest_from_dict_bozuk_veri_hata_verir():
    with pytest.raises(ProjectFormatError):
        ProjectManifest.from_dict({"name": "x"})  # project_id eksik


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migrate_schema_same_version_noop():
    conn = sqlite3.connect(":memory:")
    result = migrate_schema(conn, FORMAT_VERSION, FORMAT_VERSION)
    assert result == FORMAT_VERSION


def test_migrate_schema_unknown_step_raises():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(MigrationError):
        migrate_schema(conn, 5, 99)


def test_migrate_schema_backwards_raises():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(MigrationError):
        migrate_schema(conn, 2, 1)


def test_migrate_0_to_1_creates_meta_table():
    conn = sqlite3.connect(":memory:")
    migrate_schema(conn, 0, 1)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    assert row is not None


# ---------------------------------------------------------------------------
# ProjectDatabase
# ---------------------------------------------------------------------------


def test_create_and_open_project(tmp_path):
    path = tmp_path / "sehir.hproj"
    manifest = ProjectManifest(name="Sehir", project_id="p1")
    db = ProjectDatabase.create(path, manifest)
    db.save_object("bina_1", "mesh3d", {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]})
    db.close()

    assert path.exists()

    db2 = ProjectDatabase.open(path)
    rec = db2.load_object("bina_1")
    assert rec is not None
    assert rec.kind == "mesh3d"
    assert rec.data["vertices"][1] == [1, 0, 0]
    db2.close()


def test_create_fails_if_exists(tmp_path):
    path = tmp_path / "sehir.hproj"
    ProjectDatabase.create(path, ProjectManifest(name="A", project_id="p1")).close()
    with pytest.raises(FileExistsError):
        ProjectDatabase.create(path, ProjectManifest(name="B", project_id="p2"))


def test_open_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ProjectDatabase.open(tmp_path / "yok.hproj")


def test_open_invalid_sqlite_file_raises(tmp_path):
    path = tmp_path / "bozuk.hproj"
    path.write_text("bu bir sqlite dosyası değil")
    with pytest.raises(Exception):
        # Ya ProjectFormatError ya da sqlite3 seviyesinde bir hata olabilir;
        # her koşulda sessizce yanlış bir proje açmamalı.
        ProjectDatabase.open(path)


def test_save_update_delete_object(tmp_path):
    db = ProjectDatabase.create(tmp_path / "p.hproj", ProjectManifest(name="P", project_id="p1"))
    db.save_object("k1", "mesh3d", {"v": 1})
    db.save_object("k1", "mesh3d", {"v": 2})  # update
    assert db.load_object("k1").data == {"v": 2}
    assert db.delete_object("k1") is True
    assert db.load_object("k1") is None
    assert db.delete_object("k1") is False
    db.close()


def test_list_and_count_objects_by_kind(tmp_path):
    db = ProjectDatabase.create(tmp_path / "p.hproj", ProjectManifest(name="P", project_id="p1"))
    db.save_object("b1", "mesh3d", {})
    db.save_object("b2", "mesh3d", {})
    db.save_object("t1", "twin", {})
    assert sorted(db.list_objects()) == ["b1", "b2", "t1"]
    assert sorted(db.list_objects("mesh3d")) == ["b1", "b2"]
    assert db.count_objects() == 3
    assert db.count_objects("twin") == 1
    db.close()


def test_save_many_bulk_insert(tmp_path):
    db = ProjectDatabase.create(tmp_path / "p.hproj", ProjectManifest(name="P", project_id="p1"))
    records = [(f"obj_{i}", "mesh3d", {"i": i}) for i in range(500)]
    written = db.save_many(records)
    assert written == 500
    assert db.count_objects() == 500
    assert db.load_object("obj_499").data == {"i": 499}
    db.close()


def test_history_log_append_only(tmp_path):
    db = ProjectDatabase.create(tmp_path / "p.hproj", ProjectManifest(name="P", project_id="p1"))
    db.save_object("k1", "mesh3d", {})
    db.save_object("k1", "mesh3d", {"v": 2})
    db.delete_object("k1")
    history = list(db.iter_history())
    ops = [h[1] for h in history]
    assert ops == ["delete", "save", "save"]  # en yeni önce
    db.close()


def test_update_manifest(tmp_path):
    db = ProjectDatabase.create(tmp_path / "p.hproj", ProjectManifest(name="P", project_id="p1"))
    old_updated = db.read_manifest().updated_at
    time.sleep(0.01)
    m = db.update_manifest(description="yeni açıklama")
    assert m.description == "yeni açıklama"
    assert m.updated_at > old_updated
    db.close()


def test_auto_migrate_on_open_bumps_version(tmp_path):
    path = tmp_path / "eski.hproj"
    db = ProjectDatabase.create(path, ProjectManifest(name="Eski", project_id="p1"))
    # Şemayı elle "eski sürüm" gibi işaretleyelim.
    db.update_manifest(format_version=0)
    db.close()

    db2 = ProjectDatabase.open(path, auto_migrate=True)
    assert db2.read_manifest().format_version == FORMAT_VERSION
    db2.close()


def test_open_refuses_old_version_without_auto_migrate(tmp_path):
    path = tmp_path / "eski.hproj"
    db = ProjectDatabase.create(path, ProjectManifest(name="Eski", project_id="p1"))
    db.update_manifest(format_version=0)
    db.close()

    with pytest.raises(ProjectFormatError):
        ProjectDatabase.open(path, auto_migrate=False)


def test_vacuum_does_not_raise(tmp_path):
    db = ProjectDatabase.create(tmp_path / "p.hproj", ProjectManifest(name="P", project_id="p1"))
    db.save_object("k1", "mesh3d", {"v": 1})
    db.delete_object("k1")
    db.vacuum()  # patlamamalı
    db.close()


def test_context_manager_closes(tmp_path):
    path = tmp_path / "p.hproj"
    with ProjectDatabase.create(path, ProjectManifest(name="P", project_id="p1")) as db:
        db.save_object("k1", "mesh3d", {"v": 1})
    # Kapandıktan sonra tekrar açılabilmeli (dosya kilitli kalmamalı).
    db2 = ProjectDatabase.open(path)
    assert db2.load_object("k1").data == {"v": 1}
    db2.close()


# ---------------------------------------------------------------------------
# ProjectManager
# ---------------------------------------------------------------------------


def test_manager_create_and_open_project(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    handle = mgr.create_project(tmp_path / "kizilay.hproj", "Kızılay", "p1")
    handle.db.save_object("bina_1", "mesh3d", {"v": 1})
    mgr.close_project("p1")

    mgr2 = ProjectManager(tmp_path / "registry.hprojreg")
    handle2 = mgr2.open_project("p1")
    assert handle2.manifest.name == "Kızılay"
    assert handle2.db.load_object("bina_1").data == {"v": 1}
    mgr2.close()


def test_manager_create_duplicate_raises(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    mgr.create_project(tmp_path / "a.hproj", "A", "p1")
    with pytest.raises(ProjectAlreadyExistsError):
        mgr.create_project(tmp_path / "b.hproj", "B", "p1")
    mgr.close()


def test_manager_open_unknown_project_raises(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    with pytest.raises(ProjectNotFoundError):
        mgr.open_project("bilinmeyen")
    mgr.close()


def test_manager_open_by_path_registers(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    handle = mgr.create_project(tmp_path / "a.hproj", "A", "p1")
    mgr.close_project("p1")

    mgr2 = ProjectManager(tmp_path / "registry.hprojreg")
    handle2 = mgr2.open_project(path=tmp_path / "a.hproj")
    assert handle2.manifest.project_id == "p1"
    mgr2.close()


def test_manager_list_recent_ordered(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    mgr.create_project(tmp_path / "a.hproj", "A", "p1")
    time.sleep(0.01)
    mgr.create_project(tmp_path / "b.hproj", "B", "p2")
    recent = mgr.list_recent()
    assert recent[0]["project_id"] == "p2"
    assert recent[1]["project_id"] == "p1"
    mgr.close()


def test_manager_forget_project(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    mgr.create_project(tmp_path / "a.hproj", "A", "p1")
    assert mgr.forget_project("p1") is True
    assert mgr.forget_project("p1") is False
    with pytest.raises(ProjectNotFoundError):
        mgr.open_project("p1")
    mgr.close()


def test_manager_reopen_same_handle_from_cache(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    h1 = mgr.create_project(tmp_path / "a.hproj", "A", "p1")
    h2 = mgr.open_project("p1")
    assert h1 is h2
    mgr.close()


def test_manager_context_manager_closes_all(tmp_path):
    path = tmp_path / "a.hproj"
    with ProjectManager(tmp_path / "registry.hprojreg") as mgr:
        mgr.create_project(path, "A", "p1")
    # dosya kilitli kalmamalı; tekrar açılabilmeli
    db = ProjectDatabase.open(path)
    db.close()


# ---------------------------------------------------------------------------
# ProjectHandle autosave
# ---------------------------------------------------------------------------


def test_handle_autosave_triggers_after_interval(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    handle = mgr.create_project(tmp_path / "a.hproj", "A", "p1")
    handle.autosave_interval_s = 0.05
    handle.mark_dirty()

    assert handle.maybe_autosave(now=time.time()) is False  # henüz zaman dolmadı
    time.sleep(0.06)
    assert handle.maybe_autosave() is True
    assert handle.maybe_autosave() is False  # zaten kaydedildi, dirty değil
    mgr.close()


def test_handle_close_flushes_dirty_state(tmp_path):
    mgr = ProjectManager(tmp_path / "registry.hprojreg")
    handle = mgr.create_project(tmp_path / "a.hproj", "A", "p1")
    handle.mark_dirty()
    before = handle.manifest.updated_at
    handle.close()
    # close sonrası bağlantı kapalı; tekrar açıp doğrulayalım
    db = ProjectDatabase.open(tmp_path / "a.hproj")
    assert db.read_manifest().updated_at >= before
    db.close()
    mgr._open_handles.pop("p1", None)  # zaten kapandı, manager.close ile çakışmasın
    mgr.close()


# ---------------------------------------------------------------------------
# Ölçek / performans (Kabul kriteri: büyük projede hızlı açma/kaydetme)
# ---------------------------------------------------------------------------


def test_bulk_save_and_reopen_performance(tmp_path):
    """Kabul kriterinin küçültülmüş regresyon testi: 1GB/5sn yerine, 5.000
    nesnelik bir sahnenin açılıp toplu kaydedilmesinin ve tekrar açılıp
    okunmasının makul bir sürede (birkaç saniye) bittiğini doğrular. Gerçek
    1GB'lık uçtan uca ölçüm, `docs/PHASE_SPECS.md` içinde ayrı bir manuel
    benchmark script'i olarak belgelenir (CI'da her PR'da koşmak pahalıdır).
    """
    path = tmp_path / "buyuk.hproj"
    db = ProjectDatabase.create(path, ProjectManifest(name="Büyük Şehir", project_id="p1"))

    n = 5000
    start = time.perf_counter()
    records = [
        (f"bina_{i}", "mesh3d", {"i": i, "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, i]]})
        for i in range(n)
    ]
    db.save_many(records)
    save_elapsed = time.perf_counter() - start
    db.close()

    start = time.perf_counter()
    db2 = ProjectDatabase.open(path)
    assert db2.count_objects() == n
    open_elapsed = time.perf_counter() - start
    db2.close()

    assert save_elapsed < 5.0, f"toplu kayıt çok yavaş: {save_elapsed:.2f}s"
    assert open_elapsed < 5.0, f"açma çok yavaş: {open_elapsed:.2f}s"
