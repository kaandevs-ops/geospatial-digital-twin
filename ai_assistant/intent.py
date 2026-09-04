"""
AI Assistant - Intent Model
===========================

Roadmap Phase 12 - "AI Assistant": dogal dil komutlarini (orn. "Bu binaya
bir kat daha ekle ve catiyi duz yap") Phase 8 Editor komutlarina esleyen
intent-parsing + command-dispatch katmani.

`CommandIntent`, tek bir atomik niyeti temsil eder: bir `action` (ne
yapilacak), bir `target` (hangi nesne uzerinde) ve `parameters` (ek
argumanlar, orn. roof_type, floor_count). Bir kullanici cumlesi genelde
birden fazla `CommandIntent` uretir ("kat ekle VE catiyi duz yap" -> 2
intent); bu yuzden `IntentParser.parse()` her zaman bir liste dondurur.

Bu katman bilerek Phase 3/8'in somut siniflarina (Building, RoofType...)
bagli DEGILDIR - sadece string/sozluk tabanli, serilestirilebilir bir
ara-temsil (intermediate representation) tanimlar. Boylece:
  * kural tabanli (regex/keyword) bir parser ile de,
  * ana projenin coklu-LLM router'ina bagli bir LLM parser ile de
üretilebilir; `AssistantOrchestrator` ikisini de ayni sekilde tuketir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class IntentAction(str, Enum):
    """Desteklenen atomik editor eylemleri (Phase 8 BuildingEditor/
    ObjectEditor/TerrainEditor/RoadEditor operasyonlarina 1:1 esleşir)."""

    ADD_FLOOR = "add_floor"
    REMOVE_FLOOR = "remove_floor"
    CHANGE_ROOF = "change_roof"
    CHANGE_FACADE = "change_facade"
    ADD_DOOR = "add_door"
    REMOVE_DOOR = "remove_door"
    ADD_WINDOW = "add_window"
    REMOVE_WINDOW = "remove_window"
    ANALYZE_BUILDING = "analyze_building"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class CommandIntent:
    """Tek bir atomik niyet.

    `parameters` icerigi `action`'a gore degisir, orn:
      * CHANGE_ROOF     -> {"roof_type": "flat", "pitch_deg": 0.0}
      * CHANGE_FACADE   -> {"material": "cam"}
      * ADD_FLOOR       -> {"count": 1, "height_m": 3.0}
      * ADD_DOOR/WINDOW -> {"floor_index": 0, "x": 1.0, "y": 2.0}
    """

    action: IntentAction
    target: str = "building"
    parameters: dict = field(default_factory=dict)
    confidence: float = 1.0
    raw_fragment: str = ""

    def __repr__(self) -> str:  # pragma: no cover - kolaylik icin
        return f"CommandIntent({self.action.value}, target={self.target!r}, params={self.parameters})"


@dataclass(slots=True)
class ParseResult:
    """`IntentParser.parse()` cikti zarfi: uretilen intent'ler + parser'in
    eslestiremedigi (bilinmeyen) parcalar (kullaniciya geri bildirim icin)."""

    intents: list[CommandIntent] = field(default_factory=list)
    unmatched_fragments: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.intents) > 0 and not any(i.action is IntentAction.UNKNOWN for i in self.intents)
