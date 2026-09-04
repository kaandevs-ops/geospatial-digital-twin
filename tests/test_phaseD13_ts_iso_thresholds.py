"""
Roadmap V3 - Faz D13: Building Reconstruction - Gerçek TS/ISO Sayısal Eşikleri
================================================================================

Bu test dosyası iki şeyi doğrular:

1. Her eşik tablosunun (`MIN_ROOM_AREA_M2`, `MIN_CORRIDOR_WIDTH_M`,
   `MIN_WINDOW_WALL_RATIO`, `FIRE_ESCAPE_MIN_FLOORS`) artık belgelenmiş bir
   kaynağa (`THRESHOLD_SOURCES`) sahip olduğu - "basitleştirilmiş, kaynaksız"
   yer tutucu değil.
2. Eşiklerin numerik güncellemesi sonrasında `check_compliance()`
   fonksiyonlarının davranışı (regresyon) hâlâ beklenen yönde çalışıyor -
   eski Faz A3 testleri (`test_a3_building_reconstruction_deepening.py`)
   ile aynı senaryolar burada yeniden doğrulanır.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.building_reconstruction.room_generator import (
    MIN_CORRIDOR_WIDTH_M,
    MIN_ROOM_AREA_M2,
    THRESHOLD_SOURCES as ROOM_THRESHOLD_SOURCES,
    Room,
    RoomGenerator,
    RoomType,
)
from harita.building_reconstruction.facade_generator import (
    FIRE_ESCAPE_MIN_FLOORS,
    MIN_WINDOW_WALL_RATIO,
    THRESHOLD_SOURCES as FACADE_THRESHOLD_SOURCES,
    Facade,
    FacadeGenerator,
    FacadeMaterial,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.material_engine import ProceduralMaterials


def _square(size: float) -> Polygon:
    return Polygon([
        Point2D(0.0, 0.0), Point2D(size, 0.0),
        Point2D(size, size), Point2D(0.0, size),
    ])


class TestThresholdSourcesDocumented:
    def test_room_threshold_sources_nonempty_and_cover_used_codes(self):
        assert ROOM_THRESHOLD_SOURCES, "oda eşikleri için kaynak künyesi boş olamaz"
        for code in ("PAİY-27", "ISO 21542", "BYKHY"):
            assert code in ROOM_THRESHOLD_SOURCES
            assert len(ROOM_THRESHOLD_SOURCES[code]) > 10

    def test_facade_threshold_sources_nonempty_and_cover_used_codes(self):
        assert FACADE_THRESHOLD_SOURCES
        for code in ("PAİY-8", "TS 825", "BYKHY"):
            assert code in FACADE_THRESHOLD_SOURCES
            assert len(FACADE_THRESHOLD_SOURCES[code]) > 10

    def test_all_room_types_have_documented_minimum_area(self):
        for room_type in RoomType:
            assert room_type.value in MIN_ROOM_AREA_M2, (
                f"{room_type.value} icin belgelenmis asgari alan eksik"
            )

    def test_corridor_width_matches_iso_21542_accessible_route(self):
        # ISO 21542:2011 Bolum 10: erisilebilir yuruyus yolu asgari 1200mm.
        assert MIN_CORRIDOR_WIDTH_M == 1.2

    def test_fire_escape_threshold_is_positive_and_documented(self):
        assert FIRE_ESCAPE_MIN_FLOORS >= 1
        assert "BYKHY" in FACADE_THRESHOLD_SOURCES


class TestRoomComplianceRegression:
    def test_tiny_room_flagged_below_minimum(self):
        tiny = Room(polygon=_square(1.0), room_type=RoomType.SALON.value, room_id=0)
        report = RoomGenerator.check_compliance([tiny])
        assert not report.is_compliant
        assert report.violation_count == 1
        assert "PAİY-27" in report.issues[0].reason

    def test_compliant_room_passes(self):
        salon = Room(polygon=_square(4.0), room_type=RoomType.SALON.value, room_id=0)
        report = RoomGenerator.check_compliance([salon])
        assert report.is_compliant

    def test_narrow_corridor_flagged(self):
        narrow_corridor = Room(
            polygon=Polygon([
                Point2D(0.0, 0.0), Point2D(5.0, 0.0),
                Point2D(5.0, 0.8), Point2D(0.0, 0.8),
            ]),
            room_type=RoomType.KORIDOR.value,
            room_id=0,
        )
        report = RoomGenerator.check_compliance([narrow_corridor])
        assert not report.is_compliant
        assert "ISO 21542" in report.issues[0].reason


class TestFacadeComplianceRegression:
    def test_no_windows_fails_ratio(self):
        pbr = ProceduralMaterials.create(FacadeMaterial.BETON.value, variation_seed=1)
        facade = Facade(material=FacadeMaterial.BETON, pbr_material=pbr, windows=[], mesh=None)
        polygon = _square(10.0)
        report = FacadeGenerator.check_compliance(
            facade, polygon, floor_height=3.0, floor_count=1,
            building_type="apartman",
        )
        assert report.window_wall_ratio == 0.0
        assert not report.meets_window_ratio
        assert any("PAİY-8" in issue for issue in report.issues)

    def test_tall_building_without_second_egress_flagged(self):
        pbr = ProceduralMaterials.create(FacadeMaterial.BETON.value, variation_seed=1)
        facade = FacadeGenerator.generate(
            _square(10.0), "apartman", base_z=0.0, floor_height=3.0, seed=1,
        )
        report = FacadeGenerator.check_compliance(
            facade, _square(10.0), floor_height=3.0,
            floor_count=FIRE_ESCAPE_MIN_FLOORS, building_type="apartman",
            has_second_egress=False,
        )
        assert report.requires_fire_escape
        assert any("BYKHY" in issue for issue in report.issues)

    def test_tall_building_with_second_egress_not_flagged_for_escape(self):
        facade = FacadeGenerator.generate(
            _square(10.0), "apartman", base_z=0.0, floor_height=3.0, seed=1,
        )
        report = FacadeGenerator.check_compliance(
            facade, _square(10.0), floor_height=3.0,
            floor_count=FIRE_ESCAPE_MIN_FLOORS, building_type="apartman",
            has_second_egress=True,
        )
        assert report.requires_fire_escape
        assert not any("BYKHY" in issue for issue in report.issues)
