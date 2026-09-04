"""Roadmap V10 / Faz 4 — Bina Hasarı/Yıkımı (kademeli).

4.1 (seviye 1 önceden tanımlı hasar) → 4.2 (RigidBox çöküş) → 4.3
(ön-hesaplanmış fragmentasyon + kademeli runtime tetikleme) → 4.4
(kalıcı durum) → 4.5 (dürüst etiketleme) kapsar.
"""
from __future__ import annotations

import pytest

from harita.hazard_data.risk_scoring import RiskLevel
from harita.mesh_engine import Mesh3D, MeshSplitter, Vertex3D
from harita.physics.building_damage import (
    DAMAGE_HONESTY_NOTE,
    DamageLevel,
    DamagePersistenceStore,
    Level2CollapseSimulator,
    RuntimeFragmentTrigger,
    compute_damage_level,
    is_fragmentation_eligible,
    level1_damage_state,
    precompute_fragments,
)


def _box_mesh(name="b", x=10.0, y=10.0, z=15.0) -> Mesh3D:
    verts = [Vertex3D(px, py, pz) for px in (0.0, x) for py in (0.0, y) for pz in (0.0, z)]
    triangles = [
        (0, 1, 2), (1, 2, 3),
        (4, 5, 6), (5, 6, 7),
        (0, 1, 4), (1, 4, 5),
        (2, 3, 6), (3, 6, 7),
        (0, 2, 4), (2, 4, 6),
        (1, 3, 5), (3, 5, 7),
    ]
    return Mesh3D(vertices=verts, triangles=triangles, name=name)


# --------------------------------------------------------------------------- #
# 4.1 — Seviye 1
# --------------------------------------------------------------------------- #

class TestFaz4_1Level1PredefinedDamage:
    def test_risk_level_maps_to_base_damage(self):
        assert compute_damage_level(RiskLevel.LOW) == DamageLevel.NONE
        assert compute_damage_level(RiskLevel.MODERATE) == DamageLevel.LIGHT
        assert compute_damage_level(RiskLevel.HIGH) == DamageLevel.MODERATE
        assert compute_damage_level(RiskLevel.VERY_HIGH) == DamageLevel.SEVERE

    def test_high_shake_intensity_bumps_one_level(self):
        base = compute_damage_level(RiskLevel.HIGH, peak_shake_intensity=0.0)
        bumped = compute_damage_level(RiskLevel.HIGH, peak_shake_intensity=0.9)
        levels = list(DamageLevel)
        assert levels.index(bumped) == levels.index(base) + 1

    def test_collapse_level_caps_at_max(self):
        # VERY_HIGH -> SEVERE base, +1 with high intensity -> COLLAPSED,
        # asla listenin dışına taşmamalı.
        level = compute_damage_level(RiskLevel.VERY_HIGH, peak_shake_intensity=0.99)
        assert level == DamageLevel.COLLAPSED

    def test_opacity_decreases_with_damage(self):
        none_state = level1_damage_state("b1", RiskLevel.LOW)
        severe_state = level1_damage_state("b1", RiskLevel.VERY_HIGH)
        assert none_state.opacity > severe_state.opacity

    def test_state_includes_honesty_note(self):
        state = level1_damage_state("b1", RiskLevel.HIGH)
        assert state.honesty_note == DAMAGE_HONESTY_NOTE
        assert "temsili" in state.honesty_note


# --------------------------------------------------------------------------- #
# 4.2 — Seviye 2 (RigidBox)
# --------------------------------------------------------------------------- #

