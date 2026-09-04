"""ROADMAP_V4 — Track E / Faz E4: Core Engine LAS/LAZ nokta bulutu desteği.

Bu dosya, stdlib `struct` ile bu test içinde **sentetik olarak üretilen**
gerçek LAS 1.2 (Point Data Format 0 ve 3) dosyalarını yazıp
`LASPointCloudParser` ile okuyarak roadmap'in E4 kabul kriterini kanıtlar:
"Küçük bir sentetik LAS dosyası (test içinde stdlib ile üretilen) hatasız
okunur, nokta sayısı/bounding-box doğru raporlanır." Ayrıca `PointCloud`
→ `HeightmapGrid` gridleme köprüsü ve desteklenmeyen Point Data Format /
bozuk dosya / LAZ-olmadan-laspy senaryoları da ayrı testlerle kapatılır.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.coordinate_systems import GeoPoint
from harita.core_engine.gis_core.point_cloud import (
    LASParseError,
    LASPointCloudParser,
    PointCloud,
    PointCloudBounds,
    UnsupportedFormatError,
)
from harita.terrain_engine import HeightmapGrid

# ------------------------------------------------------------------ #
# Sentetik LAS 1.2 üretici (test yardımcı fonksiyonu - stdlib-only)
# ------------------------------------------------------------------ #


def _build_las_bytes(
    points: list[tuple[float, float, float]],
    point_format: int = 0,
    version_minor: int = 2,
    scale: float = 0.001,
) -> bytes:
    """ASPRS LAS public header (227 bayt, 1.2/1.3) + point data record'ları
    stdlib `struct` ile elle inşa eder — harici hiçbir LAS kütüphanesi
    kullanılmaz (test verisinin kendisi de roadmap ilkesine uygun)."""
    n = len(points)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]

    x_offset, y_offset, z_offset = 0.0, 0.0, 0.0
    x_scale = y_scale = z_scale = scale

    record_length = {0: 20, 1: 28, 2: 26, 3: 34}[point_format]
    header_size = 227
    offset_to_points = header_size

    header = bytearray(header_size)
    header[0:4] = b"LASF"
    struct.pack_into("<H", header, 4, 0)  # file source id
    struct.pack_into("<H", header, 6, 0)  # global encoding
    # GUID (16 bayt) - sıfır bırakılabilir
    header[24] = 1  # version major
    header[25] = version_minor  # version minor
    header[26:58] = b"HaritaTest".ljust(32, b"\x00")  # system identifier
    header[58:90] = b"phaseE4-synthetic".ljust(32, b"\x00")  # generating sw
    struct.pack_into("<H", header, 90, 1)  # creation day of year
    struct.pack_into("<H", header, 92, 2026)  # creation year
    struct.pack_into("<H", header, 94, header_size)  # header size
    struct.pack_into("<I", header, 96, offset_to_points)  # offset to point data
    struct.pack_into("<I", header, 100, 0)  # number of VLRs
    header[104] = point_format
    struct.pack_into("<H", header, 105, record_length)
    struct.pack_into("<I", header, 107, n)  # legacy number of point records
    for i in range(5):
        struct.pack_into("<I", header, 111 + i * 4, n if i == 0 else 0)
    struct.pack_into("<ddd", header, 131, x_scale, y_scale, z_scale)
    struct.pack_into("<ddd", header, 155, x_offset, y_offset, z_offset)
    struct.pack_into(
        "<dddddd",
        header,
        179,
        max(xs),
        min(xs),
        max(ys),
        min(ys),
        max(zs),
        min(zs),
    )

    body = bytearray()
    for i, (x, y, z) in enumerate(points):
        xi = round((x - x_offset) / x_scale)
        yi = round((y - y_offset) / y_scale)
        zi = round((z - z_offset) / z_scale)
        rec = bytearray(record_length)
        struct.pack_into("<iiiHBBbBH", rec, 0, xi, yi, zi, 100 + i, 0, 2, 0, 0, 1)
        cursor = 20
        if point_format in (1, 3):
            struct.pack_into("<d", rec, cursor, 1000.0 + i)
            cursor += 8
        if point_format in (2, 3):
            struct.pack_into("<HHH", rec, cursor, 1000, 2000, 3000)
            cursor += 6
        body += rec

    return bytes(header) + bytes(body)


_SAMPLE_POINTS = [
    (100.0, 200.0, 10.0),
    (100.5, 200.5, 10.2),
    (101.0, 201.0, 12.5),
    (99.0, 199.0, 9.8),
    (105.0, 205.0, 15.0),
]


class TestLASParsingBasics:
    def test_synthetic_las_format0_round_trip(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        cloud = LASPointCloudParser.parse_bytes(data)

        assert isinstance(cloud, PointCloud)
        assert len(cloud) == len(_SAMPLE_POINTS)
        assert cloud.point_format == 0
        assert cloud.version == (1, 2)
        assert cloud.gps_time is None
        assert cloud.rgb is None
        assert cloud.intensity is not None and len(cloud.intensity) == len(_SAMPLE_POINTS)

        for (x, y, z), (ox, oy, oz) in zip(cloud.points, _SAMPLE_POINTS):
            assert x == pytest.approx(ox, abs=1e-2)
            assert y == pytest.approx(oy, abs=1e-2)
            assert z == pytest.approx(oz, abs=1e-2)

    def test_bounding_box_matches_header(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        cloud = LASPointCloudParser.parse_bytes(data)
        assert cloud.bounds.min_x == pytest.approx(99.0, abs=1e-2)
        assert cloud.bounds.max_x == pytest.approx(105.0, abs=1e-2)
        assert cloud.bounds.min_y == pytest.approx(199.0, abs=1e-2)
        assert cloud.bounds.max_y == pytest.approx(205.0, abs=1e-2)
        assert cloud.bounds.min_z == pytest.approx(9.8, abs=1e-2)
        assert cloud.bounds.max_z == pytest.approx(15.0, abs=1e-2)

    def test_point_format_3_includes_gps_time_and_rgb(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=3)
        cloud = LASPointCloudParser.parse_bytes(data)
        assert cloud.point_format == 3
        assert cloud.gps_time is not None and len(cloud.gps_time) == len(_SAMPLE_POINTS)
        assert cloud.rgb is not None and cloud.rgb[0] == (1000, 2000, 3000)

    def test_point_format_1_gps_time_only(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=1)
        cloud = LASPointCloudParser.parse_bytes(data)
        assert cloud.gps_time is not None
        assert cloud.rgb is None

    def test_point_format_2_rgb_only(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=2)
        cloud = LASPointCloudParser.parse_bytes(data)
        assert cloud.rgb is not None
        assert cloud.gps_time is None

    def test_parse_file_roundtrip(self, tmp_path):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        path = tmp_path / "sample.las"
        path.write_bytes(data)
        cloud = LASPointCloudParser.parse_file(path)
        assert len(cloud) == len(_SAMPLE_POINTS)
        assert cloud.source_file == str(path)


class TestLASErrorHandling:
    def test_invalid_signature_raises(self):
        bad = b"XXXX" + b"\x00" * 223
        with pytest.raises(LASParseError):
            LASPointCloudParser.parse_bytes(bad)

    def test_too_short_file_raises(self):
        with pytest.raises(LASParseError):
            LASPointCloudParser.parse_bytes(b"LASF" + b"\x00" * 10)

    def test_unsupported_point_format_raises(self):
        data = bytearray(_build_las_bytes(_SAMPLE_POINTS, point_format=0))
        data[104] = 6  # LAS 1.4 genişletilmiş format - kapsam dışı
        with pytest.raises(UnsupportedFormatError):
            LASPointCloudParser.parse_bytes(bytes(data))

    def test_truncated_point_data_raises(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        truncated = data[:-5]  # son kaydı kesiyoruz
        with pytest.raises(LASParseError):
            LASPointCloudParser.parse_bytes(truncated)

    def test_laz_without_laspy_raises_unsupported(self, tmp_path, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "laspy":
                raise ImportError("no laspy installed (simulated)")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        fake_laz = tmp_path / "sample.laz"
        fake_laz.write_bytes(b"not a real laz file")
        with pytest.raises(UnsupportedFormatError):
            LASPointCloudParser.parse_laz_file(fake_laz)


class TestPointCloudLengthValidation:
    def test_mismatched_auxiliary_array_length_raises(self):
        bounds = PointCloudBounds(0, 0, 0, 1, 1, 1)
        with pytest.raises(LASParseError):
            PointCloud(
                points=[(0, 0, 0), (1, 1, 1)],
                bounds=bounds,
                point_format=0,
                version=(1, 2),
                intensity=[1],  # yalnızca 1 eleman, 2 nokta var - hata beklenir
            )


class TestHeightmapGridBridge:
    def test_to_heightmap_grid_produces_valid_grid(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        cloud = LASPointCloudParser.parse_bytes(data)
        grid = cloud.to_heightmap_grid(resolution_m=1.0, aggregation="max")

        assert isinstance(grid, HeightmapGrid)
        assert grid.width > 0 and grid.height > 0
        assert len(grid.elevations) == grid.height
        assert all(len(row) == grid.width for row in grid.elevations)
        # Grid'deki her yükseklik değeri, kaynak bulut aralığında olmalı.
        all_z = [z for row in grid.elevations for z in row]
        assert min(all_z) >= cloud.bounds.min_z - 1e-6
        assert max(all_z) <= cloud.bounds.max_z + 1e-6

    def test_aggregation_max_ge_mean_ge_min(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        cloud = LASPointCloudParser.parse_bytes(data)
        grid_max = cloud.to_heightmap_grid(resolution_m=10.0, aggregation="max")
        grid_min = cloud.to_heightmap_grid(resolution_m=10.0, aggregation="min")
        grid_mean = cloud.to_heightmap_grid(resolution_m=10.0, aggregation="mean")

        # Tüm noktalar tek hücreye düşecek kadar kaba çözünürlükte:
        z_max = grid_max.elevations[0][0]
        z_min = grid_min.elevations[0][0]
        z_mean = grid_mean.elevations[0][0]
        assert z_max >= z_mean >= z_min

    def test_custom_origin_is_used(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        cloud = LASPointCloudParser.parse_bytes(data)
        origin = GeoPoint(lat=41.0, lon=29.0)
        grid = cloud.to_heightmap_grid(resolution_m=1.0, origin=origin)
        assert grid.origin is origin

    def test_invalid_aggregation_raises(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        cloud = LASPointCloudParser.parse_bytes(data)
        with pytest.raises(ValueError):
            cloud.to_heightmap_grid(resolution_m=1.0, aggregation="bogus")

    def test_non_positive_resolution_raises(self):
        data = _build_las_bytes(_SAMPLE_POINTS, point_format=0)
        cloud = LASPointCloudParser.parse_bytes(data)
        with pytest.raises(ValueError):
            cloud.to_heightmap_grid(resolution_m=0.0)
