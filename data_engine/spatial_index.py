"""
Spatial Index
=============

Roadmap Phase 10 - "Spatial Database" (QuadTree, Octree, KDTree, BVH, RTree).

Bu modül, `core_engine.geometry_engine.PointIndex`'in (Phase 1, basit grid
tabanlı nokta indeksi) genelleştirilmiş / 3D versiyonlarını içerir:

- `QuadTree`   : 2D, dikdörtgen (AABB) tabanlı nesneler için bölge indeksi.
  Bina ayak izleri (footprint bounding box), tile'lar, editör nesneleri gibi
  "bu bölgede ne var" sorguları için.
- `Octree`     : 3D, kutu (AABB3D) tabanlı nesneler için bölge indeksi.
  Mesh parçaları, sahne nesneleri, terrain chunk'ları için.
- `KDTree`     : k-boyutlu nokta bulutu üzerinde en-yakın-komşu (nearest
  neighbor) ve yarıçap (range) sorguları. `PointIndex.nearest_search`'ün
  kesin (grid yaklaşıklığı olmayan) ve genelleştirilmiş (2D/3D/nD) versiyonu.
- `BVH`        : Bir `Mesh3D`'nin üçgenleri üzerinde Bounding Volume
  Hierarchy. Phase 6 `RayCasting`/`LineOfSight` gibi ray-mesh kesişim
  sorgularını O(n) yerine O(log n)'e yakın sürede çözmek için.
- `RTree`      : Guttman'ın klasik R-tree'si (quadratic split). Çakışan
  (overlapping) bounding box sorguları için - örn. "bu bölgeyle kesişen tüm
  bina ayak izlerini getir".

Bağımlılık: yalnızca stdlib.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, TypeVar

from ..mesh_engine import Mesh3D, Vertex3D

T = TypeVar("T")


# ======================================================================== #
# Sınırlayıcı kutular (Bounding Boxes)
# ======================================================================== #

@dataclass(slots=True)
class AABB2D:
    """Eksene-hizalı 2D sınırlayıcı dikdörtgen (Axis-Aligned Bounding Box)."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        if self.min_x > self.max_x or self.min_y > self.max_y:
            raise ValueError("AABB2D: min > max")

    def intersects(self, other: "AABB2D") -> bool:
        return not (
            self.max_x < other.min_x or other.max_x < self.min_x or
            self.max_y < other.min_y or other.max_y < self.min_y
        )

    def contains_point(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y

    def contains(self, other: "AABB2D") -> bool:
        return (
            self.min_x <= other.min_x and self.min_y <= other.min_y and
            self.max_x >= other.max_x and self.max_y >= other.max_y
        )

    def center(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)

    def area(self) -> float:
        return (self.max_x - self.min_x) * (self.max_y - self.min_y)

    def union(self, other: "AABB2D") -> "AABB2D":
        return AABB2D(
            min(self.min_x, other.min_x), min(self.min_y, other.min_y),
            max(self.max_x, other.max_x), max(self.max_y, other.max_y),
        )

    def enlargement(self, other: "AABB2D") -> float:
        """Bu kutuyu `other`'ı da kapsayacak şekilde büyütürsek alanın ne
        kadar artacağı (R-tree quadratic split / seçim sezgiselinde kullanılır)."""
        return self.union(other).area() - self.area()

    @staticmethod
    def from_points(points: Iterable[tuple[float, float]]) -> "AABB2D":
        xs, ys = zip(*points)
        return AABB2D(min(xs), min(ys), max(xs), max(ys))


@dataclass(slots=True)
class AABB3D:
    """Eksene-hizalı 3D sınırlayıcı kutu."""

    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    def __post_init__(self) -> None:
        if self.min_x > self.max_x or self.min_y > self.max_y or self.min_z > self.max_z:
            raise ValueError("AABB3D: min > max")

    def intersects(self, other: "AABB3D") -> bool:
        return not (
            self.max_x < other.min_x or other.max_x < self.min_x or
            self.max_y < other.min_y or other.max_y < self.min_y or
            self.max_z < other.min_z or other.max_z < self.min_z
        )

    def contains_point(self, x: float, y: float, z: float) -> bool:
        return (
            self.min_x <= x <= self.max_x and
            self.min_y <= y <= self.max_y and
            self.min_z <= z <= self.max_z
        )

    def center(self) -> tuple[float, float, float]:
        return (
            (self.min_x + self.max_x) / 2.0,
            (self.min_y + self.max_y) / 2.0,
            (self.min_z + self.max_z) / 2.0,
        )

    def union(self, other: "AABB3D") -> "AABB3D":
        return AABB3D(
            min(self.min_x, other.min_x), min(self.min_y, other.min_y), min(self.min_z, other.min_z),
            max(self.max_x, other.max_x), max(self.max_y, other.max_y), max(self.max_z, other.max_z),
        )

    @staticmethod
    def from_points(points: Iterable[tuple[float, float, float]]) -> "AABB3D":
        xs, ys, zs = zip(*points)
        return AABB3D(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    @staticmethod
    def from_triangle(a: Vertex3D, b: Vertex3D, c: Vertex3D) -> "AABB3D":
        return AABB3D(
            min(a.x, b.x, c.x), min(a.y, b.y, c.y), min(a.z, b.z, c.z),
            max(a.x, b.x, c.x), max(a.y, b.y, c.y), max(a.z, b.z, c.z),
        )

    def intersects_ray(self, origin: tuple[float, float, float],
                        inv_dir: tuple[float, float, float]) -> bool:
        """Slab yöntemi (Kay-Kajiya) ile AABB - ışın kesişim testi."""
        tmin, tmax = -math.inf, math.inf
        bounds = ((self.min_x, self.max_x), (self.min_y, self.max_y), (self.min_z, self.max_z))
        for axis in range(3):
            o = origin[axis]
            d = inv_dir[axis]
            lo, hi = bounds[axis]
            t1 = (lo - o) * d
            t2 = (hi - o) * d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return False
        return tmax >= 0.0


# ======================================================================== #
# QuadTree (2D)
# ======================================================================== #

@dataclass
class _QuadEntry(Generic[T]):
    bounds: AABB2D
    item: T


class QuadTree(Generic[T]):
    """2D bölge-tabanlı uzamsal indeks.

    Roadmap: "Spatial Database > QuadTree". `capacity` düğüm başına eleman
    sınırıdır; aşıldığında düğüm 4 alt-çeyreğe (NW/NE/SW/SE) bölünür.
    Öğeler nokta değil, `AABB2D` (bounding box) ile eklenir - bu yüzden bir
    öğe birden fazla çeyreğe düşebilir (kenar/köşe durumları), gerekirse
    her ikisine de eklenir (klasik "loose quadtree" yaklaşımı).
    """

    def __init__(self, boundary: AABB2D, capacity: int = 8, max_depth: int = 12, _depth: int = 0) -> None:
        self.boundary = boundary
        self.capacity = capacity
        self.max_depth = max_depth
        self._depth = _depth
        self._entries: list[_QuadEntry[T]] = []
        self._children: list["QuadTree[T]"] | None = None

    def _subdivide(self) -> None:
        cx, cy = self.boundary.center()
        b = self.boundary
        quads = [
            AABB2D(b.min_x, cy, cx, b.max_y),   # NW
            AABB2D(cx, cy, b.max_x, b.max_y),   # NE
            AABB2D(b.min_x, b.min_y, cx, cy),   # SW
            AABB2D(cx, b.min_y, b.max_x, cy),   # SE
        ]
        self._children = [
            QuadTree(q, self.capacity, self.max_depth, self._depth + 1) for q in quads
        ]

    def insert(self, item: T, bounds: AABB2D) -> bool:
        if not self.boundary.intersects(bounds):
            return False

        if self._children is None:
            if len(self._entries) < self.capacity or self._depth >= self.max_depth:
                self._entries.append(_QuadEntry(bounds, item))
                return True
            self._subdivide()
            # mevcut öğeleri alt-düğümlere dağıt
            old_entries, self._entries = self._entries, []
            for e in old_entries:
                self._insert_into_children(e.item, e.bounds)

        self._insert_into_children(item, bounds)
        return True

    def _insert_into_children(self, item: T, bounds: AABB2D) -> None:
        assert self._children is not None
        inserted = False
        for child in self._children:
            if child.boundary.intersects(bounds):
                inserted = child.insert(item, bounds) or inserted
        if not inserted:
            # sınırların dışına taştı (nadiren) - kökte tut
            self._entries.append(_QuadEntry(bounds, item))

    def query(self, range_: AABB2D) -> list[T]:
        """`range_` ile kesişen tüm öğeleri (tekrarsız) döndürür."""
        found: list[T] = []
        seen: set[int] = set()
        self._query(range_, found, seen)
        return found

    def _query(self, range_: AABB2D, found: list[T], seen: set[int]) -> None:
        if not self.boundary.intersects(range_):
            return
        for e in self._entries:
            if e.bounds.intersects(range_) and id(e.item) not in seen:
                seen.add(id(e.item))
                found.append(e.item)
        if self._children is not None:
            for child in self._children:
                child._query(range_, found, seen)

    def count(self) -> int:
        """Ağaçta saklanan **tekil** öğe sayısı.

        Not: sınır çizgisine denk gelen öğeler birden fazla çeyreğe
        (loose-quadtree yaklaşımı gereği) eklenebilir; bu yüzden ham düğüm
        girişi sayısı yerine `id()` ile tekilleştirilmiş sayım yapılır.
        """
        seen: set[int] = set()
        self._collect_ids(seen)
        return len(seen)

    def _collect_ids(self, seen: set[int]) -> None:
        for e in self._entries:
            seen.add(id(e.item))
        if self._children is not None:
            for child in self._children:
                child._collect_ids(seen)


# ======================================================================== #
# Octree (3D)
# ======================================================================== #

@dataclass
class _OctEntry(Generic[T]):
    bounds: AABB3D
    item: T


class Octree(Generic[T]):
    """3D bölge-tabanlı uzamsal indeks (QuadTree'nin 8 çocuklu 3D versiyonu).

    Roadmap: "Spatial Database > Octree". Sahne nesneleri, mesh parçaları,
    terrain chunk'ları için kullanılır.
    """

    def __init__(self, boundary: AABB3D, capacity: int = 8, max_depth: int = 10, _depth: int = 0) -> None:
        self.boundary = boundary
        self.capacity = capacity
        self.max_depth = max_depth
        self._depth = _depth
        self._entries: list[_OctEntry[T]] = []
        self._children: list["Octree[T]"] | None = None

    def _subdivide(self) -> None:
        cx, cy, cz = self.boundary.center()
        b = self.boundary
        xs = [(b.min_x, cx), (cx, b.max_x)]
        ys = [(b.min_y, cy), (cy, b.max_y)]
        zs = [(b.min_z, cz), (cz, b.max_z)]
        self._children = [
            Octree(AABB3D(xlo, ylo, zlo, xhi, yhi, zhi), self.capacity, self.max_depth, self._depth + 1)
            for (xlo, xhi) in xs for (ylo, yhi) in ys for (zlo, zhi) in zs
        ]

    def insert(self, item: T, bounds: AABB3D) -> bool:
        if not self.boundary.intersects(bounds):
            return False

        if self._children is None:
            if len(self._entries) < self.capacity or self._depth >= self.max_depth:
                self._entries.append(_OctEntry(bounds, item))
                return True
            self._subdivide()
            old_entries, self._entries = self._entries, []
            for e in old_entries:
                self._insert_into_children(e.item, e.bounds)

        self._insert_into_children(item, bounds)
        return True

    def _insert_into_children(self, item: T, bounds: AABB3D) -> None:
        assert self._children is not None
        inserted = False
        for child in self._children:
            if child.boundary.intersects(bounds):
                inserted = child.insert(item, bounds) or inserted
        if not inserted:
            self._entries.append(_OctEntry(bounds, item))

    def query(self, range_: AABB3D) -> list[T]:
        found: list[T] = []
        seen: set[int] = set()
        self._query(range_, found, seen)
        return found

    def _query(self, range_: AABB3D, found: list[T], seen: set[int]) -> None:
        if not self.boundary.intersects(range_):
            return
        for e in self._entries:
            if e.bounds.intersects(range_) and id(e.item) not in seen:
                seen.add(id(e.item))
                found.append(e.item)
        if self._children is not None:
            for child in self._children:
                child._query(range_, found, seen)

    def count(self) -> int:
        """Ağaçtaki tekil öğe sayısı (bkz. `QuadTree.count` notu - sınıra
        denk gelen öğeler birden fazla alt-kutuya eklenebilir)."""
        seen: set[int] = set()
        self._collect_ids(seen)
        return len(seen)

    def _collect_ids(self, seen: set[int]) -> None:
        for e in self._entries:
            seen.add(id(e.item))
        if self._children is not None:
            for child in self._children:
                child._collect_ids(seen)


# ======================================================================== #
# KDTree (nD nokta indeksi - nearest neighbor)
# ======================================================================== #

class _KDNode:
    __slots__ = ("point", "data", "axis", "left", "right")

    def __init__(self, point: tuple[float, ...], data: Any, axis: int) -> None:
        self.point = point
        self.data = data
        self.axis = axis
        self.left: "_KDNode | None" = None
        self.right: "_KDNode | None" = None


def _sq_dist(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum((p - q) ** 2 for p, q in zip(a, b))


class KDTree:
    """k-boyutlu nokta bulutu için ikili arama ağacı.

    Roadmap: "Point > indexing" / "Point > nearest search" - Phase 1
    `PointIndex`'in grid-yaklaşıklığı olmayan, kesin (exact) versiyonu.
    2D, 3D veya herhangi bir boyutta nokta kabul eder (tüm noktalar aynı
    boyutta olmalı). Dengeleme için inşa sırasında medyan-split yapılır
    (statik veri kümesi için O(n log n) inşa, O(log n) ortalama sorgu).
    """

    def __init__(self, points: list[tuple[float, ...]], data: list[Any] | None = None) -> None:
        if data is not None and len(data) != len(points):
            raise ValueError("KDTree: points ve data uzunlukları eşleşmeli")
        self.dims = len(points[0]) if points else 0
        items = list(zip(points, data if data is not None else points))
        self._root = self._build(items, depth=0)
        self._size = len(points)

    def _build(self, items: list[tuple[tuple[float, ...], Any]], depth: int) -> "_KDNode | None":
        if not items:
            return None
        axis = depth % self.dims
        items.sort(key=lambda it: it[0][axis])
        mid = len(items) // 2
        point, data = items[mid]
        node = _KDNode(point, data, axis)
        node.left = self._build(items[:mid], depth + 1)
        node.right = self._build(items[mid + 1:], depth + 1)
        return node

    def __len__(self) -> int:
        return self._size

    def nearest(self, target: tuple[float, ...]) -> tuple[tuple[float, ...], Any, float] | None:
        """En yakın noktayı `(point, data, distance)` olarak döndürür."""
        results = self.nearest_k(target, 1)
        return results[0] if results else None

    def nearest_k(self, target: tuple[float, ...], k: int) -> list[tuple[tuple[float, ...], Any, float]]:
        """En yakın `k` noktayı, artan mesafeye göre sıralı döndürür."""
        if self._root is None or k <= 0:
            return []
        best: list[tuple[float, tuple[float, ...], Any]] = []  # (dist, point, data), max-heap yerine liste (k küçük varsayımı)

        def visit(node: "_KDNode | None") -> None:
            if node is None:
                return
            d = _sq_dist(target, node.point)
            if len(best) < k:
                best.append((d, node.point, node.data))
                best.sort(key=lambda t: t[0])
            elif d < best[-1][0]:
                best[-1] = (d, node.point, node.data)
                best.sort(key=lambda t: t[0])

            axis = node.axis
            diff = target[axis] - node.point[axis]
            near, far = (node.left, node.right) if diff < 0 else (node.right, node.left)
            visit(near)
            # yalnızca ayırıcı düzleme olan mesafe, mevcut en kötü sonuçtan
            # küçükse diğer dala da bak (budama)
            if len(best) < k or diff * diff < best[-1][0]:
                visit(far)

        visit(self._root)
        return [(p, dat, math.sqrt(d)) for d, p, dat in best]

    def range_search(self, target: tuple[float, ...], radius: float) -> list[tuple[tuple[float, ...], Any, float]]:
        """`radius` içindeki tüm noktaları döndürür (mesafeye göre sıralı)."""
        found: list[tuple[float, tuple[float, ...], Any]] = []
        r_sq = radius * radius

        def visit(node: "_KDNode | None") -> None:
            if node is None:
                return
            d = _sq_dist(target, node.point)
            if d <= r_sq:
                found.append((d, node.point, node.data))
            axis = node.axis
            diff = target[axis] - node.point[axis]
            near, far = (node.left, node.right) if diff < 0 else (node.right, node.left)
            visit(near)
            if diff * diff <= r_sq:
                visit(far)

        visit(self._root)
        found.sort(key=lambda t: t[0])
        return [(p, dat, math.sqrt(d)) for d, p, dat in found]


# ======================================================================== #
# BVH (Mesh üçgenleri için Bounding Volume Hierarchy)
# ======================================================================== #

@dataclass(slots=True)
class RayHit:
    triangle_index: int
    t: float
    point: tuple[float, float, float]


class _BVHNode:
    __slots__ = ("bounds", "left", "right", "triangle_indices")

    def __init__(self, bounds: AABB3D) -> None:
        self.bounds = bounds
        self.left: "_BVHNode | None" = None
        self.right: "_BVHNode | None" = None
        self.triangle_indices: list[int] = []


def _ray_triangle_intersect(
    origin: tuple[float, float, float], direction: tuple[float, float, float],
    a: Vertex3D, b: Vertex3D, c: Vertex3D,
) -> float | None:
    """Möller-Trumbore ray-triangle kesişim algoritması. Kesişim varsa `t`
    (origin + t*direction = kesişim noktası) döndürür, yoksa None."""
    eps = 1e-9
    edge1 = (b.x - a.x, b.y - a.y, b.z - a.z)
    edge2 = (c.x - a.x, c.y - a.y, c.z - a.z)

    def cross(u, v):
        return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])

    def dot(u, v):
        return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]

    h = cross(direction, edge2)
    det = dot(edge1, h)
    if -eps < det < eps:
        return None  # ışın üçgene paralel
    inv_det = 1.0 / det
    s = (origin[0] - a.x, origin[1] - a.y, origin[2] - a.z)
    u = inv_det * dot(s, h)
    if u < 0.0 or u > 1.0:
        return None
    q = cross(s, edge1)
    v = inv_det * dot(direction, q)
    if v < 0.0 or u + v > 1.0:
        return None
    t = inv_det * dot(edge2, q)
    if t <= eps:
        return None
    return t


