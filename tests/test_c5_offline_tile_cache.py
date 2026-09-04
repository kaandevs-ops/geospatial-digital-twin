"""
ROADMAP_V7.md Faz C5 (offline mod, A4) testleri: `offline_cache` (tile
matematiği/önbellek/indirme) + `offline_cache.local_place_index` (Nominatim
offline alternatifi).

Ağ gerektirmez - indirme testlerinde sahte (fake) bir `fetcher` kullanılır.
"""

from __future__ import annotations

import pytest
from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.offline_cache import (
    DEFAULT_MAX_TILES_PER_DOWNLOAD,
    TileCache,
    deg2tile,
    download_bbox,
    download_tiles,
    tile2deg,
    tiles_for_bbox,
    tiles_for_bbox_zoom_range,
)
from harita.offline_cache.local_place_index import (
    LocalPlaceIndex,
    PlaceEntry,
    build_index_from_collection,
    merge_indices,
)

# --------------------------------------------------------------------------- #
# Slippy-map tile matematiği
# --------------------------------------------------------------------------- #


def test_deg2tile_known_reference_point():
    # OSM Wiki referans örneği: Zoom 18'de (lat=51.5, lon=0.0) civarı Londra.
    x, y = deg2tile(51.5, 0.0, 18)
    assert isinstance(x, int) and isinstance(y, int)
    assert x > 0 and y > 0


def test_deg2tile_clamps_to_valid_range():
    x, y = deg2tile(89.9, 179.9, 3)
    n = 2**3
    assert 0 <= x < n
    assert 0 <= y < n


def test_tile2deg_roundtrip_is_close_to_origin():
    zoom = 10
    x, y = deg2tile(41.0, 29.0, zoom)
    lat, lon = tile2deg(x, y, zoom)
    # tile2deg tile'ın kuzeybatı köşesini döner - orijinal noktaya "yakın"
    # olmalı (bir tile genişliği kadar tolerans).
    assert abs(lat - 41.0) < 1.0
    assert abs(lon - 29.0) < 1.0


def test_tiles_for_bbox_returns_nonempty_grid():
    tiles = tiles_for_bbox(41.0, 29.0, 41.05, 29.05, zoom=14)
    assert len(tiles) > 0
    assert all(z == 14 for z, _x, _y in tiles)


def test_tiles_for_bbox_handles_swapped_coordinates():
    normal = tiles_for_bbox(41.0, 29.0, 41.05, 29.05, zoom=12)
    swapped = tiles_for_bbox(41.05, 29.05, 41.0, 29.0, zoom=12)
    assert set(normal) == set(swapped)


def test_tiles_for_bbox_zoom_range_covers_all_levels():
    tiles = tiles_for_bbox_zoom_range(41.0, 29.0, 41.02, 29.02, zoom_min=10, zoom_max=12)
    zooms = {z for z, _x, _y in tiles}
    assert zooms == {10, 11, 12}


# --------------------------------------------------------------------------- #
# TileCache
# --------------------------------------------------------------------------- #


def test_tile_cache_write_and_read_roundtrip(tmp_path):
    cache = TileCache(tmp_path)
    cache.write_tile(12, 5, 7, b"fake-png-bytes")
    assert cache.has_tile(12, 5, 7)
    assert cache.read_tile(12, 5, 7) == b"fake-png-bytes"


def test_tile_cache_missing_tile_returns_none(tmp_path):
    cache = TileCache(tmp_path)
    assert cache.read_tile(1, 1, 1) is None
    assert not cache.has_tile(1, 1, 1)


def test_tile_cache_count_and_bytes(tmp_path):
    cache = TileCache(tmp_path)
    cache.write_tile(1, 0, 0, b"abc")
    cache.write_tile(1, 0, 1, b"de")
    assert cache.tile_count() == 2
    assert cache.total_bytes() == 5


def test_tile_cache_manifest_records_region(tmp_path):
    cache = TileCache(tmp_path)
    cache.record_region(
        name="test-bolge",
        min_lat=41.0,
        min_lon=29.0,
        max_lat=41.1,
        max_lon=29.1,
        zoom_min=10,
        zoom_max=12,
        tile_count=42,
    )
    manifest = cache.load_manifest()
    assert len(manifest) == 1
    assert manifest[0]["name"] == "test-bolge"
    assert manifest[0]["tile_count"] == 42


def test_tile_cache_manifest_empty_when_no_file(tmp_path):
    cache = TileCache(tmp_path)
    assert cache.load_manifest() == []


# --------------------------------------------------------------------------- #
# download_tiles / download_bbox - sahte fetcher, ağ gerektirmez
# --------------------------------------------------------------------------- #


def test_download_tiles_writes_all_to_cache(tmp_path):
    cache = TileCache(tmp_path)
    tiles = [(10, 0, 0), (10, 0, 1), (10, 1, 0)]

    def fake_fetcher(url: str) -> bytes:
        return f"tile-for-{url}".encode()

    result = download_tiles(
        cache, tiles, "https://example.invalid/{z}/{x}/{y}.png", fetcher=fake_fetcher
    )
    assert result.downloaded == 3
    assert result.already_cached == 0
    assert result.failed == 0
    for z, x, y in tiles:
        assert cache.has_tile(z, x, y)


def test_download_tiles_skips_already_cached(tmp_path):
    cache = TileCache(tmp_path)
    cache.write_tile(10, 0, 0, b"already-here")
    calls = []

    def fake_fetcher(url: str) -> bytes:
        calls.append(url)
        return b"new-data"

    result = download_tiles(
        cache,
        [(10, 0, 0), (10, 0, 1)],
        "https://example.invalid/{z}/{x}/{y}.png",
        fetcher=fake_fetcher,
    )
    assert result.already_cached == 1
    assert result.downloaded == 1
    assert len(calls) == 1
    # Onceden var olan tile UZERINE YAZILMADI (skip_existing varsayilan True).
    assert cache.read_tile(10, 0, 0) == b"already-here"


