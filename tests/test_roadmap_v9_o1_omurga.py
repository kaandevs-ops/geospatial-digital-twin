"""
Roadmap V9 / OMURGA / O.1 testleri
====================================

Kapsam:
- `CityClock`: sabit-adım tick üretimi, hız çarpanı, pause/resume/reset,
  kayan-nokta birikim doğruluğu.
- `SimulationRecorder`: keyframe aralığı, interpolasyon, darboğaz zaman
  serisi.
- `EvacuationSimulator.run(recorder=...)`: geriye dönük uyumluluk (recorder
  verilmezse eski davranış) + yeni `bottleneck_*` alanlarının doldurulması.
"""

from __future__ import annotations

import math

import pytest

from harita.core_engine.geometry_engine import Point2D
from harita.mobility.crowd_simulation import (
    EvacuationSimulator,
    SocialForceModel,
    spawn_random_agents,
)
from harita.mobility.simulation_recorder import AgentFrameState, SimulationRecorder
from harita.simulation_core.city_clock import CityClock, ClockEventType


# --------------------------------------------------------------------------- #
# CityClock
# --------------------------------------------------------------------------- #

def test_city_clock_produces_expected_tick_count_at_1x():
    clock = CityClock(base_dt_s=0.1, speed_multiplier=1.0)
    clock.start()
    n = clock.advance(real_dt_s=1.0)
    assert n == 10
    assert math.isclose(clock.sim_time_s, 1.0, rel_tol=1e-6)


def test_city_clock_speed_multiplier_scales_ticks():
    clock = CityClock(base_dt_s=0.1, speed_multiplier=60.0)
    clock.start()
    n = clock.advance(real_dt_s=1.0)
    assert n == 600
    assert math.isclose(clock.sim_time_s, 60.0, rel_tol=1e-6)


def test_city_clock_does_not_advance_when_paused():
    clock = CityClock(base_dt_s=0.1)
    # start edilmedi -> running=False
    n = clock.advance(real_dt_s=5.0)
    assert n == 0
    assert clock.sim_time_s == 0.0


def test_city_clock_pause_resume_preserves_time():
    clock = CityClock(base_dt_s=0.1)
    clock.start()
    clock.advance(real_dt_s=1.0)
    clock.pause()
    assert clock.advance(real_dt_s=2.0) == 0  # duraklatılmışken ilerlemez
    clock.resume()
    n = clock.advance(real_dt_s=1.0)
    assert n == 10
    assert math.isclose(clock.sim_time_s, 2.0, rel_tol=1e-6)


def test_city_clock_reset():
    clock = CityClock(base_dt_s=0.1)
    clock.start()
    clock.advance(real_dt_s=1.0)
    clock.reset()
    assert clock.sim_time_s == 0.0
    assert clock.tick_index == 0


def test_city_clock_direct_subscribers_and_event_bus_agree():
    clock = CityClock(base_dt_s=0.1, speed_multiplier=1.0)
    clock.start()

    direct_ticks = []
    clock.subscribe(lambda state: direct_ticks.append(state.tick_index))

    bus_ticks = []
    clock.event_bus.subscribe(
        ClockEventType.TICK.value, lambda event: bus_ticks.append(event.payload.tick_index)
    )

    clock.advance(real_dt_s=0.5)
    assert direct_ticks == bus_ticks == [1, 2, 3, 4, 5]


def test_city_clock_rejects_invalid_params():
    with pytest.raises(ValueError):
        CityClock(base_dt_s=0.0)
    with pytest.raises(ValueError):
        CityClock(speed_multiplier=-1.0)
    clock = CityClock()
    with pytest.raises(ValueError):
        clock.set_speed(0.0)


def test_city_clock_advance_ticks_is_realtime_independent():
    clock = CityClock(base_dt_s=0.25)
    clock.advance_ticks(4)
    assert math.isclose(clock.sim_time_s, 1.0, rel_tol=1e-6)
    assert clock.tick_index == 4


# --------------------------------------------------------------------------- #
# SimulationRecorder
# --------------------------------------------------------------------------- #

def _dummy_agents():
    return spawn_random_agents(5, Point2D(0, 0), Point2D(5, 5), Point2D(20, 5), seed=7)


def test_recorder_maybe_record_respects_interval():
    recorder = SimulationRecorder(keyframe_interval_s=0.5)
    agents = _dummy_agents()
    recorder.maybe_record(0.0, agents)
    recorder.maybe_record(0.1, agents)   # aralık dolmadı -> kaydedilmez
    recorder.maybe_record(0.4, agents)   # hâlâ dolmadı
    recorder.maybe_record(0.5, agents)   # doldu -> kaydedilir
    assert len(recorder.keyframes) == 2
    assert [kf.t for kf in recorder.keyframes] == [0.0, 0.5]


