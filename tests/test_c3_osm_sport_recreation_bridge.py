"""
ROADMAP_V7.md Faz C3 (6. dilim) — OSM spor/rekreasyon köprüsü
========================================================================

`sport_recreation.osm_bridge` için birim testler: saha/stadyum/havuz
(Polygon) -> `SportAreaItem`, oyun alanı (Point) -> `PlaygroundItem`,
ilgisiz/yanlış-geometri durumların sessizce atlanması, ve uçtan uca
`generate_sport_recreation_for_collection`.
"""

from __future__ import annotations

import unittest

from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.core_engine.gis_core.osm_client import DEFAULT_CATEGORIES
from harita.sport_recreation import (
    PlaygroundItem,
    SportAreaItem,
    SportAreaType,
    SportRecreationGenerator,
    generate_sport_recreation_for_collection,
    sport_recreation_item_from_feature,
)


def _polygon_feature(category_key: str, ring=None):
    ring = ring or [[0.0, 0.0], [20.0, 0.0], [20.0, 12.0], [0.0, 12.0], [0.0, 0.0]]
    return GeoFeature(
        geometry_type="Polygon",
        coordinates=[ring],
        properties={"__category__": category_key},
    )


def _point_feature(category_key: str, x=2.0, y=3.0):
    return GeoFeature(
        geometry_type="Point",
        coordinates=[x, y],
        properties={"__category__": category_key},
    )


class TestDefaultCategoriesRegistered(unittest.TestCase):
    def test_sport_categories_present_with_correct_geometry(self) -> None:
        self.assertEqual(DEFAULT_CATEGORIES["pitch"].geometry, "polygon")
        self.assertEqual(DEFAULT_CATEGORIES["stadium"].geometry, "polygon")
        self.assertEqual(DEFAULT_CATEGORIES["swimming_pool"].geometry, "polygon")
        self.assertEqual(DEFAULT_CATEGORIES["playground"].geometry, "point")

    def test_existing_categories_untouched(self) -> None:
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
            "marketplace",
            "restaurant",
            "cafe",
        ):
            self.assertIn(key, DEFAULT_CATEGORIES)


class TestSportAreaConversion(unittest.TestCase):
    def test_pitch_polygon_produces_sport_area_item(self) -> None:
        item = sport_recreation_item_from_feature(_polygon_feature("pitch"))
        self.assertIsInstance(item, SportAreaItem)
        self.assertEqual(item.area_type, SportAreaType.PITCH)

    def test_stadium_polygon_produces_correct_type(self) -> None:
        item = sport_recreation_item_from_feature(_polygon_feature("stadium"))
        self.assertEqual(item.area_type, SportAreaType.STADIUM)

    def test_swimming_pool_polygon_produces_correct_type(self) -> None:
        item = sport_recreation_item_from_feature(_polygon_feature("swimming_pool"))
        self.assertEqual(item.area_type, SportAreaType.SWIMMING_POOL)

    def test_non_polygon_area_returns_none(self) -> None:
        bad = GeoFeature(
            geometry_type="Point", coordinates=[0.0, 0.0], properties={"__category__": "pitch"}
        )
        self.assertIsNone(sport_recreation_item_from_feature(bad))


class TestPlaygroundConversion(unittest.TestCase):
    def test_playground_point_produces_playground_item(self) -> None:
        item = sport_recreation_item_from_feature(_point_feature("playground"))
        self.assertIsInstance(item, PlaygroundItem)

    def test_non_point_playground_returns_none(self) -> None:
        bad = _polygon_feature("playground")
        self.assertIsNone(sport_recreation_item_from_feature(bad))

    def test_unrelated_category_returns_none(self) -> None:
        self.assertIsNone(sport_recreation_item_from_feature(_point_feature("bench")))


class TestMeshGeneration(unittest.TestCase):
    def test_pitch_mesh_is_thin_and_non_degenerate(self) -> None:
        item = sport_recreation_item_from_feature(_polygon_feature("pitch"))
        mesh = SportRecreationGenerator.generate_area(item)
        self.assertGreater(len(mesh.vertices), 0)
        self.assertGreater(len(mesh.triangles), 0)

    def test_stadium_mesh_is_taller_than_pitch(self) -> None:
        pitch_item = sport_recreation_item_from_feature(_polygon_feature("pitch"))
        stadium_item = sport_recreation_item_from_feature(_polygon_feature("stadium"))
        pitch_mesh = SportRecreationGenerator.generate_area(pitch_item)
        stadium_mesh = SportRecreationGenerator.generate_area(stadium_item)
        pitch_max_z = max(v.z for v in pitch_mesh.vertices)
        stadium_max_z = max(v.z for v in stadium_mesh.vertices)
        self.assertGreater(stadium_max_z, pitch_max_z)

    def test_swimming_pool_is_embedded_below_ground(self) -> None:
        item = sport_recreation_item_from_feature(_polygon_feature("swimming_pool"))
        mesh = SportRecreationGenerator.generate_area(item)
        min_z = min(v.z for v in mesh.vertices)
        self.assertLess(min_z, 0.0)

    def test_playground_mesh_is_non_degenerate(self) -> None:
        from harita.core_engine.geometry_engine import Point2D

        mesh = SportRecreationGenerator.playground(Point2D(0.0, 0.0))
        self.assertGreater(len(mesh.vertices), 0)
        self.assertGreater(len(mesh.triangles), 0)

    def test_generate_batch_merges_areas_and_playgrounds(self) -> None:
        pitch_item = sport_recreation_item_from_feature(_polygon_feature("pitch"))
        playground_item = sport_recreation_item_from_feature(_point_feature("playground"))
        mesh = SportRecreationGenerator.generate_batch([pitch_item], [playground_item])
        self.assertGreater(len(mesh.triangles), 0)

    def test_generate_batch_empty_returns_empty_mesh_no_crash(self) -> None:
        mesh = SportRecreationGenerator.generate_batch([], [])
        self.assertEqual(len(mesh.vertices), 0)


class TestEndToEndCollection(unittest.TestCase):
    def test_mixed_collection_yields_all_sport_items(self) -> None:
        collection = GeoFeatureCollection(
            features=[
                _polygon_feature("pitch"),
                _polygon_feature("stadium"),
                _polygon_feature("swimming_pool"),
                _point_feature("playground"),
                _point_feature("bench"),  # ilgisiz, atlanmalı
            ]
        )
        items = generate_sport_recreation_for_collection(collection)
        self.assertEqual(len(items), 4)
        self.assertEqual(sum(1 for i in items if isinstance(i, SportAreaItem)), 3)
        self.assertEqual(sum(1 for i in items if isinstance(i, PlaygroundItem)), 1)

    def test_empty_collection_returns_empty_list(self) -> None:
        self.assertEqual(
            generate_sport_recreation_for_collection(GeoFeatureCollection(features=[])), []
        )


if __name__ == "__main__":
    unittest.main()
