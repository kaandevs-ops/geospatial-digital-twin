"""
Roadmap V9 / Faz III testleri — Kapasite + Erişilebilirlik Genişlemeleri
==========================================================================

Kapsam:
  - Katman 7.3: Bina kapasite batch-runner (`CapacityAnalyzer`) — birim
    testleri + `regulations` eşik entegrasyonu + REST uçtan uca akış.
  - Katman 2.1: `Agent.mobility_profile` / `reaction_time_s` /
    `preferred_route`, `spawn_random_agents` profil dağılımı, ve
    `effective_desired_speed()`'in profile göre değişmesi.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.core_engine.geometry_engine import Point2D
from harita.mobility.crowd_simulation import (
    Agent, AgentBehavior, DEFAULT_MOBILITY_PROFILE_DISTRIBUTION,
    EvacuationSimulator, MobilityProfile, SocialForceModel,
    spawn_random_agents,
)
from harita.mobility.crowd_simulation.capacity_analysis import (
    CapacityAnalyzer, DEFAULT_CAPACITY_AGENT_COUNTS,
)
from harita.building_reconstruction.regulations import (
    default_profile, strict_reference_profile, historic_zone_profile,
)
from harita.app_shell import AppSession, AppSessionError, build_app_router


# ----------------------------------------------------------------------- #
# Katman 2.1 — mobility profile / reaction time
# ----------------------------------------------------------------------- #

def test_agent_default_mobility_profile_is_walking():
    a = Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(10, 0))
    assert a.mobility_profile == MobilityProfile.WALKING
    assert a.reaction_time_s == 0.0
    assert a.preferred_route is None


def test_wheelchair_profile_reduces_effective_speed():
    walking = Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(10, 0))
    wheelchair = Agent(agent_id=1, position=Point2D(0, 0), goal=Point2D(10, 0),
                        mobility_profile=MobilityProfile.WHEELCHAIR)
    assert wheelchair.effective_desired_speed() < walking.effective_desired_speed()


def test_requires_elevator_or_ramp_only_for_wheelchair():
    wheelchair = Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(1, 0),
                        mobility_profile=MobilityProfile.WHEELCHAIR)
    walking = Agent(agent_id=1, position=Point2D(0, 0), goal=Point2D(1, 0))
    assert wheelchair.requires_elevator_or_ramp() is True
    assert walking.requires_elevator_or_ramp() is False


def test_spawn_random_agents_backward_compatible_without_profile_distribution():
    agents = spawn_random_agents(10, Point2D(0, 0), Point2D(10, 10), Point2D(5, 5), seed=1)
    assert len(agents) == 10
    assert all(a.mobility_profile == MobilityProfile.WALKING for a in agents)
    assert all(a.reaction_time_s == 0.0 for a in agents)


def test_spawn_random_agents_applies_profile_distribution_deterministically():
    agents_a = spawn_random_agents(
        200, Point2D(0, 0), Point2D(10, 10), Point2D(5, 5), seed=7,
        profile_distribution=DEFAULT_MOBILITY_PROFILE_DISTRIBUTION,
        reaction_time_range_s=(0.0, 4.0),
    )
    agents_b = spawn_random_agents(
        200, Point2D(0, 0), Point2D(10, 10), Point2D(5, 5), seed=7,
        profile_distribution=DEFAULT_MOBILITY_PROFILE_DISTRIBUTION,
        reaction_time_range_s=(0.0, 4.0),
    )
    # Determinism (aynı seed -> aynı sonuç)
    assert [a.mobility_profile for a in agents_a] == [a.mobility_profile for a in agents_b]
    assert [a.reaction_time_s for a in agents_a] == [a.reaction_time_s for a in agents_b]
    # Dağılım yaklaşık uygulanmış olmalı (200 örnekte kaba tolerans)
    walking_ratio = sum(1 for a in agents_a if a.mobility_profile == MobilityProfile.WALKING) / 200
    assert 0.55 < walking_ratio < 0.95
    assert all(0.0 <= a.reaction_time_s <= 4.0 for a in agents_a)


def test_reaction_time_delays_evacuation_start():
    """`reaction_time_s` uygulanan agent, aynı geometri/hedefte sıfır
    reaction time'lı bir agent'tan daha geç tahliye olmalı (pre-movement
    time literatür kavramının doğrudan sonucu)."""
    fast = [Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(20, 0))]
    delayed = [Agent(agent_id=0, position=Point2D(0, 0), goal=Point2D(20, 0),
                      reaction_time_s=15.0)]
    sim = EvacuationSimulator(SocialForceModel())
    result_fast = sim.run(fast, dt=0.1, max_time_s=60.0)
    result_delayed = sim.run(delayed, dt=0.1, max_time_s=60.0)
    assert result_delayed.evacuation_time_s > result_fast.evacuation_time_s
    assert result_delayed.evacuated_count == 1
    assert result_fast.evacuated_count == 1


# ----------------------------------------------------------------------- #
# Katman 7.3 — regulations eşik alanı
# ----------------------------------------------------------------------- #

def test_default_profile_has_evacuation_time_thresholds():
    profile = default_profile()
    assert profile.evacuation_time_threshold_s("office") == 180.0
    assert profile.evacuation_time_threshold_s("hospital") == 300.0
    # Bilinmeyen tip -> _default'a düşer
    assert profile.evacuation_time_threshold_s("unknown_type") == 180.0


def test_strict_profile_has_tighter_thresholds_than_default():
    default = default_profile()
    strict = strict_reference_profile()
    assert strict.evacuation_time_threshold_s("office") < default.evacuation_time_threshold_s("office")


def test_historic_zone_profile_preserves_evacuation_thresholds():
    default = default_profile()
    historic = historic_zone_profile()
    assert historic.evacuation_time_threshold_s("office") == default.evacuation_time_threshold_s("office")


# ----------------------------------------------------------------------- #
# Katman 7.3 — CapacityAnalyzer batch runner
# ----------------------------------------------------------------------- #

def test_capacity_analyzer_default_agent_counts():
    assert DEFAULT_CAPACITY_AGENT_COUNTS == (50, 200, 500)


def test_capacity_analyzer_runs_multiple_agent_counts():
    report = CapacityAnalyzer.run_batch(
        room_width_m=14.0, room_depth_m=10.0, exit_width_m=1.2,
        agent_counts=(15, 40), building_type="office", max_time_s=200.0,
    )
    assert len(report.runs) == 2
    assert [r.agent_count for r in report.runs] == [15, 40]
    assert report.regulation_profile_name == "TR_PAIY_ISO_BYKHY"
    for r in report.runs:
        assert r.evacuation_time_s > 0.0
        assert r.total_agents == r.agent_count
        assert r.threshold_s == 180.0
        assert r.within_threshold in (True, False)


def test_capacity_analyzer_more_agents_take_longer_or_equal():
    """Aynı çıkış genişliğinde daha fazla agent, tahliye süresini
    azaltmamalı (darboğaz fiziğinin doğal sonucu — SFPE/Predtechenskii-
    Milinskii kapı-akış modeliyle tutarlı)."""
    report = CapacityAnalyzer.run_batch(
        room_width_m=14.0, room_depth_m=10.0, exit_width_m=1.0,
        agent_counts=(10, 60), max_time_s=300.0,
    )
    small, large = report.runs
    assert large.evacuation_time_s >= small.evacuation_time_s


def test_capacity_analyzer_reports_over_threshold_when_exit_too_narrow():
    """Çok dar bir çıkışla çok kalabalık bir senaryoyu düşük bir eşiğe
    karşı test ederek `within_threshold=False` yolunu doğrular."""
    from harita.building_reconstruction.regulations import RegulationProfile

    tiny_threshold_profile = RegulationProfile(
        name="TEST_TINY_THRESHOLD",
        source_label="test",
        min_room_area_m2={},
        min_corridor_width_m=1.0,
        min_window_wall_ratio={},
        fire_escape_min_floors=1,
        max_evacuation_time_s={"_default": 1.0},
    )
    report = CapacityAnalyzer.run_batch(
        room_width_m=20.0, room_depth_m=15.0, exit_width_m=0.6,
        agent_counts=(80,), regulation_profile=tiny_threshold_profile,
        max_time_s=300.0,
    )
    assert report.any_over_threshold() is True
    assert report.runs[0].within_threshold is False


def test_capacity_analysis_report_to_dict_roundtrip_shape():
    report = CapacityAnalyzer.run_batch(
        room_width_m=12.0, room_depth_m=8.0, exit_width_m=1.2,
        agent_counts=(10,), max_time_s=100.0,
    )
    d = report.to_dict()
    assert "runs" in d and "disclaimer" in d and "any_over_threshold" in d
    assert d["runs"][0]["agent_count"] == 10


# ----------------------------------------------------------------------- #
# REST/session uçtan uca — capacity-analysis endpoints
# ----------------------------------------------------------------------- #

@pytest.fixture()
def session(tmp_path):
    registry = tmp_path / "registry.hprojreg"
    sess = AppSession(registry)
    yield sess
    sess.close()


@pytest.fixture()
def router(session):
    return build_app_router(session)


def _project_path(tmp_path, name="capacity_p1"):
    return tmp_path / f"{name}.hproj"


def test_session_capacity_analysis_run_and_result(session, tmp_path):
    info = session.create_project("Kapasite Test", _project_path(tmp_path))
    pid = info["project_id"]
    result = session.capacity_analysis_run(
        pid, room_width_m=15.0, room_depth_m=10.0, exit_width_m=1.2,
        agent_counts=[10, 30], building_type="school", max_time_s=200.0,
    )
    assert result["building_type"] == "school"
    assert len(result["runs"]) == 2
    result_id = result["result_id"]

    fetched = session.capacity_analysis_result(pid, result_id)
    assert fetched["result_id"] == result_id
    # JSON tabanlı persistence katmanı tuple'ları listeye çevirir (DB
    # round-trip) - içerik eşitliği agent_count bazında doğrulanır.
    assert [r["agent_count"] for r in fetched["runs"]] == [r["agent_count"] for r in result["runs"]]
    assert [r["evacuation_time_s"] for r in fetched["runs"]] == [r["evacuation_time_s"] for r in result["runs"]]


def test_session_capacity_analysis_unknown_result_raises(session, tmp_path):
    info = session.create_project("Kapasite Test 2", _project_path(tmp_path, "capacity_p2"))
    pid = info["project_id"]
    with pytest.raises(AppSessionError):
        session.capacity_analysis_result(pid, "does_not_exist")


def test_rest_capacity_analysis_run_and_fetch(router, session, tmp_path):
    info = session.create_project("Kapasite REST", _project_path(tmp_path, "capacity_rest"))
    pid = info["project_id"]

    run_resp = router.dispatch(
        "POST", f"/api/projects/{pid}/simulation/capacity-analysis/run",
        body={
            "room_width_m": 15.0, "room_depth_m": 10.0, "exit_width_m": 1.2,
            "agent_counts": [10, 25], "building_type": "office",
        },
    )
    assert run_resp.status == 200
    result_id = run_resp.body["result_id"]
    assert len(run_resp.body["runs"]) == 2

    get_resp = router.dispatch(
        "GET", f"/api/projects/{pid}/simulation/capacity-analysis/{result_id}",
    )
    assert get_resp.status == 200
    assert get_resp.body["result_id"] == result_id


def test_rest_capacity_analysis_missing_field_returns_422(router, session, tmp_path):
    info = session.create_project("Kapasite REST2", _project_path(tmp_path, "capacity_rest2"))
    pid = info["project_id"]
    resp = router.dispatch(
        "POST", f"/api/projects/{pid}/simulation/capacity-analysis/run",
        body={"room_width_m": 15.0, "room_depth_m": 10.0},
    )
    assert resp.status == 422


def test_rest_capacity_analysis_unknown_result_returns_error(router, session, tmp_path):
    info = session.create_project("Kapasite REST3", _project_path(tmp_path, "capacity_rest3"))
    pid = info["project_id"]
    resp = router.dispatch(
        "GET", f"/api/projects/{pid}/simulation/capacity-analysis/nope",
    )
    assert resp.status >= 400
