"""
Export — Geometry 3D
=====================

Roadmap Phase 11 - "3D" export bölümü:
    OBJ, FBX, GLTF, GLB, STL, PLY, USD, USDZ

Girdi: Phase 2 `Mesh3D` (mesh_engine) + opsiyonel Phase 2 `PBRMaterial`
(material_engine). Bağımlılık: yalnızca stdlib.

Bağımlılıksız yazılabilen formatlar tam olarak uygulanır: OBJ (+MTL), STL
(ASCII ve binary), PLY (ASCII), GLTF (JSON) + GLB (binary konteyner), DXF
(ASCII, 3DFACE varlıklarıyla basit mesh temsili).

FBX / USD / USDZ kapalı veya karmaşık ikili/şema tabanlı formatlardır ve
resmi SDK'lar (Autodesk FBX SDK, Pixar USD) olmadan güvenilir biçimde
yazılamaz. Bu modül onlar için `UnsupportedFormatError` fırlatan açık,
dürüst bir arayüz sağlar — sessizce bozuk dosya üretmek yerine.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..mesh_engine import Mesh3D, Vertex3D

try:
    from ..material_engine import PBRMaterial
except Exception:  # pragma: no cover - material_engine her zaman mevcut
    PBRMaterial = None  # type: ignore


class UnsupportedFormatError(NotImplementedError):
    """Resmi SDK gerektiren (FBX/USD/USDZ/DWG gibi) formatlar için fırlatılır."""


@dataclass(slots=True)
class ExportResult:
    """Bir export işleminin sonucu."""

    path: str
    format: str
    bytes_written: int
    vertex_count: int
    triangle_count: int


# ======================================================================== #
# OBJ (+ MTL)
# ======================================================================== #

class OBJExporter:
    """Wavefront OBJ exporter. Normal/UV varsa yazar, materyal varsa .mtl üretir."""

    @staticmethod
    def export(mesh: Mesh3D, path: str,
               material: Optional["PBRMaterial"] = None) -> ExportResult:
        path_obj = Path(path)
        has_normals = all(v.normal is not None for v in mesh.vertices) and len(mesh.vertices) > 0
        has_uvs = all(v.uv is not None for v in mesh.vertices) and len(mesh.vertices) > 0

        lines: list[str] = [f"# Exported by harita.export.OBJExporter", f"o {mesh.name}"]

        mtl_name = None
        if material is not None:
            mtl_name = path_obj.stem + ".mtl"
            lines.append(f"mtllib {mtl_name}")
            lines.append(f"usemtl {getattr(material, 'name', 'material')}")

        for v in mesh.vertices:
            lines.append(f"v {v.x:.6f} {v.y:.6f} {v.z:.6f}")
        if has_uvs:
            for v in mesh.vertices:
                lines.append(f"vt {v.uv[0]:.6f} {v.uv[1]:.6f}")
        if has_normals:
            for v in mesh.vertices:
                lines.append(f"vn {v.normal[0]:.6f} {v.normal[1]:.6f} {v.normal[2]:.6f}")

        for (i, j, k) in mesh.triangles:
            def _tok(idx: int) -> str:
                obj_idx = idx + 1  # OBJ 1-indexlidir
                if has_uvs and has_normals:
                    return f"{obj_idx}/{obj_idx}/{obj_idx}"
                if has_uvs:
                    return f"{obj_idx}/{obj_idx}"
                if has_normals:
                    return f"{obj_idx}//{obj_idx}"
                return f"{obj_idx}"
            lines.append(f"f {_tok(i)} {_tok(j)} {_tok(k)}")

        content = "\n".join(lines) + "\n"
        path_obj.write_text(content, encoding="utf-8")

        if material is not None and mtl_name is not None:
            OBJExporter._write_mtl(path_obj.parent / mtl_name, material)

        return ExportResult(str(path_obj), "obj", len(content.encode("utf-8")),
                             mesh.vertex_count(), mesh.triangle_count())

    @staticmethod
    def _write_mtl(path: Path, material: "PBRMaterial") -> None:
        albedo = getattr(material, "albedo", (0.8, 0.8, 0.8))
        metallic = getattr(material, "metallic", 0.0)
        roughness = getattr(material, "roughness", 0.5)
        name = getattr(material, "name", "material")
        lines = [
            f"newmtl {name}",
            f"Kd {albedo[0]:.4f} {albedo[1]:.4f} {albedo[2]:.4f}",
            "Ka 0.0 0.0 0.0",
            f"Ks {metallic:.4f} {metallic:.4f} {metallic:.4f}",
            f"Ns {max(1.0, (1.0 - roughness) * 1000.0):.2f}",
            "d 1.0",
            "illum 2",
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ======================================================================== #
# STL (ASCII ve Binary)
# ======================================================================== #

class STLExporter:
    """STL exporter. `binary=True` (varsayılan) ile 80-byte header + binary
    üçgen listesi; `binary=False` ile insan-okunur ASCII `solid` formatı."""

    @staticmethod
    def export(mesh: Mesh3D, path: str, binary: bool = True) -> ExportResult:
        if binary:
            return STLExporter._export_binary(mesh, path)
        return STLExporter._export_ascii(mesh, path)

    @staticmethod
    def _face_normal(a: Vertex3D, b: Vertex3D, c: Vertex3D) -> tuple[float, float, float]:
        ux, uy, uz = b.x - a.x, b.y - a.y, b.z - a.z
        vx, vy, vz = c.x - a.x, c.y - a.y, c.z - a.z
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = (nx * nx + ny * ny + nz * nz) ** 0.5
        if length < 1e-12:
            return (0.0, 0.0, 0.0)
        return (nx / length, ny / length, nz / length)

    @staticmethod
    def _export_ascii(mesh: Mesh3D, path: str) -> ExportResult:
        lines = [f"solid {mesh.name}"]
        for (i, j, k) in mesh.triangles:
            a, b, c = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]
            nx, ny, nz = STLExporter._face_normal(a, b, c)
            lines.append(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}")
            lines.append("    outer loop")
            for v in (a, b, c):
                lines.append(f"      vertex {v.x:.6e} {v.y:.6e} {v.z:.6e}")
            lines.append("    endloop")
            lines.append("  endfacet")
        lines.append(f"endsolid {mesh.name}")
        content = "\n".join(lines) + "\n"
        Path(path).write_text(content, encoding="utf-8")
        return ExportResult(path, "stl-ascii", len(content.encode("utf-8")),
                             mesh.vertex_count(), mesh.triangle_count())

    @staticmethod
    def _export_binary(mesh: Mesh3D, path: str) -> ExportResult:
        header = (f"Exported by harita.export.STLExporter: {mesh.name}").encode("utf-8")
        header = header[:80].ljust(80, b"\x00")
        tri_count = mesh.triangle_count()
        buf = bytearray()
        buf += header
        buf += struct.pack("<I", tri_count)
        for (i, j, k) in mesh.triangles:
            a, b, c = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]
            nx, ny, nz = STLExporter._face_normal(a, b, c)
            buf += struct.pack("<3f", nx, ny, nz)
            buf += struct.pack("<3f", a.x, a.y, a.z)
            buf += struct.pack("<3f", b.x, b.y, b.z)
            buf += struct.pack("<3f", c.x, c.y, c.z)
            buf += struct.pack("<H", 0)  # attribute byte count
        Path(path).write_bytes(bytes(buf))
        return ExportResult(path, "stl-binary", len(buf),
                             mesh.vertex_count(), mesh.triangle_count())


# ======================================================================== #
# PLY (ASCII)
# ======================================================================== #

class PLYExporter:
    """Stanford PLY (ASCII) exporter. Normal/UV varsa vertex özelliği olarak yazar."""

    @staticmethod
    def export(mesh: Mesh3D, path: str) -> ExportResult:
        has_normals = all(v.normal is not None for v in mesh.vertices) and len(mesh.vertices) > 0
        has_uvs = all(v.uv is not None for v in mesh.vertices) and len(mesh.vertices) > 0

        header = [
            "ply",
            "format ascii 1.0",
            "comment Exported by harita.export.PLYExporter",
            f"element vertex {mesh.vertex_count()}",
            "property float x",
            "property float y",
            "property float z",
        ]
        if has_normals:
            header += ["property float nx", "property float ny", "property float nz"]
        if has_uvs:
            header += ["property float u", "property float v"]
        header += [
            f"element face {mesh.triangle_count()}",
            "property list uchar int vertex_indices",
            "end_header",
        ]

        body: list[str] = []
        for v in mesh.vertices:
            row = [f"{v.x:.6f}", f"{v.y:.6f}", f"{v.z:.6f}"]
            if has_normals:
                row += [f"{v.normal[0]:.6f}", f"{v.normal[1]:.6f}", f"{v.normal[2]:.6f}"]
            if has_uvs:
                row += [f"{v.uv[0]:.6f}", f"{v.uv[1]:.6f}"]
            body.append(" ".join(row))
        for (i, j, k) in mesh.triangles:
            body.append(f"3 {i} {j} {k}")

        content = "\n".join(header + body) + "\n"
        Path(path).write_text(content, encoding="utf-8")
        return ExportResult(path, "ply", len(content.encode("utf-8")),
                             mesh.vertex_count(), mesh.triangle_count())


# ======================================================================== #
# GLTF / GLB
# ======================================================================== #

class GLTFExporter:
    """glTF 2.0 exporter. `export_gltf` ayrık JSON+.bin çifti,
    `export_glb` tek dosyalık binary konteyner üretir. Yalnızca POSITION
    (+ opsiyonel NORMAL/TEXCOORD_0) attribute'ları ve indexed triangle
    listesi desteklenir (roadmap kapsamı için yeterli minimal glTF)."""

    @staticmethod
    def _build_buffers(mesh: Mesh3D) -> tuple[bytes, dict]:
        import json as _json

        pos_bytes = bytearray()
        for v in mesh.vertices:
            pos_bytes += struct.pack("<3f", v.x, v.y, v.z)

        has_normals = all(v.normal is not None for v in mesh.vertices) and len(mesh.vertices) > 0
        has_uvs = all(v.uv is not None for v in mesh.vertices) and len(mesh.vertices) > 0

        nrm_bytes = bytearray()
        if has_normals:
            for v in mesh.vertices:
                nrm_bytes += struct.pack("<3f", *v.normal)

        uv_bytes = bytearray()
        if has_uvs:
            for v in mesh.vertices:
                uv_bytes += struct.pack("<2f", *v.uv)

        use_uint32 = mesh.vertex_count() > 65535
        idx_bytes = bytearray()
        fmt = "<I" if use_uint32 else "<H"
        for tri in mesh.triangles:
            for idx in tri:
                idx_bytes += struct.pack(fmt, idx)

        def _pad(b: bytearray) -> bytearray:
            while len(b) % 4 != 0:
                b += b"\x00"
            return b

        buffer_views = []
        accessors = []
        blob = bytearray()

        def _add_view(data: bytearray, target: int) -> int:
            offset = len(blob)
            blob.extend(_pad(bytearray(data)))
            buffer_views.append({
                "buffer": 0, "byteOffset": offset, "byteLength": len(data),
                "target": target,
            })
            return len(buffer_views) - 1

        ARRAY_BUFFER = 34962
        ELEMENT_ARRAY_BUFFER = 34963

        pos_view = _add_view(pos_bytes, ARRAY_BUFFER)
        xs = [v.x for v in mesh.vertices] or [0.0]
        ys = [v.y for v in mesh.vertices] or [0.0]
        zs = [v.z for v in mesh.vertices] or [0.0]
        accessors.append({
            "bufferView": pos_view, "componentType": 5126, "count": mesh.vertex_count(),
            "type": "VEC3", "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)],
        })
        attributes = {"POSITION": 0}

        if has_normals:
            nrm_view = _add_view(nrm_bytes, ARRAY_BUFFER)
            accessors.append({
                "bufferView": nrm_view, "componentType": 5126,
                "count": mesh.vertex_count(), "type": "VEC3",
            })
            attributes["NORMAL"] = len(accessors) - 1

        if has_uvs:
            uv_view = _add_view(uv_bytes, ARRAY_BUFFER)
            accessors.append({
                "bufferView": uv_view, "componentType": 5126,
                "count": mesh.vertex_count(), "type": "VEC2",
            })
            attributes["TEXCOORD_0"] = len(accessors) - 1

        idx_view = _add_view(idx_bytes, ELEMENT_ARRAY_BUFFER)
        accessors.append({
            "bufferView": idx_view,
            "componentType": 5125 if use_uint32 else 5123,
            "count": mesh.triangle_count() * 3, "type": "SCALAR",
        })
        indices_accessor = len(accessors) - 1

        gltf = {
            "asset": {"version": "2.0", "generator": "harita.export.GLTFExporter"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, "name": mesh.name}],
            "meshes": [{
                "name": mesh.name,
                "primitives": [{
                    "attributes": attributes,
                    "indices": indices_accessor,
                    "mode": 4,  # TRIANGLES
                }],
            }],
            "buffers": [{"byteLength": len(blob)}],
            "bufferViews": buffer_views,
            "accessors": accessors,
        }
        return bytes(blob), gltf

    @staticmethod
    def export_gltf(mesh: Mesh3D, path: str) -> ExportResult:
        import base64
        import json as _json

        blob, gltf = GLTFExporter._build_buffers(mesh)
        path_gltf = Path(path)
        bin_name = path_gltf.stem + ".bin"
        gltf["buffers"][0]["uri"] = bin_name
        (path_gltf.parent / bin_name).write_bytes(blob)
        content = _json.dumps(gltf, indent=2)
        path_gltf.write_text(content, encoding="utf-8")
        total = len(content.encode("utf-8")) + len(blob)
        return ExportResult(str(path_gltf), "gltf", total,
                             mesh.vertex_count(), mesh.triangle_count())

    @staticmethod
    def export_glb(mesh: Mesh3D, path: str) -> ExportResult:
        import json as _json

        blob, gltf = GLTFExporter._build_buffers(mesh)
        json_bytes = _json.dumps(gltf, separators=(",", ":")).encode("utf-8")
        while len(json_bytes) % 4 != 0:
            json_bytes += b" "
        while len(blob) % 4 != 0:
            blob += b"\x00"

        json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes  # 'JSON'
        bin_chunk = struct.pack("<II", len(blob), 0x004E4942) + blob                # 'BIN\0'
        total_length = 12 + len(json_chunk) + len(bin_chunk)
        header = struct.pack("<III", 0x46546C67, 2, total_length)  # magic 'glTF', version 2

        data = header + json_chunk + bin_chunk
        Path(path).write_bytes(data)
        return ExportResult(path, "glb", len(data),
                             mesh.vertex_count(), mesh.triangle_count())


class SceneGLTFExporter:
    """Çoklu-mesh/çoklu-materyal GLB exporter.

    Denetim maddesi (c) - "bina yüzey materyallerinin viewer'a uçtan uca
    bağlanması" - önceki oturumlarda yalnızca canlı viewer (`app_shell.
    session.AppSession.scene_json()` -> `render_engine.scene_bridge.Scene`
    -> `app_shell/web/index.html` WebGL2 renderer) için kapatılmıştı.
    `app_shell.session.AppSession.export_scene()`'in GLB dalı ise hâlâ
    `GLTFExporter.export_glb(mesh)` ile TEK bir mesh, HİÇBİR materyal
    olmadan (`GLTFExporter` materyal parametresi almıyor) diske yazıyordu
    - yani indirilen/paylaşılan .glb dosyasında binaların hangi malzemeden
    (beton/tuğla/cam/...) olduğuna dair hiçbir bilgi yoktu, yalnızca canlı
    tarayıcı oturumu besleniyordu. Bu sınıf o gerçek eksikliği kapatır:
    `render_engine.scene_bridge.Scene`'i (binalar zaten `resolve_building_
    materials()` ile çözülmüş `PBRMaterial`'lerle bu sahneye ekleniyor,
    bkz. `AppSession.scene_json()`/`export_scene()`) doğrudan standart
    glTF 2.0 `materials[].pbrMetallicRoughness` olarak yazar - her bina
    kendi mesh + kendi materyaliyle (`baseColorFactor`, `metallicFactor`,
    `roughnessFactor`) tek bir .glb dosyasında, herhangi bir glTF 2.0
    görüntüleyicide (Blender, glTF Viewer, vb.) açılabilir şekilde çıkar.

    Kasıtlı sınır: `albedo_map` (doku) BU exporter'a gömülmez - proje
    stdlib-only ilkesiyle gerçek bir PNG/JPEG kodlayıcı barındırmıyor
    (`material_engine.texture_baking`'in `data:harita-texture-v1;...`
    şeması yalnızca bu projenin kendi WebGL2 viewer'ının anladığı özel bir
    format, standart bir glTF image kaynağı DEĞİL - bunu ham glTF `images`
    girdisi olarak yazmak, üçüncü taraf bir görüntüleyicide sessizce bozuk
    doku üretir). Bu yüzden yalnızca renk/pürüzlülük/metaliklik (her zaman
    doğru, kayıpsız) yazılır; doku, viewer'ın kendi `data:harita-texture-v1`
    yolu üzerinden ayrı olarak (`scene_json()` + canlı WebGL2 renderer)
    sağlanmaya devam eder. Bu, sessizce yanlış/bozuk dosya üretmektense
    açıkça eksik bırakmayı tercih eden mevcut proje disipliniyle (bkz.
    `UnsupportedFormatError` kullanan diğer exporter'lar) tutarlıdır.
    """

    @staticmethod
    def export_glb(scene: "Any", path: str) -> ExportResult:
        import json as _json

        nodes_with_geometry = [n for n in scene.nodes if n.mesh.vertices and n.mesh.triangles]
        if not nodes_with_geometry:
            raise ValueError("Scene'de geometrisi olan hiçbir node yok - GLB export edilemez.")

        blob = bytearray()
        buffer_views: list[dict] = []
        accessors: list[dict] = []
        gltf_meshes: list[dict] = []
        gltf_nodes: list[dict] = []

        # Materyal adı -> gltf materials[] indeksi (aynı materyal birden
        # fazla node'da kullanılıyorsa tekrar yazılmaz).
        material_index_by_name: dict[str, int] = {}
        gltf_materials: list[dict] = []

        def _add_view(data: bytes, target: int) -> int:
            offset = len(blob)
            padded = bytearray(data)
            while len(padded) % 4 != 0:
                padded += b"\x00"
            blob.extend(padded)
            buffer_views.append({
                "buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target,
            })
            return len(buffer_views) - 1

        ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963

        for node in nodes_with_geometry:
            mesh = node.mesh
            pos_bytes = bytearray()
            for v in mesh.vertices:
                pos_bytes += struct.pack("<3f", v.x, v.y, v.z)
            has_normals = all(v.normal is not None for v in mesh.vertices)
            has_uvs = all(v.uv is not None for v in mesh.vertices)
            nrm_bytes = bytearray()
            if has_normals:
                for v in mesh.vertices:
                    nrm_bytes += struct.pack("<3f", *v.normal)
            uv_bytes = bytearray()
            if has_uvs:
                for v in mesh.vertices:
                    uv_bytes += struct.pack("<2f", *v.uv)
            use_uint32 = mesh.vertex_count() > 65535
            idx_fmt = "<I" if use_uint32 else "<H"
            idx_bytes = bytearray()
            for tri in mesh.triangles:
                for idx in tri:
                    idx_bytes += struct.pack(idx_fmt, idx)

            pos_view = _add_view(bytes(pos_bytes), ARRAY_BUFFER)
            xs = [v.x for v in mesh.vertices] or [0.0]
            ys = [v.y for v in mesh.vertices] or [0.0]
            zs = [v.z for v in mesh.vertices] or [0.0]
            accessors.append({
                "bufferView": pos_view, "componentType": 5126, "count": mesh.vertex_count(),
                "type": "VEC3", "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)],
            })
            attributes = {"POSITION": len(accessors) - 1}

            if has_normals:
                nrm_view = _add_view(bytes(nrm_bytes), ARRAY_BUFFER)
                accessors.append({
                    "bufferView": nrm_view, "componentType": 5126,
                    "count": mesh.vertex_count(), "type": "VEC3",
                })
                attributes["NORMAL"] = len(accessors) - 1

            if has_uvs:
                uv_view = _add_view(bytes(uv_bytes), ARRAY_BUFFER)
                accessors.append({
                    "bufferView": uv_view, "componentType": 5126,
                    "count": mesh.vertex_count(), "type": "VEC2",
                })
                attributes["TEXCOORD_0"] = len(accessors) - 1

            idx_view = _add_view(bytes(idx_bytes), ELEMENT_ARRAY_BUFFER)
            accessors.append({
                "bufferView": idx_view,
                "componentType": 5125 if use_uint32 else 5123,
                "count": mesh.triangle_count() * 3, "type": "SCALAR",
            })
            indices_accessor = len(accessors) - 1

            primitive: dict = {
                "attributes": attributes, "indices": indices_accessor, "mode": 4,
            }
            material = scene.materials.get(node.material_name) if node.material_name else None
            if material is not None:
                if node.material_name not in material_index_by_name:
                    albedo = getattr(material, "albedo", (0.8, 0.8, 0.8))
                    opacity = getattr(material, "opacity", 1.0)
                    gltf_materials.append({
                        "name": node.material_name,
                        "pbrMetallicRoughness": {
                            "baseColorFactor": [albedo[0], albedo[1], albedo[2], opacity],
                            "metallicFactor": getattr(material, "metallic", 0.0),
                            "roughnessFactor": getattr(material, "roughness", 0.8),
                        },
                        **({"alphaMode": "BLEND"} if opacity < 1.0 else {}),
                    })
                    material_index_by_name[node.material_name] = len(gltf_materials) - 1
                primitive["material"] = material_index_by_name[node.material_name]

            gltf_meshes.append({"name": mesh.name, "primitives": [primitive]})
            gltf_nodes.append({
                "mesh": len(gltf_meshes) - 1, "name": mesh.name,
                "translation": list(node.translation),
            })

        gltf: dict = {
            "asset": {"version": "2.0", "generator": "harita.export.SceneGLTFExporter"},
            "scene": 0,
            "scenes": [{"nodes": list(range(len(gltf_nodes)))}],
            "nodes": gltf_nodes,
            "meshes": gltf_meshes,
            "buffers": [{"byteLength": len(blob)}],
            "bufferViews": buffer_views,
            "accessors": accessors,
        }
        if gltf_materials:
            gltf["materials"] = gltf_materials

        json_bytes = _json.dumps(gltf, separators=(",", ":")).encode("utf-8")
        while len(json_bytes) % 4 != 0:
            json_bytes += b" "
        bin_blob = bytes(blob)
        while len(bin_blob) % 4 != 0:
            bin_blob += b"\x00"

        json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
        bin_chunk = struct.pack("<II", len(bin_blob), 0x004E4942) + bin_blob
        total_length = 12 + len(json_chunk) + len(bin_chunk)
        header = struct.pack("<III", 0x46546C67, 2, total_length)

        data = header + json_chunk + bin_chunk
        Path(path).write_bytes(data)
        total_verts = sum(n.mesh.vertex_count() for n in nodes_with_geometry)
        total_tris = sum(n.mesh.triangle_count() for n in nodes_with_geometry)
        return ExportResult(path, "glb", len(data), total_verts, total_tris)


class GLTFParseError(ValueError):
    """glTF/GLB girdisi geçersiz/bozuk olduğunda fırlatılır (sessiz yanlış
    veri üretmek yerine)."""


class GLTFImporter:
    """glTF 2.0 (.gltf JSON + .bin) ve GLB (binary konteyner) okuyucu.
    `GLTFExporter` ile ürettiği çıktı kümesiyle round-trip uyumludur:
    POSITION (+opsiyonel NORMAL/TEXCOORD_0) attribute'ları ve indexed
    triangle listesi (mesh başına ilk primitive) desteklenir. Desteklenmeyen
    bir accessor componentType/type ile karşılaşılırsa `GLTFParseError`
    fırlatılır — sessizce hatalı geometri üretilmez."""

    _COMPONENT_FMT = {
        5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
        5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4),
    }
    _TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
                   "MAT2": 4, "MAT3": 9, "MAT4": 16}

    @classmethod
    def _read_accessor(cls, gltf: dict, buffers: list[bytes], accessor_idx: int) -> list:
        accessor = gltf["accessors"][accessor_idx]
        component_type = accessor["componentType"]
        acc_type = accessor["type"]
        count = accessor["count"]
        if component_type not in cls._COMPONENT_FMT:
            raise GLTFParseError(f"Desteklenmeyen componentType: {component_type}")
        if acc_type not in cls._TYPE_COUNT:
            raise GLTFParseError(f"Desteklenmeyen accessor type: {acc_type}")
        fmt_char, comp_size = cls._COMPONENT_FMT[component_type]
        n_comp = cls._TYPE_COUNT[acc_type]

        view_idx = accessor.get("bufferView")
        if view_idx is None:
            raise GLTFParseError("bufferView'siz (sparse) accessor desteklenmiyor")
        view = gltf["bufferViews"][view_idx]
        buf = buffers[view.get("buffer", 0)]
        base_offset = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        stride = view.get("byteStride", comp_size * n_comp)

        out = []
        fmt = "<" + fmt_char * n_comp
        elem_size = comp_size * n_comp
        for i in range(count):
            off = base_offset + i * stride
            chunk = buf[off:off + elem_size]
            if len(chunk) < elem_size:
                raise GLTFParseError(
                    f"Buffer sınırları aşıldı (accessor {accessor_idx}, eleman {i})"
                )
            values = struct.unpack(fmt, chunk)
            out.append(values[0] if n_comp == 1 else values)
        return out

    @classmethod
    def _load_buffers(cls, gltf: dict, base_dir: Path,
                       embedded_glb_bin: Optional[bytes]) -> list[bytes]:
        import base64
        buffers = []
        for i, buf_def in enumerate(gltf.get("buffers", [])):
            uri = buf_def.get("uri")
            if uri is None:
                if embedded_glb_bin is None:
                    raise GLTFParseError(f"buffer[{i}] uri yok ve GLB BIN chunk bulunamadı")
                buffers.append(embedded_glb_bin)
            elif uri.startswith("data:"):
                _, b64data = uri.split(",", 1)
                buffers.append(base64.b64decode(b64data))
            else:
                bin_path = base_dir / uri
                if not bin_path.exists():
                    raise GLTFParseError(f"Harici buffer dosyası bulunamadı: {bin_path}")
                buffers.append(bin_path.read_bytes())
        return buffers

    @classmethod
    def _mesh_from_gltf(cls, gltf: dict, buffers: list[bytes], name_hint: str) -> Mesh3D:
        if not gltf.get("meshes"):
            raise GLTFParseError("glTF içinde 'meshes' yok")
        mesh_def = gltf["meshes"][0]
        if not mesh_def.get("primitives"):
            raise GLTFParseError("mesh içinde 'primitives' yok")
        prim = mesh_def["primitives"][0]
        attrs = prim.get("attributes", {})
        if "POSITION" not in attrs:
            raise GLTFParseError("primitive'de POSITION attribute'u yok")

        positions = cls._read_accessor(gltf, buffers, attrs["POSITION"])
        normals = cls._read_accessor(gltf, buffers, attrs["NORMAL"]) if "NORMAL" in attrs else None
        uvs = cls._read_accessor(gltf, buffers, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None

        vertices = []
        for idx, p in enumerate(positions):
            vertices.append(Vertex3D(
                x=p[0], y=p[1], z=p[2],
                normal=tuple(normals[idx]) if normals is not None else None,
                uv=tuple(uvs[idx]) if uvs is not None else None,
            ))

        if "indices" in prim:
            flat = cls._read_accessor(gltf, buffers, prim["indices"])
        else:
            flat = list(range(len(vertices)))
        if len(flat) % 3 != 0:
            raise GLTFParseError(f"Indices sayısı 3'ün katı değil: {len(flat)}")
        triangles = [tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)]

        mesh_name = mesh_def.get("name") or name_hint
        return Mesh3D(vertices=vertices, triangles=triangles, name=mesh_name)

    @classmethod
    def import_gltf(cls, path: str) -> Mesh3D:
        """`.gltf` (JSON, ayrık `.bin` veya base64 data-uri) dosyasını okur."""
        import json as _json

        p = Path(path)
        gltf = _json.loads(p.read_text(encoding="utf-8"))
        buffers = cls._load_buffers(gltf, p.parent, embedded_glb_bin=None)
        return cls._mesh_from_gltf(gltf, buffers, name_hint=p.stem)

    @classmethod
    def import_glb(cls, path: str) -> Mesh3D:
        """Binary glTF (`.glb`) konteynerini okur (magic/version/chunk
        doğrulamasıyla)."""
        import json as _json

        p = Path(path)
        data = p.read_bytes()
        if len(data) < 12:
            raise GLTFParseError("Dosya GLB header'ı için çok kısa")

        magic, version, total_length = struct.unpack_from("<III", data, 0)
        if magic != 0x46546C67:
            raise GLTFParseError(f"Geçersiz GLB magic: {magic:#x} (beklenen 'glTF')")
        if version != 2:
            raise GLTFParseError(f"Desteklenmeyen glTF versiyonu: {version} (yalnızca 2)")
        if total_length > len(data):
            raise GLTFParseError(
                f"GLB header'daki uzunluk ({total_length}) dosya boyutunu ({len(data)}) aşıyor"
            )

        offset = 12
        json_chunk = None
        bin_chunk = None
        while offset < total_length:
            if offset + 8 > total_length:
                raise GLTFParseError("Bozuk chunk header (dosya sınırı aşıldı)")
            chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
            chunk_start = offset + 8
            chunk_end = chunk_start + chunk_len
            if chunk_end > total_length:
                raise GLTFParseError("Bozuk chunk uzunluğu (dosya sınırı aşıldı)")
            payload = data[chunk_start:chunk_end]
            if chunk_type == 0x4E4F534A:  # 'JSON'
                json_chunk = payload
            elif chunk_type == 0x004E4942:  # 'BIN\0'
                bin_chunk = payload
            offset = chunk_end

        if json_chunk is None:
            raise GLTFParseError("GLB içinde JSON chunk bulunamadı")

        gltf = _json.loads(json_chunk.decode("utf-8").rstrip("\x00 "))
        buffers = cls._load_buffers(gltf, p.parent, embedded_glb_bin=bin_chunk)
        return cls._mesh_from_gltf(gltf, buffers, name_hint=p.stem)


# ======================================================================== #
# DXF (ASCII, basit 3DFACE mesh temsili)
# ======================================================================== #

class DXFExporter:
    """AutoCAD DXF (ASCII) exporter. Her üçgeni bir `3DFACE` varlığı olarak
    yazar (DXF R12 uyumlu minimal ENTITIES bölümü). DWG (ikili, kapalı
    format) desteklenmez — bkz. `UnsupportedFormatError`."""

    @staticmethod
    def export(mesh: Mesh3D, path: str, layer: str = "MESH") -> ExportResult:
        lines = ["0", "SECTION", "2", "ENTITIES"]
        for (i, j, k) in mesh.triangles:
            a, b, c = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]
            lines += ["0", "3DFACE", "8", layer]
            for tag_prefix, v in ((10, a), (11, b), (12, c), (13, c)):
                lines += [str(tag_prefix), f"{v.x:.6f}"]
                lines += [str(tag_prefix + 10), f"{v.y:.6f}"]
                lines += [str(tag_prefix + 20), f"{v.z:.6f}"]
        lines += ["0", "ENDSEC", "0", "EOF"]
        content = "\n".join(lines) + "\n"
        Path(path).write_text(content, encoding="utf-8")
        return ExportResult(path, "dxf", len(content.encode("utf-8")),
                             mesh.vertex_count(), mesh.triangle_count())


class DWGExporter:
    """DWG, Autodesk'in kapalı ikili formatıdır; resmi ODA/Autodesk SDK'sı
    olmadan spesifikasyona uygun biçimde yazılamaz. `export()` her zaman
    `UnsupportedFormatError` fırlatır — sessiz/bozuk çıktı üretmemek için."""

    @staticmethod
    def export(mesh: Mesh3D, path: str) -> ExportResult:
        raise UnsupportedFormatError(
            "DWG kapalı bir ikili formattır; bağımlılıksız yazılamaz. "
            "Alternatif olarak DXFExporter (ASCII, açık spesifikasyon) kullanın "
            "veya resmi bir DWG SDK (ODA/Autodesk) entegre edin."
        )


class FBXExporter:
    """Autodesk FBX (ikili/ASCII) resmi FBX SDK olmadan güvenilir biçimde
    yazılamaz; her zaman `UnsupportedFormatError` fırlatır."""

    @staticmethod
    def export(mesh: Mesh3D, path: str) -> ExportResult:
        raise UnsupportedFormatError(
            "FBX, Autodesk FBX SDK gerektiren karmaşık bir format. "
            "Alternatif: OBJExporter veya GLTFExporter (açık, bağımlılıksız)."
        )


class USDExporter:
    """Pixar USD/USDZ, resmi `usd-core`/OpenUSD kütüphanesi olmadan
    (özellikle binary `.usdc` ve zip-tabanlı `.usdz`) güvenilir biçimde
    yazılamaz. `export_usda` basit ASCII `.usda` metnini üretir (USD'nin
    insan-okunur alt kümesi); `.usd`/`.usdz` için SDK gerekir."""

    @staticmethod
    def export_usda(mesh: Mesh3D, path: str) -> ExportResult:
        pts = ", ".join(f"({v.x:.6f}, {v.y:.6f}, {v.z:.6f})" for v in mesh.vertices)
        face_counts = ", ".join("3" for _ in mesh.triangles)
        face_indices = ", ".join(
            f"{i}, {j}, {k}" for (i, j, k) in mesh.triangles
        )
        content = (
            f'#usda 1.0\n'
            f'def Mesh "{mesh.name or "mesh"}"\n'
            f'{{\n'
            f'    int[] faceVertexCounts = [{face_counts}]\n'
            f'    int[] faceVertexIndices = [{face_indices}]\n'
            f'    point3f[] points = [{pts}]\n'
            f'}}\n'
        )
        Path(path).write_text(content, encoding="utf-8")
        return ExportResult(path, "usda", len(content.encode("utf-8")),
                             mesh.vertex_count(), mesh.triangle_count())

    @staticmethod
    def export(mesh: Mesh3D, path: str) -> ExportResult:
        raise UnsupportedFormatError(
            "Binary .usd/.usdz, OpenUSD (usd-core) kütüphanesi gerektirir. "
            "Bağımlılıksız ASCII alternatif için USDExporter.export_usda() kullanın."
        )
