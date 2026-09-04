"""
vegetation.scatter - Arazi-Temelli Yoğunluk Dağılımı
======================================================

Roadmap V4 - Faz E18. `terrain_engine.FlowAccumulation` çıktısı (D8
akümüle akış - yüksek değer = daha nemli/düz vadi tabanı gibi alanlar)
bir **nem vekili (moisture proxy)** olarak kullanılır; buna ek olarak
yerel eğim bir ceza terimi olarak eklenir (çok dik yamaçlarda daha az
bitki örtüsü - fiziksel olarak da makul: erozyon + kök tutunması zorluğu).

Kabul kriteri (roadmap): `FlowAccumulation` değeri yüksek (nemli)
bölgelerde üretilen bitki örtüsü yoğunluğu, ölçülebilir şekilde daha
fazladır. Bu modül, ağırlıklı-örnekleme (weighted sampling, tekrarlı)
ile hücreleri seçtiği için bu özelliği **istatistiksel olarak** garanti
eder (bkz. `tests/test_phaseE18_vegetation.py`).
"""
from __future__ import annotations

import math
import random

from ..core_engine.geometry_engine import Point2D, Polygon
from ..terrain_engine import FlowAccumulation, HeightmapGrid
from .types import TreeSpecies, VegetationInstance

_DEFAULT_HEIGHT_RANGE = (4.0, 9.0)
_DEFAULT_CANOPY_RANGE = (1.2, 2.4)


def _local_slope(grid: HeightmapGrid, row: int, col: int) -> float:
    """Merkezi-fark (central difference) ile kaba bir eğim tahmini
    (dz/dx, dz/dy -> büyüklük, birimsiz - metre yükseklik / metre mesafe)."""
    z_l = grid.elevation_at(row, col - 1)
    z_r = grid.elevation_at(row, col + 1)
    z_u = grid.elevation_at(row - 1, col)
    z_d = grid.elevation_at(row + 1, col)
    dzdx = (z_r - z_l) / (2.0 * grid.resolution_m)
    dzdy = (z_d - z_u) / (2.0 * grid.resolution_m)
    return math.sqrt(dzdx * dzdx + dzdy * dzdy)


def _normalized_moisture_scores(grid: HeightmapGrid) -> list[list[float]]:
    """`FlowAccumulation.accumulate()` çıktısını [0, 1] aralığına
    log-ölçekli olarak normalize eder (akümüle akış tipik olarak birkaç
    büyüklük mertebesinde (order-of-magnitude) değişir - doğrusal
    normalizasyon aşırı sivri/tek-modlu bir dağılım üretir)."""
    accum = FlowAccumulation.accumulate(grid)
    log_vals = [[math.log1p(v) for v in row] for row in accum]
    flat = [v for row in log_vals for v in row]
    lo, hi = min(flat), max(flat)
    span = hi - lo
    if span < 1e-9:
        return [[0.5 for _ in row] for row in log_vals]
    return [[(v - lo) / span for v in row] for row in log_vals]


