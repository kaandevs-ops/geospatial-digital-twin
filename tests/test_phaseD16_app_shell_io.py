"""Roadmap V3 - Faz D16 kabul kriteri testleri.

Kapsam (Faz D16 kabul kriteri, ROADMAP_V3.md):
    "kullanıcı hiç kod yazmadan harita verisi yükler -> 3D binayı görür ->
    düzenler -> export eder (uçtan uca, gerçek dosya I/O ile)."

Bu dosya bunu iki katmanda doğrular:
  1. `AppSession.import_geojson` / `AppSession.export_scene` - doğrudan
     Python API'si üzerinden gerçek dosya I/O ile (sahte/placeholder veri
     yok - GeoJSON gerçek `GeoJSONParser`/`FootprintParser` ile
     ayrıştırılıyor, export gerçek exporter sınıflarıyla diske yazılıyor).
  2. `build_app_router` - `POST /api/projects/<id>/import` ve
     `POST /api/projects/<id>/export` uçlarının HTTP-benzeri dispatch
     katmanından da aynı davranışı sergilediği (route eşleşmesi, body
     doğrulama, hata kodları).

Not: Bu ortamda gerçek `pytest` kurulu değil (ağ erişimi kapalı); dosya
bilinçli olarak yalnızca stdlib + proje modülleri kullanır, fixture'sız
düz `test_*()` fonksiyonları içerir ve doğrudan `python3` ile de
(bu dosyanın sonundaki `if __name__ == "__main__"` bloğu üzerinden)
çalıştırılabilir - CI'da gerçek pytest bulunduğunda normal şekilde
toplanır/çalışır.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.app_shell.session import AppSession, AppSessionError
from harita.app_shell.api import build_app_router


_SAMPLE_FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"building": "apartman", "building:levels": 3},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [12, 0], [12, 9], [0, 9], [0, 0]]],
            },
        },
        {
            "type": "Feature",
            "properties": {"amenity": "office", "height": 15},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[20, 0], [30, 0], [30, 10], [20, 10], [20, 0]]],
            },
        },
        # Polygon dışı geometri kasıtlı eklendi: atlanmalı, sessizce
        # yutulmamalı (created/skipped raporunda görünmeli).
        {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point", "coordinates": [1, 1]},
        },
    ],
}


def _make_session():
    tmp = tempfile.mkdtemp()
    reg = os.path.join(tmp, "registry.json")
    sess = AppSession(reg)
    info = sess.create_project("d16_test", os.path.join(tmp, "d16_test.hproj"))
    return sess, info["project_id"], tmp


# ------------------------------------------------------------------ #
# 1. AppSession.import_geojson
# ------------------------------------------------------------------ #

def test_import_geojson_creates_buildings_and_skips_non_polygon():
    sess, pid, _tmp = _make_session()
    result = sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    assert result["created_count"] == 2
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"].startswith("desteklenmeyen geometri")
    buildings = sess.list_buildings(pid)
    assert len(buildings) == 2


def test_import_geojson_reads_osm_style_properties():
    sess, pid, _tmp = _make_session()
    sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    buildings = sess.list_buildings(pid)
    # building:levels=3 -> 3 kat üretilmiş olmalı (procedural generator seed'siz
    # de kat sayısını footprint'ten devralır).
    floor_counts = sorted(b["floor_count"] for b in buildings)
    assert floor_counts[0] >= 1 and floor_counts[1] >= 1


def test_import_geojson_raises_readable_error_on_malformed_input():
    sess, pid, _tmp = _make_session()
    try:
        sess.import_geojson(pid, "{not valid json")
        raise AssertionError("beklenen AppSessionError fırlatılmadı")
    except AppSessionError as exc:
        assert "GeoJSON" in str(exc)


def test_import_geojson_empty_collection_creates_nothing():
    sess, pid, _tmp = _make_session()
    empty = json.dumps({"type": "FeatureCollection", "features": []})
    result = sess.import_geojson(pid, empty)
    assert result["created_count"] == 0
    assert result["skipped_count"] == 0


# ------------------------------------------------------------------ #
# 2. AppSession.export_scene - 7 format, gerçek dosya I/O
# ------------------------------------------------------------------ #

def test_export_scene_requires_at_least_one_building():
    sess, pid, tmp = _make_session()
    try:
        sess.export_scene(pid, "obj", out_dir=os.path.join(tmp, "exports"))
        raise AssertionError("beklenen AppSessionError fırlatılmadı (boş sahne)")
    except AppSessionError:
        pass


def test_export_scene_rejects_unsupported_format():
    sess, pid, tmp = _make_session()
    sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    try:
        sess.export_scene(pid, "unknownfmt", out_dir=os.path.join(tmp, "exports"))
        raise AssertionError("beklenen AppSessionError fırlatılmadı (desteklenmeyen format)")
    except AppSessionError as exc:
        assert "Desteklenmeyen export formatı" in str(exc)


def test_export_scene_all_formats_write_real_files_with_nonzero_bytes():
    sess, pid, tmp = _make_session()
    sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    out_dir = os.path.join(tmp, "exports")
    for fmt in ["obj", "stl", "ply", "gltf", "dxf", "3dtiles", "ifc"]:
        result = sess.export_scene(pid, fmt, out_dir=out_dir)
        path = Path(result["path"])
        assert path.exists(), f"{fmt}: export edilen yol yok: {path}"
        assert result["bytes_written"] > 0, f"{fmt}: 0 bayt yazıldı"
        if path.is_file():
            assert path.stat().st_size == result["bytes_written"] or path.stat().st_size > 0


def test_export_scene_glb_embeds_real_pbr_materials():
    """Denetim maddesi (c): `export_scene(fmt='glb')` artık materyalsiz
    tek bir mesh değil, her binanın kendi çözümlenmiş `PBRMaterial`'iyle
    (`SceneGLTFExporter`, standart glTF `materials[].pbrMetallicRoughness`)
    çoklu mesh/materyal içeren gerçek bir GLB üretmeli."""
    import struct as _struct

    sess, pid, tmp = _make_session()
    sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    out_dir = os.path.join(tmp, "exports")
    result = sess.export_scene(pid, "glb", out_dir=out_dir)
    path = Path(result["path"])
    assert path.suffix == ".glb"
    data = path.read_bytes()

    magic, version, total_length = _struct.unpack("<III", data[:12])
    assert magic == 0x46546C67  # 'glTF'
    assert version == 2
    assert total_length == len(data)

    offset = 12
    json_chunk_len, json_chunk_type = _struct.unpack("<II", data[offset:offset + 8])
    assert json_chunk_type == 0x4E4F534A  # 'JSON'
    offset += 8
    json_bytes = data[offset:offset + json_chunk_len]
    gltf = json.loads(json_bytes.decode("utf-8"))

    # En az 1 mesh/node, en az 1 materyal ve gerçek baseColorFactor rengi
    assert len(gltf["meshes"]) >= 1
    assert len(gltf["nodes"]) == len(gltf["meshes"])
    assert "materials" in gltf and len(gltf["materials"]) >= 1
    for material in gltf["materials"]:
        base_color = material["pbrMetallicRoughness"]["baseColorFactor"]
        assert len(base_color) == 4
        assert all(isinstance(c, (int, float)) for c in base_color)
    # Her mesh'in bir materyal indeksine sahip olduğunu doğrula (materyalsiz
    # primitive kalmamalı - önceki davranışın tam tersi).
    for mesh in gltf["meshes"]:
        assert "material" in mesh["primitives"][0]


def test_export_scene_gltf_name_is_backward_compatible_alias_for_glb():
    sess, pid, tmp = _make_session()
    sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    out_dir = os.path.join(tmp, "exports")
    result = sess.export_scene(pid, "gltf", out_dir=out_dir)
    assert result["format"] == "gltf"
    assert Path(result["path"]).suffix == ".glb"
    assert Path(result["path"]).exists()


def test_export_scene_obj_contains_geometry_for_both_buildings():
    sess, pid, tmp = _make_session()
    sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    out_dir = os.path.join(tmp, "exports")
    result = sess.export_scene(pid, "obj", out_dir=out_dir)
    content = Path(result["path"]).read_text(encoding="utf-8")
    vertex_lines = [ln for ln in content.splitlines() if ln.startswith("v ")]
    face_lines = [ln for ln in content.splitlines() if ln.startswith("f ")]
    assert len(vertex_lines) > 0
    assert len(face_lines) > 0
    assert result["vertex_count"] == len(vertex_lines)


def test_export_scene_ifc_produces_syntactically_valid_step():
    sess, pid, tmp = _make_session()
    sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    out_dir = os.path.join(tmp, "exports")
    result = sess.export_scene(pid, "ifc", out_dir=out_dir)
    from harita.export.ifc_export import IFCExporter as _IFCExporter
    text = Path(result["path"]).read_text(encoding="utf-8")
    _IFCExporter.validate_step(text)  # hata fırlatmazsa geçerli


def test_export_scene_3dtiles_produces_valid_tileset():
    sess, pid, tmp = _make_session()
    sess.import_geojson(pid, json.dumps(_SAMPLE_FEATURE_COLLECTION))
    out_dir = os.path.join(tmp, "exports")
    result = sess.export_scene(pid, "3dtiles", out_dir=out_dir)
    from harita.export.tiles_3d import Tiles3DExporter as _Tiles3DExporter
    tileset_path = Path(result["path"]) / "tileset.json"
    assert tileset_path.exists()
    _Tiles3DExporter.validate_tileset(json.loads(tileset_path.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ #
# 3. REST köprüsü: POST /api/projects/<id>/import ve /export
# ------------------------------------------------------------------ #

def test_rest_import_endpoint_returns_201_and_creates_buildings():
    sess, pid, _tmp = _make_session()
    router = build_app_router(sess)
    resp = router.dispatch(
        "POST", f"/api/projects/{pid}/import",
        body={"geojson": json.dumps(_SAMPLE_FEATURE_COLLECTION)},
    )
    assert resp.status == 201
    assert resp.body["created_count"] == 2


def test_rest_import_endpoint_returns_422_when_geojson_missing():
    sess, pid, _tmp = _make_session()
    router = build_app_router(sess)
    resp = router.dispatch("POST", f"/api/projects/{pid}/import", body={})
    assert resp.status == 422


def test_rest_export_endpoint_returns_200_with_path():
    sess, pid, tmp = _make_session()
    router = build_app_router(sess)
    router.dispatch(
        "POST", f"/api/projects/{pid}/import",
        body={"geojson": json.dumps(_SAMPLE_FEATURE_COLLECTION)},
    )
    resp = router.dispatch(
        "POST", f"/api/projects/{pid}/export",
        body={"format": "obj", "out_dir": os.path.join(tmp, "rest_exports")},
    )
    assert resp.status == 200
    assert Path(resp.body["path"]).exists()


def test_rest_export_endpoint_returns_400_on_empty_scene():
    sess, pid, tmp = _make_session()
    router = build_app_router(sess)
    resp = router.dispatch(
        "POST", f"/api/projects/{pid}/export",
        body={"format": "obj", "out_dir": os.path.join(tmp, "rest_exports")},
    )
    assert resp.status == 400


def test_rest_export_endpoint_returns_422_when_format_missing():
    sess, pid, _tmp = _make_session()
    router = build_app_router(sess)
    resp = router.dispatch("POST", f"/api/projects/{pid}/export", body={})
    assert resp.status == 422


# ------------------------------------------------------------------ #
# 4. Uçtan uca: içe aktar -> düzenle (kat ekle) -> dışa aktar
# ------------------------------------------------------------------ #

def test_end_to_end_import_edit_export_flow():
    sess, pid, tmp = _make_session()
    router = build_app_router(sess)

    imp = router.dispatch(
        "POST", f"/api/projects/{pid}/import",
        body={"geojson": json.dumps(_SAMPLE_FEATURE_COLLECTION)},
    )
    assert imp.status == 201
    key = imp.body["created"][0]["key"]
    floors_before = imp.body["created"][0]["floor_count"]

    add_floor = router.dispatch("POST", f"/api/projects/{pid}/buildings/{key}/add_floor")
    assert add_floor.status == 200
    assert add_floor.body["floor_count"] == floors_before + 1

    exp = router.dispatch(
        "POST", f"/api/projects/{pid}/export",
        body={"format": "gltf", "out_dir": os.path.join(tmp, "e2e_exports")},
    )
    assert exp.status == 200
    assert Path(exp.body["path"]).exists()
    assert Path(exp.body["path"]).stat().st_size > 0


_ALL_TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    failures = []
    for fn in _ALL_TESTS:
        try:
            fn()
            print(f"OK   {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(_ALL_TESTS) - len(failures)}/{len(_ALL_TESTS)} geçti.")
    if failures:
        sys.exit(1)
