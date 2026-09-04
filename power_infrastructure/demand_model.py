"""
power_infrastructure.demand_model - Bina Enerji Talep Modeli (Katman 4 madde 1)
==================================================================================

ROADMAP_V9.md / Faz VIII / Katman 4:

    "Bina enerji talep modeli: `building_reconstruction/energy_audit.py`
    zaten var - zaman-bağımlı hale getirilip (sabah/akşam pik) şehir
    ölçeğinde toplam talebe agregasyon (`digital_twin/hierarchy.py`'nin
    agregasyon deseni burada da kullanılır)."

Bu modül **yeni bir enerji hesap motoru yazmaz** (roadmap ilkesi #2):
`building_reconstruction.energy_audit`'in ürettiği yıllık ısıtma enerjisi
tahminini (`EnvelopeAuditReport.estimated_annual_heating_kwh`) girdi olarak
alır, literatürde bilinen basit ve adı konmuş bir "tipik günlük yük eğrisi"
(load profile) ile saatlik güce dağıtır, ve `digital_twin.hierarchy.
TwinHierarchy.aggregate()`'in **var olan** memoized agregasyon desenini
(zaten mahalle->blok->bina toplamları için kullanılıyordu) yeniden
kullanarak şehir/blok ölçeğinde toplam talebi hesaplar.

GÖSTERGE NİTELİĞİ (risk_scoring.py / energy_audit.py ile aynı disiplin):
Kullanılan saatlik yük-eğrisi katsayıları, TS 825/ENERJİ KİMLİK BELGESİ gibi
resmi bir kaynaktan alınmamıştır - literatürde yaygın kabul gören "konut:
sabah+akşam çift-tepe, ticari: gündüz tek-yayvan-tepe" genel şeklini takip
eden **normalize edilmiş, toplamı 1.0 olan** bir dağılımdır (yıllık toplam
enerji korunur, yalnızca gün içi dağılım şekli temsili). Gerçek bir enerji
yönetim sistemi (EYS) ölçümünün yerini tutmaz.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..digital_twin import DigitalTwin, DigitalTwinRegistry
from ..digital_twin.hierarchy import TwinHierarchy


class BuildingDemandType(str, Enum):
    """Yük-eğrisi şeklini belirleyen kaba bina kullanım kategorisi.

    `building_reconstruction.regulations` içindeki bina tipi sınıflandırması
    yeniden yazılmadı - yalnızca yük-eğrisi seçimi için 2 kategoriye
    indirgendi (konut/ticari); üçüncü bir tip verilirse konut varsayılan
    alınır (sessizce değil - `DemandProfile.demand_type` alanında görünür).
    """

    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"


#: Saat (0-23) -> normalize ağırlık. Her iki liste de toplamı 24'e (yani
#: ortalama ağırlık 1.0'a) normalize edilmiştir; `hourly_load_kw()` bu
#: ağırlığı yıllık_kwh/8760 ortalama gücüyle çarpar.
#: Konut: sabah (07-09) ve akşam (18-22) çift-tepe, gece düşük - yaygın
#: bilinen "residential load duck-curve"in basitleştirilmiş şekli.
_RESIDENTIAL_RAW_WEIGHTS = [
    0.4, 0.35, 0.3, 0.3, 0.35, 0.5,   # 00-05 gece
    0.8, 1.3, 1.5, 1.1, 0.9, 0.85,    # 06-11 sabah tepesi
    0.9, 0.85, 0.8, 0.85, 0.95, 1.2,  # 12-17 öğleden sonra
    1.6, 1.7, 1.5, 1.2, 0.9, 0.6,     # 18-23 akşam tepesi
]
#: Ticari/işyeri: gündüz (09-18) tek yayvan tepe, gece/hafta sonu düşük.
_COMMERCIAL_RAW_WEIGHTS = [
    0.25, 0.2, 0.2, 0.2, 0.2, 0.3,    # 00-05
    0.45, 0.7, 1.1, 1.4, 1.5, 1.55,   # 06-11
    1.5, 1.55, 1.5, 1.45, 1.3, 1.0,   # 12-17
    0.7, 0.5, 0.4, 0.35, 0.3, 0.25,   # 18-23
]


def _normalized(raw: list[float]) -> list[float]:
    total = sum(raw)
    scale = len(raw) / total  # toplam == len(raw) (ortalama ağırlık 1.0)
    return [w * scale for w in raw]


HOURLY_LOAD_WEIGHTS: dict[BuildingDemandType, list[float]] = {
    BuildingDemandType.RESIDENTIAL: _normalized(_RESIDENTIAL_RAW_WEIGHTS),
    BuildingDemandType.COMMERCIAL: _normalized(_COMMERCIAL_RAW_WEIGHTS),
}

HOURS_PER_YEAR = 8760.0


@dataclass(slots=True)
class BuildingDemandProfile:
    """Tek bir binanın yıllık enerji tahminini (energy_audit çıktısı veya
    kullanıcı girdisi) saatlik güce çevirmek için gereken minimum veri.

    `annual_kwh`, `building_reconstruction.energy_audit.EnvelopeAuditReport.
    estimated_annual_heating_kwh`'den ya da başka bir kaynaktan (gerçek
    fatura verisi vb.) doğrudan taşınabilir - bu modül onu **yeniden
    hesaplamaz**, yalnızca gün içine dağıtır.
    """

    building_id: str
    annual_kwh: float
    demand_type: BuildingDemandType = BuildingDemandType.RESIDENTIAL

    def average_kw(self) -> float:
        return self.annual_kwh / HOURS_PER_YEAR

    def hourly_load_kw(self, hour: int) -> float:
        """`hour` (0-23) için gösterge niteliğinde saatlik güç (kW).

        Not: bu bir *tahmindir*, gerçek dakika-bazlı ölçüm değildir - bkz.
        modül docstring'indeki gösterge niteliği notu.
        """
        if not 0 <= hour <= 23:
            raise ValueError(f"hour 0-23 aralığında olmalı, verilen: {hour}")
        weight = HOURLY_LOAD_WEIGHTS[self.demand_type][hour]
        return self.average_kw() * weight

    def daily_curve_kw(self) -> list[float]:
        """0-23 saatlerinin tamamı için güç eğrisi (grafik/rapor için)."""
        return [self.hourly_load_kw(h) for h in range(24)]


@dataclass(slots=True)
class BuildingDemandRegistry:
    """`building_id -> BuildingDemandProfile` basit kayıt defteri.

    Yeni bir depolama icat edilmedi - bu, `TwinHierarchy.aggregate()`'in
    beklediği `metric_fn` imzasına (bir `DigitalTwin` alıp `float` döner)
    köprü kurmak için gereken ince bir eşleme katmanı; gerçek kalıcılık
    `persistence`/`digital_twin` katmanlarına bırakılmıştır (roadmap
    ilkesi #2 - yeni bir depolama şeması icat edilmez).
    """

    profiles: dict[str, BuildingDemandProfile] = field(default_factory=dict)

    def register(self, profile: BuildingDemandProfile) -> None:
        self.profiles[profile.building_id] = profile

    def get(self, building_id: str) -> Optional[BuildingDemandProfile]:
        return self.profiles.get(building_id)

    def metric_fn_for_hour(self, hour: int):
        """`TwinHierarchy.aggregate(registry, twin_id, metric_fn=...)`'e
        doğrudan verilebilecek bir `metric_fn` üretir - `metric_fn(twin)`
        imzası korunur (`twin=None` -> 0.0, roadmap'in "yaprakta twin yoksa
        metric_fn(None) çağrılır" sözleşmesiyle tutarlı)."""

        def _metric(twin: Optional[DigitalTwin]) -> float:
            if twin is None:
                return 0.0
            profile = self.profiles.get(twin.id)
            if profile is None:
                return 0.0
            return profile.hourly_load_kw(hour)

        return _metric


def aggregate_city_demand_kw(
    hierarchy: TwinHierarchy,
    registry: DigitalTwinRegistry,
    demand_registry: BuildingDemandRegistry,
    root_twin_id: str,
    hour: int,
) -> float:
    """Roadmap'in "şehir ölçeğinde toplam talebe agregasyon" maddesi -
    `TwinHierarchy.aggregate()`'in **var olan** memoized toplama desenini
    (yeni bir agregasyon motoru yazılmadı) `demand_registry`'nin ürettiği
    `metric_fn` ile birleştirir. `root_twin_id` bir mahalle/blok/tekil bina
    olabilir - hiyerarşideki herhangi bir düğüm için çalışır.
    """
    metric_fn = demand_registry.metric_fn_for_hour(hour)
    return hierarchy.aggregate(
        registry, root_twin_id, metric_fn, sum, cache_key=f"power_demand_h{hour}",
    )


def peak_hour(demand_registry: BuildingDemandRegistry, building_id: str) -> tuple[int, float]:
    """Tek bir bina için (kullanım kolaylığı) tepe saat + tepe güç."""
    profile = demand_registry.get(building_id)
    if profile is None:
        raise KeyError(f"Bilinmeyen bina: {building_id}")
    curve = profile.daily_curve_kw()
    peak_kw = max(curve)
    return curve.index(peak_kw), peak_kw


__all__ = [
    "BuildingDemandType",
    "HOURLY_LOAD_WEIGHTS",
    "HOURS_PER_YEAR",
    "BuildingDemandProfile",
    "BuildingDemandRegistry",
    "aggregate_city_demand_kw",
    "peak_hour",
]
