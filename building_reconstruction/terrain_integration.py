"""
Terrain Integration
====================

ROADMAP_V5 M2.4 - "Arazi/zemin entegrasyonu (terrain_engine ile birleşim)":

- Bina tabanının arazi eğimine göre "oturması" (foundation stepping /
  eğimli taban).
- Bahçe duvarı, istinat duvarı (retaining wall) - eğimli arsalarda bina
  çevresinin gerçekçi tamamlanması.
- Bina-arazi kesişim hatası kontrolü (bina gövdesinin zemin altında kalan
  kısımlarının otomatik tespiti/uyarısı).

Bu modül `terrain_engine.HeightmapGrid` (gerçek DEM verisi) ile
`building_reconstruction` (bina üretimi) arasında bağımsız, opt-in bir
köprü katmanıdır - `ProceduralBuildingGenerator.generate` bu modülü
bilmeden de eskisi gibi çalışmaya devam eder (mevcut mimari korunur);
kullanan taraf yalnızca `heightmap` verildiğinde devreye girer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core_engine.geometry_engine import Point2D, Polygon
from ..mesh_engine import Mesh3D, MeshBuilder, NormalGenerator, Vertex3D
from ..terrain_engine import HeightmapGrid


@dataclass(slots=True)
class TerrainIntersectionReport:
    """M2.4 kabul kriteri: "bina-arazi kesişim hatası kontrolü" - bina
    tabanının zemin altında kalan kısımlarının otomatik tespiti/uyarısı.
    Bu bir "hata" değil, bir *rapordur* (gizlenmez, kullanıcıya gösterilir).
    """

    min_ground_z: float
    max_ground_z: float
    building_base_z: float
    is_embedded: bool  # bina tabanı en yüksek zemin noktasının altında mı
    is_floating: bool  # bina tabanı en düşük zemin noktasından belirgin yukarıda mı
    max_gap_m: float  # taban ile zemin arasındaki en büyük düşey boşluk


class TerrainFoundationGenerator:
    """Bina footprint köşelerindeki gerçek zemin kotunu `HeightmapGrid`'den
    okuyup, bina tabanı (`building_base_z`, genelde 0.0 ya da AI/OSM'den
    gelen bir zemin kotu) ile zemin arasındaki boşluğu kapatan bir temel/
    kaide (foundation skirt) mesh'i üretir."""

    @staticmethod
    def sample_ground_elevations(polygon: Polygon, heightmap: HeightmapGrid) -> list[float]:
        ring = polygon.closed_ring()[:-1]
        return [heightmap.sample_bilinear(p.x, p.y) for p in ring]

    @staticmethod
    def analyze_intersection(
        polygon: Polygon,
        building_base_z: float,
        heightmap: HeightmapGrid,
        floating_tolerance_m: float = 0.15,
    ) -> TerrainIntersectionReport:
        elevations = TerrainFoundationGenerator.sample_ground_elevations(polygon, heightmap)
        min_z, max_z = min(elevations), max(elevations)
        gaps = [building_base_z - e for e in elevations]  # pozitif: zemin taban altında
        max_gap = max(gaps) if gaps else 0.0
        return TerrainIntersectionReport(
            min_ground_z=min_z,
            max_ground_z=max_z,
            building_base_z=building_base_z,
            is_embedded=building_base_z < max_z,
            is_floating=building_base_z - min_z > floating_tolerance_m and building_base_z >= max_z,
            max_gap_m=max_gap,
        )

    @staticmethod
    def foundation_skirt_mesh(
        polygon: Polygon,
        building_base_z: float,
        heightmap: HeightmapGrid,
        name: str = "foundation_skirt",
    ) -> Mesh3D:
        """Bina tabanı (`building_base_z`, sabit düz kat) ile eğimli gerçek
        zemin arasındaki her köşedeki düşey boşluğu, o kenar boyunca
        kademeli bir "kaide" (foundation skirt) yüzeyiyle kapatır. Eğimli
        arsalarda binanın havada asılı görünmesini (`is_floating`) ya da
        zemine gömülü görünmesini (`is_embedded`) engelleyen, roadmap'in
        "bina tabanının arazi eğimine göre oturması" maddesinin geometrik
        karşılığıdır."""
        ring = polygon.closed_ring()[:-1]
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))
        ground_elevations = [heightmap.sample_bilinear(p.x, p.y) for p in ring]

        n = len(ring)
        top_verts = [Vertex3D(p.x, p.y, building_base_z) for p in ring]
        bottom_verts = [Vertex3D(p.x, p.y, ground_elevations[i]) for i, p in enumerate(ring)]
        vertices = top_verts + bottom_verts
        triangles = []
        for i in range(n):
            i2 = (i + 1) % n
            a, b = i, i2
            c, d = i + n, i2 + n
            # Dışa bakan düşey kaide yüzeyi (bina tabanından zemine iner)
            triangles.append((a, b, d))
            triangles.append((a, d, c))
        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh


class RetainingWallGenerator:
    """M2.4: "İstinat duvarı (retaining wall) - eğimli arsalarda bina
    çevresinin gerçekçi tamamlanması." Footprint'in `wall_offset_m` kadar
    dışında, en yüksek ve en düşük zemin kotu arasındaki farkı (eğim
    yüksekliğini) kapatan basit bir düşey duvar bandı üretir - gerçek bir
    istinat duvarı statik hesabı değil, görsel/hacimsel bir tamamlama."""

    @staticmethod
    def generate(
        polygon: Polygon,
        heightmap: HeightmapGrid,
        wall_offset_m: float = 1.5,
        wall_thickness: float = 0.3,
        name: str = "retaining_wall",
    ) -> Mesh3D | None:
        ring = polygon.closed_ring()[:-1]
        cx = sum(p.x for p in ring) / len(ring)
        cy = sum(p.y for p in ring) / len(ring)
        outer_ring = []
        for p in ring:
            dx, dy = p.x - cx, p.y - cy
            dist = math.hypot(dx, dy) or 1e-6
            outer_ring.append(
                Point2D(p.x + dx / dist * wall_offset_m, p.y + dy / dist * wall_offset_m)
            )

        elevations = [heightmap.sample_bilinear(p.x, p.y) for p in outer_ring]
        min_z, max_z = min(elevations), max(elevations)
        slope_height = max_z - min_z
        if slope_height < 0.3:
            return None  # eğim ihmal edilebilir seviyede - istinat duvarına gerek yok

        outer_poly = Polygon(outer_ring)
        return MeshBuilder.extrude_polygon(outer_poly, min_z, slope_height, name=name)
