"""Analysis Engine - Sonuc Anlatici (Result Narrator)
=======================================================

`docs/AI_INTEGRATION_MAP.md` fırsat #4: "sayısal simülasyon çıktısını
('bu bölge günde 3 saat gölgede kalıyor') doğal dil özetine çevirme -
`report_narrator` ile aynı desen tekrar kullanılabilir."

Bu modül, `analysis_engine` altındaki güneş/çevresel simülasyon
sonuçlarını (dataclass) teknik olmayan bir okuyucuya 2-4 cümlelik Türkçe
açıklamaya çevirir. Tasarım ilkesi `ai_assistant/report_narrator.py` ile
birebir aynıdır: **provider yoksa veya çağrı başarısız olursa sessizce
kural tabanlı (şablon) bir özete düşer**, hiçbir zaman istisna fırlatmaz
ve hiçbir zaman boş/None dönmez.

Not: bu modül `ai_assistant`'a değil `analysis_engine`'e ait çünkü
sonuç tipleri (`SolarExposureResult`, `HeatIslandResult`, ...) burada
tanımlı; `LLMProvider` Protocol'ü `ai_assistant.llm_providers`'tan içe
aktarılır (döngüsel bağımlılık yok - `ai_assistant` bu modülü kullanır,
tersi değil).
"""

from __future__ import annotations

from typing import Any, Optional

from ..ai_assistant.llm_providers import LLMCallError, LLMProvider, ProviderUnavailableError

__all__ = [
    "narrate_solar_exposure",
    "narrate_heat_island",
    "narrate_noise",
    "narrate_flood",
    "narrate_evacuation_result",
    "narrate_capacity_report",
    "narrate_scenario_comparison",
]

_NARRATOR_SYSTEM_PROMPT = (
    "Sen bir cevresel/gunes simulasyonu sonucunu, teknik olmayan bir "
    "mal sahibine 2-4 cumleyle, somut ve gunluk dilde Turkce aciklayan "
    "bir asistansin. Sadece verilen sayisal verilere dayan, uydurma "
    "sayi ekleme. Gerekirse ne yapilmasi gerektigine dair kisa bir "
    "oneri ekle (orn. golge/isi/gurultu azaltma)."
)


def _fallback_solar_exposure(result: Any) -> str:
    ratio_pct = result.exposure_ratio * 100
    return (
        f"Bu nokta günde ortalama {result.daylight_hours:.1f} saat gün "
        f"ışığı alıyor; bunun {result.direct_sun_hours:.1f} saati doğrudan "
        f"güneş, {result.shaded_hours:.1f} saati gölge (doğrudan güneş "
        f"oranı: %{ratio_pct:.0f})."
    )


def _fallback_heat_island(result: Any) -> str:
    delta = result.average_delta
    if delta >= 5:
        yorum = "belirgin bir sıcak-ada etkisi var; açık renkli/yeşil yüzeyler artırılabilir."
    elif delta >= 2:
        yorum = "hafif bir ısınma etkisi görülüyor."
    else:
        yorum = "ısınma etkisi düşük seviyede."
    return f"Bölgedeki ortalama sıcaklık artışı yaklaşık {delta:.1f}°C — {yorum}"


def _fallback_noise(result: Any) -> str:
    spl = result.spl_db
    if spl >= 70:
        yorum = "sürekli maruziyette rahatsız edici, gürültü perdesi/mesafe önerilir."
    elif spl >= 55:
        yorum = "orta düzeyde, konut alanları için sınırda."
    else:
        yorum = "düşük düzeyde, günlük yaşamı etkilemesi beklenmez."
    return f"Bu noktadaki tahmini ses basınç seviyesi {spl:.1f} dB SPL — {yorum}"


def _fallback_flood(result: Any) -> str:
    count = getattr(result, "flooded_cell_count", None)
    level = getattr(result, "water_level_m", None)
    if count is not None and level is not None:
        return f"{level:.2f} m su seviyesinde tahmini {count} hücre su altında kalıyor."
    return "Taşkın tahmini tamamlandı."


def _fallback_evacuation(result: Any) -> str:
    total = getattr(result, "total_agents", 0)
    evacuated = getattr(result, "evacuated_count", 0)
    time_s = getattr(result, "evacuation_time_s", 0.0)
    timed_out = getattr(result, "timed_out", False)
    parts = [
        f"{total} kişilik senaryoda {evacuated} kişi {time_s:.0f} saniyede tahliye edildi."
    ]
    if timed_out:
        parts.append("Simülasyon azami süre içinde tamamlanamadı (bazı ajanlar dışarı çıkamadı).")
    bottleneck = getattr(result, "bottleneck_peak_count", None)
    if bottleneck:
        peak_t = getattr(result, "bottleneck_peak_time_s", None)
        when = f" (t≈{peak_t:.0f}s)" if peak_t is not None else ""
        parts.append(f"En yoğun darboğaz anında{when} aynı hücrede {bottleneck} kişi birikti.")
    return " ".join(parts)


