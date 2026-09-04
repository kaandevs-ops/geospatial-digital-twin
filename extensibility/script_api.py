"""
Script API
==========

Roadmap Phase 14 - "Script API (Python/Lua/JavaScript)".

Python desteği doğrudan (stdlib `exec`, kısıtlı `globals`) sağlanır. Lua/JS
desteği ise *opsiyonel* bağımlılıklar (`lupa`, `PyMiniRacer` sırasıyla)
üzerinden - ana projenin `plugin_loader.py`'daki `_try(module)` deseniyle
aynı şekilde, yoklarsa `ScriptEngineUnavailable` fırlatılır (import zamanında
değil, çağrı zamanında - böylece bu modül onlar kurulu olmadan da import
edilebilir).

Güvenlik notu: Python sandbox'ı "tam" bir güvenlik sınırı değildir (CPython'da
saf-Python tabanlı mükemmel sandbox mümkün değildir); burada amaç, script
API'ye açılan yüzeyi *kazayla* geniş tutmamaktır (örn. `open`, `__import__`
varsayılan olarak kapalı). Güvenilmeyen/dış kaynaklı script çalıştırmak için
yeterli değildir - yalnızca dahili otomasyon/makro senaryoları için tasarlanmıştır.
"""

from __future__ import annotations

import builtins
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .sandbox_guard import check_source

# Faz 14 -> A14 güvenlik sertleştirmesi: bir script'in tek çalıştırmada
# alabileceği en uzun süre (saniye). `signal.alarm` yalnızca ana iş
# parçacığında ve Unix'te desteklenir; kullanılamıyorsa (Windows, worker
# thread) zaman aşımı sessizce uygulanmaz — bu bilinen bir sınırdır (bkz.
# `extensibility/README.md` "A14 güvenlik sertleştirmesi" bölümü).
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 5.0


class ScriptTimeoutError(Exception):
    """Script, izin verilen süre içinde tamamlanamadı (olası sonsuz döngü)."""


def _supports_alarm_timeout() -> bool:
    if not hasattr(signal, "SIGALRM") or not hasattr(signal, "alarm"):
        return False
    try:
        # `signal.signal` yalnızca ana iş parçacığında çağrılabilir; başka
        # bir thread'den çağrılırsa ValueError fırlatır.
        current = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, current)
        return True
    except (ValueError, RuntimeError):
        return False


class ScriptEngineUnavailable(Exception):
    """İstenen script motorunun opsiyonel bağımlılığı kurulu değil."""


class ScriptError(Exception):
    """Script çalıştırılırken (Python/Lua/JS) oluşan hatayı sarmalar."""


_SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "float",
        "int",
        "len",
        "list",
        "map",
        "max",
        "min",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
        "print",
        "isinstance",
        "True",
        "False",
        "None",
        # `class` ifadesinin çalışması için CPython'ın gerektirdiği dahili
        # kanca — introspection/erişim riski taşımaz, salt dil mekaniğidir;
        # kullanıcı tanımlı sınıflar (`sandbox_guard`'ın izin verdiği
        # `def __init__` vb.) bu olmadan hiç çalışmaz.
        "__build_class__",
    )
    if hasattr(builtins, name)
}


@dataclass
class ScriptResult:
    ok: bool
    result: Any = None
    stdout: str = ""
    error: str | None = None
    elapsed_seconds: float = 0.0


@dataclass
class ScriptContext:
    """Script'e enjekte edilecek isim -> değer eşlemesi (API yüzeyi)."""

    variables: dict[str, Any] = field(default_factory=dict)
    functions: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def as_globals(self) -> dict[str, Any]:
        merged = dict(self.variables)
        merged.update(self.functions)
        return merged


class PythonScriptEngine:
    """`exec` tabanlı, kısıtlı-builtins Python script motoru.

    A14 güvenlik sertleştirmesi (bkz. `sandbox_guard.py`): `exec`'ten önce
    kaynak, statik AST denetiminden geçirilir (introspection tabanlı
    dunder/subclasses kaçışları, `eval`/`getattr`/`open` gibi tehlikeli
    çağrılar ve `import` reddedilir); ayrıca ana iş parçacığında/Unix'te
    `SIGALRM` tabanlı bir yürütme zaman aşımı uygulanır (sonsuz döngü DoS'una
    karşı).
    """

    def __init__(self, timeout_seconds: float = DEFAULT_SCRIPT_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def run(self, source: str, context: ScriptContext | None = None) -> ScriptResult:
        context = context or ScriptContext()

        # 1) Statik güvenlik denetimi — exec'ten ÖNCE, yan etkisiz.
        guard = check_source(source)
        if not guard.ok:
            return ScriptResult(
                ok=False,
                error=f"SandboxViolation: {guard.reason}",
            )

        scope: dict[str, Any] = {
            "__builtins__": dict(_SAFE_BUILTINS),
            # `class` ifadesi (üzerinden `__build_class__`) modül seviyesinde
            # `__name__`'in tanımlı olmasını bekler; introspection riski
            # taşımaz (sabit, zararsız bir string).
            "__name__": "<harita-script>",
        }
        scope.update(context.as_globals())
        scope["_result"] = None

        captured: list[str] = []

        def _capture_print(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
            captured.append(" ".join(str(a) for a in args))

        scope["__builtins__"]["print"] = _capture_print

        # 2) Yürütme zaman aşımı (mümkünse). `SIGALRM` desteklenmiyorsa
        #    (Windows / ana olmayan thread) zaman aşımı uygulanmadan devam
        #    edilir — bilinen sınır, README'de belgelendi.
        use_alarm = _supports_alarm_timeout() and self.timeout_seconds > 0
        previous_handler = None

        def _on_alarm(signum: int, frame: Any) -> None:  # noqa: ANN401
            raise ScriptTimeoutError(
                f"Script {self.timeout_seconds}s içinde tamamlanamadı (olası sonsuz döngü)"
            )

        start = time.perf_counter()
        try:
            if use_alarm:
                previous_handler = signal.signal(signal.SIGALRM, _on_alarm)
                signal.setitimer(signal.ITIMER_REAL, self.timeout_seconds)
            try:
                # Son ifadeyi `_result`'a atamaya çalış (basit tek-satır
                # ifadeler için kullanışlı); başarısız olursa sorun değil,
                # exec devam eder.
                exec(compile(source, "<harita-script>", "exec"), scope)  # noqa: S102
            finally:
                if use_alarm:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, previous_handler)
            elapsed = time.perf_counter() - start
            return ScriptResult(
                ok=True,
                result=scope.get("_result"),
                stdout="\n".join(captured),
                elapsed_seconds=elapsed,
            )
        except Exception as exc:  # noqa: BLE001 - script hatası kullanıcıya döndürülür
            elapsed = time.perf_counter() - start
            return ScriptResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                stdout="\n".join(captured),
                elapsed_seconds=elapsed,
            )


