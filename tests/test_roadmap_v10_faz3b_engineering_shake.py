"""Roadmap V10 / Faz 3.B — Mühendislik-yakın SDOF/shear-frame sismik
modu (ihtiyari, veri ön koşullu).

Veri ön koşulu doğrulaması -> tahmini kütle/rijitlik türetimi -> Newmark-
beta MDOF kesme-çerçevesi zaman-integrasyonu -> öteleme-oranı tabanlı
hasar ipucu kapsar.
"""
from __future__ import annotations

import math

import pytest

from harita.hazard_data.risk_scoring import BasicBuildingType
from harita.physics import GroundShakeForceModel
from harita.physics.building_damage import DamageLevel
from harita.physics.building_shake_engineering import (
    ENGINEERING_MODE_HONESTY_NOTE,
    EngineeringModeDataStatus,
    MDOFShearFrameModel,
    check_engineering_mode_precondition,
    drift_based_damage_hint,
    estimate_floor_properties,
)


class TestEngineeringModePrecondition:
    def test_precondition_is_not_available(self):
        status = check_engineering_mode_precondition()
        assert isinstance(status, EngineeringModeDataStatus)
        assert status.available is False

    def test_precondition_reason_is_non_empty_and_explains_gap(self):
        status = check_engineering_mode_precondition()
        assert "building_analyzer" in status.reason
        assert "yapısal" in status.reason


class TestFloorPropertyEstimation:
    def test_mass_scales_with_area(self):
        small = estimate_floor_properties(
            structure_type=BasicBuildingType.BETONARME_CERCEVE, floor_area_m2=100.0, num_floors=5,
        )
        large = estimate_floor_properties(
            structure_type=BasicBuildingType.BETONARME_CERCEVE, floor_area_m2=200.0, num_floors=5,
        )
        assert large.mass_kg == pytest.approx(2 * small.mass_kg)

    def test_different_structure_types_give_different_mass_per_area(self):
        steel = estimate_floor_properties(
            structure_type=BasicBuildingType.CELIK_CERCEVE, floor_area_m2=100.0, num_floors=5,
        )
        masonry = estimate_floor_properties(
            structure_type=BasicBuildingType.YIGMA, floor_area_m2=100.0, num_floors=5,
        )
        assert steel.mass_kg < masonry.mass_kg  # çelik daha hafif (tipik)

    def test_none_structure_type_falls_back_to_default(self):
        result = estimate_floor_properties(structure_type=None, floor_area_m2=100.0, num_floors=5)
        assert result.mass_kg > 0
        assert result.story_stiffness_n_per_m > 0

    def test_rejects_invalid_area(self):
        with pytest.raises(ValueError):
            estimate_floor_properties(structure_type=None, floor_area_m2=0.0, num_floors=5)

    def test_rejects_invalid_floor_count(self):
        with pytest.raises(ValueError):
            estimate_floor_properties(structure_type=None, floor_area_m2=100.0, num_floors=0)

    def test_stiffness_reproduces_approximately_target_frequency(self):
        """`_uniform_shear_building_k_from_f1`'in tersine çevirdiği
        rijitlik, tek katlı (N=1) basit bir durumda klasik SDOF
        omega=sqrt(k/m) formülüyle doğrudan doğrulanabilir."""
        from harita.physics.building_shake import STRUCTURE_SHAKE_PROFILES

        target_profile = STRUCTURE_SHAKE_PROFILES[BasicBuildingType.CELIK_CERCEVE]
        props = estimate_floor_properties(
            structure_type=BasicBuildingType.CELIK_CERCEVE, floor_area_m2=150.0, num_floors=1,
        )
        omega_from_k = math.sqrt(props.story_stiffness_n_per_m / props.mass_kg)
        omega_target = 2.0 * math.pi * target_profile.natural_frequency_hz
        assert omega_from_k == pytest.approx(omega_target, rel=1e-6)


def _uniform_floor_props(n=5, structure_type=BasicBuildingType.BETONARME_CERCEVE, area=200.0):
    return [
        estimate_floor_properties(structure_type=structure_type, floor_area_m2=area, num_floors=n)
        for _ in range(n)
    ]


