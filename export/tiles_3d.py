"""
Export — 3D Tiles
==================

Roadmap V3 - Faz D7 (3D Tiles bölümü). Büyük şehir sahneleri için
streaming-friendly bir format: OGC 3D Tiles 1.0 `tileset.json` +
Batched 3D Model (`.b3dm`) tile içerikleri.

Girdi: Faz 15 `Scene` (render_engine.scene_bridge) — her `SceneNode`,
D2'de zaten üretilen `lod_groups`'un en yüksek çözünürlüklü (`ratio=1.0`)
mesh'i kaynak alınarak bir 3D Tiles "leaf" tile'ına dönüştürülür (çok
seviyeli LOD ağacı yerine tek seviyeli, ama D2'nin JS-taraflı LOD seçimiyle
çelişmeyen, doğru/basit bir başlangıç noktası — roadmap D7 kapsamı budur).

Bağımlılık: yalnızca stdlib. `GLTFExporter._build_buffers` (geometry_3d.py)
yeniden kullanılır — b3dm gövdesi geçerli bir GLB'dir.

Format notları (CesiumJS / 3D Tiles 1.0 spesifikasyonuna uygun):
- `tileset.json`: `asset.version = "1.0"`, kök `boundingVolume.box`
  (Cesium 3D Tiles box: center + 3 half-axis vektörü), `geometricError`,
  `content.uri` her tile için ayrı `.b3dm` dosyasına işaret eder.
- `.b3dm`: 28 baytlık binary header (magic `b3dm`, version 1, byteLength,
  featureTableJSONByteLength, featureTableBinaryByteLength,
  batchTableJSONByteLength, batchTableBinaryByteLength) + Feature Table JSON
  (`{"BATCH_LENGTH": N}`) + (boş binary) + (boş batch table) + GLB gövdesi.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..mesh_engine import Mesh3D
from .geometry_3d import ExportResult, GLTFExporter


class TilesetValidationError(ValueError):
    """Üretilen `tileset.json` kendi şema kurallarını ihlal ediyorsa fırlatılır."""


@dataclass(slots=True)
class BoundingBox3DTiles:
    """3D Tiles `box` bounding volume: merkez + 3 yarı-eksen vektörü."""

    center: tuple[float, float, float]
    half_x: tuple[float, float, float]
    half_y: tuple[float, float, float]
    half_z: tuple[float, float, float]

    @staticmethod
    def from_aabb(min_pt: tuple[float, float, float],
                  max_pt: tuple[float, float, float]) -> "BoundingBox3DTiles":
        cx = (min_pt[0] + max_pt[0]) / 2.0
        cy = (min_pt[1] + max_pt[1]) / 2.0
        cz = (min_pt[2] + max_pt[2]) / 2.0
        hx = max((max_pt[0] - min_pt[0]) / 2.0, 1e-6)
        hy = max((max_pt[1] - min_pt[1]) / 2.0, 1e-6)
        hz = max((max_pt[2] - min_pt[2]) / 2.0, 1e-6)
        return BoundingBox3DTiles(
            center=(cx, cy, cz),
            half_x=(hx, 0.0, 0.0), half_y=(0.0, hy, 0.0), half_z=(0.0, 0.0, hz),
        )

    def to_array(self) -> list[float]:
        return [
            *self.center, *self.half_x, *self.half_y, *self.half_z,
        ]

    def union(self, other: "BoundingBox3DTiles") -> "BoundingBox3DTiles":
        """İki box'ı kapsayan eksene-hizalı bir box üretir (basitleştirme:
        her iki box'ın köşe noktalarından yeni bir AABB çıkarılır)."""
        def corners(b: "BoundingBox3DTiles") -> list[tuple[float, float, float]]:
            cx, cy, cz = b.center
            hx, hy, hz = b.half_x[0], b.half_y[1], b.half_z[2]
            pts = []
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        pts.append((cx + sx * hx, cy + sy * hy, cz + sz * hz))
            return pts

        pts = corners(self) + corners(other)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        zs = [p[2] for p in pts]
        return BoundingBox3DTiles.from_aabb(
            (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))
        )


