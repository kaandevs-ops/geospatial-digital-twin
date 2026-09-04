"""
Visualization (Phase 9)
=========================

Roadmap Phase 9 - "VISUALIZATION". Render-agnostic sahne/pass tanımları +
kamera state machine + Section View / X-Ray / Explosion View.

Alt modüller:
    render_passes     - RenderPassType, RenderPass, RenderPipeline (PBR/HDR/SSAO/...)
    camera_rig         - Camera, CameraMode, CameraRig, CinematicKeyframe
    section_view        - SectionPlane, SectionView (mesh'i düzlemle kesme)
    xray                 - XRayState, occlusion_ratio
    explosion_view        - FloorBand, ExplodedFloor, ExplosionView

Teknik spesifikasyon: `docs/PHASE_SPECS.md` (Phase 9 bölümü).
"""

from __future__ import annotations

from .render_passes import RenderPass, RenderPassType, RenderPipeline
from .camera_rig import Camera, CameraMode, CameraRig, CinematicKeyframe
from .section_view import SectionPlane, SectionView
from .xray import XRayState, occlusion_ratio
from .explosion_view import ExplodedFloor, ExplosionView, FloorBand, floor_bands_from_heights
from .heatmap_overlay import (
    HeatmapCell,
    StationHeatmapCell,
    crowd_heatmap_overlay,
    crowd_heatmap_overlay_from_agents,
    station_heatmap_overlay,
)

__all__ = [
    "RenderPass", "RenderPassType", "RenderPipeline",
    "Camera", "CameraMode", "CameraRig", "CinematicKeyframe",
    "SectionPlane", "SectionView",
    "XRayState", "occlusion_ratio",
    "ExplodedFloor", "ExplosionView", "FloorBand", "floor_bands_from_heights",
    "HeatmapCell", "StationHeatmapCell", "crowd_heatmap_overlay",
    "crowd_heatmap_overlay_from_agents", "station_heatmap_overlay",
]
