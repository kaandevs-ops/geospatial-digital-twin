"""
Roadmap V10 / Faz 1 — "Ortak Omurga" regresyon testleri.

Bu dosya Faz 1.7'nin ("Sürekli regresyon / kalite güvence hattı") somut
karşılığıdır: `ci.yml` / `quality_ci.yml` zaten `pytest tests/ -v` ile tüm
`tests/` ağacını koşuyor — bu testler o ağacın bir parçası olduğu için
otomatik olarak her push/PR'da tetiklenir, ayrı bir workflow eklemeye
gerek yoktur (mevcut CI altyapısı roadmap'in "projede zaten CI altyapısı
mevcut" notuyla tutarlı şekilde yeniden kullanılıyor).

Kapsanan roadmap maddeleri:
- 1.1 — `AgentSnapshot` şema genişlemesi (`floor_index`, `z_m`, `room_id`).
- 1.5 — Deterministik replay: aynı seed → bit-bit aynı yörünge.
- 1.6 — Gerçekçilik denetim raporu (tahliye süresi, collision-sanity,
  darboğaz yoğunluğu, seed etiketi).
- 1.7 — Bu testlerin kendisi: eşik aşılırsa CI kırmızı olur.
"""

from __future__ import annotations

from harita.core_engine.geometry_engine import Point2D
from harita.mobility.crowd_simulation import EvacuationSimulator, spawn_random_agents
from harita.mobility.simulation_recorder import AgentSnapshot, SimulationRecorder
from harita.mobility.simulation_recorder.realism_audit import (
    AuditSeverity,
    EvacuationAuditThresholds,
    run_realism_audit,
)


def _run_reference_scenario(seed: int = 42):
    agents = spawn_random_agents(
        count=25,
        area_min=Point2D(0.0, 0.0),
        area_max=Point2D(20.0, 20.0),
        goal=Point2D(25.0, 10.0),
        seed=seed,
    )
    obstacles = [Point2D(10.0, 5.0), Point2D(10.0, 15.0)]
    sim = EvacuationSimulator()
    recorder = SimulationRecorder(keyframe_interval_s=0.5)
    result = sim.run(
        agents, obstacles=obstacles, dt=0.1, max_time_s=120.0,
        recorder=recorder, seed=seed, scenario_id="ci_reference_room",
    )
    return result, recorder, obstacles


class TestFaz1_1AgentSnapshotSchema:
    """1.1 — şema geriye dönük uyumlu genişletildi mi?"""

    def test_agent_snapshot_has_floor_room_z_fields(self):
        snap = AgentSnapshot(agent_id=1, x=0.0, y=0.0, state="moving")
        assert snap.floor_index == 0
        assert snap.room_id is None
        assert snap.z_m == 0.0

    def test_recorder_captures_agent_floor_room_z(self):
        _, recorder, _ = _run_reference_scenario()
        assert recorder.keyframes, "en az bir keyframe kaydedilmiş olmalı"
        for snap in recorder.keyframes[0].agents:
            assert hasattr(snap, "floor_index")
            assert hasattr(snap, "room_id")
            assert hasattr(snap, "z_m")


class TestFaz1_5DeterministicReplay:
    """1.5 — aynı seed → bit-bit aynı yörünge (tolerans sıfır)."""

    def test_same_seed_produces_identical_trajectories(self):
        _, recorder_a, _ = _run_reference_scenario(seed=7)
        _, recorder_b, _ = _run_reference_scenario(seed=7)

        assert len(recorder_a.keyframes) == len(recorder_b.keyframes)
        for kf_a, kf_b in zip(recorder_a.keyframes, recorder_b.keyframes):
            assert kf_a.t == kf_b.t
            snaps_a = sorted(kf_a.agents, key=lambda s: s.agent_id)
            snaps_b = sorted(kf_b.agents, key=lambda s: s.agent_id)
            for sa, sb in zip(snaps_a, snaps_b):
                assert sa.agent_id == sb.agent_id
                assert sa.x == sb.x
                assert sa.y == sb.y
                assert sa.state == sb.state

    def test_run_metadata_captures_seed(self):
        _, recorder, _ = _run_reference_scenario(seed=99)
        assert recorder.run_metadata.seed == 99
        assert recorder.run_metadata.agent_count == 25
        assert recorder.run_metadata.scenario_id == "ci_reference_room"

    def test_different_seed_is_not_required_to_match(self):
        _, recorder_a, _ = _run_reference_scenario(seed=1)
        _, recorder_b, _ = _run_reference_scenario(seed=2)
        # Determinizm iddiası yalnızca *aynı* seed için geçerlidir; farklı
        # seed'lerin farklı sonuç üretebilmesi (rastgeleliğin gerçekten işe
        # yaradığının kanıtı) beklenen davranıştır.
        first_a = recorder_a.keyframes[0].agents[0]
        first_b = recorder_b.keyframes[0].agents[0]
        assert (first_a.x, first_a.y) != (first_b.x, first_b.y) or len(
            recorder_a.keyframes[0].agents
        ) != len(recorder_b.keyframes[0].agents)


class TestFaz1_6RealismAudit:
    """1.6 — otomatik gerçekçilik denetim raporu."""

    def test_reference_scenario_passes_audit(self):
        result, recorder, obstacles = _run_reference_scenario()
        report = run_realism_audit(result, recorder=recorder, obstacles=obstacles)
        assert report.passed, report.to_markdown()
        check_ids = {f.check_id for f in report.findings}
        assert {
            "evacuation_time_distribution",
            "collision_sanity",
            "bottleneck_density",
            "deterministic_replay_seed",
        } <= check_ids

    def test_audit_flags_missing_seed_as_warning_not_failure(self):
        agents = spawn_random_agents(
            10, Point2D(0, 0), Point2D(10, 10), Point2D(15, 5), seed=1
        )
        sim = EvacuationSimulator()
        recorder = SimulationRecorder(keyframe_interval_s=0.5)
        result = sim.run(agents, dt=0.1, max_time_s=60.0, recorder=recorder)
        report = run_realism_audit(result, recorder=recorder)
        seed_finding = next(f for f in report.findings if f.check_id == "deterministic_replay_seed")
        assert seed_finding.severity == AuditSeverity.WARN
        assert report.passed  # WARN CI'yı kırmaz, yalnızca FAIL kırar

    def test_audit_fails_on_wall_penetration(self):
        # Kasıtlı olarak agent yarıçapından çok daha yakın bir engel
        # koyarak collision-sanity ihlalini tetikliyoruz — denetimin
        # gerçekten ihlali yakaladığını (yalnızca hep PASS dönmediğini)
        # doğrular.
        result, recorder, _ = _run_reference_scenario()
        # Agent'ların gerçek yol güzergahının tam üstüne bir "duvar" koy.
        fabricated_obstacle = [recorder.keyframes[1].agents[0]]
        obstacle_points = [Point2D(s.x, s.y) for s in fabricated_obstacle]
        report = run_realism_audit(
            result, recorder=recorder, obstacles=obstacle_points,
            agent_radius_m=5.0,  # abartılı yarıçap: penetrasyonu garantiler
            thresholds=EvacuationAuditThresholds(obstacle_penetration_tolerance_m=0.01),
        )
        collision_finding = next(f for f in report.findings if f.check_id == "collision_sanity")
        assert collision_finding.severity == AuditSeverity.FAIL
        assert not report.passed
