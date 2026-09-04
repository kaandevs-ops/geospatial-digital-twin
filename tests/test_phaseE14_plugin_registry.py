"""
ROADMAP_V4 - Faz E14: Extensibility - Plugin Marketplace ve Versiyon
Uyumluluk Matrisi.

Kabul kriteri (bu test dosyasının kanıtladığı):
    * `parse_version` / `Version` / `VersionRange`: semver ayrıştırma ve
      aralık kontrolü (`"*"`, `">=1.0.0"`, `">=1.0.0,<2.0.0"`, vb.) doğru
      çalışır; geçersiz string'ler `VersionParseError` fırlatır.
    * `PluginVersionRegistry.check()`: `compatible_versions` mevcut
      `harita` sürümüyle uyumsuzsa `IncompatiblePluginError` fırlatır;
      `depends_on` içindeki sürüm aralığı beyanları (`"dep>=1.0.0"`) da
      yüklü bağımlılık sürümlerine karşı kontrol edilir.
    * `PluginManager.discover_directory(..., version_registry=...)`:
      mevcut `harita` sürümüyle uyumsuz bir `compatible_versions` beyan
      eden bir plugin dosyası `exec` edilir (meta okunur) ama `register()`
      çağrılmadan reddedilir; `self.rejected`'a sebep metniyle eklenir.
    * Uyumlu bir plugin normal şekilde keşfedilip yüklenir.
    * `version_registry=None` (varsayılan) iken davranış tamamen eskisiyle
      aynıdır - regresyon yok (mevcut imzalama testleri bozulmaz).
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.extensibility.plugin_registry import (
    IncompatiblePluginError,
    PluginVersionRegistry,
    Version,
    VersionParseError,
    VersionRange,
    parse_version,
)
from harita.extensibility.plugin_system import PluginManager, PluginMeta


class TestVersionParsing:
    def test_parse_simple_version(self) -> None:
        assert parse_version("1.2.3") == Version(1, 2, 3)

    def test_parse_ignores_prerelease_suffix(self) -> None:
        assert parse_version("1.2.3-beta.1") == Version(1, 2, 3)

    def test_parse_invalid_raises(self) -> None:
        with pytest.raises(VersionParseError):
            parse_version("not-a-version")

    def test_ordering(self) -> None:
        assert parse_version("1.0.0") < parse_version("1.2.0")
        assert parse_version("2.0.0") > parse_version("1.99.99")


class TestVersionRange:
    def test_wildcard_accepts_everything(self) -> None:
        r = VersionRange.parse("*")
        assert r.contains(parse_version("0.0.1"))
        assert r.contains(parse_version("99.99.99"))

    def test_single_bound(self) -> None:
        r = VersionRange.parse(">=1.0.0")
        assert r.contains(parse_version("1.0.0"))
        assert not r.contains(parse_version("0.9.9"))

    def test_combined_and_bounds(self) -> None:
        r = VersionRange.parse(">=1.0.0,<2.0.0")
        assert r.contains(parse_version("1.5.0"))
        assert not r.contains(parse_version("2.0.0"))
        assert not r.contains(parse_version("0.5.0"))

    def test_invalid_range_raises(self) -> None:
        with pytest.raises(VersionParseError):
            VersionRange.parse(">=not-a-version")


class TestPluginVersionRegistryCheck:
    def test_compatible_version_passes(self) -> None:
        reg = PluginVersionRegistry()
        meta = PluginMeta(name="a", version="1.0.0", compatible_versions=">=1.0.0,<2.0.0")
        reg.check(meta, "1.5.0")  # raise etmemeli

    def test_incompatible_version_raises(self) -> None:
        reg = PluginVersionRegistry()
        meta = PluginMeta(name="b", version="1.0.0", compatible_versions=">=2.0.0")
        with pytest.raises(IncompatiblePluginError):
            reg.check(meta, "1.5.0")

    def test_default_compatible_versions_is_wildcard(self) -> None:
        reg = PluginVersionRegistry()
        meta = PluginMeta(name="c", version="1.0.0")  # compatible_versions="*"
        reg.check(meta, "0.0.1")  # raise etmemeli

    def test_dependency_version_range_satisfied(self) -> None:
        reg = PluginVersionRegistry()
        meta = PluginMeta(name="d", version="1.0.0", depends_on=["other>=1.0.0"])
        reg.check(meta, "1.0.0", loaded_plugin_versions={"other": "1.2.0"})

    def test_dependency_version_range_violated(self) -> None:
        reg = PluginVersionRegistry()
        meta = PluginMeta(name="e", version="1.0.0", depends_on=["other>=2.0.0"])
        with pytest.raises(IncompatiblePluginError):
            reg.check(meta, "1.0.0", loaded_plugin_versions={"other": "1.2.0"})

    def test_plain_dependency_name_still_supported(self) -> None:
        # Sürüm aralığı olmayan düz isim -> "*" e eşlenir, regresyon yok.
        reg = PluginVersionRegistry()
        meta = PluginMeta(name="f", version="1.0.0", depends_on=["other"])
        reg.check(meta, "1.0.0", loaded_plugin_versions={"other": "0.0.1"})

    def test_dependency_not_yet_loaded_is_skipped(self) -> None:
        # Bağımlılık henüz yüklü değilse (topological sıraya bırakılır),
        # sadece beyanın kendisi biçimsel doğrulanır - hata fırlatılmaz.
        reg = PluginVersionRegistry()
        meta = PluginMeta(name="g", version="1.0.0", depends_on=["other>=1.0.0"])
        reg.check(meta, "1.0.0", loaded_plugin_versions={})


_COMPATIBLE_PLUGIN_SOURCE = textwrap.dedent(
    """
    from harita.extensibility.plugin_system import Plugin, PluginMeta

    class _CompatPlugin(Plugin):
        pass

    PLUGIN = _CompatPlugin(PluginMeta(name="compat_plugin", version="1.0.0",
                                       compatible_versions=">=1.0.0,<2.0.0"))
    """
)

_INCOMPATIBLE_PLUGIN_SOURCE = textwrap.dedent(
    """
    SIDE_EFFECT_TRIGGERED = []
    SIDE_EFFECT_TRIGGERED.append("exec-oldu")  # exec edildiginin kaniti

    from harita.extensibility.plugin_system import Plugin, PluginMeta

    class _IncompatPlugin(Plugin):
        pass

    PLUGIN = _IncompatPlugin(PluginMeta(name="incompat_plugin", version="1.0.0",
                                         compatible_versions=">=99.0.0"))
    """
)


class TestDiscoverDirectoryVersionRegistry:
    def test_incompatible_plugin_rejected_not_registered(self, tmp_path: Path) -> None:
        (tmp_path / "incompat.py").write_text(_INCOMPATIBLE_PLUGIN_SOURCE, encoding="utf-8")

        manager = PluginManager()
        reg = PluginVersionRegistry()
        discovered = manager.discover_directory(
            str(tmp_path), version_registry=reg, harita_version="1.0.0"
        )

        assert discovered == []
        assert "incompat_plugin" not in manager.list_plugins()
        assert len(manager.rejected) == 1
        rejected_filename, reason = manager.rejected[0]
        assert rejected_filename == "incompat.py"
        assert "incompat_plugin" in reason

    def test_compatible_plugin_registered_normally(self, tmp_path: Path) -> None:
        (tmp_path / "compat.py").write_text(_COMPATIBLE_PLUGIN_SOURCE, encoding="utf-8")

        manager = PluginManager()
        reg = PluginVersionRegistry()
        discovered = manager.discover_directory(
            str(tmp_path), version_registry=reg, harita_version="1.0.0"
        )

        assert discovered == ["compat_plugin"]
        assert "compat_plugin" in manager.list_plugins()
        assert manager.rejected == []

    def test_no_version_registry_behaves_exactly_as_before(self, tmp_path: Path) -> None:
        # version_registry=None (varsayilan) -> eskisiyle birebir ayni:
        # uyumsuz beyan edilmis olsa bile plugin normal sekilde kaydedilir.
        (tmp_path / "incompat.py").write_text(_INCOMPATIBLE_PLUGIN_SOURCE, encoding="utf-8")

        manager = PluginManager()
        discovered = manager.discover_directory(str(tmp_path))

        assert discovered == ["incompat_plugin"]
        assert "incompat_plugin" in manager.list_plugins()
