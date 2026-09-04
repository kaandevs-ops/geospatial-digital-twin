"""Roadmap V4 - Track E / Faz E17: Physics (rijit cisim fiziği) testleri."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.physics import (
    ContactManifold,
    GroundShakeForceModel,
    PhysicsWorld,
    RigidBox,
    TowerStabilityScenario,
    detect_collisions,
)


class TestRigidBox:
    def test_rejects_zero_mass_dynamic_body(self):
        with pytest.raises(ValueError):
            RigidBox("b", (0, 0, 0), (1, 1, 1), mass=0)

    def test_static_body_allows_zero_mass(self):
        body = RigidBox("g", (0, 0, 0), (1, 1, 1), mass=0, is_static=True)
        assert body.inverse_mass() == 0.0

    def test_aabb_matches_extents_when_upright(self):
        body = RigidBox("b", (1, 2, 3), (1, 2, 3))
        box = body.aabb()
        assert box.min_x == 0 and box.max_x == 2
        assert box.min_z == 0 and box.max_z == 6

    def test_not_toppled_when_upright(self):
        body = RigidBox("b", (0, 0, 1), (1, 1, 1))
        assert not body.is_toppled()

    def test_toppled_when_orientation_exceeds_critical_angle(self):
        body = RigidBox("b", (0, 0, 1), (1, 1, 1))
        body.orientation = math.radians(80)
        assert body.is_toppled()


class TestGroundShakeForceModel:
    def test_zero_force_on_static_body(self):
        model = GroundShakeForceModel(peak_acceleration_g=0.5)
        static_body = RigidBox("g", (0, 0, 0), (1, 1, 1), is_static=True)
        assert model.force_on(static_body, t=0.0) == (0.0, 0.0, 0.0)

    def test_force_scales_with_mass_and_peak_g(self):
        model = GroundShakeForceModel(peak_acceleration_g=1.0, frequency_hz=1.0, phase=math.pi / 2)
        body = RigidBox("b", (0, 0, 1), (1, 1, 1), mass=10.0)
        fx, fy, fz = model.force_on(body, t=0.0)
        # phase=pi/2 -> sin(pi/2) = 1 -> tepe ivme.
        assert fx == pytest.approx(10.0 * 1.0 * 9.81, rel=1e-6)
        assert fy == 0.0 and fz == 0.0


class TestCollisionDetection:
    def test_no_contact_when_far_apart(self):
        a = RigidBox("a", (0, 0, 0), (1, 1, 1))
        b = RigidBox("b", (100, 100, 100), (1, 1, 1))
        assert detect_collisions([a, b]) == []

    def test_contact_when_overlapping(self):
        a = RigidBox("a", (0, 0, 0), (1, 1, 1))
        b = RigidBox("b", (1.5, 0, 0), (1, 1, 1))
        contacts = detect_collisions([a, b])
        assert len(contacts) == 1
        assert isinstance(contacts[0], ContactManifold)
        assert contacts[0].penetration > 0

    def test_two_static_bodies_skip_collision(self):
        a = RigidBox("a", (0, 0, 0), (1, 1, 1), is_static=True)
        b = RigidBox("b", (0.5, 0, 0), (1, 1, 1), is_static=True)
        assert detect_collisions([a, b]) == []


class TestPhysicsWorld:
    def test_body_falls_under_gravity(self):
        world = PhysicsWorld()
        box = RigidBox("b", (0, 0, 100), (1, 1, 1), mass=1)
        world.add_body(box)
        world.step(1.0 / 60.0)
        assert box.velocity[2] < 0

    def test_body_settles_on_static_ground(self):
        world = PhysicsWorld()
        ground = RigidBox("g", (0, 0, -1), (10, 10, 1), is_static=True)
        box = RigidBox("b", (0, 0, 5), (1, 1, 1), mass=10)
        world.add_body(ground)
        world.add_body(box)
        world.run(duration_s=3.0)
        # Zemin üstü (z=0) civarında dinlenmeli, zeminin içine batmamalı.
        assert box.position[2] == pytest.approx(1.0, abs=0.5)
        assert box.position[2] > -0.5

    def test_static_ground_never_moves(self):
        world = PhysicsWorld()
        ground = RigidBox("g", (0, 0, -1), (10, 10, 1), is_static=True)
        box = RigidBox("b", (0, 0, 5), (1, 1, 1), mass=10)
        world.add_body(ground)
        world.add_body(box)
        world.run(duration_s=2.0)
        assert ground.position == (0, 0, -1)


class TestTowerStabilityScenario:
    def test_stable_under_low_acceleration(self):
        scenario = TowerStabilityScenario(num_blocks=4)
        toppled = scenario.run_stability_test(peak_acceleration_g=0.02, duration_s=6.0)
        assert toppled is False

    def test_topples_under_high_acceleration(self):
        scenario = TowerStabilityScenario(num_blocks=4)
        toppled = scenario.run_stability_test(peak_acceleration_g=1.0, duration_s=6.0)
        assert toppled is True

    def test_build_world_contains_ground_and_blocks(self):
        scenario = TowerStabilityScenario(num_blocks=3)
        world = scenario.build_world(peak_acceleration_g=0.0)
        assert len(world.bodies) == 4  # 1 zemin + 3 blok
        assert world.bodies[0].is_static
