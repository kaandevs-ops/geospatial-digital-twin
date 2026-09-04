"""Kullanıcı talebi madde 3: manifest scripti testleri.

Gerçek bir proje diskte oluşturulur, gerçek bir bina eklenir, `AppSession.
export_manifest()` gerçek dosyalara export eder ve gerçek bir `manifest.json`
yazar; ayrıca `scripts/generate_manifest.py`'nin `main()` fonksiyonu (aynı
process içinde, alt-process açmadan) uçtan uca çağrılıp gerçek çıktı/çıkış
kodu doğrulanır.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harita.app_shell.session import AppSession  # noqa: E402
from harita.export.manifest import load_manifest, verify_manifest_checksums  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_manifest  # noqa: E402


_PASS = 0
_FAIL = 0


def check(name: str, condition: bool) -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"FAIL: {name}")


def _make_project(tmp_dir: Path) -> tuple[Path, str]:
    project_path = tmp_dir / "test_project.harita"
    registry_path = tmp_dir / ".harita_registry.json"
    session = AppSession(registry_path)
    try:
        info = session.create_project("Test Projesi", project_path)
        project_id = info["project_id"]
        session.add_building(
            project_id,
            [(0.0, 0.0), (20.0, 0.0), (20.0, 15.0), (0.0, 15.0)],
            building_type="apartman", floor_count=4, height_m=12.0, seed=7,
        )
        session.save_project(project_id)
    finally:
        session.close()
    return project_path, project_id


def test_export_manifest_writes_real_files_and_checksums():
    tmp_dir = Path(tempfile.mkdtemp(prefix="harita_manifest_test_"))
    try:
        project_path, project_id = _make_project(tmp_dir)
        registry_path = tmp_dir / ".harita_registry.json"
        session = AppSession(registry_path)
        try:
            session.open_project(path=project_path)
            result = session.export_manifest(project_id, ["obj", "gltf"])
        finally:
            session.close()

        manifest_path = Path(result["manifest_path"])
        check("manifest file exists on disk", manifest_path.is_file())
        data = load_manifest(manifest_path)
        check("manifest building_count == 1", data["building_count"] == 1)
        check("manifest has 2 files", len(data["files"]) == 2)
        check("manifest files have sha256", all(f.get("sha256") for f in data["files"]))
        for f in data["files"]:
            check(f"exported file exists: {f['format']}", Path(f["path"]).exists())

        mismatches = verify_manifest_checksums(manifest_path)
        check("no checksum mismatches right after export", mismatches == [])

        # Dosyayı boz, checksum uyumsuzluğu tespit edilsin mi diye kontrol et.
        target = Path(data["files"][0]["path"])
        original = target.read_bytes()
        target.write_bytes(original + b"BOZULDU")
        mismatches_after = verify_manifest_checksums(manifest_path)
        check("checksum mismatch detected after corruption", str(target) in mismatches_after)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_cli_script_end_to_end():
    tmp_dir = Path(tempfile.mkdtemp(prefix="harita_manifest_cli_"))
    try:
        project_path, _ = _make_project(tmp_dir)
        exit_code = generate_manifest.main([
            str(project_path), "--format", "obj", "--format", "stl",
        ])
        check("cli exits 0", exit_code == 0)
        manifest_path = project_path.parent / "exports" / "manifest.json"
        check("cli produced manifest.json", manifest_path.is_file())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        check("cli manifest has 2 files", len(data["files"]) == 2)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_cli_script_verify_mode():
    tmp_dir = Path(tempfile.mkdtemp(prefix="harita_manifest_cli_verify_"))
    try:
        project_path, _ = _make_project(tmp_dir)
        generate_manifest.main([str(project_path), "--format", "obj"])
        manifest_path = project_path.parent / "exports" / "manifest.json"
        exit_code = generate_manifest.main(["--verify", str(manifest_path)])
        check("cli verify mode exits 0 on clean manifest", exit_code == 0)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_cli_script_missing_project_file():
    exit_code = generate_manifest.main(["/nonexistent/path/x.harita", "--format", "obj"])
    check("cli exits 1 for missing project", exit_code == 1)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n{_PASS} passed, {_FAIL} failed (of {_PASS + _FAIL})")
    if _FAIL:
        raise SystemExit(1)
