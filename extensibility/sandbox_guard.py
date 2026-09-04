"""
Sandbox Guard — A14 Güvenlik Sertleştirmesi
=============================================

Roadmap V2, A14 (Extensibility derinleştirme): "``script_api``'deki kısıtlı
``exec`` sandbox'ı güvenlik denetiminden geçirilir ... Plugin
imzalama/doğrulama." Bu modül birinci maddeyi karşılar: `PythonScriptEngine`
tarafından çalıştırılmadan **önce** kaynak kodu statik olarak (AST üzerinden)
denetler ve bilinen kaçış sınıflarını reddeder.

Neden statik analiz (yalnızca kısıtlı ``__builtins__`` yetmez)?
-----------------------------------------------------------------
Kısıtlı ``__builtins__`` sözlüğü ``__import__``/``open``/``eval`` gibi
isimleri kaldırsa da, CPython'da *her nesne* ortak ata sınıfı ``object``
üzerinden erişilebilir durumdadır::

    ().__class__.__bases__[0].__subclasses__()

bu ifade, ``__builtins__`` kısıtlamasından tamamen bağımsız olarak process
içindeki tüm yüklü sınıfları (örn. ``subprocess.Popen``) döndürür — script
API'nin önceki hâli bu yolla **rastgele process başlatmaya** açıktı (bu
oturumda doğrulandı, bkz. `tests/test_phase14_security_audit.py::
test_subclasses_traversal_blocks_popen_escape`). Aynı aile: ``__globals__``,
``__code__``, ``__closure__``, ``f_locals``/``f_globals`` (frame
introspection), ``__builtins__`` sözlüğünün scope içinden yeniden ele
geçirilmesi.

Yaklaşım
--------
``ast.parse`` ile kaynak koddan bir sözdizimi ağacı çıkarılır ve şu desenler
reddedilir (her biri en az bir gerçek kaçış PoC'siyle test edilir):

1. **Dunder attribute erişimi** — ``x.__class__``, ``x.__bases__``,
   ``x.__subclasses__``, ``x.__globals__``, ``x.__code__``, ``x.__mro__``,
   ``x.__builtins__``, ``x.__import__``, ``x.__loader__``, ``x.__dict__``
   ve benzerleri (izin verilen tek istisna: ``self.__init__`` gibi kullanıcı
   *tanımlı* sınıflarda çok yaygın olan ``__init__`` — yalnızca **tanım**
   (``def __init__``) serbest, **erişim** (``x.__init__``) değil).
2. **Tehlikeli isimler** — ``eval``, ``exec``, ``compile``, ``__import__``,
   ``getattr``/``setattr``/``delattr``/``vars``/``globals``/``locals`` (bu
   fonksiyonlar dolaylı dunder erişimini bypass edebilir), ``open``.
3. **Import ifadeleri** — ``import``/``from ... import`` tamamen yasak
   (zaten ``__builtins__``'ten ``__import__`` kaldırılmış olsa da savunma
   derinliği için statik seviyede de engellenir).
4. **String-tabanlı dolaylı erişim** — ``getattr(x, "__globals__")`` gibi
   desenler madde 2'deki isim yasağıyla zaten kapsanır.

Kabul edilen risk (belgelenmiş sınır — README'deki "tam bir güvenlik sınırı
değildir" notuyla tutarlı): bu, tam bir bytecode-seviyeli sandbox değildir;
CPython içinde yüzde yüz garantili saf-Python sandbox mümkün değildir (bkz.
PyPy sandbox / Python resmi dokümantasyonu). Amaç, **bilinen, yaygın kaçış
ailelerini** (introspection tabanlı) kapatmak ve dahili
otomasyon/makro senaryoları için savunma derinliği sağlamaktır — dış
kaynaklı/güvenilmeyen script çalıştırma garantisi vermez (script_api.py'deki
mevcut uyarı korunuyor).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

# Erişimi (attribute olarak okunması) tamamen yasak dunder isimler.
# Not: ``__init__``/``__repr__``/``__str__``/``__len__`` gibi kullanıcı
# script'lerinin kendi sınıflarında *tanımlaması* gayet normaldir (bkz.
# _is_safe_dunder_def) — burada listelenenler yalnızca *attribute erişimi*
# (``x.__foo__``) olarak reddedilir.
_FORBIDDEN_DUNDER_ATTRS = {
    "__class__", "__bases__", "__base__", "__subclasses__", "__mro__",
    "__globals__", "__code__", "__closure__", "__builtins__", "__import__",
    "__loader__", "__spec__", "__dict__", "__getattribute__", "__setattr__",
    "__delattr__", "__reduce__", "__reduce_ex__", "__module__", "__self__",
    "__func__", "__objclass__", "__init_subclass__", "__subclasshook__",
}

# Çağrılması tamamen yasak isimler (kısıtlı builtins'te zaten yoklar, ama
# script kendi kapsamında yeniden tanımlayıp dolaylı erişmeye çalışabilir —
# statik seviyede de kapatılır; savunma derinliği).
_FORBIDDEN_CALL_NAMES = {
    "eval", "exec", "compile", "__import__", "getattr", "setattr",
    "delattr", "vars", "globals", "locals", "open", "input",
    "breakpoint", "memoryview", "help",
}

# ``def __init__`` gibi kullanıcı tanımı olarak serbest bırakılan dunder'lar
# (fonksiyon/metod *tanımlamak* attribute *okumak* değildir, zararsızdır).
_SAFE_DUNDER_DEFS = {
    "__init__", "__repr__", "__str__", "__len__", "__eq__", "__lt__",
    "__gt__", "__le__", "__ge__", "__hash__", "__iter__", "__next__",
    "__call__", "__enter__", "__exit__",
}


class SandboxViolation(Exception):
    """Kaynak kod, statik güvenlik denetiminden geçemedi."""

    def __init__(self, reason: str, lineno: int | None = None) -> None:
        self.reason = reason
        self.lineno = lineno
        location = f" (satır {lineno})" if lineno else ""
        super().__init__(f"{reason}{location}")


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str | None = None
    lineno: int | None = None


class _EscapeVisitor(ast.NodeVisitor):
    """Yasak desenleri arayan tek-geçişli AST ziyaretçisi."""

    def __init__(self) -> None:
        self.violation: SandboxViolation | None = None

    def _flag(self, reason: str, node: ast.AST) -> None:
        if self.violation is None:  # ilk ihlali raporla, tarama durabilir
            self.violation = SandboxViolation(reason, getattr(node, "lineno", None))

    # -- import ifadeleri -----------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self._flag("`import` ifadesi script sandbox'ında yasak", node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self._flag("`from ... import` ifadesi script sandbox'ında yasak", node)

    # -- attribute erişimi (dunder introspection) ------------------------
    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr in _FORBIDDEN_DUNDER_ATTRS:
            self._flag(
                f"yasak attribute erişimi: `.{node.attr}` "
                "(introspection tabanlı sandbox kaçışı)",
                node,
            )
            return
        self.generic_visit(node)

    # -- fonksiyon tanımları: yalnızca güvenli dunder'lara izin ver -------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        if node.name.startswith("__") and node.name.endswith("__"):
            if node.name not in _SAFE_DUNDER_DEFS:
                self._flag(
                    f"yasak dunder metod tanımı: `def {node.name}`", node
                )
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    # -- tehlikeli çağrılar ----------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALL_NAMES:
            self._flag(f"yasak çağrı: `{func.id}(...)`", node)
        self.generic_visit(node)

    # -- isim erişimi: dunder isimleri doğrudan kullanmayı da kapat -------
    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id in _FORBIDDEN_CALL_NAMES and isinstance(node.ctx, ast.Load):
            # `f = eval; f(...)` gibi dolaylı çağrı denemesi de yakalanır.
            self._flag(f"yasak isim referansı: `{node.id}`", node)
        self.generic_visit(node)


def check_source(source: str) -> GuardResult:
    """Kaynağı statik olarak denetler; sözdizimi hatası da bir ihlal sayılır
    (böylece çağıran taraf ``ast.parse``'ı iki kez yapmak zorunda kalmaz)."""
    try:
        tree = ast.parse(source, filename="<harita-script>", mode="exec")
    except SyntaxError as exc:
        return GuardResult(ok=False, reason=f"SözdizimiHatası: {exc}", lineno=exc.lineno)

    visitor = _EscapeVisitor()
    visitor.visit(tree)
    if visitor.violation is not None:
        return GuardResult(
            ok=False, reason=visitor.violation.reason, lineno=visitor.violation.lineno
        )
    return GuardResult(ok=True)


def assert_safe(source: str) -> None:
    """`check_source`'un exception fırlatan biçimi."""
    result = check_source(source)
    if not result.ok:
        raise SandboxViolation(result.reason or "bilinmeyen ihlal", result.lineno)
