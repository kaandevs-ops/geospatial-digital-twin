"""
harita_modelleme/phase1_core_engine/tests/test_phase1.py
============================================================
FAZ 1 doğrulama testleri. Çalıştırma:

    python -m pytest harita_modelleme/phase1_core_engine/tests -v

veya bağımsız script olarak:

    python harita_modelleme/phase1_core_engine/tests/test_phase1.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from harita_modelleme.phase1_core_engine import (  # noqa: E402
    WGS84,
    DiskTileCache,
    Feature,
    FeatureCollection,
    FormatRegistry,
    LineOps,
    LocalCoordinateSystem,
    MemoryTileCache,
    PointOps,
    PolygonOps,
    TileCoordinate,
    TileEngine,
    haversine_distance_m,
    lonlat_to_tile,
    parse_geojson,
    tile_to_lonlat_bounds,
    utm_to_wgs84,
    web_mercator_to_wgs84,
    wgs84_to_utm,
    wgs84_to_web_mercator,
    write_geojson,
)
from harita_modelleme.phase1_core_engine.geojson_gis import FormatNotImplementedError

# ============================================================================
# COORDINATE SYSTEMS
# ============================================================================


def test_web_mercator_roundtrip():
    original = WGS84(lon=32.8597, lat=39.9334)  # Ankara
    merc = wgs84_to_web_mercator(original)
    back = web_mercator_to_wgs84(merc)
    assert abs(back.lon - original.lon) < 1e-9
    assert abs(back.lat - original.lat) < 1e-9


def test_utm_roundtrip():
    original = WGS84(lon=32.8597, lat=39.9334)  # Ankara -> zone 36
    utm = wgs84_to_utm(original)
    assert utm.zone == 36
    assert utm.hemisphere == "N"
    back = utm_to_wgs84(utm)
    assert abs(back.lon - original.lon) < 1e-6
    assert abs(back.lat - original.lat) < 1e-6


def test_haversine_known_distance():
    # Ankara -> Istanbul yaklaşık 351 km büyük daire mesafesi
    ankara = WGS84(lon=32.8597, lat=39.9334)
    istanbul = WGS84(lon=28.9784, lat=41.0082)
    d = haversine_distance_m(ankara, istanbul)
    assert 340_000 < d < 360_000


def test_local_coordinate_system_roundtrip():
    origin = WGS84(lon=32.8597, lat=39.9334)
    lcs = LocalCoordinateSystem(origin)
    target = WGS84(lon=32.8620, lat=39.9350, alt=15.0)
    x, y, z = lcs.to_local(target)
    back = lcs.to_wgs84(x, y, z)
    assert abs(back.lon - target.lon) < 1e-7
    assert abs(back.lat - target.lat) < 1e-7
    assert abs(back.alt - target.alt) < 1e-9


# ============================================================================
# TILE ENGINE
# ============================================================================


def test_tile_math_roundtrip_bounds_contains_point():
    lon, lat, zoom = 32.8597, 39.9334, 14
    tile = lonlat_to_tile(lon, lat, zoom)
    west, south, east, north = tile_to_lonlat_bounds(tile)
    assert west <= lon <= east
    assert south <= lat <= north


def test_tile_parent_children_consistency():
    tile = TileCoordinate(x=10, y=10, z=5)
    children = tile.children()
    for child in children:
        assert child.parent() == tile


def test_memory_cache_lru_eviction():
    cache = MemoryTileCache(max_items=2)
    t1, t2, t3 = TileCoordinate(0, 0, 1), TileCoordinate(1, 0, 1), TileCoordinate(0, 1, 1)
    cache.put(t1, b"a")
    cache.put(t2, b"b")
    cache.put(t3, b"c")  # t1 evict edilmeli (LRU)
    assert cache.get(t1) is None
    assert cache.get(t2) == b"b"
    assert cache.get(t3) == b"c"


def test_disk_cache_persist():
    with tempfile.TemporaryDirectory() as tmp:
        cache = DiskTileCache(tmp)
        tile = TileCoordinate(3, 4, 5)
        cache.put(tile, b"tile-data")
        assert cache.has(tile)
        assert cache.get(tile) == b"tile-data"


def test_tile_engine_async_load_and_cache_chain():
    calls = {"n": 0}

    async def loader(tile: TileCoordinate) -> bytes:
        calls["n"] += 1
        await asyncio.sleep(0)
        return f"data-{tile.key()}".encode()

    with tempfile.TemporaryDirectory() as tmp:
        engine = TileEngine(loader_fn=loader, disk_cache=DiskTileCache(tmp))
        tile = TileCoordinate(1, 1, 2)

        async def run():
            data1 = await engine.get_tile(tile)
            data2 = await engine.get_tile(tile)  # memory cache'ten gelmeli
            return data1, data2

        data1, data2 = asyncio.run(run())
        assert data1 == data2 == b"data-2/1/1"
        assert calls["n"] == 1  # loader sadece 1 kez çağrılmalı
        assert engine.stats.memory_hits == 1


def test_visible_tiles_covers_viewport():
    engine = TileEngine()
    tiles = engine.visible_tiles(west=32.80, south=39.90, east=32.92, north=39.97, zoom=13)
    assert len(tiles) > 0
    for t in tiles:
        assert t.z == 13


# ============================================================================
# GEOJSON / GIS CORE
# ============================================================================


def test_geojson_parse_polygon_feature():
    gj = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "test-bina"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [32.85, 39.93],
                            [32.86, 39.93],
                            [32.86, 39.94],
                            [32.85, 39.94],
                            [32.85, 39.93],
                        ]
                    ],
                },
            }
        ],
    }
    fc = parse_geojson(gj)
    assert len(fc.features) == 1
    assert fc.features[0].properties["name"] == "test-bina"
    bbox = fc.bbox()
    assert bbox is not None
    assert bbox[0] == 32.85 and bbox[2] == 32.86


def test_geojson_roundtrip_string():
    fc = FeatureCollection(
        features=[
            Feature(geometry={"type": "Point", "coordinates": [32.85, 39.93]}, properties={"a": 1})
        ]
    )
    text = write_geojson(fc)
    fc2 = parse_geojson(text)
    assert fc2.features[0].geometry["coordinates"] == [32.85, 39.93]


def test_geojson_invalid_lat_raises():
    bad = {"type": "Point", "coordinates": [32.85, 999.0]}
    try:
        parse_geojson(bad)
        assert False, "hata bekleniyordu"
    except Exception:
        pass


def test_format_registry_geojson_works_and_others_are_explicit_not_implemented():
    registry = FormatRegistry()
    assert "geojson" in registry.supported_formats()
    assert "shapefile" in registry.planned_formats()
    fc = FeatureCollection(
        features=[Feature(geometry={"type": "Point", "coordinates": [1.0, 2.0]})]
    )
    data = registry.write("geojson", fc)
    fc_back = registry.read("geojson", data)
    assert fc_back.features[0].geometry["coordinates"] == [1.0, 2.0]

    try:
        registry.read("dxf", b"...")
        assert False, "FormatNotImplementedError bekleniyordu"
    except FormatNotImplementedError:
        pass


# ============================================================================
# GEOMETRY ENGINE
# ============================================================================


def test_polygon_area_and_centroid_square():
    square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    assert abs(PolygonOps.area(square) - 100.0) < 1e-9
    cx, cy = PolygonOps.centroid(square)
    assert abs(cx - 5.0) < 1e-9 and abs(cy - 5.0) < 1e-9


def test_polygon_contains_point():
    square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    assert PolygonOps.contains_point(square, (5, 5)) is True
    assert PolygonOps.contains_point(square, (15, 5)) is False


def test_polygon_convex_hull():
    pts = [(0, 0), (1, 1), (2, 2), (0, 2), (2, 0), (1, 0.5)]
    hull = PolygonOps.convex_hull(pts)
    assert (0, 0) in hull and (2, 0) in hull and (0, 2) in hull
    assert hull[0] == hull[-1]


def test_polygon_simplify_reduces_points():
    noisy_line = [(0, 0), (1, 0.05), (2, -0.05), (3, 0.02), (4, 0)]
    simplified = PolygonOps.simplify(noisy_line, epsilon=0.5)
    assert len(simplified) <= len(noisy_line)
    assert simplified[0] == noisy_line[0]
    assert simplified[-1] == noisy_line[-1]


def test_polygon_clip_square_to_triangle():
    square = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]
    triangle = [(0, 0), (4, 0), (0, 4), (0, 0)]
    clipped = PolygonOps.clip(square, triangle)
    area = abs(PolygonOps.area(clipped))
    assert abs(area - 8.0) < 1e-6  # üçgen alanı = 0.5*4*4 = 8


def test_polygon_split_by_line():
    square = [(0, 0), (4, 0), (4, 4), (0, 4)]
    left, right = PolygonOps.split(square, (2, -1), (2, 5))
    total_area = abs(PolygonOps.area(left)) + abs(PolygonOps.area(right))
    assert abs(total_area - 16.0) < 1e-6


def test_line_smoothing_chaikin():
    line = [(0, 0), (5, 5), (10, 0)]
    smoothed = LineOps.smoothing(line, iterations=2)
    assert len(smoothed) > len(line)


def test_line_offset_parallel_distance():
    line = [(0, 0), (10, 0)]
    offset = LineOps.offset(line, distance=2.0)
    assert abs(offset[0][1] - 2.0) < 1e-9 or abs(offset[0][1] + 2.0) < 1e-9


def test_line_intersection():
    a = ((0, 0), (10, 10))
    b = ((0, 10), (10, 0))
    ip = LineOps.intersection(a, b)
    assert ip is not None
    assert abs(ip[0] - 5.0) < 1e-9 and abs(ip[1] - 5.0) < 1e-9


def test_point_clustering_dbscan():
    cluster1 = [(0, 0), (0.1, 0.1), (0.2, 0.0)]
    cluster2 = [(50, 50), (50.1, 50.1)]
    points = cluster1 + cluster2
    clusters = PointOps.clustering(points, eps=1.0, min_points=2)
    sizes = sorted(len(c) for c in clusters)
    assert 3 in sizes and 2 in sizes


def test_point_indexing_and_nearest():
    points = [(0, 0), (10, 10), (5, 5), (5.1, 5.1)]
    idx = PointOps.indexing(points, cell_size=2.0)
    nearest_idx = idx.nearest((5.0, 5.0))
    assert nearest_idx in (2, 3)


def test_point_nearest_search_brute_force():
    points = [(0, 0), (10, 10), (5, 5)]
    results = PointOps.nearest_search(points, query=(5.1, 5.1), k=1)
    assert results[0][0] == 2


# ============================================================================
# TEST RUNNER (pytest yoksa bağımsız çalıştırma)
# ============================================================================

if __name__ == "__main__":
    test_fns = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    passed, failed = 0, 0
    for fn in test_fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {fn.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed (toplam {passed + failed})")
    if failed:
        raise SystemExit(1)
