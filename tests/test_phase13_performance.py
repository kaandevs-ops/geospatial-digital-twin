"""Phase 13 (Performance Engine) için birim testleri."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.data_engine.spatial_index import AABB3D
from harita.mesh_engine import Vertex3D
from harita.performance import (
    AssetDependencyManager,
    AsyncAssetLoader,
    CPUProfiler,
    CyclicDependencyError,
    DynamicBatcher,
    FrustumCulling,
    GeometryStreaming,
    GPUProfiler,
    IncrementalMeshGenerator,
    InstancingBatch,
    LODLevel,
    LODManager,
    MemoryProfiler,
    OcclusionCulling,
    SpatialPartitioning,
    TaskScheduler,
    TextureAtlas,
)
from harita.visualization.camera_rig import Camera

# ============================================================================ #
# task_scheduler.py
# ============================================================================ #


class TestTaskScheduler:
    def test_runs_task_and_returns_result(self):
        scheduler = TaskScheduler(max_workers=2)
        future = scheduler.submit(lambda: 2 + 2)
        assert future.result(timeout=2) == 4
        scheduler.shutdown()

    def test_priority_ordering_when_single_worker(self):
        scheduler = TaskScheduler(max_workers=1)
        # ilk görevi işgal ederek kuyruğu doldurmadan önce worker'ı meşgul et
        gate_started = []

        def blocker():
            gate_started.append(True)
            time.sleep(0.05)
            return "blocker"

        order: list[str] = []
        f_blocker = scheduler.submit(blocker, priority=5)
        while not gate_started:
            time.sleep(0.001)

        f_low = scheduler.submit(lambda: order.append("low") or "low", priority=10)
        f_high = scheduler.submit(lambda: order.append("high") or "high", priority=0)

        f_blocker.result(timeout=2)
        f_low.result(timeout=2)
        f_high.result(timeout=2)
        assert order == ["high", "low"]
        scheduler.shutdown()

    def test_exception_propagates_via_future(self):
        scheduler = TaskScheduler(max_workers=1)

        def boom():
            raise ValueError("kaboom")

        future = scheduler.submit(boom)
        with pytest.raises(ValueError):
            future.result(timeout=2)
        scheduler.shutdown()


class TestAsyncAssetLoader:
    def test_loads_and_caches(self):
        scheduler = TaskScheduler(max_workers=2)
        calls = {"n": 0}

        def loader(key):
            calls["n"] += 1
            return f"data:{key}"

        asset_loader = AsyncAssetLoader(scheduler, loader)
        f1 = asset_loader.load("tile_1")
        assert f1.result(timeout=2) == "data:tile_1"
        assert asset_loader.is_cached("tile_1")

        f2 = asset_loader.load("tile_1")
        assert f2.result(timeout=2) == "data:tile_1"
        assert calls["n"] == 1  # cache hit, tekrar yüklenmedi
        scheduler.shutdown()

    def test_invalidate_forces_reload(self):
        scheduler = TaskScheduler(max_workers=2)
        calls = {"n": 0}

        def loader(key):
            calls["n"] += 1
            return calls["n"]

        asset_loader = AsyncAssetLoader(scheduler, loader)
        asset_loader.load("x").result(timeout=2)
        asset_loader.invalidate("x")
        second = asset_loader.load("x").result(timeout=2)
        assert second == 2
        scheduler.shutdown()


# ============================================================================ #
# culling.py
# ============================================================================ #


class TestFrustumCulling:
    def test_object_in_front_is_visible(self):
        camera = Camera(position=(0, 0, 0), target=(0, 0, -1), fov_deg=90.0)
        culler = FrustumCulling(aspect=1.0, near=0.01, far=100.0)
        box = AABB3D(-1, -1, -6, 1, 1, -4)
        assert culler.is_visible(camera, box)

    def test_object_behind_camera_not_visible(self):
        camera = Camera(position=(0, 0, 0), target=(0, 0, -1), fov_deg=90.0)
        culler = FrustumCulling(aspect=1.0, near=0.01, far=100.0)
        box = AABB3D(-1, -1, 4, 1, 1, 6)
        assert not culler.is_visible(camera, box)

    def test_object_far_outside_range_not_visible(self):
        camera = Camera(position=(0, 0, 0), target=(0, 0, -1), fov_deg=60.0)
        culler = FrustumCulling(aspect=1.0, near=0.01, far=10.0)
        box = AABB3D(-1, -1, -1000, 1, 1, -998)
        assert not culler.is_visible(camera, box)

    def test_cull_filters_dict(self):
        camera = Camera(position=(0, 0, 0), target=(0, 0, -1), fov_deg=90.0)
        culler = FrustumCulling(aspect=1.0, near=0.01, far=100.0)
        boxes = {
            "front": AABB3D(-1, -1, -6, 1, 1, -4),
            "behind": AABB3D(-1, -1, 4, 1, 1, 6),
        }
        visible = culler.cull(camera, boxes)
        assert visible == ["front"]


class TestOcclusionCulling:
    def test_target_behind_occluder_is_occluded(self):
        occluder = AABB3D(-1, -1, -5, 1, 1, -4)
        occlusion = OcclusionCulling([occluder])
        assert occlusion.is_occluded((0, 0, 0), (0, 0, -10))

    def test_target_in_front_of_occluder_not_occluded(self):
        occluder = AABB3D(-1, -1, -10, 1, 1, -9)
        occlusion = OcclusionCulling([occluder])
        assert not occlusion.is_occluded((0, 0, 0), (0, 0, -3))

    def test_no_occluders_never_occluded(self):
        occlusion = OcclusionCulling([])
        assert not occlusion.is_occluded((0, 0, 0), (0, 0, -10))


class TestLODManager:
    def test_selects_highest_detail_when_close(self):
        manager = LODManager(
            [
                LODLevel(max_distance=10, mesh_key="high"),
                LODLevel(max_distance=50, mesh_key="medium"),
                LODLevel(max_distance=200, mesh_key="low"),
            ]
        )
        assert manager.select(5).mesh_key == "high"

    def test_selects_lowest_detail_beyond_max(self):
        manager = LODManager(
            [
                LODLevel(max_distance=10, mesh_key="high"),
                LODLevel(max_distance=50, mesh_key="medium"),
            ]
        )
        assert manager.select(500).mesh_key == "medium"

    def test_select_mesh_key_uses_distance(self):
        manager = LODManager(
            [
                LODLevel(max_distance=10, mesh_key="high"),
                LODLevel(max_distance=100, mesh_key="low"),
            ]
        )
        assert manager.select_mesh_key((0, 0, 0), (5, 0, 0)) == "high"
        assert manager.select_mesh_key((0, 0, 0), (50, 0, 0)) == "low"

    def test_requires_at_least_one_level(self):
        with pytest.raises(ValueError):
            LODManager([])


class TestSpatialPartitioning:
    def test_insert_and_query(self):
        world = AABB3D(-100, -100, -100, 100, 100, 100)
        partition = SpatialPartitioning(world)
        partition.insert("building_a", AABB3D(0, 0, 0, 5, 5, 5))
        partition.insert("building_b", AABB3D(50, 50, 0, 55, 55, 5))
        result = partition.query(AABB3D(-1, -1, -1, 6, 6, 6))
        assert "building_a" in result
        assert "building_b" not in result


# ============================================================================ #
# streaming.py
# ============================================================================ #


class TestIncrementalMeshGenerator:
    def test_yields_expected_chunk_count(self):
        verts = [Vertex3D(i, 0, 0) for i in range(10)]
        tris = [(0, 1, 2)] * 10
        gen = IncrementalMeshGenerator(verts, tris, chunk_size=4)
        chunks = list(gen.generate())
        assert len(chunks) == 3
        assert gen.total_chunks == 3
        assert sum(c.triangle_count() for c in chunks) == 10


class TestGeometryStreaming:
    def test_diff_load_unload(self):
        positions = {"a": (0, 0, 0), "b": (100, 0, 0), "c": (5, 0, 0)}
        streaming = GeometryStreaming(positions, radius=10)
        diff1 = streaming.diff((0, 0, 0))
        assert diff1.to_load == {"a", "c"}
        assert diff1.to_unload == set()

        diff2 = streaming.diff((100, 0, 0))
        assert diff2.to_load == {"b"}
        assert diff2.to_unload == {"a", "c"}


class TestTextureAtlas:
    def test_packs_without_overlap(self):
        atlas = TextureAtlas(atlas_width=256, atlas_height=256)
        e1 = atlas.add("brick", 64, 64)
        e2 = atlas.add("glass", 64, 64)
        assert e1.x != e2.x or e1.y != e2.y

    def test_raises_when_full(self):
        atlas = TextureAtlas(atlas_width=64, atlas_height=64)
        atlas.add("a", 64, 64)
        with pytest.raises(ValueError):
            atlas.add("b", 64, 64)

    def test_occupancy_ratio(self):
        atlas = TextureAtlas(atlas_width=100, atlas_height=100)
        atlas.add("a", 50, 50)
        assert atlas.occupancy_ratio() == pytest.approx(0.25)


class TestDynamicBatcher:
    def test_groups_by_material(self):
        batcher = DynamicBatcher()
        batcher.add("mat_glass", "mesh_1")
        batcher.add("mat_glass", "mesh_2")
        batcher.add("mat_brick", "mesh_3")
        assert batcher.draw_call_count() == 2
        assert set(batcher.batches()["mat_glass"]) == {"mesh_1", "mesh_2"}


class TestInstancingBatch:
    def test_instance_count(self):
        batch = InstancingBatch(mesh_key="tree", transforms=[(0, 0, 0), (1, 0, 0)])
        assert batch.instance_count() == 2


# ============================================================================ #
# profiler.py
# ============================================================================ #


class TestCPUProfiler:
    def test_measures_block_duration(self):
        profiler = CPUProfiler()
        with profiler.measure("work"):
            time.sleep(0.01)
        assert profiler.total_for("work") > 0

    def test_summary_aggregates_multiple_samples(self):
        profiler = CPUProfiler()
        for _ in range(3):
            with profiler.measure("op"):
                pass
        summary = profiler.summary()
        assert summary["op"]["count"] == 3


class TestMemoryProfiler:
    def test_current_usage_returns_positive_numbers(self):
        profiler = MemoryProfiler()
        current, peak = profiler.current_usage_bytes()
        assert current >= 0
        assert peak >= current
        profiler.stop()

    def test_diff_requires_snapshots(self):
        profiler = MemoryProfiler()
        with pytest.raises(KeyError):
            profiler.diff("a", "b")
        profiler.stop()


class TestGPUProfiler:
    def test_frame_cycle_tracks_draw_calls(self):
        profiler = GPUProfiler()
        profiler.begin_frame()
        profiler.draw_call(triangle_count=100)
        profiler.draw_call(triangle_count=50)
        stats = profiler.end_frame()
        assert stats.draw_calls == 2
        assert stats.triangles == 150

    def test_draw_call_without_begin_frame_raises(self):
        profiler = GPUProfiler()
        with pytest.raises(RuntimeError):
            profiler.draw_call()

    def test_average_fps_positive(self):
        profiler = GPUProfiler()
        profiler.begin_frame()
        profiler.draw_call()
        profiler.end_frame()
        assert profiler.average_fps() >= 0


class TestAssetDependencyManager:
    def test_simple_load_order(self):
        mgr = AssetDependencyManager()
        mgr.add_asset("building_texture", depends_on=["material_lib"])
        mgr.add_asset("material_lib")
        order = mgr.load_order()
        assert order.index("material_lib") < order.index("building_texture")

    def test_detects_cycle(self):
        mgr = AssetDependencyManager()
        mgr.add_asset("a", depends_on=["b"])
        mgr.add_asset("b", depends_on=["a"])
        with pytest.raises(CyclicDependencyError):
            mgr.load_order()
