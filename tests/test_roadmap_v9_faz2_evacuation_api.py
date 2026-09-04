"""
Roadmap V9 / Faz II testleri — Agent-Bazlı Deprem Tahliye Animasyonu
========================================================================

Uçtan uca akış: proje oluştur → bina ekle → senaryo kaydet (O.3) → agent
tabanlı `EvacuationSimulator`'ı Recorder(O.1)/Scene.agent_frames(O.2)/
Event Bus(O.4) omurgasına bağlayarak koştur → sonucu ayrı bir uçtan tam
(agent_frames dahil) geri al. `editor` fazının "input-binding olmadan
headless mantık testi" ilkesiyle aynı yaklaşım: gerçek soket açılmaz,
`RestRouter.dispatch()` doğrudan çağrılır.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.app_shell import AppSession, AppSessionError, build_app_router
from harita.extensibility.city_events import CityEventType
from harita.extensibility.event_system import default_bus


@pytest.fixture()
def session(tmp_path):
    registry = tmp_path / "registry.hprojreg"
    sess = AppSession(registry)
    yield sess
    sess.close()


@pytest.fixture()
def router(session):
    return build_app_router(session)


def _project_path(tmp_path, name="p1"):
    return tmp_path / f"{name}.hproj"


def _make_project_with_building(session, tmp_path):
    info = session.create_project("Deprem Test", _project_path(tmp_path))
    pid = info["project_id"]
    poly = [(0, 0), (20, 0), (20, 15), (0, 15)]
    b = session.add_building(
        pid, poly, building_type="apartman", floor_count=5, height_m=15.0, seed=7
    )
    return pid, b["key"]


def _scenario_payload(building_key: str, scenario_id: str = "s1", count: int = 20) -> dict:
    return {
        "scenario_id": scenario_id,
        "name": "Test Deprem Tahliyesi",
        "building": {"building_ref": building_key},
        "agents": {
            "count": count,
            "behavior_distribution": {"normal": 0.7, "panic": 0.3},
            "seed": 42,
        },
        "hazard": "earthquake",
        "disable_elevators": True,
    }


# --------------------------------------------------------------------------- #
# AppSession — doğrudan kullanım
# --------------------------------------------------------------------------- #


class TestSimulationSessionDirect:
    def test_save_and_load_scenario(self, session, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)
        saved = session.simulation_scenario_save(pid, _scenario_payload(key))
        assert saved["scenario_id"] == "s1"
        loaded = session.simulation_scenario_get(pid, "s1")
        assert loaded["agents"]["count"] == 20

    def test_load_missing_scenario_raises(self, session, tmp_path):
        pid, _ = _make_project_with_building(session, tmp_path)
        with pytest.raises(AppSessionError):
            session.simulation_scenario_get(pid, "does-not-exist")

    def test_run_evacuation_inline_scenario_produces_result(self, session, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)
        result = session.simulation_evacuation_run(
            pid,
            scenario_data=_scenario_payload(key, count=15),
            max_time_s=120.0,
        )
        assert result["total_agents"] == 15
        assert result["evacuated_count"] <= 15
        assert result["evacuation_time_s"] >= 0
        assert result["agent_frames_count"] > 0
        assert "agent_frames" not in result  # özet, tam veri ayrı uçta
        assert result["disable_elevators_requested"] is True
        assert "gösterge" in result["note"]

    def test_run_evacuation_by_scenario_id(self, session, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)
        session.simulation_scenario_save(pid, _scenario_payload(key, scenario_id="s2", count=10))
        result = session.simulation_evacuation_run(pid, scenario_id="s2", max_time_s=120.0)
        assert result["scenario_id"] == "s2"
        assert result["total_agents"] == 10

    def test_run_evacuation_disable_elevators_computes_real_accessibility_impact(
        self, session, tmp_path
    ):
        """ROADMAP_V9.md Katman 2.4 madde 2 — gerçek bina RoomGenerator
        çıktısı taşıdığından (`add_building` varsayılan olarak odalar/
        merdiven/asansör üretir), `disable_elevators=True` istendiğinde
        gerçek `IndoorNavigationBuilder.BuildingNavGraph` kurulup
        erişilebilirlik etkisi hesaplanmalı — artık eski "motor tarafında
        henüz tüketilmiyor" belirsizliği değil, somut bir sonuç dönmeli."""
        pid, key = _make_project_with_building(session, tmp_path)
        result = session.simulation_evacuation_run(
            pid,
            scenario_data=_scenario_payload(key, scenario_id="s3", count=5),
            max_time_s=60.0,
        )
        impact = result["elevator_accessibility_impact"]
        assert impact is not None
        assert impact["room_graph_available"] is True
        assert impact["floor_count"] == 5
        assert impact["elevator_edge_count"] >= 1
        assert "unreachable_room_count" in impact
        assert "accessibility_warning" in impact
        # Gerçek etki hesaplandığı için not artık motor kısıtından değil,
        # graf/hareket-motoru ayrımından bahsetmeli.
        assert "BuildingNavGraph erişilebilirlik etkisi hesaplandı" in result["note"]

    def test_run_evacuation_without_disable_elevators_has_no_impact_field(self, session, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)
        payload = _scenario_payload(key, scenario_id="s4", count=5)
        payload["disable_elevators"] = False
        result = session.simulation_evacuation_run(pid, scenario_data=payload, max_time_s=60.0)
        assert result["elevator_accessibility_impact"] is None
        assert result["disable_elevators_requested"] is False

    def test_run_without_scenario_raises(self, session, tmp_path):
        pid, _ = _make_project_with_building(session, tmp_path)
        with pytest.raises(AppSessionError):
            session.simulation_evacuation_run(pid)

    def test_run_with_unknown_building_raises(self, session, tmp_path):
        pid, _ = _make_project_with_building(session, tmp_path)
        bad_scenario = _scenario_payload("does-not-exist", count=5)
        with pytest.raises(AppSessionError):
            session.simulation_evacuation_run(pid, scenario_data=bad_scenario)

    def test_result_retrieval_includes_agent_frames(self, session, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)
        summary = session.simulation_evacuation_run(
            pid,
            scenario_data=_scenario_payload(key, count=8),
            max_time_s=60.0,
        )
        full = session.simulation_evacuation_result(pid, summary["result_id"])
        assert "agent_frames" in full
        assert len(full["agent_frames"]) == summary["agent_frames_count"]
        # her kare, O.2 render_engine şemasıyla uyumlu olmalı (t + agents listesi)
        first_frame = full["agent_frames"][0]
        assert "t" in first_frame and "agents" in first_frame

    def test_missing_result_raises(self, session, tmp_path):
        pid, _ = _make_project_with_building(session, tmp_path)
        with pytest.raises(AppSessionError):
            session.simulation_evacuation_result(pid, "does-not-exist")

    def test_default_safe_point_is_flagged(self, session, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)
        result = session.simulation_evacuation_run(
            pid,
            scenario_data=_scenario_payload(key, count=5),
            max_time_s=60.0,
        )
        assert result["safe_point_is_default"] is True

    def test_explicit_safe_point_used(self, session, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)
        result = session.simulation_evacuation_run(
            pid,
            scenario_data=_scenario_payload(key, count=5),
            safe_point={"x": 500.0, "y": 500.0},
            max_time_s=60.0,
        )
        assert result["safe_point_is_default"] is False
        assert result["safe_point"] == {"x": 500.0, "y": 500.0}

    def test_evacuation_events_emitted_on_default_bus(self, session, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)
        default_bus.clear()
        try:
            session.simulation_evacuation_run(
                pid,
                scenario_data=_scenario_payload(key, count=5),
                max_time_s=60.0,
            )
            started = default_bus.history(CityEventType.EVACUATION_STARTED.value)
            completed = default_bus.history(CityEventType.EVACUATION_COMPLETED.value)
            assert len(started) == 1
            assert len(completed) == 1
            assert completed[0].payload["project_id"] == pid
        finally:
            default_bus.clear()


