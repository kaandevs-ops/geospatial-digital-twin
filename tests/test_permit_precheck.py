from harita.building_reconstruction import (
    BuildingType,
    Footprint,
    PermitVerdict,
    ProceduralBuildingGenerator,
    precheck_building,
)
from harita.core_engine.geometry_engine import Point2D, Polygon


def _make_building(floor_count=5):
    poly = Polygon(points=[Point2D(0, 0), Point2D(10, 0), Point2D(10, 8), Point2D(0, 8)])
    footprint = Footprint(polygon=poly)
    return ProceduralBuildingGenerator.generate(
        footprint,
        building_type=BuildingType.APARTMAN,
        floor_count=floor_count,
        seed=1,
    )


def test_no_plot_no_plan_defaults_to_likely_pass_or_revision():
    b = _make_building(floor_count=3)
    report = precheck_building(b)
    assert report.verdict in (PermitVerdict.LIKELY_PASS, PermitVerdict.NEEDS_REVISION)
    codes = {i.code for i in report.items}
    assert {
        "window_wall_ratio",
        "fire_escape_route",
        "setback_distance",
        "structural_plausibility",
    } <= codes


def test_setback_violation_fails():
    b = _make_building(floor_count=3)
    plot = Polygon(points=[Point2D(-2, -2), Point2D(12, -2), Point2D(12, 10), Point2D(-2, 10)])
    report = precheck_building(b, plot_polygon=plot, min_setback_m=3.0)
    setback_item = next(i for i in report.items if i.code == "setback_distance")
    assert setback_item.passed is False
    assert report.verdict == PermitVerdict.LIKELY_FAIL


def test_setback_satisfied_passes():
    b = _make_building(floor_count=3)
    plot = Polygon(points=[Point2D(-5, -5), Point2D(15, -5), Point2D(15, 13), Point2D(-5, 13)])
    report = precheck_building(b, plot_polygon=plot, min_setback_m=3.0)
    setback_item = next(i for i in report.items if i.code == "setback_distance")
    assert setback_item.passed is True


def test_max_floor_count_violation():
    b = _make_building(floor_count=6)
    report = precheck_building(b, max_floor_count=4)
    item = next(i for i in report.items if i.code == "max_floor_count")
    assert item.passed is False
    assert report.verdict == PermitVerdict.LIKELY_FAIL


def test_max_height_violation():
    b = _make_building(floor_count=6)
    report = precheck_building(b, max_height_m=1.0)
    item = next(i for i in report.items if i.code == "max_height")
    assert item.passed is False


def test_fire_escape_flagged_for_tall_building():
    b = _make_building(floor_count=5)
    report = precheck_building(b)
    item = next(i for i in report.items if i.code == "fire_escape_route")
    assert item.value == 5.0


def test_report_summary_line_contains_verdict():
    b = _make_building(floor_count=2)
    report = precheck_building(b)
    assert report.verdict.value in report.summary_line()
