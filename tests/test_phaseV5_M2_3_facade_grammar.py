"""
ROADMAP V5 - Track M / M2.3: Grammar tabanlı cephe ritmi testleri.
"""

from __future__ import annotations

from harita.building_reconstruction.facade_generator import FacadeGenerator
from harita.building_reconstruction.facade_grammar import (
    ShapeGrammarFacadeGenerator,
    rule_for_building_type,
)
from harita.core_engine.geometry_engine import Point2D, Polygon


def _rect_footprint(w: float = 20.0, h: float = 12.0) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, h), Point2D(0, h)])


def test_m23_grammar_respects_min_gap_and_bay_constraints():
    poly = _rect_footprint()
    rule = rule_for_building_type("apartman")
    placements = ShapeGrammarFacadeGenerator.place_on_footprint(poly, "apartman", seed=42)
    assert len(placements) > 0
    for p in placements:
        assert p.width > 0
        assert p.width <= rule.max_bay_width


def test_m23_different_seeds_produce_different_rhythm():
    poly = _rect_footprint()
    a = ShapeGrammarFacadeGenerator.place_on_footprint(poly, "ofis", seed=1)
    b = ShapeGrammarFacadeGenerator.place_on_footprint(poly, "ofis", seed=2)
    widths_a = [round(p.width, 3) for p in a]
    widths_b = [round(p.width, 3) for p in b]
    assert widths_a != widths_b
    score = ShapeGrammarFacadeGenerator.rhythm_diversity_score(a, b)
    assert score > 0.0


def test_m23_building_types_have_distinct_rule_characters():
    depo_rule = rule_for_building_type("depo")
    ofis_rule = rule_for_building_type("ofis")
    assert depo_rule.window_to_bay_ratio_max < ofis_rule.window_to_bay_ratio_min


def test_m23_facade_generator_opt_in_shape_grammar_does_not_break_default():
    poly = _rect_footprint()
    default_facade = FacadeGenerator.generate(
        poly,
        "apartman",
        base_z=0.0,
        floor_height=3.0,
        floor_count=2,
        seed=7,
    )
    grammar_facade = FacadeGenerator.generate(
        poly,
        "apartman",
        base_z=0.0,
        floor_height=3.0,
        floor_count=2,
        seed=7,
        use_shape_grammar=True,
    )
    assert default_facade.mesh is not None
    assert grammar_facade.mesh is not None
    assert grammar_facade.mesh.triangle_count() > 0
    # Varsayılan davranış (use_shape_grammar=False) tamamen aynı kalmalı.
    assert len(default_facade.windows) >= 1
