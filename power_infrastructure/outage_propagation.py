"""
power_infrastructure.outage_propagation - Kesinti Senaryosu / Yayılım (Katman 4 madde 2)
============================================================================================

ROADMAP_V9.md / Faz VIII / Katman 4:

    "Kesinti senaryosu: Katman 7'den `POWER_OUTAGE` olayı -> hangi
    binaların/trafoların etkilendiği `power_infrastructure` grafiği
    üzerinde yayılım analizi - doğrudan tahliye simülasyonunu etkiler
    (karanlık koridorda yürüme hızı düşer, asansör zaten kapalıydı, şimdi
    aydınlatma da gidiyor)."

Bu modül **yeni bir graf yapısı icat etmez** (roadmap ilkesi #2):
`mobility.pathfinding.NavGraph` - Katman 3.1'in açık-alan ağı ve Faz IV/V'in
dinamik ağırlıklandırması için zaten kullanılan aynı sınıf - burada elektrik
şebekesi topolojisini (trafo <-> hat <-> bina) temsil etmek için yeniden
kullanılır. Yayılım analizi de yeni bir algoritma değil: `NavGraph.
neighbors()` üzerinde basit bir BFS + karşılaştırmalı erişilebilirlik
kümesi farkı ("trafo X devre dışıyken hangi binalar artık HİÇBİR sağlam
trafodan erişilemez").

Event Bus entegrasyonu `extensibility.city_events` (O.4, değiştirilmedi)
üzerinden - Katman 7 Cascade Engine'in `POWER_OUTAGE`/`POWER_RESTORED`
olaylarını hem dinleyebilir (bir trafo depremde devre dışı kaldığında) hem
de kendi ayrıntılı sonucunu (`etkilenen bina listesi`) tekrar yayınlayabilir.

GÖSTERGE NİTELİĞİ: gerçek şebeke koruma/röle mantığı (kesinti anında hangi
kesicinin açacağı, N-1 güvenilirlik analizi vb.) modellenmemiştir - bu,
"trafo X çalışmıyorsa, kalan trafolardan grafiksel olarak ulaşılamayan
binalar etkilenmiştir" düzeyinde basit bir bağlanabilirlik analizidir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..core_engine.geometry_engine import Point2D
from ..extensibility.city_events import CityEventType, emit_city_event
from ..extensibility.event_system import EventSystem
from ..mobility.indoor_navigation import HazardScenarioRules
from ..mobility.pathfinding import NavGraph

#: Roadmap'in "karanlık koridorda yürüme hızı düşer" notu - `mobility.
#: crowd_simulation.Agent.effective_desired_speed()`'e (Faz III'te profil
#: çarpanı eklendi) uygulanabilecek **ek** bir çarpan olarak dışa açılır.
#: Bilinçli kapsam sınırı: bu modül `SocialForceModel`'i değiştirmez,
#: yalnızca çarpanı hesaplar - motora bağlama roadmap'in kendi "izlenebilir
#: bir sonraki adım" disipliniyle ayrı bırakılmıştır (Faz II'nin asansör
#: entegrasyonunda da aynı desen kullanılmıştı).
DARK_CORRIDOR_SPEED_MULTIPLIER = 0.7


def build_power_network_graph(
    substation_ids: Iterable[str],
    building_ids: Iterable[str],
    lines: Iterable[tuple[str, str]],
    positions: Optional[dict[str, Point2D]] = None,
) -> NavGraph:
    """`lines`: (kaynak_id, hedef_id) çiftleri - trafo-trafo veya
    trafo-bina bağlantısı, `power_infrastructure.osm_bridge`'in ürettiği
    hat/trafo verisinden çağıran taraf tarafından türetilir (bu fonksiyon
    OSM'i yeniden parse etmez, yalnızca topolojiyi grafa yazar).

    `positions` verilmezse tüm düğümler `Point2D(0, 0)`'a yerleştirilir -
    bu modül yalnızca *bağlanabilirlik* için grafı kullanır, `AStar`'ın
    öklid-mesafe sezgiselini değil `Dijkstra.shortest_paths_from`'u
    (mesafe-agnostik BFS-benzeri) kullanacağından pozisyon doğruluğu bu
    analiz için gerekli değildir.
    """
    graph = NavGraph()
    default_pos = Point2D(0.0, 0.0)
    for sub_id in substation_ids:
        graph.add_node(sub_id, (positions or {}).get(sub_id, default_pos))
    for bld_id in building_ids:
        graph.add_node(bld_id, (positions or {}).get(bld_id, default_pos))
    for a, b in lines:
        if not graph.has_node(a) or not graph.has_node(b):
            continue
        graph.add_edge(a, b, cost=1.0)
    return graph


def _reachable_from(graph: NavGraph, sources: Iterable[str]) -> set[str]:
    """Basit çok-kaynaklı BFS - `NavGraph.neighbors()` üzerinde (yeni bir
    algoritma değil, `indoor_navigation.unreachable_rooms_without_elevator()`
    ile aynı BFS deseni, ayrı bir dosyada tekrar kullanılıyor)."""
    visited: set[str] = set()
    frontier = [s for s in sources if graph.has_node(s)]
    visited.update(frontier)
    while frontier:
        nxt = []
        for node in frontier:
            for neighbor, _cost in graph.neighbors(node):
                if neighbor not in visited:
                    visited.add(neighbor)
                    nxt.append(neighbor)
        frontier = nxt
    return visited


@dataclass(slots=True)
class OutageImpactReport:
    failed_substation_ids: list[str]
    affected_building_ids: list[str]
    still_powered_building_ids: list[str]
    hazard_rules: HazardScenarioRules
    walk_speed_multiplier: float
    disclaimer: str = (
        "Gösterge niteliğindedir - gerçek şebeke koruma/röle mantığını "
        "(N-1 güvenilirlik analizi) modellemez; yalnızca kalan trafolardan "
        "grafiksel erişilebilirlik farkına dayanır."
    )

    def to_dict(self) -> dict:
        return {
            "failed_substation_ids": list(self.failed_substation_ids),
            "affected_building_ids": list(self.affected_building_ids),
            "still_powered_building_ids": list(self.still_powered_building_ids),
            "disable_elevators": self.hazard_rules.disable_elevators,
            "walk_speed_multiplier": self.walk_speed_multiplier,
            "disclaimer": self.disclaimer,
        }


class OutagePropagationEngine:
    """Trafo arızası/kesinti senaryosunu grafiksel yayılım analiziyle
    binalara indirger + (opsiyonel) Event Bus'a `POWER_OUTAGE` ayrıntı
    olayı yayınlar (Cascade Engine'in ham `POWER_OUTAGE` olayının
    üzerine, hangi binaların etkilendiği bilgisini ekler)."""

    def __init__(self, graph: NavGraph, all_substation_ids: Iterable[str],
                 all_building_ids: Iterable[str], bus: Optional[EventSystem] = None):
        self.graph = graph
        self.all_substation_ids = list(all_substation_ids)
        self.all_building_ids = set(all_building_ids)
        self.bus = bus

    def propagate(self, failed_substation_ids: Iterable[str], *,
                   source: Optional[str] = None) -> OutageImpactReport:
        failed = list(failed_substation_ids)
        surviving = [s for s in self.all_substation_ids if s not in failed]

        reachable_before = _reachable_from(self.graph, self.all_substation_ids) & self.all_building_ids
        reachable_after = _reachable_from(self.graph, surviving) & self.all_building_ids

        affected = sorted(reachable_before - reachable_after)
        still_powered = sorted(reachable_after)

        report = OutageImpactReport(
            failed_substation_ids=list(failed),
            affected_building_ids=affected,
            still_powered_building_ids=still_powered,
            hazard_rules=HazardScenarioRules(disable_elevators=bool(affected)),
            walk_speed_multiplier=DARK_CORRIDOR_SPEED_MULTIPLIER if affected else 1.0,
        )

        if self.bus is not None:
            emit_city_event(
                self.bus, CityEventType.POWER_OUTAGE, source=source,
                failed_substation_ids=list(failed),
                affected_building_ids=affected,
            )
        return report

    def restore(self, restored_substation_ids: Iterable[str], *,
                source: Optional[str] = None) -> None:
        """Kesinti sonrası toparlanma - Faz VII `resilience_timeline`'ın
        `POWER_RESTORED` eşleşmesiyle tutarlı, yeni bir olay tipi icat
        edilmedi."""
        if self.bus is not None:
            emit_city_event(
                self.bus, CityEventType.POWER_RESTORED, source=source,
                restored_substation_ids=list(restored_substation_ids),
            )


__all__ = [
    "DARK_CORRIDOR_SPEED_MULTIPLIER",
    "build_power_network_graph",
    "OutageImpactReport",
    "OutagePropagationEngine",
]
