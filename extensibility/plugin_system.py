"""
Plugin System
=============

Roadmap Phase 14 - "Plugin sistemi".

Ana projenin `plugin_loader.py`'ı ile aynı desen: kayıt-tabanlı (registry),
dinamik yükleme, harici bağımlılık zorunlu değil. `plugin_loader.py` LLM
sağlayıcılarına (OpenAI/Anthropic/Ollama) odaklıyken, bu modül genel amaçlı
"herhangi bir Python objesi/modülü plugin olabilir" sözleşmesini sağlar ve
`harita/` alt sistemine özeldir (Editor komutları, Export formatları, AI
Assistant intent'leri gibi genişletme noktaları için).

Tasarım:
    * `PluginMeta`   - isim/sürüm/yazar/bağımlılık meta verisi.
    * `Plugin`       - taban sınıf (abstract): `on_load` / `on_unload` hook'ları.
    * `PluginManager`- kayıt, keşif (entry-point benzeri: bir dizindeki
      `*.py` dosyalarını tarar), bağımlılık sıralı yükleme (topological),
      etkinleştirme/devre dışı bırakma, hata izolasyonu (bir plugin patlarsa
      diğerlerini etkilemez).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import traceback
from abc import ABC
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plugin_signing import PluginTrustStore


@dataclass
class PluginMeta:
    name: str
    version: str = "0.1.0"
    author: str = ""
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    # ROADMAP_V4 - Faz E14: hangi `harita` sürüm aralığıyla uyumlu olduğunu
    # beyan eder (semver aralığı, örn. ">=0.10.0,<1.0.0"). Varsayılan "*"
    # (her sürüm) - geriye uyumlu, mevcut plugin'ler hiçbir değişiklik
    # yapmadan çalışmaya devam eder. `depends_on` girdileri de opsiyonel
    # olarak sürüm aralığı taşıyabilir (`"other_plugin>=1.0.0"`); düz isim
    # (`"other_plugin"`) hâlâ desteklenir.
    compatible_versions: str = "*"


class Plugin(ABC):
    """Tüm pluginlerin taban sözleşmesi."""

    meta: PluginMeta

    def __init__(self, meta: PluginMeta | None = None) -> None:
        self.meta = meta or PluginMeta(name=self.__class__.__name__)
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def on_load(self, manager: PluginManager) -> None:
        """Plugin etkinleştirilirken çağrılır. Alt sınıflar override edebilir."""

    def on_unload(self, manager: PluginManager) -> None:
        """Plugin devre dışı bırakılırken çağrılır."""


class FunctionPlugin(Plugin):
    """Sınıf yazmadan, tek bir fonksiyonu/objeyi plugin olarak kaydetmek için."""

    def __init__(self, name: str, obj: Any, version: str = "0.1.0") -> None:
        super().__init__(PluginMeta(name=name, version=version))
        self.obj = obj

    def __getattr__(self, item: str) -> Any:
        return getattr(self.obj, item)


@dataclass
class PluginRecord:
    plugin: Plugin
    enabled: bool = False
    error: str | None = None
    load_time: float | None = None


class PluginDependencyError(Exception):
    pass


class PluginManager:
    """Plugin kayıt/keşif/yükleme yöneticisi.

    Bağımlılık çözümü: Kahn algoritması (topological sort) - Phase 13
    `AssetDependencyManager` ile aynı yaklaşım, döngü tespiti dahil.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, PluginRecord] = {}
        self._event_hooks: list[Callable[[str, str], None]] = []

    # -- kayıt -----------------------------------------------------------
    def register(self, plugin: Plugin) -> None:
        name = plugin.meta.name
        if name in self._plugins:
            raise ValueError(f"Plugin zaten kayıtlı: {name}")
        self._plugins[name] = PluginRecord(plugin=plugin)

    def register_function(self, name: str, obj: Any, version: str = "0.1.0") -> None:
        self.register(FunctionPlugin(name, obj, version))

    def unregister(self, name: str) -> None:
        if name in self._plugins and self._plugins[name].enabled:
            self.disable(name)
        self._plugins.pop(name, None)

    def get(self, name: str) -> Any:
        record = self._plugins.get(name)
        if record is None:
            raise KeyError(f"Plugin bulunamadı: {name}")
        return record.plugin

    def get_plugin(self, name: str) -> Any:
        return self.get(name)

    def list_plugins(self) -> list[str]:
        return list(self._plugins.keys())

    def is_enabled(self, name: str) -> bool:
        record = self._plugins.get(name)
        return bool(record and record.enabled)

    # -- bağımlılık sıralama ----------------------------------------------
    def _topological_order(self, names: list[str]) -> list[str]:
        in_degree: dict[str, int] = {n: 0 for n in names}
        edges: dict[str, list[str]] = {n: [] for n in names}
        for n in names:
            for dep in self._plugins[n].plugin.meta.depends_on:
                if dep not in self._plugins:
                    raise PluginDependencyError(f"'{n}' bağımlılığı eksik: '{dep}'")
                edges.setdefault(dep, []).append(n)
                in_degree[n] += 1

        queue = [n for n in names if in_degree.get(n, 0) == 0]
        order: list[str] = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for nxt in edges.get(node, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(order) != len(names):
            remaining = set(names) - set(order)
            raise PluginDependencyError(
                f"Döngüsel plugin bağımlılığı tespit edildi: {sorted(remaining)}"
            )
        return order

    # -- yaşam döngüsü -----------------------------------------------------
    def enable(self, name: str) -> None:
        """Tek bir plugini (ve varsa bağımlılıklarını) etkinleştirir.

        Bir plugin `on_load` içinde hata fırlatırsa, hata izole edilir
        (`record.error`'a yazılır) ve diğer pluginler etkilenmez.
        """
        if name not in self._plugins:
            raise KeyError(f"Plugin bulunamadı: {name}")
        order = self._topological_order(self._collect_with_deps(name))
        for n in order:
            self._enable_one(n)

    def _collect_with_deps(self, name: str, seen: set | None = None) -> list[str]:
        seen = seen if seen is not None else set()
        if name in seen:
            return []
        seen.add(name)
        result = [name]
        for dep in self._plugins[name].plugin.meta.depends_on:
            if dep not in self._plugins:
                raise PluginDependencyError(f"'{name}' bağımlılığı eksik: '{dep}'")
            result = self._collect_with_deps(dep, seen) + result
        return result

    def _enable_one(self, name: str) -> None:
        record = self._plugins[name]
        if record.enabled:
            return
        try:
            record.plugin.on_load(self)
            record.plugin._loaded = True
            record.enabled = True
            record.error = None
            record.load_time = time.time()
            self._emit("enabled", name)
        except Exception:  # noqa: BLE001 - hata izolasyonu kasıtlı
            record.error = traceback.format_exc()
            record.enabled = False
            self._emit("error", name)

    def disable(self, name: str) -> None:
        record = self._plugins.get(name)
        if record is None or not record.enabled:
            return
        try:
            record.plugin.on_unload(self)
        finally:
            record.plugin._loaded = False
            record.enabled = False
            self._emit("disabled", name)

    def enable_all(self) -> None:
        order = self._topological_order(list(self._plugins.keys()))
        for n in order:
            self._enable_one(n)

    def disable_all(self) -> None:
        for name in list(self._plugins.keys()):
            self.disable(name)

    # -- keşif (discovery) --------------------------------------------------
    def discover_directory(
        self,
        path: str,
        factory_attr: str = "PLUGIN",
        trust_store: PluginTrustStore | None = None,
        require_signature: bool = False,
        version_registry: PluginVersionRegistry | None = None,
        harita_version: str | None = None,
    ) -> list[str]:
        """Bir dizindeki `*.py` dosyalarını modül olarak yükler.

        `trust_store` verilirse her dosya yüklenmeden ÖNCE imzası
        doğrulanır. `require_signature=True` -> imzasız/geçersiz dosyalar
        hiç `exec` edilmeden atlanır (`self.rejected`'a kaydedilir).
        `trust_store=None` iken davranış eskisiyle birebir aynıdır.

        ROADMAP_V4 - Faz E14: `version_registry` verilirse, modül `exec`
        edildikten (meta'sı okunabilir hale geldikten) sonra ama
        `register()` çağrılmadan ÖNCE, plugin'in `compatible_versions`/
        `depends_on` beyanı `harita_version` ile ve zaten yüklü
        plugin'lerin sürümleriyle kontrol edilir. Uyumsuzsa plugin
        kaydedilmez, `self.rejected`'a bir sebep metniyle eklenir.
        `version_registry=None` iken davranış tamamen eskisiyle aynıdır.
        """
        discovered: list[str] = []
        self.rejected: list[tuple[str, str]] = getattr(self, "rejected", [])
        if not os.path.isdir(path):
            return discovered
        for filename in sorted(os.listdir(path)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            module_path = os.path.join(path, filename)

            if trust_store is not None:
                ok, reason = trust_store.verify_plugin_file(module_path)
                if not ok:
                    self.rejected.append((filename, reason))
                    if require_signature:
                        continue

            module_name = f"_harita_plugin_{os.path.splitext(filename)[0]}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                plugin_obj = getattr(module, factory_attr, None)
                if isinstance(plugin_obj, Plugin):
                    if version_registry is not None:
                        current_version = harita_version
                        if current_version is None:
                            import harita as _harita  # gecikmeli import - dongusel bagimliligi onler

                            current_version = _harita.__version__
                        loaded_versions = {n: p.meta.version for n, p in self._plugins.items()}
                        try:
                            version_registry.check(
                                plugin_obj.meta, current_version, loaded_versions
                            )
                        except Exception as exc:  # noqa: BLE001 - IncompatiblePluginError dahil
                            self.rejected.append((filename, str(exc)))
                            continue
                    self.register(plugin_obj)
                    discovered.append(plugin_obj.meta.name)
            except Exception:  # noqa: BLE001 - keşifte tek dosya hatası diğerlerini etkilemez
                continue
        return discovered

    # -- event hook'ları (EventSystem ile entegrasyon için) ------------------
    def on_event(self, callback: Callable[[str, str], None]) -> None:
        self._event_hooks.append(callback)

    def _emit(self, event: str, name: str) -> None:
        for hook in self._event_hooks:
            try:
                hook(event, name)
            except Exception:  # noqa: BLE001
                pass
