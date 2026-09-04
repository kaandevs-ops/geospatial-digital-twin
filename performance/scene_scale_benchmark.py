"""
Performance — Faz D8: Gerçek Streaming/LOD Ölçek Testi
=========================================================

Roadmap V3 - Faz D8 (Roadmap V2 A13'ün "10.000+ bina, gerçek zamanlı LOD
geçişi" kabul kriterine karşılık gelir). D1 (gerçek QEM `MeshSimplifier`)
ve D2 (`Scene.add_mesh_with_lod` + `total_triangle_count_for_camera`)
tamamlandıktan sonra anlamlı hale gelen ölçek benchmark'ı.

**Mimari fikir:** büyük bir şehir katalogunun tamamını (10.000+ bina)
asla aynı anda `Mesh3D`'ye çevirip belleğe almamak. `BuildingDescriptor`
yalnızca konum/boyut tutan ucuz bir "katalog girdisi"; gerçek `Mesh3D` +
LOD zinciri, yalnızca Faz 13 `SceneStreaming`'in kamera çevresindeki
`radius` içinde "istenen" (desired) olarak işaretlediği girdiler için
`StreamingSceneCache.sync()` çağrısında geç (lazy) üretilir. Kamera
uzaklaştığında karşılık gelen `SceneNode`'lar sahneden çıkarılır
(bellekten düşürülür - "unload").

Bu, roadmap A13'ün kabul kriterini doğrudan karşılar: **bellek kullanımı
sahne (katalog) büyüklüğüyle doğrusal-altı büyür** — çünkü resident
(bellekte tutulan) küme, `radius`/yoğunluk tarafından sınırlanır ve
katalog büyüklüğünden (N) bağımsızdır; N büyüdükçe
`resident_bytes / N` oranı ölçülebilir şekilde küçülür.

Bağımlılık: yalnızca stdlib (`tracemalloc` - gerçek Python-heap ölçümü,
`performance/profiler.py`'nin `MemoryProfiler`'ıyla aynı ilke).
"""

from __future__ import annotations

import math
import tracemalloc
from dataclasses import dataclass, field

from ..core_engine.geometry_engine import Point2D, Polygon
from ..mesh_engine import MeshBuilder, Mesh3D
from ..render_engine.scene_bridge import (
    Scene,
    total_triangle_count_for_camera,
    total_triangle_count_full_detail,
)
from .streaming import SceneStreaming, StreamingDiff

Vec3 = tuple[float, float, float]


# ============================================================================ #
# Ucuz katalog modeli
# ============================================================================ #

@dataclass(slots=True)
class BuildingDescriptor:
    """Bir binanın 'katalog' seviyesindeki ucuz tanımı — henüz `Mesh3D`
    üretilmemiş, yalnızca konum/boyut bilgisi tutulur. Bellek maliyeti
    ihmal edilebilir (birkaç `float`/`str`); 10.000+ girdi bile önemsiz
    miktarda bellek tutar - streaming'in ön koşulu budur."""

    name: str
    position: Vec3
    width: float = 8.0
    depth: float = 8.0
    height: float = 12.0


def build_city_catalog(n_side: int, spacing: float = 20.0) -> dict[str, BuildingDescriptor]:
    """`n_side x n_side` ızgarada (`n_side**2` bina) bir katalog üretir.
    Bu aşamada hiçbir `Mesh3D` oluşturulmaz — yalnızca konum tablosu."""
    catalog: dict[str, BuildingDescriptor] = {}
    for i in range(n_side):
        for j in range(n_side):
            name = f"bina_{i}_{j}"
            catalog[name] = BuildingDescriptor(
                name=name, position=(i * spacing, 0.0, j * spacing),
            )
    return catalog


def _descriptor_to_mesh(desc: BuildingDescriptor) -> Mesh3D:
    """Katalog girdisinden gerçek geometriyi (yalnızca "yükleme" anında)
    üretir - Faz 2 `MeshBuilder.extrude_polygon` ile aynı yol, D2'nin
    `add_mesh_with_lod`'una beslenecek."""
    poly = Polygon(points=[
        Point2D(0.0, 0.0), Point2D(desc.width, 0.0),
        Point2D(desc.width, desc.depth), Point2D(0.0, desc.depth),
    ])
    return MeshBuilder.extrude_polygon(poly, base_z=0.0, height=desc.height, name=desc.name)


# ============================================================================ #
# Streaming <-> Scene köprüsü
# ============================================================================ #

