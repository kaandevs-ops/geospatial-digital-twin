"""Roadmap V2 — Faz 18 — Uygulama Kabuğu (app_shell) testleri.

`editor` fazının "input-binding olmadan headless mantık testi" ilkesiyle
aynı yaklaşım: gerçek bir tarayıcı/soket açmadan, `AppSession` ve
`RestRouter.dispatch()` doğrudan çağrılarak uçtan uca akış (proje oluştur
→ bina ekle → düzenle → undo/redo → AI asistan komutu → sahne üret →
kaydet/kapat/yeniden aç) doğrulanır.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.app_shell import AppSession, AppSessionError, build_app_router


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


# ---------------------------------------------------------------------------
# AppSession — doğrudan (framework'süz) kullanım
# ---------------------------------------------------------------------------


def test_create_and_open_project(session, tmp_path):
    info = session.create_project("Kızılay", _project_path(tmp_path))
    assert info["name"] == "Kızılay"
    assert info["building_count"] == 0

    recents = session.list_projects()
    assert any(p["project_id"] == info["project_id"] for p in recents)


def test_open_nonexistent_project_raises(session):
    with pytest.raises(AppSessionError):
        session.open_project(project_id="does-not-exist")


def test_add_building_and_describe(session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    poly = [(0, 0), (20, 0), (20, 15), (0, 15)]

    b = session.add_building(pid, poly, building_type="apartman", floor_count=5, height_m=15.0, seed=7)
    assert b["floor_count"] == 5
    assert b["building_type"] == "apartman"
    assert b["can_undo"] is False

    listed = session.list_buildings(pid)
    assert len(listed) == 1
    assert listed[0]["key"] == b["key"]


def test_add_building_rejects_degenerate_polygon(session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    with pytest.raises(AppSessionError):
        session.add_building(info["project_id"], [(0, 0), (1, 1)])


def test_editor_bridge_add_remove_floor_and_undo_redo(session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
    b = session.add_building(pid, poly, floor_count=3, height_m=9.0)
    key = b["key"]

    grown = session.add_floor(pid, key)
    assert grown["floor_count"] == 4
    assert grown["can_undo"] is True

    undone = session.undo(pid, key)
    assert undone["floor_count"] == 3
    assert undone["can_redo"] is True

    redone = session.redo(pid, key)
    assert redone["floor_count"] == 4

    shrunk = session.remove_floor(pid, key)
    assert shrunk["floor_count"] == 3


def test_undo_without_history_raises(session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    b = session.add_building(pid, [(0, 0), (10, 0), (10, 10), (0, 10)])
    with pytest.raises(AppSessionError):
        session.undo(pid, b["key"])


def test_assistant_bridge_natural_language(session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    b = session.add_building(pid, [(0, 0), (10, 0), (10, 10), (0, 10)], floor_count=2, height_m=6.0)
    key = b["key"]

    result = session.run_assistant_command(pid, key, "2 kat ekle")
    assert result["success"] is True
    assert result["building"]["floor_count"] == 4


def test_scene_json_contains_added_buildings(session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    session.add_building(pid, [(0, 0), (10, 0), (10, 10), (0, 10)], floor_count=2, height_m=6.0)
    session.add_building(pid, [(30, 0), (40, 0), (40, 10), (30, 10)], floor_count=3, height_m=9.0)

    scene = session.scene_json(pid)
    assert scene["schema_version"]
    assert len(scene["nodes"]) == 2
    assert all(n["triangle_count"] > 0 for n in scene["nodes"])


def test_remove_building(session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    b = session.add_building(pid, [(0, 0), (10, 0), (10, 10), (0, 10)])
    assert session.remove_building(pid, b["key"]) is True
    assert session.list_buildings(pid) == []
    assert session.remove_building(pid, b["key"]) is False


def test_energy_envelope_monthly_balance(session, tmp_path):
    """EN ISO 13790/EN 832 aylık denge yönteminin (`MonthlyBalanceAuditor`)
    session katmanına gerçekten bağlandığını doğrular (önceki turda sadece
    building_reconstruction/energy_audit.py'de tanımlıydı, hiçbir yerden
    çağrılmıyordu)."""
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    poly = [(0, 0), (10, 0), (10, 8), (0, 8)]
    b = session.add_building(pid, poly, building_type="apartman", floor_count=4, height_m=12.0)
    key = b["key"]

    result = session.energy_envelope_monthly_balance(
        pid, key,
        monthly_mean_external_temp_c=[2, 3, 6, 10, 15, 19, 22, 22, 18, 13, 7, 3],
        monthly_solar_gain_kwh=[200, 250, 350, 400, 500, 550, 600, 550, 450, 350, 250, 180],
        monthly_internal_gain_kwh=[150] * 12,
    )
    assert len(result["months"]) == 12
    assert result["annual_heating_demand_kwh"] > 0
    assert result["building_volume_m3"] > 0
    assert "gösterge" not in result["disclaimer"].lower() or "iklim" in result["disclaimer"].lower()

    with pytest.raises(AppSessionError):
        session.energy_envelope_monthly_balance(
            pid, key,
            monthly_mean_external_temp_c=[2, 3],  # eksik ay -> ValueError -> AppSessionError
            monthly_solar_gain_kwh=[200] * 12,
            monthly_internal_gain_kwh=[150] * 12,
        )


def test_router_energy_envelope_monthly_balance(router, session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    b = session.add_building(pid, [(0, 0), (10, 0), (10, 8), (0, 8)], floor_count=4, height_m=12.0)

    resp = router.dispatch(
        "POST", f"/api/projects/{pid}/energy/monthly-balance",
        body={
            "key": b["key"],
            "monthly_mean_external_temp_c": [2, 3, 6, 10, 15, 19, 22, 22, 18, 13, 7, 3],
            "monthly_solar_gain_kwh": [200, 250, 350, 400, 500, 550, 600, 550, 450, 350, 250, 180],
            "monthly_internal_gain_kwh": [150] * 12,
        },
    )
    assert resp.status == 200
    assert resp.body["annual_heating_demand_kwh"] > 0

    missing = router.dispatch(
        "POST", f"/api/projects/{pid}/energy/monthly-balance",
        body={"key": b["key"]},
    )
    assert missing.status == 422


def test_persistence_roundtrip_across_sessions(tmp_path):
    registry = tmp_path / "registry.hprojreg"
    path = _project_path(tmp_path)

    with AppSession(registry) as s1:
        info = s1.create_project("Kalıcı Proje", path)
        pid = info["project_id"]
        s1.add_building(pid, [(0, 0), (12, 0), (12, 9), (0, 9)], floor_count=4, height_m=12.0, seed=3)
        s1.save_project(pid)

    with AppSession(registry) as s2:
        reopened = s2.open_project(project_id=pid)
        assert reopened["project_id"] == pid
        buildings = s2.list_buildings(pid)
        assert len(buildings) == 1
        assert buildings[0]["floor_count"] == 4


def test_history_reflects_object_writes(session, tmp_path):
    info = session.create_project("Proje", _project_path(tmp_path))
    pid = info["project_id"]
    session.add_building(pid, [(0, 0), (10, 0), (10, 10), (0, 10)])
    hist = session.history(pid)
    assert len(hist) >= 1
    assert hist[0]["kind"] == "building"


# ---------------------------------------------------------------------------
# RestRouter — HTTP-benzeri dispatch (soket açılmadan, Faz 14 sözleşmesi)
# ---------------------------------------------------------------------------


def test_router_health(router):
    resp = router.dispatch("GET", "/api/health")
    assert resp.status == 200
    assert resp.body == {"status": "ok"}


def test_router_full_workflow(router, tmp_path):
    path = str(tmp_path / "via_api.hproj")
    created = router.dispatch("POST", "/api/projects", body={"name": "API Projesi", "path": path})
    assert created.status == 201
    pid = created.body["project_id"]

    opened = router.dispatch("POST", f"/api/projects/{pid}/open")
    assert opened.status == 200

    added = router.dispatch(
        "POST", f"/api/projects/{pid}/buildings",
        body={"polygon": [[0, 0], [20, 0], [20, 15], [0, 15]], "building_type": "ofis", "floor_count": 6, "height_m": 18.0},
    )
    assert added.status == 201
    key = added.body["key"]
    assert added.body["floor_count"] == 6

    floor_up = router.dispatch("POST", f"/api/projects/{pid}/buildings/{key}/add_floor")
    assert floor_up.status == 200
    assert floor_up.body["floor_count"] == 7

    undo = router.dispatch("POST", f"/api/projects/{pid}/buildings/{key}/undo")
    assert undo.status == 200
    assert undo.body["floor_count"] == 6

    chat = router.dispatch(
        "POST", f"/api/projects/{pid}/buildings/{key}/assistant", body={"text": "1 kat ekle"},
    )
    assert chat.status == 200
    assert chat.body["success"] is True

    scene = router.dispatch("GET", f"/api/projects/{pid}/scene")
    assert scene.status == 200
    assert len(scene.body["nodes"]) == 1

    listed = router.dispatch("GET", f"/api/projects/{pid}/buildings")
    assert listed.status == 200
    assert len(listed.body["buildings"]) == 1

    saved = router.dispatch("POST", f"/api/projects/{pid}/save")
    assert saved.status == 200


def test_router_missing_field_returns_422(router):
    resp = router.dispatch("POST", "/api/projects", body={"name": "eksik"})
    assert resp.status == 422


def test_router_invalid_project_returns_400(router):
    resp = router.dispatch("POST", "/api/projects/ghost/open")
    assert resp.status == 400


def test_router_unknown_route_raises_not_found(router):
    from harita.extensibility.rest_api import RestNotFoundError

    with pytest.raises(RestNotFoundError):
        router.dispatch("GET", "/api/does-not-exist")


def test_router_delete_building(router, tmp_path):
    path = str(tmp_path / "del.hproj")
    created = router.dispatch("POST", "/api/projects", body={"name": "Silme Testi", "path": path})
    pid = created.body["project_id"]
    router.dispatch("POST", f"/api/projects/{pid}/open")
    added = router.dispatch(
        "POST", f"/api/projects/{pid}/buildings",
        body={"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    )
    key = added.body["key"]
    deleted = router.dispatch("DELETE", f"/api/projects/{pid}/buildings/{key}")
    assert deleted.status == 200
    assert deleted.body["removed"] is True


# ---------------------------------------------------------------------------
# server.py — gerçek soket üzerinden uçtan uca (opsiyonel ağır test)
# ---------------------------------------------------------------------------


def test_server_serves_static_and_api(tmp_path):
    import json
    import threading
    import urllib.request

    from harita.app_shell.server import make_server

    registry = tmp_path / "registry.hprojreg"
    with AppSession(registry) as sess:
        httpd = make_server(sess, host="127.0.0.1", port=0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                html = resp.read().decode("utf-8")
                assert "Harita Modelleme" in html

            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health") as resp:
                body = json.loads(resp.read().decode("utf-8"))
                assert body == {"status": "ok"}
        finally:
            httpd.shutdown()
            httpd.server_close()
