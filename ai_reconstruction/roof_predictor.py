"""
AI Roof Predictor
==================

Roadmap Phase 4 - "AIRoofPredictor".

Bina tipi + iklim bölgesi -> çatı tipi olasılık dağılımı.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..building_reconstruction.roof_generator import RoofType
from .building_analyzer import ClimateZone
from .predictor import Predictor


@dataclass(slots=True)
class RoofPrediction:
    """Olasılık dağılımı (`RoofType` -> olasılık, toplam ~1.0) + en olası tip."""

    probabilities: dict[RoofType, float]

    @property
    def most_likely(self) -> RoofType:
        return max(self.probabilities, key=self.probabilities.get)

    def top_n(self, n: int = 3) -> list[tuple[RoofType, float]]:
        return sorted(self.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:n]


# building_type -> {RoofType: ağırlık}. İklim bölgesine göre çarpan
# `_CLIMATE_MODIFIERS` ile uygulanır.
_BASE_WEIGHTS: dict[str, dict[RoofType, float]] = {
    "apartments": {RoofType.FLAT: 0.55, RoofType.HIP: 0.25, RoofType.GABLE: 0.15, RoofType.MODERN: 0.05},
    "house": {RoofType.HIP: 0.35, RoofType.GABLE: 0.35, RoofType.PYRAMID: 0.15, RoofType.MANSARD: 0.10, RoofType.FLAT: 0.05},
    "office": {RoofType.FLAT: 0.65, RoofType.MODERN: 0.25, RoofType.HIP: 0.10},
    "commercial": {RoofType.FLAT: 0.7, RoofType.MODERN: 0.2, RoofType.SAWTOOTH: 0.1},
    "industrial": {RoofType.INDUSTRIAL: 0.5, RoofType.SAWTOOTH: 0.3, RoofType.FLAT: 0.2},
    "warehouse": {RoofType.INDUSTRIAL: 0.6, RoofType.FLAT: 0.3, RoofType.SAWTOOTH: 0.1},
    "hospital": {RoofType.FLAT: 0.6, RoofType.MODERN: 0.3, RoofType.HIP: 0.1},
    "school": {RoofType.HIP: 0.4, RoofType.GABLE: 0.3, RoofType.FLAT: 0.3},
    "_default": {RoofType.FLAT: 0.4, RoofType.HIP: 0.3, RoofType.GABLE: 0.2, RoofType.MODERN: 0.1},
}

# İklim bölgesine göre çarpan (ör. kutup bölgede düz çatı azalır, çünkü kar
# yükü tahliyesi için eğimli çatı tercih edilir; tropikal bölgede yağmur
# tahliyesi için eğimli/hip çatı avantajlı).
_CLIMATE_MODIFIERS: dict[ClimateZone, dict[RoofType, float]] = {
    ClimateZone.TROPIKAL: {RoofType.HIP: 1.3, RoofType.GABLE: 1.2, RoofType.FLAT: 0.7},
    ClimateZone.ILIMAN: {},  # nötr - değişiklik yok
    ClimateZone.KARASAL: {RoofType.GABLE: 1.2, RoofType.MANSARD: 1.2, RoofType.FLAT: 0.85},
    ClimateZone.KUTUP: {RoofType.GABLE: 1.4, RoofType.PYRAMID: 1.3, RoofType.FLAT: 0.4},
}


class RoofHeuristicPredictor:
    """Varsayılan, bağımlılıksız `Predictor` uygulaması."""

    def predict(self, features: dict) -> dict:
        building_type = (features.get("building_type") or "_default").lower()
        climate = ClimateZone(features.get("climate_zone", ClimateZone.ILIMAN.value))

        base = dict(_BASE_WEIGHTS.get(building_type, _BASE_WEIGHTS["_default"]))
        modifiers = _CLIMATE_MODIFIERS.get(climate, {})

        weighted = {rt: w * modifiers.get(rt, 1.0) for rt, w in base.items()}
        total = sum(weighted.values()) or 1.0
        normalized = {rt: w / total for rt, w in weighted.items()}
        return {"probabilities": {rt.value: p for rt, p in normalized.items()}}


class AIRoofPredictor:
    """Bina tipi + iklim bölgesi -> `RoofPrediction`."""

    def __init__(self, predictor: Predictor | None = None) -> None:
        self._predictor = predictor or RoofHeuristicPredictor()

    def predict(
        self,
        building_type: str | None,
        climate_zone: ClimateZone = ClimateZone.ILIMAN,
    ) -> RoofPrediction:
        features = {
            "building_type": building_type,
            "climate_zone": climate_zone.value,
        }
        result = self._predictor.predict(features)
        probabilities = {RoofType(k): v for k, v in result["probabilities"].items()}
        return RoofPrediction(probabilities=probabilities)

    @staticmethod
    def climate_zone_for_latitude(lat: float) -> ClimateZone:
        """Kaba enlem -> iklim bölgesi eşlemesi (gerçek Köppen sınıflandırması
        yerine basit bir yaklaşım; harici iklim verisi `Predictor` ile
        entegre edilebilir)."""
        abs_lat = abs(lat)
        if abs_lat < 23.5:
            return ClimateZone.TROPIKAL
        if abs_lat < 45.0:
            return ClimateZone.ILIMAN
        if abs_lat < 66.5:
            return ClimateZone.KARASAL
        return ClimateZone.KUTUP
