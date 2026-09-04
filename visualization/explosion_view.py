"""
Visualization - Explosion View (Phase 9)
===========================================

Katları ayrı ayrı 3D olarak "patlatma". Girdi: `Mesh3D` (Phase 3 `Building`'in
`full_mesh()` çıktısı gibi) + kat sınırlarını tanımlayan Z aralıkları
(`Phase 3 Floor.height_m` toplamlarından türetilebilir). Çıktı: her katın
kendi alt-mesh'i olarak ayrılmış ve `progress` parametresine göre
Z ekseninde aralıklı ötelenmiş bir sahne.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..mesh_engine import Mesh3D, Vertex3D


@dataclass(slots=True)
class FloorBand:
    """Bir katın mesh'te kapladığı Z aralığı [z_min, z_max)."""

    level: int
    z_min: float
    z_max: float


def floor_bands_from_heights(heights_m: list[float], z_start: float = 0.0) -> list[FloorBand]:
    """Phase 3 `Building.floors` listesindeki `height_m` değerlerinden
    ardışık Z aralıkları üretir (zemin kat z_start'tan başlar)."""
    bands = []
    z = z_start
    for level, h in enumerate(heights_m):
        bands.append(FloorBand(level=level, z_min=z, z_max=z + h))
        z += h
    return bands


@dataclass(slots=True)
class ExplodedFloor:
    band: FloorBand
    mesh: Mesh3D
    offset_z: float = 0.0


class ExplosionView:
    """Bir `Mesh3D`'i verilen kat bantlarına göre alt-mesh'lere ayırır ve
    `progress` (0..1) parametresine göre artan boşluklarla Z ekseninde
    yukarı doğru dağıtır (patlatma animasyonu)."""

    def __init__(self, mesh: Mesh3D, bands: list[FloorBand], gap_m: float = 2.0):
        self.mesh = mesh
        self.bands = sorted(bands, key=lambda b: b.level)
        self.gap_m = gap_m
        self._floor_meshes = self._split_by_band()

    def _split_by_band(self) -> list[Mesh3D]:
        """Her üçgeni, ağırlık-merkezi hangi banda düşüyorsa o alt-mesh'e
        atar (üçgenler kat sınırında nadiren bölünür; küçük tolerans ile
        en yakın banda yuvarlanır)."""
        floor_meshes = [Mesh3D(name=f"floor_{b.level}") for b in self.bands]

        for tri in self.mesh.triangles:
            verts = [self.mesh.vertices[i] for i in tri]
            centroid_z = sum(v.z for v in verts) / 3
            band_idx = self._band_index_for_z(centroid_z)
            target = floor_meshes[band_idx]
            base = len(target.vertices)
            target.vertices.extend(
                Vertex3D(v.x, v.y, v.z, v.normal, v.tangent, v.uv) for v in verts
            )
            target.triangles.append((base, base + 1, base + 2))

        return floor_meshes

    def _band_index_for_z(self, z: float) -> int:
        for i, b in enumerate(self.bands):
            if b.z_min <= z < b.z_max:
                return i
        # sınırların dışında kalanları en yakın banda ata
        if z < self.bands[0].z_min:
            return 0
        return len(self.bands) - 1

    def floor_mesh(self, level: int) -> Mesh3D:
        return self._floor_meshes[level]

    def state_at(self, progress: float) -> list[ExplodedFloor]:
        """`progress`=0 -> orijinal (bitişik) hal, `progress`=1 -> tam
        `gap_m` boşluklu patlamış hal. Ara değerler lineer interpolasyon."""
        progress = max(0.0, min(1.0, progress))
        result = []
        for i, band in enumerate(self.bands):
            offset = i * self.gap_m * progress
            offset_mesh = self._offset_mesh_z(self._floor_meshes[i], offset)
            result.append(ExplodedFloor(band=band, mesh=offset_mesh, offset_z=offset))
        return result

    @staticmethod
    def _offset_mesh_z(mesh: Mesh3D, offset_z: float) -> Mesh3D:
        cloned = mesh.clone()
        for v in cloned.vertices:
            v.z += offset_z
        return cloned

    def combined_mesh_at(self, progress: float) -> Mesh3D:
        """Tüm katları tek bir `Mesh3D`'de birleştirir (belirtilen `progress`
        durumuyla) — hızlı önizleme/export için."""
        combined = Mesh3D(name=f"{self.mesh.name}_exploded")
        for exploded in self.state_at(progress):
            base = len(combined.vertices)
            combined.vertices.extend(exploded.mesh.vertices)
            combined.triangles.extend(
                (a + base, b + base, c + base) for (a, b, c) in exploded.mesh.triangles
            )
        return combined
