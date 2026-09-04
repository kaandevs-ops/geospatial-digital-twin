#!/usr/bin/env python3
"""Semver sürüm yükseltme betiği (stdlib-only).

`pyproject.toml`'daki `project.version` ile `harita/__init__.py`'deki
`__version__`'i tek kaynaktan senkron biçimde günceller, CHANGELOG.md'nin
`[Unreleased]` bölümünü yeni sürüm başlığına taşır.

Kullanım::

    python scripts/bump_version.py patch          # 0.15.0 -> 0.15.1
    python scripts/bump_version.py minor          # 0.15.0 -> 0.16.0
    python scripts/bump_version.py major          # 0.15.0 -> 1.0.0
    python scripts/bump_version.py 0.16.0-rc1      # açık sürüm belirt

CI/manuel akış: betik dosyaları günceller ve değişiklikleri commit etmez —
çağıran (geliştirici ya da release iş akışı) commit + `git tag vX.Y.Z` +
`git push --tags` adımlarını kendisi yürütür.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
# NOT (Roadmap V4/C5 düzeltmesi): paket kökü `ROOT/harita/__init__.py` DEĞİL,
# doğrudan `ROOT/__init__.py`'dir (setup.py paket keşfi bu dizini `harita`
# adıyla eşliyor). Eski yol asla var olmayan bir dosyayı gösteriyordu; bu
# script `--dry-run` olmadan çağrılsaydı `_update_init()` hata verirdi.
INIT_PY = ROOT / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

_SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<pre>-[0-9A-Za-z.\-]+)?$"
)


def _read_current_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml içinde `version = \"...\"` bulunamadı")
    return match.group(1)


def _next_version(current: str, bump: str) -> str:
    if bump in {"major", "minor", "patch"}:
        m = _SEMVER_RE.match(current)
        if not m:
            raise SystemExit(f"Geçersiz mevcut sürüm (semver değil): {current!r}")
        major, minor, patch = int(m["major"]), int(m["minor"]), int(m["patch"])
        if bump == "major":
            major, minor, patch = major + 1, 0, 0
        elif bump == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
        return f"{major}.{minor}.{patch}"
    # Açık sürüm belirtilmiş; biçimini doğrula.
    if not _SEMVER_RE.match(bump):
        raise SystemExit(f"Geçersiz sürüm biçimi: {bump!r} (beklenen: X.Y.Z[-pre])")
    return bump


def _update_pyproject(new_version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    updated = re.sub(
        r'^version = "[^"]+"', f'version = "{new_version}"', text, count=1, flags=re.MULTILINE
    )
    PYPROJECT.write_text(updated, encoding="utf-8")


def _update_init(new_version: str) -> None:
    text = INIT_PY.read_text(encoding="utf-8")
    if text.count('__version__ = "') == 0:
        raise SystemExit("harita/__init__.py içinde __version__ bulunamadı")
    updated = re.sub(
        r'__version__ = "[^"]+"', f'__version__ = "{new_version}"', text
    )
    INIT_PY.write_text(updated, encoding="utf-8")


def _read_init_version() -> str:
    text = INIT_PY.read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', text)
    if not match:
        raise SystemExit("__init__.py içinde __version__ bulunamadı")
    return match.group(1)


def _check_sync() -> int:
    """Roadmap V4 - Track C / C5: `pyproject.toml` ve `__init__.py`'deki
    sürüm string'lerinin aynı temel semver'e (MAJOR.MINOR.PATCH) karşılık
    geldiğini doğrular (tek kaynak: `pyproject.toml`). `__init__.py`
    tarafındaki bir ön-sürüm eki (örn. ``-phase17-a12-partial``) ek bilgi
    kabul edilir ve kontrol dışıdır.
    """
    pyproject_version = _read_current_version()
    init_version = _read_init_version()

    def _base(v: str) -> str:
        m = re.match(r"^(\d+\.\d+\.\d+)", v)
        return m.group(1) if m else v

    ok = _base(pyproject_version) == _base(init_version)
    print(f"pyproject.toml : {pyproject_version}")
    print(f"__init__.py    : {init_version}")
    if ok:
        print("Sürüm senkron. ✔")
        return 0
    print(
        "SÜRÜM UYUMSUZLUĞU: pyproject.toml ve __init__.py farklı temel "
        "semver taşıyor. `python scripts/bump_version.py --dry-run patch` "
        "ile mevcut durumu görebilir, gerçek bir bump ile senkronlayabilirsiniz."
    )
    return 1


def _update_changelog(new_version: str) -> None:
    if not CHANGELOG.exists():
        return
    text = CHANGELOG.read_text(encoding="utf-8")
    if "## [Unreleased]" not in text:
        return
    today = _dt.date.today().isoformat()
    heading = f"## [Unreleased]\n\n## [{new_version}] - {today}"
    updated = text.replace("## [Unreleased]", heading, 1)
    CHANGELOG.write_text(updated, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "bump", nargs="?", default=None,
        help="major | minor | patch veya açık bir X.Y.Z[-pre] sürümü",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Sadece yeni sürümü yazdır, dosyaları değiştirme"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Bump yapmadan pyproject.toml/__init__.py sürüm senkronunu doğrula (CI için)",
    )
    args = parser.parse_args(argv)

    if args.check:
        return _check_sync()

    if not args.bump:
        parser.error("bump argümanı gerekli (--check kullanılmıyorsa)")

    current = _read_current_version()
    new_version = _next_version(current, args.bump)

    if args.dry_run:
        print(f"{current} -> {new_version}")
        return 0

    _update_pyproject(new_version)
    _update_init(new_version)
    _update_changelog(new_version)

    print(f"Sürüm güncellendi: {current} -> {new_version}")
    print("Sonraki adım: git add -A && git commit -m 'chore: release "
          f"v{new_version}' && git tag v{new_version} && git push --tags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
