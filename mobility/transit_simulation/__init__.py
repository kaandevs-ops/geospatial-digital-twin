"""
Transit Simulation (Roadmap V4 - Track E / Faz E13)
=====================================================

"Mobility: Çok-Modlu Ulaşım ve Toplu Taşıma Simülasyonu".

D6 (`traffic_simulation`) araç trafiğini, `crowd_simulation` yaya
tahliyesini kapsıyordu; bu paket iki eksik parçayı ekliyor:

1. `TransitLine` / `TransitVehicle` - sabit-zamanlı sefer tarifesiyle
   çalışan otobüs/tramvay hattı simülasyonu (durak sırası + kapasite +
   yolcu indirme/bindirme).
2. `MultiModalRoute` - `pathfinding.NavGraph` (yürüme grafiği) ile bir
   transit grafiğini (duraklar + hatlar) birleştiren, transfer maliyeti
   dahil A*-tabanlı rota planlayıcı: yürüme -> durakta bekleme -> transit
   yolculuğu -> yürüme zinciri.

Bağımlılık yok (stdlib-only), Phase 7 `NavGraph`/`AStar` ile aynı prensip.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

from ..pathfinding import AStar, NavGraph, NodeId, PathResult
from ...core_engine.geometry_engine import Point2D

StopId = Hashable


# ============================================================================ #
# Transit hattı veri modeli
# ============================================================================ #

@dataclass
class TransitStop:
    """Bir toplu taşıma durağı - konumu, hangi yürüme-grafiği düğümüne
    (varsa) bağlı olduğu ile birlikte."""

    stop_id: StopId
    position: Point2D
    walk_node: Optional[NodeId] = None  # NavGraph'taki karşılık gelen düğüm


@dataclass
class TransitLine:
    """Sabit güzergahlı, sabit-zamanlı sefer tarifeli bir hat (otobüs/
    tramvay). `stops` sırası güzergahı belirler; `schedule_headway_s`
    sefer sıklığı, `travel_times_s[i]` `stops[i] -> stops[i+1]` seyahat
    süresidir (uzunluk == len(stops) - 1)."""

    line_id: str
    stops: List[StopId]
    travel_times_s: List[float]
    schedule_headway_s: float
    first_departure_s: float = 0.0
    capacity: int = 60

    def __post_init__(self) -> None:
        if len(self.stops) < 2:
            raise ValueError("TransitLine en az 2 durak içermeli")
        if len(self.travel_times_s) != len(self.stops) - 1:
            raise ValueError(
                "travel_times_s uzunluğu len(stops)-1 olmalı "
                f"(stops={len(self.stops)}, travel_times={len(self.travel_times_s)})"
            )
        if self.schedule_headway_s <= 0:
            raise ValueError("schedule_headway_s pozitif olmalı")

    def stop_index(self, stop_id: StopId) -> int:
        return self.stops.index(stop_id)

    def cumulative_time_s(self, from_index: int, to_index: int) -> float:
        """`from_index` durağından `to_index` durağına (aynı yönde,
        from < to varsayılır) kümülatif seyahat süresi."""
        if from_index == to_index:
            return 0.0
        if from_index > to_index:
            raise ValueError("from_index <= to_index olmalı (tek yönlü hat)")
        return sum(self.travel_times_s[from_index:to_index])

    def next_departure(self, stop_index: int, after_time_s: float) -> float:
        """`stop_index` durağından, `after_time_s`'den sonraki (dahil) ilk
        kalkış zamanını döndürür (durağa göre kaydırılmış tarife)."""
        offset = self.cumulative_time_s(0, stop_index) if stop_index > 0 else 0.0
        base = self.first_departure_s + offset
        if after_time_s <= base:
            return base
        n = math.ceil((after_time_s - base) / self.schedule_headway_s)
        return base + n * self.schedule_headway_s


@dataclass
class TransitVehicle:
    """Bir `TransitLine` üzerinde tek bir sefer yapan araç - kapasite ve
    güncel yolcu sayısını takip eder (D6 `TrafficAgent` ile aynı ruhta,
    ama sabit güzergah/tarifeye bağlı)."""

    line: TransitLine
    departure_time_s: float
    onboard: int = 0

    def can_board(self, n: int = 1) -> bool:
        return self.onboard + n <= self.line.capacity

    def board(self, n: int = 1) -> int:
        """Kapasiteyle sınırlı biniş - gerçekte binen yolcu sayısını
        döndürür (kapasite dolmuşsa daha az olabilir)."""
        allowed = max(0, min(n, self.line.capacity - self.onboard))
        self.onboard += allowed
        return allowed

    def alight(self, n: int = 1) -> None:
        self.onboard = max(0, self.onboard - n)

    def arrival_time_s(self, stop_index_from_departure_stop: int) -> float:
        return self.departure_time_s + self.line.cumulative_time_s(
            0, stop_index_from_departure_stop
        )


# ============================================================================ #
# Çok-modlu rota planlama
# ============================================================================ #

@dataclass
class RouteLeg:
    """Bir rotanın tek bir bacağı: yürüme veya transit yolculuğu."""

    mode: str  # "walk" | "wait" | "transit"
    start: Hashable
    end: Hashable
    duration_s: float
    line_id: Optional[str] = None


@dataclass
class MultiModalRoute:
    """Yürüme grafiği + transit hatlarını birleştirip, transfer maliyeti
    dahil en-hızlı rotayı bulan planlayıcı.

    İç graf inşası: her `TransitStop` bir düğüm; art arda duraklar arası
    kenar ağırlığı = hattın saf seyahat süresi (`travel_times_s`); durak
    ile bağlı olduğu yürüme-graf düğümü arasında `transfer_penalty_s/2`
    maliyetli bir "transfer/bekleme" kenarı (walk_node biliniyorsa)
    eklenir - klasik transit-planlama modelindeki "ortalama bekleme"
    burada açıkça ayrı bir bacak (`RouteLeg(mode="wait")`) olarak görünür,
    transit bacağının süresi saf seyahat süresiyle bire bir eşleşir.
    Yürüme grafiğinin
    kendisi olduğu gibi (aynı kenar ağırlıklarıyla) dahil edilir - böylece
    tek bir `NavGraph` üzerinde tek bir A* çağrısıyla tüm zincir çözülür.
    """

    walk_graph: NavGraph
    lines: List[TransitLine] = field(default_factory=list)
    stops: Dict[StopId, TransitStop] = field(default_factory=dict)
    transfer_penalty_s: float = 30.0

    def add_stop(self, stop: TransitStop) -> None:
        self.stops[stop.stop_id] = stop

    def add_line(self, line: TransitLine) -> None:
        self.lines.append(line)

    def _combined_graph(self) -> Tuple[NavGraph, Dict[Tuple[Hashable, Hashable], str]]:
        """Yürüme grafiğini ve transit hat kenarlarını tek bir `NavGraph`
        üzerinde birleştirir. Dönen ikinci değer, hangi kenarın hangi
        `line_id`'ye ait olduğunu (yürüme kenarları için None) tutan bir
        harita - `describe_route`'un bacak modunu (walk/transit)
        ayırt edebilmesi için.
        """
        combined = NavGraph()
        edge_line: Dict[Tuple[Hashable, Hashable], str] = {}

        # 1) Yürüme grafiğini birebir kopyala.
        for node, pos in self.walk_graph.positions.items():
            combined.add_node(("walk", node), pos)
        for node in self.walk_graph.positions:
            for nb, cost in self.walk_graph.neighbors(node):
                a, b = ("walk", node), ("walk", nb)
                if b in combined._adj.get(a, {}):
                    continue
                combined.add_edge(a, b, cost=cost, bidirectional=False)

        # 2) Transit durakları + hat kenarları (bekleme dahil ortalama
        #    süre) + durak<->yürüme düğümü transfer kenarları.
        for stop in self.stops.values():
            combined.add_node(("stop", stop.stop_id), stop.position)
            if stop.walk_node is not None:
                wn = ("walk", stop.walk_node)
                sn = ("stop", stop.stop_id)
                if combined.has_node(wn):
                    combined.add_edge(wn, sn, cost=self.transfer_penalty_s / 2, bidirectional=True)

        for line in self.lines:
            for i in range(len(line.stops) - 1):
                a_id, b_id = line.stops[i], line.stops[i + 1]
                a, b = ("stop", a_id), ("stop", b_id)
                if not combined.has_node(a) or not combined.has_node(b):
                    continue
                travel = line.travel_times_s[i]
                combined.add_edge(a, b, cost=travel, bidirectional=False)
                edge_line[(a, b)] = line.line_id

        return combined, edge_line

    def plan(self, start_walk_node: NodeId, goal_walk_node: NodeId) -> "MultiModalPlanResult":
        combined, edge_line = self._combined_graph()
        start = ("walk", start_walk_node)
        goal = ("walk", goal_walk_node)
        if not combined.has_node(start) or not combined.has_node(goal):
            return MultiModalPlanResult([], 0.0, False)

        result: PathResult = AStar.find_path(combined, start, goal, heuristic=lambda a, b: 0.0)
        if not result.found:
            return MultiModalPlanResult([], 0.0, False)

        legs = self._legs_from_path(result.path, edge_line)
        total = sum(leg.duration_s for leg in legs)
        return MultiModalPlanResult(legs, total, True)

    def _legs_from_path(
        self,
        path: Sequence[Tuple[str, Hashable]],
        edge_line: Dict[Tuple[Hashable, Hashable], str],
    ) -> List[RouteLeg]:
        legs: List[RouteLeg] = []
        combined, _ = self._combined_graph()
        for a, b in zip(path, path[1:]):
            cost = dict(combined.neighbors(a)).get(b, 0.0)
            line_id = edge_line.get((a, b))
            if line_id is not None:
                mode = "transit"
            elif a[0] == "stop" or b[0] == "stop":
                mode = "wait"
            else:
                mode = "walk"
            legs.append(RouteLeg(mode=mode, start=a[1], end=b[1], duration_s=cost, line_id=line_id))
        return legs


@dataclass
class MultiModalPlanResult:
    legs: List[RouteLeg]
    total_duration_s: float
    found: bool


__all__ = [
    "StopId",
    "TransitStop",
    "TransitLine",
    "TransitVehicle",
    "RouteLeg",
    "MultiModalRoute",
    "MultiModalPlanResult",
]
