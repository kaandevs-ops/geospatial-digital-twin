"""
commerce_props.economic_resilience - Ekonomik Dayanıklılık / Toparlanma
Eğrisi (Katman 6.3)
=================================================================================

ROADMAP_V9.md / Faz IX / Katman 6:

    "Ekonomik dayanıklılık (afet sonrası): Hangi ticari bölgelerin ne
    kadar süre kapalı kalacağı (hasar tahmini × onarım süresi) -
    'toparlanma eğrisi' (resilience curve)."

Bu modül **yeni bir hasar tahmin motoru yazmaz** (roadmap ilkesi #2):
`hazard_data.risk_scoring.RiskLevel` (zaten var - RVS/FEMA P-154 kökenli)
girdi olarak alınır, buna karşılık gelen **gösterge niteliğinde** bir
kapanma-süresi (gün) tablosuyla eşlenir. Süre, `hazard_data.
resilience_timeline`'ın (Faz VII) `COMMERCE_CLOSED`/`COMMERCE_REOPENED`
(Faz IX'ta eklendi) olay çiftini besleyecek şekilde Event Bus'a yazılabilir
- yeni bir kayıt mekanizması icat edilmedi, `resilience_timeline`'ın zaten
tükettiği aynı `EventSystem.history()` deseni kullanılır.

GÖSTERGE NİTELİĞİ: Kapanma-süresi tablosu, FEMA HAZUS-MH tarzı raporlarda
sıkça görülen "hafif/orta/ağır/çok ağır hasar -> kaba onarım süresi
mertebeleri" genel biçimini takip eden **temsili** (yuvarlak) değerlerdir;
HAZUS'un kendi kalibre edilmiş envanter/kırılganlık eğrilerinin YERİNİ
TUTMAZ - yalnızca zaten var olan `RiskLevel` sınıflandırmasına bir "kaç
gün kapalı kalır" mertebesi eklemek için kullanılmıştır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from ..extensibility.city_events import CityEventType, emit_city_event
from ..extensibility.event_system import EventSystem
from ..hazard_data.risk_scoring import RiskLevel

DISCLAIMER = (
    "Gösterge niteliğindedir; FEMA HAZUS-MH tarzı raporlardaki genel "
    "'hasar derecesi -> kaba onarım süresi mertebesi' biçimini takip eden "
    "temsili (yuvarlak) değerler kullanılır. HAZUS'un kendi kalibre "
    "edilmiş kırılganlık eğrilerinin yerini TUTMAZ."
)

#: `RiskLevel` (zaten var, `hazard_data.risk_scoring`) -> gösterge
#: niteliğinde kaba kapanma süresi (gün). HAZUS-MH'in "hafif/orta/ağır/
#: tam hasar" kategorilerine karşılık gelen mertebelerle KABACA tutarlı
#: (birebir HAZUS tablosu değildir).
INDICATIVE_CLOSURE_DAYS: dict[RiskLevel, float] = {
    RiskLevel.LOW: 1.0,
    RiskLevel.MODERATE: 7.0,
    RiskLevel.HIGH: 30.0,
    RiskLevel.VERY_HIGH: 120.0,
}


@dataclass(slots=True)
class CommercialAreaResilience:
    """Tek bir ticari bölge/AVM/çarşı için toparlanma durumu."""

    area_id: str
    risk_level: RiskLevel
    closure_days: float
    disrupted_at: float

    def operational_fraction_at(self, t: float) -> float:
        """`t` (mutlak saat, `disrupted_at` ile aynı zaman tabanında) anında
        beklenen "açık/faal" oranı (0-1) - basit, adı açık bir S-eğrisi
        (lojistik) kullanır: kapanma anında ~0, `closure_days` sonunda
        ~1'e yaklaşır. Kara kutu bir ML tahmini değil, kapalı-form bir
        fonksiyondur (roadmap ilkesi #5 - gösterge disiplini)."""
        if t < self.disrupted_at:
            return 1.0
        elapsed_days = (t - self.disrupted_at) / 86400.0
        if self.closure_days <= 0:
            return 1.0
        # Lojistik S-eğrisi: orta nokta closure_days/2, dikliği closure_days
        # ile ölçeklenir (kısa kapanmalar hızlı, uzun kapanmalar yavaş
        # toparlanır - HAZUS'un "restoration curve" genel şekliyle
        # niteliksel olarak tutarlı).
        midpoint = self.closure_days / 2.0
        steepness = 6.0 / max(self.closure_days, 0.1)
        x = steepness * (elapsed_days - midpoint)
        return 1.0 / (1.0 + math.exp(-x))

    def is_recovered_at(self, t: float, threshold: float = 0.95) -> bool:
        return self.operational_fraction_at(t) >= threshold


@dataclass(slots=True)
class ResilienceCurveReport:
    area_id: str
    samples: list[tuple[float, float]]  # (gün, operasyonel_oran)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "area_id": self.area_id,
            "samples": [{"day": round(d, 2), "operational_fraction": round(f, 3)}
                        for d, f in self.samples],
            "disclaimer": self.disclaimer,
        }


