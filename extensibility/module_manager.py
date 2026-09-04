"""
Module Manager & Theme System
==============================

Roadmap Phase 14 - "Modül yöneticisi", "Tema sistemi".

`ModuleManager`: `harita/` alt paketlerini (core_engine, mesh_engine, ...)
isim üzerinden lazy-import ile açar/kapatır - büyük sahnelerde kullanılmayan
alt sistemleri belleğe hiç yüklememek için (Phase 13 `AssetDependencyManager`
ile aynı ruhta, ama modül-seviyesinde). Devre dışı bırakılan bir modül
tekrar `enable()` edilene kadar `get()` ile erişilemez.

`ThemeSystem`: Editor/Visualization katmanları için renk/stil şeması
yönetimi - sahit veri, harici UI framework bağımlılığı yok.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class ModuleNotEnabledError(Exception):
    pass


@dataclass
class ModuleRecord:
    import_path: str
    enabled: bool = True
    _module: Any = None

    def load(self) -> Any:
        if self._module is None:
            self._module = importlib.import_module(self.import_path)
        return self._module


class ModuleManager:
    """`harita.<alt_paket>` modüllerinin lazy-load + enable/disable yönetimi."""

    #: Roadmap'teki 14 faz ile birebir eşleşen varsayılan modül haritası.
    DEFAULT_MODULES: Dict[str, str] = {
        "core_engine": "harita.core_engine",
        "terrain_engine": "harita.terrain_engine",
        "mesh_engine": "harita.mesh_engine",
        "material_engine": "harita.material_engine",
        "lighting": "harita.lighting",
        "building_reconstruction": "harita.building_reconstruction",
        "ai_reconstruction": "harita.ai_reconstruction",
        "digital_twin": "harita.digital_twin",
        "analysis_engine": "harita.analysis_engine",
        "mobility": "harita.mobility",
        "editor": "harita.editor",
        "visualization": "harita.visualization",
        "data_engine": "harita.data_engine",
        "export": "harita.export",
        "ai_assistant": "harita.ai_assistant",
        "performance": "harita.performance",
        # Roadmap V4 - Track C / C2: Faz 15/16/18/19 daha önce hiç kayıtlı
        # değildi - merkezi modül kaydı tamamlanıyor (denetim maddesi #2).
        "render_engine": "harita.render_engine",
        "persistence": "harita.persistence",
        "app_shell": "harita.app_shell",
        "collaboration": "harita.collaboration",
        "extensibility": "harita.extensibility",
        # Roadmap V4 - Faz E15: yeni observability alt sistemi.
        "observability": "harita.observability",
        # Roadmap V4 - Faz E16: yeni i18n alt sistemi.
        "i18n": "harita.i18n",
        # Roadmap V4 - Faz E18: yeni vegetation alt sistemi.
        "vegetation": "harita.vegetation",
        # Roadmap V4 - Faz E17: yeni physics alt sistemi.
        "physics": "harita.physics",
        # Faz 5.4: security (rate limiting) daha önce kayıtlı değildi.
        "security": "harita.security",
        # Roadmap Faz 2.3: yeni hazard_data alt sistemi (AFAD/USGS + risk).
        "hazard_data": "harita.hazard_data",
        # ROADMAP_V5 M2.5: street_furniture (sokak mobilyası + köprü/su/
        # peyzaj) - daha önce kayıtlı değildi.
        "street_furniture": "harita.street_furniture",
        # ROADMAP_V5 D2: climate_data (Open-Meteo) - daha önce kayıtlı değildi.
        "climate_data": "harita.climate_data",
        # ROADMAP_V7.md Faz C3 (4-7. dilimler): OSM köprü modülleri daha
        # önce kayıtlı değildi - merkezi modül kaydı tamamlanıyor.
        "religious_structures": "harita.religious_structures",
        "commerce_props": "harita.commerce_props",
        "sport_recreation": "harita.sport_recreation",
        "power_infrastructure": "harita.power_infrastructure",
        # Feature-survey (saha ölçüm/nokta bulutu) alt sistemi - daha önce
        # kayıtlı değildi.
        "feature_survey": "harita.feature_survey",
        # ROADMAP_V7.md Faz C5 (offline mod, A4): tile önbelleği + yerel
        # yer adı indeksi - daha önce kayıtlı değildi (bu oturumda C6/2.
        # dilim testleri koşulurken `test_phaseC2_module_registry_
        # completeness.py`/`test_phaseC3_root_reexport_completeness.py`
        # "unutma koruması" testleri tarafından yakalandı).
        "offline_cache": "harita.offline_cache",
        # ROADMAP_V9 Faz I / O.1: küresel şehir saati - daha önce kayıtlı
        # değildi - merkezi modül kaydı tamamlanıyor.
        "simulation_core": "harita.simulation_core",
        # ROADMAP_V9 Faz VI / Katman 2.1-2.2: sentetik nüfus + günlük rutin
        # motoru - daha önce kayıtlı değildi.
        "population": "harita.population",
    }

    def __init__(self, register_defaults: bool = True) -> None:
        self._modules: Dict[str, ModuleRecord] = {}
        if register_defaults:
            for name, path in self.DEFAULT_MODULES.items():
                self.register(name, path)

    def register(self, name: str, import_path: str) -> None:
        self._modules[name] = ModuleRecord(import_path=import_path)

    def enable(self, name: str) -> None:
        self._require(name).enabled = True

    def disable(self, name: str) -> None:
        record = self._require(name)
        record.enabled = False
        record._module = None  # bellekten düşür

    def is_enabled(self, name: str) -> bool:
        return name in self._modules and self._modules[name].enabled

    def get(self, name: str) -> Any:
        record = self._require(name)
        if not record.enabled:
            raise ModuleNotEnabledError(f"Modül devre dışı: {name}")
        return record.load()

    def list_modules(self) -> List[str]:
        return list(self._modules.keys())

    def enabled_modules(self) -> List[str]:
        return [n for n, r in self._modules.items() if r.enabled]

    def _require(self, name: str) -> ModuleRecord:
        if name not in self._modules:
            raise KeyError(f"Bilinmeyen modül: {name}")
        return self._modules[name]


@dataclass
class Theme:
    name: str
    colors: Dict[str, str] = field(default_factory=dict)
    fonts: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def get_color(self, key: str, default: str = "#000000") -> str:
        return self.colors.get(key, default)

    def merged_with(self, overrides: "Theme") -> "Theme":
        """Bu temayı temel alıp `overrides` ile eşlenmiş yeni bir tema üretir."""
        return Theme(
            name=overrides.name,
            colors={**self.colors, **overrides.colors},
            fonts={**self.fonts, **overrides.fonts},
            extra={**self.extra, **overrides.extra},
        )


_DEFAULT_DARK = Theme(
    name="dark",
    colors={
        "background": "#12141a",
        "panel": "#1c1f27",
        "text": "#e6e6e6",
        "accent": "#3b82f6",
        "grid": "#2a2e38",
    },
    fonts={"ui": "Inter", "mono": "JetBrains Mono"},
)

_DEFAULT_LIGHT = Theme(
    name="light",
    colors={
        "background": "#ffffff",
        "panel": "#f4f4f5",
        "text": "#111111",
        "accent": "#2563eb",
        "grid": "#dddddd",
    },
    fonts={"ui": "Inter", "mono": "JetBrains Mono"},
)


class ThemeSystem:
    """Editor/Visualization için isimlendirilmiş tema kayıt defteri."""

    def __init__(self) -> None:
        self._themes: Dict[str, Theme] = {
            "dark": _DEFAULT_DARK,
            "light": _DEFAULT_LIGHT,
        }
        self._active = "dark"
        self._listeners: List[Any] = []

    def register_theme(self, theme: Theme) -> None:
        self._themes[theme.name] = theme

    def get_theme(self, name: Optional[str] = None) -> Theme:
        name = name or self._active
        if name not in self._themes:
            raise KeyError(f"Bilinmeyen tema: {name}")
        return self._themes[name]

    def set_active(self, name: str) -> None:
        if name not in self._themes:
            raise KeyError(f"Bilinmeyen tema: {name}")
        self._active = name
        for listener in self._listeners:
            listener(self._themes[name])

    @property
    def active_theme(self) -> Theme:
        return self._themes[self._active]

    def list_themes(self) -> List[str]:
        return list(self._themes.keys())

    def on_change(self, listener) -> None:  # noqa: ANN001
        self._listeners.append(listener)
