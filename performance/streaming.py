"""
Performance Engine - Incremental Generation & Streaming
=========================================================

Roadmap Phase 13 - "Incremental mesh generation", "Geometry streaming",
"Scene streaming", "Texture atlas", "Instancing", "Dynamic batching".

`IncrementalMeshGenerator`: buyuk bir mesh'i tek seferde uretmek yerine
sabit boyutlu ucgen "chunk"lari halinde, cagiran taraf tuketebildikce
uretir (bir generator/coroutine). Boylece cagiran, her chunk'tan sonra
kontrolu geri alabilir (orn. bir frame butcesi asilirsa uretime devam
etmeden once bir frame render edilebilir).

`GeometryStreaming`/`SceneStreaming`: Phase 2 `TerrainStreaming` ile ayni
desen - kamera konumuna gore hangi chunk/tile'larin bellekte tutulmasi
gerektigini (yuklenecek/bosaltilacak kumeler farki olarak) hesaplar.
Gercek disk/ag I/O'sunu `AsyncAssetLoader`'a (task_scheduler.py) devreder.

`TextureAtlas`: kucuk texture'lari tek bir buyuk dokuya (atlas) paketleyen
basit bir "shelf" (raf) paketleyici + UV donusum tablosu.

`InstancingBatch`/`DynamicBatcher`: ayni mesh'in cok sayida farkli
transform ile cizilmesini (instancing) ve farkli mesh'lerin materyale
gore gruplanip tek draw call'a indirgenmesini (dynamic batching) temsil
eden veri modelleri - gercek GPU instancing API cagirisi backend'e ozeldir,
burada CPU tarafinda "hangi nesneler birlikte cizilebilir" gruplamasi
saglanir.
"""

from __future__ import annotations

import math
from collections.abc import Generator, Iterable
from dataclasses import dataclass, field

from ..mesh_engine import Mesh3D, Triangle, Vertex3D

Vec3 = tuple[float, float, float]


# ============================================================================ #
# Incremental Mesh Generation
# ============================================================================ #


class IncrementalMeshGenerator:
    """`triangles_source`'tan (herhangi bir iterable üçgen üreticisi;
    orn. bir procedural building generator'in ic ureticisi) `chunk_size`
    ucgenlik parcalar halinde `Mesh3D` uretir."""

    def __init__(
        self, vertices: list[Vertex3D], triangles: Iterable[Triangle], chunk_size: int = 256
    ) -> None:
        self._vertices = vertices
        self._triangles = list(triangles)
        self.chunk_size = max(1, chunk_size)

    def generate(self, name: str = "incremental_mesh") -> Generator[Mesh3D, None, None]:
        total = len(self._triangles)
        for start in range(0, total, self.chunk_size):
            chunk_tris = self._triangles[start : start + self.chunk_size]
            yield Mesh3D(
                vertices=self._vertices,
                triangles=chunk_tris,
                name=f"{name}_chunk{start // self.chunk_size}",
            )

    @property
    def total_chunks(self) -> int:
        return math.ceil(len(self._triangles) / self.chunk_size) if self._triangles else 0


# ============================================================================ #
# Geometry / Scene Streaming
# ============================================================================ #


@dataclass(slots=True)
class StreamingDiff:
    to_load: set[str]
    to_unload: set[str]


class GeometryStreaming:
    """Kamera konumuna gore `radius` icindeki chunk anahtarlarini "yuklu
    tutulmasi gereken kume" olarak belirler; onceki kareyle farki
    (`diff()`) `AsyncAssetLoader.load()`/bellekten dusme icin kullanilabilir."""

    def __init__(self, chunk_positions: dict[str, Vec3], radius: float) -> None:
        self.chunk_positions = chunk_positions
        self.radius = radius
        self._loaded: set[str] = set()

    def desired_set(self, camera_position: Vec3) -> set[str]:
        cx, cy, cz = camera_position
        desired = set()
        for key, (x, y, z) in self.chunk_positions.items():
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2)
            if dist <= self.radius:
                desired.add(key)
        return desired

    def diff(self, camera_position: Vec3) -> StreamingDiff:
        desired = self.desired_set(camera_position)
        to_load = desired - self._loaded
        to_unload = self._loaded - desired
        self._loaded = desired
        return StreamingDiff(to_load=to_load, to_unload=to_unload)

    @property
    def loaded(self) -> set[str]:
        return set(self._loaded)


