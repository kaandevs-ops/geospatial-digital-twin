"""
Render Engine (Phase 15)
==========================

Roadmap V2 - Track B, Faz 15. Platformun en kritik boşluğunu kapatır: önceki
14 fazın ürettiği veri/geometriyi gerçek zamanlı olarak GÖRÜNÜR kılmak.

İki parçadan oluşur:
    1. `scene_bridge` (bu paket, Python/stdlib-only): Mesh3D + PBRMaterial +
       ışıklar -> JSON `Scene` dönüşümü.
    2. `viewer/index.html` (bağımsız, tek dosya): WebGL2 tabanlı, kurulum
       gerektirmeyen tarayıcı içi 3D görüntüleyici. `Scene` JSON'unu okuyup
       orbit-kamera ile PBR-lite (Lambert + Blinn-Phong specular) shading
       kullanarak ekrana çizer; Phase 2 `lighting` çıktısını (güneş yönü/
       rengi) doğrudan tüketir.

Kullanım:
    from harita.render_engine import Scene, scene_from_meshes

    scene = scene_from_meshes([mesh1, mesh2], materials=[mat1, mat2])
    scene.write("scene.json")
    # sonra viewer/index.html içinde "Sahne Yükle" ile scene.json seçilir.
"""

from .animation_export import (
    AGENT_STATE_COLORS,
    DEFAULT_AGENT_RADIUS_PX,
    AnimationExportError,
    ExportBounds,
    compute_export_bounds,
    export_keyframes_gif,
    export_keyframes_svg_sequence,
    render_keyframe_svg,
)
from .scene_bridge import (
    DEFAULT_LOD_DISTANCES,
    # Faz D2 - Render Engine <-> Performance Köprüsü
    DEFAULT_LOD_RATIOS,
    SCENE_SCHEMA_VERSION,
    Scene,
    SceneLight,
    SceneNode,
    SceneSky,
    ambient_light_to_scene_light,
    apply_light_space_matrix,
    compute_light_space_matrix,
    hdr_sky_to_scene_sky,
    node_world_center,
    scene_from_meshes,
    select_lod_for_distance,
    sun_light_to_scene_light,
    total_triangle_count_for_camera,
    total_triangle_count_full_detail,
    visible_node_names,
)

__all__ = [
    "Scene",
    "SceneNode",
    "SceneLight",
    "SceneSky",
    "SCENE_SCHEMA_VERSION",
    "scene_from_meshes",
    "sun_light_to_scene_light",
    "ambient_light_to_scene_light",
    "hdr_sky_to_scene_sky",
    # Faz E1 - Render Engine: gölge haritası / IBL / post-processing pipeline
    "compute_light_space_matrix",
    "apply_light_space_matrix",
    "DEFAULT_LOD_RATIOS",
    "DEFAULT_LOD_DISTANCES",
    "select_lod_for_distance",
    "node_world_center",
    "total_triangle_count_for_camera",
    "total_triangle_count_full_detail",
    "visible_node_names",
    "AGENT_STATE_COLORS",
    "DEFAULT_AGENT_RADIUS_PX",
    "AnimationExportError",
    "ExportBounds",
    "compute_export_bounds",
    "render_keyframe_svg",
    "export_keyframes_svg_sequence",
    "export_keyframes_gif",
]
