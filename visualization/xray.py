"""
Visualization - X-Ray (Phase 9)
=================================

Malzeme opaklığını (opacity) geometri değiştirmeden override eden bir
render-state katmanı. Gerçek şeffaflık blend'i backend'e ait, burada
"hangi malzeme ne opaklıkta çizilecek" kararı + basit mesafeye göre
otomatik x-ray (yakın yüzeyler saydamlaşır, uzak katman görünür kalır)
mantığı var.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..mesh_engine import Mesh3D


@dataclass(slots=True)
class XRayState:
    enabled: bool = False
    default_opacity: float = 1.0
    material_opacity: dict[str, float] = field(default_factory=dict)
    edge_highlight: bool = True

    def set_material_opacity(self, material_name: str, opacity: float) -> None:
        self.material_opacity[material_name] = max(0.0, min(1.0, opacity))

    def opacity_for(self, material_name: str | None) -> float:
        if not self.enabled:
            return 1.0
        if material_name is not None and material_name in self.material_opacity:
            return self.material_opacity[material_name]
        return self.default_opacity

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        return self.enabled


def occlusion_ratio(mesh: Mesh3D, view_point: tuple[float, float, float]) -> float:
    """Basit "ne kadarı gizli kalıyor" tahmini: mesh merkez noktasına göre
    view_point'e daha yakın üçgenlerin oranı (gerçek ray-casting yerine hızlı
    yaklaşıklık — Phase 6 `RayCasting`/`LineOfSight` ile birlikte
    kullanılabilir)."""
    if not mesh.triangles:
        return 0.0
    cx = sum(v.x for v in mesh.vertices) / len(mesh.vertices)
    cy = sum(v.y for v in mesh.vertices) / len(mesh.vertices)
    cz = sum(v.z for v in mesh.vertices) / len(mesh.vertices)
    center_dist = (
        (view_point[0] - cx) ** 2 + (view_point[1] - cy) ** 2 + (view_point[2] - cz) ** 2
    ) ** 0.5

    closer = 0
    for tri in mesh.triangles:
        tx = sum(mesh.vertices[i].x for i in tri) / 3
        ty = sum(mesh.vertices[i].y for i in tri) / 3
        tz = sum(mesh.vertices[i].z for i in tri) / 3
        d = (
            (view_point[0] - tx) ** 2 + (view_point[1] - ty) ** 2 + (view_point[2] - tz) ** 2
        ) ** 0.5
        if d < center_dist:
            closer += 1
    return closer / len(mesh.triangles)