class VegetationScatterer:
    """Roadmap V4 - Faz E18: `HeightmapGrid` üzerinde eğim + nem-vekili
    (flow accumulation) temelli, ağırlıklı-örneklemeli bitki örtüsü
    yerleşimi."""

    @staticmethod
    def moisture_field(grid: HeightmapGrid) -> list[list[float]]:
        """Her hücre için [0, 1] normalize nem skoru (dışarıdan da
        sorgulanabilir - örn. görselleştirme/ısı haritası için)."""
        return _normalized_moisture_scores(grid)

    @staticmethod
    def scatter(
        grid: HeightmapGrid,
        target_count: int,
        seed: int = 0,
        species: TreeSpecies = TreeSpecies.GENERIC,
        slope_penalty: float = 3.0,
        max_slope: float = 0.9,
    ) -> list[VegetationInstance]:
        """`target_count` adet `VegetationInstance` üretir; hücre seçimi
        nem-ağırlıklı, tekrarlı (with-replacement) rastgele örneklemedir
        - böylece toplam sayı sabit kalırken yoğunluk dağılımı istenen
        istatistiksel özelliği (nemli yerlerde daha fazla) taşır.

        `max_slope`'un üzerindeki hücreler (çok dik - örn. bina/kaya
        yüzeyi olası) tamamen elenir; kalanlar için ağırlık
        `moisture ** (1) * max(0, 1 - slope_penalty * slope)` şeklindedir.
        """
        if target_count <= 0:
            return []

        moisture = _normalized_moisture_scores(grid)
        rng = random.Random(seed)

        cells: list[tuple[int, int]] = []
        weights: list[float] = []
        for r in range(grid.height):
            for c in range(grid.width):
                slope = _local_slope(grid, r, c)
                if slope > max_slope:
                    continue
                slope_factor = max(0.0, 1.0 - slope_penalty * slope)
                weight = moisture[r][c] * slope_factor
                if weight <= 0.0:
                    continue
                cells.append((r, c))
                weights.append(weight)

        if not cells:
            return []

        chosen = rng.choices(cells, weights=weights, k=target_count)

        instances: list[VegetationInstance] = []
        for idx, (r, c) in enumerate(chosen):
            inst_seed = seed * 1_000_003 + idx
            inst_rng = random.Random(inst_seed)
            # Hücre içinde alt-piksel (sub-cell) jitter - ızgara-hizalı,
            # yapay görünen bir dağılım yerine daha doğal bir serpiliş.
            jitter_x = (inst_rng.random() - 0.5) * grid.resolution_m
            jitter_y = (inst_rng.random() - 0.5) * grid.resolution_m
            x = c * grid.resolution_m + jitter_x
            y = r * grid.resolution_m + jitter_y
            z = grid.elevations[r][c]
            height = inst_rng.uniform(*_DEFAULT_HEIGHT_RANGE)
            canopy_radius = inst_rng.uniform(*_DEFAULT_CANOPY_RANGE)
            rotation_deg = inst_rng.uniform(0.0, 360.0)
            instances.append(
                VegetationInstance(
                    species=species,
                    x=x, y=y, z=z,
                    height=height,
                    canopy_radius=canopy_radius,
                    rotation_deg=rotation_deg,
                    seed=inst_seed,
                )
            )
        return instances

    # ==================================================================== #
    # Faz C3 (ROADMAP_V7.md B2) — OSM alan feature'ları için poligon-temelli
    # Poisson-disc dağıtım (orman/park/su gibi HeightmapGrid'e bağlı
    # olmayan, doğrudan OSM poligonundan gelen alanlar için).
    # ==================================================================== #

    @staticmethod
    def scatter_polygon_poisson_disc(
        polygon: "Polygon",
        min_distance_m: float,
        seed: int = 0,
        species: TreeSpecies = TreeSpecies.GENERIC,
        height_range: tuple[float, float] = _DEFAULT_HEIGHT_RANGE,
        canopy_radius_range: tuple[float, float] = _DEFAULT_CANOPY_RANGE,
        max_points: int = 5000,
        z: float = 0.0,
    ) -> list[VegetationInstance]:
        """B2'nin "yoğunluk bazlı dağılım... Poisson-disc örnekleme ile
        doğal görünümlü, çakışmasız dağılım" kabul kriterinin, `scatter()`
        (HeightmapGrid + nem/eğim ağırlıklı) yönteminden **bağımsız**,
        yalnızca bir OSM alan feature'ının (poligon) geometrisine
        dayanan karşılığı. `scatter()` "arazi hazır olduğunda nemli
        yerlerde daha fazla ağaç" senaryosunu çözer; bu fonksiyon ise
        "bu OSM `landuse=forest` poligonunun içini doldur" senaryosunu
        çözer — ikisi birbirini dışlamaz, farklı girdi kaynaklarına
        (arazi ızgarası vs. OSM poligonu) hizmet eder.

        Bridson'ın Poisson-disc algoritmasının basitleştirilmiş bir
        varyantı: aday noktalar poligonun sınırlayıcı kutusu (bounding
        box) içinde tekdüze rastgele üretilir, önceden kabul edilmiş
        tüm noktalara olan mesafe `min_distance_m`'den küçükse veya
        poligon dışındaysa reddedilir (dart-throwing / rejection
        sampling — tam Bridson ızgara-hızlandırmalı versiyonuna göre
        büyük N için daha yavaştır ama basit ve deterministiktir,
        `max_points`/deneme sınırı üst sınırı garanti eder).
        """
        if min_distance_m <= 0:
            raise ValueError("min_distance_m pozitif olmalı.")

        min_x, min_y, max_x, max_y = polygon.bounding_box()
        width = max_x - min_x
        height_span = max_y - min_y
        if width <= 0 or height_span <= 0:
            return []

        rng = random.Random(seed)
        accepted: list[Point2D] = []
        min_dist_sq = min_distance_m * min_distance_m

        # Alana göre makul bir deneme bütçesi (çok küçük min_distance_m ile
        # sonsuz döngüye girmemek için sabit bir üst sınır kullanılır).
        max_attempts = max(200, min(max_points * 30, 200_000))

        for _ in range(max_attempts):
            if len(accepted) >= max_points:
                break
            candidate = Point2D(
                min_x + rng.random() * width,
                min_y + rng.random() * height_span,
            )
            if not polygon.contains_point(candidate):
                continue
            too_close = False
            for existing in accepted:
                dx = candidate.x - existing.x
                dy = candidate.y - existing.y
                if dx * dx + dy * dy < min_dist_sq:
                    too_close = True
                    break
            if too_close:
                continue
            accepted.append(candidate)

        instances: list[VegetationInstance] = []
        for idx, point in enumerate(accepted):
            inst_seed = seed * 1_000_003 + idx
            inst_rng = random.Random(inst_seed)
            instances.append(
                VegetationInstance(
                    species=species,
                    x=point.x, y=point.y, z=z,
                    height=inst_rng.uniform(*height_range),
                    canopy_radius=inst_rng.uniform(*canopy_radius_range),
                    rotation_deg=inst_rng.uniform(0.0, 360.0),
                    seed=inst_seed,
                )
            )
        return instances