def _fallback_capacity_report(report: Any) -> str:
    runs = getattr(report, "runs", []) or []
    if not runs:
        return "Kapasite analizi için koşum verisi bulunamadı."
    worst = max(runs, key=lambda r: getattr(r, "evacuation_time_s", 0.0))
    over_threshold = [r for r in runs if getattr(r, "within_threshold", None) is False]
    parts = [
        f"{len(runs)} farklı doluluk senaryosu test edildi; en uzun tahliye süresi "
        f"{worst.evacuation_time_s:.0f} saniyeyle {worst.agent_count} kişilik senaryoda görüldü."
    ]
    if over_threshold:
        counts = ", ".join(str(r.agent_count) for r in over_threshold)
        parts.append(f"{counts} kişilik senaryo(lar) yönetmelik eşiğini aşıyor.")
    else:
        parts.append("Test edilen tüm senaryolar eşik dahilinde tamamlandı.")
    return " ".join(parts)


def _fallback_scenario_comparison(comparisons: Any) -> str:
    items = list(comparisons)
    if not items:
        return "Karşılaştırılacak senaryo metriği bulunamadı."
    sentences = []
    for c in items:
        pct = getattr(c, "pct_change", None)
        metric = getattr(c, "metric_name", "metrik")
        if pct is None:
            continue
        yon = "azaldı" if pct < 0 else "arttı"
        sentences.append(f"{metric} %{abs(pct):.0f} {yon}")
    if not sentences:
        return "Karşılaştırılan metriklerde yüzde değişim hesaplanamadı (başlangıç değeri sıfır)."
    return "Bu senaryoda " + ", ".join(sentences) + "."


def _narrate(result: Any, fallback_fn, provider: Optional[LLMProvider]) -> str:
    fallback = fallback_fn(result)
    if provider is None:
        return fallback
    prompt = f"Simülasyon sonucu (ham veri): {result!r}\n\nŞablon özet: {fallback}"
    try:
        text = provider.complete(prompt, system=_NARRATOR_SYSTEM_PROMPT).strip()
    except (ProviderUnavailableError, LLMCallError):
        return fallback
    return text or fallback


def narrate_solar_exposure(result: Any, provider: Optional[LLMProvider] = None) -> str:
    """`SolarExposureResult`'ı doğal dilde kısa bir açıklamaya çevirir."""
    return _narrate(result, _fallback_solar_exposure, provider)


def narrate_heat_island(result: Any, provider: Optional[LLMProvider] = None) -> str:
    """`HeatIslandResult`'ı doğal dilde kısa bir açıklamaya çevirir."""
    return _narrate(result, _fallback_heat_island, provider)


def narrate_noise(result: Any, provider: Optional[LLMProvider] = None) -> str:
    """`NoiseResult`'ı doğal dilde kısa bir açıklamaya çevirir."""
    return _narrate(result, _fallback_noise, provider)


def narrate_flood(result: Any, provider: Optional[LLMProvider] = None) -> str:
    """`FloodResult`'ı doğal dilde kısa bir açıklamaya çevirir."""
    return _narrate(result, _fallback_flood, provider)


def narrate_evacuation_result(result: Any, provider: Optional[LLMProvider] = None) -> str:
    """ROADMAP_V9 Katman 9 madde 4: `mobility.crowd_simulation.
    EvacuationResult`'ı (duck-typing — döngüsel bağımlılık yaratmamak için
    tip doğrudan import edilmez, `analysis_engine/decision_support.py` ile
    aynı disiplin) doğal dilde kısa bir açıklamaya çevirir."""
    return _narrate(result, _fallback_evacuation, provider)


def narrate_capacity_report(report: Any, provider: Optional[LLMProvider] = None) -> str:
    """`mobility.crowd_simulation.capacity_analysis.CapacityAnalysisReport`'ı
    doğal dilde özetler (duck-typing, bkz. `narrate_evacuation_result`)."""
    return _narrate(report, _fallback_capacity_report, provider)


def narrate_scenario_comparison(
    comparisons: Any, provider: Optional[LLMProvider] = None,
) -> str:
    """ROADMAP_V9 Katman 9 madde 1+4: `analysis_engine.decision_support.
    ScenarioComparison` listesini ("Bu senaryoda toplam tahliye süresi
    %15 azaldı, ancak enerji talebi %8 arttı" — roadmap'in kendi örneği)
    tek bir doğal dil cümlesine çevirir. `comparisons` bir iterable'dır
    (`compare_evacuation_results`/`compare_capacity_reports` çıktısı veya
    farklı katmanlardan derlenmiş herhangi bir `ScenarioComparison`
    listesi — duck-typing, `pct_change`/`metric_name` alanları yeterli)."""
    return _narrate(comparisons, _fallback_scenario_comparison, provider)
