"""
Roadmap V4 - Faz E3: Terrain Engine - Hidroloji Görselleştirmesi ve
Gerçek DEM Kaynak Entegrasyonu
====================================================================

Kapanmadan önceki durum (denetim maddesi): D10'un `FlowAccumulation`
çıktısı (nehir/dere ağı) yalnızca sayısal bir grid olarak kalıyordu -
`render_engine`/viewer'a hiç bağlanmamıştı; ayrıca D14'ün gerçek
DEFLATE-GeoTIFF okuyucusuyla üretilen gerçek yükseklik verisinin uçtan
uca `TerrainMeshGenerator`'a beslendiği bir entegrasyon testi yoktu.

Bu test dosyası ikisini birden kapatır:

1. D14'ün kendi test-amaçlı minimal TIFF yazıcısını (bkz.
   `test_phaseD14_geotiff_deflate.py::_build_tiff`) yeniden kullanarak,
   sentetik DEĞİL ama gerçek bir DEFLATE-sıkıştırmalı GeoTIFF dosyası
   (gerçek TIFF IFD + gerçek zlib strip) üretir - bilinen, tekil bir
   "V" vadisi profiline sahip (merkez sütun en alçak, kenarlara doğru
   yükselir) böylece akış ağının nereden geçmesi gerektiği önceden
   bilinir.
2. Bu dosyayı `HeightmapParser.parse_file()` (gerçek TIFF/DEFLATE
   çözücü) ile okuyup `HeightmapGrid`'e çevirir, `TerrainMeshGenerator.
   generate()` ile mesh üretir, `ErosionSimulator` ile örnek bir erozyon
   geçişi uygular (mevcut sentetik-DEM testlerini bozmadığını
   doğrulamak için ayrı bir kontrol grubu olarak), `FlowAccumulation`
   ile akış ağını hesaplar ve `render_engine.scene_bridge.Scene.
   attach_flow_network()` ile sahneye bağlar - `to_dict()` çıktısında
   `flow_lines` anahtarının viewer'ın tüketebileceği world-space
   segmentlerle dolu olduğunu doğrular.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.coordinate_systems import GeoPoint
from harita.core_engine.gis_core import HeightmapParser
from harita.render_engine.scene_bridge import Scene
from harita.terrain_engine import (
    ErosionSimulator,
    FlowAccumulation,
    HeightmapGrid,
    TerrainMeshGenerator,
)

# ======================================================================== #
# D14 TIFF yazıcısının yeniden kullanımı (aynı minimal-ama-gerçek yazıcı)
# ======================================================================== #


def _build_tiff(width, height, elevations, compression=8, endian="<"):
    flat = [v for row in elevations for v in row]
    raw = struct.pack(f"{endian}{len(flat)}f", *flat)
    strip_data = zlib.compress(raw) if compression in (8, 32946) else raw

    header = struct.pack(f"{endian}2sHI", b"II" if endian == "<" else b"MM", 42, 8)

    entries = [
        (256, 4, 1, width),
        (257, 4, 1, height),
        (258, 3, 1, 32),
        (259, 3, 1, compression),
        (273, 4, 1, 0),  # patched below
        (277, 3, 1, 1),
        (278, 4, 1, height),
        (279, 4, 1, len(strip_data)),
        (339, 3, 1, 3),
    ]
    entries.sort(key=lambda e: e[0])
    n = len(entries)
    ifd_offset = 8
    ifd_size = 2 + n * 12 + 4
    strip_offset = ifd_offset + ifd_size

    _CODE = {3: "H", 4: "I"}

    body = struct.pack(f"{endian}H", n)
    for tag, ftype, count, value in entries:
        if tag == 273:
            value = strip_offset
        code = _CODE[ftype]
        value_bytes = struct.pack(f"{endian}{code}", value)
        value_bytes = value_bytes + b"\x00" * (4 - len(value_bytes))
        body += struct.pack(f"{endian}HHI", tag, ftype, count)
        body += value_bytes
    body += struct.pack(f"{endian}I", 0)

    return header + body + strip_data


def _v_valley_grid(width=9, height=7):
    """Bilinen bir 'V' vadi profili: merkez sütun (width//2) her satırda
    en alçak nokta, kenarlara doğru doğrusal olarak yükselir; ayrıca
    kuzeyden (row 0, yüksek) güneye (row height-1, alçak) genel bir eğim
    var - böylece akış hem merkez sütuna toplanmalı hem de güneye doğru
    akmalı (tek, öngörülebilir bir ana dere hattı)."""
    mid = width // 2
    grid = []
    for r in range(height):
        row = []
        for c in range(width):
            lateral = abs(c - mid) * 2.0  # vadi kenarlarına doğru yükselti
            longitudinal = (height - 1 - r) * 1.0  # kuzeyde daha yüksek
            row.append(10.0 + lateral + longitudinal)
        grid.append(row)
    return grid


def _write_geotiff(tmp_name: str, elevations) -> str:
    height = len(elevations)
    width = len(elevations[0])
    data = _build_tiff(width, height, elevations, compression=8)
    path = f"/tmp/{tmp_name}"
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _grid_from_parsed(fc, resolution_m: float = 2.0) -> HeightmapGrid:
    feature = fc.features[0]
    elevations = feature.coordinates["elevations"]
    width = feature.coordinates["width"]
    height = feature.coordinates["height"]
    return HeightmapGrid(
        width=width,
        height=height,
        resolution_m=resolution_m,
        elevations=elevations,
        origin=GeoPoint(lat=41.0, lon=29.0),
    )


# ======================================================================== #
# Uçtan uca: gerçek DEFLATE-GeoTIFF -> HeightmapGrid
# ======================================================================== #


def test_real_deflate_geotiff_roundtrips_into_heightmap_grid():
    elevations = _v_valley_grid()
    path = _write_geotiff("e3_valley.tif", elevations)

    parser = HeightmapParser()
    fc = parser.parse_file(path)
    grid = _grid_from_parsed(fc)

    assert grid.width == 9
    assert grid.height == 7
    for r_row, e_row in zip(grid.elevations, elevations):
        for got, expected in zip(r_row, e_row):
            assert abs(got - expected) < 1e-6


def test_real_geotiff_dem_feeds_terrain_mesh_generator_end_to_end():
    elevations = _v_valley_grid()
    path = _write_geotiff("e3_valley_mesh.tif", elevations)

    grid = _grid_from_parsed(HeightmapParser().parse_file(path))
    mesh = TerrainMeshGenerator.generate(grid, name="e3_valley_terrain")

    assert mesh.vertex_count() == grid.width * grid.height
    assert mesh.triangle_count() == 2 * (grid.width - 1) * (grid.height - 1)
    # Regression: mevcut sentetik-DEM davranışı bozulmamalı - mesh
    # köşeleri grid'in gerçek (parser'dan gelen) elevation'larıyla eşleşir.
    corner = mesh.vertices[0]
    assert abs(corner.z - elevations[0][0]) < 1e-6


def test_real_geotiff_dem_survives_erosion_pass_without_breaking_shape():
    """D10 ErosionSimulator'ın gerçek (parser'dan gelen) veri üzerinde de
    çalıştığını ve mevcut sentetik-DEM erozyon testlerini bozmadığını
    (grid boyutları/aralığı korunur) doğrular."""
    elevations = _v_valley_grid()
    path = _write_geotiff("e3_valley_erosion.tif", elevations)
    grid = _grid_from_parsed(HeightmapParser().parse_file(path))

    result = ErosionSimulator.thermal_erosion(grid, iterations=5)

    assert result.grid.width == grid.width
    assert result.grid.height == grid.height


# ======================================================================== #
# Faz E3 çekirdek: FlowAccumulation -> Scene.attach_flow_network()
# ======================================================================== #


def test_flow_network_converges_to_known_valley_centerline():
    """Bilinen 'V' vadi profilinde, en yüksek akümülasyona sahip hücreler
    merkez sütunda (mid) toplanmalı - bu, D8 algoritmasının doğru
    çalıştığının bağımsız bir kanıtıdır (roadmap'in kendi öngördüğü
    'gerçek DEM ucu ucuna işlenip görüntülenebilir bir akış ağı üretir'
    kabul kriterinin sayısal karşılığı)."""
    elevations = _v_valley_grid(width=9, height=7)
    grid = HeightmapGrid(
        width=9,
        height=7,
        resolution_m=2.0,
        elevations=elevations,
        origin=GeoPoint(lat=41.0, lon=29.0),
    )
    accum = FlowAccumulation.accumulate(grid)

    mid = 9 // 2
    last_row_idx = 6
    row_accum = accum[last_row_idx]
    max_col = max(range(9), key=lambda c: row_accum[c])
    assert max_col == mid


def test_attach_flow_network_produces_world_space_segments_matching_mesh():
    elevations = _v_valley_grid()
    path = _write_geotiff("e3_valley_scene.tif", elevations)
    grid = _grid_from_parsed(HeightmapParser().parse_file(path), resolution_m=2.0)

    mesh = TerrainMeshGenerator.generate(grid, name="e3_scene_terrain")
    scene = Scene(name="e3_hydrology_scene")
    node = scene.add_mesh(mesh)

    segments = scene.attach_flow_network(node, grid, accumulation_threshold=3.0)

    assert len(segments) > 0
    assert scene.flow_lines[node.name] is segments

    # Her segmentin uçları, aynı grid<->world dönüşümünü kullanan mesh
    # vertex'lerinden biriyle piksel-hassasiyetinde örtüşmeli (aynı
    # x=col*res, y=row*res, z=elevation formülü).
    mesh_points = {(round(v.x, 6), round(v.y, 6), round(v.z, 6)) for v in mesh.vertices}
    for seg in segments:
        start = tuple(round(v, 6) for v in seg["start"])
        end = tuple(round(v, 6) for v in seg["end"])
        assert start in mesh_points
        assert end in mesh_points
        assert 0.0 <= seg["intensity"] <= 1.0

    # En yüksek yoğunluklu segment (en fazla yukarı-havza) mansaba yakın
    # (yüksek row) olmalı - vadinin ana kolunun mantığıyla tutarlı.
    strongest = max(segments, key=lambda s: s["intensity"])
    assert strongest["start"][1] >= grid.resolution_m * (grid.height // 3)


def test_attach_flow_network_serializes_into_scene_to_dict():
    elevations = _v_valley_grid()
    path = _write_geotiff("e3_valley_json.tif", elevations)
    grid = _grid_from_parsed(HeightmapParser().parse_file(path), resolution_m=1.5)

    mesh = TerrainMeshGenerator.generate(grid, name="e3_json_terrain")
    scene = Scene(name="e3_json_scene")
    node = scene.add_mesh(mesh)
    scene.attach_flow_network(node, grid, accumulation_threshold=3.0)

    payload = scene.to_dict()
    assert "flow_lines" in payload
    assert node.name in payload["flow_lines"]
    assert len(payload["flow_lines"][node.name]) == len(scene.flow_lines[node.name])
    for seg in payload["flow_lines"][node.name]:
        assert set(seg.keys()) == {"start", "end", "intensity"}


def test_attach_flow_network_empty_scene_stays_backward_compatible():
    """flow_lines hiç doldurulmazsa (mevcut, E3-öncesi davranış) to_dict()
    çıktısı boş sözlük döner - viewer eski koduyla hiçbir fark görmez."""
    mesh = TerrainMeshGenerator.generate(
        HeightmapGrid(
            width=3,
            height=3,
            resolution_m=1.0,
            elevations=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            origin=GeoPoint(lat=0.0, lon=0.0),
        ),
        name="flat",
    )
    scene = Scene(name="e3_empty_scene")
    scene.add_mesh(mesh)
    payload = scene.to_dict()
    assert payload["flow_lines"] == {}


def test_attach_flow_network_rejects_foreign_node():
    grid = HeightmapGrid(
        width=3,
        height=3,
        resolution_m=1.0,
        elevations=[[3.0, 2.0, 1.0], [3.0, 2.0, 1.0], [3.0, 2.0, 1.0]],
        origin=GeoPoint(lat=0.0, lon=0.0),
    )
    mesh = TerrainMeshGenerator.generate(grid, name="foreign")
    scene_a = Scene(name="a")
    scene_b = Scene(name="b")
    node = scene_a.add_mesh(mesh)

    try:
        scene_b.attach_flow_network(node, grid)
        assert False, "yabancı node için ValueError beklenirdi"
    except ValueError:
        pass
