"""
Pathfinding
===========

Roadmap Phase 7 - "Path Finding": A*, Dijkstra, Jump Point Search, Theta*.

Ortak veri sözleşmesi `NavGraph`: node = `Point2D` (veya `Room.room_id`
üzerinden `indoor_navigation` tarafından sarmalanan bir kimlik), edge =
geçiş maliyeti (mesafe * agent tipi çarpanı). Bu modül tamamen generic'tir;
2D açık-alan graflarında da, `indoor_navigation`'ın kat-graf'ında da,
`traffic_simulation`'ın yol ağında da aynı arayüzle kullanılır.

Bağımlılık yok (numpy dahi kullanılmaz) - Phase 1 Geometry Engine ile aynı
prensip.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Callable, Hashable, Iterable

from ...core_engine.geometry_engine import Point2D


# ============================================================================ #
# NavGraph
# ============================================================================ #

NodeId = Hashable


@dataclass(slots=True)
class NavGraph:
    """Ağırlıklı, yönsüz (varsayılan) navigasyon grafiği.

    node = herhangi bir hashable kimlik (Point2D, int room_id, str vb.)
    edge = (cost, blocked) - blocked True ise düğüm geçici olarak kapalı
    (ör. tıkanmış koridor, kapalı asansör) sayılır; A*/Dijkstra bu kenarı
    atlar ama graf yapısından silmez (geri açılabilir).
    """

    positions: dict[NodeId, Point2D] = field(default_factory=dict)
    _adj: dict[NodeId, dict[NodeId, float]] = field(default_factory=dict)
    _blocked_edges: set[tuple[NodeId, NodeId]] = field(default_factory=set)

    # -- yapı ---------------------------------------------------------- #

    def add_node(self, node: NodeId, position: Point2D) -> None:
        self.positions[node] = position
        self._adj.setdefault(node, {})

    def has_node(self, node: NodeId) -> bool:
        return node in self._adj

    def add_edge(self, a: NodeId, b: NodeId, cost: float | None = None,
                 bidirectional: bool = True) -> None:
        if a not in self._adj or b not in self._adj:
            raise KeyError("add_edge: her iki düğüm de önce add_node ile eklenmeli")
        if cost is None:
            cost = self.positions[a].distance_to(self.positions[b])
        self._adj[a][b] = cost
        if bidirectional:
            self._adj[b][a] = cost

    def set_blocked(self, a: NodeId, b: NodeId, blocked: bool = True) -> None:
        key = (a, b)
        if blocked:
            self._blocked_edges.add(key)
        else:
            self._blocked_edges.discard(key)

    def update_edge_cost(self, a: NodeId, b: NodeId, cost: float,
                          bidirectional: bool = True) -> None:
        """Roadmap V9 / Katman 2.4 madde 7 (Faz IV, davranış kuralları) +
        Katman 7.2 (Faz V, yangın dinamik ağırlıklandırma) — kenar
        **silinmeden** maliyetinin mutable olarak güncellenmesi. Aynı
        teknik iki farklı katmanda (kalabalık-kaçınma cezası, duman/ateş
        cezası) yeniden kullanılır (roadmap'in kendi notu). Düğümler zaten
        var olmalı, yoksa `KeyError` (yapısal bütünlük - `add_edge` ile
        aynı disiplin)."""
        if a not in self._adj or b not in self._adj:
            raise KeyError("update_edge_cost: her iki düğüm de önce eklenmeli")
        self._adj[a][b] = cost
        if bidirectional:
            self._adj[b][a] = cost

    def edges(self) -> Iterable[tuple[NodeId, NodeId, float]]:
        """Tüm yönlü kenarları `(a, b, cost)` olarak döner - davranış
        kuralları / hazard katmanlarının orijinal (baseline) maliyetleri
        okuyup ceza uygulaması için."""
        for a, neighbors in self._adj.items():
            for b, cost in neighbors.items():
                yield a, b, cost

    def neighbors(self, node: NodeId) -> Iterable[tuple[NodeId, float]]:
        for nb, cost in self._adj.get(node, {}).items():
            if (node, nb) in self._blocked_edges:
                continue
            yield nb, cost

    def edge_cost(self, a: NodeId, b: NodeId) -> float | None:
        if (a, b) in self._blocked_edges:
            return None
        return self._adj.get(a, {}).get(b)

    def node_count(self) -> int:
        return len(self._adj)

    def edge_count(self) -> int:
        return sum(len(v) for v in self._adj.values())

    # -- yardımcı: ızgara üretimi (test/DEM entegrasyonu için) --------- #

    @staticmethod
    def from_grid(width: int, height: int, cell_size: float = 1.0,
                   blocked_cells: set[tuple[int, int]] | None = None,
                   diagonal: bool = True) -> "NavGraph":
        """Dikdörtgen ızgaradan `NavGraph` üretir. `blocked_cells` içindeki
        hücreler düğüm olarak eklenmez (engel/duvar)."""
        blocked_cells = blocked_cells or set()
        graph = NavGraph()
        for gy in range(height):
            for gx in range(width):
                if (gx, gy) in blocked_cells:
                    continue
                graph.add_node((gx, gy), Point2D(gx * cell_size, gy * cell_size))

        offsets_4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        offsets_diag = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
        offsets = offsets_4 + (offsets_diag if diagonal else [])

        for gy in range(height):
            for gx in range(width):
                if (gx, gy) not in graph._adj:
                    continue
                for dx, dy in offsets:
                    nx, ny = gx + dx, gy + dy
                    if (nx, ny) in graph._adj:
                        graph.add_edge((gx, gy), (nx, ny), bidirectional=False)
        return graph


@dataclass(slots=True)
class PathResult:
    path: list[NodeId]
    cost: float
    expanded_nodes: int
    found: bool


def _euclidean_heuristic(graph: NavGraph) -> Callable[[NodeId, NodeId], float]:
    def h(a: NodeId, b: NodeId) -> float:
        pa, pb = graph.positions.get(a), graph.positions.get(b)
        if pa is None or pb is None:
            return 0.0
        return pa.distance_to(pb)
    return h


def _reconstruct(came_from: dict[NodeId, NodeId], current: NodeId) -> list[NodeId]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


# ============================================================================ #
# A*
# ============================================================================ #

class AStar:
    """Klasik A* - `heuristic(a, b)` verilmezse Öklid mesafesi kullanılır."""

    @staticmethod
    def find_path(graph: NavGraph, start: NodeId, goal: NodeId,
                   heuristic: Callable[[NodeId, NodeId], float] | None = None) -> PathResult:
        if not graph.has_node(start) or not graph.has_node(goal):
            return PathResult([], math.inf, 0, False)
        if start == goal:
            return PathResult([start], 0.0, 0, True)

        h = heuristic or _euclidean_heuristic(graph)
        open_heap: list[tuple[float, int, NodeId]] = [(h(start, goal), 0, start)]
        came_from: dict[NodeId, NodeId] = {}
        g_score: dict[NodeId, float] = {start: 0.0}
        visited: set[NodeId] = set()
        counter = 1
        expanded = 0

        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current in visited:
                continue
            visited.add(current)
            expanded += 1

            if current == goal:
                path = _reconstruct(came_from, current)
                return PathResult(path, g_score[current], expanded, True)

            for nb, cost in graph.neighbors(current):
                tentative = g_score[current] + cost
                if tentative < g_score.get(nb, math.inf):
                    came_from[nb] = current
                    g_score[nb] = tentative
                    priority = tentative + h(nb, goal)
                    heapq.heappush(open_heap, (priority, counter, nb))
                    counter += 1

        return PathResult([], math.inf, expanded, False)


# ============================================================================ #
# Dijkstra
# ============================================================================ #

class Dijkstra:
    """A*'ın heuristic=0 özel durumu; ayrı, sade bir implementasyon olarak
    tutulur (roadmap'te ayrı algoritma olarak listelendiği için)."""

    @staticmethod
    def find_path(graph: NavGraph, start: NodeId, goal: NodeId) -> PathResult:
        return AStar.find_path(graph, start, goal, heuristic=lambda a, b: 0.0)

    @staticmethod
    def shortest_paths_from(graph: NavGraph, start: NodeId) -> dict[NodeId, float]:
        """Tek kaynaktan tüm düğümlere en kısa mesafe (ör. evacuation
        analizinde 'en yakın çıkış' hesaplarken kullanılır)."""
        if not graph.has_node(start):
            return {}
        dist: dict[NodeId, float] = {start: 0.0}
        heap: list[tuple[float, NodeId]] = [(0.0, start)]
        visited: set[NodeId] = set()
        while heap:
            d, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            for nb, cost in graph.neighbors(node):
                nd = d + cost
                if nd < dist.get(nb, math.inf):
                    dist[nb] = nd
                    heapq.heappush(heap, (nd, nb))
        return dist


# ============================================================================ #
# Jump Point Search (ızgara grafiklerine özel A* optimizasyonu)
# ============================================================================ #

class JumpPointSearch:
    """JPS, düzgün-maliyetli ızgara graflarında A*'a denk sonuç üretip çok
    daha az düğüm genişletir (simetrik yolları budayarak). Bu implementasyon
    `NavGraph.from_grid` ile üretilmiş graflar üzerinde çalışır; grid
    olmayan genel graflarda normal A*'a düşer (fallback)."""

    @staticmethod
    def find_path(graph: NavGraph, start: NodeId, goal: NodeId) -> PathResult:
        grid_coords = JumpPointSearch._extract_grid(graph)
        if grid_coords is None:
            return AStar.find_path(graph, start, goal)

        blocked = grid_coords["blocked"]
        cell_size = grid_coords["cell_size"]
        origin = grid_coords["origin"]

        def is_walkable(x: int, y: int) -> bool:
            return (x, y) not in blocked and graph.has_node(
                JumpPointSearch._node_id_at(graph, origin, cell_size, x, y))

        start_xy = JumpPointSearch._xy_of(graph, start, origin, cell_size)
        goal_xy = JumpPointSearch._xy_of(graph, goal, origin, cell_size)
        if start_xy is None or goal_xy is None:
            return AStar.find_path(graph, start, goal)

        h = _euclidean_heuristic(graph)
        open_heap: list[tuple[float, int, tuple[int, int]]] = [(h(start, goal), 0, start_xy)]
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        g_score: dict[tuple[int, int], float] = {start_xy: 0.0}
        visited: set[tuple[int, int]] = set()
        counter = 1
        expanded = 0

        dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]

        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current in visited:
                continue
            visited.add(current)
            expanded += 1

            if current == goal_xy:
                xy_path = _reconstruct(came_from, current)
                node_path = [JumpPointSearch._node_id_at(graph, origin, cell_size, x, y)
                             for x, y in xy_path]
                cost = g_score[current]
                return PathResult(node_path, cost, expanded, True)

            for dx, dy in dirs:
                jump = JumpPointSearch._jump(current[0], current[1], dx, dy, goal_xy, is_walkable)
                if jump is None:
                    continue
                step_cost = math.hypot(jump[0] - current[0], jump[1] - current[1])
                tentative = g_score[current] + step_cost
                if tentative < g_score.get(jump, math.inf):
                    came_from[jump] = current
                    g_score[jump] = tentative
                    node_j = JumpPointSearch._node_id_at(graph, origin, cell_size, *jump)
                    node_g = JumpPointSearch._node_id_at(graph, origin, cell_size, *goal_xy)
                    priority = tentative + h(node_j, node_g)
                    heapq.heappush(open_heap, (priority, counter, jump))
                    counter += 1

        return PathResult([], math.inf, expanded, False)

    # -- iç yardımcılar -------------------------------------------------- #

    @staticmethod
    def _jump(x: int, y: int, dx: int, dy: int, goal_xy: tuple[int, int],
               is_walkable: Callable[[int, int], bool]) -> tuple[int, int] | None:
        nx, ny = x + dx, y + dy
        if not is_walkable(nx, ny):
            return None
        if (nx, ny) == goal_xy:
            return (nx, ny)

        # yatay/dikey hareket
        if dx != 0 and dy == 0:
            if (is_walkable(nx, ny + 1) and not is_walkable(x, ny + 1)) or \
               (is_walkable(nx, ny - 1) and not is_walkable(x, ny - 1)):
                return (nx, ny)
        elif dy != 0 and dx == 0:
            if (is_walkable(nx + 1, ny) and not is_walkable(nx + 1, y)) or \
               (is_walkable(nx - 1, ny) and not is_walkable(nx - 1, y)):
                return (nx, ny)
        else:
            # diyagonal hareket - önce yatay/dikey forced-neighbor kontrolü
            if (is_walkable(nx - dx, ny + dy) and not is_walkable(x - dx, y)) or \
               (is_walkable(nx + dx, ny - dy) and not is_walkable(x, y - dy)):
                return (nx, ny)
            if JumpPointSearch._jump(nx, ny, dx, 0, goal_xy, is_walkable) is not None or \
               JumpPointSearch._jump(nx, ny, 0, dy, goal_xy, is_walkable) is not None:
                return (nx, ny)

        return JumpPointSearch._jump(nx, ny, dx, dy, goal_xy, is_walkable)

    @staticmethod
    def _extract_grid(graph: NavGraph) -> dict | None:
        """`NavGraph.from_grid` ile üretilmiş graflar için (gx, gy) tuple
        node id'lerini tanır; aksi halde None döner (fallback tetikler)."""
        if not graph.positions:
            return None
        sample = next(iter(graph.positions))
        if not (isinstance(sample, tuple) and len(sample) == 2
                and all(isinstance(v, int) for v in sample)):
            return None
        xs = sorted({p.x for p in graph.positions.values()})
        cell_size = (xs[1] - xs[0]) if len(xs) > 1 else 1.0
        origin = Point2D(0.0, 0.0)
        all_gx = {n[0] for n in graph.positions}
        all_gy = {n[1] for n in graph.positions}
        full_w, full_h = max(all_gx) + 1, max(all_gy) + 1
        blocked = {(gx, gy) for gy in range(full_h) for gx in range(full_w)
                   if (gx, gy) not in graph.positions}
        return {"blocked": blocked, "cell_size": cell_size, "origin": origin}

    @staticmethod
    def _xy_of(graph: NavGraph, node: NodeId, origin: Point2D, cell_size: float) -> tuple[int, int] | None:
        if isinstance(node, tuple) and len(node) == 2 and all(isinstance(v, int) for v in node):
            return node
        return None

    @staticmethod
    def _node_id_at(graph: NavGraph, origin: Point2D, cell_size: float, x: int, y: int) -> NodeId:
        return (x, y)


# ============================================================================ #
# Theta* (any-angle pathfinding - line-of-sight tabanlı kısaltma)
# ============================================================================ #

class ThetaStar:
    """Theta*, A*'ın 'line-of-sight' genişletmesidir: bir düğümün ebeveynini
    doğrudan görüyorsa (ara engel yoksa) komşu üzerinden değil doğrudan
    ebeveynden maliyet hesaplar - sonuçta ızgaraya bağlı olmayan, daha kısa
    ve doğal (any-angle) yollar üretir.

    `line_of_sight(graph, a, b)` verilmezse varsayılan: iki düğüm arasında
    doğrudan bir kenar varsa görüş var sayılır (genel graf için güvenli
    fallback); grid tabanlı kullanım için Bresenham tabanlı görüş kontrolü
    enjekte edilebilir.
    """

    @staticmethod
    def find_path(graph: NavGraph, start: NodeId, goal: NodeId,
                   line_of_sight: Callable[[NavGraph, NodeId, NodeId], bool] | None = None) -> PathResult:
        if not graph.has_node(start) or not graph.has_node(goal):
            return PathResult([], math.inf, 0, False)
        if start == goal:
            return PathResult([start], 0.0, 0, True)

        los = line_of_sight or ThetaStar._default_los
        h = _euclidean_heuristic(graph)

        open_heap: list[tuple[float, int, NodeId]] = [(h(start, goal), 0, start)]
        came_from: dict[NodeId, NodeId] = {start: start}
        g_score: dict[NodeId, float] = {start: 0.0}
        visited: set[NodeId] = set()
        counter = 1
        expanded = 0

        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current in visited:
                continue
            visited.add(current)
            expanded += 1

            if current == goal:
                path = _reconstruct({k: v for k, v in came_from.items() if k != start}, current)
                return PathResult(path, g_score[current], expanded, True)

            parent = came_from[current]
            for nb, cost in graph.neighbors(current):
                if los(graph, parent, nb):
                    tentative = g_score[parent] + graph.positions[parent].distance_to(graph.positions[nb])
                    candidate_parent = parent
                else:
                    tentative = g_score[current] + cost
                    candidate_parent = current

                if tentative < g_score.get(nb, math.inf):
                    g_score[nb] = tentative
                    came_from[nb] = candidate_parent
                    priority = tentative + h(nb, goal)
                    heapq.heappush(open_heap, (priority, counter, nb))
                    counter += 1

        return PathResult([], math.inf, expanded, False)

    @staticmethod
    def _default_los(graph: NavGraph, a: NodeId, b: NodeId) -> bool:
        return graph.edge_cost(a, b) is not None
