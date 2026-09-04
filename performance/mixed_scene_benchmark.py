"""
performance.mixed_scene_benchmark - Faz C4 (LOD/instancing/performans),
2. dilimin B3 kabul kriteri ölçümü
================================================================================

ROADMAP_V7.md Bölüm B3 kabul kriteri: "1000+ karışık feature (bina+yol+
ağaç+POI) içeren bir sahne kabul edilebilir FPS'te render edilmeli
(benchmark raporuyla)."

`performance/scene_scale_benchmark.py` (Faz D8) bu kriterin **bina**
tarafını zaten karşılıyordu (10.000+ bina, streaming/LOD ile bellek
ölçeklenmesi). Bu modül aynı kriterin **karışık OSM nokta-prop**
tarafını (ağaç, sokak mobilyası, dini yapı eki, oyun alanı, dış mekan
oturma, iletişim kulesi - C2/C3'ün altı köprüsü) kapatır: sentetik ama
gerçekçi büyüklükte (varsayılan 1200 feature) karışık bir sahne üretir,
`scene_instancing.build_scene_instancing_result` (C4/1. dilim) +
`scene_lod.apply_scene_lod` (C4/2. dilim) ile işler ve B3'ün istediği
"benchmark raporu"nu (`MixedSceneBenchmarkResult`) döner.

Bu modül gerçek bir Overpass ağ çağrısı yapmaz (roadmap'in "network
gerektirmeyen, deterministik test" ilkesiyle tutarlı, bkz.
`tests/test_c2_osm_category_layers.py` docstring'i) - `random.Random`
ile sabit tohumlu (seed) sentetik `VegetationInstance`/`StreetFurnitureItem`/
... listeleri üretir. Gerçek OSM verisiyle uçtan uca akış zaten
C2 (`fetch_category_features`) + C3'ün yedi köprüsü tarafından
karşılanıyor; burada amaç yalnızca **ölçek/performans** ölçümüdür.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

from ..commerce_props import OutdoorSeatingItem
from ..core_engine.geometry_engine import Point2D
from ..power_infrastructure import CommunicationTowerItem
from ..religious_structures import ReligionKind, ReligiousStructureItem
from ..sport_recreation import PlaygroundItem
from ..street_furniture import StreetFurnitureItem, StreetFurnitureType
from ..vegetation.types import TreeSpecies, VegetationInstance
from ..visualization.camera_rig import Camera
from .scene_instancing import SceneInstancingResult, build_scene_instancing_result
from .scene_lod import LODAwareSceneResult, apply_scene_lod

#: B3'ün "1000+" eşiğini rahatça aşan, ama tek bir test çalıştırmasında
#: makul sürede tamamlanan varsayılan sahne büyüklüğü.
DEFAULT_MIXED_FEATURE_COUNT = 1200

#: Kategoriler arası dağılım oranı (toplamı 1.0) - roadmap'in B1
#: listesindeki göreli yoğunluk beklentisiyle kabaca tutarlı: ağaçlar
#: en yoğun kategori (orman/park saçılımı), iletişim kulesi en seyrek.
_CATEGORY_WEIGHTS: dict[str, float] = {
    "vegetation": 0.45,
    "street_furniture": 0.30,
    "religious_structures": 0.03,
    "playgrounds": 0.05,
    "outdoor_seating": 0.12,
    "communication_towers": 0.05,
}


@dataclass(slots=True)
class MixedSceneBenchmarkResult:
    """B3'ün "benchmark raporuyla" istediği ölçülebilir çıktı."""

    feature_count: int
    build_seconds: float
    lod_seconds: float
    instancing: SceneInstancingResult
    lod_aware: LODAwareSceneResult

    def total_instance_count(self) -> int:
        return self.instancing.total_instance_count()

    def total_template_count(self) -> int:
        return self.instancing.total_template_count()

    def naive_triangle_count(self) -> int:
        return self.instancing.total_naive_triangle_count()

    def lod_rendered_triangle_count(self) -> int:
        return self.lod_aware.total_rendered_triangle_count()

    def triangle_reduction_ratio_vs_naive(self) -> float:
        """LOD+instancing sonrası render üçgen sayısının, hiçbir
        optimizasyon uygulanmamış (naive) hale göre azalma oranı (0..1) -
        B3'ün "kabul edilebilir FPS" kriterinin dolaylı kanıtı: daha az
        üçgen -> daha az GPU maliyeti."""
        naive = self.naive_triangle_count()
        if naive == 0:
            return 0.0
        return 1.0 - (self.lod_rendered_triangle_count() / naive)

    def culled_ratio(self) -> float:
        return self.lod_aware.culled_ratio()

    def meets_b3_criterion(self, min_feature_count: int = 1000) -> bool:
        """B3'ün "1000+ karışık feature" eşiğini karşılayıp
        karşılamadığının doğrudan, okunabilir kontrolü."""
        return self.feature_count >= min_feature_count


def _random_point(rng: random.Random, radius_m: float) -> Point2D:
    angle = rng.uniform(0.0, 6.283185307179586)
    dist = rng.uniform(0.0, radius_m)
    return Point2D(dist * math.cos(angle), dist * math.sin(angle))


