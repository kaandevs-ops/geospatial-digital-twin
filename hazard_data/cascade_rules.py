"""
hazard_data.cascade_rules - Kademeli Etki Motoru / Cascade Engine (Katman 7.4)
==================================================================================

Roadmap V9 / Faz VII / Katman 7.4:

"Kademeli Etki Motoru (Cascade Engine): Yukarıdaki zincirin Event Bus
üzerinden otomatik tetiklenmesi — `hazard_data/cascade_rules.py`, 'X
olayı olduğunda Y olasılıkla Z tetiklenir' kural tablosu (basit, açık,
ayarlanabilir olasılık kuralları — kara kutu değil)."

Bu modül Katman 7 girişindeki zinciri (DEPREM -> elektrik kesintisi ->
trafik ışıkları söner / asansör durur, DEPREM -> bazı binalarda yangın,
yangın -> duman -> rota yeniden hesaplama, deprem -> yol hasarı ->
NavGraph kenar kapanması, ...) **yeni bir simülasyon motoru olarak
yazmaz** (roadmap ilkesi #2) - `extensibility.event_system.EventSystem`
(zaten var, Faz 14) + `extensibility.city_events.CityEventType` (O.4'te
zaten tanımlı `HAZARD_PATTERN`/`POWER_PATTERN`/... glob desenleri dahil)
üzerine ince bir kural-tetikleyici katmanı ekler: "X deseni geldiğinde P
olasılıkla Y olayını yayınla."

Gösterge disiplini: kurallar `CascadeRule` dataclass'ında **açık, okunabilir
alanlar** olarak tanımlanır (kara kutu bir ML modeli değil) - her kuralın
olasılığı, koşulu ve kaynağı roadmap metninden doğrudan izlenebilir olacak
şekilde yorumlanmıştır. `DEFAULT_CASCADE_RULES` roadmap'in Katman 7
girişindeki ASCII zincir diyagramının **birebir** kural tablosuna
çevrilmiş halidir - yeni bir varsayım eklenmemiştir.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from ..extensibility.city_events import CityEventType, emit_city_event
from ..extensibility.event_system import Event, EventSystem


@dataclass(slots=True, frozen=True)
class CascadeRule:
    """Tek bir "X olduğunda P olasılıkla Y tetiklenir" kuralı.

    `trigger_type`: dinlenecek `CityEventType` (tam eşleşme - glob için
    `CascadeEngine`'in kendi desen-bazlı alt kayıtları kullanılır, bkz.
    aşağı).
    `probability`: 0.0-1.0 - roadmap'in "olasılıksal" notuyla tutarlı
    (deterministik bir kural için 1.0 verilir, örn. "deprem her zaman yol
    hasarı riskini artırır" gibi kesin zincirler).
    `effect_type`: tetiklenirse yayınlanacak yeni `CityEventType`.
    `condition`: opsiyonel, tetikleyici olayın payload'ını inceleyip
    True/False döneven ek filtre (ör. yalnızca `magnitude >= 6.0` ise
    yangın riski değerlendirilsin). Verilmezse her tetikleyici olay
    değerlendirilir.
    `payload_fn`: opsiyonel, tetikleyici `Event`'ten yeni olayın payload'ını
    türeten fonksiyon (verilmezse boş payload ile yayınlanır - sessizce
    tetikleyici payload'ı kopyalanmaz, çağıran taraf hangi alanların
    taşınacağını **bilinçli olarak** seçer).
    `cooldown_s`: aynı kural, aynı kaynak (`Event.source`) için bu süre
    içinde tekrar tetiklenmez (ör. bir binanın yangın riski her tick'te
    yeniden değerlendirilip sürekli yeni FIRE_IGNITED olayı üretmesin diye).
    """

    name: str
    trigger_type: CityEventType
    probability: float
    effect_type: CityEventType
    condition: Callable[[Event], bool] | None = None
    payload_fn: Callable[[Event], dict] | None = None
    cooldown_s: float = 0.0


def _magnitude_at_least(min_magnitude: float) -> Callable[[Event], bool]:
    def _check(event: Event) -> bool:
        payload = event.payload or {}
        magnitude = payload.get("magnitude")
        return magnitude is not None and magnitude >= min_magnitude

    return _check


def _carry_epicenter(event: Event) -> dict:
    payload = event.payload or {}
    out = {}
    if "epicenter" in payload:
        out["epicenter"] = payload["epicenter"]
    if "hazard_type" in payload:
        out["triggered_by"] = payload["hazard_type"]
    return out


#: Roadmap'in Katman 7 girişindeki ASCII zincirinin kural tablosu karşılığı.
#: Olasılık değerleri roadmap'in kendi metninde sayısal olarak verilmediği
#: için buradaki değerler **açıkça gösterge niteliğinde, ayarlanabilir**
#: varsayılanlardır (kara kutu değil - her satır tek başına okunup
#: değiştirilebilir); gerçek bir kalibre edilmiş afet-etki modeli değildir.
DEFAULT_CASCADE_RULES: tuple[CascadeRule, ...] = (
    CascadeRule(
        name="deprem_elektrik_kesintisi",
        trigger_type=CityEventType.HAZARD_STARTED,
        probability=0.7,
        effect_type=CityEventType.POWER_OUTAGE,
        condition=_magnitude_at_least(5.0),
        payload_fn=_carry_epicenter,
    ),
    CascadeRule(
        name="deprem_yangin_riski",
        trigger_type=CityEventType.HAZARD_STARTED,
        probability=0.15,
        effect_type=CityEventType.FIRE_IGNITED,
        condition=_magnitude_at_least(6.0),
        payload_fn=_carry_epicenter,
    ),
    CascadeRule(
        name="deprem_yol_hasari",
        trigger_type=CityEventType.HAZARD_STARTED,
        probability=0.4,
        effect_type=CityEventType.ROAD_CLOSED,
        condition=_magnitude_at_least(5.5),
        payload_fn=_carry_epicenter,
    ),
    CascadeRule(
        name="elektrik_kesintisi_toplu_tasima_aksamasi",
        trigger_type=CityEventType.POWER_OUTAGE,
        probability=0.5,
        effect_type=CityEventType.TRANSIT_DISRUPTED,
        payload_fn=_carry_epicenter,
    ),
    CascadeRule(
        name="yangin_tahliye_baslatma",
        trigger_type=CityEventType.FIRE_IGNITED,
        probability=1.0,
        effect_type=CityEventType.EVACUATION_STARTED,
        payload_fn=_carry_epicenter,
    ),
)


@dataclass(slots=True)
class CascadeEngine:
    """`EventSystem`'e abone olup `DEFAULT_CASCADE_RULES` (veya çağıranın
    verdiği özel kural listesi) doğrultusunda kademeli olayları otomatik
    tetikler.

    **Dürüstlük notu:** olasılıksal tetikleme `random.Random` (seed'lenebilir
    - determinizm/test edilebilirlik için) ile yapılır; "tetiklenmedi"
    durumu sessizce yutulmaz, `triggered_log`/`skipped_log` her ikisi de
    gözlemlenebilirlik için ayrı ayrı tutulur.
    """

    bus: EventSystem
    rules: tuple[CascadeRule, ...] = DEFAULT_CASCADE_RULES
    seed: int | None = None
    triggered_log: list[dict] = field(default_factory=list)
    skipped_log: list[dict] = field(default_factory=list)
    _rng: random.Random = field(init=False, repr=False)
    _last_triggered_at: dict[tuple[str, str | None], float] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _now_fn: Callable[[], float] = field(default=lambda: 0.0, repr=False)
    _rules_by_trigger: dict[str, list[CascadeRule]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        for rule in self.rules:
            self._rules_by_trigger.setdefault(str(rule.trigger_type.value), []).append(rule)

    def set_clock(self, now_fn: Callable[[], float]) -> None:
        """Cooldown hesaplamasının kullanacağı zaman kaynağını değiştirir
        (ör. `city_clock.CityClock.now`) - verilmezse sabit 0.0 zaman
        kullanılır (cooldown'suz, her zaman izin verir - test kolaylığı)."""
        self._now_fn = now_fn

    def start(self) -> None:
        """Kural tablosundaki her tetikleyici tip için tek bir abonelik
        açar (aynı tetikleyici tipe birden çok kural bağlı olsa da,
        `EventSystem`'e tek dinleyici kaydedilir - roadmap ilkesi #2)."""
        for trigger_name in self._rules_by_trigger:
            self.bus.subscribe(trigger_name, self._on_event)

    def _on_event(self, event: Event) -> None:
        rules = self._rules_by_trigger.get(event.name, [])
        now = self._now_fn()
        for rule in rules:
            key = (rule.name, event.source)
            last = self._last_triggered_at.get(key)
            if last is not None and (now - last) < rule.cooldown_s:
                self.skipped_log.append(
                    {"rule": rule.name, "reason": "cooldown", "event": event.name}
                )
                continue
            if rule.condition is not None and not rule.condition(event):
                self.skipped_log.append(
                    {"rule": rule.name, "reason": "condition_false", "event": event.name}
                )
                continue
            roll = self._rng.random()
            if roll >= rule.probability:
                self.skipped_log.append(
                    {"rule": rule.name, "reason": "probability_miss", "event": event.name}
                )
                continue
            payload = rule.payload_fn(event) if rule.payload_fn else {}
            emitted = emit_city_event(
                self.bus, rule.effect_type, source=f"cascade:{rule.name}", **payload
            )
            self._last_triggered_at[key] = now
            self.triggered_log.append(
                {"rule": rule.name, "trigger_event": event.name, "effect_event": emitted.name}
            )
