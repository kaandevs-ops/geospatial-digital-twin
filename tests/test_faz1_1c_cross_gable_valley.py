"""FAZ 1.1 son açık madde — `cross_gable` gerçek vadi (valley) geometrisi.

Önceki sürüm iki tam gable mesh'ini olduğu gibi üst üste bindiriyordu (kod
yorumunda itiraf edilen "kaba yaklaşık"). Bu testler yeni implementasyonun
iki kanadın eğim yüzeyleri arasında GERÇEK bir kesişim (min-yükseklik vadi
çizgisi) ürettiğini, eski protrüzyon (bir ridge'in diğer çatının içinden
geçmesi) sorununun artık olmadığını doğrular.

Bkz. FAZ0_DENETIM_RAPORU.md "Açık Bulgular" madde 1.
"""

from __future__ import annotations

import math

import pytest
from harita.building_reconstruction.roof_generator import RoofGenerator
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine.quality_metrics import MeshQualityAnalyzer


def _square_footprint(size: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(size, 0), Point2D(size, size), Point2D(0, size)])


def _rect_footprint(w: float, d: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


class TestCrossGableValleyBasics:
    def test_produces_valid_mesh(self):
        mesh = RoofGenerator.cross_gable(_square_footprint(20.0), base_z=6.0, pitch_deg=30.0)
        assert mesh.vertex_count() > 0
        assert mesh.triangle_count() > 0

    def test_quality_report_runs_without_crashing(self):
        mesh = RoofGenerator.cross_gable(_square_footprint(20.0), base_z=6.0, pitch_deg=30.0)
        report = MeshQualityAnalyzer.analyze(mesh)
        assert report.degenerate_triangle_count == 0

    def test_rectangular_footprint_also_works(self):
        mesh = RoofGenerator.cross_gable(_rect_footprint(30.0, 18.0), base_z=3.0, pitch_deg=25.0)
        assert mesh.vertex_count() > 0
        assert mesh.triangle_count() > 0


class TestCrossGableValleyIsGeometricallyCorrect:
    """Vadi noktalarının analitik olarak min(yükseklik_a, yükseklik_b)
    formülüne uyduğunu, dolayısıyla eski "protrüzyon" hatasının artık
    olmadığını doğrular."""

    def _expected_heights(self, size: float, base_z: float, pitch_deg: float):
        half_depth = size * 0.15
        slope = math.tan(math.radians(pitch_deg))
        ridge_h = half_depth * slope
        return half_depth, ridge_h, slope

    def test_center_point_equals_min_of_both_ridge_heights_for_equal_wings(self):
        """Kare footprint'te iki kanat simetrik (aynı genişlik) -> merkez
        noktasında (ridge kesişimi) her iki kanadın tavan yüksekliği eşit
        olmalı ve bu, çatının en yüksek noktası olmalı (klasik simetrik
        çapraz-vadi/hip deseni)."""
        size, base_z, pitch = 20.0, 6.0, 30.0
        mesh = RoofGenerator.cross_gable(_square_footprint(size), base_z=base_z, pitch_deg=pitch)
        half_depth, ridge_h, _ = self._expected_heights(size, base_z, pitch)

        centroid = (size / 2.0, size / 2.0)
        # Merkez nokta mesh'te tam olarak bulunmalı (add_cell merkez fan noktası).
        center_matches = [
            v
            for v in mesh.vertices
            if abs(v.x - centroid[0]) < 1e-6 and abs(v.y - centroid[1]) < 1e-6
        ]
        assert center_matches, "Merkez (vadi kesişim) noktası mesh'te bulunamadı"
        assert center_matches[0].z == pytest.approx(base_z + ridge_h, abs=1e-6)

        max_z = max(v.z for v in mesh.vertices)
        assert max_z == pytest.approx(base_z + ridge_h, abs=1e-6), (
            "Çatının en yüksek noktası merkez vadi kesişimi olmalı - eğer "
            "daha yüksek bir nokta varsa bu, eski protrüzyon hatasının geri "
            "geldiği anlamına gelir."
        )

    def test_overlap_cell_corner_is_at_eave_level_for_equal_wings(self):
        """Kesişim hücresinin köşeleri (her iki kanadın da eğiminin saçak
        seviyesine indiği nokta) base_z'ye eşit olmalı - vadi burada başlar."""
        size, base_z, pitch = 20.0, 6.0, 30.0
        mesh = RoofGenerator.cross_gable(_square_footprint(size), base_z=base_z, pitch_deg=pitch)
        half_depth, _, _ = self._expected_heights(size, base_z, pitch)
        centroid = (size / 2.0, size / 2.0)
        corner = (centroid[0] - half_depth, centroid[1] - half_depth)
        matches = [
            v for v in mesh.vertices if abs(v.x - corner[0]) < 1e-6 and abs(v.y - corner[1]) < 1e-6
        ]
        assert matches
        assert matches[0].z == pytest.approx(base_z, abs=1e-6)

    def test_no_vertex_exceeds_the_higher_individual_ridge_height(self):
        """Eski implementasyonda (basit merge) bir kanadın ridge'i diğer
        kanadın eğim yüzeyinin İÇİNDEN GEÇEBİLİYORDU (protrüzyon). Yeni
        implementasyonda hiçbir nokta, iki kanadın kendi bağımsız ridge
        yüksekliklerinin en büyüğünü aşamaz."""
        w, d, base_z, pitch = 40.0, 16.0, 4.0, 35.0
        mesh = RoofGenerator.cross_gable(_rect_footprint(w, d), base_z=base_z, pitch_deg=pitch)
        half_depth_a = d * 0.15
        half_depth_b = w * 0.15
        slope = math.tan(math.radians(pitch))
        ridge_h_a = half_depth_a * slope
        ridge_h_b = half_depth_b * slope
        expected_max = base_z + max(ridge_h_a, ridge_h_b)
        max_z = max(v.z for v in mesh.vertices)
        assert max_z <= expected_max + 1e-6

    def test_valley_is_strictly_lower_than_the_taller_wings_ridge(self):
        """Asimetrik (dikdörtgen) footprint'te vadi kesişim noktası, iki
        kanadın kendi ridge yüksekliklerinin daha KISA olanına eşit olmalı
        (min kuralı) - daha uzun kanadın ridge'i vadi boyunca görünmemeli."""
        w, d, base_z, pitch = 40.0, 16.0, 4.0, 35.0
        mesh = RoofGenerator.cross_gable(_rect_footprint(w, d), base_z=base_z, pitch_deg=pitch)
        half_depth_a = d * 0.15
        half_depth_b = w * 0.15
        slope = math.tan(math.radians(pitch))
        ridge_h_a = half_depth_a * slope
        ridge_h_b = half_depth_b * slope
        centroid = (w / 2.0, d / 2.0)
        center_matches = [
            v
            for v in mesh.vertices
            if abs(v.x - centroid[0]) < 1e-6 and abs(v.y - centroid[1]) < 1e-6
        ]
        assert center_matches
        expected = base_z + min(ridge_h_a, ridge_h_b)
        assert center_matches[0].z == pytest.approx(expected, abs=1e-6)
        assert ridge_h_a != ridge_h_b  # test senaryosunun anlamlı olduğunu doğrula
