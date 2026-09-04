"""Roadmap V4 - Faz E7 kabul kriteri testleri.

Kapsam: "Kural motorunun bilerek çözemeyeceği bir cümle ... LLM fallback
etkinken bir CommandIntent'e çözülür; devre dışıyken unknown olarak
raporlanır - iki davranış da ayrı testlerle kanıtlanır."

Bu ortamda gerçek bir `ANTHROPIC_API_KEY` yok, dolayısıyla gerçek ağ
çağrısı yapılamıyor (roadmap'in kendi metninde de öngörülen "API anahtarı/
erişim yoksa mock bir llm_fn ile davranış test edilir" yolu izleniyor).
Buna ek olarak, `AnthropicLLMBridge`'in HTTP/JSON ayrıştırma mantığı
`unittest.mock.patch` ile `urllib.request.urlopen`'i taklit ederek -
gerçek ağa çıkmadan - doğrudan test ediliyor (böylece köprünün kendisi de
gerçekten kanıtlanmış oluyor, yalnızca mock'un davranışı değil).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.ai_assistant.intent import IntentAction
from harita.ai_assistant.intent_parser import IntentParser
from harita.ai_assistant.llm_bridge import (
    AnthropicLLMBridge,
    AnthropicLLMBridgeConfig,
    LLMBridgeError,
    make_mock_llm_fn,
)


# ------------------------------------------------------------------ #
# 1. IntentParser <-> LLM fallback davranışsal sözleşmesi (mock ile)
# ------------------------------------------------------------------ #

def test_ambiguous_sentence_is_unknown_without_llm_fallback():
    parser = IntentParser()  # llm_fn=None
    result = parser.parse("şu tuhaf binayı biraz daha havalı yap")
    assert len(result.intents) == 1
    assert result.intents[0].action == IntentAction.UNKNOWN
    assert result.unmatched_fragments == ["şu tuhaf binayı biraz daha havalı yap"]


def test_ambiguous_sentence_resolves_via_llm_fallback_when_enabled():
    mock_fn = make_mock_llm_fn({
        "şu tuhaf binayı biraz daha havalı yap": [
            {"action": "change_facade", "target": "building",
             "parameters": {"material": "glass"}, "confidence": 0.55},
        ],
    })
    parser = IntentParser(llm_fn=mock_fn)
    result = parser.parse("şu tuhaf binayı biraz daha havalı yap")
    assert len(result.intents) == 1
    assert result.intents[0].action == IntentAction.CHANGE_FACADE
    assert result.intents[0].parameters == {"material": "glass"}
    assert result.unmatched_fragments == []  # LLM çözdüğü için "çözülemedi" listesine girmez


def test_llm_fallback_only_triggers_when_rule_engine_fails():
    calls: list[str] = []

    def _spy_llm(fragment: str) -> list[dict]:
        calls.append(fragment)
        return []

    parser = IntentParser(llm_fn=_spy_llm)
    parser.parse("bir kat ekle")  # kural motoru bunu doğrudan çözer
    assert calls == [], "kural motoru zaten çözdüğü için LLM'e hiç düşülmemeli"


def test_llm_fallback_no_match_falls_back_to_unknown():
    mock_fn = make_mock_llm_fn({})  # hiçbir fragmanı çözemeyen mock
    parser = IntentParser(llm_fn=mock_fn)
    result = parser.parse("anlaşılmaz bir komut buraya")
    assert result.intents[0].action == IntentAction.UNKNOWN
    assert result.unmatched_fragments


def test_llm_fallback_can_resolve_multiple_fragments_in_one_sentence():
    mock_fn = make_mock_llm_fn({
        "bir garip şey yap": [
            {"action": "add_window", "parameters": {}, "confidence": 0.5},
        ],
    })
    parser = IntentParser(llm_fn=mock_fn)
    result = parser.parse("2 kat ekle ve bir garip şey yap")
    actions = [i.action for i in result.intents]
    assert IntentAction.ADD_FLOOR in actions
    assert IntentAction.ADD_WINDOW in actions


# ------------------------------------------------------------------ #
# 2. AnthropicLLMBridge - API anahtarı yok -> sessizce [] (varsayılan)
# ------------------------------------------------------------------ #

def test_bridge_without_api_key_returns_empty_silently():
    env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        bridge = AnthropicLLMBridge()
        assert bridge.is_configured is False
        assert bridge("herhangi bir komut") == []
    finally:
        if env_backup is not None:
            os.environ["ANTHROPIC_API_KEY"] = env_backup


def test_bridge_without_api_key_raises_when_raise_on_error_true():
    env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        bridge = AnthropicLLMBridge(raise_on_error=True)
        try:
            bridge("herhangi bir komut")
            assert False, "LLMBridgeError beklenıyordu"
        except LLMBridgeError:
            pass
    finally:
        if env_backup is not None:
            os.environ["ANTHROPIC_API_KEY"] = env_backup


def test_bridge_is_configured_true_when_key_present():
    env_backup = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key-for-config-check"
    try:
        bridge = AnthropicLLMBridge()
        assert bridge.is_configured is True
    finally:
        if env_backup is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = env_backup


# ------------------------------------------------------------------ #
# 3. AnthropicLLMBridge - HTTP/JSON ayrıştırma mantığı (urlopen mock'lu)
# ------------------------------------------------------------------ #

class _FakeHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_anthropic_response(text: str) -> bytes:
    return json.dumps({"content": [{"type": "text", "text": text}]}).encode("utf-8")


def test_bridge_parses_successful_response_into_intents():
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key"
    try:
        response_json = json.dumps([
            {"action": "add_floor", "target": "building",
             "parameters": {"count": 2}, "confidence": 0.9},
        ])
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _FakeHTTPResponse(_fake_anthropic_response(response_json))
            bridge = AnthropicLLMBridge()
            result = bridge("2 kat ekle")
        assert result == [
            {"action": "add_floor", "target": "building",
             "parameters": {"count": 2}, "confidence": 0.9},
        ]
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_bridge_strips_markdown_code_fence_from_response():
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key"
    try:
        fenced = "```json\n" + json.dumps([{"action": "unknown"}]) + "\n```"
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _FakeHTTPResponse(_fake_anthropic_response(fenced))
            bridge = AnthropicLLMBridge()
            result = bridge("garip komut")
        assert result == [{"action": "unknown"}]
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_bridge_network_error_returns_empty_by_default():
    import urllib.error

    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key"
    try:
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("ağ hatası")):
            bridge = AnthropicLLMBridge()
            assert bridge("herhangi bir şey") == []
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_bridge_network_error_raises_when_raise_on_error_true():
    import urllib.error

    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key"
    try:
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("ağ hatası")):
            bridge = AnthropicLLMBridge(raise_on_error=True)
            try:
                bridge("herhangi bir şey")
                assert False, "LLMBridgeError beklenıyordu"
            except LLMBridgeError:
                pass
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_bridge_non_list_response_returns_empty_by_default():
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key"
    try:
        not_a_list = json.dumps({"oops": "not a list"})
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _FakeHTTPResponse(_fake_anthropic_response(not_a_list))
            bridge = AnthropicLLMBridge()
            assert bridge("bir şey") == []
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_bridge_malformed_json_returns_empty_by_default():
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key"
    try:
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _FakeHTTPResponse(_fake_anthropic_response("{not valid json"))
            bridge = AnthropicLLMBridge()
            assert bridge("bir şey") == []
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_bridge_filters_non_dict_items_from_list():
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key"
    try:
        mixed = json.dumps([{"action": "add_door"}, "garbage", 42, {"action": "add_window"}])
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _FakeHTTPResponse(_fake_anthropic_response(mixed))
            bridge = AnthropicLLMBridge()
            result = bridge("bir şey")
        assert result == [{"action": "add_door"}, {"action": "add_window"}]
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_bridge_end_to_end_with_intent_parser_via_mocked_http():
    """Roadmap E7'nin tam kabul kriteri: gerçek `AnthropicLLMBridge`
    (mock'lanmış HTTP katmanıyla) `IntentParser`'a enjekte edilir ve kural
    motorunun çözemediği bir cümle gerçek uçtan uca zincirle çözülür."""
    os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake-key"
    try:
        response_json = json.dumps([
            {"action": "change_roof", "target": "building",
             "parameters": {"roof_type": "hip"}, "confidence": 0.72},
        ])
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _FakeHTTPResponse(_fake_anthropic_response(response_json))
            bridge = AnthropicLLMBridge()
            parser = IntentParser(llm_fn=bridge)
            result = parser.parse("şu çatıyı biraz daha şık yap")
        assert result.intents[0].action == IntentAction.CHANGE_ROOF
        assert result.intents[0].parameters == {"roof_type": "hip"}
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


if __name__ == "__main__":
    import inspect

    mod = sys.modules[__name__]
    test_fns = [
        obj for name, obj in inspect.getmembers(mod)
        if name.startswith("test_") and inspect.isfunction(obj)
    ]
    failures = 0
    for fn in test_fns:
        try:
            fn()
            print(f"OK   {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(test_fns) - failures}/{len(test_fns)} test gecti.")
    if failures:
        sys.exit(1)
