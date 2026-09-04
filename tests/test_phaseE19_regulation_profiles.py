"""
Roadmap V4 - Faz E19: Cok-ulkeli bina yonetmeligi motoru.

Kabul kriteri: Ayni bina, iki farkli `RegulationProfile` ile kontrol
edildiginde farkli uygunluk sonuclari uretebilir (TS/ISO'da gecen bir
oda, daha kati bir profilde gecmeyebilir).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))

from harita.building_reconstruction import (
    FacadeGenerator,
    Room,
    RoomGenerator,
    RoomType,
    available_regulation_profiles,
    default_regulation_profile,
    get_regulation_profile,
    register_regulation_profile,
    strict_reference_profile,
)
from harita.core_engine.geometry_engine import Point2D, Polygon


class TestPhaseE19RegulationProfiles(unittest.TestCase):
    def test_default_profile_matches_legacy_behavior(self):
        poly = Polygon([Point2D(0, 0), Point2D(4, 0), Point2D(4, 4), Point2D(0, 4)])
        room = Room(polygon=poly, room_type=RoomType.SALON.value, room_id=0)
        legacy = RoomGenerator.check_compliance([room])
        explicit_default = RoomGenerator.check_compliance([room], profile=None)
        self.assertEqual(legacy.violation_count, explicit_default.violation_count)

    def test_strict_profile_rejects_room_that_default_accepts(self):
        # Salon: TR asgari 12 m2, strict 18 m2 -> 14 m2'lik oda TR'de
        # gecer, strict'te gecmez.
        poly = Polygon([Point2D(0, 0), Point2D(3.5, 0), Point2D(3.5, 4.0), Point2D(0, 4.0)])
        room = Room(polygon=poly, room_type=RoomType.SALON.value, room_id=0)
        self.assertAlmostEqual(room.area_m2, 14.0, places=3)

        default_report = RoomGenerator.check_compliance([room])
        strict_report = RoomGenerator.check_compliance([room], profile=strict_reference_profile())

        self.assertTrue(default_report.is_compliant)
        self.assertFalse(strict_report.is_compliant)
        self.assertGreater(strict_report.violation_count, default_report.violation_count)

    def test_facade_window_ratio_profile_parametrization(self):
        poly = Polygon([Point2D(0, 0), Point2D(10, 0), Point2D(10, 8), Point2D(0, 8)])
        facade = FacadeGenerator.generate(
            poly,
            building_type="konut",
            base_z=0.0,
            floor_height=3.0,
            seed=7,
            build_mesh=False,
        )
        default_report = FacadeGenerator.check_compliance(
            facade,
            poly,
            floor_height=3.0,
            floor_count=2,
            building_type="konut",
        )
        strict_report = FacadeGenerator.check_compliance(
            facade,
            poly,
            floor_height=3.0,
            floor_count=2,
            building_type="konut",
            profile=strict_reference_profile(),
        )
        self.assertGreaterEqual(strict_report.min_required_ratio, default_report.min_required_ratio)

    def test_registry_lookup_and_custom_profile_registration(self):
        names = available_regulation_profiles()
        self.assertIn("TR_PAIY_ISO_BYKHY", names)
        self.assertIn("STRICT_REFERENCE", names)

        custom = default_regulation_profile().with_overrides(name="CUSTOM_TEST_PROFILE")
        register_regulation_profile(custom)
        fetched = get_regulation_profile("CUSTOM_TEST_PROFILE")
        self.assertEqual(fetched.name, "CUSTOM_TEST_PROFILE")

    def test_unknown_profile_raises(self):
        with self.assertRaises(KeyError):
            get_regulation_profile("__does_not_exist__")


if __name__ == "__main__":
    unittest.main()
