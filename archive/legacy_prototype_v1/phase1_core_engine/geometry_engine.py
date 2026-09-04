"""
harita_modelleme/phase1_core_engine/geometry_engine.py
==========================================================
FAZ 1 — Geometry Engine (sıfırdan)

Roadmap kapsamı:
    Polygon : simplify, merge, split, clip, union, intersection,
              convex hull, concave hull
    Line    : smoothing, snapping, offset, intersection
    Point   : clustering, indexing, nearest search

Harici bağımlılık yok. Sadece stdlib + numpy.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import numpy as np

Point2D = tuple[float, float]
Ring = list[Point2D]


class GeometryError(ValueError):
    pass


def _cross(o: Point2D, a: Point2D, b: Point2D) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _dist(a: Point2D, b: Point2D) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ============================================================================
# POLYGON OPS
# ============================================================================


class PolygonOps:
    """Polygon (halka listesi = [(x,y), ...], kapalı olması gerekmez) işlemleri."""

    # ---- simplify (Douglas-Peucker) -----------------------------------
    @staticmethod
    def simplify(ring: Ring, epsilon: float) -> Ring:
        if epsilon < 0:
            raise GeometryError("epsilon >= 0 olmalı")
        if len(ring) < 3:
            return list(ring)
        simplified = PolygonOps._douglas_peucker(ring, epsilon)
        if simplified[0] != simplified[-1] and ring[0] == ring[-1]:
            simplified.append(simplified[0])
        return simplified

    @staticmethod
    def _douglas_peucker(points: Ring, epsilon: float) -> Ring:
        if len(points) < 3:
            return list(points)

        def _perp_dist(pt: Point2D, a: Point2D, b: Point2D) -> float:
            if a == b:
                return _dist(pt, a)
            num = abs(_cross(a, b, pt))
            den = _dist(a, b)
            return num / den

        def _rdp(pts: Ring) -> Ring:
            if len(pts) < 3:
                return pts
            dmax, idx = 0.0, 0
            for i in range(1, len(pts) - 1):
                d = _perp_dist(pts[i], pts[0], pts[-1])
                if d > dmax:
                    dmax, idx = d, i
            if dmax > epsilon:
                left = _rdp(pts[: idx + 1])
                right = _rdp(pts[idx:])
                return left[:-1] + right
            return [pts[0], pts[-1]]

        return _rdp(list(points))

    # ---- convex hull (Andrew's monotone chain) -------------------------
    @staticmethod
    def convex_hull(points: Sequence[Point2D]) -> Ring:
        pts = sorted(set(points))
        if len(pts) <= 2:
            return list(pts)

        lower: Ring = []
        for p in pts:
            while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)

        upper: Ring = []
        for p in reversed(pts):
            while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)

        hull = lower[:-1] + upper[:-1]
        hull.append(hull[0])
        return hull

    # ---- concave hull (k-nearest-neighbour tabanlı, basitleştirilmiş) --
    @staticmethod
    def concave_hull(points: Sequence[Point2D], k: int = 3) -> Ring:
        """
        K-nearest neighbours tabanlı concave hull (Moreira & Santos, 2007
        algoritmasının basitleştirilmiş bir uygulaması). `k` küçüldükçe
        hull daha "içbükey" olur; k >= len(points) olduğunda convex hull'a
        yakınsar.
        """
        pts = list(dict.fromkeys(points))
        if len(pts) < 3:
            return pts
        k = max(3, min(k, len(pts) - 1))

        def _knn(p: Point2D, candidates: list[Point2D], n: int) -> list[Point2D]:
            return sorted(candidates, key=lambda q: _dist(p, q))[:n]

        start = min(pts, key=lambda p: p[1])
        hull = [start]
        current = start
        prev_angle = 0.0
        remaining = [p for p in pts if p != start]

        step_k = k
        while True:
            if not remaining and len(hull) > 1:
                break
            candidates = _knn(current, remaining if remaining else pts, step_k)
            if not candidates:
                if step_k >= len(pts):
                    break
                step_k += 1
                continue

            def _angle(p: Point2D) -> float:
                ang = math.atan2(p[1] - current[1], p[0] - current[0])
                diff = (prev_angle - ang) % (2 * math.pi)
                return diff

            candidates.sort(key=_angle)

            next_pt = None
            for cand in candidates:
                # kendisini kesmeyen ilk adayı seç
                if len(hull) < 3:
                    next_pt = cand
                    break
                intersects = False
                for i in range(len(hull) - 2):
                    if _segments_intersect(hull[i], hull[i + 1], current, cand):
                        intersects = True
                        break
                if not intersects:
                    next_pt = cand
                    break
            if next_pt is None:
                step_k += 1
                if step_k >= len(pts):
                    break
                continue

            prev_angle = math.atan2(next_pt[1] - current[1], next_pt[0] - current[0])
            hull.append(next_pt)
            current = next_pt
            if current in remaining:
                remaining.remove(current)
            if current == start:
                break
            step_k = k

        if hull[-1] != hull[0]:
            hull.append(hull[0])
        return hull

    # ---- Sutherland–Hodgman clip (convex clip polygon gerektirir) ------
    @staticmethod
    def clip(subject: Ring, clip_convex: Ring) -> Ring:
        """`subject` polygon'unu `clip_convex` (dışbükey!) polygon'a kırpar."""
        output = list(subject)
        clip_ring = clip_convex[:-1] if clip_convex[0] == clip_convex[-1] else clip_convex

        for i in range(len(clip_ring)):
            if not output:
                break
            a, b = clip_ring[i], clip_ring[(i + 1) % len(clip_ring)]
            input_list = output
            output = []
            if not input_list:
                continue
            s = input_list[-1]
            for e in input_list:
                e_inside = _cross(a, b, e) >= 0
                s_inside = _cross(a, b, s) >= 0
                if e_inside:
                    if not s_inside:
                        output.append(_line_intersect_point(s, e, a, b))
                    output.append(e)
                elif s_inside:
                    output.append(_line_intersect_point(s, e, a, b))
                s = e
        if output and output[0] != output[-1]:
            output.append(output[0])
        return output

    # ---- intersection / union (clip tabanlı, dışbükey varsayımıyla) ----
    @staticmethod
    def intersection(poly_a: Ring, poly_b: Ring) -> Ring:
        """İki dışbükey polygon'un kesişimi (Sutherland–Hodgman)."""
        return PolygonOps.clip(poly_a, poly_b)

    @staticmethod
    def union(poly_a: Ring, poly_b: Ring) -> Ring:
        """
        İki polygon'un union'ı: convex hull üzerinden birleşik dışbükey
        zarf (yaklaşık union — kavisli/iç bükey union için gelecek
        iterasyonda tam polygon-boolean algoritması — Weiler–Atherton —
        eklenecek; bkz. ROADMAP.md).
        """
        pts = list(poly_a) + list(poly_b)
        return PolygonOps.convex_hull(pts)

    # ---- merge / split ---------------------------------------------------
    @staticmethod
    def merge(polygons: Iterable[Ring]) -> Ring:
        """Birden çok polygonu tek bir dışbükey zarfta birleştirir."""
        pts: list[Point2D] = []
        for poly in polygons:
            pts.extend(poly)
        if not pts:
            raise GeometryError("merge: en az bir polygon gerekli")
        return PolygonOps.convex_hull(pts)

    @staticmethod
    def split(ring: Ring, line_a: Point2D, line_b: Point2D) -> tuple[Ring, Ring]:
        """Bir polygon'u bir doğru ile ikiye böler (dışbükey polygon varsayımı)."""
        core = ring[:-1] if ring[0] == ring[-1] else list(ring)
        left, right = [], []
        n = len(core)
        for i in range(n):
            cur = core[i]
            nxt = core[(i + 1) % n]
            side_cur = _cross(line_a, line_b, cur)
            side_nxt = _cross(line_a, line_b, nxt)
            if side_cur >= 0:
                left.append(cur)
            else:
                right.append(cur)
            if (side_cur > 0 and side_nxt < 0) or (side_cur < 0 and side_nxt > 0):
                ip = _line_intersect_point(cur, nxt, line_a, line_b)
                left.append(ip)
                right.append(ip)
        if left and left[0] != left[-1]:
            left.append(left[0])
        if right and right[0] != right[-1]:
            right.append(right[0])
        return left, right

    # ---- misc --------------------------------------------------------
    @staticmethod
    def area(ring: Ring) -> float:
        """Shoelace formülü ile imzalı alan (CCW pozitif)."""
        core = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        n = len(core)
        if n < 3:
            return 0.0
        s = 0.0
        for i in range(n):
            x1, y1 = core[i]
            x2, y2 = core[(i + 1) % n]
            s += x1 * y2 - x2 * y1
        return s / 2.0

    @staticmethod
    def perimeter(ring: Ring) -> float:
        core = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        n = len(core)
        return sum(_dist(core[i], core[(i + 1) % n]) for i in range(n))

    @staticmethod
    def centroid(ring: Ring) -> Point2D:
        core = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        n = len(core)
        if n == 0:
            raise GeometryError("boş polygon'un centroid'i yok")
        a = PolygonOps.area(ring)
        if abs(a) < 1e-12:
            xs = [p[0] for p in core]
            ys = [p[1] for p in core]
            return (sum(xs) / n, sum(ys) / n)
        cx = cy = 0.0
        for i in range(n):
            x1, y1 = core[i]
            x2, y2 = core[(i + 1) % n]
            cross = x1 * y2 - x2 * y1
            cx += (x1 + x2) * cross
            cy += (y1 + y2) * cross
        factor = 1.0 / (6.0 * a)
        return (cx * factor, cy * factor)

    @staticmethod
    def contains_point(ring: Ring, point: Point2D) -> bool:
        """Ray-casting point-in-polygon testi."""
        core = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
        x, y = point
        inside = False
        n = len(core)
        j = n - 1
        for i in range(n):
            xi, yi = core[i]
            xj, yj = core[j]
            intersects = ((yi > y) != (yj > y)) and (
                x < (xj - xi) * (y - yi) / (yj - yi + 1e-15) + xi
            )
            if intersects:
                inside = not inside
            j = i
        return inside


