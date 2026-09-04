"""Phase 1 (Core Engine) için birim testleri."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.coordinate_systems import CoordinateConverter, GeoPoint, CoordinateSystem
from harita.core_engine.geometry_engine import Point2D, Polygon, LineString, GeometryEngine, PointIndex
from harita.core_engine.gis_core import (
    GeoJSONParser,
    ShapefileParser,
    KMLParser,
    KMZParser,
    GeoPackageParser,
    DXFParser,
    Mesh3DParser,
    HeightmapParser,
)
from harita.core_engine.tile_engine import TileCoordinate, TileEngine, TileCache, MemoryCache


# ------------------------------------------------------------------ #
# Coordinate systems
# ------------------------------------------------------------------ #

def test_web_mercator_roundtrip():
    p = GeoPoint(lat=41.0082, lon=28.9784)  # İstanbul
    merc = CoordinateConverter.wgs84_to_web_mercator(p)
    back = CoordinateConverter.web_mercator_to_wgs84(merc)
    assert math.isclose(back.lat, p.lat, abs_tol=1e-6)
    assert math.isclose(back.lon, p.lon, abs_tol=1e-6)


def test_utm_roundtrip():
    p = GeoPoint(lat=39.9334, lon=32.8597)  # Ankara
    zone = CoordinateConverter.utm_zone_for(p.lon)
    utm = CoordinateConverter.wgs84_to_utm(p, zone)
    back = CoordinateConverter.utm_to_wgs84(utm, northern_hemisphere=True)
    assert math.isclose(back.lat, p.lat, abs_tol=1e-4)
    assert math.isclose(back.lon, p.lon, abs_tol=1e-4)


def test_local_roundtrip():
    origin = GeoPoint(lat=39.9334, lon=32.8597)
    p = GeoPoint(lat=39.9350, lon=32.8620)
    local = CoordinateConverter.wgs84_to_local(p, origin)
    back = CoordinateConverter.local_to_wgs84(local, origin)
    assert math.isclose(back.lat, p.lat, abs_tol=1e-6)
    assert math.isclose(back.lon, p.lon, abs_tol=1e-6)


def test_epsg_transform_wgs84_to_web_mercator_matches_direct():
    p = GeoPoint(lat=39.9334, lon=32.8597)  # Ankara
    direct = CoordinateConverter.wgs84_to_web_mercator(p)
    via_epsg = CoordinateConverter.transform_epsg(
        p, CoordinateConverter.EPSG_WGS84, CoordinateConverter.EPSG_WEB_MERCATOR
    )
    assert math.isclose(direct.x, via_epsg.x, abs_tol=1e-6)
    assert math.isclose(direct.y, via_epsg.y, abs_tol=1e-6)


def test_epsg_transform_utm_roundtrip():
    p = GeoPoint(lat=39.9334, lon=32.8597)  # Ankara -> UTM zone 36N
    epsg_utm = CoordinateConverter.utm_zone_to_epsg(36, northern_hemisphere=True)
    assert epsg_utm == 32636
    utm_point = CoordinateConverter.transform_epsg(p, CoordinateConverter.EPSG_WGS84, epsg_utm)
    back = CoordinateConverter.transform_epsg(utm_point, epsg_utm, CoordinateConverter.EPSG_WGS84)
    assert math.isclose(back.lat, p.lat, abs_tol=1e-4)
    assert math.isclose(back.lon, p.lon, abs_tol=1e-4)


def test_epsg_transform_utm_to_web_mercator_chain():
    p = GeoPoint(lat=39.9334, lon=32.8597)
    epsg_utm = CoordinateConverter.utm_zone_to_epsg(36, northern_hemisphere=True)
    utm_point = CoordinateConverter.transform_epsg(p, CoordinateConverter.EPSG_WGS84, epsg_utm)
    merc = CoordinateConverter.transform_epsg(
        utm_point, epsg_utm, CoordinateConverter.EPSG_WEB_MERCATOR
    )
    direct_merc = CoordinateConverter.wgs84_to_web_mercator(p)
    assert math.isclose(merc.x, direct_merc.x, abs_tol=1e-3)
    assert math.isclose(merc.y, direct_merc.y, abs_tol=1e-3)


def test_epsg_same_source_target_is_identity():
    p = GeoPoint(lat=39.9334, lon=32.8597)
    result = CoordinateConverter.transform_epsg(p, 4326, 4326)
    assert result is p


def test_epsg_unknown_code_raises():
    p = GeoPoint(lat=39.9334, lon=32.8597)
    with pytest.raises(ValueError):
        CoordinateConverter.transform_epsg(p, 4326, 5253)  # ED50/TM30 - kayıtlı değil


def test_epsg_is_known_and_description():
    assert CoordinateConverter.is_known_epsg(4326)
    assert CoordinateConverter.is_known_epsg(3857)
    assert CoordinateConverter.is_known_epsg(32636)  # UTM 36N
    assert CoordinateConverter.is_known_epsg(32736)  # UTM 36S
    assert not CoordinateConverter.is_known_epsg(5253)
    assert "UTM zone 36N" in CoordinateConverter.epsg_description(32636)


def test_epsg_hemisphere_mismatch_raises():
    p = GeoPoint(lat=39.9334, lon=32.8597)  # kuzey yarımküre
    epsg_utm_south = CoordinateConverter.utm_zone_to_epsg(36, northern_hemisphere=False)
    with pytest.raises(ValueError):
        CoordinateConverter.transform_epsg(p, CoordinateConverter.EPSG_WGS84, epsg_utm_south)


def test_haversine_known_distance():
    istanbul = GeoPoint(41.0082, 28.9784)
    ankara = GeoPoint(39.9334, 32.8597)
    d_km = CoordinateConverter.haversine_distance(istanbul, ankara) / 1000
    assert 340 < d_km < 360  # gerçek ~350 km


# ------------------------------------------------------------------ #
# Geometry engine
# ------------------------------------------------------------------ #

def test_polygon_area_and_centroid_square():
    square = Polygon([Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)])
    assert math.isclose(square.unsigned_area(), 100.0)
    c = square.centroid()
    assert math.isclose(c.x, 5.0, abs_tol=1e-9)
    assert math.isclose(c.y, 5.0, abs_tol=1e-9)


def test_convex_hull():
    pts = [Point2D(0, 0), Point2D(2, 0), Point2D(2, 2), Point2D(0, 2), Point2D(1, 1)]
    hull = GeometryEngine.convex_hull(pts)
    assert hull.unsigned_area() == 4.0
    assert len(hull.points) == 4  # iç nokta hull'da olmamalı


def test_point_in_polygon():
    square = Polygon([Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)])
    assert GeometryEngine.point_in_polygon(Point2D(5, 5), square) is True
    assert GeometryEngine.point_in_polygon(Point2D(15, 5), square) is False


def test_simplify_polygon_reduces_points():
    # neredeyse düz bir kenar üzerinde gereksiz köşeler
    poly = Polygon([Point2D(0, 0), Point2D(1, 0.01), Point2D(2, -0.01), Point2D(3, 0),
                     Point2D(3, 3), Point2D(0, 3)])
    simplified = GeometryEngine.simplify_polygon(poly, tolerance=0.1)
    assert len(simplified.points) < len(poly.points)


def test_clip_polygon():
    subject = Polygon([Point2D(-5, -5), Point2D(15, -5), Point2D(15, 15), Point2D(-5, 15)])
    window = Polygon([Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)])
    clipped = GeometryEngine.clip_polygon(subject, window)
    assert math.isclose(clipped.unsigned_area(), 100.0, rel_tol=1e-6)


def test_line_smoothing_increases_points():
    line = LineString([Point2D(0, 0), Point2D(5, 5), Point2D(10, 0)])
    smoothed = GeometryEngine.smooth_line(line, iterations=1)
    assert len(smoothed.points) > len(line.points)


def test_point_clustering():
    pts = [Point2D(0, 0), Point2D(0.5, 0.5), Point2D(10, 10), Point2D(10.2, 10.1)]
    clusters = GeometryEngine.cluster_points(pts, radius=1.0)
    assert len(clusters) == 2


def test_point_index_nearest_search():
    idx = PointIndex(cell_size=5.0)
    for p in [Point2D(0, 0), Point2D(20, 20), Point2D(21, 21)]:
        idx.insert(p)
    nearest = idx.nearest_search(Point2D(19, 19))
    assert nearest == Point2D(20, 20)


# ------------------------------------------------------------------ #
# GIS core (GeoJSON)
# ------------------------------------------------------------------ #

def test_geojson_parse_feature_collection():
    geojson_text = """
    {
      "type": "FeatureCollection",
      "features": [
        {
          "type": "Feature",
          "properties": {"bina_tipi": "ofis", "kat_sayisi": 5},
          "geometry": {
            "type": "Polygon",
            "coordinates": [[[28.97, 41.00], [28.98, 41.00], [28.98, 41.01], [28.97, 41.01], [28.97, 41.00]]]
          }
        }
      ]
    }
    """
    fc = GeoJSONParser.parse(geojson_text)
    assert len(fc) == 1
    feature = fc.features[0]
    assert feature.properties["bina_tipi"] == "ofis"
    poly = feature.to_polygon()
    assert isinstance(poly, Polygon)
    assert poly.unsigned_area() > 0


def test_geojson_filter_by_properties():
    import json as _json
    fc = GeoJSONParser.parse(_json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"tip": "villa"},
             "geometry": {"type": "Point", "coordinates": [0, 0]}},
            {"type": "Feature", "properties": {"tip": "ofis"},
             "geometry": {"type": "Point", "coordinates": [1, 1]}},
        ],
    }))
    ofisler = fc.filter(tip="ofis")
    assert len(ofisler) == 1


def test_geojson_roundtrip_to_geojson():
    original = """
    {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"a": 1},
         "geometry": {"type": "Point", "coordinates": [10, 20]}}
    ]}
    """
    fc = GeoJSONParser.parse(original)
    out = GeoJSONParser.to_geojson(fc)
    fc2 = GeoJSONParser.parse(out)
    assert fc2.features[0].properties["a"] == 1
    assert fc2.features[0].coordinates == [10, 20]


# ------------------------------------------------------------------ #
# Tile engine
# ------------------------------------------------------------------ #

def test_tile_coordinate_from_geopoint_and_bounds():
    istanbul = GeoPoint(41.0082, 28.9784)
    tile = TileCoordinate.from_geopoint(istanbul, zoom=10)
    nw, se = tile.to_bounds()
    assert nw.lat > istanbul.lat > se.lat
    assert nw.lon < istanbul.lon < se.lon


def test_tile_children_and_parent():
    tile = TileCoordinate(z=5, x=10, y=10)
    children = tile.children()
    assert len(children) == 4
    assert all(c.z == 6 for c in children)
    assert children[0].parent() == tile


def test_memory_cache_lru_eviction():
    cache = MemoryCache(capacity=2)
    from harita.core_engine.tile_engine import TileData
    t1 = TileData(TileCoordinate(0, 0, 0), b"a", "raster")
    t2 = TileData(TileCoordinate(0, 1, 0), b"b", "raster")
    t3 = TileData(TileCoordinate(0, 2, 0), b"c", "raster")
    cache.put(t1)
    cache.put(t2)
    cache.put(t3)  # t1 dışarı atılmalı
    assert len(cache) == 2
    assert cache.get(TileCoordinate(0, 0, 0)) is None
    assert cache.get(TileCoordinate(0, 2, 0)) is not None


def test_tile_engine_fetch_and_cache_hit_count():
    calls = {"n": 0}

    def fetch(coord: TileCoordinate) -> bytes:
        calls["n"] += 1
        return f"tile-{coord.key()}".encode()

    engine = TileEngine(fetch_fn=fetch)
    coord = TileCoordinate(3, 1, 1)
    t1 = engine.get_tile(coord)
    t2 = engine.get_tile(coord)  # cache'ten gelmeli, fetch tekrar çağrılmamalı
    assert t1.content == t2.content
    assert calls["n"] == 1
    engine.shutdown()


def test_tile_engine_multi_thread_get_tiles():
    def fetch(coord: TileCoordinate) -> bytes:
        return coord.key().encode()

    engine = TileEngine(fetch_fn=fetch)
    coords = [TileCoordinate(2, x, y) for x in range(4) for y in range(4)]
    tiles = engine.get_tiles(coords)
    assert len(tiles) == 16
    assert {t.coord.key() for t in tiles} == {c.key() for c in coords}
    engine.shutdown()


def test_tile_engine_viewport_and_offline_preload(tmp_path):
    def fetch(coord: TileCoordinate) -> bytes:
        return b"x"

    cache = TileCache(disk_root=tmp_path)
    engine = TileEngine(fetch_fn=fetch, cache=cache)
    center = GeoPoint(41.0082, 28.9784)
    viewport = engine.tiles_in_viewport(center, zoom=8, viewport_tiles_radius=1)
    assert len(viewport) == 9  # 3x3 pencere

    nw = GeoPoint(41.02, 28.95)
    se = GeoPoint(40.98, 29.02)
    downloaded = engine.preload_offline_region(nw, se, zoom_levels=[10])
    assert downloaded > 0
    engine.shutdown()


# ------------------------------------------------------------------ #
# GIS core - ek format parser'lar (Shapefile/KML/KMZ/GPKG/DXF/Mesh3D/Heightmap)
# ------------------------------------------------------------------ #

def test_shapefile_parser_polygon(tmp_path):
    import struct
    shp_path = tmp_path / "test.shp"
    pts = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    rec_content = struct.pack("<i", 5)
    rec_content += struct.pack("<4d", 0, 0, 10, 10)
    rec_content += struct.pack("<ii", 1, len(pts))
    rec_content += struct.pack("<i", 0)
    for x, y in pts:
        rec_content += struct.pack("<dd", x, y)
    record = struct.pack(">ii", 1, len(rec_content) // 2) + rec_content
    header = struct.pack(">i", 9994) + b"\x00" * 20
    header += struct.pack(">i", (100 + len(record)) // 2)
    header += struct.pack("<i", 1000) + struct.pack("<i", 5) + b"\x00" * 64
    with open(shp_path, "wb") as f:
        f.write(header[:100])
        f.write(record)
    fc = ShapefileParser().parse_file(str(shp_path))
    assert len(fc) == 1
    assert fc.features[0].geometry_type == "Polygon"
    assert fc.features[0].to_polygon().points[0].x == 0.0


def test_kml_parser_placemark(tmp_path):
    kml_path = tmp_path / "test.kml"
    kml_path.write_text(
        '<?xml version="1.0"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><name>Bina A</name>"
        "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
        "0,0,0 10,0,0 10,10,0 0,10,0 0,0,0"
        "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
        "</Placemark></Document></kml>",
        encoding="utf-8",
    )
    fc = KMLParser().parse_file(str(kml_path))
    assert len(fc) == 1
    assert fc.features[0].geometry_type == "Polygon"
    assert fc.features[0].properties["name"] == "Bina A"


def test_kmz_parser_delegates_to_kml(tmp_path):
    import zipfile
    kml_path = tmp_path / "doc.kml"
    kml_path.write_text(
        '<?xml version="1.0"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><Point><coordinates>28.9784,41.0082,0</coordinates></Point></Placemark>"
        "</Document></kml>",
        encoding="utf-8",
    )
    kmz_path = tmp_path / "test.kmz"
    with zipfile.ZipFile(kmz_path, "w") as zf:
        zf.write(kml_path, "doc.kml")
    fc = KMZParser().parse_file(str(kmz_path))
    assert len(fc) == 1
    assert fc.features[0].geometry_type == "Point"


def test_geopackage_parser_point(tmp_path):
    import sqlite3
    import struct
    gpkg_path = tmp_path / "test.gpkg"
    conn = sqlite3.connect(gpkg_path)
    conn.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT)")
    conn.execute("CREATE TABLE buildings (id INTEGER, name TEXT, geom BLOB)")
    conn.execute("INSERT INTO gpkg_geometry_columns VALUES ('buildings','geom')")
    wkb = struct.pack("<BIdd", 1, 1, 5.0, 7.0)
    gpb = b"GP" + bytes([0]) + bytes([0]) + struct.pack("<i", 0) + wkb
    conn.execute("INSERT INTO buildings VALUES (1,'A',?)", (gpb,))
    conn.commit()
    conn.close()
    fc = GeoPackageParser().parse_file(str(gpkg_path))
    assert len(fc) == 1
    assert fc.features[0].geometry_type == "Point"
    assert fc.features[0].coordinates == [5.0, 7.0]
    assert fc.features[0].properties["name"] == "A"


def test_dxf_parser_line_and_polyline(tmp_path):
    dxf_path = tmp_path / "test.dxf"
    dxf_path.write_text(
        "0\nSECTION\n2\nENTITIES\n"
        "0\nLINE\n8\nLAYER1\n10\n0.0\n20\n0.0\n11\n5.0\n21\n5.0\n"
        "0\nLWPOLYLINE\n8\nLAYER2\n70\n1\n10\n0.0\n20\n0.0\n10\n2.0\n20\n0.0\n10\n2.0\n20\n2.0\n"
        "0\nENDSEC\n0\nEOF\n",
        encoding="utf-8",
    )
    fc = DXFParser().parse_file(str(dxf_path))
    assert len(fc) == 2
    assert fc.features[0].geometry_type == "LineString"
    assert fc.features[1].geometry_type == "Polygon"  # closed (70 flag bit 1)


# ------------------------------------------------------------------ #
# GIS core - fuzz / bozuk dosya testleri (Roadmap V2, A1 kabul kriteri)
# ------------------------------------------------------------------ #

def test_shapefile_too_short_raises_parse_error(tmp_path):
    from harita.core_engine.gis_core import GISParseError
    path = tmp_path / "short.shp"
    path.write_bytes(b"\x00" * 10)
    with pytest.raises(GISParseError):
        ShapefileParser().parse_file(str(path))


def test_shapefile_bad_file_code_raises_parse_error(tmp_path):
    from harita.core_engine.gis_core import GISParseError
    import struct
    path = tmp_path / "badcode.shp"
    path.write_bytes(struct.pack(">i", 1234) + b"\x00" * 96)
    with pytest.raises(GISParseError):
        ShapefileParser().parse_file(str(path))


def test_shapefile_truncated_record_raises_parse_error(tmp_path):
    from harita.core_engine.gis_core import GISParseError
    import struct
    path = tmp_path / "trunc.shp"
    header = struct.pack(">i", 9994) + b"\x00" * 20
    header += struct.pack(">i", 50)
    header += struct.pack("<i", 1000) + struct.pack("<i", 5) + b"\x00" * 64
    # Polygon (type 5) record'u ilan eder ama gövde verisi eksik bırakılır
    # -> num_parts/num_points struct.unpack sınır dışına taşar (struct.error).
    rec_content = struct.pack("<i", 5) + b"\x00" * 3  # header'a göre 40 byte bekleniyor, 7 var
    record = struct.pack(">ii", 1, len(rec_content) // 2) + rec_content
    path.write_bytes(header[:100] + record)
    with pytest.raises(GISParseError):
        ShapefileParser().parse_file(str(path))


def test_kml_malformed_xml_raises_parse_error(tmp_path):
    from harita.core_engine.gis_core import GISParseError
    path = tmp_path / "bad.kml"
    path.write_text("<kml><Document><Placemark><Point>NOT CLOSED", encoding="utf-8")
    with pytest.raises(GISParseError):
        KMLParser().parse_file(str(path))


def test_kml_bad_coordinate_text_raises_parse_error(tmp_path):
    from harita.core_engine.gis_core import GISParseError
    path = tmp_path / "badcoord.kml"
    path.write_text(
        '<?xml version="1.0"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><Point><coordinates>not,a,number</coordinates></Point></Placemark>"
        "</Document></kml>",
        encoding="utf-8",
    )
    with pytest.raises(GISParseError):
        KMLParser().parse_file(str(path))


def test_kmz_not_a_zip_raises_parse_error(tmp_path):
    from harita.core_engine.gis_core import GISParseError
    path = tmp_path / "fake.kmz"
    path.write_bytes(b"this is not a zip archive")
    with pytest.raises(GISParseError):
        KMZParser().parse_file(str(path))


def test_kmz_missing_kml_entry_raises_parse_error(tmp_path):
    import zipfile
    from harita.core_engine.gis_core import GISParseError
    path = tmp_path / "empty.kmz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("readme.txt", "no kml here")
    with pytest.raises(GISParseError):
        KMZParser().parse_file(str(path))


def test_dxf_bad_numeric_group_code_raises_parse_error(tmp_path):
    from harita.core_engine.gis_core import GISParseError
    path = tmp_path / "bad.dxf"
    path.write_text(
        "0\nSECTION\n2\nENTITIES\n"
        "0\nPOINT\n8\nLAYER1\n10\nNOT_A_NUMBER\n20\n0.0\n"
        "0\nENDSEC\n0\nEOF\n",
        encoding="utf-8",
    )
    with pytest.raises(GISParseError):
        DXFParser().parse_file(str(path))


def test_valid_files_still_parse_after_fuzz_hardening(tmp_path):
    """Regresyon: hata-sarmalama, geçerli dosyaların davranışını bozmamalı."""
    kml_path = tmp_path / "ok.kml"
    kml_path.write_text(
        '<?xml version="1.0"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><Point><coordinates>28.9784,41.0082,0</coordinates></Point></Placemark>"
        "</Document></kml>",
        encoding="utf-8",
    )
    fc = KMLParser().parse_file(str(kml_path))
    assert len(fc) == 1


def test_mesh3d_parser_obj(tmp_path):
    obj_path = tmp_path / "test.obj"
    obj_path.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n", encoding="utf-8")
    fc = Mesh3DParser("OBJ").parse_file(str(obj_path))
    mesh = fc.features[0].coordinates
    assert len(mesh["vertices"]) == 4
    assert len(mesh["faces"]) == 2  # fan-triangulated quad


def test_mesh3d_parser_stl_binary(tmp_path):
    import struct
    stl_path = tmp_path / "test.stl"
    with open(stl_path, "wb") as f:
        f.write(b"\x00" * 80)
        f.write(struct.pack("<I", 1))
        f.write(struct.pack("<12f", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0))
        f.write(struct.pack("<H", 0))
    fc = Mesh3DParser("STL").parse_file(str(stl_path))
    mesh = fc.features[0].coordinates
    assert len(mesh["vertices"]) == 3
    assert mesh["faces"] == [[0, 1, 2]]


def test_mesh3d_parser_gltf(tmp_path):
    import base64
    import json as _json
    import struct
    verts = [0, 0, 0, 1, 0, 0, 1, 1, 0]
    buf_bytes = struct.pack("<9f", *verts)
    b64 = base64.b64encode(buf_bytes).decode()
    doc = {
        "buffers": [{"uri": f"data:application/octet-stream;base64,{b64}", "byteLength": len(buf_bytes)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(buf_bytes)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
    }
    gltf_path = tmp_path / "test.gltf"
    gltf_path.write_text(_json.dumps(doc), encoding="utf-8")
    fc = Mesh3DParser("GLTF").parse_file(str(gltf_path))
    assert len(fc.features[0].coordinates["vertices"]) == 3


def test_mesh3d_parser_unsupported_format_raises(tmp_path):
    fbx_path = tmp_path / "test.fbx"
    fbx_path.write_text("dummy", encoding="utf-8")
    import pytest
    with pytest.raises(NotImplementedError):
        Mesh3DParser("FBX").parse_file(str(fbx_path))


def test_heightmap_parser_esri_ascii_grid(tmp_path):
    asc_path = tmp_path / "test.asc"
    asc_path.write_text(
        "ncols 3\nnrows 2\nxllcorner 0\nyllcorner 0\ncellsize 1\nNODATA_value -9999\n"
        "1 2 3\n4 5 -9999\n",
        encoding="utf-8",
    )
    fc = HeightmapParser().parse_file(str(asc_path))
    data = fc.features[0].coordinates
    assert data["width"] == 3 and data["height"] == 2
    assert data["elevations"][1][2] is None
    assert data["elevations"][0] == [1.0, 2.0, 3.0]


def test_heightmap_parser_raw_binary(tmp_path):
    import json as _json
    import struct
    bin_path = tmp_path / "test.bin"
    with open(bin_path, "wb") as f:
        f.write(struct.pack("<4f", 1.0, 2.0, 3.0, 4.0))
    (tmp_path / "test.json").write_text(
        _json.dumps({"width": 2, "height": 2, "resolution_m": 2.0, "origin": [10, 20]}),
        encoding="utf-8",
    )
    fc = HeightmapParser().parse_file(str(bin_path))
    data = fc.features[0].coordinates
    assert data["elevations"] == [[1.0, 2.0], [3.0, 4.0]]
    assert data["resolution_m"] == 2.0
