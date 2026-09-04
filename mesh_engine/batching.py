"""
ROADMAP V5 - Track M / M1.2: Draw call / batch birleştirme
============================================================================

`performance.streaming.InstancingBatch` / `DynamicBatcher` / `TextureAtlas`
zaten CPU tarafında "hangi nesneler birlikte çizilebilir" gruplamasını
yapıyordu (Phase 13). Roadmap V5 M1.2'nin eksik bıraktığı parça, roadmap
metninin de belirttiği gibi, bunun **mesh_engine seviyesinde
genelleştirilmesi**: `vegetation/scatter.py`'nin instancing mantığına
benzer ama bina/mobilya/vejetasyon fark etmeksizin herhangi bir
`Mesh3D` + transform listesi için çalışan somut geometri üretimi.

Bu modül iki somut işlemi ekler:

1. `StaticMeshBatcher` - aynı malzemeyi paylaşan birden fazla mesh'i
   (örn. bir sokaktaki tüm bina cepheleri aynı malzemeyse) tek bir
   `Mesh3D`'ye statik olarak birleştirir (gerçek geometri birleşimi,
   `MeshMerger.merge`'in malzeme-anahtarına göre gruplanmış hali).
2. `InstanceMeshBaker` - `InstancingBatch`'teki (mesh_key, transform)
   listesini alıp *gerçek* dünya-uzayı geometrisine dönüştürür: ya tek bir
   dev "birleşik" mesh'e (düşük nesne sayısı, çok draw call'lu backend'ler
   için) ya da instance başına transformlanmış ayrı mesh listesine
   (gerçek GPU instancing API'sine devredilecek backend'ler için) çevirir.

Kabul kriteri ölçümü (`profiler.py` ile birlikte kullanılmak üzere):
`DrawCallEstimator.estimate()` batching/instancing öncesi/sonrası draw
call sayısını karşılaştırır.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import Mesh3D, MeshMerger, Vertex3D

Vec3 = tuple[float, float, float]


def _rotation_matrix_z(deg: float) -> tuple[tuple[float, float, float], ...]:
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return (
        (c, -s, 0.0),
        (s, c, 0.0),
        (0.0, 0.0, 1.0),
    )


def _apply_transform(v: Vertex3D, translation: Vec3, rotation_deg_z: float, scale: float) -> Vertex3D:
    x, y, z = v.x * scale, v.y * scale, v.z * scale
    if rotation_deg_z:
        m = _rotation_matrix_z(rotation_deg_z)
        x, y, z = (
            m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z,
        )
    tx, ty, tz = translation
    return Vertex3D(x + tx, y + ty, z + tz, normal=v.normal, uv=v.uv)


@dataclass(slots=True)
class InstanceTransform:
    """Tek bir instance için dünya-uzayı yerleşimi (2.5D - Z ekseninde
    döndürme, düz arazi/basit yerleşim senaryoları için yeterli; roll/pitch
    gerektiren durumlar için `translation`'a önceden dönüştürülmüş vertex
    verilmesi önerilir)."""

    translation: Vec3
    rotation_deg_z: float = 0.0
    scale: float = 1.0


class StaticMeshBatcher:
    """M1.2: Aynı malzemeyi paylaşan mesh parçalarının tek mesh'e statik
    birleştirilmesi (static batching). Girdi: `{material_key: [Mesh3D, ...]}`.
    Çıktı: her malzeme grubu için tek bir birleşik `Mesh3D` - draw call
    sayısı, grup içindeki parça sayısından 1'e iner."""

    @staticmethod
    def batch_by_material(groups: dict[str, list[Mesh3D]]) -> dict[str, Mesh3D]:
        result: dict[str, Mesh3D] = {}
        for material_key, meshes in groups.items():
            if not meshes:
                continue
            result[material_key] = MeshMerger.merge(meshes, name=f"batch_{material_key}")
        return result

    @staticmethod
    def draw_call_count_before(groups: dict[str, list[Mesh3D]]) -> int:
        return sum(len(meshes) for meshes in groups.values())

    @staticmethod
    def draw_call_count_after(groups: dict[str, list[Mesh3D]]) -> int:
        return sum(1 for meshes in groups.values() if meshes)

    @staticmethod
    def reduction_ratio(groups: dict[str, list[Mesh3D]]) -> float:
        before = StaticMeshBatcher.draw_call_count_before(groups)
        after = StaticMeshBatcher.draw_call_count_after(groups)
        if before == 0:
            return 0.0
        return 1.0 - (after / before)