def _build_b3dm(mesh: Mesh3D) -> bytes:
    """Tek bir mesh'i geçerli bir `.b3dm` (Batched 3D Model) binary'sine
    paketler. Batch table boş bırakılır (BATCH_LENGTH=1, tek "feature")."""
    glb_blob, gltf = GLTFExporter._build_buffers(mesh)
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4 != 0:
        json_bytes += b" "
    bin_bytes = bytearray(glb_blob)
    while len(bin_bytes) % 4 != 0:
        bin_bytes += b"\x00"

    json_chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    bin_chunk = struct.pack("<II", len(bin_bytes), 0x004E4942) + bytes(bin_bytes)
    glb_total_len = 12 + len(json_chunk) + len(bin_chunk)
    glb_header = struct.pack("<III", 0x46546C67, 2, glb_total_len)
    glb = glb_header + json_chunk + bin_chunk

    feature_table = {"BATCH_LENGTH": 1}
    ft_json = json.dumps(feature_table, separators=(",", ":")).encode("utf-8")
    while len(ft_json) % 8 != 0:
        ft_json += b" "

    header_len = 28
    total_len = header_len + len(ft_json) + len(glb)
    b3dm_header = struct.pack(
        "<4sIIIIII",
        b"b3dm", 1, total_len,
        len(ft_json), 0,  # feature table JSON/binary byte length
        0, 0,             # batch table JSON/binary byte length
    )
    return b3dm_header + ft_json + glb


@dataclass(slots=True)
class TileEntry:
    """Bir sahne node'undan üretilmiş tek bir tile: mesh + world-space AABB."""

    name: str
    mesh: Mesh3D
    min_pt: tuple[float, float, float]
    max_pt: tuple[float, float, float]