def generate_synthetic_mixed_scene(
    feature_count: int = DEFAULT_MIXED_FEATURE_COUNT,
    area_radius_m: float = 600.0,
    seed: int = 42,
) -> dict[str, list]:
    """B3'ün "1000+ karışık feature" senaryosunu sentetik olarak üretir.
    Deterministik (sabit `seed`) - CI'da tekrarlanabilir benchmark için."""
    rng = random.Random(seed)
    counts = {key: round(feature_count * weight) for key, weight in _CATEGORY_WEIGHTS.items()}

    vegetation: list[VegetationInstance] = []
    species_cycle = list(TreeSpecies)
    for i in range(counts["vegetation"]):
        p = _random_point(rng, area_radius_m)
        height = rng.uniform(3.0, 18.0)
        vegetation.append(
            VegetationInstance(
                species=species_cycle[i % len(species_cycle)],
                x=p.x,
                y=p.y,
                z=0.0,
                height=height,
                canopy_radius=height * rng.uniform(0.25, 0.4),
                rotation_deg=rng.uniform(0.0, 360.0),
                seed=i,
            )
        )

    street_furniture: list[StreetFurnitureItem] = []
    furniture_cycle = list(StreetFurnitureType)
    for i in range(counts["street_furniture"]):
        p = _random_point(rng, area_radius_m)
        street_furniture.append(
            StreetFurnitureItem(
                furniture_type=furniture_cycle[i % len(furniture_cycle)],
                position=p,
                rotation_deg=rng.uniform(0.0, 360.0),
            )
        )

    religious_structures: list[ReligiousStructureItem] = []
    religion_cycle = list(ReligionKind)
    for i in range(counts["religious_structures"]):
        p = _random_point(rng, area_radius_m)
        religious_structures.append(
            ReligiousStructureItem(
                religion=religion_cycle[i % len(religion_cycle)],
                position=p,
                base_height_m=rng.uniform(6.0, 12.0),
            )
        )

    playgrounds: list[PlaygroundItem] = []
    for _ in range(counts["playgrounds"]):
        p = _random_point(rng, area_radius_m)
        playgrounds.append(PlaygroundItem(position=p, rotation_deg=rng.uniform(0.0, 360.0)))

    outdoor_seating: list[OutdoorSeatingItem] = []
    for i in range(counts["outdoor_seating"]):
        p = _random_point(rng, area_radius_m)
        outdoor_seating.append(
            OutdoorSeatingItem(
                position=p, table_count=1 + (i % 4), rotation_deg=rng.uniform(0.0, 360.0)
            )
        )

    communication_towers: list[CommunicationTowerItem] = []
    for _ in range(counts["communication_towers"]):
        p = _random_point(rng, area_radius_m)
        communication_towers.append(
            CommunicationTowerItem(position=p, height_m=rng.uniform(15.0, 35.0))
        )

    return {
        "vegetation": vegetation,
        "street_furniture": street_furniture,
        "religious_structures": religious_structures,
        "playgrounds": playgrounds,
        "outdoor_seating": outdoor_seating,
        "communication_towers": communication_towers,
    }


def run_mixed_scene_benchmark(
    feature_count: int = DEFAULT_MIXED_FEATURE_COUNT,
    area_radius_m: float = 600.0,
    seed: int = 42,
    camera: Camera | None = None,
) -> MixedSceneBenchmarkResult:
    """B3'ün kabul kriterini uçtan uca çalıştırır: sentetik sahne ->
    instancing gruplama (C4/1. dilim) -> LOD seçimi (C4/2. dilim) ->
    ölçülebilir rapor. Kamera verilmezse sahne merkezinde, biraz yukarıda
    varsayılan bir kamera kullanılır (tipik "kuş bakışı gözden geçirme"
    konumu)."""
    scene = generate_synthetic_mixed_scene(feature_count, area_radius_m, seed)
    cam = camera or Camera(position=(0.0, 0.0, 120.0), target=(0.0, 0.0, 0.0))

    t0 = time.perf_counter()
    instancing = build_scene_instancing_result(
        vegetation=scene["vegetation"],
        street_furniture=scene["street_furniture"],
        religious_structures=scene["religious_structures"],
        playgrounds=scene["playgrounds"],
        outdoor_seating=scene["outdoor_seating"],
        communication_towers=scene["communication_towers"],
    )
    t1 = time.perf_counter()
    lod_aware = apply_scene_lod(instancing, cam)
    t2 = time.perf_counter()

    actual_feature_count = sum(len(v) for v in scene.values())
    return MixedSceneBenchmarkResult(
        feature_count=actual_feature_count,
        build_seconds=t1 - t0,
        lod_seconds=t2 - t1,
        instancing=instancing,
        lod_aware=lod_aware,
    )


__all__ = [
    "DEFAULT_MIXED_FEATURE_COUNT",
    "MixedSceneBenchmarkResult",
    "generate_synthetic_mixed_scene",
    "run_mixed_scene_benchmark",
]
