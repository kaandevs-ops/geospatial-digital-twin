"""
City Events — Şehir-Geneli Olay Sözlüğü
=========================================

ROADMAP_V9.md — Faz I / OMURGA / O.4 "Olay Veri Yolu (Event Bus)".

`extensibility.event_system.EventSystem` (Faz 14) zaten stdlib-only,
isim-bazlı (glob destekli) bir pub/sub sağlıyor. O.4 yeni bir mesajlaşma
altyapısı istemiyor — roadmap metninin kendi ifadesiyle: "`TopicBus`/
`EventSystem` zaten mevcut... `CityEventType` enum'uyla genişletilir."
Yani bu modül **event_system'i değiştirmez**, üzerine ortak bir sözlük
(isim sabitleri + tipli payload zarfları + küçük yardımcı fonksiyonlar)
ekler ki K1-K9 katmanları aynı olay isimlerini rastgele string yazmak
yerine tek bir kaynaktan tüketsin (yazım hatası/çakışma riskini ortadan
kaldırmak için).

Kullanım deseni (roadmap örneği):

    from extensibility.event_system import default_bus
    from extensibility.city_events import CityEventType, emit_city_event

    emit_city_event(default_bus, CityEventType.HAZARD_STARTED,
                     hazard_type="earthquake", magnitude=6.1, epicenter=(39.9, 32.8))

    default_bus.subscribe(CityEventType.HAZARD_STARTED,
                           lambda e: crowd_sim.begin_evacuation_mode(e.payload))

Not: `EventSystem.subscribe` glob destekliyor (`fnmatch`) — bu yüzden
`CityEventType` değerleri hiyerarşik nokta-ayraçlı isimlendirilir
(`"hazard.started"`, `"power.outage"` vb.), böylece `"hazard.*"` gibi bir
desenle tüm tehlike olayları tek noktadan dinlenebilir (Katman 7'nin
Cascade Engine'i tam olarak bu deseni kullanacak).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from .event_system import Event, EventSystem


class CityEventType(str, Enum):
    """Roadmap O.4'te sayılan şehir-geneli olay tipleri.

    `str` alt sınıfı olması, `EventSystem.emit(name, ...)`'in beklediği
    düz string API'siyle sürtünmesiz kullanılabilmesi içindir
    (`CityEventType.HAZARD_STARTED == "hazard.started"` doğrudur).
    """

    # --- Katman 7 (Tehlike & Kriz) ---
    HAZARD_STARTED = "hazard.started"
    HAZARD_ENDED = "hazard.ended"
    FIRE_IGNITED = "hazard.fire_ignited"
    FIRE_SPREAD_UPDATED = "hazard.fire_spread_updated"

    # --- Katman 4 (Enerji/Altyapı) ---
    POWER_OUTAGE = "power.outage"
    POWER_RESTORED = "power.restored"

    # --- Katman 3 (Hareket) ---
    ROAD_CLOSED = "traffic.road_closed"
    ROAD_REOPENED = "traffic.road_reopened"
    TRANSIT_DISRUPTED = "transit.disrupted"
    TRANSIT_RESTORED = "transit.restored"

    # --- Katman 2 (İnsan/Kalabalık) ---
    CROWD_SURGE = "crowd.surge"
    EVACUATION_STARTED = "crowd.evacuation_started"
    EVACUATION_COMPLETED = "crowd.evacuation_completed"

    # --- Omurga (City Clock / Recorder) — O.1'de zaten emit ediliyordu,
    #     burada isimler tek kaynağa taşındı (geriye dönük uyumluluk için
    #     city_clock.py kendi ham string'lerini de kabul etmeye devam eder).
    CLOCK_TICK = "city_clock.tick"
    CLOCK_STARTED = "city_clock.started"
    CLOCK_PAUSED = "city_clock.paused"

    # --- O.5 (Reality Feed) ---
    REALITY_FEED_EARTHQUAKE = "reality_feed.earthquake"
    REALITY_FEED_WEATHER = "reality_feed.weather_update"
    REALITY_FEED_SOURCE_ERROR = "reality_feed.source_error"

    # --- Katman 6 (Ekonomi/Ticaret) — Faz IX'ta eklendi. Faz VII'nin
    #     `hazard_data/resilience_timeline.py` docstring'indeki "ticaret
    #     satırı henüz eklenmedi" bilinçli kapsam sınırını kapatır.
    COMMERCE_CLOSED = "commerce.closed"
    COMMERCE_REOPENED = "commerce.reopened"

    # --- Katman 1 (Fiziksel Şehir) — Faz X'te eklendi. "Bina yıkılıp
    #     yeniden yapıldığında (imar/kentsel dönüşüm), üstündeki tüm diğer
    #     katmanların otomatik yeniden hesaplanması" (ROADMAP_V9.md,
    #     Katman 1). Bina geometrisi/kat sayısı değiştiğinde yayınlanır.
    BUILDING_CHANGED = "building.changed"


def emit_city_event(
    bus: EventSystem,
    event_type: CityEventType,
    *,
    source: str | None = None,
    **payload_fields: Any,
) -> Event:
    """`EventSystem.emit`'in ince, tipli bir sarmalayıcısı.

    Roadmap ilkesi #2 ("tekrar yazma yok") gereği `EventSystem.emit`
    tekrar uygulanmaz — yalnızca `CityEventType` → düz string dönüşümü
    ve payload'ı bir sözlükte toplama işini standartlaştırır.
    """
    payload = dict(payload_fields)
    return bus.emit(str(event_type.value), payload=payload, source=source)


def subscribe_city_event(
    bus: EventSystem,
    event_type: CityEventType,
    listener,
) -> Any:
    """`EventSystem.subscribe`'ın `CityEventType` alan ince sarmalayıcısı."""
    return bus.subscribe(str(event_type.value), listener)


#: Katman 7.4 Cascade Engine'in ("X olayı olunca Y tetiklenir") tüketeceği
#: kaba kategori grupları — glob deseni olarak `EventSystem.subscribe`'a
#: doğrudan verilebilir (örn. `subscribe(HAZARD_PATTERN, handler)`).
HAZARD_PATTERN = "hazard.*"
POWER_PATTERN = "power.*"
TRAFFIC_PATTERN = "traffic.*"
TRANSIT_PATTERN = "transit.*"
CROWD_PATTERN = "crowd.*"
REALITY_FEED_PATTERN = "reality_feed.*"
