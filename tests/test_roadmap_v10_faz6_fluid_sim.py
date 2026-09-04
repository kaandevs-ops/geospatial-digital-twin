"""Roadmap V10 / Faz 6 — Gerçek Zamanlı Akışkan/Duman CFD Simülasyonu
(Deneysel).

6.2.1 (Stable Fluids çözücü) -> 6.2.2 (kademeli devreye alma, tek bina) ->
6.2.3 (ısı kaynağı = FireSpreadModel) -> 6.3 (zorunlu sprite fallback)
kapsar.
"""
from __future__ import annotations

import math

import pytest

from harita.core_engine.geometry_engine import Point2D
from harita.hazard_data.fire_spread import FireSpreadModel
from harita.physics.fluid_sim import (
    CFD_EXPERIMENTAL_HONESTY_NOTE,
    FluidGridConfig,
    SmokeVoxel,
    StableFluidsSimulation,
    cfd_or_sprite_frame,
    is_cfd_eligible,
    voxel_frame,
)


def _small_sim(**overrides) -> StableFluidsSimulation:
    params = {"nx": 6, "ny": 6, "nz": 6, "cell_size_m": 0.5}
    params.update(overrides)
    return StableFluidsSimulation(FluidGridConfig(**params))


class TestFluidGridConfig:
    def test_defaults_are_sane(self):
        cfg = FluidGridConfig()
        assert cfg.nx == 16 and cfg.ny == 16 and cfg.nz == 8
        assert cfg.cell_count == 16 * 16 * 8

    def test_rejects_degenerate_grid(self):
        with pytest.raises(ValueError):
            FluidGridConfig(nx=1)
        with pytest.raises(ValueError):
            FluidGridConfig(cell_size_m=0.0)


class TestStableFluidsSimulationBasics:
    def test_initial_state_is_zero(self):
        sim = _small_sim()
        assert sum(sim.density) == 0.0
        assert sum(sim.vx) == sum(sim.vy) == sum(sim.vz) == 0.0

    def test_index_out_of_bounds_raises(self):
        sim = _small_sim()
        with pytest.raises(IndexError):
            sim.index(99, 0, 0)

    def test_add_density_source_then_step_increases_density(self):
        sim = _small_sim()
        sim.add_density_source(3, 3, 0, amount=5.0)
        sim.step(dt=0.1)
        assert sim.density_at(3, 3, 0) > 0.0

    def test_out_of_bounds_source_is_silently_ignored(self):
        sim = _small_sim()
        sim.add_density_source(999, 999, 999, amount=5.0)
        # Ne exception ne de yan etki - roadmap disiplini: sessizce yok say.
        sim.step(dt=0.1)
        assert sum(sim.density) == 0.0

    def test_heat_source_creates_upward_buoyancy(self):
        sim = _small_sim()
        sim.add_heat_source(3, 3, 0, amount=5.0)
        sim.step(dt=0.05)
        # Isı kaynağı +z yönünde kaldırma kuvveti üretmeli (6.2.3).
        assert sim.vz[sim.index(3, 3, 0)] > 0.0

    def test_step_advances_elapsed_time_and_count(self):
        sim = _small_sim()
        sim.step(dt=0.2)
        sim.step(dt=0.2)
        assert sim.step_count == 2
        assert math.isclose(sim.elapsed_s, 0.4, rel_tol=1e-9)

    def test_density_stays_bounded_after_many_steps(self):
        sim = _small_sim()
        sim.add_density_source(3, 3, 0, amount=8.0)
        sim.add_heat_source(3, 3, 0, amount=8.0)
        for _ in range(15):
            sim.step(dt=0.08)
            sim.add_density_source(3, 3, 0, amount=1.0)
        assert all(0.0 <= d <= 1.0001 for d in sim.density)
        assert not sim.diverged

    def test_closed_boundary_cell_has_no_density(self):
        sim = _small_sim()
        sim.set_boundary(3, 3, 3, closed=True)
        sim.add_density_source(3, 3, 3, amount=5.0)
        for _ in range(5):
            sim.step(dt=0.1)
        assert sim.density_at(3, 3, 3) == 0.0

    def test_density_diffuses_to_neighbouring_cells(self):
        sim = _small_sim()
        sim.add_density_source(3, 3, 3, amount=10.0)
        for _ in range(6):
            sim.step(dt=0.1)
        neighbour_total = (
            sim.density_at(2, 3, 3) + sim.density_at(4, 3, 3)
            + sim.density_at(3, 2, 3) + sim.density_at(3, 4, 3)
        )
        assert neighbour_total > 0.0


