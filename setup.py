"""Paket keşfi ve önekleme (bkz. pyproject.toml [tool.setuptools] notu).

Bu depo (`harita/`) kendi başına bir Python paketidir (kökte `__init__.py`
var) ve tüm alt modüller (`core_engine`, `persistence`, `app_shell`, ...)
birbirlerine göreli importlarla (`from ...core_engine import ...`)
bağlıdır — yani doğru kurulu isimleri `harita.core_engine`,
`harita.persistence` şeklinde, "harita." önekiyle olmalıdır.

`pyproject.toml`'daki bildirimsel `[tool.setuptools.packages.find]` bunu
otomatik yapamaz (bulduğu adları yeniden öneklemez), bu yüzden burada
`find_packages()` ile gerçek alt paketler bulunup elle "harita." önekiyle
`package_dir` haritasına ekleniyor. Faz 22 dokümantasyon denetimi
sırasında bulunan ve düzeltilen bir paketleme hatasının çözümüdür — bkz.
`ROADMAP_V2.md` Faz 22 notları ve `DEVOPS.md`.
"""
from __future__ import annotations

import os

from setuptools import find_packages, setup

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

_EXCLUDE = (
    "tests", "tests.*", "*.tests", "*.tests.*",
    "scripts", "scripts.*",
    "docs", "docs.*",
)

_subpackages = find_packages(where=_REPO_ROOT, exclude=_EXCLUDE)

# Her alt paketi "harita." önekiyle yeniden adlandır; kaynağı hâlâ gerçek
# repo kökündeki (CWD'den bağımsız) orijinal yoldan okunur (package_dir
# eşlemesi). Roadmap V8 Faz 1.1: önceki "." göreli yolu, betiğin çalışma
# dizinine (CWD) bağımlıydı; artık __file__ üzerinden mutlak repo köküne
# çözülüyor, hangi dizinden çağrılırsa çağrılsın aynı sonucu üretir.
packages = ["harita"] + [f"harita.{name}" for name in _subpackages]
# NOT: package_dir setuptools tarafından ZORUNLU olarak setup.py'ın kendi
# konumuna göreli olmalı (mutlak yol verilirse "setup script specifies an
# absolute path" hatasıyla reddedilir) — bu yüzden burası kasıtlı olarak
# "." kalıyor; CWD bağımlılığı yalnızca yukarıdaki find_packages(where=...)
# taramasındaydı ve orası artık mutlak _REPO_ROOT kullanıyor.
package_dir = {"harita": "."}

setup(
    packages=packages,
    package_dir=package_dir,
)