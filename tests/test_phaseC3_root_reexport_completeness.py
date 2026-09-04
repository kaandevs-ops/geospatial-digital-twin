"""Roadmap V4 - Track C / C3: Üst seviye `harita/__init__.py` re-export
tamamlama.

Denetim maddesi #3: `building_reconstruction`, `ai_reconstruction`,
`persistence`, `render_engine`, `app_shell`, `collaboration` alt paketleri
daha önce üst seviyeden hiç re-export edilmiyordu - "tek giriş noktası"
ilkesi fiilen kısmen ihlal ediliyordu.

Bu test dosyası iki şeyi doğrular:
1. Roadmap V4'te açıkça örneklenen sınıflar tek bir `from harita import ...`
   satırıyla, hiçbir alt paket yolu bilmeden çalışır.
2. "Unutma koruması" (C2 ile aynı desen): her üst-düzey alt-paket dizininin
   (dinamik olarak keşfedilen) EN AZ bir public sınıfı `dir(harita)` /
   `harita.__all__` içinde bulunur - gelecekte yeni bir faz eklenip
   re-export'un unutulması durumunda bu test kırmızı olur.
"""
from __future__ import annotations

import os
from pathlib import Path

import harita

REPO_ROOT = Path(harita.__file__).resolve().parent

# Roadmap V4/C4 ile arşivlenen ölü kod - kasıtlı olarak hariç tutulur.
EXCLUDED_SUBPACKAGES = {"harita_modelleme", "archive"}


def _top_level_subpackages() -> set[str]:
    result = set()
    for entry in os.listdir(REPO_ROOT):
        full = REPO_ROOT / entry
        if entry.startswith(".") or entry in EXCLUDED_SUBPACKAGES:
            continue
        if full.is_dir() and (full / "__init__.py").exists():
            result.add(entry)
    return result


# Her alt paket için, o alt paketten (doğrudan ya da bilinen bir takma adla)
# üst seviyede beklenen en az bir sembol. Bu harita, C3'ün "en az bir public
# sınıf" kabul kriterini somutlaştırır.
EXPECTED_AT_LEAST_ONE = {
    "core_engine": {"CoordinateSystem", "GeoJSONParser", "TileEngine"},
    "mesh_engine": {"Mesh3D", "MeshBuilder"},
    "terrain_engine": {"HeightmapGrid", "TerrainMeshGenerator"},
    "material_engine": {"PBRMaterial", "TextureLoader"},
    "lighting": {"SolarPositionCalculator", "SunLight"},
    "digital_twin": {"DigitalTwin", "TwinHierarchy"},
    "analysis_engine": {"MeasurementEngine", "ShadowAnalysis"},
    "mobility": {"AStar", "TrafficSimulator"},
    "editor": {"ObjectEditor", "TerrainEditor", "RoadEditor"},
    "visualization": {"RenderPipeline", "Camera"},
    "data_engine": {"QuadTree", "RTree", "ObjectCache"},
    "export": {"OBJExporter", "ReportBuilder"},
    "ai_assistant": {"IntentParser", "AssistantOrchestrator"},
    "performance": {"TaskScheduler", "GPUProfiler"},
    "extensibility": {"ModuleManager", "PluginManager"},
    "building_reconstruction": {"FootprintParser", "ProceduralBuildingGenerator", "RoofGenerator"},
    "ai_reconstruction": {"HeightRegressionModel", "MLAssistedHeightPredictor", "AIBuildingAnalyzer"},
    "persistence": {"ProjectManager", "ProjectDatabase"},
    "render_engine": {"Scene", "scene_from_meshes"},
    "app_shell": {"AppSession", "build_app_router"},
    "collaboration": {"AuthService", "CollaborationHub", "CRDTBuildingState"},
    "observability": {"StructuredLogger", "MetricsRegistry"},
    "i18n": {"translate", "Translator", "detect_language"},
    "vegetation": {"TreeGenerator", "VegetationScatterer", "VegetationInstance"},
    "physics": {"RigidBox", "PhysicsWorld", "TowerStabilityScenario"},
    "security": {"RateLimitExceededError", "SlidingWindowRateLimiter"},
    "hazard_data": {"AFADClient", "USGSClient", "score_building_risk", "prioritize_evacuation"},
    "street_furniture": {"StreetFurnitureGenerator", "BridgeGenerator", "WaterSurfaceGenerator"},
    "climate_data": {"OpenMeteoClient"},
    "religious_structures": {"ReligiousStructureGenerator", "ReligiousStructureItem"},
    "commerce_props": {"CommercePropsGenerator", "OutdoorSeatingItem"},
    "sport_recreation": {"SportRecreationGenerator", "PlaygroundItem"},
    "power_infrastructure": {"PowerInfrastructureGenerator", "CommunicationTowerItem"},
    "feature_survey": {"FieldSurveySession", "FieldPoint"},
    "offline_cache": {"TileCache", "LocalPlaceIndex"},
    # ROADMAP_V9 denetim düzeltmesi: simulation_core (O.1) ve population
    # (Faz VI) daha önce bu haritaya kaydedilmemişti.
    "simulation_core": {"CityClock"},
    "population": {"SyntheticPopulationGenerator", "ActivityModel"},
}


