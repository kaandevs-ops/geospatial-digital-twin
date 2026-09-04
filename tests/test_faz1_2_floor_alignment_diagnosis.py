"""FAZ 1.2 — "katlar tam çıkmıyor" kök neden teşhisi.

`quality_metrics.FloorAlignmentAnalyzer`'ı gerçek
`ProceduralBuildingGenerator` çıktısı üzerinde çalıştırır. Bu, roadmap'in
"kök neden analizi yapılmalı, net teşhis edilmeden düzeltme yapılmamalı"
maddesinin karşılığıdır: önce ölçüyoruz, sonuca göre düzeltme kararı
sonraki adımda verilir.

Bulgular (bu test dosyasının yazıldığı an itibarıyla, iki düzeltme sonrası):
    - Kat TABANLARININ Z konumu (base_z) tam olarak beklenen değerde
      (0mm hata). "Katlar tam çıkmıyor" hatası Z ekseninde yok.
    - İlk ölçümde katlar arası duvar XY hizalamasında ~50mm'lik görünen
      bir sapma tespit edilmişti. Kök neden incelemesi bunun GERÇEK bir
      geometri kayması OLMADIĞINI ortaya çıkardı: giriş kapısının u-ekseni
      sınırları duvar grid'inde tüm yükseklik boyunca ekstra bölünme
      noktaları yaratıyor (bkz. `WallOpeningMeshBuilder`), bu da zemin
      katta (kapı olan) üst kattan (kapı olmayan) farklı sayıda/konumda
      vertex oluşturuyordu. Ölçüm yöntemi "en yakın nokta" yerine
      "duvar taban çizgisi bounding-box" karşılaştırmasına çevrildi
      (`FloorAlignmentAnalyzer`), gerçek sapma artık doğru şekilde 0mm
      ölçülüyor. Ayrıca giriş kapısına çok yakın/çakışan pencereler artık
      zemin katta otomatik elenip mimari olarak daha tutarlı bir sonuç
      üretiyor (`FacadeGenerator`, `door_clearance` mantığı).
"""

from __future__ import annotations

import pytest
from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import (
    BuildingType,
    ProceduralBuildingGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine.quality_metrics import FloorAlignmentAnalyzer, MeshQualityAnalyzer


def _make_rect_footprint(w: float, d: float, floor_count: int, total_height: float) -> Footprint:
    ring = [Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)]
    return Footprint(polygon=Polygon(ring), floor_count=floor_count, height_m=total_height)


class TestRealBuildingFloorAlignment:
    def test_floor_meshes_are_exposed_for_diagnostics(self):
        fp = _make_rect_footprint(12.0, 20.0, floor_count=8, total_height=24.0)
        building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.APARTMAN)
        assert building.facade.floor_meshes is not None
        assert len(building.facade.floor_meshes) == len(building.floors)

    def test_floor_base_z_has_zero_error(self):
        """Kat TABANLARININ Z konumu tam hizalı (roadmap 1.2 kök neden #1 çözülmüş)."""
        fp = _make_rect_footprint(12.0, 20.0, floor_count=8, total_height=24.0)
        building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.APARTMAN)
        heights = [f.height_m for f in building.floors]
        report = FloorAlignmentAnalyzer.check(
            heights, building.facade.floor_meshes, tolerance_m=0.005
        )
        assert report.max_z_error_m == pytest.approx(0.0, abs=1e-9)

    def test_wall_xy_alignment_is_within_tolerance(self):
        """Kök neden düzeltmesi sonrası: kat arası duvar hattı (bounding-box
        bazlı) tam hizalı - önceki ~50mm sapma bir ölçüm artifaktıydı, gerçek
        bir geometri kayması değildi (bkz. modül docstring'i)."""
        fp = _make_rect_footprint(12.0, 20.0, floor_count=8, total_height=24.0)
        building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.APARTMAN)
        heights = [f.height_m for f in building.floors]
        report = FloorAlignmentAnalyzer.check(
            heights, building.facade.floor_meshes, tolerance_m=0.005
        )
        assert report.within_tolerance is True
        assert report.max_wall_xy_error_m == pytest.approx(0.0, abs=1e-6)

    def test_facade_mesh_quality_report_runs_on_real_output(self):
        fp = _make_rect_footprint(12.0, 20.0, floor_count=8, total_height=24.0)
        building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.APARTMAN)
        report = MeshQualityAnalyzer.analyze(building.facade.mesh)
        # Pencere/kapı boşlukları gerçek delikler olduğu için watertight
        # OLMAMASI beklenir - bu bir hata değil, tasarım gereği.
        assert report.non_manifold_edge_count == 0
        assert report.degenerate_triangle_count == 0
