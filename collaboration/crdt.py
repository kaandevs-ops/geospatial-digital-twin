"""
CRDT — Çakışmasız Eşzamanlı Düzenleme (Conflict-free Replicated Data Type)
=============================================================================

ROADMAP_V2 Faz 19 kabul kriteri: "İki kullanıcı aynı binayı eşzamanlı
düzenler, çakışma otomatik ve kayıpsız çözülür."

Yaklaşım: **operational-transform (OT) yerine bilinçli olarak CRDT**
seçildi. Gerekçe: OT, merkezi bir sunucunun işlem sırasını (operation
order) tutması ve gelen her işlemi geçmiş işlemlere göre "dönüştürmesi"
gerektiği karmaşık, hataya açık bir algoritma ailesidir (bkz. Google Docs/
ShareDB'nin OT implementasyonlarının onlarca yıllık olgunlaşma süreci).
CRDT'ler ise matematiksel olarak **commutative + associative + idempotent**
birleştirme (merge) fonksiyonlarına dayanır: hangi sırayla, kaç kez
uygulanırsa uygulansın (ağ gecikmesi/yeniden gönderim dahil) sonuç
**garanti olarak aynıdır** — merkezi bir sıra-koordinatörüne ihtiyaç
duymaz, bu da WebSocket üzerinden çoklu istemci senaryosunda daha az
kırılgan bir tasarımdır.

İki CRDT tipi uygulanmıştır:

1. **`LWWRegister`** (Last-Write-Wins Register) — bina property'leri
   (yükseklik, kat sayısı, isim, malzeme...) gibi "tek değerli" alanlar
   için. Çakışma, `(timestamp, actor_id)` çiftinin sözlük sırasına göre
   çözülür — `actor_id` tie-break, aynı milisaniyede iki farklı istemciden
   gelen eşzamanlı yazımlarda bile **deterministik** (herkes aynı sonuca
   varır) bir sonuç garantiler.
2. **`ORSet`** (Observed-Remove Set) — bir binaya eklenen/çıkarılan
   nesnelerin (örn. kat listesi, oda listesi) kayıpsız birleştirilmesi
   için. Ekleme her zaman kazanır (bir eleman eşzamanlı hem eklenip hem
   silinirse, "ekleme" tercih edilir — kullanıcı niyetini kaybetmeme
   ilkesi, CRDT literatüründeki standart OR-Set davranışı).

`CRDTBuildingState`, bir binanın tüm düzenlenebilir alanlarını bu iki
ilkel üzerine kurarak temsil eder ve `merge()` ile iki replikanın
(örn. iki kullanıcının yerel state'i) birleştirilmesini sağlar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


# ======================================================================== #
# LWWRegister — tek değerli alanlar için "son yazan kazanır"
# ======================================================================== #

@dataclass(slots=True)
class LWWRegister(Generic[T]):
    """Tek bir değeri tutan, çakışmasız birleştirilebilir register.

    Sıralama anahtarı `(timestamp, actor_id)` — aynı timestamp'te bile
    `actor_id` sözlük sırasına göre deterministik bir kazanan seçilir,
    böylece hangi replikada hangi sırayla `merge()` çağrılırsa çağrılsın
    (commutative + associative + idempotent) sonuç her zaman aynıdır.
    """

    value: T
    timestamp: float
    actor_id: str

    def _key(self) -> tuple[float, str]:
        return (self.timestamp, self.actor_id)

    def set(self, value: T, timestamp: float, actor_id: str) -> "LWWRegister[T]":
        """Yerel bir yazma işlemi uygular — yalnızca yeni yazım "kazanırsa"
        değeri günceller, yoksa mevcut değeri korur. Her zaman kendi
        (muhtemelen değişmemiş) kopyasını döner (fluent API)."""
        candidate = LWWRegister(value=value, timestamp=timestamp, actor_id=actor_id)
        return self.merge(candidate)

    def merge(self, other: "LWWRegister[T]") -> "LWWRegister[T]":
        """İki register'ı çakışmasız birleştirir — büyük olan `_key()`
        kazanır. Commutative: `a.merge(b) == b.merge(a)`. Associative ve
        idempotent (aynı register'ı tekrar merge etmek sonucu değiştirmez)."""
        return self if self._key() >= other._key() else other

    # -- serilestirme (Faz D18: kalicilik) --------------------------- #
    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "timestamp": self.timestamp, "actor_id": self.actor_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LWWRegister[T]":
        return cls(value=data["value"], timestamp=data["timestamp"], actor_id=data["actor_id"])


# ======================================================================== #
# ORSet — çoklu-eleman koleksiyonları için (kat/oda listesi vb.)
# ======================================================================== #

@dataclass(slots=True)
class ORSet(Generic[T]):
    """Observed-Remove Set: her ekleme benzersiz bir "tag" ile işaretlenir;
    silme yalnızca o ana kadar *gözlemlenen* tag'leri "tombstone" (mezar
    taşı) kümesine ekler. Eşzamanlı ekle+sil çakışmasında **ekleme kazanır**
    (kullanıcı niyetini kaybetmeme ilkesi — bir kullanıcı bir kat eklerken
    diğeri binayı silmeye çalışıyorsa, ekleme korunur).
    """

    # element -> o elemanı ekleyen tag'ler kümesi (benzersiz (actor, seq) çiftleri)
    _adds: dict[T, set[tuple[str, int]]] = field(default_factory=dict)
    _tombstones: set[tuple[str, int]] = field(default_factory=set)

    def add(self, element: T, tag: tuple[str, int]) -> None:
        self._adds.setdefault(element, set()).add(tag)

    def remove(self, element: T) -> None:
        """`element`'i şu ana kadar gözlemlenen tüm tag'leriyle birlikte
        tombstone'a taşır — bu replikada (henüz görülmemiş) gelecekteki
        bir eşzamanlı `add()` bu remove'dan etkilenmez (yeni tag farklı
        olacağı için)."""
        tags = self._adds.get(element, set())
        self._tombstones |= tags

    def merge(self, other: "ORSet[T]") -> "ORSet[T]":
        """İki ORSet'i çakışmasız birleştirir (commutative/associative/
        idempotent) — yeni bir ORSet döner, `self`/`other` değiştirilmez."""
        merged = ORSet()
        merged._tombstones = self._tombstones | other._tombstones
        all_elements = set(self._adds) | set(other._adds)
        for el in all_elements:
            tags = self._adds.get(el, set()) | other._adds.get(el, set())
            merged._adds[el] = tags
        return merged

    def elements(self) -> set[T]:
        """Şu anda kümede olan (tombstone'lanmamış en az bir tag'i olan)
        elemanlar."""
        result = set()
        for el, tags in self._adds.items():
            if tags - self._tombstones:
                result.add(el)
        return result

    def __contains__(self, element: T) -> bool:
        return element in self.elements()

    def __len__(self) -> int:
        return len(self.elements())

    # -- serilestirme (Faz D18: kalicilik) --------------------------- #
    def to_dict(self) -> dict[str, Any]:
        """JSON-uyumlu (stdlib `json.dumps`) bir sozluge cevirir. Tag'ler
        `(actor, seq)` tuple'lari oldugundan `"actor:seq"` string'ine
        kodlanir (JSON'da tuple/set yerlestirilemez)."""
        return {
            "adds": {
                str(el): [f"{actor}:{seq}" for actor, seq in tags]
                for el, tags in self._adds.items()
            },
            "tombstones": [f"{actor}:{seq}" for actor, seq in self._tombstones],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ORSet[T]":
        def _decode_tag(raw: str) -> tuple[str, int]:
            actor, _, seq = raw.rpartition(":")
            return (actor, int(seq))

        result: "ORSet[T]" = cls()
        for el, raw_tags in data.get("adds", {}).items():
            result._adds[el] = {_decode_tag(t) for t in raw_tags}
        result._tombstones = {_decode_tag(t) for t in data.get("tombstones", [])}
        return result


# ======================================================================== #
# CRDTBuildingState — bir binanın tüm düzenlenebilir durumu
# ======================================================================== #

@dataclass(slots=True)
class CRDTBuildingState:
    """Bir binanın çakışmasız-birleştirilebilir tam durumu.

    - `fields`: `LWWRegister` ile korunan tekil alanlar (height_m, name,
      material, ...).
    - `floor_ids`: `ORSet` ile korunan kat kimlikleri kümesi.
    """

    building_key: str
    fields: dict[str, LWWRegister] = field(default_factory=dict)
    floor_ids: ORSet = field(default_factory=ORSet)
    _floor_tag_seq: int = 0

    # -- alan (field) yazma/okuma ------------------------------------------
    def set_field(self, name: str, value: Any, timestamp: float, actor_id: str) -> None:
        current = self.fields.get(name)
        candidate = LWWRegister(value=value, timestamp=timestamp, actor_id=actor_id)
        self.fields[name] = candidate if current is None else current.merge(candidate)

    def get_field(self, name: str, default: Any = None) -> Any:
        reg = self.fields.get(name)
        return reg.value if reg is not None else default

    # -- kat (floor) ekleme/çıkarma ------------------------------------------
    def add_floor(self, floor_id: str, actor_id: str) -> None:
        self._floor_tag_seq += 1
        self.floor_ids.add(floor_id, (actor_id, self._floor_tag_seq))

    def remove_floor(self, floor_id: str) -> None:
        self.floor_ids.remove(floor_id)

    @property
    def floors(self) -> set[str]:
        return self.floor_ids.elements()

    # -- birleştirme ------------------------------------------------------
    def merge(self, other: "CRDTBuildingState") -> "CRDTBuildingState":
        """İki replikayı (örn. iki kullanıcının yerel durumu) çakışmasız
        birleştirir. Commutative/associative/idempotent — bkz. modül
        docstring'i ve `tests/test_faz19_collaboration.py` (birleştirme
        sırası bağımsızlığı testleri)."""
        if self.building_key != other.building_key:
            raise ValueError(
                f"farkli bina anahtarlari birlestirilemez: "
                f"{self.building_key!r} != {other.building_key!r}"
            )
        merged_fields: dict[str, LWWRegister] = {}
        for name in set(self.fields) | set(other.fields):
            a, b = self.fields.get(name), other.fields.get(name)
            if a is None:
                merged_fields[name] = b
            elif b is None:
                merged_fields[name] = a
            else:
                merged_fields[name] = a.merge(b)

        merged = CRDTBuildingState(
            building_key=self.building_key,
            fields=merged_fields,
            floor_ids=self.floor_ids.merge(other.floor_ids),
            _floor_tag_seq=max(self._floor_tag_seq, other._floor_tag_seq),
        )
        return merged

    # -- serilestirme (Faz D18: kalicilik) --------------------------- #
    def to_dict(self) -> dict[str, Any]:
        """`persistence.db_backend.ProjectDatabase.save_object` ile
        uyumlu, stdlib `json.dumps`'a verilebilir bir sozluk uretir."""
        return {
            "building_key": self.building_key,
            "fields": {name: reg.to_dict() for name, reg in self.fields.items()},
            "floor_ids": self.floor_ids.to_dict(),
            "floor_tag_seq": self._floor_tag_seq,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CRDTBuildingState":
        return cls(
            building_key=data["building_key"],
            fields={
                name: LWWRegister.from_dict(raw) for name, raw in data.get("fields", {}).items()
            },
            floor_ids=ORSet.from_dict(data.get("floor_ids", {})),
            _floor_tag_seq=data.get("floor_tag_seq", 0),
        )


__all__ = [
    "LWWRegister",
    "ORSet",
    "CRDTBuildingState",
]
