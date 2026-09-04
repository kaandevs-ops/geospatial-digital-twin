"""
AI Environment Generator
==========================

Roadmap Phase 4 - "AIEnvironmentGenerator".

Eksik çevre objelerini (Ağaç, Park, Yol, Otopark, Bahçe, Solar Panel,
Elektrik Direği, Çöp Kutusu, Bank, Lamba, Çit) footprint çevresindeki boş
alana yerleştirir (Poisson-disk sampling ile doğal dağılım).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum

from ..core_engine.geometry_engine import GeometryEngine, Point2D, Polygon


class EnvironmentObjectType(str, Enum):
    AGAC = "agac"
    PARK = "park"
    YOL = "yol"
    OTOPARK = "otopark"
    BAHCE = "bahce"
    SOLAR_PANEL = "solar_panel"
    ELEKTRIK_DIREGI = "elektrik_diregi"
    COP_KUTUSU = "cop_kutusu"
    BANK = "bank"
    LAMBA = "lamba"
    CIT = "cit"


@dataclass(slots=True)
class EnvironmentObject:
    object_type: EnvironmentObjectType
    position: Point2D
    rotation_deg: float = 0.0
    scale: float = 1.0


# Her obje tipi için minimum ayrım mesafesi (Poisson-disk yarıçapı, metre)
# ve göreli üretim ağırlığı (bahçe alanında ağaç yoğun, elektrik direği
# seyrek olmalı vb.).
_OBJECT_PROFILES: dict[EnvironmentObjectType, dict] = {
    EnvironmentObjectType.AGAC: dict(min_dist=3.0, weight=4.0),
    EnvironmentObjectType.BAHCE: dict(min_dist=4.0, weight=1.5),
    EnvironmentObjectType.LAMBA: dict(min_dist=6.0, weight=1.5),
    EnvironmentObjectType.BANK: dict(min_dist=5.0, weight=1.0),
    EnvironmentObjectType.COP_KUTUSU: dict(min_dist=8.0, weight=0.8),
    EnvironmentObjectType.ELEKTRIK_DIREGI: dict(min_dist=15.0, weight=0.3),
    EnvironmentObjectType.CIT: dict(min_dist=2.0, weight=0.5),
}


class AIEnvironmentGenerator:
    """Footprint etrafındaki boşluğa Poisson-disk sampling (Bridson
    algoritması, ızgara hızlandırmalı) ile çevre objeleri dağıtır."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def generate(
        self,
        footprint_polygon: Polygon,
        margin_m: float = 15.0,
        object_types: list[EnvironmentObjectType] | None = None,
        min_setback_m: float = 1.5,
    ) -> list[EnvironmentObject]:
        """`footprint_polygon` çevresindeki (margin_m genişliğinde) boş
        alana, footprint'in `min_setback_m` içine girmeyecek şekilde,
        Poisson-disk dağılımlı çevre objeleri yerleştirir."""
        object_types = object_types or list(_OBJECT_PROFILES.keys())
        xs = [p.x for p in footprint_polygon.points]
        ys = [p.y for p in footprint_polygon.points]
        bounds = (min(xs) - margin_m, min(ys) - margin_m, max(xs) + margin_m, max(ys) + margin_m)

        objects: list[EnvironmentObject] = []
        for obj_type in object_types:
            profile = _OBJECT_PROFILES.get(obj_type, dict(min_dist=5.0, weight=1.0))
            count_target = max(1, int(profile["weight"] * (margin_m / 10.0) * 3))
            points = self._poisson_disk_sample(
                bounds,
                profile["min_dist"],
                max_points=count_target,
            )
            for p in points:
                if self._is_valid_placement(p, footprint_polygon, min_setback_m):
                    objects.append(
                        EnvironmentObject(
                            object_type=obj_type,
                            position=p,
                            rotation_deg=self._rng.uniform(0, 360),
                            scale=self._rng.uniform(0.85, 1.15),
                        )
                    )
        return objects

    def _is_valid_placement(self, point: Point2D, polygon: Polygon, min_setback_m: float) -> bool:
        if GeometryEngine.point_in_polygon(point, polygon):
            return False
        ring = polygon.closed_ring()
        min_edge_dist = min(
            self._point_segment_distance(point, a, b) for a, b in zip(ring, ring[1:])
        )
        return min_edge_dist >= min_setback_m

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

    # ------------------------------------------------------------------ #
    # Poisson-disk sampling (Bridson'ın algoritması, basitleştirilmiş)
    # ------------------------------------------------------------------ #
    def _poisson_disk_sample(
        self,
        bounds: tuple[float, float, float, float],
        min_dist: float,
        max_points: int,
        k_attempts: int = 20,
    ) -> list[Point2D]:
        min_x, min_y, max_x, max_y = bounds
        width, height = max_x - min_x, max_y - min_y
        if width <= 0 or height <= 0:
            return []

        cell_size = min_dist / math.sqrt(2)
        grid_w = max(1, int(width / cell_size) + 1)
        grid_h = max(1, int(height / cell_size) + 1)
        grid: dict[tuple[int, int], Point2D] = {}

        def grid_index(p: Point2D) -> tuple[int, int]:
            return (int((p.x - min_x) / cell_size), int((p.y - min_y) / cell_size))

        def fits(p: Point2D) -> bool:
            gx, gy = grid_index(p)
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    neighbor = grid.get((gx + dx, gy + dy))
                    if neighbor is not None and p.distance_to(neighbor) < min_dist:
                        return False
            return True

        first = Point2D(self._rng.uniform(min_x, max_x), self._rng.uniform(min_y, max_y))
        points = [first]
        grid[grid_index(first)] = first
        active = [first]

        while active and len(points) < max_points:
            idx = self._rng.randrange(len(active))
            origin = active[idx]
            placed = False
            for _ in range(k_attempts):
                angle = self._rng.uniform(0, 2 * math.pi)
                radius = self._rng.uniform(min_dist, 2 * min_dist)
                candidate = Point2D(
                    origin.x + math.cos(angle) * radius,
                    origin.y + math.sin(angle) * radius,
                )
                if not (min_x <= candidate.x <= max_x and min_y <= candidate.y <= max_y):
                    continue
                if fits(candidate):
                    points.append(candidate)
                    grid[grid_index(candidate)] = candidate
                    active.append(candidate)
                    placed = True
                    if len(points) >= max_points:
                        break
            if not placed:
                active.pop(idx)

        return points