class BVH:
    """`Mesh3D` üçgenleri üzerinde Bounding Volume Hierarchy.

    Roadmap: "Spatial Database" (Phase 10) - Phase 6 `RayCasting`/`LineOfSight`
    ve Phase 9 `XRay`/`occlusion_ratio` gibi ray-mesh kesişim ihtiyaçlarını
    O(n) doğrusal taramadan O(log n)'e yakın performansa taşır.

    İnşa: üst-aşağı (top-down), her düğümde en uzun eksene göre medyan-split
    (surface-area-heuristic'in basitleştirilmiş, yine de etkili bir versiyonu).
    """

    def __init__(self, mesh: Mesh3D, leaf_size: int = 4) -> None:
        self.mesh = mesh
        self.leaf_size = leaf_size
        tri_bounds = [
            AABB3D.from_triangle(*mesh.triangle_positions(tri)) for tri in mesh.triangles
        ]
        self._root = self._build(list(range(len(mesh.triangles))), tri_bounds)

    def _build(self, indices: list[int], tri_bounds: list[AABB3D]) -> "_BVHNode | None":
        if not indices:
            return None
        bounds = tri_bounds[indices[0]]
        for i in indices[1:]:
            bounds = bounds.union(tri_bounds[i])

        node = _BVHNode(bounds)
        if len(indices) <= self.leaf_size:
            node.triangle_indices = indices
            return node

        # en uzun ekseni bul, o eksendeki centroid medyanına göre böl
        extents = (bounds.max_x - bounds.min_x, bounds.max_y - bounds.min_y, bounds.max_z - bounds.min_z)
        axis = extents.index(max(extents))

        def centroid_on_axis(i: int) -> float:
            b = tri_bounds[i]
            centers = ((b.min_x + b.max_x) / 2.0, (b.min_y + b.max_y) / 2.0, (b.min_z + b.max_z) / 2.0)
            return centers[axis]

        indices = sorted(indices, key=centroid_on_axis)
        mid = len(indices) // 2
        node.left = self._build(indices[:mid], tri_bounds)
        node.right = self._build(indices[mid:], tri_bounds)
        return node

    def intersect_ray(
        self, origin: tuple[float, float, float], direction: tuple[float, float, float],
    ) -> RayHit | None:
        """Işını ağaçta gezerek en yakın (en küçük `t`) kesişimi bulur."""
        length = math.sqrt(sum(d * d for d in direction))
        if length < 1e-12:
            return None
        direction = tuple(d / length for d in direction)
        inv_dir = tuple((1.0 / d if abs(d) > 1e-12 else math.inf) for d in direction)

        best: RayHit | None = None

        def visit(node: "_BVHNode | None") -> None:
            nonlocal best
            if node is None:
                return
            if not node.bounds.intersects_ray(origin, inv_dir):
                return
            if node.triangle_indices:
                for ti in node.triangle_indices:
                    a, b, c = self.mesh.triangle_positions(self.mesh.triangles[ti])
                    t = _ray_triangle_intersect(origin, direction, a, b, c)
                    if t is not None and (best is None or t < best.t):
                        point = (origin[0] + t * direction[0], origin[1] + t * direction[1], origin[2] + t * direction[2])
                        best = RayHit(triangle_index=ti, t=t, point=point)
                return
            visit(node.left)
            visit(node.right)

        visit(self._root)
        return best

    def query_aabb(self, range_: AABB3D) -> list[int]:
        """`range_` ile kesişen üçgen indekslerini döndürür."""
        found: list[int] = []

        def visit(node: "_BVHNode | None") -> None:
            if node is None or not node.bounds.intersects(range_):
                return
            if node.triangle_indices:
                found.extend(node.triangle_indices)
                return
            visit(node.left)
            visit(node.right)

        visit(self._root)
        return found

    def query(self, range_: AABB3D) -> list[int]:
        """`query_aabb()`'nin alias'ı (Roadmap V4/C6) — diğer spatial index
        sınıflarıyla (`QuadTree`/`Octree`/`KDTree`/`RTree`) ortak `query(...)`
        arayüzü sağlar. `query_aabb()` geriye uyumluluk için korunur."""
        return self.query_aabb(range_)


