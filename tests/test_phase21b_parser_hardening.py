"""Faz 21 (Ölçek & Güvenlik Sertleştirmesi) — devam.

ROADMAP_V2.md'de "Faz 21'in kalan işi" olarak belirtilen maddelerden biri:
"diğer GIS parser'larda (Shapefile/DXF/GPKG) benzer boyut sertleştirmesi"
ve "sistematik bir tarama". Bu dosya, önceki Faz 21 oturumunda bulunmayan
4 yeni gerçek bulguyu PoC ile kanıtlar ve düzeltmeyi doğrular:

1. `GeoPackageParser`, `Mesh3DParser`, `HeightmapParser`, `GeoJSONParser`
   -- daha önce (Shapefile/KML/KMZ/DXF'in aksine) düşük seviye ayrıştırma
   hatalarını (struct.error/ValueError/KeyError/json.JSONDecodeError/
   sqlite3.Error) `GISParseError`'a çevirmiyorlardı; bozuk bir dosya ham
   bir traceback'e neden olabilirdi. Artık tüm parser'lar tutarlı şekilde
   `GISParseError` fırlatıyor.
2. `ShapefileParser._parse_record`: PolyLine/Polygon/MultiPoint kayıtlarında
   `num_parts`/`num_points` alanları kayıt içeriğinden okunan kontrolsüz
   32-bit tam sayılardır. Kötü niyetli/bozuk bir dosya bu alanı dev bir
   değerle (örn. ~2 milyar) beyan edip gerçek veriyi sağlamayabilir; eski
   kodda bu doğrudan `f"<{num_parts}i"` format string'i kurup
   `struct.unpack` çağırıyordu — bu, gerçek dosya birkaç yüz byte olsa
   bile devasa bir format string/tuple kurarak bellek tüketen bir DoS'a
   yol açabilirdi. Artık beyan edilen sayı, kalan gerçek byte miktarına
   göre önce doğrulanıyor.
3. `GeoPackageParser._parse_wkb`: WKB'deki ring/parça `count` alanları da
   aynı şekilde kontrolsüz 32-bit tam sayılardı (Polygon/MultiPoint/
   MultiLineString/MultiPolygon). Artık her `count` okunuşunda kalan
   byte'a göre üst sınır doğrulanıyor.
4. `GeoPackageParser`: tablo adı doğrudan bir f-string ile SQL sorgusuna
   gömülüyordu (identifier quoting yoktu). Artık çift tırnak kaçırılarak
   güvenli hale getirildi.

Tüm testler: önce PoC'nin (fix öncesi) gerçek bir sorun ürettiğini kanıtlar
niyetiyle yazıldı, düzeltme sonrası hepsi beklenen `GISParseError`'ı
üretir/geçerli veriyi bozmadan çalışır.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.gis_core import (
    GeoJSONParser,
    GeoPackageParser,
    GISParseError,
    HeightmapParser,
    Mesh3DParser,
    ShapefileParser,
)

# ------------------------------------------------------------------ #
# 1. GeoJSON — artık GISParseError'a sarmalanıyor
# ------------------------------------------------------------------ #


def test_geojson_malformed_json_raises_parse_error(tmp_path):
    path = tmp_path / "bad.geojson"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(GISParseError):
        GeoJSONParser.parse_file(str(path))


def test_geojson_unsupported_type_raises_parse_error(tmp_path):
    path = tmp_path / "bad2.geojson"
    path.write_text(json.dumps({"type": "NotAGeoJSONType"}), encoding="utf-8")
    with pytest.raises(GISParseError):
        GeoJSONParser.parse_file(str(path))


def test_geojson_valid_file_still_parses(tmp_path):
    path = tmp_path / "ok.geojson"
    path.write_text(
        json.dumps(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [28.9784, 41.0082]},
                "properties": {"name": "Istanbul"},
            }
        ),
        encoding="utf-8",
    )
    fc = GeoJSONParser.parse_file(str(path))
    assert len(fc) == 1
    assert fc.features[0].geometry_type == "Point"


# ------------------------------------------------------------------ #
# 2. GeoPackage — GISParseError sarmalaması + SQL identifier güvenliği
# ------------------------------------------------------------------ #


def _make_gpkg(
    tmp_path,
    table="points",
    geom_col="geom",
    extra_sql=None,
    blob_rows=(b"garbage-not-a-gpb-blob",),
):
    import sqlite3

    path = tmp_path / "test.gpkg"
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT)")
    cur.execute("INSERT INTO gpkg_geometry_columns VALUES (?, ?)", (table, geom_col))
    safe_table = table.replace('"', '""')
    cur.execute(f'CREATE TABLE "{safe_table}" (id INTEGER, "{geom_col}" BLOB)')
    for i, blob in enumerate(blob_rows):
        cur.execute(f'INSERT INTO "{safe_table}" VALUES (?, ?)', (i, blob))
    if extra_sql:
        cur.execute(extra_sql)
    conn.commit()
    conn.close()
    return path


def test_gpkg_missing_geometry_columns_table_raises_parse_error(tmp_path):
    import sqlite3

    path = tmp_path / "nogeo.gpkg"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(GISParseError):
        GeoPackageParser().parse_file(str(path))


def test_gpkg_corrupt_wkb_blob_raises_parse_error_not_raw_exception(tmp_path):
    """PoC: eskiden bu, yakalanmamış bir struct.error/IndexError fırlatıyordu.
    Header geçerli (GP magic + SRS ID) ama WKB kısmı bir Point ilan edip
    gerçek koordinat verisini sağlamıyor -> struct.unpack buffer-too-small
    fırlatır; eskiden bu yakalanmıyordu."""
    truncated_wkb = (
        struct.pack("<BI", 1, 1) + b"\x00" * 3
    )  # Point ilan edildi, 16 byte yerine 3 byte
    blob = b"GP" + bytes([0, 0]) + b"\x00\x00\x00\x00" + truncated_wkb
    path = _make_gpkg(tmp_path, blob_rows=(blob,))
    with pytest.raises(GISParseError):
        GeoPackageParser().parse_file(str(path))


def test_gpkg_table_name_with_quote_is_handled_safely(tmp_path):
    """SQL identifier kaçış PoC'si: tablo adında çift tırnak olsa bile
    sorgu güvenli şekilde inşa edilir (crash/injection yok)."""
    weird_table = 'weird"table'
    path = _make_gpkg(tmp_path, table=weird_table, blob_rows=())
    # Boş tablo -> features boş dönmeli, exception fırlatılmamalı.
    fc = GeoPackageParser().parse_file(str(path))
    assert len(fc) == 0


def test_gpkg_wkb_huge_declared_ring_count_raises_parse_error_fast(tmp_path):
    """PoC: WKB Polygon'da num_rings alanı gerçek dosyadan çok daha büyük
    (2^31-1) beyan edilirse, eskiden `range(num_rings)` üzerinde dönülmeye
    çalışılırdı (bellek/CPU tüketen DoS potansiyeli). Artık kalan byte'a
    göre imkansız olduğu hemen anlaşılıp GISParseError fırlatılır."""
    # WKB: byte_order(1=little) + geom_type(3=Polygon, 4 byte) + num_rings(4 byte, dev deger)
    wkb = struct.pack("<BIi", 1, 3, 0)[:5] + struct.pack("<i", 2**31 - 1)
    blob = b"GP" + bytes([0, 0]) + b"\x00\x00\x00\x00" + wkb
    path = _make_gpkg(tmp_path, blob_rows=(blob,))
    import time

    start = time.monotonic()
    with pytest.raises(GISParseError):
        GeoPackageParser().parse_file(str(path))
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, "dev count beyanı bellek/CPU tüketen bir döngüye yol açmamalı"


def test_gpkg_valid_point_still_parses(tmp_path):
    wkb = struct.pack("<BI", 1, 1) + struct.pack("<dd", 28.9784, 41.0082)
    blob = b"GP" + bytes([0, 0]) + b"\x00\x00\x00\x00" + wkb
    path = _make_gpkg(tmp_path, blob_rows=(blob,))
    fc = GeoPackageParser().parse_file(str(path))
    assert len(fc) == 1
    assert fc.features[0].geometry_type == "Point"
    assert fc.features[0].coordinates == [28.9784, 41.0082]


# ------------------------------------------------------------------ #
# 3. Shapefile — num_parts/num_points bellek-tükenmesi DoS koruması
# ------------------------------------------------------------------ #


def _shp_with_record(rec_content: bytes) -> bytes:
    header = struct.pack(">i", 9994) + b"\x00" * 20  # 0:24  file code + unused
    header += struct.pack(">i", 50)  # 24:28 file length
    header += struct.pack("<i", 1000)  # 28:32 version
    header += struct.pack("<i", 5) + b"\x00" * 64  # 32:36 shape type + 36:100 bbox etc.
    assert len(header) == 100
    record = struct.pack(">ii", 1, len(rec_content) // 2) + rec_content
    return header + record


def test_shapefile_huge_declared_num_parts_raises_parse_error_fast(tmp_path):
    """PoC: Polygon kaydı num_parts alanını 2^31-1 olarak beyan eder ama
    gerçek dosyada bu kadar veri yoktur. Eskiden bu doğrudan
    `f"<{num_parts}i"` format stringi kurmaya çalışırdı (dev bellek
    tüketimi). Artık kalan byte'a göre önce doğrulanır."""
    rec_content = struct.pack("<i", 5) + b"\x00" * 32  # shape_type(4) + box(32)
    rec_content += struct.pack("<ii", 2**31 - 1, 10)  # num_parts, num_points (dev deger)
    path = tmp_path / "bomb.shp"
    path.write_bytes(_shp_with_record(rec_content))
    import time

    start = time.monotonic()
    with pytest.raises(GISParseError):
        ShapefileParser().parse_file(str(path))
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, "dev num_parts beyanı bellek tüketen bir yapı kurmamalı"


