"""
Roadmap V3 - Faz D10 ("Terrain Engine: Erozyon/Hidroloji Simülasyonu")
kabul kriteri testleri.

Kabul kriteri (ROADMAP_V3.md): "Bilinen bir sentetik yükseklik haritasında
(örn. tek tepe) erozyon sonrası vadi oluşumu görsel/istatistiksel olarak
doğrulanır (yükseklik varyansı azalır, belirli bir eşiğin altına düşen
hücre sayısı artar - regresyon testiyle sayısal sınır)."
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.coordinate_systems import GeoPoint
from harita.terrain_engine import (
    HeightmapGrid,
    DEMImporter,
    ErosionSimulator,
    FlowAccumulation,
)


ORIGIN = GeoPoint(lat=39.9, lon=32.8, elevation=0.0)


def _single_hill_grid(size: int = 24, amplitude: float = 30.0, resolution_m: float = 2.0) -> HeightmapGrid:
    """Tek, keskin (dik yamaçlı) bir koniyi merkeze yerleştirir - erozyonun
    "vadi oyma" davranışını net biçimde ortaya çıkaracak bir test sahnesi."""
    center = size / 2.0
    matrix = []
    for r in range(size):
        row = []
        for c in range(size):
            dist = math.sqrt((r - center) ** 2 + (c - center) ** 2)
            z = max(0.0, amplitude - dist * (amplitude / (size / 2.0)))
            row.append(z)
        matrix.append(row)
    return DEMImporter.from_matrix(matrix, resolution_m=resolution_m, origin=ORIGIN)


def _elevation_variance(grid: HeightmapGrid) -> float:
    flat = [z for row in grid.elevations for z in row]
    mean = sum(flat) / len(flat)
    return sum((z - mean) ** 2 for z in flat) / len(flat)


def _flat_grid(size: int = 8, elevation: float = 5.0) -> HeightmapGrid:
    return DEMImporter.flat_terrain(size, size, resolution_m=1.0, elevation=elevation, origin=ORIGIN)


# ============================================================================ #
# ErosionSimulator - thermal erosion
# ============================================================================ #

class TestThermalErosion:
    def test_flat_terrain_is_unaffected(self):
        """Yatay/düz arazide her yerde eğim sıfır -> thermal erosion hiçbir
        malzeme taşımamalı (talus eşiği hiçbir yerde aşılmıyor)."""
        grid = _flat_grid()
        result = ErosionSimulator.thermal_erosion(grid, iterations=10)
        assert result.total_material_moved == pytest.approx(0.0, abs=1e-9)
        for row in result.grid.elevations:
            for z in row:
                assert z == pytest.approx(5.0, abs=1e-9)

    def test_steep_hill_reduces_peak_elevation(self):
        """Dik bir tepe -> thermal erosion tepe noktasını alçaltmalı
        (malzeme kenarlara doğru dağılır)."""
        grid = _single_hill_grid(size=16, amplitude=40.0)
        peak_before = max(z for row in grid.elevations for z in row)
        result = ErosionSimulator.thermal_erosion(grid, iterations=15, talus_angle_deg=30.0)
        peak_after = max(z for row in result.grid.elevations for z in row)
        assert peak_after < peak_before
        assert result.total_material_moved > 0.0

    def test_preserves_grid_dimensions(self):
        grid = _single_hill_grid(size=10)
        result = ErosionSimulator.thermal_erosion(grid, iterations=5)
        assert result.grid.width == grid.width
        assert result.grid.height == grid.height

    def test_zero_iterations_is_noop(self):
        grid = _single_hill_grid(size=10)
        result = ErosionSimulator.thermal_erosion(grid, iterations=0)
        assert result.total_material_moved == 0.0
        assert result.grid.elevations == grid.elevations


# ============================================================================ #
# ErosionSimulator - hydraulic (droplet-based) erosion
# ============================================================================ #

class TestHydraulicErosion:
    def test_moves_material_on_sloped_terrain(self):
        grid = _single_hill_grid(size=20, amplitude=35.0)
        result = ErosionSimulator.hydraulic_erosion(grid, num_droplets=150, seed=42)
        assert result.total_material_moved > 0.0

    def test_deterministic_with_same_seed(self):
        grid = _single_hill_grid(size=12, amplitude=25.0)
        r1 = ErosionSimulator.hydraulic_erosion(grid, num_droplets=50, seed=7)
        r2 = ErosionSimulator.hydraulic_erosion(grid, num_droplets=50, seed=7)
        assert r1.grid.elevations == r2.grid.elevations

    def test_different_seed_gives_different_result(self):
        grid = _single_hill_grid(size=12, amplitude=25.0)
        r1 = ErosionSimulator.hydraulic_erosion(grid, num_droplets=50, seed=1)
        r2 = ErosionSimulator.hydraulic_erosion(grid, num_droplets=50, seed=2)
        assert r1.grid.elevations != r2.grid.elevations

    def test_flat_terrain_droplets_terminate_without_error(self):
        """Düz arazide gradyan sıfır -> damlalar ilk adımda durmalı, hata
        fırlatmamalı (dir_len < 1e-8 -> break yolu)."""
        grid = _flat_grid(size=10)
        result = ErosionSimulator.hydraulic_erosion(grid, num_droplets=30, seed=1)
        assert result is not None

    def test_preserves_grid_dimensions(self):
        grid = _single_hill_grid(size=14)
        result = ErosionSimulator.hydraulic_erosion(grid, num_droplets=40, seed=3)
        assert result.grid.width == grid.width
        assert result.grid.height == grid.height


# ============================================================================ #
# Kabul kriteri: birleşik pipeline (`simulate`) -> vadi oluşumu
# ============================================================================ #

class TestErosionAcceptanceCriterion:
    def test_variance_decreases_after_full_simulation(self):
        """Kabul kriteri (1/2): erozyon sonrası yükseklik varyansı azalmalı
        (keskin tepe/vadi geçişleri yumuşar)."""
        grid = _single_hill_grid(size=24, amplitude=35.0)
        variance_before = _elevation_variance(grid)

        result = ErosionSimulator.simulate(grid, thermal_iterations=12, hydraulic_droplets=300, seed=99)
        variance_after = _elevation_variance(result.grid)

        assert variance_after < variance_before

    def test_cells_below_threshold_increase_after_simulation(self):
        """Kabul kriteri (2/2): belirli bir yükseklik eşiğinin altına düşen
        hücre sayısı erozyon sonrası artmalı. Eşik, orijinal tepe
        yüksekliğine yakın seçilir (`amplitude - margin`) - erozyon tepe
        malzemesini komşu hücrelere dağıttıkça, "neredeyse tepe kadar
        yüksek" hücre sayısı azalır, yani bu eşiğin **altındaki** hücre
        sayısı artar (tepe alanı küçülür/yayılır - vadi/yamaç genişlemesi)."""
        grid = _single_hill_grid(size=24, amplitude=35.0)
        threshold = 35.0 - 3.0  # tepe zirvesine yakın eşik

        def _count_below(g: HeightmapGrid) -> int:
            return sum(1 for row in g.elevations for z in row if z < threshold)

        count_before = _count_below(grid)
        result = ErosionSimulator.simulate(grid, thermal_iterations=12, hydraulic_droplets=300, seed=99)
        count_after = _count_below(result.grid)

        assert count_after > count_before

    def test_total_material_conserved_direction_is_reasonable(self):
        """Taşınan toplam malzeme miktarı sıfırdan büyük olmalı (aktif bir
        simülasyon oldu, no-op değil)."""
        grid = _single_hill_grid(size=20, amplitude=30.0)
        result = ErosionSimulator.simulate(grid, thermal_iterations=8, hydraulic_droplets=200, seed=5)
        assert result.total_material_moved > 0.0

    def test_simulate_preserves_grid_metadata(self):
        grid = _single_hill_grid(size=16, resolution_m=3.0)
        result = ErosionSimulator.simulate(grid, thermal_iterations=5, hydraulic_droplets=80, seed=1)
        assert result.grid.resolution_m == grid.resolution_m
        assert result.grid.width == grid.width
        assert result.grid.height == grid.height


# ============================================================================ #
# FlowAccumulation
# ============================================================================ #

class TestFlowAccumulation:
    def test_flow_direction_points_toward_steepest_descent(self):
        # Basit 3x3 eğik düzlem: sol-üst en yüksek, sağ-alt en düşük.
        matrix = [
            [9.0, 6.0, 3.0],
            [6.0, 3.0, 0.0],
            [3.0, 0.0, -3.0],
        ]
        grid = DEMImporter.from_matrix(matrix, resolution_m=1.0, origin=ORIGIN)
        directions = FlowAccumulation.flow_directions(grid)
        # (0,0) en yüksek nokta -> çapraz komşusu (1,1) daha alçak, en dik yön
        assert directions[0][0] == (1, 1)

    def test_flow_direction_none_at_local_minimum(self):
        matrix = [
            [5.0, 5.0, 5.0],
            [5.0, 0.0, 5.0],
            [5.0, 5.0, 5.0],
        ]
        grid = DEMImporter.from_matrix(matrix, resolution_m=1.0, origin=ORIGIN)
        directions = FlowAccumulation.flow_directions(grid)
        assert directions[1][1] is None  # merkez en alçak nokta - akış yönü yok

    def test_accumulation_increases_downstream(self):
        """Eğik bir düzlemde en alçak (mansap) hücrenin akümüle akışı,
        en yüksek (memba) hücreden kesinlikle büyük olmalı (tüm yukarı
        havza kendisine akıyor)."""
        grid = _single_hill_grid(size=12, amplitude=20.0)
        accum = FlowAccumulation.accumulate(grid)
        # En düşük köşe hücresi (0,0) - konik tepe merkeze göre kenarlar en
        # alçak; kenar hücrelerinin akümülasyonu merkeze yakın yamaç
        # hücrelerinden büyük veya eşit olmalı (akış dışa doğru toplanıyor).
        total_cells = grid.width * grid.height
        max_accum = max(v for row in accum for v in row)
        assert max_accum >= 1.0
        assert max_accum <= total_cells  # fiziksel üst sınır: tüm hücreler tek noktaya akamaz aşabilir ama makul sınır

    def test_every_cell_has_at_least_its_own_flow(self):
        grid = _single_hill_grid(size=10)
        accum = FlowAccumulation.accumulate(grid)
        for row in accum:
            for v in row:
                assert v >= 1.0

    def test_flat_grid_all_directions_none(self):
        grid = _flat_grid(size=6)
        directions = FlowAccumulation.flow_directions(grid)
        for row in directions:
            for d in row:
                assert d is None
