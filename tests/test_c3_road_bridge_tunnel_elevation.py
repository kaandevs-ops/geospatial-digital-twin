"""
ROADMAP_V7.md Faz C3 (2. dilimin kalan alt maddesi) — köprü/tünel Z-seviyesi
==============================================================================

`editor.osm_bridge`'e eklenen `bridge=yes`/`tunnel=yes` -> `elevation_z`
ayrımı için birim testler (B1: "Kavşak/köprü/tünel ayrımı ... farklı Z
seviyesinde render"). `test_c3_osm_road_water_bridge.py`'deki mevcut
testlerle aynı yardımcı fonksiyon desenini kullanır, ayrı dosyada tutulur
(mevcut regresyon dosyasına dokunulmadı).
"""
from __future__ import annotations

import unittest

from harita.core_engine.gis_core import GeoFeature
from harita.editor.osm_bridge import (
    DEFAULT_BRIDGE_CLEARANCE_M,
    DEFAULT_TUNNEL_DEPTH_M,
    DEFAULT_WATER_DEPTH_OFFSET_M,
    road_from_linestring_feature,
    waterway_from_linestring_feature,
)


def _road_feature(highway="residential", bridge=None, tunnel=None, coords=None):
    tags = {"highway": highway, "osm_id": 101}
    if bridge is not None:
        tags["bridge"] = bridge
    if tunnel is not None:
        tags["tunnel"] = tunnel
    return GeoFeature(
        geometry_type="LineString",
        coordinates=coords or [(0.0, 0.0), (10.0, 0.0), (20.0, 5.0)],
        properties={**tags, "__category__": "roads"},
    )


def _waterway_feature(waterway="river", tunnel=None, coords=None):
    tags = {"waterway": waterway, "osm_id": 202}
    if tunnel is not None:
        tags["tunnel"] = tunnel
    return GeoFeature(
        geometry_type="LineString",
        coordinates=coords or [(0.0, 0.0), (5.0, 0.0), (10.0, 2.0)],
        properties={**tags, "__category__": "waterway"},
    )


class TestRoadElevation(unittest.TestCase):
    def test_plain_road_stays_at_ground_level(self) -> None:
        road = road_from_linestring_feature(_road_feature())
        self.assertEqual(road.elevation_z, 0.0)

    def test_bridge_yes_raises_road_above_ground(self) -> None:
        road = road_from_linestring_feature(_road_feature(bridge="yes"))
        self.assertEqual(road.elevation_z, DEFAULT_BRIDGE_CLEARANCE_M)

    def test_tunnel_yes_lowers_road_below_ground(self) -> None:
        road = road_from_linestring_feature(_road_feature(tunnel="yes"))
        self.assertEqual(road.elevation_z, DEFAULT_TUNNEL_DEPTH_M)

    def test_bridge_no_is_treated_as_ground_level(self) -> None:
        road = road_from_linestring_feature(_road_feature(bridge="no"))
        self.assertEqual(road.elevation_z, 0.0)

    def test_bridge_takes_priority_over_tunnel_when_both_yes(self) -> None:
        # OSM'de anlamsız bir kombinasyon (B4) — köprü önceliklidir.
        road = road_from_linestring_feature(_road_feature(bridge="yes", tunnel="yes"))
        self.assertEqual(road.elevation_z, DEFAULT_BRIDGE_CLEARANCE_M)

    def test_bridge_road_produces_non_degenerate_mesh(self) -> None:
        road = road_from_linestring_feature(_road_feature(bridge="yes"))
        mesh = road.to_mesh()
        self.assertGreater(len(mesh.vertices), 0)
        min_z = min(v.z for v in mesh.vertices)
        self.assertGreaterEqual(min_z, DEFAULT_BRIDGE_CLEARANCE_M - 0.01)


class TestWaterwayElevation(unittest.TestCase):
    def test_plain_waterway_uses_default_embedded_depth(self) -> None:
        waterway = waterway_from_linestring_feature(_waterway_feature())
        self.assertEqual(waterway.elevation_z, -DEFAULT_WATER_DEPTH_OFFSET_M)

    def test_tunnel_yes_waterway_goes_deeper(self) -> None:
        waterway = waterway_from_linestring_feature(_waterway_feature(tunnel="yes"))
        self.assertEqual(waterway.elevation_z, DEFAULT_TUNNEL_DEPTH_M)

    def test_tunnel_waterway_deeper_than_plain_waterway(self) -> None:
        plain = waterway_from_linestring_feature(_waterway_feature())
        tunneled = waterway_from_linestring_feature(_waterway_feature(tunnel="yes"))
        self.assertLess(tunneled.elevation_z, plain.elevation_z)


if __name__ == "__main__":
    unittest.main()
