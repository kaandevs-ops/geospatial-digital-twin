"""
Geometry Engine
================

Roadmap Phase 1 - "Geometry Engine" (sıfırdan yazılacak).

Kapsam:
    Polygon : simplify, merge, split, clip, union, intersection,
              convex hull, concave hull
    Line    : smoothing, snapping, offset, intersection
    Point   : clustering, indexing, nearest search

Bağımlılık yok (numpy dahi kullanılmaz) - bu, roadmap'in "sıfırdan yazılacak"
talimatına uygun, taşınabilir bir çekirdektir. Performans kritik senaryolarda
(çok büyük veri setleri) `performance/` fazında numpy/vektörize varyantlar
eklenecektir (bkz. SPEC).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ======================================================================== #
# Temel tipler
# ======================================================================== #


@dataclass(frozen=True, slots=True)
class Point2D:
    x: float
    y: float

    def distance_to(self, other: Point2D) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def __add__(self, other: Point2D) -> Point2D:
        return Point2D(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Point2D) -> Point2D:
        return Point2D(self.x - other.x, self.y - other.y)


@dataclass(slots=True)
class LineString:
    points: list[Point2D]

    def length(self) -> float:
        return sum(a.distance_to(b) for a, b in zip(self.points, self.points[1:]))


@dataclass(slots=True)
class Polygon:
    """Basit (delikli olmayan) çokgen; noktalar CCW veya CW olabilir."""

    points: list[Point2D]

    def is_closed(self) -> bool:
        return len(self.points) > 2 and self.points[0] == self.points[-1]

    def closed_ring(self) -> list[Point2D]:
        pts = self.points
        if pts and pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        return pts

    def area(self) -> float:
        """Shoelace formülü - imzalı alan (CCW pozitif)."""
        ring = self.closed_ring()
        s = 0.0
        for p1, p2 in zip(ring, ring[1:]):
            s += p1.x * p2.y - p2.x * p1.y
        return s / 2.0

    def unsigned_area(self) -> float:
        return abs(self.area())

    def perimeter(self) -> float:
        ring = self.closed_ring()
        return sum(a.distance_to(b) for a, b in zip(ring, ring[1:]))

    def centroid(self) -> Point2D:
        ring = self.closed_ring()
        a = self.area()
        if abs(a) < 1e-12:
            # dejenere durum: aritmetik ortalama
            n = len(self.points)
            return Point2D(sum(p.x for p in self.points) / n, sum(p.y for p in self.points) / n)
        cx = cy = 0.0
        for p1, p2 in zip(ring, ring[1:]):
            cross = p1.x * p2.y - p2.x * p1.y
            cx += (p1.x + p2.x) * cross
            cy += (p1.y + p2.y) * cross
        factor = 1.0 / (6.0 * a)
        return Point2D(cx * factor, cy * factor)

    def is_ccw(self) -> bool:
        return self.area() > 0

    def bounding_box(self) -> tuple[float, float, float, float]:
        xs = [p.x for p in self.points]
        ys = [p.y for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def contains_point(self, point: Point2D) -> bool:
        """Ray-casting (even-odd kuralı) ile nokta-poligon içindelik testi.

        ROADMAP_V7.md Faz C3 için eklendi: OSM'den gelen alan feature'ları
        (orman, park, su...) içine bitki örtüsü/nesne dağıtırken (B2
        "yoğunluk bazlı dağılım") bir noktanın poligon sınırları içinde
        olup olmadığını belirlemek gerekiyor - `vegetation.scatter`
        bunu kullanır. Delikli (multipolygon "inner ring") poligonlar
        desteklenmiyor (mevcut `GeoFeature.to_polygon()` tek-halka
        modeliyle tutarlı - roadmap kapsamı dışı, bkz. `osm_client.py`
        modül docstring'i)."""
        ring = self.closed_ring()
        inside = False
        x, y = point.x, point.y
        for p1, p2 in zip(ring, ring[1:]):
            x1, y1 = p1.x, p1.y
            x2, y2 = p2.x, p2.y
            if (y1 > y) != (y2 > y):
                x_intersect = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < x_intersect:
                    inside = not inside
        return inside


# ======================================================================== #
# Geometry Engine
# ======================================================================== #


class GeometryEngine:
    """Polygon / Line / Point işlemleri için stateless static metotlar."""

    # ------------------------------------------------------------------ #
    # POLYGON
    # ------------------------------------------------------------------ #
    @staticmethod
    def simplify_polygon(poly: Polygon, tolerance: float) -> Polygon:
        """Ramer-Douglas-Peucker algoritması (açık polyline üzerinde çalışır,
        halka kapatılıp tekrar açılır)."""
        pts = poly.closed_ring()
        simplified = GeometryEngine._rdp(pts, tolerance)
        if simplified[0] == simplified[-1] and len(simplified) > 1:
            simplified = simplified[:-1]
        return Polygon(simplified)

    @staticmethod
    def _rdp(points: list[Point2D], epsilon: float) -> list[Point2D]:
        if len(points) < 3:
            return points[:]
        start, end = points[0], points[-1]
        max_dist, index = -1.0, -1
        for i in range(1, len(points) - 1):
            d = GeometryEngine._point_segment_distance(points[i], start, end)
            if d > max_dist:
                max_dist, index = d, i
        if max_dist > epsilon:
            left = GeometryEngine._rdp(points[: index + 1], epsilon)
            right = GeometryEngine._rdp(points[index:], epsilon)
            return left[:-1] + right
        return [start, end]

    @staticmethod
    def _point_segment_distance(p: Point2D, a: Point2D, b: Point2D) -> float:
        if a == b:
            return p.distance_to(a)
        t = ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / (
            (b.x - a.x) ** 2 + (b.y - a.y) ** 2
        )
        t = max(0.0, min(1.0, t))
        proj = Point2D(a.x + t * (b.x - a.x), a.y + t * (b.y - a.y))
        return p.distance_to(proj)

    @staticmethod
    def convex_hull(points: list[Point2D]) -> Polygon:
        """Andrew's monotone chain, O(n log n)."""
        pts = sorted(set((p.x, p.y) for p in points))
        if len(pts) <= 2:
            return Polygon([Point2D(x, y) for x, y in pts])

        def cross(o, a, b):
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        lower: list[tuple[float, float]] = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)

        upper: list[tuple[float, float]] = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)

        hull = lower[:-1] + upper[:-1]
        return Polygon([Point2D(x, y) for x, y in hull])

    @staticmethod
    def concave_hull(points: list[Point2D], k: int = 3) -> Polygon:
        """
        k-nearest-neighbours tabanlı basit concave hull (Moreira-Santos benzeri
        yaklaşım). Küçük/orta veri setleri için yeterlidir; büyük veri setleri
        için Phase 13'te (Performance Engine) optimize edilecektir.
        """
        if len(points) < 3:
            return Polygon(points[:])
        k = max(3, min(k, len(points) - 1))
        remaining = points[:]
        # Başlangıç: en alt-sağ nokta
        start = min(remaining, key=lambda p: (p.y, p.x))
        hull = [start]
        current = start
        prev_angle = 0.0
        remaining.remove(start)
        first_point = start

        while True:
            candidates = sorted(remaining, key=lambda p: current.distance_to(p))[:k]
            candidates.sort(
                key=lambda p: (
                    -GeometryEngine._angle_diff(
                        prev_angle, math.atan2(p.y - current.y, p.x - current.x)
                    )
                )
            )
            next_point = None
            for cand in candidates:
                # basit self-intersection kontrolü atlanabilir (yaklaşık algoritma)
                next_point = cand
                break
            if next_point is None:
                break
            prev_angle = math.atan2(next_point.y - current.y, next_point.x - current.x)
            hull.append(next_point)
            if next_point in remaining:
                remaining.remove(next_point)
            current = next_point
            if current == first_point or not remaining:
                break
        return Polygon(hull)

    @staticmethod
    def _angle_diff(a: float, b: float) -> float:
        d = b - a
        while d < 0:
            d += 2 * math.pi
        while d > 2 * math.pi:
            d -= 2 * math.pi
        return d

    @staticmethod
    def clip_polygon(subject: Polygon, clip_window: Polygon) -> Polygon:
        """Sutherland-Hodgman algoritması (clip_window dışbükey olmalı)."""
        output = subject.closed_ring()[:-1]
        clip_ring = clip_window.closed_ring()

        for i in range(len(clip_ring) - 1):
            if not output:
                break
            cp1, cp2 = clip_ring[i], clip_ring[i + 1]
            input_list, output = output, []
            if not input_list:
                continue
            s = input_list[-1]
            for e in input_list:
                e_inside = GeometryEngine._is_inside(e, cp1, cp2)
                s_inside = GeometryEngine._is_inside(s, cp1, cp2)
                if e_inside:
                    if not s_inside:
                        output.append(GeometryEngine._line_intersection(s, e, cp1, cp2))
                    output.append(e)
                elif s_inside:
                    output.append(GeometryEngine._line_intersection(s, e, cp1, cp2))
                s = e
        return Polygon(output)

    @staticmethod
    def _is_inside(p: Point2D, a: Point2D, b: Point2D) -> bool:
        return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x) >= 0

    @staticmethod
    def _line_intersection(p1: Point2D, p2: Point2D, p3: Point2D, p4: Point2D) -> Point2D:
        x1, y1, x2, y2 = p1.x, p1.y, p2.x, p2.y
        x3, y3, x4, y4 = p3.x, p3.y, p4.x, p4.y
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-12:
            return p2
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        return Point2D(x1 + t * (x2 - x1), y1 + t * (y2 - y1))

    @staticmethod
    def union(polygons: list[Polygon]) -> list[Polygon]:
        """
        Basitleştirilmiş union: örtüşen bbox'ları gruplar, dışbükey birleşimini
        alır. Tam robust boolean union (Weiler-Atherton / Vatti) Phase 13'te
        performans motoru ile birlikte genişletilecektir (bkz. SPEC).
        """
        if not polygons:
            return []
        groups: list[list[Polygon]] = []
        for poly in polygons:
            placed = False
            for group in groups:
                if any(GeometryEngine._bbox_overlap(poly, g) for g in group):
                    group.append(poly)
                    placed = True
                    break
            if not placed:
                groups.append([poly])
        result = []
        for group in groups:
            all_points = [p for poly in group for p in poly.points]
            result.append(GeometryEngine.convex_hull(all_points))
        return result

    @staticmethod
    def _bbox_overlap(a: Polygon, b: Polygon) -> bool:
        ax0, ay0, ax1, ay1 = a.bounding_box()
        bx0, by0, bx1, by1 = b.bounding_box()
        return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)

    @staticmethod
    def intersects(a: Polygon, b: Polygon) -> bool:
        if not GeometryEngine._bbox_overlap(a, b):
            return False
        # Herhangi bir köşe diğerinin içinde mi (yaklaşık ama pratik test)
        return any(GeometryEngine.point_in_polygon(p, b) for p in a.points) or any(
            GeometryEngine.point_in_polygon(p, a) for p in b.points
        )

    @staticmethod
    def point_in_polygon(point: Point2D, poly: Polygon) -> bool:
        """Ray casting algoritması."""
        ring = poly.closed_ring()
        inside = False
        for p1, p2 in zip(ring, ring[1:]):
            if ((p1.y > point.y) != (p2.y > point.y)) and (
                point.x < (p2.x - p1.x) * (point.y - p1.y) / (p2.y - p1.y + 1e-15) + p1.x
            ):
                inside = not inside
        return inside

    # ------------------------------------------------------------------ #
    # LINE
    # ------------------------------------------------------------------ #
    @staticmethod
    def smooth_line(line: LineString, iterations: int = 1) -> LineString:
        """Chaikin's corner-cutting algoritması."""
        pts = line.points
        for _ in range(iterations):
            if len(pts) < 3:
                break
            new_pts = [pts[0]]
            for p1, p2 in zip(pts, pts[1:]):
                q = Point2D(0.75 * p1.x + 0.25 * p2.x, 0.75 * p1.y + 0.25 * p2.y)
                r = Point2D(0.25 * p1.x + 0.75 * p2.x, 0.25 * p1.y + 0.75 * p2.y)
                new_pts.extend([q, r])
            new_pts.append(pts[-1])
            pts = new_pts
        return LineString(pts)

    @staticmethod
    def snap_point(point: Point2D, candidates: list[Point2D], tolerance: float) -> Point2D:
        """Verilen noktayı tolerans içindeki en yakın adaya snap'ler."""
        best, best_dist = point, tolerance
        for c in candidates:
            d = point.distance_to(c)
            if d <= best_dist:
                best, best_dist = c, d
        return best

    @staticmethod
    def offset_line(line: LineString, distance: float) -> LineString:
        """Basit normal-offset (miter join olmadan, segment bazlı)."""
        pts = line.points
        if len(pts) < 2:
            return LineString(pts[:])
        offset_points = []
        for p1, p2 in zip(pts, pts[1:]):
            dx, dy = p2.x - p1.x, p2.y - p1.y
            length = math.hypot(dx, dy) or 1e-9
            nx, ny = -dy / length * distance, dx / length * distance
            offset_points.append(Point2D(p1.x + nx, p1.y + ny))
        offset_points.append(Point2D(pts[-1].x + nx, pts[-1].y + ny))
        return LineString(offset_points)

    @staticmethod
    def line_intersection(a1: Point2D, a2: Point2D, b1: Point2D, b2: Point2D) -> Point2D | None:
        denom = (a1.x - a2.x) * (b1.y - b2.y) - (a1.y - a2.y) * (b1.x - b2.x)
        if abs(denom) < 1e-12:
            return None
        t = ((a1.x - b1.x) * (b1.y - b2.y) - (a1.y - b1.y) * (b1.x - b2.x)) / denom
        u = -((a1.x - a2.x) * (a1.y - b1.y) - (a1.y - a2.y) * (a1.x - b1.x)) / denom
        if 0 <= t <= 1 and 0 <= u <= 1:
            return Point2D(a1.x + t * (a2.x - a1.x), a1.y + t * (a2.y - a1.y))
        return None

    # ------------------------------------------------------------------ #
    # POINT
    # ------------------------------------------------------------------ #
    @staticmethod
    def nearest(point: Point2D, candidates: list[Point2D]) -> Point2D | None:
        if not candidates:
            return None
        return min(candidates, key=lambda c: point.distance_to(c))

    @staticmethod
    def cluster_points(points: list[Point2D], radius: float) -> list[list[Point2D]]:
        """Basit yoğunluk tabanlı kümeleme (single-link, radius eşiği)."""
        clusters: list[list[Point2D]] = []
        visited = [False] * len(points)
        for i, p in enumerate(points):
            if visited[i]:
                continue
            cluster = [p]
            visited[i] = True
            queue = [i]
            while queue:
                idx = queue.pop()
                for j, q in enumerate(points):
                    if not visited[j] and points[idx].distance_to(q) <= radius:
                        visited[j] = True
                        cluster.append(q)
                        queue.append(j)
            clusters.append(cluster)
        return clusters


@dataclass
class PointIndex:
    """
    Roadmap: "Point > indexing". Basit grid-based spatial index
    (Phase 10'daki QuadTree/RTree'nin hafif ön sürümü).
    """

    cell_size: float
    _grid: dict[tuple[int, int], list[Point2D]] = field(default_factory=dict)

    def _cell(self, p: Point2D) -> tuple[int, int]:
        return (int(p.x // self.cell_size), int(p.y // self.cell_size))

    def insert(self, p: Point2D) -> None:
        self._grid.setdefault(self._cell(p), []).append(p)

    def nearest_search(self, p: Point2D, max_radius: float | None = None) -> Point2D | None:
        cx, cy = self._cell(p)
        best, best_dist = None, math.inf
        radius_cells = 1
        while True:
            for dx in range(-radius_cells, radius_cells + 1):
                for dy in range(-radius_cells, radius_cells + 1):
                    for candidate in self._grid.get((cx + dx, cy + dy), []):
                        d = p.distance_to(candidate)
                        if d < best_dist:
                            best, best_dist = candidate, d
            if best is not None or (max_radius and radius_cells * self.cell_size > max_radius):
                break
            radius_cells += 1
            if radius_cells > 1000:  # güvenlik sınırı
                break
        return best
