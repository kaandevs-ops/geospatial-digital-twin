"""Roadmap web arayüzü genişletmesi — Deprem Risk & Tahliye (hazard_data köprüsü).

`hazard_data/` paketi (afad_client, usgs_client, pga_estimate, risk_scoring,
evacuation) daha önce hiçbir yerden çağrılmıyordu. Bu testler `AppSession`
ve `RestRouter.dispatch()` üzerinden uçtan uca akışı doğrular: PGA tahmini
→ bina risk skoru (sahnedeki gerçek binadan kat sayısı/narinlik otomatik
okunarak) → çoklu bina tahliye önceliklendirmesi + gerçek rota
(`mobility.pathfinding` üzerinden, mevcut `find_path` metodu yeniden
kullanılarak). Ağ gerektiren deprem kataloğu uç noktası bu sandboxda
`is_live=False` ile açıkça başarısız olmalı (sessizce sahte veri DEĞİL).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.app_shell import AppSession, AppSessionError, build_app_router


@pytest.fixture()
def session(tmp_path):
    sess = AppSession(tmp_path / "registry.hprojreg")
    yield sess
    sess.close()


@pytest.fixture()
def router(session):
    return build_app_router(session)


def _open_project(session, tmp_path, name="p1"):
    info = session.create_project(name, tmp_path / f"{name}.hproj")
    return info["project_id"]


# ---------------------------------------------------------------------------
# AppSession — doğrudan kullanım
# ---------------------------------------------------------------------------


def test_pga_estimate_known_city(session, tmp_path):
    pid = _open_project(session, tmp_path)
    result = session.hazard_pga_estimate(pid, lat=41.01, lon=28.95)
    assert result["pga_g"] > 0
    assert result["zone_name"] == "İstanbul (Avrupa yakası)"
    assert result["is_official_source"] is False


def test_building_risk_reads_floor_count_and_slenderness_from_scene(session, tmp_path):
    pid = _open_project(session, tmp_path)
    poly = [(0, 0), (20, 0), (20, 15), (0, 15)]
    b = session.add_building(pid, poly, building_type="apartman", floor_count=8, height_m=24.0, seed=1)

    report = session.hazard_building_risk(
        pid, lat=41.01, lon=28.95, key=b["key"], construction_year=1985,
    )
    assert report["key"] == b["key"]
    assert 0.0 <= report["risk_index_0_100"] <= 100.0
    assert report["risk_level"] in ("dusuk", "orta", "yuksek", "cok_yuksek")
    factor_names = {f["name"] for f in report["factors"]}
    assert "Narinlik oranı (structural_validation)" in factor_names
    slenderness_factor = next(f for f in report["factors"] if f["name"].startswith("Narinlik"))
    assert slenderness_factor["subscore_0_100"] is not None  # sahneden otomatik okundu
    assert "ÖN DEĞERLENDİRME" in report["disclaimer"]


def test_building_risk_pre_1999_scores_higher_than_post_2018(session, tmp_path):
    pid = _open_project(session, tmp_path)
    old = session.hazard_building_risk(pid, lat=41.01, lon=28.95, construction_year=1990, floor_count=5)
    new = session.hazard_building_risk(pid, lat=41.01, lon=28.95, construction_year=2021, floor_count=5)
    assert old["risk_index_0_100"] > new["risk_index_0_100"]


def test_evacuation_plan_orders_by_risk_and_finds_real_route(session, tmp_path):
    pid = _open_project(session, tmp_path)
    poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
    risky = session.add_building(pid, poly, floor_count=12, height_m=36.0, seed=2)
    safe = session.add_building(
        pid, [(50, 50), (60, 50), (60, 60), (50, 60)], floor_count=2, height_m=6.0, seed=3,
    )

    plan = session.hazard_evacuation_plan(
        pid,
        buildings=[
            {"key": risky["key"], "lat": 37.57, "lon": 36.93, "construction_year": 1985, "occupant_estimate": 80},
            {"key": safe["key"], "lat": 37.57, "lon": 36.93, "construction_year": 2022, "occupant_estimate": 5},
        ],
        safe_points=[{"name": "Meydan", "x": 100.0, "y": 100.0}],
    )
    ranks = {p["building_id"]: p["rank"] for p in plan["priorities"]}
    assert ranks[risky["key"]] == 1  # daha riskli bina önce
    route = next(p["evacuation_route"] for p in plan["priorities"] if p["building_id"] == risky["key"])
    assert route["found"] is True
    assert route["cost_m"] > 0
    assert route["safe_point"] == "Meydan"


def test_evacuation_plan_requires_safe_points(session, tmp_path):
    pid = _open_project(session, tmp_path)
    with pytest.raises(AppSessionError):
        session.hazard_evacuation_plan(
            pid, buildings=[{"lat": 41.0, "lon": 29.0}], safe_points=[],
        )


def test_earthquake_catalog_fails_openly_without_network(session, tmp_path):
    """`hazard_data` ilkesi: ağ yoksa sessizce sahte veri üretmez."""
    pid = _open_project(session, tmp_path)
    result = session.hazard_earthquake_catalog(
        pid, min_lat=40.8, max_lat=41.3, min_lon=28.5, max_lon=29.5, source="usgs",
    )
    assert result["events"] == []
    if result["is_live"] is False:
        assert "ulaşılamadı" in result["note"] or "erişim" in result["note"]


# ---------------------------------------------------------------------------
# REST router — HTTP-benzeri dispatch üzerinden
# ---------------------------------------------------------------------------


def test_rest_hazard_pga_endpoint(router, session, tmp_path):
    pid = _open_project(session, tmp_path)
    r = router.dispatch("POST", f"/api/projects/{pid}/hazard/pga", body={"lat": 39.93, "lon": 32.86})
    assert r.status == 200
    assert r.body["zone_name"] == "Ankara"


def test_rest_hazard_pga_endpoint_missing_field(router, session, tmp_path):
    pid = _open_project(session, tmp_path)
    r = router.dispatch("POST", f"/api/projects/{pid}/hazard/pga", body={"lat": 39.93})
    assert r.status == 422


def test_rest_hazard_building_risk_and_evacuation_plan(router, session, tmp_path):
    pid = _open_project(session, tmp_path)
    poly = [(0, 0), (20, 0), (20, 15), (0, 15)]
    r = router.dispatch(
        "POST", f"/api/projects/{pid}/buildings",
        body={"polygon": poly, "building_type": "apartman", "floor_count": 10, "height_m": 30, "seed": 1},
    )
    key = r.body["key"]

    r = router.dispatch(
        "POST", f"/api/projects/{pid}/hazard/building-risk",
        body={"lat": 38.42, "lon": 27.14, "key": key, "construction_year": 1980},
    )
    assert r.status == 200
    assert r.body["risk_level"] in ("dusuk", "orta", "yuksek", "cok_yuksek")

    r = router.dispatch(
        "POST", f"/api/projects/{pid}/hazard/evacuation-plan",
        body={
            "buildings": [{"key": key, "lat": 38.42, "lon": 27.14, "construction_year": 1980, "occupant_estimate": 30}],
            "safe_points": [{"name": "Park", "x": 80, "y": 80}],
        },
    )
    assert r.status == 200
    assert r.body["priorities"][0]["evacuation_route"]["found"] is True


def test_rest_hazard_evacuation_plan_requires_buildings(router, session, tmp_path):
    pid = _open_project(session, tmp_path)
    r = router.dispatch(
        "POST", f"/api/projects/{pid}/hazard/evacuation-plan",
        body={"buildings": [], "safe_points": [{"name": "Park", "x": 0, "y": 0}]},
    )
    assert r.status == 422