def test_shapefile_huge_declared_num_points_raises_parse_error_fast(tmp_path):
    rec_content = struct.pack("<i", 5) + b"\x00" * 32
    rec_content += struct.pack("<ii", 1, 2**31 - 1)  # num_parts makul, num_points dev
    rec_content += struct.pack("<i", 0)  # tek part'ın başlangıç indeksi
    path = tmp_path / "bomb2.shp"
    path.write_bytes(_shp_with_record(rec_content))
    with pytest.raises(GISParseError):
        ShapefileParser().parse_file(str(path))


def test_shapefile_multipoint_huge_num_points_raises_parse_error(tmp_path):
    header = struct.pack(">i", 9994) + b"\x00" * 20
    header += struct.pack(">i", 50)
    header += struct.pack("<i", 1000)
    header += struct.pack("<i", 8) + b"\x00" * 64  # shape type 8 = MultiPoint
    assert len(header) == 100
    rec_content = struct.pack("<i", 8) + b"\x00" * 32
    rec_content += struct.pack("<i", 2**31 - 1)  # num_points dev
    record = struct.pack(">ii", 1, len(rec_content) // 2) + rec_content
    path = tmp_path / "bomb3.shp"
    path.write_bytes(header + record)
    with pytest.raises(GISParseError):
        ShapefileParser().parse_file(str(path))


def test_shapefile_valid_polygon_still_parses_after_hardening(tmp_path):
    """Regresyon: doğrulama, gerçek/geçerli bir Polygon kaydını bozmamalı."""
    ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    box = struct.pack("<4d", 0.0, 0.0, 1.0, 1.0)
    body = struct.pack("<i", 5) + box + struct.pack("<ii", 1, len(ring))
    body += struct.pack("<i", 0)  # parts[0] = 0
    for x, y in ring:
        body += struct.pack("<dd", x, y)
    path = tmp_path / "ok.shp"
    path.write_bytes(_shp_with_record(body))
    fc = ShapefileParser().parse_file(str(path))
    assert len(fc) == 1
    assert fc.features[0].geometry_type == "Polygon"
    assert len(fc.features[0].coordinates[0]) == 5


# ------------------------------------------------------------------ #
# 4. Mesh3D / Heightmap — GISParseError sarmalaması
# ------------------------------------------------------------------ #


def test_mesh3d_obj_bad_float_raises_parse_error(tmp_path):
    path = tmp_path / "bad.obj"
    path.write_text("v NOT A FLOAT 0 0\n", encoding="utf-8")
    with pytest.raises(GISParseError):
        Mesh3DParser("OBJ").parse_file(str(path))


def test_mesh3d_stl_binary_truncated_header_raises_parse_error(tmp_path):
    path = tmp_path / "bad.stl"
    path.write_bytes(b"\x00" * 70)  # 80 byte header için çok kısa + count alanı eksik
    with pytest.raises(GISParseError):
        Mesh3DParser("STL").parse_file(str(path))


def test_mesh3d_gltf_malformed_json_raises_parse_error(tmp_path):
    path = tmp_path / "bad.gltf"
    path.write_text("{not valid json at all", encoding="utf-8")
    with pytest.raises(GISParseError):
        Mesh3DParser("GLTF").parse_file(str(path))


def test_mesh3d_gltf_missing_accessor_raises_parse_error(tmp_path):
    doc = {
        "meshes": [{"primitives": [{"attributes": {"POSITION": 99}}]}],
        "accessors": [],
        "bufferViews": [],
        "buffers": [],
    }
    path = tmp_path / "bad2.gltf"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(GISParseError):
        Mesh3DParser("GLTF").parse_file(str(path))


def test_heightmap_missing_header_field_raises_parse_error(tmp_path):
    path = tmp_path / "bad.asc"
    path.write_text("ncols 4\nnrows 4\n0 0 0 0\n0 0 0 0\n0 0 0 0\n0 0 0 0\n", encoding="utf-8")
    # cellsize/origin eksik olsa da bunlar .get() ile toleranslı; gerçek
    # bir bozukluk için ncols/nrows'u tamamen kaldıralım.
    path.write_text("cellsize 1\n0 0 0 0\n", encoding="utf-8")
    with pytest.raises(GISParseError):
        HeightmapParser().parse_file(str(path))


def test_heightmap_raw_binary_size_mismatch_raises_parse_error(tmp_path):
    json_path = tmp_path / "dem.json"
    json_path.write_text(json.dumps({"width": 100, "height": 100}), encoding="utf-8")
    bin_path = tmp_path / "dem.bin"
    bin_path.write_bytes(b"\x00" * 8)  # 100*100*4 bekleniyor, yalnızca 8 byte var
    with pytest.raises(GISParseError):
        HeightmapParser().parse_file(str(bin_path))


def test_heightmap_valid_esri_ascii_still_parses(tmp_path):
    path = tmp_path / "ok.asc"
    path.write_text(
        "ncols 2\nnrows 2\nxllcorner 0\nyllcorner 0\ncellsize 1\nnodata_value -9999\n1 2\n3 4\n",
        encoding="utf-8",
    )
    fc = HeightmapParser().parse_file(str(path))
    coords = fc.features[0].coordinates
    assert coords["width"] == 2
    assert coords["height"] == 2
    assert coords["elevations"] == [[1.0, 2.0], [3.0, 4.0]]
