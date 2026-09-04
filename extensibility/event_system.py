"""
Event System
============

Roadmap Phase 14 - "Olay (event) sistemi".

Basit senkron pub/sub. Ana projenin (varsa) global event bus'ına
`bridge_to` ile bağlanabilir - bu modül dış bağımlılık gerektirmez, saf
stdlib. Wildcard (`"*"`) dinleyiciler tüm olayları görür (loglama, plugin
hook'ları için kullanışlı - bkz. `PluginManager.on_event`).
"""

from __future__ import annotations

import fnmatch
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    name: str
    payload: Any = None
    timestamp: float = field(default_factory=time.time)
    source: str | None = None


Listener = Callable[[Event], None]


class EventSystem:
    """Senkron, isim-bazlı (glob destekli) pub/sub olay veri yolu."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Listener]] = {}
        self._history: list[Event] = []
        self._history_limit = 500
        self._bridges: list[Callable[[Event], None]] = []

    def subscribe(self, pattern: str, listener: Listener) -> Callable[[], None]:
        """`pattern` bir tam isim ya da glob (`building.*`, `*`) olabilir.

        Geriye, aboneliği iptal eden bir `unsubscribe()` fonksiyonu döner.
        """
        self._listeners.setdefault(pattern, []).append(listener)

        def _unsubscribe() -> None:
            listeners = self._listeners.get(pattern, [])
            if listener in listeners:
                listeners.remove(listener)

        return _unsubscribe

    def unsubscribe_all(self, pattern: str) -> None:
        self._listeners.pop(pattern, None)

    def emit(self, name: str, payload: Any = None, source: str | None = None) -> Event:
        event = Event(name=name, payload=payload, source=source)
        self._record(event)
        for pattern, listeners in list(self._listeners.items()):
            if fnmatch.fnmatchcase(name, pattern):
                for listener in list(listeners):
                    listener(event)
        for bridge in self._bridges:
            bridge(event)
        return event

    def _record(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history.pop(0)

    def history(self, pattern: str = "*") -> list[Event]:
        return [e for e in self._history if fnmatch.fnmatchcase(e.name, pattern)]

    def bridge_to(self, sink: Callable[[Event], None]) -> None:
        """Her olayı harici bir sink'e (örn. ana projenin event bus'ı) iletir."""
        self._bridges.append(sink)

    def clear(self) -> None:
        self._listeners.clear()
        self._history.clear()
        self._bridges.clear()


# Süreç genelinde tekil (singleton) varsayılan veri yolu - modüller arası
# gevşek bağlı iletişim için (örn. MacroSystem <-> ThemeSystem).
default_bus = EventSystem()
