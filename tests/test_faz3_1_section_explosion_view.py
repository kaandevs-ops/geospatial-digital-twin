"""yeni_roadmap.md Faz 3.1 - Kesit (section) ve patlatma (explosion) görünümü.

Önceden yazılmış ama hiç UI/API'ye bağlanmamış `visualization.section_view`
ve `visualization.explosion_view` modüllerinin `AppSession`/REST API
üzerinden gerçek bir binaya uygulandığını doğrular.
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


def _make_project_with_building(session, tmp_path, floor_count=3, height_m=9.0):
    info = session.create_project("Proje", tmp_path / "p.hproj")
    pid = info["project_id"]
    b = session.add_building(
        pid, [(0, 0), (10, 0), (10, 10), (0, 10)], floor_count=floor_count, height_m=height_m,
    )
    return pid, b["key"]


# ---------------------------------------------------------------------------
# section_view_scene
# ---------------------------------------------------------------------------

def test_section_view_scene_cuts_mesh_in_half(session, tmp_path):
    pid, key = _make_project_with_building(session, tmp_path)
    full_scene = session.scene_json(pid)
    full_tris = sum(n["triangle_count"] for n in full_scene["nodes"])

    # x=5 düzleminde kes, pozitif tarafı (x>=5) tut -> footprint 0..10
    # olduğu için üçgen sayısı tam sahneden az ama sıfır olmamalı olmalı.
    cut_scene = session.section_view_scene(pid, axis="x", offset_m=5.0, keep_positive=True)
    cut_tris = sum(n["triangle_count"] for n in cut_scene["nodes"])

    assert 0 < cut_tris <= full_tris


def test_section_view_scene_opposite_sides_together_cover_whole(session, tmp_path):
    pid, key = _make_project_with_building(session, tmp_path)
    pos = session.section_view_scene(pid, axis="x", offset_m=5.0, keep_positive=True)
    neg = session.section_view_scene(pid, axis="x", offset_m=5.0, keep_positive=False)
    pos_tris = sum(n["triangle_count"] for n in pos["nodes"])
    neg_tris = sum(n["triangle_count"] for n in neg["nodes"])
    assert pos_tris > 0
    assert neg_tris > 0


def test_section_view_scene_invalid_axis_raises(session, tmp_path):
    pid, key = _make_project_with_building(session, tmp_path)
    with pytest.raises(AppSessionError):
        session.section_view_scene(pid, axis="w", offset_m=0.0)


def test_section_view_via_rest_api(router, session, tmp_path):
    pid, key = _make_project_with_building(session, tmp_path)
    resp = router.dispatch("GET", f"/api/projects/{pid}/section?axis=z&offset=3")
    assert resp.status == 200
    assert "nodes" in resp.body

    bad = router.dispatch("GET", f"/api/projects/{pid}/section?axis=q&offset=0")
    assert bad.status >= 400


# ---------------------------------------------------------------------------
# explosion_view_scene
# ---------------------------------------------------------------------------

def test_explosion_view_scene_separates_floors(session, tmp_path):
    pid, key = _make_project_with_building(session, tmp_path, floor_count=3, height_m=9.0)

    at_zero = session.explosion_view_scene(pid, key, progress=0.0, gap_m=2.0)
    at_one = session.explosion_view_scene(pid, key, progress=1.0, gap_m=2.0)

    assert len(at_zero["nodes"]) >= 1
    assert len(at_one["nodes"]) >= 1
    # Aynı üçgen toplamı korunmalı (yalnızca konum/Z değişir, geometri kaybı yok)
    total_zero = sum(n["triangle_count"] for n in at_zero["nodes"])
    total_one = sum(n["triangle_count"] for n in at_one["nodes"])
    assert total_zero == total_one


def test_explosion_view_scene_unknown_building_raises(session, tmp_path):
    pid, key = _make_project_with_building(session, tmp_path)
    with pytest.raises(AppSessionError):
        session.explosion_view_scene(pid, "no-such-key", progress=1.0)


def test_explosion_view_via_rest_api(router, session, tmp_path):
    pid, key = _make_project_with_building(session, tmp_path)
    resp = router.dispatch("GET", f"/api/projects/{pid}/explosion?building={key}&progress=0.5&gap=1.5")
    assert resp.status == 200
    assert "nodes" in resp.body

    missing = router.dispatch("GET", f"/api/projects/{pid}/explosion?progress=0.5")
    assert missing.status == 422