class Tiles3DExporter:
    """Bir `Scene` (render_engine.scene_bridge) nesnesini OGC 3D Tiles
    (`tileset.json` + `.b3dm` tile'ları) olarak diske yazar. Çok-tile bölme
    Faz 13 streaming mantığıyla hizalı: her `SceneNode` bağımsız bir tile'dır
    (grid-tabanlı üst-seviye gruplama, roadmap D8/streaming ile birleşir)."""

    @staticmethod
    def _mesh_world_bounds(mesh: Mesh3D,
                            translation: tuple[float, float, float]) -> tuple[
            tuple[float, float, float], tuple[float, float, float]]:
        (mn_x, mn_y, mn_z), (mx_x, mx_y, mx_z) = mesh.bounding_box()
        tx, ty, tz = translation
        return (mn_x + tx, mn_y + ty, mn_z + tz), (mx_x + tx, mx_y + ty, mx_z + tz)

    @classmethod
    def export(cls, scene, out_dir: str, geometric_error: float = 500.0) -> ExportResult:
        """`scene`: `render_engine.scene_bridge.Scene`. Her node için
        `node.mesh` (varsa `lod_groups[0]`, en yüksek detay) bir `.b3dm`
        tile'ına yazılır; `tileset.json` bunları tek seviyeli bir kök tile
        listesinde toplar."""
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        tiles: list[TileEntry] = []
        seen_names: dict[str, int] = {}
        for node in getattr(scene, "nodes", []):
            mesh = getattr(node, "mesh", None)
            if mesh is None or mesh.vertex_count() == 0:
                continue
            translation = getattr(node, "translation", (0.0, 0.0, 0.0))
            min_pt, max_pt = cls._mesh_world_bounds(mesh, translation)
            # Node isimleri (Scene.add_mesh varsayılanı: mesh.name) çakışabilir
            # — dosya üzerine yazmayı önlemek için benzersizleştirilir.
            base_name = node.name
            count = seen_names.get(base_name, 0)
            seen_names[base_name] = count + 1
            unique_name = base_name if count == 0 else f"{base_name}_{count}"
            tiles.append(TileEntry(name=unique_name, mesh=mesh, min_pt=min_pt, max_pt=max_pt))

        if not tiles:
            raise ValueError("Scene boş — export edilecek mesh içeren node yok")

        children_json = []
        root_box: Optional[BoundingBox3DTiles] = None
        total_bytes = 0

        for tile in tiles:
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tile.name)
            b3dm_path = out_path / f"{safe_name}.b3dm"
            b3dm_bytes = _build_b3dm(tile.mesh)
            b3dm_path.write_bytes(b3dm_bytes)
            total_bytes += len(b3dm_bytes)

            box = BoundingBox3DTiles.from_aabb(tile.min_pt, tile.max_pt)
            root_box = box if root_box is None else root_box.union(box)

            children_json.append({
                "boundingVolume": {"box": box.to_array()},
                "geometricError": 0.0,
                "refine": "REPLACE",
                "content": {"uri": f"{safe_name}.b3dm"},
            })

        assert root_box is not None
        tileset = {
            "asset": {"version": "1.0", "generator": "harita.export.Tiles3DExporter"},
            "geometricError": geometric_error,
            "root": {
                "boundingVolume": {"box": root_box.to_array()},
                "geometricError": geometric_error,
                "refine": "REPLACE",
                "children": children_json,
            },
        }
        tileset_json = json.dumps(tileset, indent=2)
        tileset_path = out_path / "tileset.json"
        tileset_path.write_text(tileset_json, encoding="utf-8")
        total_bytes += len(tileset_json.encode("utf-8"))

        cls.validate_tileset(tileset)

        total_vertices = sum(t.mesh.vertex_count() for t in tiles)
        total_triangles = sum(t.mesh.triangle_count() for t in tiles)
        return ExportResult(str(tileset_path), "3dtiles", total_bytes,
                             total_vertices, total_triangles)

    @staticmethod
    def validate_tileset(tileset: dict) -> None:
        """`tileset.json` şemasının stdlib-`json` ile doğrulanabilir asgari
        kurallarını kontrol eder — bozuk bir tileset sessizce üretilmez."""
        if "asset" not in tileset or tileset["asset"].get("version") != "1.0":
            raise TilesetValidationError("asset.version '1.0' olmalı")
        if "geometricError" not in tileset or tileset["geometricError"] < 0:
            raise TilesetValidationError("geçersiz kök geometricError")
        root = tileset.get("root")
        if root is None:
            raise TilesetValidationError("root eksik")
        if "boundingVolume" not in root or "box" not in root["boundingVolume"]:
            raise TilesetValidationError("root.boundingVolume.box eksik")
        box = root["boundingVolume"]["box"]
        if len(box) != 12:
            raise TilesetValidationError("boundingVolume.box tam olarak 12 sayı içermeli")
        if root.get("refine") not in ("ADD", "REPLACE"):
            raise TilesetValidationError("root.refine 'ADD' veya 'REPLACE' olmalı")
        for child in root.get("children", []):
            if "content" not in child or "uri" not in child["content"]:
                raise TilesetValidationError("child.content.uri eksik")
            cbox = child.get("boundingVolume", {}).get("box")
            if not cbox or len(cbox) != 12:
                raise TilesetValidationError("child.boundingVolume.box eksik/hatalı")


def read_b3dm_header(path: str) -> dict:
    """Bir `.b3dm` dosyasının 28 baytlık header'ını okuyup doğrular
    (round-trip / test yardımcı fonksiyonu)."""
    data = Path(path).read_bytes()
    if len(data) < 28:
        raise TilesetValidationError("b3dm dosyası header için çok kısa")
    magic, version, byte_length, ft_json_len, ft_bin_len, bt_json_len, bt_bin_len = (
        struct.unpack("<4sIIIIII", data[:28])
    )
    if magic != b"b3dm":
        raise TilesetValidationError(f"geçersiz magic: {magic!r}")
    if byte_length != len(data):
        raise TilesetValidationError("byteLength dosya boyutuyla eşleşmiyor")
    return {
        "version": version,
        "byte_length": byte_length,
        "feature_table_json_length": ft_json_len,
        "feature_table_binary_length": ft_bin_len,
        "batch_table_json_length": bt_json_len,
        "batch_table_binary_length": bt_bin_len,
        "glb_offset": 28 + ft_json_len + ft_bin_len + bt_json_len + bt_bin_len,
    }