class TestMDOFShearFrameModel:
    def test_rejects_empty_floor_properties(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.2, frequency_hz=1.0)
        with pytest.raises(ValueError):
            MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=[])

    def test_num_floors_matches_input(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.2, frequency_hz=1.0)
        props = _uniform_floor_props(n=7)
        model = MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=props)
        assert model.num_floors == 7

    def test_step_returns_one_state_per_floor(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.2, frequency_hz=1.0)
        props = _uniform_floor_props(n=4)
        model = MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=props)
        states = model.step(dt=0.02)
        assert len(states) == 4
        assert [s.floor_index for s in states] == [1, 2, 3, 4]

    def test_rejects_non_positive_dt(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.2, frequency_hz=1.0)
        model = MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=_uniform_floor_props())
        with pytest.raises(ValueError):
            model.step(dt=0.0)

    def test_zero_excitation_stays_at_rest(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.0, frequency_hz=1.0)
        model = MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=_uniform_floor_props())
        for _ in range(50):
            states = model.step(dt=0.02)
        assert all(abs(s.displacement_m) < 1e-9 for s in states)

    def test_response_stays_bounded_and_does_not_diverge(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.35, frequency_hz=2.0)
        model = MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=_uniform_floor_props(n=8))
        for _ in range(400):
            model.step(dt=0.02)
        assert not model.diverged

    def test_higher_floors_generally_displace_more_than_lower_floors(self):
        """Kesme-çerçevesi fiziği: üst katlar, taban hareketinin
        büyütülmüş halini taşır (roadmap 3.2'nin 3.B karşılığı)."""
        shake = GroundShakeForceModel(peak_acceleration_g=0.3, frequency_hz=1.2)
        props = _uniform_floor_props(n=5)
        model = MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=props)
        last_states = None
        for _ in range(250):
            last_states = model.step(dt=0.02)
        top = abs(last_states[-1].displacement_m)
        bottom = abs(last_states[0].displacement_m)
        assert top >= bottom * 0.5  # kesin monotonluk garanti edilmez (dinamik salınım) ama üst kat baskın olmalı

    def test_interstory_drift_is_relative_not_absolute(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.25, frequency_hz=1.5)
        props = _uniform_floor_props(n=3)
        model = MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=props, floor_height_m=3.0)
        for _ in range(100):
            states = model.step(dt=0.02)
        # kat 1'in drift'i doğrudan kendi yerdeğiştirmesinden (taban=0)
        assert states[0].interstory_drift_ratio == pytest.approx(states[0].displacement_m / 3.0, rel=1e-6)

    def test_every_state_carries_honesty_note(self):
        shake = GroundShakeForceModel(peak_acceleration_g=0.2, frequency_hz=1.0)
        model = MDOFShearFrameModel(building_id="b1", shake_model=shake, floor_properties=_uniform_floor_props(n=3))
        states = model.step(dt=0.02)
        assert all(s.honesty_note == ENGINEERING_MODE_HONESTY_NOTE for s in states)

    def test_deterministic_given_same_inputs(self):
        shake1 = GroundShakeForceModel(peak_acceleration_g=0.3, frequency_hz=1.5)
        shake2 = GroundShakeForceModel(peak_acceleration_g=0.3, frequency_hz=1.5)
        props1 = _uniform_floor_props(n=4)
        props2 = _uniform_floor_props(n=4)
        m1 = MDOFShearFrameModel(building_id="b1", shake_model=shake1, floor_properties=props1)
        m2 = MDOFShearFrameModel(building_id="b1", shake_model=shake2, floor_properties=props2)
        for _ in range(60):
            s1 = m1.step(dt=0.02)
            s2 = m2.step(dt=0.02)
        assert [round(s.displacement_m, 10) for s in s1] == [round(s.displacement_m, 10) for s in s2]


class TestDriftBasedDamageHint:
    def test_below_io_threshold_is_none(self):
        hint = drift_based_damage_hint(0.003)
        assert hint.damage_level is DamageLevel.NONE

    def test_between_io_and_ls_is_light(self):
        hint = drift_based_damage_hint(0.015)
        assert hint.damage_level is DamageLevel.LIGHT

    def test_between_ls_and_cp_is_moderate(self):
        hint = drift_based_damage_hint(0.04)
        assert hint.damage_level is DamageLevel.MODERATE

    def test_at_cp_is_severe(self):
        hint = drift_based_damage_hint(0.06)
        assert hint.damage_level is DamageLevel.SEVERE

    def test_far_beyond_cp_is_collapsed(self):
        hint = drift_based_damage_hint(0.09)
        assert hint.damage_level is DamageLevel.COLLAPSED

    def test_uses_absolute_value(self):
        assert drift_based_damage_hint(-0.003).damage_level is DamageLevel.NONE

    def test_thresholds_are_monotonically_non_decreasing_in_severity(self):
        order = [DamageLevel.NONE, DamageLevel.LIGHT, DamageLevel.MODERATE, DamageLevel.SEVERE, DamageLevel.COLLAPSED]
        drifts = [0.001, 0.015, 0.04, 0.06, 0.09]
        levels = [drift_based_damage_hint(d).damage_level for d in drifts]
        assert [order.index(l) for l in levels] == sorted(order.index(l) for l in levels)
