"""Faz AI-1: Coklu LLM saglayici katmani testleri.

Gercek ag erisimi bu ortamda yok - HTTP tabanli saglayicilar (OpenAI-uyumlu,
Anthropic) icin `opener` enjeksiyon noktasi kullanilarak sahte (fake) bir
HTTP tasiyici enjekte edilir (gercek `urllib.request.urlopen` hicbir zaman
cagrilmaz). GGUF saglayicisi icin `llama-cpp-python` kurulu degilse
`ProviderUnavailableError` firlatildigi dogrulanir (kurulumsuz ortamin
kendisi test edilir - bu da gercek/dogru bir davranistir).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from harita.ai_assistant.llm_providers import (  # noqa: E402
    AnthropicConfig,
    AnthropicProvider,
    GGUFConfig,
    GGUFProvider,
    LLMCallError,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ProviderUnavailableError,
    create_provider_from_env,
    intent_llm_fn,
    make_fixed_provider,
)
from harita.ai_assistant.report_narrator import (  # noqa: E402
    narrate_facade_compliance,
    narrate_room_compliance,
)
from harita.building_reconstruction.facade_generator import FacadeComplianceReport  # noqa: E402
from harita.building_reconstruction.room_generator import (  # noqa: E402
    RoomComplianceIssue,
    RoomComplianceReport,
)

_PASS = 0
_FAIL = 0


def check(name: str, condition: bool) -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
    else:
        _FAIL += 1
        print(f"FAIL: {name}")


# ---------------------------------------------------------------------- #
# GGUFProvider
# ---------------------------------------------------------------------- #


def test_gguf_unavailable_without_package_or_model():
    provider = GGUFProvider(GGUFConfig(model_path="/nonexistent/model.gguf"))
    check("gguf.is_available false", provider.is_available is False)
    try:
        provider.complete("merhaba")
        check("gguf.complete raises when unavailable", False)
    except ProviderUnavailableError:
        check("gguf.complete raises when unavailable", True)


def test_gguf_config_lazy_construction_never_touches_disk():
    # Nesne yaratmak (henuz complete() cagrilmadan) hicbir dosya/paket
    # erisimi denememeli - DI icin guvenli olmali.
    provider = GGUFProvider(GGUFConfig(model_path="/definitely/does/not/exist.gguf"))
    check("gguf provider constructs without error", provider is not None)


# ---------------------------------------------------------------------- #
# OpenAICompatibleProvider (fake opener - gercek ag yok)
# ---------------------------------------------------------------------- #


def _fake_opener_factory(response_body: str, capture: dict):
    def _opener(request, timeout_s):
        capture["url"] = request.full_url
        capture["headers"] = dict(request.headers)
        capture["body"] = json.loads(request.data.decode("utf-8"))
        capture["timeout"] = timeout_s
        return response_body

    return _opener


def test_openai_compatible_success():
    capture: dict = {}
    body = json.dumps({"choices": [{"message": {"content": "merhaba dunya"}}]})
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(api_key="sk-test", base_url="https://api.example.com/v1"),
        opener=_fake_opener_factory(body, capture),
    )
    result = provider.complete("selam", system="sen bir asistansin")
    check("openai.complete returns content", result == "merhaba dunya")
    check("openai.url correct", capture["url"] == "https://api.example.com/v1/chat/completions")
    check("openai.auth header", capture["headers"].get("Authorization") == "Bearer sk-test")
    check(
        "openai.system in messages",
        capture["body"]["messages"][0]["content"] == "sen bir asistansin",
    )


def test_openai_compatible_missing_key_raises_unavailable():
    provider = OpenAICompatibleProvider(OpenAICompatibleConfig(api_key=""))
    try:
        provider.complete("x")
        check("openai missing key raises", False)
    except ProviderUnavailableError:
        check("openai missing key raises", True)


def test_openai_compatible_bad_response_raises_call_error():
    capture: dict = {}
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(api_key="sk-test"),
        opener=_fake_opener_factory("not json at all", capture),
    )
    try:
        provider.complete("x")
        check("openai bad response raises LLMCallError", False)
    except LLMCallError:
        check("openai bad response raises LLMCallError", True)


# ---------------------------------------------------------------------- #
# AnthropicProvider (fake opener)
# ---------------------------------------------------------------------- #


def test_anthropic_success():
    capture: dict = {}
    body = json.dumps({"content": [{"type": "text", "text": "cevap metni"}]})
    provider = AnthropicProvider(
        AnthropicConfig(api_key="ak-test"),
        opener=_fake_opener_factory(body, capture),
    )
    result = provider.complete("prompt")
    check("anthropic.complete returns text", result == "cevap metni")
    check("anthropic.header x-api-key", capture["headers"].get("X-api-key") == "ak-test")


def test_anthropic_missing_key_raises_unavailable():
    provider = AnthropicProvider(AnthropicConfig(api_key=""))
    try:
        provider.complete("x")
        check("anthropic missing key raises", False)
    except ProviderUnavailableError:
        check("anthropic missing key raises", True)


# ---------------------------------------------------------------------- #
# create_provider_from_env
# ---------------------------------------------------------------------- #


def test_create_provider_from_env_none_by_default():
    provider = create_provider_from_env(env={})
    check("env default is None", provider is None)


def test_create_provider_from_env_gguf():
    provider = create_provider_from_env(
        env={
            "HARITA_LLM_BACKEND": "gguf",
            "HARITA_GGUF_MODEL_PATH": "/tmp/model.gguf",
            "HARITA_GGUF_N_CTX": "2048",
        }
    )
    check("env gguf provider type", isinstance(provider, GGUFProvider))


def test_create_provider_from_env_openai():
    provider = create_provider_from_env(
        env={
            "HARITA_LLM_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-abc",
            "OPENAI_MODEL": "gpt-4o-mini",
        }
    )
    check("env openai provider type", isinstance(provider, OpenAICompatibleProvider))


def test_create_provider_from_env_anthropic():
    provider = create_provider_from_env(
        env={
            "HARITA_LLM_BACKEND": "anthropic",
            "ANTHROPIC_API_KEY": "ak-abc",
        }
    )
    check("env anthropic provider type", isinstance(provider, AnthropicProvider))


def test_create_provider_from_env_unknown_raises():
    try:
        create_provider_from_env(env={"HARITA_LLM_BACKEND": "bogus"})
        check("env unknown backend raises", False)
    except ValueError:
        check("env unknown backend raises", True)


# ---------------------------------------------------------------------- #
# intent_llm_fn adapter
# ---------------------------------------------------------------------- #


def test_intent_llm_fn_parses_json_list():
    provider = make_fixed_provider(
        {
            "bir kat ekle": json.dumps(
                [{"action": "add_floor", "target": "building", "parameters": {}, "confidence": 0.9}]
            ),
        }
    )
    fn = intent_llm_fn(provider)
    result = fn("bir kat ekle")
    check("intent_llm_fn parses list", isinstance(result, list) and len(result) == 1)
    check("intent_llm_fn action correct", result[0]["action"] == "add_floor")


def test_intent_llm_fn_strips_markdown_fence():
    provider = make_fixed_provider(
        {
            "test": '```json\n[{"action": "unknown"}]\n```',
        }
    )
    fn = intent_llm_fn(provider)
    result = fn("test")
    check("intent_llm_fn strips fence", result == [{"action": "unknown"}])


def test_intent_llm_fn_silently_falls_back_on_unavailable():
    class _Unavailable:
        def complete(self, prompt, system=None):
            raise ProviderUnavailableError("yok")

    fn = intent_llm_fn(_Unavailable())
    check("intent_llm_fn silent fallback", fn("herhangi bir sey") == [])


def test_intent_llm_fn_raise_on_error_true():
    class _Unavailable:
        def complete(self, prompt, system=None):
            raise ProviderUnavailableError("yok")

    fn = intent_llm_fn(_Unavailable(), raise_on_error=True)
    try:
        fn("x")
        check("intent_llm_fn raise_on_error propagates", False)
    except ProviderUnavailableError:
        check("intent_llm_fn raise_on_error propagates", True)


# ---------------------------------------------------------------------- #
# report_narrator
# ---------------------------------------------------------------------- #


def test_narrate_facade_compliance_fallback_no_provider():
    report = FacadeComplianceReport(
        wall_area_m2=100.0,
        window_area_m2=5.0,
        window_wall_ratio=0.05,
        min_required_ratio=0.12,
        meets_window_ratio=False,
        floor_count=6,
        requires_fire_escape=True,
        issues=["pencere/duvar orani 0.050 < asgari 0.120"],
    )
    text = narrate_facade_compliance(report)
    check("narrate facade fallback non-empty", len(text) > 0)
    check("narrate facade fallback mentions issue", "0.050" in text or "%5.0" in text)


def test_narrate_facade_compliance_with_provider():
    report = FacadeComplianceReport(
        wall_area_m2=100.0,
        window_area_m2=20.0,
        window_wall_ratio=0.20,
        min_required_ratio=0.12,
        meets_window_ratio=True,
        floor_count=2,
        requires_fire_escape=False,
        issues=[],
    )
    provider = make_fixed_provider({})

    class _AlwaysProvider:
        def complete(self, prompt, system=None):
            return "LLM tarafindan uretilmis dogal dil ozet."

    text = narrate_facade_compliance(report, provider=_AlwaysProvider())
    check("narrate facade uses provider text", text == "LLM tarafindan uretilmis dogal dil ozet.")


def test_narrate_facade_compliance_provider_failure_falls_back():
    report = FacadeComplianceReport(
        wall_area_m2=100.0,
        window_area_m2=20.0,
        window_wall_ratio=0.20,
        min_required_ratio=0.12,
        meets_window_ratio=True,
        floor_count=2,
        requires_fire_escape=False,
        issues=[],
    )

    class _Broken:
        def complete(self, prompt, system=None):
            raise LLMCallError("ag hatasi")

    text = narrate_facade_compliance(report, provider=_Broken())
    check(
        "narrate facade falls back on provider error",
        "%20.0" in text or "0.20" in text.replace(",", "."),
    )


def test_narrate_room_compliance_fallback():
    report = RoomComplianceReport(
        total_rooms=3,
        issues=[RoomComplianceIssue(room_id=1, room_type="yatak_odasi", reason="alan yetersiz")],
    )
    text = narrate_room_compliance(report)
    check("narrate room fallback mentions room", "yatak_odasi" in text and "alan yetersiz" in text)


def test_narrate_room_compliance_all_ok():
    report = RoomComplianceReport(total_rooms=3, issues=[])
    text = narrate_room_compliance(report)
    check("narrate room all-ok text", "karsiliyor" in text)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n{_PASS} passed, {_FAIL} failed (of {_PASS + _FAIL})")
    if _FAIL:
        raise SystemExit(1)
