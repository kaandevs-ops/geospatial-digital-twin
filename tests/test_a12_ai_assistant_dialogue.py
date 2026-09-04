"""Roadmap V2 - A12 (AI Assistant derinleştirme) - çok-turlu diyalog testleri."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction.footprint_parser import Footprint
from harita.building_reconstruction.procedural_generator import Building, BuildingType, Floor

from harita.ai_assistant import (
    BuildingRegistry,
    DialogueSession,
    IntentAction,
    UnknownBuildingError,
)


def _make_building(floor_count: int = 2) -> Building:
    polygon = Polygon(points=[Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)])
    footprint = Footprint(polygon=polygon, building_type="apartments", floor_count=floor_count, height_m=3.0 * floor_count)
    building = Building(footprint=footprint, building_type=BuildingType.APARTMAN)
    building.floors = [Floor(level=i, height_m=3.0) for i in range(floor_count)]
    return building


# ============================================================================ #
# BuildingRegistry
# ============================================================================ #

class TestBuildingRegistry:
    def test_register_and_lookup(self):
        registry = BuildingRegistry()
        b = _make_building()
        registry.register("A", b)
        assert registry.get_building("A") is b
        assert "A" in registry
        assert registry.names() == ["A"]

    def test_unknown_building_raises(self):
        registry = BuildingRegistry()
        registry.register("A", _make_building())
        try:
            registry.get_building("B")
            assert False, "should have raised"
        except UnknownBuildingError:
            pass

    def test_each_building_has_independent_undo_stack(self):
        registry = BuildingRegistry()
        registry.register("A", _make_building())
        registry.register("B", _make_building())
        orch_a = registry.get_orchestrator("A")
        orch_b = registry.get_orchestrator("B")
        assert orch_a is not orch_b
        assert orch_a.undo_stack is not orch_b.undo_stack


# ============================================================================ #
# Tek bina — belirsizlik yok, doğrudan uygulanır (geriye uyumlu davranış)
# ============================================================================ #

class TestSingleBuildingNoAmbiguity:
    def test_single_building_executes_immediately(self):
        registry = BuildingRegistry()
        building = _make_building(floor_count=2)
        registry.register("Tek Bina", building)
        session = DialogueSession(registry)

        result = session.send("bir kat daha ekle")

        assert result.needs_clarification is False
        assert result.building_name == "Tek Bina"
        assert len(building.floors) == 3

    def test_no_registered_building_gives_informative_reply(self):
        session = DialogueSession(BuildingRegistry())
        result = session.send("bir kat ekle")
        assert result.needs_clarification is False
        assert "kayıtlı bir bina yok" in result.reply


# ============================================================================ #
# Çok bina — Kabul kriteri senaryosu:
# "bir kat ekle" -> "hangi binaya?" -> "A binası" akışı
# ============================================================================ #

class TestMultiBuildingClarificationFlow:
    def test_acceptance_scenario_add_floor_then_clarify(self):
        registry = BuildingRegistry()
        building_a = _make_building(floor_count=2)
        building_b = _make_building(floor_count=4)
        registry.register("A", building_a)
        registry.register("B", building_b)
        session = DialogueSession(registry)

        # 1. Tur: belirsiz komut -> açıklayıcı soru
        r1 = session.send("bir kat daha ekle")
        assert r1.needs_clarification is True
        assert "Hangi binaya" in r1.reply
        assert "A" in r1.reply and "B" in r1.reply
        assert session.awaiting_clarification is True
        # Henüz hiçbir binaya uygulanmadı
        assert len(building_a.floors) == 2
        assert len(building_b.floors) == 4

        # 2. Tur: kullanıcı hedefi belirtir -> komut şimdi uygulanır
        r2 = session.send("A binası")
        assert r2.needs_clarification is False
        assert r2.building_name == "A"
        assert len(building_a.floors) == 3
        assert len(building_b.floors) == 4  # B binası etkilenmedi
        assert session.awaiting_clarification is False

    def test_explicit_building_name_in_first_message_skips_clarification(self):
        registry = BuildingRegistry()
        building_a = _make_building(floor_count=2)
        building_b = _make_building(floor_count=4)
        registry.register("A", building_a)
        registry.register("B", building_b)
        session = DialogueSession(registry)

        result = session.send("B binasına bir kat daha ekle")

        assert result.needs_clarification is False
        assert result.building_name == "B"
        assert len(building_b.floors) == 5
        assert len(building_a.floors) == 2

    def test_unresolvable_clarification_reply_reasks(self):
        registry = BuildingRegistry()
        registry.register("A", _make_building())
        registry.register("B", _make_building())
        session = DialogueSession(registry)

        session.send("çatıyı düz yap")
        assert session.awaiting_clarification is True

        r2 = session.send("bilmiyorum")
        assert r2.needs_clarification is True
        assert session.awaiting_clarification is True  # hâlâ bekliyor

        r3 = session.send("B binası")
        assert r3.needs_clarification is False
        assert r3.building_name == "B"

    def test_multi_step_dialogue_history_is_recorded(self):
        registry = BuildingRegistry()
        registry.register("A", _make_building())
        registry.register("B", _make_building())
        session = DialogueSession(registry)

        session.send("bir kat daha ekle")
        session.send("A binası")

        assert len(session.history) == 2
        assert session.history[0].needs_clarification is True
        assert session.history[1].needs_clarification is False
        assert session.history[1].building_name == "A"

    def test_change_roof_command_full_multi_turn_scenario(self):
        registry = BuildingRegistry()
        building_a = _make_building()
        building_b = _make_building()
        registry.register("A", building_a)
        registry.register("B", building_b)
        session = DialogueSession(registry)

        r1 = session.send("çatıyı mansard yap")
        assert r1.needs_clarification is True

        r2 = session.send("B binasını kastettim")
        assert r2.needs_clarification is False
        assert r2.building_name == "B"
        assert building_b.roof is not None

    def test_cancel_pending_clears_clarification_state(self):
        registry = BuildingRegistry()
        registry.register("A", _make_building())
        registry.register("B", _make_building())
        session = DialogueSession(registry)

        session.send("bir kat ekle")
        assert session.awaiting_clarification is True
        session.cancel_pending()
        assert session.awaiting_clarification is False

    def test_unknown_fragment_still_asks_for_target_when_ambiguous(self):
        """Anlaşılmayan bir cümle bile (UNKNOWN intent) bir bina adı
        içermiyorsa, mevcut davranış onu da "hangi binaya" akışına sokar —
        bu, DialogueSession'ın kendi sorumluluğu olan hedef-belirsizliği ile
        IntentParser'ın kendi sorumluluğu olan eylem-belirsizliğini
        birbirinden ayırdığını gösterir (ikisi karışmaz, IntentExecution
        seviyesinde "Anlaşılamadı" mesajı hedef netleşince görünür)."""
        registry = BuildingRegistry()
        registry.register("A", _make_building())
        registry.register("B", _make_building())
        session = DialogueSession(registry)

        r1 = session.send("bugün hava çok güzel")
        assert r1.needs_clarification is True

        r2 = session.send("A binası")
        assert r2.needs_clarification is False
        assert r2.assistant_result is not None
        assert r2.assistant_result.executions[0].intent.action is IntentAction.UNKNOWN
        assert "Anlaşılamadı" in r2.reply
