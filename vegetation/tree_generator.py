"""
vegetation.tree_generator - Prosedürel Düşük-Poli Ağaç Üretimi
================================================================

Roadmap V4 - Faz E18. Basit bir L-sistem yerine (roadmap "basit L-sistem
**veya** prosedürel budaklanma kuralları" diyor - "veya" ile ikisinden
biri yeterli), tam bir dallanma-iskeleti simülasyonu yerine kasıtlı olarak
daha basit ama üretime uygun bir yaklaşım seçildi: gövde (trunk) + taç
(canopy) iki ayrı prosedürel primitiften, mevcut `mesh_engine` altyapısı
(`MeshBuilder.extrude_polygon`, `MeshMerger`, `NormalGenerator`) yeniden
kullanılarak inşa edilir. Bu, roadmap'in kendi ilkesiyle (stdlib-only,
mevcut mimariyi koru) birebir uyumludur ve düşük-poli / gerçek-zamanlı
sahne kullanımı için yeterli görsel çeşitliliği (tür başına farklı
taç şekli) sağlar.

Tür başına taç şekli:
  - CONIFER   : tek sivri koni (çam/ladin silüeti)
  - DECIDUOUS : üst üste 2 basık koni (yuvarlak/geniş taç yaklaşıklaması)
  - SHRUB     : tek basık/geniş koni, gövdesiz (çalı)
  - GENERIC   : DECIDUOUS ile aynı (varsayılan)

Roadmap V8 Faz 5.2 ile eklenen 6 yeni tür (Türkiye/Akdeniz iklimi için
görsel olarak belirgin şekilde farklı siluetler):
  - PALM      : ince, hafif eğik tek gövde + tepede ışınsal yelpaze
                yaprak seti (koni değil, düz üçgen "palmiye yaprağı"
                primitiflerinin gövde tepesinden ışınsal dizilimi).
  - CYPRESS   : çok dar/dikey, ince uzun tek koni (sütun silüeti,
                CONIFER'dan çok daha dar açı).
  - OLIVE     : kısa/kalın gövde + düşük, geniş/basık tek koni taç
                (zeytin ağacının alçak, yayvan görünümüne yaklaşım).
  - PINE      : CONIFER'dan ayrıştırılmış, gövde üzerinde 2-3 katmanlı
                (üst üste konili) daha "şişman" çam silüeti.
  - OAK       : kalın gövdeli, DECIDUOUS'tan daha geniş/yuvarlak tek
                büyük taç (meşenin masif görünümü).
  - PLANE     : yüksek gövde + çok geniş yayılan, basık-yassı taç
                (çınarın karakteristik geniş gölgelik siluet).
"""
from __future__ import annotations

import math
import random

from ..core_engine.geometry_engine import Point2D, Polygon
from ..mesh_engine import Mesh3D, MeshBuilder, MeshMerger, NormalGenerator, Vertex3D
from .types import TreeSpecies

_TRUNK_SEGMENTS = 6
_CANOPY_SEGMENTS = 8


def _regular_polygon(radius: float, segments: int) -> Polygon:
    points = []
    for i in range(segments):
        angle = 2.0 * math.pi * i / segments
        points.append(Point2D(radius * math.cos(angle), radius * math.sin(angle)))
    return Polygon(points)


def _cone_mesh(base_radius: float, base_z: float, apex_height: float, segments: int, name: str) -> Mesh3D:
    """Taban çemberi (poligon olarak) + tek bir tepe noktasına (apex)
    birleşen üçgenlerden oluşan basit bir koni. Taban kapalıdır (alt yüz
    üçgenlenir) böylece mesh manifold kalır."""
    ring = _regular_polygon(base_radius, segments).closed_ring()[:-1]
    if not Polygon(ring).is_ccw():
        ring = list(reversed(ring))

    vertices: list[Vertex3D] = [Vertex3D(p.x, p.y, base_z) for p in ring]
    apex_index = len(vertices)
    vertices.append(Vertex3D(0.0, 0.0, base_z + apex_height))

    triangles = []
    n = segments
    for i in range(n):
        i2 = (i + 1) % n
        triangles.append((i, i2, apex_index))
    # taban (alt yüz, normal -Z için ters sıra) - basit fan (dışbükey düzenli
    # poligon olduğu için ear-clipping'e gerek yok)
    for i in range(1, n - 1):
        triangles.append((0, i + 1, i))

    mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
    NormalGenerator.compute_face_averaged_normals(mesh)
    return mesh


