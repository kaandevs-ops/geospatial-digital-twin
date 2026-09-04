"""AI Assistant - Coklu LLM Saglayici Katmani (GGUF + API-Key)
================================================================

Kullanici talebi: "gguf modelleri api key ile ekleme ozelligi olsun yani
gercekten yapay zeka modelini kullanabileyim".

`llm_bridge.py` (Faz E7) yalnizca Anthropic'e sabit-kodlanmis, tek bir
gercek HTTP koprusu sunuyordu. Bu modul onu genellestirir:

  1. `LLMProvider` Protocol'u - tum saglayicilarin uydugu ortak arayuz:
     `complete(prompt, system=None) -> str`. Girdi/cikti duz metindir;
     "intent JSON'a cevir" gibi gorev-ozel mantik saglayicidan bagimsizdir
     (bkz. `intent_llm_fn` adaptoru asagida).

  2. Uc somut saglayici:
     - `GGUFProvider`      : yerel bir .gguf model dosyasini
       `llama-cpp-python` ile calistirir (kurulu degilse veya model dosyasi
       yoksa acik `ProviderUnavailableError`).
     - `OpenAICompatibleProvider` : `/v1/chat/completions` semasina uyan
       HERHANGI bir servise (OpenAI'nin kendisi, Azure OpenAI, Groq,
       Together AI, veya yerel bir `llama.cpp server`/`Ollama` OpenAI-uyumlu
       endpoint'i) `base_url` + `api_key` ile baglanir.
     - `AnthropicProvider` : Anthropic Messages API'sine baglanir (eski
       `AnthropicLLMBridge` ile ayni HTTP sozlesmesi, fakat duz metin
       donduren genel `LLMProvider` arayuzune uyar).

  3. `create_provider_from_env()` - ortam degiskenlerinden hangi
     saglayicinin kullanilacagini secen fabrika fonksiyonu:

        HARITA_LLM_BACKEND = "gguf" | "openai" | "anthropic" | "none"
        HARITA_GGUF_MODEL_PATH        (gguf icin)
        HARITA_GGUF_N_CTX             (opsiyonel, varsayilan 4096)
        HARITA_GGUF_N_GPU_LAYERS      (opsiyonel, varsayilan 0)
        OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL   (openai icin)
        ANTHROPIC_API_KEY / ANTHROPIC_MODEL               (anthropic icin)

     `HARITA_LLM_BACKEND` tanimli degilse veya "none" ise `None` doner
     (cagiran taraf kural-tabanli/heuristic davranisa duser - proje
     genelindeki "kurulu/yapilandirilmis degilse sessizce mevcut
     davranisa dus" ilkesiyle birebir).

  4. `intent_llm_fn(provider, ...)` - herhangi bir `LLMProvider`'i,
     `ai_assistant.intent_parser.IntentParser(llm_fn=...)` sozlesmesine
     (`str -> list[dict]`) uyarlayan adaptor. Boylece GGUF/OpenAI/Anthropic
     saglayicilarindan HERHANGI biri dogal-dil komut ayristirmada
     kullanilabilir; ayni adaptor `ai_assistant.report_narrator` gibi diger
     "metne cevir" gorevlerinde de yeniden kullanilir (bkz. o modul).

Tasarim ilkesi (E6/E7 ile ortak): **kurulu/yapilandirilmis degilse
sessizce/acikca dus** - stdlib-only cekirdek her zaman calisir; gercek bir
LLM sadece acikca istendiginde ve kurulu/yapilandirilmisken devreye girer.
`llama-cpp-python` bu ortamda kurulu degildir ve `pyproject.toml`'daki
`[llm]` extra'sinda tanimlidir; gercek bir agirlik dosyasi bu pakete
gomulu degildir (boyut/lisans belirsizligi) - testler gercek modelsiz,
saglayicinin HTTP/CLI katmanini enjekte edilebilir sahte (fake) tasiyicilar
ile dogrular (bkz. `tests/test_ai_llm_providers.py`).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, runtime_checkable

__all__ = [
    "LLMProvider",
    "ProviderUnavailableError",
    "LLMCallError",
    "GGUFProvider",
    "GGUFConfig",
    "OpenAICompatibleProvider",
    "OpenAICompatibleConfig",
    "AnthropicProvider",
    "AnthropicConfig",
    "create_provider_from_env",
    "create_provider_from_config",
    "InvalidProviderConfigError",
    "intent_llm_fn",
    "DEFAULT_INTENT_SYSTEM_PROMPT",
    "make_fixed_provider",
]


class ProviderUnavailableError(RuntimeError):
    """Saglayici kullanilmaya calisildi ama kurulu/yapilandirilmis degil
    (paket eksik, model dosyasi yok, API anahtari tanimli degil)."""


class LLMCallError(RuntimeError):
    """Saglayici kurulu/yapilandirilmis ama gercek cagri basarisiz oldu
    (ag hatasi, zaman asimi, beklenmeyen yanit sekli)."""


@runtime_checkable
class LLMProvider(Protocol):
    """Tum LLM saglayicilarinin uydugu ortak, gorev-bagimsiz arayuz."""

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        """`prompt` (+ opsiyonel `system` talimati) icin duz-metin yanit
        dondurur. Kurulu/yapilandirilmis degilse `ProviderUnavailableError`,
        cagri basarisiz olursa `LLMCallError` firlatir."""
        ...


# ============================================================================ #
# 1) GGUF (yerel, llama-cpp-python)
# ============================================================================ #

@dataclass(slots=True)
class GGUFConfig:
    model_path: str
    n_ctx: int = 4096
    n_gpu_layers: int = 0
    temperature: float = 0.2
    max_tokens: int = 512
    chat_format: Optional[str] = None  # None -> llama-cpp-python kendi tahminine birakir


class GGUFProvider:
    """Yerel bir `.gguf` modelini `llama-cpp-python` ile calistirir.

    Model tembel (lazy) yuklenir - `GGUFProvider(config)` yaratmak henuz
    dosyayi acmaz; ilk `complete()` cagrisinda yuklenir ve tekrar
    kullanilmak uzere onbelleklenir. Boylece paket/model kurulu degilken
    bile nesne olusturmak/DI icin enjekte etmek guvenlidir - hata yalnizca
    gercekten cagrildiginda ortaya cikar.
    """

    def __init__(self, config: GGUFConfig) -> None:
        self._config = config
        self._llm: Any = None

    @property
    def is_available(self) -> bool:
        """`llama_cpp` paketi kurulu VE model dosyasi diskte mevcut mu."""
        try:
            import llama_cpp  # noqa: F401
        except ImportError:
            return False
        return os.path.isfile(self._config.model_path)

    def _ensure_loaded(self) -> Any:
        if self._llm is not None:
            return self._llm
        try:
            from llama_cpp import Llama  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ProviderUnavailableError(
                "llama-cpp-python kurulu degil. Kurulum icin: "
                "`pip install harita-modelleme[llm]` veya "
                "`pip install llama-cpp-python`."
            ) from exc
        if not os.path.isfile(self._config.model_path):
            raise ProviderUnavailableError(
                f"GGUF model dosyasi bulunamadi: {self._config.model_path!r}. "
                "HARITA_GGUF_MODEL_PATH ortam degiskenini gecerli bir .gguf "
                "dosyasina isaret edecek sekilde ayarlayin."
            )
        try:
            self._llm = Llama(
                model_path=self._config.model_path,
                n_ctx=self._config.n_ctx,
                n_gpu_layers=self._config.n_gpu_layers,
                chat_format=self._config.chat_format,
                verbose=False,
            )
        except Exception as exc:  # pragma: no cover - ortam bagimli gercek yukleme hatasi
            raise LLMCallError(f"GGUF modeli yuklenemedi: {exc}") from exc
        return self._llm

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        llm = self._ensure_loaded()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = llm.create_chat_completion(
                messages=messages,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
            )
        except Exception as exc:  # pragma: no cover - ortam bagimli gercek cikarim hatasi
            raise LLMCallError(f"GGUF cikarim cagrisi basarisiz: {exc}") from exc
        try:
            return response["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMCallError(
                f"GGUF yaniti beklenmeyen sekilde: {response!r}"
            ) from exc


# ============================================================================ #
# 2) OpenAI-uyumlu HTTP API (OpenAI, Azure, Groq, Together, yerel sunucular, ...)
# ============================================================================ #

@dataclass(slots=True)
class OpenAICompatibleConfig:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout_s: float = 30.0
    max_tokens: int = 512
    temperature: float = 0.2
    extra_headers: dict = field(default_factory=dict)


class OpenAICompatibleProvider:
    """`/v1/chat/completions` semasina uyan herhangi bir API-key tabanli
    servise baglanir. `base_url` degistirilerek OpenAI'nin kendisi disinda
    Azure OpenAI, Groq, Together AI, veya OpenAI-uyumlu bir yerel sunucu
    (orn. `llama.cpp --server`, Ollama'nin `/v1` uyumluluk katmani) da
    hedeflenebilir - bu sayede kullanici kendi yerel/uzak modelini de
    "API key ile" bu saglayici uzerinden ekleyebilir.

    `urllib.request.urlopen`, `opener` parametresiyle degistirilebilir -
    test/DI amaciyla gercek ag cagrisi yapmayan bir sahte (fake) tasiyici
    enjekte edilebilir.
    """

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
    ) -> None:
        self._config = config
        self._opener = opener or _default_opener

    @property
    def is_available(self) -> bool:
        return bool(self._config.api_key)

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        if not self._config.api_key:
            raise ProviderUnavailableError(
                "OpenAI-uyumlu saglayici icin api_key tanimli degil "
                "(OPENAI_API_KEY ortam degiskeni veya OpenAICompatibleConfig.api_key)."
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self._config.model,
            "messages": messages,
            "max_tokens": self._config.max_tokens,
            "temperature": self._config.temperature,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
            **self._config.extra_headers,
        }
        request = urllib.request.Request(
            f"{self._config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        raw_body = _perform_request(self._opener, request, self._config.timeout_s)
        try:
            body = json.loads(raw_body)
            return body["choices"][0]["message"]["content"] or ""
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMCallError(
                f"OpenAI-uyumlu API yaniti beklenmeyen sekilde: {raw_body[:300]!r}"
            ) from exc


# ============================================================================ #
# 3) Anthropic Messages API
# ============================================================================ #

@dataclass(slots=True)
class AnthropicConfig:
    api_key: str
    base_url: str = "https://api.anthropic.com/v1"
    model: str = "claude-sonnet-4-6"
    timeout_s: float = 30.0
    max_tokens: int = 512
    anthropic_version: str = "2023-06-01"


class AnthropicProvider:
    """Anthropic Messages API'sine baglanir; `AnthropicLLMBridge`
    (`llm_bridge.py`, Faz E7) ile ayni HTTP sozlesmesini kullanir ancak
    duz-metin donduren genel `LLMProvider` arayuzune uyar (JSON-intent'e
    cevirme sorumlulugu `intent_llm_fn` adaptorunde, saglayicida degil)."""

    def __init__(
        self,
        config: AnthropicConfig,
        *,
        opener: Optional[Callable[[urllib.request.Request, float], Any]] = None,
    ) -> None:
        self._config = config
        self._opener = opener or _default_opener

    @property
    def is_available(self) -> bool:
        return bool(self._config.api_key)

    def complete(self, prompt: str, system: Optional[str] = None) -> str:
        if not self._config.api_key:
            raise ProviderUnavailableError(
                "Anthropic saglayicisi icin api_key tanimli degil "
                "(ANTHROPIC_API_KEY ortam degiskeni veya AnthropicConfig.api_key)."
            )
        payload: dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        request = urllib.request.Request(
            f"{self._config.base_url.rstrip('/')}/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._config.api_key,
                "anthropic-version": self._config.anthropic_version,
            },
            method="POST",
        )
        raw_body = _perform_request(self._opener, request, self._config.timeout_s)
        try:
            body = json.loads(raw_body)
            blocks = body.get("content", [])
            text_parts = [
                b.get("text", "") for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return "".join(text_parts)
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            raise LLMCallError(
                f"Anthropic API yaniti beklenmeyen sekilde: {raw_body[:300]!r}"
            ) from exc


class InvalidProviderConfigError(ValueError):
    """`create_provider_from_config` girdisi bilinmeyen/eksik alan içerir."""


def create_provider_from_config(config: dict) -> LLMProvider:
    """Faz 4.1 — AI ayarları paneli için çalışma-zamanı (runtime) fabrika.

    `create_provider_from_env`'in ortam değişkeni yerine, arayüzden gelen
    bir sözlükten (JSON body) sağlayıcı üretir. Böylece kullanıcı, süreç
    yeniden başlatmadan/ortam değişkeni ayarlamadan doğrudan arayüzden
    sağlayıcı seçip anahtar/model dosyası girebilir.

    Beklenen `config` şeması:
        {"backend": "gguf", "model_path": "...", "n_ctx"?, "n_gpu_layers"?}
        {"backend": "openai", "api_key": "...", "base_url"?, "model"?}
        {"backend": "anthropic", "api_key": "...", "model"?}
    """
    backend = str(config.get("backend", "")).strip().lower()
    if backend == "gguf":
        model_path = config.get("model_path", "")
        if not model_path:
            raise InvalidProviderConfigError("gguf icin model_path zorunlu.")
        return GGUFProvider(GGUFConfig(
            model_path=model_path,
            n_ctx=int(config.get("n_ctx", 4096)),
            n_gpu_layers=int(config.get("n_gpu_layers", 0)),
        ))
    if backend == "openai":
        api_key = config.get("api_key", "")
        if not api_key:
            raise InvalidProviderConfigError("openai icin api_key zorunlu.")
        return OpenAICompatibleProvider(OpenAICompatibleConfig(
            api_key=api_key,
            base_url=config.get("base_url", "https://api.openai.com/v1"),
            model=config.get("model", "gpt-4o-mini"),
        ))
    if backend == "anthropic":
        api_key = config.get("api_key", "")
        if not api_key:
            raise InvalidProviderConfigError("anthropic icin api_key zorunlu.")
        return AnthropicProvider(AnthropicConfig(
            api_key=api_key,
            model=config.get("model", "claude-sonnet-4-6"),
        ))
    raise InvalidProviderConfigError(
        f"Bilinmeyen backend: {backend!r} (gguf|openai|anthropic olmali)."
    )


def _default_opener(request: urllib.request.Request, timeout_s: float) -> str:
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMCallError(f"HTTP istegi basarisiz: {exc}") from exc


def _perform_request(
    opener: Callable[[urllib.request.Request, float], Any],
    request: urllib.request.Request,
    timeout_s: float,
) -> str:
    return opener(request, timeout_s)


# ============================================================================ #
# Ortam-tabanli fabrika
# ============================================================================ #

def create_provider_from_env(env: Optional[dict] = None) -> Optional[LLMProvider]:
    """`HARITA_LLM_BACKEND` ortam degiskenine gore uygun saglayiciyi
    olusturur. Tanimsiz/"none" ise `None` doner (cagiran taraf kural
    tabanli/heuristic davranisa duser).

    `env` parametresi testlerde gercek `os.environ`'i degistirmeden
    enjeksiyon icin kullanilir; verilmezse `os.environ` okunur.
    """
    e = env if env is not None else os.environ
    backend = (e.get("HARITA_LLM_BACKEND") or "none").strip().lower()

    if backend in ("", "none"):
        return None

    if backend == "gguf":
        model_path = e.get("HARITA_GGUF_MODEL_PATH", "")
        return GGUFProvider(GGUFConfig(
            model_path=model_path,
            n_ctx=int(e.get("HARITA_GGUF_N_CTX", "4096")),
            n_gpu_layers=int(e.get("HARITA_GGUF_N_GPU_LAYERS", "0")),
        ))

    if backend == "openai":
        return OpenAICompatibleProvider(OpenAICompatibleConfig(
            api_key=e.get("OPENAI_API_KEY", ""),
            base_url=e.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=e.get("OPENAI_MODEL", "gpt-4o-mini"),
        ))

    if backend == "anthropic":
        return AnthropicProvider(AnthropicConfig(
            api_key=e.get("ANTHROPIC_API_KEY", ""),
            model=e.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        ))

    raise ValueError(
        f"Bilinmeyen HARITA_LLM_BACKEND={backend!r}. "
        "Gecerli degerler: 'gguf', 'openai', 'anthropic', 'none'."
    )


# ============================================================================ #
# IntentParser adaptoru (llm_bridge.py'deki JSON-ayristirma mantiginin
# saglayicidan bagimsiz genel hali)
# ============================================================================ #

DEFAULT_INTENT_SYSTEM_PROMPT = (
    "Sen bir 3D bina duzenleme komutu ayristiricisisin. Kullanicinin Turkce "
    "(veya Ingilizce) serbest metin komutunu, asagidaki JSON semasina uyan bir "
    "LISTE olarak ayristir ve YALNIZCA bu JSON listesini dondur (baska hicbir "
    "metin, aciklama veya markdown code-fence olmadan):\n\n"
    '[{"action": "<add_floor|remove_floor|change_roof|change_facade|'
    'add_door|remove_door|add_window|remove_window|analyze_building|unknown>", '
    '"target": "building", "parameters": {...}, "confidence": <0.0-1.0>}]\n\n'
    'change_roof icin parameters = {"roof_type": "<flat|gable|hip|...>"}; '
    'change_facade icin parameters = {"material": "<brick|glass|concrete|...>"}. '
    'Komut hicbir eyleme karsilik gelmiyorsa action="unknown" ver. Cumle '
    "birden fazla eylem iceriyorsa listede birden fazla eleman dondur."
)


def intent_llm_fn(
    provider: LLMProvider,
    *,
    system_prompt: str = DEFAULT_INTENT_SYSTEM_PROMPT,
    raise_on_error: bool = False,
) -> Callable[[str], list[dict]]:
    """Herhangi bir `LLMProvider`'i `IntentParser(llm_fn=...)` sozlesmesine
    (`str -> list[dict]`) uyarlar. Saglayici kurulu/yapilandirilmis degilse
    veya cagri basarisiz olursa, `raise_on_error=False` (varsayilan) iken
    sessizce `[]` doner - kural motorunun coz(e)medigi fragman `unknown`
    olarak kalir, hicbir istisna cagiran kodu kirmaz."""

    def _fn(fragment: str) -> list[dict]:
        try:
            raw_text = provider.complete(fragment, system=system_prompt)
        except (ProviderUnavailableError, LLMCallError) as exc:
            if raise_on_error:
                raise
            return []
        return _parse_intent_json(raw_text, raise_on_error=raise_on_error)

    return _fn


def _parse_intent_json(raw_text: str, *, raise_on_error: bool) -> list[dict]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        if raise_on_error:
            raise LLMCallError(f"LLM yaniti gecerli JSON degil: {text[:200]!r}") from exc
        return []
    if not isinstance(parsed, list):
        if raise_on_error:
            raise LLMCallError(f"LLM yaniti bir liste degil: {type(parsed)!r}")
        return []
    return [item for item in parsed if isinstance(item, dict)]


def make_fixed_provider(responses: dict) -> LLMProvider:
    """Test/cevrimdisi kullanim icin: sabit `prompt -> yanit metni` eslemesi
    yapan, gercek ag/model cagrisi yapmayan bir `LLMProvider`.

    `responses` anahtarlari `prompt.strip()` ile eslestirilir; eslesme yoksa
    bos JSON listesi metni ("[]") doner."""

    class _FixedProvider:
        def complete(self, prompt: str, system: Optional[str] = None) -> str:
            return responses.get(prompt.strip(), "[]")

    return _FixedProvider()
