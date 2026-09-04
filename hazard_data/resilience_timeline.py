"""
hazard_data.resilience_timeline - Toparlanma Zaman Çizelgesi (Katman 7.6)
=============================================================================

Roadmap V9 / Faz VII / Katman 7.6:

"Toparlanma Zaman Çizelgesi (resilience timeline): Afet anından itibaren
saatler/günler içinde hangi katmanın ne zaman normale döndüğü (elektrik X
saatte, yollar Y günde, ticaret Z haftada) — 'resilience curve'
görselleştirmesi."

Bu modül yeni bir olay kayıt mekanizması **icat etmez** (roadmap ilkesi
#2) - `extensibility.event_system.EventSystem`'in zaten var olan
`history()` metodunu (Faz 14) ve `extensibility.city_events.
CityEventType`'ın zaten tanımlı kesinti/onarım çift-olay isimlerini
(`POWER_OUTAGE`/`POWER_RESTORED`, `ROAD_CLOSED`/`ROAD_REOPENED`,
`TRANSIT_DISRUPTED`/`TRANSIT_RESTORED`, `EVACUATION_STARTED`/
`EVACUATION_COMPLETED`) kullanarak "başlangıç olayı" ile "toparlanma
olayı" arasındaki süreyi hesaplar - roadmap'in kendi örnek katmanlarıyla
(elektrik/yol/ticaret) birebir eşleşen bir eşleştirme tablosu (`SYSTEM_
EVENT_PAIRS`) dışında yeni bir kavram eklemez.

**Güncelleme (Faz IX):** Katman 6 (ticaret) satırı artık eklendi -
`extensibility.city_events.CityEventType.COMMERCE_CLOSED`/
`COMMERCE_REOPENED` (Faz IX'ta tanımlandı) + `commerce_props.
economic_resilience`'in ürettiği olaylarla besleniyor. Önceki oturumun
bilinçli kapsam sınırı (bu satırın icat edilmemiş olması) böylece
kapatılmıştır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..extensibility.city_events import CityEventType
from ..extensibility.event_system import Event, EventSystem


@dataclass(slots=True, frozen=True)
class SystemRecoveryPair:
    """Bir "sistem"in kesinti/toparlanma olay çifti - roadmap'in "hangi
    katman ne zaman normale döndü" tablosunun tek bir satırı."""

    system_name: str  # roadmap'in kendi diliyle: "elektrik", "yollar", "toplu_tasima", "tahliye"
    disruption_type: CityEventType
    recovery_type: CityEventType


#: Roadmap 7.6'nın örnek katmanlarıyla (elektrik/yol) ve mevcut projede
#: gerçekten tanımlı olay çiftleriyle (bkz. `city_events.py`) birebir
#: eşleşen tablo. Ticaret (K6) kasıtlı olarak dışarıda bırakıldı (yukarı
#: bakınız "Dürüstlük notu").
SYSTEM_EVENT_PAIRS: tuple[SystemRecoveryPair, ...] = (
    SystemRecoveryPair("elektrik", CityEventType.POWER_OUTAGE, CityEventType.POWER_RESTORED),
    SystemRecoveryPair("yollar", CityEventType.ROAD_CLOSED, CityEventType.ROAD_REOPENED),
    SystemRecoveryPair("toplu_tasima", CityEventType.TRANSIT_DISRUPTED, CityEventType.TRANSIT_RESTORED),
    SystemRecoveryPair("tahliye", CityEventType.EVACUATION_STARTED, CityEventType.EVACUATION_COMPLETED),
    SystemRecoveryPair("ticaret", CityEventType.COMMERCE_CLOSED, CityEventType.COMMERCE_REOPENED),
)


