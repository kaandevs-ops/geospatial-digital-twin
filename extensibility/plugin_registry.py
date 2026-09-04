"""
Plugin Registry - Versiyon Uyumluluk Matrisi
=============================================

ROADMAP_V4 - Faz E14 (Extensibility: Plugin Marketplace ve Versiyon
Uyumluluk Matrisi).

Faz 14/A14 ile plugin imzalama/doğrulama (`plugin_signing.py`) tamamlandı
ama plugin'ler arası **versiyon uyumluluğu** hiç takip edilmiyordu: bir
plugin hangi `harita` sürümüyle test edildi, bağımlı olduğu diğer
plugin'ler hangi sürüm aralığında olmalı - bunlar hiç kontrol edilmiyordu.

Bu modül, tamamen yerel/dosya-tabanlı (ağ erişimi gerektirmeyen) bir
"marketplace" mantığı sağlar:

* `parse_version()` / `Version` - basit semver (major.minor.patch,
  opsiyonel pre-release parçası göz ardı edilir) ayrıştırma, stdlib-only.
* `VersionRange` - `">=1.0.0,<2.0.0"` gibi virgülle ayrılmış AND
  koşulları içeren bir aralık; `"*"` her sürümü kabul eder.
* `PluginVersionRegistry` - `PluginMeta.compatible_versions` (bu fazda
  `PluginMeta`'ya eklenen yeni, geriye-uyumlu varsayılan `"*"` alanı) ve
  `PluginMeta.depends_on`'daki her bağımlılığın **sürüm aralığıyla**
  birlikte ifade edilebildiği (`"other_plugin>=1.0.0"` sözdizimi, düz
  `"other_plugin"` da hâlâ desteklenir - geriye uyumlu) bağımlılık
  beyanlarını kontrol eder.

`PluginManager.discover_directory` bu registry'i opsiyonel bir parametre
olarak kabul eder (`version_registry=None` iken davranış tamamen eskisiyle
aynıdır - regresyon yok); verilirse uyumsuz bir plugin `exec` edildikten
(meta'sı okunduktan) hemen sonra, `register()` çağrılmadan önce reddedilir
ve `self.rejected`'a (imzasız plugin'lerle aynı netlikte) bir sebep
metniyle eklenir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:[-+].*)?$")

_OP_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(\d+\.\d+\.\d+)$")


class VersionParseError(ValueError):
    """Geçersiz bir semver string'i ayrıştırılmaya çalışıldığında fırlatılır."""


class IncompatiblePluginError(Exception):
    """Bir plugin, mevcut `harita` sürümüyle veya bağımlılıklarıyla uyumsuz
    olduğunda `PluginVersionRegistry.check()` tarafından fırlatılır."""


@dataclass(frozen=True, order=True)
class Version:
    """Basit bir (major, minor, patch) semver temsili.

    Karşılaştırma operatörleri (`<`, `<=`, `>`, `>=`, `==`) tuple sıralamasına
    dayanır - `@dataclass(order=True)` bunu otomatik sağlar.
    """

    major: int
    minor: int
    patch: int

    def __str__(self) -> str:  # pragma: no cover - kolaylık
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_version(text: str) -> Version:
    """`\"1.2.3\"` -> `Version(1, 2, 3)`. Pre-release/build eki yok sayılır."""
    match = _VERSION_RE.match(text.strip())
    if not match:
        raise VersionParseError(f"Geçersiz semver string'i: {text!r} (beklenen biçim: 'X.Y.Z')")
    return Version(int(match.group("major")), int(match.group("minor")), int(match.group("patch")))