class LuaScriptEngine:
    """`lupa` (opsiyonel bağımlılık) üzerinden Lua script desteği."""

    def __init__(self) -> None:
        self._lupa = None

    def _ensure(self) -> Any:
        if self._lupa is None:
            try:
                import lupa  # type: ignore

                self._lupa = lupa
            except Exception as exc:  # noqa: BLE001
                raise ScriptEngineUnavailable(
                    "Lua desteği için 'lupa' paketi kurulu değil."
                ) from exc
        return self._lupa

    def run(self, source: str, context: ScriptContext | None = None) -> ScriptResult:
        lupa = self._ensure()
        context = context or ScriptContext()
        start = time.perf_counter()
        try:
            runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
            for key, value in context.as_globals().items():
                runtime.globals()[key] = value
            result = runtime.execute(source)
            return ScriptResult(ok=True, result=result, elapsed_seconds=time.perf_counter() - start)
        except Exception as exc:  # noqa: BLE001
            return ScriptResult(
                ok=False, error=str(exc), elapsed_seconds=time.perf_counter() - start
            )


class JavaScriptEngine:
    """`PyMiniRacer` (opsiyonel bağımlılık) üzerinden JS script desteği."""

    def __init__(self) -> None:
        self._ctx = None

    def _ensure(self) -> Any:
        if self._ctx is None:
            try:
                from py_mini_racer import MiniRacer  # type: ignore

                self._ctx = MiniRacer()
            except Exception as exc:  # noqa: BLE001
                raise ScriptEngineUnavailable(
                    "JavaScript desteği için 'py_mini_racer' paketi kurulu değil."
                ) from exc
        return self._ctx

    def run(self, source: str, context: ScriptContext | None = None) -> ScriptResult:
        ctx = self._ensure()
        context = context or ScriptContext()
        start = time.perf_counter()
        try:
            for key, value in context.variables.items():
                ctx.eval(f"var {key} = {value!r};")
            result = ctx.eval(source)
            return ScriptResult(ok=True, result=result, elapsed_seconds=time.perf_counter() - start)
        except Exception as exc:  # noqa: BLE001
            return ScriptResult(
                ok=False, error=str(exc), elapsed_seconds=time.perf_counter() - start
            )


class ScriptAPI:
    """Çoklu dil script motorlarına tek giriş noktası.

    ``api.run("python", "x = 1 + 1\\n_result = x")`` gibi kullanılır.
    """

    def __init__(self) -> None:
        self._engines: dict[str, Any] = {
            "python": PythonScriptEngine(),
            "lua": LuaScriptEngine(),
            "javascript": JavaScriptEngine(),
            "js": JavaScriptEngine(),
        }
        self._global_context = ScriptContext()

    def register_function(self, name: str, fn: Callable[..., Any]) -> None:
        self._global_context.functions[name] = fn

    def set_variable(self, name: str, value: Any) -> None:
        self._global_context.variables[name] = value

    def run(
        self,
        language: str,
        source: str,
        context: ScriptContext | None = None,
    ) -> ScriptResult:
        language = language.lower()
        if language not in self._engines:
            return ScriptResult(ok=False, error=f"Bilinmeyen dil: {language}")
        merged = ScriptContext(
            variables={**self._global_context.variables, **(context.variables if context else {})},
            functions={**self._global_context.functions, **(context.functions if context else {})},
        )
        try:
            return self._engines[language].run(source, merged)
        except ScriptEngineUnavailable as exc:
            return ScriptResult(ok=False, error=str(exc))

    def available_languages(self) -> list[str]:
        """Hangi dillerin gerçekten çalışabileceğini (opsiyonel bağımlılık
        kurulu mu) kontrol ederek döner. Python her zaman kullanılabilir."""
        available = ["python"]
        try:
            import lupa  # type: ignore  # noqa: F401

            available.append("lua")
        except Exception:  # noqa: BLE001
            pass
        try:
            import py_mini_racer  # type: ignore  # noqa: F401

            available.append("javascript")
        except Exception:  # noqa: BLE001
            pass
        return available