def test_recorder_interpolate_at_midpoint():
    recorder = SimulationRecorder(keyframe_interval_s=1.0)
    a = spawn_random_agents(1, Point2D(0, 0), Point2D(0, 0), Point2D(10, 0), seed=1)
    a[0].position = Point2D(0.0, 0.0)
    recorder.record_frame(0.0, a)
    a[0].position = Point2D(10.0, 0.0)
    recorder.record_frame(2.0, a)

    mid = recorder.interpolate_at(1.0)
    assert 0 in mid
    assert math.isclose(mid[0].x, 5.0, rel_tol=1e-6)


def test_recorder_interpolate_clamps_outside_range():
    recorder = SimulationRecorder(keyframe_interval_s=1.0)
    a = _dummy_agents()
    recorder.record_frame(1.0, a)
    recorder.record_frame(2.0, a)
    before = recorder.interpolate_at(-5.0)
    after = recorder.interpolate_at(999.0)
    assert len(before) == len(a)
    assert len(after) == len(a)


def test_recorder_empty_interpolate_returns_empty():
    recorder = SimulationRecorder()
    assert recorder.interpolate_at(1.0) == {}


def test_recorder_bottleneck_over_time_and_peak():
    recorder = SimulationRecorder(keyframe_interval_s=1.0)
    agents = spawn_random_agents(3, Point2D(0, 0), Point2D(0, 0), Point2D(10, 0), seed=3)
    for a in agents:
        a.position = Point2D(0.0, 0.0)  # hepsi aynı hücrede -> yoğunluk 3
    recorder.record_frame(0.0, agents)

    series = recorder.bottleneck_over_time(cell_size=1.0)
    assert len(series) == 1
    t, cell, count = series[0]
    assert t == 0.0
    assert count == 3

    peak = recorder.peak_bottleneck(cell_size=1.0)
    assert peak == series[0]


def test_recorder_reset_clears_state():
    recorder = SimulationRecorder()
    recorder.record_frame(0.0, _dummy_agents())
    recorder.reset()
    assert recorder.keyframes == []
    assert recorder.peak_bottleneck() is None


# --------------------------------------------------------------------------- #
# EvacuationSimulator + recorder entegrasyonu
# --------------------------------------------------------------------------- #

def test_evacuation_run_without_recorder_is_backward_compatible():
    agents = spawn_random_agents(5, Point2D(0, 0), Point2D(5, 5), Point2D(20, 5), seed=11)
    sim = EvacuationSimulator(SocialForceModel())
    result = sim.run(agents, dt=0.1, max_time_s=15.0)
    assert result.bottleneck_location is None
    assert result.bottleneck_peak_time_s is None
    assert result.bottleneck_peak_count is None
    assert result.total_agents == 5


def test_evacuation_run_with_recorder_populates_bottleneck_and_frames():
    agents = spawn_random_agents(15, Point2D(0, 0), Point2D(6, 6), Point2D(20, 3), seed=42)
    recorder = SimulationRecorder(keyframe_interval_s=0.5)
    sim = EvacuationSimulator(SocialForceModel())
    result = sim.run(agents, dt=0.1, max_time_s=30.0, recorder=recorder)

    assert result.total_agents == 15
    assert len(recorder.keyframes) >= 2
    # Koşum tamamlandıysa (veya zaman aşımına uğradıysa) darboğaz bilgisi
    # dolu olmalı çünkü ajanlar en az bir keyframe boyunca var oldu.
    assert result.bottleneck_peak_count is not None
    assert result.bottleneck_peak_count >= 1

    # Son kare koşumun bittiği zamana (ya da yakınına) denk gelmeli.
    last_kf_t = recorder.keyframes[-1].t
    assert math.isclose(last_kf_t, result.evacuation_time_s, rel_tol=1e-6)


def test_evacuation_run_recorder_frame_states_are_valid_enum():
    agents = spawn_random_agents(4, Point2D(0, 0), Point2D(3, 3), Point2D(15, 3), seed=5)
    recorder = SimulationRecorder(keyframe_interval_s=0.5)
    sim = EvacuationSimulator(SocialForceModel())
    sim.run(agents, dt=0.1, max_time_s=20.0, recorder=recorder)

    for kf in recorder.keyframes:
        for snap in kf.agents:
            assert isinstance(snap.state, AgentFrameState)
