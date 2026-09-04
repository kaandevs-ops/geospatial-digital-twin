"""
AppSession — Uygulama Kabuğu Oturum Durumu
============================================

`app_shell`'in kalbi. Tek bir kullanıcı oturumunda:

- `persistence.ProjectManager` üzerinden proje aç/oluştur/kaydet,
- her açık proje için bellekte tutulan `Building` koleksiyonu
  (anahtar -> `Building`), her ekleme/silme `ProjectDatabase.save_object`
  ile diske de yazılır (Faz 16 sözleşmesi),
- her bina için ayrı bir `editor.commands.UndoRedoStack` + `ai_assistant`
  orkestratörü (Faz 8 + Faz 12 doğrudan buradan tetiklenir),
- `render_engine.Scene` ile geçerli sahnenin JSON köprüsü (Faz 15)

birleşik biçimde yönetilir. Bu modül **framework-agnostic**tir: `api.py`
bunu bir REST router'a sarar, ama `AppSession` doğrudan da (örn. bir betik
veya Jupyter içinde) kullanılabilir.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import isfinite as _isfinite
from pathlib import Path
from typing import Any

from ..ai_assistant.orchestrator import AssistantOrchestrator
from ..ai_reconstruction.environment_generator import AIEnvironmentGenerator
from ..ai_reconstruction.interior_layout import AIInteriorLayout
from ..ai_reconstruction.material_bridge import resolve_building_materials
from ..ai_reconstruction.material_predictor import AIMaterialPredictor, SurfaceClass
from ..analysis_engine.decision_support import (
    INDICATIVE_DISCLAIMER,
    RecommendationEngine,
    ScenarioComparison,
)
from ..analysis_engine.measurement import MeasurementEngine
from ..analysis_engine.sun_simulation import RoofIrradiance, SeasonalSunPath
from ..analysis_engine.visibility import BlindSpotAnalysis, ShadowAnalysis
from ..building_reconstruction import (
    Building,
    BuildingType,
    FacadeComplianceReport,
    FacadeGenerator,
    Footprint,
    ProceduralBuildingGenerator,
)
from ..building_reconstruction.footprint_parser import FootprintParser
from ..building_reconstruction.regulations import (
    default_profile as default_regulation_profile,
)
from ..building_reconstruction.regulations import (
    get_profile as get_regulation_profile,
)
from ..climate_data.air_quality_estimate import RoadSegmentTraffic, estimate_network_air_quality
from ..climate_data.microclimate import UrbanFabricSample, estimate_heat_island_index
from ..climate_data.noise_estimate import estimate_noise
from ..climate_data.open_meteo_client import ClimateError, HourlyClimateSample, OpenMeteoClient
from ..collaboration.auth import AuthError, AuthService, Role
from ..collaboration.crdt import CRDTBuildingState
from ..collaboration.scenario_permissions import ScenarioAction, require_scenario_permission
from ..commerce_props import CommercePropsGenerator, MarketStallLayout, OutdoorSeatingItem
from ..commerce_props.city_event_simulation import (
    CityEventCategory,
    CityEventProfile,
    CityEventSimulator,
)
from ..commerce_props.economic_resilience import (
    CommercialAreaResilience,
    build_resilience_curve,
    closure_days_for_risk_level,
)
from ..commerce_props.osm_bridge import generate_commerce_props_for_collection
from ..core_engine.coordinate_systems import GeoPoint
from ..core_engine.coordinate_systems import GeoPoint as _SurveyGeoPoint
from ..core_engine.geometry_engine import GeometryEngine, Point2D, Polygon
from ..core_engine.gis_core import GeoJSONParser
from ..core_engine.gis_core.osm_client import (
    DEFAULT_CATEGORIES,
    BBox,
    OverpassClient,
    OverpassError,
    fetch_category_features,
    local_meters_collection_centroid_to_wgs84,
    project_to_local_meters,
    summarize_categories,
)
from ..digital_twin import DigitalTwin, SensorBinding
from ..digital_twin.hierarchy import TwinHierarchy
from ..digital_twin.iot_bridge import MqttBridge, SensorIotBinding, TopicBus
from ..editor.building_editor import BuildingEditor
from ..editor.commands import UndoRedoStack
from ..editor.osm_bridge import generate_infrastructure_for_collection
from ..editor.road_editor import Road, RoadEditor
from ..editor.terrain_editor import Brush, TerrainEditor, TerrainPaintLayer
from ..export.citygml_export import CityGMLExporter
from ..export.geometry_3d import (
    DXFExporter,
    OBJExporter,
    PLYExporter,
    SceneGLTFExporter,
    STLExporter,
    UnsupportedFormatError,
    USDExporter,
)
from ..export.ifc_export import IFCBuildingModel, IFCExporter, IFCRoom
from ..export.manifest import ManifestBuilder
from ..export.tiles_3d import Tiles3DExporter
from ..extensibility.city_events import CityEventType, emit_city_event
from ..extensibility.event_system import EventSystem, default_bus
from ..feature_survey.bridge import (
    photogrammetry_result_to_point_cloud,
    session_to_geofeatures,
    session_to_wgs84_geofeatures,
)
from ..feature_survey.field_point import FieldSurveySession
from ..feature_survey.io_import import PENZDImportError, import_penzd_csv_text
from ..feature_survey.orchestration.persistence_bridge import (
    PersistenceBridgeError,
    load_survey_result_payload,
    save_survey_result,
)
from ..feature_survey.orchestration.pipeline import (
    OrchestrationError as SurveyOrchestrationError,
)
from ..feature_survey.orchestration.pipeline import (
    SurveyOrchestrationResult,
    run_field_survey_pipeline,
)
from ..feature_survey.pipeline import ExternalToolNotAvailableError, WebODMPipeline
from ..hazard_data.cascade_rules import DEFAULT_CASCADE_RULES, CascadeEngine
from ..hazard_data.fire_spread import FireCellState, FireSpreadModel
from ..material_engine import PBRMaterial
from ..material_engine.texture_presets import procedural_material_texture_data_uri
from ..mesh_engine import Mesh3D, MeshMerger, Vertex3D
from ..mobility.city_scale_evacuation import (
    most_congested_region,
    regional_agent_density,
)
from ..mobility.crowd_simulation import (
    Agent as CrowdAgent,
)
from ..mobility.crowd_simulation import (
    AgentBehavior,
    EvacuationSimulator,
    OccupancyHeatmap,
    SocialForceModel,
    spawn_random_agents,
)
from ..mobility.crowd_simulation.agent_visuals import (
    DEFAULT_CAPSULE_MAX_DISTANCE_M,
    DEFAULT_SKELETAL_MAX_DISTANCE_M,
    agent_visual_variant,
)
from ..mobility.crowd_simulation.capacity_analysis import (
    DEFAULT_CAPACITY_AGENT_COUNTS,
    CapacityAnalyzer,
)
from ..mobility.emergency_response import (
    EmergencyStation,
    EmergencyUnitType,
    dispatch_nearest_unit,
)
from ..mobility.indoor_navigation import (
    FLOOR_HEIGHT_DEFAULT,
    IndoorNavigationBuilder,
)
from ..mobility.indoor_navigation import (
    Floor as IndoorFloor,
)
from ..mobility.pathfinding import AStar, Dijkstra, NavGraph
from ..mobility.scenario import (
    ScenarioValidationError,
    SimulationScenario,
    load_scenario,
    save_scenario,
)
from ..mobility.simulation_recorder import SimulationRecorder
from ..mobility.traffic_simulation import GreenshieldsModel

# ROADMAP_V7.md Faz C5 (offline mod, A4): tile önbellekleme + yerel
# isim->koordinat indeksi (Nominatim offline alternatifi).
from ..offline_cache import TileCache, TileDownloadResult, download_bbox
from ..offline_cache.local_place_index import LocalPlaceIndex, build_index_from_collection
from ..persistence.project_manager import (
    ProjectHandle,
    ProjectManager,
    ProjectNotFoundError,
)
from ..physics import GroundShakeForceModel, TowerStabilityScenario
from ..physics.building_damage import (
    DamageLevel,
    DamagePersistenceStore,
    compute_damage_level,
    damage_state_for_level,
)
from ..physics.building_damage import (
    RiskLevel as DamageRiskLevel,
)
from ..physics.building_shake import (
    DEFAULT_STRUCTURE_SHAKE_PROFILE,
    STRUCTURE_SHAKE_PROFILES,
    BuildingShakeSimulator,
    panic_probability_from_intensity,
    structure_type_for_usage,
)
from ..physics.building_shake_engineering import (
    MDOFShearFrameModel,
    drift_based_damage_hint,
    estimate_floor_properties,
)
from ..population.synthetic_population import (
    SyntheticPopulationGenerator,
)
from ..power_infrastructure import (
    CommunicationTowerItem,
    PowerInfrastructureGenerator,
    SubstationItem,
)
from ..power_infrastructure.osm_bridge import generate_power_infrastructure_for_collection
from ..power_infrastructure.outage_propagation import (
    DARK_CORRIDOR_SPEED_MULTIPLIER,
    OutagePropagationEngine,
    build_power_network_graph,
)
from ..religious_structures import ReligiousStructureGenerator
from ..religious_structures.osm_bridge import generate_religious_structures_for_collection
from ..render_engine import Scene
from ..sport_recreation import PlaygroundItem, SportAreaItem, SportRecreationGenerator
from ..sport_recreation.osm_bridge import generate_sport_recreation_for_collection
from ..street_furniture import StreetFurnitureGenerator
from ..street_furniture.osm_bridge import generate_street_furniture_for_collection
from ..terrain_engine import DEMImporter, HeightmapGrid, TerrainMeshGenerator
from ..vegetation.osm_bridge import generate_vegetation_for_collection, mesh_for_tree_instance
from ..vegetation.scatter import VegetationScatterer
from ..vegetation.tree_generator import TreeGenerator
from ..vegetation.types import TreeSpecies, VegetationInstance
from ..visualization.explosion_view import ExplosionView, floor_bands_from_heights
from ..visualization.scenario_visual_bridge import FireSpriteKind, fire_facade_overlay
from ..visualization.section_view import SectionPlane, SectionView


def _mesh_to_dict(mesh: Mesh3D) -> dict[str, Any]:
    """`Mesh3D`'yi kayıpsız, JSON-serileştirilebilir bir sözlüğe çevirir.

    Kök neden düzeltmesi: OSM katman importu (`import_osm_categories`) daha
    önce yalnızca vertex/triangle SAYISINI saklıyordu, gerçek geometriyi
    değil — bu yüzden `scene_json()` bu prop'ları hiçbir zaman 3D sahneye
    ekleyemiyordu (ekleyecek veri yoktu). Bu fonksiyon gerçek geometriyi
    diskte de hayatta kalacak şekilde saklar.
    """
    return {
        "vertices": [
            [v.x, v.y, v.z, list(v.normal) if v.normal else None, list(v.uv) if v.uv else None]
            for v in mesh.vertices
        ],
        "triangles": [list(t) for t in mesh.triangles],
        "name": mesh.name,
    }


def _mesh_from_dict(data: dict[str, Any]) -> Mesh3D:
    vertices = [
        Vertex3D(
            x=v[0],
            y=v[1],
            z=v[2],
            normal=tuple(v[3]) if v[3] else None,
            uv=tuple(v[4]) if len(v) > 4 and v[4] else None,
        )
        for v in data.get("vertices", [])
    ]
    triangles = [tuple(t) for t in data.get("triangles", [])]
    return Mesh3D(vertices=vertices, triangles=triangles, name=data.get("name", "scene_prop"))


# Kategori -> (albedo rengi, roughness) — sahne materyali için makul
# varsayılanlar. `import_osm_categories`'in ürettiği her kategori burada
# karşılık bulmalı, aksi halde o kategori sahnede görünmez (nötr griye düşer).
_SCENE_PROP_MATERIALS: dict[str, tuple[tuple[float, float, float], float]] = {
    "vegetation": ((0.25, 0.45, 0.2), 0.9),
    "roads": ((0.25, 0.25, 0.27), 0.85),
    "lane_marking": ((0.85, 0.85, 0.8), 0.6),
    "crosswalk": ((0.8, 0.8, 0.78), 0.6),
    "waterway": ((0.15, 0.35, 0.55), 0.15),
    "water_area": ((0.15, 0.35, 0.55), 0.15),
    "street_furniture": ((0.5, 0.5, 0.5), 0.7),
    "religious_structures": ((0.75, 0.72, 0.65), 0.8),
    "marketplace": ((0.7, 0.55, 0.35), 0.85),
    "outdoor_seating": ((0.6, 0.4, 0.3), 0.8),
    "playground": ((0.9, 0.6, 0.2), 0.75),
    "power_line": ((0.2, 0.2, 0.2), 0.5),
    "substation": ((0.4, 0.4, 0.45), 0.6),
    "communication_tower": ((0.3, 0.3, 0.3), 0.5),
}
_DEFAULT_SCENE_PROP_MATERIAL: tuple[tuple[float, float, float], float] = ((0.55, 0.55, 0.55), 0.8)


class AppSessionError(Exception):
    """`AppSession` seviyesinde kullanıcıya gösterilebilir hata."""


@dataclass
class _BuildingEntry:
    """Bir projede yaşayan tek bir binanın oturum-içi (in-memory) durumu."""

    key: str
    building: Building
    undo_stack: UndoRedoStack = field(default_factory=UndoRedoStack)
    assistant: AssistantOrchestrator | None = None

    def orchestrator(self) -> AssistantOrchestrator:
        if self.assistant is None:
            self.assistant = AssistantOrchestrator(self.building, undo_stack=self.undo_stack)
        return self.assistant


@dataclass
class _TerrainEntry:
    """Faz E9: bir projenin oturum-içi (in-memory) arazi (terrain) durumu.
    `editor.terrain_editor.TerrainEditor` fırça operasyonları bu grid
    üzerinde çalışır; her operasyon kendi `undo_stack`'ine `EditorCommand`
    olarak eklenir (bina undo/redo'suyla aynı desen — bkz. `_BuildingEntry`)."""

    grid: HeightmapGrid
    paint: TerrainPaintLayer
    undo_stack: UndoRedoStack = field(default_factory=UndoRedoStack)


@dataclass
class _RoadEntry:
    """Faz E9: bir projede yaşayan tek bir yolun (spline) oturum-içi
    durumu — `editor.road_editor.Road` + kendi undo/redo yığını."""

    road: Road
    undo_stack: UndoRedoStack = field(default_factory=UndoRedoStack)


class AppSession:
    """Faz 18 uygulama kabuğunun tuttuğu tüm çalışma zamanı durumu.

    Not: `registry_path`, `ProjectManager`'ın çoklu-proje kaydını tutar;
    her `AppSession`, tek bir registry (yani tek bir "kullanıcı/masaüstü")
    ile eşleşir. Aynı anda birden çok proje açık tutulabilir.
    """

    def __init__(self, registry_path: str | Path) -> None:
        self._manager = ProjectManager(registry_path)
        self._buildings: dict[str, dict[str, _BuildingEntry]] = {}
        # project_id -> {building_key: _BuildingEntry}
        # Faz E9: proje başına en fazla bir arazi (terrain) durumu.
        self._terrains: dict[str, _TerrainEntry] = {}
        # project_id -> {road_id: _RoadEntry}
        self._roads: dict[str, dict[str, _RoadEntry]] = {}
        # ROADMAP_V7.md Faz C6 (2. dilim): project_id -> {prop_key: dict}.
        # B5'in "kullanıcı sadece 'Yollar + Ağaçlar' seçip bbox import
        # edebilmeli" maddesinin gerçek karşılığı — C3'ün 7 OSM köprüsünden
        # (vegetation, editor.osm_bridge, street_furniture,
        # religious_structures, commerce_props, sport_recreation,
        # power_infrastructure) üretilen hafif prop kayıtları. Binaların
        # aksine (tam `Building` dataclass'ı deterministik seed'den yeniden
        # üretilir) bu prop'lar zaten ucuz/basit olduğu için doğrudan
        # sözlük olarak saklanır - `objects` tablosunda `kind="scene_prop"`.
        self._scene_props: dict[str, dict[str, dict[str, Any]]] = {}
        # Web arayüzü genişletmesi: project_id -> son üretilen VegetationInstance listesi
        self._vegetation: dict[str, list[VegetationInstance]] = {}
        # Feature Survey modu: project_id -> aktif FieldSurveySession
        self._feature_surveys: dict[str, FieldSurveySession] = {}
        # Feature Survey modu: project_id -> son WebODM görev referansı
        # {"webodm_project_id": int, "task_id": str, "base_url": str, "output_dir": str}
        self._feature_survey_jobs: dict[str, dict[str, Any]] = {}
        # Web arayüzü genişletmesi: çok kullanıcılı kimlik doğrulama/rol yönetimi
        self._auth = AuthService()
        # Faz 4.1 — AI ayarları paneli: çalışma-zamanı sağlayıcı config'i.
        # Yalnızca bellekte tutulur (diske/projeye yazılmaz) — anahtarlar
        # asla client'a düz metin geri dönmez, yalnızca maskelenmiş özet.
        self._ai_config: dict[str, Any] | None = None
        # Fikir 10 — IoT dijital ikiz: project_id -> {building_key: DigitalTwin}
        self._twins: dict[str, dict[str, DigitalTwin]] = {}
        # (project_id, key) -> gerçek MQTT bağlantısı bileşenleri (bkz.
        # connect_iot_bridge / disconnect_iot_bridge). Bağlantı yoksa
        # iot_digital_twin_tick() dürüstçe etiketlenmiş simüle veriye
        # düşer (bkz. o metodun disclaimer alanı).
        self._iot_bridges: dict[tuple[str, str], dict[str, Any]] = {}
        # Fikir 12 — gerçek zamanlı çoklu kullanıcı: project_id -> {building_key: CRDTBuildingState}
        self._collab_states: dict[str, dict[str, CRDTBuildingState]] = {}
        # Roadmap V10 / Faz 4.4 — sallanma sonrası KALICI hasar durumu:
        # `DamagePersistenceStore` (physics.building_damage, DEĞİŞTİRİLMEDEN)
        # tek bir örneği tüm oturum boyunca paylaşılır; `project_id`,
        # modülün beklediği `scenario_id` yerine kullanılır (proje = tek
        # senaryo varsayımı, web arayüzü basitliği için yeterli).
        self._damage_store = DamagePersistenceStore()
        # ROADMAP_V7.md Faz C5 (offline mod, A4): registry'nin yanına, tüm
        # projeler arasında paylaşılan tek bir tile önbelleği + yerel yer
        # adı indeksi (tile'lar bir "basemap" kavramıdır, proje-özel değil -
        # bu yüzden proje bazlı değil registry bazlı tutulur, tıpkı
        # registry'nin kendisi gibi).
        offline_root = (
            Path(self._manager.registry_path).expanduser().resolve().parent / "offline_cache"
        )
        self._offline_cache = TileCache(offline_root)
        self._offline_place_index_path = offline_root / "place_index.json"
        self._offline_place_index = LocalPlaceIndex.load(self._offline_place_index_path)
        # ROADMAP_V8.md Faz 3.3 — A4 offline modun davranışsal sıkılaştırılması:
        # V7'nin kendi notu "kullanıcı offline modda yine de 'İçe aktar'a
        # basarsa ağ hatası alır, engellenmez" idi. Artık offline mod
        # açıkken OSM önizleme/içe aktarma/katman-özeti çağrıları backend'e
        # HİÇBİR ağ isteği atmadan, net bir AppSessionError ile en baştan
        # reddedilir (bkz. `_require_online()`).
        self._offline_mode: bool = False
        # Roadmap V10 / Faz 7 + Mekansal Ses — son çalıştırılan tahliye
        # sonucunun `result_id`'sini proje bazında hatırlar (yeni bir
        # sorgu/indeks motoru icat edilmez; `simulation_evacuation_run()`
        # zaten sonucu `handle.db.save_object()` ile kalıcı olarak
        # saklıyordu — burada yalnızca "en sonuncusu hangisiydi" bilgisi
        # tutulur ki sinematik kamera/ses katmanları onu yeniden bulabilsin).
        self._last_evac_result_id: dict[str, str] = {}
        # Roadmap V10 / Mekansal Ses (Faz 5.6): son sallanma/yangın
        # koşumlarının özet durumunu (bina bazında) hatırlar — yeni bir
        # olay-veriyolu icat edilmez, `trigger_event_sound()`'un girdisi
        # olarak `building_shake_simulate()`/`building_fire_simulate()`
        # zaten hesapladığı değerlerin (max_intensity, cam kırılması/enkaz
        # eşiği, aktif yangın) doğrudan saklanmış hâlidir.
        self._last_shake_status: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_fire_status: dict[tuple[str, str], dict[str, Any]] = {}

    def set_offline_mode(self, enabled: bool) -> dict[str, Any]:
        """Offline modu aç/kapat. Kapsam: registry/oturum geneli (tıpkı tile
        önbelleği ve yer adı indeksi gibi) — tek bir proje ile sınırlı değil,
        çünkü ağ erişimi kullanıcının cihazının/bağlantısının bir özelliği,
        proje verisinin değil."""
        self._offline_mode = bool(enabled)
        return {"offline_mode": self._offline_mode}

    def is_offline_mode(self) -> bool:
        return self._offline_mode

    def _require_online(self, action: str) -> None:
        """Offline moddayken ağ gerektiren bir eylem çağrılırsa, Overpass'a
        (veya başka bir dış servise) HİÇBİR istek atılmadan burada durur."""
        if self._offline_mode:
            raise AppSessionError(
                f"Çevrimdışı (offline) moddasınız — '{action}' ağ erişimi "
                "gerektirir ve bu istek gönderilmedi. Devam etmek için önce "
                "çevrimiçi moda geçin (offline modu kapatın)."
            )

    def close(self) -> None:
        self._manager.close()

    def __enter__(self) -> AppSession:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- Proje yönetimi (Faz 16 köprüsü) ---------------------------------

    def list_projects(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._manager.list_recent(limit=limit)

    def create_project(self, name: str, path: str | Path) -> dict[str, Any]:
        project_id = uuid.uuid4().hex[:12]
        handle = self._manager.create_project(path=path, name=name, project_id=project_id)
        self._buildings[project_id] = {}
        self._roads[project_id] = {}
        self._scene_props[project_id] = {}
        return self._project_info(handle)

    def open_project(
        self, project_id: str | None = None, *, path: str | Path | None = None
    ) -> dict[str, Any]:
        try:
            handle = self._manager.open_project(project_id=project_id, path=path)
        except ProjectNotFoundError as exc:
            raise AppSessionError(str(exc)) from exc
        self._buildings.setdefault(handle.manifest.project_id, {})
        self._roads.setdefault(handle.manifest.project_id, {})
        self._scene_props.setdefault(handle.manifest.project_id, {})
        self._load_buildings_from_disk(handle)
        self._load_terrain_from_disk(handle)
        self._load_roads_from_disk(handle)
        self._load_scene_props_from_disk(handle)
        return self._project_info(handle)

    def close_project(self, project_id: str) -> None:
        self._manager.close_project(project_id)
        self._buildings.pop(project_id, None)
        self._terrains.pop(project_id, None)
        self._roads.pop(project_id, None)
        self._scene_props.pop(project_id, None)

    def save_project(self, project_id: str) -> dict[str, Any]:
        handle = self._handle(project_id)
        handle.save_now()
        return self._project_info(handle)

    def _project_info(self, handle: ProjectHandle) -> dict[str, Any]:
        m = handle.manifest
        return {
            "project_id": m.project_id,
            "name": m.name,
            "path": str(handle.path),
            "updated_at": m.updated_at,
            "building_count": len(self._buildings.get(m.project_id, {})),
        }

    def _handle(self, project_id: str) -> ProjectHandle:
        try:
            return self._manager.open_project(project_id=project_id)
        except ProjectNotFoundError as exc:
            raise AppSessionError(f"Proje açık değil veya bulunamadı: {project_id}") from exc

    def _load_buildings_from_disk(self, handle: ProjectHandle) -> None:
        """`.hproj` içinde `kind='building'` olarak saklanmış her nesneyi
        oturuma yükler. Basitlik için burada yalnızca footprint/parametreleri
        (dataclass alanları) saklanır; `Building` yeniden üretilir — böylece
        prosedürel üretim deterministik (seed'e bağlı) kaldığı sürece disk
        temsili küçük ve okunur kalır."""
        entries = self._buildings.setdefault(handle.manifest.project_id, {})
        for key in handle.db.list_objects(kind="building"):
            record = handle.db.load_object(key)
            if record is None:
                continue
            entries[key] = self._entry_from_dict(key, record.data)

    def _load_terrain_from_disk(self, handle: ProjectHandle) -> None:
        """`kind='terrain'` olarak saklanmış tekil arazi kaydını (varsa)
        oturuma yükler — Faz E9. Elevations/paint matrisleri doğrudan
        JSON-serileştirilebilir listeler olarak tutulur (stdlib-only)."""
        record = handle.db.load_object("terrain")
        if record is None:
            return
        data = record.data
        grid = HeightmapGrid(
            width=data["width"],
            height=data["height"],
            resolution_m=data["resolution_m"],
            elevations=data["elevations"],
            origin=GeoPoint(data.get("origin_lat", 0.0), data.get("origin_lon", 0.0)),
        )
        paint = TerrainPaintLayer(
            width=data["width"], height=data["height"], weights=data.get("paint_weights") or []
        )
        self._terrains[handle.manifest.project_id] = _TerrainEntry(grid=grid, paint=paint)

    def _persist_terrain(self, project_id: str) -> None:
        handle = self._handle(project_id)
        entry = self._terrains.get(project_id)
        if entry is None:
            return
        handle.db.save_object(
            "terrain",
            "terrain",
            {
                "width": entry.grid.width,
                "height": entry.grid.height,
                "resolution_m": entry.grid.resolution_m,
                "elevations": entry.grid.elevations,
                "origin_lat": entry.grid.origin.lat,
                "origin_lon": entry.grid.origin.lon,
                "paint_weights": entry.paint.weights,
            },
        )
        handle.mark_dirty()

    def _load_roads_from_disk(self, handle: ProjectHandle) -> None:
        entries = self._roads.setdefault(handle.manifest.project_id, {})
        for key in handle.db.list_objects(kind="road"):
            record = handle.db.load_object(key)
            if record is None:
                continue
            data = record.data
            road = Road(
                road_id=key,
                control_points=[Point2D(p[0], p[1]) for p in data["control_points"]],
                width_m=data.get("width_m", 6.0),
                elevation_z=data.get("elevation_z", 0.0),
                samples_per_segment=data.get("samples_per_segment", 12),
                name=data.get("name", key),
            )
            entries[key] = _RoadEntry(road=road)

    def _persist_road(self, project_id: str, road_id: str) -> None:
        handle = self._handle(project_id)
        entry = self._roads.get(project_id, {}).get(road_id)
        if entry is None:
            return
        road = entry.road
        handle.db.save_object(
            road_id,
            "road",
            {
                "control_points": [[p.x, p.y] for p in road.control_points],
                "width_m": road.width_m,
                "elevation_z": road.elevation_z,
                "samples_per_segment": road.samples_per_segment,
                "name": road.name,
            },
        )
        handle.mark_dirty()

    def _load_scene_props_from_disk(self, handle: ProjectHandle) -> None:
        """`kind='scene_prop'` olarak saklanmış tüm C6/2. dilim import
        kayıtlarını (bkz. `import_osm_categories`) oturuma yükler. Bu
        kayıtlar zaten hafif JSON sözlükleridir (mesh yeniden üretilmez,
        yalnızca özet + konum saklanır) — `_load_buildings_from_disk`'in
        aksine bir "yeniden üretim" adımı gerekmez."""
        entries = self._scene_props.setdefault(handle.manifest.project_id, {})
        for key in handle.db.list_objects(kind="scene_prop"):
            record = handle.db.load_object(key)
            if record is None:
                continue
            entries[key] = record.data

    @staticmethod
    def _entry_from_dict(key: str, data: dict[str, Any]) -> _BuildingEntry:
        poly = Polygon([Point2D(p[0], p[1]) for p in data["polygon"]])
        footprint = Footprint(
            polygon=poly,
            building_type=data.get("building_type"),
            floor_count=data.get("floor_count"),
            height_m=data.get("height_m"),
        )
        building = ProceduralBuildingGenerator.generate(
            footprint,
            building_type=data.get("building_type"),
            floor_count=data.get("floor_count"),
            seed=data.get("seed"),
        )
        return _BuildingEntry(key=key, building=building)

    @staticmethod
    def _entry_to_dict(entry: _BuildingEntry) -> dict[str, Any]:
        fp = entry.building.footprint
        return {
            "polygon": [[p.x, p.y] for p in fp.polygon.points],
            "building_type": entry.building.building_type.value,
            "floor_count": len(entry.building.floors),
            "height_m": entry.building.total_height_m,
            "seed": None,
        }

    # -- Bina koleksiyonu -------------------------------------------------

    #: Roadmap V3 - D19: OWASP-tarzı fuzz sertleştirme - kabul edilebilir
    #: girdi üst sınırları (kaynak tüketimi / DoS'a karşı, API4:2023
    #: "Unrestricted Resource Consumption").
    _MAX_POLYGON_POINTS = 10_000
    _MAX_FLOOR_COUNT = 10_000
    _MAX_HEIGHT_M = 1_000_000.0
    _MAX_NAME_LEN = 200

    @staticmethod
    def _validate_name(name: str | None, *, field_label: str) -> None:
        """Kullanıcı tarafından verilen bir anahtar/isim alanını güvenlik
        için doğrular: yol ayırıcı, `..` gezinme dizisi, null byte veya
        kontrol karakteri içeremez, makul bir uzunluk sınırı vardır.
        Bu değer şu an yalnızca parametreli SQLite anahtarı olarak
        kullanılıyor (dosya yolu olarak KULLANILMIYOR), ama gelecekte
        dosya-tabanlı bir backend'e geçilirse yol-gezinme sınıfı bir
        açığın önceden kapatılması amacıyla savunma-derinliği ilkesiyle
        burada da reddediliyor.
        """
        if name is None:
            return
        if not isinstance(name, str):
            raise AppSessionError(f"{field_label} bir metin (str) olmalı.")
        if len(name) == 0 or len(name) > AppSession._MAX_NAME_LEN:
            raise AppSessionError(f"{field_label} 1-{AppSession._MAX_NAME_LEN} karakter olmalı.")
        if "\x00" in name or any(ord(c) < 0x20 for c in name):
            raise AppSessionError(f"{field_label} kontrol karakteri içeremez.")
        if "/" in name or "\\" in name or ".." in name:
            raise AppSessionError(f"{field_label} yol ayırıcı veya '..' içeremez.")

    def add_building(
        self,
        project_id: str,
        polygon_points: list[tuple[float, float]],
        *,
        building_type: str = "apartman",
        floor_count: int | None = None,
        height_m: float | None = None,
        seed: int | None = None,
        name: str | None = None,
        basement_floor_count: int = 0,
        token: str | None = None,
        generate_interior: bool = False,
        min_room_size: float | None = None,
        window_spacing: float | None = None,
    ) -> dict[str, Any]:
        self._authorize_write(project_id, token)
        # Roadmap V3 - D19: fuzz-güvenli girdi doğrulaması. Aşağıdaki
        # kontroller olmadan kötü biçimli gövdeler (dize/None koordinat,
        # devasa nokta listesi, negatif/aşırı büyük kat sayısı) alt
        # katmanlarda okunaksız bir `TypeError`/`ValueError`/bellek şişmesine
        # yol açıp 500'e (veya daha kötüsü, kontrolsüz kaynak tüketimine)
        # neden oluyordu; artık hepsi kullanıcıya okunur bir 400 olarak
        # (AppSessionError -> api.py'de 400/422) geri döner.
        if not isinstance(polygon_points, list):
            raise AppSessionError("polygon bir nokta listesi olmalı.")
        if len(polygon_points) < 3:
            raise AppSessionError("Bina footprint'i en az 3 nokta gerektirir.")
        if len(polygon_points) > self._MAX_POLYGON_POINTS:
            raise AppSessionError(f"polygon en fazla {self._MAX_POLYGON_POINTS} nokta içerebilir.")
        validated_points: list[tuple[float, float]] = []
        for i, p in enumerate(polygon_points):
            try:
                x, y = p
                x = float(x)
                y = float(y)
            except (TypeError, ValueError):
                raise AppSessionError(f"polygon[{i}] geçerli bir (x, y) sayı çifti değil.")
            if not (_isfinite(x) and _isfinite(y)):
                raise AppSessionError(f"polygon[{i}] sonlu (finite) sayılar içermeli.")
            validated_points.append((x, y))
        polygon_points = validated_points

        if floor_count is not None:
            if isinstance(floor_count, bool) or not isinstance(floor_count, int):
                raise AppSessionError("floor_count bir tam sayı olmalı.")
            if not (0 <= floor_count <= self._MAX_FLOOR_COUNT):
                raise AppSessionError(f"floor_count 0-{self._MAX_FLOOR_COUNT} aralığında olmalı.")
        if height_m is not None:
            if isinstance(height_m, bool) or not isinstance(height_m, (int, float)):
                raise AppSessionError("height_m bir sayı olmalı.")
            height_m = float(height_m)
            if not _isfinite(height_m) or not (0 < height_m <= self._MAX_HEIGHT_M):
                raise AppSessionError(
                    f"height_m 0-{self._MAX_HEIGHT_M} aralığında sonlu bir sayı olmalı."
                )
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise AppSessionError("seed bir tam sayı olmalı.")
        if isinstance(basement_floor_count, bool) or not isinstance(basement_floor_count, int):
            raise AppSessionError("basement_floor_count bir tam sayı olmalı.")
        if not (0 <= basement_floor_count <= 5):
            raise AppSessionError("basement_floor_count 0-5 aralığında olmalı.")
        if not isinstance(generate_interior, bool):
            raise AppSessionError("generate_interior bir boolean (true/false) olmalı.")
        if min_room_size is not None:
            if isinstance(min_room_size, bool) or not isinstance(min_room_size, (int, float)):
                raise AppSessionError("min_room_size bir sayı olmalı.")
            min_room_size = float(min_room_size)
            if not _isfinite(min_room_size) or not (1.0 <= min_room_size <= 200.0):
                raise AppSessionError("min_room_size 1-200 m² aralığında olmalı.")
        if window_spacing is not None:
            if isinstance(window_spacing, bool) or not isinstance(window_spacing, (int, float)):
                raise AppSessionError("window_spacing bir sayı olmalı.")
            window_spacing = float(window_spacing)
            if not _isfinite(window_spacing) or not (0.8 <= window_spacing <= 20.0):
                raise AppSessionError("window_spacing 0.8-20 m aralığında olmalı.")
        self._validate_name(name, field_label="name")
        self._validate_name(
            building_type if isinstance(building_type, str) else None, field_label="building_type"
        )

        handle = self._handle(project_id)
        poly = Polygon([Point2D(x, y) for x, y in polygon_points])
        footprint = Footprint(
            polygon=poly,
            building_type=building_type,
            floor_count=floor_count,
            height_m=height_m,
        )
        try:
            bt = BuildingType(building_type)
        except ValueError:
            bt = BuildingType.APARTMAN
        building = ProceduralBuildingGenerator.generate(
            footprint,
            building_type=bt,
            floor_count=floor_count,
            seed=seed,
            basement_floor_count=basement_floor_count,
            generate_interior=generate_interior,
            min_room_size=min_room_size,
            window_spacing=window_spacing,
        )
        key = name or f"building_{uuid.uuid4().hex[:8]}"
        entry = _BuildingEntry(key=key, building=building)
        self._buildings.setdefault(project_id, {})[key] = entry
        handle.db.save_object(key, "building", self._entry_to_dict(entry))
        handle.mark_dirty()
        return self.describe_building(project_id, key)

    def list_buildings(self, project_id: str) -> list[dict[str, Any]]:
        return [
            self.describe_building(project_id, key) for key in self._buildings.get(project_id, {})
        ]

    def describe_building(self, project_id: str, key: str) -> dict[str, Any]:
        entry = self._entry(project_id, key)
        b = entry.building
        return {
            "key": key,
            "building_type": b.building_type.value,
            "floor_count": len(b.floors),
            "basement_floor_count": sum(1 for f in b.floors if f.is_below_grade),
            "total_height_m": round(b.total_height_m, 2),
            "has_roof": b.roof is not None,
            "can_undo": entry.undo_stack.can_undo(),
            "can_redo": entry.undo_stack.can_redo(),
        }

    def validate_structure(self, project_id: str, key: str) -> dict[str, Any]:
        """Faz 1.5 — binanın basit fiziksel tutarlılık kontrolü (bkz.
        `building_reconstruction.structural_validation`). Kesin bir
        mühendislik raporu değildir, gösterge niteliğindedir."""
        from ..building_reconstruction import validate_building

        entry = self._entry(project_id, key)
        report = validate_building(entry.building)
        return {
            "is_plausible": report.is_plausible,
            "slenderness_ratio": round(report.slenderness_ratio, 2),
            "max_cantilever_m": round(report.max_cantilever_m, 2),
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity.value,
                    "message": i.message,
                    "value": round(i.value, 2),
                    "limit": round(i.limit, 2),
                }
                for i in report.issues
            ],
        }

    def regenerate_interior(
        self,
        project_id: str,
        key: str,
        *,
        seed: int | None = None,
        min_room_size: float | None = None,
        window_spacing: float | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Var olan bir binayı, footprint/tip/kat sayısı AYNI kalacak
        şekilde ama `generate_interior=True` ile YENİDEN üretir — yani
        oda bölme duvarları, merdiven, asansör kuyusu ve gerçek mobilya/
        donatı mesh'i (`FurnitureGenerator`) artık binanın içinde var olur.

        Bu, "AI İç Mekan Planı Üret" (yalnızca metin/oda listesi döndüren,
        3D'ye hiç dokunmayan `generate_interior_layout`) ile KARIŞTIRILMAMALI
        — bu metod gerçekten sahnedeki bina mesh'ini değiştirir.
        """
        self._authorize_write(project_id, token)
        entry = self._entry(project_id, key)
        footprint = entry.building.footprint
        floor_count = len(entry.building.floors) or None
        bt = entry.building.building_type
        new_building = ProceduralBuildingGenerator.generate(
            footprint,
            building_type=bt,
            floor_count=floor_count,
            seed=seed,
            generate_interior=True,
            min_room_size=min_room_size,
            window_spacing=window_spacing,
        )
        entry.building = new_building
        entry.undo_stack = UndoRedoStack()
        entry.assistant = None
        handle = self._handle(project_id)
        handle.db.save_object(key, "building", self._entry_to_dict(entry))
        handle.mark_dirty()
        return self.describe_building(project_id, key)

    def remove_building(self, project_id: str, key: str, *, token: str | None = None) -> bool:
        self._authorize_write(project_id, token)
        entries = self._buildings.get(project_id, {})
        existed = entries.pop(key, None) is not None
        if existed:
            handle = self._handle(project_id)
            handle.db.delete_object(key)
            handle.mark_dirty()
        return existed

    def _entry(self, project_id: str, key: str) -> _BuildingEntry:
        entries = self._buildings.get(project_id, {})
        if key not in entries:
            raise AppSessionError(f"Bina bulunamadı: proje={project_id} bina={key}")
        return entries[key]

    def _authorize_write(self, project_id: str, token: str | None) -> None:
        """Roadmap Faz 5.1: "rol bazlı yetkilendirme eklenmeli". FAZ0
        raporu madde 21'de tespit edilen boşluk — `AuthService`/
        `grant_project_role` tam işlevsel olmasına rağmen, hiçbir
        veri-değiştiren `AppSession` metodu bunu kontrol etmiyordu (rol
        atamak mümkündü ama hiçbir yerde uygulanmıyordu).

        Geriye dönük uyumluluk ilkesi (mevcut `grant_project_role`
        bootstrap mantığıyla tutarlı): bir projenin HENÜZ hiç üyesi
        yoksa (auth hiç kurulmamış/tek-kullanıcı senaryosu) yazma
        serbesttir — mevcut testler/akışlar token göndermeden çalışmaya
        devam eder. Proje bir kez üyelendirildiyse (`grant_project_role`
        çağrıldıysa), bundan sonra yazma işlemleri geçerli bir token +
        en az EDITOR rolü gerektirir.
        """
        if not self._auth.members(project_id):
            return  # henüz kimse yetkilendirilmemiş -> geriye dönük uyumlu, serbest
        if not token:
            raise AppSessionError(
                "Bu proje yetkilendirilmiş kullanıcılara sahip; yazma işlemi için "
                "geçerli bir oturum token'ı gerekli."
            )
        try:
            user = self._auth.authenticate_token(token)
            self._auth.require_role(project_id, user.user_id, at_least=Role.EDITOR)
        except AuthError as exc:
            raise AppSessionError(str(exc)) from exc

    def _persist_building(self, project_id: str, key: str) -> None:
        handle = self._handle(project_id)
        entry = self._entry(project_id, key)
        handle.db.save_object(key, "building", self._entry_to_dict(entry))
        handle.mark_dirty()

    # -- Editör köprüsü (Faz 8) --------------------------------------------

    def add_floor(self, project_id: str, key: str, *, token: str | None = None) -> dict[str, Any]:
        self._authorize_write(project_id, token)
        entry = self._entry(project_id, key)
        cmd = BuildingEditor.add_floor(entry.building)
        entry.undo_stack.execute(cmd)
        self._persist_building(project_id, key)
        return self.describe_building(project_id, key)

    def remove_floor(
        self, project_id: str, key: str, *, token: str | None = None
    ) -> dict[str, Any]:
        self._authorize_write(project_id, token)
        entry = self._entry(project_id, key)
        if not entry.building.floors:
            raise AppSessionError("Silinecek kat yok.")
        cmd = BuildingEditor.remove_floor(entry.building, len(entry.building.floors) - 1)
        entry.undo_stack.execute(cmd)
        self._persist_building(project_id, key)
        return self.describe_building(project_id, key)

    def undo(self, project_id: str, key: str, *, token: str | None = None) -> dict[str, Any]:
        self._authorize_write(project_id, token)
        entry = self._entry(project_id, key)
        if not entry.undo_stack.can_undo():
            raise AppSessionError("Geri alınacak işlem yok.")
        entry.undo_stack.undo()
        self._persist_building(project_id, key)
        return self.describe_building(project_id, key)

    def redo(self, project_id: str, key: str, *, token: str | None = None) -> dict[str, Any]:
        self._authorize_write(project_id, token)
        entry = self._entry(project_id, key)
        if not entry.undo_stack.can_redo():
            raise AppSessionError("İleri alınacak işlem yok.")
        entry.undo_stack.redo()
        self._persist_building(project_id, key)
        return self.describe_building(project_id, key)

    # -- Arazi (Terrain) Editör köprüsü (Roadmap V4 - Faz E9) --------------
    #
    # D16'da UI iskeleti eklenmiş ama arka uca hiç bağlanmamıştı (bkz.
    # `app_shell/web/index.html` eski "terrain-layer-note" uyarısı). Bu
    # köprü, D5'in `GizmoInputSession` deseniyle aynı ilkeyi uygular:
    # viewer taraf (JS) yalnızca gerçek dünya (x, y) konumunu ray-cast
    # ederek hesaplar; fırça matematiği/undo-redo tamamen Python
    # tarafında (`editor.terrain_editor.TerrainEditor`) kalır.

    _MAX_TERRAIN_DIM = 512  # Faz D19 tarzı fuzz-güvenli üst sınır.

    def terrain_init(
        self,
        project_id: str,
        *,
        width: int = 64,
        height: int = 64,
        resolution_m: float = 2.0,
        base_elevation: float = 0.0,
    ) -> dict[str, Any]:
        """Projede henüz arazi yoksa düz bir `HeightmapGrid` oluşturur;
        zaten varsa mevcut durumu (yeniden oluşturmadan) döndürür —
        kullanıcı sayfayı yenilediğinde araziyi kaybetmez."""
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            raise AppSessionError("width/height pozitif tam sayılar olmalı.")
        if width > self._MAX_TERRAIN_DIM or height > self._MAX_TERRAIN_DIM:
            raise AppSessionError(f"width/height en fazla {self._MAX_TERRAIN_DIM} olabilir.")
        self._handle(project_id)  # proje açık mı doğrula
        existing = self._terrains.get(project_id)
        if existing is not None:
            return self.terrain_state(project_id)
        grid = DEMImporter.flat_terrain(
            width=width,
            height=height,
            resolution_m=resolution_m,
            elevation=base_elevation,
            origin=GeoPoint(0.0, 0.0),
        )
        paint = TerrainPaintLayer(width=width, height=height)
        self._terrains[project_id] = _TerrainEntry(grid=grid, paint=paint)
        self._persist_terrain(project_id)
        return self.terrain_state(project_id)

    def terrain_state(self, project_id: str) -> dict[str, Any] | None:
        entry = self._terrains.get(project_id)
        if entry is None:
            return None
        lo, hi = entry.grid.min_max()
        return {
            "width": entry.grid.width,
            "height": entry.grid.height,
            "resolution_m": entry.grid.resolution_m,
            "min_elevation": lo,
            "max_elevation": hi,
            "can_undo": entry.undo_stack.can_undo(),
            "can_redo": entry.undo_stack.can_redo(),
        }

    _TERRAIN_OPS = {"raise", "lower", "flatten", "smooth", "noise", "paint"}

    def terrain_brush(
        self,
        project_id: str,
        operation: str,
        center_x_m: float,
        center_y_m: float,
        *,
        radius_m: float = 6.0,
        strength: float = 1.0,
        amount_m: float = 1.0,
        target_elevation: float | None = None,
        iterations: int = 1,
        seed: int | None = None,
        paint_weight: float = 1.0,
    ) -> dict[str, Any]:
        """`center_x_m`/`center_y_m` ve `radius_m`, D16'nın metre-cinsinden
        dünya uzayı sözleşmesiyle tutarlı — viewer, mouse ray'ini zemin
        düzlemiyle (y=0) kesiştirip buradaki (x, z) dünya konumunu doğrudan
        gönderir; hücre indeksine çevirme burada (resolution_m ile) yapılır."""
        entry = self._terrains.get(project_id)
        if entry is None:
            raise AppSessionError("Projede arazi yok — önce terrain_init çağrılmalı.")
        if operation not in self._TERRAIN_OPS:
            raise AppSessionError(f"Bilinmeyen arazi operasyonu: {operation!r}")
        if radius_m <= 0:
            raise AppSessionError("radius_m pozitif olmalı.")

        grid = entry.grid
        center_row = center_y_m / grid.resolution_m
        center_col = center_x_m / grid.resolution_m
        radius_cells = radius_m / grid.resolution_m
        brush = Brush(
            center_row=center_row,
            center_col=center_col,
            radius_cells=radius_cells,
            strength=strength,
        )

        if operation == "raise":
            cmd = TerrainEditor.raise_terrain(grid, brush, amount_m=amount_m)
        elif operation == "lower":
            cmd = TerrainEditor.lower_terrain(grid, brush, amount_m=amount_m)
        elif operation == "flatten":
            cmd = TerrainEditor.flatten(grid, brush, target_elevation=target_elevation)
        elif operation == "smooth":
            cmd = TerrainEditor.smooth(grid, brush, iterations=iterations)
        elif operation == "noise":
            cmd = TerrainEditor.add_noise(grid, brush, amplitude_m=amount_m, seed=seed)
        else:  # "paint"
            cmd = TerrainEditor.paint(entry.paint, brush, target_weight=paint_weight)

        entry.undo_stack.execute(cmd)
        self._persist_terrain(project_id)
        return self.terrain_state(project_id)

    def terrain_undo(self, project_id: str) -> dict[str, Any]:
        entry = self._terrains.get(project_id)
        if entry is None or not entry.undo_stack.can_undo():
            raise AppSessionError("Geri alınacak arazi işlemi yok.")
        entry.undo_stack.undo()
        self._persist_terrain(project_id)
        return self.terrain_state(project_id)

    def terrain_redo(self, project_id: str) -> dict[str, Any]:
        entry = self._terrains.get(project_id)
        if entry is None or not entry.undo_stack.can_redo():
            raise AppSessionError("İleri alınacak arazi işlemi yok.")
        entry.undo_stack.redo()
        self._persist_terrain(project_id)
        return self.terrain_state(project_id)

    def terrain_mesh(self, project_id: str) -> Mesh3D | None:
        entry = self._terrains.get(project_id)
        if entry is None:
            return None
        return TerrainMeshGenerator.generate(entry.grid, name="terrain")

    # -- Web arayüzü köprüsü: Sel/Heyelan Riski (hazard_data.flood_landslide) --
    #
    # Roadmap iş fikri #2: "Sel/heyelan riski görselleştirme — terrain_engine
    # + hazard verisiyle eğim/drenaj analizi (şu an terrain var ama hazard
    # ile hiç birleşmemiş, yeni bağlanacak bir yön)." `FlowAccumulation`
    # zaten vardı, hiç kullanılmıyordu — burada eğim hesabıyla birleştirilip
    # gerçek bir risk skoruna dönüşür.

    def terrain_hazard_summary(
        self,
        project_id: str,
        *,
        rainfall_mm_24h: float | None = None,
    ) -> dict[str, Any]:
        """Projedeki mevcut araziyi (terrain_init ile oluşturulmuş) analiz
        edip genel eğim/drenaj özetini döner — arazi yoksa açık hata
        fırlatır (sessizce boş/varsayılan üretmez)."""
        from ..hazard_data import TerrainHazardAnalyzer

        entry = self._terrains.get(project_id)
        if entry is None:
            raise AppSessionError("Projede arazi yok — önce terrain_init çağrılmalı.")
        analyzer = TerrainHazardAnalyzer(entry.grid, rainfall_mm_24h=rainfall_mm_24h)
        return {**analyzer.summary(), "rainfall_mm_24h": rainfall_mm_24h}

    def terrain_hazard_point(
        self,
        project_id: str,
        *,
        x_m: float,
        y_m: float,
        rainfall_mm_24h: float | None = None,
    ) -> dict[str, Any]:
        """Arazi üzerindeki (x_m, y_m) yerel konumu için sel/heyelan
        risk raporu — örn. bir binanın konumu ya da haritada tıklanan
        herhangi bir nokta."""
        from ..hazard_data import TerrainHazardAnalyzer

        entry = self._terrains.get(project_id)
        if entry is None:
            raise AppSessionError("Projede arazi yok — önce terrain_init çağrılmalı.")
        analyzer = TerrainHazardAnalyzer(entry.grid, rainfall_mm_24h=rainfall_mm_24h)
        r = analyzer.assess_point(x_m, y_m)
        return self._terrain_hazard_report_to_dict(r)

    def terrain_hazard_top_cells(
        self,
        project_id: str,
        *,
        kind: str = "landslide",
        limit: int = 20,
        rainfall_mm_24h: float | None = None,
    ) -> dict[str, Any]:
        """Sahnedeki en riskli `limit` hücreyi döner — "bu mahalle/parselde
        en riskli noktalar nerede" sorusuna doğrudan cevap; tüm grid'i
        (512x512'ye kadar) istemciye göndermek yerine."""
        from ..hazard_data import TerrainHazardAnalyzer

        entry = self._terrains.get(project_id)
        if entry is None:
            raise AppSessionError("Projede arazi yok — önce terrain_init çağrılmalı.")
        if kind not in ("landslide", "flood"):
            raise AppSessionError("kind 'landslide' ya da 'flood' olmalı.")
        analyzer = TerrainHazardAnalyzer(entry.grid, rainfall_mm_24h=rainfall_mm_24h)
        cells = analyzer.top_risk_cells(kind=kind, limit=limit)
        return {
            "kind": kind,
            "cells": [self._terrain_hazard_report_to_dict(c) for c in cells],
        }

    @staticmethod
    def _terrain_hazard_report_to_dict(r: Any) -> dict[str, Any]:
        return {
            "row": r.row,
            "col": r.col,
            "x_m": r.x_m,
            "y_m": r.y_m,
            "slope_percent": r.slope_percent,
            "slope_degrees": r.slope_degrees,
            "flow_accumulation": r.flow_accumulation,
            "landslide_index_0_100": r.landslide_index_0_100,
            "landslide_level": r.landslide_level.value,
            "landslide_factors": [
                {
                    "name": f_.name,
                    "subscore_0_100": f_.subscore_0_100,
                    "weight": f_.weight,
                    "note": f_.note,
                }
                for f_ in r.landslide_factors
            ],
            "flood_index_0_100": r.flood_index_0_100,
            "flood_level": r.flood_level.value,
            "flood_factors": [
                {
                    "name": f_.name,
                    "subscore_0_100": f_.subscore_0_100,
                    "weight": f_.weight,
                    "note": f_.note,
                }
                for f_ in r.flood_factors
            ],
            "disclaimer": r.disclaimer,
        }

    # -- Yol (Road) Editör köprüsü (Roadmap V4 - Faz E9) --------------------

    def road_add(
        self,
        project_id: str,
        *,
        road_id: str | None = None,
        width_m: float = 6.0,
        elevation_z: float = 0.0,
        name: str | None = None,
    ) -> dict[str, Any]:
        self._handle(project_id)
        rid = road_id or f"road_{uuid.uuid4().hex[:8]}"
        if rid in self._roads.get(project_id, {}):
            raise AppSessionError(f"Bu road_id zaten kullanılıyor: {rid}")
        road = Road(road_id=rid, width_m=width_m, elevation_z=elevation_z, name=name or rid)
        self._roads.setdefault(project_id, {})[rid] = _RoadEntry(road=road)
        self._persist_road(project_id, rid)
        return self.describe_road(project_id, rid)

    def list_roads(self, project_id: str) -> list[dict[str, Any]]:
        return [self.describe_road(project_id, rid) for rid in self._roads.get(project_id, {})]

    def describe_road(self, project_id: str, road_id: str) -> dict[str, Any]:
        entry = self._road_entry(project_id, road_id)
        road = entry.road
        return {
            "road_id": road_id,
            "name": road.name,
            "width_m": road.width_m,
            "elevation_z": road.elevation_z,
            "control_points": [[p.x, p.y] for p in road.control_points],
            "can_undo": entry.undo_stack.can_undo(),
            "can_redo": entry.undo_stack.can_redo(),
        }

    def _road_entry(self, project_id: str, road_id: str) -> _RoadEntry:
        entries = self._roads.get(project_id, {})
        if road_id not in entries:
            raise AppSessionError(f"Yol bulunamadı: proje={project_id} yol={road_id}")
        return entries[road_id]

    def remove_road(self, project_id: str, road_id: str, *, token: str | None = None) -> bool:
        self._authorize_write(project_id, token)
        entries = self._roads.get(project_id, {})
        existed = entries.pop(road_id, None) is not None
        if existed:
            self._handle(project_id).db.delete_object(road_id)
            self._handle(project_id).mark_dirty()
        return existed

    def road_add_point(
        self,
        project_id: str,
        road_id: str,
        x_m: float,
        y_m: float,
        *,
        index: int | None = None,
    ) -> dict[str, Any]:
        entry = self._road_entry(project_id, road_id)
        cmd = RoadEditor.add_point(entry.road, Point2D(x_m, y_m), index=index)
        entry.undo_stack.execute(cmd)
        self._persist_road(project_id, road_id)
        return self.describe_road(project_id, road_id)

    def road_move_point(
        self,
        project_id: str,
        road_id: str,
        index: int,
        x_m: float,
        y_m: float,
    ) -> dict[str, Any]:
        entry = self._road_entry(project_id, road_id)
        if not (0 <= index < len(entry.road.control_points)):
            raise AppSessionError(f"Geçersiz kontrol noktası indeksi: {index}")
        cmd = RoadEditor.move_point(entry.road, index, Point2D(x_m, y_m))
        entry.undo_stack.execute(cmd)
        self._persist_road(project_id, road_id)
        return self.describe_road(project_id, road_id)

    def road_remove_point(self, project_id: str, road_id: str, index: int) -> dict[str, Any]:
        entry = self._road_entry(project_id, road_id)
        if not (0 <= index < len(entry.road.control_points)):
            raise AppSessionError(f"Geçersiz kontrol noktası indeksi: {index}")
        cmd = RoadEditor.remove_point(entry.road, index)
        entry.undo_stack.execute(cmd)
        self._persist_road(project_id, road_id)
        return self.describe_road(project_id, road_id)

    def road_set_width(self, project_id: str, road_id: str, width_m: float) -> dict[str, Any]:
        if width_m <= 0:
            raise AppSessionError("width_m pozitif olmalı.")
        entry = self._road_entry(project_id, road_id)
        cmd = RoadEditor.set_width(entry.road, width_m)
        entry.undo_stack.execute(cmd)
        self._persist_road(project_id, road_id)
        return self.describe_road(project_id, road_id)

    def road_undo(self, project_id: str, road_id: str) -> dict[str, Any]:
        entry = self._road_entry(project_id, road_id)
        if not entry.undo_stack.can_undo():
            raise AppSessionError("Geri alınacak yol işlemi yok.")
        entry.undo_stack.undo()
        self._persist_road(project_id, road_id)
        return self.describe_road(project_id, road_id)

    def road_redo(self, project_id: str, road_id: str) -> dict[str, Any]:
        entry = self._road_entry(project_id, road_id)
        if not entry.undo_stack.can_redo():
            raise AppSessionError("İleri alınacak yol işlemi yok.")
        entry.undo_stack.redo()
        self._persist_road(project_id, road_id)
        return self.describe_road(project_id, road_id)

    # -- AI Assistant köprüsü (Faz 12) ------------------------------------

    def run_assistant_command(
        self, project_id: str, key: str, text: str, *, token: str | None = None
    ) -> dict[str, Any]:
        self._authorize_write(project_id, token)
        entry = self._entry(project_id, key)
        result = entry.orchestrator().execute_text(text)
        self._persist_building(project_id, key)
        return {
            "success": result.success,
            "messages": result.messages,
            "building": self.describe_building(project_id, key),
        }

    # -- Render köprüsü (Faz 15) --------------------------------------------

    # -- yeni_roadmap.md Faz 3.1: İç mekan görünürlüğü (kesit / patlatma) --

    # -- yeni_roadmap.md Faz 3.3: Ölçüm araçları -----------------------

    def measure(self, project_id: str, tool: str, points: list[list[float]]) -> dict[str, Any]:
        """Roadmap 3.3: 'analysis_engine/measurement zaten var - arayüzde
        tıkla-ölç (mesafe, alan, açı) aracı olarak yüzeye çıkarılmalı'.
        Önceden hiç `app_shell`e bağlanmamış `MeasurementEngine`'i, elle
        girilen (bu UI'daki diğer analiz panelleriyle - güneş/görünürlük -
        tutarlı) 3D nokta listeleriyle çalıştırır.

        `project_id` şu an yalnızca gelecekteki proje-bağlamlı ölçümler
        (örn. seçili binanın gerçek yüzeyine snap) için ayrılmıştır -
        mevcut haliyle ölçüm tamamen verilen noktalara göre yapılır ve
        projenin var olup olmadığı bilerek kontrol edilmez (measurement
        bina durumundan bağımsız saf geometri).
        """
        tool = tool.lower()
        pts = [tuple(float(c) for c in p) for p in points]

        if tool == "distance":
            if len(pts) < 2:
                raise AppSessionError("distance için en az 2 nokta gerekli")
            result = MeasurementEngine.distance_3d(pts[0], pts[1])
        elif tool == "height":
            if len(pts) < 2:
                raise AppSessionError("height için en az 2 nokta gerekli")
            result = MeasurementEngine.height_between(pts[0], pts[1])
        elif tool == "angle":
            if len(pts) < 3:
                raise AppSessionError("angle için en az 3 nokta gerekli (p1, köşe, p2)")
            result = MeasurementEngine.angle_3d(pts[0], pts[1], pts[2])
        elif tool == "slope":
            if len(pts) < 2:
                raise AppSessionError("slope için en az 2 nokta gerekli")
            result = MeasurementEngine.slope(pts[0], pts[1])
        elif tool == "area":
            if len(pts) < 3:
                raise AppSessionError("area için en az 3 nokta gerekli")
            polygon = Polygon(points=[Point2D(p[0], p[1]) for p in pts])
            result = MeasurementEngine.area(polygon)
        else:
            raise AppSessionError(
                f"Bilinmeyen ölçüm aracı: '{tool}'. Seçenekler: distance, height, angle, slope, area"
            )

        return {"tool": tool, "value": result.value, "unit": result.unit, "label": result.label}

    # -- Faz 4.2: rapor anlatıcı (report_narrator) köprüsü ----------------- #

    def facade_compliance(self, project_id: str, key: str) -> FacadeComplianceReport:
        """Roadmap 4.2/1.3: bir binanın cephesini (gerçek footprint/kat
        sayısı üzerinden, mesh üretmeden — `build_mesh=False` ile ucuz)
        yönetmelik uygunluğu için denetler. Önceden `session.py` hiç
        `FacadeGenerator`/`check_compliance` çağırmıyordu (FAZ0 raporu
        madde 19'da tespit edilen gerçek boşluk) — bu metod o boşluğu
        kapatır."""
        entry = self._entry(project_id, key)
        b = entry.building
        if not b.floors:
            raise AppSessionError("Bina hiç kat içermiyor, cephe denetimi yapılamaz.")
        floor_height = b.floors[0].height_m
        building_type = (
            b.building_type.value if hasattr(b.building_type, "value") else str(b.building_type)
        )
        facade = FacadeGenerator.generate(
            polygon=b.footprint.polygon,
            building_type=building_type,
            base_z=0.0,
            floor_height=floor_height,
            floor_count=len(b.floors),
            build_mesh=False,
        )
        return FacadeGenerator.check_compliance(
            facade,
            b.footprint.polygon,
            floor_height,
            len(b.floors),
            building_type,
        )

    def permit_precheck(
        self,
        project_id: str,
        key: str,
        *,
        plot_points: list[list[float]] | None = None,
        min_setback_m: float = 3.0,
        max_floor_count: int | None = None,
        max_height_m: float | None = None,
        profile_name: str | None = None,
    ) -> dict[str, Any]:
        """Roadmap iş fikri #5 — 'imar ön-kontrol / ruhsat alır mı
        simülasyonu'. `building_reconstruction.permit_precheck`'i sarar:
        pencere oranı + kaçış yolu (facade_generator), çekme mesafesi
        (parsel poligonu verilirse), kat sayısı/yükseklik sınırı (plan
        verisi verilirse) ve yapısal makuliyeti (structural_validation)
        tek bir 'muhtemelen geçer / revizyon gerekli / muhtemelen
        geçmez' raporunda birleştirir. Resmi ruhsat değerlendirmesinin
        yerine geçmez."""
        from ..building_reconstruction import get_regulation_profile, precheck_building
        from ..building_reconstruction.regulations import default_profile

        entry = self._entry(project_id, key)
        plot_polygon = None
        if plot_points:
            if len(plot_points) < 3:
                raise AppSessionError("plot_points en az 3 nokta içermeli (parsel sınırı).")
            plot_polygon = Polygon(points=[Point2D(p[0], p[1]) for p in plot_points])

        try:
            profile = get_regulation_profile(profile_name) if profile_name else default_profile()
        except KeyError as exc:
            raise AppSessionError(str(exc)) from exc

        report = precheck_building(
            entry.building,
            plot_polygon=plot_polygon,
            min_setback_m=min_setback_m,
            max_floor_count=max_floor_count,
            max_height_m=max_height_m,
            profile=profile,
        )
        return {
            "key": key,
            "verdict": report.verdict.value,
            "profile_name": report.profile_name,
            "profile_source": report.profile_source,
            "pass_count": report.pass_count,
            "fail_count": report.fail_count,
            "items": [
                {
                    "code": i.code,
                    "title": i.title,
                    "passed": i.passed,
                    "value": i.value,
                    "limit": i.limit,
                    "unit": i.unit,
                    "source": i.source,
                    "message": i.message,
                    "severity": i.severity.value,
                }
                for i in report.items
            ],
        }

    def energy_envelope_audit(
        self,
        project_id: str,
        key: str,
        *,
        climate_zone: int = 2,
        u_wall: float | None = None,
        u_window: float | None = None,
        u_roof: float | None = None,
        u_floor: float | None = None,
    ) -> dict[str, Any]:
        """Roadmap #4: "Bina enerji kabuğu denetimi — TS 825 camlanma
        oranı zaten regulations'ta var, ısı kaybı tahminine
        genişletilebilir." `facade_compliance()`'ın ürettiği gerçek
        duvar/pencere alanlarını (sahnedeki footprint/kat sayısından)
        `building_reconstruction.energy_audit.EnvelopeAuditor`'a besler."""
        from ..building_reconstruction.energy_audit import EnvelopeAuditor

        entry = self._entry(project_id, key)
        facade_report = self.facade_compliance(project_id, key)
        roof_area_m2 = entry.building.footprint.area_m2
        floor_area_m2 = entry.building.footprint.area_m2

        try:
            report = EnvelopeAuditor.audit(
                climate_zone=climate_zone,
                wall_area_m2=facade_report.wall_area_m2,
                window_area_m2=facade_report.window_area_m2,
                roof_area_m2=roof_area_m2,
                floor_area_m2=floor_area_m2,
                u_wall=u_wall,
                u_window=u_window,
                u_roof=u_roof,
                u_floor=u_floor,
            )
        except ValueError as exc:
            raise AppSessionError(str(exc)) from exc

        result = report.to_dict()
        result["key"] = key
        result["wall_area_m2"] = round(facade_report.wall_area_m2, 1)
        result["window_area_m2"] = round(facade_report.window_area_m2, 1)
        result["window_wall_ratio"] = round(facade_report.window_wall_ratio, 3)
        return result

    def energy_envelope_monthly_balance(
        self,
        project_id: str,
        key: str,
        *,
        monthly_mean_external_temp_c: list[float],
        monthly_solar_gain_kwh: list[float],
        monthly_internal_gain_kwh: list[float],
        climate_zone: int = 2,
        indoor_temp_c: float = 20.0,
        u_wall: float | None = None,
        u_window: float | None = None,
        u_roof: float | None = None,
        u_floor: float | None = None,
        air_changes_per_hour: float | None = None,
    ) -> dict[str, Any]:
        """Roadmap #4 devamı: EN ISO 13790 / EN 832 aylık quasi-steady-state
        yöntemi (`MonthlyBalanceAuditor`) — `energy_envelope_audit()`'in
        ürettiği iletim kaybı katsayısını (H_t) ve sahnedeki bina hacmini
        (footprint alanı x kat sayısı x kat yüksekliği) besler; havalandırma
        kaybı + güneş/iç kazanç + kullanım faktörünü de dahil eder. Aylık
        dış sıcaklık/kazanç dizileri (12 ay, Ocak..Aralık) kullanıcıdan
        gelir — sahte iklim verisi üretilmez."""
        from ..building_reconstruction.energy_audit import (
            DEFAULT_AIR_CHANGES_PER_HOUR,
            EnvelopeAuditor,
            MonthlyBalanceAuditor,
        )

        entry = self._entry(project_id, key)
        b = entry.building
        if not b.floors:
            raise AppSessionError("Bina hiç kat içermiyor, enerji denetimi yapılamaz.")
        floor_height = b.floors[0].height_m
        floor_count = len(b.floors)

        facade_report = self.facade_compliance(project_id, key)
        roof_area_m2 = entry.building.footprint.area_m2
        floor_area_m2 = entry.building.footprint.area_m2
        building_volume_m3 = floor_area_m2 * floor_count * floor_height

        try:
            envelope_report = EnvelopeAuditor.audit(
                climate_zone=climate_zone,
                wall_area_m2=facade_report.wall_area_m2,
                window_area_m2=facade_report.window_area_m2,
                roof_area_m2=roof_area_m2,
                floor_area_m2=floor_area_m2,
                u_wall=u_wall,
                u_window=u_window,
                u_roof=u_roof,
                u_floor=u_floor,
            )
            report = MonthlyBalanceAuditor.audit(
                total_heat_transfer_coefficient_w_per_k=(
                    envelope_report.total_heat_loss_coefficient_w_per_k
                ),
                building_volume_m3=building_volume_m3,
                indoor_temp_c=indoor_temp_c,
                monthly_mean_external_temp_c=monthly_mean_external_temp_c,
                monthly_solar_gain_kwh=monthly_solar_gain_kwh,
                monthly_internal_gain_kwh=monthly_internal_gain_kwh,
                air_changes_per_hour=(
                    air_changes_per_hour
                    if air_changes_per_hour is not None
                    else DEFAULT_AIR_CHANGES_PER_HOUR
                ),
            )
        except ValueError as exc:
            raise AppSessionError(str(exc)) from exc

        result = report.to_dict()
        result["key"] = key
        result["building_volume_m3"] = round(building_volume_m3, 1)
        result["transmission_source"] = "EnvelopeAuditor (facade/footprint tabanlı)"
        return result

    def narrate_facade_compliance(self, project_id: str, key: str) -> dict[str, Any]:
        """Roadmap 4.2: "Rapor anlatıcı (report_narrator.py) — analiz
        sonuçlarını doğal dilde özetleme özelliği arayüze bağlanmalı".
        AI sağlayıcısı yapılandırılmışsa gerçek LLM özeti dener; değilse
        (veya sağlayıcı başarısız olursa) `report_narrator` sessizce
        şablon tabanlı bir özete düşer — kullanıcı her durumda bir
        açıklama görür, hiçbir zaman istisna almaz."""
        report = self.facade_compliance(project_id, key)

        provider = None
        if self._ai_config is not None:
            from ..ai_assistant.llm_providers import (
                InvalidProviderConfigError,
                create_provider_from_config,
            )

            try:
                provider = create_provider_from_config(self._ai_config)
            except InvalidProviderConfigError:
                provider = None  # geçersiz yapılandırma -> sessizce şablona düş

        from ..ai_assistant.report_narrator import (
            narrate_facade_compliance as _narrate_facade,
        )

        narrative = _narrate_facade(report, provider=provider)
        return {
            "narrative": narrative,
            "used_ai": provider is not None,
            "is_compliant": report.is_compliant,
            "window_wall_ratio": round(report.window_wall_ratio, 4),
            "min_required_ratio": round(report.min_required_ratio, 4),
            "requires_fire_escape": report.requires_fire_escape,
            "issues": list(report.issues),
        }

    def section_view_scene(
        self,
        project_id: str,
        axis: str,
        offset_m: float,
        keep_positive: bool = True,
    ) -> dict[str, Any]:
        """Roadmap 3.1: 'Kesit düzlemi (section/clipping plane): X, Y, Z
        eksenlerinde sürüklenebilir kesit çizgisi'. Mevcut `visualization.
        section_view.SectionView.cut` (Faz 3'ten önce yazılmış, hiç UI'ya
        bağlanmamıştı) her binanın `full_mesh()`'ini verilen eksen +
        offset'teki düzlemle keser; `keep_positive` hangi tarafın sahnede
        kalacağını belirler. Arazi/yol/bitki örtüsü kesime dahil edilmez
        (yalnızca binalar - iç mekan görünürlüğü roadmap kapsamı).
        """
        axis = axis.lower()
        normals = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
        if axis not in normals:
            raise AppSessionError(f"Geçersiz eksen: '{axis}'. Seçenekler: x, y, z")
        nx, ny, nz = normals[axis]
        if not keep_positive:
            nx, ny, nz = -nx, -ny, -nz

        point = {
            "x": (offset_m, 0.0, 0.0),
            "y": (0.0, offset_m, 0.0),
            "z": (0.0, 0.0, offset_m),
        }[axis]
        plane = SectionPlane(point=point, normal=(nx, ny, nz))

        entries = self._buildings.get(project_id, {})
        scene = Scene(name=f"{project_id}_section")
        palette = [(0.68, 0.66, 0.62), (0.55, 0.58, 0.65), (0.72, 0.6, 0.5), (0.6, 0.65, 0.55)]
        for i, entry in enumerate(entries.values()):
            keep, _cut_away = SectionView.cut(
                entry.building.full_mesh(include_interior=True), plane
            )
            if not keep.triangles:
                continue
            keep.name = entry.key
            material = PBRMaterial(name=f"mat_{entry.key}", albedo=palette[i % len(palette)])
            scene.add_mesh(keep, material=material)
        return scene.to_dict()

    def explosion_view_scene(
        self,
        project_id: str,
        building_key: str,
        progress: float,
        gap_m: float = 2.0,
    ) -> dict[str, Any]:
        """Roadmap 3.1: 'Patlatma görünümü (explosion_view.py zaten var):
        katları birbirinden ayırarak gösterme, animasyonlu geçiş'. Tek bir
        binanın katlarını `ExplosionView` ile `progress` (0..1) oranında
        Z ekseninde ayırıp tek `Scene`'e (viewer'ın zaten bildiği JSON
        formatı) çevirir.
        """
        entries = self._buildings.get(project_id, {})
        entry = entries.get(building_key)
        if entry is None:
            raise AppSessionError(f"Bina bulunamadı: '{building_key}'")

        heights = [f.height_m for f in entry.building.floors]
        if not heights:
            raise AppSessionError(
                f"'{building_key}' binasının kat bilgisi yok (patlatma için gerekli)"
            )

        bands = floor_bands_from_heights(heights)
        view = ExplosionView(entry.building.full_mesh(include_interior=True), bands, gap_m=gap_m)

        scene = Scene(name=f"{project_id}_{building_key}_exploded")
        palette = [(0.68, 0.66, 0.62), (0.55, 0.58, 0.65), (0.72, 0.6, 0.5), (0.6, 0.65, 0.55)]
        for exploded in view.state_at(progress):
            floor_mesh = exploded.mesh
            if not floor_mesh.triangles:
                continue
            floor_mesh.name = f"{building_key}_floor_{exploded.band.level}"
            material = PBRMaterial(
                name=f"mat_floor_{exploded.band.level}",
                albedo=palette[exploded.band.level % len(palette)],
            )
            scene.add_mesh(floor_mesh, material=material)
        return scene.to_dict()

    def scene_json(self, project_id: str) -> dict[str, Any]:
        """Projedeki tüm binaları (+ Faz E9: arazi ve yollar, varsa) tek bir
        `Scene`'e birleştirip JSON'a çevirir."""
        entries = self._buildings.get(project_id, {})
        scene = Scene(name=project_id)
        palette = [
            (0.68, 0.66, 0.62),
            (0.55, 0.58, 0.65),
            (0.72, 0.6, 0.5),
            (0.6, 0.65, 0.55),
        ]
        # ROADMAP_V7 entegrasyon adımı: `ai_reconstruction.material_predictor.
        # AIMaterialPredictor` (bina bağlamına göre yüzey başı `FacadeMaterial`
        # tahmini) ve `ai_reconstruction.material_bridge.
        # resolve_building_materials()` (bu tahmini gerçek `PBRMaterial`'a -
        # ağ varsa ambientCG, yoksa prosedürel fallback - çeviren köprü)
        # önceden ikisi de hazır/test edilmişti ama hiçbir çağıran onları
        # kullanmıyordu. Artık burada gerçekten çağrılıyor: bina duvar
        # (`SurfaceClass.DUVAR`) yüzeyi için çözümlenen materyal, binanın
        # `Scene.add_mesh()` çağrısına geçiriliyor. `Scene.to_dict()`
        # (materyalleri `albedo_map` dahil JSON'a serileştirir, değişmedi)
        # ve viewer'daki `loadMaterialTextures`/`uAlbedoMap` (impostor
        # dokuları için yazılmıştı, değişmedi) bu binaların materyallerini
        # de aynı mekanizmayla besliyor.
        #
        # Eski `facade.pbr_material` + yerel prosedürel doku önbelleği
        # yolu, AI zinciri herhangi bir sebeple (ör. beklenmeyen
        # `building_type`, önbellek I/O hatası) materyal üretemezse güvenli
        # bir geri düşüş olarak korunuyor - sahne üretimi hiçbir zaman
        # kesilmez (B4 ilkesi).
        material_predictor = AIMaterialPredictor()
        texture_cache: dict[str, str] = {}
        for i, entry in enumerate(entries.values()):
            facade = entry.building.facade
            building_type = (
                entry.building.building_type.value
                if hasattr(entry.building.building_type, "value")
                else str(entry.building.building_type)
            )
            wall_material = None
            try:
                predictions = material_predictor.predict_all_surfaces(building_type=building_type)
                surface_materials = resolve_building_materials(predictions)
                wall_material = surface_materials.get(SurfaceClass.DUVAR.value)
            except Exception:
                wall_material = None

            if wall_material is not None:
                wall_material.name = f"mat_{entry.key}"
                material = wall_material
                mesh = entry.building.full_mesh(include_interior=True, generate_uvs=True)
            elif facade is not None and facade.pbr_material is not None:
                material_type = (
                    facade.material.value
                    if hasattr(facade.material, "value")
                    else str(facade.material)
                )
                albedo_map = texture_cache.get(material_type)
                if albedo_map is None:
                    try:
                        albedo_map = procedural_material_texture_data_uri(material_type)
                    except ValueError:
                        albedo_map = None
                    texture_cache[material_type] = albedo_map
                base = facade.pbr_material
                material = PBRMaterial(
                    name=f"mat_{entry.key}",
                    albedo=base.albedo,
                    albedo_map=albedo_map,
                    roughness=base.roughness,
                    metallic=base.metallic,
                    emissive=base.emissive,
                    opacity=base.opacity,
                )
                mesh = entry.building.full_mesh(include_interior=True, generate_uvs=True)
            else:
                # Güvenli geri düşüş: cephe/malzeme üretilmemiş (beklenmedik,
                # eski/kısmi bir `Building` nesnesi) - önceki jenerik palet
                # davranışı birebir korunur, hiçbir istisna fırlatılmaz.
                material = PBRMaterial(name=f"mat_{entry.key}", albedo=palette[i % len(palette)])
                mesh = entry.building.full_mesh(include_interior=True)
            mesh.name = entry.key
            # Roadmap V10 / Faz 4.4 — kalıcı hasar durumu (varsa) sahneye
            # her yüklemede yeniden uygulanır: bina eğik/opak render
            # edilir, sıfırlanmaz (`_damage_store`, Faz 3.A/3.B sallanma
            # sonrası `building_shake_simulate()` tarafından doldurulur).
            damage = self._damage_store.get(project_id, entry.key)
            rotation_deg = (0.0, 0.0, 0.0)
            if damage is not None:
                material.opacity = damage.opacity
                rotation_deg = (0.0, 0.0, math.degrees(damage.permanent_tilt_rad))
            scene.add_mesh(mesh, material=material, rotation_deg=rotation_deg)

        # Faz E9: arazi (varsa) yeşilimsi bir zemin materyaliyle eklenir —
        # her fırça darbesinden sonra `terrain_brush()` çağrıldığında
        # viewer bu sahneyi yeniden çekip (GET /scene) gerçek zamanlı
        # güncellenmiş geometriyi görür.
        terrain_entry = self._terrains.get(project_id)
        if terrain_entry is not None:
            terrain_mesh = TerrainMeshGenerator.generate(terrain_entry.grid, name="terrain")
            scene.add_mesh(
                terrain_mesh,
                material=PBRMaterial(name="mat_terrain", albedo=(0.35, 0.5, 0.3), roughness=0.95),
            )

        # Faz E9: her yol, spline'dan extrude edilmiş asfalt-gri bir
        # şerit mesh'i olarak eklenir.
        for road_entry in self._roads.get(project_id, {}).values():
            road_mesh = road_entry.road.to_mesh()
            if road_mesh.vertices:
                scene.add_mesh(
                    road_mesh,
                    material=PBRMaterial(
                        name=f"mat_{road_entry.road.road_id}",
                        albedo=(0.25, 0.25, 0.27),
                        roughness=0.85,
                    ),
                )

        # Web arayüzü genişletmesi: bitki örtüsü (vegetation) - her
        # VegetationInstance için deterministik bir ağaç mesh'i üretilip
        # kendi konumuna ötelenir.
        veg_material = PBRMaterial(name="mat_vegetation", albedo=(0.25, 0.45, 0.2), roughness=0.9)
        for idx, inst in enumerate(self._vegetation.get(project_id, [])):
            tree_mesh = TreeGenerator.generate(
                species=inst.species,
                height=inst.height,
                canopy_radius=inst.canopy_radius,
                seed=inst.seed,
                name=f"tree_{idx}",
            )
            placed = self._translate_mesh(tree_mesh, inst.x, inst.y, inst.z)
            scene.add_mesh(placed, material=veg_material)

        # Kök neden düzeltmesi (osm/import-layers -> 3D'de görünmüyor):
        # `import_osm_categories` ile içe aktarılan yol/ağaç/su/vb. katmanlar
        # `self._scene_props`'a kaydediliyordu ama `scene_json()` bu
        # sözlüğe hiç bakmıyordu — yalnızca binalar/arazi/manuel yol/manuel
        # bitki örtüsü sahneye ekleniyordu. Artık her scene_prop'un
        # (yukarıda `_save`'de eklenen) serileştirilmiş gerçek mesh'i
        # sahneye kategoriye uygun materyalle ekleniyor. Eski (mesh_data
        # içermeyen, önceki sürümden kalma) kayıtlar sessizce atlanır.
        for key, prop in self._scene_props.get(project_id, {}).items():
            mesh_data = prop.get("mesh_data")
            if not mesh_data:
                continue
            try:
                prop_mesh = _mesh_from_dict(mesh_data)
            except Exception:
                continue
            if not prop_mesh.vertices:
                continue
            prop_mesh.name = key
            albedo, roughness = _SCENE_PROP_MATERIALS.get(
                prop.get("category", ""), _DEFAULT_SCENE_PROP_MATERIAL
            )
            scene.add_mesh(
                prop_mesh,
                material=PBRMaterial(name=f"mat_{key}", albedo=albedo, roughness=roughness),
            )

        return scene.to_dict()

    # -- İçe/dışa aktarım köprüsü (Roadmap V3 Faz D16) ---------------------

    def import_geojson(
        self,
        project_id: str,
        geojson_text: str,
        *,
        seed: int | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Bir GeoJSON metnini (FeatureCollection/Feature/geometry) gerçek
        `GeoJSONParser` + `FootprintParser` ile ayrıştırıp, `Polygon`
        geometrisine sahip her feature'ı prosedürel bir `Building`'e
        dönüştürüp projeye ekler. Polygon dışı geometriler (Point/LineString)
        ve ayrıştırma hatası veren feature'lar atlanır, sonuçta raporlanır —
        sessizce yutulmaz.
        """
        self._authorize_write(project_id, token)
        handle = self._handle(project_id)
        try:
            collection = GeoJSONParser.parse(geojson_text)
        except Exception as exc:  # noqa: BLE001 - kullanıcıya okunur hata
            raise AppSessionError(f"GeoJSON ayrıştırma hatası: {exc}") from exc

        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for i, feature in enumerate(collection.features):
            if feature.geometry_type != "Polygon":
                skipped.append(
                    {"index": i, "reason": f"desteklenmeyen geometri: {feature.geometry_type}"}
                )
                continue
            try:
                footprint = FootprintParser.parse(feature)
                points = [(p.x, p.y) for p in footprint.polygon.points]
                info = self.add_building(
                    project_id,
                    points,
                    building_type=footprint.building_type or "apartman",
                    floor_count=footprint.floor_count,
                    height_m=footprint.height_m,
                    seed=seed,
                    token=token,
                )
                created.append(info)
            except Exception as exc:  # noqa: BLE001
                skipped.append({"index": i, "reason": str(exc)})

        handle.mark_dirty()
        return {
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created": created,
            "skipped": skipped,
        }

    OSM_ATTRIBUTION = "© OpenStreetMap katkıda bulunanlar (ODbL)"

    def preview_osm_bbox(
        self,
        project_id: str,
        south: float,
        west: float,
        north: float,
        east: float,
        *,
        client: OverpassClient | None = None,
    ) -> dict[str, Any]:
        """Tek tek bina seçimi için: bbox içindeki OSM bina footprint'lerini
        Overpass'tan çeker ama HİÇBİRİNİ projeye eklemez — yalnızca WGS84
        (lat/lon) halkalarını, `osm_id`'lerini ve etiketlerini döndürür ki
        web arayüzü haritada her binayı ayrı ayrı tıklanabilir bir poligon
        olarak çizip kullanıcının tek tek/çoklu seçim yapmasını sağlasın.
        `import_osm_bbox`'tan farklı olarak `add_building`/undo-redo/
        kalıcılık boru hattına hiç dokunmaz (salt-okunur önizleme).
        """
        self._handle(project_id)  # projenin var olduğunu doğrula
        self._require_online("OSM bina önizleme (preview_osm_bbox)")
        try:
            bbox = BBox(
                min_lat=float(south), min_lon=float(west), max_lat=float(north), max_lon=float(east)
            )
        except (TypeError, ValueError) as exc:
            raise AppSessionError(f"Geçersiz bbox: {exc}") from exc

        if client is None:
            client = OverpassClient()
        try:
            raw_collection = client.fetch_building_footprints(bbox)
        except OverpassError as exc:
            raise AppSessionError(f"OSM Overpass sorgusu başarısız: {exc}") from exc

        features_out: list[dict[str, Any]] = []
        for feature in raw_collection.features:
            ring = feature.coordinates[0] if feature.coordinates else []
            # ring elemanları (lon, lat) sırasında; frontend Leaflet
            # [lat, lon] bekler.
            latlngs = [[pt[1], pt[0]] for pt in ring]
            props = feature.properties or {}
            floor_count = props.get("building:levels")
            try:
                floor_count = int(float(floor_count)) if floor_count is not None else None
            except (TypeError, ValueError):
                floor_count = None
            features_out.append(
                {
                    "osm_id": props.get("osm_id"),
                    "osm_type": props.get("osm_type", "way"),
                    "building_type": props.get("building"),
                    "name": props.get("name"),
                    "floor_count": floor_count,
                    "latlngs": latlngs,
                }
            )

        return {
            "feature_count": len(features_out),
            "features": features_out,
            "attribution": self.OSM_ATTRIBUTION,
        }

    def osm_category_summary(
        self,
        project_id: str,
        south: float,
        west: float,
        north: float,
        east: float,
        *,
        categories: list[str] | None = None,
        client: OverpassClient | None = None,
    ) -> dict[str, Any]:
        """ROADMAP_V7.md Faz C6 (A2/B5): "Katman bazlı istatistik: 'Bu
        bölgede: 42 bina, 156 ağaç, 12 direk, 3.2 km yol' gibi özet bilgi
        import öncesi önizlemede gösterilecek." ve "Katman bazlı içe
        aktarma: kullanıcı sadece 'Yollar + Ağaçlar' seçip bbox import
        edebilmeli."

        Bu, C2'nin `fetch_category_features`/`summarize_categories`'ini
        (değiştirilmeden) tüketen salt-okunur bir önizleme uç noktasıdır -
        `preview_osm_bbox`'un bina-dışı kategoriler için karşılığı. Hiçbir
        şeyi projeye eklemez (B5 "import öncesi önizleme" ilkesi).
        `categories=None` => `DEFAULT_CATEGORIES`'in tamamı istenir.
        """
        self._handle(project_id)  # projenin var olduğunu doğrula
        self._require_online("OSM kategori özeti (osm_category_summary)")
        try:
            bbox = BBox(
                min_lat=float(south), min_lon=float(west), max_lat=float(north), max_lon=float(east)
            )
        except (TypeError, ValueError) as exc:
            raise AppSessionError(f"Geçersiz bbox: {exc}") from exc

        if categories is not None:
            unknown = [k for k in categories if k not in DEFAULT_CATEGORIES]
            if unknown:
                raise AppSessionError(f"Bilinmeyen kategori(ler): {unknown}")

        try:
            collection = fetch_category_features(bbox, categories, client=client)
        except OverpassError as exc:
            raise AppSessionError(f"OSM Overpass kategori sorgusu başarısız: {exc}") from exc

        counts = summarize_categories(collection)
        return {
            "counts": counts,
            "total_feature_count": sum(counts.values()),
            "available_categories": sorted(DEFAULT_CATEGORIES.keys()),
            "attribution": self.OSM_ATTRIBUTION,
        }

    #: Kategori anahtarı -> hangi "grup" işleyicisine düştüğü (B1'in
    #: kategori-gruplarıyla bire bir): C3'ün 7 OSM köprüsünün tümü tek
    #: yerden yönlendirilir. `roads`/`waterway`/`water_area` tek bir
    #: `generate_infrastructure_for_collection` çağrısına düşer (o
    #: fonksiyon zaten üç kategoriyi de kendi içinde ayırıyor).
    _SCENE_PROP_GROUPS: dict[str, str] = {
        "trees": "vegetation",
        "forest": "vegetation",
        "wood": "vegetation",
        "roads": "infrastructure",
        "waterway": "infrastructure",
        "water_area": "infrastructure",
        # ROADMAP_V8 Faz 5.6a — `crossing` (Point, highway=crossing) de
        # `generate_infrastructure_for_collection` içinde işleniyor
        # (yaya geçidi doku yaması); aynı gruba eklendi.
        "crossing": "infrastructure",
        "street_lamp": "street_furniture",
        "power_pole": "street_furniture",
        "waste_basket": "street_furniture",
        "bench": "street_furniture",
        "bus_stop": "street_furniture",
        "bus_station": "street_furniture",
        "place_of_worship": "religious_structures",
        "marketplace": "commerce_props",
        "restaurant": "commerce_props",
        "cafe": "commerce_props",
        "pitch": "sport_recreation",
        "stadium": "sport_recreation",
        "swimming_pool": "sport_recreation",
        "playground": "sport_recreation",
        "power_line": "power_infrastructure",
        "substation": "power_infrastructure",
        "communication_tower": "power_infrastructure",
    }

    @staticmethod
    def _mesh_centroid_local(mesh) -> tuple[float, float]:
        if not mesh.vertices:
            return (0.0, 0.0)
        n = len(mesh.vertices)
        return (
            sum(v.x for v in mesh.vertices) / n,
            sum(v.y for v in mesh.vertices) / n,
        )

    def import_osm_categories(
        self,
        project_id: str,
        south: float,
        west: float,
        north: float,
        east: float,
        categories: list[str],
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        """ROADMAP_V7.md Faz C6 (2. dilim) — B5'in "kullanıcı sadece
        'Yollar + Ağaçlar' seçip bbox import edebilmeli" maddesinin
        gerçek (yalnızca önizleme değil) karşılığı: seçilen kategoriler
        C3'ün 7 OSM köprüsünden (vegetation, editor.osm_bridge,
        street_furniture, religious_structures, commerce_props,
        sport_recreation, power_infrastructure) geçirilir, her feature
        gerçekten prosedürel mesh'e çevrilir (doğrulama amaçlı — mesh'in
        kendisi disk kaydına yazılmaz, `describe`de vertex/triangle
        sayısı ve WGS84 konumu tutulur; roadmap'in "mevcut mimari
        korunacak" ilkesiyle tutarlı, binaların aksine bu prop'lar
        `Building` gibi ağır bir dataclass değil - deterministik ve
        ucuz, tekrar üretmek yerine özet saklamak yeterli).

        `import_osm_bbox` (binalar) ile AYNI mimariyi izler: bbox
        doğrulama -> Overpass -> yerel metre projeksiyonu -> üretim ->
        `handle.db.save_object(..., "scene_prop", ...)` -> `mark_dirty`.
        Bina boru hattına hiç dokunmaz (ayrı, ek bir yol).
        """
        self._authorize_write(project_id, token)
        self._require_online("OSM katman içe aktarma (import_osm_categories)")
        handle = self._handle(project_id)
        try:
            bbox = BBox(
                min_lat=float(south), min_lon=float(west), max_lat=float(north), max_lon=float(east)
            )
        except (TypeError, ValueError) as exc:
            raise AppSessionError(f"Geçersiz bbox: {exc}") from exc

        if not isinstance(categories, list) or not categories:
            raise AppSessionError("categories boş olmayan bir liste olmalı.")
        unknown = [k for k in categories if k not in DEFAULT_CATEGORIES]
        if unknown:
            raise AppSessionError(f"Bilinmeyen kategori(ler): {unknown}")

        try:
            raw_collection = fetch_category_features(bbox, categories)
        except OverpassError as exc:
            raise AppSessionError(f"OSM Overpass kategori sorgusu başarısız: {exc}") from exc

        origin = bbox.center()
        local_collection = project_to_local_meters(raw_collection, origin=origin)

        groups_needed = {
            self._SCENE_PROP_GROUPS[k] for k in categories if k in self._SCENE_PROP_GROUPS
        }
        entries = self._scene_props.setdefault(project_id, {})
        created: list[dict[str, Any]] = []
        counts: dict[str, int] = {}

        def _save(
            category: str, local_x: float, local_y: float, mesh, extra: dict[str, Any] | None = None
        ) -> None:
            geo = local_meters_collection_centroid_to_wgs84(local_x, local_y, origin)
            key = f"prop_{uuid.uuid4().hex[:10]}"
            data = {
                "category": category,
                "lat": geo.lat,
                "lon": geo.lon,
                "vertex_count": mesh.vertex_count(),
                "triangle_count": mesh.triangle_count(),
                "mesh_name": mesh.name,
                # Kök neden düzeltmesi: önceden yalnızca özet (vertex/triangle
                # sayısı) saklanıyordu, gerçek geometri hiçbir yerde tutulmuyordu.
                # `scene_json()` binaları/yolları/ağaçları sahneye eklerken bu
                # kategoriye (scene_prop) hiç bakmıyordu çünkü bakacak geometri
                # yoktu — 3D görünümde bina dışında hiçbir şeyin görünmemesinin
                # gerçek sebebi buydu. Mesh artık serileştirilip kaydediliyor,
                # böylece hem oturum içinde hem disk'ten yeniden yüklemede
                # `scene_json()` gerçek geometriyi sahneye ekleyebiliyor.
                "mesh_data": _mesh_to_dict(mesh),
            }
            if extra:
                data.update(extra)
            entries[key] = data
            handle.db.save_object(key, "scene_prop", data)
            created.append({k: v for k, v in data.items() if k != "mesh_data"} | {"key": key})
            counts[category] = counts.get(category, 0) + 1

        if "vegetation" in groups_needed:
            for instance in generate_vegetation_for_collection(local_collection):
                mesh = mesh_for_tree_instance(instance)
                _save(
                    "vegetation", instance.x, instance.y, mesh, {"species": instance.species.value}
                )

        if "infrastructure" in groups_needed:
            infra = generate_infrastructure_for_collection(local_collection)
            # ROADMAP_V8 Faz 5.6a — her yol mesh'i artık paralel-indeksli
            # `road_surface_materials`'tan bir malzeme preset anahtarı
            # taşıyor; `vegetation` köprüsünün `{"species": ...}` deseniyle
            # tutarlı şekilde `extra` metadata olarak kaydedilir.
            for i, mesh in enumerate(infra.road_meshes):
                x, y = self._mesh_centroid_local(mesh)
                surface = (
                    infra.road_surface_materials[i]
                    if i < len(infra.road_surface_materials)
                    else None
                )
                _save("roads", x, y, mesh, {"surface_material": surface} if surface else None)
            for mesh in infra.waterway_meshes:
                x, y = self._mesh_centroid_local(mesh)
                _save("waterway", x, y, mesh)
            for mesh in infra.water_area_meshes:
                x, y = self._mesh_centroid_local(mesh)
                _save("water_area", x, y, mesh)
            # ROADMAP_V8 Faz 5.6a — şerit çizgisi ve yaya geçidi, ayrı
            # kategorilerde (B3: istemci tarafında ayrı LOD/görünürlük
            # anahtarı olabilsin diye `roads`'a karıştırılmaz).
            for mesh in infra.lane_marking_meshes:
                x, y = self._mesh_centroid_local(mesh)
                _save("lane_marking", x, y, mesh)
            for mesh in infra.crosswalk_meshes:
                x, y = self._mesh_centroid_local(mesh)
                _save("crosswalk", x, y, mesh)

        if "street_furniture" in groups_needed:
            for item in generate_street_furniture_for_collection(local_collection):
                mesh = StreetFurnitureGenerator.generate(item)
                _save(
                    "street_furniture",
                    item.position.x,
                    item.position.y,
                    mesh,
                    {"furniture_type": item.furniture_type.value},
                )

        if "religious_structures" in groups_needed:
            for item in generate_religious_structures_for_collection(local_collection):
                mesh = ReligiousStructureGenerator.generate(item)
                _save(
                    "religious_structures",
                    item.position.x,
                    item.position.y,
                    mesh,
                    {"religion": item.religion.value},
                )

        if "commerce_props" in groups_needed:
            for prop in generate_commerce_props_for_collection(local_collection):
                if isinstance(prop, MarketStallLayout):
                    mesh = CommercePropsGenerator.generate_market(prop)
                    xs = [p.x for p in prop.positions] or [0.0]
                    ys = [p.y for p in prop.positions] or [0.0]
                    _save(
                        "marketplace",
                        sum(xs) / len(xs),
                        sum(ys) / len(ys),
                        mesh,
                        {"stall_count": len(prop.positions)},
                    )
                elif isinstance(prop, OutdoorSeatingItem):
                    mesh = CommercePropsGenerator.outdoor_seating_set(prop)
                    _save("outdoor_seating", prop.position.x, prop.position.y, mesh)

        if "sport_recreation" in groups_needed:
            for item in generate_sport_recreation_for_collection(local_collection):
                if isinstance(item, SportAreaItem):
                    mesh = SportRecreationGenerator.generate_area(item)
                    xs = [p.x for p in item.polygon.points]
                    ys = [p.y for p in item.polygon.points]
                    _save(item.area_type.value, sum(xs) / len(xs), sum(ys) / len(ys), mesh)
                elif isinstance(item, PlaygroundItem):
                    mesh = SportRecreationGenerator.playground(item.position, item.ground_z)
                    _save("playground", item.position.x, item.position.y, mesh)

        if "power_infrastructure" in groups_needed:
            for item in generate_power_infrastructure_for_collection(local_collection):
                if isinstance(item, Road):
                    mesh = PowerInfrastructureGenerator.mesh_for_power_line(item)
                    x, y = self._mesh_centroid_local(mesh)
                    _save("power_line", x, y, mesh)
                elif isinstance(item, SubstationItem):
                    mesh = PowerInfrastructureGenerator.substation(item)
                    xs = [p.x for p in item.polygon.points]
                    ys = [p.y for p in item.polygon.points]
                    _save("substation", sum(xs) / len(xs), sum(ys) / len(ys), mesh)
                elif isinstance(item, CommunicationTowerItem):
                    mesh = PowerInfrastructureGenerator.communication_tower(item)
                    _save("communication_tower", item.position.x, item.position.y, mesh)

        handle.mark_dirty()
        return {
            "raw_feature_count": len(raw_collection.features),
            "created_count": len(created),
            "counts": counts,
            "created": created,
            "origin": {"lat": origin.lat, "lon": origin.lon},
            "attribution": self.OSM_ATTRIBUTION,
        }

    def list_scene_props(self, project_id: str) -> list[dict[str, Any]]:
        self._handle(project_id)  # var olduğunu doğrula
        return [{"key": key, **data} for key, data in self._scene_props.get(project_id, {}).items()]

    def remove_scene_prop(self, project_id: str, key: str, *, token: str | None = None) -> bool:
        self._authorize_write(project_id, token)
        entries = self._scene_props.get(project_id, {})
        existed = entries.pop(key, None) is not None
        if existed:
            handle = self._handle(project_id)
            handle.db.delete_object(key)
            handle.mark_dirty()
        return existed

    def import_osm_bbox(
        self,
        project_id: str,
        south: float,
        west: float,
        north: float,
        east: float,
        *,
        seed: int | None = None,
        client: OverpassClient | None = None,
        token: str | None = None,
        osm_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """Roadmap Faz 2.5'in uçtan uca akışının somut karşılığı: kullanıcının
        haritada çizdiği bir bbox -> gerçek Overpass sorgusu -> yerel metre
        projeksiyonu -> `FootprintParser` -> `add_building` (mevcut undo/redo,
        doğrulama ve kalıcılık boru hattından geçerek) -> projeye gerçek
        binalar olarak eklenir.

        `fetch_and_generate_buildings`'ten (osm_client) farklı olarak burada
        binalar `AppSession.add_building` üzerinden eklenir, böylece her biri
        de diğer tüm binalar gibi undo/redo geçmişine, kalıcı depolamaya ve
        girdi doğrulamasına tabi olur.

        `osm_ids` verilirse (bkz. `preview_osm_bbox` + haritadaki tek tek
        bina seçimi), yalnızca bu OSM way/relation id'lerine sahip binalar
        içe aktarılır; bbox içindeki diğerleri "seçilmedi" sebebiyle
        `skipped` listesine eklenir. `osm_ids=None` (varsayılan) eski
        toplu-aktarma davranışını korur.
        """
        self._require_online("OSM bina içe aktarma (import_osm_bbox)")
        handle = self._handle(project_id)
        try:
            bbox = BBox(
                min_lat=float(south), min_lon=float(west), max_lat=float(north), max_lon=float(east)
            )
        except (TypeError, ValueError) as exc:
            raise AppSessionError(f"Geçersiz bbox: {exc}") from exc

        if client is None:
            client = OverpassClient()
        try:
            raw_collection = client.fetch_building_footprints(bbox)
        except OverpassError as exc:
            raise AppSessionError(f"OSM Overpass sorgusu başarısız: {exc}") from exc

        origin = bbox.center()
        local_collection = project_to_local_meters(raw_collection, origin=origin)

        osm_id_filter = set(osm_ids) if osm_ids else None

        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for i, feature in enumerate(local_collection.features):
            try:
                if osm_id_filter is not None:
                    feature_osm_id = (feature.properties or {}).get("osm_id")
                    if feature_osm_id not in osm_id_filter:
                        skipped.append({"index": i, "reason": "seçilmedi (tek bina seçim modu)"})
                        continue
                footprint = FootprintParser.parse(feature)
                if footprint.area_m2 <= 0.5:
                    skipped.append({"index": i, "reason": "alan çok küçük (<0.5 m²)"})
                    continue
                points = [(p.x, p.y) for p in footprint.polygon.points]
                info = self.add_building(
                    project_id,
                    points,
                    building_type=footprint.building_type or "apartman",
                    floor_count=footprint.floor_count,
                    height_m=footprint.height_m,
                    seed=seed,
                    token=token,
                )
                created.append(info)
            except Exception as exc:  # noqa: BLE001
                skipped.append({"index": i, "reason": str(exc)})

        handle.mark_dirty()
        return {
            "raw_feature_count": len(raw_collection.features),
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created": created,
            "skipped": skipped,
            "origin": {"lat": origin.lat, "lon": origin.lon},
            "attribution": self.OSM_ATTRIBUTION,
        }

    _EXPORT_FORMATS = {
        "obj",
        "stl",
        "ply",
        "gltf",
        "glb",
        "dxf",
        "3dtiles",
        "ifc",
        "usda",
        "citygml",
    }

    def export_scene(
        self,
        project_id: str,
        fmt: str,
        *,
        out_dir: str | None = None,
    ) -> dict[str, Any]:
        """Projedeki tüm binaları, `fmt`'e göre gerçek bir dosyaya (ya da
        3D Tiles için bir dizine) yazar. Dönüş değeri diskteki gerçek
        yol(lar)ı ve okunabilir bir özet içerir — sahte/placeholder bayt
        döndürülmez.
        """
        fmt = fmt.lower()
        if fmt not in self._EXPORT_FORMATS:
            raise AppSessionError(
                f"Desteklenmeyen export formatı: {fmt!r} (desteklenen: {sorted(self._EXPORT_FORMATS)})"
            )
        handle = self._handle(project_id)
        entries = list(self._buildings.get(project_id, {}).values())
        if not entries:
            raise AppSessionError("Projede export edilecek bina yok.")

        # Roadmap V3 - D19: `out_dir` bilinçli olarak esnek bırakılıyor -
        # `app_shell` çok-kiracılı bir sunucu değil, tek kullanıcının kendi
        # dosya sistemine export ettiği yerel bir masaüstü/CLI oturumu
        # (bkz. modül docstring'i, "gerçek bir betik veya Jupyter içinde de
        # kullanılabilir"); kullanıcı kendi diskinde istediği hedefi
        # seçebilmeli (tıpkı bir masaüstü uygulamasındaki "Farklı Kaydet"
        # gibi). Yine de fuzz-güvenli asgari tip/biçim kontrolü (D19)
        # burada uygulanıyor: `out_dir` bir metin (str) olmalı ve null-byte
        # / kontrol karakteri içeremez (dosya sistemi çağrılarına
        # geçirilmeden önce OWASP API8:2023 "Security Misconfiguration"
        # sınıfında girdi-doğrulama asgarisi).
        if out_dir is not None:
            if not isinstance(out_dir, str):
                raise AppSessionError("out_dir bir metin (str) olmalı.")
            if "\x00" in out_dir or any(ord(c) < 0x20 and c not in ("\t",) for c in out_dir):
                raise AppSessionError("out_dir kontrol karakteri (null-byte dahil) içeremez.")

        allowed_root = Path(handle.path).resolve().parent
        if out_dir:
            candidate = Path(out_dir)
            resolved = (
                (allowed_root / candidate).resolve()
                if not candidate.is_absolute()
                else candidate.resolve()
            )
            try:
                resolved.relative_to(allowed_root)
            except ValueError as exc:
                raise AppSessionError(
                    f"out_dir izin verilen kökün ({allowed_root}) dışında: {out_dir!r} "
                    "(path-traversal engellendi — OWASP API8:2023)."
                ) from exc
            export_root = resolved
        else:
            export_root = allowed_root / "exports"
        export_root.mkdir(parents=True, exist_ok=True)

        if fmt == "3dtiles":
            # ROADMAP_V7 entegrasyon adımı (devamı): `scene_json()` ile aynı
            # AI materyal zincirini (`AIMaterialPredictor.predict_all_surfaces()`
            # + `resolve_building_materials()`) burada da çalıştırıyoruz -
            # önceden bu export yolu materyal geçirmeden `add_mesh(mesh)`
            # çağırıyordu, yani diske yazılan 3D Tiles dosyalarında AI
            # materyalleri hiç yoktu (yalnızca canlı viewer besleniyordu).
            # Aynı güvenli geri düşüş (facade.pbr_material -> None) burada
            # da geçerli; export hiçbir sebeple kesilmez.
            scene = Scene(name=project_id)
            material_predictor = AIMaterialPredictor()
            for entry in entries:
                building_type = (
                    entry.building.building_type.value
                    if hasattr(entry.building.building_type, "value")
                    else str(entry.building.building_type)
                )
                wall_material = None
                try:
                    predictions = material_predictor.predict_all_surfaces(
                        building_type=building_type
                    )
                    surface_materials = resolve_building_materials(predictions)
                    wall_material = surface_materials.get(SurfaceClass.DUVAR.value)
                except Exception:
                    wall_material = None
                if wall_material is not None:
                    wall_material.name = f"mat_{entry.key}"
                mesh = entry.building.full_mesh(include_interior=True)
                mesh.name = entry.key
                scene.add_mesh(mesh, material=wall_material)
            tiles_dir = export_root / f"{project_id}_3dtiles"
            result = Tiles3DExporter.export(scene, str(tiles_dir))
            return {"format": fmt, "path": str(tiles_dir), "bytes_written": result.bytes_written}

        if fmt == "ifc":
            model = IFCBuildingModel(name=project_id)
            for entry in entries:
                b = entry.building
                z = 0.0
                for floor_idx, floor in enumerate(b.floors):
                    room = IFCRoom(
                        name=f"{entry.key}_floor{floor_idx}",
                        polygon=b.footprint.polygon,
                        floor_z=z,
                        height=floor.height_m,
                    )
                    model.rooms.append(room)
                    z += floor.height_m
            ifc_path = export_root / f"{project_id}.ifc"
            ifc_result = IFCExporter.export(model, str(ifc_path))
            return {"format": fmt, "path": str(ifc_path), "bytes_written": ifc_result.bytes_written}

        if fmt == "citygml":
            # Fikir #6: Konsept mimari kütle çalışması -> CityGML export.
            # LOD1 (ekstrüde kütle) -- mimarlık ofisi ön-tasarımını
            # şehir-ölçeği semantik BIM/GIS formatına köprüler.
            from ..export.cityjson_export import CityBuilding, CityModel

            city_model = CityModel(crs_name=None)
            for entry in entries:
                b = entry.building
                city_model.add(
                    CityBuilding(
                        building_id=entry.key,
                        footprint=b.footprint.polygon,
                        height=b.total_height_m,
                        ground_z=0.0,
                        year_of_construction=getattr(entry.building, "construction_year", None),
                        function=str(getattr(entry.building, "building_type", "") or "") or None,
                        attributes={"floor_count": len(b.floors)},
                    )
                )
            citygml_path = export_root / f"{project_id}.gml"
            citygml_result = CityGMLExporter.export(city_model, str(citygml_path))
            return {
                "format": fmt,
                "path": str(citygml_path),
                "bytes_written": citygml_result.bytes_written,
            }

        # Roadmap V7 - "FBX/GLB desteği" fazı: GLB (binary glTF) zaten tam
        # ve gerçek biçimde uygulanmıştı (bkz. export/geometry_3d.py
        # GLTFExporter.export_glb / GLTFImporter.import_glb - açık, bağımlılıksız
        # bir binary format). API'de "glb" ayrı, açık bir format adı olarak
        # seçilebiliyor ("gltf" adı da geriye dönük uyumlu olarak kabul edilir).
        #
        # Denetim maddesi (c) - "bina yüzey materyallerinin viewer'a uçtan uca
        # bağlanması ... export akışına henüz bağlanmamış": bu dal önceden
        # `MeshMerger.merge(...)` ile TÜM binaları TEK, materyalsiz bir mesh'e
        # birleştirip `GLTFExporter.export_glb(merged_mesh)` çağırıyordu -
        # `GLTFExporter` hiçbir materyal parametresi almadığından, diske
        # yazılan .glb dosyasında binaların malzemesine dair HİÇBİR bilgi
        # yoktu (yalnızca `scene_json()`/canlı viewer besleniyordu). Artık
        # `scene_json()` ile AYNI materyal zincirini (`AIMaterialPredictor.
        # predict_all_surfaces()` + `resolve_building_materials()`, güvenli
        # geri düşüş: facade.pbr_material -> jenerik palet) kullanarak her
        # binayı kendi çözümlenmiş `PBRMaterial`'iyle bir `Scene`'e ekliyor
        # ve `SceneGLTFExporter.export_glb()` (bkz. o sınıfın docstring'i -
        # standart glTF `materials[].pbrMetallicRoughness`, çoklu mesh/
        # materyal desteği) ile TEK dosyada, her bina kendi rengiyle/
        # pürüzlülüğüyle çıkarılıyor.
        if fmt in ("gltf", "glb"):
            scene = Scene(name=project_id)
            material_predictor = AIMaterialPredictor()
            palette = [
                (0.68, 0.66, 0.62),
                (0.55, 0.58, 0.65),
                (0.72, 0.6, 0.5),
                (0.6, 0.65, 0.55),
            ]
            for i, entry in enumerate(entries):
                building_type = (
                    entry.building.building_type.value
                    if hasattr(entry.building.building_type, "value")
                    else str(entry.building.building_type)
                )
                wall_material = None
                try:
                    predictions = material_predictor.predict_all_surfaces(
                        building_type=building_type
                    )
                    surface_materials = resolve_building_materials(predictions)
                    wall_material = surface_materials.get(SurfaceClass.DUVAR.value)
                except Exception:
                    wall_material = None
                facade = entry.building.facade
                if wall_material is not None:
                    wall_material.name = f"mat_{entry.key}"
                    material = wall_material
                elif facade is not None and facade.pbr_material is not None:
                    base = facade.pbr_material
                    material = PBRMaterial(
                        name=f"mat_{entry.key}",
                        albedo=base.albedo,
                        roughness=base.roughness,
                        metallic=base.metallic,
                        emissive=base.emissive,
                        opacity=base.opacity,
                    )
                else:
                    material = PBRMaterial(
                        name=f"mat_{entry.key}", albedo=palette[i % len(palette)]
                    )
                mesh = entry.building.full_mesh(include_interior=True)
                mesh.name = entry.key
                scene.add_mesh(mesh, material=material)
            out_path = export_root / f"{project_id}.glb"
            result = SceneGLTFExporter.export_glb(scene, str(out_path))
            return {
                "format": fmt,
                "path": str(out_path),
                "bytes_written": result.bytes_written,
                "vertex_count": result.vertex_count,
                "triangle_count": result.triangle_count,
            }

        merged = MeshMerger.merge(
            [entry.building.full_mesh(include_interior=True) for entry in entries],
            name=project_id,
        )
        ext = fmt
        out_path = export_root / f"{project_id}.{ext}"
        export_fn = {
            "obj": OBJExporter.export,
            "stl": STLExporter.export,
            "ply": PLYExporter.export,
            "dxf": DXFExporter.export,
            # FBX ve ikili USD/USDZ resmi SDK gerektirdiği için desteklenmiyor
            # (bkz. export/geometry_3d.py FBXExporter/USDExporter docstring'leri).
            # `usda`, USD'nin bağımlılıksız yazılabilen ASCII alt kümesidir ve
            # Blender/Houdini/USD View gibi araçlarca doğrudan okunabilir.
            "usda": USDExporter.export_usda,
        }[fmt]
        try:
            result = export_fn(merged, str(out_path))
        except UnsupportedFormatError as exc:
            raise AppSessionError(str(exc)) from exc
        return {
            "format": fmt,
            "path": str(out_path),
            "bytes_written": result.bytes_written,
            "vertex_count": result.vertex_count,
            "triangle_count": result.triangle_count,
        }

    def export_manifest(
        self,
        project_id: str,
        formats: list[str],
        *,
        out_dir: str | None = None,
        crs: str | None = None,
        compute_checksums: bool = True,
    ) -> dict[str, Any]:
        """Kullanıcı talebi (madde 3): "somut manifest scripti". Verilen
        `formats` listesindeki her formatı `export_scene()` ile gerçekten
        diske yazar, ardından hepsini tek bir `manifest.json`'da
        kataloglar (SHA-256 checksum + bina meta verisi dahil).

        Dönüş: `{"manifest_path": ..., "manifest": <dict>}`. `manifest.json`
        export köküne (`out_dir` verilmişse oraya, verilmezse
        `<proje>/exports/`'e) yazılır.
        """
        if not formats:
            raise AppSessionError("formats listesi boş olamaz.")
        entries = list(self._buildings.get(project_id, {}).values())
        if not entries:
            raise AppSessionError("Projede export edilecek bina yok.")

        export_results: list[dict[str, Any]] = []
        manifest_dir: Path | None = None
        for fmt in formats:
            result = self.export_scene(project_id, fmt, out_dir=out_dir)
            export_results.append(result)
            if manifest_dir is None:
                p = Path(result["path"])
                # export_scene her zaman export_root altına yazar; path'in
                # üst dizini export_root'tur (3dtiles çıktısı da export_root
                # altında bir alt dizin olarak yazılır) - .parent her iki
                # durumda da doğru export köküdür.
                manifest_dir = p.parent

        manifest = ManifestBuilder.build(
            project_id,
            entries,
            export_results,
            crs=crs,
            compute_checksums=compute_checksums,
        )
        manifest_path = manifest_dir / "manifest.json"
        manifest.write(manifest_path)
        return {"manifest_path": str(manifest_path), "manifest": manifest.to_dict()}

    def history(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        handle = self._handle(project_id)
        return [
            {"ts": ts, "op": op, "key": key, "kind": kind}
            for ts, op, key, kind in handle.db.iter_history(limit=limit)
        ]

    # -- Web arayüzü köprüsü: Güneş Simülasyonu (analysis_engine) --------

    def sun_analysis(
        self,
        project_id: str,
        *,
        lat: float,
        lon: float,
        year: int | None = None,
        roof_tilt_deg: float = 0.0,
        roof_azimuth_deg: float = 180.0,
    ) -> dict[str, Any]:
        """`analysis_engine.sun_simulation`'ı bir konum için çalıştırır ve
        web arayüzünde tablo olarak gösterilebilecek düz bir JSON döner.
        `project_id` şu an yalnızca projenin açık olduğunu doğrulamak için
        kullanılır (ileride proje başına konum saklamak için genişletilebilir).
        """
        self._handle(project_id)  # proje açık mı doğrula (AppSessionError fırlatabilir)
        try:
            location = GeoPoint(lat=float(lat), lon=float(lon))
        except (TypeError, ValueError) as exc:
            raise AppSessionError(f"geçersiz konum: {exc}") from exc

        yr = year or datetime.now(tz=timezone.utc).year
        seasons = SeasonalSunPath.sample_seasons(location, year=yr, hour_utc=12)

        rows: list[dict[str, Any]] = []
        for season_key, pos in seasons.items():
            flat = RoofIrradiance.compute(pos, roof_tilt_deg=0.0)
            tilted = RoofIrradiance.compute(
                pos,
                roof_tilt_deg=roof_tilt_deg,
                roof_azimuth_deg=roof_azimuth_deg,
            )
            rows.append(
                {
                    "season": season_key,
                    "elevation_deg": round(pos.elevation_deg, 1),
                    "azimuth_deg": round(pos.azimuth_deg, 1),
                    "is_daylight": pos.is_daylight,
                    "flat_roof_w_m2": round(flat.watts_per_m2, 1),
                    "tilted_roof_w_m2": round(tilted.watts_per_m2, 1),
                }
            )

        return {
            "lat": location.lat,
            "lon": location.lon,
            "year": yr,
            "roof_tilt_deg": roof_tilt_deg,
            "roof_azimuth_deg": roof_azimuth_deg,
            "rows": rows,
        }

    # -- Web arayüzü köprüsü: Güneş Paneli Fizibilitesi (sun_simulation) -- #

    def solar_feasibility(
        self,
        project_id: str,
        *,
        lat: float,
        lon: float,
        key: str | None = None,
        roof_area_m2: float | None = None,
        roof_tilt_deg: float | None = None,
        roof_azimuth_deg: float = 180.0,
        panel_efficiency: float = 0.20,
        performance_ratio: float = 0.80,
        usable_roof_fraction: float = 0.70,
        year: int | None = None,
    ) -> dict[str, Any]:
        """Roadmap #3: çatı güneş paneli fizibilitesi."""
        self._handle(project_id)
        try:
            location = GeoPoint(lat=float(lat), lon=float(lon))
        except (TypeError, ValueError) as exc:
            raise AppSessionError(f"geçersiz konum: {exc}") from exc

        area_source = "elle girildi"
        if roof_area_m2 is None:
            if key is not None:
                entry = self._entry(project_id, key)
                roof_area_m2 = entry.building.footprint.area_m2
                area_source = f"bina ayak izinden ('{key}')"
            else:
                raise AppSessionError(
                    "roof_area_m2 verilmedi ve bina seçilmedi (key) — çatı alanı bilinmiyor."
                )
        if roof_area_m2 <= 0:
            raise AppSessionError("roof_area_m2 pozitif olmalı.")

        tilt = roof_tilt_deg if roof_tilt_deg is not None else round(abs(location.lat), 1)
        yr = year or datetime.now(tz=timezone.utc).year

        seasons = SeasonalSunPath.SEASON_REPRESENTATIVE_MONTHS_DAYS
        season_rows: list[dict[str, Any]] = []
        daily_tilted_total = 0.0
        daily_flat_total = 0.0
        for season_key, (month, day) in seasons.items():
            sample_date = datetime(yr, month, day, tzinfo=timezone.utc)
            tilted_kwh = RoofIrradiance.daily_energy_kwh_per_m2(
                location,
                sample_date,
                roof_tilt_deg=tilt,
                roof_azimuth_deg=roof_azimuth_deg,
            )
            flat_kwh = RoofIrradiance.daily_energy_kwh_per_m2(
                location,
                sample_date,
                roof_tilt_deg=0.0,
                roof_azimuth_deg=roof_azimuth_deg,
            )
            daily_tilted_total += tilted_kwh
            daily_flat_total += flat_kwh
            season_rows.append(
                {
                    "season": season_key,
                    "date": sample_date.date().isoformat(),
                    "daily_kwh_per_m2_tilted": round(tilted_kwh, 2),
                    "daily_kwh_per_m2_flat": round(flat_kwh, 2),
                }
            )

        avg_daily_tilted = daily_tilted_total / len(seasons)
        avg_daily_flat = daily_flat_total / len(seasons)
        annual_kwh_per_m2_tilted = avg_daily_tilted * 365.0
        annual_kwh_per_m2_flat = avg_daily_flat * 365.0

        usable_area_m2 = roof_area_m2 * usable_roof_fraction
        annual_production_kwh = (
            annual_kwh_per_m2_tilted * usable_area_m2 * panel_efficiency * performance_ratio
        )
        estimated_system_kwp = usable_area_m2 * panel_efficiency

        return {
            "lat": location.lat,
            "lon": location.lon,
            "key": key,
            "roof_area_m2": round(roof_area_m2, 1),
            "roof_area_source": area_source,
            "usable_roof_fraction": usable_roof_fraction,
            "usable_area_m2": round(usable_area_m2, 1),
            "roof_tilt_deg": tilt,
            "roof_azimuth_deg": roof_azimuth_deg,
            "tilt_is_auto": roof_tilt_deg is None,
            "panel_efficiency": panel_efficiency,
            "performance_ratio": performance_ratio,
            "seasons": season_rows,
            "annual_kwh_per_m2_tilted": round(annual_kwh_per_m2_tilted, 1),
            "annual_kwh_per_m2_flat": round(annual_kwh_per_m2_flat, 1),
            "tilt_gain_pct": round(
                100.0
                * (annual_kwh_per_m2_tilted - annual_kwh_per_m2_flat)
                / max(annual_kwh_per_m2_flat, 1e-6),
                1,
            ),
            "estimated_system_kwp": round(estimated_system_kwp, 1),
            "estimated_annual_production_kwh": round(annual_production_kwh, 0),
            "disclaimer": (
                "Basitleştirilmiş açık-gökyüzü (clear-sky) irradiance modeli kullanılır — "
                "gerçek bulutluluk/hava durumu, gölgeleme (komşu bina/ağaç) ve panel "
                "sıcaklık kaybı hesaba katılmaz. GÖSTERGE niteliğindedir, resmi bir "
                "fizibilite/mühendislik raporunun yerini tutmaz."
            ),
        }

    # -- Web arayüzü köprüsü: Görünürlük/Gölge Analizi (analysis_engine) --

    def _occluder_meshes(self, project_id: str) -> list[Mesh3D]:
        """Projedeki tüm binaların mesh'lerini (görüş/gölge engelleyicisi
        olarak) döner."""
        entries = self._buildings.get(project_id, {})
        return [entry.building.full_mesh(include_interior=True) for entry in entries.values()]

    def visibility_analysis(
        self,
        project_id: str,
        *,
        observer_x: float,
        observer_y: float,
        observer_z: float = 1.7,
        scan_radius: float = 100.0,
        angle_step_deg: float = 10.0,
        lat: float | None = None,
        lon: float | None = None,
        when_iso: str | None = None,
    ) -> dict[str, Any]:
        """`analysis_engine.visibility`'yi bir gözlemci noktası için
        çalıştırır: 360 derece kör-nokta taraması (`BlindSpotAnalysis`) +
        (lat/lon verilirse) o noktanın verilen zamanda gölgede olup
        olmadığı (`ShadowAnalysis`). Web arayüzünde tablo/özet olarak
        gösterilebilecek düz bir JSON döner."""
        self._handle(project_id)  # proje açık mı doğrula
        occluders = self._occluder_meshes(project_id)
        observer = (float(observer_x), float(observer_y), float(observer_z))

        blind_spots = BlindSpotAnalysis.scan(
            observer,
            occluders,
            scan_radius=scan_radius,
            angle_step_deg=angle_step_deg,
        )
        total_directions = max(1, int(360 / angle_step_deg))
        blocked_ratio = len(blind_spots) / total_directions

        result: dict[str, Any] = {
            "observer": {"x": observer[0], "y": observer[1], "z": observer[2]},
            "scan_radius_m": scan_radius,
            "angle_step_deg": angle_step_deg,
            "blind_spot_count": len(blind_spots),
            "total_directions": total_directions,
            "blocked_ratio": round(blocked_ratio, 3),
            "blind_spots": [
                {
                    "direction_deg": bs.direction_deg,
                    "max_visible_distance_m": round(bs.max_visible_distance, 1),
                }
                for bs in blind_spots
            ],
        }

        if lat is not None and lon is not None:
            try:
                location = GeoPoint(lat=float(lat), lon=float(lon))
            except (TypeError, ValueError) as exc:
                raise AppSessionError(f"geçersiz konum: {exc}") from exc
            when = datetime.now(tz=timezone.utc)
            if when_iso:
                try:
                    when = datetime.fromisoformat(when_iso)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                except ValueError as exc:
                    raise AppSessionError(f"geçersiz tarih/saat: {exc}") from exc

            shadow_now = ShadowAnalysis.evaluate(observer, location, when, occluders)
            daily_hours = ShadowAnalysis.daily_shadow_hours(observer, location, when, occluders)
            result["shadow"] = {
                "lat": location.lat,
                "lon": location.lon,
                "when_utc": when.isoformat(),
                "in_shadow_now": shadow_now.in_shadow,
                "sun_elevation_deg": round(shadow_now.sun_elevation_deg, 1),
                "daily_shadow_hours": round(daily_hours, 2),
            }

        return result

    # -- Web arayüzü köprüsü: Bitki Örtüsü (vegetation) -------------------

    @staticmethod
    def _translate_mesh(mesh: Mesh3D, dx: float, dy: float, dz: float) -> Mesh3D:
        """`Mesh3D`'yi (dx, dy, dz) kadar öteler - yeni bir kopya döner
        (vegetation/environment gibi kaynağı sabit origin'de üretilen
        mesh'leri sahnedeki konumuna taşımak için)."""
        from ..mesh_engine import Vertex3D

        new_vertices = [
            Vertex3D(
                x=v.x + dx, y=v.y + dy, z=v.z + dz, normal=v.normal, tangent=v.tangent, uv=v.uv
            )
            for v in mesh.vertices
        ]
        return Mesh3D(vertices=new_vertices, triangles=list(mesh.triangles), name=mesh.name)

    def vegetation_scatter(
        self,
        project_id: str,
        *,
        target_count: int = 40,
        seed: int = 0,
        species: str = "generic",
        slope_penalty: float = 3.0,
        max_slope: float = 0.9,
    ) -> dict[str, Any]:
        """`vegetation.scatter.VegetationScatterer`'ı projenin arazisi
        (terrain) üzerinde çalıştırır; sonuç oturumda saklanır ve
        `scene_json()` bir sonraki çağrıda bu ağaçları sahneye ekler."""
        self._handle(project_id)
        terrain_entry = self._terrains.get(project_id)
        if terrain_entry is None:
            raise AppSessionError("bitki örtüsü için önce arazi (terrain) oluşturulmalı.")
        try:
            species_enum = TreeSpecies(species)
        except ValueError as exc:
            raise AppSessionError(f"geçersiz tür: {species}") from exc

        instances = VegetationScatterer.scatter(
            terrain_entry.grid,
            target_count=target_count,
            seed=seed,
            species=species_enum,
            slope_penalty=slope_penalty,
            max_slope=max_slope,
        )
        self._vegetation[project_id] = instances
        return {
            "count": len(instances),
            "species": species_enum.value,
            "instances": [
                {
                    "x": round(i.x, 2),
                    "y": round(i.y, 2),
                    "z": round(i.z, 2),
                    "height": round(i.height, 2),
                    "canopy_radius": round(i.canopy_radius, 2),
                }
                for i in instances
            ],
        }

    def vegetation_clear(self, project_id: str) -> dict[str, Any]:
        self._handle(project_id)
        self._vegetation.pop(project_id, None)
        return {"cleared": True}

    # -- Web arayüzü genişletmesi: Fikir 11 — Yeşil Alan/Ağaçlandırma -----
    # ("bu bölgeye hangi ağaç türü uyar, ne kadar gölge/CO2 katkısı")

    _SPECIES_CO2_KG_YR = {
        TreeSpecies.CONIFER: 10.0,  # yavaş büyüyen, iğne yapraklı - yıl boyu yeşil
        TreeSpecies.DECIDUOUS: 21.0,  # hızlı büyüyen geniş taçlı - ortalama olgun ağaç tahmini
        TreeSpecies.SHRUB: 3.0,
        TreeSpecies.GENERIC: 15.0,
    }

    def vegetation_species_recommendation(
        self,
        project_id: str,
        *,
        lat: float,
        lon: float,
        tree_count: int | None = None,
        days_back: int = 10,
    ) -> dict[str, Any]:
        """`climate_data.OpenMeteoClient` ile son `days_back` günün saatlik
        sıcaklık/bulutluluk verisini çeker, iklime uygun `TreeSpecies`
        önerir, ve (varsa `vegetation_scatter` ile önceden üretilmiş ağaç
        sayısı, yoksa `tree_count`/varsayılan) için kaba bir gölge alanı +
        yıllık CO2 tutma tahmini döner."""
        self._handle(project_id)
        try:
            location = GeoPoint(lat=float(lat), lon=float(lon))
        except (TypeError, ValueError) as exc:
            raise AppSessionError(f"geçersiz konum: {exc}") from exc

        client = OpenMeteoClient()
        today = datetime.now(tz=timezone.utc).date()
        start = today - timedelta(days=max(1, days_back))
        end = today - timedelta(days=1)
        try:
            samples = client.fetch_hourly(
                latitude=location.lat, longitude=location.lon, start_date=start, end_date=end
            )
        except ClimateError as exc:
            raise AppSessionError(f"iklim verisi alınamadı: {exc}") from exc

        temps = [s.temperature_c for s in samples if s.temperature_c is not None]
        clouds = [s.cloud_cover_pct for s in samples if s.cloud_cover_pct is not None]
        if not temps:
            raise AppSessionError("iklim servisinden sıcaklık verisi alınamadı.")
        avg_temp = sum(temps) / len(temps)
        min_temp = min(temps)
        max_temp = max(temps)
        avg_cloud = (sum(clouds) / len(clouds)) if clouds else 50.0

        if min_temp < -5.0:
            species = TreeSpecies.CONIFER
            rationale = "kışlar soğuk (min. sıcaklık < -5°C) — iğne yapraklı, dona dayanıklı türler önerilir."
        elif avg_temp >= 22.0 and avg_cloud < 40.0:
            species = TreeSpecies.SHRUB
            rationale = "ortalama sıcaklık yüksek ve az bulutlu (kurak/sıcak eğilim) — düşük su ihtiyacı olan çalılık tercih edilir."
        elif avg_temp >= 8.0:
            species = TreeSpecies.DECIDUOUS
            rationale = "ılıman iklim (ortalama sıcaklık 8-22°C) — geniş taçlı yaprak döken türler hem gölge hem mevsimsel çeşitlilik sağlar."
        else:
            species = TreeSpecies.CONIFER
            rationale = (
                "serin iklim (ortalama sıcaklık < 8°C) — iğne yapraklı türler daha dayanıklıdır."
            )

        existing = self._vegetation.get(project_id)
        count = len(existing) if existing else int(tree_count or 50)
        if existing:
            total_shade_m2 = sum(math.pi * (i.canopy_radius**2) for i in existing)
        else:
            canopy_r = 1.8  # (1.2-2.4 m tipik taç yarıçapı aralığının ortası)
            total_shade_m2 = count * math.pi * (canopy_r**2)
        co2_per_tree = self._SPECIES_CO2_KG_YR.get(species, 15.0)
        annual_co2_kg = count * co2_per_tree

        return {
            "lat": location.lat,
            "lon": location.lon,
            "climate_window_days": days_back,
            "avg_temp_c": round(avg_temp, 1),
            "min_temp_c": round(min_temp, 1),
            "max_temp_c": round(max_temp, 1),
            "avg_cloud_cover_pct": round(avg_cloud, 1),
            "recommended_species": species.value,
            "rationale": rationale,
            "tree_count_basis": (
                "mevcut sahne (vegetation_scatter)" if existing else "varsayılan/istenen sayı"
            ),
            "tree_count": count,
            "estimated_total_shade_m2": round(total_shade_m2, 1),
            "estimated_annual_co2_kg": round(annual_co2_kg, 1),
            "co2_kg_per_tree_per_year": co2_per_tree,
            "disclaimer": (
                "Kurala-dayalı basit iklim eşiği + tür-başına ortalama CO2/gölge "
                "sabitleri kullanılır (gerçek tür kataloğu/toprak analizi değil). "
                "GÖSTERGE niteliğindedir, peyzaj mimarlığı danışmanlığının yerini tutmaz."
            ),
        }

    # -- Web arayüzü köprüsü: Feature Survey (saha ölçümü) ----------------

    def feature_survey_import_penzd(
        self,
        project_id: str,
        *,
        csv_text: str,
        has_header: bool = True,
        instrument: str = "total_station",
    ) -> dict[str, Any]:
        """PENZD formatındaki yapıştırılmış CSV metnini içe aktarır ve
        oturumda proje başına saklar (sonraki `feature_survey_summary`/
        `feature_survey_geojson` çağrılarının okuduğu kaynak)."""
        self._handle(project_id)
        if not csv_text or not csv_text.strip():
            raise AppSessionError("PENZD CSV metni boş olamaz.")
        try:
            session = import_penzd_csv_text(
                csv_text, session_name=project_id, has_header=has_header, instrument=instrument
            )
        except PENZDImportError as exc:
            raise AppSessionError(f"PENZD içe aktarma hatası: {exc}") from exc
        if not session.points:
            raise AppSessionError("CSV içe aktarıldı ama hiç geçerli nokta bulunamadı.")
        self._feature_surveys[project_id] = session
        return {
            "imported_points": len(session.points),
            "summary": session.summary(),
        }

    def feature_survey_summary(self, project_id: str) -> dict[str, Any]:
        self._handle(project_id)
        session = self._feature_surveys.get(project_id)
        if session is None:
            raise AppSessionError("bu proje için içe aktarılmış bir feature survey oturumu yok.")
        return {
            "name": session.name,
            "crs": session.crs,
            "point_count": len(session.points),
            "by_code": session.summary(),
            "string_count": len(session.strings()),
        }

    def feature_survey_geojson(
        self,
        project_id: str,
        *,
        origin_lat: float | None = None,
        origin_lon: float | None = None,
        origin_elevation: float = 0.0,
    ) -> dict[str, Any]:
        """Saha oturumunu GeoJSON'a (FeatureCollection) çevirir.

        `origin_lat`/`origin_lon` verilirse, yerel/projeksiyonlu metre
        koordinatları bu WGS84 referans noktasına göre lat/lon'a çevrilir
        (`session_to_wgs84_geofeatures`) — böylece sonuç doğrudan
        Leaflet/OSM haritasına çizilebilir. Verilmezse ham yerel
        koordinatlarla (`session_to_geofeatures`) döner (yalnızca
        önizleme/analiz amaçlı, harita katmanına uygun değildir).
        """
        self._handle(project_id)
        session = self._feature_surveys.get(project_id)
        if session is None:
            raise AppSessionError("bu proje için içe aktarılmış bir feature survey oturumu yok.")

        if origin_lat is not None and origin_lon is not None:
            try:
                origin = _SurveyGeoPoint(
                    lat=float(origin_lat), lon=float(origin_lon), elevation=float(origin_elevation)
                )
            except (TypeError, ValueError) as exc:
                raise AppSessionError(f"geçersiz orijin: {exc}") from exc
            collection = session_to_wgs84_geofeatures(session, origin)
            projected_to_wgs84 = True
        else:
            collection = session_to_geofeatures(session)
            projected_to_wgs84 = False

        return {
            "projected_to_wgs84": projected_to_wgs84,
            "geojson": {
                "type": "FeatureCollection",
                "crs": collection.crs,
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": f.geometry_type, "coordinates": f.coordinates},
                        "properties": f.properties,
                    }
                    for f in collection.features
                ],
            },
        }

    def feature_survey_webodm_test_connection(
        self, *, base_url: str, token: str | None = None
    ) -> dict[str, Any]:
        """Web arayüzündeki 'bağlantıyı test et' butonu için: verilen WebODM
        sunucusuna ulaşılabiliyor mu diye kontrol eder — hiçbir görev
        göndermez, yalnızca `ping()`."""
        if not base_url or not base_url.strip():
            raise AppSessionError("WebODM sunucu adresi boş olamaz.")
        pipeline = WebODMPipeline(base_url=base_url.strip(), token=token, timeout=5.0)
        reachable = pipeline.ping()
        return {"base_url": pipeline.base_url, "reachable": reachable}

    _IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")

    #: Dosya-türü doğrulaması sadece uzantıya güvenmez (bir istemci
    #: kötü niyetli/yanlış bir dosyayı ".jpg" olarak yeniden adlandırabilir)
    #: — ilk birkaç byte'ın (magic number) beklenen imzayla eşleşip
    #: eşleşmediği de kontrol edilir.
    _IMAGE_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
        ".jpg": (b"\xff\xd8\xff",),
        ".jpeg": (b"\xff\xd8\xff",),
        ".png": (b"\x89PNG\r\n\x1a\n",),
        ".tif": (b"II*\x00", b"MM\x00*"),
        ".tiff": (b"II*\x00", b"MM\x00*"),
    }

    def _feature_survey_upload_dir(self, project_id: str) -> Path:
        """Bir projenin yüklenen fotoğraflarının saklandığı sabit,
        deterministik dizin — `.hproj` dosyasının yanında (proje diskteki
        her yerde olsa da) `<proje_adı>_survey_uploads/` olarak durur."""
        handle = self._handle(project_id)
        db_path = Path(handle.path)
        upload_dir = db_path.parent / f"{db_path.stem}_survey_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    def feature_survey_upload_photos(
        self,
        project_id: str,
        *,
        files: list[tuple[str, bytes]],
    ) -> dict[str, Any]:
        """Roadmap V7 - tarayıcıdan sunucuya gerçek `multipart/form-data`
        dosya yükleme. `app_shell/server.py`'deki `_dispatch_upload`
        tarafından, `app_shell/multipart.py` ile ayrıştırılmış dosya
        listesiyle çağrılır.

        Her dosya için: (1) dosya adı path-traversal'e karşı temizlenir
        (yalnızca `Path(name).name` kullanılır — `../../etc/passwd` gibi
        girdiler zararsızlaştırılır), (2) uzantı `_IMAGE_EXTENSIONS`
        kümesinde olmalı, (3) ilk byte'lar beklenen magic number ile
        eşleşmeli (uzantı sahteciliğine karşı). Doğrulamayı geçemeyen
        dosyalar sessizce atlanmaz — `AppSessionError` ile reddedilir,
        böylece istemci hangi dosyanın neden reddedildiğini görür.
        """
        if not files:
            raise AppSessionError("Yüklenecek dosya gönderilmedi.")

        upload_dir = self._feature_survey_upload_dir(project_id)
        saved: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []

        for raw_name, data in files:
            safe_name = Path(raw_name).name  # path traversal koruması
            if not safe_name:
                rejected.append({"filename": raw_name, "reason": "geçersiz dosya adı"})
                continue
            ext = Path(safe_name).suffix.lower()
            if ext not in self._IMAGE_EXTENSIONS:
                rejected.append(
                    {
                        "filename": safe_name,
                        "reason": f"desteklenmeyen uzantı {ext!r} "
                        f"(izin verilenler: {', '.join(self._IMAGE_EXTENSIONS)})",
                    }
                )
                continue
            if not data:
                rejected.append({"filename": safe_name, "reason": "boş dosya"})
                continue
            signatures = self._IMAGE_MAGIC_SIGNATURES.get(ext, ())
            if signatures and not any(data.startswith(sig) for sig in signatures):
                rejected.append(
                    {
                        "filename": safe_name,
                        "reason": "dosya içeriği uzantıyla uyuşmuyor (magic number doğrulaması başarısız)",
                    }
                )
                continue

            out_path = upload_dir / safe_name
            out_path.write_bytes(data)
            saved.append({"filename": safe_name, "bytes": len(data), "path": str(out_path)})

        if not saved and rejected:
            raise AppSessionError(
                "Hiçbir dosya kabul edilmedi: "
                + "; ".join(f"{r['filename']}: {r['reason']}" for r in rejected)
            )

        return {
            "project_id": project_id,
            "upload_dir": str(upload_dir),
            "saved_count": len(saved),
            "saved": saved,
            "rejected": rejected,
        }

    def feature_survey_webodm_submit(
        self,
        project_id: str,
        *,
        base_url: str,
        image_dir: str = "",
        token: str | None = None,
        task_name: str | None = None,
    ) -> dict[str, Any]:
        """Bir klasördeki fotoğrafları WebODM'e gönderir ve görevi başlatır.

        `image_dir` boş bırakılırsa (veya hiç verilmezse), önce
        `feature_survey_upload_photos` ile tarayıcıdan yüklenmiş fotoğrafların
        bulunduğu proje-özel yükleme dizini denenir (Roadmap V7 dosya yükleme
        fazı) — bu dizin de yoksa/boşsa, eskisi gibi (self-hosted WebODM
        kurulumu + saha kamerası/drone senkronizasyonuyla tutarlı biçimde)
        sunucunun erişebildiği bir yol açıkça verilmiş olmalıdır.
        """
        self._handle(project_id)
        if not base_url or not base_url.strip():
            raise AppSessionError("WebODM sunucu adresi boş olamaz.")
        directory = Path(image_dir) if image_dir else None
        if directory is None or not directory.is_dir():
            fallback = self._feature_survey_upload_dir(project_id)
            if fallback.is_dir() and any(fallback.iterdir()):
                directory = fallback
            else:
                raise AppSessionError(
                    f"fotoğraf klasörü bulunamadı: {image_dir!r} (ve tarayıcıdan "
                    f"yüklenmiş fotoğraf da yok — önce /feature-survey/uploads "
                    f"uç noktasına dosya gönderin veya sunucu-tarafı bir yol verin)"
                )
        images = sorted(
            p for p in directory.iterdir() if p.suffix.lower() in self._IMAGE_EXTENSIONS
        )
        if not images:
            raise AppSessionError(
                f"'{directory}' içinde desteklenen fotoğraf bulunamadı "
                f"({', '.join(self._IMAGE_EXTENSIONS)})."
            )
        name = task_name or f"feature-survey-{project_id}"
        pipeline = WebODMPipeline(base_url=base_url.strip(), token=token, timeout=15.0)
        try:
            webodm_project_id = pipeline._get_or_create_project(name)
            task_id = pipeline.submit_task(name, images)
        except ExternalToolNotAvailableError as exc:
            raise AppSessionError(str(exc)) from exc

        self._feature_survey_jobs[project_id] = {
            "base_url": pipeline.base_url,
            "webodm_project_id": webodm_project_id,
            "task_id": task_id,
            "image_count": len(images),
            "task_name": name,
        }
        return {
            "task_id": task_id,
            "webodm_project_id": webodm_project_id,
            "image_count": len(images),
            "task_name": name,
        }

    def feature_survey_webodm_status(self, project_id: str) -> dict[str, Any]:
        """Son gönderilen WebODM görevinin durumunu sorgular (`status.code`:
        10=sırada, 20=çalışıyor, 40=tamamlandı, 30/50=başarısız/iptal —
        WebODM API sabitleri)."""
        self._handle(project_id)
        job = self._feature_survey_jobs.get(project_id)
        if job is None:
            raise AppSessionError("bu proje için gönderilmiş bir WebODM görevi yok.")
        pipeline = WebODMPipeline(base_url=job["base_url"], timeout=15.0)
        try:
            status = pipeline.task_status(job["webodm_project_id"], job["task_id"])
        except ExternalToolNotAvailableError as exc:
            raise AppSessionError(str(exc)) from exc
        code = status.get("status", {}).get("code")
        code_labels = {
            10: "sırada",
            20: "işleniyor",
            40: "tamamlandı",
            30: "başarısız",
            50: "iptal edildi",
        }
        return {
            "task_id": job["task_id"],
            "code": code,
            "label": code_labels.get(code, f"bilinmeyen ({code})"),
            "progress": status.get("running_progress"),
            "raw": status,
        }

    def feature_survey_webodm_fetch(self, project_id: str, *, output_dir: str) -> dict[str, Any]:
        """Tamamlanmış WebODM görevinin sonucunu indirir (point cloud/mesh/
        orthomosaic) ve varsa üretilen `.las` nokta bulutunu mevcut
        `PointCloud` temsiline okuyup `terrain_engine`'in kullanabileceği
        şekilde oturuma kaydeder (bkz. `PointCloud.to_heightmap_grid`)."""
        self._handle(project_id)
        job = self._feature_survey_jobs.get(project_id)
        if job is None:
            raise AppSessionError("bu proje için gönderilmiş bir WebODM görevi yok.")
        if not output_dir or not output_dir.strip():
            raise AppSessionError("çıktı klasörü boş olamaz.")
        pipeline = WebODMPipeline(base_url=job["base_url"], timeout=30.0)
        try:
            result = pipeline.fetch_result(job["webodm_project_id"], job["task_id"], output_dir)
        except ExternalToolNotAvailableError as exc:
            raise AppSessionError(str(exc)) from exc

        point_cloud_imported = False
        point_count = 0
        if result.point_cloud_path is not None and result.point_cloud_path.suffix.lower() == ".las":
            try:
                cloud = photogrammetry_result_to_point_cloud(result)
                point_count = len(cloud.points)
                point_cloud_imported = True
            except Exception:  # noqa: BLE001 - opsiyonel zenginleştirme, akışı bozmasın
                point_cloud_imported = False

        return {
            "engine": result.engine,
            "point_cloud_path": str(result.point_cloud_path) if result.point_cloud_path else None,
            "mesh_path": str(result.mesh_path) if result.mesh_path else None,
            "orthomosaic_path": str(result.orthomosaic_path) if result.orthomosaic_path else None,
            "point_cloud_imported": point_cloud_imported,
            "point_count": point_count,
        }

    # -- ROADMAP_V6 FAZ S6: uçtan uca orkestrasyon + kalıcı kayıt --------
    #
    # Bu iki metod, roadmap'in "Web arayüzü: saha projesi yükleme, adım
    # adım işlem geçmişi (audit trail)" maddesini tamamlar. S1'in çıktısı
    # olan `self._feature_surveys[project_id]` (zaten yukarıdaki
    # `feature_survey_import_penzd` ile doldurulmuş oturum) buradan S4
    # (vektörleştirme) + S5 (QC raporu, varsa kontrol noktası/kapatma
    # girdisiyle) + S3 (varsa nokta bulutu) fazlarını `orchestration.
    # pipeline.run_field_survey_pipeline` ile tek çağrıda çalıştırır ve
    # sonucu `.hproj` dosyasına (`persistence_bridge.save_survey_result`)
    # kalıcı olarak yazar — yeni bir şema icat edilmez, var olan genel
    # `objects`/`history` tablosu kullanılır (bkz. `persistence_bridge.py`).

    def feature_survey_run_orchestration(
        self,
        project_id: str,
        *,
        checkpoint_comparisons: list[dict[str, float]] | None = None,
        checkpoint_tolerance_horizontal_m: float | None = None,
        checkpoint_tolerance_vertical_m: float | None = None,
        checkpoint_standard_reference: str | None = None,
        angular_closure: dict[str, float] | None = None,
        linear_closure: dict[str, float] | None = None,
        angular_tolerance_gon: float | None = None,
        max_relative_precision: float | None = None,
        closure_standard_reference: str | None = None,
        pointcloud_points: list[list[float]] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """S1'in oturumdaki çıktısından (import edilmiş `FieldSurveySession`)
        başlayarak S3-S4-S5'i çalıştırır, denetlenebilir bir audit trail
        üretir ve (`persist=True` ise, varsayılan) proje dosyasına kaydeder.

        S2'nin çıktıları (kontrol noktası karşılaştırması, poligon kapatma)
        bu katmanda gerçek hesap YAPILMAZ -- roadmap'in kendi mimarisiyle
        tutarlı olarak `geodetic_engine` tarafından zaten hesaplanmış
        değerler burada yalnızca sözlükten dataclass'a taşınır; web
        istemcisi bu sayıları kendisi uydurmaz, `geodetic_engine`'in
        ilgili uç noktalarından (traverse/gnss_adjustment) alır.
        """
        handle = self._handle(project_id)
        session = self._feature_surveys.get(project_id)

        checkpoint_objs = None
        if checkpoint_comparisons is not None:
            from ..feature_survey.geodetic_engine.gnss_adjustment import ControlPointComparison

            try:
                checkpoint_objs = [
                    ControlPointComparison(
                        delta_easting_m=float(c["delta_easting_m"]),
                        delta_northing_m=float(c["delta_northing_m"]),
                        delta_elevation_m=float(c["delta_elevation_m"]),
                        rmse_2d_m=float(c["rmse_2d_m"]),
                        rmse_3d_m=float(c["rmse_3d_m"]),
                    )
                    for c in checkpoint_comparisons
                ]
            except (KeyError, TypeError, ValueError) as exc:
                raise AppSessionError(f"geçersiz checkpoint_comparisons: {exc}") from exc

        angular_obj = None
        linear_obj = None
        if angular_closure is not None or linear_closure is not None:
            from ..feature_survey.geodetic_engine.traverse import AngularClosure, LinearClosure

            try:
                if angular_closure is not None:
                    angular_obj = AngularClosure(
                        n_points=int(angular_closure["n_points"]),
                        measured_sum_gon=float(angular_closure["measured_sum_gon"]),
                        theoretical_sum_gon=float(angular_closure["theoretical_sum_gon"]),
                        closure_error_gon=float(angular_closure["closure_error_gon"]),
                    )
                if linear_closure is not None:
                    linear_obj = LinearClosure(
                        delta_easting_sum_m=float(linear_closure["delta_easting_sum_m"]),
                        delta_northing_sum_m=float(linear_closure["delta_northing_sum_m"]),
                        closure_distance_m=float(linear_closure["closure_distance_m"]),
                        perimeter_m=float(linear_closure["perimeter_m"]),
                        relative_precision=float(linear_closure["relative_precision"]),
                    )
            except (KeyError, TypeError, ValueError) as exc:
                raise AppSessionError(f"geçersiz angular_closure/linear_closure: {exc}") from exc

        pointcloud_tuples = None
        if pointcloud_points is not None:
            try:
                pointcloud_tuples = [
                    (float(p[0]), float(p[1]), float(p[2])) for p in pointcloud_points
                ]
            except (IndexError, TypeError, ValueError) as exc:
                raise AppSessionError(f"geçersiz pointcloud_points: {exc}") from exc

        try:
            result: SurveyOrchestrationResult = run_field_survey_pipeline(
                project_name=handle.manifest.name,
                session=session,
                checkpoint_comparisons=checkpoint_objs,
                checkpoint_tolerance_horizontal_m=checkpoint_tolerance_horizontal_m,
                checkpoint_tolerance_vertical_m=checkpoint_tolerance_vertical_m,
                checkpoint_standard_reference=checkpoint_standard_reference,
                angular_closure=angular_obj,
                linear_closure=linear_obj,
                angular_tolerance_gon=angular_tolerance_gon,
                max_relative_precision=max_relative_precision,
                closure_standard_reference=closure_standard_reference,
                pointcloud_points=pointcloud_tuples,
            )
        except SurveyOrchestrationError as exc:
            raise AppSessionError(f"orkestrasyon hatası: {exc}") from exc

        payload = result.to_project_manifest_payload()
        if persist:
            try:
                save_survey_result(handle.db, result, project_slug=project_id)
            except PersistenceBridgeError as exc:
                raise AppSessionError(f"sonuç kaydedilemedi: {exc}") from exc
            handle.mark_dirty()
        return payload

    def feature_survey_orchestration_result(self, project_id: str) -> dict[str, Any]:
        """Daha önce `feature_survey_run_orchestration(persist=True)` ile
        `.hproj` dosyasına kaydedilmiş son orkestrasyon sonucunu okur."""
        handle = self._handle(project_id)
        try:
            return load_survey_result_payload(handle.db, project_id)
        except PersistenceBridgeError as exc:
            raise AppSessionError(str(exc)) from exc

    # -- Web arayüzü köprüsü: Navigasyon/Yol Bulma (mobility.pathfinding) --

    def find_path(
        self,
        project_id: str,
        *,
        start_x: float,
        start_y: float,
        goal_x: float,
        goal_y: float,
        cell_size: float = 2.0,
        algorithm: str = "astar",
    ) -> dict[str, Any]:
        """`mobility.pathfinding`'i, projedeki bina ayak izlerini engel
        (blocked cell) sayarak kurulan bir ızgara `NavGraph` üzerinde
        çalıştırır. Başlangıç/hedef, en yakın ızgara düğümüne
        (snapping) yuvarlanır."""
        self._handle(project_id)
        entries = self._buildings.get(project_id, {})
        footprints = [entry.building.footprint.polygon for entry in entries.values()]

        xs = [start_x, goal_x] + [p.x for poly in footprints for p in poly.points]
        ys = [start_y, goal_y] + [p.y for poly in footprints for p in poly.points]
        margin = cell_size * 3
        min_x, max_x = min(xs) - margin, max(xs) + margin
        min_y, max_y = min(ys) - margin, max(ys) + margin
        width = max(2, int((max_x - min_x) / cell_size) + 1)
        height = max(2, int((max_y - min_y) / cell_size) + 1)

        blocked: set[tuple[int, int]] = set()
        for gy in range(height):
            for gx in range(width):
                cx = min_x + (gx + 0.5) * cell_size
                cy = min_y + (gy + 0.5) * cell_size
                point = Point2D(cx, cy)
                if any(GeometryEngine.point_in_polygon(point, poly) for poly in footprints):
                    blocked.add((gx, gy))

        graph = NavGraph.from_grid(
            width, height, cell_size=cell_size, blocked_cells=blocked, diagonal=True
        )

        def _to_grid(x: float, y: float) -> tuple[int, int]:
            gx = min(width - 1, max(0, round((x - min_x) / cell_size)))
            gy = min(height - 1, max(0, round((y - min_y) / cell_size)))
            return (gx, gy)

        def _nearest_open(node: tuple[int, int]) -> tuple[int, int] | None:
            if graph.has_node(node):
                return node
            for radius in range(1, max(width, height)):
                for dx in range(-radius, radius + 1):
                    for dy in range(-radius, radius + 1):
                        candidate = (node[0] + dx, node[1] + dy)
                        if graph.has_node(candidate):
                            return candidate
            return None

        start_node = _nearest_open(_to_grid(start_x, start_y))
        goal_node = _nearest_open(_to_grid(goal_x, goal_y))
        if start_node is None or goal_node is None:
            raise AppSessionError("başlangıç veya hedef için açık bir ızgara hücresi bulunamadı.")

        solver = Dijkstra if algorithm == "dijkstra" else AStar
        result = solver.find_path(graph, start_node, goal_node)

        path_points = [
            {"x": round(min_x + gx * cell_size, 2), "y": round(min_y + gy * cell_size, 2)}
            for gx, gy in result.path
        ]
        return {
            "found": result.found,
            "cost_m": round(result.cost, 2) if result.found else None,
            "expanded_nodes": result.expanded_nodes,
            "algorithm": "dijkstra" if algorithm == "dijkstra" else "astar",
            "grid": {
                "width": width,
                "height": height,
                "cell_size": cell_size,
                "origin_x": round(min_x, 2),
                "origin_y": round(min_y, 2),
            },
            "path": path_points,
        }

    # -- Web arayüzü genişletmesi: Fikir #9 — Yaya/Trafik Erişilebilirlik -
    # ("bu bölgeye yeni bir AVM/gelişme gelirse trafik nasıl etkilenir")

    def traffic_impact_scenario(
        self,
        project_id: str,
        *,
        lane_count: int = 2,
        free_flow_speed_kmh: float = 50.0,
        jam_density_veh_km_per_lane: float = 140.0,
        baseline_daily_trips: float = 8000.0,
        added_daily_trips: float = 0.0,
        peak_hour_factor: float = 0.09,
        pedestrian_count: int = 0,
        site_x: float = 0.0,
        site_y: float = 0.0,
        area_radius_m: float = 60.0,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """`mobility.traffic_simulation.GreenshieldsModel` ile önce/sonra
        (baseline / development eklendi) trafik yoğunluğu ve seviye-of-
        servis (LOS benzeri) karşılaştırması + `mobility.crowd_simulation`
        ile site girişi çevresindeki yaya yoğunluğu tahmini. Bu, "yeni bir
        AVM/bina gelirse trafik/yaya erişimi nasıl etkilenir" senaryo
        aracıdır — gerçek bir ulaşım etki analizinin (traffic impact
        assessment) yerini tutmaz, GÖSTERGE niteliğindedir."""
        self._handle(project_id)
        if lane_count < 1:
            raise AppSessionError("lane_count en az 1 olmalı.")
        model = GreenshieldsModel(
            free_flow_speed=free_flow_speed_kmh, jam_density=jam_density_veh_km_per_lane
        )
        capacity_per_lane = model.capacity()
        total_capacity_veh_h = capacity_per_lane * lane_count

        def _scenario(daily_trips: float) -> dict[str, Any]:
            peak_hour_veh = daily_trips * peak_hour_factor
            demand_per_lane = peak_hour_veh / lane_count
            # Greenshields: q = v_f*k*(1-k/kj)  ->  k çözümü (alt/uncongested kök)
            vf, kj = free_flow_speed_kmh, jam_density_veh_km_per_lane
            disc = max(0.0, vf**2 - 4.0 * (vf / kj) * demand_per_lane)
            if vf <= 0:
                density = 0.0
            else:
                density = (vf - math.sqrt(disc)) / (2.0 * vf / kj)
            oversaturated = demand_per_lane > capacity_per_lane
            speed = (
                model.speed_at_density(density) if not oversaturated else free_flow_speed_kmh * 0.15
            )
            v_c_ratio = (
                demand_per_lane / capacity_per_lane if capacity_per_lane > 0 else float("inf")
            )
            if v_c_ratio < 0.6:
                los = "A/B (akıcı)"
            elif v_c_ratio < 0.8:
                los = "C (kararlı)"
            elif v_c_ratio < 1.0:
                los = "D/E (yoğun, sınırda)"
            else:
                los = "F (tıkanık/kuyruklanma)"
            return {
                "daily_trips": round(daily_trips, 0),
                "peak_hour_veh": round(peak_hour_veh, 0),
                "demand_per_lane_veh_h": round(demand_per_lane, 0),
                "density_veh_km": round(density, 1),
                "speed_kmh": round(speed, 1),
                "v_c_ratio": round(v_c_ratio, 2),
                "level_of_service": los,
                "oversaturated": oversaturated,
            }

        before = _scenario(baseline_daily_trips)
        after = _scenario(baseline_daily_trips + added_daily_trips)

        pedestrian_result: dict[str, Any] | None = None
        if pedestrian_count > 0:
            center = Point2D(site_x, site_y)
            area_min = Point2D(site_x - area_radius_m, site_y - area_radius_m)
            area_max = Point2D(site_x + area_radius_m, site_y + area_radius_m)
            agents = spawn_random_agents(pedestrian_count, area_min, area_max, center, seed=seed)
            model_sf = SocialForceModel()
            for _ in range(50):
                model_sf.step(agents, obstacles=None, dt=0.2)
            heatmap = OccupancyHeatmap.compute(agents, cell_size=5.0)
            hottest = OccupancyHeatmap.max_density_cell(heatmap)
            pedestrian_result = {
                "pedestrian_count": pedestrian_count,
                "area_radius_m": area_radius_m,
                "hottest_cell_agent_count": hottest[1] if hottest else 0,
                "hottest_cell_grid": list(hottest[0]) if hottest else None,
                "avg_agents_per_5m_cell": round(pedestrian_count / max(1, len(heatmap)), 1),
            }

        return {
            "road": {
                "lane_count": lane_count,
                "free_flow_speed_kmh": free_flow_speed_kmh,
                "jam_density_veh_km_per_lane": jam_density_veh_km_per_lane,
                "capacity_per_lane_veh_h": round(capacity_per_lane, 0),
                "total_capacity_veh_h": round(total_capacity_veh_h, 0),
            },
            "before": before,
            "after": after,
            "delta": {
                "added_daily_trips": added_daily_trips,
                "v_c_ratio_increase": round(after["v_c_ratio"] - before["v_c_ratio"], 2),
                "speed_drop_kmh": round(before["speed_kmh"] - after["speed_kmh"], 1),
                "los_worsened": after["level_of_service"] != before["level_of_service"],
            },
            "pedestrian": pedestrian_result,
            "disclaimer": (
                "Greenshields temel akış modeli + basit sosyal-kuvvet yaya modeli kullanılır. "
                "Gerçek kavşak geometrisi, sinyalizasyon, dönüş hareketleri ve gerçek OD "
                "(origin-destination) talebi hesaba katılmaz. GÖSTERGE niteliğindedir, resmi bir "
                "ulaşım etki değerlendirmesinin (traffic impact assessment) yerini tutmaz."
            ),
        }

    # -- Web arayüzü genişletmesi: Fikir 10 — IoT Sensör + Dijital İkiz ---

    _IOT_COLOR_STOPS = [  # (sıcaklık °C, hex renk) - mavi(soğuk) -> kırmızı(sıcak)
        (14.0, "#3b82f6"),
        (19.0, "#22c55e"),
        (23.0, "#eab308"),
        (28.0, "#ef4444"),
    ]

    @classmethod
    def _temp_to_color(cls, temp_c: float) -> str:
        stops = cls._IOT_COLOR_STOPS
        if temp_c <= stops[0][0]:
            return stops[0][1]
        if temp_c >= stops[-1][0]:
            return stops[-1][1]
        return min(stops, key=lambda s: abs(s[0] - temp_c))[1]

    def connect_iot_bridge(
        self,
        project_id: str,
        key: str,
        *,
        host: str = "localhost",
        port: int = 1883,
        timeout_s: float = 5.0,
        topic_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Faz E12 — bu binayı GERÇEK bir MQTT broker'ına bağlar
        (`digital_twin.iot_bridge.MqttBridge`, opsiyonel `paho-mqtt`
        bağımlılığı gerektirir: `pip install harita[iot]`).

        Broker'a bağlanılamazsa (paket kurulu değil veya `connect()`
        başarısız olursa) `MqttBackendUnavailable` YUKARI FIRLATILIR —
        sessizce simülasyona düşülmez; çağıran (REST katmanı) bunu 503
        gibi bir hataya çevirmeli. Bağlantı BAŞARILI olduktan SONRA
        `iot_digital_twin_tick()` artık sahte veri üretmeyi bırakır ve bu
        binanın son gerçek sensör okumalarını döner (bkz. o metodun
        docstring'i).

        Beklenen topic şeması: `<topic_prefix>/floor/<i>/temp` ve
        `<topic_prefix>/floor/<i>/occ` (varsayılan prefix: `harita/<key>`),
        payload doğrudan sayı ya da `{"value": <sayı>}` olabilir (bkz.
        `SensorIotBinding._extract_value`).
        """
        entry = self._entry(project_id, key)
        twin = self._twins.setdefault(project_id, {}).get(key)
        if twin is None:
            twin = DigitalTwin(id=key)
            self._twins[project_id][key] = twin
        existing_ids = {s.sensor_id for s in twin.sensors}
        for floor_idx in range(len(entry.building.floors)):
            if f"{key}:f{floor_idx}:temp" not in existing_ids:
                twin.bind_sensor(
                    SensorBinding(
                        sensor_id=f"{key}:f{floor_idx}:temp",
                        sensor_type="temperature",
                        target_ref=f"floor:{floor_idx}",
                        unit="°C",
                    )
                )
            if f"{key}:f{floor_idx}:occ" not in existing_ids:
                twin.bind_sensor(
                    SensorBinding(
                        sensor_id=f"{key}:f{floor_idx}:occ",
                        sensor_type="occupancy",
                        target_ref=f"floor:{floor_idx}",
                        unit="ratio",
                    )
                )

        prefix = topic_prefix or f"harita/{key}"
        bus = TopicBus()
        bridge = MqttBridge(bus, client_id=f"harita-{project_id}-{key}")
        # connect() burada bilerek try/except İLE SARILMIYOR: paho-mqtt
        # kurulu değilse veya broker'a ulaşılamazsa MqttBackendUnavailable
        # doğrudan çağırana yükselsin (roadmap ilkesi: sessiz sahte-başarı
        # yok, bkz. iot_bridge.py modül docstring'i).
        bridge.connect(host=host, port=port, topics=[f"{prefix}/#"], timeout_s=timeout_s)

        bindings: list[SensorIotBinding] = []
        for floor_idx in range(len(entry.building.floors)):
            for suffix, sensor_id in (
                ("temp", f"{key}:f{floor_idx}:temp"),
                ("occ", f"{key}:f{floor_idx}:occ"),
            ):
                binding = SensorIotBinding(
                    bus, twin, sensor_id, f"{prefix}/floor/{floor_idx}/{suffix}"
                )
                binding.bind()
                bindings.append(binding)

        self._iot_bridges[(project_id, key)] = {
            "bus": bus,
            "bridge": bridge,
            "bindings": bindings,
            "host": host,
            "port": port,
            "prefix": prefix,
        }
        return {
            "connected": True,
            "host": host,
            "port": port,
            "topic_prefix": prefix,
            "sensor_bindings": len(bindings),
        }

    def disconnect_iot_bridge(self, project_id: str, key: str) -> bool:
        """`connect_iot_bridge` ile açılmış gerçek MQTT bağlantısını kapatır.
        Bağlantı yoksa `False` döner (hata fırlatmaz — idempotent)."""
        state = self._iot_bridges.pop((project_id, key), None)
        if state is None:
            return False
        for binding in state["bindings"]:
            binding.unbind()
        state["bridge"].disconnect()
        return True

    def iot_digital_twin_tick(
        self,
        project_id: str,
        key: str,
        *,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """`digital_twin.DigitalTwin` + `digital_twin.iot_bridge` fikrini
        REST üzerinden gösterir.

        İKİ MOD (dürüstçe ayrıştırılmış):
          - GERÇEK MOD: `connect_iot_bridge()` bu bina için başarıyla
            çağrılmışsa (bkz. `self._iot_bridges`), burada YENİ VERİ
            ÜRETİLMEZ — yalnızca `MqttBridge`'in arka planda gerçek
            broker'dan aldığı ve `SensorIotBinding` aracılığıyla twin'e
            zaten yazılmış olan SON GERÇEK OKUMALAR okunup döner.
          - SİMÜLASYON MODU (varsayılan, broker bağlı değilken): her kat
            için sinüzoidal + gürültülü sahte sensör okuması üretir. Bu
            mod açıkça `disclaimer` alanıyla etiketlenir.
        """
        entry = self._entry(project_id, key)
        twin = self._twins.setdefault(project_id, {}).get(key)
        if twin is None:
            twin = DigitalTwin(id=key)
            self._twins[project_id][key] = twin
            for floor_idx in range(len(entry.building.floors)):
                twin.bind_sensor(
                    SensorBinding(
                        sensor_id=f"{key}:f{floor_idx}:temp",
                        sensor_type="temperature",
                        target_ref=f"floor:{floor_idx}",
                        unit="°C",
                    )
                )
                twin.bind_sensor(
                    SensorBinding(
                        sensor_id=f"{key}:f{floor_idx}:occ",
                        sensor_type="occupancy",
                        target_ref=f"floor:{floor_idx}",
                        unit="ratio",
                    )
                )

        bridge_state = self._iot_bridges.get((project_id, key))
        is_live = bridge_state is not None and bridge_state["bridge"].connected

        floors_out: list[dict[str, Any]] = []
        if is_live:
            # -- GERÇEK MOD: sahnede yeni sayı ÜRETİLMİYOR, twin'in son
            # bilinen (broker'dan gelmiş) değerleri okunuyor. Henüz hiç
            # mesaj gelmediyse sensör None kalabilir — bunu da açıkça
            # yansıtıyoruz (uydurma varsayılan değer koymuyoruz).
            sensors_by_id = {s.sensor_id: s for s in twin.sensors}
            for floor_idx in range(len(entry.building.floors)):
                temp_sensor = sensors_by_id.get(f"{key}:f{floor_idx}:temp")
                occ_sensor = sensors_by_id.get(f"{key}:f{floor_idx}:occ")
                temp = temp_sensor.last_value if temp_sensor is not None else None
                occ = occ_sensor.last_value if occ_sensor is not None else None
                floors_out.append(
                    {
                        "floor_index": floor_idx,
                        "temperature_c": round(temp, 1) if temp is not None else None,
                        "occupancy_ratio": round(occ, 2) if occ is not None else None,
                        "color": self._temp_to_color(temp) if temp is not None else None,
                        "has_reading": temp is not None or occ is not None,
                    }
                )
            disclaimer = (
                f"GERÇEK VERİ: {bridge_state['host']}:{bridge_state['port']} adresindeki "
                f"MQTT broker'ından `{bridge_state['prefix']}/floor/<i>/{{temp,occ}}` "
                "konularına gelen son mesajlar (digital_twin.iot_bridge.MqttBridge)."
            )
        else:
            rng = random.Random(seed if seed is not None else time.time())
            t = time.time()
            for floor_idx in range(len(entry.building.floors)):
                base_temp = 21.0 + 2.0 * math.sin(t / 600.0 + floor_idx * 0.7)
                temp = base_temp + rng.uniform(-0.8, 0.8)
                occ = max(
                    0.0,
                    min(1.0, 0.5 + 0.4 * math.sin(t / 900.0 + floor_idx) + rng.uniform(-0.1, 0.1)),
                )
                twin.update_sensor(f"{key}:f{floor_idx}:temp", round(temp, 2))
                twin.update_sensor(f"{key}:f{floor_idx}:occ", round(occ, 3))
                floors_out.append(
                    {
                        "floor_index": floor_idx,
                        "temperature_c": round(temp, 1),
                        "occupancy_ratio": round(occ, 2),
                        "color": self._temp_to_color(temp),
                        "has_reading": True,
                    }
                )
            disclaimer = (
                "SİMÜLASYON: gerçek bir MQTT/IoT broker'ına bağlı değil — sinüzoidal + "
                "gürültülü sahte veri üretir. Gerçek veriye geçmek için önce "
                "`connect_iot_bridge(project_id, key, host=..., port=...)` çağırın "
                "(opsiyonel `paho-mqtt` bağımlılığı gerekir: `pip install harita[iot]`)."
            )

        return {
            "key": key,
            "twin_id": twin.id,
            "sensor_count": len(twin.sensors),
            "event_count": len(twin.history),
            "is_live": is_live,
            "floors": floors_out,
            "disclaimer": disclaimer,
        }

    # -- Web arayüzü genişletmesi: Fikir 12 — Gerçek Zamanlı Çoklu Kullanıcı

    def collab_get_state(self, project_id: str, key: str) -> dict[str, Any]:
        """`collaboration.crdt.CRDTBuildingState`'i döner (yoksa boş
        oluşturur). Web arayüzü kısa aralıklarla bunu polling ederek çoklu
        kullanıcı senkronizasyonunu simüle eder (gerçek WebSocket sunucusu
        `collaboration.ws_server.CollaborationWebSocketServer`'da mevcuttur;
        bu REST köprüsü, tam bir async WS sunucusu ayağa kaldırmadan aynı
        CRDT birleştirme garantisini -- son-yazan-kazanır + eşzamanlı
        kat ekleme/silme birleştirmesi -- sergiler)."""
        self._entry(project_id, key)  # bina var mı doğrula
        states = self._collab_states.setdefault(project_id, {})
        state = states.get(key)
        if state is None:
            state = CRDTBuildingState(building_key=key)
            states[key] = state
        return {
            "key": key,
            "fields": {name: reg.value for name, reg in state.fields.items()},
            "floors": sorted(state.floors),
        }

    def collab_edit(
        self,
        project_id: str,
        key: str,
        *,
        actor_id: str,
        field_name: str,
        value: Any,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Bir kullanıcının yaptığı alan değişikliğini `LWWRegister` ile
        yazar (eşzamanlı iki kullanıcı aynı alanı değiştirirse en yeni
        zaman damgası kazanır) ve güncel birleşik durumu döner."""
        self._entry(project_id, key)
        states = self._collab_states.setdefault(project_id, {})
        state = states.setdefault(key, CRDTBuildingState(building_key=key))
        state.set_field(field_name, value, timestamp or time.time(), actor_id)
        return self.collab_get_state(project_id, key)

    def collab_floor_op(
        self,
        project_id: str,
        key: str,
        *,
        actor_id: str,
        op: str,
        floor_id: str,
    ) -> dict[str, Any]:
        """`add_floor`/`remove_floor` — `ORSet` tabanlı, eşzamanlı
        ekleme/silme çakışmalarını "ekleme kazanır" (add-wins) semantiğiyle
        birleştirir."""
        self._entry(project_id, key)
        states = self._collab_states.setdefault(project_id, {})
        state = states.setdefault(key, CRDTBuildingState(building_key=key))
        if op == "add_floor":
            state.add_floor(floor_id, actor_id)
        elif op == "remove_floor":
            state.remove_floor(floor_id)
        else:
            raise AppSessionError(f"bilinmeyen collab floor operasyonu: {op!r}")
        return self.collab_get_state(project_id, key)

    # -- Web arayüzü genişletmesi: Fikir 14 — Otomatik Proje/Bina Raporu --
    # ("bu bina/mahalle için tek tuşla fizibilite/analiz raporu")

    def generate_building_report(
        self,
        project_id: str,
        key: str,
        *,
        lat: float | None = None,
        lon: float | None = None,
        fmt: str = "markdown",
        out_dir: str | None = None,
    ) -> dict[str, Any]:
        """`export.reports.ReportBuilder`'ı kullanarak, bu oturumda mevcut
        analiz köprülerinin (deprem riski, güneş fizibilitesi, enerji
        kabuğu, yapısal makûliyet) hepsini TEK bir raporda birleştirir.
        `fmt`: 'markdown' | 'pdf' | 'json'. `lat`/`lon` verilmezse
        konum-bağımlı bölümler (deprem, güneş) atlanır."""
        from ..export.reports import ReportBuilder, ReportSection

        entry = self._entry(project_id, key)
        sections: list[ReportSection] = [
            ReportSection(
                "Bina Özeti",
                summary={
                    "Bina anahtarı": key,
                    "Kat sayısı": len(entry.building.floors),
                    "Toplam yükseklik (m)": round(entry.building.total_height_m, 2),
                    "Taban alanı (m²)": round(entry.building.footprint.area_m2, 1),
                },
            ),
        ]

        if lat is not None and lon is not None:
            try:
                risk = self.hazard_building_risk(project_id, lat=lat, lon=lon, key=key)
                sections.append(
                    ReportSection(
                        "Deprem Riski (Fikir 1)",
                        summary={
                            "PGA (g)": risk.get("pga_g"),
                            "Risk indeksi (0-100)": risk.get("risk_index_0_100"),
                            "Risk seviyesi": risk.get("risk_level"),
                        },
                        notes=str(risk.get("disclaimer", "")),
                    )
                )
            except AppSessionError as exc:
                sections.append(
                    ReportSection("Deprem Riski (Fikir 1)", notes=f"Hesaplanamadı: {exc}")
                )

            try:
                solar = self.solar_feasibility(project_id, lat=lat, lon=lon, key=key)
                sections.append(
                    ReportSection(
                        "Güneş Paneli Fizibilitesi (Fikir 3)",
                        summary={
                            "Kullanılabilir çatı alanı (m²)": solar.get("usable_area_m2"),
                            "Tahmini sistem gücü (kWp)": solar.get("estimated_system_kwp"),
                            "Yıllık üretim (kWh)": solar.get("estimated_annual_production_kwh"),
                        },
                        notes=str(solar.get("disclaimer", "")),
                    )
                )
            except AppSessionError as exc:
                sections.append(
                    ReportSection(
                        "Güneş Paneli Fizibilitesi (Fikir 3)", notes=f"Hesaplanamadı: {exc}"
                    )
                )

        try:
            envelope = self.energy_envelope_audit(project_id, key)
            sections.append(
                ReportSection(
                    "Enerji Kabuğu Denetimi — TS 825 (Fikir 4)",
                    summary={
                        "Pencere/duvar oranı": envelope.get("window_wall_ratio"),
                        "Toplam ısı kaybı katsayısı (W/K)": envelope.get(
                            "total_heat_loss_coefficient_w_per_k"
                        ),
                        "TS 825'e uygun mu": "EVET" if envelope.get("is_compliant") else "HAYIR",
                    },
                )
            )
        except AppSessionError as exc:
            sections.append(
                ReportSection("Enerji Kabuğu Denetimi (Fikir 4)", notes=f"Hesaplanamadı: {exc}")
            )

        try:
            structural = self.validate_structure(project_id, key)
            sections.append(
                ReportSection(
                    "Yapısal Makûliyet Denetimi (Fikir 7)",
                    summary={
                        "Fiziksel olarak makûl mü": "EVET"
                        if structural["is_plausible"]
                        else "HAYIR",
                        "Narinlik oranı": structural["slenderness_ratio"],
                        "En büyük konsol (m)": structural["max_cantilever_m"],
                    },
                    table_headers=["kod", "önem", "mesaj"],
                    table_rows=[
                        {"kod": i["code"], "önem": i["severity"], "mesaj": i["message"]}
                        for i in structural["issues"]
                    ],
                )
            )
        except AppSessionError as exc:
            sections.append(
                ReportSection("Yapısal Makûliyet Denetimi (Fikir 7)", notes=f"Hesaplanamadı: {exc}")
            )

        builder = ReportBuilder(title=f"Proje Raporu — {key}", sections=sections)

        handle = self._handle(project_id)
        allowed_root = Path(handle.path).resolve().parent
        export_root = (
            (allowed_root / out_dir) if out_dir else (allowed_root / "exports" / "reports")
        )
        export_root.mkdir(parents=True, exist_ok=True)

        fmt = fmt.lower()
        if fmt == "pdf":
            out_path = export_root / f"{key}_rapor.pdf"
            result = builder.export_pdf(str(out_path))
        elif fmt == "json":
            out_path = export_root / f"{key}_rapor.json"
            result = builder.export_json(str(out_path))
        else:
            fmt = "markdown"
            out_path = export_root / f"{key}_rapor.md"
            result = builder.export_markdown(str(out_path))

        return {
            "key": key,
            "format": fmt,
            "path": str(out_path),
            "bytes_written": result.bytes_written,
            "section_count": len(sections),
        }

    # -- Web arayüzü köprüsü: Fizik / Deprem-Sarsıntı Stabilite Testi -----

    def physics_tower_test(
        self,
        project_id: str,
        *,
        num_blocks: int = 4,
        peak_acceleration_g: float = 0.3,
        frequency_hz: float = 1.5,
        duration_s: float = 6.0,
    ) -> dict[str, Any]:
        """`physics.TowerStabilityScenario`'yu çalıştırır: artan taban
        ivmesi altında bir blok kulesinin devrilip devrilmediğini test
        eder (basitleştirilmiş, tam-3D olmayan sismik yaklaşım - bkz.
        `physics/__init__.py` docstring'i)."""
        self._handle(project_id)  # proje açık mı doğrula
        scenario = TowerStabilityScenario(num_blocks=num_blocks)
        world = scenario.build_world(peak_acceleration_g, frequency_hz)
        world.run(duration_s)

        blocks = [b for b in world.bodies if not b.is_static]
        return {
            "num_blocks": num_blocks,
            "peak_acceleration_g": peak_acceleration_g,
            "frequency_hz": frequency_hz,
            "duration_s": duration_s,
            "any_toppled": any(b.is_toppled() for b in blocks),
            "blocks": [
                {
                    "body_id": b.body_id,
                    "position": [round(c, 3) for c in b.position],
                    "orientation_deg": round(math.degrees(b.orientation), 2),
                    "toppled": b.is_toppled(),
                }
                for b in blocks
            ],
        }

    # -- Web arayüzü genişletmesi: Roadmap V10 / Faz 3.A + 3.B ------------
    #
    # Bu metoda kadar Faz 3.A (`physics.building_shake`) ve Faz 3.B
    # (`physics.building_shake_engineering`) veri/mantık katmanları
    # tamamlanmıştı ama HİÇBİR YERDEN dashboard'a bağlı değildi (bkz.
    # ROADMAP_V10_STATUS.md dürüstlük notu). Bu metot, seçili bir binanın
    # gerçek kat sayısını/tipini kullanarak bir zaman-serisi sallanma
    # simülasyonu üretir ve web arayüzünün 3D viewer'ının doğrudan
    # oynatabileceği örneklenmiş bir çerçeve dizisi döner.
    def building_shake_simulate(
        self,
        project_id: str,
        key: str,
        *,
        mode: str = "standard",  # "standard" (3.A) | "engineering" (3.B)
        peak_acceleration_g: float = 0.3,
        frequency_hz: float = 1.5,
        duration_s: float = 8.0,
        fps: int = 12,
        risk_level: str = "orta",  # yalnızca mode="standard" için (4.1) — RiskLevel değeri
    ) -> dict[str, Any]:
        self._handle(project_id)  # proje açık mı doğrula
        entry = self._entry(project_id, key)
        b = entry.building
        if not b.floors:
            raise AppSessionError("Binanın hiç katı yok — sallanma simüle edilemez.")
        if mode not in ("standard", "engineering"):
            raise AppSessionError("mode 'standard' veya 'engineering' olmalı.")
        if not (0.01 <= peak_acceleration_g <= 2.0):
            raise AppSessionError("peak_acceleration_g 0.01-2.0 aralığında olmalı.")
        if not (0.1 <= frequency_hz <= 10.0):
            raise AppSessionError("frequency_hz 0.1-10.0 aralığında olmalı.")
        if not (1.0 <= duration_s <= 60.0):
            raise AppSessionError("duration_s 1-60 sn aralığında olmalı.")
        try:
            DamageRiskLevel(risk_level)
        except ValueError as exc:
            valid = ", ".join(v.value for v in DamageRiskLevel)
            raise AppSessionError(f"risk_level geçersiz: {risk_level} (geçerli: {valid})") from exc
        fps = max(2, min(int(fps), 30))

        num_floors = len(b.floors)
        floor_height = b.floors[0].height_m or 3.0
        usage_type = (
            b.building_type.value if hasattr(b.building_type, "value") else str(b.building_type)
        )
        structure_type = structure_type_for_usage(usage_type)
        shake_model = GroundShakeForceModel(
            peak_acceleration_g=peak_acceleration_g,
            frequency_hz=frequency_hz,
        )

        dt = 1.0 / fps
        num_steps = max(1, int(duration_s * fps))
        frames: list[dict[str, Any]] = []
        max_intensity = 0.0
        debris_triggered_floors: set[int] = set()

        if mode == "standard":
            simulator = BuildingShakeSimulator(
                building_id=key,
                shake_model=shake_model,
                structure_type=structure_type,
                num_floors=num_floors,
                floor_height_m=floor_height,
            )
            for step in range(num_steps):
                t = step * dt
                floor_states = [simulator.state_at(t, i) for i in range(num_floors)]
                intensity = max((s.intensity for s in floor_states), default=0.0)
                max_intensity = max(max_intensity, intensity)
                for i, s in enumerate(floor_states):
                    if simulator.local_peak_acceleration_g(t, i) >= simulator.debris_threshold_g:
                        debris_triggered_floors.add(i)
                frames.append(
                    {
                        "t": round(t, 3),
                        "intensity": round(intensity, 4),
                        "floors": [
                            {
                                "floor_index": s.floor_index,
                                "offset_m": [
                                    round(s.horizontal_offset_m[0], 4),
                                    round(s.horizontal_offset_m[1], 4),
                                ],
                                "rotation_rad": round(s.rotation_rad, 5),
                            }
                            for s in floor_states
                        ],
                    }
                )
            profile = simulator._profile()
            structure_label = profile.label
            drift_hints: list[dict[str, Any]] | None = None
        else:
            floor_area_m2 = max(getattr(b.footprint, "area_m2", None) or 200.0, 1.0)
            floor_props = [
                estimate_floor_properties(
                    structure_type=structure_type,
                    floor_area_m2=floor_area_m2,
                    num_floors=num_floors,
                )
                for _ in range(num_floors)
            ]
            mdof = MDOFShearFrameModel(
                building_id=key,
                shake_model=shake_model,
                floor_properties=floor_props,
                floor_height_m=floor_height,
            )
            worst_drift = 0.0
            for step in range(num_steps):
                states = mdof.step(dt)
                t = (step + 1) * dt
                max_disp = max((abs(s.displacement_m) for s in states), default=0.0)
                worst_drift = max(worst_drift, max(abs(s.interstory_drift_ratio) for s in states))
                frames.append(
                    {
                        "t": round(t, 3),
                        "diverged": mdof.diverged,
                        "floors": [
                            {
                                "floor_index": s.floor_index - 1,
                                "offset_m": [round(s.displacement_m, 4), 0.0],
                                "rotation_rad": round(
                                    math.atan2(
                                        s.displacement_m,
                                        max(floor_height * s.floor_index, floor_height),
                                    )
                                    * 0.3,
                                    5,
                                ),
                                "drift_ratio": round(s.interstory_drift_ratio, 5),
                            }
                            for s in states
                        ],
                    }
                )
                if mdof.diverged:
                    break
            hint = drift_based_damage_hint(worst_drift)
            drift_hints = [
                {
                    "damage_level": hint.damage_level.value
                    if hasattr(hint.damage_level, "value")
                    else str(hint.damage_level),
                    "drift_ratio": round(hint.drift_ratio, 5),
                    "threshold_label": hint.threshold_label,
                }
            ]
            structure_label = STRUCTURE_SHAKE_PROFILES.get(
                structure_type,
                DEFAULT_STRUCTURE_SHAKE_PROFILE,
            ).label
            max_intensity = min(worst_drift / 0.05, 1.0)

        panic_prob = panic_probability_from_intensity(max_intensity)

        # Roadmap V10 / Faz 4.1 + 4.4 — sallanma sonucundan hasar durumu
        # türet ve KALICI olarak sakla (senaryo/proje sıfırlanınca eski
        # hâline dönmemeli — sonraki `scene_json()` çağrıları bu binayı
        # artık kalıcı eğik/opak render eder).
        if mode == "standard":
            risk_enum = DamageRiskLevel(risk_level)
            damage_level = compute_damage_level(risk_enum, peak_shake_intensity=max_intensity)
        else:
            damage_level = drift_based_damage_hint(worst_drift).damage_level
        damage_state = damage_state_for_level(key, damage_level)
        self._damage_store.record(project_id, damage_state)
        # Mekansal Ses (Faz 5.6.1) girdisi — yeni bir eşik icat edilmedi:
        # "cam kırılması" için zaten hesaplanmış hasar kademesi (agir/cokme)
        # kullanılır, "enkaz sesi" için zaten hesaplanmış debris_triggered_floors
        # kullanılır.
        self._last_shake_status[(project_id, key)] = {
            "max_intensity": round(max_intensity, 4),
            "glass_shatter_triggered": damage_state.damage_level
            in (
                DamageLevel.SEVERE,
                DamageLevel.COLLAPSED,
            ),
            "debris_impact_triggered": bool(debris_triggered_floors) or damage_state.is_collapsed,
        }
        damage_payload = {
            "damage_level": damage_state.damage_level.value,
            "opacity": damage_state.opacity,
            "permanent_tilt_rad": damage_state.permanent_tilt_rad,
            "is_collapsed": damage_state.is_collapsed,
            "honesty_note": damage_state.honesty_note,
        }

        return {
            "building_key": key,
            "mode": mode,
            "structure_type": structure_type.value if structure_type else None,
            "structure_label": structure_label,
            "num_floors": num_floors,
            "floor_height_m": floor_height,
            "peak_acceleration_g": peak_acceleration_g,
            "frequency_hz": frequency_hz,
            "duration_s": duration_s,
            "fps": fps,
            "frame_count": len(frames),
            "frames": frames,
            "max_intensity": round(max_intensity, 4),
            "panic_probability": round(panic_prob, 4),
            "debris_floors": sorted(debris_triggered_floors) if mode == "standard" else [],
            "drift_damage_hints": drift_hints,
            "damage": damage_payload,
            "honesty_note": (
                "Faz 3.A: pseudo-static sinüzoidal taban hareketi + kategorik "
                "frekans/sönüm profili — gerçek modal analiz değildir."
                if mode == "standard"
                else "Faz 3.B: tahmini kütle/rijitlik + Newmark-beta MDOF çözücü — "
                "gerçek kolon/donatı verisi yok, kesin mühendislik tespiti değildir."
            ),
        }

    # -- Web arayüzü köprüsü: Afet/Deprem Risk Modülü (hazard_data) -------
    def building_damage_state(self, project_id: str, key: str) -> dict[str, Any]:
        """Faz 4.4: binanın en güncel kalıcı hasar durumunu (varsa)
        döner — `damage: null` ise henüz hiç sallanma/hasar simüle
        edilmemiş demektir (henüz `hasarsiz` bile atanmamış, nötr)."""
        self._entry(project_id, key)  # bina var mı doğrula
        state = self._damage_store.get(project_id, key)
        if state is None:
            return {"building_key": key, "damage": None}
        return {
            "building_key": key,
            "damage": {
                "damage_level": state.damage_level.value,
                "opacity": state.opacity,
                "permanent_tilt_rad": state.permanent_tilt_rad,
                "is_collapsed": state.is_collapsed,
                "honesty_note": state.honesty_note,
            },
        }

    # -- Web arayüzü köprüsü: Faz 5.1 — Duman/Yangın sprite fallback ------
    def building_fire_simulate(
        self,
        project_id: str,
        key: str,
        *,
        duration_s: float = 30.0,
        fps: int = 4,
        spread_rate_per_s: float = 0.35,
        grid_size: int = 8,
        ignition: str = "center",  # "center" | "corner"
        seed: int = 42,
    ) -> dict[str, Any]:
        """Roadmap V10 / Faz 5.1: binanın footprint'i üzerine oturtulmuş
        küçük bir hücre-otomat (`hazard_data.fire_spread.FireSpreadModel`,
        DEĞİŞTİRİLMEDEN) çalıştırıp her karede cepheye bindirilecek
        duman/alev sprite listesini (`visualization.scenario_visual_bridge.
        fire_facade_overlay()`, DEĞİŞTİRİLMEDEN) döner.

        Dürüstlük notu: bu endpoint HER ZAMAN sprite fallback modundadır —
        Faz 6'nın deneysel CFD çözücüsü (`physics.fluid_sim`) kameraya
        odaklanmış tek-bina senaryosu için ayrı, opsiyonel bir yoldur ve
        burada tetiklenmez (roadmap 6.2.2'nin "şehir genelinde asla"
        kısıtıyla tutarlı — dashboard'da her bina için varsayılan/güvenli
        görselleştirme her zaman bu spritedır).
        """
        self._handle(project_id)
        entry = self._entry(project_id, key)
        b = entry.building
        if not b.floors:
            raise AppSessionError("Binanın hiç katı yok — yangın simüle edilemez.")
        if not (2.0 <= duration_s <= 300.0):
            raise AppSessionError("duration_s 2-300 sn aralığında olmalı.")
        if not (0.05 <= spread_rate_per_s <= 2.0):
            raise AppSessionError("spread_rate_per_s 0.05-2.0 aralığında olmalı.")
        if ignition not in ("center", "corner"):
            raise AppSessionError("ignition 'center' veya 'corner' olmalı.")
        grid_size = max(4, min(int(grid_size), 24))
        fps = max(1, min(int(fps), 10))

        num_floors = len(b.floors)
        floor_height = b.floors[0].height_m or 3.0
        polygon_points = list(b.footprint.polygon.points)
        xs = [p.x for p in polygon_points]
        ys = [p.y for p in polygon_points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)

        ignition_cell = (grid_size // 2, grid_size // 2) if ignition == "center" else (0, 0)
        model = FireSpreadModel(
            width=grid_size,
            height=grid_size,
            ignition_cells=[ignition_cell],
            spread_rate_per_s=spread_rate_per_s,
            seed=seed,
        )

        def _cell_to_world(cell: tuple[int, int]) -> Point2D:
            cx, cy = cell
            wx = min_x + (cx + 0.5) / grid_size * span_x
            wy = min_y + (cy + 0.5) / grid_size * span_y
            return Point2D(wx, wy)

        def _cell_to_floor(cell: tuple[int, int]) -> int:
            # Duman/alev grid'in y-eksenini bina yüksekliğine yayar —
            # zemine yakın hücreler alt kata, üstteki hücreler üst kata
            # (roadmap'in "kat-hücre eşlemesini icat etmez" notuyla
            # tutarlı ama dashboard'da GÖRSEL bir yaklaşıklık gerektiği
            # için burada, sadece bu köprüde, doğrusal bir eşleme
            # uygulanır — fire_facade_overlay() bunu değiştirmiyor).
            _, cy = cell
            return min(int(cy / grid_size * num_floors), max(num_floors - 1, 0))

        dt = 1.0 / fps
        num_steps = max(1, int(duration_s * fps))
        frames: list[dict[str, Any]] = []
        peak_burning = 0
        for step in range(num_steps):
            t = (step + 1) * dt
            model.step(dt)
            sprites = fire_facade_overlay(
                model,
                key,
                cell_to_world=_cell_to_world,
                floor_height_m=floor_height,
                cell_to_floor=_cell_to_floor,
            )
            burning = model.burning_cell_count()
            peak_burning = max(peak_burning, burning)
            frames.append(
                {
                    "t": round(t, 3),
                    "sprites": [
                        {
                            "x": round(s.world_position.x, 3),
                            "y": round(s.world_position.y, 3),
                            "height_m": round(s.height_m, 3),
                            "kind": s.kind.value,
                            "intensity": round(s.intensity, 4),
                        }
                        for s in sprites
                    ],
                }
            )
            if burning == 0 and step > fps:  # tamamen söndü, erken bitir
                break

        # Mekansal Ses (Faz 5.6.1) girdisi — bu koşumun sonunda binada
        # hâlâ yanan hücre var mı (yangın alarmı sesi tetiklensin mi).
        last_frame_sprites = frames[-1]["sprites"] if frames else []
        self._last_fire_status[(project_id, key)] = {
            "fire_alarm_active": any(
                s["kind"] == FireSpriteKind.FLAME.value for s in last_frame_sprites
            ),
            "peak_burning_cell_count": peak_burning,
        }

        return {
            "building_key": key,
            "mode": "sprite_fallback",
            "grid_size": grid_size,
            "num_floors": num_floors,
            "floor_height_m": floor_height,
            "duration_s": duration_s,
            "fps": fps,
            "frame_count": len(frames),
            "frames": frames,
            "peak_burning_cell_count": peak_burning,
            "honesty_note": (
                "Faz 5.1: hücre-otomat tabanlı yaklaşık duman/alev "
                "yayılımıdır (gerçek CFD değildir); cepheye bindirilen "
                "sprite konumları/kat eşlemesi görsel bir yaklaşıklıktır."
            ),
        }

    # ==================================================================== #
    # Roadmap V10 / Faz 7 — Sinematik Yönetmenlik ve Sunum Katmanı
    # ==================================================================== #

    def cinematic_scene_events(self, project_id: str) -> dict[str, Any]:
        """Faz 7.1: mevcut proje durumundan (kalıcı hasar kayıtları,
        `DamagePersistenceStore` — Faz 4.4, DEĞİŞTİRİLMEDEN — ve son
        tahliye koşumunun son karesi) bir `visualization.cinematic_
        director.SceneEvent` listesi üretir, `InterestScorer` (aynı
        modül, DEĞİŞTİRİLMEDEN) ile en ilginçten en sıradana sıralar.

        Yeni bir olay-veriyolu icat edilmez: bu metod salt-okunur bir
        köprüdür, zaten hesaplanmış/saklanmış verinin (hasar durumu,
        son ajan karesi) `SceneEvent` sözleşmesine dökülmüş hâlidir.
        """
        handle = self._handle(project_id)
        from ..visualization.cinematic_director import (
            InterestScorer,
            SceneEvent,
            SceneEventKind,
        )

        entries = self._buildings.get(project_id, {})
        events: list[SceneEvent] = []

        for state in self._damage_store.all_for_scenario(project_id):
            entry = entries.get(state.building_id)
            if entry is None:
                continue
            b = entry.building
            centroid = b.footprint.polygon.centroid()
            floor_h = (b.floors[0].height_m or 3.0) if b.floors else 3.0
            z = floor_h * max(len(b.floors), 1) * 0.5
            if state.is_collapsed:
                kind, magnitude = SceneEventKind.BUILDING_COLLAPSE_START, 1.0
            elif state.damage_level != DamageLevel.NONE:
                kind = SceneEventKind.BUILDING_FIRST_CRACK
                magnitude = {
                    DamageLevel.LIGHT: 0.4,
                    DamageLevel.MODERATE: 0.7,
                    DamageLevel.SEVERE: 0.95,
                }.get(state.damage_level, 0.5)
            else:
                continue
            events.append(
                SceneEvent(
                    kind=kind,
                    time_s=0.0,
                    position=(centroid.x, centroid.y, z),
                    magnitude=magnitude,
                )
            )

        # Son tahliye koşumundan bir "kalabalık yoğunluğu / grup davranışı"
        # olayı — son ajan karesindeki panik oranından türetilir.
        result_id = self._last_evac_result_id.get(project_id)
        if result_id is not None:
            record = handle.db.load_object(result_id)
            if record is not None and record.kind == self._SIMULATION_RESULT_KIND:
                agent_frames = record.data.get("agent_frames") or []
                if agent_frames:
                    last = agent_frames[-1]
                    agents = last.get("agents", [])
                    if agents:
                        panic_count = sum(1 for a in agents if a.get("state") == "panic")
                        panic_ratio = panic_count / len(agents)
                        cx = sum(a["x"] for a in agents) / len(agents)
                        cy = sum(a["y"] for a in agents) / len(agents)
                        events.append(
                            SceneEvent(
                                kind=(
                                    SceneEventKind.GROUP_BEHAVIOR
                                    if panic_ratio >= 0.3
                                    else SceneEventKind.CROWD_DENSITY_PEAK
                                ),
                                time_s=float(last.get("t", 0.0)),
                                position=(cx, cy, 1.6),
                                magnitude=min(1.0, 0.3 + panic_ratio),
                            )
                        )

        scorer = InterestScorer()
        ranked = scorer.ranked(events)
        return {
            "events": [
                {
                    "kind": e.kind.value,
                    "time_s": e.time_s,
                    "position": list(e.position),
                    "magnitude": round(e.magnitude, 3),
                    "score": round(scorer.score(e), 2),
                }
                for e in ranked
            ],
            "honesty_note": (
                "Faz 7.1: skor, roadmap'in kendi kural-tabanlı öncelik "
                "sırasına (çökme > çatlak > asansör > yoğunluk > grup > "
                "sıradan) dayanan bir sezgiseldir - 'gerçek önem' iddiası "
                "taşımaz. Asansör mahsur kalma olayı (ELEVATOR_STRANDED) "
                "henüz bu köprüde üretilmiyor - erişilebilirlik etkisi "
                "hesaplanıyor ama olay konumu (odanın 3D koordinatı) "
                "şu an sahnede taşınmıyor."
            ),
        }

    def cinematic_camera_track(
        self,
        project_id: str,
        *,
        event_index: int,
        from_position: tuple[float, float, float],
        from_target: tuple[float, float, float],
        transition_duration_s: float = 2.5,
        hold_duration_s: float = 4.0,
        viewing_distance_m: float = 25.0,
        fov_deg: float = 45.0,
    ) -> dict[str, Any]:
        """Faz 7.2: `cinematic_scene_events()`'in döndürdüğü sıralı
        listeden `event_index`'teki olayı seçip, `visualization.
        cinematic_director.build_transition_keyframes()` (DEĞİŞTİRİLMEDEN)
        ile mevcut kameradan o olaya yumuşak bir geçiş keyframe dizisi
        üretir. Bu dizi doğrudan `visualization.camera_rig.CameraRig.
        cinematic_at(t)`'in tükettiği biçimdedir; ancak o interpolasyonu
        burada da (frontend tekrar hesaplamasın diye) düz bir örnekleme
        olarak (`samples`) önceden hesaplayıp döneriz - `CameraRig`'in
        kendisi DEĞİŞTİRİLMEDEN, yalnızca sonucu web tarafının anlayacağı
        düz bir zaman dizisine döktük.
        """
        from ..visualization.camera_rig import Camera, CameraRig
        from ..visualization.cinematic_director import build_transition_keyframes

        events_payload = self.cinematic_scene_events(project_id)["events"]
        if not (0 <= event_index < len(events_payload)):
            raise AppSessionError(f"event_index aralık dışı (0-{max(len(events_payload) - 1, 0)}).")
        from ..visualization.cinematic_director import SceneEvent, SceneEventKind

        ev = events_payload[event_index]
        scene_event = SceneEvent(
            kind=SceneEventKind(ev["kind"]),
            time_s=ev["time_s"],
            position=tuple(ev["position"]),
            magnitude=ev["magnitude"],
        )
        from_cam = Camera(position=tuple(from_position), target=tuple(from_target))
        keyframes = build_transition_keyframes(
            from_cam,
            scene_event,
            transition_duration_s=transition_duration_s,
            hold_duration_s=hold_duration_s,
            viewing_distance_m=viewing_distance_m,
            fov_deg=fov_deg,
        )
        rig = CameraRig(camera=Camera(position=tuple(from_position), target=tuple(from_target)))
        rig.set_cinematic_track(keyframes)

        total_duration_s = keyframes[-1].time_s if keyframes else 0.0
        fps = 20
        num_samples = max(2, int(total_duration_s * fps) + 1)
        samples: list[dict[str, Any]] = []
        for i in range(num_samples):
            t = total_duration_s * i / (num_samples - 1) if num_samples > 1 else 0.0
            cam = rig.cinematic_at(t)
            samples.append(
                {
                    "t": round(t, 3),
                    "position": [round(v, 3) for v in cam.position],
                    "target": [round(v, 3) for v in cam.target],
                    "fov_deg": round(cam.fov_deg, 3),
                }
            )

        return {
            "event": ev,
            "duration_s": round(total_duration_s, 3),
            "samples": samples,
            "honesty_note": (
                "Faz 7.2: geçiş, `_smoothstep` (3t²-2t³) ile örneklenen "
                "düz konum/hedef interpolasyonudur - gerçek bir dolly/"
                "crane kamera fizik motoru değildir; FOV geçiş boyunca "
                "sabit tutulur (ani zoom yapılmaz)."
            ),
        }

    # ==================================================================== #
    # Roadmap V10 / Mekansal Ses (Faz 5.6)
    # ==================================================================== #

    def spatial_audio_state(
        self,
        project_id: str,
        *,
        listener_position: tuple[float, float, float] = (0.0, 0.0, 1.6),
    ) -> dict[str, Any]:
        """Faz 5.6: mevcut proje durumundan (son sallanma/yangın koşumları
        + son tahliye koşumunun son karesi) `visualization.spatial_audio`
        (DEĞİŞTİRİLMEDEN) ile bir ses-olay + kalabalık ambiyansı + konum/
        kazanç listesi üretir. Yeni bir ses motoru icat edilmez - WebAudio
        `AudioContext`/`PannerNode` viewer tarafında kurulur, bu metod
        yalnızca "hangi efekt, ne zaman, ne kazançla, nerede" kararını
        üretir.
        """
        handle = self._handle(project_id)
        from ..mobility.crowd_simulation.agent_visuals import CrowdPressureLevel
        from ..visualization.spatial_audio import (
            PositionalAudioSource,
            crowd_ambience_mix,
            positional_gain,
            trigger_event_sound,
        )

        entries = self._buildings.get(project_id, {})
        sound_events: list[dict[str, Any]] = []

        def _emit(key: str, **kwargs: Any) -> None:
            entry = entries.get(key)
            if entry is None:
                return
            centroid = entry.building.footprint.polygon.centroid()
            source = PositionalAudioSource(position=(centroid.x, centroid.y, 1.6))
            gain = positional_gain(source, listener_position)
            for ev in trigger_event_sound(source=source, **kwargs):
                sound_events.append(
                    {
                        "building_key": key,
                        "effect": ev.effect.value,
                        "intensity": round(ev.intensity, 3),
                        "gain": round(gain * ev.intensity, 4),
                        "position": [round(v, 3) for v in source.position],
                    }
                )

        for (pid, key), status in self._last_shake_status.items():
            if pid != project_id:
                continue
            _emit(
                key,
                shake_intensity=status["max_intensity"],
                glass_shatter_triggered=status["glass_shatter_triggered"],
                debris_impact_triggered=status["debris_impact_triggered"],
            )

        for (pid, key), status in self._last_fire_status.items():
            if pid != project_id or not status["fire_alarm_active"]:
                continue
            _emit(key, fire_alarm_active=True)

        # 5.6.2 — kalabalık ambiyansı, son tahliye koşumunun son karesinden
        ambience: dict[str, Any] | None = None
        result_id = self._last_evac_result_id.get(project_id)
        if result_id is not None:
            record = handle.db.load_object(result_id)
            if record is not None and record.kind == self._SIMULATION_RESULT_KIND:
                agent_frames = record.data.get("agent_frames") or []
                if agent_frames:
                    agents = agent_frames[-1].get("agents", [])
                    if agents:
                        panic_ratio = sum(1 for a in agents if a.get("state") == "panic") / len(
                            agents
                        )
                        # Kaba yoğunluk tahmini: ajan sayısı / tipik spawn
                        # alanı (20x20m — `simulation_evacuation_run()`'ın
                        # kendi spawn alanı varsayımıyla tutarlı, yeni bir
                        # alan hesabı icat edilmedi).
                        density = len(agents) / 400.0
                        pressure = (
                            CrowdPressureLevel.SQUEEZE
                            if density > 0.5
                            else CrowdPressureLevel.MILD
                            if density > 0.15
                            else CrowdPressureLevel.NONE
                        )
                        mix = crowd_ambience_mix(pressure, panic_ratio)
                        cx = sum(a["x"] for a in agents) / len(agents)
                        cy = sum(a["y"] for a in agents) / len(agents)
                        ambience = {
                            "mix": {k.value: round(v, 4) for k, v in mix.items()},
                            "position": [round(cx, 3), round(cy, 3), 1.6],
                            "pressure_level": pressure.value,
                            "panic_ratio": round(panic_ratio, 4),
                        }

        return {
            "sound_events": sound_events,
            "crowd_ambience": ambience,
            "listener_position": [round(v, 3) for v in listener_position],
            "honesty_note": (
                "Faz 5.6: bu, WebAudio'nun PannerNode/AudioBufferSourceNode'"
                "unun tüketeceği bir konum+kazanç sözleşmesidir - gerçek "
                "ses dosyaları (.ogg/.wav) viewer tarafında eşlenir; burada "
                "yalnızca 'hangi efekt, ne kazançla, nerede' kararı üretilir."
            ),
        }

    #
    # Roadmap iş fikri #1: "Deprem risk + tahliye haritası — AFAD/USGS canlı
    # veri, PGA tahmini, risk skoru, tahliye rotası → mahalle bazlı rapor."
    # `hazard_data/` paketi (afad_client, usgs_client, pga_estimate,
    # risk_scoring, evacuation) zaten vardı ama hiçbir yerden çağrılmıyordu;
    # burada uçtan uca bağlanır: gerçek katalog verisi (varsa) + PGA tahmini
    # + bina risk skoru + `find_path` (mobility.pathfinding) ile gerçek
    # tahliye rotası.

    def hazard_earthquake_catalog(
        self,
        project_id: str,
        *,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        source: str = "usgs",
        min_magnitude: float = 2.5,
        days: int = 30,
    ) -> dict[str, Any]:
        """AFAD/USGS'ten gerçek deprem kataloğunu çeker. `hazard_data` kendi
        ilkesi gereği ağ erişimi yoksa sessizce sahte veri ÜRETMEZ —
        `HazardNetworkError` fırlatır; bu metod bunu yakalayıp
        `is_live=False` ile açıkça raporlar."""
        from ..hazard_data import AFADClient, HazardError, USGSClient

        self._handle(project_id)
        end = datetime.now(tz=timezone.utc)
        start = end.fromtimestamp(end.timestamp() - days * 86400, tz=timezone.utc)
        try:
            if source == "afad":
                events = AFADClient().fetch_earthquakes(
                    min_lat=min_lat,
                    max_lat=max_lat,
                    min_lon=min_lon,
                    max_lon=max_lon,
                    start=start,
                    end=end,
                    min_magnitude=min_magnitude,
                )
            else:
                events = USGSClient().fetch_earthquakes(
                    min_lat=min_lat,
                    max_lat=max_lat,
                    min_lon=min_lon,
                    max_lon=max_lon,
                    start=start,
                    end=end,
                    min_magnitude=min_magnitude,
                )
        except HazardError as exc:
            return {
                "is_live": False,
                "source": source,
                "events": [],
                "note": (
                    f"{source.upper()} kataloğuna ulaşılamadı (ağ erişimi yok/engelli): {exc}. "
                    "Risk skoru yine de hesaplanabilir; bu sadece son N günün gerçek "
                    "deprem listesidir, PGA tahminini etkilemez."
                ),
            }
        return {
            "is_live": True,
            "source": source,
            "events": [e.to_dict() for e in events],
            "note": f"{len(events)} olay, son {days} gün, M>={min_magnitude}.",
        }

    def hazard_pga_estimate(self, project_id: str, *, lat: float, lon: float) -> dict[str, Any]:
        """Bölgesel PGA (Peak Ground Acceleration) tahmini — resmi TDTH
        haritası YERİNE GEÇMEZ, sadece kaba bir varsayılan (bkz.
        `hazard_data.pga_estimate` docstring'i)."""
        from ..hazard_data import RegionalPGAEstimate

        self._handle(project_id)
        result = RegionalPGAEstimate().estimate(lat, lon)
        return {
            "lat": lat,
            "lon": lon,
            "pga_g": round(result.pga_g, 3),
            "zone_name": result.zone_name,
            "is_official_source": result.is_official_source,
            "note": result.note,
        }

    def _building_slenderness(self, project_id: str, key: str) -> float | None:
        from ..building_reconstruction import validate_building

        entry = self._buildings.get(project_id, {}).get(key)
        if entry is None:
            return None
        report = validate_building(entry.building)
        return report.slenderness_ratio

    def hazard_building_risk(
        self,
        project_id: str,
        *,
        lat: float,
        lon: float,
        key: str | None = None,
        construction_year: int | None = None,
        floor_count: int | None = None,
        soil_type: str = "bilinmiyor",
        pga_g_override: float | None = None,
    ) -> dict[str, Any]:
        """Roadmap #1'in çekirdeği: bölgesel PGA + yapım yılı + kat sayısı +
        (varsa gerçek `key` ile sahnedeki binadan) narinlik oranını
        birleştirip 0-100 GÖSTERGE risk indeksi üretir. `key` verilirse kat
        sayısı ve narinlik oranı sahnedeki gerçek binadan otomatik okunur."""
        from ..hazard_data import RegionalPGAEstimate, SoilType, score_building_risk

        self._handle(project_id)
        slenderness = None
        if key is not None:
            entry = self._entry(project_id, key)
            floor_count = floor_count or len(entry.building.floors)
            slenderness = self._building_slenderness(project_id, key)

        if pga_g_override is not None:
            pga_g = pga_g_override
            pga_note = "Kullanıcı tarafından elle girilen PGA değeri."
            pga_official = True
        else:
            pga_result = RegionalPGAEstimate().estimate(lat, lon)
            pga_g = pga_result.pga_g
            pga_note = pga_result.note
            pga_official = pga_result.is_official_source

        try:
            soil = SoilType(soil_type)
        except ValueError:
            soil = SoilType.UNKNOWN

        report = score_building_risk(
            pga_g=pga_g,
            construction_year=construction_year,
            floor_count=floor_count,
            slenderness_ratio=slenderness,
            soil_type=soil,
        )
        return {
            "key": key,
            "lat": lat,
            "lon": lon,
            "pga_g": round(pga_g, 3),
            "pga_note": pga_note,
            "pga_official_source": pga_official,
            "risk_index_0_100": round(report.risk_index_0_100, 1),
            "risk_level": report.risk_level.value,
            "factors": [
                {
                    "name": f_.name,
                    "subscore_0_100": round(f_.subscore_0_100, 1)
                    if f_.subscore_0_100 is not None
                    else None,
                    "weight": f_.weight,
                    "note": f_.note,
                }
                for f_ in report.factors
            ],
            "disclaimer": report.disclaimer,
        }

    def hazard_evacuation_plan(
        self,
        project_id: str,
        *,
        buildings: list[dict[str, Any]],
        safe_points: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Birden fazla bina için risk skoruna göre tahliye/denetim
        önceliklendirmesi (`hazard_data.evacuation.prioritize_evacuation`) +
        her bina için sahnedeki en yakın güvenli toplanma alanına gerçek
        rota (mevcut `find_path` — `mobility.pathfinding`, bina ayak
        izlerini engel sayarak A* çalıştırır). `buildings`: her biri
        {key?, lat, lon, construction_year?, floor_count?, soil_type?,
        occupant_estimate?}; `safe_points`: her biri {name, x, y}
        (sahne yerel koordinatı, örn. bir park/meydan)."""
        from ..hazard_data import BuildingRiskReport, prioritize_evacuation
        from ..hazard_data.risk_scoring import BuildingRiskFactor, RiskLevel

        self._handle(project_id)
        if not safe_points:
            raise AppSessionError("en az bir güvenli toplanma alanı (safe_points) gerekli.")

        scored: list[tuple[str, BuildingRiskReport]] = []
        occupant_estimates: dict[str, int] = {}
        meta: dict[str, dict[str, Any]] = {}

        for idx, b in enumerate(buildings):
            building_id = str(b.get("key") or b.get("id") or f"bina_{idx + 1}")
            risk = self.hazard_building_risk(
                project_id,
                lat=float(b["lat"]),
                lon=float(b["lon"]),
                key=b.get("key"),
                construction_year=b.get("construction_year"),
                floor_count=b.get("floor_count"),
                soil_type=b.get("soil_type", "bilinmiyor"),
            )
            report = BuildingRiskReport(
                risk_index_0_100=risk["risk_index_0_100"],
                risk_level=RiskLevel(risk["risk_level"]),
                factors=tuple(
                    BuildingRiskFactor(f_["name"], f_["subscore_0_100"], f_["weight"], f_["note"])
                    for f_ in risk["factors"]
                ),
            )
            scored.append((building_id, report))
            meta[building_id] = {"key": b.get("key"), "risk": risk}
            if b.get("occupant_estimate") is not None:
                occupant_estimates[building_id] = int(b["occupant_estimate"])

        priorities = prioritize_evacuation(scored, occupant_estimates=occupant_estimates)

        routes: dict[str, Any] = {}
        for p in priorities:
            key = meta[p.building_id].get("key")
            if key is None:
                routes[p.building_id] = {
                    "note": "sahnede gerçek bina eşleşmesi yok (key verilmedi), rota hesaplanmadı."
                }
                continue
            entry = self._buildings.get(project_id, {}).get(key)
            if entry is None:
                routes[p.building_id] = {"note": "bina sahnede bulunamadı."}
                continue
            centroid = entry.building.footprint.polygon.centroid()
            best_route = None
            for sp in safe_points:
                try:
                    route = self.find_path(
                        project_id,
                        start_x=centroid.x,
                        start_y=centroid.y,
                        goal_x=float(sp["x"]),
                        goal_y=float(sp["y"]),
                        cell_size=2.0,
                    )
                except AppSessionError:
                    continue
                if route["found"] and (
                    best_route is None or route["cost_m"] < best_route["cost_m"]
                ):
                    best_route = {**route, "safe_point": sp.get("name", "isimsiz")}
            routes[p.building_id] = best_route or {
                "note": "hiçbir güvenli noktaya rota bulunamadı."
            }

        return {
            "priorities": [
                {
                    "building_id": p.building_id,
                    "rank": p.rank,
                    "risk_index_0_100": round(p.risk_report.risk_index_0_100, 1),
                    "risk_level": p.risk_report.risk_level.value,
                    "occupant_estimate": p.occupant_estimate,
                    "note": p.note,
                    "risk_detail": meta[p.building_id]["risk"],
                    "evacuation_route": routes.get(p.building_id),
                }
                for p in priorities
            ],
            "safe_points": safe_points,
        }

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz II — Agent-Bazlı Deprem
    #    Tahliye Animasyonu (Katman 2.4). `hazard_evacuation_plan` (yukarı)
    #    bina-seviyesi risk/rota önceliklendirmesiydi; bu ikisi agent-bazlı
    #    gerçek kalabalık simülasyonu (`EvacuationSimulator`/
    #    `SocialForceModel`) + O.1 (Recorder) + O.2 (Scene agent_frames) +
    #    O.3 (SimulationScenario) + O.4 (CityEventType) omurgasını birbirine
    #    bağlar. Yeni bir depolama icat edilmedi: sonuç, mevcut
    #    `ProjectDatabase.save_object(..., kind="simulation_result", ...)`
    #    genel nesne deposuyla saklanır (senaryonun kendisi zaten
    #    `save_scenario` ile aynı deseni kullanıyordu).

    _SIMULATION_RESULT_KIND = "simulation_result"
    _CAPACITY_ANALYSIS_KIND = "capacity_analysis_result"
    _FIRE_SPREAD_KIND = "fire_spread_result"

    def simulation_scenario_save(
        self,
        project_id: str,
        scenario_data: dict[str, Any],
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Bir `SimulationScenario`'yu doğrulayıp projeye kaydeder (O.3).

        ROADMAP_V9 Faz X / Katman 8 madde 1: `role` verilirse (rol-bazlı
        senaryo izinleri), yalnızca EDITOR/OWNER senaryo kaydedebilir —
        `role=None` (varsayılan) geriye uyumluluk için denetimsiz bırakır.
        """
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        handle = self._handle(project_id)
        try:
            scenario = SimulationScenario.from_dict(scenario_data)
        except ScenarioValidationError as exc:
            raise AppSessionError(f"geçersiz senaryo: {exc}") from exc
        save_scenario(handle.db, scenario)
        return scenario.to_dict()

    def simulation_scenario_get(self, project_id: str, scenario_id: str) -> dict[str, Any]:
        handle = self._handle(project_id)
        scenario = load_scenario(handle.db, scenario_id)
        if scenario is None:
            raise AppSessionError(f"senaryo bulunamadı: {scenario_id}")
        return scenario.to_dict()

    def simulation_evacuation_run(
        self,
        project_id: str,
        *,
        scenario_id: str | None = None,
        scenario_data: dict[str, Any] | None = None,
        safe_point: dict[str, Any] | None = None,
        max_time_s: float = 600.0,
        keyframe_interval_s: float = 0.5,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Katman 2.4'ün temel akışını çalıştırır: mevcut
        `EvacuationSimulator`/`SocialForceModel` motorunu, kayıtlı (veya
        gövdeyle birlikte verilen) bir `SimulationScenario`'ya göre kurar,
        `SimulationRecorder` ile ara kareleri kaydeder (O.1), sonucu bir
        `Scene.agent_frames`'e (O.2) yazıp saklar ve
        `CityEventType.EVACUATION_STARTED`/`EVACUATION_COMPLETED` olaylarını
        yayınlar (O.4). `scenario_id` verilirse önceden kaydedilmiş senaryo
        yüklenir; `scenario_data` verilirse hem doğrulanır hem (tekrar
        kullanılabilsin diye) kaydedilir. İkisi de verilmezse hata.

        ROADMAP_V9 Faz X / Katman 8 madde 1: `role` verilirse EDITOR/OWNER
        gerekir (VIEWER yalnızca sonuç görüntüleyebilir) — `role=None`
        varsayılanında denetimsiz (geriye uyumlu).
        """
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        handle = self._handle(project_id)

        if scenario_data is not None:
            try:
                scenario = SimulationScenario.from_dict(scenario_data)
            except ScenarioValidationError as exc:
                raise AppSessionError(f"geçersiz senaryo: {exc}") from exc
            save_scenario(handle.db, scenario)
        elif scenario_id is not None:
            scenario = load_scenario(handle.db, scenario_id)
            if scenario is None:
                raise AppSessionError(f"senaryo bulunamadı: {scenario_id}")
        else:
            raise AppSessionError("scenario_id veya scenario_data gerekli.")

        entry = self._buildings.get(project_id, {}).get(scenario.building.building_ref)
        if entry is None:
            raise AppSessionError(
                f"senaryonun işaret ettiği bina sahnede bulunamadı: "
                f"{scenario.building.building_ref}"
            )
        centroid = entry.building.footprint.polygon.centroid()

        # Katman 2.4 madde 2 — "asansör kullanma kısıtı": bina gerçek oda
        # verisi (RoomGenerator çıktısı) taşıyorsa, IndoorNavigationBuilder
        # ile gerçek BuildingNavGraph kurulup asansör kenarları bloke
        # edilir ve erişilebilirlik etkisi hesaplanır. Motorun kendisi
        # (SocialForceModel açık-alan spawn'ı) bu oturumda değiştirilmedi —
        # bu, dürüst bir ara adım: asansör kısıtının GERÇEK graf etkisini
        # ölçüp raporluyoruz, ama agent hareketini henüz bu graf üzerinden
        # yürütmüyoruz (bkz. aşağıdaki `elevator_accessibility_impact`).
        elevator_accessibility_impact: dict[str, Any] | None = None
        if scenario.disable_elevators:
            elevator_accessibility_impact = self._elevator_accessibility_impact(entry)

        if safe_point is not None:
            safe_x, safe_y = float(safe_point["x"]), float(safe_point["y"])
        else:
            # Roadmap'in "toplanma alanı" kavramı — açık-alan yaya ağı
            # (Katman 3.1) henüz yok; makul bir varsayılan: bina
            # merkezinden 50m uzakta, dürüstlük notu döndürülen sonuca
            # eklenir (aşağıda `safe_point_is_default`).
            safe_x, safe_y = centroid.x + 50.0, centroid.y

        agents = spawn_random_agents(
            count=scenario.agents.count,
            area_min=Point2D(centroid.x - 10.0, centroid.y - 10.0),
            area_max=Point2D(centroid.x + 10.0, centroid.y + 10.0),
            goal=Point2D(safe_x, safe_y),
            seed=scenario.agents.seed,
        )
        self._apply_behavior_distribution(
            agents, scenario.agents.behavior_distribution, scenario.agents.seed
        )

        emit_city_event(
            default_bus,
            CityEventType.EVACUATION_STARTED,
            source="app_shell.session",
            project_id=project_id,
            scenario_id=scenario.scenario_id,
            agent_count=len(agents),
        )

        recorder = SimulationRecorder(keyframe_interval_s=keyframe_interval_s)
        result = EvacuationSimulator().run(
            agents,
            dt=0.1,
            max_time_s=max_time_s,
            recorder=recorder,
        )

        scene = Scene(name=f"{project_id}_evac_{scenario.scenario_id}")
        scene.push_agent_recording(recorder)

        result_id = f"evac_{uuid.uuid4().hex[:12]}"
        # Roadmap V10 / Faz 2.4 — her ajan için deterministik (agent_id +
        # seed'e bağlı, çalıştırmadan çalıştırmaya SABİT) mesh/renk
        # varyantı. `mobility.crowd_simulation.agent_visuals` DEĞİŞTİRİLMEDEN
        # tekrar kullanılır — burada yalnızca çalışan ajan kümesine
        # uygulanıp render'a taşınacak sözleşmeye dönüştürülür. LOD
        # (billboard/kapsül/iskelet) mesafe eşiği render tarafının kamera
        # konumuna ihtiyaç duyduğu için burada SEÇİLMEZ — eşik SABİTLERİ
        # (aynı `agent_visuals` modülünden, tek kaynak) frontend'in kendi
        # hesaplaması için birlikte döndürülür.
        agent_visuals = {
            str(a.agent_id): {
                "mesh_variant": (v := agent_visual_variant(a.agent_id, seed=scenario.agents.seed))[
                    0
                ],
                "color_hex": v[1],
            }
            for a in agents
        }
        result_data: dict[str, Any] = {
            "result_id": result_id,
            "project_id": project_id,
            "scenario_id": scenario.scenario_id,
            "scenario": scenario.to_dict(),
            "safe_point": {"x": safe_x, "y": safe_y},
            "safe_point_is_default": safe_point is None,
            "total_agents": result.total_agents,
            "evacuated_count": result.evacuated_count,
            "evacuation_time_s": round(result.evacuation_time_s, 2),
            "timed_out": result.timed_out,
            "bottleneck_location": result.bottleneck_location,
            "bottleneck_peak_time_s": result.bottleneck_peak_time_s,
            "bottleneck_peak_count": result.bottleneck_peak_count,
            "agent_frames": scene.to_dict()["agent_frames"],
            "agent_visuals": agent_visuals,
            "agent_visual_lod_thresholds_m": {
                "skeletal_max_distance_m": DEFAULT_SKELETAL_MAX_DISTANCE_M,
                "capsule_max_distance_m": DEFAULT_CAPSULE_MAX_DISTANCE_M,
            },
            "disable_elevators_requested": scenario.disable_elevators,
            "elevator_accessibility_impact": elevator_accessibility_impact,
            "note": (
                (
                    "Bu bir gösterge simülasyonudur, kesin mühendislik raporu "
                    "değildir. Asansör kısıtı (disable_elevators) için gerçek "
                    "BuildingNavGraph erişilebilirlik etkisi hesaplandı (bkz. "
                    "elevator_accessibility_impact) — ancak agent hareket "
                    "motoru (SocialForceModel) bu oturumda hâlâ düz-alan "
                    "(open-area) yaklaşımı kullanıyor, indoor graf üzerinde "
                    "yürümüyor. ROADMAP_V9.md Katman 2.4 madde 2'nin tam "
                    "kapanması için bir sonraki adım: agent rotalarının bu "
                    "graf üzerinden hesaplanması."
                    if elevator_accessibility_impact is not None
                    and elevator_accessibility_impact.get("room_graph_available")
                    else "Bu bir gösterge simülasyonudur, kesin mühendislik raporu "
                    "değildir. Asansör kısıtı (disable_elevators) istendi "
                    "ama bu bina için oda-graf verisi (RoomGenerator çıktısı) "
                    "bulunamadığından erişilebilirlik etkisi hesaplanamadı; "
                    "motor düz-alan (open-area) yaklaşımı kullanır."
                )
                if scenario.disable_elevators
                else "Bu bir gösterge simülasyonudur, kesin mühendislik raporu değildir."
            ),
        }
        handle.db.save_object(result_id, self._SIMULATION_RESULT_KIND, result_data)
        self._last_evac_result_id[project_id] = result_id

        emit_city_event(
            default_bus,
            CityEventType.EVACUATION_COMPLETED,
            source="app_shell.session",
            project_id=project_id,
            scenario_id=scenario.scenario_id,
            result_id=result_id,
            evacuated_count=result.evacuated_count,
            evacuation_time_s=result.evacuation_time_s,
        )

        summary = dict(result_data)
        summary.pop("agent_frames", None)
        summary["agent_frames_count"] = len(result_data["agent_frames"])
        return summary

    def _elevator_accessibility_impact(self, entry: _BuildingEntry) -> dict[str, Any]:
        """Katman 2.4 madde 2 + Katman 9.6 (erişilebilirlik uyarı motoru)
        için temel veri: bu bina için gerçek `IndoorNavigationBuilder`
        graf'ı kurulabiliyorsa (RoomGenerator çıktısı mevcutsa), asansörler
        devre dışı bırakıldığında hangi odaların merdiven/rota üzerinden
        artık ulaşılamaz kaldığını hesaplar. Oda verisi yoksa (yalnızca
        footprint/mesh üretilmiş, RoomGenerator hiç çalıştırılmamış bina)
        `room_graph_available: False` ile açıkça belirtir — sessizce
        yanıltıcı "0 oda etkilendi" sonucu döndürmez.
        """
        floors = getattr(entry.building, "floors", None) or []
        indoor_floors: list[IndoorFloor] = []
        for f in floors:
            if not getattr(f, "rooms", None):
                continue
            indoor_floors.append(
                IndoorFloor(
                    floor_index=f.level,
                    rooms=f.rooms,
                    stairs=list(getattr(f, "stair_objects", []) or []),
                    elevators=list(getattr(f, "elevator_objects", []) or []),
                )
            )

        if len(indoor_floors) < 2:
            return {
                "room_graph_available": False,
                "reason": (
                    "Bu bina için en az iki katta oda-graf verisi "
                    "(RoomGenerator çıktısı) bulunamadı; asansör "
                    "erişilebilirlik etkisi hesaplanamaz."
                ),
                "elevator_edge_count": 0,
                "unreachable_room_count": 0,
                "unreachable_rooms": [],
            }

        floor_height = floors[0].height_m if floors and floors[0].height_m else FLOOR_HEIGHT_DEFAULT
        building_graph = IndoorNavigationBuilder.build(indoor_floors, floor_height=floor_height)
        ground_index = min(fl.floor_index for fl in indoor_floors)
        unreachable = building_graph.unreachable_rooms_without_elevator(
            ground_floor_index=ground_index
        )

        return {
            "room_graph_available": True,
            "floor_count": len(indoor_floors),
            "elevator_edge_count": len(building_graph.elevator_edges),
            "unreachable_room_count": len(unreachable),
            "unreachable_rooms": [
                {"floor_index": node[0], "room_id": node[1]} for node in unreachable
            ],
            "accessibility_warning": (
                "Asansörler devre dışı kaldığında bu binada merdivenle "
                f"ulaşılamayan {len(unreachable)} oda tespit edildi — "
                "engelli/hareket kısıtlı tahliye planı eksik olabilir "
                "(ROADMAP_V9.md Katman 2.4 madde 2 çelişki uyarısı)."
                if unreachable
                else "Asansörler devre dışı kalsa bile tüm odalara merdivenle "
                "ulaşılabiliyor (bu binanın bu analizi için)."
            ),
        }

    def simulation_evacuation_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Tam sonucu (agent_frames animasyon verisi dahil) döner —
        `render_engine/viewer`'ın oynatabileceği aynı `agent_frames` şeması
        (O.2)."""
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._SIMULATION_RESULT_KIND:
            raise AppSessionError(f"tahliye sonucu bulunamadı: {result_id}")
        return record.data

    def capacity_analysis_run(
        self,
        project_id: str,
        *,
        room_width_m: float,
        room_depth_m: float,
        exit_width_m: float,
        agent_counts: list[int] | None = None,
        building_type: str | None = None,
        regulation_profile_name: str | None = None,
        seed: int = 42,
        max_time_s: float = 900.0,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Roadmap V9 / Katman 7.3 (Faz III) — "aynı bina için 50/200/500
        kişi senaryolarını otomatik art arda koşturup karşılaştırmalı
        rapor üreten" batch-runner'ın REST/session köprüsü. Yeni bir
        simülasyon motoru çağırmaz; ince `CapacityAnalyzer` sarmalayıcısı
        (mevcut `EvacuationBenchmark`/`EvacuationSimulator`'ı tekrar tekrar
        koşturur) kullanılır. Sonuç, O.3'ün senaryo depolama desenindeki
        aynı ilkeyle (`save_object(kind=...)`, yeni bir depolama icat
        edilmeden) saklanır.

        ROADMAP_V9 Faz X / Katman 8 madde 1: `role` verilirse EDITOR/OWNER
        gerekir — `role=None` varsayılanında denetimsiz (geriye uyumlu).
        """
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        handle = self._handle(project_id)
        profile = (
            get_regulation_profile(regulation_profile_name)
            if regulation_profile_name
            else default_regulation_profile()
        )
        counts = tuple(agent_counts) if agent_counts else DEFAULT_CAPACITY_AGENT_COUNTS
        report = CapacityAnalyzer.run_batch(
            room_width_m=room_width_m,
            room_depth_m=room_depth_m,
            exit_width_m=exit_width_m,
            agent_counts=counts,
            building_type=building_type,
            regulation_profile=profile,
            seed=seed,
            max_time_s=max_time_s,
        )
        result_id = f"cap_{uuid.uuid4().hex[:12]}"
        result_data = report.to_dict()
        result_data["result_id"] = result_id
        result_data["project_id"] = project_id
        handle.db.save_object(result_id, self._CAPACITY_ANALYSIS_KIND, result_data)
        return result_data

    def capacity_analysis_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._CAPACITY_ANALYSIS_KIND:
            raise AppSessionError(f"kapasite analizi sonucu bulunamadı: {result_id}")
        return record.data

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz V — Yangın Yayılım Demosu -- #

    def fire_spread_demo_run(
        self,
        project_id: str,
        *,
        width: int,
        height: int,
        ignition_cells: list[tuple[int, int]],
        wall_cells: list[tuple[int, int]] | None = None,
        door_cells: list[tuple[int, int]] | None = None,
        duration_s: float = 60.0,
        dt: float = 1.0,
        spread_rate_per_s: float = 0.35,
        seed: int = 42,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """`hazard_data.fire_spread.FireSpreadModel`'in doğrudan, ızgara-
        seviyesinde bir web-panel köprüsü. Roadmap V9 / Katman 7.2 (Faz V)
        motoru **hiç değiştirilmeden** çağrılır (yeni bir yangın motoru
        yazılmadı) — bu bilinçli olarak bina/graf entegrasyonundan bağımsız
        soyut bir ızgara demosu: kullanıcı ateşin başladığı hücreleri ve
        (opsiyonel) duvar/kapı hücrelerini seçip yayılımı adım adım
        gözlemleyebilir. Gerçek bina-graf entegrasyonu
        (`FireAwareRouter`/`PeriodicFireRerouter`) mevcut motor testlerinde
        zaten doğrulanmıştır (`tests/test_roadmap_v9_faz5_fire_spread.py`).
        """
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)  # proje var mı diye doğrula (izin kontrolü)
        if width <= 0 or height <= 0:
            raise AppSessionError("width/height pozitif olmalı.")
        if not ignition_cells:
            raise AppSessionError("en az bir ignition_cells (ateşin başladığı hücre) gerekli.")

        model = FireSpreadModel(
            width=width,
            height=height,
            ignition_cells=[tuple(c) for c in ignition_cells],
            wall_cells=frozenset(tuple(c) for c in (wall_cells or [])),
            door_cells=frozenset(tuple(c) for c in (door_cells or [])),
            spread_rate_per_s=spread_rate_per_s,
            seed=seed,
        )
        model.run(duration_s=duration_s, dt=dt)

        grid = [
            [round(model.intensity.get((x, y), 0.0), 3) for x in range(width)]
            for y in range(height)
        ]
        result_id = f"fire_{uuid.uuid4().hex[:12]}"
        result_data = {
            "result_id": result_id,
            "project_id": project_id,
            "width": width,
            "height": height,
            "elapsed_s": model.elapsed_s,
            "grid": grid,
            "burning_cell_count": model.burning_cell_count(),
            "smoke_cell_count": len(model.cells_by_state(FireCellState.SMOKE)),
            "clear_cell_count": len(model.cells_by_state(FireCellState.CLEAR)),
            "wall_cells": [list(c) for c in (wall_cells or [])],
            "door_cells": [list(c) for c in (door_cells or [])],
            "ignition_cells": [list(c) for c in ignition_cells],
            "disclaimer": (
                "Bu hücre-otomat tabanlı yayılım gösterge niteliğindedir; "
                "tam bir CFD (hesaplamalı akışkanlar dinamiği) simülasyonu "
                "değildir ve kesin bir yangın mühendisliği raporunun yerini "
                "tutmaz."
            ),
        }
        self._handle(project_id).db.save_object(result_id, self._FIRE_SPREAD_KIND, result_data)
        return result_data

    def fire_spread_demo_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._FIRE_SPREAD_KIND:
            raise AppSessionError(f"yangın yayılım sonucu bulunamadı: {result_id}")
        return record.data

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz VIII — Enerji Kesintisi +
    # Mikroklima/Hava Kalitesi/Gürültü göstergeleri (Katman 4). Üç ayrı
    # motor (`climate_data.microclimate`, `climate_data.air_quality_estimate`,
    # `climate_data.noise_estimate`) tek bir "çevre göstergeleri" panelinde
    # birleştirilir - hiçbiri değiştirilmedi, yalnızca formdan gelen kaba
    # girdilerle çağrılıp tek bir JSON'da toplanır. Kesinti yayılımı
    # (`power_infrastructure.outage_propagation`) ayrı bir demo olarak,
    # kullanıcının verdiği basit trafo/bina bağlantı listesinden bir
    # `NavGraph` kurup BFS-bazlı erişilebilirlik farkını gösterir. -- #

    _ENVIRONMENT_KIND = "environment_indicators_result"
    _OUTAGE_KIND = "power_outage_result"

    def environment_indicators_run(
        self,
        project_id: str,
        *,
        average_building_height_m: float,
        average_street_width_m: float,
        building_footprint_ratio: float,
        canopy_coverage_ratio: float = 0.0,
        baseline_temperature_c: float | None = None,
        vehicles_per_hour: float | None = None,
        density_people_per_m2: float | None = None,
        road_segments: list[dict[str, Any]] | None = None,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Isı adası + hava kalitesi + gürültü göstergelerini tek turda
        hesaplar (Faz VIII). Hiçbiri gerçek CFD/ölçüm yerine geçmez -
        her alt-rapor kendi `disclaimer` alanını taşır."""
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)
        if not (0.0 <= building_footprint_ratio <= 1.0):
            raise AppSessionError("building_footprint_ratio 0-1 aralığında olmalı.")
        if not (0.0 <= canopy_coverage_ratio <= 1.0):
            raise AppSessionError("canopy_coverage_ratio 0-1 aralığında olmalı.")

        fabric = UrbanFabricSample(
            average_building_height_m=average_building_height_m,
            average_street_width_m=average_street_width_m,
            building_footprint_ratio=building_footprint_ratio,
            canopy_coverage_ratio=canopy_coverage_ratio,
        )
        baseline = None
        if baseline_temperature_c is not None:
            baseline = HourlyClimateSample(
                time_iso="manual",
                temperature_c=baseline_temperature_c,
                cloud_cover_pct=0.0,
                shortwave_radiation_wm2=0.0,
                direct_radiation_wm2=None,
                diffuse_radiation_wm2=None,
            )
        heat_report = estimate_heat_island_index(fabric, baseline=baseline)

        aq_reports: list[dict[str, Any]] = []
        for seg in road_segments or []:
            traffic = RoadSegmentTraffic(
                segment_id=str(seg.get("segment_id", "seg")),
                length_m=float(seg.get("length_m", 0.0)),
                vehicles_per_hour=float(seg.get("vehicles_per_hour", 0.0)),
            )
            aq_reports.append(estimate_network_air_quality([traffic])[0].to_dict())

        noise_report = estimate_noise(
            vehicles_per_hour=vehicles_per_hour,
            density_people_per_m2=density_people_per_m2,
        )

        result_id = f"env_{uuid.uuid4().hex[:12]}"
        result_data = {
            "result_id": result_id,
            "project_id": project_id,
            "heat_island": heat_report.to_dict(),
            "air_quality_segments": aq_reports,
            "noise": noise_report.to_dict(),
        }
        self._handle(project_id).db.save_object(result_id, self._ENVIRONMENT_KIND, result_data)
        return result_data

    def environment_indicators_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._ENVIRONMENT_KIND:
            raise AppSessionError(f"çevre göstergesi sonucu bulunamadı: {result_id}")
        return record.data

    def power_outage_demo_run(
        self,
        project_id: str,
        *,
        substation_ids: list[str],
        building_ids: list[str],
        edges: list[tuple[str, str]],
        failed_substation_ids: list[str],
        role: Role | None = None,
    ) -> dict[str, Any]:
        """`power_infrastructure.outage_propagation`'ın doğrudan bir web
        köprüsü - kullanıcı basit bir trafo/bina/hat listesi girer, hangi
        binaların artık hiçbir sağlam trafodan erişilemediği (BFS-bazlı
        erişilebilirlik farkı) hesaplanır. Yeni bir graf motoru icat
        edilmedi - `mobility.pathfinding.NavGraph` yeniden kullanılır."""
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)
        if not substation_ids:
            raise AppSessionError("en az bir substation_ids gerekli.")
        if not failed_substation_ids:
            raise AppSessionError("en az bir failed_substation_ids gerekli.")

        graph = build_power_network_graph(
            substation_ids,
            building_ids,
            [tuple(e) for e in edges],
        )
        engine = OutagePropagationEngine(
            graph,
            all_substation_ids=substation_ids,
            all_building_ids=building_ids,
        )
        report = engine.propagate(failed_substation_ids=failed_substation_ids)

        result_id = f"outage_{uuid.uuid4().hex[:12]}"
        result_data = {
            "result_id": result_id,
            "project_id": project_id,
            "failed_substation_ids": list(failed_substation_ids),
            "affected_building_ids": list(report.affected_building_ids),
            "still_powered_building_ids": list(report.still_powered_building_ids),
            "dark_corridor_speed_multiplier": DARK_CORRIDOR_SPEED_MULTIPLIER,
        }
        self._handle(project_id).db.save_object(result_id, self._OUTAGE_KIND, result_data)
        return result_data

    def power_outage_demo_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._OUTAGE_KIND:
            raise AppSessionError(f"kesinti sonucu bulunamadı: {result_id}")
        return record.data

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz IX — Katman 6: Etkinlik
    # Simülasyonu (`commerce_props.city_event_simulation`) + Ekonomik
    # Dayanıklılık / Toparlanma Eğrisi (`commerce_props.economic_resilience`).
    # İkisi de kalıcı bir motor durumu tutmaz - her çağrı taze bir rapor
    # üretip döner (Faz VII cascade demo'suyla aynı "her karar izlenebilir,
    # kara kutu değil" disiplini). -- #

    _CITY_EVENT_KIND = "city_event_result"
    _ECONOMIC_RESILIENCE_KIND = "economic_resilience_result"

    def city_event_demo_run(
        self,
        project_id: str,
        *,
        event_id: str,
        category: str,
        location_ref: str,
        expected_attendance: int,
        start_hour: float,
        duration_h: float,
        ramp_fraction: float = 0.15,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Konser/maç/festival/pazar gibi geçici yüksek-yoğunluk bir
        etkinliğin katılımcı-zaman eğrisini hesaplar ve Event Bus'a
        `CROWD_SURGE` olarak yayınlar (Faz IX / Katman 6.2)."""
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)
        try:
            cat = CityEventCategory(category)
        except ValueError as exc:
            valid = ", ".join(c.value for c in CityEventCategory)
            raise AppSessionError(f"geçersiz category: {category} (geçerli: {valid})") from exc
        if expected_attendance <= 0:
            raise AppSessionError("expected_attendance pozitif olmalı.")
        if duration_h <= 0:
            raise AppSessionError("duration_h pozitif olmalı.")

        profile = CityEventProfile(
            event_id=event_id,
            category=cat,
            location_ref=location_ref,
            expected_attendance=expected_attendance,
            start_hour=start_hour,
            duration_h=duration_h,
            ramp_fraction=ramp_fraction,
        )
        bus = EventSystem()
        simulator = CityEventSimulator(bus=bus)
        curve = simulator.trigger(profile, source=location_ref)

        result_id = f"cevt_{uuid.uuid4().hex[:12]}"
        result_data = {
            "result_id": result_id,
            "project_id": project_id,
            "event_id": event_id,
            "category": cat.value,
            "location_ref": location_ref,
            "peak_attendance": profile.expected_attendance,
            "curve": [{"hour": round(p.hour, 2), "attendance": p.attendance} for p in curve],
            "crowd_surge_events": len(bus.history()),
        }
        self._handle(project_id).db.save_object(result_id, self._CITY_EVENT_KIND, result_data)
        return result_data

    def city_event_demo_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._CITY_EVENT_KIND:
            raise AppSessionError(f"etkinlik sonucu bulunamadı: {result_id}")
        return record.data

    def economic_resilience_demo_run(
        self,
        project_id: str,
        *,
        area_id: str,
        risk_level: str,
        horizon_days: float | None = None,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Bir ticari bölgenin deprem-sonrası kapanma süresini
        (`RiskLevel` -> gösterge niteliğinde gün sayısı) ve toparlanma
        (lojistik S-eğrisi) eğrisini hesaplar (Faz IX / Katman 6.3)."""
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)
        from ..hazard_data.risk_scoring import RiskLevel

        try:
            level = RiskLevel(risk_level)
        except ValueError as exc:
            valid = ", ".join(r.value for r in RiskLevel)
            raise AppSessionError(f"geçersiz risk_level: {risk_level} (geçerli: {valid})") from exc

        bus = EventSystem()
        area = CommercialAreaResilience(
            area_id=area_id,
            risk_level=level,
            closure_days=closure_days_for_risk_level(level),
            disrupted_at=0.0,
        )
        report = build_resilience_curve(area, horizon_days=horizon_days)

        result_id = f"econ_{uuid.uuid4().hex[:12]}"
        result_data = {
            **report.to_dict(),
            "result_id": result_id,
            "project_id": project_id,
            "risk_level": level.value,
            "closure_days": area.closure_days,
        }
        self._handle(project_id).db.save_object(
            result_id, self._ECONOMIC_RESILIENCE_KIND, result_data
        )
        return result_data

    def economic_resilience_demo_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._ECONOMIC_RESILIENCE_KIND:
            raise AppSessionError(f"ekonomik dayanıklılık sonucu bulunamadı: {result_id}")
        return record.data

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz X — Katman 8.1 madde 3:
    # Bölgesel Yığılma Tespiti. `digital_twin.hierarchy.TwinHierarchy`
    # (değiştirilmedi) üzerinde parent/child ilişkisi kurulup
    # `mobility.city_scale_evacuation.regional_agent_density`/
    # `most_congested_region` (yeni bir motor değil, mevcut agregasyon
    # deseninin sarmalayıcısı) ile hangi bölgenin en yoğun tahliye-agent
    # sayısına sahip olduğu bulunur. Kalıcı durum tutulmaz - her çağrı
    # taze bir `TwinHierarchy` kurar. -- #

    _CONGESTION_KIND = "regional_congestion_result"

    def regional_congestion_demo_run(
        self,
        project_id: str,
        *,
        hierarchy_edges: list[dict[str, Any]],
        agent_counts_by_leaf: dict[str, int],
        candidate_region_ids: list[str],
        role: Role | None = None,
    ) -> dict[str, Any]:
        """`hierarchy_edges`: [{child, parent}] listesinden bir
        `TwinHierarchy` kurar, `candidate_region_ids` arasından en yoğun
        (en çok tahliye-agent'ı olan) bölgeyi ve her adayın kendi
        yoğunluğunu döner (Faz X / Katman 8.1 madde 3)."""
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)
        if not candidate_region_ids:
            raise AppSessionError("en az bir candidate_region_ids gerekli.")

        hierarchy = TwinHierarchy()
        for edge in hierarchy_edges:
            child = str(edge.get("child", ""))
            parent = edge.get("parent")
            if not child:
                continue
            hierarchy.add(child, str(parent) if parent else None)
        # Adayların da hiyerarşide (kök olarak) var olduğundan emin ol.
        for region_id in candidate_region_ids:
            hierarchy.add(region_id)

        per_region = {
            region_id: regional_agent_density(hierarchy, region_id, agent_counts_by_leaf)
            for region_id in candidate_region_ids
        }
        best_id, best_count = most_congested_region(
            hierarchy, candidate_region_ids, agent_counts_by_leaf
        )

        result_id = f"cong_{uuid.uuid4().hex[:12]}"
        result_data = {
            "result_id": result_id,
            "project_id": project_id,
            "per_region_agent_count": per_region,
            "most_congested_region_id": best_id,
            "most_congested_agent_count": best_count,
        }
        self._handle(project_id).db.save_object(result_id, self._CONGESTION_KIND, result_data)
        return result_data

    def regional_congestion_demo_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._CONGESTION_KIND:
            raise AppSessionError(f"bölgesel yığılma sonucu bulunamadı: {result_id}")
        return record.data

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz XI — Katman 9: Karar Destek
    # Motoru. Yeni bir simülasyon çalıştırmaz - Faz II panelinde zaten
    # kaydedilmiş iki `simulation_result` kaydını (öncesi/sonrası) yükleyip
    # `analysis_engine.decision_support`'un (değiştirilmedi) karşılaştırma +
    # kural-tabanlı öneri mantığını üzerlerine uygular ("before/after" ve
    # "otomatik öneri" maddeleri). -- #

    _SCENARIO_COMPARISON_KIND = "scenario_comparison_result"

    def scenario_comparison_demo_run(
        self,
        project_id: str,
        *,
        before_result_id: str,
        after_result_id: str,
        label: str = "tahliye senaryosu",
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Daha önce kaydedilmiş (Faz II panelinden) iki deprem tahliye
        sonucunu (`simulation_result`) yükleyip metriklerini karşılaştırır
        (before/after) ve varsa `after` sonucunun asansör-erişilebilirlik
        etkisine göre kural-tabanlı öneriler üretir (Faz XI / Katman 9
        madde 1+2)."""
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        handle = self._handle(project_id)

        before_record = handle.db.load_object(before_result_id)
        after_record = handle.db.load_object(after_result_id)
        if before_record is None or before_record.kind != self._SIMULATION_RESULT_KIND:
            raise AppSessionError(f"before_result_id bulunamadı: {before_result_id}")
        if after_record is None or after_record.kind != self._SIMULATION_RESULT_KIND:
            raise AppSessionError(f"after_result_id bulunamadı: {after_result_id}")
        before = before_record.data
        after = after_record.data

        comparisons = [
            ScenarioComparison(
                label=label,
                metric_name="evacuation_time_s",
                before_value=float(before["evacuation_time_s"]),
                after_value=float(after["evacuation_time_s"]),
            ).to_dict(),
            ScenarioComparison(
                label=label,
                metric_name="evacuated_count",
                before_value=float(before["evacuated_count"]),
                after_value=float(after["evacuated_count"]),
            ).to_dict(),
        ]
        if (
            before.get("bottleneck_peak_count") is not None
            and after.get("bottleneck_peak_count") is not None
        ):
            comparisons.append(
                ScenarioComparison(
                    label=label,
                    metric_name="bottleneck_peak_count",
                    before_value=float(before["bottleneck_peak_count"]),
                    after_value=float(after["bottleneck_peak_count"]),
                ).to_dict()
            )

        recommendations: list[dict[str, Any]] = []
        impact = after.get("elevator_accessibility_impact")
        if impact is not None:
            recs = RecommendationEngine.from_accessibility_impact(
                unreachable_room_count=int(impact.get("unreachable_room_count", 0)),
                room_graph_available=bool(impact.get("room_graph_available", False)),
            )
            recommendations = [
                {"issue": r.issue, "suggestions": r.suggestions, "severity": r.severity}
                for r in recs
            ]

        result_id = f"cmp_{uuid.uuid4().hex[:12]}"
        result_data = {
            "result_id": result_id,
            "project_id": project_id,
            "before_result_id": before_result_id,
            "after_result_id": after_result_id,
            "comparisons": comparisons,
            "recommendations": recommendations,
            "disclaimer": INDICATIVE_DISCLAIMER,
        }
        handle.db.save_object(result_id, self._SCENARIO_COMPARISON_KIND, result_data)
        return result_data

    def scenario_comparison_demo_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._SCENARIO_COMPARISON_KIND:
            raise AppSessionError(f"senaryo karşılaştırma sonucu bulunamadı: {result_id}")
        return record.data

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz VI — Katman 2.1: Sentetik
    # Nüfus. `population.synthetic_population.SyntheticPopulationGenerator`
    # (değiştirilmedi) doğrudan çağrılır - bina başına hane/birey üretip
    # yaş grubu + mobilite profili + günlük rutin tipi dağılımını
    # gösterir. Kalıcı bir nüfus deposu tutulmaz - her çağrı taze bir
    # üreteç kurar (seed'li, tekrarlanabilir). -- #

    _SYNTHETIC_POPULATION_KIND = "synthetic_population_result"

    def synthetic_population_demo_run(
        self,
        project_id: str,
        *,
        building_ref: str,
        household_count: int,
        avg_household_size: float = 2.6,
        seed: int | None = 42,
        role: Role | None = None,
    ) -> dict[str, Any]:
        """Bir bina için `household_count` adet sentetik hane üretir,
        yaş grubu / mobilite profili / günlük rutin tipi dağılımlarını
        özetler (Faz VI / Katman 2.1)."""
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)
        if household_count < 0:
            raise AppSessionError("household_count negatif olamaz.")
        if avg_household_size <= 0:
            raise AppSessionError("avg_household_size pozitif olmalı.")

        generator = SyntheticPopulationGenerator(
            avg_household_size=avg_household_size,
            seed=seed,
        )
        households = generator.generate_for_building(building_ref, household_count)
        individuals = generator.all_individuals(households)

        age_counts: dict[str, int] = {}
        routine_counts: dict[str, int] = {}
        mobility_counts: dict[str, int] = {}
        for ind in individuals:
            age_counts[ind.age_group.value] = age_counts.get(ind.age_group.value, 0) + 1
            routine_counts[ind.routine_type.value] = (
                routine_counts.get(ind.routine_type.value, 0) + 1
            )
            mobility_counts[ind.mobility_profile.value] = (
                mobility_counts.get(ind.mobility_profile.value, 0) + 1
            )

        result_id = f"pop_{uuid.uuid4().hex[:12]}"
        result_data = {
            "result_id": result_id,
            "project_id": project_id,
            "building_ref": building_ref,
            "household_count": len(households),
            "total_individuals": len(individuals),
            "households": [
                {
                    "household_id": hh.household_id,
                    "size": hh.size(),
                    "individuals": [
                        {
                            "individual_id": ind.individual_id,
                            "age_group": ind.age_group.value,
                            "mobility_profile": ind.mobility_profile.value,
                            "routine_type": ind.routine_type.value,
                        }
                        for ind in hh.individuals
                    ],
                }
                for hh in households
            ],
            "age_group_counts": age_counts,
            "routine_type_counts": routine_counts,
            "mobility_profile_counts": mobility_counts,
            "disclaimer": (
                "Gösterge niteliğindedir; TÜİK/WorldPop genel eğilimlerinin "
                "kaba bir yaklaşımıdır, belirli bir binanın gerçek "
                "sakinlerini temsil etmez, gerçek kişi verisi DEĞİLDİR."
            ),
        }
        self._handle(project_id).db.save_object(
            result_id,
            self._SYNTHETIC_POPULATION_KIND,
            result_data,
        )
        return result_data

    def synthetic_population_demo_result(
        self,
        project_id: str,
        result_id: str,
        *,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.VIEW_RESULT)
        handle = self._handle(project_id)
        record = handle.db.load_object(result_id)
        if record is None or record.kind != self._SYNTHETIC_POPULATION_KIND:
            raise AppSessionError(f"sentetik nüfus sonucu bulunamadı: {result_id}")
        return record.data

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz VII — Kademeli Etki (Cascade)
    # Katman 7.1/7.4: bir `HAZARD_STARTED` (deprem) olayı yayınlayıp
    # `CascadeEngine`'in (mevcut motor, hiç değiştirilmedi) DEFAULT_CASCADE_
    # RULES tablosuna göre hangi ikincil olayları (elektrik kesintisi/
    # yangın/yol hasarı/...) tetiklediğini tek bir istek-cevap turunda
    # gösterir - kalıcı bir simülasyon durumu tutulmaz (her çağrı taze bir
    # EventSystem + CascadeEngine kurar, roadmap'in "kara kutu değil, her
    # karar izlenebilir" ilkesiyle tutarlı triggered_log/skipped_log
    # doğrudan döner). -- #

    def cascade_demo_run(
        self,
        project_id: str,
        *,
        magnitude: float,
        epicenter_lat: float | None = None,
        epicenter_lon: float | None = None,
        seed: int = 42,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)
        bus = EventSystem()
        engine = CascadeEngine(bus=bus, rules=DEFAULT_CASCADE_RULES, seed=seed)
        engine.start()
        payload: dict[str, Any] = {"magnitude": magnitude}
        if epicenter_lat is not None:
            payload["lat"] = epicenter_lat
        if epicenter_lon is not None:
            payload["lon"] = epicenter_lon
        triggering_event = emit_city_event(
            bus,
            CityEventType.HAZARD_STARTED,
            source="web_panel_demo",
            **payload,
        )
        return {
            "triggering_event": {
                "name": triggering_event.name,
                "payload": triggering_event.payload,
            },
            "triggered": engine.triggered_log,
            "skipped": engine.skipped_log,
            "rule_count": len(DEFAULT_CASCADE_RULES),
            "disclaimer": (
                "Kural olasılıkları ayarlanabilir gösterge değerleridir "
                "(kalibre edilmiş bir afet-etki modeli değildir); "
                "yayınlanan olaylar gerçek bir enerji/trafik/yangın "
                "motorunu bu demoda tetiklemez, yalnızca Event Bus "
                "üzerindeki kademeli tetiklemeyi gösterir."
            ),
        }

    # -- Web arayüzü köprüsü: Roadmap V9 / Faz VII.5 — Acil Müdahale ------ #
    # `dispatch_nearest_unit()` (mevcut motor) çağrılır; küçük bir
    # tam-bağlı (complete graph) yol ağı bu demo için otomatik kurulur --
    # gerçek yol ağı verisi (OSM/`open_area_navgraph`) burada YOKTUR,
    # bu bilinçli bir basitleştirme olarak sonuçta açıkça belirtilir.

    def emergency_dispatch_demo_run(
        self,
        project_id: str,
        *,
        stations: list[dict[str, Any]],
        incident_x: float,
        incident_y: float,
        unit_type: str,
        role: Role | None = None,
    ) -> dict[str, Any]:
        require_scenario_permission(role, ScenarioAction.RUN_SCENARIO)
        self._handle(project_id)
        if not stations:
            raise AppSessionError("en az bir istasyon (stations) gerekli.")
        try:
            unit = EmergencyUnitType(unit_type)
        except ValueError as exc:
            raise AppSessionError(f"geçersiz unit_type: {unit_type}") from exc

        graph = NavGraph()
        graph.add_node("incident", Point2D(incident_x, incident_y))
        station_objs: list[EmergencyStation] = []
        for i, s in enumerate(stations):
            node_id = f"station_{i}"
            graph.add_node(node_id, Point2D(float(s["x"]), float(s["y"])))
            graph.add_edge(node_id, "incident")
            types = frozenset(EmergencyUnitType(t) for t in s.get("unit_types", [unit_type]))
            station_objs.append(
                EmergencyStation(
                    station_id=s.get("id", node_id),
                    node_id=node_id,
                    position=Point2D(float(s["x"]), float(s["y"])),
                    unit_types=types,
                )
            )
        # İstasyonlar birbirine de bağlı (tam-bağlı graf) - A*'ın gerçekten
        # "maliyete göre en yakın" seçimi yapabilmesi için (yıldız-topoloji
        # tek başına bunu zaten sağlıyor, ama tam-bağlı olması gerçek yol
        # ağına daha yakın bir yaklaşımdır).
        for i in range(len(station_objs)):
            for j in range(i + 1, len(station_objs)):
                graph.add_edge(f"station_{i}", f"station_{j}")

        result = dispatch_nearest_unit(station_objs, "incident", graph, unit_type=unit)
        if result is None:
            return {
                "found": False,
                "reason": f"'{unit_type}' tipini barındıran hiçbir istasyondan olay yerine ulaşılamadı.",
            }
        return {
            "found": True,
            "station_id": result.station.station_id,
            "unit_type": result.unit_type.value,
            "path_node_count": len(result.path_result.path),
            "path_cost_m": round(result.path_result.cost, 1),
            "estimated_response_seconds": round(result.estimated_response_seconds, 1),
            "disclaimer": (
                "Bu demo gerçek yol ağı verisi kullanmaz — istasyonlar/olay "
                "yeri arasında basitleştirilmiş tam-bağlı bir graf kurulur. "
                "Gerçek entegrasyon `mobility/open_area_navgraph.py` (Faz "
                "VI) ile OSM yol ağı üzerinden yapılmalıdır."
            ),
        }

    @staticmethod
    def _apply_behavior_distribution(
        agents: list[CrowdAgent],
        distribution: dict[str, float],
        seed: int | None,
    ) -> None:
        """`spawn_random_agents` her agent'ı `AgentBehavior.NORMAL` ile
        üretir; senaryonun `behavior_distribution`'ına göre (deterministik,
        seed'li) bazılarını `cautious/hurried/panic` olarak yeniden
        etiketler. Dağılım `{"normal": 1.0}` ise (varsayılan) hiçbir şey
        değişmez — mevcut davranış korunur."""
        if not distribution or set(distribution) == {"normal"}:
            return
        rng = random.Random(seed if seed is not None else 0)
        behaviors = list(distribution.keys())
        weights = list(distribution.values())
        for agent in agents:
            chosen = rng.choices(behaviors, weights=weights, k=1)[0]
            try:
                agent.behavior = AgentBehavior(chosen)
            except ValueError:
                continue

    # -- Web arayüzü köprüsü: Çoklu Kullanıcı / Giriş (collaboration.auth) --

    def register_user(self, username: str, password: str) -> dict[str, Any]:
        try:
            user = self._auth.register(username, password)
        except AuthError as exc:
            raise AppSessionError(str(exc)) from exc
        return {"user_id": user.user_id, "username": user.username}

    def login_user(self, username: str, password: str) -> dict[str, Any]:
        try:
            token = self._auth.login(username, password)
        except AuthError as exc:
            raise AppSessionError(str(exc)) from exc
        return {"token": token.token, "user_id": token.user_id, "expires_at": token.expires_at}

    def logout_user(self, token: str) -> dict[str, Any]:
        self._auth.logout(token)
        return {"logged_out": True}

    def whoami(self, token: str) -> dict[str, Any]:
        try:
            user = self._auth.authenticate_token(token)
        except AuthError as exc:
            raise AppSessionError(str(exc)) from exc
        return {"user_id": user.user_id, "username": user.username}

    def grant_project_role(
        self, project_id: str, token: str, target_user_id: str, role: str
    ) -> dict[str, Any]:
        """Yalnızca projede zaten en az OWNER yetkisine sahip bir
        kullanıcı, başka bir kullanıcıya rol atayabilir. Projenin hiç
        üyesi yoksa (ilk çağrı), token sahibi otomatik OWNER olarak
        eklenir - bir projenin "sahipsiz" kalmaması için."""
        try:
            requester = self._auth.authenticate_token(token)
            role_enum = Role[role.upper()]
        except AuthError as exc:
            raise AppSessionError(str(exc)) from exc
        except KeyError as exc:
            raise AppSessionError(f"geçersiz rol: {role}") from exc

        if not self._auth.members(project_id):
            self._auth.grant_role(project_id, requester.user_id, Role.OWNER)
        else:
            try:
                self._auth.require_role(project_id, requester.user_id, at_least=Role.OWNER)
            except AuthError as exc:
                raise AppSessionError(str(exc)) from exc

        membership = self._auth.grant_role(project_id, target_user_id, role_enum)
        return {"project_id": project_id, "user_id": target_user_id, "role": membership.role.name}

    def project_members(self, project_id: str) -> dict[str, Any]:
        members = self._auth.members(project_id)
        return {
            "members": [{"user_id": m.user_id, "role": m.role.name} for m in members],
        }

    # -- Web arayüzü köprüsü: AI İç Mekan / Çevre Üretimi (ai_reconstruction) --

    def generate_interior_layout(
        self,
        project_id: str,
        key: str,
        *,
        n_variants: int = 5,
        min_room_size: float = 3.0,
    ) -> dict[str, Any]:
        """`AIInteriorLayout`'u binanın zemin ayak izi (footprint) üzerinde
        çalıştırır; `n_variants` alternatif üretip en çeşitli (diversity
        skoru en yüksek) planı seçer. Diğer tüm alternatifler de dönüşte
        yer alır (kullanıcı seçebilsin diye)."""
        entry = self._entry(project_id, key)
        layout = AIInteriorLayout()
        variants = layout.generate_alternatives(
            entry.building.footprint.polygon,
            building_type=entry.building.building_type.value
            if hasattr(entry.building.building_type, "value")
            else str(entry.building.building_type),
            min_room_size=min_room_size,
            n_variants=n_variants,
        )
        best = layout.best_variant(variants)
        return {
            "best_seed": best.seed,
            "best_diversity_score": round(best.diversity_score, 3),
            "best_rooms": [
                {"room_type": r.room_type, "area_m2": round(r.area_m2, 1)} for r in best.rooms
            ],
            "alternatives": [
                {
                    "seed": v.seed,
                    "diversity_score": round(v.diversity_score, 3),
                    "room_count": len(v.rooms),
                }
                for v in variants
            ],
        }

    def generate_environment(
        self,
        project_id: str,
        key: str,
        *,
        margin_m: float = 15.0,
        min_setback_m: float = 1.5,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """`AIEnvironmentGenerator`'ı binanın footprint'i etrafındaki boş
        alana çevre objesi (ağaç, bank, lamba, direk vb.) yerleştirmek
        için çalıştırır."""
        entry = self._entry(project_id, key)
        generator = AIEnvironmentGenerator(seed=seed)
        objects = generator.generate(
            entry.building.footprint.polygon,
            margin_m=margin_m,
            min_setback_m=min_setback_m,
        )
        counts: dict[str, int] = {}
        for obj in objects:
            counts[obj.object_type.value] = counts.get(obj.object_type.value, 0) + 1
        return {
            "count": len(objects),
            "counts_by_type": counts,
            "objects": [
                {
                    "type": o.object_type.value,
                    "x": round(o.position.x, 2),
                    "y": round(o.position.y, 2),
                    "rotation_deg": round(o.rotation_deg, 1),
                    "scale": round(o.scale, 2),
                }
                for o in objects
            ],
        }

    # -- Faz 4.1: AI ayarları paneli (sağlayıcı seçimi, çalışma-zamanı) ---- #

    _AI_ALLOWED_BACKENDS = ("gguf", "openai", "anthropic")

    def set_ai_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Arayüzden gelen sağlayıcı seçimini bellekte saklar (diske
        yazılmaz). `llm_providers.create_provider_from_config` ile aynı
        şemayı kullanır; burada yalnızca *doğrulama* yapılır — gerçek
        sağlayıcı nesnesi her çağrıda taze üretilir, böylece anahtar
        bellekte tek bir yerde (bu sözlükte) durur."""
        backend = str(config.get("backend", "")).strip().lower()
        if backend not in self._AI_ALLOWED_BACKENDS:
            raise AppSessionError(
                f"Bilinmeyen AI backend: {backend!r} "
                f"({'|'.join(self._AI_ALLOWED_BACKENDS)} olmali)."
            )
        if backend == "gguf" and not config.get("model_path"):
            raise AppSessionError("gguf icin model_path zorunlu.")
        if backend in ("openai", "anthropic") and not config.get("api_key"):
            raise AppSessionError(f"{backend} icin api_key zorunlu.")
        self._ai_config = dict(config)
        return self.get_ai_config()

    def get_ai_config(self) -> dict[str, Any]:
        """Anahtar/model yolunu ASLA düz metin döndürmez — yalnızca
        maskelenmiş bir özet (arayüzün "şu an X yapılandırılı" göstermesi
        için)."""
        if self._ai_config is None:
            return {"configured": False, "backend": None}
        cfg = self._ai_config
        backend = cfg.get("backend")
        out: dict[str, Any] = {"configured": True, "backend": backend}
        if backend == "gguf":
            out["model_path"] = cfg.get("model_path", "")
            out["n_gpu_layers"] = cfg.get("n_gpu_layers", 0)
        else:
            api_key = str(cfg.get("api_key", ""))
            out["api_key_masked"] = (
                ("*" * max(len(api_key) - 4, 0)) + api_key[-4:] if api_key else ""
            )
            out["model"] = cfg.get("model", "")
            if backend == "openai":
                out["base_url"] = cfg.get("base_url", "https://api.openai.com/v1")
        return out

    def clear_ai_config(self) -> None:
        self._ai_config = None

    def test_ai_connection(self) -> dict[str, Any]:
        """Faz 4.1 — "Bağlantı testi" butonu: yapılandırılmış sağlayıcıya
        gerçek, küçük bir tamamlama (completion) isteği gönderir ve
        başarılı/başarısız olduğunu döner. Anahtar/model yolu hatasız da
        olsa yanıt gövdesine hiç dahil edilmez."""
        from ..ai_assistant.llm_providers import (
            InvalidProviderConfigError,
            LLMCallError,
            ProviderUnavailableError,
            create_provider_from_config,
        )

        if self._ai_config is None:
            return {"ok": False, "error": "Once bir AI saglayicisi yapilandirin."}
        try:
            provider = create_provider_from_config(self._ai_config)
            provider.complete("ping", system="Yalnizca 'ok' yaz.")
        except InvalidProviderConfigError as exc:
            return {"ok": False, "error": f"Gecersiz yapilandirma: {exc}"}
        except ProviderUnavailableError as exc:
            return {"ok": False, "error": f"Saglayici kullanilamiyor: {exc}"}
        except LLMCallError as exc:
            return {"ok": False, "error": f"Cagri basarisiz: {exc}"}
        except Exception as exc:  # pragma: no cover - beklenmeyen ag/ortam hatasi
            return {"ok": False, "error": f"Beklenmeyen hata: {exc}"}
        return {"ok": True, "backend": self._ai_config.get("backend")}

    # -- Faz C5 (offline mod, A4) ---------------------------------------- #

    def offline_download_region(
        self,
        *,
        min_lat: float,
        min_lon: float,
        max_lat: float,
        max_lon: float,
        zoom_min: int,
        zoom_max: int,
        url_template: str,
        region_name: str = "offline_region",
    ) -> dict[str, Any]:
        """A4'ün "bir bölgeyi offline için indir" kabul kriteri: seçili
        bbox + zoom aralığındaki tile'ları indirip diskteki ortak tile
        önbelleğine yazar. `url_template` istemcinin seçtiği basemap'e
        göre değişir (ör. OSM standart, OpenTopoMap...) — sunucu hangi
        basemap'in indirileceğine karışmaz, yalnızca A3'te tanımlı
        katmanlardan biri olması beklenir."""
        result: TileDownloadResult = download_bbox(
            self._offline_cache,
            min_lat=min_lat,
            min_lon=min_lon,
            max_lat=max_lat,
            max_lon=max_lon,
            zoom_min=zoom_min,
            zoom_max=zoom_max,
            url_template=url_template,
            region_name=region_name,
        )
        return {
            "requested": result.requested,
            "downloaded": result.downloaded,
            "already_cached": result.already_cached,
            "failed": result.failed,
            "success_ratio": result.success_ratio(),
        }

    def offline_cache_stats(self) -> dict[str, Any]:
        """B5'in "istatistik özeti" ilkesiyle tutarlı: önbellekteki toplam
        tile/byte sayısı + daha önce indirilmiş bölgelerin defteri (A4'ün
        "offline paket" kabul kriterinin doğrulanabilirliği)."""
        return {
            "tile_count": self._offline_cache.tile_count(),
            "total_bytes": self._offline_cache.total_bytes(),
            "regions": self._offline_cache.load_manifest(),
        }

    def offline_get_tile(self, z: int, x: int, y: int) -> bytes | None:
        """A4: "Offline modda Leaflet, uzak tile URL'i yerine yerel bir
        HTTP endpoint'ten tile'ları çeker." — bu, o yerel endpoint'in
        arkasındaki okuma fonksiyonudur (bkz. `app_shell/api.py` ve
        `server.py`'nin binary yanıt desteği)."""
        return self._offline_cache.read_tile(z, x, y)

    def offline_index_collection(
        self, project_id: str, category: str, features_geojson: dict[str, Any]
    ) -> dict[str, Any]:
        """A4: "Adres arama (Nominatim) offline alternatifi... önceden
        import edilmiş OSM verisinden çıkarılan yer adları." İstemci,
        C2 (`fetch_category_features`) ile çektiği bir kategori sonucunu
        (GeoJSON `FeatureCollection` sözlüğü) buraya gönderir; sunucu
        `name` tag'i olan feature'ları kalıcı yerel indekse ekler."""
        from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection

        raw_features = features_geojson.get("features", [])
        collection = GeoFeatureCollection(
            features=[
                GeoFeature(
                    geometry_type=f.get("geometry", {}).get("type", ""),
                    coordinates=f.get("geometry", {}).get("coordinates"),
                    properties=f.get("properties", {}) or {},
                )
                for f in raw_features
                if f.get("geometry")
            ]
        )
        new_index = build_index_from_collection(collection, category=category)
        self._offline_place_index.entries.extend(new_index.entries)
        self._offline_place_index.save(self._offline_place_index_path)
        return {"indexed": len(new_index), "total_indexed": len(self._offline_place_index)}

    def offline_search_places(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """A4'ün yerel isim->koordinat aramasının dışa açık ucu."""
        return [
            {
                "name": e.name,
                "lat": e.lat,
                "lon": e.lon,
                "category": e.category,
                "feature_type": e.feature_type,
            }
            for e in self._offline_place_index.search(query, limit=limit)
        ]
