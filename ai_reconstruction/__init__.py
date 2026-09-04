"""
AI Reconstruction (Phase 4)
=============================

Roadmap Phase 4 - "AI RECONSTRUCTION".

İki katmanlı tasarım:
    (a) kural/istatistik tabanlı `*HeuristicPredictor` sınıfları -
        bağımlılıksız, hemen çalışır, varsayılan davranıştır.
    (b) `Predictor` Protocol'ü - kullanıcı kendi eğitilmiş ML modelini
        (`predictor=` parametresiyle) her bileşene takabilir.

Alt modüller:
    predictor               - ortak `Predictor` Protocol'ü
    building_analyzer         - AIBuildingAnalyzer (yükseklik/kat/kullanım/stil/yaş/cephe)
    roof_predictor             - AIRoofPredictor (çatı tipi olasılık dağılımı)
    interior_layout             - AIInteriorLayout (RoomGenerator üzerine varyasyon)
    material_predictor           - AIMaterialPredictor (yüzey malzeme sınıflandırması)
    environment_generator         - AIEnvironmentGenerator (Poisson-disk çevre objeleri)

Teknik spesifikasyon: `docs/PHASE_SPECS.md` (Phase 4 bölümü).
"""

from __future__ import annotations

from .predictor import Predictor

from .building_analyzer import (
    AIBuildingAnalyzer, ArchitecturalStyle, BuildingAnalysis,
    ClimateZone, HeuristicPredictor,
)
from .roof_predictor import AIRoofPredictor, RoofHeuristicPredictor, RoofPrediction
from .interior_layout import AIInteriorLayout, InteriorLayoutVariant
from .room_labeling import RoomLabel, suggest_room_labels
from .material_predictor import (
    AIMaterialPredictor, MaterialHeuristicPredictor, MaterialPrediction, SurfaceClass,
)
from .environment_generator import (
    AIEnvironmentGenerator, EnvironmentObject, EnvironmentObjectType,
)
from .height_model import (
    HeightRegressionModel, MLAssistedHeightPredictor, ModelNotTrainedError,
    HeightBenchmarkReport, generate_synthetic_training_set,
    train_default_height_model, benchmark_height_predictors,
)
from .onnx_predictor import (
    ImageBasedPredictor, OnnxBackendUnavailable, OnnxInferenceResult,
    OnnxModelShapeError, DEFAULT_ROOF_TYPES as ONNX_DEFAULT_ROOF_TYPES,
)
from .material_bridge import (
    DEFAULT_MATERIAL_CACHE_DIR, resolve_pbr_material, resolve_building_materials,
)

__all__ = [
    "Predictor",
    "AIBuildingAnalyzer", "ArchitecturalStyle", "BuildingAnalysis",
    "ClimateZone", "HeuristicPredictor",
    "AIRoofPredictor", "RoofHeuristicPredictor", "RoofPrediction",
    "AIInteriorLayout", "InteriorLayoutVariant",
    "RoomLabel", "suggest_room_labels",
    "AIMaterialPredictor", "MaterialHeuristicPredictor", "MaterialPrediction", "SurfaceClass",
    "AIEnvironmentGenerator", "EnvironmentObject", "EnvironmentObjectType",
    "HeightRegressionModel", "MLAssistedHeightPredictor", "ModelNotTrainedError",
    "HeightBenchmarkReport", "generate_synthetic_training_set",
    "train_default_height_model", "benchmark_height_predictors",
    "ImageBasedPredictor", "OnnxBackendUnavailable", "OnnxInferenceResult",
    "OnnxModelShapeError", "ONNX_DEFAULT_ROOF_TYPES",
    "DEFAULT_MATERIAL_CACHE_DIR", "resolve_pbr_material", "resolve_building_materials",
]
