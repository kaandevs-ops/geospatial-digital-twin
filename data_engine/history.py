"""
History / Versioning
======================

Roadmap Phase 10 - "Undo", "Redo", "History", "Versioning".

`editor.commands.UndoRedoStack` zaten Phase 8'de "do/undo/redo" sözleşmesini
(Command Pattern) tanımladı ve editördeki mutasyonlar için kullanılıyor.
`editor/commands.py`'deki tasarım notunda belirtildiği gibi, Phase 10
geldiğinde aynı sözleşmeyi kullanan `History`/`Versioning` bu sınıfın
üzerine inşa edilir - burada tam olarak bunu yapıyoruz:

- `History`     : `UndoRedoStack`'i "dinleyen" salt-okunur bir olay günlüğü.
  Editördeki undo/redo işlemlerinden bağımsız olarak, sahnede *hiç geri
  alınmamış* olsa bile "ne zaman ne yapıldı" sorusuna cevap verir (bir
  `UndoRedoStack.undo()` çağrısı `_undo_stack`'ten siler ama `History` bunu
  hâlâ kayıtlı tutar - denetim/log amaçlı).
- `Versioning`  : anlık görüntü (snapshot) tabanlı sürümleme. Herhangi bir
  durumu (`state` - JSON-uyumlu dict, `DigitalTwin.to_dict()` çıktısı vb.)
  bir "versiyon" olarak saklar, `restore(version)` ile o ana geri döner.
  `UndoRedoStack`'in aksine adım-adım değil, tam-durum (full-state)
  tabanlıdır; bu yüzden `DigitalTwin` gibi büyük, karmaşık nesnelerin
  periyodik "kaydet" noktaları (checkpoint) için uygundur.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..editor.commands import EditorCommand, UndoRedoStack


# ======================================================================== #
# History
# ======================================================================== #

@dataclass(slots=True)
class HistoryEntry:
    """Tek bir olayın değişmez (immutable) kaydı."""

    label: str
    action: str  # "do" | "undo" | "redo"
    timestamp: float
    sequence: int


class History:
    """`UndoRedoStack` üzerindeki tüm do/undo/redo olaylarının kalıcı,
    asla-silinmeyen günlüğü.

    `UndoRedoStack.execute/undo/redo` çağrılarını sarmalayan (wrap eden)
    ince bir katmandır - stack'in kendi mantığına dokunmaz, yalnızca
    gözlemler. Böylece editördeki normal undo/redo akışı hiç değişmeden,
    "bu oturumda gerçekte neler yapıldı" denetim günlüğü elde edilir.
    """

    def __init__(self, stack: UndoRedoStack | None = None) -> None:
        self.stack = stack if stack is not None else UndoRedoStack()
        self._entries: list[HistoryEntry] = []
        self._sequence = 0

    def _record(self, label: str, action: str) -> None:
        self._sequence += 1
        self._entries.append(HistoryEntry(label=label, action=action,
                                           timestamp=time.time(), sequence=self._sequence))

    def execute(self, command: EditorCommand) -> EditorCommand:
        result = self.stack.execute(command)
        self._record(command.label, "do")
        return result

    def undo(self) -> EditorCommand | None:
        command = self.stack.undo()
        if command is not None:
            self._record(command.label, "undo")
        return command

    def redo(self) -> EditorCommand | None:
        command = self.stack.redo()
        if command is not None:
            self._record(command.label, "redo")
        return command

    def entries(self) -> list[HistoryEntry]:
        """Tüm günlüğü kronolojik sırayla döndürür (salt-okunur kopya)."""
        return list(self._entries)

    def entries_since(self, sequence: int) -> list[HistoryEntry]:
        """`sequence` numarasından sonraki tüm olayları döndürür (örn. bir
        istemcinin son senkronizasyondan bu yana kaçırdığı olayları
        getirmek için)."""
        return [e for e in self._entries if e.sequence > sequence]

    def last_sequence(self) -> int:
        return self._sequence

    def clear_log(self) -> None:
        """Yalnızca günlüğü temizler; `stack`'in undo/redo durumuna
        dokunmaz."""
        self._entries.clear()


# ======================================================================== #
# Versioning
# ======================================================================== #

@dataclass(slots=True)
class VersionSnapshot:
    """Tek bir anlık görüntü: sürüm numarası + zaman damgası + etiket +
    (derin kopyalanmış) durum."""

    version: int
    label: str
    timestamp: float
    state: Any


class Versioning:
    """Tam-durum (full-state) tabanlı sürümleme.

    `UndoRedoStack`'ten farklı olarak adım-adım komut değil, o anki tüm
    durumun (genelde bir `dict` - `DigitalTwin.to_dict()`, sahne serileştirme
    çıktısı vb.) derin kopyasını saklar. Büyük nesneler için `max_versions`
    ile bellek sınırlandırılabilir (en eski sürümler atılır - sürüm 0 /
    "baseline" her zaman korunur).
    """

    def __init__(self, max_versions: int | None = 50) -> None:
        self.max_versions = max_versions
        self._versions: list[VersionSnapshot] = []
        self._next_version = 0

    def snapshot(self, state: Any, label: str = "") -> VersionSnapshot:
        """Verilen `state`'in derin kopyasını yeni bir sürüm olarak kaydeder
        ve döndürür."""
        snap = VersionSnapshot(
            version=self._next_version, label=label or f"v{self._next_version}",
            timestamp=time.time(), state=copy.deepcopy(state),
        )
        self._versions.append(snap)
        self._next_version += 1
        if self.max_versions is not None and len(self._versions) > self.max_versions:
            # sürüm 0 (baseline) her zaman korunur, en eski (0 hariç) atılır
            if len(self._versions) > 1:
                del self._versions[1]
        return snap

    def restore(self, version: int) -> Any:
        """`version` numaralı sürümün durumunun derin kopyasını döndürür.

        Bilinmeyen bir sürüm için `KeyError` fırlatır (sessizce None dönmek
        yerine - versiyon numarası tipik olarak kullanıcı girdisidir ve
        yanlış numarayı sessizce yutmak veri kaybına yol açabilir).
        """
        for snap in self._versions:
            if snap.version == version:
                return copy.deepcopy(snap.state)
        raise KeyError(f"Versioning: sürüm {version} bulunamadı")

    def latest(self) -> VersionSnapshot | None:
        return self._versions[-1] if self._versions else None

    def list_versions(self) -> list[VersionSnapshot]:
        return list(self._versions)

    def diff_keys(self, version_a: int, version_b: int) -> dict[str, tuple[Any, Any]]:
        """İki sürüm arasında (yalnızca dict-tabanlı durumlar için)
        değişen üst-seviye anahtarları `{key: (eski_değer, yeni_değer)}`
        olarak döndürür. Derin/iç içe diff değil - hızlı "ne değişti"
        özeti için tasarlanmıştır."""
        state_a = self.restore(version_a)
        state_b = self.restore(version_b)
        if not isinstance(state_a, dict) or not isinstance(state_b, dict):
            raise TypeError("diff_keys yalnızca dict tabanlı state için desteklenir")
        keys = set(state_a.keys()) | set(state_b.keys())
        diffs = {}
        for k in keys:
            va, vb = state_a.get(k), state_b.get(k)
            if va != vb:
                diffs[k] = (va, vb)
        return diffs

    def __len__(self) -> int:
        return len(self._versions)
