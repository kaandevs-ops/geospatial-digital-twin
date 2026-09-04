"""Roadmap V2 - A3 (Building Reconstruction derinleştirme) testleri.

Kapsam:
    - FootprintShape sınıflandırması (rectangle/L/T/U/complex)
    - Konkav tabanlarda çatı tahmininin `hip`'e düşmesi
    - FacadeGenerator parametrik uygunluk denetimi (pencere/duvar oranı +
      kaçış yolu kuralı)
    - RoomGenerator TS/ISO benzeri asgari alan/koridor genişliği denetimi
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction import (
    Footprint, FootprintParser, FootprintShape, RoofTypeGuess,
    FacadeGenerator, FacadeComplianceReport,
    RoomGenerator, RoomComplianceReport, RoomType,
)


def _rect(w: float, d: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


def _l_shape() -> Polygon:
    return Polygon([
        Point2D(0, 0), Point2D(10, 0), Point2D(10, 4),
        Point2D(5, 4), Point2D(5, 8), Point2D(0, 8),
    ])


def _t_shape() -> Polygon:
    return Polygon([
        Point2D(0, 4), Point2D(4, 4), Point2D(4, 0), Point2D(8, 0),
        Point2D(8, 4), Point2D(12, 4), Point2D(12, 8), Point2D(0, 8),
    ])


def _u_shape() -> Polygon:
    return Polygon([
        Point2D(0, 0), Point2D(3, 0), Point2D(3, 6), Point2D(7, 6),
        Point2D(7, 0), Point2D(10, 0), Point2D(10, 8), Point2D(0, 8),
    ])


# ------------------------------------------------------------------ #
# FootprintShape sınıflandırması
# ------------------------------------------------------------------ #

def test_rectangle_has_no_concave_vertices_and_is_classified_rectangle():
    poly = _rect(10, 6)
    assert FootprintParser.concave_vertex_count(poly) == 0
    assert FootprintParser.classify_shape(poly) == FootprintShape.RECTANGLE


def test_l_shape_classified_correctly():
    poly = _l_shape()
    assert FootprintParser.concave_vertex_count(poly) == 1
    assert FootprintParser.classify_shape(poly) == FootprintShape.L_SHAPE


def test_t_shape_classified_correctly():
    poly = _t_shape()
    assert FootprintParser.concave_vertex_count(poly) == 2
    assert FootprintParser.classify_shape(poly) == FootprintShape.T_SHAPE


def test_u_shape_classified_correctly():
    poly = _u_shape()
    assert FootprintParser.concave_vertex_count(poly) == 2
    assert FootprintParser.classify_shape(poly) == FootprintShape.U_SHAPE


def test_footprint_dataclass_exposes_shape_field():
    fp = Footprint(polygon=_l_shape())
    assert fp.shape == FootprintShape.L_SHAPE.value
    assert fp.concave_vertex_count == 1


# ------------------------------------------------------------------ #
# Konkav taban -> güvenli hip çatı tahmini
# ------------------------------------------------------------------ #

def test_concave_footprint_never_guesses_gable_or_pyramid():
    for poly in (_l_shape(), _t_shape(), _u_shape()):
        fp = Footprint(polygon=poly)
        assert fp.roof_type == RoofTypeGuess.HIP.value


def test_rectangle_with_high_aspect_ratio_still_guesses_gable():
    fp = Footprint(polygon=_rect(30, 6))
    assert fp.roof_type == RoofTypeGuess.GABLE.value


# ------------------------------------------------------------------ #
# Facade uygunluk denetimi
# ------------------------------------------------------------------ #

def test_facade_compliance_flags_fire_escape_for_tall_building():
    poly = _rect(10, 6)
    facade = FacadeGenerator.generate(poly, "apartman", base_z=0.0, floor_height=3.0, seed=1)
    report = FacadeGenerator.check_compliance(
        facade, poly, floor_height=3.0, floor_count=6, building_type="apartman",
    )
    assert isinstance(report, FacadeComplianceReport)
    assert report.requires_fire_escape is True
    assert any("kacis" in issue for issue in report.issues)


def test_facade_compliance_no_fire_escape_flag_when_provided():
    poly = _rect(10, 6)
    facade = FacadeGenerator.generate(poly, "apartman", base_z=0.0, floor_height=3.0, seed=1)
    report = FacadeGenerator.check_compliance(
        facade, poly, floor_height=3.0, floor_count=6, building_type="apartman",
        has_second_egress=True,
    )
    assert report.requires_fire_escape is True
    assert not any("kacis" in issue for issue in report.issues)


def test_facade_compliance_low_rise_building_no_fire_escape_requirement():
    poly = _rect(10, 6)
    facade = FacadeGenerator.generate(poly, "apartman", base_z=0.0, floor_height=3.0, seed=1)
    report = FacadeGenerator.check_compliance(
        facade, poly, floor_height=3.0, floor_count=2, building_type="apartman",
    )
    assert report.requires_fire_escape is False


def test_facade_compliance_window_ratio_below_minimum_flagged():
    poly = _rect(10, 6)
    # Depo tipi çok az pencere üretir (window generator kuralları); yine de
    # oranı manuel olarak asgarinin altına düşürmek için sentetik bir facade
    # kuruyoruz (window listesi boş).
    facade = FacadeGenerator.generate(
        poly, "depo", base_z=0.0, floor_height=3.0, seed=1, build_mesh=False,
    )
    facade.windows = []  # pencere yok -> oran 0
    report = FacadeGenerator.check_compliance(
        facade, poly, floor_height=3.0, floor_count=1, building_type="depo",
    )
    assert report.meets_window_ratio is False
    assert report.window_wall_ratio == 0.0
    assert not report.is_compliant


# ------------------------------------------------------------------ #
# Oda alanı / koridor genişliği uygunluk denetimi
# ------------------------------------------------------------------ #

def test_room_compliance_report_type():
    rooms = RoomGenerator.generate(_rect(12, 10), building_type="apartman", seed=3)
    report = RoomGenerator.check_compliance(rooms)
    assert isinstance(report, RoomComplianceReport)
    assert report.total_rooms == len(rooms)


def test_room_compliance_flags_undersized_room():
    from harita.building_reconstruction.room_generator import Room

    tiny = Room(
        polygon=Polygon([Point2D(0, 0), Point2D(1, 0), Point2D(1, 1), Point2D(0, 1)]),
        room_type=RoomType.SALON.value,
        room_id=0,
    )
    report = RoomGenerator.check_compliance([tiny])
    assert not report.is_compliant
    assert report.violation_count == 1
    assert "asgari" in report.issues[0].reason


def test_room_compliance_flags_narrow_corridor():
    from harita.building_reconstruction.room_generator import Room

    narrow_corridor = Room(
        polygon=Polygon([Point2D(0, 0), Point2D(5, 0), Point2D(5, 0.5), Point2D(0, 0.5)]),
        room_type=RoomType.KORIDOR.value,
        room_id=0,
    )
    report = RoomGenerator.check_compliance([narrow_corridor])
    assert not report.is_compliant
    assert any("koridor" in issue.reason for issue in report.issues)


def test_room_compliance_passes_for_generous_layout():
    from harita.building_reconstruction.room_generator import Room

    salon = Room(
        polygon=Polygon([Point2D(0, 0), Point2D(5, 0), Point2D(5, 4), Point2D(0, 4)]),
        room_type=RoomType.SALON.value,
        room_id=0,
    )
    report = RoomGenerator.check_compliance([salon])
    assert report.is_compliant
    assert report.violation_count == 0