class TestFireHeatSourceInjection:
    def _fire_model(self) -> FireSpreadModel:
        model = FireSpreadModel(width=6, height=6, ignition_cells=[(2, 2)], seed=7)
        model.run(duration_s=6.0, dt=1.0)
        return model

    def test_injects_only_non_clear_cells(self):
        sim = _small_sim()
        model = self._fire_model()
        injected = sim.inject_fire_heat_source(
            model, cell_to_grid_xy=lambda cell: cell, source_z=0,
        )
        non_clear = sum(1 for c in model.intensity if model.state_of(c).value != "clear")
        assert injected == non_clear
        assert injected > 0

    def test_injection_then_step_produces_density(self):
        sim = _small_sim()
        model = self._fire_model()
        sim.inject_fire_heat_source(model, cell_to_grid_xy=lambda cell: cell, source_z=0)
        sim.step(dt=0.1)
        assert sum(sim.density) > 0.0

    def test_out_of_grid_fire_cells_are_skipped(self):
        sim = _small_sim(nx=2, ny=2, nz=2)
        model = self._fire_model()  # 6x6 ızgara, sim yalnızca 2x2
        injected = sim.inject_fire_heat_source(
            model, cell_to_grid_xy=lambda cell: cell, source_z=0,
        )
        # 2x2x2 ızgarada yalnızca x<2 ve y<2 olan (en fazla 4) hücre var
        # olabilir - 6x6 modeldeki geri kalan tüm hücreler sessizce atlanır.
        assert injected <= 4


class TestCfdEligibility:
    def test_not_eligible_when_not_focused(self):
        assert not is_cfd_eligible(
            is_camera_focused=False, active_cfd_building_count=0, distance_to_camera_m=5.0,
        )

    def test_not_eligible_when_too_far(self):
        assert not is_cfd_eligible(
            is_camera_focused=True, active_cfd_building_count=0, distance_to_camera_m=100.0,
        )

    def test_not_eligible_when_concurrency_limit_reached(self):
        assert not is_cfd_eligible(
            is_camera_focused=True, active_cfd_building_count=1, distance_to_camera_m=5.0,
        )

    def test_eligible_when_all_conditions_met(self):
        assert is_cfd_eligible(
            is_camera_focused=True, active_cfd_building_count=0, distance_to_camera_m=5.0,
        )


class TestVoxelFrame:
    def test_voxels_below_threshold_are_excluded(self):
        sim = _small_sim()
        sim.add_density_source(1, 1, 1, amount=0.0001)
        sim.step(dt=0.01)
        voxels = voxel_frame(sim, density_threshold=0.5)
        assert voxels == []

    def test_voxel_world_position_uses_origin_and_cell_size(self):
        sim = _small_sim(cell_size_m=1.0)
        sim.add_density_source(2, 1, 0, amount=8.0)
        sim.step(dt=0.1)
        voxels = voxel_frame(sim, origin_world=(10.0, 20.0, 0.0), density_threshold=0.0)
        target = next(v for v in voxels if v.grid_xyz == (2, 1, 0))
        assert target.world_position == (12.0, 21.0, 0.0)

    def test_every_voxel_carries_honesty_note(self):
        sim = _small_sim()
        sim.add_density_source(1, 1, 1, amount=5.0)
        sim.step(dt=0.1)
        voxels = voxel_frame(sim, density_threshold=0.0)
        assert voxels, "test setup should produce at least one voxel"
        assert all(v.honesty_note == CFD_EXPERIMENTAL_HONESTY_NOTE for v in voxels)


class TestCfdOrSpriteFallback:
    def _fire_model(self) -> FireSpreadModel:
        model = FireSpreadModel(width=4, height=4, ignition_cells=[(1, 1)], seed=3)
        model.run(duration_s=4.0, dt=1.0)
        return model

    def test_falls_back_to_sprites_when_not_eligible(self):
        model = self._fire_model()
        result = cfd_or_sprite_frame(
            model, "b1",
            sim=None,
            is_camera_focused=False,
            active_cfd_building_count=0,
            distance_to_camera_m=5.0,
            cell_to_world=lambda cell: Point2D(cell[0], cell[1]),
        )
        assert result["mode"] == "sprite_fallback"
        assert isinstance(result["sprites"], list)

    def test_falls_back_when_sim_not_provided_even_if_eligible(self):
        model = self._fire_model()
        result = cfd_or_sprite_frame(
            model, "b1",
            sim=None,
            is_camera_focused=True,
            active_cfd_building_count=0,
            distance_to_camera_m=1.0,
            cell_to_world=lambda cell: Point2D(cell[0], cell[1]),
        )
        assert result["mode"] == "sprite_fallback"

    def test_falls_back_when_sim_diverged(self):
        model = self._fire_model()
        sim = _small_sim()
        sim.diverged = True
        result = cfd_or_sprite_frame(
            model, "b1",
            sim=sim,
            is_camera_focused=True,
            active_cfd_building_count=0,
            distance_to_camera_m=1.0,
            cell_to_world=lambda cell: Point2D(cell[0], cell[1]),
        )
        assert result["mode"] == "sprite_fallback"

    def test_uses_cfd_when_eligible_and_sim_healthy(self):
        model = self._fire_model()
        sim = _small_sim()
        sim.inject_fire_heat_source(model, cell_to_grid_xy=lambda c: c, source_z=0)
        sim.step(dt=0.1)
        result = cfd_or_sprite_frame(
            model, "b1",
            sim=sim,
            is_camera_focused=True,
            active_cfd_building_count=0,
            distance_to_camera_m=1.0,
            cell_to_world=lambda cell: Point2D(cell[0], cell[1]),
        )
        assert result["mode"] == "cfd"
        assert "voxels" in result
