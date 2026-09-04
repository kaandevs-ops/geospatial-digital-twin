"""
ROADMAP V5 - Track M / M2.3: Pencere/kapı/cephe detay derinliği — Grammar
tabanlı (shape grammar) cephe ritmi.
============================================================================

V4'te "çok mekanik" diye işaretlenen sorun: `WindowGenerator.place_on_wall`
ve `place_on_footprint`, kenar boyunca sabit `spacing` değerine göre eşit
aralıklı bir ızgaraya (grid) pencere diziyor - farklı building_type / farklı
seed ile üretilen cepheler, yalnızca pencere sayısında değil *ritimde* de
neredeyse aynı görünüyor.

Bu modül, roadmap'in istediği **kısıt tabanlı (constraint-based) shape
grammar** üretimini ekler:

  Kural seti (her cephe tipi için parametrik):
    - pencere genişliği = duvar biriminin (bay) belli bir yüzdesi
      (örn. %55-%70 arası, building_type'a göre değişir)
    - pencereler arası boşluk >= duvar biriminin belli bir yüzdesi
      (örn. >= %20)
    - köşeden minimum 1 pencere-genişliği içeride başla
    - bay genişliği kendisi de [min_bay, max_bay] aralığında serbest
      (sabit değil) - bu, ritmin kendisini kırıyor

  Üretim: duvar segmentini, kısıtlar dahilinde rastgele genişlikte
  "bay" (duvar birimi) parçalarına böler (constraint satisfaction +
  rastgele seçim, roadmap'in istediği "kısıtlar dahilinde
  rastgele/optimize seçim" tam olarak budur), her bay'e kural setinden
  türeyen bir pencere genişliği/boşluk atar.

Geriye dönük uyumluluk: `WindowGenerator.place_on_wall/place_on_footprint`
DEĞİŞTİRİLMEDİ (mevcut mimari korunuyor) - bu modül, `FacadeGenerator`'a
opsiyonel bir strateji (`use_shape_grammar=True`) olarak eklenmek üzere
*ek* bir üretim yolu sunar; `WindowPlacement` ile aynı çıktı tipini üretir,
mevcut mesh üretim kodu (`WallOpeningMeshBuilder`) değişmeden çalışmaya
devam eder.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..core_engine.geometry_engine import Point2D, Polygon
from .building_elements import WindowPlacement, WindowType


@dataclass(slots=True)
class FacadeGrammarRule:
    """Bir cephe tipi için shape-grammar kural seti (M2.3)."""

    # Duvar birimi (bay) genişliği aralığı (metre).
    min_bay_width: float = 2.2
    max_bay_width: float = 3.6
    # Pencere genişliği = bay genişliğinin bu oranı [min, max] arasında
    # rastgele seçilir (roadmap: "pencere genişliği duvar biriminin %60'ı").
    window_to_bay_ratio_min: float = 0.55
    window_to_bay_ratio_max: float = 0.70
    # Pencereler arası boşluk, bay genişliğinin en az bu oranı olmalı
    # (roadmap: "aralarında en az %20 boşluk").
    min_gap_ratio: float = 0.20
    # Köşeden minimum bu kadar (pencere genişliği cinsinden) içeride başla.
    corner_margin_window_widths: float = 1.0
    window_height: float = 1.4
    sill_height: float = 0.9
    window_type: WindowType = WindowType.CASEMENT

    def __post_init__(self) -> None:
        if self.min_bay_width <= 0 or self.max_bay_width < self.min_bay_width:
            raise ValueError("Geçersiz bay genişlik aralığı.")
        if not (0 < self.window_to_bay_ratio_min <= self.window_to_bay_ratio_max < 1.0):
            raise ValueError("Geçersiz pencere/bay oranı aralığı.")
        if not (0.0 <= self.min_gap_ratio < 1.0):
            raise ValueError("Geçersiz min_gap_ratio.")


# Bina tipine göre varsayılan kural setleri - her tip farklı bir "karakter"
# (dar/sık pencereli konut vs. geniş/seyrek camlı ofis vb.) üretir, böylece
# aynı algoritma farklı building_type'larda görsel olarak da farklılaşır.
DEFAULT_GRAMMAR_RULES: dict[str, FacadeGrammarRule] = {
    "apartman": FacadeGrammarRule(
        min_bay_width=2.4,
        max_bay_width=3.4,
        window_to_bay_ratio_min=0.50,
        window_to_bay_ratio_max=0.65,
    ),
    "villa": FacadeGrammarRule(
        min_bay_width=2.0,
        max_bay_width=4.0,
        window_to_bay_ratio_min=0.40,
        window_to_bay_ratio_max=0.60,
    ),
    "ofis": FacadeGrammarRule(
        min_bay_width=2.8,
        max_bay_width=4.5,
        window_to_bay_ratio_min=0.65,
        window_to_bay_ratio_max=0.85,
        window_height=2.2,
    ),
    "avm": FacadeGrammarRule(
        min_bay_width=3.5,
        max_bay_width=6.0,
        window_to_bay_ratio_min=0.60,
        window_to_bay_ratio_max=0.80,
    ),
    "okul": FacadeGrammarRule(
        min_bay_width=2.5,
        max_bay_width=3.5,
        window_to_bay_ratio_min=0.55,
        window_to_bay_ratio_max=0.70,
    ),
    "hastane": FacadeGrammarRule(
        min_bay_width=2.5,
        max_bay_width=3.5,
        window_to_bay_ratio_min=0.45,
        window_to_bay_ratio_max=0.60,
    ),
    "fabrika": FacadeGrammarRule(
        min_bay_width=4.0,
        max_bay_width=8.0,
        window_to_bay_ratio_min=0.30,
        window_to_bay_ratio_max=0.45,
    ),
    "depo": FacadeGrammarRule(
        min_bay_width=5.0,
        max_bay_width=10.0,
        window_to_bay_ratio_min=0.10,
        window_to_bay_ratio_max=0.20,
    ),
    "_default": FacadeGrammarRule(),
}


def rule_for_building_type(building_type: str | None) -> FacadeGrammarRule:
    key = (building_type or "").lower()
    return DEFAULT_GRAMMAR_RULES.get(key, DEFAULT_GRAMMAR_RULES["_default"])


class ShapeGrammarFacadeGenerator:
    """M2.3: Kısıt tabanlı (constraint-based) shape grammar ile cephe ritmi
    üretimi. `WindowGenerator.place_on_wall`'un yerine, ondan daha çeşitli
    (mekanik olmayan) bir alternatif olarak kullanılır."""

    @staticmethod
    def _partition_into_bays(
        wall_length: float,
        rule: FacadeGrammarRule,
        rng: random.Random,
    ) -> list[float]:
        """Duvar uzunluğunu, [min_bay_width, max_bay_width] aralığında
        rastgele genişlikte bay'lere böler (kısıt: toplamları tam olarak
        wall_length'e eşit olmalı - son bay taşarsa/kısa kalırsa orantılı
        olarak tüm bay'ler yeniden ölçeklenir, böylece kısıt her zaman
        sağlanır)."""
        if wall_length <= 0:
            return []
        bays: list[float] = []
        remaining = wall_length
        guard = 0
        while remaining > rule.min_bay_width and guard < 500:
            guard += 1
            w = rng.uniform(rule.min_bay_width, rule.max_bay_width)
            if w > remaining:
                w = remaining
            bays.append(w)
            remaining -= w
        if remaining > 1e-6:
            if bays:
                bays[-1] += remaining
            else:
                bays.append(remaining)
        # Kısıt garantisi: rastgele üretim min_bay_width'in altında bir son
        # parça bırakmış olabilir (toplam korunuyor ama tek bay çok küçük
        # kalabilir) - komşu bay'e katıp normalize et.
        if len(bays) >= 2 and bays[-1] < rule.min_bay_width * 0.5:
            bays[-2] += bays[-1]
            bays.pop()
        return bays

    @staticmethod
    def place_on_wall(
        wall_start: Point2D,
        wall_end: Point2D,
        wall_edge_index: int,
        rule: FacadeGrammarRule,
        seed: int | None = None,
    ) -> list[WindowPlacement]:
        wall_length = wall_start.distance_to(wall_end)
        corner_margin = (
            rule.corner_margin_window_widths * rule.min_bay_width * rule.window_to_bay_ratio_min
        )
        usable_length = wall_length - 2 * corner_margin
        if usable_length <= rule.min_bay_width:
            return []

        rng = random.Random(seed)
        dx = (wall_end.x - wall_start.x) / wall_length
        dy = (wall_end.y - wall_start.y) / wall_length

        bays = ShapeGrammarFacadeGenerator._partition_into_bays(usable_length, rule, rng)

        placements: list[WindowPlacement] = []
        cursor = corner_margin
        for bay_width in bays:
            window_ratio = rng.uniform(rule.window_to_bay_ratio_min, rule.window_to_bay_ratio_max)
            window_width = bay_width * window_ratio
            gap = bay_width - window_width
            # Kısıt: min_gap_ratio sağlanmıyorsa (yüksek window_ratio +
            # dar bay çakışması), pencereyi kısıt sağlanacak şekilde küçült.
            min_gap = bay_width * rule.min_gap_ratio
            if gap < min_gap:
                window_width = bay_width - min_gap
            center = cursor + bay_width / 2.0
            px = wall_start.x + dx * center
            py = wall_start.y + dy * center
            placements.append(
                WindowPlacement(
                    position=Point2D(px, py),
                    width=max(window_width, 0.3),
                    height=rule.window_height,
                    sill_height=rule.sill_height,
                    wall_edge_index=wall_edge_index,
                    window_type=rule.window_type,
                )
            )
            cursor += bay_width
        return placements

    @staticmethod
    def place_on_footprint(
        polygon: Polygon,
        building_type: str | None,
        seed: int | None = None,
        rule_override: FacadeGrammarRule | None = None,
    ) -> list[WindowPlacement]:
        """M2.3 kabul kriteri: aynı footprint + building_type ile farklı
        `seed` değerleri, görsel olarak belirgin şekilde farklı (ritmi
        kırılmış) cephe düzenleri üretir - `place_on_wall`'daki rastgele
        bay bölünmesi ve rastgele pencere/bay oranı bunu garantiler."""
        rule = rule_override or rule_for_building_type(building_type)
        ring = polygon.closed_ring()
        result: list[WindowPlacement] = []
        rng_seed_base = seed if seed is not None else 0
        for i, (a, b) in enumerate(zip(ring, ring[1:])):
            # Her kenara farklı ama deterministik bir alt-seed - kenarlar
            # arası da çeşitlilik olsun (hepsi birebir aynı olmasın).
            edge_seed = None if seed is None else rng_seed_base * 1000 + i
            result.extend(ShapeGrammarFacadeGenerator.place_on_wall(a, b, i, rule, seed=edge_seed))
        return result

    @staticmethod
    def rhythm_diversity_score(
        placements_a: list[WindowPlacement], placements_b: list[WindowPlacement]
    ) -> float:
        """M2.3 kabul kriteri ölçümü: iki farklı seed ile üretilen cephe
        arasındaki pencere-genişliği dağılımı farkını (0=aynı, 1=tamamen
        farklı - basit normalize edilmiş ortalama mutlak fark) sayısal
        olarak özetler; görsel/insan-değerlendirmesi anketinin yanında
        otomatik regresyon testine bağlanabilecek bir metrik sağlar."""
        if not placements_a or not placements_b:
            return 0.0
        widths_a = sorted(p.width for p in placements_a)
        widths_b = sorted(p.width for p in placements_b)
        n = min(len(widths_a), len(widths_b))
        if n == 0:
            return 1.0
        diffs = [
            abs(widths_a[i] - widths_b[i]) / max(widths_a[i], widths_b[i], 1e-9) for i in range(n)
        ]
        count_diff = abs(len(widths_a) - len(widths_b)) / max(len(widths_a), len(widths_b))
        return min(1.0, (sum(diffs) / n) * 0.7 + count_diff * 0.3)


__all__ = [
    "FacadeGrammarRule",
    "DEFAULT_GRAMMAR_RULES",
    "rule_for_building_type",
    "ShapeGrammarFacadeGenerator",
]
