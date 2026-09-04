"""Roadmap V4 - Faz E9 kabul kriteri testleri.

Kapsam (Faz E9, ROADMAP_V4.md):
    "Faz 18'in kendi kabul kriteri artık tamamen karşılanır - kullanıcı
    hiç kod yazmadan: harita verisi yükler -> 3D binayı görür -> düzenler
    (bina VE arazi VE yol) -> export eder."

D16'da yalnızca UI iskeleti (`app_shell/web/index.html` "terrain-layer-note")
bırakılmıştı; bu dosya, bu oturumda eklenen gerçek arka-uç köprüsünü
(`AppSession.terrain_*` / `AppSession.road_*` + `app_shell/api.py`
REST uçları) iki katmanda doğrular:

  1. Doğrudan `AppSession` Python API'si (fırça operasyonları gerçek
     `editor.terrain_editor.TerrainEditor` / `editor.road_editor.RoadEditor`
     kullanır, sahte/placeholder mantık yok).
  2. `build_app_router` - `POST .../terrain/*` ve `POST .../roads/*`
     uçlarının HTTP-benzeri dispatch katmanından da aynı davranışı
     sergilediği.

Ayrıca: kalıcılık (proje kapat -> yeniden aç -> arazi/yol durumu kayıpsız
geri yüklenir - R1'in persistence-derinliği ilkesiyle aynı desen) ve
`scene_json()`'ın arazi/yol mesh'lerini gerçekten sahneye kattığı
doğrulanır (D16 kabul kriterinin "arazi VE yol" eksik kısmı).

Bu dosya, projedeki diğer `test_phase*.py` dosyalarıyla aynı desende:
fixture'sız düz `test_*()` fonksiyonları + `__main__` çalıştırıcısı
(pytest kurulu olmayan ortamlarda da `python3 <bu dosya>` ile çalışır).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.app_shell.session import AppSession, AppSessionError
from harita.app_shell.api import build_app_router


def _make_session():
    tmp = tempfile.mkdtemp()
    reg = os.path.join(tmp, "registry.json")
    sess = AppSession(reg)
    info = sess.create_project("e9_test", os.path.join(tmp, "e9_test.hproj"))
    return sess, info["project_id"], tmp


# ------------------------------------------------------------------ #
# 1. Terrain: AppSession Python API
# ------------------------------------------------------------------ #

def test_terrain_init_creates_flat_grid():
    sess, pid, _tmp = _make_session()
    state = sess.terrain_init(pid, width=16, height=16, resolution_m=2.0, base_elevation=5.0)
    assert state["width"] == 16 and state["height"] == 16
    assert state["min_elevation"] == 5.0 and state["max_elevation"] == 5.0
    assert state["can_undo"] is False


def test_terrain_init_is_idempotent():
    sess, pid, _tmp = _make_session()
    sess.terrain_init(pid, width=16, height=16)
    sess.terrain_init(pid, width=32, height=32)  # ikinci çağrı yok sayılmalı
    state = sess.terrain_state(pid)
    assert state["width"] == 16  # ilk boyut korunur


def test_terrain_raise_brush_increases_elevation_under_center():
    sess, pid, _tmp = _make_session()
    sess.terrain_init(pid, width=32, height=32, resolution_m=1.0, base_elevation=0.0)
    before = sess.terrain_state(pid)["max_elevation"]
    state = sess.terrain_brush(
        pid, "raise", center_x_m=16.0, center_y_m=16.0, radius_m=5.0, amount_m=3.0,
    )
    assert state["max_elevation"] > before
    assert state["can_undo"] is True


def test_terrain_lower_brush_decreases_elevation():
    sess, pid, _tmp = _make_session()
    sess.terrain_init(pid, width=32, height=32, resolution_m=1.0, base_elevation=10.0)
    state = sess.terrain_brush(
        pid, "lower", center_x_m=16.0, center_y_m=16.0, radius_m=5.0, amount_m=4.0,
    )
    assert state["min_elevation"] < 10.0


def test_terrain_undo_redo_round_trip():
    sess, pid, _tmp = _make_session()
    sess.terrain_init(pid, width=32, height=32, resolution_m=1.0, base_elevation=0.0)
    sess.terrain_brush(pid, "raise", center_x_m=16.0, center_y_m=16.0, radius_m=5.0, amount_m=3.0)
    raised_max = sess.terrain_state(pid)["max_elevation"]
    undone = sess.terrain_undo(pid)
    assert undone["max_elevation"] < raised_max
    assert undone["can_redo"] is True
    redone = sess.terrain_redo(pid)
    assert redone["max_elevation"] == raised_max


def test_terrain_brush_without_init_raises():
    sess, pid, _tmp = _make_session()
    try:
        sess.terrain_brush(pid, "raise", center_x_m=0.0, center_y_m=0.0)
        raise AssertionError("beklenen AppSessionError fırlatılmadı")
    except AppSessionError as exc:
        assert "önce terrain_init" in str(exc)


def test_terrain_unknown_operation_rejected():
    sess, pid, _tmp = _make_session()
    sess.terrain_init(pid, width=16, height=16)
    try:
        sess.terrain_brush(pid, "explode", center_x_m=0.0, center_y_m=0.0)
        raise AssertionError("beklenen AppSessionError fırlatılmadı")
    except AppSessionError as exc:
        assert "Bilinmeyen arazi operasyonu" in str(exc)


def test_terrain_paint_layer_blends_toward_target_weight():
    sess, pid, _tmp = _make_session()
    sess.terrain_init(pid, width=16, height=16, resolution_m=1.0)
    entry = sess._terrains[pid]  # noqa: SLF001 - iç durumu doğrudan doğrula
    assert entry.paint.get(8, 8) == 0.0
    sess.terrain_brush(pid, "paint", center_x_m=8.0, center_y_m=8.0, radius_m=3.0, paint_weight=1.0)
    assert entry.paint.get(8, 8) > 0.0


# ------------------------------------------------------------------ #
# 2. Terrain: kalıcılık (proje kapat -> yeniden aç)
# ------------------------------------------------------------------ #

def test_terrain_persists_across_project_reopen():
    sess, pid, tmp = _make_session()
    sess.terrain_init(pid, width=16, height=16, resolution_m=1.0, base_elevation=0.0)
    sess.terrain_brush(pid, "raise", center_x_m=8.0, center_y_m=8.0, radius_m=4.0, amount_m=5.0)
    max_before = sess.terrain_state(pid)["max_elevation"]

    sess.close_project(pid)
    sess.open_project(pid)
    state_after = sess.terrain_state(pid)
    assert state_after is not None
    assert abs(state_after["max_elevation"] - max_before) < 1e-9


# ------------------------------------------------------------------ #
# 3. Road: AppSession Python API
# ------------------------------------------------------------------ #

def test_road_add_and_add_points_builds_valid_mesh():
    sess, pid, _tmp = _make_session()
    info = sess.road_add(pid, width_m=6.0)
    rid = info["road_id"]
    sess.road_add_point(pid, rid, 0.0, 0.0)
    sess.road_add_point(pid, rid, 10.0, 0.0)
    sess.road_add_point(pid, rid, 20.0, 5.0)
    desc = sess.road_add_point(pid, rid, 30.0, 5.0)
    assert len(desc["control_points"]) == 4
    assert desc["can_undo"] is True


def test_road_move_point_updates_position():
    sess, pid, _tmp = _make_session()
    info = sess.road_add(pid)
    rid = info["road_id"]
    sess.road_add_point(pid, rid, 0.0, 0.0)
    sess.road_add_point(pid, rid, 10.0, 0.0)
    desc = sess.road_move_point(pid, rid, 1, 12.0, 3.0)
    assert desc["control_points"][1] == [12.0, 3.0]


def test_road_remove_point_and_invalid_index():
    sess, pid, _tmp = _make_session()
    info = sess.road_add(pid)
    rid = info["road_id"]
    sess.road_add_point(pid, rid, 0.0, 0.0)
    sess.road_add_point(pid, rid, 10.0, 0.0)
    desc = sess.road_remove_point(pid, rid, 0)
    assert len(desc["control_points"]) == 1
    try:
        sess.road_remove_point(pid, rid, 99)
        raise AssertionError("beklenen AppSessionError fırlatılmadı")
    except AppSessionError:
        pass


def test_road_set_width_and_undo_redo():
    sess, pid, _tmp = _make_session()
    info = sess.road_add(pid, width_m=6.0)
    rid = info["road_id"]
    sess.road_set_width(pid, rid, 10.0)
    assert sess.describe_road(pid, rid)["width_m"] == 10.0
    sess.road_undo(pid, rid)
    assert sess.describe_road(pid, rid)["width_m"] == 6.0
    sess.road_redo(pid, rid)
    assert sess.describe_road(pid, rid)["width_m"] == 10.0


def test_road_persists_across_project_reopen():
    sess, pid, tmp = _make_session()
    info = sess.road_add(pid, width_m=8.0)
    rid = info["road_id"]
    sess.road_add_point(pid, rid, 0.0, 0.0)
    sess.road_add_point(pid, rid, 15.0, 2.0)

    sess.close_project(pid)
    sess.open_project(pid)
    roads = sess.list_roads(pid)
    assert len(roads) == 1
    assert roads[0]["road_id"] == rid
    assert len(roads[0]["control_points"]) == 2
    assert roads[0]["width_m"] == 8.0


def test_remove_road():
    sess, pid, _tmp = _make_session()
    info = sess.road_add(pid)
    rid = info["road_id"]
    assert sess.remove_road(pid, rid) is True
    assert sess.list_roads(pid) == []
    assert sess.remove_road(pid, rid) is False


# ------------------------------------------------------------------ #
# 4. Scene entegrasyonu: arazi + yol gerçekten sahneye ekleniyor
# ------------------------------------------------------------------ #

def test_scene_json_includes_terrain_and_road_meshes():
    sess, pid, _tmp = _make_session()
    sess.terrain_init(pid, width=8, height=8, resolution_m=2.0, base_elevation=0.0)
    info = sess.road_add(pid, width_m=6.0)
    rid = info["road_id"]
    sess.road_add_point(pid, rid, 0.0, 0.0)
    sess.road_add_point(pid, rid, 10.0, 0.0)
    sess.road_add_point(pid, rid, 20.0, 5.0)

    scene = sess.scene_json(pid)
    node_names = {n["name"] for n in scene["nodes"]}
    assert "terrain" in node_names
    assert rid in node_names


def test_scene_json_without_terrain_or_roads_has_no_extra_nodes():
    sess, pid, _tmp = _make_session()
    scene = sess.scene_json(pid)
    assert scene["nodes"] == []


# ------------------------------------------------------------------ #
# 5. REST köprüsü (build_app_router) - E9'un HTTP-benzeri uçları
# ------------------------------------------------------------------ #

def test_rest_terrain_full_flow():
    sess, pid, _tmp = _make_session()
    router = build_app_router(sess)

    r = router.dispatch("POST", f"/api/projects/{pid}/terrain/init",
                         body={"width": 16, "height": 16, "resolution_m": 1.0})
    assert r.status == 201

    r = router.dispatch("GET", f"/api/projects/{pid}/terrain")
    assert r.status == 200
    assert r.body["terrain"]["width"] == 16

    r = router.dispatch(
        "POST", f"/api/projects/{pid}/terrain/brush",
        body={"operation": "raise", "center_x_m": 8.0, "center_y_m": 8.0,
              "radius_m": 4.0, "amount_m": 2.0},
    )
    assert r.status == 200
    assert r.body["max_elevation"] > 0.0
    assert r.body["can_undo"] is True

    r = router.dispatch("POST", f"/api/projects/{pid}/terrain/undo")
    assert r.status == 200
    assert r.body["max_elevation"] == 0.0

    # eksik alan -> 422 (D19 tarzı fuzz-güvenli girdi doğrulaması)
    r = router.dispatch("POST", f"/api/projects/{pid}/terrain/brush", body={"operation": "raise"})
    assert r.status == 422


def test_rest_road_full_flow():
    sess, pid, _tmp = _make_session()
    router = build_app_router(sess)

    r = router.dispatch("POST", f"/api/projects/{pid}/roads", body={"width_m": 7.0})
    assert r.status == 201
    rid = r.body["road_id"]

    r = router.dispatch(
        "POST", f"/api/projects/{pid}/roads/{rid}/points", body={"x_m": 0.0, "y_m": 0.0},
    )
    assert r.status == 201
    r = router.dispatch(
        "POST", f"/api/projects/{pid}/roads/{rid}/points", body={"x_m": 10.0, "y_m": 0.0},
    )
    assert r.status == 201
    assert len(r.body["control_points"]) == 2

    r = router.dispatch(
        "PUT", f"/api/projects/{pid}/roads/{rid}/points/1", body={"x_m": 12.0, "y_m": 3.0},
    )
    assert r.status == 200
    assert r.body["control_points"][1] == [12.0, 3.0]

    r = router.dispatch("GET", f"/api/projects/{pid}/roads")
    assert r.status == 200
    assert len(r.body["roads"]) == 1

    r = router.dispatch(
        "DELETE", f"/api/projects/{pid}/roads/{rid}/points/0",
    )
    assert r.status == 200
    assert len(r.body["control_points"]) == 1

    r = router.dispatch("DELETE", f"/api/projects/{pid}/roads/{rid}")
    assert r.status == 200
    assert r.body["removed"] is True


def test_rest_road_missing_field_returns_422():
    sess, pid, _tmp = _make_session()
    router = build_app_router(sess)
    r = router.dispatch("POST", f"/api/projects/{pid}/roads", body={})
    assert r.status == 201  # road_id/width_m opsiyonel, varsayılanla oluşur
    rid = r.body["road_id"]
    r = router.dispatch("POST", f"/api/projects/{pid}/roads/{rid}/points", body={"x_m": 1.0})
    assert r.status == 422


# ------------------------------------------------------------------ #
# 6. Uçtan uca (D16/E9 birleşik kabul kriteri): bina + arazi + yol
# ------------------------------------------------------------------ #

def test_end_to_end_building_terrain_and_road_together():
    """Faz E9 kabul kriterinin tam metni: kullanıcı hiç kod yazmadan bina
    ekler, araziyi şekillendirir, yol çizer - hepsi tek bir sahnede birlikte
    var olur ve export için hazırdır (export'un kendisi D16'da zaten
    doğrulanmıştı, burada yalnızca sahne birleşimi test ediliyor)."""
    sess, pid, _tmp = _make_session()
    router = build_app_router(sess)

    router.dispatch(
        "POST", f"/api/projects/{pid}/buildings",
        body={"polygon": [[0, 0], [10, 0], [10, 8], [0, 8]], "building_type": "apartman"},
    )
    router.dispatch("POST", f"/api/projects/{pid}/terrain/init", body={"width": 20, "height": 20})
    router.dispatch(
        "POST", f"/api/projects/{pid}/terrain/brush",
        body={"operation": "raise", "center_x_m": 10.0, "center_y_m": 10.0, "radius_m": 5.0},
    )
    road_resp = router.dispatch("POST", f"/api/projects/{pid}/roads", body={})
    rid = road_resp.body["road_id"]
    router.dispatch("POST", f"/api/projects/{pid}/roads/{rid}/points", body={"x_m": -5, "y_m": -5})
    router.dispatch("POST", f"/api/projects/{pid}/roads/{rid}/points", body={"x_m": 25, "y_m": -5})

    scene = router.dispatch("GET", f"/api/projects/{pid}/scene").body
    node_names = {n["name"] for n in scene["nodes"]}
    assert "terrain" in node_names
    assert rid in node_names
    assert len(node_names) >= 3  # bina + arazi + yol


_ALL_TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    failures = []
    for fn in _ALL_TESTS:
        try:
            fn()
            print(f"OK   {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(_ALL_TESTS) - len(failures)}/{len(_ALL_TESTS)} geçti.")
    if failures:
        sys.exit(1)
