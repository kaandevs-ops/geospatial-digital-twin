"""
Measurement
============

Roadmap Phase 6 - "Measurement": Distance, Area, Volume, Height, Angle, Slope.

Bu modül yeni bir geometri motoru icat etmez; Phase 1 `GeometryEngine`/`Polygon`
ve Phase 2 `Mesh3D` üzerine ince, amaca yönelik yardımcı fonksiyonlar ekler
(roadmap'in katmanlı mimari ilkesi). Tüm sonuçlar `MeasurementResult` ile
birimiyle birlikte döner, böylece raporlama (Phase 11 Export) tek bir
sözleşmeye bağlı kalabilir.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ...core_engine.geometry_engine import Point2D, Polygon
from ...mesh_engine import Mesh3D, Vertex3D


@dataclass(slots=True, frozen=True)
class MeasurementResult:
    """Tek bir ölçüm sonucu: değer + birim + insan-okur açıklama."""

    value: float
    unit: str
    label: str = ""

    def __str__(self) -> str:  # pragma: no cover - kolaylık
        prefix = f"{self.label}: " if self.label else ""
        return f"{prefix}{self.value:.4f} {self.unit}"


class MeasurementEngine:
    """Roadmap: 'Measurement' bölümünün tüm alt maddeleri."""

    # -- Distance -------------------------------------------------------- #
    @staticmethod
    def distance_2d(a: Point2D, b: Point2D) -> MeasurementResult:
        return MeasurementResult(a.distance_to(b), "m", "distance_2d")

    @staticmethod
    def distance_3d(a: tuple[float, float, float] | Vertex3D,
                     b: tuple[float, float, float] | Vertex3D) -> MeasurementResult:
        ax, ay, az = a.as_tuple() if isinstance(a, Vertex3D) else a
        bx, by, bz = b.as_tuple() if isinstance(b, Vertex3D) else b
        d = math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)
        return MeasurementResult(d, "m", "distance_3d")

    # -- Area -------------------------------------------------------------- #
    @staticmethod
    def area(polygon: Polygon) -> MeasurementResult:
        return MeasurementResult(polygon.unsigned_area(), "m2", "area")

    @staticmethod
    def mesh_surface_area(mesh: Mesh3D) -> MeasurementResult:
        return MeasurementResult(mesh.surface_area(), "m2", "surface_area")

    # -- Volume ------------------------------------------------------------ #
    @staticmethod
    def volume_tetrahedral(mesh: Mesh3D) -> MeasurementResult:
        """Kapalı/manifold mesh için kesin (işaretli tetrahedron toplamı)."""
        return MeasurementResult(mesh.volume(), "m3", "volume_tetrahedral")

    @staticmethod
    def volume_monte_carlo(mesh: Mesh3D, samples: int = 20_000,
                            seed: int | None = None) -> MeasurementResult:
        """Kapalı olması garanti olmayan mesh'ler için Monte-Carlo hacim
        tahmini: bounding-box içine rastgele nokta atıp mesh içinde kalanların
        oranını box hacmiyle çarpar. Ray-casting parity testi (bir eksende
        tek yönlü ışın atıp kesişim sayısının tekliği) ile "içeride mi"
        belirlenir.
        """
        rng = random.Random(seed)
        (min_x, min_y, min_z), (max_x, max_y, max_z) = mesh.bounding_box()
        box_volume = (max_x - min_x) * (max_y - min_y) * (max_z - min_z)
        if box_volume <= 0.0 or not mesh.triangles:
            return MeasurementResult(0.0, "m3", "volume_monte_carlo")

        inside = 0
        for _ in range(samples):
            p = (
                rng.uniform(min_x, max_x),
                rng.uniform(min_y, max_y),
                rng.uniform(min_z, max_z),
            )
            if _point_inside_mesh(p, mesh):
                inside += 1
        estimate = box_volume * (inside / samples)
        return MeasurementResult(estimate, "m3", "volume_monte_carlo")

    # -- Height -------------------------------------------------------------- #
    @staticmethod
    def height(mesh: Mesh3D) -> MeasurementResult:
        (_, _, min_z), (_, _, max_z) = mesh.bounding_box()
        return MeasurementResult(max_z - min_z, "m", "height")

    @staticmethod
    def height_between(base: tuple[float, float, float], top: tuple[float, float, float]) -> MeasurementResult:
        return MeasurementResult(abs(top[2] - base[2]), "m", "height_between")

    # -- Angle -------------------------------------------------------------- #
    @staticmethod
    def angle(p1: Point2D, vertex: Point2D, p2: Point2D) -> MeasurementResult:
        """`vertex` köşesindeki açı (derece), p1-vertex-p2 arasında."""
        v1 = (p1.x - vertex.x, p1.y - vertex.y)
        v2 = (p2.x - vertex.x, p2.y - vertex.y)
        len1 = math.hypot(*v1)
        len2 = math.hypot(*v2)
        if len1 < 1e-12 or len2 < 1e-12:
            return MeasurementResult(0.0, "deg", "angle")
        cos_a = (v1[0] * v2[0] + v1[1] * v2[1]) / (len1 * len2)
        cos_a = max(-1.0, min(1.0, cos_a))
        return MeasurementResult(math.degrees(math.acos(cos_a)), "deg", "angle")

    @staticmethod
    def angle_3d(a: tuple[float, float, float], vertex: tuple[float, float, float],
                 b: tuple[float, float, float]) -> MeasurementResult:
        v1 = tuple(a[i] - vertex[i] for i in range(3))
        v2 = tuple(b[i] - vertex[i] for i in range(3))
        len1 = math.sqrt(sum(c * c for c in v1))
        len2 = math.sqrt(sum(c * c for c in v2))
        if len1 < 1e-12 or len2 < 1e-12:
            return MeasurementResult(0.0, "deg", "angle_3d")
        dot = sum(v1[i] * v2[i] for i in range(3))
        cos_a = max(-1.0, min(1.0, dot / (len1 * len2)))
        return MeasurementResult(math.degrees(math.acos(cos_a)), "deg", "angle_3d")

    # -- Slope -------------------------------------------------------------- #
    @staticmethod
    def slope(a: tuple[float, float, float], b: tuple[float, float, float]) -> MeasurementResult:
        """İki nokta arasındaki eğim, yüzde (%) cinsinden (rise/run * 100)."""
        horizontal = math.hypot(b[0] - a[0], b[1] - a[1])
        rise = b[2] - a[2]
        if horizontal < 1e-12:
            pct = math.inf if rise != 0 else 0.0
        else:
            pct = (rise / horizontal) * 100.0
        return MeasurementResult(pct, "%", "slope")

    @staticmethod
    def slope_degrees(a: tuple[float, float, float], b: tuple[float, float, float]) -> MeasurementResult:
        horizontal = math.hypot(b[0] - a[0], b[1] - a[1])
        rise = b[2] - a[2]
        if horizontal < 1e-12:
            deg = 90.0 if rise != 0 else 0.0
        else:
            deg = math.degrees(math.atan2(rise, horizontal))
        return MeasurementResult(deg, "deg", "slope_degrees")


def _point_inside_mesh(p: tuple[float, float, float], mesh: Mesh3D) -> bool:
    """Tek eksenli (+X) ışın atıp kesişim parite testi (odd = içeride)."""
    hits = 0
    direction = (1.0, 0.0, 0.0)
    for tri in mesh.triangles:
        a, b, c = mesh.triangle_positions(tri)
        t = _ray_triangle(p, direction, a.as_tuple(), b.as_tuple(), c.as_tuple())
        if t is not None and t > 1e-9:
            hits += 1
    return hits % 2 == 1


def _ray_triangle(origin, direction, v0, v1, v2) -> float | None:
    eps = 1e-9
    edge1 = tuple(v1[i] - v0[i] for i in range(3))
    edge2 = tuple(v2[i] - v0[i] for i in range(3))
    h = (
        direction[1] * edge2[2] - direction[2] * edge2[1],
        direction[2] * edge2[0] - direction[0] * edge2[2],
        direction[0] * edge2[1] - direction[1] * edge2[0],
    )
    a = edge1[0] * h[0] + edge1[1] * h[1] + edge1[2] * h[2]
    if -eps < a < eps:
        return None
    f = 1.0 / a
    s = tuple(origin[i] - v0[i] for i in range(3))
    u = f * (s[0] * h[0] + s[1] * h[1] + s[2] * h[2])
    if u < 0.0 or u > 1.0:
        return None
    q = (
        s[1] * edge1[2] - s[2] * edge1[1],
        s[2] * edge1[0] - s[0] * edge1[2],
        s[0] * edge1[1] - s[1] * edge1[0],
    )
    v = f * (direction[0] * q[0] + direction[1] * q[1] + direction[2] * q[2])
    if v < 0.0 or u + v > 1.0:
        return None
    t = f * (edge2[0] * q[0] + edge2[1] * q[1] + edge2[2] * q[2])
    return t if t > eps else None