class TestFaz4_2Level2RigidBoxCollapse:
    def test_weak_structure_strong_shake_can_topple(self):
        sim = Level2CollapseSimulator("bWeak", num_floors=5)
        states = sim.run(
            peak_acceleration_g=1.8, duration_s=5.0, structural_integrity=0.05,
        )
        assert len(states) == 5
        assert sim.any_floor_collapsed(states)

    def test_no_shake_stays_stable(self):
        sim = Level2CollapseSimulator("bStable", num_floors=3)
        states = sim.run(peak_acceleration_g=0.0, duration_s=3.0, structural_integrity=1.0)
        assert not sim.any_floor_collapsed(states)

    def test_floor_states_are_ordered_by_index(self):
        sim = Level2CollapseSimulator("bOrder", num_floors=4)
        states = sim.run(peak_acceleration_g=0.5, duration_s=1.0)
        assert [s.floor_index for s in states] == [0, 1, 2, 3]


# --------------------------------------------------------------------------- #
# 4.3 — Seviye 3 (fragmentasyon)
# --------------------------------------------------------------------------- #

class TestFaz4_3FragmentationPrecompute:
    def test_precompute_produces_multiple_pieces(self):
        mesh = _box_mesh()
        frags = precompute_fragments(mesh, building_id="bFrag", pieces_x=3, pieces_y=3)
        assert 1 < len(frags) <= 9

    def test_precompute_is_deterministic(self):
        mesh = _box_mesh()
        frags_a = precompute_fragments(mesh, building_id="bFrag", pieces_x=3, pieces_y=2)
        frags_b = precompute_fragments(mesh, building_id="bFrag", pieces_x=3, pieces_y=2)
        assert [f.piece_id for f in frags_a] == [f.piece_id for f in frags_b]
        assert [f.center for f in frags_a] == [f.center for f in frags_b]

    def test_pieces_within_original_bounds(self):
        mesh = _box_mesh(x=10.0, y=10.0, z=15.0)
        frags = precompute_fragments(mesh, building_id="bFrag", pieces_x=2, pieces_y=2)
        for frag in frags:
            cx, cy, cz = frag.center
            assert -0.5 <= cx <= 10.5
            assert -0.5 <= cy <= 10.5
            assert -0.5 <= cz <= 15.5

    def test_invalid_piece_counts_rejected(self):
        mesh = _box_mesh()
        with pytest.raises(ValueError):
            precompute_fragments(mesh, building_id="b", pieces_x=0, pieces_y=1)


class TestFaz4_3EligibilityGate:
    def test_eligible_when_focused_and_close(self):
        assert is_fragmentation_eligible(is_camera_focused=True, distance_to_camera_m=10.0)

    def test_not_eligible_when_far(self):
        assert not is_fragmentation_eligible(is_camera_focused=True, distance_to_camera_m=500.0)

    def test_not_eligible_when_not_focused(self):
        assert not is_fragmentation_eligible(is_camera_focused=False, distance_to_camera_m=5.0)


class TestFaz4_3RuntimeTrigger:
    def test_trigger_builds_world_when_eligible(self):
        mesh = _box_mesh()
        frags = precompute_fragments(mesh, building_id="bTrig", pieces_x=2, pieces_y=2)
        trigger = RuntimeFragmentTrigger("bTrig")
        world = trigger.trigger(
            frags, peak_acceleration_g=1.0, is_camera_focused=True, distance_to_camera_m=5.0,
        )
        assert world is not None
        # +1 for the static ground body.
        assert len(world.bodies) == len(frags) + 1

    def test_trigger_returns_none_when_not_eligible(self):
        mesh = _box_mesh()
        frags = precompute_fragments(mesh, building_id="bTrig2", pieces_x=2, pieces_y=2)
        trigger = RuntimeFragmentTrigger("bTrig2")
        world = trigger.trigger(
            frags, peak_acceleration_g=1.0, is_camera_focused=False, distance_to_camera_m=500.0,
        )
        assert world is None

    def test_triggered_fragments_fall_under_gravity(self):
        mesh = _box_mesh()
        frags = precompute_fragments(mesh, building_id="bFall", pieces_x=2, pieces_y=2)
        trigger = RuntimeFragmentTrigger("bFall")
        world = trigger.trigger(
            frags, peak_acceleration_g=0.5, is_camera_focused=True, distance_to_camera_m=1.0,
        )
        initial_z = [b.position[2] for b in world.bodies if not b.is_static]
        world.run(1.0)
        final_z = [b.position[2] for b in world.bodies if not b.is_static]
        assert sum(final_z) < sum(initial_z)


