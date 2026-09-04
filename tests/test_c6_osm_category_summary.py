"""
ROADMAP_V7.md Faz C6 (A2/B5) — katman bazlı istatistik önizlemesi.

`AppSession.osm_category_summary` / `POST /api/projects/<id>/osm/category-
summary` için ağ gerektirmeyen (sentetik Overpass JSON + `urlopen` mock'u
ile) testler. `fetch_category_features` doğrudan `urllib.request.urlopen`
kullandığı için (bkz. `test_faz2_osm_map_e2e.py`'nin `fetch_raw` mock
desenine benzer ama farklı bir giriş noktası), burada
`osm_client.urllib.request.urlopen` monkeypatch edilir.
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


def _fixture_overpass_response() -> dict:
    """1 yol (way, highway=residential) + 1 ağaç (node, natural=tree) +
    1 su alanı (way, natural=water) içeren sentetik Overpass JSON."""
    return {
        "version": 0.6,
        "generator": "Overpass API (fixture)",
        "elements": [
            # Tekil ağaç
            {"type": "node", "id": 100, "lat": 39.9205, "lon": 32.8543, "tags": {"natural": "tree"}},
            # Yol (LineString)
            {"type": "node", "id": 1, "lat": 39.9200, "lon": 32.8541},
            {"type": "node", "id": 2, "lat": 39.9201, "lon": 32.8546},
            {"type": "way", "id": 2001, "nodes": [1, 2], "tags": {"highway": "residential"}},
            # Su alanı (Polygon)
            {"type": "node", "id": 11, "lat": 39.9210, "lon": 32.8551},
            {"type": "node", "id": 12, "lat": 39.9210, "lon": 32.8555},
            {"type": "node", "id": 13, "lat": 39.9213, "lon": 32.8555},
            {"type": "node", "id": 14, "lat": 39.9213, "lon": 32.8551},
            {"type": "way", "id": 2002, "nodes": [11, 12, 13, 14, 11], "tags": {"natural": "water"}},
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


class TestOsmCategorySummarySessionLevel(unittest.TestCase):
    def _new_session_with_project(self):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("test-proje", path=str(tmp / "proj"))
        return session, info["project_id"]

    def test_summary_counts_all_default_categories(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                result = session.osm_category_summary(project_id, **ANKARA_BBOX)
            self.assertEqual(result["counts"]["trees"], 1)
            self.assertEqual(result["counts"]["roads"], 1)
            self.assertEqual(result["counts"]["water_area"], 1)
            self.assertEqual(result["total_feature_count"], sum(result["counts"].values()))
            self.assertIn("attribution", result)
            self.assertIn("roads", result["available_categories"])
        finally:
            session.close()

    def test_summary_respects_category_filter(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                result = session.osm_category_summary(project_id, **ANKARA_BBOX, categories=["trees"])
            self.assertEqual(set(result["counts"].keys()), {"trees"})
        finally:
            session.close()

    def test_unknown_category_rejected(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with self.assertRaises(AppSessionError):
                session.osm_category_summary(project_id, **ANKARA_BBOX, categories=["not_real"])
        finally:
            session.close()

    def test_invalid_bbox_rejected(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            with self.assertRaises(AppSessionError):
                session.osm_category_summary(project_id, south="x", west=0, north=1, east=1)
        finally:
            session.close()

    def test_unknown_project_rejected(self) -> None:
        session, _ = self._new_session_with_project()
        try:
            with self.assertRaises(AppSessionError):
                session.osm_category_summary("does-not-exist", **ANKARA_BBOX)
        finally:
            session.close()


class TestOsmCategorySummaryApiLevel(unittest.TestCase):
    def _router_with_project(self):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("test-proje", path=str(tmp / "proj"))
        router = build_app_router(session)
        return router, session, info["project_id"]

    def test_post_category_summary_returns_counts(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _patched_urlopen):
                response = router.dispatch(
                    "POST", f"/api/projects/{project_id}/osm/category-summary", body=ANKARA_BBOX,
                )
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body["counts"]["trees"], 1)
        finally:
            session.close()

    def test_missing_fields_returns_422(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            response = router.dispatch(
                "POST", f"/api/projects/{project_id}/osm/category-summary", body={"south": 1.0},
            )
            self.assertEqual(response.status, 422)
        finally:
            session.close()

    def test_non_list_categories_returns_422(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            body = dict(ANKARA_BBOX)
            body["categories"] = "roads"
            response = router.dispatch(
                "POST", f"/api/projects/{project_id}/osm/category-summary", body=body,
            )
            self.assertEqual(response.status, 422)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
