"""
Bina Değişimi / Kentsel Dönüşüm Kaskadı — ROADMAP_V9 Faz X / Katman 1
==========================================================================

Roadmap metni (Katman 1): "Yaşayan ikiz için eklenmesi gereken: Katmanın
**değişebilir** olması — bina yıkılıp yeniden yapıldığında (imar/kentsel
dönüşüm senaryosu), üstündeki tüm diğer katmanların (nüfus, trafik,
enerji talebi) otomatik yeniden hesaplanması. Bu, Katman 9 (What-If) ile
doğrudan bağlantılı — `persistence/project_manager.py`'nin versiyonlama
altyapısı temel oluşturur."

Bu modül **yeni bir hesap motoru yazmaz** (roadmap ilkesi #2): enerji
talebi `power_infrastructure.demand_model`, nüfus `population.
synthetic_population`, ticari canlılık `commerce_props.footfall_model`
tarafından zaten hesaplanıyor — burada yalnızca "bina değişti" olayını
(`CityEventType.BUILDING_CHANGED`, olay-güdümlü mimari ilkesi #5) dinleyip
hangi katmanların yeniden hesaplanması gerektiğini **orkestre eden** ince
bir katman var. Katmanlar birbirini doğrudan çağırmaz (roadmap'in kendi
disiplini) — her biri `BuildingRedevelopmentOrchestrator`'a kayıtlı bir
`recompute` callback'i olarak katılır.

Faz X'in `persistence.project_manager` dallanması (branching) ile birlikte
kullanılabilir: "3 kat daha yüksek bina yapılırsa" senaryosu önce bir dala
(`create_branch`) uygulanır, bu modül dal üzerinde yeniden hesaplamayı
tetikler, sonuçlar Katman 9'un before/after karşılaştırmasına girdi olur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..extensibility.city_events import CityEventType, emit_city_event
from ..extensibility.event_system import EventSystem


@dataclass(slots=True)
class BuildingChangeRecord:
    """Bir bina değişikliğinin özeti — roadmap'in "yıkılıp yeniden
    yapıldığında" senaryosunu genel tutmak için yalnızca **öncesi/sonrası**
    ölçülebilir alanları taşır (geometri motoruna bağımlı değildir)."""

    building_id: str
    change_kind: str  # "demolished" | "rebuilt" | "floor_count_changed" | ...
    old_floor_count: Optional[int] = None
    new_floor_count: Optional[int] = None
    old_building_type: Optional[str] = None
    new_building_type: Optional[str] = None


#: Bir yeniden-hesaplama callback'i: değişikliği alır, hangi katmanı
#: güncellediğini bir dize olarak döner (raporlama/izlenebilirlik için).
RecomputeCallback = Callable[[BuildingChangeRecord], str]


@dataclass(slots=True)
class RedevelopmentReport:
    """Bir `BUILDING_CHANGED` olayına tepki olarak hangi katmanların
    yeniden hesaplandığının izlenebilir kaydı — "kara kutu değil"
    ilkesiyle tutarlı."""

    change: BuildingChangeRecord
    recomputed_layers: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class BuildingRedevelopmentOrchestrator:
    """`CityEventType.BUILDING_CHANGED` olayını dinler, kayıtlı tüm
    katman-yeniden-hesaplama callback'lerini sırayla çağırır.

    Roadmap ilkesi #5 (olay-güdümlü mimari): katmanlar birbirini doğrudan
    çağırmaz — her katman (enerji, nüfus, ticaret, ...) kendi
    `register_layer()` çağrısıyla bağımsız olarak katılır; yeni bir katman
    eklemek mevcut kodu bozmadan mümkündür.
    """

    def __init__(self, bus: EventSystem | None = None) -> None:
        self._bus = bus
        self._layers: dict[str, RecomputeCallback] = {}
        self.history: list[RedevelopmentReport] = []
        if bus is not None:
            bus.subscribe(CityEventType.BUILDING_CHANGED.value, self._on_building_changed)

    def register_layer(self, name: str, callback: RecomputeCallback) -> None:
        """Bir katmanı (örn. `"energy_demand"`, `"synthetic_population"`,
        `"commerce_footfall"`) yeniden-hesaplama listesine ekler."""
        self._layers[name] = callback

    def unregister_layer(self, name: str) -> None:
        self._layers.pop(name, None)

    def _on_building_changed(self, event: Any) -> None:
        payload = getattr(event, "payload", {}) or {}
        change = BuildingChangeRecord(**payload) if not isinstance(payload, BuildingChangeRecord) else payload
        self.apply(change)

    def apply(self, change: BuildingChangeRecord) -> RedevelopmentReport:
        """Bir değişikliği tüm kayıtlı katmanlara uygular ve raporu döner.

        Bir katmanın callback'i hata fırlatırsa **sessizce yutulmaz**
        (roadmap ilkesi #1, gösterge disiplini/şeffaflık) — `errors`
        listesine düşer, diğer katmanların yeniden hesaplanması durmaz.
        """
        report = RedevelopmentReport(change=change)
        for name, callback in self._layers.items():
            try:
                result = callback(change)
                report.recomputed_layers.append(result or name)
            except Exception as exc:  # noqa: BLE001 - izlenebilir hata toplama
                report.errors.append(f"{name}: {exc}")
        self.history.append(report)
        return report

    def notify_building_changed(self, change: BuildingChangeRecord, *, source: str = "editor") -> RedevelopmentReport:
        """Doğrudan çağrı yolu (Event Bus olmadan da kullanılabilir —
        örn. `app_shell.session`'ın senkron REST akışında): olayı yayınlar
        (varsa) ve yeniden hesaplamayı tetikler.

        Bus verilmişse `emit_city_event` zaten kayıtlı `_on_building_changed`
        abonesini senkron tetikler (`EventSystem.emit` senkron yayın yapar)
        — bu yüzden burada **ikinci kez** `apply()` çağrılmaz (çift
        hesaplama önlenir). Bus verilmemişse doğrudan `apply()` çağrılır.
        """
        if self._bus is not None:
            event = emit_city_event(
                self._bus, CityEventType.BUILDING_CHANGED,
                source=source,
                building_id=change.building_id,
                change_kind=change.change_kind,
                old_floor_count=change.old_floor_count,
                new_floor_count=change.new_floor_count,
                old_building_type=change.old_building_type,
                new_building_type=change.new_building_type,
            )
            # `_on_building_changed` zaten `self.history`'ye ekledi.
            return self.history[-1] if self.history else self.apply(change)
        return self.apply(change)


__all__ = [
    "BuildingChangeRecord",
    "RedevelopmentReport",
    "BuildingRedevelopmentOrchestrator",
    "RecomputeCallback",
]