class InstanceMeshBaker:
    """M1.2: Tekrar eden elemanlar (pencere çerçevesi, balkon korkuluğu,
    ağaç, sokak lambası) için tek base-mesh + çoklu transform ->
    `vegetation/scatter.py`'deki mantığın mesh_engine seviyesinde
    genellenmiş hali."""

    @staticmethod
    def bake_merged(base_mesh: Mesh3D, transforms: list[InstanceTransform], name: str = "baked_instances") -> Mesh3D:
        """Tüm instance'ları TEK bir mesh'e gömer (1 draw call, ama
        GPU-instancing'in bellek avantajından yararlanmaz - az sayıda
        (<~200) tekrar için uygundur, ör. bir avlu içindeki bank grubu)."""
        merged = Mesh3D(name=name)
        for t in transforms:
            offset = len(merged.vertices)
            merged.vertices.extend(
                _apply_transform(v, t.translation, t.rotation_deg_z, t.scale) for v in base_mesh.vertices
            )
            merged.triangles.extend((a + offset, b + offset, c + offset) for (a, b, c) in base_mesh.triangles)
        return merged

    @staticmethod
    def bake_instances(base_mesh: Mesh3D, transforms: list[InstanceTransform]) -> list[Mesh3D]:
        """Her instance için ayrı transformlanmış mesh döner - gerçek GPU
        instancing API'sine (tek base-mesh + transform buffer) devredilecek
        backend'ler için (bellek: base_mesh 1 kez GPU'ya yüklenir, yalnızca
        transform'lar CPU->GPU kopyalanır; büyük (>~200) instance sayıları
        için `bake_merged`'e göre çok daha verimli)."""
        return [
            Mesh3D(
                vertices=[_apply_transform(v, t.translation, t.rotation_deg_z, t.scale) for v in base_mesh.vertices],
                triangles=list(base_mesh.triangles),
                name=f"{base_mesh.name}_instance",
            )
            for t in transforms
        ]


class DrawCallEstimator:
    """M1.2 kabul kriteri ölçümü: batching/instancing öncesi ve sonrası
    tahmini draw call sayısını karşılaştırır (`profiler.py` ile birlikte
    kullanılmak üzere - gerçek GPU draw call sayısı render backend'e
    özeldir, burada CPU-tarafı üst sınır tahmini yapılır: 1 mesh/malzeme
    grubu = 1 draw call varsayımı)."""

    @staticmethod
    def estimate(mesh_material_pairs: list[tuple[str, int]]) -> int:
        """`[(material_key, instance_count), ...]` -> toplam tahmini draw
        call (batching/instancing UYGULANMAMIŞ hal, karşılaştırma temeli)."""
        return sum(count for _material, count in mesh_material_pairs)

    @staticmethod
    def estimate_after_batching(mesh_material_pairs: list[tuple[str, int]]) -> int:
        """Aynı malzemedeki tüm nesneler tek draw call'a indiği varsayımı."""
        materials = {m for m, _ in mesh_material_pairs}
        return len(materials)

    @staticmethod
    def reduction_percent(mesh_material_pairs: list[tuple[str, int]]) -> float:
        before = DrawCallEstimator.estimate(mesh_material_pairs)
        after = DrawCallEstimator.estimate_after_batching(mesh_material_pairs)
        if before == 0:
            return 0.0
        return 100.0 * (1.0 - after / before)


__all__ = [
    "InstanceTransform",
    "StaticMeshBatcher",
    "InstanceMeshBaker",
    "DrawCallEstimator",
]