# --------------------------------------------------------------------------- #
# 4.4 — Kalıcı durum
# --------------------------------------------------------------------------- #

class TestFaz4_4PersistenceStore:
    def test_record_and_get(self):
        store = DamagePersistenceStore()
        state = level1_damage_state("b1", RiskLevel.HIGH)
        store.record("scn1", state)
        assert store.get("scn1", "b1").damage_level == DamageLevel.MODERATE

    def test_does_not_downgrade_damage(self):
        store = DamagePersistenceStore()
        severe = level1_damage_state("b1", RiskLevel.VERY_HIGH)
        light = level1_damage_state("b1", RiskLevel.MODERATE)
        store.record("scn1", severe)
        store.record("scn1", light)  # daha hafif - göz ardı edilmeli
        assert store.get("scn1", "b1").damage_level == DamageLevel.SEVERE

    def test_scenarios_are_isolated(self):
        store = DamagePersistenceStore()
        store.record("scnA", level1_damage_state("b1", RiskLevel.HIGH))
        assert store.get("scnB", "b1") is None

    def test_all_for_scenario(self):
        store = DamagePersistenceStore()
        store.record("scn1", level1_damage_state("b1", RiskLevel.HIGH))
        store.record("scn1", level1_damage_state("b2", RiskLevel.LOW))
        store.record("scn2", level1_damage_state("b3", RiskLevel.LOW))
        assert len(store.all_for_scenario("scn1")) == 2

    def test_reset_scenario(self):
        store = DamagePersistenceStore()
        store.record("scn1", level1_damage_state("b1", RiskLevel.HIGH))
        removed = store.reset_scenario("scn1")
        assert removed == 1
        assert store.get("scn1", "b1") is None


# --------------------------------------------------------------------------- #
# 4.5 — Dürüst etiketleme (her katmanda mevcut olmalı)
# --------------------------------------------------------------------------- #

class TestFaz4_5HonestyLabeling:
    def test_level1_state_always_carries_note(self):
        for risk in RiskLevel:
            state = level1_damage_state("b1", risk)
            assert state.honesty_note

    def test_engineering_mode_keeps_a_note_but_updates_it(self):
        default_state = level1_damage_state("b1", RiskLevel.HIGH, engineering_mode=False)
        eng_state = level1_damage_state("b1", RiskLevel.HIGH, engineering_mode=True)
        assert default_state.honesty_note != eng_state.honesty_note
        assert eng_state.honesty_note  # not asla kaldırılmaz


# --------------------------------------------------------------------------- #
# Faz 4 kapsayan `MeshSplitter.split_by_axis_plane` genelleme regresyonu
# --------------------------------------------------------------------------- #

class TestMeshSplitterAxisGeneralization:
    def test_z_split_matches_legacy_split_by_plane(self):
        mesh = _box_mesh()
        below_legacy, above_legacy = MeshSplitter.split_by_plane(mesh, plane_z=7.5)
        below_generic, above_generic = MeshSplitter.split_by_axis_plane(mesh, axis=2, value=7.5)
        assert below_legacy.triangle_count() == below_generic.triangle_count()
        assert above_legacy.triangle_count() == above_generic.triangle_count()

    def test_x_and_y_axis_split_work(self):
        mesh = _box_mesh()
        lo_x, hi_x = MeshSplitter.split_by_axis_plane(mesh, axis=0, value=5.0)
        lo_y, hi_y = MeshSplitter.split_by_axis_plane(mesh, axis=1, value=5.0)
        assert lo_x.triangle_count() + hi_x.triangle_count() == mesh.triangle_count()
        assert lo_y.triangle_count() + hi_y.triangle_count() == mesh.triangle_count()
