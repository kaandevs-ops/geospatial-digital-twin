"""
ROADMAP_V7.md Faz C3 (3. dilim) — OSM kentsel mobilya köprüsü
===============================================================

`street_furniture.osm_bridge` için birim testler: kategori damgalı nokta
feature'ları -> `StreetFurnitureItem`, bilinmeyen kategorilerin sessizce
atlanması, ve uçtan uca `generate_street_furniture_for_collection`.
"""
from __future__ import annotations

import unittest

from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.core_engine.gis_core.osm_client import DEFAULT_CATEGORIES
from harita.street_furniture import (
    StreetFurnitureGenerator,
    StreetFurnitureType,
    furniture_item_from_point,
    generate_street_furniture_for_collection,
)


def _point_feature(category_key: str, x=5.0, y=7.0):
    return GeoFeature(
        geometry_type="Point",
        coordinates=[x, y],
        properties={"__category__": category_key},
    )


class TestDefaultCategoriesRegistered(unittest.TestCase):
    def test_all_furniture_categories_present_and_are_points(self) -> None:
        expected = {
            "street_lamp", "power_pole", "waste_basket",
            "bench", "bus_stop", "bus_station",
        }
        self.assertTrue(expected.issubset(DEFAULT_CATEGORIES.keys()))
        for key in expected:
            self.assertEqual(DEFAULT_CATEGORIES[key].geometry, "point")

    def test_existing_categories_untouched(self) -> None:
        # Faz C2'de eklenen kategoriler bu genişlemeyle bozulmamalı.
        for key in ("roads", "trees", "forest", "wood", "water_area", "waterway"):
            self.assertIn(key, DEFAULT_CATEGORIES)


class TestFurnitureItemFromPoint(unittest.TestCase):
    def test_street_lamp_category_maps_to_correct_type(self) -> None:
        item = furniture_item_from_point(_point_feature("street_lamp"))
        self.assertIsNotNone(item)
        self.assertEqual(item.furniture_type, StreetFurnitureType.STREET_LAMP)

    def test_waste_basket_maps_to_trash_bin_type(self) -> None:
        item = furniture_item_from_point(_point_feature("waste_basket"))
        self.assertEqual(item.furniture_type, StreetFurnitureType.TRASH_BIN)

    def test_bus_stop_and_bus_station_both_map_to_bus_stop_type(self) -> None:
        a = furniture_item_from_point(_point_feature("bus_stop"))
        b = furniture_item_from_point(_point_feature("bus_station"))
        self.assertEqual(a.furniture_type, StreetFurnitureType.BUS_STOP)
        self.assertEqual(b.furniture_type, StreetFurnitureType.BUS_STOP)

    def test_position_preserved(self) -> None:
        item = furniture_item_from_point(_point_feature("bench", x=12.5, y=-3.0))
        self.assertAlmostEqual(item.position.x, 12.5)
        self.assertAlmostEqual(item.position.y, -3.0)

    def test_unknown_category_returns_none(self) -> None:
        self.assertIsNone(furniture_item_from_point(_point_feature("trees")))

    def test_non_point_geometry_returns_none(self) -> None:
        line = GeoFeature(
            geometry_type="LineString",
            coordinates=[(0.0, 0.0), (1.0, 1.0)],
            properties={"__category__": "street_lamp"},
        )
        self.assertIsNone(furniture_item_from_point(line))


class TestGenerateStreetFurnitureForCollection(unittest.TestCase):
    def test_mixed_collection_yields_only_furniture_items(self) -> None:
        collection = GeoFeatureCollection(
            [
                _point_feature("street_lamp"),
                _point_feature("bench"),
                _point_feature("trees"),  # ait değil, atlanmalı
                GeoFeature(  # Polygon, atlanmalı
                    geometry_type="Polygon",
                    coordinates=[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]],
                    properties={"__category__": "water_area"},
                ),
            ],
            crs="EPSG:32636",
        )
        items = generate_street_furniture_for_collection(collection)
        self.assertEqual(len(items), 2)
        types = {item.furniture_type for item in items}
        self.assertEqual(types, {StreetFurnitureType.STREET_LAMP, StreetFurnitureType.BENCH})

    def test_items_are_mesh_ready(self) -> None:
        collection = GeoFeatureCollection([_point_feature("power_pole")], crs="EPSG:32636")
        items = generate_street_furniture_for_collection(collection)
        mesh = StreetFurnitureGenerator.generate(items[0])
        self.assertGreater(mesh.vertex_count(), 0)
        self.assertGreater(mesh.triangle_count(), 0)

    def test_empty_collection_yields_empty_list(self) -> None:
        collection = GeoFeatureCollection([], crs="EPSG:32636")
        self.assertEqual(generate_street_furniture_for_collection(collection), [])


if __name__ == "__main__":
    unittest.main()