def build_resilience_curve(
    area: CommercialAreaResilience, *, sample_count: int = 20, horizon_days: Optional[float] = None,
) -> ResilienceCurveReport:
    """`area.closure_days`'in `1.5x`'i kadar bir ufuk boyunca örnekleme -
    tam toparlanmanın ötesini de göstererek eğrinin platoya oturduğunu
    görünür kılar (yeni bir yaklaşım değil, `EvacuationBenchmark`/
    `FireEvacuationComparator`'ın rapor-üretme deseniyle aynı ruh)."""
    horizon = horizon_days if horizon_days is not None else area.closure_days * 1.5
    if sample_count < 2:
        raise ValueError("sample_count en az 2 olmalı")
    samples = []
    for i in range(sample_count):
        day = (i / (sample_count - 1)) * horizon
        t = area.disrupted_at + day * 86400.0
        samples.append((day, area.operational_fraction_at(t)))
    return ResilienceCurveReport(area_id=area.area_id, samples=samples)


def closure_days_for_risk_level(risk_level: RiskLevel) -> float:
    return INDICATIVE_CLOSURE_DAYS[risk_level]


def start_commercial_disruption(
    bus: EventSystem, area_id: str, risk_level: RiskLevel, *, source: Optional[str] = None,
) -> CommercialAreaResilience:
    """`RiskLevel`'i kapanma süresine çevirir, `COMMERCE_CLOSED` olayını
    yayınlar (zamanı `bus`'ın kendi `Event.timestamp`'inden - `resilience_
    timeline.build_resilience_report()`'un zaten okuduğu alan - alınır,
    yeni bir zaman kaynağı icat edilmedi) ve çağıran tarafın toparlanma
    eğrisi çizebilmesi için bir `CommercialAreaResilience` döner."""
    event = emit_city_event(
        bus, CityEventType.COMMERCE_CLOSED, source=source or area_id,
        area_id=area_id, risk_level=risk_level.value,
    )
    closure_days = closure_days_for_risk_level(risk_level)
    return CommercialAreaResilience(
        area_id=area_id, risk_level=risk_level, closure_days=closure_days,
        disrupted_at=event.timestamp,
    )


def complete_commercial_recovery(
    bus: EventSystem, area: CommercialAreaResilience, *, source: Optional[str] = None,
) -> None:
    """Toparlanma tamamlandığında `COMMERCE_REOPENED` yayınlar -
    `resilience_timeline.build_resilience_report()`'un `SYSTEM_EVENT_
    PAIRS`'teki "ticaret" satırını artık doldurabilmesi için."""
    emit_city_event(
        bus, CityEventType.COMMERCE_REOPENED, source=source or area.area_id,
        area_id=area.area_id,
    )


__all__ = [
    "DISCLAIMER",
    "INDICATIVE_CLOSURE_DAYS",
    "CommercialAreaResilience",
    "ResilienceCurveReport",
    "build_resilience_curve",
    "closure_days_for_risk_level",
    "start_commercial_disruption",
    "complete_commercial_recovery",
]
