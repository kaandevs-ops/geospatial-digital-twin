"""AI Assistant - Rapor Anlatici (Report Narrator)
====================================================

Kullanici talebi (madde 2, devami): "bu projede baska nerelerde yapay zeka
kullanilabilir bak bakalim".

Projede zaten yapisal (structured) denetim raporlari uretiliyor -
`FacadeGenerator.check_compliance()` -> `FacadeComplianceReport`,
`RoomGenerator.check_compliance()` -> `RoomComplianceReport`. Bunlar sayisal/
kural tabanli ve makine-okunur; ama bir kullaniciya "bu bina neden uygun
degil, ne yapmali" diye DOGAL DILDE aciklamiyorlar. Bu, LLM'in gercek katma
deger katacagi ikinci somut nokta: **yapisal veriyi kisa, eyleme donuk bir
Turkce/Ingilizce aciklamaya cevirmek**.

Tasarim, `llm_providers.py` ile ayni ilkeyi izler: saglayici yoksa/basarisiz
olursa **sessizce kural-tabanli (sablon) bir ozete duser** - hicbir zaman
istisna firlatip cagiran akisi kirmaz, hicbir zaman bos/None donmez (LLM
yoksa bile kullanici en azindan sablon bir aciklama gorur).

Diger tespit edilen (bu oturumda henuz kod yazilmayan, ileride ele alinacak)
AI-katma-deger noktalari icin bkz. `docs/AI_INTEGRATION_MAP.md`.
"""

from __future__ import annotations

from .llm_providers import LLMCallError, LLMProvider, ProviderUnavailableError

__all__ = ["narrate_facade_compliance", "narrate_room_compliance"]

_NARRATOR_SYSTEM_PROMPT = (
    "Sen bir bina yonetmeligi denetim raporunu, teknik olmayan bir mal "
    "sahibine 2-4 cumleyle, somut ve eyleme donuk (ne yapilmasi gerektigini "
    "soyleyen) sekilde Turkce aciklayan bir asistansin. Sadece verilen "
    "sayisal verilere dayan, uydurma sayi/madde ekleme. Madde numaralarini "
    "([PAİY-8] gibi) oldugu gibi koru."
)


def _fallback_facade_summary(report) -> str:
    lines = [
        f"Pencere/duvar orani: %{report.window_wall_ratio * 100:.1f} "
        f"(asgari %{report.min_required_ratio * 100:.1f}).",
    ]
    if report.is_compliant:
        lines.append("Cephe mevcut yonetmelik esiklerini karsiliyor.")
    else:
        lines.append("Cephe asagidaki noktalarda yonetmelik esiklerini karsilamiyor:")
        lines.extend(f"- {issue}" for issue in report.issues)
    return " ".join(lines)


def _fallback_room_summary(report) -> str:
    if not report.issues:
        return "Tum odalar asgari alan/genislik gereksinimlerini karsiliyor."
    lines = ["Asagidaki odalarda yonetmelik uygunsuzlugu tespit edildi:"]
    lines.extend(
        f"- oda #{issue.room_id} ({issue.room_type}): {issue.reason}" for issue in report.issues
    )
    return " ".join(lines)


def _narrate(report, fallback_fn, provider: LLMProvider | None) -> str:
    fallback = fallback_fn(report)
    if provider is None:
        return fallback
    prompt = f"Denetim raporu (ham veri): {report!r}\n\nSablon ozet: {fallback}"
    try:
        text = provider.complete(prompt, system=_NARRATOR_SYSTEM_PROMPT).strip()
    except (ProviderUnavailableError, LLMCallError):
        return fallback
    return text or fallback


def narrate_facade_compliance(report, provider: LLMProvider | None = None) -> str:
    """`FacadeComplianceReport`'u dogal dilde kisa bir aciklamaya cevirir.
    `provider=None` (varsayilan) veya saglayici basarisiz olursa, sablon
    tabanli (LLM'siz) bir ozete sessizce duser - hicbir zaman istisna
    firlatmaz."""
    return _narrate(report, _fallback_facade_summary, provider)


def narrate_room_compliance(report, provider: LLMProvider | None = None) -> str:
    """`RoomComplianceReport`'u dogal dilde kisa bir aciklamaya cevirir.
    Ayni sessiz-dusme davranisi icin bkz. `narrate_facade_compliance`."""
    return _narrate(report, _fallback_room_summary, provider)
