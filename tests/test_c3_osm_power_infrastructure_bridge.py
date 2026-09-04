"""
ROADMAP_V7.md Faz C3 (7. dilim, B1'in son dilimi) — OSM altyapı köprüsü
========================================================================

`power_infrastructure.osm_bridge` için birim testler: elektrik hattı
(LineString) -> `Road`, trafo (Polygon) -> `SubstationItem`, baz istasyonu
(Point + `tower:type=communication`) -> `CommunicationTowerItem`, ilgisiz/
yanlış-tag durumların sessizce atlanması, ve uçtan uca
`generate_power_infrastructure_for_collection`.
"""

from __future__ import annotations

import unittest

from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.core_engine.gis_core.osm_client import DEFAULT_CATEGORIES
from harita.editor import Road
from harita.power_infrastructure import (
    DEFAULT_POWER_LINE_HEIGHT_M,
    CommunicationTowerItem,
    PowerInfrastructureGenerator,
    SubstationItem,
)
from harita.power_infrastructure.osm_bridge import (
    DEFAULT_COMMUNICATION_TOWER_HEIGHT_M,
    generate_power_infrastructure_for_collection,
    infra_item_from_feature,
)


def _power_line_feature(coords=None, osm_id=501):
    return GeoFeature(
        geometry_type="LineString",
        coordinates=coords or [(0.0, 0.0), (10.0, 0.0), (20.0, 3.0)],
        properties={"power": "line", "osm_id": osm_id, "__category__": "power_line"},
    )


def _substation_feature(ring=None):
    ring = ring or [[0.0, 0.0], [15.0, 0.0], [15.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
    return GeoFeature(
        geometry_type="Polygon",
        coordinates=[ring],
        properties={"power": "substation", "__category__": "substation"},
    )


def _tower_feature(tower_type="communication", height=None, x=4.0, y=5.0):
    props = {"__category__": "communication_tower"}
    if tower_type is not None:
        props["tower:type"] = tower_type
    if height is not None:
        props["height"] = height
    return GeoFeature(geometry_type="Point", coordinates=[x, y], properties=props)


class TestDefaultCategoriesRegistered(unittest.TestCase):
    def test_infra_categories_present_with_correct_geometry(self) -> None:
        self.assertEqual(DEFAULT_CATEGORIES["power_line"].geometry, "line")
        self.assertEqual(DEFAULT_CATEGORIES["substation"].geometry, "polygon")
        self.assertEqual(DEFAULT_CATEGORIES["communication_tower"].geometry, "point")

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
            "pitch",
            "stadium",
            "swimming_pool",
            "playground",
        ):
            self.assertIn(key, DEFAULT_CATEGORIES)


class TestPowerLineConversion(unittest.TestCase):
    def test_power_line_produces_road(self) -> None:
        item = infra_item_from_feature(_power_line_feature())
        self.assertIsInstance(item, Road)
        self.assertEqual(item.elevation_z, DEFAULT_POWER_LINE_HEIGHT_M)

    def test_power_line_too_short_returns_none(self) -> None:
        bad = _power_line_feature(coords=[(0.0, 0.0)])
        self.assertIsNone(infra_item_from_feature(bad))

    def test_non_linestring_power_line_returns_none(self) -> None:
        bad = GeoFeature(
            geometry_type="Point", coordinates=[0.0, 0.0], properties={"__category__": "power_line"}
        )
        self.assertIsNone(infra_item_from_feature(bad))

    def test_power_line_mesh_is_non_degenerate(self) -> None:
        item = infra_item_from_feature(_power_line_feature())
        mesh = PowerInfrastructureGenerator.mesh_for_power_line(item)
        self.assertGreater(len(mesh.vertices), 0)


class TestSubstationConversion(unittest.TestCase):
    def test_substation_polygon_produces_item(self) -> None:
        item = infra_item_from_feature(_substation_feature())
        self.assertIsInstance(item, SubstationItem)

    def test_non_polygon_substation_returns_none(self) -> None:
        bad = GeoFeature(
            geometry_type="Point", coordinates=[0.0, 0.0], properties={"__category__": "substation"}
        )
        self.assertIsNone(infra_item_from_feature(bad))

    def test_degenerate_substation_ring_returns_none(self) -> None:
        bad = _substation_feature(ring=[[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]])
        self.assertIsNone(infra_item_from_feature(bad))

    def test_substation_mesh_is_low_and_non_degenerate(self) -> None:
        item = infra_item_from_feature(_substation_feature())
        mesh = PowerInfrastructureGenerator.substation(item)
        self.assertGreater(len(mesh.vertices), 0)
        max_z = max(v.z for v in mesh.vertices)
        self.assertLess(max_z, 3.0)  # "basit hacim", çok yüksek olmamalı


class TestCommunicationTowerConversion(unittest.TestCase):
    def test_communication_tower_produces_item(self) -> None:
        item = infra_item_from_feature(_tower_feature())
        self.assertIsInstance(item, CommunicationTowerItem)
        self.assertEqual(item.height_m, DEFAULT_COMMUNICATION_TOWER_HEIGHT_M)

    def test_non_communication_tower_type_returns_none(self) -> None:
        self.assertIsNone(infra_item_from_feature(_tower_feature(tower_type="observation")))

    def test_missing_tower_type_returns_none(self) -> None:
        self.assertIsNone(infra_item_from_feature(_tower_feature(tower_type=None)))

    def test_height_tag_overrides_default(self) -> None:
        item = infra_item_from_feature(_tower_feature(height="40"))
        self.assertEqual(item.height_m, 40.0)

    def test_non_point_tower_returns_none(self) -> None:
        bad = GeoFeature(
            geometry_type="LineString",
            coordinates=[(0.0, 0.0), (1.0, 1.0)],
            properties={"__category__": "communication_tower", "tower:type": "communication"},
        )
        self.assertIsNone(infra_item_from_feature(bad))

    def test_tower_mesh_is_non_degenerate(self) -> None:
        item = infra_item_from_feature(_tower_feature())
        mesh = PowerInfrastructureGenerator.communication_tower(item)
        self.assertGreater(len(mesh.vertices), 0)
        self.assertGreater(len(mesh.triangles), 0)


class TestEndToEndCollection(unittest.TestCase):
    def test_mixed_collection_yields_all_infra_items(self) -> None:
        collection = GeoFeatureCollection(
            features=[
                _power_line_feature(),
                _substation_feature(),
                _tower_feature(),
                _tower_feature(tower_type="observation"),  # atlanmalı
                GeoFeature(
                    geometry_type="Point",
                    coordinates=[0.0, 0.0],
                    properties={"__category__": "bench"},
                ),  # ilgisiz
            ]
        )
        items = generate_power_infrastructure_for_collection(collection)
        self.assertEqual(len(items), 3)
        self.assertEqual(sum(1 for i in items if isinstance(i, Road)), 1)
        self.assertEqual(sum(1 for i in items if isinstance(i, SubstationItem)), 1)
        self.assertEqual(sum(1 for i in items if isinstance(i, CommunicationTowerItem)), 1)

    def test_empty_collection_returns_empty_list(self) -> None:
        self.assertEqual(
            generate_power_infrastructure_for_collection(GeoFeatureCollection(features=[])), []
        )


if __name__ == "__main__":
    unittest.main()
