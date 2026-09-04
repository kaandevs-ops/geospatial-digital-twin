"""Phase 11 - Export testleri."""

from __future__ import annotations

import json
import struct

import pytest
from harita.export import (
    CSVReportExporter,
    DWGExporter,
    DXFExporter,
    FBXExporter,
    FloorPlanSVGExporter,
    GLTFExporter,
    GLTFImporter,
    GLTFParseError,
    JSONReportExporter,
    MarkdownReportExporter,
    OBJExporter,
    PDFExporter,
    PLYExporter,
    ReportBuilder,
    ReportSection,
    STLExporter,
    SVGCanvas,
    UnsupportedFormatError,
    USDExporter,
    XMLReportExporter,
)
from harita.material_engine import PBRMaterial
from harita.mesh_engine import Mesh3D, NormalGenerator, UVGenerator, Vertex3D


def _make_tetrahedron() -> Mesh3D:
    verts = [
        Vertex3D(0.0, 0.0, 0.0),
        Vertex3D(1.0, 0.0, 0.0),
        Vertex3D(0.0, 1.0, 0.0),
        Vertex3D(0.0, 0.0, 1.0),
    ]
    tris = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
    mesh = Mesh3D(vertices=verts, triangles=tris, name="tetra")
    mesh = NormalGenerator.compute_face_averaged_normals(mesh)
    mesh.name = "tetra"
    return mesh


# ------------------------------------------------------------------ #
# OBJ
# ------------------------------------------------------------------ #


