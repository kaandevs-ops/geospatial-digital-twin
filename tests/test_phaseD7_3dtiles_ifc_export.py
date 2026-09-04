"""Roadmap V3 - Faz D7 kabul kriteri testleri.

Kapsam:
  1. `Tiles3DExporter` - bir `Scene`'den üretilen `tileset.json` kendi
     şema doğrulamasından (`validate_tileset`) hatasız geçer; her
     `.b3dm` tile'ı geçerli bir 28-baytlık header (`b3dm` magic,
     byteLength=dosya boyutu) + geçerli bir GLB gövdesi taşır (round-trip:
     `GLTFImporter.import_glb` ile geometri geri okunabilir).
  2. `IFCExporter` - minimal IFC4 STEP çıktısı kendi söz dizimi
     doğrulamasından (`validate_step`: header/footer, dengeli `#id`
     referansları) hatasız geçer; oda/duvar sayısı ve varlık hiyerarşisi
     (`IFCPROJECT` -> `IFCSITE` -> `IFCBUILDING` -> `IFCBUILDINGSTOREY` ->
     `IFCSPACE`/`IFCWALLSTANDARDCASE`) çıktıda doğrulanır.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.export.ifc_export import (
    IFCBuildingModel,
    IFCExporter,
    IFCRoom,
    IFCValidationError,
    IFCWall,
)
from harita.export.tiles_3d import (
    BoundingBox3DTiles,
    Tiles3DExporter,
    TilesetValidationError,
    read_b3dm_header,
)
from harita.mesh_engine import Mesh3D, Vertex3D
from harita.render_engine.scene_bridge import Scene


def _cube_mesh(name: str = "cube") -> Mesh3D:
    m = Mesh3D(name=name)
    m.vertices = [
        Vertex3D(0, 0, 0),
        Vertex3D(1, 0, 0),
        Vertex3D(1, 1, 0),
        Vertex3D(0, 1, 0),
        Vertex3D(0, 0, 1),
        Vertex3D(1, 0, 1),
        Vertex3D(1, 1, 1),
        Vertex3D(0, 1, 1),
    ]
    m.triangles = [
        (0, 1, 2),
        (0, 2, 3),  # taban
        (4, 5, 6),
        (4, 6, 7),  # tavan
    ]
    return m


# ============================================================================ #
# BoundingBox3DTiles
# ============================================================================ #


def test_bounding_box_from_aabb_has_correct_center_and_half_extent():
    box = BoundingBox3DTiles.from_aabb((0.0, 0.0, 0.0), (10.0, 4.0, 6.0))
    assert box.center == (5.0, 2.0, 3.0)
    assert box.half_x == (5.0, 0.0, 0.0)
    assert box.half_y == (0.0, 2.0, 0.0)
    assert box.half_z == (0.0, 0.0, 3.0)
    assert len(box.to_array()) == 12


def test_bounding_box_union_covers_both_boxes():
    a = BoundingBox3DTiles.from_aabb((0.0, 0.0, 0.0), (2.0, 2.0, 2.0))
    b = BoundingBox3DTiles.from_aabb((10.0, 10.0, 0.0), (12.0, 12.0, 2.0))
    u = a.union(b)
    # union kutusu her iki kutunun köşelerini de kapsamalı
    cx, cy, cz = u.center
    hx, hy, hz = u.half_x[0], u.half_y[1], u.half_z[2]
    assert cx - hx <= 0.0 and cx + hx >= 12.0
    assert cy - hy <= 0.0 and cy + hy >= 12.0


# ============================================================================ #
# Tiles3DExporter
# ============================================================================ #


def test_tileset_export_produces_valid_schema_and_files(tmp_path):
    scene = Scene()
    scene.add_mesh(_cube_mesh(), translation=(0.0, 0.0, 0.0))
    scene.add_mesh(_cube_mesh(), translation=(20.0, 0.0, 0.0))
    scene.add_mesh(_cube_mesh(), translation=(40.0, 10.0, 0.0))

    out_dir = tmp_path / "tileset"
    result = Tiles3DExporter.export(scene, str(out_dir))

    assert result.format == "3dtiles"
    assert (out_dir / "tileset.json").exists()
    assert result.vertex_count == 24  # 3 x 8 vertex
    assert result.triangle_count == 12  # 3 x 4 triangle

    import json

    tileset = json.loads((out_dir / "tileset.json").read_text(encoding="utf-8"))
    Tiles3DExporter.validate_tileset(tileset)  # hatasız geçmeli

    assert tileset["asset"]["version"] == "1.0"
    assert len(tileset["root"]["children"]) == 3
    for child in tileset["root"]["children"]:
        uri = child["content"]["uri"]
        assert (out_dir / uri).exists()


def test_tileset_root_bounding_volume_covers_all_children(tmp_path):
    scene = Scene()
    scene.add_mesh(_cube_mesh(), translation=(-50.0, -50.0, 0.0))
    scene.add_mesh(_cube_mesh(), translation=(50.0, 50.0, 0.0))
    out_dir = tmp_path / "tileset2"
    Tiles3DExporter.export(scene, str(out_dir))

    import json

    tileset = json.loads((out_dir / "tileset.json").read_text(encoding="utf-8"))
    root_box = tileset["root"]["boundingVolume"]["box"]
    cx, cy, cz = root_box[0], root_box[1], root_box[2]
    hx, hy = root_box[3], root_box[7]
    assert cx - hx <= -50.0 and cx + hx >= 51.0
    assert cy - hy <= -50.0 and cy + hy >= 51.0


def test_b3dm_header_is_well_formed_and_byte_length_matches_file(tmp_path):
    scene = Scene()
    scene.add_mesh(_cube_mesh(), translation=(0.0, 0.0, 0.0))
    out_dir = tmp_path / "tileset3"
    Tiles3DExporter.export(scene, str(out_dir))

    b3dm_files = list(out_dir.glob("*.b3dm"))
    assert len(b3dm_files) == 1
    header = read_b3dm_header(str(b3dm_files[0]))
    assert header["version"] == 1
    assert header["byte_length"] == b3dm_files[0].stat().st_size
    assert header["feature_table_json_length"] > 0
    assert header["glb_offset"] < header["byte_length"]


def test_b3dm_embedded_glb_round_trips_through_gltf_importer(tmp_path):
    """b3dm gövdesindeki glb, GLTFImporter ile geçerli bir mesh olarak
    geri okunabilmeli (glb konteynerinin doğruluğunun kanıtı)."""
    from harita.export.geometry_3d import GLTFImporter

    mesh = _cube_mesh("roundtrip")
    scene = Scene()
    scene.add_mesh(mesh, translation=(0.0, 0.0, 0.0))
    out_dir = tmp_path / "tileset4"
    Tiles3DExporter.export(scene, str(out_dir))

    b3dm_path = next(out_dir.glob("*.b3dm"))
    data = b3dm_path.read_bytes()
    header = read_b3dm_header(str(b3dm_path))
    glb_bytes = data[header["glb_offset"] :]

    extracted_glb_path = tmp_path / "extracted.glb"
    extracted_glb_path.write_bytes(glb_bytes)

    imported = GLTFImporter.import_glb(str(extracted_glb_path))
    assert imported.vertex_count() == mesh.vertex_count()
    assert imported.triangle_count() == mesh.triangle_count()


def test_tileset_export_raises_on_empty_scene(tmp_path):
    scene = Scene()
    try:
        Tiles3DExporter.export(scene, str(tmp_path / "empty"))
        assert False, "boş sahne için ValueError bekleniyordu"
    except ValueError:
        pass


def test_validate_tileset_rejects_malformed_schema():
    bad = {"asset": {"version": "0.0"}, "geometricError": 1.0, "root": {}}
    try:
        Tiles3DExporter.validate_tileset(bad)
        assert False, "TilesetValidationError bekleniyordu"
    except TilesetValidationError:
        pass


def test_validate_tileset_rejects_missing_bounding_box():
    bad = {
        "asset": {"version": "1.0"},
        "geometricError": 1.0,
        "root": {"geometricError": 1.0, "refine": "REPLACE"},
    }
    try:
        Tiles3DExporter.validate_tileset(bad)
        assert False, "TilesetValidationError bekleniyordu (boundingVolume yok)"
    except TilesetValidationError:
        pass


def test_read_b3dm_header_rejects_bad_magic(tmp_path):
    bad_path = tmp_path / "bad.b3dm"
    bad_path.write_bytes(struct.pack("<4sIIIIII", b"XXXX", 1, 28, 0, 0, 0, 0))
    try:
        read_b3dm_header(str(bad_path))
        assert False, "TilesetValidationError bekleniyordu (geçersiz magic)"
    except TilesetValidationError:
        pass


# ============================================================================ #
# IFCExporter
# ============================================================================ #


def _sample_model() -> IFCBuildingModel:
    room = IFCRoom(
        name="Salon",
        polygon=Polygon(
            points=[
                Point2D(0.0, 0.0),
                Point2D(6.0, 0.0),
                Point2D(6.0, 4.0),
                Point2D(0.0, 4.0),
            ]
        ),
        floor_z=0.0,
        height=3.0,
    )
    wall_a = IFCWall(
        name="Duvar-Kuzey", start=Point2D(0.0, 0.0), end=Point2D(6.0, 0.0), floor_z=0.0, height=3.0
    )
    wall_b = IFCWall(
        name="Duvar-Doğu", start=Point2D(6.0, 0.0), end=Point2D(6.0, 4.0), floor_z=0.0, height=3.0
    )
    return IFCBuildingModel(name="Örnek Bina", rooms=[room], walls=[wall_a, wall_b])


def test_ifc_export_produces_syntactically_valid_step_file(tmp_path):
    model = _sample_model()
    out_path = tmp_path / "building.ifc"
    result = IFCExporter.export(model, str(out_path))

    assert out_path.exists()
    assert result.room_count == 1
    assert result.wall_count == 2
    assert result.entity_count > 0

    content = out_path.read_text(encoding="utf-8")
    IFCExporter.validate_step(content)  # hatasız geçmeli
    assert content.startswith("ISO-10303-21;")
    assert content.rstrip().endswith("END-ISO-10303-21;")


def test_ifc_export_contains_expected_entity_hierarchy(tmp_path):
    model = _sample_model()
    out_path = tmp_path / "hierarchy.ifc"
    IFCExporter.export(model, str(out_path))
    content = out_path.read_text(encoding="utf-8")

    for entity in (
        "IFCPROJECT",
        "IFCSITE",
        "IFCBUILDING",
        "IFCBUILDINGSTOREY",
        "IFCSPACE",
        "IFCWALLSTANDARDCASE",
        "IFCRELAGGREGATES",
        "IFCRELCONTAINEDINSPATIALSTRUCTURE",
        "IFCEXTRUDEDAREASOLID",
    ):
        assert entity in content, f"{entity} çıktıda bulunamadı"

    assert content.count("IFCSPACE(") == 1
    assert content.count("IFCWALLSTANDARDCASE(") == 2


def test_ifc_export_guids_are_unique_and_correct_length():

    model = _sample_model()
    from harita.export.ifc_export import _ifc_guid

    guids = {_ifc_guid() for _ in range(200)}
    assert len(guids) == 200  # çakışma yok
    for g in guids:
        assert len(g) == 22


def test_validate_step_rejects_dangling_reference():
    broken = (
        "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"
        "#1=IFCCARTESIANPOINT((0.0,0.0,0.0));\n"
        "#2=IFCAXIS2PLACEMENT3D(#1,#99,$);\n"  # #99 tanımsız
        "ENDSEC;\nEND-ISO-10303-21;\n"
    )
    try:
        IFCExporter.validate_step(broken)
        assert False, "IFCValidationError bekleniyordu (dangling reference)"
    except IFCValidationError:
        pass


def test_validate_step_rejects_missing_footer():
    broken = "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\n"
    try:
        IFCExporter.validate_step(broken)
        assert False, "IFCValidationError bekleniyordu (footer eksik)"
    except IFCValidationError:
        pass


def test_ifc_export_room_polygon_vertices_present_in_output(tmp_path):
    model = _sample_model()
    out_path = tmp_path / "room_poly.ifc"
    IFCExporter.export(model, str(out_path))
    content = out_path.read_text(encoding="utf-8")
    assert "6.0000,4.0000" in content or "6.0000,0.0000" in content
