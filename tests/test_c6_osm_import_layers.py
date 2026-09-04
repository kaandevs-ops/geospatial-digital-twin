"""
ROADMAP_V7.md Faz C6 (2. dilim) — B5'in "kullanıcı sadece 'Yollar +
Ağaçlar' seçip bbox import edebilmeli" maddesinin GERÇEK karşılığı.

`AppSession.import_osm_categories` / `POST /api/projects/<id>/osm/
import-layers` için ağ gerektirmeyen (sentetik Overpass JSON + `urlopen`
mock'u ile) testler — `tests/test_c6_osm_category_summary.py`'nin
deseniyle birebir tutarlı. Ayrıca bu dilimin önkoşulu olan
`project_to_local_meters`'ın Point/LineString desteğini de doğrudan
kapsar (önceden yalnızca Polygon işliyordu).
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.app_shell.api import build_app_router
from harita.app_shell.session import AppSession, AppSessionError
from harita.core_engine.coordinate_systems import GeoPoint
from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.core_engine.gis_core.osm_client import project_to_local_meters


def _fixture_overpass_response() -> dict:
    """Karma bir Overpass JSON: 1 ağaç, 1 yol, 1 aydınlatma direği,
    1 dini yapı (cami), 1 oyun alanı, 1 elektrik hattı — B1'in farklı
    kategori gruplarının (vegetation/infrastructure/street_furniture/
    religious_structures/sport_recreation/power_infrastructure) hepsini
    tek bir sentetik fixture'da uçtan uca egzersiz eder."""
    return {
        "version": 0.6,
        "generator": "Overpass API (fixture)",
        "elements": [
            # Tekil ağaç (vegetation)
            {"type": "node", "id": 100, "lat": 39.9205, "lon": 32.8543, "tags": {"natural": "tree"}},
            # Aydınlatma direği (street_furniture)
            {"type": "node", "id": 101, "lat": 39.9206, "lon": 32.8544, "tags": {"highway": "street_lamp"}},
            # Cami (religious_structures)
            {"type": "node", "id": 102, "lat": 39.9207, "lon": 32.8545,
             "tags": {"amenity": "place_of_worship", "religion": "muslim"}},
            # Oyun alanı (sport_recreation)
            {"type": "node", "id": 103, "lat": 39.9208, "lon": 32.8546, "tags": {"leisure": "playground"}},
            # Yol (infrastructure, LineString)
            {"type": "node", "id": 1, "lat": 39.9200, "lon": 32.8541},
            {"type": "node", "id": 2, "lat": 39.9201, "lon": 32.8546},
            {"type": "way", "id": 2001, "nodes": [1, 2], "tags": {"highway": "residential"}},
            # Elektrik hattı (power_infrastructure, LineString)
            {"type": "node", "id": 3, "lat": 39.9202, "lon": 32.8547},
            {"type": "node", "id": 4, "lat": 39.9203, "lon": 32.8549},
            {"type": "way", "id": 2003, "nodes": [3, 4], "tags": {"power": "line"}},
        ],
    }


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _patched_urlopen(*_args, **_kwargs) -> _FakeHTTPResponse:
    return _FakeHTTPResponse(_fixture_overpass_response())


ANKARA_BBOX = dict(south=39.9198, west=32.8539, north=39.9215, east=32.8557)

ALL_TEST_CATEGORIES = [
    "trees", "roads", "street_lamp", "place_of_worship", "playground", "power_line",
]


