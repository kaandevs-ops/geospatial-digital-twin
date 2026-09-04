"""
ROADMAP_V7.md Faz C3 (4. dilim) — OSM dini yapı köprüsü
=========================================================

`religious_structures` modülü + `religious_structures.osm_bridge` için
birim testler: `religion` tag sınıflandırması, tag->item çevrimi, tip
bazlı mesh üretimi (cami/kilise/genel kubbe), ve uçtan uca
`generate_religious_structures_for_collection`.
"""

from __future__ import annotations

import unittest

from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.core_engine.gis_core.osm_client import DEFAULT_CATEGORIES
from harita.religious_structures import (
    ReligionKind,
    ReligiousStructureGenerator,
    classify_religion,
    generate_religious_structures_for_collection,
    religious_structure_item_from_point,
)


def _worship_feature(religion=None, x=3.0, y=4.0, height=None, category="place_of_worship"):
    tags = {"amenity": "place_of_worship"}
    if religion is not None:
        tags["religion"] = religion
    if height is not None:
        tags["height"] = height
    return GeoFeature(
        geometry_type="Point",
        coordinates=[x, y],
        properties={**tags, "__category__": category},
    )


class TestCategoryRegistered(unittest.TestCase):
    def test_place_of_worship_category_present_and_point(self) -> None:
        self.assertIn("place_of_worship", DEFAULT_CATEGORIES)
        self.assertEqual(DEFAULT_CATEGORIES["place_of_worship"].geometry, "point")

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
        ):
            self.assertIn(key, DEFAULT_CATEGORIES)


class TestClassifyReligion(unittest.TestCase):
    def test_muslim_tag(self) -> None:
        self.assertEqual(classify_religion({"religion": "muslim"}), ReligionKind.MUSLIM)

    def test_christian_tag(self) -> None:
        self.assertEqual(classify_religion({"religion": "christian"}), ReligionKind.CHRISTIAN)

    def test_jewish_tag(self) -> None:
        self.assertEqual(classify_religion({"religion": "jewish"}), ReligionKind.JEWISH)

    def test_missing_or_unknown_falls_back_to_generic(self) -> None:
        self.assertEqual(classify_religion({}), ReligionKind.GENERIC)
        self.assertEqual(classify_religion({"religion": "buddhist"}), ReligionKind.GENERIC)


class TestReligiousStructureItemFromPoint(unittest.TestCase):
    def test_muslim_worship_point_converts(self) -> None:
        item = religious_structure_item_from_point(_worship_feature(religion="muslim"))
        self.assertIsNotNone(item)
        self.assertEqual(item.religion, ReligionKind.MUSLIM)
        self.assertAlmostEqual(item.position.x, 3.0)
        self.assertAlmostEqual(item.position.y, 4.0)

    def test_height_tag_overrides_default(self) -> None:
        item = religious_structure_item_from_point(
            _worship_feature(religion="christian", height="15 m")
        )
        self.assertAlmostEqual(item.base_height_m, 15.0)

    def test_missing_height_uses_default(self) -> None:
        item = religious_structure_item_from_point(_worship_feature(religion="christian"))
        self.assertAlmostEqual(item.base_height_m, 8.0)

    def test_wrong_category_returns_none(self) -> None:
        feature = _worship_feature(religion="muslim", category="bench")
        self.assertIsNone(religious_structure_item_from_point(feature))

    def test_non_point_geometry_returns_none(self) -> None:
        line = GeoFeature(
            geometry_type="LineString",
            coordinates=[(0.0, 0.0), (1.0, 1.0)],
            properties={"amenity": "place_of_worship", "__category__": "place_of_worship"},
        )
        self.assertIsNone(religious_structure_item_from_point(line))


class TestReligiousStructureGenerator(unittest.TestCase):
    def test_mosque_silhouette_is_nondegenerate(self) -> None:
        item = religious_structure_item_from_point(_worship_feature(religion="muslim"))
        mesh = ReligiousStructureGenerator.generate(item)
        self.assertGreater(mesh.vertex_count(), 0)
        self.assertGreater(mesh.triangle_count(), 0)

    def test_church_silhouette_is_nondegenerate(self) -> None:
        item = religious_structure_item_from_point(_worship_feature(religion="christian"))
        mesh = ReligiousStructureGenerator.generate(item)
        self.assertGreater(mesh.vertex_count(), 0)
        self.assertGreater(mesh.triangle_count(), 0)

    def test_generic_dome_used_for_jewish_and_unknown(self) -> None:
        jewish_item = religious_structure_item_from_point(_worship_feature(religion="jewish"))
        unknown_item = religious_structure_item_from_point(_worship_feature(religion="other"))
        mesh_a = ReligiousStructureGenerator.generate(jewish_item)
        mesh_b = ReligiousStructureGenerator.generate(unknown_item)
        self.assertGreater(mesh_a.vertex_count(), 0)
        self.assertGreater(mesh_b.vertex_count(), 0)


class TestGenerateReligiousStructuresForCollection(unittest.TestCase):
    def test_end_to_end_mixed_collection(self) -> None:
        collection = GeoFeatureCollection(
            [
                _worship_feature(religion="muslim"),
                _worship_feature(religion="christian", x=10.0, y=10.0),
                GeoFeature(  # ilgisiz kategori, atlanmalı
                    geometry_type="Point",
                    coordinates=[0.0, 0.0],
                    properties={"__category__": "bench"},
                ),
            ],
            crs="EPSG:32636",
        )
        items = generate_religious_structures_for_collection(collection)
        self.assertEqual(len(items), 2)
        religions = {item.religion for item in items}
        self.assertEqual(religions, {ReligionKind.MUSLIM, ReligionKind.CHRISTIAN})

    def test_empty_collection_yields_empty_list(self) -> None:
        collection = GeoFeatureCollection([], crs="EPSG:32636")
        self.assertEqual(generate_religious_structures_for_collection(collection), [])


if __name__ == "__main__":
    unittest.main()
