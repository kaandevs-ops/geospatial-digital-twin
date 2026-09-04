"""
Macro & Automation System
=========================

Roadmap Phase 14 - "Makro ve otomasyon sistemi".

Phase 8 `EditorCommand`/`UndoRedoStack` deseninin üzerine kurulur: bir
`MacroRecorder`, kaydedilen komut dizisini (`EditorCommand.do()` çağrıları)
yakalar; `Macro.play()` bunları sırayla yeniden uygular. Zamanlanmış
(scheduled) veya olay-tetikli (event-triggered) otomasyon için
`AutomationRule` + `AutomationEngine` sağlanır - `EventSystem` ile entegre
çalışır (bir olay tetiklendiğinde bir makroyu otomatik oynatır).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .event_system import Event, EventSystem


@dataclass
class MacroStep:
    label: str
    action: Callable[[], Any]
    recorded_at: float = field(default_factory=time.time)


class Macro:
    """Kaydedilmiş komut/eylem dizisi; tekrar tekrar `play()` edilebilir."""

    def __init__(self, name: str, steps: list[MacroStep] | None = None) -> None:
        self.name = name
        self.steps: list[MacroStep] = list(steps) if steps else []
        self.run_count = 0
        self.last_run_error: str | None = None

    def add_step(self, label: str, action: Callable[[], Any]) -> None:
        self.steps.append(MacroStep(label=label, action=action))

    def play(self, stop_on_error: bool = True) -> list[Any]:
        results: list[Any] = []
        self.last_run_error = None
        for step in self.steps:
            try:
                results.append(step.action())
            except Exception as exc:  # noqa: BLE001
                self.last_run_error = f"{step.label}: {exc}"
                if stop_on_error:
                    raise
                results.append(None)
        self.run_count += 1
        return results

    def __len__(self) -> int:
        return len(self.steps)


class MacroRecorder:
    """`start()`/`capture()`/`stop()` ile bir `EditorCommand` dizisini yakalar."""

    def __init__(self) -> None:
        self._recording = False
        self._current: Macro | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self, name: str) -> None:
        self._current = Macro(name)
        self._recording = True

    def capture(self, label: str, action: Callable[[], Any]) -> Any:
        """Bir eylemi hem hemen çalıştırır hem de (kayıt açıksa) makroya ekler.

        Bu sayede kayıt sırasında kullanıcı normal şekilde çalışmaya devam
        eder; ayrıca eylem otomatik olarak makronun bir adımı olur.
        """
        result = action()
        if self._recording and self._current is not None:
            self._current.add_step(label, action)
        return result

    def stop(self) -> Macro:
        if not self._recording or self._current is None:
            raise RuntimeError("Aktif bir kayıt yok.")
        macro = self._current
        self._recording = False
        self._current = None
        return macro


@dataclass
class AutomationRule:
    """Bir olay deseni (`EventSystem` glob) gerçekleştiğinde çalışacak kural."""

    name: str
    event_pattern: str
    macro: Macro
    condition: Callable[[Event], bool] | None = None
    enabled: bool = True
    trigger_count: int = 0


class AutomationEngine:
    """`EventSystem` üzerinden `AutomationRule`'ları dinleyip makroları tetikler."""

    def __init__(self, event_system: EventSystem | None = None) -> None:
        self.events = event_system or EventSystem()
        self._rules: dict[str, AutomationRule] = {}
        self._unsubscribers: dict[str, Callable[[], None]] = {}

    def add_rule(self, rule: AutomationRule) -> None:
        self._rules[rule.name] = rule
        self._unsubscribers[rule.name] = self.events.subscribe(
            rule.event_pattern, self._make_handler(rule)
        )

    def _make_handler(self, rule: AutomationRule) -> Callable[[Event], None]:
        def _handler(event: Event) -> None:
            if not rule.enabled:
                return
            if rule.condition is not None and not rule.condition(event):
                return
            rule.macro.play(stop_on_error=False)
            rule.trigger_count += 1

        return _handler

    def remove_rule(self, name: str) -> None:
        unsub = self._unsubscribers.pop(name, None)
        if unsub:
            unsub()
        self._rules.pop(name, None)

    def list_rules(self) -> list[str]:
        return list(self._rules.keys())

    def get_rule(self, name: str) -> AutomationRule:
        return self._rules[name]
