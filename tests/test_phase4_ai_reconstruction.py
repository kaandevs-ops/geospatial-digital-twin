"""Phase 4 (AI Reconstruction) için birim testleri."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.roof_generator import RoofType
from harita.building_reconstruction.facade_generator import FacadeMaterial
from harita.building_reconstruction.room_generator import RoomType

from harita.ai_reconstruction import (
    Predictor,
    AIBuildingAnalyzer, ArchitecturalStyle, ClimateZone, HeuristicPredictor,
    AIRoofPredictor, RoofPrediction,
    AIInteriorLayout,
    AIMaterialPredictor, SurfaceClass,
    AIEnvironmentGenerator, EnvironmentObjectType,
)


def _rect_polygon(w: float, d: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


# ------------------------------------------------------------------ #
# Predictor protocol
# ------------------------------------------------------------------ #

def test_heuristic_predictor_satisfies_predictor_protocol():
    predictor = HeuristicPredictor()
    assert isinstance(predictor, Predictor)


# ------------------------------------------------------------------ #
# AIBuildingAnalyzer
# ------------------------------------------------------------------ #

def test_building_analyzer_uses_known_footprint_data():
    fp = Footprint(polygon=_rect_polygon(20, 15), building_type="apartments",
                    floor_count=6, height_m=18.0)
    analysis = AIBuildingAnalyzer().analyze(fp)
    assert analysis.floor_count == 6
    assert analysis.height_m == 18.0
    assert analysis.confidence > 0.5
    assert isinstance(analysis.architectural_style, ArchitecturalStyle)
    assert isinstance(analysis.facade_material, FacadeMaterial)


def test_building_analyzer_infers_when_data_missing():
    fp = Footprint(polygon=_rect_polygon(12, 10), building_type="office")
    analysis = AIBuildingAnalyzer().analyze(fp)
    assert analysis.floor_count >= 1
    assert analysis.height_m > 0
    assert analysis.confidence < 0.6  # veri eksik -> düşük güven


def test_building_analyzer_custom_predictor_injection():
    class FixedPredictor:
        def predict(self, features: dict) -> dict:
            return {
                "height_m": 99.0, "floor_count": 33, "usage": "custom",
                "architectural_style": "modern", "estimated_age_years": 1,
                "facade_material": "metal", "confidence": 1.0,
            }

    fp = Footprint(polygon=_rect_polygon(10, 10))
    analysis = AIBuildingAnalyzer(predictor=FixedPredictor()).analyze(fp)
    assert analysis.height_m == 99.0
    assert analysis.floor_count == 33
    assert analysis.confidence == 1.0


# ------------------------------------------------------------------ #
# AIRoofPredictor
# ------------------------------------------------------------------ #

def test_roof_predictor_probabilities_sum_to_one():
    prediction = AIRoofPredictor().predict("apartments", ClimateZone.ILIMAN)
    total = sum(prediction.probabilities.values())
    assert abs(total - 1.0) < 1e-9


def test_roof_predictor_industrial_prefers_industrial_roof():
    prediction = AIRoofPredictor().predict("warehouse", ClimateZone.ILIMAN)
    assert prediction.most_likely in (RoofType.INDUSTRIAL, RoofType.FLAT)


def test_roof_predictor_climate_shifts_distribution():
    temperate = AIRoofPredictor().predict("house", ClimateZone.ILIMAN)
    polar = AIRoofPredictor().predict("house", ClimateZone.KUTUP)
    assert polar.probabilities[RoofType.GABLE] >= temperate.probabilities[RoofType.GABLE]


def test_roof_predictor_top_n_sorted_descending():
    prediction = AIRoofPredictor().predict("house")
    top = prediction.top_n(3)
    values = [v for _, v in top]
    assert values == sorted(values, reverse=True)


def test_climate_zone_for_latitude():
    assert AIRoofPredictor.climate_zone_for_latitude(10.0) == ClimateZone.TROPIKAL
    assert AIRoofPredictor.climate_zone_for_latitude(40.0) == ClimateZone.ILIMAN
    assert AIRoofPredictor.climate_zone_for_latitude(55.0) == ClimateZone.KARASAL
    assert AIRoofPredictor.climate_zone_for_latitude(75.0) == ClimateZone.KUTUP


# ------------------------------------------------------------------ #
# AIInteriorLayout
# ------------------------------------------------------------------ #

def test_interior_layout_variant_covers_full_area():
    poly = _rect_polygon(18, 14)
    variant = AIInteriorLayout(base_seed=1).generate_variant(poly, building_type="ofis", seed=5)
    total = sum(r.area_m2 for r in variant.rooms)
    assert abs(total - poly.unsigned_area()) < 1e-6


def test_interior_layout_alternatives_differ():
    poly = _rect_polygon(20, 16)
    layout = AIInteriorLayout(base_seed=42)
    variants = layout.generate_alternatives(poly, building_type="apartman", n_variants=5)
    assert len(variants) == 5
    seeds = {v.seed for v in variants}
    assert len(seeds) == 5  # farklı seed'ler


def test_interior_layout_best_variant_has_highest_diversity():
    poly = _rect_polygon(20, 16)
    layout = AIInteriorLayout(base_seed=7)
    variants = layout.generate_alternatives(poly, building_type="hastane", n_variants=6)
    best = layout.best_variant(variants)
    assert best.diversity_score == max(v.diversity_score for v in variants)


# ------------------------------------------------------------------ #
# AIMaterialPredictor
# ------------------------------------------------------------------ #

def test_material_predictor_glass_for_office_windows():
    prediction = AIMaterialPredictor().predict(SurfaceClass.CAM, building_type="office")
    assert prediction.material == FacadeMaterial.CAM


def test_material_predictor_roof_material_from_roof_type():
    prediction = AIMaterialPredictor().predict(
        SurfaceClass.CATI, building_type="office", roof_type=RoofType.INDUSTRIAL,
    )
    assert prediction.material == FacadeMaterial.METAL
    assert prediction.confidence > 0.5


def test_material_predictor_all_surfaces_covers_every_class():
    predictions = AIMaterialPredictor().predict_all_surfaces(building_type="house")
    assert set(predictions.keys()) == set(SurfaceClass)


# ------------------------------------------------------------------ #
# AIEnvironmentGenerator
# ------------------------------------------------------------------ #

def test_environment_generator_objects_outside_footprint():
    poly = _rect_polygon(20, 15)
    generator = AIEnvironmentGenerator(seed=3)
    objects = generator.generate(poly, margin_m=20.0, object_types=[EnvironmentObjectType.AGAC])
    assert len(objects) > 0
    from harita.core_engine.geometry_engine import GeometryEngine
    for obj in objects:
        assert not GeometryEngine.point_in_polygon(obj.position, poly)


def test_environment_generator_respects_minimum_distance():
    poly = _rect_polygon(10, 10)
    generator = AIEnvironmentGenerator(seed=9)
    objects = generator.generate(poly, margin_m=25.0, object_types=[EnvironmentObjectType.AGAC])
    tree_positions = [o.position for o in objects if o.object_type == EnvironmentObjectType.AGAC]
    for i in range(len(tree_positions)):
        for j in range(i + 1, len(tree_positions)):
            assert tree_positions[i].distance_to(tree_positions[j]) >= 3.0 - 1e-6


def test_environment_generator_deterministic_with_seed():
    poly = _rect_polygon(15, 15)
    objs_a = AIEnvironmentGenerator(seed=123).generate(poly, margin_m=15.0)
    objs_b = AIEnvironmentGenerator(seed=123).generate(poly, margin_m=15.0)
    positions_a = [(o.object_type, round(o.position.x, 6), round(o.position.y, 6)) for o in objs_a]
    positions_b = [(o.object_type, round(o.position.x, 6), round(o.position.y, 6)) for o in objs_b]
    assert positions_a == positions_b


def test_environment_generator_multiple_object_types():
    poly = _rect_polygon(20, 20)
    generator = AIEnvironmentGenerator(seed=1)
    types = [EnvironmentObjectType.AGAC, EnvironmentObjectType.BANK, EnvironmentObjectType.LAMBA]
    objects = generator.generate(poly, margin_m=20.0, object_types=types)
    found_types = {o.object_type for o in objects}
    assert found_types.issubset(set(types))
    assert len(found_types) > 0
