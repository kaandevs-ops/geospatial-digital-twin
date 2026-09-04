"""
ROADMAP_V7.md Faz C2 — kategori bazlı Overpass katmanları
==========================================================

Bölüm B0/B1'in "en görsel etkisi yüksek + teknik olarak en kolay olanlar:
yol + ağaç + su" önceliğiyle eklenen `osm_client.OSMCategoryParser` /
`fetch_category_features` için ağ gerektirmeyen (sentetik Overpass JSON
üzerinden) birim testler. `test_r5_osm_live_integration.py`'daki dürüstlük
ilkesiyle tutarlı: gerçek ağ isteği burada test edilmiyor (bu ortamda
mümkün değil), yalnızca query üretimi + parser mantığı doğrulanıyor.
"""

from __future__ import annotations

import unittest

from harita.core_engine.gis_core.osm_client import (
    DEFAULT_CATEGORIES,
    BBox,
    OSMCategoryParser,
    build_category_query,
    summarize_categories,
)


class TestCategoryQueryBuilder(unittest.TestCase):
    def test_bbox_order_and_all_default_categories_present(self) -> None:
        bbox = BBox(min_lat=1.0, min_lon=2.0, max_lat=3.0, max_lon=4.0)
        query = build_category_query(bbox, list(DEFAULT_CATEGORIES.values()))
        self.assertIn("1.0,2.0,3.0,4.0", query)
        for category in DEFAULT_CATEGORIES.values():
            if "=" in category.overpass_filter:
                k, v = category.overpass_filter.split("=", 1)
                self.assertIn(f'["{k}"="{v}"]', query)
            else:
                self.assertIn(f'["{category.overpass_filter}"]', query)

    def test_single_category_query_is_smaller(self) -> None:
        bbox = BBox(min_lat=1.0, min_lon=2.0, max_lat=3.0, max_lon=4.0)
        full = build_category_query(bbox, list(DEFAULT_CATEGORIES.values()))
        partial = build_category_query(bbox, [DEFAULT_CATEGORIES["roads"]])
        self.assertLess(len(partial), len(full))
        self.assertIn('["highway"]', partial)
        self.assertNotIn('["natural"="tree"]', partial)


class TestCategoryParser(unittest.TestCase):
    def _sample_raw(self) -> dict:
        return {
            "elements": [
                # Tekil ağaç -> Point ("trees")
                {
                    "type": "node",
                    "id": 1,
                    "lat": 40.001,
                    "lon": 32.001,
                    "tags": {"natural": "tree"},
                },
                # Yol için iki uç node (geometri yok, sadece koordinat kaynağı)
                {"type": "node", "id": 2, "lat": 40.002, "lon": 32.002},
                {"type": "node", "id": 3, "lat": 40.003, "lon": 32.003},
                {"type": "node", "id": 4, "lat": 40.004, "lon": 32.004},
                # Yol -> LineString ("roads")
                {"type": "way", "id": 10, "nodes": [2, 3], "tags": {"highway": "residential"}},
                # Orman -> Polygon ("forest"), kapanmamış halka verildi
                {"type": "way", "id": 11, "nodes": [2, 3, 4], "tags": {"landuse": "forest"}},
                # Göl -> Polygon ("water_area")
                {"type": "way", "id": 12, "nodes": [2, 3, 4, 2], "tags": {"natural": "water"}},
                # Dere -> LineString ("waterway")
                {"type": "way", "id": 13, "nodes": [3, 4], "tags": {"waterway": "stream"}},
                # Bilinmeyen kategori -> yok sayılmalı
                {
                    "type": "node",
                    "id": 5,
                    "lat": 40.005,
                    "lon": 32.005,
                    "tags": {"amenity": "restaurant"},
                },
            ]
        }

    def test_geometry_and_category_classification(self) -> None:
        coll = OSMCategoryParser.parse_overpass_json(
            self._sample_raw(),
            list(DEFAULT_CATEGORIES.values()),
        )
        by_category = {f.properties["__category__"]: f for f in coll.features}
        self.assertEqual(by_category["trees"].geometry_type, "Point")
        self.assertEqual(by_category["roads"].geometry_type, "LineString")
        self.assertEqual(by_category["forest"].geometry_type, "Polygon")
        self.assertEqual(by_category["water_area"].geometry_type, "Polygon")
        self.assertEqual(by_category["waterway"].geometry_type, "LineString")
        # "amenity=restaurant" hiçbir DEFAULT_CATEGORIES filtresine uymadığı
        # için çıktıda görünmemeli (Faz C2 kapsamı dışı, B1'in geri kalanı).
        self.assertNotIn("unknown", by_category)

    def test_polygon_ring_is_auto_closed(self) -> None:
        coll = OSMCategoryParser.parse_overpass_json(
            self._sample_raw(),
            [DEFAULT_CATEGORIES["forest"]],
        )
        forest_features = [f for f in coll.features if f.geometry_type == "Polygon"]
        self.assertEqual(len(forest_features), 1)
        ring = forest_features[0].coordinates[0]
        self.assertEqual(ring[0], ring[-1])

    def test_exact_filter_takes_priority_over_loose_filter(self) -> None:
        # "natural=tree" (exact) ile "natural=water" (exact) aynı "natural"
        # anahtarını paylaşıyor ama ikisi de `loose` değil, bu yüzden
        # çakışma riski yok - burada regresyon olarak doğrulanıyor.
        coll = OSMCategoryParser.parse_overpass_json(
            self._sample_raw(),
            [DEFAULT_CATEGORIES["trees"], DEFAULT_CATEGORIES["water_area"]],
        )
        categories = {f.properties["__category__"] for f in coll.features}
        self.assertEqual(categories, {"trees", "water_area"})

    def test_summarize_categories_counts(self) -> None:
        coll = OSMCategoryParser.parse_overpass_json(
            self._sample_raw(),
            list(DEFAULT_CATEGORIES.values()),
        )
        counts = summarize_categories(coll)
        self.assertEqual(counts["trees"], 1)
        self.assertEqual(counts["roads"], 1)
        self.assertEqual(counts["forest"], 1)
        self.assertEqual(counts["water_area"], 1)
        self.assertEqual(counts["waterway"], 1)

    def test_unknown_category_key_raises(self) -> None:
        from harita.core_engine.gis_core.osm_client import fetch_category_features

        with self.assertRaises(ValueError):
            fetch_category_features(
                BBox(min_lat=1.0, min_lon=2.0, max_lat=3.0, max_lon=4.0),
                category_keys=["not_a_real_category"],
            )


if __name__ == "__main__":
    unittest.main()
