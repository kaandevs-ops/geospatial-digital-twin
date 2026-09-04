"""ROADMAP_V6 FAZ S5 — nokta bulutu-mesh karşılaştırması (Hausdorff/RMS)."""

from __future__ import annotations

import pytest
from harita.feature_survey.qc.pointcloud_mesh_comparison import (
    ComparisonError,
    compare_pointcloud_to_mesh,
)
from harita.mesh_engine import Mesh3D, Vertex3D


def _flat_plane_mesh(size: float = 10.0) -> Mesh3D:
    """Z=0 düzleminde basit bir kare düzlem, 2 üçgen."""
    verts = [
        Vertex3D(0.0, 0.0, 0.0),
        Vertex3D(size, 0.0, 0.0),
        Vertex3D(size, size, 0.0),
        Vertex3D(0.0, size, 0.0),
    ]
    tris = [(0, 1, 2), (0, 2, 3)]
    return Mesh3D(vertices=verts, triangles=tris, name="plane")


def test_pointcloud_on_mesh_surface_has_near_zero_distance():
    mesh = _flat_plane_mesh(10.0)
    # Nokta bulutu tam düzlem üzerinde (Z=0)
    points = [(float(i), float(j), 0.0) for i in range(11) for j in range(11)]
    report = compare_pointcloud_to_mesh(points, mesh)
    # RMS mesafesi kesin nokta-üçgen izdüşümünden gelir -> gerçekten sıfıra yakın
    assert report.rms_distance_m < 1e-6
    # Hausdorff, ızgara-örneklemesi tabanlı bir yaklaşıklamadır (bkz. modül
    # docstring'i) — bulut nokta aralığıyla (1m) aynı mertebede bir üst
    # sınırı olması beklenir, sıfır değil.
    assert report.hausdorff_distance_m < 1.0


def test_pointcloud_offset_from_mesh_reports_known_distance():
    mesh = _flat_plane_mesh(10.0)
    offset = 0.75
    points = [(float(i), float(j), offset) for i in range(11) for j in range(11)]
    report = compare_pointcloud_to_mesh(points, mesh)
    assert report.rms_distance_m == pytest.approx(offset, abs=1e-6)


def test_empty_pointcloud_rejected():
    mesh = _flat_plane_mesh(10.0)
    with pytest.raises(ComparisonError):
        compare_pointcloud_to_mesh([], mesh)


def test_empty_mesh_rejected():
    mesh = Mesh3D(vertices=[], triangles=[], name="empty")
    with pytest.raises(ComparisonError):
        compare_pointcloud_to_mesh([(0.0, 0.0, 0.0)], mesh)
