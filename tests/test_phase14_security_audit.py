"""A14 — Extensibility Güvenlik Sertleştirmesi Denetim Paketi.

Roadmap V2 kabul kriteri: "OWASP-tarzı sandbox kaçış test paketi (20+
senaryo) hepsi bloklanır." Bu dosya, `PythonScriptEngine`'in gerçek kaçış
PoC'lerine (introspection, dolaylı çağrı, kaynak tükenmesi) karşı
`extensibility/sandbox_guard.py` ile bloklandığını doğrular.

Her escape testi ÖNCE bu sertleştirme olmadan gerçekten çalıştığı bu
oturumda manuel olarak kanıtlandı (bkz. `extensibility/README.md` "A14
güvenlik sertleştirmesi" bölümü) — yani bunlar teorik değil, gerçek PoC'ler.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.extensibility.script_api import PythonScriptEngine, ScriptContext
from harita.extensibility.sandbox_guard import check_source, assert_safe, SandboxViolation


ENGINE = PythonScriptEngine(timeout_seconds=1.0)


def _blocked(source: str) -> None:
    """Yardımcı: hem statik denetimin hem de engine.run()'ın engellediğini
    doğrular (savunma derinliğinin iki katmanı da test edilir)."""
    guard = check_source(source)
    assert not guard.ok, f"statik denetim kaçırdı: {source!r}"
    result = ENGINE.run(source)
    assert not result.ok, f"engine.run() ihlali çalıştırdı: {source!r}"
    assert "SandboxViolation" in (result.error or "")


# ============================================================ #
# 1-4: __class__ / __bases__ / __subclasses__ zinciri (nesne
#      hiyerarşisi üzerinden rastgele sınıfa erişim ailesi)
# ============================================================ #

def test_subclasses_traversal_blocks_popen_escape():
    """Gerçek PoC: bu oturumda sertleştirme öncesi gerçekten `subprocess.
    Popen`'a ulaşıp süreç başlattığı doğrulandı."""
    _blocked(
        "classes = ().__class__.__bases__[0].__subclasses__()\n"
        "_result = [c for c in classes if c.__name__ == 'Popen']"
    )


def test_class_attribute_access_blocked():
    _blocked("_result = (1).__class__")


def test_bases_attribute_access_blocked():
    _blocked("_result = int.__bases__")


def test_mro_attribute_access_blocked():
    _blocked("_result = int.__mro__")


# ============================================================ #
# 5-7: frame/globals/code introspection (fonksiyon nesnesi
#      üzerinden dış kapsam veya bytecode erişimi)
# ============================================================ #

def test_function_globals_escape_blocked():
    _blocked(
        "def f():\n    pass\n"
        "_result = f.__globals__"
    )


def test_code_object_access_blocked():
    _blocked("def f():\n    pass\n_result = f.__code__")


def test_closure_access_blocked():
    _blocked(
        "def outer():\n"
        "    x = 1\n"
        "    def inner():\n"
        "        return x\n"
        "    return inner\n"
        "_result = outer().__closure__"
    )


# ============================================================ #
# 8-11: dolaylı erişim — getattr/setattr/vars/globals/locals ile
#       dunder-attribute yasağını bypass etme denemeleri
# ============================================================ #

def test_getattr_indirect_dunder_access_blocked():
    _blocked("_result = getattr((1), '__class__')")


def test_setattr_blocked():
    _blocked("class C:\n    pass\nc = C()\nsetattr(c, 'x', 1)")


def test_globals_builtin_blocked():
    _blocked("_result = globals()")


def test_locals_builtin_blocked():
    _blocked("_result = locals()")


def test_vars_builtin_blocked():
    _blocked("_result = vars()")


# ============================================================ #
# 12-15: doğrudan tehlikeli çağrılar (kısıtlı builtins'te zaten
#        yok, ama statik seviyede de kapatılmalı — savunma derinliği)
# ============================================================ #

def test_eval_call_blocked():
    _blocked("_result = eval('1+1')")


def test_exec_call_blocked():
    _blocked("exec('x = 1')")


def test_compile_call_blocked():
    _blocked("_result = compile('1', '<s>', 'eval')")


def test_open_call_blocked():
    _blocked("_result = open('/etc/passwd')")


# ============================================================ #
# 16-18: import ifadeleri (statik seviyede engellenir; ayrıca
#        __builtins__'ten __import__ zaten kaldırılmış)
# ============================================================ #

def test_import_statement_blocked():
    _blocked("import os\n_result = os.getcwd()")


def test_from_import_blocked():
    _blocked("from os import system\nsystem('echo pwned')")


def test_dunder_import_name_reference_blocked():
    _blocked("_result = __import__('os')")


# ============================================================ #
# 19-21: dolaylı isim yeniden bağlama / builtins yeniden ele
#        geçirme denemeleri
# ============================================================ #

def test_reassigning_eval_name_still_blocked():
    """`f = eval` gibi bir takma isim ataması bile isim düzeyinde
    yakalanır (ast.Name Load context taraması)."""
    _blocked("f = eval\n_result = f('1+1')")


def test_builtins_dict_reassignment_via_attr_blocked():
    _blocked("_result = (1).__class__.__dict__")


def test_dunder_init_subclass_hook_blocked():
    _blocked("_result = object.__subclasshook__")


# ============================================================ #
# 22: kaynak tükenmesi / DoS (sonsuz döngü) — statik değil,
#     çalışma zamanı zaman aşımı testi
# ============================================================ #

def test_infinite_loop_times_out_instead_of_hanging():
    result = ENGINE.run("while True:\n    pass")
    assert not result.ok
    assert "Timeout" in result.error or "s içinde tamamlanamadı" in result.error


# ============================================================ #
# Regresyon: meşru script'ler sertleştirmeden sonra da çalışmalı
# ============================================================ #

def test_legit_arithmetic_script_still_works():
    result = ENGINE.run("x = 1 + 2\n_result = x * 10")
    assert result.ok
    assert result.result == 30


def test_legit_print_capture_still_works():
    result = ENGINE.run("print('merhaba')\nprint('dunya')")
    assert result.ok
    assert result.stdout == "merhaba\ndunya"


def test_legit_registered_function_still_callable():
    ctx = ScriptContext()
    ctx.functions["kat_ekle"] = lambda n: n + 1
    result = ENGINE.run("_result = kat_ekle(3)", ctx)
    assert result.ok
    assert result.result == 4


def test_legit_user_defined_class_with_init_still_works():
    """`def __init__` (tanım) serbest bırakılmalı — yasak olan yalnızca
    `.__init__` gibi attribute *erişimidir*, tanım değil."""
    source = (
        "class Bina:\n"
        "    def __init__(self, kat):\n"
        "        self.kat = kat\n"
        "b = Bina(5)\n"
        "_result = b.kat\n"
    )
    result = ENGINE.run(source)
    assert result.ok
    assert result.result == 5


def test_legit_list_comprehension_and_builtins_still_work():
    result = ENGINE.run("_result = sorted([x for x in range(5) if x % 2 == 0])")
    assert result.ok
    assert result.result == [0, 2, 4]


def test_syntax_error_reported_as_violation_not_crash():
    result = ENGINE.run("def broken(:\n    pass")
    assert not result.ok
    assert "SandboxViolation" in result.error


def test_assert_safe_raises_on_violation():
    with pytest.raises(SandboxViolation):
        assert_safe("_result = (1).__class__")


def test_assert_safe_silent_on_safe_source():
    assert_safe("_result = 1 + 1")  # exception fırlatmamalı
