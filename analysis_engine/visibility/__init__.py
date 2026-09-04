"""
Visibility
==========

Roadmap Phase 6 - "Visibility": Ray Casting, LOS, Shadow, Blind Spot,
Visibility Heatmap.

`RayCasting` burada bağımsız bir kopya olarak tanımlanır (Phase 2 `lighting`
modülündeki Möller-Trumbore ile aynı algoritma, fakat Phase 6 kendi
alt-sistemi olarak tekrar kullanılabilir/bağımsız test edilebilir olsun diye
kopyalanmıştır - roadmap'in "her modül bağımsız test edilebilir" ilkesi).
`ShadowAnalysis`, tek doğruluk kaynağı ilkesi gereği Phase 2 `lighting`
modülündeki `SolarPositionCalculator`/`ShadowCalculator`'ı kullanır.

Roadmap V3 - Faz D3 ("Analysis Engine: BVH-Hızlandırmalı Visibility"):
`RayCasting.cast`/`cast_any`, `LineOfSight.check`, `BlindSpotAnalysis.scan`
ve `VisibilityHeatmap.compute` artık isteğe bağlı olarak `data_engine.
spatial_index.BVH` (Phase 10) kullanabiliyor. Eski imzalar **korunur**
(geriye uyumlu, `bvh`/`bvhs` parametresi verilmezse eski doğrusal tarama
davranışı aynen çalışır); `BlindSpotAnalysis.scan` ve `VisibilityHeatmap.
compute` gibi *aynı engelleyici kümesi üzerinde çok sayıda ray attıran*
fonksiyonlar, BVH açıkça verilmediğinde bile **kendi içlerinde bir kez**
BVH inşa edip tüm ray'ler için yeniden kullanır - roadmap A6 kabul
kriterinin ("10.000 binalık sahnede visibility sorgusu <100ms") hedeflediği
asıl kazanım budur: n ray x m üçgen doğrusal taramasından, ray başına
O(log m) BVH gezintisine geçiş.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from ...core_engine.coordinate_systems import GeoPoint
from ...data_engine.spatial_index import AABB3D, BVH, Octree
from ...lighting import ShadowCalculator, SolarPositionCalculator, SunLight
from ...mesh_engine import Mesh3D

Vec3 = tuple[float, float, float]


# ============================================================================ #
# Ray Casting
# ============================================================================ #


@dataclass(slots=True)
class RayHit:
    distance: float
    point: Vec3
    triangle_index: int


class RayCasting:
    """Roadmap: 'Ray Casting'. Möller-Trumbore ray-triangle intersection.

    Roadmap V3 / Faz D3: `bvh`/`bvhs` parametresi verilirse, üçgenler
    üzerinde doğrusal tarama yerine `data_engine.spatial_index.BVH`
    gezintisi kullanılır (O(n) -> O(log n)'e yakın). Parametre verilmezse
    davranış birebir eskisiyle aynıdır (geriye uyumluluk)."""

    @staticmethod
    def build_bvh(mesh: Mesh3D) -> BVH:
        """Bir mesh için yeniden kullanılabilir bir BVH inşa eder. Aynı mesh
        üzerinde çok sayıda ray atılacaksa (LOS taraması, heatmap, blind-spot
        taraması gibi), BVH'yi bir kez inşa edip tüm sorgularda yeniden
        kullanmak, her ray için mesh'i baştan taramaktan çok daha hızlıdır."""
        return BVH(mesh)

    @staticmethod
    def cast(
        origin: Vec3,
        direction: Vec3,
        mesh: Mesh3D,
        max_distance: float = 1e6,
        bvh: BVH | None = None,
    ) -> RayHit | None:
        length = math.sqrt(sum(c * c for c in direction))
        if length < 1e-12:
            return None
        d = tuple(c / length for c in direction)

        if bvh is not None:
            hit = bvh.intersect_ray(origin, d)
            if hit is None or not (1e-6 < hit.t <= max_distance):
                return None
            return RayHit(distance=hit.t, point=hit.point, triangle_index=hit.triangle_index)

        best: RayHit | None = None
        for idx, tri in enumerate(mesh.triangles):
            a, b, c = mesh.triangle_positions(tri)
            t = _ray_triangle(origin, d, a.as_tuple(), b.as_tuple(), c.as_tuple())
            if t is not None and 1e-6 < t <= max_distance:
                if best is None or t < best.distance:
                    point = tuple(origin[i] + d[i] * t for i in range(3))
                    best = RayHit(distance=t, point=point, triangle_index=idx)
        return best

    @staticmethod
    def cast_any(
        origin: Vec3,
        direction: Vec3,
        meshes: list[Mesh3D],
        max_distance: float = 1e6,
        bvhs: list[BVH | None] | None = None,
    ) -> RayHit | None:
        best: RayHit | None = None
        for i, mesh in enumerate(meshes):
            bvh = bvhs[i] if bvhs is not None else None
            hit = RayCasting.cast(origin, direction, mesh, max_distance, bvh=bvh)
            if hit is not None and (best is None or hit.distance < best.distance):
                best = hit
        return best


# ============================================================================ #
# Line of Sight
# ============================================================================ #


@dataclass(slots=True)
class LOSResult:
    visible: bool
    distance: float
    blocked_at: Vec3 | None = None
    blocking_triangle: int | None = None


class LineOfSight:
    """Roadmap: 'LOS (Line-of-Sight)'.

    Roadmap V3 / Faz D3: `bvhs` parametresi (occluders ile aynı sırada bir
    `BVH` listesi) verilirse, `RayCasting.cast_any` doğrusal tarama yerine
    BVH gezintisi kullanır. Aynı `occluders` kümesiyle çok sayıda `check()`
    çağıracak çağıranlar (örn. `VisibilityHeatmap`), BVH'leri bir kez inşa
    edip burada yeniden kullanmalıdır."""

    @staticmethod
    def check(
        observer: Vec3, target: Vec3, occluders: list[Mesh3D], bvhs: list[BVH] | None = None
    ) -> LOSResult:
        direction = tuple(target[i] - observer[i] for i in range(3))
        total_distance = math.sqrt(sum(c * c for c in direction))
        if total_distance < 1e-9:
            return LOSResult(visible=True, distance=0.0)
        hit = RayCasting.cast_any(
            observer, direction, occluders, max_distance=total_distance - 1e-4, bvhs=bvhs
        )
        if hit is None:
            return LOSResult(visible=True, distance=total_distance)
        return LOSResult(
            visible=False,
            distance=total_distance,
            blocked_at=hit.point,
            blocking_triangle=hit.triangle_index,
        )


# ============================================================================ #
# Shadow Analysis (Phase 2 lighting üzerine)
# ============================================================================ #


@dataclass(slots=True)
class ShadowAnalysisResult:
    point: Vec3
    in_shadow: bool
    sun_elevation_deg: float


class ShadowAnalysis:
    """Roadmap: 'Shadow'. `lighting.ShadowCalculator` + `SolarPositionCalculator`
    üzerine, belirli bir zamanda bir noktanın gölgede olup olmadığını
    değerlendiren ince katman."""

    @staticmethod
    def evaluate(
        point: Vec3,
        location: GeoPoint,
        when_utc: datetime,
        occluders: list[Mesh3D],
        max_distance: float = 500.0,
    ) -> ShadowAnalysisResult:
        sun = SunLight.at(location, when_utc)
        if not sun.position.is_daylight:
            return ShadowAnalysisResult(
                point=point, in_shadow=True, sun_elevation_deg=sun.position.elevation_deg
            )
        in_shadow = ShadowCalculator.point_in_shadow(point, sun, occluders, max_distance)
        return ShadowAnalysisResult(
            point=point, in_shadow=in_shadow, sun_elevation_deg=sun.position.elevation_deg
        )

    @staticmethod
    def daily_shadow_hours(
        point: Vec3,
        location: GeoPoint,
        date: datetime,
        occluders: list[Mesh3D],
        step_minutes: int = 30,
    ) -> float:
        """Bir günde noktanın kaç saat gölgede kaldığını örnekleyerek tahmin eder."""
        shadow_samples = 0
        total_daylight_samples = 0
        steps_per_day = (24 * 60) // step_minutes
        for i in range(steps_per_day):
            minutes = i * step_minutes
            when = date.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
            result = ShadowAnalysis.evaluate(point, location, when, occluders)
            if result.sun_elevation_deg > 0:
                total_daylight_samples += 1
                if result.in_shadow:
                    shadow_samples += 1
        if total_daylight_samples == 0:
            return 0.0
        fraction = shadow_samples / total_daylight_samples
        daylight_hours = total_daylight_samples * step_minutes / 60.0
        return fraction * daylight_hours


# ============================================================================ #
# Blind Spot Analysis
# ============================================================================ #


@dataclass(slots=True)
class BlindSpot:
    direction_deg: float
    max_visible_distance: float


class BlindSpotAnalysis:
    """Roadmap: 'Blind Spot'. Bir gözlem noktasından, çevredeki engelleyici
    mesh'ler nedeniyle görüşün ne kadar mesafede kesildiğini yön (azimuth)
    bazında tarar."""

    @staticmethod
    def scan(
        observer: Vec3,
        occluders: list[Mesh3D],
        scan_radius: float = 200.0,
        angle_step_deg: float = 5.0,
        bvhs: list[BVH] | None = None,
    ) -> list[BlindSpot]:
        """Roadmap V3 / Faz D3: aynı `occluders` kümesi üzerinde (varsayılan
        72 açı adımı x N engelleyici) çok sayıda ray atıldığından, `bvhs`
        verilmezse fonksiyon kendi içinde **bir kez** BVH inşa edip taramanın
        tamamında yeniden kullanır - böylece her açı adımında tüm mesh'leri
        baştan taramak yerine, ray başına O(log n) BVH gezintisi yapılır."""
        if bvhs is None and occluders:
            bvhs = [RayCasting.build_bvh(m) for m in occluders]

        results: list[BlindSpot] = []
        steps = int(360 / angle_step_deg)
        for i in range(steps):
            angle_deg = i * angle_step_deg
            rad = math.radians(angle_deg)
            direction = (math.sin(rad), math.cos(rad), 0.0)
            hit = RayCasting.cast_any(
                observer, direction, occluders, max_distance=scan_radius, bvhs=bvhs
            )
            visible_distance = hit.distance if hit is not None else scan_radius
            if visible_distance < scan_radius:
                results.append(
                    BlindSpot(direction_deg=angle_deg, max_visible_distance=visible_distance)
                )
        return results


# ============================================================================ #
# Visibility Heatmap
# ============================================================================ #


@dataclass(slots=True)
class VisibilityHeatmapCell:
    x: float
    y: float
    visible_observer_count: int


class VisibilityHeatmap:
    """Roadmap: 'Visibility Heatmap'. Grid örnekleme + her hücre için,
    verilen gözlemci noktalarından kaçının o hücreyi görebildiğini sayar."""

    @staticmethod
    def compute(
        observers: list[Vec3],
        grid_origin: tuple[float, float],
        grid_width: int,
        grid_height: int,
        cell_size: float,
        z: float,
        occluders: list[Mesh3D],
        bvhs: list[BVH] | None = None,
    ) -> list[VisibilityHeatmapCell]:
        """Roadmap V3 / Faz D3: `grid_width * grid_height * len(observers)`
        adet LOS sorgusu aynı `occluders` kümesini kullandığından, BVH'ler
        (verilmemişse) bir kez inşa edilip tüm hücre/gözlemci kombinasyonları
        için yeniden kullanılır."""
        if bvhs is None and occluders:
            bvhs = [RayCasting.build_bvh(m) for m in occluders]

        cells: list[VisibilityHeatmapCell] = []
        ox, oy = grid_origin
        for row in range(grid_height):
            for col in range(grid_width):
                cx = ox + col * cell_size
                cy = oy + row * cell_size
                target = (cx, cy, z)
                count = 0
                for obs in observers:
                    if LineOfSight.check(obs, target, occluders, bvhs=bvhs).visible:
                        count += 1
                cells.append(VisibilityHeatmapCell(x=cx, y=cy, visible_observer_count=count))
        return cells


# ============================================================================ #
# Sahne Görünürlük İndeksi (broad-phase + narrow-phase BVH)
# ============================================================================ #


class SceneVisibilityIndex:
    """Roadmap V3 / Faz D3 - A6 kabul kriteri: "10.000 binalık sahnede
    visibility sorgusu <100ms".

    `RayCasting.cast_any`/`LineOfSight.check`'e BVH vermek, ray başına
    üçgen taramasını O(triangles) -> O(log triangles)'a indirger, ama bina
    **sayısı** arttıkça (10.000 bina) yine de her ray için 10.000 BVH'nin
    hepsine bakmak gerekir - bu hâlâ O(bina_sayısı)'dır. Gerçek A6 kazanımı
    için iki aşamalı bir hızlandırma gerekir:

    1. **Broad-phase**: bina AABB'leri `data_engine.spatial_index.Octree`'ye
       eklenir. Bir ray/segment sorgusunda önce ray'in kapsadığı bölgenin
       (origin-target arası) AABB'siyle kesişen az sayıda bina **adayı**
       bulunur - çoğu bina bu aşamada elenir, hiç BVH testine girmez.
    2. **Narrow-phase**: yalnızca adaylar için, D3'ün BVH-hızlandırmalı
       `RayCasting.cast`'i (gerçek üçgen kesişimi) çalıştırılır.

    Not: broad-phase sorgusu ray'in **eksene-hizalı sınırlayıcı kutusunu**
    kullanır (Octree.query yalnızca AABB-aralık sorgusu destekliyor, tam
    ray-slab gezintisi değil); bu, gözlemci-hedef arası tipik LOS
    sorgularında (sonlu segment) etkili bir yaklaşıklamadır, tam sonsuz-ışın
    sorgularında daha az seçicidir - bu durumda narrow-phase BVH testi yine
    de doğruluğu garanti eder, yalnızca eleme oranı düşer.
    """

    def __init__(self, meshes: list[Mesh3D]) -> None:
        self.meshes = meshes
        self._bvhs: list[BVH] = [RayCasting.build_bvh(m) for m in meshes]
        mesh_bounds = [self._mesh_aabb(m) for m in meshes]

        if mesh_bounds:
            scene_bounds = mesh_bounds[0]
            for b in mesh_bounds[1:]:
                scene_bounds = scene_bounds.union(b)
        else:
            scene_bounds = AABB3D(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)

        self._octree = Octree(scene_bounds) if mesh_bounds else None
        if self._octree is not None:
            for idx, b in enumerate(mesh_bounds):
                self._octree.insert(idx, b)

    @staticmethod
    def _mesh_aabb(mesh: Mesh3D) -> AABB3D:
        min_c, max_c = mesh.bounding_box()
        return AABB3D(min_c[0], min_c[1], min_c[2], max_c[0], max_c[1], max_c[2])

    def _candidate_indices(self, origin: Vec3, target: Vec3, padding: float = 1.0) -> list[int]:
        if self._octree is None:
            return []
        lo = tuple(min(origin[i], target[i]) - padding for i in range(3))
        hi = tuple(max(origin[i], target[i]) + padding for i in range(3))
        segment_box = AABB3D(lo[0], lo[1], lo[2], hi[0], hi[1], hi[2])
        return self._octree.query(segment_box)

    def line_of_sight(self, observer: Vec3, target: Vec3) -> LOSResult:
        """`LineOfSight.check` ile aynı sonucu üretir, fakat yalnızca
        broad-phase Octree'nin elemediği aday binalar narrow-phase BVH ile
        test edilir - 10.000 binalık bir sahnede tipik sorgular yalnızca
        birkaç düzine adayla sonuçlanır."""
        candidates = self._candidate_indices(observer, target)
        if not candidates:
            direction = tuple(target[i] - observer[i] for i in range(3))
            total_distance = math.sqrt(sum(c * c for c in direction))
            return LOSResult(visible=True, distance=total_distance)
        occluders = [self.meshes[i] for i in candidates]
        bvhs = [self._bvhs[i] for i in candidates]
        return LineOfSight.check(observer, target, occluders, bvhs=bvhs)

    def candidate_count(self, observer: Vec3, target: Vec3) -> int:
        """Test/benchmark amaçlı: broad-phase sonrası kaç binanın narrow-phase
        BVH testine girdiğini döndürür (toplam bina sayısından çok daha az
        olması beklenir)."""
        return len(self._candidate_indices(observer, target))


def _ray_triangle(origin: Vec3, direction: Vec3, v0: Vec3, v1: Vec3, v2: Vec3) -> float | None:
    """Möller-Trumbore. Bkz. modül docstring'i - `lighting`'deki kopyayla aynı."""
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
