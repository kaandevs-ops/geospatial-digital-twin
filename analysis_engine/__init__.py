"""
Analysis Engine
===============

Roadmap Phase 6 - "ANALYSIS ENGINE".

Kapsam: Measurement, Visibility, Sun Simulation, Environmental Simulation
(Wind/Rain/Flood/Heat Island/Noise/Reflection). Bu paket, Phase 1 (core_engine)
ve Phase 2 (mesh_engine/terrain_engine/lighting) çıktıları üzerine kurulan,
üretim çıktısı üretmeyen ama Phase 3-5'in ürettiği geometriyi/dijital ikizi
*analiz eden* salt-okunur bir katmandır (hiçbir alt modül, girdi Mesh3D/
Polygon/DigitalTwin nesnelerini mutasyona uğratmaz).

Alt paketler:
    measurement/        -> Distance, Area, Volume, Height, Angle, Slope
    visibility/          -> Ray Casting, LOS, Shadow, Blind Spot, Heatmap
    sun_simulation/      -> Solar Position/Path, Exposure, Roof Irradiance,
                             Shadow Projection
    environmental_sim/   -> Wind, Rain, Flood, Heat Island, Noise, Reflection
"""

from __future__ import annotations

from .decision_support import (
    INDICATIVE_DISCLAIMER,
    AccessibilityWarningEngine,
    Recommendation,
    RecommendationEngine,
    ScenarioComparison,
    SensitivityAnalyzer,
    SensitivityResult,
    compare_capacity_reports,
    compare_evacuation_results,
    what_if_close_exit_and_rerun,
)
from .environmental_sim import (
    FloodEstimation,
    FloodResult,
    GaussianPlumeSimulation,
    HeatIslandResult,
    HeatIslandSimulation,
    NoiseResult,
    NoiseSimulation,
    PasquillGiffordStability,
    PlumeConcentrationResult,
    RainRunoffResult,
    RainSimulation,
    ReflectionResult,
    ReflectionSimulation,
    ThermalComfort,
    ThermalComfortResult,
    WindField,
    WindSimulation,
)
from .measurement import MeasurementEngine, MeasurementResult
from .result_narrator import (
    narrate_capacity_report,
    narrate_evacuation_result,
    narrate_flood,
    narrate_heat_island,
    narrate_noise,
    narrate_scenario_comparison,
    narrate_solar_exposure,
)
from .sun_simulation import (
    IrradianceResult,
    RoofIrradiance,
    SeasonalSunPath,
    ShadowProjection,
    SolarExposure,
    SolarExposureResult,
    SolarPosition,
    SolarPositionCalculator,
)
from .visibility import (
    BlindSpot,
    BlindSpotAnalysis,
    LineOfSight,
    LOSResult,
    RayCasting,
    RayHit,
    SceneVisibilityIndex,
    ShadowAnalysis,
    ShadowAnalysisResult,
    VisibilityHeatmap,
    VisibilityHeatmapCell,
)

__all__ = [
    # measurement
    "MeasurementEngine",
    "MeasurementResult",
    # visibility
    "RayCasting",
    "RayHit",
    "LineOfSight",
    "LOSResult",
    "ShadowAnalysis",
    "ShadowAnalysisResult",
    "BlindSpotAnalysis",
    "BlindSpot",
    "VisibilityHeatmap",
    "VisibilityHeatmapCell",
    "SceneVisibilityIndex",
    # sun_simulation
    "SolarPosition",
    "SolarPositionCalculator",
    "SeasonalSunPath",
    "SolarExposure",
    "SolarExposureResult",
    "RoofIrradiance",
    "IrradianceResult",
    "ShadowProjection",
    # environmental_sim
    "WindSimulation",
    "WindField",
    "RainSimulation",
    "RainRunoffResult",
    "FloodEstimation",
    "FloodResult",
    "HeatIslandSimulation",
    "HeatIslandResult",
    "NoiseSimulation",
    "NoiseResult",
    "ReflectionSimulation",
    "ReflectionResult",
    "ThermalComfort",
    "ThermalComfortResult",
    # result_narrator
    "narrate_solar_exposure",
    "narrate_heat_island",
    "narrate_noise",
    "narrate_flood",
    "narrate_evacuation_result",
    "narrate_capacity_report",
    "narrate_scenario_comparison",
    # decision_support (Faz XI / Katman 9)
    "INDICATIVE_DISCLAIMER",
    "AccessibilityWarningEngine",
    "Recommendation",
    "RecommendationEngine",
    "ScenarioComparison",
    "SensitivityAnalyzer",
    "SensitivityResult",
    "compare_capacity_reports",
    "compare_evacuation_results",
    "what_if_close_exit_and_rerun",
]