def _blade_mesh(
    length: float, width: float, tilt_deg: float, yaw_deg: float,
    base_x: float, base_y: float, base_z: float, name: str,
) -> Mesh3D:
    """İnce, düz bir yaprak/palmiye yaprağı yaklaşıklaması: tabanda dar,
    ucu sivrilen, çift yüzlü (iki yönden görünür - normal ters çift
    üçgen) yassı bir "bıçak" primitifi. Koniden farklı olarak dönme
    simetrisi yok, tek bir düzlemsel yönde uzanır - bu yüzden palmiye
    taçları koni-tabanlı türlerden görsel olarak belirgin şekilde ayrışır.
    """
    yaw = math.radians(yaw_deg)
    tilt = math.radians(tilt_deg)
    # Yerel eksen: uzunluk boyunca ilerleme yönü (dx, dy, dz), genişlik
    # ekseni buna dik ve yatay düzlemde.
    dx = math.cos(yaw) * math.cos(tilt)
    dy = math.sin(yaw) * math.cos(tilt)
    dz = math.sin(tilt)
    wx = -math.sin(yaw)
    wy = math.cos(yaw)

    tip_x = base_x + dx * length
    tip_y = base_y + dy * length
    tip_z = base_z + dz * length

    hw = width * 0.5
    v0 = Vertex3D(base_x + wx * hw, base_y + wy * hw, base_z)
    v1 = Vertex3D(base_x - wx * hw, base_y - wy * hw, base_z)
    v2 = Vertex3D(tip_x, tip_y, tip_z)

    vertices = [v0, v1, v2]
    # Çift yüzlü (iki taraftan da görünür olsun diye ters sıralı ikinci
    # üçgen) - tek katmanlı yassı geometrilerde arkadan bakınca kaybolma
    # sorununu engeller.
    triangles = [(0, 1, 2), (2, 1, 0)]
    mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
    NormalGenerator.compute_face_averaged_normals(mesh)
    return mesh


