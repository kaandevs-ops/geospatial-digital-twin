"""Phase 13 - Performance Engine: scheduling, culling/LOD, streaming, profiling."""

from .task_scheduler import TaskScheduler, AsyncAssetLoader
from .culling import FrustumCulling, OcclusionCulling, LODLevel, LODManager, SpatialPartitioning
from .streaming import (
    IncrementalMeshGenerator,
    StreamingDiff,
    GeometryStreaming,
    SceneStreaming,
    AtlasEntry,
    TextureAtlas,
    InstancingBatch,
    DynamicBatcher,
)
from .profiler import (
    ProfileSample,
    CPUProfiler,
    MemoryProfiler,
    FrameStats,
    GPUProfiler,
    CyclicDependencyError,
    AssetDependencyManager,
)
from .scene_scale_benchmark import (
    BuildingDescriptor,
    build_city_catalog,
    StreamingSceneCache,
    ScaleBenchmarkResult,
    benchmark_resident_memory_scaling,
    assert_sub_linear_growth,
)

# ROADMAP_V7.md Faz C4 (LOD/instancing/performans), 1. dilim: OSM
# nokta-prop köprülerini (vegetation/street_furniture/religious_structures/
# sport_recreation/commerce_props/power_infrastructure) mevcut
# `InstanceMeshBaker`/`DrawCallEstimator` (Roadmap V5 M1.2, değiştirilmedi)
# altyapısına bağlayan "son kilometre" köprüsü.
from .scene_instancing import (
    DEFAULT_TREE_HEIGHT_BUCKET_M,
    InstanceGroup,
    SceneInstancingResult,
    instancing_groups_for_street_furniture,
    instancing_groups_for_religious_structures,
    instancing_groups_for_playgrounds,
    instancing_groups_for_outdoor_seating,
    instancing_groups_for_communication_towers,
    instancing_groups_for_vegetation,
    build_scene_instancing_result,
)

# ROADMAP_V7.md Faz C4 (LOD/instancing/performans), 2. dilim: mesafe bazlı
# LOD seçiminin (mevcut `LODManager`, Faz 13, degistirilmedi) scene_instancing
# gruplarina baglanmasi + B3'un "1000+ karisik feature" kabul kriterini
# olcen sentetik sahne benchmark'i.
from .scene_lod import (
    IMPOSTOR_TRIANGLE_FACTOR,
    DEFAULT_FULL_DETAIL_DISTANCE_M,
    DEFAULT_IMPOSTOR_DISTANCE_M,
    FULL,
    IMPOSTOR,
    CULLED,
    FRUSTUM_CULLED,
    OCCLUDED,
    building_occluder_aabbs,
    default_instance_lod_manager,
    build_cross_billboard_impostor,
    build_impostor_texture,
    decode_impostor_texture_data_uri,
    impostor_material_for,
    LODInstanceGroup,
    LODAwareSceneResult,
    apply_lod_to_group,
    apply_scene_lod,
)
from .mixed_scene_benchmark import (
    DEFAULT_MIXED_FEATURE_COUNT,
    MixedSceneBenchmarkResult,
    generate_synthetic_mixed_scene,
    run_mixed_scene_benchmark,
)

__all__ = [
    "TaskScheduler",
    "AsyncAssetLoader",
    "FrustumCulling",
    "OcclusionCulling",
    "LODLevel",
    "LODManager",
    "SpatialPartitioning",
    "IncrementalMeshGenerator",
    "StreamingDiff",
    "GeometryStreaming",
    "SceneStreaming",
    "AtlasEntry",
    "TextureAtlas",
    "InstancingBatch",
    "DynamicBatcher",
    "ProfileSample",
    "CPUProfiler",
    "MemoryProfiler",
    "FrameStats",
    "GPUProfiler",
    "CyclicDependencyError",
    "AssetDependencyManager",
    "BuildingDescriptor",
    "build_city_catalog",
    "StreamingSceneCache",
    "ScaleBenchmarkResult",
    "benchmark_resident_memory_scaling",
    "assert_sub_linear_growth",
    "DEFAULT_TREE_HEIGHT_BUCKET_M",
    "InstanceGroup",
    "SceneInstancingResult",
    "instancing_groups_for_street_furniture",
    "instancing_groups_for_religious_structures",
    "instancing_groups_for_playgrounds",
    "instancing_groups_for_outdoor_seating",
    "instancing_groups_for_communication_towers",
    "instancing_groups_for_vegetation",
    "build_scene_instancing_result",
    "IMPOSTOR_TRIANGLE_FACTOR",
    "DEFAULT_FULL_DETAIL_DISTANCE_M",
    "DEFAULT_IMPOSTOR_DISTANCE_M",
    "FULL",
    "IMPOSTOR",
    "CULLED",
    "FRUSTUM_CULLED",
    "OCCLUDED",
    "building_occluder_aabbs",
    "default_instance_lod_manager",
    "build_cross_billboard_impostor",
    "build_impostor_texture",
    "decode_impostor_texture_data_uri",
    "impostor_material_for",
    "LODInstanceGroup",
    "LODAwareSceneResult",
    "apply_lod_to_group",
    "apply_scene_lod",
    "DEFAULT_MIXED_FEATURE_COUNT",
    "MixedSceneBenchmarkResult",
    "generate_synthetic_mixed_scene",
    "run_mixed_scene_benchmark",
]
