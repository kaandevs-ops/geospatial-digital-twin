"""
Terrain Editor
==============

Roadmap Phase 8 - "Terrain Editor": Raise, Lower, Flatten, Smooth, Noise,
Paint.

Phase 2 `terrain_engine.HeightmapGrid` üzerinde çalışır. Tüm operasyonlar
bir **fırça (brush)** modeli kullanır: dairesel bir yarıçap içindeki
hücreler, merkeze göre azalan bir ağırlıkla (falloff) etkilenir - gerçek
arazi editörlerindeki (World Machine, Unity Terrain) davranışın aynısı.

Her operasyon, etkilenen hücrelerin *önceki* değerlerini saklayarak bir
`EditorCommand` üretir; böylece undo, tüm grid'i değil yalnızca dokunulan
hücreleri geri yükler (büyük heightmap'lerde bellek/performans için
önemli).

"Paint" operasyonu yükseklik değil, hücre başına bir **katman ağırlığı**
(örn. çim/kaya/kar dokusu karışım oranı) boyar - `material_engine`'in
`Procedural Materials` sistemiyle birlikte kullanılmak üzere ayrı bir
`TerrainPaintLayer` ızgarası tutulur (heightmap'ten bağımsız, aynı
boyutta).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ..terrain_engine import HeightmapGrid
from .commands import EditorCommand, FunctionCommand

# ============================================================================ #
# Brush
# ============================================================================ #


@dataclass(slots=True)
class Brush:
    """Dairesel fırça: `center_row/center_col` etrafında `radius_cells`
    yarıçapında, merkeze göre lineer (varsayılan) veya smoothstep falloff
    ile ağırlıklandırılmış hücre kümesi üretir."""

    center_row: float
    center_col: float
    radius_cells: float
    strength: float = 1.0
    smoothstep: bool = True

    def weight(self, row: int, col: int) -> float:
        dist = math.hypot(row - self.center_row, col - self.center_col)
        if dist >= self.radius_cells:
            return 0.0
        t = 1.0 - (dist / self.radius_cells)
        if self.smoothstep:
            t = t * t * (3 - 2 * t)
        return t * self.strength

    def affected_cells(self, grid_width: int, grid_height: int) -> list[tuple[int, int, float]]:
        r0 = max(0, int(self.center_row - self.radius_cells))
        r1 = min(grid_height - 1, int(self.center_row + self.radius_cells))
        c0 = max(0, int(self.center_col - self.radius_cells))
        c1 = min(grid_width - 1, int(self.center_col + self.radius_cells))
        cells = []
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                w = self.weight(r, c)
                if w > 0.0:
                    cells.append((r, c, w))
        return cells


# ============================================================================ #
# TerrainPaintLayer
# ============================================================================ #


@dataclass(slots=True)
class TerrainPaintLayer:
    """Heightmap ile aynı boyutta, [0, 1] aralığında katman ağırlığı
    (örn. bir doku katmanının karışım oranı) tutan ek grid."""

    width: int
    height: int
    weights: list[list[float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.weights:
            self.weights = [[0.0 for _ in range(self.width)] for _ in range(self.height)]

    def get(self, row: int, col: int) -> float:
        if 0 <= row < self.height and 0 <= col < self.width:
            return self.weights[row][col]
        return 0.0

    def set(self, row: int, col: int, value: float) -> None:
        if 0 <= row < self.height and 0 <= col < self.width:
            self.weights[row][col] = max(0.0, min(1.0, value))


# ============================================================================ #
# TerrainEditor
# ============================================================================ #


class TerrainEditor:
    """`HeightmapGrid` (ve opsiyonel `TerrainPaintLayer`) üzerinde fırça
    tabanlı düzenleme operasyonları. Her metod bir `EditorCommand`
    döndürür; grid'i doğrudan mutasyona uğratmaz (komut `do()` edilene
    kadar)."""

    # -- ortak yardımcı: snapshot + apply ---------------------------------- #
    @staticmethod
    def _apply_delta(
        grid: HeightmapGrid, cells: list[tuple[int, int, float]], delta_fn, label: str
    ) -> EditorCommand:
        before: dict[tuple[int, int], float] = {(r, c): grid.elevations[r][c] for r, c, _w in cells}

        def do() -> None:
            for r, c, w in cells:
                grid.elevations[r][c] = delta_fn(before[(r, c)], w)

        def undo() -> None:
            for (r, c), z in before.items():
                grid.elevations[r][c] = z

        return FunctionCommand(do, undo, label=label)

    # -- Raise / Lower -------------------------------------------------------- #
    @staticmethod
    def raise_terrain(grid: HeightmapGrid, brush: Brush, amount_m: float = 1.0) -> EditorCommand:
        cells = brush.affected_cells(grid.width, grid.height)
        return TerrainEditor._apply_delta(
            grid, cells, lambda z, w: z + amount_m * w, "terrain:raise"
        )

    @staticmethod
    def lower_terrain(grid: HeightmapGrid, brush: Brush, amount_m: float = 1.0) -> EditorCommand:
        cells = brush.affected_cells(grid.width, grid.height)
        return TerrainEditor._apply_delta(
            grid, cells, lambda z, w: z - amount_m * w, "terrain:lower"
        )

    # -- Flatten ------------------------------------------------------------- #
    @staticmethod
    def flatten(
        grid: HeightmapGrid, brush: Brush, target_elevation: float | None = None
    ) -> EditorCommand:
        cells = brush.affected_cells(grid.width, grid.height)
        if target_elevation is None:
            # Belirtilmezse fırça merkezindeki mevcut yüksekliği hedef al.
            cr, cc = int(round(brush.center_row)), int(round(brush.center_col))
            target_elevation = grid.elevation_at(cr, cc)
        return TerrainEditor._apply_delta(
            grid, cells, lambda z, w: z + (target_elevation - z) * w, "terrain:flatten"
        )

    # -- Smooth ---------------------------------------------------------------- #
    @staticmethod
    def smooth(grid: HeightmapGrid, brush: Brush, iterations: int = 1) -> EditorCommand:
        cells = brush.affected_cells(grid.width, grid.height)
        cell_set = {(r, c) for r, c, _w in cells}

        def box_blur_value(r: int, c: int) -> float:
            total, count = 0.0, 0
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    total += grid.elevation_at(r + dr, c + dc)
                    count += 1
            return total / count

        before: dict[tuple[int, int], float] = {(r, c): grid.elevations[r][c] for r, c in cell_set}

        def do() -> None:
            current = {k: v for k, v in before.items()}
            for _ in range(max(1, iterations)):
                new_values = {}
                for r, c, w in cells:
                    blurred = box_blur_value(r, c)
                    new_values[(r, c)] = current[(r, c)] + (blurred - current[(r, c)]) * w
                current = new_values
                for (r, c), z in current.items():
                    grid.elevations[r][c] = z

        def undo() -> None:
            for (r, c), z in before.items():
                grid.elevations[r][c] = z

        return FunctionCommand(do, undo, label="terrain:smooth")

    # -- Noise ------------------------------------------------------------------ #
    @staticmethod
    def add_noise(
        grid: HeightmapGrid, brush: Brush, amplitude_m: float = 0.5, seed: int | None = None
    ) -> EditorCommand:
        cells = brush.affected_cells(grid.width, grid.height)
        rng = random.Random(seed)
        noise_values = {(r, c): rng.uniform(-amplitude_m, amplitude_m) for r, c, _w in cells}
        return TerrainEditor._apply_delta_with_noise(grid, cells, noise_values)

    @staticmethod
    def _apply_delta_with_noise(
        grid: HeightmapGrid,
        cells: list[tuple[int, int, float]],
        noise_values: dict[tuple[int, int], float],
    ) -> EditorCommand:
        before: dict[tuple[int, int], float] = {(r, c): grid.elevations[r][c] for r, c, _w in cells}

        def do() -> None:
            for r, c, w in cells:
                grid.elevations[r][c] = before[(r, c)] + noise_values[(r, c)] * w

        def undo() -> None:
            for (r, c), z in before.items():
                grid.elevations[r][c] = z

        return FunctionCommand(do, undo, label="terrain:noise")

    # -- Paint ------------------------------------------------------------------- #
    @staticmethod
    def paint(layer: TerrainPaintLayer, brush: Brush, target_weight: float = 1.0) -> EditorCommand:
        cells = brush.affected_cells(layer.width, layer.height)
        before: dict[tuple[int, int], float] = {(r, c): layer.get(r, c) for r, c, _w in cells}

        def do() -> None:
            for r, c, w in cells:
                blended = before[(r, c)] + (target_weight - before[(r, c)]) * w
                layer.set(r, c, blended)

        def undo() -> None:
            for (r, c), v in before.items():
                layer.set(r, c, v)

        return FunctionCommand(do, undo, label="terrain:paint")