# ======================================================================== #
# RTree (Guttman, quadratic split)
# ======================================================================== #

class _RTreeNode:
    __slots__ = ("is_leaf", "entries", "bounds", "parent")

    def __init__(self, is_leaf: bool) -> None:
        self.is_leaf = is_leaf
        # leaf: list[(AABB2D, item)] ; internal: list[(AABB2D, _RTreeNode)]
        self.entries: list[tuple[AABB2D, Any]] = []
        self.bounds: AABB2D | None = None
        self.parent: "_RTreeNode | None" = None

    def recompute_bounds(self) -> None:
        if not self.entries:
            self.bounds = None
            return
        b = self.entries[0][0]
        for eb, _ in self.entries[1:]:
            b = b.union(eb)
        self.bounds = b


class RTree(Generic[T]):
    """Guttman'ın klasik R-tree'si (quadratic-cost split algoritması).

    Roadmap: "Spatial Database > RTree". Çakışan bounding-box sorguları için
    (`QuadTree`'den farkı: R-tree öğeleri yaprak düğümlerde tutar ve iç
    düğümler MBR - minimum bounding rectangle - hiyerarşisi oluşturur; çok
    sayıda küçük/örtüşen dikdörtgen için genelde daha az düğüm ziyaret eder).

    Performans notu (Roadmap V2, A10): her düğüm `parent` işaretçisi tutar,
    bu yüzden bir ekleme sonrası MBR güncellemesi ve gerekiyorsa bölünme,
    yalnızca kökten yaprağa giden **yol** üzerinde (O(log n)) yapılır — her
    `insert`'te kökten itibaren tüm ağacı yeniden hesaplayan eski yaklaşım
    (O(n) / ekleme, dolayısıyla O(n^2) toplam) kaldırıldı. 100.000+ nesnelik
    sahnelerde bu fark saniyelerden milisaniyelere iner (bkz.
    `data_engine/BENCHMARK.md`).
    """

    def __init__(self, max_entries: int = 8, min_entries: int | None = None) -> None:
        self.max_entries = max_entries
        self.min_entries = min_entries if min_entries is not None else max(2, max_entries // 2)
        self._root = _RTreeNode(is_leaf=True)
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def insert(self, item: T, bounds: AABB2D) -> None:
        leaf = self._choose_leaf(self._root, bounds)
        leaf.entries.append((bounds, item))
        leaf.recompute_bounds()
        self._size += 1
        if len(leaf.entries) > self.max_entries:
            self._split(leaf)
        else:
            self._propagate_bounds(leaf)

    def _choose_leaf(self, node: "_RTreeNode", bounds: AABB2D) -> "_RTreeNode":
        if node.is_leaf:
            return node
        # en az büyüme gerektiren çocuğu seç (Guttman: ChooseSubtree)
        best_child: "_RTreeNode | None" = None
        best_enlargement = math.inf
        best_area = math.inf
        for eb, child in node.entries:
            enlargement = eb.enlargement(bounds)
            area = eb.area()
            if (enlargement, area) < (best_enlargement, best_area):
                best_enlargement, best_area = enlargement, area
                best_child = child
        assert best_child is not None
        return self._choose_leaf(best_child, bounds)

    def _propagate_bounds(self, node: "_RTreeNode") -> None:
        """`node`'un MBR'ini üst düğümlerdeki karşılık gelen girişe yansıtır
        ve kök yönünde yukarı doğru ilerler. Yalnızca kökten `node`'a giden
        yol üzerinde çalışır (tüm ağacı değil) — O(log n) amortize maliyet.
        """
        child = node
        parent = node.parent
        while parent is not None:
            self._update_child_bounds(parent, child)
            parent.recompute_bounds()
            child = parent
            parent = parent.parent

    def _update_child_bounds(self, parent: "_RTreeNode", child: "_RTreeNode") -> None:
        """`parent.entries` içindeki `child`'a ait (bounds, child) girişini
        `child`'ın güncel MBR'iyle değiştirir. `parent.entries` boyutu her
        zaman `max_entries` ile sınırlı (sabit/küçük) olduğundan bu tarama
        O(1)'e yakındır, ağacın toplam boyutuna bağlı değildir.
        """
        for i, (_, c) in enumerate(parent.entries):
            if c is child:
                parent.entries[i] = (child.bounds, child)
                return
        raise AssertionError("RTree: iç tutarlılık hatası - child parent.entries içinde yok")

    def _split(self, node: "_RTreeNode") -> None:
        group_a, group_b = self._quadratic_split(node.entries)
        node_a = _RTreeNode(node.is_leaf)
        node_a.entries = group_a
        node_a.recompute_bounds()
        node_b = _RTreeNode(node.is_leaf)
        node_b.entries = group_b
        node_b.recompute_bounds()

        if not node.is_leaf:
            for _, child in node_a.entries:
                child.parent = node_a
            for _, child in node_b.entries:
                child.parent = node_b

        if node is self._root:
            new_root = _RTreeNode(is_leaf=False)
            node_a.parent = new_root
            node_b.parent = new_root
            new_root.entries = [(node_a.bounds, node_a), (node_b.bounds, node_b)]
            new_root.recompute_bounds()
            self._root = new_root
            return

        parent = node.parent
        assert parent is not None
        for i, (_, c) in enumerate(parent.entries):
            if c is node:
                node_a.parent = parent
                node_b.parent = parent
                parent.entries[i] = (node_a.bounds, node_a)
                parent.entries.append((node_b.bounds, node_b))
                break
        else:
            raise AssertionError("RTree: iç tutarlılık hatası - node parent.entries içinde yok")

        parent.recompute_bounds()
        if len(parent.entries) > self.max_entries:
            self._split(parent)
        else:
            self._propagate_bounds(parent)

    def _quadratic_split(
        self, entries: list[tuple[AABB2D, Any]],
    ) -> tuple[list[tuple[AABB2D, Any]], list[tuple[AABB2D, Any]]]:
        """Guttman'ın Quadratic Split algoritması: en 'kötü çift'i (birlikte
        en büyük boşa alanı yaratan iki giriş) tohum olarak seçer, sonra
        kalan girişleri en az büyüme gerektiren gruba dağıtır."""
        remaining = list(entries)

        # 1) PickSeeds: en kötü çift
        worst_waste = -math.inf
        seed_i, seed_j = 0, 1
        for i in range(len(remaining)):
            for j in range(i + 1, len(remaining)):
                combined = remaining[i][0].union(remaining[j][0])
                waste = combined.area() - remaining[i][0].area() - remaining[j][0].area()
                if waste > worst_waste:
                    worst_waste = waste
                    seed_i, seed_j = i, j

        seed_a = remaining[seed_i]
        seed_b = remaining[seed_j]
        for idx in sorted([seed_i, seed_j], reverse=True):
            remaining.pop(idx)

        group_a = [seed_a]
        group_b = [seed_b]
        bounds_a = seed_a[0]
        bounds_b = seed_b[0]

        min_needed = self.min_entries
        while remaining:
            # Kalan girişten fazlası kalmıyorsa hepsini eksik gruba ata
            if len(group_a) + len(remaining) <= min_needed:
                group_a.extend(remaining)
                remaining = []
                break
            if len(group_b) + len(remaining) <= min_needed:
                group_b.extend(remaining)
                remaining = []
                break

            # 2) PickNext: en yüksek "tercih farkı" olan girişi seç
            best_idx = 0
            best_pref = -math.inf
            best_target = "a"
            for idx, (eb, _item) in enumerate(remaining):
                grow_a = bounds_a.enlargement(eb)
                grow_b = bounds_b.enlargement(eb)
                pref = abs(grow_a - grow_b)
                if pref > best_pref:
                    best_pref = pref
                    best_idx = idx
                    best_target = "a" if grow_a < grow_b else "b"

            entry = remaining.pop(best_idx)
            if best_target == "a":
                group_a.append(entry)
                bounds_a = bounds_a.union(entry[0])
            else:
                group_b.append(entry)
                bounds_b = bounds_b.union(entry[0])

        return group_a, group_b

    def search(self, range_: AABB2D) -> list[T]:
        """`range_` ile çakışan tüm öğeleri döndürür."""
        found: list[T] = []
        self._search(self._root, range_, found)
        return found

    def query(self, range_: AABB2D) -> list[T]:
        """`search()`'ün alias'ı (Roadmap V4/C6) — `QuadTree`/`Octree`/`KDTree`
        ile ortak `query(...)` arayüzü sağlamak için. Geriye uyumluluk için
        `search()` de aynen korunur; ikisi de aynı sonucu döndürür."""
        return self.search(range_)

    def _search(self, node: "_RTreeNode", range_: AABB2D, found: list[T]) -> None:
        for eb, child_or_item in node.entries:
            if not eb.intersects(range_):
                continue
            if node.is_leaf:
                found.append(child_or_item)
            else:
                self._search(child_or_item, range_, found)
