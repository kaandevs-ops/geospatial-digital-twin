"""Phase 3 (Building Reconstruction) için birim testleri."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.building_reconstruction import (
    BalconyGenerator,
    BuildingType,
    BuildingTypeRules,
    DoorGenerator,
    ElevatorCoreGenerator,
    FacadeGenerator,
    FacadeMaterial,
    Footprint,
    FootprintParser,
    ProceduralBuildingGenerator,
    RoofGenerator,
    RoofType,
    RoofTypeGuess,
    RoomGenerator,
    RoomType,
    StairGenerator,
    WindowGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.core_engine.gis_core import GeoFeature


def _rect_polygon(w: float, d: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


# ------------------------------------------------------------------ #
# Footprint Parser
# ------------------------------------------------------------------ #


def test_footprint_parser_from_geofeature():
    feature = GeoFeature(
        geometry_type="Polygon",
        coordinates=[[[0, 0], [20, 0], [20, 12], [0, 12], [0, 0]]],
        properties={"building": "apartments", "building:levels": "5", "height": "16"},
    )
    fp = FootprintParser.parse(feature)
    assert fp.building_type == "apartments"
    assert fp.floor_count == 5
    assert fp.height_m == 16.0
    assert abs(fp.area_m2 - 240.0) < 1e-6
    assert fp.roof_type == RoofTypeGuess.FLAT.value


def test_footprint_orientation_and_aspect_ratio():
    poly = _rect_polygon(30, 10)
    fp = Footprint(polygon=poly)
    assert fp.aspect_ratio > 2.5
    assert 0.0 <= fp.orientation_deg < 180.0


def test_footprint_compactness_and_roof_guess_villa_like():
    poly = _rect_polygon(10, 10)
    fp = Footprint(polygon=poly, building_type="house")
    assert FootprintParser.compactness(poly) > 0.5
    guess = FootprintParser.guess_roof_type(fp)
    assert guess in (RoofTypeGuess.PYRAMID, RoofTypeGuess.HIP)


# ------------------------------------------------------------------ #
# Roof Generator
# ------------------------------------------------------------------ #


def test_roof_flat_generates_closed_prism():
    poly = _rect_polygon(10, 8)
    mesh = RoofGenerator.generate(poly, base_z=15.0, roof_type=RoofType.FLAT)
    assert mesh.vertex_count() == 8
    assert mesh.triangle_count() > 0


def test_roof_pyramid_apex_above_eave():
    poly = _rect_polygon(10, 10)
    mesh = RoofGenerator.generate(poly, base_z=10.0, roof_type=RoofType.PYRAMID, pitch_deg=35)
    zs = [v.z for v in mesh.vertices]
    assert max(zs) > 10.0
    assert min(zs) >= 10.0 - 1e-6


def test_roof_hip_on_rectangle_has_ridge():
    poly = _rect_polygon(20, 8)
    mesh = RoofGenerator.generate(poly, base_z=6.0, roof_type=RoofType.HIP, pitch_deg=25)
    assert mesh.vertex_count() == 6  # 4 eave + 2 ridge
    zs = [v.z for v in mesh.vertices]
    assert max(zs) > 6.0


def test_roof_gable_ridge_height_positive():
    poly = _rect_polygon(12, 6)
    mesh = RoofGenerator.generate(poly, base_z=9.0, roof_type=RoofType.GABLE, pitch_deg=30)
    assert max(v.z for v in mesh.vertices) > 9.0


def test_roof_sawtooth_multiple_segments():
    poly = _rect_polygon(20, 10)
    mesh = RoofGenerator.generate(poly, base_z=8.0, roof_type=RoofType.SAWTOOTH)
    assert mesh.triangle_count() > 0


def test_roof_dome_apex_above_eave_and_base_aligned():
    poly = _rect_polygon(10, 8)
    mesh = RoofGenerator.generate(poly, base_z=12.0, roof_type=RoofType.DOME, pitch_deg=45)
    zs = [v.z for v in mesh.vertices]
    assert min(zs) == 12.0  # taban halkası bina üst kotuyla tam hizalı
    assert max(zs) > 12.0
    assert mesh.triangle_count() > 0
    bad = [t for t in mesh.triangles if any(i < 0 or i >= mesh.vertex_count() for i in t)]
    assert not bad, "dome mesh geçersiz vertex indexine referans veriyor"


def test_all_roof_types_produce_valid_mesh():
    poly = _rect_polygon(14, 9)
    for rt in RoofType:
        mesh = RoofGenerator.generate(poly, base_z=12.0, roof_type=rt)
        assert mesh.vertex_count() > 0, f"{rt} boş mesh üretti"
        assert mesh.triangle_count() > 0, f"{rt} üçgensiz mesh üretti"


# ------------------------------------------------------------------ #
# Facade Generator
# ------------------------------------------------------------------ #


def test_facade_generator_office_uses_glass():
    poly = _rect_polygon(15, 12)
    facade = FacadeGenerator.generate(poly, "ofis", base_z=0.0, floor_height=24.0, seed=1)
    assert facade.material == FacadeMaterial.CAM
    assert facade.mesh is not None
    assert len(facade.windows) > 0


def test_facade_material_override():
    poly = _rect_polygon(10, 10)
    facade = FacadeGenerator.generate(
        poly,
        "villa",
        base_z=0.0,
        floor_height=6.0,
        material_override=FacadeMaterial.AHSAP,
    )
    assert facade.material == FacadeMaterial.AHSAP
    assert facade.pbr_material.name == "ahsap"


# ------------------------------------------------------------------ #
# Room Generator
# ------------------------------------------------------------------ #


def test_room_generator_covers_full_area():
    poly = _rect_polygon(20, 15)
    rooms = RoomGenerator.generate(poly, building_type="apartman", min_room_size=3.0, seed=42)
    total = RoomGenerator.total_area(rooms)
    assert abs(total - poly.unsigned_area()) < 1e-6


def test_room_generator_deterministic_with_seed():
    poly = _rect_polygon(18, 12)
    rooms_a = RoomGenerator.generate(poly, building_type="ofis", seed=7)
    rooms_b = RoomGenerator.generate(poly, building_type="ofis", seed=7)
    assert [r.room_type for r in rooms_a] == [r.room_type for r in rooms_b]


def test_room_generator_has_corridor_when_enough_rooms():
    poly = _rect_polygon(24, 18)
    rooms = RoomGenerator.generate(poly, building_type="hastane", min_room_size=2.5, seed=3)
    assert any(r.room_type == RoomType.KORIDOR.value for r in rooms)


def test_room_generator_adjacency_is_symmetric():
    poly = _rect_polygon(20, 20)
    rooms = RoomGenerator.generate(poly, building_type="okul", seed=5)
    by_id = {r.room_id: r for r in rooms}
    for room in rooms:
        for neighbor_id in room.neighbors:
            assert room.room_id in by_id[neighbor_id].neighbors


# ------------------------------------------------------------------ #
# Building Elements
# ------------------------------------------------------------------ #


def test_window_generator_symmetric_spacing():
    a, b = Point2D(0, 0), Point2D(20, 0)
    windows = WindowGenerator.place_on_wall(a, b, wall_edge_index=0, spacing=2.5)
    assert len(windows) > 0
    xs = sorted(w.position.x for w in windows)
    center = 10.0
    assert abs((xs[0] + xs[-1]) / 2.0 - center) < 1e-6  # simetrik


def test_door_generator_exterior_entrance_on_longest_edge():
    poly = _rect_polygon(30, 10)
    door = DoorGenerator.exterior_entrance(poly)
    assert door.is_exterior
    assert door.wall_edge_index == 0  # (0,0)-(30,0) en uzun kenar


def test_stair_generator_step_dimensions_within_comfort():
    stair = StairGenerator.generate(Point2D(5, 5), floor_height=3.0)
    assert stair.step_height <= 0.18 + 1e-9
    assert stair.step_count >= 1
    assert abs(stair.step_count * stair.step_height - 3.0) < 1e-6


def test_elevator_core_shaft_spans_building_height():
    core = ElevatorCoreGenerator.generate(Point2D(0, 0), total_building_height=30.0, base_z=0.0)
    assert core.shaft_top_z > 30.0
    assert core.shaft_bottom_z == 0.0


def test_balcony_generator_skips_ground_floor():
    a, b = Point2D(0, 0), Point2D(10, 0)
    windows = WindowGenerator.place_on_wall(a, b, wall_edge_index=0, spacing=2.5)
    ground = BalconyGenerator.place_on_windows(windows, floor_level=0)
    upper = BalconyGenerator.place_on_windows(windows, floor_level=2)
    assert ground == []
    assert len(upper) > 0


# ------------------------------------------------------------------ #
# Procedural Building Generator (uçtan uca)
# ------------------------------------------------------------------ #


def test_building_type_rules_registry_has_all_types():
    for bt in BuildingType:
        rule = BuildingTypeRules.get(bt)
        assert rule.default_floor_height > 0
        assert rule.default_floor_count >= 1


def test_procedural_generator_end_to_end_apartman():
    poly = _rect_polygon(18, 14)
    fp = Footprint(polygon=poly, building_type="apartments", floor_count=5, height_m=15.0)
    building = ProceduralBuildingGenerator.generate(
        fp, building_type=BuildingType.APARTMAN, seed=11
    )

    assert len(building.floors) == 5
    assert building.roof is not None
    assert building.roof.triangle_count() > 0
    assert building.facade is not None
    assert building.facade.mesh is not None
    assert abs(building.total_height_m - 15.0) < 1e-6

    full = building.full_mesh()
    assert full.vertex_count() > 0
    assert full.triangle_count() > 0


def test_procedural_generator_villa_has_elevator_disabled():
    poly = _rect_polygon(12, 10)
    fp = Footprint(polygon=poly, building_type="house")
    building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.VILLA, seed=2)
    assert all(len(f.elevators) == 0 for f in building.floors)


def test_procedural_generator_ofis_has_elevator():
    poly = _rect_polygon(20, 16)
    fp = Footprint(polygon=poly, building_type="office")
    building = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.OFIS, seed=9)
    assert all(len(f.elevators) == 1 for f in building.floors)


def test_procedural_generator_deterministic_with_seed():
    poly = _rect_polygon(16, 12)
    fp = Footprint(polygon=poly, building_type="apartments")
    b1 = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.APARTMAN, seed=99)
    b2 = ProceduralBuildingGenerator.generate(fp, building_type=BuildingType.APARTMAN, seed=99)
    types1 = [[r.room_type for r in f.rooms] for f in b1.floors]
    types2 = [[r.room_type for r in f.rooms] for f in b2.floors]
    assert types1 == types2
