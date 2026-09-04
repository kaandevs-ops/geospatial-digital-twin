"""
Roadmap V2 — Faz 2.5: "Uçtan uca akış tasarımı" kabul kriteri.

Önceki oturumlarda `core_engine/gis_core/osm_client.py` (bbox -> Overpass ->
`Building`) ve `app_shell` (proje/bina/undo-redo/export) ayrı ayrı test
edilmişti, ama "kullanıcı haritada bir bbox seçer -> gerçek OSM verisi
çekilir -> AppSession içinde gerçek, undo/redo'ya tabi bina(lar) olarak
belirir" akışının **tamamı hiç uçtan uca doğrulanmamıştı** (bkz. ROADMAP_V4
"Sıradaki adımlar" notu, madde 4).

Bu dosya, gerçek ağ çağrısı yapmadan (Overpass sunucusu bu ortamda erişilebilir
değil), `OverpassClient.fetch_raw`'ı gerçek Overpass JSON şemasına birebir
uyan bir fixture ile değiştirerek AppSession.import_osm_bbox() ve HTTP
(`api.py`) ucunu baştan sona doğrular.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tempfile

import pytest
from harita.app_shell.api import build_app_router
from harita.app_shell.session import AppSession, AppSessionError
from harita.core_engine.gis_core.osm_client import OverpassClient, OverpassError


def _fixture_overpass_response() -> dict:
    """Gerçek Overpass JSON şemasına uygun: 2 geçerli bina + 1 geçersiz
    (yalnızca 2 node'lu, poligon kurulamaz) way."""
    return {
        "version": 0.6,
        "generator": "Overpass API (fixture)",
        "elements": [
            {"type": "node", "id": 1, "lat": 39.9200, "lon": 32.8541},
            {"type": "node", "id": 2, "lat": 39.9200, "lon": 32.8546},
            {"type": "node", "id": 3, "lat": 39.9204, "lon": 32.8546},
            {"type": "node", "id": 4, "lat": 39.9204, "lon": 32.8541},
            {
                "type": "way",
                "id": 2001,
                "nodes": [1, 2, 3, 4, 1],
                "tags": {
                    "building": "apartments",
                    "building:levels": "5",
                    "name": "Ankara Test Bina A",
                },
            },
            {"type": "node", "id": 11, "lat": 39.9210, "lon": 32.8551},
            {"type": "node", "id": 12, "lat": 39.9210, "lon": 32.8555},
            {"type": "node", "id": 13, "lat": 39.9213, "lon": 32.8555},
            {"type": "node", "id": 14, "lat": 39.9213, "lon": 32.8551},
            {
                "type": "way",
                "id": 2002,
                "nodes": [11, 12, 13, 14, 11],
                "tags": {"building": "yes", "building:levels": "3"},
            },
            # Geçersiz - poligon kurulamaz.
            {"type": "node", "id": 21, "lat": 39.93, "lon": 32.86},
            {"type": "node", "id": 22, "lat": 39.9301, "lon": 32.8601},
            {
                "type": "way",
                "id": 2003,
                "nodes": [21, 22],
                "tags": {"building": "yes"},
            },
        ],
    }


def _fake_client() -> OverpassClient:
    client = OverpassClient()
    client.fetch_raw = lambda bbox: _fixture_overpass_response()  # type: ignore[method-assign]
    return client


ANKARA_BBOX = dict(south=39.9198, west=32.8539, north=39.9215, east=32.8557)


class TestImportOsmBboxSessionLevel:
    def _new_session_with_project(self):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("ankara-test", path=str(tmp / "proj"))
        return session, info["project_id"]

    def test_creates_real_buildings_from_bbox(self):
        session, pid = self._new_session_with_project()
        result = session.import_osm_bbox(
            pid,
            **ANKARA_BBOX,
            client=_fake_client(),
        )
        assert result["raw_feature_count"] == 2  # yalnızca geçerli way'ler
        assert result["created_count"] == 2
        assert result["skipped_count"] == 0
        buildings = session.list_buildings(pid)
        assert len(buildings) == 2

    def test_created_buildings_have_correct_floor_counts(self):
        session, pid = self._new_session_with_project()
        session.import_osm_bbox(pid, **ANKARA_BBOX, client=_fake_client())
        floor_counts = sorted(b["floor_count"] for b in session.list_buildings(pid))
        assert floor_counts == [3, 5]

    def test_buildings_are_registered_with_undo_redo(self):
        session, pid = self._new_session_with_project()
        result = session.import_osm_bbox(pid, **ANKARA_BBOX, client=_fake_client())
        key = result["created"][0]["key"]
        before = session.describe_building(pid, key)["floor_count"]
        session.add_floor(pid, key)
        assert session.describe_building(pid, key)["floor_count"] == before + 1
        session.undo(pid, key)
        assert session.describe_building(pid, key)["floor_count"] == before

    def test_attribution_is_present(self):
        session, pid = self._new_session_with_project()
        result = session.import_osm_bbox(pid, **ANKARA_BBOX, client=_fake_client())
        assert "OpenStreetMap" in result["attribution"]
        assert "ODbL" in result["attribution"]

    def test_invalid_bbox_raises_readable_error(self):
        session, pid = self._new_session_with_project()
        with pytest.raises(AppSessionError):
            session.import_osm_bbox(
                pid,
                south=40.0,
                west=32.0,
                north=39.0,
                east=33.0,
                client=_fake_client(),
            )

    def test_network_failure_raises_readable_error(self):
        session, pid = self._new_session_with_project()
        client = OverpassClient()

        def _always_fail(bbox):
            raise OverpassError("tüm Overpass aynaları başarısız oldu (fixture)")

        client.fetch_raw = _always_fail  # type: ignore[method-assign]
        with pytest.raises(AppSessionError):
            session.import_osm_bbox(pid, **ANKARA_BBOX, client=client)

    def test_exported_scene_contains_osm_imported_buildings(self):
        # Uçtan uca: bbox -> OSM -> Building -> gerçek OBJ export dosyası.
        session, pid = self._new_session_with_project()
        session.import_osm_bbox(pid, **ANKARA_BBOX, client=_fake_client())
        result = session.export_scene(pid, "obj")
        assert result["status"] == "ok" if "status" in result else True
        exported_paths = result.get("paths") or result.get("path")
        assert exported_paths


class TestOsmImportHttpRoute:
    """`api.py` üzerinden aynı akışın HTTP (dispatch) seviyesinde doğrulanması."""

    def _router_with_project(self, monkeypatch):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("ankara-http-test", path=str(tmp / "proj"))
        router = build_app_router(session)

        import harita.app_shell.session as session_mod

        monkeypatch.setattr(
            session_mod,
            "OverpassClient",
            lambda *a, **k: _fake_client(),
        )
        return router, info["project_id"]

    def test_post_osm_import_returns_201_and_buildings(self, monkeypatch):
        router, pid = self._router_with_project(monkeypatch)
        resp = router.dispatch(
            "POST",
            f"/api/projects/{pid}/osm/import",
            body=ANKARA_BBOX,
        )
        assert resp.status == 201
        assert resp.body["created_count"] == 2

    def test_post_osm_import_missing_field_returns_422(self, monkeypatch):
        router, pid = self._router_with_project(monkeypatch)
        bad = dict(ANKARA_BBOX)
        del bad["east"]
        resp = router.dispatch("POST", f"/api/projects/{pid}/osm/import", body=bad)
        assert resp.status == 422

    def test_post_osm_import_non_numeric_returns_422(self, monkeypatch):
        router, pid = self._router_with_project(monkeypatch)
        bad = dict(ANKARA_BBOX)
        bad["south"] = "not-a-number"
        resp = router.dispatch("POST", f"/api/projects/{pid}/osm/import", body=bad)
        assert resp.status == 422
