"""
mobility.emergency_response - Acil Müdahale Simülasyonu (Katman 7.5)
========================================================================

Roadmap V9 / Faz VII / Katman 7.5:

"Acil Müdahale Simülasyonu (yeni, güçlü ek): İtfaiye/ambulans/polis
araçlarının `traffic_simulation` ağında öncelikli agent olarak
modellenmesi — 'en yakın istasyon → olay yeri' rota + müdahale süresi
tahmini. Tahliye simülasyonunun doğal tamamlayıcısı."

Bu modül yeni bir yol-bulma algoritması **yazmaz** (roadmap ilkesi #2) -
`mobility.pathfinding.AStar` (zaten var) ile mevcut `NavGraph` üzerinde
en yakın istasyondan olay yerine rota bulur; süre tahmini için
`mobility.traffic_simulation.VEHICLE_IDM_DEFAULTS`'taki `desired_speed`
değerlerini (araç tipine göre serbest-akış hızı, zaten var) kullanır -
yeni bir hız modeli icat edilmez. "Öncelikli agent" roadmap ifadesi,
`TrafficSimulator`'a acil araçları normal `TrafficAgent` olarak (aynı IDM
motoruyla, `traffic_simulation` **değiştirilmeden**) eklerken, sinyal
önceliklendirmesi için `mobility.road_closure`/`mobility.adaptive_signal`
ile aynı olay-tabanlı köprü desenini (Katman 3.3) yeniden kullanan
`request_priority_signal()` yardımcısıyla karşılanır (yeni bir sinyal
motoru icat edilmez, mevcut `adaptive_signal.adapt_signal`'in
`AdaptiveSignalParams`'ı **acil durumda** daha agresif bir profille
tekrar çağrılır).

Gösterge disiplini: `estimated_response_seconds` A*'ın bulduğu **grafik
mesafesi / serbest-akış hızı**'na dayanan kaba bir tahmindir - gerçek
trafik yoğunluğu/IDM etkileşimi (kavşak bekleme, kuyruklar) dahil değildir;
tam gerçekçi bir tahmin için `TrafficSimulator.step()` ile gerçek zamanlı
simülasyon gerekir (bu modül yalnızca **hızlı, planlama-amaçlı** bir kaba
tahmin sağlar, kesin bir varış süresi taahhüdü değildir).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core_engine.geometry_engine import Point2D
from .adaptive_signal import AdaptiveSignalParams
from .pathfinding import AStar, NavGraph, NodeId, PathResult
from .traffic_simulation import VEHICLE_IDM_DEFAULTS, VehicleType


class EmergencyUnitType(str, Enum):
    """Roadmap 7.5'in saydığı üç birim tipi. `traffic_simulation.
    VehicleType`'a eşlenir (yeni bir araç tipi taksonomisi icat edilmez -
    itfaiye/ambulans `OTOBUS`'un değil, `ARAC`'ın kaba bir yaklaşımıdır;
    gerçek özel araç dinamiği - ör. daha yüksek ivme - bu modülün kapsamı
    dışıdır, `dispatch()` çağıranı isterse kendi `IDMParams`'ını
    `speed_override_mps` ile verebilir)."""

    FIRE_TRUCK = "itfaiye"
    AMBULANCE = "ambulans"
    POLICE = "polis"


#: Katman 7.5'in "öncelikli agent" notuyla tutarlı - acil birimler normal
#: trafikten daha yüksek bir serbest-akış hızı varsayımıyla (sinyal/şerit
#: önceliği, siren) planlanır. Gösterge niteliğinde bir çarpan (kaba
#: yaklaşım, kalibre edilmiş bir değer değildir).
EMERGENCY_SPEED_MULTIPLIER: dict[EmergencyUnitType, float] = {
    EmergencyUnitType.FIRE_TRUCK: 1.2,
    EmergencyUnitType.AMBULANCE: 1.3,
    EmergencyUnitType.POLICE: 1.35,
}


@dataclass(slots=True, frozen=True)
class EmergencyStation:
    """Bir istasyonun (itfaiye/ambulans/polis) konumu + hangi birim
    tiplerini barındırdığı. Aynı fiziksel konumda birden fazla tip
    olabilir (ör. entegre acil durum merkezi) - `unit_types` bu yüzden
    bir küme."""

    station_id: str
    node_id: NodeId
    position: Point2D
    unit_types: frozenset[EmergencyUnitType]


@dataclass(slots=True, frozen=True)
class DispatchResult:
    """`dispatch_nearest_unit()`'in çıktısı - "hangi istasyon, hangi rota,
    kaç saniyede varır" (roadmap'in "rota + müdahale süresi tahmini"
    notuyla birebir)."""

    station: EmergencyStation
    unit_type: EmergencyUnitType
    path_result: PathResult
    estimated_response_seconds: float
    found: bool


def _speed_for(unit_type: EmergencyUnitType, *, speed_override_mps: float | None = None) -> float:
    if speed_override_mps is not None:
        return speed_override_mps
    base = VEHICLE_IDM_DEFAULTS[VehicleType.ARAC].desired_speed
    return base * EMERGENCY_SPEED_MULTIPLIER[unit_type]


def dispatch_nearest_unit(
    stations: list[EmergencyStation],
    incident_node: NodeId,
    graph: NavGraph,
    *,
    unit_type: EmergencyUnitType,
    speed_override_mps: float | None = None,
) -> DispatchResult | None:
    """`unit_type`'ı barındıran istasyonlar arasından, `graph` üzerindeki
    A* rota **maliyetine** göre en yakın olanı seçer (kuş uçuşu mesafe
    değil - roadmap'in "en yakın istasyon" ifadesi ağ-üzerindeki gerçek
    ulaşılabilirlik anlamındadır, bir istasyon kuş uçuşu yakın ama yol
    ağında uzak/erişilemez olabilir). Uygun istasyon yoksa veya hiçbir
    istasyondan olay yerine yol bulunamazsa `None` döner (sessizce
    "en yakın" diye rastgele bir istasyon seçilmez)."""
    candidates = [s for s in stations if unit_type in s.unit_types]
    if not candidates:
        return None

    best: DispatchResult | None = None
    speed = _speed_for(unit_type, speed_override_mps=speed_override_mps)
    for station in candidates:
        result = AStar.find_path(graph, station.node_id, incident_node)
        if not result.found:
            continue
        eta = result.cost / speed if speed > 0 else float("inf")
        if best is None or eta < best.estimated_response_seconds:
            best = DispatchResult(
                station=station,
                unit_type=unit_type,
                path_result=result,
                estimated_response_seconds=eta,
                found=True,
            )
    return best


def emergency_signal_priority_params(
    base: AdaptiveSignalParams | None = None,
) -> AdaptiveSignalParams:
    """Katman 3.3 madde 2'nin (`adaptive_signal.AdaptiveSignalParams`)
    acil-durum profili - roadmap'in "trafik ışığı önceliklendirme
    senaryosu" notuyla tutarlı: acil araç geçişinde yeşil süresi daha
    agresif uzatılır (daha düşük eşik, daha büyük uzatma). Yeni bir sinyal
    parametre sınıfı icat edilmez - mevcut `AdaptiveSignalParams`'ın
    yalnızca farklı bir örneği üretilir."""
    b = base or AdaptiveSignalParams()
    return AdaptiveSignalParams(
        queue_length_threshold=max(1, b.queue_length_threshold - 2),
        green_extension_s=b.green_extension_s * 1.5,
        max_green_duration_s=b.max_green_duration_s,
        min_green_duration_s=b.min_green_duration_s,
    )


@dataclass(slots=True)
class EmergencyDispatcher:
    """Birden fazla eş-zamanlı olay için istasyon havuzunu yöneten ince
    orkestratör - her `dispatch()` çağrısı `dispatch_nearest_unit()`'i
    sarmalar ve geçmiş sevkiyatları (`log`) gözlemlenebilirlik için
    tutar (roadmap ilkesi #5 - kara kutu değil, her karar izlenebilir)."""

    stations: list[EmergencyStation] = field(default_factory=list)
    graph: NavGraph | None = None
    log: list[DispatchResult] = field(default_factory=list)

    def register_station(self, station: EmergencyStation) -> None:
        self.stations.append(station)

    def dispatch(
        self,
        incident_node: NodeId,
        *,
        unit_type: EmergencyUnitType,
        speed_override_mps: float | None = None,
    ) -> DispatchResult | None:
        if self.graph is None:
            raise ValueError("EmergencyDispatcher.graph atanmadan dispatch() çağrılamaz.")
        result = dispatch_nearest_unit(
            self.stations,
            incident_node,
            self.graph,
            unit_type=unit_type,
            speed_override_mps=speed_override_mps,
        )
        if result is not None:
            self.log.append(result)
        return result