class TestProjectToLocalMetersPointLineString(unittest.TestCase):
    """C6/2. dilimin önkoşulu: `project_to_local_meters` artık Point/
    LineString'i de projekte ediyor (önceden yalnızca Polygon)."""

    def test_point_feature_is_projected(self) -> None:
        origin = GeoPoint(lat=39.92, lon=32.85)
        collection = GeoFeatureCollection([
            GeoFeature(geometry_type="Point", coordinates=(32.8543, 39.9205), properties={"__category__": "trees"}),
        ])
        local = project_to_local_meters(collection, origin=origin)
        self.assertEqual(len(local.features), 1)
        feat = local.features[0]
        self.assertEqual(feat.geometry_type, "Point")
        x, y = feat.coordinates
        # Orijine yakın küçük bir bölge -> birkaç yüz metre mertebesinde
        self.assertLess(abs(x), 2000)
        self.assertLess(abs(y), 2000)
        self.assertEqual(feat.properties["__category__"], "trees")

    def test_linestring_feature_is_projected(self) -> None:
        origin = GeoPoint(lat=39.92, lon=32.85)
        collection = GeoFeatureCollection([
            GeoFeature(
                geometry_type="LineString",
                coordinates=[(32.8541, 39.9200), (32.8546, 39.9201)],
                properties={"__category__": "roads"},
            ),
        ])
        local = project_to_local_meters(collection, origin=origin)
        self.assertEqual(len(local.features), 1)
        feat = local.features[0]
        self.assertEqual(feat.geometry_type, "LineString")
        self.assertEqual(len(feat.coordinates), 2)
        for x, y in feat.coordinates:
            self.assertIsInstance(x, float)
            self.assertIsInstance(y, float)

    def test_polygon_feature_still_projected_unchanged(self) -> None:
        """Regresyon: mevcut Polygon davranışı birebir korunmalı."""
        origin = GeoPoint(lat=39.92, lon=32.85)
        ring = [(32.8551, 39.9210), (32.8555, 39.9210), (32.8555, 39.9213), (32.8551, 39.9213), (32.8551, 39.9210)]
        collection = GeoFeatureCollection([
            GeoFeature(geometry_type="Polygon", coordinates=[ring], properties={"__category__": "water_area"}),
        ])
        local = project_to_local_meters(collection, origin=origin)
        self.assertEqual(len(local.features), 1)
        self.assertEqual(local.features[0].geometry_type, "Polygon")
        self.assertEqual(len(local.features[0].coordinates[0]), 5)

    def test_mixed_collection_preserves_all_geometry_types(self) -> None:
        origin = GeoPoint(lat=39.92, lon=32.85)
        collection = GeoFeatureCollection([
            GeoFeature(geometry_type="Point", coordinates=(32.8543, 39.9205), properties={"__category__": "trees"}),
            GeoFeature(
                geometry_type="LineString",
                coordinates=[(32.8541, 39.9200), (32.8546, 39.9201)],
                properties={"__category__": "roads"},
            ),
            GeoFeature(
                geometry_type="Polygon",
                coordinates=[[(32.8551, 39.9210), (32.8555, 39.9210), (32.8555, 39.9213), (32.8551, 39.9210)]],
                properties={"__category__": "water_area"},
            ),
        ])
        local = project_to_local_meters(collection, origin=origin)
        self.assertEqual(len(local.features), 3)
        types = {f.geometry_type for f in local.features}
        self.assertEqual(types, {"Point", "LineString", "Polygon"})


