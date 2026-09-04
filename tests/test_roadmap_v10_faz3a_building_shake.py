"""
Roadmap V10 / Faz 3.A — "Bina Fiziği: Sallanma (Standart seviye)"
regresyon testleri.

Kapsanan maddeler:
- 3.1 — `BuildingShakeSimulator.state_at()`: `GroundShakeForceModel`'e
  bağlı bina kök transform üretimi (uçtan uca, sıfır ivmede sıfır ofset).
- 3.2 — Yükseklikle artan genlik: üst kat, zemin kata göre daha çok
  sallanmalı.
- 3.4 — Yapısal tip farkı: aynı zemin ivmesi altında farklı
  `BasicBuildingType` farklı tepki genliği/karakteri üretmeli.
- 3.3 — `panic_probability_from_intensity` + `apply_shake_panic`:
  şiddet arttıkça panik olasılığı monotonik artmalı; uçtan uca en az bir
  ajanı panikletebilmeli.
- 3.5 — `debris_particle_state`: eşik altı sessiz, eşik üstü kademeli
  emisyon.
"""

from __future__ import annotations

from harita.core_engine.geometry_engine import Point2D
from harita.hazard_data.risk_scoring import BasicBuildingType
from harita.mobility.crowd_simulation import Agent, AgentBehavior
from harita.physics import GroundShakeForceModel
from harita.physics.building_shake import (
    STRUCTURE_SHAKE_PROFILES,
    BuildingShakeSimulator,
    apply_shake_panic,
    debris_particle_state,
    panic_probability_from_intensity,
)


class TestFaz3_1RootTransform:
    def test_zero_peak_acceleration_gives_zero_offset(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.0)
        sim = BuildingShakeSimulator(building_id="b1", shake_model=shake, num_floors=5)
        state = sim.state_at(t=1.0, floor_index=2)
        assert state.horizontal_offset_m[0] == 0.0
        assert state.intensity == 0.0

    def test_nonzero_peak_acceleration_gives_nonzero_offset_at_some_t(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.3, frequency_hz=2.0)
        sim = BuildingShakeSimulator(
            building_id="b1", shake_model=shake,
            structure_type=BasicBuildingType.BETONARME_CERCEVE, num_floors=5,
        )
        offsets = [sim.state_at(t=t * 0.05, floor_index=3).horizontal_offset_m[0]
                   for t in range(40)]
        assert any(abs(o) > 1e-6 for o in offsets)

    def test_negative_floor_index_rejected(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.2)
        sim = BuildingShakeSimulator(building_id="b1", shake_model=shake)
        try:
            sim.state_at(t=0.0, floor_index=-1)
            assert False, "negatif floor_index reddedilmeliydi"
        except ValueError:
            pass


class TestFaz3_2HeightBasedAmplitude:
    def test_higher_floor_shakes_more(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.25, frequency_hz=1.8)
        sim = BuildingShakeSimulator(
            building_id="b1", shake_model=shake,
            structure_type=BasicBuildingType.BETONARME_CERCEVE, num_floors=10,
        )
        t = 0.13
        ground_floor = abs(sim.state_at(t, floor_index=0).horizontal_offset_m[0])
        top_floor = abs(sim.state_at(t, floor_index=9).horizontal_offset_m[0])
        assert top_floor > ground_floor


class TestFaz3_4StructureTypeDifferentiation:
    def test_all_basic_building_types_have_a_profile(self):
        for bt in BasicBuildingType:
            assert bt in STRUCTURE_SHAKE_PROFILES

    def test_yigma_has_higher_frequency_lower_damping_than_celik(self):
        yigma = STRUCTURE_SHAKE_PROFILES[BasicBuildingType.YIGMA]
        celik = STRUCTURE_SHAKE_PROFILES[BasicBuildingType.CELIK_CERCEVE]
        assert yigma.natural_frequency_hz > celik.natural_frequency_hz
        assert yigma.damping_ratio < celik.damping_ratio

    def test_different_structure_types_respond_differently(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.3, frequency_hz=2.0)
        results = {}
        for bt in BasicBuildingType:
            sim = BuildingShakeSimulator(
                building_id="b1", shake_model=shake, structure_type=bt, num_floors=5)
            results[bt] = sim.state_at(t=0.3, floor_index=4).horizontal_offset_m[0]
        assert len(set(round(v, 6) for v in results.values())) > 1

    def test_unknown_structure_type_falls_back_to_default(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.2)
        sim = BuildingShakeSimulator(building_id="b1", shake_model=shake, structure_type=None)
        # Çökmeden bir sonuç üretmeli (dürüst varsayılan, hata değil).
        state = sim.state_at(t=0.5, floor_index=1)
        assert state.building_id == "b1"


class TestFaz3_3PanicFeedback:
    def test_probability_is_monotonic_increasing(self):
        values = [panic_probability_from_intensity(i / 10) for i in range(11)]
        for a, b in zip(values, values[1:]):
            assert b >= a

    def test_zero_intensity_never_panics(self):
        assert panic_probability_from_intensity(0.0) == 0.0

    def test_high_intensity_can_trigger_panic(self):
        agents = [
            Agent(agent_id=i, position=Point2D(0, 0), goal=Point2D(10, 0))
            for i in range(30)
        ]
        newly_panicked = apply_shake_panic(agents, intensity=1.0, seed=7)
        assert newly_panicked > 0
        assert any(a.behavior == AgentBehavior.PANIC for a in agents)

    def test_evacuated_agents_are_never_panicked(self):
        agent = Agent(agent_id=1, position=Point2D(0, 0), goal=Point2D(0, 0), evacuated=True)
        apply_shake_panic([agent], intensity=1.0, seed=1)
        assert agent.behavior != AgentBehavior.PANIC

    def test_already_panicked_agents_are_not_recounted(self):
        agent = Agent(agent_id=1, position=Point2D(0, 0), goal=Point2D(10, 0),
                      behavior=AgentBehavior.PANIC)
        newly_panicked = apply_shake_panic([agent], intensity=1.0, seed=1)
        assert newly_panicked == 0


class TestFaz3_5DebrisParticles:
    def test_low_intensity_emits_nothing(self):
        state = debris_particle_state(0.05)
        assert state.emit is False
        assert state.emission_rate_per_s == 0.0

    def test_mid_intensity_emits_dust(self):
        state = debris_particle_state(0.2)
        assert state.emit is True
        assert state.particle_type == "siva_tozu"
        assert state.emission_rate_per_s > 0

    def test_high_intensity_emits_glass(self):
        state = debris_particle_state(0.9)
        assert state.emit is True
        assert state.particle_type == "cam_kirigi"
        assert state.emission_rate_per_s > 0

    def test_emission_rate_increases_with_intensity(self):
        low = debris_particle_state(0.2).emission_rate_per_s
        high = debris_particle_state(0.34).emission_rate_per_s
        assert high >= low
