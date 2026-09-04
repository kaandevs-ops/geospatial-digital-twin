"""
ROADMAP_V7.md Faz C3 (5. dilim) — OSM ticaret/gündelik-yaşam köprüsü
========================================================================

`commerce_props.osm_bridge` için birim testler: pazar yeri (Polygon) ->
tezgah grid'i, restoran/kafe (Point + `outdoor_seating=yes`) -> masa-
sandalye seti, ilgisiz/eksik-tag durumların sessizce atlanması, ve
uçtan uca `generate_commerce_props_for_collection`.
"""

from __future__ import annotations

import unittest

from harita.commerce_props import (
    CommercePropsGenerator,
    MarketStallLayout,
    OutdoorSeatingItem,
    commerce_prop_from_feature,
    generate_commerce_props_for_collection,
)
from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.core_engine.gis_core.osm_client import DEFAULT_CATEGORIES


def _marketplace_feature(ring=None):
    ring = ring or [[0.0, 0.0], [10.0, 0.0], [10.0, 6.0], [0.0, 6.0], [0.0, 0.0]]
    return GeoFeature(
        geometry_type="Polygon",
        coordinates=[ring],
        properties={"__category__": "marketplace"},
    )


def _restaurant_feature(outdoor_seating="yes", category="restaurant", x=3.0, y=4.0):
    props = {"__category__": category}
    if outdoor_seating is not None:
        props["outdoor_seating"] = outdoor_seating
    return GeoFeature(geometry_type="Point", coordinates=[x, y], properties=props)


class TestDefaultCategoriesRegistered(unittest.TestCase):
    def test_commerce_categories_present_with_correct_geometry(self) -> None:
        self.assertEqual(DEFAULT_CATEGORIES["marketplace"].geometry, "polygon")
        self.assertEqual(DEFAULT_CATEGORIES["restaurant"].geometry, "point")
        self.assertEqual(DEFAULT_CATEGORIES["cafe"].geometry, "point")

    def test_existing_categories_untouched(self) -> None:
        # Önceki dilimlerde eklenen tüm kategoriler bozulmamalı (regresyon).
        for key in (
            "roads",
            "trees",
            "forest",
            "wood",
            "water_area",
            "waterway",
            "street_lamp",
            "power_pole",
            "waste_basket",
            "bench",
            "bus_stop",
            "bus_station",
            "place_of_worship",
        ):
            self.assertIn(key, DEFAULT_CATEGORIES)


class TestMarketplaceLayout(unittest.TestCase):
    def test_marketplace_polygon_produces_stall_layout(self) -> None:
        result = commerce_prop_from_feature(_marketplace_feature())
        self.assertIsInstance(result, MarketStallLayout)
        self.assertGreater(len(result.positions), 0)

    def test_stall_positions_are_inside_polygon_bounds(self) -> None:
        result = commerce_prop_from_feature(_marketplace_feature())
        for p in result.positions:
            self.assertTrue(0.0 <= p.x <= 10.0)
            self.assertTrue(0.0 <= p.y <= 6.0)

    def test_tiny_polygon_may_yield_empty_layout_without_error(self) -> None:
        ring = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5], [0.0, 0.0]]
        result = commerce_prop_from_feature(_marketplace_feature(ring))
        self.assertIsInstance(result, MarketStallLayout)  # boş olabilir, hata olmamalı

    def test_non_polygon_marketplace_returns_none(self) -> None:
        bad = GeoFeature(
            geometry_type="Point",
            coordinates=[0.0, 0.0],
            properties={"__category__": "marketplace"},
        )
        self.assertIsNone(commerce_prop_from_feature(bad))


class TestOutdoorSeating(unittest.TestCase):
    def test_restaurant_with_outdoor_seating_yes_produces_item(self) -> None:
        result = commerce_prop_from_feature(_restaurant_feature())
        self.assertIsInstance(result, OutdoorSeatingItem)
        self.assertEqual(result.table_count, 2)

    def test_cafe_with_outdoor_seating_yes_also_produces_item(self) -> None:
        result = commerce_prop_from_feature(_restaurant_feature(category="cafe"))
        self.assertIsInstance(result, OutdoorSeatingItem)

    def test_restaurant_without_outdoor_seating_tag_returns_none(self) -> None:
        result = commerce_prop_from_feature(_restaurant_feature(outdoor_seating=None))
        self.assertIsNone(result)

    def test_restaurant_with_outdoor_seating_no_returns_none(self) -> None:
        result = commerce_prop_from_feature(_restaurant_feature(outdoor_seating="no"))
        self.assertIsNone(result)

    def test_unrelated_category_returns_none(self) -> None:
        f = GeoFeature(
            geometry_type="Point", coordinates=[1.0, 1.0], properties={"__category__": "bench"}
        )
        self.assertIsNone(commerce_prop_from_feature(f))


class TestMeshGeneration(unittest.TestCase):
    def test_market_stall_mesh_is_non_degenerate(self) -> None:
        from harita.core_engine.geometry_engine import Point2D

        mesh = CommercePropsGenerator.market_stall(Point2D(0.0, 0.0))
        self.assertGreater(len(mesh.vertices), 0)
        self.assertGreater(len(mesh.triangles), 0)

    def test_outdoor_seating_set_mesh_scales_with_table_count(self) -> None:
        from harita.core_engine.geometry_engine import Point2D

        item = OutdoorSeatingItem(position=Point2D(0.0, 0.0), table_count=3)
        mesh = CommercePropsGenerator.outdoor_seating_set(item)
        self.assertGreater(len(mesh.vertices), 0)

    def test_generate_market_from_layout(self) -> None:
        layout = commerce_prop_from_feature(_marketplace_feature())
        mesh = CommercePropsGenerator.generate_market(layout)
        self.assertGreater(len(mesh.triangles), 0)

    def test_generate_market_empty_layout_returns_empty_mesh_no_crash(self) -> None:
        empty = MarketStallLayout(positions=[])
        mesh = CommercePropsGenerator.generate_market(empty)
        self.assertEqual(len(mesh.vertices), 0)


class TestEndToEndCollection(unittest.TestCase):
    def test_mixed_collection_yields_market_and_seating_props(self) -> None:
        collection = GeoFeatureCollection(
            features=[
                _marketplace_feature(),
                _restaurant_feature(),
                _restaurant_feature(category="cafe", x=8.0, y=1.0),
                _restaurant_feature(outdoor_seating="no"),  # atlanmalı
                GeoFeature(
                    geometry_type="Point",
                    coordinates=[0.0, 0.0],
                    properties={"__category__": "trees"},
                ),  # ilgisiz, atlanmalı
            ]
        )
        props = generate_commerce_props_for_collection(collection)
        self.assertEqual(len(props), 3)
        self.assertEqual(sum(1 for p in props if isinstance(p, MarketStallLayout)), 1)
        self.assertEqual(sum(1 for p in props if isinstance(p, OutdoorSeatingItem)), 2)

    def test_empty_collection_returns_empty_list(self) -> None:
        self.assertEqual(
            generate_commerce_props_for_collection(GeoFeatureCollection(features=[])), []
        )


if __name__ == "__main__":
    unittest.main()
