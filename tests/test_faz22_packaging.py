"""Faz 22 — paketleme regresyon testi.

Roadmap V2 Faz 22 (dokümantasyon/geliştirici deneyimi) denetimi sırasında
gerçek bir kurulum hatası bulundu: `pyproject.toml`'daki eski
``packages = { find = { include = ["harita*"] } }`` yalnızca "harita" ile
BAŞLAYAN dizin adlarını buluyordu (tek eşleşen: eski/iskelet
`harita_modelleme/`); gerçek 25+ modüllük kod tabanı (`core_engine`,
`persistence`, `app_shell`, ...) hiç paketlenmiyordu — yani
``pip install -e .`` çalışan koda erişim SAĞLAMIYORDU.

Düzeltme `setup.py`'a taşındı (bkz. o dosyadaki gerekçe). Bu test, gerçek
bir `pip install` çalıştırmadan (bu ortamda/CI'da yavaş ve gereksiz),
`setup.py`'ın ürettiği paket listesinin doğru olduğunu — beklenen
alt modüllerin `harita.` önekiyle bulunduğunu ve test/doküman/betik
dizinlerinin dışarıda bırakıldığını — doğrudan doğrular.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent


def _discover_packages() -> list[str]:
    """`setup.py`'ı gerçekten çalıştırmadan (setup() çağrısını yakalayıp)
    ürettiği `packages` listesini döndürür."""
    captured: dict[str, object] = {}

    def _fake_setup(**kwargs):
        captured.update(kwargs)

    with mock.patch("setuptools.setup", _fake_setup):
        runpy.run_path(str(REPO_ROOT / "setup.py"), run_name="__main__")

    assert "packages" in captured, "setup.py setuptools.setup() çağırmadı"
    return captured["packages"]  # type: ignore[return-value]


def test_root_harita_package_present():
    packages = _discover_packages()
    assert "harita" in packages


def test_real_submodules_are_discovered_with_prefix():
    packages = _discover_packages()
    # Roadmap V2'de "gerçek 25+ modül" olarak anılan çekirdek fazlardan bir
    # örnek küme — hepsi "harita." önekiyle bulunmalı.
    expected = {
        "harita.core_engine",
        "harita.persistence",
        "harita.app_shell",
        "harita.render_engine",
        "harita.building_reconstruction",
        "harita.building_reconstruction.footprint_parser",
        "harita.collaboration",
        "harita.export",
    }
    missing = expected - set(packages)
    assert not missing, f"Beklenen alt paketler eksik: {missing}"


def test_old_broken_prefix_only_match_is_not_the_only_result():
    """Regresyon kilidi: eski hata yalnızca 'harita_modelleme*' bulup
    gerçek kod tabanını atlıyordu. Artık en az bir düzine gerçek modül
    bulunmalı."""
    packages = _discover_packages()
    real_modules = [p for p in packages if p != "harita" and "harita_modelleme" not in p]
    assert len(real_modules) >= 12, (
        "Beklenenden az alt modül bulundu — paketleme yeniden bozulmuş "
        f"olabilir: {sorted(real_modules)}"
    )


def test_tests_docs_scripts_excluded():
    packages = _discover_packages()
    for pkg in packages:
        suffix = pkg.split(".")[-1]
        assert suffix not in {"tests", "scripts", "docs"}, (
            f"'{pkg}' paketlenmeye dahil edilmemeliydi (test/doküman/betik dizini)"
        )