@dataclass
class VersionRange:
    """Virgülle ayrılmış AND koşulları içeren bir semver aralığı.

    Örnekler: `"*"` (her şey), `">=1.0.0"`, `">=1.0.0,<2.0.0"`,
    `"==1.2.3"`, `"!=1.5.0"`.
    """

    raw: str
    _clauses: tuple[tuple[str, Version], ...]

    @staticmethod
    def parse(text: str) -> VersionRange:
        text = text.strip()
        if text == "" or text == "*":
            return VersionRange(raw=text or "*", _clauses=())
        clauses: list[tuple[str, Version]] = []
        for part in text.split(","):
            part = part.strip()
            m = _OP_RE.match(part)
            if not m:
                raise VersionParseError(
                    f"Geçersiz versiyon aralığı ifadesi: {part!r} (tüm ifade: {text!r})"
                )
            op, ver_text = m.group(1), m.group(2)
            clauses.append((op, parse_version(ver_text)))
        return VersionRange(raw=text, _clauses=tuple(clauses))

    def contains(self, version: Version) -> bool:
        if not self._clauses:
            return True
        for op, bound in self._clauses:
            if op == ">=" and not (version >= bound):
                return False
            if op == "<=" and not (version <= bound):
                return False
            if op == ">" and not (version > bound):
                return False
            if op == "<" and not (version < bound):
                return False
            if op == "==" and not (version == bound):
                return False
            if op == "!=" and not (version != bound):
                return False
        return True

    def __str__(self) -> str:  # pragma: no cover
        return self.raw


def _parse_dependency_spec(spec: str) -> tuple[str, VersionRange]:
    """`\"other_plugin>=1.0.0\"` -> `(\"other_plugin\", VersionRange(\">=1.0.0\"))`.

    Sürüm ifadesi olmayan düz bir isim (`\"other_plugin\"`) geriye uyumlu
    olarak `\"*\"` (her sürüm) aralığına eşlenir.
    """
    m = re.match(r"^([A-Za-z0-9_\-\.]+?)\s*(>=|<=|==|!=|>|<)(.+)$", spec.strip())
    if not m:
        return spec.strip(), VersionRange.parse("*")
    name, op, ver_text = m.group(1), m.group(2), m.group(3)
    return name, VersionRange.parse(f"{op}{ver_text}")


class PluginVersionRegistry:
    """Plugin meta'larındaki `compatible_versions`/`depends_on` beyanlarını
    mevcut `harita` sürümüne ve zaten yüklü diğer plugin'lerin sürümlerine
    karşı kontrol eder.
    """

    def check(
        self,
        meta,  # PluginMeta - dolaşan import'u önlemek için tip belirtilmedi
        harita_version: str,
        loaded_plugin_versions: dict[str, str] | None = None,
    ) -> None:
        """Uyumsuzluk varsa `IncompatiblePluginError` fırlatır; aksi halde
        sessizce döner (uyumlu)."""
        loaded_plugin_versions = loaded_plugin_versions or {}

        compatible = getattr(meta, "compatible_versions", None) or "*"
        try:
            version_range = VersionRange.parse(compatible)
            current = parse_version(harita_version)
        except VersionParseError as exc:
            raise IncompatiblePluginError(
                f"Plugin '{meta.name}': versiyon ayrıştırma hatası: {exc}"
            ) from exc

        if not version_range.contains(current):
            raise IncompatiblePluginError(
                f"Plugin '{meta.name}' (compatible_versions={compatible!r}) "
                f"mevcut harita sürümü {harita_version} ile uyumsuz."
            )

        for dep_spec in getattr(meta, "depends_on", None) or []:
            dep_name, dep_range = _parse_dependency_spec(dep_spec)
            dep_version_text = loaded_plugin_versions.get(dep_name)
            if dep_version_text is None:
                # Bağımlılık henüz yüklü değil - bu, yükleme sırasına
                # (topological order) bırakılır; burada sadece sürüm
                # beyanının kendisi biçimsel olarak doğrulanır.
                continue
            try:
                dep_version = parse_version(dep_version_text)
            except VersionParseError as exc:
                raise IncompatiblePluginError(
                    f"Plugin '{meta.name}' bağımlılığı '{dep_name}' için "
                    f"geçersiz yüklü sürüm: {exc}"
                ) from exc
            if not dep_range.contains(dep_version):
                raise IncompatiblePluginError(
                    f"Plugin '{meta.name}', '{dep_spec}' bağımlılığını "
                    f"gerektiriyor ama yüklü '{dep_name}' sürümü "
                    f"{dep_version_text} bu aralığın dışında."
                )