def test_expected_map_covers_every_discovered_subpackage():
    """Unutma koruması: gelecekte yeni bir alt paket eklenip bu haritaya
    kaydı yapılmazsa bu test kırmızı olur."""
    discovered = _top_level_subpackages()
    missing = discovered - set(EXPECTED_AT_LEAST_ONE)
    assert not missing, (
        f"Yeni alt paket(ler) bulundu ama EXPECTED_AT_LEAST_ONE haritasına "
        f"eklenmemiş: {missing}"
    )


def test_every_subpackage_has_at_least_one_symbol_at_top_level():
    exported = set(harita.__all__)
    for subpkg, candidates in EXPECTED_AT_LEAST_ONE.items():
        found = candidates & exported
        assert found, (
            f"'{subpkg}' alt paketinden hiçbir sembol üst seviyede "
            f"(`harita.__all__`) re-export edilmemiş - beklenenlerden biri: "
            f"{candidates}"
        )
        # Ayrıca gerçekten `dir(harita)` içinde erişilebilir olmalı.
        for name in found:
            assert hasattr(harita, name), f"harita.{name} erişilemiyor"


def test_roadmap_v4_c3_example_import_line_works():
    """Roadmap V4/C3'ün kendi kabul kriteri örneği: tek bir import satırı,
    hiçbir alt paket yolu bilmeden çalışmalı."""
    from harita import (
        ProceduralBuildingGenerator,
        FootprintParser,
        RoofGenerator,
        FacadeGenerator,
        RoomGenerator,
        HeightRegressionModel,
        MLAssistedHeightPredictor,
        ProjectManager,
        ProjectDatabase,
        Scene,
        AppSession,
        AuthService,
        CRDTBuildingState,
        CollaborationHub,
    )

    assert ProceduralBuildingGenerator is not None
    assert FootprintParser is not None
    assert RoofGenerator is not None
    assert FacadeGenerator is not None
    assert RoomGenerator is not None
    assert HeightRegressionModel is not None
    assert MLAssistedHeightPredictor is not None
    assert ProjectManager is not None
    assert ProjectDatabase is not None
    assert Scene is not None
    assert AppSession is not None
    assert AuthService is not None
    assert CRDTBuildingState is not None
    assert CollaborationHub is not None


def test_no_name_collisions_with_existing_exports():
    """`Floor` (mobility) ve `SceneNode` (editor) ile isim çakışması
    yaşanmaması için building_reconstruction.Floor/render_engine.SceneNode
    takma adlarla (`BuildingFloor`/`RenderSceneNode`) eklendi - bu test
    orijinal isimlerin hâlâ doğru alt pakete işaret ettiğini doğrular."""
    from harita.mobility import Floor as MobilityFloor
    from harita.editor import SceneNode as EditorSceneNode
    from harita.building_reconstruction import Floor as BRFloor
    from harita.render_engine import SceneNode as RenderEngineSceneNode

    assert harita.Floor is MobilityFloor
    assert harita.SceneNode is EditorSceneNode
    assert harita.BuildingFloor is BRFloor
    assert harita.RenderSceneNode is RenderEngineSceneNode
    assert harita.Floor is not harita.BuildingFloor
    assert harita.SceneNode is not harita.RenderSceneNode
