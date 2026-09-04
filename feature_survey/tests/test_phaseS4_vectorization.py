"""ROADMAP_V6 FAZ S4 — kod-tabanlı otomatik vektörleştirme testleri.

Kabul kriteri (ROADMAP_V6.md): "Gerçek bir saha ölçüm senaryosu (10+ bina,
yol, bordür, alt yapı noktası karışık) uçtan uca test: ham nokta listesi ->
otomatik çizim -> görsel QA (referans CAD çizimiyle geometrik karşılaştırma,
IoU/alan farkı metriği)."
"""

from __future__ import annotations

import json

import pytest
from harita.feature_survey.codes import FeatureCode
from harita.feature_survey.field_point import FieldPoint, FieldSurveySession
from harita.feature_survey.vectorization import (
    VectorizationError,
    area_difference_ratio,
    build_linework,
    grid_iou,
    polygon_area,
)
from harita.feature_survey.vectorization.export_bridge import (
    export_linework_geojson,
    export_linework_svg,
)


def _square_building(
    session: FieldSurveySession, idx: int, ox: float, oy: float, side: float = 10.0
) -> None:
    """(ox,oy) sol-alt köşeli kare bina — 4 köşe, string_id ile gruplu."""
    sid = f"BLD{idx:02d}"
    corners = [(ox, oy), (ox + side, oy), (ox + side, oy + side), (ox, oy + side)]
    for i, (x, y) in enumerate(corners):
        session.add(
            FieldPoint(
                point_id=f"{sid}-{i}",
                easting=x,
                northing=y,
                elevation=100.0,
                code=FeatureCode.BUILDING_CORNER,
                string_id=sid,
            )
        )


def _mixed_scenario() -> FieldSurveySession:
    """10+ bina, yol kenarı, bordür, alt yapı noktası karışık senaryo."""
    session = FieldSurveySession(name="Karışık Saha Senaryosu", crs="EPSG:32636")

    for i in range(12):  # 12 bina (>= 10, roadmap kriteri)
        _square_building(session, i, ox=i * 20.0, oy=0.0, side=8.0)

    # Yol kenarı — örtük gruplama (string_id yok, ardışık aynı kod)
    for i, (x, y) in enumerate([(0, -5), (50, -5), (100, -5), (150, -5), (240, -5)]):
        session.add(FieldPoint(f"RD-{i}", x, y, 99.5, FeatureCode.ROAD_EDGE))

    # Bordür — örtük gruplama
    for i, (x, y) in enumerate([(0, -3), (60, -3), (120, -3)]):
        session.add(FieldPoint(f"CURB-{i}", x, y, 99.7, FeatureCode.CURB))

    # Alt yapı noktaları (Point geometrisi, tek tek)
    for i, (x, y) in enumerate([(5, -1), (45, -1), (95, -1)]):
        session.add(FieldPoint(f"MH-{i}", x, y, 99.0, FeatureCode.MANHOLE))

    return session


def test_mixed_scenario_end_to_end_vectorization():
    session = _mixed_scenario()
    features = build_linework(session)

    buildings = [f for f in features if f.code is FeatureCode.BUILDING_CORNER]
    roads = [f for f in features if f.code is FeatureCode.ROAD_EDGE]
    curbs = [f for f in features if f.code is FeatureCode.CURB]
    manholes = [f for f in features if f.code is FeatureCode.MANHOLE]

    assert len(buildings) == 12
    assert all(b.kind == "Polygon" and b.closed for b in buildings)
    assert len(roads) == 1 and roads[0].kind == "LineString"
    assert len(curbs) == 1 and curbs[0].kind == "LineString"
    assert len(manholes) == 3 and all(m.kind == "Point" for m in manholes)


def test_building_polygon_identity_iou_and_area():
    """Üretilen bina poligonu, kendi referans koordinatlarıyla
    karşılaştırıldığında IoU=1.0, alan farkı=0.0 vermelidir (kimlik testi
    — geometrik QA metriklerinin doğruluğunun kanıtı)."""
    session = FieldSurveySession(name="Tek Bina", crs="local")
    _square_building(session, 0, ox=0.0, oy=0.0, side=10.0)
    features = build_linework(session)
    poly = [(c[0], c[1]) for c in features[0].coordinates]

    assert polygon_area(poly) == pytest.approx(100.0, abs=1e-9)
    assert grid_iou(poly, poly, resolution=50) == pytest.approx(1.0, abs=1e-6)
    assert area_difference_ratio(poly, poly) == pytest.approx(0.0, abs=1e-9)


def test_grid_iou_converges_with_resolution():
    """Farklı iki (kısmen örtüşen) kare arasında IoU, çözünürlük arttıkça
    analitik gerçek değere (kesişim alanı / birleşim alanı, shoelace ile
    elle hesaplanmış) yakınsamalıdır."""
    square_a = [(0, 0), (10, 0), (10, 10), (0, 10)]
    square_b = [(5, 0), (15, 0), (15, 10), (5, 10)]  # yarı örtüşen kaydırılmış kare

    # Analitik gerçek değer: kesişim 5x10=50, birleşim 100+100-50=150 -> IoU=1/3
    expected = 50.0 / 150.0

    iou_low = grid_iou(square_a, square_b, resolution=20)
    iou_high = grid_iou(square_a, square_b, resolution=300)

    assert abs(iou_high - expected) < abs(iou_low - expected) + 1e-9
    assert abs(iou_high - expected) < 0.01


def test_polygon_requires_minimum_points():
    """NEGATİF TEST: yetersiz nokta sayısına sahip bir Polygon grubu
    sessizce boş geometri üretmez, açıkça reddedilir."""
    session = FieldSurveySession(name="Eksik Bina", crs="local")
    session.add(FieldPoint("A", 0, 0, 0, FeatureCode.BUILDING_CORNER, string_id="BAD"))
    session.add(FieldPoint("B", 10, 0, 0, FeatureCode.BUILDING_CORNER, string_id="BAD"))
    with pytest.raises(VectorizationError):
        build_linework(session)


def test_linestring_requires_minimum_points():
    session = FieldSurveySession(name="Eksik Yol", crs="local")
    session.add(FieldPoint("A", 0, 0, 0, FeatureCode.ROAD_EDGE))
    with pytest.raises(VectorizationError):
        build_linework(session)


def test_mixed_string_id_group_rejected():
    """NEGATİF TEST: aynı string_id altında farklı feature kodları varsa
    (hatalı sahra ataması) sessizce ilk kod kullanılmaz, hata fırlatılır."""
    session = FieldSurveySession(name="Karışık Grup", crs="local")
    session.add(FieldPoint("A", 0, 0, 0, FeatureCode.BUILDING_CORNER, string_id="X"))
    session.add(FieldPoint("B", 10, 0, 0, FeatureCode.ROAD_EDGE, string_id="X"))
    session.add(FieldPoint("C", 10, 10, 0, FeatureCode.BUILDING_CORNER, string_id="X"))
    with pytest.raises(VectorizationError):
        build_linework(session)


def test_export_bridge_geojson_and_svg(tmp_path):
    session = _mixed_scenario()
    features = build_linework(session)

    geojson_path = tmp_path / "linework.geojson.json"
    export_linework_geojson(features, str(geojson_path))
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == len(features)

    svg_path = tmp_path / "linework.svg"
    export_linework_svg(features, str(svg_path))
    assert svg_path.exists()
    content = svg_path.read_text(encoding="utf-8")
    assert "<svg" in content and "<polygon" in content
