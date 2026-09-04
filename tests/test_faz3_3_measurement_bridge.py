"""yeni_roadmap.md Faz 3.3 - Ölçüm araçları (distance/height/angle/slope/area)
`AppSession`/REST API köprüsü.
"""
from __future__ import annotations

import sys
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


@pytest.fixture()
def project(session, tmp_path):
    info = session.create_project("Proje", tmp_path / "p.hproj")
    return info["project_id"]


def test_measure_distance(session, project):
    r = session.measure(project, "distance", [[0, 0, 0], [3, 4, 0]])
    assert r["value"] == pytest.approx(5.0)
    assert r["unit"] == "m"


def test_measure_height(session, project):
    r = session.measure(project, "height", [[0, 0, 2], [0, 0, 9]])
    assert r["value"] == pytest.approx(7.0)


def test_measure_slope(session, project):
    r = session.measure(project, "slope", [[0, 0, 0], [10, 0, 5]])
    assert r["value"] == pytest.approx(50.0)
    assert r["unit"] == "%"


def test_measure_angle(session, project):
    r = session.measure(project, "angle", [[1, 0, 0], [0, 0, 0], [0, 1, 0]])
    assert r["value"] == pytest.approx(90.0)


def test_measure_area(session, project):
    r = session.measure(project, "area", [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]])
    assert r["value"] == pytest.approx(100.0)
    assert r["unit"] == "m2"


def test_measure_unknown_tool_raises(session, project):
    with pytest.raises(AppSessionError):
        session.measure(project, "volume-of-the-universe", [[0, 0, 0], [1, 1, 1]])


def test_measure_insufficient_points_raises(session, project):
    with pytest.raises(AppSessionError):
        session.measure(project, "angle", [[0, 0, 0], [1, 1, 1]])


def test_measure_via_rest_api(router, project):
    resp = router.dispatch(
        "POST", f"/api/projects/{project}/measure",
        body={"tool": "distance", "points": [[0, 0, 0], [6, 8, 0]]},
    )
    assert resp.status == 200
    assert resp.body["value"] == pytest.approx(10.0)


def test_measure_via_rest_api_missing_fields(router, project):
    resp = router.dispatch("POST", f"/api/projects/{project}/measure", body={"points": [[0, 0, 0]]})
    assert resp.status == 422

    resp2 = router.dispatch("POST", f"/api/projects/{project}/measure", body={"tool": "distance"})
    assert resp2.status == 422