def test_download_tiles_handles_individual_failures(tmp_path):
    cache = TileCache(tmp_path)

    def flaky_fetcher(url: str) -> bytes:
        if "/1/" in url:
            raise TimeoutError("simulated network failure")
        return b"ok"

    tiles = [(10, 0, 0), (10, 1, 0)]
    result = download_tiles(
        cache, tiles, "https://example.invalid/{z}/{x}/{y}.png", fetcher=flaky_fetcher
    )
    assert result.downloaded == 1
    assert result.failed == 1
    assert (10, 1, 0) in result.failed_tiles


def test_download_tiles_respects_max_tiles_limit(tmp_path):
    cache = TileCache(tmp_path)
    tiles = [(10, x, 0) for x in range(50)]

    def fake_fetcher(url: str) -> bytes:
        return b"x"

    result = download_tiles(
        cache, tiles, "https://example.invalid/{z}/{x}/{y}.png", fetcher=fake_fetcher, max_tiles=10
    )
    assert result.requested == 10
    assert cache.tile_count() == 10


def test_default_max_tiles_is_a_reasonable_positive_number():
    assert DEFAULT_MAX_TILES_PER_DOWNLOAD > 0


def test_download_bbox_end_to_end_records_manifest(tmp_path):
    cache = TileCache(tmp_path)

    def fake_fetcher(url: str) -> bytes:
        return b"tile-bytes"

    result = download_bbox(
        cache,
        min_lat=41.0,
        min_lon=29.0,
        max_lat=41.02,
        max_lon=29.02,
        zoom_min=13,
        zoom_max=14,
        url_template="https://example.invalid/{z}/{x}/{y}.png",
        region_name="ankara-test",
        fetcher=fake_fetcher,
    )
    assert result.downloaded > 0
    manifest = cache.load_manifest()
    assert len(manifest) == 1
    assert manifest[0]["name"] == "ankara-test"
    assert manifest[0]["zoom_range"] == [13, 14]


# --------------------------------------------------------------------------- #
# LocalPlaceIndex - offline Nominatim alternatifi
# --------------------------------------------------------------------------- #


def test_search_finds_case_and_turkish_char_insensitive_match():
    index = LocalPlaceIndex()
    index.add(PlaceEntry(name="Kızılay Meydanı", lat=39.92, lon=32.85))
    results = index.search("kizilay")
    assert len(results) == 1
    assert results[0].name == "Kızılay Meydanı"


def test_search_empty_query_returns_empty():
    index = LocalPlaceIndex()
    index.add(PlaceEntry(name="Ulus", lat=39.94, lon=32.86))
    assert index.search("") == []


def test_search_respects_limit():
    index = LocalPlaceIndex()
    for i in range(5):
        index.add(PlaceEntry(name=f"Park {i}", lat=0.0, lon=0.0))
    assert len(index.search("park", limit=3)) == 3


def test_save_and_load_roundtrip(tmp_path):
    index = LocalPlaceIndex()
    index.add(PlaceEntry(name="Anıtkabir", lat=39.925, lon=32.836, category="place_of_worship"))
    path = tmp_path / "places.json"
    index.save(path)
    loaded = LocalPlaceIndex.load(path)
    assert len(loaded) == 1
    assert loaded.entries[0].name == "Anıtkabir"


def test_load_missing_file_returns_empty_index(tmp_path):
    loaded = LocalPlaceIndex.load(tmp_path / "does-not-exist.json")
    assert len(loaded) == 0


def test_build_index_from_collection_extracts_named_point_features():
    collection = GeoFeatureCollection(
        features=[
            GeoFeature(
                geometry_type="Point",
                coordinates=(32.85, 39.92),
                properties={"name": "Kugulu Park", "amenity": "cafe"},
            ),
            GeoFeature(
                geometry_type="Point", coordinates=(32.86, 39.93), properties={}
            ),  # isimsiz -> atlanir
        ]
    )
    index = build_index_from_collection(collection, category="cafe")
    assert len(index) == 1
    assert index.entries[0].name == "Kugulu Park"
    assert index.entries[0].category == "cafe"


def test_build_index_from_collection_handles_polygon_centroid():
    collection = GeoFeatureCollection(
        features=[
            GeoFeature(
                geometry_type="Polygon",
                coordinates=[[(29.0, 41.0), (29.01, 41.0), (29.01, 41.01), (29.0, 41.01)]],
                properties={"name": "Orman Parkı"},
            ),
        ]
    )
    index = build_index_from_collection(collection)
    assert len(index) == 1
    assert index.entries[0].lat == pytest.approx(41.005, abs=1e-6)
    assert index.entries[0].lon == pytest.approx(29.005, abs=1e-6)


def test_build_index_from_collection_handles_linestring_midpoint():
    collection = GeoFeatureCollection(
        features=[
            GeoFeature(
                geometry_type="LineString",
                coordinates=[(29.0, 41.0), (29.01, 41.0), (29.02, 41.0)],
                properties={"name": "Ana Cadde"},
            ),
        ]
    )
    index = build_index_from_collection(collection)
    assert len(index) == 1
    assert index.entries[0].lon == pytest.approx(29.01)


def test_merge_indices_combines_entries():
    a = LocalPlaceIndex([PlaceEntry(name="A", lat=0.0, lon=0.0)])
    b = LocalPlaceIndex([PlaceEntry(name="B", lat=1.0, lon=1.0)])
    merged = merge_indices([a, b])
    assert len(merged) == 2
