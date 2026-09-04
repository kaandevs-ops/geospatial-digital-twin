"""
commerce_props.footfall_model - Ziyaret/Canlılık (Footfall) Modeli (Katman 6.1)
==================================================================================

ROADMAP_V9.md / Faz IX / Katman 6:

    "Ziyaret/canlılık modeli: Katman 2'nin rutin motoruyla, dükkan/kafe/
    AVM'lerin gün içi 'canlılık' (footfall) tahmini - Katman 2/3'ün yaya
    akışının yan ürünü."

Bu modül **yeni bir yaya/rutin simülasyonu yazmaz** (roadmap ilkesi #2):
`commerce_props.CommercePropType` (zaten var - `MARKET_STALL`/
`OUTDOOR_SEATING`/`SHOPPING_MALL`/`SUPERMARKET`) için literatürde bilinen
tipik gün-içi ziyaret şeklini (market: öğleden önce yoğun, kafe/restoran:
öğle+akşam çift-tepe, AVM/süpermarket: öğleden sonra-akşam tek yayvan tepe)
`power_infrastructure.demand_model`'deki **aynı** normalize ağırlık
tekniğiyle uygular; `population.activity_model.ODDemandEntry` listesi
(Katman 2.2, zaten üretiliyor) verilirse, `LOCAL_ERRAND`/`SOCIAL_EVENING`
hedefli talebin saatlik dağılımını **doğrudan** bir talep çarpanı olarak
kullanır - roadmap'in "yaya akışının yan ürünü" ifadesinin somut karşılığı.

GÖSTERGE NİTELİĞİ: `risk_scoring`/`energy_audit` ile aynı disiplin - gerçek
bir POS/kartlı-ödeme ziyaret sayacının yerini TUTMAZ, yalnızca göreli gün
içi dağılım şekli hakkında bir işaret verir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from . import CommercePropType
from ..population.activity_model import ActivityType, ODDemandEntry

DISCLAIMER = (
    "Gösterge niteliğindedir; literatürde bilinen tipik gün-içi ziyaret "
    "şekli (market: öğleden önce, kafe/restoran: öğle+akşam çift-tepe, "
    "AVM/süpermarket: öğleden sonra-akşam) kullanılır. Gerçek bir POS/"
    "kartlı-ödeme ziyaret sayacının yerini TUTMAZ."
)

#: Her kategori için 0-23 saat ağırlıkları - toplamı 24 (ortalama 1.0)
#: olacak şekilde normalize edilmiştir (`demand_model.HOURLY_LOAD_WEIGHTS`
#: ile aynı teknik, farklı bir alanda yeniden kullanıldı).
_MARKET_STALL_RAW = [
    0.1, 0.1, 0.1, 0.1, 0.1, 0.2,     # 00-05 kapalı
    0.5, 1.2, 1.8, 2.0, 1.7, 1.3,     # 06-11 sabah pazarı yoğun
    0.9, 0.5, 0.3, 0.2, 0.15, 0.1,    # 12-17 kapanış
    0.05, 0.02, 0.02, 0.02, 0.02, 0.02,  # 18-23 kapalı
]
_OUTDOOR_SEATING_RAW = [
    0.05, 0.02, 0.02, 0.02, 0.02, 0.05,  # 00-05
    0.2, 0.4, 0.6, 0.8, 1.1, 1.6,        # 06-11 kahvaltı+öğlene doğru
    2.0, 1.5, 0.9, 0.7, 0.8, 1.1,        # 12-17 öğle tepesi + ikindi
    1.6, 2.1, 1.9, 1.4, 0.9, 0.4,        # 18-23 akşam tepesi
]
_SHOPPING_MALL_RAW = [
    0.02, 0.02, 0.02, 0.02, 0.02, 0.02,  # 00-05 kapalı
    0.02, 0.05, 0.15, 0.5, 0.9, 1.3,     # 06-11 açılış
    1.6, 1.7, 1.6, 1.5, 1.6, 1.9,        # 12-17 öğleden sonra tepesi
    2.1, 1.9, 1.3, 0.6, 0.15, 0.05,      # 18-23 akşam tepesi + kapanış
]
_SUPERMARKET_RAW = [
    0.05, 0.02, 0.02, 0.02, 0.02, 0.1,   # 00-05
    0.4, 0.8, 1.1, 1.2, 1.2, 1.4,        # 06-11
    1.5, 1.2, 1.0, 1.1, 1.4, 1.9,        # 12-17 akşam yemeği hazırlığı öncesi
    2.2, 1.7, 0.9, 0.4, 0.15, 0.05,      # 18-23 iş çıkışı tepesi
]


def _normalized(raw: list[float]) -> list[float]:
    total = sum(raw)
    scale = len(raw) / total
    return [w * scale for w in raw]


FOOTFALL_HOURLY_WEIGHTS: dict[CommercePropType, list[float]] = {
    CommercePropType.MARKET_STALL: _normalized(_MARKET_STALL_RAW),
    CommercePropType.OUTDOOR_SEATING: _normalized(_OUTDOOR_SEATING_RAW),
    CommercePropType.SHOPPING_MALL: _normalized(_SHOPPING_MALL_RAW),
    CommercePropType.SUPERMARKET: _normalized(_SUPERMARKET_RAW),
}

#: `ODDemandEntry.destination_activity` içinde "ticaret/gündelik yaşam"
#: sayılabilecek hedefler - Katman 2.2'nin `ActivityType`'ında ayrı bir
#: "SHOPPING" tipi yok (roadmap 2.2'nin kendi tablosu), bu yüzden en
#: yakın karşılıklar (`LOCAL_ERRAND`, `SOCIAL_EVENING`) kullanılır -
#: sessizce icat edilmiş bir kategori değil, mevcut tipin açık eşlemesi.
FOOTFALL_RELEVANT_ACTIVITIES: frozenset[ActivityType] = frozenset(
    {ActivityType.LOCAL_ERRAND, ActivityType.SOCIAL_EVENING}
)


@dataclass(slots=True)
class FootfallProfile:
    """Tek bir ticaret nesnesi (dükkan/kafe/AVM) için taban günlük ziyaret
    sayısı + kategori - `commerce_props.osm_bridge`'in ürettiği item'lardan
    veya doğrudan senaryo girdisinden doldurulur."""

    prop_id: str
    prop_type: CommercePropType
    baseline_daily_visits: float

    def hourly_visits(self, hour: int, demand_multiplier: float = 1.0) -> float:
        if not 0 <= hour <= 23:
            raise ValueError(f"hour 0-23 aralığında olmalı, verilen: {hour}")
        weight = FOOTFALL_HOURLY_WEIGHTS[self.prop_type][hour]
        return (self.baseline_daily_visits / 24.0) * weight * demand_multiplier

    def daily_curve(self, demand_multiplier_by_hour: Optional[dict[int, float]] = None) -> list[float]:
        mult = demand_multiplier_by_hour or {}
        return [self.hourly_visits(h, mult.get(h, 1.0)) for h in range(24)]

    def peak_hour(self) -> tuple[int, float]:
        curve = self.daily_curve()
        peak = max(curve)
        return curve.index(peak), peak


def demand_multiplier_by_hour_from_od(
    entries: Sequence[ODDemandEntry],
) -> dict[int, float]:
    """Katman 2.2'nin OD talebinden (yeniden hesaplanmadan, doğrudan
    tüketilerek) saatlik bir çarpan üretir - `LOCAL_ERRAND`/
    `SOCIAL_EVENING` hedefli yolculukların saatlik payı, ortalamaya göre
    normalize edilir (ortalama saat -> çarpan 1.0). Boş girdi/ilgili
    aktivite yoksa tüm çarpanlar 1.0 döner (sessizce sıfırlanmaz -
    "veri yok" durumu "etkisiz" ile aynı davranır, roadmap'in "eksik veri
    sahneyi bozmasın" ilkesiyle tutarlı)."""
    counts = [0] * 24
    for entry in entries:
        if entry.destination_activity in FOOTFALL_RELEVANT_ACTIVITIES:
            hour = int(entry.departure_hour) % 24
            counts[hour] += 1

    total = sum(counts)
    if total == 0:
        return {h: 1.0 for h in range(24)}

    average = total / 24.0
    return {h: (counts[h] / average if average > 0 else 1.0) for h in range(24)}


__all__ = [
    "DISCLAIMER",
    "FOOTFALL_HOURLY_WEIGHTS",
    "FOOTFALL_RELEVANT_ACTIVITIES",
    "FootfallProfile",
    "demand_multiplier_by_hour_from_od",
]