# --------------------------------------------------------------------------- #
# REST router — dispatch() üzerinden headless HTTP-benzeri akış
# --------------------------------------------------------------------------- #


class TestSimulationRestRouter:
    def test_full_rest_flow(self, session, router, tmp_path):
        pid, key = _make_project_with_building(session, tmp_path)

        resp = router.dispatch(
            "POST",
            f"/api/projects/{pid}/simulation/scenario",
            body=_scenario_payload(key, scenario_id="rest1", count=12),
        )
        assert resp.status == 200
        assert resp.body["scenario_id"] == "rest1"

        resp = router.dispatch("GET", f"/api/projects/{pid}/simulation/scenario/rest1")
        assert resp.status == 200
        assert resp.body["agents"]["count"] == 12

        resp = router.dispatch(
            "POST",
            f"/api/projects/{pid}/simulation/evacuation/run",
            body={"scenario_id": "rest1", "max_time_s": 60.0},
        )
        assert resp.status == 200
        result_id = resp.body["result_id"]
        assert resp.body["total_agents"] == 12

        resp = router.dispatch("GET", f"/api/projects/{pid}/simulation/evacuation/{result_id}")
        assert resp.status == 200
        assert "agent_frames" in resp.body

    def test_run_without_scenario_returns_422(self, session, router, tmp_path):
        pid, _ = _make_project_with_building(session, tmp_path)
        resp = router.dispatch("POST", f"/api/projects/{pid}/simulation/evacuation/run", body={})
        assert resp.status == 422

    def test_unknown_scenario_returns_400(self, session, router, tmp_path):
        pid, _ = _make_project_with_building(session, tmp_path)
        resp = router.dispatch(
            "GET",
            f"/api/projects/{pid}/simulation/scenario/nope",
        )
        assert resp.status == 400
