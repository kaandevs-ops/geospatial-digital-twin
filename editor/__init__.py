"""
Editor (Phase 8)
================

Roadmap Phase 8 - "EDITOR": kendi Blender-benzeri düzenleyicin.

Alt modüller:
    commands          - EditorCommand, UndoRedoStack, CommandGroup (undo/redo çekirdeği)
    object_editor       - SceneNode/Prefab hiyerarşisi + move/rotate/scale/align/mirror/snap/duplicate/group
    terrain_editor       - Brush tabanlı raise/lower/flatten/smooth/noise/paint (HeightmapGrid üzerinde)
    road_editor           - Catmull-Rom spline tabanlı yol editörü
    building_editor        - kat ekle/sil, çatı/cephe değiştir, kapı/pencere ekle-sil
    gizmo                 - Roadmap V3/D5: translate/rotate/scale gizmo matematiği (ray-tabanlı)
    input_bindings        - Roadmap V3/D5: mouse/klavye event'lerini editör komutlarına bağlayan katman

Teknik spesifikasyon: `docs/PHASE_SPECS.md` (Phase 8 bölümü).
"""

from __future__ import annotations

from .building_editor import BuildingEditor
from .commands import CommandGroup, EditorCommand, FunctionCommand, UndoRedoStack
from .gizmo import AXES, Ray, RotateGizmo, ScaleGizmo, TranslateGizmo
from .input_bindings import (
    GizmoInputSession,
    GizmoMode,
    KeyBindingRegistry,
    MouseDownEvent,
    MouseMoveEvent,
    MouseUpEvent,
)
from .object_editor import ObjectEditor, Prefab, PrefabLibrary, SceneNode, Vec3
from .osm_bridge import (
    InfrastructureResult,
    generate_infrastructure_for_collection,
    mesh_for_road,
    mesh_for_water_area,
    road_from_linestring_feature,
    waterway_from_linestring_feature,
)
from .road_editor import Road, RoadEditor, catmull_rom_point, catmull_rom_spline
from .terrain_editor import Brush, TerrainEditor, TerrainPaintLayer

__all__ = [
    "EditorCommand",
    "FunctionCommand",
    "CommandGroup",
    "UndoRedoStack",
    "SceneNode",
    "Vec3",
    "Prefab",
    "PrefabLibrary",
    "ObjectEditor",
    "Brush",
    "TerrainPaintLayer",
    "TerrainEditor",
    "Road",
    "RoadEditor",
    "catmull_rom_spline",
    "catmull_rom_point",
    "BuildingEditor",
    "AXES",
    "Ray",
    "TranslateGizmo",
    "RotateGizmo",
    "ScaleGizmo",
    "GizmoInputSession",
    "GizmoMode",
    "KeyBindingRegistry",
    "MouseDownEvent",
    "MouseMoveEvent",
    "MouseUpEvent",
    "InfrastructureResult",
    "generate_infrastructure_for_collection",
    "mesh_for_road",
    "mesh_for_water_area",
    "road_from_linestring_feature",
    "waterway_from_linestring_feature",
]
