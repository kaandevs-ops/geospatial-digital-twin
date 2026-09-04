import csv

import pytest

from harita.feature_survey.codes import FeatureCategory, FeatureCode
from harita.feature_survey.field_point import FieldPoint, FieldSurveySession
from harita.feature_survey.io_import import PENZDImportError, import_penzd_csv
from harita.core_engine.coordinate_systems import GeoPoint
from harita.feature_survey.bridge import session_to_geofeatures, session_to_wgs84_geofeatures
from harita.feature_survey.pipeline import MeshroomPipeline, WebODMPipeline, ExternalToolNotAvailableError


def test_feature_code_category_and_geometry_hint():
    assert FeatureCode.BUILDING_CORNER.category == FeatureCategory.BUILDING
    assert FeatureCode.BUILDING_CORNER.geometry_hint == "Polygon"
    assert FeatureCode.TREE.geometry_hint == "Point"
    assert FeatureCode.ROAD_EDGE.geometry_hint == "LineString"


def test_session_summary_and_by_code():
    session = FieldSurveySession(name="demo")
    session.add(FieldPoint("P1", 100, 200, 5, FeatureCode.TREE))
    session.add(FieldPoint("P2", 101, 201, 5, FeatureCode.TREE))
    session.add(FieldPoint("P3", 102, 202, 5, FeatureCode.MANHOLE))
    assert session.summary() == {"MANHOLE": 1, "TREE": 2}
    assert len(session.by_code(FeatureCode.TREE)) == 2


def test_penzd_csv_import(tmp_path):
    csv_path = tmp_path / "field.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["point", "easting", "northing", "elevation", "description"])
        writer.writerow(["1", "500000.0", "4500000.0", "850.5", "BLD_COR_BLD01"])
        writer.writerow(["2", "500001.0", "4500000.0", "850.6", "BLD_COR_BLD01"])
        writer.writerow(["3", "500001.0", "4500001.0", "850.4", "BLD_COR_BLD01"])
        writer.writerow(["4", "500010.0", "4500010.0", "849.0", "TREE"])
        writer.writerow(["5", "500020.0", "4500020.0", "848.0", "WHATEVER_XYZ"])

    session = import_penzd_csv(csv_path)
    assert len(session.points) == 5
    assert session.points[0].code == FeatureCode.BUILDING_CORNER
    assert session.points[0].string_id == "BLD01"
    assert session.points[3].code == FeatureCode.TREE
    assert session.points[4].code == FeatureCode.OTHER
    assert session.points[4].raw_code == "WHATEVER_XYZ"


def test_penzd_csv_import_malformed_row_raises(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("point,easting,northing,elevation,description\n1,100,200\n")
    with pytest.raises(PENZDImportError):
        import_penzd_csv(csv_path)


def test_session_to_geofeatures_polygon_and_point():
    session = FieldSurveySession(name="demo", crs="EPSG:32636")
    session.add(FieldPoint("1", 0, 0, 10, FeatureCode.BUILDING_CORNER, string_id="B1"))
    session.add(FieldPoint("2", 10, 0, 10, FeatureCode.BUILDING_CORNER, string_id="B1"))
    session.add(FieldPoint("3", 10, 10, 10, FeatureCode.BUILDING_CORNER, string_id="B1"))
    session.add(FieldPoint("4", 5, 5, 9, FeatureCode.TREE))

    fc = session_to_geofeatures(session)
    assert fc.crs == "EPSG:32636"
    polys = [f for f in fc.features if f.geometry_type == "Polygon"]
    points = [f for f in fc.features if f.geometry_type == "Point"]
    assert len(polys) == 1
    assert len(points) == 1
    ring = polys[0].coordinates[0]
    assert ring[0] == ring[-1]  # halka kapatılmış olmalı


def test_webodm_ping_unreachable_returns_false():
    pipe = WebODMPipeline(base_url="http://127.0.0.1:1", timeout=0.2)
    assert pipe.ping() is False


def test_meshroom_not_available_raises():
    pipe = MeshroomPipeline(binary="definitely_not_a_real_binary_xyz")
    assert pipe.is_available() is False
    with pytest.raises(ExternalToolNotAvailableError):
        pipe.run("in", "out")


def test_webodm_build_multipart_contains_fields_and_files(tmp_path):
    img = tmp_path / "photo1.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fakejpegdata")
    body, content_type = WebODMPipeline._build_multipart(
        {"name": "demo"}, [("images", img)]
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=")[1]
    assert boundary.encode() in body
    assert b'name="name"' in body
    assert b"demo" in body
    assert b'name="images"; filename="photo1.jpg"' in body
    assert b"fakejpegdata" in body


def test_webodm_submit_task_requires_images():
    pipe = WebODMPipeline(base_url="http://127.0.0.1:1", timeout=0.2)
    with pytest.raises(ValueError):
        pipe.submit_task("demo", [])


def test_session_to_wgs84_geofeatures_round_trip():
    session = FieldSurveySession(name="demo", crs="local")
    session.add(FieldPoint("P1", 10.0, 20.0, 5.0, FeatureCode.TREE))
    origin = GeoPoint(lat=39.925, lon=32.837, elevation=850.0)
    fc = session_to_wgs84_geofeatures(session, origin)
    assert fc.crs == "EPSG:4326"
    point = fc.features[0]
    lon, lat, elev = point.coordinates
    assert -180.0 <= lon <= 180.0
    assert -90.0 <= lat <= 90.0
    # orijine yakın küçük bir ofset olmalı (10m doğu, 20m kuzey)
    assert abs(lat - origin.lat) < 0.01
    assert abs(lon - origin.lon) < 0.01
    assert lat > origin.lat  # kuzeye kaymış olmalı