@dataclass(slots=True, frozen=True)
class RecoveryRecord:
    """Tek bir kesinti-toparlanma olayı çiftinin ölçülmüş süresi.
    `recovered=False` ise `recovery_seconds`/`recovered_at` `None`'dır -
    henüz toparlanmamış (sessizce 0 veya sonsuz varsayılmaz)."""

    system_name: str
    disrupted_at: float
    recovered_at: Optional[float]
    recovery_seconds: Optional[float]
    recovered: bool
    disruption_source: Optional[str]


@dataclass(slots=True, frozen=True)
class ResilienceReport:
    """Bir hazard penceresi (`window_start`/`window_end`) için tüm
    sistemlerin toparlanma kayıtları - Katman 9'un "resilience curve"
    görselleştirmesinin/rapor anlatıcısının doğrudan tüketebileceği
    düz veri yapısı."""

    window_start: float
    window_end: Optional[float]
    records: list[RecoveryRecord]

    def by_system(self, system_name: str) -> list[RecoveryRecord]:
        return [r for r in self.records if r.system_name == system_name]

    def slowest_system(self) -> Optional[RecoveryRecord]:
        """En uzun toparlanma süresine sahip (toparlanmış) kaydı döner -
        "hangi sistem en yavaş normale döndü" sorusu için (roadmap'in
        "Z haftada" karşılaştırma diliyle tutarlı). Hiç toparlanmış kayıt
        yoksa `None` döner."""
        recovered = [r for r in self.records if r.recovered and r.recovery_seconds is not None]
        if not recovered:
            return None
        return max(recovered, key=lambda r: r.recovery_seconds)

    def unrecovered_systems(self) -> list[str]:
        """Pencere sonunda hâlâ toparlanmamış sistemlerin adları - "hâlâ
        elektrik yok" gibi açık bir uyarı listesi (sessizce yutulmaz)."""
        return [r.system_name for r in self.records if not r.recovered]


def build_resilience_report(
    bus: EventSystem,
    *,
    window_start: float = 0.0,
    window_end: Optional[float] = None,
    pairs: tuple[SystemRecoveryPair, ...] = SYSTEM_EVENT_PAIRS,
) -> ResilienceReport:
    """`EventSystem.history()`'yi (yeni bir kayıt mekanizması icat
    edilmeden, roadmap ilkesi #2) tarayıp her sistem için en erken
    kesinti olayını, ondan **sonraki** en erken toparlanma olayıyla
    eşleştirir (basit, sıralı eşleştirme - iç içe geçmiş çoklu
    kesinti/toparlanma döngüleri varsa yalnızca ilk çift raporlanır; bu
    bilinçli bir basitleştirmedir, roadmap'in "resilience curve" özeti
    ihtiyacı için yeterlidir, ayrıntılı çoklu-döngü analizi kapsam dışıdır).
    """
    records: list[RecoveryRecord] = []
    for pair in pairs:
        disruptions = [
            e for e in bus.history(str(pair.disruption_type.value))
            if window_start <= e.timestamp and (window_end is None or e.timestamp <= window_end)
        ]
        if not disruptions:
            continue
        disruption = min(disruptions, key=lambda e: e.timestamp)

        recoveries = [
            e for e in bus.history(str(pair.recovery_type.value))
            if e.timestamp >= disruption.timestamp
            and (window_end is None or e.timestamp <= window_end)
        ]
        if recoveries:
            recovery = min(recoveries, key=lambda e: e.timestamp)
            duration = recovery.timestamp - disruption.timestamp
            records.append(RecoveryRecord(
                system_name=pair.system_name, disrupted_at=disruption.timestamp,
                recovered_at=recovery.timestamp, recovery_seconds=duration,
                recovered=True, disruption_source=disruption.source,
            ))
        else:
            records.append(RecoveryRecord(
                system_name=pair.system_name, disrupted_at=disruption.timestamp,
                recovered_at=None, recovery_seconds=None,
                recovered=False, disruption_source=disruption.source,
            ))
    return ResilienceReport(window_start=window_start, window_end=window_end, records=records)
