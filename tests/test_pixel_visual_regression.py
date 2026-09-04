"""Roadmap V7 — `render_engine.software_rasterizer` + `scripts/pixel_visual_regression.py`
için testler: gerçek piksel-tabanlı görsel regresyon katmanı.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from harita.building_reconstruction import BuildingType, Footprint, ProceduralBuildingGenerator
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.render_engine.software_rasterizer import (
    Camera, Image, pixel_diff, rasterize_mesh, read_ppm, write_png, write_ppm,
)

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "pixel_visual_regression.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("pixel_visual_regression", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_mesh():
    footprint = Footprint(
        polygon=Polygon([Point2D(0, 0), Point2D(10, 0), Point2D(10, 8), Point2D(0, 8)]),
        building_type=BuildingType.OFIS.value, floor_count=3, height_m=9.0,
    )
    building = ProceduralBuildingGenerator.generate(footprint, building_type=BuildingType.OFIS, seed=99)
    return building.full_mesh(include_interior=False)


def test_rasterize_produces_real_pixels_not_blank_canvas():
    mesh = _sample_mesh()
    img = rasterize_mesh(mesh, width=120, height=90)
    assert img.width == 120 and img.height == 90
    background = (24, 26, 32)
    non_bg = sum(
        1 for p in range(0, len(img.pixels), 3)
        if tuple(img.pixels[p:p + 3]) != background
    )
    # Bina siluetinin makul bir kısmı arka plandan farklı olmalı - yoksa
    # rasterizer hiçbir şey çizmiyor demektir (regresyon).
    assert non_bg > (120 * 90) * 0.03


def test_rasterize_is_deterministic_for_same_seed():
    mesh = _sample_mesh()
    img1 = rasterize_mesh(mesh, width=100, height=80)
    img2 = rasterize_mesh(mesh, width=100, height=80)
    assert bytes(img1.pixels) == bytes(img2.pixels)


def test_ppm_round_trip_is_lossless(tmp_path):
    mesh = _sample_mesh()
    img = rasterize_mesh(mesh, width=64, height=48)
    path = tmp_path / "out.ppm"
    write_ppm(img, str(path))
    loaded = read_ppm(str(path))
    assert loaded.width == img.width and loaded.height == img.height
    assert bytes(loaded.pixels) == bytes(img.pixels)


def test_png_writer_produces_valid_signature(tmp_path):
    mesh = _sample_mesh()
    img = rasterize_mesh(mesh, width=32, height=24)
    path = tmp_path / "out.png"
    write_png(img, str(path))
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"IHDR" in data[:20]
    assert data[-8:-4] == b"IEND"


def test_pixel_diff_zero_for_identical_images():
    mesh = _sample_mesh()
    img = rasterize_mesh(mesh, width=64, height=48)
    diff = pixel_diff(img, img)
    assert diff.max_channel_diff == 0
    assert diff.changed_pixel_count == 0
    assert diff.within_tolerance()


def test_pixel_diff_detects_real_regression():
    mesh = _sample_mesh()
    img_a = rasterize_mesh(mesh, width=64, height=48)
    img_b = Image(img_a.width, img_a.height, bytearray(img_a.pixels))
    # Bariz bir "regresyon" simüle et: bir bölgeyi tamamen değiştir.
    for y in range(10, 20):
        for x in range(10, 20):
            img_b.set_pixel(x, y, (255, 0, 0))
    diff = pixel_diff(img_a, img_b)
    assert diff.changed_pixel_count >= 100
    assert not diff.within_tolerance(max_diff=5, max_ratio=0.001)


def test_pixel_diff_rejects_mismatched_dimensions():
    a = Image.new(10, 10)
    b = Image.new(20, 10)
    with pytest.raises(ValueError):
        pixel_diff(a, b)


def test_script_render_all_demo_cases_nonempty():
    module = _load_script()
    images = module.render_all()
    assert set(images) == {name for name, *_ in module._DEMO_CASES}
    for img in images.values():
        assert img.width == module.WIDTH and img.height == module.HEIGHT


def test_script_main_passes_against_freshly_written_baseline(tmp_path, monkeypatch):
    module = _load_script()
    monkeypatch.setattr(module, "BASELINE_DIR", tmp_path)
    assert module.main() == 0  # ilk çalışma: baseline yazar
    assert module.main() == 0  # ikinci çalışma: aynı görüntüler, fark olmamalı


def test_camera_view_matrix_is_orthonormal_rotation_part():
    cam = Camera(eye=(5.0, -5.0, 5.0), target=(0.0, 0.0, 0.0))
    m = cam.view_matrix()
    # Üst-sol 3x3 blok bir rotasyon matrisi olmalı: satırlar birim uzunlukta.
    for row in range(3):
        length_sq = sum(m[row][c] ** 2 for c in range(3))
        assert abs(length_sq - 1.0) < 1e-6