class StreamingSceneCache:
    """Faz 13 `SceneStreaming` ile Faz D2 `Scene.add_mesh_with_lod`
    arasında köprü: yalnızca kamera `radius`'u içindeki katalog girdileri
    için gerçekten `Mesh3D` + LOD zinciri üretip `Scene`'e ekler; kamera
    uzaklaştığında karşılık gelen node'ları sahneden çıkarır (evict).
    Katalog boyutundan bağımsız, yalnızca resident kümeyle orantılı
    bellek tutar."""

    def __init__(self, catalog: dict[str, BuildingDescriptor], radius: float) -> None:
        self.catalog = catalog
        chunk_positions = {name: desc.position for name, desc in catalog.items()}
        self._streamer = SceneStreaming(chunk_positions, radius)
        self.scene = Scene(name="streaming_city")

    def sync(self, camera_position: Vec3) -> StreamingDiff:
        """Kamera konumuna göre `to_load`/`to_unload` farkını hesaplar,
        yalnızca yeni istenen girdiler için `Mesh3D` üretip sahneye ekler
        ve artık istenmeyenleri sahneden çıkarır. Diff'i döndürür (test/
        gözlem amaçlı)."""
        diff = self._streamer.diff(camera_position)
        for name in diff.to_load:
            desc = self.catalog[name]
            mesh = _descriptor_to_mesh(desc)
            self.scene.add_mesh_with_lod(mesh, translation=desc.position)
        if diff.to_unload:
            self.scene.nodes = [
                n for n in self.scene.nodes if n.name not in diff.to_unload
            ]
        return diff

    @property
    def resident_count(self) -> int:
        return len(self._streamer.loaded)

    def resident_triangle_count(self, camera_position: Vec3) -> int:
        """Yalnızca şu anda bellekte tutulan (resident) node'lar için,
        D2'nin kamera-mesafe tabanlı LOD seçimiyle toplam çizilen üçgen
        sayısı — 'gerçek zamanlı LOD geçişi'nin resident kümeye
        uygulandığının kanıtı."""
        return total_triangle_count_for_camera(self.scene, camera_position)

    def resident_triangle_count_full_detail(self) -> int:
        return total_triangle_count_full_detail(self.scene)


# ============================================================================ #
# Ölçek benchmark'ı
# ============================================================================ #

@dataclass(slots=True)
class ScaleBenchmarkResult:
    catalog_size: int
    resident_count: int
    resident_bytes: int
    bytes_per_catalog_building: float


def benchmark_resident_memory_scaling(
    n_sides: tuple[int, ...] = (10, 20, 40),
    spacing: float = 20.0,
    radius: float = 45.0,
) -> list[ScaleBenchmarkResult]:
    """A13 kabul kriterinin doğrudan ölçümü: `n_sides` ile artan katalog
    büyüklüklerinde (`n_side**2` bina), kamera şehrin merkezine
    yerleştirilip yalnızca `radius` içindeki girdiler için gerçekten
    bellek ayrılır (`tracemalloc` ile ölçülür - gerçek Python-heap
    ölçümü). `bytes_per_catalog_building` (resident_bytes / N) katalog
    büyüdükçe **azalmalı** — bu, resident bellek büyümesinin toplam sahne
    büyüklüğüyle doğrusal-altı (sub-linear) olduğunun sayısal kanıtıdır.
    """
    results: list[ScaleBenchmarkResult] = []
    for n_side in n_sides:
        catalog = build_city_catalog(n_side, spacing=spacing)
        cache = StreamingSceneCache(catalog, radius=radius)
        center = (
            (n_side - 1) * spacing / 2.0, 0.0, (n_side - 1) * spacing / 2.0,
        )

        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()
        cache.sync(center)
        snapshot_after = tracemalloc.take_snapshot()
        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        resident_bytes = sum(max(stat.size_diff, 0) for stat in stats)
        if not was_tracing:
            tracemalloc.stop()

        catalog_size = n_side * n_side
        results.append(ScaleBenchmarkResult(
            catalog_size=catalog_size,
            resident_count=cache.resident_count,
            resident_bytes=resident_bytes,
            bytes_per_catalog_building=resident_bytes / catalog_size,
        ))
    return results


def assert_sub_linear_growth(results: list[ScaleBenchmarkResult]) -> None:
    """`benchmark_resident_memory_scaling()` çıktısının, katalog
    büyüdükçe `bytes_per_catalog_building`'in kesin şekilde azaldığını
    (doğrusal-altı büyüme) doğrular. Regresyon testi bu fonksiyonu
    doğrudan çağırır; ihlal durumunda `AssertionError` fırlatılır."""
    if len(results) < 2:
        raise ValueError("Karşılaştırma için en az 2 ölçek noktası gerekli")
    for prev, curr in zip(results, results[1:]):
        if curr.catalog_size <= prev.catalog_size:
            raise ValueError("n_sides artan sırada olmalı")
        assert curr.bytes_per_catalog_building < prev.bytes_per_catalog_building, (
            f"Doğrusal-altı büyüme ihlali: catalog_size={prev.catalog_size}'de "
            f"{prev.bytes_per_catalog_building:.2f} B/bina iken "
            f"catalog_size={curr.catalog_size}'de "
            f"{curr.bytes_per_catalog_building:.2f} B/bina "
            f"(azalmadı/arttı)"
        )
        # resident_count, radius/yoğunluğa bağlı sabit bir üst sınırla
        # sınırlı kalmalı - katalog büyüklüğüyle orantılı büyümemeli.
        assert curr.resident_count <= prev.resident_count * 4, (
            "resident_count katalog büyüklüğüyle orantılı büyüyor gibi görünüyor "
            "(streaming radius'u etkisiz kalmış olabilir)"
        )