class TreeGenerator:
    """Roadmap V4 - Faz E18: `TreeGenerator.generate(...)` -> `Mesh3D`.

    Deterministiktir: aynı `(species, height, canopy_radius, seed)`
    girdisi her zaman aynı mesh'i üretir (regresyon testlerinin ve
    `VegetationInstance`'ın mesh'i saklamadan yeniden üretebilmesinin
    ön koşulu)."""

    @staticmethod
    def generate(
        species: TreeSpecies = TreeSpecies.GENERIC,
        height: float = 6.0,
        canopy_radius: float = 1.8,
        seed: int = 0,
        name: str = "tree",
    ) -> Mesh3D:
        if height <= 0:
            raise ValueError("height pozitif olmalı.")
        if canopy_radius <= 0:
            raise ValueError("canopy_radius pozitif olmalı.")

        rng = random.Random(seed)
        # Küçük, deterministik-tohumlu bir varyasyon (görsel çeşitlilik):
        # gerçek ağaçlar birebir aynı boyutta olmaz.
        jitter = 1.0 + (rng.random() - 0.5) * 0.12

        parts: list[Mesh3D] = []

        if species == TreeSpecies.SHRUB:
            canopy_h = height * jitter
            parts.append(
                _cone_mesh(canopy_radius * jitter, 0.0, canopy_h, _CANOPY_SEGMENTS, f"{name}_shrub_canopy")
            )
        elif species == TreeSpecies.PALM:
            # İnce, hafif eğik tek gövde (diğer türlerden daha uzun/ince
            # oranlı - palmiye gövdesinin karakteristik silindirik-ince
            # görünümü) + tepede ışınsal yelpaze yapraklar.
            trunk_h = height * 0.82 * jitter
            trunk_radius = max(0.06, canopy_radius * 0.09)
            trunk_polygon = _regular_polygon(trunk_radius, _TRUNK_SEGMENTS)
            trunk = MeshBuilder.extrude_polygon(trunk_polygon, 0.0, trunk_h, name=f"{name}_trunk")
            parts.append(trunk)
            frond_count = 8
            frond_len = canopy_radius * 1.8 * jitter
            for i in range(frond_count):
                yaw_deg = (360.0 / frond_count) * i + rng.uniform(-8.0, 8.0)
                tilt_deg = -22.0 + rng.uniform(-6.0, 6.0)  # hafif aşağı sarkan yapraklar
                parts.append(
                    _blade_mesh(
                        frond_len, canopy_radius * 0.35, tilt_deg, yaw_deg,
                        0.0, 0.0, trunk_h, f"{name}_frond_{i}",
                    )
                )
        elif species == TreeSpecies.CYPRESS:
            # Çok dar/dikey tek koni (CONIFER'a göre çok daha dar açı -
            # servi'nin karakteristik sütun/dikey silüeti).
            trunk_h = height * 0.12 * jitter
            trunk_radius = max(0.06, canopy_radius * 0.08)
            trunk_polygon = _regular_polygon(trunk_radius, _TRUNK_SEGMENTS)
            parts.append(MeshBuilder.extrude_polygon(trunk_polygon, 0.0, trunk_h, name=f"{name}_trunk"))
            narrow_radius = canopy_radius * 0.32 * jitter
            parts.append(
                _cone_mesh(narrow_radius, trunk_h * 0.5, height - trunk_h * 0.5, _CANOPY_SEGMENTS, f"{name}_canopy")
            )
        elif species == TreeSpecies.OLIVE:
            # Kısa/kalın gövde + düşük, geniş/basık tek taç (zeytin'in
            # alçak, yayvan görünümü).
            trunk_h = height * 0.32 * jitter
            trunk_radius = max(0.1, canopy_radius * 0.16)
            trunk_polygon = _regular_polygon(trunk_radius, _TRUNK_SEGMENTS)
            parts.append(MeshBuilder.extrude_polygon(trunk_polygon, 0.0, trunk_h, name=f"{name}_trunk"))
            canopy_base_z = trunk_h * 0.7
            wide_radius = canopy_radius * 1.35 * jitter
            parts.append(
                _cone_mesh(wide_radius, canopy_base_z, (height - canopy_base_z) * 0.85,
                           _CANOPY_SEGMENTS, f"{name}_canopy")
            )
        elif species == TreeSpecies.PINE:
            # CONIFER'dan ayrıştırılmış: gövde üzerinde 2-3 katmanlı,
            # daha "şişman" (geniş taban açılı) çam silüeti.
            trunk_h = height * 0.35 * jitter
            trunk_radius = max(0.08, canopy_radius * 0.11)
            trunk_polygon = _regular_polygon(trunk_radius, _TRUNK_SEGMENTS)
            parts.append(MeshBuilder.extrude_polygon(trunk_polygon, 0.0, trunk_h, name=f"{name}_trunk"))
            layers = 3
            remaining_h = height - trunk_h
            layer_h = remaining_h / layers
            for i in range(layers):
                layer_base_z = trunk_h + layer_h * i * 0.72
                layer_radius = canopy_radius * jitter * (1.0 - 0.22 * i)
                parts.append(
                    _cone_mesh(layer_radius, layer_base_z, layer_h, _CANOPY_SEGMENTS, f"{name}_pine_layer_{i}")
                )
        elif species == TreeSpecies.OAK:
            # Kalın gövdeli, DECIDUOUS'tan daha geniş/yuvarlak tek büyük
            # taç (meşenin masif görünümü - iki koni yerine tek, geniş).
            trunk_h = height * 0.45 * jitter
            trunk_radius = max(0.12, canopy_radius * 0.18)
            trunk_polygon = _regular_polygon(trunk_radius, _TRUNK_SEGMENTS)
            parts.append(MeshBuilder.extrude_polygon(trunk_polygon, 0.0, trunk_h, name=f"{name}_trunk"))
            canopy_base_z = trunk_h * 0.7
            wide_radius = canopy_radius * 1.5 * jitter
            parts.append(
                _cone_mesh(wide_radius, canopy_base_z, height - canopy_base_z, _CANOPY_SEGMENTS, f"{name}_canopy")
            )
        elif species == TreeSpecies.PLANE:
            # Yüksek gövde + çok geniş yayılan, basık-yassı taç
            # (çınarın karakteristik geniş gölgelik siluet).
            trunk_h = height * 0.6 * jitter
            trunk_radius = max(0.1, canopy_radius * 0.14)
            trunk_polygon = _regular_polygon(trunk_radius, _TRUNK_SEGMENTS)
            parts.append(MeshBuilder.extrude_polygon(trunk_polygon, 0.0, trunk_h, name=f"{name}_trunk"))
            canopy_base_z = trunk_h * 0.8
            wide_radius = canopy_radius * 2.0 * jitter
            flat_h = (height - canopy_base_z) * 0.55  # basık/yassı - düşük oranlı yükseklik
            parts.append(
                _cone_mesh(wide_radius, canopy_base_z, flat_h, _CANOPY_SEGMENTS, f"{name}_canopy")
            )
        else:
            trunk_h = height * 0.55 * jitter
            trunk_radius = max(0.08, canopy_radius * 0.10)
            trunk_polygon = _regular_polygon(trunk_radius, _TRUNK_SEGMENTS)
            trunk = MeshBuilder.extrude_polygon(trunk_polygon, 0.0, trunk_h, name=f"{name}_trunk")
            parts.append(trunk)

            canopy_base_z = trunk_h * 0.75  # taç, gövdenin üst kısmıyla hafif örtüşür (görsel süreklilik)
            canopy_h = height - canopy_base_z

            if species == TreeSpecies.CONIFER:
                parts.append(
                    _cone_mesh(canopy_radius * jitter, canopy_base_z, canopy_h, _CANOPY_SEGMENTS, f"{name}_canopy")
                )
            else:  # DECIDUOUS / GENERIC - iki basık koni üst üste (yuvarlak taç yaklaşıklaması)
                lower_h = canopy_h * 0.55
                upper_h = canopy_h * 0.55
                parts.append(
                    _cone_mesh(canopy_radius * jitter, canopy_base_z, lower_h, _CANOPY_SEGMENTS, f"{name}_canopy_lo")
                )
                upper_base_z = canopy_base_z + canopy_h * 0.45
                parts.append(
                    _cone_mesh(
                        canopy_radius * 0.65 * jitter, upper_base_z, upper_h, _CANOPY_SEGMENTS, f"{name}_canopy_hi"
                    )
                )

        merged = MeshMerger.merge(parts, name=name)
        return merged