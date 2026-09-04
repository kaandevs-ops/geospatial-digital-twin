"""
mobility.mode_choice - Çok-Modlu Talep Dağıtım Motoru (Katman 3.3 madde 1)
==============================================================================

Roadmap V9 / Faz VI / Katman 3.3 madde 1:

"Çok-modlu talep motoru: Katman 2'nin ürettiği 'kim nereye ne zamanda
gidiyor' talebi, MultiModalRoute (yürüme+transit) ve traffic_simulation
(araç) arasında otomatik dağıtılır (gelir/mesafe/hava durumuna göre
'mode choice model')."

Bu modül yeni bir rota motoru YAZMAZ - `mobility.osm_demand_bridge.
ResolvedDemand`'i (somut origin/destination) girdi alır, her yolculuk için
`TravelMode` (walk/transit/vehicle) seçer ve seçilen moda göre çağıranın
zaten sahip olduğu motoru (`MultiModalRoute`, `traffic_simulation`,
`crowd_simulation`) beslemesi için bir `ModeChoiceResult` üretir - rota
hesaplamasının kendisi mod-seçiminden **ayrı bir sorumluluktur** (tek
sorumluluk ilkesi, roadmap'in "tekrar yazma yok" ilkesiyle tutarlı).

Gösterge disiplini: bu basit bir **logit-benzeri** (ama tam multinomial
logit değil - roadmap'in kendi kapsamıyla tutarlı bir basitleştirme)
skor/ eşik modelidir; gerçek bir kalibre edilmiş talep modeli değildir.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .osm_demand_bridge import ResolvedDemand


class TravelMode(str, Enum):
    WALK = "walk"
    TRANSIT = "transit"
    VEHICLE = "vehicle"


@dataclass(slots=True, frozen=True)
class ModeChoiceParams:
    """Mod seçim eşikleri - basit, açık, ayarlanabilir kurallar (kara
    kutu değil, roadmap ilkesi #5 ile tutarlı)."""

    walk_max_distance_m: float = 1200.0  # bu mesafeye kadar yürüme tercih edilir
    vehicle_min_distance_m: float = 3000.0  # bu mesafenin üstünde araç ağırlık kazanır
    #: Katman 2.3 ("hane gelir düzeyi -> araç sahipliği oranı") - basit bir
    #: çarpan olarak modellenir; gerçek bir hane geliri veri kümesi bu
    #: modülün kapsamı dışıdır (çağıran taraf sağlar, sessizce icat edilmez).
    vehicle_ownership_probability: float = 0.55
    #: Katman 3.3 madde 4 ("hava durumu etkisi") - yağışlı/düşük görüşte
    #: yürüme/bisiklet cazibesi azalır, transit/araç ağırlık kazanır.
    bad_weather_walk_penalty: float = 0.4  # 0-1, 1 = yürüme tamamen elenir


@dataclass(slots=True, frozen=True)
class ModeChoiceResult:
    demand: ResolvedDemand
    distance_m: float
    chosen_mode: TravelMode
    scores: dict[str, float]


def _euclidean_distance(demand: ResolvedDemand, origin_position) -> float:
    if origin_position is None:
        return 0.0
    return origin_position.distance_to(demand.destination_position)


class ModeChoiceModel:
    """Roadmap 3.3 madde 1'in üreteci. `choose()` tek bir yolculuk için,
    `choose_batch()` bir `ResolvedDemand` listesi için mod seçer.
    """

    def __init__(self, params: ModeChoiceParams | None = None, *, seed: int | None = None) -> None:
        import random

        self.params = params or ModeChoiceParams()
        self._rng = random.Random(seed)

    def choose(
        self,
        demand: ResolvedDemand,
        *,
        origin_position=None,
        has_vehicle_access: bool | None = None,
        bad_weather: bool = False,
    ) -> ModeChoiceResult:
        distance_m = _euclidean_distance(demand, origin_position)
        p = self.params

        walk_score = max(0.0, 1.0 - distance_m / max(p.walk_max_distance_m, 1e-6))
        if bad_weather:
            walk_score *= 1.0 - p.bad_weather_walk_penalty

        transit_score = 0.5  # nötr taban - gerçek transit erişilebilirliği (durak yakınlığı)
        # çağıran tarafın `transit_osm_bridge` ile ayrıca sağlayabileceği bir zenginleştirmedir;
        # burada sabit taban tutuluyor (roadmap kapsamının ötesine geçmemek için bilinçli sınır).
        if distance_m > p.walk_max_distance_m:
            transit_score += 0.2

        if has_vehicle_access is None:
            has_vehicle_access = self._rng.random() < p.vehicle_ownership_probability
        vehicle_score = 0.0
        if has_vehicle_access:
            vehicle_score = min(1.0, distance_m / max(p.vehicle_min_distance_m, 1e-6))
            if bad_weather:
                vehicle_score *= 1.15  # hava kötüyse araç cazibesi hafifçe artar

        scores = {
            TravelMode.WALK.value: walk_score,
            TravelMode.TRANSIT.value: transit_score,
            TravelMode.VEHICLE.value: vehicle_score,
        }
        chosen = TravelMode(max(scores, key=scores.get))
        return ModeChoiceResult(
            demand=demand, distance_m=distance_m, chosen_mode=chosen, scores=scores
        )

    def choose_batch(
        self,
        demands: list[ResolvedDemand],
        *,
        origin_position_lookup=None,
        bad_weather: bool = False,
    ) -> list[ModeChoiceResult]:
        origin_position_lookup = origin_position_lookup or {}
        results = []
        for demand in demands:
            origin_position = origin_position_lookup.get(demand.individual_id)
            results.append(
                self.choose(demand, origin_position=origin_position, bad_weather=bad_weather)
            )
        return results

    def mode_share(self, results: list[ModeChoiceResult]) -> dict[str, float]:
        """Sonuçlardan mod payı (%) - Katman 9'un rapor anlatıcısının
        tüketebileceği basit bir özet."""
        if not results:
            return {}
        counts: dict[str, int] = {}
        for r in results:
            counts[r.chosen_mode.value] = counts.get(r.chosen_mode.value, 0) + 1
        total = len(results)
        return {mode: count / total for mode, count in counts.items()}
