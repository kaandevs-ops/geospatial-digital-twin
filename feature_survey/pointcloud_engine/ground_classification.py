"""FAZ S3 — Zemin / Zemin-Dışı Sınıflandırma: Progressive Morphological Filter.

Roadmap ROADMAP_V6.md S3: "Zemin/zemin-dışı sınıflandırma: Cloth Simulation
Filter (CSF) veya Progressive Morphological Filter — gerçek, yayınlanmış
algoritmalar ... rastgele/kural-of-thumb eşik değil."

Bu modül, Zhang et al. (2003) "A Progressive Morphological Filter for
Removing Nonground Measurements from Airborne LIDAR Data" makalesindeki
**gerçek, yayınlanmış** algoritmayı stdlib-only olarak uygular:

1. Nokta bulutu düzenli bir 2D ızgaraya (grid) örneklenir (her hücrede en
   düşük Z — ön-zemin tahmini).
2. Artan pencere boyutlarıyla (`window_sizes`) morfolojik **açma**
   (erosion + dilation) uygulanır.
3. Her adımda, açma öncesi/sonrası yükseklik farkı, pencere boyutuna göre
   ölçeklenen bir eşik (`elevation_threshold`, eğim parametresiyle
   büyüyen — makaledeki `dh_T`) ile karşılaştırılır; eşiği aşan hücreler
   zemin-dışı (non-ground) olarak işaretlenir.
4. Orijinal noktalar, bulundukları hücrenin son sınıflandırmasına göre
   ASPRS kodlarıyla etiketlenir: **2 = zemin**, **1 = sınıflandırılmamış
   zemin-dışı** (roadmap'in "LAS sınıflandırma kodları ... gerçek
   sınıflandırma sonucundan yazılır" ilkesiyle tutarlı — placeholder kod
   atanmaz, gerçek algoritma çıktısı yazılır).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point3 = tuple[float, float, float]

ASPRS_GROUND = 2
ASPRS_UNCLASSIFIED = 1


class GroundClassificationError(ValueError):
    pass


@dataclass(slots=True)
class GroundClassificationResult:
    classification: list[int]  # ASPRS kodu, points ile aynı sırada
    cell_size_m: float
    window_sizes: list[int]

    @property
    def n_ground(self) -> int:
        return sum(1 for c in self.classification if c == ASPRS_GROUND)

    @property
    def n_nonground(self) -> int:
        return sum(1 for c in self.classification if c != ASPRS_GROUND)

    @property
    def ground_ratio(self) -> float:
        n = len(self.classification)
        return self.n_ground / n if n else 0.0


def _grid_min_z(
    points: list[Point3], cell_size: float
) -> tuple[dict[tuple[int, int], float], float, float]:
    min_x = min(p[0] for p in points)
    min_y = min(p[1] for p in points)
    grid: dict[tuple[int, int], float] = {}
    for x, y, z in points:
        cell = (int((x - min_x) // cell_size), int((y - min_y) // cell_size))
        if cell not in grid or z < grid[cell]:
            grid[cell] = z
    return grid, min_x, min_y


def _morphological_erode(grid: dict[tuple[int, int], float], radius: int) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    for (cx, cy) in grid:
        vals = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                neighbor = (cx + dx, cy + dy)
                if neighbor in grid:
                    vals.append(grid[neighbor])
        out[(cx, cy)] = min(vals) if vals else grid[(cx, cy)]
    return out


def _morphological_dilate(grid: dict[tuple[int, int], float], radius: int) -> dict[tuple[int, int], float]:
    out: dict[tuple[int, int], float] = {}
    for (cx, cy) in grid:
        vals = []
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                neighbor = (cx + dx, cy + dy)
                if neighbor in grid:
                    vals.append(grid[neighbor])
        out[(cx, cy)] = max(vals) if vals else grid[(cx, cy)]
    return out


def progressive_morphological_filter(
    points: list[Point3],
    cell_size_m: float = 1.0,
    window_sizes: list[int] | None = None,
    slope: float = 0.3,
    initial_threshold_m: float = 0.5,
    max_threshold_m: float = 3.0,
) -> GroundClassificationResult:
    """Zhang et al. (2003) Progressive Morphological Filter.

    `slope`: arazi eğim parametresi (makaledeki `S`) — eşik büyümesini
    kontrol eder: `dh_T[k] = min(initial_threshold + slope * (w[k]-w[k-1]) *
    cell_size, max_threshold)`. Bu, düz araziden dağlık araziye kadar
    farklı senaryolarda **gerçek** fiziksel bir gerekçeye (eğim arttıkça
    zemin de daha hızlı yükselebilir, bu nedenle daha büyük fark toleransı
    gerekir) dayanır — rastgele bir sabit değildir.
    """
    if len(points) < 4:
        raise GroundClassificationError(
            "Zemin sınıflandırması için en az 4 nokta gerekir (2D ızgara oluşturmak için)."
        )
    if cell_size_m <= 0:
        raise GroundClassificationError("cell_size_m pozitif olmalı.")

    if window_sizes is None:
        window_sizes = [1, 2, 3, 5, 7, 11, 17]

    grid, min_x, min_y = _grid_min_z(points, cell_size_m)
    ground_grid = dict(grid)
    nonground_cells: set[tuple[int, int]] = set()

    prev_window = 0
    for w in window_sizes:
        radius = max(w // 2, 1)
        opened = _morphological_dilate(_morphological_erode(ground_grid, radius), radius)
        dh_threshold = min(
            initial_threshold_m + slope * (w - prev_window) * cell_size_m,
            max_threshold_m,
        )
        for cell, z in list(ground_grid.items()):
            if cell in nonground_cells:
                continue
            diff = z - opened.get(cell, z)
            if diff > dh_threshold:
                nonground_cells.add(cell)
            else:
                ground_grid[cell] = opened[cell]
        prev_window = w

    classification: list[int] = []
    for (x, y, z) in points:
        cell = (int((x - min_x) // cell_size_m), int((y - min_y) // cell_size_m))
        if cell in nonground_cells:
            classification.append(ASPRS_UNCLASSIFIED)
        else:
            # Hücre zemin olarak işaretlendiyse, hücredeki en düşük noktaya
            # göreceli yüksekliği son bir kez kontrol et (hücre içi
            # zemin-üstü nesneleri — örn. aynı hücrede bina + zemin
            # karışmışsa — ayırt etmek için kaba bir yükseklik farkı testi).
            cell_min = grid[cell]
            if (z - cell_min) > max_threshold_m:
                classification.append(ASPRS_UNCLASSIFIED)
            else:
                classification.append(ASPRS_GROUND)

    return GroundClassificationResult(
        classification=classification, cell_size_m=cell_size_m, window_sizes=list(window_sizes)
    )
