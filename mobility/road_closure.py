"""
mobility.road_closure - Kaza / Yol Kapanması Olay Entegrasyonu (Katman 3.3 madde 3)
=======================================================================================

Roadmap V9 / Faz VI / Katman 3.3 madde 3:

"Kaza / yol kapanması olayları: Event Bus'tan ROAD_CLOSED geldiğinde
NavGraph kenarının ağırlığının sonsuz olması - Katman 7'deki yangın
'dinamik graf ağırlıklandırma' tekniği burada trafiğe de uygulanır (aynı
teknik, iki farklı katmanda yeniden kullanılır)."

Bu modül `mobility.pathfinding.NavGraph.set_blocked()`'i (Faz IV'te
eklendi, kenar **silinmez** - roadmap ilkesi #2) ve `extensibility.
city_events.CityEventType.ROAD_CLOSED`/`ROAD_REOPENED`'i (O.4) doğrudan
birbirine bağlar - yeni bir olay tipi ya da graf mekaniği icat edilmez,
yalnızca ikisi arasındaki köprü kurulur.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..extensibility.city_events import CityEventType
from ..extensibility.event_system import Event, EventSystem
from .pathfinding import NavGraph, NodeId


@dataclass(slots=True)
class RoadClosureSubscriber:
    """`EventSystem`'e abone olup `CityEventType.ROAD_CLOSED`/
    `ROAD_REOPENED` olaylarını dinler, ilgili `NavGraph` kenarını bloke
    eder/açar. Olay payload'ı `{"node_a": ..., "node_b": ..., "route_key":
    ...}` şeklinde beklenir (`route_key` yalnızca birden çok graf
    yönetiliyorsa filtre olarak kullanılır - tek graf senaryosunda
    `route_key=None` her olayı bu graf'a uygular).

    **Dürüstlük notu:** Bu köprü yalnızca **kayıtlı** (`register_graph`
    ile eklenen) graf(lar)ı günceller; olay payload'ındaki düğüm kimlikleri
    graf'ta yoksa sessizce yutulmaz, `unresolved_events` listesine
    (gözlemlenebilirlik için) eklenir.
    """

    graphs: dict[str | None, NavGraph] = field(default_factory=dict)
    unresolved_events: list[Event] = field(default_factory=list)
    closed_edges: set[tuple[str | None, NodeId, NodeId]] = field(default_factory=set)

    def register_graph(self, graph: NavGraph, *, route_key: str | None = None) -> None:
        self.graphs[route_key] = graph

    def subscribe(self, bus: EventSystem) -> None:
        bus.subscribe(str(CityEventType.ROAD_CLOSED.value), self._on_closed)
        bus.subscribe(str(CityEventType.ROAD_REOPENED.value), self._on_reopened)

    def _resolve_graph(self, payload: dict) -> NavGraph | None:
        route_key = payload.get("route_key")
        if route_key in self.graphs:
            return self.graphs[route_key]
        # route_key belirtilmemişse (veya bilinmiyorsa) tekil-graf modunu dene.
        if None in self.graphs:
            return self.graphs[None]
        return None

    def _on_closed(self, event: Event) -> None:
        self._apply(event, blocked=True)

    def _on_reopened(self, event: Event) -> None:
        self._apply(event, blocked=False)

    def _apply(self, event: Event, *, blocked: bool) -> None:
        payload = event.payload or {}
        node_a = payload.get("node_a")
        node_b = payload.get("node_b")
        graph = self._resolve_graph(payload)
        if graph is None or node_a is None or node_b is None:
            self.unresolved_events.append(event)
            return
        if not graph.has_node(node_a) or not graph.has_node(node_b):
            self.unresolved_events.append(event)
            return
        graph.set_blocked(node_a, node_b, blocked=blocked)
        graph.set_blocked(node_b, node_a, blocked=blocked)
        key = (payload.get("route_key"), node_a, node_b)
        if blocked:
            self.closed_edges.add(key)
        else:
            self.closed_edges.discard(key)


def emit_road_closed(
    bus: EventSystem,
    node_a: NodeId,
    node_b: NodeId,
    *,
    route_key: str | None = None,
    reason: str = "unspecified",
) -> Event:
    """`CityEventType.ROAD_CLOSED` olayını standart payload şemasıyla
    yayınlayan ince yardımcı (roadmap 7.1'in `HazardEvent`/Katman 7.2'nin
    "moloz -> NavGraph kenar kapanması" notuyla aynı payload sözleşmesini
    paylaşır)."""
    return bus.emit(
        str(CityEventType.ROAD_CLOSED.value),
        payload={"node_a": node_a, "node_b": node_b, "route_key": route_key, "reason": reason},
    )


def emit_road_reopened(
    bus: EventSystem,
    node_a: NodeId,
    node_b: NodeId,
    *,
    route_key: str | None = None,
) -> Event:
    return bus.emit(
        str(CityEventType.ROAD_REOPENED.value),
        payload={"node_a": node_a, "node_b": node_b, "route_key": route_key},
    )