def test_obj_export_basic(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.obj"
    result = OBJExporter.export(mesh, str(path))
    assert path.exists()
    content = path.read_text()
    assert content.count("\nv ") + content.startswith("v ") >= 0
    assert "v 0.000000 0.000000 0.000000" in content
    assert "f " in content
    assert result.vertex_count == 4
    assert result.triangle_count == 4


def test_obj_export_with_material_writes_mtl(tmp_path):
    mesh = _make_tetrahedron()
    mat = PBRMaterial(name="concrete", albedo=(0.5, 0.5, 0.5), metallic=0.0, roughness=0.8)
    path = tmp_path / "tetra_mat.obj"
    OBJExporter.export(mesh, str(path), material=mat)
    mtl_path = tmp_path / "tetra_mat.mtl"
    assert mtl_path.exists()
    mtl_content = mtl_path.read_text()
    assert "newmtl concrete" in mtl_content
    obj_content = path.read_text()
    assert "mtllib tetra_mat.mtl" in obj_content
    assert "usemtl concrete" in obj_content


def test_obj_export_with_normals_and_uvs(tmp_path):
    mesh = _make_tetrahedron()
    mesh = UVGenerator.planar_mapping(mesh, axis="z")
    path = tmp_path / "tetra_full.obj"
    OBJExporter.export(mesh, str(path))
    content = path.read_text()
    assert "vn " in content
    assert "vt " in content
    # face tokens should have vertex/uv/normal form: e.g. "1/1/1"
    face_line = [l for l in content.splitlines() if l.startswith("f ")][0]
    assert "/" in face_line.split()[1]


# ------------------------------------------------------------------ #
# STL
# ------------------------------------------------------------------ #


def test_stl_ascii_export(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.stl"
    result = STLExporter.export(mesh, str(path), binary=False)
    content = path.read_text()
    assert content.startswith("solid tetra")
    assert content.strip().endswith("endsolid tetra")
    assert content.count("facet normal") == 4
    assert result.format == "stl-ascii"


def test_stl_binary_export_roundtrip_header(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra_bin.stl"
    result = STLExporter.export(mesh, str(path), binary=True)
    data = path.read_bytes()
    assert len(data) == 80 + 4 + 4 * (12 * 4 + 2)
    tri_count = struct.unpack_from("<I", data, 80)[0]
    assert tri_count == 4
    assert result.format == "stl-binary"


def test_stl_binary_vs_ascii_same_triangle_count(tmp_path):
    mesh = _make_tetrahedron()
    ascii_res = STLExporter.export(mesh, str(tmp_path / "a.stl"), binary=False)
    bin_res = STLExporter.export(mesh, str(tmp_path / "b.stl"), binary=True)
    assert ascii_res.triangle_count == bin_res.triangle_count == 4


# ------------------------------------------------------------------ #
# PLY
# ------------------------------------------------------------------ #


def test_ply_export_ascii_structure(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.ply"
    PLYExporter.export(mesh, str(path))
    content = path.read_text()
    assert content.startswith("ply\n")
    assert "format ascii 1.0" in content
    assert "element vertex 4" in content
    assert "element face 4" in content
    assert "end_header" in content
    assert content.count("3 ") >= 4  # face lines "3 i j k"


def test_ply_with_normals_adds_properties(tmp_path):
    mesh = _make_tetrahedron()  # normals already generated
    path = tmp_path / "tetra_n.ply"
    PLYExporter.export(mesh, str(path))
    content = path.read_text()
    assert "property float nx" in content


# ------------------------------------------------------------------ #
# GLTF / GLB
# ------------------------------------------------------------------ #


def test_gltf_export_valid_json_and_bin(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.gltf"
    result = GLTFExporter.export_gltf(mesh, str(path))
    gltf = json.loads(path.read_text())
    assert gltf["asset"]["version"] == "2.0"
    assert gltf["meshes"][0]["primitives"][0]["attributes"]["POSITION"] == 0
    bin_path = tmp_path / "tetra.bin"
    assert bin_path.exists()
    assert result.vertex_count == 4
    assert result.triangle_count == 4


def test_glb_export_header_magic(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.glb"
    GLTFExporter.export_glb(mesh, str(path))
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67  # 'glTF'
    assert version == 2
    assert length == len(data)


def test_glb_json_chunk_parses(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra2.glb"
    GLTFExporter.export_glb(mesh, str(path))
    data = path.read_bytes()
    json_len, json_type = struct.unpack_from("<II", data, 12)
    assert json_type == 0x4E4F534A  # 'JSON'
    json_bytes = data[20 : 20 + json_len]
    gltf = json.loads(json_bytes.decode("utf-8"))
    assert "meshes" in gltf


# ------------------------------------------------------------------ #
# GLTF / GLB — Importer (A1: FBX/GLB okuyucu, GLB kısmı)
# ------------------------------------------------------------------ #


def _mesh_with_normals_uvs() -> Mesh3D:
    mesh = _make_tetrahedron()
    mesh = NormalGenerator.compute_face_averaged_normals(mesh)
    mesh = UVGenerator.planar_mapping(mesh)
    return mesh


def test_glb_roundtrip_positions_and_triangles(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.glb"
    GLTFExporter.export_glb(mesh, str(path))
    loaded = GLTFImporter.import_glb(str(path))
    assert loaded.vertex_count() == mesh.vertex_count()
    assert loaded.triangle_count() == mesh.triangle_count()
    for a, b in zip(loaded.vertices, mesh.vertices):
        assert a.x == pytest.approx(b.x)
        assert a.y == pytest.approx(b.y)
        assert a.z == pytest.approx(b.z)
    assert loaded.triangles == mesh.triangles


def test_glb_roundtrip_with_normals_and_uvs(tmp_path):
    mesh = _mesh_with_normals_uvs()
    path = tmp_path / "tetra_nrm_uv.glb"
    GLTFExporter.export_glb(mesh, str(path))
    loaded = GLTFImporter.import_glb(str(path))
    assert loaded.vertices[0].normal is not None
    assert loaded.vertices[0].uv is not None
    for a, b in zip(loaded.vertices, mesh.vertices):
        for i in range(3):
            assert a.normal[i] == pytest.approx(b.normal[i], abs=1e-5)


def test_gltf_roundtrip_external_bin(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.gltf"
    GLTFExporter.export_gltf(mesh, str(path))
    loaded = GLTFImporter.import_gltf(str(path))
    assert loaded.vertex_count() == mesh.vertex_count()
    assert loaded.triangle_count() == mesh.triangle_count()


def test_glb_large_mesh_uint32_indices_roundtrip(tmp_path):
    # 65535 sınırını aşan indeks -> uint32 path'i test eder
    verts = [Vertex3D(float(i), 0.0, 0.0) for i in range(70000)]
    mesh = Mesh3D(vertices=verts, triangles=[(0, 1, 2)], name="big")
    path = tmp_path / "big.glb"
    GLTFExporter.export_glb(mesh, str(path))
    loaded = GLTFImporter.import_glb(str(path))
    assert loaded.vertex_count() == 70000
    assert loaded.triangles == [(0, 1, 2)]


def test_glb_invalid_magic_raises(tmp_path):
    path = tmp_path / "bad.glb"
    path.write_bytes(b"NOTG" + b"\x00" * 20)
    with pytest.raises(GLTFParseError):
        GLTFImporter.import_glb(str(path))


def test_glb_truncated_file_raises(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "trunc.glb"
    GLTFExporter.export_glb(mesh, str(path))
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])
    with pytest.raises(GLTFParseError):
        GLTFImporter.import_glb(str(path))


def test_glb_missing_position_raises(tmp_path):
    import struct as _struct

    gltf = {
        "asset": {"version": "2.0"},
        "meshes": [{"primitives": [{"attributes": {}}]}],
        "buffers": [{"byteLength": 0}],
        "bufferViews": [],
        "accessors": [],
    }
    json_bytes = json.dumps(gltf).encode("utf-8")
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "
    json_chunk = _struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk = _struct.pack("<II", 0, 0x004E4942)
    total = 12 + len(json_chunk) + len(bin_chunk)
    header = _struct.pack("<III", 0x46546C67, 2, total)
    path = tmp_path / "nopos.glb"
    path.write_bytes(header + json_chunk + bin_chunk)
    with pytest.raises(GLTFParseError):
        GLTFImporter.import_glb(str(path))


def test_gltf_missing_bin_file_raises(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "orphan.gltf"
    GLTFExporter.export_gltf(mesh, str(path))
    (tmp_path / "orphan.bin").unlink()
    with pytest.raises(GLTFParseError):
        GLTFImporter.import_gltf(str(path))


# ------------------------------------------------------------------ #
# DXF / DWG / FBX / USD
# ------------------------------------------------------------------ #


def test_dxf_export_entity_count(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.dxf"
    DXFExporter.export(mesh, str(path))
    content = path.read_text()
    assert content.count("3DFACE") == 4
    assert content.strip().endswith("EOF")


def test_dwg_export_raises_unsupported(tmp_path):
    mesh = _make_tetrahedron()
    with pytest.raises(UnsupportedFormatError):
        DWGExporter.export(mesh, str(tmp_path / "x.dwg"))


def test_fbx_export_raises_unsupported(tmp_path):
    mesh = _make_tetrahedron()
    with pytest.raises(UnsupportedFormatError):
        FBXExporter.export(mesh, str(tmp_path / "x.fbx"))


def test_usd_export_raises_unsupported_binary(tmp_path):
    mesh = _make_tetrahedron()
    with pytest.raises(UnsupportedFormatError):
        USDExporter.export(mesh, str(tmp_path / "x.usd"))


def test_usda_ascii_export_works(tmp_path):
    mesh = _make_tetrahedron()
    path = tmp_path / "tetra.usda"
    result = USDExporter.export_usda(mesh, str(path))
    content = path.read_text()
    assert content.startswith("#usda 1.0")
    assert "faceVertexCounts" in content
    assert result.format == "usda"


# ------------------------------------------------------------------ #
# SVG / Floor Plan
# ------------------------------------------------------------------ #


def test_svg_canvas_basic_shapes(tmp_path):
    canvas = SVGCanvas(width=200, height=100)
    canvas.line(0, 0, 100, 100)
    canvas.polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
    canvas.circle(50, 50, 5)
    canvas.text(10, 10, "test")
    svg_str = canvas.to_string()
    assert svg_str.startswith("<?xml")
    assert "<svg" in svg_str
    assert "<line" in svg_str
    assert "<polygon" in svg_str
    assert "<circle" in svg_str
    assert "<text" in svg_str


def test_svg_canvas_save(tmp_path):
    canvas = SVGCanvas(width=50, height=50)
    canvas.line(0, 0, 50, 50)
    path = tmp_path / "canvas.svg"
    result = canvas.save(str(path))
    assert path.exists()
    assert result.format == "svg"


class _FakeRoom:
    def __init__(self, name, polygon):
        self.name = name
        self.polygon = polygon


def test_floorplan_svg_exporter(tmp_path):
    rooms = [
        _FakeRoom("Salon", [(0, 0), (5, 0), (5, 4), (0, 4)]),
        _FakeRoom("WC", [(5, 0), (7, 0), (7, 2), (5, 2)]),
    ]
    path = tmp_path / "floor.svg"
    result = FloorPlanSVGExporter.export(rooms, str(path))
    content = path.read_text()
    assert "Salon" in content
    assert "WC" in content
    assert result.format == "svg"


# ------------------------------------------------------------------ #
# PDF (minimal, bağımlılıksız yol garanti edilmeli)
# ------------------------------------------------------------------ #


def test_pdf_minimal_export_produces_valid_header(tmp_path):
    path = tmp_path / "report.pdf"
    result = PDFExporter.export_text_report("Test Raporu", ["satır 1", "satır 2"], str(path))
    data = path.read_bytes()
    assert data.startswith(b"%PDF-1.4") or data.startswith(b"%PDF")
    assert b"%%EOF" in data
    assert result.format in ("pdf", "pdf-minimal")


# ------------------------------------------------------------------ #
# Reports: JSON / CSV / XML / Markdown / ReportBuilder
# ------------------------------------------------------------------ #


def test_json_report_export(tmp_path):
    data = {"building": "A1", "floors": 5, "rooms": ["salon", "mutfak"]}
    path = tmp_path / "r.json"
    JSONReportExporter.export(data, str(path))
    loaded = json.loads(path.read_text())
    assert loaded == data


def test_csv_report_export_columns(tmp_path):
    rows = [
        {"name": "Salon", "area": 25.0},
        {"name": "WC", "area": 4.0},
    ]
    path = tmp_path / "r.csv"
    CSVReportExporter.export(rows, str(path))
    content = path.read_text()
    lines = content.strip().splitlines()
    assert lines[0] == "name,area"
    assert "Salon" in lines[1]


def test_xml_report_export_nested(tmp_path):
    data = {"building": {"name": "A1", "rooms": ["salon", "wc"]}}
    path = tmp_path / "r.xml"
    XMLReportExporter.export(data, str(path))
    content = path.read_text()
    assert content.startswith('<?xml version="1.0"')
    assert "<building>" in content
    assert "<item>salon</item>" in content


def test_markdown_report_export(tmp_path):
    sections = [
        ReportSection(
            heading="Özet",
            summary={"Bina": "A1", "Kat Sayısı": 5},
            table_headers=["Oda", "Alan"],
            table_rows=[{"Oda": "Salon", "Alan": 25.0}, {"Oda": "WC", "Alan": 4.0}],
            notes="Bu otomatik üretilmiş bir rapordur.",
        )
    ]
    path = tmp_path / "r.md"
    MarkdownReportExporter.export("Bina Raporu", sections, str(path))
    content = path.read_text()
    assert content.startswith("# Bina Raporu")
    assert "## Özet" in content
    assert "| Oda | Alan |" in content
    assert "Salon" in content


def test_report_builder_all_formats(tmp_path):
    sections = [
        ReportSection(
            heading="Genel",
            summary={"id": "twin-1"},
            table_rows=[{"metric": "height", "value": 12.5}],
        )
    ]
    builder = ReportBuilder("Digital Twin Raporu", sections)

    json_res = builder.export_json(str(tmp_path / "b.json"))
    xml_res = builder.export_xml(str(tmp_path / "b.xml"))
    md_res = builder.export_markdown(str(tmp_path / "b.md"))
    csv_res = builder.export_csv(str(tmp_path / "b.csv"))
    pdf_res = builder.export_pdf(str(tmp_path / "b.pdf"))

    assert (tmp_path / "b.json").exists()
    assert (tmp_path / "b.xml").exists()
    assert (tmp_path / "b.md").exists()
    assert (tmp_path / "b.csv").exists()
    assert (tmp_path / "b.pdf").exists()
    assert json_res.format == "json"
    assert xml_res.format == "xml"
    assert md_res.format == "markdown"
    assert csv_res.format == "csv"
    assert pdf_res.format in ("pdf", "pdf-minimal")


def test_report_builder_csv_without_table_raises(tmp_path):
    sections = [ReportSection(heading="Boş")]
    builder = ReportBuilder("Rapor", sections)
    with pytest.raises(ValueError):
        builder.export_csv(str(tmp_path / "x.csv"))


# ------------------------------------------------------------------ #
# Top-level harita re-export sanity
# ------------------------------------------------------------------ #


def test_top_level_reexports_available():
    import harita

    assert hasattr(harita, "OBJExporter")
    assert hasattr(harita, "GLTFExporter")
    assert hasattr(harita, "ReportBuilder")
    assert hasattr(harita, "UnsupportedFormatError")
