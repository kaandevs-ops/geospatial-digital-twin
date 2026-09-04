"""Roadmap V4 - Track C / C2: Merkezi Modül Kaydının Tamamlanması.

Denetim maddesi #2: `ModuleManager.DEFAULT_MODULES` yalnızca ilk 14 fazı
biliyordu; Faz 15/16/18/19 (`render_engine`, `persistence`, `app_shell`,
`collaboration`) hiç kayıtlı değildi.

Bu test dosyası:
1. `DEFAULT_MODULES` anahtar kümesinin, `harita/` altındaki tüm üst-düzey
   alt-paket dizinleriyle (dinamik keşif) birebir eşleştiğini doğrular -
   gelecekte yeni bir faz eklenip modül kaydı unutulursa kırmızı olur.
2. Daha önce eksik olan 4 modülün gerçekten `enable()` + lazy-import ile
   çalıştığını kanıtlar.
"""
from __future__ import annotations

import os
from pathlib import Path

import harita
from harita.extensibility import ModuleManager

REPO_ROOT = Path(harita.__file__).resolve().parent
EXCLUDED = {"harita_modelleme", "archive"}


def _discovered_subpackages() -> set[str]:
    result = set()
    for entry in os.listdir(REPO_ROOT):
        full = REPO_ROOT / entry
        if entry.startswith(".") or entry in EXCLUDED:
            continue
        if full.is_dir() and (full / "__init__.py").exists():
            result.add(entry)
    return result


def test_default_modules_matches_all_discovered_subpackages():
    discovered = _discovered_subpackages()
    registered = set(ModuleManager.DEFAULT_MODULES)
    missing_from_registry = discovered - registered
    stale_in_registry = registered - discovered
    assert not missing_from_registry, (
        f"Diskte bulunan ama ModuleManager.DEFAULT_MODULES'a kaydedilmemiş "
        f"alt paketler: {missing_from_registry}"
    )
    assert not stale_in_registry, (
        f"ModuleManager.DEFAULT_MODULES'da kayıtlı ama artık diskte olmayan "
        f"(silinmiş/taşınmış) modüller: {stale_in_registry}"
    )


def test_previously_missing_modules_enable_and_lazy_import_successfully():
    mm = ModuleManager()
    for name in ("render_engine", "persistence", "app_shell", "collaboration"):
        mm.enable(name)
        module = mm.get(name)
        assert module.__name__ == f"harita.{name}"


def test_disabled_module_raises_before_enable():
    from harita.extensibility.module_manager import ModuleNotEnabledError

    mm = ModuleManager()
    mm.disable("collaboration")
    try:
        mm.get("collaboration")
        assert False, "beklenmeyen: ModuleNotEnabledError fırlatılmadı"
    except ModuleNotEnabledError:
        pass
