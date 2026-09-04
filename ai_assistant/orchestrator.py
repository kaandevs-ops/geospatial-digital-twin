"""
AI Assistant - Orchestrator
===========================

Roadmap Phase 12. `CommandIntent` listesini alir, Phase 8 `BuildingEditor`
komutlarina cevirir, `UndoRedoStack` uzerinden uygular. Bu, roadmap'in
"3D sahneyi dogal dille duzenle" ozelligini somutlastirir: `execute_text()`
tek cagriyla dogal dil -> parse -> editor komutlari -> uygulama -> sonuc
raporu zincirini calistirir.

Bilinen (`UNKNOWN` olmayan) ama gecerli parametreye sahip olmayan intent'ler
(orn. "catiyi X yap" - X taninmayan bir catı tipi) `AssistantError` ile
raporlanir, uygulama durdurulmaz - diger intent'ler yine de calistirilir;
tum basarisiz/basarili intent'ler `AssistantResult` icinde toplanir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..building_reconstruction.facade_generator import FacadeMaterial
from ..building_reconstruction.procedural_generator import Building
from ..building_reconstruction.roof_generator import RoofType
from ..editor.building_editor import BuildingEditor
from ..editor.commands import EditorCommand, UndoRedoStack
from .intent import CommandIntent, IntentAction, ParseResult
from .intent_parser import IntentParser


@dataclass(slots=True)
class IntentExecution:
    intent: CommandIntent
    success: bool
    message: str
    command: EditorCommand | None = None


@dataclass(slots=True)
class AssistantResult:
    executions: list[IntentExecution] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.executions) > 0 and all(e.success for e in self.executions)

    @property
    def messages(self) -> list[str]:
        return [e.message for e in self.executions]


class AssistantOrchestrator:
    """Dogal dil komutlarini bir `Building` uzerinde `BuildingEditor`
    araciligiyla uygular; her basarili islem `UndoRedoStack`'e kaydedilir
    (Ctrl+Z ile geri alinabilir - Phase 8 sozlesmesiyle birebir uyumlu)."""

    def __init__(
        self,
        building: Building,
        parser: IntentParser | None = None,
        undo_stack: UndoRedoStack | None = None,
    ) -> None:
        self.building = building
        self.parser = parser or IntentParser()
        self.undo_stack = undo_stack or UndoRedoStack()

    def execute_text(self, text: str) -> AssistantResult:
        parse_result: ParseResult = self.parser.parse(text)
        return self.execute_intents(parse_result.intents)

    def execute_intents(self, intents: list[CommandIntent]) -> AssistantResult:
        result = AssistantResult()
        for intent in intents:
            result.executions.append(self._execute_one(intent))
        return result

    # -- Tek intent -> editor komutu ------------------------------------ #

    def _execute_one(self, intent: CommandIntent) -> IntentExecution:
        try:
            if intent.action is IntentAction.UNKNOWN:
                return IntentExecution(intent, False, f"Anlaşılamadı: '{intent.raw_fragment}'")

            if intent.action is IntentAction.ADD_FLOOR:
                count = int(intent.parameters.get("count", 1))
                last_cmd: EditorCommand | None = None
                for _ in range(count):
                    cmd = BuildingEditor.add_floor(self.building)
                    self.undo_stack.execute(cmd)
                    last_cmd = cmd
                return IntentExecution(intent, True, f"{count} kat eklendi.", last_cmd)

            if intent.action is IntentAction.REMOVE_FLOOR:
                count = int(intent.parameters.get("count", 1))
                removed = 0
                last_cmd = None
                for _ in range(count):
                    if not self.building.floors:
                        break
                    cmd = BuildingEditor.remove_floor(self.building, len(self.building.floors) - 1)
                    self.undo_stack.execute(cmd)
                    last_cmd = cmd
                    removed += 1
                return IntentExecution(intent, removed > 0, f"{removed} kat silindi.", last_cmd)

            if intent.action is IntentAction.CHANGE_ROOF:
                roof_type_str = intent.parameters.get("roof_type")
                try:
                    roof_type = RoofType(roof_type_str)
                except ValueError:
                    return IntentExecution(intent, False, f"Bilinmeyen çatı tipi: {roof_type_str}")
                cmd = BuildingEditor.change_roof(self.building, roof_type)
                self.undo_stack.execute(cmd)
                return IntentExecution(
                    intent, True, f"Çatı '{roof_type.value}' olarak değiştirildi.", cmd
                )

            if intent.action is IntentAction.CHANGE_FACADE:
                material_str = intent.parameters.get("material")
                try:
                    material = FacadeMaterial(material_str)
                except ValueError:
                    return IntentExecution(
                        intent, False, f"Bilinmeyen cephe malzemesi: {material_str}"
                    )
                cmd = BuildingEditor.change_facade(self.building, material=material)
                self.undo_stack.execute(cmd)
                return IntentExecution(
                    intent, True, f"Cephe '{material.value}' olarak değiştirildi.", cmd
                )

            if intent.action is IntentAction.ANALYZE_BUILDING:
                summary = self._analyze()
                return IntentExecution(intent, True, summary)

            return IntentExecution(intent, False, f"Desteklenmeyen eylem: {intent.action.value}")
        except Exception as exc:  # savunmacı: tekil intent hatası tüm zinciri bozmasın
            return IntentExecution(intent, False, f"Hata: {exc}")

    def _analyze(self) -> str:
        b = self.building
        floor_count = len(b.floors)
        has_roof = getattr(b, "roof", None) is not None
        return (
            f"Bina analizi: {floor_count} kat, toplam yükseklik "
            f"{b.total_height_m:.1f} m, çatı {'mevcut' if has_roof else 'yok'}."
        )

    def undo(self) -> bool:
        return self.undo_stack.undo() is not None

    def redo(self) -> bool:
        return self.undo_stack.redo() is not None