class TestImportOsmCategoriesSessionLevel(unittest.TestCase):
    def _new_session_with_project(self):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("test-proje", path=str(tmp / "proj"))
        return session, info["project_id"]

    def test_import_creates_scene_props_across_all_groups(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                result = session.import_osm_categories(
                    project_id, categories=ALL_TEST_CATEGORIES, **ANKARA_BBOX,
                )
            self.assertEqual(result["created_count"], 6)
            self.assertEqual(result["counts"].get("vegetation"), 1)
            self.assertEqual(result["counts"].get("roads"), 1)
            self.assertEqual(result["counts"].get("street_furniture"), 1)
            self.assertEqual(result["counts"].get("religious_structures"), 1)
            self.assertEqual(result["counts"].get("playground"), 1)
            self.assertEqual(result["counts"].get("power_line"), 1)
            self.assertIn("attribution", result)
            for item in result["created"]:
                self.assertIn("lat", item)
                self.assertIn("lon", item)
                self.assertGreater(item["vertex_count"], 0)
                self.assertGreater(item["triangle_count"], 0)
                # lat/lon gerçek dünya koordinatlarına (Ankara civarı) yakın olmalı,
                # yerel-metre değerleri sızmamalı.
                self.assertAlmostEqual(item["lat"], 39.92, delta=0.05)
                self.assertAlmostEqual(item["lon"], 32.855, delta=0.05)
        finally:
            session.close()

    def test_import_persists_and_roundtrips_via_list_scene_props(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                session.import_osm_categories(project_id, categories=["trees"], **ANKARA_BBOX)
            listed = session.list_scene_props(project_id)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["category"], "vegetation")
        finally:
            session.close()

    def test_scene_props_survive_project_reopen(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                session.import_osm_categories(project_id, categories=["trees"], **ANKARA_BBOX)
            session.save_project(project_id)
            session.close_project(project_id)
            session.open_project(project_id)
            listed = session.list_scene_props(project_id)
            self.assertEqual(len(listed), 1)
        finally:
            session.close()

    def test_remove_scene_prop(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                result = session.import_osm_categories(project_id, categories=["trees"], **ANKARA_BBOX)
            key = result["created"][0]["key"]
            self.assertTrue(session.remove_scene_prop(project_id, key))
            self.assertEqual(session.list_scene_props(project_id), [])
            self.assertFalse(session.remove_scene_prop(project_id, key))
        finally:
            session.close()

    def test_empty_categories_rejected(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with self.assertRaises(AppSessionError):
                session.import_osm_categories(project_id, categories=[], **ANKARA_BBOX)
        finally:
            session.close()

    def test_unknown_category_rejected(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with self.assertRaises(AppSessionError):
                session.import_osm_categories(project_id, categories=["not_real"], **ANKARA_BBOX)
        finally:
            session.close()

    def test_invalid_bbox_rejected(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with self.assertRaises(AppSessionError):
                session.import_osm_categories(
                    project_id, categories=["trees"], south="x", west=0, north=1, east=1,
                )
        finally:
            session.close()

    def test_unknown_project_rejected(self) -> None:
        session, _ = self._new_session_with_project()
        try:
            with self.assertRaises(AppSessionError):
                session.import_osm_categories("does-not-exist", categories=["trees"], **ANKARA_BBOX)
        finally:
            session.close()

    def test_import_does_not_touch_building_pipeline(self) -> None:
        """Roadmap'in 'mevcut mimari korunacak' ilkesi: kategori import'u
        bina koleksiyonuna hiç dokunmamalı (regresyon kontrolü)."""
        session, project_id = self._new_session_with_project()
        try:
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                session.import_osm_categories(project_id, categories=["trees"], **ANKARA_BBOX)
            self.assertEqual(session.list_buildings(project_id), [])
        finally:
            session.close()


class TestImportOsmCategoriesApiLevel(unittest.TestCase):
    def _router_with_project(self):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("test-proje", path=str(tmp / "proj"))
        router = build_app_router(session)
        return router, session, info["project_id"]

    def test_post_import_layers_returns_201_and_counts(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            body = dict(ANKARA_BBOX)
            body["categories"] = ALL_TEST_CATEGORIES
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                response = router.dispatch(
                    "POST", f"/api/projects/{project_id}/osm/import-layers", body=body,
                )
            self.assertEqual(response.status, 201)
            self.assertEqual(response.body["created_count"], 6)
        finally:
            session.close()

    def test_missing_categories_returns_422(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            response = router.dispatch(
                "POST", f"/api/projects/{project_id}/osm/import-layers", body=dict(ANKARA_BBOX),
            )
            self.assertEqual(response.status, 422)
        finally:
            session.close()

    def test_non_list_categories_returns_422(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            body = dict(ANKARA_BBOX)
            body["categories"] = "trees"
            response = router.dispatch(
                "POST", f"/api/projects/{project_id}/osm/import-layers", body=body,
            )
            self.assertEqual(response.status, 422)
        finally:
            session.close()

    def test_get_scene_props_list_after_import(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            body = dict(ANKARA_BBOX)
            body["categories"] = ["trees"]
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                router.dispatch("POST", f"/api/projects/{project_id}/osm/import-layers", body=body)
            response = router.dispatch("GET", f"/api/projects/{project_id}/scene-props")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["count"], 1)
        finally:
            session.close()

    def test_delete_scene_prop_via_api(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            body = dict(ANKARA_BBOX)
            body["categories"] = ["trees"]
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                import_resp = router.dispatch(
                    "POST", f"/api/projects/{project_id}/osm/import-layers", body=body,
                )
            key = import_resp.body["created"][0]["key"]
            response = router.dispatch(
                "POST", f"/api/projects/{project_id}/scene-props/{key}/delete", body={},
            )
            self.assertEqual(response.status, 200)
            missing = router.dispatch(
                "POST", f"/api/projects/{project_id}/scene-props/{key}/delete", body={},
            )
            self.assertEqual(missing.status, 404)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
