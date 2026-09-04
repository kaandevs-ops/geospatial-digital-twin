"""
ROADMAP_V7.md Faz C3 — OSM kategori feature'larından bitki örtüsü üretimi
===========================================================================

`vegetation.osm_bridge` için birim testler: tekil ağaç (Point) ->
`VegetationInstance`/`Mesh3D`, orman poligonu -> Poisson-disc dağıtım,
ve uçtan uca `generate_vegetation_for_collection`.
"""

from __future__ import annotations

import unittest

from harita.core_engine.gis_core import GeoFeature, GeoFeatureCollection
from harita.vegetation import (
    TreeSpecies,
    classify_species,
    estimate_height_m,
    generate_vegetation_for_collection,
    mesh_for_tree_instance,
    scatter_forest_polygon,
    tree_instance_from_point,
)


class TestSpeciesClassification(unittest.TestCase):
    def test_leaf_type_needleleaved_is_conifer(self) -> None:
        self.assertEqual(classify_species({"leaf_type": "needleleaved"}), TreeSpecies.CONIFER)

    def test_leaf_type_broadleaved_is_deciduous(self) -> None:
        self.assertEqual(classify_species({"leaf_type": "broadleaved"}), TreeSpecies.DECIDUOUS)

    def test_genus_pine_hint_is_pine(self) -> None:
        # ROADMAP_V8 Faz 5.2: `genus=Pinus` artık genel CONIFER kovası
        # yerine daha isabetli PINE alt tipine sınıflandırılıyor (sişman
        # katmanlı taç siluetiyle görsel olarak ayırt edilebilir).
        self.assertEqual(classify_species({"genus": "Pinus"}), TreeSpecies.PINE)

    def test_no_tags_is_generic(self) -> None:
        self.assertEqual(classify_species({}), TreeSpecies.GENERIC)

    def test_height_tag_parsed_with_unit_suffix(self) -> None:
        self.assertAlmostEqual(estimate_height_m({"height": "15 m"}, TreeSpecies.GENERIC), 15.0)

    def test_missing_height_falls_back_to_species_table(self) -> None:
        self.assertEqual(estimate_height_m({}, TreeSpecies.CONIFER), 12.0)


class TestTreeInstanceFromPoint(unittest.TestCase):
    def test_point_feature_converts(self) -> None:
        feature = GeoFeature(
            geometry_type="Point",
            coordinates=[10.0, 20.0],
            properties={
                "natural": "tree",
                "leaf_type": "needleleaved",
                "height": "8",
                "osm_id": 42,
            },
        )
        instance = tree_instance_from_point(feature, seed=5)
        self.assertEqual(instance.x, 10.0)
        self.assertEqual(instance.y, 20.0)
        self.assertEqual(instance.species, TreeSpecies.CONIFER)
        self.assertAlmostEqual(instance.height, 8.0)

    def test_non_point_raises(self) -> None:
        feature = GeoFeature(geometry_type="Polygon", coordinates=[[]], properties={})
        with self.assertRaises(ValueError):
            tree_instance_from_point(feature)

    def test_mesh_generation_does_not_raise(self) -> None:
        feature = GeoFeature(
            geometry_type="Point",
            coordinates=[0.0, 0.0],
            properties={},
        )
        instance = tree_instance_from_point(feature)
        mesh = mesh_for_tree_instance(instance)
        self.assertGreater(len(mesh.vertices), 0)
        self.assertGreater(len(mesh.triangles), 0)

    def test_deterministic_same_seed_same_result(self) -> None:
        feature = GeoFeature(
            geometry_type="Point",
            coordinates=[1.0, 1.0],
            properties={"osm_id": 7},
        )
        a = tree_instance_from_point(feature, seed=3)
        b = tree_instance_from_point(feature, seed=3)
        self.assertEqual(a, b)


class TestForestScatter(unittest.TestCase):
    def _square_forest_feature(self, side_m: float = 40.0) -> GeoFeature:
        ring = [(0.0, 0.0), (side_m, 0.0), (side_m, side_m), (0.0, side_m), (0.0, 0.0)]
        return GeoFeature(
            geometry_type="Polygon",
            coordinates=[ring],
            properties={"landuse": "forest", "osm_id": 99},
        )

    def test_scatter_produces_points_inside_polygon(self) -> None:
        feature = self._square_forest_feature()
        result = scatter_forest_polygon(feature, density_per_100m2=2.0, min_distance_m=3.0, seed=1)
        self.assertEqual(result.osm_id, 99)
        self.assertGreater(len(result.instances), 0)
        for inst in result.instances:
            self.assertTrue(0.0 <= inst.x <= 40.0)
            self.assertTrue(0.0 <= inst.y <= 40.0)

    def test_min_distance_is_respected(self) -> None:
        feature = self._square_forest_feature()
        result = scatter_forest_polygon(feature, density_per_100m2=5.0, min_distance_m=4.0, seed=2)
        pts = [(i.x, i.y) for i in result.instances]
        for idx, (x1, y1) in enumerate(pts):
            for x2, y2 in pts[idx + 1 :]:
                dist_sq = (x1 - x2) ** 2 + (y1 - y2) ** 2
                self.assertGreaterEqual(dist_sq, 4.0**2 - 1e-6)

    def test_non_polygon_raises(self) -> None:
        feature = GeoFeature(geometry_type="Point", coordinates=[0.0, 0.0], properties={})
        with self.assertRaises(ValueError):
            scatter_forest_polygon(feature)


class TestGenerateVegetationForCollection(unittest.TestCase):
    def test_mixed_collection_only_processes_relevant_categories(self) -> None:
        tree = GeoFeature(
            geometry_type="Point",
            coordinates=[5.0, 5.0],
            properties={"__category__": "trees", "osm_id": 1},
        )
        forest_ring = [
            (100.0, 100.0),
            (130.0, 100.0),
            (130.0, 130.0),
            (100.0, 130.0),
            (100.0, 100.0),
        ]
        forest = GeoFeature(
            geometry_type="Polygon",
            coordinates=[forest_ring],
            properties={"__category__": "forest", "osm_id": 2},
        )
        road = GeoFeature(
            geometry_type="LineString",
            coordinates=[(0.0, 0.0), (10.0, 10.0)],
            properties={"__category__": "roads", "osm_id": 3},
        )
        collection = GeoFeatureCollection([tree, forest, road], crs="local")
        instances = generate_vegetation_for_collection(collection, seed=0)
        # En az 1 (tekil ağaç) + orman içi dağıtımdan gelen bir grup olmalı.
        self.assertGreaterEqual(len(instances), 2)


if __name__ == "__main__":
    unittest.main()
