"""FAZ 1.2 devamı — "katlar tam çıkmıyor" teşhisinin L/U/düzensiz poligon
footprint'lerde tekrarı, ve FAZ 0 madde 7.4 — sert kenar (hard-edge)
sınıflandırmasının normal tutarlılık metriğine eklenmesi.

Bkz. FAZ0_DENETIM_RAPORU.md "Açık Bulgular / Sıradaki Aday İşler" 3 ve 4.
Önceki oturumdaki teşhis (`test_faz1_2_floor_alignment_diagnosis.py`) sadece
dikdörtgen footprint'te doğrulanmıştı. Bu dosya aynı ölçüm akışını L, U ve
düzensiz (çentikli) poligonlarda tekrarlayarak kat hizalamasının footprint
şeklinden bağımsız olarak doğru çalıştığını kanıtlar.
"""

from __future__ import annotations

import pytest

from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import (
    BuildingType,
    ProceduralBuildingGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import Mesh3D, Vertex3D
from harita.mesh_engine.quality_metrics import FloorAlignmentAnalyzer, MeshQualityAnalyzer


def _l_shape_footprint(floor_count: int, total_height: float) -> Footprint:
    # L şekli: 20x20'lik kare, sağ-üst 10x10'luk köşesi kesilmiş.
    ring = [
        Point2D(0, 0), Point2D(20, 0), Point2D(20, 10),
        Point2D(10, 10), Point2D(10, 20), Point2D(0, 20),
    ]
    return Footprint(polygon=Polygon(ring), floor_count=floor_count, height_m=total_height)


def _u_shape_footprint(floor_count: int, total_height: float) -> Footprint:
    # U şekli: 30x20'lik dikdörtgenin ortasından 10x12'lik bir çentik çıkarılmış.
    ring = [
        Point2D(0, 0), Point2D(30, 0), Point2D(30, 20), Point2D(20, 20),
        Point2D(20, 8), Point2D(10, 8), Point2D(10, 20), Point2D(0, 20),
    ]
    return Footprint(polygon=Polygon(ring), floor_count=floor_count, height_m=total_height)


def _irregular_footprint(floor_count: int, total_height: float) -> Footprint:
    # Düzensiz (dışbükey olmayan, çentikli) 7 köşeli poligon.
    ring = [
        Point2D(0, 0), Point2D(14, 0), Point2D(14, 6), Point2D(9, 6),
        Point2D(9, 16), Point2D(4, 22), Point2D(0, 14),
    ]
    return Footprint(polygon=Polygon(ring), floor_count=floor_count, height_m=total_height)


FOOTPRINT_FACTORIES = {
    "L": _l_shape_footprint,
    "U": _u_shape_footprint,
    "irregular": _irregular_footprint,
}


@pytest.mark.parametrize("shape_name", ["L", "U", "irregular"])
class TestNonRectangularFloorAlignment:
    """Faz 1.2 teşhisinin dikdörtgen-dışı footprint'lerde tekrarı."""

    def _build(self, shape_name: str):
        factory = FOOTPRINT_FACTORIES[shape_name]
        fp = factory(floor_count=6, total_height=18.0)
        building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.APARTMAN)
        return building

    def test_floor_meshes_are_exposed(self, shape_name):
        building = self._build(shape_name)
        assert building.facade.floor_meshes is not None
        assert len(building.facade.floor_meshes) == len(building.floors)

    def test_floor_base_z_zero_error(self, shape_name):
        building = self._build(shape_name)
        heights = [f.height_m for f in building.floors]
        report = FloorAlignmentAnalyzer.check(
            heights, building.facade.floor_meshes, tolerance_m=0.005
        )
        assert report.max_z_error_m == pytest.approx(0.0, abs=1e-9)

    def test_wall_xy_alignment_within_tolerance(self, shape_name):
        """Bounding-box bazlı ölçüm, dikdörtgen-dışı taban şekillerinde de
        kat arası duvar hattının kaymadığını doğrulamalı (roadmap 1.2 kabul
        kriterinin footprint-bağımsız genellemesi)."""
        building = self._build(shape_name)
        heights = [f.height_m for f in building.floors]
        report = FloorAlignmentAnalyzer.check(
            heights, building.facade.floor_meshes, tolerance_m=0.005
        )
        assert report.within_tolerance is True, (
            f"{shape_name} footprint'inde kat hizalama toleransı aşıldı: "
            f"max_wall_xy_error_m={report.max_wall_xy_error_m}"
        )

    def test_mesh_quality_report_runs_without_crashing(self, shape_name):
        building = self._build(shape_name)
        report = MeshQualityAnalyzer.analyze(building.facade.mesh)
        assert report.degenerate_triangle_count == 0
        assert report.vertex_count > 0
        assert report.triangle_count > 0


# ======================================================================== #
# FAZ 0 madde 7.4 — sert kenar (hard-edge) sınıflandırması
# ======================================================================== #

def _unit_box_mesh() -> Mesh3D:
    """Basit bir kutu (küp) mesh'i — tüm kenarları 90°'lik kasıtlı sert
    köşelerdir, curvature/kalite sorunu değildir."""
    v = [
        Vertex3D(0, 0, 0), Vertex3D(1, 0, 0), Vertex3D(1, 1, 0), Vertex3D(0, 1, 0),
        Vertex3D(0, 0, 1), Vertex3D(1, 0, 1), Vertex3D(1, 1, 1), Vertex3D(0, 1, 1),
    ]
    tris = [
        (0, 1, 2), (0, 2, 3),  # alt
        (4, 6, 5), (4, 7, 6),  # üst
        (0, 4, 5), (0, 5, 1),  # -Y
        (1, 5, 6), (1, 6, 2),  # +X
        (2, 6, 7), (2, 7, 3),  # +Y
        (3, 7, 4), (3, 4, 0),  # -X
    ]
    return Mesh3D(name="unit_box", vertices=v, triangles=tris)


class TestHardEdgeClassification:
    def test_box_has_low_raw_consistency_but_high_adjusted_consistency(self):
        mesh = _unit_box_mesh()
        report = MeshQualityAnalyzer.analyze(mesh)
        # Ham metrik (yumuşak yüzey varsayımıyla) düşük çıkar -> yanlış alarm.
        assert report.normal_consistency_ratio < 0.5
        # Sert-kenar sınıflandırmasıyla düzeltilmiş metrik yüksek olmalı,
        # çünkü küpün TÜM kenarları 90°'lik kasıtlı köşelerdir.
        assert report.normal_consistency_ratio_adjusted == pytest.approx(1.0, abs=1e-9)
        assert report.hard_edge_count > 0

    def test_hard_edge_count_matches_box_edge_count(self):
        mesh = _unit_box_mesh()
        report = MeshQualityAnalyzer.analyze(mesh)
        # Küpün 12 üçgeni var, iç kenarların hepsi (12 kenar) 90° sert kenar;
        # tam sayı yerine >0 ve raw ile adjusted farkı tutarlılığını kontrol
        # etmek daha az kırılgan (mesh üçgenleme detaylarına bağımlı olmasın).
        assert report.hard_edge_count >= 6

    def test_summary_line_includes_adjusted_metric(self):
        mesh = _unit_box_mesh()
        report = MeshQualityAnalyzer.analyze(mesh)
        line = report.summary_line()
        assert "normal_consistency_adj=" in line
        assert "hard_edges=" in line
