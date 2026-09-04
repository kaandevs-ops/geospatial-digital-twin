"""
ROADMAP_V7.md Faz C5 (offline mod, A4) — `AppSession`/`RestRouter`
entegrasyon testleri: tile indirme/servis + yerel yer adı indeksleme,
`AppSession`/`RestRouter.dispatch()` üzerinden (gerçek soket açmadan,
`test_phase18_app_shell.py` ile aynı yaklaşım). Gerçek indirmede
`AppSession.offline_download_region` -> `offline_cache.download_bbox`
zincirine kadar iniyoruz; ağ çağrısını izole etmek için `offline_cache.
download_bbox`'un `fetcher` parametresi olmadan burada test edilmiyor —
onun yerine sunucunun KENDİ önbelleğine doğrudan yazıp `offline_get_tile`/
`/api/offline/tiles/<z>/<x>/<y>` uçlarının doğru okuduğu doğrulanıyor
(ağ gerektirmeyen, ama uçtan uca gerçek entegrasyon testi).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.app_shell import AppSession, build_app_router


@pytest.fixture()
def session(tmp_path):
    registry = tmp_path / "registry.hprojreg"
    sess = AppSession(registry)
    yield sess
    sess.close()


@pytest.fixture()
def router(session):
    return build_app_router(session)


# --------------------------------------------------------------------------- #
# AppSession dogrudan kullanim
# --------------------------------------------------------------------------- #

def test_offline_cache_created_next_to_registry(session, tmp_path):
    assert session._offline_cache.cache_dir.parent == tmp_path
    assert session._offline_cache.cache_dir.name == "offline_cache"


def test_offline_cache_stats_starts_empty(session):
    stats = session.offline_cache_stats()
    assert stats["tile_count"] == 0
    assert stats["regions"] == []


def test_offline_get_tile_returns_none_when_missing(session):
    assert session.offline_get_tile(10, 5, 5) is None


def test_offline_get_tile_returns_bytes_after_manual_cache_write(session):
    session._offline_cache.write_tile(10, 5, 5, b"fake-tile-bytes")
    assert session.offline_get_tile(10, 5, 5) == b"fake-tile-bytes"


def test_offline_index_and_search_roundtrip(session):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [32.85, 39.92]},
                "properties": {"name": "Kızılay Meydanı", "amenity": "marketplace"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [32.86, 39.93]},
                "properties": {},  # isimsiz -> atlanir
            },
        ],
    }
    result = session.offline_index_collection("proj-1", "marketplace", geojson)
    assert result["indexed"] == 1
    assert result["total_indexed"] == 1

    hits = session.offline_search_places("kizilay")
    assert len(hits) == 1
    assert hits[0]["name"] == "Kızılay Meydanı"


def test_offline_place_index_persists_across_sessions(tmp_path):
    registry = tmp_path / "registry.hprojreg"
    with AppSession(registry) as s1:
        s1.offline_index_collection(
            "p1", "cafe",
            {
                "features": [
                    {
                        "geometry": {"type": "Point", "coordinates": [32.85, 39.92]},
                        "properties": {"name": "Kugulu Park Kafe"},
                    }
                ]
            },
        )
    with AppSession(registry) as s2:
        hits = s2.offline_search_places("kugulu")
        assert len(hits) == 1


# --------------------------------------------------------------------------- #
# RestRouter uzerinden
# --------------------------------------------------------------------------- #

def test_router_offline_stats_empty(router):
    resp = router.dispatch("GET", "/api/offline/stats")
    assert resp.status == 200
    assert resp.body["tile_count"] == 0


def test_router_offline_tile_not_found(router):
    resp = router.dispatch("GET", "/api/offline/tiles/10/5/5")
    assert resp.status == 404


def test_router_offline_tile_served_after_cache_write(router, session):
    session._offline_cache.write_tile(12, 3, 4, b"\x89PNGfake")
    resp = router.dispatch("GET", "/api/offline/tiles/12/3/4.png")
    assert resp.status == 200
    assert resp.body == b"\x89PNGfake"
    assert resp.headers["Content-Type"] == "image/png"


def test_router_offline_tile_rejects_non_integer_coords(router):
    resp = router.dispatch("GET", "/api/offline/tiles/abc/5/5")
    assert resp.status == 400


def test_router_offline_download_requires_fields(router):
    resp = router.dispatch("POST", "/api/offline/download", body={"min_lat": 41.0})
    assert resp.status == 422


def test_router_offline_download_executes_with_fake_url_template(router):
    # Gercek Overpass/tile sunucusuna erisim yok - bu test "istek dogru
    # sekilde AppSession'a ulasiyor mu" seviyesini dogrular; gercek network
    # hatasi TileDownloadResult.failed alanina sessizce dusecek sekilde
    # tasarlandi (offline_cache.download_tiles docstring'i), 500 donmez.
    resp = router.dispatch(
        "POST",
        "/api/offline/download",
        body={
            "min_lat": 41.0, "min_lon": 29.0, "max_lat": 41.001, "max_lon": 29.001,
            "zoom_min": 18, "zoom_max": 18,
            "url_template": "https://invalid.example.invalid/{z}/{x}/{y}.png",
        },
    )
    assert resp.status == 200
    assert "requested" in resp.body
    assert resp.body["failed"] == resp.body["requested"]  # ag yok -> hepsi basarisiz, ama cokme yok


def test_router_offline_index_places_requires_features(router):
    resp = router.dispatch("POST", "/api/offline/places/index", body={"project_id": "p1"})
    assert resp.status == 422


def test_router_offline_index_and_search(router):
    index_resp = router.dispatch(
        "POST",
        "/api/offline/places/index",
        body={
            "project_id": "p1",
            "category": "cafe",
            "features_geojson": {
                "features": [
                    {
                        "geometry": {"type": "Point", "coordinates": [32.85, 39.92]},
                        "properties": {"name": "Sakarya Kafe"},
                    }
                ]
            },
        },
    )
    assert index_resp.status == 200
    assert index_resp.body["indexed"] == 1

    search_resp = router.dispatch("GET", "/api/offline/places/search?q=sakarya")
    assert search_resp.status == 200
    assert len(search_resp.body["results"]) == 1
    assert search_resp.body["results"][0]["name"] == "Sakarya Kafe"


def test_router_offline_search_empty_query_returns_empty_results(router):
    resp = router.dispatch("GET", "/api/offline/places/search")
    assert resp.status == 200
    assert resp.body["results"] == []
