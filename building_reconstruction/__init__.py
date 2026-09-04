"""
Building Reconstruction (Phase 3)
==================================

Roadmap Phase 3 - "BUILDING RECONSTRUCTION". Haritadaki bina polygon'undan
(footprint) tam bir `Building` nesnesi (kat planları, çatı, cephe, dolaşım
elemanları) üretir.

Alt modüller:
    footprint_parser        - GeoFeature -> Footprint
    procedural_generator     - Footprint -> Building (orkestratör)
    roof_generator            - 11 çatı tipi -> Mesh3D
    facade_generator          - malzeme + pencere paterni -> Facade
    room_generator             - BSP + adjacency graph -> Room listesi
    building_elements          - Window/Balcony/Stair/Elevator/Corridor/Door

Teknik spesifikasyon: `docs/PHASE_SPECS.md` (Phase 3 bölümü).
"""

from __future__ import annotations

from .footprint_parser import Footprint, FootprintParser, RoofTypeGuess, FootprintShape
from .roof_generator import RoofGenerator, RoofType, RoofDetailGenerator, MultiPartRoofGenerator
from .terrain_integration import (
    TerrainFoundationGenerator, TerrainIntersectionReport, RetainingWallGenerator,
)
from .facade_generator import Facade, FacadeGenerator, FacadeMaterial, FacadeComplianceReport
from .room_generator import Room, RoomGenerator, RoomType, RoomComplianceReport, RoomComplianceIssue
from .building_elements import (
    Balcony, BalconyGenerator,
    BayWindow, BayWindowGenerator,
    CorridorGenerator,
    Door, DoorGenerator, DoorType, DOOR_TYPE_DEFAULTS,
    ElevatorCore, ElevatorCoreGenerator,
    EntranceCanopy, EntranceCanopyGenerator,
    Stair, StairGenerator,
    WindowGenerator, WindowPlacement, WindowType, WINDOW_TYPE_DEFAULTS,
)
from .procedural_generator import (
    Building, BuildingType, BuildingTypeRule, BuildingTypeRules,
    Floor, ProceduralBuildingGenerator,
)
from .regulations import (
    RegulationProfile,
    default_profile as default_regulation_profile,
    strict_reference_profile,
    historic_zone_profile,
    get_profile as get_regulation_profile,
    register_profile as register_regulation_profile,
    available_profiles as available_regulation_profiles,
)
from .structural_validation import (
    IssueSeverity,
    StructuralIssue,
    StructuralValidationReport,
    validate_building,
)
from .permit_precheck import (
    PermitCheckItem,
    PermitPrecheckReport,
    PermitVerdict,
    precheck_building,
)
__all__ = [
    "Footprint", "FootprintParser", "RoofTypeGuess", "FootprintShape",
    "RoofGenerator", "RoofType", "RoofDetailGenerator", "MultiPartRoofGenerator",
    "TerrainFoundationGenerator", "TerrainIntersectionReport", "RetainingWallGenerator",
    "Facade", "FacadeGenerator", "FacadeMaterial", "FacadeComplianceReport",
    "Room", "RoomGenerator", "RoomType", "RoomComplianceReport", "RoomComplianceIssue",
    "Balcony", "BalconyGenerator",
    "BayWindow", "BayWindowGenerator",
    "CorridorGenerator",
    "Door", "DoorGenerator", "DoorType", "DOOR_TYPE_DEFAULTS",
    "ElevatorCore", "ElevatorCoreGenerator",
    "EntranceCanopy", "EntranceCanopyGenerator",
    "Stair", "StairGenerator",
    "WindowGenerator", "WindowPlacement", "WindowType", "WINDOW_TYPE_DEFAULTS",
    "Building", "BuildingType", "BuildingTypeRule", "BuildingTypeRules",
    "Floor", "ProceduralBuildingGenerator",
    "RegulationProfile", "default_regulation_profile", "strict_reference_profile",
    "historic_zone_profile",
    "get_regulation_profile", "register_regulation_profile", "available_regulation_profiles",
    "IssueSeverity", "StructuralIssue", "StructuralValidationReport", "validate_building",
    "PermitCheckItem", "PermitPrecheckReport", "PermitVerdict", "precheck_building",
]
# Not: `redevelopment` (ROADMAP_V9 Faz X / Katman 1) burada paket-seviyesi
# eager import edilmiyor — `extensibility` paketinin tam __init__ zinciri
# (ai_assistant -> editor -> building_reconstruction) ile döngüsel import
# riski taşıyor (bkz. mevcut `editor`/`ai_assistant`/`data_engine` zinciri).
# Modül `harita.building_reconstruction.redevelopment` yolundan doğrudan
# içe aktarılabilir (tıpkı testlerin yaptığı gibi); davranışta bir kayıp
# yok, yalnızca paket __init__'inin "yüzeysel" (lazy) tutulması.