class SceneStreaming(GeometryStreaming):
    """`GeometryStreaming`'in sahne-nesnesi (bina/prefab/actor) seviyesinde
    kullanimi icin isimlendirilmis alt-sinif - ayni algoritma, farkli
    anlamsal seviye (Phase 2 chunk yerine Phase 5/8 sahne nesneleri)."""


# ============================================================================ #
# Texture Atlas
# ============================================================================ #


@dataclass(slots=True)
class AtlasEntry:
    key: str
    x: int
    y: int
    width: int
    height: int

    def uv_transform(
        self, atlas_width: int, atlas_height: int
    ) -> tuple[float, float, float, float]:
        """(u_offset, v_offset, u_scale, v_scale) - orijinal [0,1] UV'yi
        atlas icindeki alt-bolgeye esler: `u' = u * u_scale + u_offset`."""
        return (
            self.x / atlas_width,
            self.y / atlas_height,
            self.width / atlas_width,
            self.height / atlas_height,
        )


class TextureAtlas:
    """Basit "shelf" (raf) paketleyici: girdileri yukseklige gore azalan
    sirada yerlestirir, her raf kendi max yuksekligine gore ilerler.
    Gercek piksel blit islemi (Pillow vb.) render/asset pipeline'inin
    sorumlulugundadir - bu sinif sadece paket duzenini (layout) hesaplar."""

    def __init__(self, atlas_width: int = 2048, atlas_height: int = 2048) -> None:
        self.atlas_width = atlas_width
        self.atlas_height = atlas_height
        self._entries: dict[str, AtlasEntry] = {}
        self._shelf_x = 0
        self._shelf_y = 0
        self._shelf_height = 0

    def add(self, key: str, width: int, height: int) -> AtlasEntry:
        if width > self.atlas_width:
            raise ValueError(
                f"Texture genisligi ({width}) atlas genisligini ({self.atlas_width}) asiyor"
            )
        if self._shelf_x + width > self.atlas_width:
            # yeni rafa gec
            self._shelf_x = 0
            self._shelf_y += self._shelf_height
            self._shelf_height = 0
        if self._shelf_y + height > self.atlas_height:
            raise ValueError("TextureAtlas dolu: atlas boyutunu artirin veya paketleri bolun")
        entry = AtlasEntry(key=key, x=self._shelf_x, y=self._shelf_y, width=width, height=height)
        self._entries[key] = entry
        self._shelf_x += width
        self._shelf_height = max(self._shelf_height, height)
        return entry

    def get(self, key: str) -> AtlasEntry:
        return self._entries[key]

    def occupancy_ratio(self) -> float:
        used = sum(e.width * e.height for e in self._entries.values())
        total = self.atlas_width * self.atlas_height
        return used / total if total else 0.0


# ============================================================================ #
# Instancing & Dynamic Batching
# ============================================================================ #


@dataclass(slots=True)
class InstancingBatch:
    """Ayni mesh'in farkli transform'larla (pozisyon) tekrar tekrar
    cizilmesi - orn. bir sokaktaki yuzlerce ayni agac/direk prefab'i."""

    mesh_key: str
    transforms: list[Vec3] = field(default_factory=list)

    def instance_count(self) -> int:
        return len(self.transforms)


class DynamicBatcher:
    """Farkli nesneleri materyal anahtarina gore gruplayarak, roadmap'in
    "Dynamic batching" ihtiyacini karsilayan CPU-tarafi bir on-gruplayici.
    Her materyal grubu tek bir draw-call adayi olarak dusunulur."""

    def __init__(self) -> None:
        self._groups: dict[str, list[str]] = {}

    def add(self, material_key: str, mesh_key: str) -> None:
        self._groups.setdefault(material_key, []).append(mesh_key)

    def batches(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._groups.items()}

    def draw_call_count(self) -> int:
        return len(self._groups)
