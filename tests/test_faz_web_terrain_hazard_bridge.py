"""Roadmap web arayüzü genişletmesi — Sel/Heyelan Riski
(`terrain_engine.FlowAccumulation` + `hazard_data.flood_landslide` köprüsü).

Roadmap iş fikri #2: "terrain_engine + hazard verisiyle eğim/drenaj analizi
(şu an terrain var ama hazard ile hiç birleşmemiş)." Bu testler eğim
hesabının doğru yönde davrandığını (dik yamaç = yüksek heyelan riski, düz +
yüksek drenaj = yüksek sel riski) ve `AppSession`/REST köprüsünün uçtan uca
çalıştığını doğrular.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.app_shell import AppSession, AppSessionError, build_app_router
from harita.core_engine.coordinate_systems import GeoPoint
from harita.hazard_data.flood_landslide import RiskLevel, TerrainHazardAnalyzer
from harita.terrain_engine import DEMImporter, HeightmapGrid


def _open_project(session, tmp_path, name="p1"):
    info = session.create_project(name, tmp_path / f"{name}.hproj")
    return info["project_id"]


# ---------------------------------------------------------------------------
# flood_landslide modülü — doğrudan
# ---------------------------------------------------------------------------


def test_flat_terrain_has_zero_slope_and_low_landslide_risk():
    grid = DEMImporter.flat_terrain(
        width=10, height=10, resolution_m=2.0, elevation=5.0, origin=GeoPoint(0, 0)
    )
    analyzer = TerrainHazardAnalyzer(grid)
    report = analyzer.assess_point(10.0, 10.0)
    assert report.slope_percent == 0.0
    assert report.landslide_level == RiskLevel.LOW


def test_steep_ramp_scores_higher_landslide_risk_than_flat():
    # Doğrusal rampa: sütun arttıkça hızla yükselir -> yüksek eğim.
    matrix = [[col * 3.0 for col in range(20)] for _row in range(20)]
    ramp = HeightmapGrid(
        width=20, height=20, resolution_m=1.0, elevations=matrix, origin=GeoPoint(0, 0)
    )
    flat = DEMImporter.flat_terrain(
        width=20, height=20, resolution_m=1.0, elevation=0.0, origin=GeoPoint(0, 0)
    )

    ramp_report = TerrainHazardAnalyzer(ramp).assess_point(10.0, 10.0)
    flat_report = TerrainHazardAnalyzer(flat).assess_point(10.0, 10.0)

    assert ramp_report.slope_percent > flat_report.slope_percent
    assert ramp_report.landslide_index_0_100 > flat_report.landslide_index_0_100
    assert "GÖSTERGE" in ramp_report.disclaimer


def test_valley_bottom_has_higher_flood_risk_than_ridge():
    # V şeklinde bir vadi: orta sütun en düşük, kenarlar yüksek -> akış
    # merkeze doğru birikir (yüksek drenaj) ve merkez düzdür.
    width = 16
    matrix = [[abs(col - width // 2) * 2.0 for col in range(width)] for _row in range(width)]
    grid = HeightmapGrid(
        width=width, height=width, resolution_m=2.0, elevations=matrix, origin=GeoPoint(0, 0)
    )
    analyzer = TerrainHazardAnalyzer(grid)
    valley_report = analyzer.assess_cell(width // 2, width // 2)
    ridge_report = analyzer.assess_cell(1, 1)
    assert valley_report.flow_accumulation >= ridge_report.flow_accumulation


def test_rainfall_missing_is_reported_as_no_data():
    grid = DEMImporter.flat_terrain(
        width=8, height=8, resolution_m=2.0, elevation=0.0, origin=GeoPoint(0, 0)
    )
    report = TerrainHazardAnalyzer(grid).assess_point(4.0, 4.0)
    rain_factor = next(f for f in report.landslide_factors if "yağış" in f.name.lower())
    assert rain_factor.subscore_0_100 is None
    assert "verilmedi" in rain_factor.note


def test_top_risk_cells_sorted_descending():
    matrix = [[col * 3.0 for col in range(15)] for _row in range(15)]
    grid = HeightmapGrid(
        width=15, height=15, resolution_m=1.0, elevations=matrix, origin=GeoPoint(0, 0)
    )
    top = TerrainHazardAnalyzer(grid).top_risk_cells(kind="landslide", limit=5)
    assert len(top) == 5
    indices = [c.landslide_index_0_100 for c in top]
    assert indices == sorted(indices, reverse=True)


# ---------------------------------------------------------------------------
# AppSession köprüsü
# ---------------------------------------------------------------------------


def test_session_requires_terrain_before_hazard_query(tmp_path):
    session = AppSession(tmp_path / "registry.hprojreg")
    pid = _open_project(session, tmp_path)
    with pytest.raises(AppSessionError):
        session.terrain_hazard_summary(pid)
    session.close()


def test_session_terrain_hazard_end_to_end(tmp_path):
    session = AppSession(tmp_path / "registry.hprojreg")
    pid = _open_project(session, tmp_path)
    session.terrain_init(pid, width=20, height=20, resolution_m=2.0)
    session.terrain_brush(
        pid, "raise", center_x_m=20.0, center_y_m=20.0, radius_m=8.0, amount_m=12.0
    )

    summary = session.terrain_hazard_summary(pid, rainfall_mm_24h=25.0)
    assert summary["grid_width"] == 20
    assert summary["max_slope_percent"] > 0

    point = session.terrain_hazard_point(pid, x_m=20.0, y_m=20.0, rainfall_mm_24h=25.0)
    assert point["landslide_level"] in ("dusuk", "orta", "yuksek", "cok_yuksek")
    assert point["disclaimer"]

    top = session.terrain_hazard_top_cells(pid, kind="landslide", limit=5)
    assert len(top["cells"]) == 5
    session.close()


# ---------------------------------------------------------------------------
# REST router
# ---------------------------------------------------------------------------


def test_rest_terrain_hazard_flow(tmp_path):
    session = AppSession(tmp_path / "registry.hprojreg")
    router = build_app_router(session)
    pid = _open_project(session, tmp_path)

    r = router.dispatch("POST", f"/api/projects/{pid}/hazard/terrain-summary", body={})
    assert r.status == 400  # arazi henüz yok -> AppSessionError -> 400

    router.dispatch(
        "POST",
        f"/api/projects/{pid}/terrain/init",
        body={"width": 16, "height": 16, "resolution_m": 2.0},
    )
    router.dispatch(
        "POST",
        f"/api/projects/{pid}/terrain/brush",
        body={
            "operation": "raise",
            "center_x_m": 16,
            "center_y_m": 16,
            "radius_m": 6,
            "amount_m": 10,
        },
    )

    r = router.dispatch("POST", f"/api/projects/{pid}/hazard/terrain-summary", body={})
    assert r.status == 200
    assert r.body["grid_width"] == 16

    r = router.dispatch(
        "POST", f"/api/projects/{pid}/hazard/terrain-point", body={"x_m": 16, "y_m": 16}
    )
    assert r.status == 200
    assert "landslide_index_0_100" in r.body

    r = router.dispatch(
        "POST", f"/api/projects/{pid}/hazard/terrain-top-cells", body={"kind": "flood", "limit": 4}
    )
    assert r.status == 200
    assert len(r.body["cells"]) == 4
    session.close()