def _line_intersect_point(p1: Point2D, p2: Point2D, p3: Point2D, p4: Point2D) -> Point2D:
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-15:
        return p2
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _segments_intersect(a: Point2D, b: Point2D, c: Point2D, d: Point2D) -> bool:
    def ccw(p: Point2D, q: Point2D, r: Point2D) -> float:
        return _cross(p, q, r)

    d1 = ccw(c, d, a)
    d2 = ccw(c, d, b)
    d3 = ccw(a, b, c)
    d4 = ccw(a, b, d)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    return False


# ============================================================================
# LINE OPS
# ============================================================================


class LineOps:
    """Polyline (nokta listesi) işlemleri."""

    @staticmethod
    def smoothing(
        line: Sequence[Point2D], iterations: int = 1, factor: float = 0.5
    ) -> list[Point2D]:
        """Chaikin's corner-cutting algoritması ile çizgi yumuşatma."""
        pts = list(line)
        if len(pts) < 3 or iterations <= 0:
            return pts
        for _ in range(iterations):
            new_pts: list[Point2D] = [pts[0]]
            for i in range(len(pts) - 1):
                p0, p1 = pts[i], pts[i + 1]
                q = (
                    p0[0] + factor * (p1[0] - p0[0]) * 0.5,
                    p0[1] + factor * (p1[1] - p0[1]) * 0.5,
                )
                r = (
                    p0[0] + (1 - factor * 0.5) * (p1[0] - p0[0]),
                    p0[1] + (1 - factor * 0.5) * (p1[1] - p0[1]),
                )
                new_pts.append(q)
                new_pts.append(r)
            new_pts.append(pts[-1])
            pts = new_pts
        return pts

    @staticmethod
    def snapping(
        line: Sequence[Point2D], reference_points: Sequence[Point2D], tolerance: float
    ) -> list[Point2D]:
        """Line üzerindeki her noktayı, tolerans içindeyse en yakın referans noktasına yapıştırır (snap)."""
        result = []
        for p in line:
            nearest = min(reference_points, key=lambda r: _dist(p, r), default=None)
            if nearest is not None and _dist(p, nearest) <= tolerance:
                result.append(nearest)
            else:
                result.append(p)
        return result

    @staticmethod
    def offset(line: Sequence[Point2D], distance: float) -> list[Point2D]:
        """Polyline'ı normal yönünde `distance` kadar öteler (basit segment-normal ofseti)."""
        pts = list(line)
        if len(pts) < 2:
            return pts
        offset_pts: list[Point2D] = []
        for i in range(len(pts)):
            if i == 0:
                dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
            elif i == len(pts) - 1:
                dx, dy = pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]
            else:
                dx = pts[i + 1][0] - pts[i - 1][0]
                dy = pts[i + 1][1] - pts[i - 1][1]
            length = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / length, dx / length
            offset_pts.append((pts[i][0] + nx * distance, pts[i][1] + ny * distance))
        return offset_pts

    @staticmethod
    def intersection(
        line_a: tuple[Point2D, Point2D], line_b: tuple[Point2D, Point2D]
    ) -> Point2D | None:
        """İki doğru segmentinin kesişim noktası (varsa)."""
        a, b = line_a
        c, d = line_b
        if not _segments_intersect(a, b, c, d):
            return None
        return _line_intersect_point(a, b, c, d)

    @staticmethod
    def length(line: Sequence[Point2D]) -> float:
        pts = list(line)
        return sum(_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


# ============================================================================
# POINT OPS
# ============================================================================


class PointOps:
    """Point cloud işlemleri: clustering, indexing, nearest search."""

    @staticmethod
    def clustering(points: Sequence[Point2D], eps: float, min_points: int = 2) -> list[list[int]]:
        """
        DBSCAN (yoğunluk tabanlı kümeleme). Döndürülen değer, `points`
        listesindeki indekslerden oluşan küme listesidir; -1 kümesi yoktur,
        gürültü noktaları tek elemanlı kendi kümesinde kalır.
        """
        pts = np.asarray(points, dtype=float)
        n = len(pts)
        labels = [-1] * n  # -1 = henüz atanmadı
        visited = [False] * n
        cluster_id = 0

        def region_query(idx: int) -> list[int]:
            diffs = pts - pts[idx]
            dists = np.sqrt((diffs**2).sum(axis=1))
            return list(np.where(dists <= eps)[0])

        for i in range(n):
            if visited[i]:
                continue
            visited[i] = True
            neighbors = region_query(i)
            if len(neighbors) < min_points:
                labels[i] = -2  # gürültü (geçici işaret)
                continue
            labels[i] = cluster_id
            seeds = list(neighbors)
            k = 0
            while k < len(seeds):
                j = seeds[k]
                if not visited[j]:
                    visited[j] = True
                    j_neighbors = region_query(j)
                    if len(j_neighbors) >= min_points:
                        seeds.extend(x for x in j_neighbors if x not in seeds)
                if labels[j] in (-1, -2):
                    labels[j] = cluster_id
                k += 1
            cluster_id += 1

        clusters: dict = {}
        for idx, lab in enumerate(labels):
            key = lab if lab >= 0 else f"noise_{idx}"
            clusters.setdefault(key, []).append(idx)
        return list(clusters.values())

    @staticmethod
    def indexing(points: Sequence[Point2D], cell_size: float) -> SpatialGridIndex:
        """Uniform-grid spatial index oluşturur (Faz 10'daki QuadTree/RTree'nin ön aşaması)."""
        return SpatialGridIndex(points, cell_size)

    @staticmethod
    def nearest_search(
        points: Sequence[Point2D], query: Point2D, k: int = 1
    ) -> list[tuple[int, float]]:
        """Brute-force k-nearest-neighbour arama (index'siz, küçük veri setleri için)."""
        pts = np.asarray(points, dtype=float)
        q = np.asarray(query, dtype=float)
        dists = np.sqrt(((pts - q) ** 2).sum(axis=1))
        order = np.argsort(dists)[:k]
        return [(int(i), float(dists[i])) for i in order]


class SpatialGridIndex:
    """
    Basit uniform-grid tabanlı mekansal indeks. O(1) hücre araması ile
    yakın-nokta sorgularını hızlandırır (tam QuadTree/RTree Faz 10'da).
    """

    def __init__(self, points: Sequence[Point2D], cell_size: float):
        if cell_size <= 0:
            raise GeometryError("cell_size > 0 olmalı")
        self.cell_size = cell_size
        self.points = list(points)
        self._grid: dict = {}
        for i, (x, y) in enumerate(self.points):
            cell = self._cell_of(x, y)
            self._grid.setdefault(cell, []).append(i)

    def _cell_of(self, x: float, y: float) -> tuple[int, int]:
        return (int(math.floor(x / self.cell_size)), int(math.floor(y / self.cell_size)))

    def query_radius(self, center: Point2D, radius: float) -> list[int]:
        cx, cy = self._cell_of(*center)
        cell_span = int(math.ceil(radius / self.cell_size)) + 1
        result = []
        for dx in range(-cell_span, cell_span + 1):
            for dy in range(-cell_span, cell_span + 1):
                for idx in self._grid.get((cx + dx, cy + dy), []):
                    if _dist(self.points[idx], center) <= radius:
                        result.append(idx)
        return result

    def nearest(self, query: Point2D) -> int | None:
        radius = self.cell_size
        while radius < self.cell_size * 1000:
            candidates = self.query_radius(query, radius)
            if candidates:
                return min(candidates, key=lambda i: _dist(self.points[i], query))
            radius *= 2
        return None
