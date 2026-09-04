"""
AI Interior Layout
====================

Roadmap Phase 4 - "AIInteriorLayout".

Phase 3 `RoomGenerator`'ın üstüne rastgele seed + ağırlıklı kural
varyasyonu (her üretimde farklı plan).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..core_engine.geometry_engine import Polygon
from ..building_reconstruction.room_generator import Room, RoomGenerator, RoomType


@dataclass(slots=True)
class InteriorLayoutVariant:
    """Tek bir üretim denemesi: `seed` + üretilen odalar + kalite skoru."""

    seed: int
    rooms: list[Room]
    diversity_score: float  # oda tipi çeşitliliği (Shannon entropy tabanlı, 0-1)


class AIInteriorLayout:
    """`RoomGenerator` üzerine varyasyon katmanı: her çağrıda (farklı seed
    ile) farklı ama geçerli bir kat planı üretir; opsiyonel olarak birden
    çok aday üretip en 'çeşitli' (monoton olmayan) planı seçer."""

    def __init__(self, base_seed: int | None = None) -> None:
        self._rng = random.Random(base_seed)

    def generate_variant(
        self,
        floor_polygon: Polygon,
        building_type: str = "_default",
        min_room_size: float = 3.0,
        seed: int | None = None,
    ) -> InteriorLayoutVariant:
        used_seed = seed if seed is not None else self._rng.randrange(1_000_000)
        rooms = RoomGenerator.generate(
            floor_polygon, building_type=building_type,
            min_room_size=min_room_size, seed=used_seed,
        )
        return InteriorLayoutVariant(
            seed=used_seed, rooms=rooms,
            diversity_score=self._diversity(rooms),
        )

    def generate_alternatives(
        self,
        floor_polygon: Polygon,
        building_type: str = "_default",
        min_room_size: float = 3.0,
        n_variants: int = 5,
    ) -> list[InteriorLayoutVariant]:
        """`n_variants` farklı seed ile alternatif plan üretir (kullanıcı
        alternatifler arasından seçebilsin diye - roadmap: 'İç mekan
        alternatifleri üret')."""
        variants = []
        for _ in range(n_variants):
            seed = self._rng.randrange(1_000_000)
            variants.append(self.generate_variant(
                floor_polygon, building_type, min_room_size, seed=seed,
            ))
        return variants

    def best_variant(self, variants: list[InteriorLayoutVariant]) -> InteriorLayoutVariant:
        """En yüksek çeşitlilik skoruna (en 'dengeli' oda dağılımına)
        sahip varyantı döner."""
        return max(variants, key=lambda v: v.diversity_score)

    @staticmethod
    def _diversity(rooms: list[Room]) -> float:
        """Shannon entropy tabanlı, 0 (tek tip oda) - 1 (maksimum çeşitlilik)
        arası normalize edilmiş çeşitlilik skoru."""
        if not rooms:
            return 0.0
        counts: dict[str, int] = {}
        for r in rooms:
            counts[r.room_type] = counts.get(r.room_type, 0) + 1
        n = len(rooms)
        import math
        entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
        max_entropy = math.log2(len(RoomType)) or 1.0
        return min(1.0, entropy / max_entropy)
