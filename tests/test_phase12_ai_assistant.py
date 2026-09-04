"""Phase 12 (AI Assistant) için birim testleri."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import Building, BuildingType, Floor

from harita.ai_assistant import (
    AssistantOrchestrator,
    CommandIntent,
    IntentAction,
    IntentParser,
)


def _make_building():
    polygon = Polygon(points=[Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)])
    footprint = Footprint(polygon=polygon, building_type="apartments", floor_count=2, height_m=6.0)
    building = Building(footprint=footprint, building_type=BuildingType.APARTMAN)
    building.floors = [Floor(level=0, height_m=3.0), Floor(level=1, height_m=3.0)]
    return building


# ============================================================================ #
# intent_parser.py
# ============================================================================ #

class TestIntentParser:
    def test_add_floor_simple(self):
        result = IntentParser().parse("bir kat daha ekle")
        assert result.ok
        assert result.intents[0].action is IntentAction.ADD_FLOOR
        assert result.intents[0].parameters["count"] == 1

    def test_add_floor_numeric(self):
        result = IntentParser().parse("3 kat ekle")
        assert result.intents[0].parameters["count"] == 3

    def test_remove_floor(self):
        result = IntentParser().parse("son katı sil")
        assert result.intents[0].action is IntentAction.REMOVE_FLOOR

    def test_change_roof_flat(self):
        result = IntentParser().parse("çatıyı düz yap")
        assert result.intents[0].action is IntentAction.CHANGE_ROOF
        assert result.intents[0].parameters["roof_type"] == "flat"

    def test_change_roof_mansard(self):
        result = IntentParser().parse("çatıyı mansard yap")
        assert result.intents[0].parameters["roof_type"] == "mansard"

    def test_change_facade(self):
        result = IntentParser().parse("cepheyi cam yap")
        assert result.intents[0].action is IntentAction.CHANGE_FACADE
        assert result.intents[0].parameters["material"] == "cam"

    def test_multi_intent_conjunction(self):
        result = IntentParser().parse("bir kat daha ekle ve çatıyı düz yap")
        assert result.ok
        assert len(result.intents) == 2
        assert result.intents[0].action is IntentAction.ADD_FLOOR
        assert result.intents[1].action is IntentAction.CHANGE_ROOF

    def test_add_door_and_window(self):
        r1 = IntentParser().parse("kapı ekle")
        r2 = IntentParser().parse("pencere ekle")
        assert r1.intents[0].action is IntentAction.ADD_DOOR
        assert r2.intents[0].action is IntentAction.ADD_WINDOW

    def test_analyze(self):
        result = IntentParser().parse("binayı analiz et")
        assert result.intents[0].action is IntentAction.ANALYZE_BUILDING

    def test_unknown_fragment(self):
        result = IntentParser().parse("bilmediğim bir şey yap")
        assert not result.ok
        assert result.unmatched_fragments

    def test_llm_fallback_used_when_rule_based_fails(self):
        def fake_llm(fragment: str):
            return [{"action": "add_floor", "parameters": {"count": 2}}]

        parser = IntentParser(llm_fn=fake_llm)
        result = parser.parse("garip bir talep")
        assert result.ok
        assert result.intents[0].action is IntentAction.ADD_FLOOR
        assert result.intents[0].parameters["count"] == 2


# ============================================================================ #
# orchestrator.py
# ============================================================================ #

class TestAssistantOrchestrator:
    def test_execute_add_floor(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        result = orch.execute_text("bir kat daha ekle")
        assert result.success
        assert len(building.floors) == 3

    def test_execute_change_roof(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        result = orch.execute_text("çatıyı düz yap")
        assert result.success
        assert building.roof is not None

    def test_execute_change_facade(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        result = orch.execute_text("cepheyi metal yap")
        assert result.success
        assert building.facade is not None

    def test_execute_multi_command_sentence(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        result = orch.execute_text("bir kat daha ekle ve çatıyı düz yap")
        assert result.success
        assert len(building.floors) == 3
        assert building.roof is not None

    def test_undo_after_add_floor(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        orch.execute_text("bir kat daha ekle")
        assert len(building.floors) == 3
        assert orch.undo() is True
        assert len(building.floors) == 2

    def test_unknown_command_reports_failure_without_raising(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        result = orch.execute_text("uzaylılarla iletişim kur")
        assert not result.success
        assert "Anlaşılamadı" in result.messages[0]

    def test_invalid_roof_type_reported_gracefully(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        result = orch.execute_intents(
            [CommandIntent(action=IntentAction.CHANGE_ROOF, parameters={"roof_type": "not_a_roof"})]
        )
        assert not result.success
        assert "Bilinmeyen çatı tipi" in result.messages[0]

    def test_analyze_building_message(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        result = orch.execute_text("binayı analiz et")
        assert result.success
        assert "kat" in result.messages[0]

    def test_remove_floor_limited_by_available_floors(self):
        building = _make_building()
        orch = AssistantOrchestrator(building)
        result = orch.execute_text("5 kat sil")
        assert len(building.floors) == 0
        assert "2 kat silindi" in result.messages[0]
