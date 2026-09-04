"""
Editor Commands
===============

Roadmap Phase 8 - "EditorCommand" (Undo/Redo için komut deseni).

Tüm editör alt modülleri (ObjectEditor, TerrainEditor, RoadEditor,
BuildingEditor) sahne üzerindeki her mutasyonu bir `EditorCommand` olarak
üretir. Bu, hem undo/redo'yu hem de (ileride Phase 10 Data Engine'in
`History`/`Versioning` mekanizmasıyla paylaşılacak) işlem günlüğünü mümkün
kılar. Phase 10 henüz uygulanmadığı için `UndoRedoStack` burada, bu fazın
ihtiyacını karşılayacak şekilde tanımlanır; Phase 10 geldiğinde aynı
sözleşmeyi (do/undo/redo) kullanan `History`/`Versioning` bu sınıfın
üzerine inşa edilir veya onu sarmalar.

Tasarım: klasik Command Pattern. Her komut `do()` ile uygulanır ve kendi
tersini geri almak için yeterli durumu (`_before` / `_after` state) saklar.
Komutlar yan etkisiz (pure) değildir - hedef nesneyi (`target`) doğrudan
mutasyona uğratırlar; bu yüzden `do()` / `undo()` idempotent olacak şekilde
yazılmıştır (tekrar tekrar çağrılabilir).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


class EditorCommand(ABC):
    """Tüm editör komutlarının temel sözleşmesi.

    Alt sınıflar `_do()` ve `_undo()` metodlarını uygular; bu sınıf
    `applied` bayrağı ile çift-uygulama / çift-geri-alma hatalarını önler.
    """

    label: str = "command"

    def __init__(self, label: str | None = None) -> None:
        if label is not None:
            self.label = label
        self._applied = False

    @property
    def applied(self) -> bool:
        return self._applied

    def do(self) -> None:
        if self._applied:
            return
        self._do()
        self._applied = True

    def undo(self) -> None:
        if not self._applied:
            return
        self._undo()
        self._applied = False

    @abstractmethod
    def _do(self) -> None:
        ...

    @abstractmethod
    def _undo(self) -> None:
        ...


class FunctionCommand(EditorCommand):
    """Genel amaçlı komut: rastgele bir do/undo çift fonksiyonundan komut
    üretir. `ObjectEditor` gibi alt modüller çoğu zaman bunu kullanır -
    her operasyon için ayrı bir `EditorCommand` alt sınıfı yazmak yerine
    kapanan (closure) fonksiyon çiftleri geçilir."""

    def __init__(self, do_fn: Callable[[], None], undo_fn: Callable[[], None], label: str = "op") -> None:
        super().__init__(label)
        self._do_fn = do_fn
        self._undo_fn = undo_fn

    def _do(self) -> None:
        self._do_fn()

    def _undo(self) -> None:
        self._undo_fn()


@dataclass(slots=True)
class CommandGroup(EditorCommand):
    """Birden fazla komutu tek bir undo/redo adımı olarak gruplar (örn.
    "Katı Sil" -> oda/koridor/kapı/pencere silme komutlarının tümü tek
    Ctrl+Z ile geri alınır)."""

    commands: list[EditorCommand] = field(default_factory=list)
    label: str = "group"
    _applied: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        EditorCommand.__init__(self, self.label)

    def add(self, command: EditorCommand) -> "CommandGroup":
        self.commands.append(command)
        return self

    def _do(self) -> None:
        for cmd in self.commands:
            cmd.do()

    def _undo(self) -> None:
        for cmd in reversed(self.commands):
            cmd.undo()


class UndoRedoStack:
    """Sınırlı boyutlu undo/redo yığını.

    - `execute(command)`: komutu uygular, undo yığınına ekler, redo
      yığınını temizler (yeni bir dal başladığı için eski redo geçmişi
      geçersizleşir - standart editör davranışı).
    - `undo()` / `redo()`: sırasıyla son işlemi geri alır / yeniden uygular.
    - `max_history`: bellek sınırlaması (0 veya None = sınırsız).
    """

    def __init__(self, max_history: int | None = 200) -> None:
        self.max_history = max_history
        self._undo_stack: list[EditorCommand] = []
        self._redo_stack: list[EditorCommand] = []

    def execute(self, command: EditorCommand) -> EditorCommand:
        command.do()
        self._undo_stack.append(command)
        self._redo_stack.clear()
        if self.max_history and len(self._undo_stack) > self.max_history:
            self._undo_stack.pop(0)
        return command

    def undo(self) -> EditorCommand | None:
        if not self._undo_stack:
            return None
        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)
        return command

    def redo(self) -> EditorCommand | None:
        if not self._redo_stack:
            return None
        command = self._redo_stack.pop()
        command.do()
        self._undo_stack.append(command)
        return command

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def clear(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()

    def history_labels(self) -> list[str]:
        """Undo yığınındaki komutların etiketlerini (eskiden yeniye)
        döndürür - bir "History" panelinde göstermek için."""
        return [c.label for c in self._undo_stack]

    def __len__(self) -> int:
        return len(self._undo_stack)
