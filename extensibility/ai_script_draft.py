"""Extensibility - AI Script Draft (Dogal Dilden Otomasyon Taslagi)
=====================================================================

`docs/AI_INTEGRATION_MAP.md` fırsat #3: "kullanıcı 'her apartman binasına
otomatik yangın merdiveni ekle' dediğinde, LLM bunu bir `PythonScriptEngine`
script'ine taslak olarak çevirebilir - **ancak** üretilen script yine de
mevcut güvenlik denetiminden (`sandbox_guard.py`) geçmek zorunda; LLM
çıktısı asla doğrudan güvenilmez."

Bu modül KASITLI OLARAK üretilen script'i **çalıştırmaz**. Tek işi:
doğal dil -> Python taslağı + statik güvenlik denetimi (`check_source`)
sonucunu birlikte döndürmek. Script'i gerçekten çalıştırmak, çağıran
tarafın (kullanıcının açıkça onayladıktan sonra) `ScriptAPI.run("python",
draft.source, ...)` çağırmasıyla, ayrı ve bilinçli bir adımdır. Bu ayrım
kasıtlıdır: bir LLM'in "bu güvenli" demesi asla statik AST denetiminin
yerini almaz (bkz. `AI_INTEGRATION_MAP.md` "kasıtlı olarak AI
eklenmeyen yerler").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..ai_assistant.llm_providers import LLMCallError, LLMProvider, ProviderUnavailableError
from .sandbox_guard import GuardResult, check_source

__all__ = ["ScriptDraft", "generate_script_draft"]


@dataclass(slots=True)
class ScriptDraft:
    """Doğal dilden üretilen bir script taslağı + güvenlik denetim sonucu.

    `is_safe_to_run` False ise `source` KESİNLİKLE çalıştırılmamalıdır -
    çağıran taraf yalnızca `guard.reason`'ı kullanıcıya gösterip taslağı
    reddetmelidir. `is_safe_to_run` True olsa bile bu yalnızca "statik
    AST denetiminden geçti" anlamına gelir, script'in *mantıksal* olarak
    doğru/istenen şeyi yaptığı anlamına gelmez - kullanıcı çalıştırmadan
    önce taslağı gözden geçirmelidir.
    """

    source: str
    guard: GuardResult
    generated_by_llm: bool

    @property
    def is_safe_to_run(self) -> bool:
        return self.guard.ok


_DRAFT_SYSTEM_PROMPT = (
    "Sen bir bina modelleme aracinin otomasyon script yazariyisin. "
    "Kullanicinin dogal dil istegini, asagidaki KISITLI Python "
    "ortaminda calisacak KISA bir script taslagina cevir:\n"
    "- Sadece guvenli bir 'ScriptContext' icindeki degiskenlere/"
    "fonksiyonlara erisebilirsin (context.variables, context.functions).\n"
    "- import, exec, eval, __ ile baslayan ozel nitelikler, dosya/ag "
    "erisimi KESINLIKLE YASAK - kullanma.\n"
    "- Sonucu '_result' adli bir degiskene ata.\n"
    "YALNIZCA ham Python kodu dondur - aciklama, markdown, kod bloğu "
    "isareti (```), veya baska hicbir metin ekleme."
)


def _strip_code_fences(text: str) -> str:
    """LLM'in isteğe rağmen ```python ... ``` bloğuna sarma ihtimaline
    karşı basit bir temizlik (savunma amaçlı, güvenlik denetimini
    ikame etmez - `check_source` her durumda çalışır)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return stripped.strip()


def generate_script_draft(
    instruction: str,
    provider: Optional[LLMProvider] = None,
    available_variables: Optional[list[str]] = None,
    available_functions: Optional[list[str]] = None,
) -> ScriptDraft:
    """Doğal dil bir otomasyon isteğini script taslağına çevirir ve
    HEMEN `sandbox_guard.check_source` ile statik olarak denetler.

    `provider=None` ise (ya da çağrı başarısız olursa), bir script
    üretmeye ÇALIŞMAZ - boş/yorum-satırı bir taslak + `ok=False` bir
    `GuardResult` döner, böylece çağıran taraf "üretilemedi" durumunu
    "güvensiz üretildi" durumundan ayırt edebilir.
    """
    if provider is None:
        empty_guard = GuardResult(
            ok=False,
            reason="LLM sağlayıcı yapılandırılmamış - script taslağı üretilemedi.",
        )
        return ScriptDraft(source="", guard=empty_guard, generated_by_llm=False)

    context_hint = (
        f"Kullanılabilir değişkenler: {available_variables or []}\n"
        f"Kullanılabilir fonksiyonlar: {available_functions or []}\n\n"
        f"İstek: {instruction}"
    )
    try:
        raw = provider.complete(context_hint, system=_DRAFT_SYSTEM_PROMPT)
    except (ProviderUnavailableError, LLMCallError) as exc:
        failed_guard = GuardResult(ok=False, reason=f"LLM çağrısı başarısız: {exc}")
        return ScriptDraft(source="", guard=failed_guard, generated_by_llm=False)

    source = _strip_code_fences(raw)
    guard = check_source(source) if source else GuardResult(
        ok=False, reason="LLM boş bir taslak döndürdü."
    )
    return ScriptDraft(source=source, guard=guard, generated_by_llm=True)
