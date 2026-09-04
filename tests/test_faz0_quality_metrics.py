"""FAZ 0 — mesh_engine.quality_metrics testleri.

Kapsam: watertight kutu, delikli mesh, dejenere üçgen, normal
tutarlılığı, kat hizalama toleransı (mm), çoklu-mesh batch raporu.
"""

from __future__ import annotations

import math

import pytest

from harita.mesh_engine import Mesh3D, MeshBuilder, Vertex3D
from harita.mesh_engine.quality_metrics import (
    BatchQualityAnalyzer,
    FloorAlignmentAnalyzer,
    MeshQualityAnalyzer,
)


class TestMeshQualityAnalyzer:
    def test_watertight_box_has_no_boundary_or_nonmanifold_edges(self):
        box = MeshBuilder.build_box(4.0, 3.0, 2.5, name="box")
        report = MeshQualityAnalyzer.analyze(box)
        assert report.is_manifold is True
        assert report.is_watertight is True
        assert report.non_manifold_edge_count == 0
        assert report.boundary_edge_count == 0
        assert report.degenerate_triangle_count == 0
        assert report.vertex_count == box.vertex_count()
        assert report.triangle_count == box.triangle_count()

    def test_open_mesh_reports_boundary_edges(self):
        # Sadece taban üçgeni: kapalı olmayan tek bir üçgen -> 3 boundary kenar
        mesh = Mesh3D(
            vertices=[Vertex3D(0, 0, 0), Vertex3D(1, 0, 0), Vertex3D(0, 1, 0)],
            triangles=[(0, 1, 2)],
            name="open_triangle",
        )
        report = MeshQualityAnalyzer.analyze(mesh)
        assert report.boundary_edge_count == 3
        assert report.is_watertight is False
        assert report.is_manifold is True  # her kenar sadece 1 üçgene ait, 2'den fazla değil

    def test_degenerate_triangle_is_detected(self):
        mesh = Mesh3D(
            vertices=[Vertex3D(0, 0, 0), Vertex3D(1, 0, 0), Vertex3D(2, 0, 0)],  # doğrusal -> sıfır alan
            triangles=[(0, 1, 2)],
            name="degenerate",
        )
        report = MeshQualityAnalyzer.analyze(mesh)
        assert report.degenerate_triangle_count == 1

    def test_nonmanifold_edge_detected_when_edge_shared_by_three_triangles(self):
        # Kenar (0,1)'i paylaşan 3 üçgen -> non-manifold
        vertices = [
            Vertex3D(0, 0, 0), Vertex3D(1, 0, 0),
            Vertex3D(0, 1, 0), Vertex3D(0, -1, 0), Vertex3D(-1, 0, 0),
        ]
        triangles = [(0, 1, 2), (0, 1, 3), (1, 0, 4)]
        mesh = Mesh3D(vertices=vertices, triangles=triangles, name="fan")
        report = MeshQualityAnalyzer.analyze(mesh)
        assert report.non_manifold_edge_count == 1
        assert report.is_manifold is False

    def test_normal_consistency_is_high_for_flat_coplanar_quad(self):
        # İki üçgenden oluşan düz bir dörtgen: normaller tamamen aynı yönde olmalı
        mesh = MeshBuilder.build_flat_quad(2.0, 2.0)
        report = MeshQualityAnalyzer.analyze(mesh)
        assert report.normal_consistency_ratio == pytest.approx(1.0)


class TestFloorAlignmentAnalyzer:
    def test_perfect_alignment_within_tolerance(self):
        heights = [3.0, 3.0, 3.0]
        floor_meshes = [
            MeshBuilder.build_box(5.0, 5.0, h, base_z=sum(heights[:i]), name=f"floor{i}")
            for i, h in enumerate(heights)
        ]
        report = FloorAlignmentAnalyzer.check(heights, floor_meshes, tolerance_m=0.005)
        assert report.within_tolerance is True
        assert report.max_z_error_m == pytest.approx(0.0, abs=1e-9)

    def test_misaligned_floor_exceeds_tolerance(self):
        heights = [3.0, 3.0]
        # İkinci kat kasıtlı olarak 2cm yukarı kaymış (üretim hatası simülasyonu)
        floor_meshes = [
            MeshBuilder.build_box(5.0, 5.0, 3.0, base_z=0.0, name="floor0"),
            MeshBuilder.build_box(5.0, 5.0, 3.0, base_z=3.02, name="floor1"),
        ]
        report = FloorAlignmentAnalyzer.check(heights, floor_meshes, tolerance_m=0.005)
        assert report.within_tolerance is False
        assert report.max_z_error_m == pytest.approx(0.02, abs=1e-9)

    def test_wall_xy_misalignment_between_floors_detected(self):
        heights = [3.0, 3.0]
        floor_meshes = [
            MeshBuilder.build_box(5.0, 5.0, 3.0, center_x=0.0, base_z=0.0, name="floor0"),
            # Üstteki kat 0.5m öteye kaymış (setback değil, hatalı offset)
            MeshBuilder.build_box(5.0, 5.0, 3.0, center_x=0.5, base_z=3.0, name="floor1"),
        ]
        report = FloorAlignmentAnalyzer.check(heights, floor_meshes, tolerance_m=0.005)
        assert report.within_tolerance is False
        assert report.max_wall_xy_error_m == pytest.approx(0.5, abs=1e-6)

    def test_no_actual_meshes_falls_back_to_expected_positions(self):
        heights = [3.0, 3.5, 3.0]
        report = FloorAlignmentAnalyzer.check(heights)
        assert report.within_tolerance is True
        assert report.expected_base_z == [0.0, 3.0, 6.5]


class TestBatchQualityAnalyzer:
    def test_batch_report_aggregates_multiple_meshes(self):
        meshes = [
            MeshBuilder.build_box(4.0, 4.0, 3.0, name="b1"),
            MeshBuilder.build_box(6.0, 5.0, 3.0, name="b2"),
        ]
        batch = BatchQualityAnalyzer.analyze_all(meshes)
        assert len(batch.reports) == 2
        assert batch.watertight_ratio == pytest.approx(1.0)
        assert batch.total_vertices == sum(m.vertex_count() for m in meshes)
        md = batch.to_markdown()
        assert "b1" in md and "b2" in md
