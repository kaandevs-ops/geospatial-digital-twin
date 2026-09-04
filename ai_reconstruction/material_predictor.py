"""
AI Material Predictor
=======================

Roadmap Phase 4 - "AIMaterialPredictor".

Duvar / Cam / Metal / Çatı / Yüzey malzeme sınıflandırması.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..building_reconstruction.facade_generator import FacadeMaterial
from ..building_reconstruction.roof_generator import RoofType
from .predictor import Predictor


class SurfaceClass(str, Enum):
    """Sınıflandırılan yüzey kategorileri."""

    DUVAR = "duvar"
    CAM = "cam"
    METAL = "metal"
    CATI = "cati"
    YUZEY = "yuzey"  # zemin/döşeme gibi diğer genel yüzeyler


@dataclass(slots=True)
class MaterialPrediction:
    surface_class: SurfaceClass
    material: FacadeMaterial
    confidence: float


# Çatı tipine göre en olası çatı malzemesi (FacadeMaterial sözlüğü çatı
# için de yeniden kullanılıyor - roof "material" kavramsal olarak facade
# malzemeleriyle aynı kümededir: metal/kompozit/vb.).
_ROOF_TYPE_TO_MATERIAL: dict[RoofType, FacadeMaterial] = {
    RoofType.FLAT: FacadeMaterial.BETON,
    RoofType.HIP: FacadeMaterial.TUGLA,
    RoofType.GABLE: FacadeMaterial.TUGLA,
    RoofType.CROSS_GABLE: FacadeMaterial.TUGLA,
    RoofType.MANSARD: FacadeMaterial.METAL,
    RoofType.PYRAMID: FacadeMaterial.TUGLA,
    RoofType.SAWTOOTH: FacadeMaterial.METAL,
    RoofType.INDUSTRIAL: FacadeMaterial.METAL,
    RoofType.MODERN: FacadeMaterial.KOMPOZIT,
    RoofType.SOLAR: FacadeMaterial.METAL,
    RoofType.GREEN: FacadeMaterial.KOMPOZIT,
}

# building_type -> her SurfaceClass için en olası FacadeMaterial.
_TYPE_SURFACE_MATERIALS: dict[str, dict[SurfaceClass, FacadeMaterial]] = {
    "office": {
        SurfaceClass.DUVAR: FacadeMaterial.BETON, SurfaceClass.CAM: FacadeMaterial.CAM,
        SurfaceClass.METAL: FacadeMaterial.METAL, SurfaceClass.YUZEY: FacadeMaterial.KOMPOZIT,
    },
    "house": {
        SurfaceClass.DUVAR: FacadeMaterial.TAS, SurfaceClass.CAM: FacadeMaterial.CAM,
        SurfaceClass.METAL: FacadeMaterial.METAL, SurfaceClass.YUZEY: FacadeMaterial.AHSAP,
    },
    "industrial": {
        SurfaceClass.DUVAR: FacadeMaterial.ENDUSTRIYEL, SurfaceClass.CAM: FacadeMaterial.CAM,
        SurfaceClass.METAL: FacadeMaterial.METAL, SurfaceClass.YUZEY: FacadeMaterial.BETON,
    },
    "_default": {
        SurfaceClass.DUVAR: FacadeMaterial.BETON, SurfaceClass.CAM: FacadeMaterial.CAM,
        SurfaceClass.METAL: FacadeMaterial.METAL, SurfaceClass.YUZEY: FacadeMaterial.KOMPOZIT,
    },
}


class MaterialHeuristicPredictor:
    """Varsayılan, bağımlılıksız `Predictor` uygulaması."""

    def predict(self, features: dict) -> dict:
        surface = SurfaceClass(features["surface_class"])
        building_type = (features.get("building_type") or "_default").lower()

        if surface == SurfaceClass.CATI:
            roof_type = features.get("roof_type")
            material = _ROOF_TYPE_TO_MATERIAL.get(
                RoofType(roof_type) if roof_type else RoofType.FLAT, FacadeMaterial.BETON,
            )
            confidence = 0.7 if roof_type else 0.4
        else:
            table = _TYPE_SURFACE_MATERIALS.get(building_type, _TYPE_SURFACE_MATERIALS["_default"])
            material = table.get(surface, FacadeMaterial.BETON)
            confidence = 0.75 if building_type in _TYPE_SURFACE_MATERIALS else 0.5

        return {"material": material.value, "confidence": confidence}


class AIMaterialPredictor:
    """Yüzey kategorisi + bina bağlamı -> `MaterialPrediction`."""

    def __init__(self, predictor: Predictor | None = None) -> None:
        self._predictor = predictor or MaterialHeuristicPredictor()

    def predict(
        self,
        surface_class: SurfaceClass,
        building_type: str | None = None,
        roof_type: RoofType | None = None,
    ) -> MaterialPrediction:
        features = {
            "surface_class": surface_class.value,
            "building_type": building_type,
            "roof_type": roof_type.value if roof_type else None,
        }
        result = self._predictor.predict(features)
        return MaterialPrediction(
            surface_class=surface_class,
            material=FacadeMaterial(result["material"]),
            confidence=result["confidence"],
        )

    def predict_all_surfaces(
        self, building_type: str | None = None, roof_type: RoofType | None = None,
    ) -> dict[SurfaceClass, MaterialPrediction]:
        return {
            sc: self.predict(sc, building_type=building_type, roof_type=roof_type)
            for sc in SurfaceClass
        }
