"""
Performance Engine - Culling & LOD
===================================

Roadmap Phase 13 - "Frustum culling", "Occlusion culling", "LOD sistemi",
"Spatial partitioning".

`FrustumCulling`: kameranın görüş konisi (view frustum) dışındaki nesneleri
eler. Tam düzlem-tabanlı (6-plane) bir frustum yerine, roadmap'in diğer
"approximate" simülasyonlarıyla (Wind/CFD, Heat Island...) aynı dürüstlük
ilkesiyle **açı-tabanlı bir yaklaşıklık** kullanılır: bir AABB'nin 8
köşesinden en az biri kameranın FOV konisi ve near/far aralığı içindeyse
kutu "görünür" sayılır. Bu, gerçek bir GPU'nun 6-plane frustum testinden
daha ucuz ama daha az kesin bir testtir; büyük kutular kamerayı çevrelediği
ama hiçbir köşesi koni içinde olmadığı nadir durumlarda yanlış-negatif
verebilir. Bu sınırlama yorum satırında ve README'de açıkça belirtilir.

`OcclusionCulling`: basit bir "occluder listesi + ray-cast" yaklaşımı -
kamera ile hedef AABB merkezi arasındaki ışın, listedeki başka bir AABB'yi
kesiyorsa hedef "gizli" sayılır (tam bir HZB/portal sistemi değil, roadmap
seviyesinde yeterli bir yaklaşıklık).

`LODManager`: mesafeye göre LOD seviyesi seçer (Phase 2 Terrain LOD ile
aynı desen, genel nesnelere uygulanabilir hale getirilmiş).

`SpatialPartitioning`: Phase 10 `data_engine.spatial_index` ağaçlarının
(QuadTree/Octree/BVH/RTree) ince bir cephe (facade) sarmalayıcısı - bu
fazın "spatial partitioning" ihtiyacını, tekrar kod yazmadan, Phase 10
üzerine kurarak karşılar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..data_engine.spatial_index import AABB3D, Octree
from ..visualization.camera_rig import Camera

Vec3 = tuple[float, float, float]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _length(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _cross_up(camera: Camera) -> Vec3:
    # Camera.right = normalize(forward x up); gercek "ekran yukarisi"
    # right x forward ile yeniden hesaplanir (ortonormal taban).
    f = camera.forward()
    r = camera.right()
    return (
        r[1] * f[2] - r[2] * f[1],
        r[2] * f[0] - r[0] * f[2],
        r[0] * f[1] - r[1] * f[0],
    )


def _aabb_corners(box: AABB3D) -> list[Vec3]:
    xs = (box.min_x, box.max_x)
    ys = (box.min_y, box.max_y)
    zs = (box.min_z, box.max_z)
    return [(x, y, z) for x in xs for y in ys for z in zs]


class FrustumCulling:
    """Kamera view-frustum'una gore AABB gorunurluk testi (bkz. modul
    docstring'i - aci-tabanli yaklasiklik)."""

    def __init__(self, aspect: float = 16 / 9, near: float = 0.1, far: float = 1000.0) -> None:
        self.aspect = aspect
        self.near = near
        self.far = far

    def is_visible(self, camera: Camera, aabb: AABB3D) -> bool:
        forward = camera.forward()
        right = camera.right()
        up = _cross_up(camera)
        half_v = math.tan(math.radians(camera.fov_deg / 2.0))
        for corner in _aabb_corners(aabb):
            rel = _sub(corner, camera.position)
            z = _dot(rel, forward)
            if z < self.near or z > self.far:
                continue
            x = _dot(rel, right)
            y = _dot(rel, up)
            allowed_v = half_v * z
            allowed_h = allowed_v * self.aspect
            if abs(x) <= allowed_h and abs(y) <= allowed_v:
                return True
        return False

    def cull(self, camera: Camera, boxes: dict[str, AABB3D]) -> list[str]:
        """Gorunur kalan (elenmeyen) obje kimliklerini dondurur."""
        return [key for key, box in boxes.items() if self.is_visible(camera, box)]


class OcclusionCulling:
    """Basit occluder-listesi tabanli gorunurluk testi: kamera->hedef
    isini baska bir occluder AABB'sini kesiyorsa hedef gizlenmis sayilir."""

    def __init__(self, occluders: list[AABB3D] | None = None) -> None:
        self.occluders = occluders or []

    def is_occluded(self, camera_position: Vec3, target_center: Vec3, exclude: AABB3D | None = None) -> bool:
        direction = _sub(target_center, camera_position)
        dist = _length(direction)
        if dist < 1e-9:
            return False
        inv_dir = tuple(1.0 / d if abs(d) > 1e-12 else math.inf for d in direction)
        for occluder in self.occluders:
            if exclude is not None and occluder is exclude:
                continue
            if occluder.contains_point(*target_center):
                continue  # hedefin kendi kutusu sayilmaz
            if occluder.intersects_ray(camera_position, inv_dir):
                center = occluder.center()
                occ_dist = _length(_sub(center, camera_position))
                if occ_dist < dist:
                    return True
        return False


@dataclass(slots=True)
class LODLevel:
    max_distance: float
    mesh_key: str


class LODManager:
    """Mesafeye gore LOD seviyesi secimi (Phase 2 Terrain LOD deseninin
    genellemesi). Seviyeler `max_distance`'a gore artan sirada tutulur;
    mesafe son seviyenin `max_distance`'ini asarsa en dusuk detay
    (son seviye) dondurulur."""

    def __init__(self, levels: list[LODLevel]) -> None:
        self.levels = sorted(levels, key=lambda lv: lv.max_distance)
        if not self.levels:
            raise ValueError("LODManager: en az bir LOD seviyesi gerekli")

    def select(self, distance: float) -> LODLevel:
        for level in self.levels:
            if distance <= level.max_distance:
                return level
        return self.levels[-1]

    def select_mesh_key(self, camera_position: Vec3, object_position: Vec3) -> str:
        distance = _length(_sub(object_position, camera_position))
        return self.select(distance).mesh_key


class SpatialPartitioning:
    """Phase 10 `Octree` uzerine ince cephe: sahne nesnelerini bina/tile
    anahtarlariyla ekleyip AABB sorgusu ile geri alma."""

    def __init__(self, world_bounds: AABB3D, capacity: int = 8, max_depth: int = 8) -> None:
        self._tree: Octree[str] = Octree(world_bounds, capacity=capacity, max_depth=max_depth)

    def insert(self, key: str, bounds: AABB3D) -> None:
        self._tree.insert(key, bounds)

    def query(self, region: AABB3D) -> list[str]:
        return list(self._tree.query(region))
