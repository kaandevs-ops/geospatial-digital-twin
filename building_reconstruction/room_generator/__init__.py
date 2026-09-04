"""
Room Generator
==============

Roadmap Phase 3 - "Room Generator" - graph tabanlı.

Bir katın (floor) dikdörtgensel alanını Binary Space Partitioning (BSP)
ile alt-dikdörtgenlere böler, ardından bitişiklik listesi (adjacency list -
basit bir komşuluk grafiği, `networkx` gerekmez) üzerinden oda tipi ataması
yapar (Salon/Yatak/WC/Koridor/Mutfak/Lab/Ofis/Toplantı/Sunucu/Elektrik/
Depo/Garaj/Makine).

Deterministik/tekrarlanabilir çıktı için `seed` parametresi kullanılır.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from ...core_engine.geometry_engine import Point2D, Polygon
from ..regulations import RegulationProfile, default_profile


class RoomType(str, Enum):
    SALON = "salon"
    YATAK_ODASI = "yatak_odasi"
    WC = "wc"
    KORIDOR = "koridor"
    MUTFAK = "mutfak"
    LABORATUVAR = "laboratuvar"
    OFIS = "ofis"
    TOPLANTI = "toplanti"
    SUNUCU_ODASI = "sunucu_odasi"
    ELEKTRIK_ODASI = "elektrik_odasi"
    DEPO = "depo"
    GARAJ = "garaj"
    MAKINE_ODASI = "makine_odasi"


@dataclass(slots=True)
class Room:
    """Roadmap Phase 3 veri modeli: `Room`."""

    polygon: Polygon
    room_type: str  # RoomType.value
    room_id: int = 0
    neighbors: list[int] = field(default_factory=list)  # komşu room_id'ler

    @property
    def area_m2(self) -> float:
        return self.polygon.unsigned_area()


# Bina tipine göre kullanılabilecek oda tipi havuzu + ağırlıklar (rastgele
# seçimde daha 'gerçekçi' dağılım için).
_ROOM_POOLS: dict[str, list[tuple[RoomType, float]]] = {
    "apartman": [
        (RoomType.SALON, 3.0), (RoomType.YATAK_ODASI, 3.0), (RoomType.WC, 1.5),
        (RoomType.MUTFAK, 2.0), (RoomType.KORIDOR, 1.0),
    ],
    "villa": [
        (RoomType.SALON, 2.5), (RoomType.YATAK_ODASI, 3.5), (RoomType.WC, 2.0),
        (RoomType.MUTFAK, 1.5), (RoomType.KORIDOR, 1.0), (RoomType.GARAJ, 1.0),
    ],
    "ofis": [
        (RoomType.OFIS, 4.0), (RoomType.TOPLANTI, 2.0), (RoomType.WC, 1.0),
        (RoomType.KORIDOR, 2.0), (RoomType.SUNUCU_ODASI, 0.5),
        (RoomType.ELEKTRIK_ODASI, 0.5),
    ],
    "fabrika": [
        (RoomType.MAKINE_ODASI, 3.0), (RoomType.DEPO, 3.0),
        (RoomType.ELEKTRIK_ODASI, 1.0), (RoomType.KORIDOR, 1.0),
        (RoomType.OFIS, 1.0), (RoomType.WC, 0.5),
    ],
    "hastane": [
        (RoomType.KORIDOR, 3.0), (RoomType.LABORATUVAR, 1.5),
        (RoomType.OFIS, 1.0), (RoomType.WC, 2.0), (RoomType.DEPO, 1.0),
        (RoomType.ELEKTRIK_ODASI, 0.5),
    ],
    "okul": [
        (RoomType.OFIS, 1.0), (RoomType.KORIDOR, 3.0), (RoomType.WC, 2.0),
        (RoomType.DEPO, 1.0), (RoomType.TOPLANTI, 1.0),
    ],
    "depo": [
        (RoomType.DEPO, 6.0), (RoomType.KORIDOR, 1.0), (RoomType.ELEKTRIK_ODASI, 0.5),
    ],
    "_default": [
        (RoomType.SALON, 2.0), (RoomType.KORIDOR, 2.0), (RoomType.DEPO, 1.0),
        (RoomType.WC, 1.0), (RoomType.OFIS, 1.0),
    ],
}


# Roadmap V3 - D13: gerçek, alıntılanabilir standart/yönetmelik madde
# referanslarına dayalı asgari oda alanı (m²) ve asgari koridor genişliği
# (m) tablosu. Kaynaklar (tam metin kopyalanmadan, yalnızca sayısal eşik +
# madde referansı):
#
#   [PAİY-27] Planlı Alanlar İmar Yönetmeliği (RG 3.7.2017/30113), Madde 27
#             - konutlarda oturma odası, yatak odası, mutfak gibi mekanların
#             asgari net alanlarını ve ıslak hacim asgari alanlarını
#             tanımlar.
#   [ISO 21542] ISO 21542:2011 "Building construction - Accessibility and
#             usability of the built environment", Böl. 10 - erişilebilir
#             yürüme yolu/koridor asgari serbest genişliği 1200 mm.
#   [BYKHY]   Binaların Yangından Korunması Hakkında Yönetmelik (RG
#             19.12.2007/26735), kaçış yolları bölümü - koridor tipi kaçış
#             yollarında asgari serbest genişlik.
#
# Bu tablo genişletilebilir bir kural motorunun veri katmanıdır; madde
# metinleri kopyalanmamış, yalnızca sayısal eşik ve kaynak adı tutulmuştur.
MIN_ROOM_AREA_M2: dict[str, float] = {
    RoomType.SALON.value: 12.0,  # [PAİY-27] oturma odası asgari net alan
    RoomType.YATAK_ODASI.value: 9.0,  # [PAİY-27] tek kişilik yatak odası asgari net alan
    RoomType.WC.value: 2.5,  # [PAİY-27] ıslak hacim (WC) asgari net alan
    RoomType.MUTFAK.value: 6.0,  # [PAİY-27] mutfak asgari net alan
    RoomType.KORIDOR.value: 1.5,  # alan değil; asıl kısıt MIN_CORRIDOR_WIDTH_M genişliğidir
    RoomType.LABORATUVAR.value: 12.0,  # [PAİY-27] derslik/çalışma mekânı asgari net alan
    RoomType.OFIS.value: 6.0,  # [PAİY-27] büro/çalışma odası asgari net alan
    RoomType.TOPLANTI.value: 10.0,  # [PAİY-27] toplantı/derslik benzeri mekân asgari net alan
    RoomType.SUNUCU_ODASI.value: 4.0,  # [PAİY-27] tesisat/teknik hacim asgari net alan
    RoomType.ELEKTRIK_ODASI.value: 2.0,  # [PAİY-27] tesisat/teknik hacim asgari net alan (küçük)
    RoomType.DEPO.value: 4.0,  # [PAİY-27] depo/kiler asgari net alan
    RoomType.GARAJ.value: 12.5,  # [PAİY-27] tek araçlık kapalı garaj asgari net alan
    RoomType.MAKINE_ODASI.value: 6.0,  # [PAİY-27] tesisat/teknik hacim asgari net alan
}

# [ISO 21542] Böl. 10 + [BYKHY] kaçış yolu hükümleri: koridor/kaçış yolu
# asgari serbest genişliği 1200 mm (1.2 m).
MIN_CORRIDOR_WIDTH_M = 1.2

# Kaynak künyesi - README'de ve raporlarda gösterim için.
THRESHOLD_SOURCES: dict[str, str] = {
    "PAİY-27": "Planlı Alanlar İmar Yönetmeliği, Madde 27 (RG 3.7.2017/30113)",
    "ISO 21542": "ISO 21542:2011, Bölüm 10 (erişilebilir koridor genişliği)",
    "BYKHY": "Binaların Yangından Korunması Hakkında Yönetmelik (RG 19.12.2007/26735)",
}


@dataclass(slots=True)
class RoomComplianceIssue:
    room_id: int
    room_type: str
    reason: str


@dataclass(slots=True)
class RoomComplianceReport:
    """`RoomGenerator.check_compliance()` çıktısı."""

    total_rooms: int
    issues: list[RoomComplianceIssue] = field(default_factory=list)

    @property
    def is_compliant(self) -> bool:
        return not self.issues

    @property
    def violation_count(self) -> int:
        return len(self.issues)


@dataclass(slots=True)
class _Rect:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    def to_polygon(self) -> Polygon:
        return Polygon([
            Point2D(self.min_x, self.min_y),
            Point2D(self.max_x, self.min_y),
            Point2D(self.max_x, self.max_y),
            Point2D(self.min_x, self.max_y),
        ])

    def touches(self, other: "_Rect", eps: float = 1e-6) -> bool:
        """İki dikdörtgen bir kenar boyunca komşu mu (bitişik mi)?"""
        x_overlap = min(self.max_x, other.max_x) - max(self.min_x, other.min_x)
        y_overlap = min(self.max_y, other.max_y) - max(self.min_y, other.min_y)
        vertically_adjacent = abs(self.max_x - other.min_x) < eps or abs(other.max_x - self.min_x) < eps
        horizontally_adjacent = abs(self.max_y - other.min_y) < eps or abs(other.max_y - self.min_y) < eps
        return (vertically_adjacent and y_overlap > eps) or (horizontally_adjacent and x_overlap > eps)


class RoomGenerator:
    """BSP (Binary Space Partitioning) + adjacency-graph tabanlı oda üretimi."""

    @staticmethod
    def generate(
        floor_polygon: Polygon,
        building_type: str = "_default",
        min_room_size: float = 3.0,
        max_depth: int = 6,
        seed: int | None = None,
    ) -> list[Room]:
        """`floor_polygon`'un bounding-box'ını BSP ile böler ve her yaprağa
        (leaf) bir `Room` atar. Konkav taban için önce axis-aligned
        bounding-box kullanılır (gerçek konkav-BSP `data_engine` fazındaki
        QuadTree ile birlikte ileri iterasyonda genişletilebilir)."""
        rng = random.Random(seed)
        xs = [p.x for p in floor_polygon.points]
        ys = [p.y for p in floor_polygon.points]
        root = _Rect(min(xs), min(ys), max(xs), max(ys))

        leaves = RoomGenerator._bsp_split(root, rng, min_room_size, max_depth)
        rooms = RoomGenerator._assign_types(leaves, building_type, rng)
        RoomGenerator._build_adjacency(rooms)
        return rooms

    # ------------------------------------------------------------------ #
    # BSP
    # ------------------------------------------------------------------ #
    @staticmethod
    def _bsp_split(rect: _Rect, rng: random.Random, min_size: float, depth: int) -> list[_Rect]:
        if depth <= 0 or rect.width < min_size * 2 or rect.height < min_size * 2:
            return [rect]

        split_vertical = rect.width >= rect.height
        if split_vertical:
            low = rect.min_x + min_size
            high = rect.max_x - min_size
            if low >= high:
                return [rect]
            cut = rng.uniform(low, high)
            left = _Rect(rect.min_x, rect.min_y, cut, rect.max_y)
            right = _Rect(cut, rect.min_y, rect.max_x, rect.max_y)
        else:
            low = rect.min_y + min_size
            high = rect.max_y - min_size
            if low >= high:
                return [rect]
            cut = rng.uniform(low, high)
            left = _Rect(rect.min_x, rect.min_y, rect.max_x, cut)
            right = _Rect(rect.min_x, cut, rect.max_x, rect.max_y)

        return (
            RoomGenerator._bsp_split(left, rng, min_size, depth - 1)
            + RoomGenerator._bsp_split(right, rng, min_size, depth - 1)
        )

    # ------------------------------------------------------------------ #
    # Tip ataması (ağırlıklı rastgele - seed ile tekrarlanabilir)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _assign_types(rects: list[_Rect], building_type: str, rng: random.Random) -> list[Room]:
        pool = _ROOM_POOLS.get(building_type, _ROOM_POOLS["_default"])
        types = [t for t, _ in pool]
        weights = [w for _, w in pool]

        rooms: list[Room] = []
        for i, rect in enumerate(rects):
            chosen = rng.choices(types, weights=weights, k=1)[0]
            rooms.append(Room(polygon=rect.to_polygon(), room_type=chosen.value, room_id=i))
        RoomGenerator._ensure_corridor(rooms, rects)
        return rooms

    @staticmethod
    def _ensure_corridor(rooms: list[Room], rects: list[_Rect]) -> None:
        """En az bir oda koridor olmalı (dolaşım için); en büyük odayı
        koridora çevirir eğer hiç koridor yoksa ve oda sayısı >= 3."""
        if len(rooms) < 3:
            return
        if any(r.room_type == RoomType.KORIDOR.value for r in rooms):
            return
        largest_idx = max(range(len(rects)), key=lambda i: rects[i].width * rects[i].height)
        rooms[largest_idx].room_type = RoomType.KORIDOR.value

    # ------------------------------------------------------------------ #
    # Bitişiklik grafiği
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_adjacency(rooms: list[Room]) -> None:
        rects = [
            _Rect(
                min(p.x for p in r.polygon.points), min(p.y for p in r.polygon.points),
                max(p.x for p in r.polygon.points), max(p.y for p in r.polygon.points),
            )
            for r in rooms
        ]
        for i in range(len(rooms)):
            for j in range(i + 1, len(rooms)):
                if rects[i].touches(rects[j]):
                    rooms[i].neighbors.append(rooms[j].room_id)
                    rooms[j].neighbors.append(rooms[i].room_id)

    @staticmethod
    def total_area(rooms: list[Room]) -> float:
        return sum(r.area_m2 for r in rooms)

    @staticmethod
    def rooms_by_type(rooms: list[Room], room_type: RoomType | str) -> list[Room]:
        value = room_type.value if isinstance(room_type, RoomType) else room_type
        return [r for r in rooms if r.room_type == value]

    # ------------------------------------------------------------------ #
    # Roadmap V2 - A3: TS/ISO benzeri asgari alan/genişlik uygunluk denetimi
    # ------------------------------------------------------------------ #
    @staticmethod
    def check_compliance(
        rooms: list[Room], profile: RegulationProfile | None = None
    ) -> RoomComplianceReport:
        """Her oda için asgari alan kuralını, koridor tipindeki odalar
        için ayrıca asgari genişlik kuralını (bounding-box kısa kenarı
        üzerinden) denetler.

        Roadmap V4 - Faz E19: eşikler artık `RegulationProfile` üzerinden
        parametriktir. `profile=None` verilirse (geriye uyumlu varsayılan)
        modül seviyesindeki TS/ISO tabanlı `MIN_ROOM_AREA_M2` /
        `MIN_CORRIDOR_WIDTH_M` sabitleriyle birebir aynı olan
        `default_profile()` kullanılır - mevcut davranış değişmez.
        """
        active_profile = profile if profile is not None else default_profile()
        issues: list[RoomComplianceIssue] = []
        for room in rooms:
            min_area = active_profile.room_area_threshold(room.room_type)
            if min_area is not None and room.area_m2 < min_area:
                issues.append(RoomComplianceIssue(
                    room_id=room.room_id,
                    room_type=room.room_type,
                    reason=(
                        f"alan {room.area_m2:.2f}m2 < asgari {min_area:.2f}m2 "
                        f"[PAİY-27 | profil={active_profile.name}]"
                    ),
                ))
            if room.room_type == RoomType.KORIDOR.value:
                xs = [p.x for p in room.polygon.points]
                ys = [p.y for p in room.polygon.points]
                short_side = min(max(xs) - min(xs), max(ys) - min(ys))
                if short_side < active_profile.min_corridor_width_m:
                    issues.append(RoomComplianceIssue(
                        room_id=room.room_id,
                        room_type=room.room_type,
                        reason=(
                            f"koridor genisligi {short_side:.2f}m < asgari "
                            f"{active_profile.min_corridor_width_m:.2f}m "
                            f"[ISO 21542/BYKHY | profil={active_profile.name}]"
                        ),
                    ))
        return RoomComplianceReport(total_rooms=len(rooms), issues=issues)
