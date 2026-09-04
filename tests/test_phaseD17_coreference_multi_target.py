"""
ROADMAP_V3 — Faz D17: AI Assistant coreference + coklu-hedef intent testleri.

Kabul kriteri: "A binasina bir kat ekle, B'ye de aynisini yap" gibi
cok-hedefli bir senaryo, her iki binaya da dogru komutu uygulayarak
gecer.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.ai_assistant import BuildingRegistry, DialogueSession
from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import Building, BuildingType, Floor
from harita.core_engine.geometry_engine import Point2D, Polygon


def _make_building(floor_count: int = 2) -> Building:
    polygon = Polygon(points=[Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)])
    footprint = Footprint(
        polygon=polygon,
        building_type="apartments",
        floor_count=floor_count,
        height_m=3.0 * floor_count,
    )
    building = Building(footprint=footprint, building_type=BuildingType.APARTMAN)
    building.floors = [Floor(level=i, height_m=3.0) for i in range(floor_count)]
    return building


def _registry_ab() -> BuildingRegistry:
    registry = BuildingRegistry()
    registry.register("A", _make_building())
    registry.register("B", _make_building())
    return registry


class TestMultiTargetExplicitCommand:
    def test_two_buildings_each_with_explicit_command(self) -> None:
        registry = _registry_ab()
        session = DialogueSession(registry)
        a_before = len(registry.get_building("A").floors)
        b_before = len(registry.get_building("B").floors)

        result = session.send("A binasina bir kat ekle, B binasina da bir kat ekle")

        assert not result.needs_clarification
        assert len(registry.get_building("A").floors) == a_before + 1
        assert len(registry.get_building("B").floors) == b_before + 1
        assert "A" in result.reply and "B" in result.reply


class TestCoreferenceAppliesLastIntent:
    def test_coreference_onu_da_repeats_last_command(self) -> None:
        registry = _registry_ab()
        session = DialogueSession(registry)
        a_before = len(registry.get_building("A").floors)
        b_before = len(registry.get_building("B").floors)

        result = session.send("A binasina bir kat ekle, B'ye de onu da yap")

        assert len(registry.get_building("A").floors) == a_before + 1
        # coreference: B icin de ayni komut (kat ekleme) tekrarlanmali
        assert len(registry.get_building("B").floors) == b_before + 1
        assert not result.needs_clarification

    def test_coreference_aynisini_yap_repeats_last_command(self) -> None:
        registry = _registry_ab()
        session = DialogueSession(registry)
        a_before = len(registry.get_building("A").floors)
        b_before = len(registry.get_building("B").floors)

        result = session.send("A binasina bir kat daha ekle, B binasina da aynisini yap")

        assert len(registry.get_building("A").floors) == a_before + 1
        assert len(registry.get_building("B").floors) == b_before + 1
        assert not result.needs_clarification

    def test_coreference_roof_change_repeats_across_buildings(self) -> None:
        registry = _registry_ab()
        session = DialogueSession(registry)

        result = session.send("A binasinin catisini duz yap, B'ye de aynisini yap")

        assert not result.needs_clarification
        # her iki bina icin de ayni cati-degistirme komutu calistirilmali
        # (basarili/basarisiz olmasi bina geometrisine bagli olabilir,
        # ancak coreference sayesinde HER IKI binaya da komut uygulanmis
        # olmali - yaniti her iki bina adini da icermeli).
        assert "A" in result.reply and "B" in result.reply


class TestSingleTargetStillWorksWithMultipleRegistered:
    def test_single_explicit_name_untouched_by_multi_target_logic(self) -> None:
        registry = _registry_ab()
        session = DialogueSession(registry)
        a_before = len(registry.get_building("A").floors)
        b_before = len(registry.get_building("B").floors)

        result = session.send("A binasina bir kat ekle")

        assert len(registry.get_building("A").floors) == a_before + 1
        assert len(registry.get_building("B").floors) == b_before  # B degismedi
        assert result.building_name == "A"

    def test_ambiguous_message_still_asks_clarification(self) -> None:
        registry = _registry_ab()
        session = DialogueSession(registry)

        result = session.send("bir kat daha ekle")

        assert result.needs_clarification is True


class TestMultiTargetThreeBuildings:
    def test_three_buildings_sequential_commands(self) -> None:
        registry = BuildingRegistry()
        registry.register("A", _make_building())
        registry.register("B", _make_building())
        registry.register("C", _make_building())
        session = DialogueSession(registry)

        result = session.send(
            "A binasina bir kat ekle, B binasina da bir kat ekle, C'ye de aynisini yap"
        )

        assert len(registry.get_building("A").floors) == 3
        assert len(registry.get_building("B").floors) == 3
        assert len(registry.get_building("C").floors) == 3
        assert not result.needs_clarification
