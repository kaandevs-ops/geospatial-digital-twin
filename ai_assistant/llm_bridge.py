"""Roadmap V4 - Faz E7: AI Assistant icin gercek LLM fallback koprusu.

`IntentParser`'in `llm_fn: Callable[[str], list[dict]]` enjeksiyon noktasi
V1'den beri var (bkz. `intent_parser.py`) ama hicbir oturumda gercek bir
LLM API'sine karsi uctan uca calistirilmadi - "ana projenin coklu-LLM
router'ina baglanabilir" iddiasi teorikti.

Bu modul iki parca sunar:

1. `AnthropicLLMBridge` - stdlib `urllib.request` ile Anthropic Messages
   API'sine (`api.anthropic.com/v1/messages`) gercek bir HTTP cagrisi yapan,
   `IntentParser(llm_fn=...)` sozlesmesini (`str -> list[dict]`) karsilayan
   cagrilabilir bir sinif. API anahtari (`ANTHROPIC_API_KEY` ortam degiskeni)
   tanimli degilse veya cagri herhangi bir nedenle basarisiz olursa
   (ag hatasi, zaman asimi, gecersiz JSON, beklenmeyen sekil) **sessizce bos
   liste doner** - `IntentParser` bunu normal "kural motoru da coz(e)medi"
   yoluna dusurur (fragman `unknown` olarak raporlanir), hicbir istisna
   cagiran kodu kirmaz. `raise_on_error=True` ile (yalniz testler icin)
   bu sessiz-dusme kapatilabilir - boylece "ag/anahtar yok -> sessizce []"
   ile "ag var ama yanit bozuk -> acik hata" yollari ayri ayri test edilebilir.

2. `make_mock_llm_fn(responses)` - gercek ag erisimi olmadan (bu ortamda
   `ANTHROPIC_API_KEY` yok) `IntentParser` <-> LLM-fallback entegrasyonunun
   davranissal olarak test edilmesini saglayan, sabit-yanit bir mock uretici.

Tasarim ilkesi (roadmap E6 ile ortak): **kurulu/yapilandirilmis degilse
sessizce mevcut davranisa dus** - stdlib-only cekirdek + opsiyonel gercek
entegrasyon.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

__all__ = [
    "AnthropicLLMBridge",
    "AnthropicLLMBridgeConfig",
    "LLMBridgeError",
    "make_mock_llm_fn",
]


class LLMBridgeError(Exception):
    """Gercek koprü cagrisi beklenmeyen bir sekilde basarisiz oldu (ag hatasi,
    gecersiz JSON, beklenmeyen yanit sekli, ...). Yalnizca `raise_on_error=True`
    ile acikca istenirse firlatilir - varsayilan davranis sessizce `[]` donmektir.
    """


_SYSTEM_PROMPT = (
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


@dataclass
class AnthropicLLMBridgeConfig:
    """Gercek HTTP LLM cagrisi icin yapilandirma. Varsayilanlar Anthropic
    Messages API'sini hedefler; `endpoint`/`model` gerekirse degistirilebilir
    (orn. ana projenin kendi coklu-LLM router endpoint'ine yonlendirmek icin).
    """

    endpoint: str = "https://api.anthropic.com/v1/messages"
    model: str = "claude-sonnet-4-6"
    api_key_env: str = "ANTHROPIC_API_KEY"
    timeout_s: float = 15.0
    max_tokens: int = 512
    anthropic_version: str = "2023-06-01"


class AnthropicLLMBridge:
    """`IntentParser(llm_fn=...)` sozlesmesini (`str -> list[dict]`) karsilayan,
    gercek bir HTTP cagrisi yapan koprü sinifi.

    Kullanim:
        bridge = AnthropicLLMBridge()
        parser = IntentParser(llm_fn=bridge)

    API anahtari yoksa veya cagri basarisiz olursa varsayilan olarak sessizce
    `[]` doner (kural motorunun coz(e)medigi fragman `unknown` olarak kalir).
    """

    def __init__(
        self,
        config: AnthropicLLMBridgeConfig | None = None,
        *,
        raise_on_error: bool = False,
    ) -> None:
        self._config = config or AnthropicLLMBridgeConfig()
        self._raise_on_error = raise_on_error

    @property
    def is_configured(self) -> bool:
        """API anahtari ortam degiskeninde tanimli mi (gercek bir ag cagrisi
        denenmeden once hizli bir kontrol icin)."""
        return bool(os.environ.get(self._config.api_key_env))

    def __call__(self, fragment: str) -> list[dict]:
        api_key = os.environ.get(self._config.api_key_env)
        if not api_key:
            if self._raise_on_error:
                raise LLMBridgeError(f"{self._config.api_key_env} ortam degiskeni tanimli degil.")
            return []

        payload = {
            "model": self._config.model,
            "max_tokens": self._config.max_tokens,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": fragment}],
        }
        request = urllib.request.Request(
            self._config.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": self._config.anthropic_version,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_s) as resp:
                raw_body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if self._raise_on_error:
                raise LLMBridgeError(f"LLM istegi basarisiz: {exc}") from exc
            return []

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            if self._raise_on_error:
                raise LLMBridgeError(f"LLM API yaniti gecerli JSON degil: {exc}") from exc
            return []

        return self._extract_intents(body)

    def _extract_intents(self, body: dict) -> list[dict]:
        content_blocks = body.get("content", []) if isinstance(body, dict) else []
        text_parts = [
            block.get("text", "")
            for block in content_blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        raw_text = "".join(text_parts).strip()

        # Bazi modeller yaniti ```json ... ``` icine sarabilir; temizle.
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`").strip()
            if raw_text.lower().startswith("json"):
                raw_text = raw_text[4:].strip()

        try:
            parsed = json.loads(raw_text) if raw_text else None
        except json.JSONDecodeError as exc:
            if self._raise_on_error:
                raise LLMBridgeError(f"LLM yaniti gecerli JSON degil: {raw_text[:200]!r}") from exc
            return []

        if not isinstance(parsed, list):
            if self._raise_on_error:
                raise LLMBridgeError(f"LLM yaniti bir liste degil: {type(parsed)!r}")
            return []

        return [item for item in parsed if isinstance(item, dict)]


def make_mock_llm_fn(responses: dict[str, list[dict]]) -> Callable[[str], list[dict]]:
    """Test/cevrimdisi kullanim icin: `IntentParser(llm_fn=...)` sozlesmesini
    karsilayan, gercek ag cagrisi yapmayan sabit-yanit bir mock uretir.

    `responses`: fragment metni (kucuk harfe cevrilip strip edilerek
    anahtarlanir) -> intent-dict listesi. Eslesme yoksa bos liste doner
    (kural motoru zaten coz(e)medigi icin fragman `unknown` olarak kalir -
    gercek `AnthropicLLMBridge` ile ayni sozlesme/davranis).
    """

    def _mock(fragment: str) -> list[dict]:
        return responses.get(fragment.strip().lower(), [])

    return _mock
