"""
hazard_data.hazard_event - Çoklu Tehlike Tipi Ortak Arayüzü (Katman 7.1)
============================================================================

Roadmap V9 / Faz VII / Katman 7.1:

"Çoklu Tehlike Tipi Çerçevesi: `hazard_data/` şu an deprem-ağırlıklı
(AFAD/USGS/PGA) + sel/heyelan (`flood_landslide.py`) içeriyor. Genel bir
`HazardEvent` soyutlaması (`hazard_data/hazard_event.py`) — deprem,
yangın, sel, sıcak dalgası gibi farklı tehlike tiplerinin ortak arayüzü
(etki alanı, şiddet, zaman içi yayılım fonksiyonu)."

Bu modül `afad_client`/`usgs_client`/`flood_landslide`/`fire_spread`'i
**değiştirmez** (roadmap ilkesi #2) — her biri kendi alanının uzmanı
kalır (deprem kataloğu, terrain-tabanlı sel/heyelan skoru, hücre-otomat
duman yayılımı). `HazardEvent` bunların **üzerine**, Katman 7.4'ün
(Cascade Engine) ve Katman 9'un (rapor anlatıcısı/what-if) tek bir tipten
okuyabileceği ortak bir zarf (envelope) sağlar: "hangi tip, nerede, ne
şiddette, ne zaman başladı/bitti, o noktayı etkiliyor mu."

Gösterge disiplini: `severity` 0.0-1.0 aralığında GÖSTERGE niteliğinde bir
şiddet skorudur (deprem için PGA/büyüklük türevi, yangın için
`FireSpreadModel` yoğunluğu, sel/heyelan için `TerrainHazardReport.risk_index`
türevi olabilir) - kesin bir bilimsel ölçüm birimi değildir, çağıran taraf
kendi alanının skorunu 0-1'e kendi normalize eder (burada sessizce bir
dönüşüm icat edilmez).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..core_engine.geometry_engine import Point2D


class HazardType(str, Enum):
    """Roadmap 7.1'in listelediği tehlike tipleri - `extensibility.
    city_events.CityEventType` ile aynı `str` alt sınıfı deseni (event
    payload'ında sürtünmesiz kullanılabilsin diye)."""

    EARTHQUAKE = "earthquake"
    FIRE = "fire"
    FLOOD = "flood"
    LANDSLIDE = "landslide"
    HEATWAVE = "heatwave"
    STORM = "storm"


@dataclass(slots=True)
class HazardEvent:
    """Tek bir aktif (veya geçmiş) tehlikenin ortak zarfı.

    `epicenter`/`radius_m`: dairesel etki alanı yaklaşımı (roadmap'in
    kendi gösterge disipliniyle tutarlı basitleştirme - gerçek deprem
    izoseist eğrileri veya yangın hücre-otomat sınırları elips/düzensiz
    olabilir; `affects_point()` bu basit modeli kullanır, ama çağıran
    taraf isterse kendi alan-özel testini (ör. `FireSpreadModel`'in hücre
    durumunu) ayrıca sorgulayabilir - bu sınıf onu **engellemez**, yalnızca
    ortak bir hızlı-yaklaşım sağlar).
    `spread_fn`: opsiyonel, `(elapsed_seconds) -> yeni radius_m` biçiminde
    zaman-içi yayılım fonksiyonu (verilmezse tehlike statik kabul edilir -
    ör. sabit-alanlı bir sel haritası; deprem PGA alanı da tipik olarak
    statiktir, yalnızca yangın gibi dinamik yayılan tehlikeler bunu verir).
    """

    hazard_type: HazardType
    epicenter: Point2D
    radius_m: float
    severity: float  # 0.0 (etkisiz) - 1.0 (şiddetli), gösterge niteliğinde
    started_at: float  # sahne/simülasyon zamanı (saniye), city_clock ile tutarlı birim
    ended_at: Optional[float] = None
    source: str = "unspecified"  # ör. "afad", "fire_spread_model", "terrain_hazard"
    spread_fn: Optional[callable] = None
    metadata: dict = field(default_factory=dict)

    def is_active(self, now: float) -> bool:
        if now < self.started_at:
            return False
        if self.ended_at is not None and now >= self.ended_at:
            return False
        return True

    def current_radius_m(self, now: float) -> float:
        """`spread_fn` verilmişse zamana göre büyüyen/küçülen yarıçapı
        döner; verilmemişse statik `radius_m`'i döner (sessizce sıfır
        yayılım varsayılmaz - yalnızca çağıranın bilinçli olarak
        `spread_fn=None` bıraktığı anlamına gelir)."""
        if self.spread_fn is None:
            return self.radius_m
        elapsed = max(0.0, now - self.started_at)
        return max(0.0, self.spread_fn(elapsed))

    def affects_point(self, point: Point2D, *, now: Optional[float] = None) -> bool:
        """Verilen noktanın (basit dairesel yaklaşımla) tehlike alanı
        içinde olup olmadığını döner. `now` verilmezse statik `radius_m`
        kullanılır (zaman-bağımsız bir "bu alan tehlikeli mi" sorgusu)."""
        radius = self.radius_m if now is None else self.current_radius_m(now)
        if radius <= 0.0:
            return False
        return self.epicenter.distance_to(point) <= radius


@dataclass(slots=True)
class HazardRegistry:
    """Aktif/geçmiş `HazardEvent`'lerin basit, bellek-içi kaydı - Katman
    7.4 (Cascade Engine) ve Katman 9 (rapor/what-if) buradan okur.

    Yeni bir kalıcılık mekanizması icat edilmez (roadmap ilkesi #2) - bu
    yalnızca çalışma-anı bir liste + basit sorgu yardımcılarıdır; kalıcı
    saklama gerekiyorsa çağıran taraf `persistence/` katmanını ayrıca
    kullanır.
    """

    events: list[HazardEvent] = field(default_factory=list)

    def register(self, event: HazardEvent) -> HazardEvent:
        self.events.append(event)
        return event

    def active_at(self, now: float) -> list[HazardEvent]:
        return [e for e in self.events if e.is_active(now)]

    def active_of_type(self, hazard_type: HazardType, now: float) -> list[HazardEvent]:
        return [e for e in self.active_at(now) if e.hazard_type == hazard_type]

    def affecting_point(self, point: Point2D, now: float) -> list[HazardEvent]:
        return [e for e in self.active_at(now) if e.affects_point(point, now=now)]

    def end(self, event: HazardEvent, *, now: float) -> None:
        """Bir tehlikeyi bu registry üzerinden kapatır (`event.ended_at`
        set edilir) - `event` bu registry'de kayıtlı olmasa bile
        (çağıranın referansı elindeyse) çalışır, çünkü `HazardEvent`
        `slots=True` mutable bir dataclass'tır."""
        event.ended_at = now
