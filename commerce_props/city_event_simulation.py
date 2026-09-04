"""
commerce_props.city_event_simulation - Etkinlik/Olay Simülasyonu (Katman 6.2)
=================================================================================

ROADMAP_V9.md / Faz IX / Katman 6:

    "Etkinlik/olay simülasyonu: Konser, maç, festival gibi geçici
    yüksek-yoğunluk olayları (`sport_recreation`) - Event Bus'a
    `CROWD_SURGE` olarak düşer, Katman 2/3 anlık talep artışıyla tepki
    verir."

**Dürüstlük notu (isim çakışması, bilinçli):** Roadmap metninin "Nereye"
notu "yeni `city_events/` modülü" diyordu - ama O.4'te (Faz I / Omurga)
zaten `extensibility/city_events.py` adında, tam olarak bu işi (şehir-
geneli olay tipleri + emit/subscribe sarmalayıcıları) yapan bir modül
oluşturulmuştu; roadmap'in orijinal yazıldığı an ile O.4'ün uygulandığı an
arasındaki bir isimlendirme rastlantısı. Roadmap ilkesi #2 ("tekrar yazma
yok") gereği burada **yeni bir olay-yolu modülü açılmadı** - `CityEventType.
CROWD_SURGE` (zaten O.4'te tanımlıydı) doğrudan kullanıldı; bu modül yalnızca
etkinliğin kendisini (katılımcı sayısı, süre, konum) modelleyip doğru
payload'la CROWD_SURGE'ü tetikleyen ince bir orkestrasyon katmanıdır.

Katılımcı zaman eğrisi (ramp-up/plateau/ramp-down) `mobility.
crowd_simulation`'ın "anlık talep artışı" ile tüketilebilecek basit bir
yamuk (trapezoid) profildir - `hazard_data.fire_spread`'in hücre-otomat
modeli gibi tam bir kalabalık-akış simülasyonu DEĞİLDİR (roadmap'in kendi
notuyla tutarlı bilinçli basitleştirme, "en çok mühendislik" burada değil
Katman 7.2'de harcanmıştı).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..extensibility.city_events import CityEventType, emit_city_event
from ..extensibility.event_system import EventSystem


class CityEventCategory(str, Enum):
    CONCERT = "concert"
    SPORTS_MATCH = "sports_match"
    FESTIVAL = "festival"
    MARKET_FAIR = "market_fair"


@dataclass(slots=True)
class CityEventProfile:
    """Tek bir geçici yüksek-yoğunluk etkinliğinin tanımı - `sport_recreation`
    veya `commerce_props` nesnelerinden (stadyum, meydan, pazar alanı)
    konum referansı taşır, geometriyi yeniden üretmez."""

    event_id: str
    category: CityEventCategory
    location_ref: str  # ör. bir sport_recreation/commerce_props nesne id'si
    expected_attendance: int
    start_hour: float  # 0-24 ondalık saat
    duration_h: float
    ramp_fraction: float = 0.15  # başlangıç/bitişteki yoğunluk-artış payı


@dataclass(slots=True)
class AttendanceCurvePoint:
    hour: float
    attendance: int


def attendance_curve(profile: CityEventProfile, samples: int = 20) -> list[AttendanceCurvePoint]:
    """Yamuk (trapezoid) katılımcı-zaman eğrisi: `ramp_fraction` payı kadar
    süre içinde 0 -> tepe, ortada plato, son `ramp_fraction` payı kadar
    süre içinde tepe -> 0. `samples` nokta sayısı grafik/animasyon için
    yeterli çözünürlük sağlar (roadmap'in O.1 recorder'ındaki "keyframe"
    fikriyle aynı disiplin - tam çözünürlükte kayıt yapılmaz)."""
    if samples < 2:
        raise ValueError("samples en az 2 olmalı")
    ramp = max(0.0, min(0.5, profile.ramp_fraction))
    points: list[AttendanceCurvePoint] = []
    for i in range(samples):
        t = i / (samples - 1)  # 0..1 normalize zaman
        if t < ramp:
            factor = t / ramp if ramp > 0 else 1.0
        elif t > 1.0 - ramp:
            factor = (1.0 - t) / ramp if ramp > 0 else 1.0
        else:
            factor = 1.0
        hour = profile.start_hour + t * profile.duration_h
        attendance = round(profile.expected_attendance * max(0.0, min(1.0, factor)))
        points.append(AttendanceCurvePoint(hour=hour, attendance=attendance))
    return points


def peak_attendance(profile: CityEventProfile) -> int:
    return profile.expected_attendance


class CityEventSimulator:
    """`CityEventProfile`'ı Event Bus'a `CROWD_SURGE` olarak tetikleyen
    ince orkestratör - `extensibility.event_system.EventSystem` (Faz 14,
    değiştirilmedi) üzerine `extensibility.city_events.emit_city_event`
    (O.4, değiştirilmedi) ile ince bir sarmalayıcı."""

    def __init__(self, bus: EventSystem | None = None):
        self.bus = bus
        self.triggered_log: list[CityEventProfile] = []

    def trigger(
        self, profile: CityEventProfile, *, source: str | None = None
    ) -> list[AttendanceCurvePoint]:
        curve = attendance_curve(profile)
        self.triggered_log.append(profile)
        if self.bus is not None:
            emit_city_event(
                self.bus,
                CityEventType.CROWD_SURGE,
                source=source or profile.location_ref,
                event_id=profile.event_id,
                category=profile.category.value,
                location_ref=profile.location_ref,
                expected_attendance=profile.expected_attendance,
                start_hour=profile.start_hour,
                duration_h=profile.duration_h,
            )
        return curve


__all__ = [
    "CityEventCategory",
    "CityEventProfile",
    "AttendanceCurvePoint",
    "attendance_curve",
    "peak_attendance",
    "CityEventSimulator",
]
