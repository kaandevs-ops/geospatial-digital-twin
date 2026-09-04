"""Phase 10 (Data Engine) için birim testleri."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.mesh_engine import Mesh3D, Vertex3D
from harita.editor.commands import FunctionCommand
from harita.data_engine import (
    AABB2D, AABB3D, QuadTree, Octree, KDTree, BVH, RayHit, RTree,
    ObjectCache, SceneCache,
    History, Versioning,
    UndoRedoStack,
)


# ======================================================================== #
# AABB2D / AABB3D
# ======================================================================== #

def test_aabb2d_intersects_and_contains():
    a = AABB2D(0, 0, 10, 10)
    b = AABB2D(5, 5, 15, 15)
    c = AABB2D(20, 20, 30, 30)
    assert a.intersects(b)
    assert not a.intersects(c)
    assert a.contains_point(5, 5)
    assert not a.contains_point(20, 20)


def test_aabb2d_union_and_enlargement():
    a = AABB2D(0, 0, 10, 10)
    b = AABB2D(5, 5, 20, 20)
    u = a.union(b)
    assert u.min_x == 0 and u.max_x == 20
    assert a.enlargement(b) == pytest.approx(u.area() - a.area())
    assert a.enlargement(AABB2D(1, 1, 2, 2)) == 0.0  # zaten içeride


def test_aabb2d_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        AABB2D(10, 0, 0, 10)


def test_aabb3d_intersects_ray_hits_and_misses():
    box = AABB3D(-1, -1, -1, 1, 1, 1)
    assert box.intersects_ray((-5, 0, 0), (1.0, math.inf, math.inf))
    assert not box.intersects_ray((-5, 5, 0), (1.0, math.inf, math.inf))


# ======================================================================== #
# QuadTree
# ======================================================================== #

def test_quadtree_basic_insert_and_query():
    qt = QuadTree(AABB2D(0, 0, 100, 100), capacity=4)
    for i in range(20):
        qt.insert(f"item-{i}", AABB2D(i, i, i + 1, i + 1))
    assert qt.count() == 20
    result = qt.query(AABB2D(0, 0, 5, 5))
    assert "item-0" in result
    assert "item-19" not in result  # (19,19)-(20,20) 0..5 aralığıyla kesişmez


def test_quadtree_subdivides_beyond_capacity():
    qt = QuadTree(AABB2D(0, 0, 100, 100), capacity=2, max_depth=8)
    for i in range(50):
        qt.insert(i, AABB2D(i % 90, (i * 3) % 90, i % 90 + 1, (i * 3) % 90 + 1))
    assert qt._children is not None
    assert qt.count() == 50


def test_quadtree_query_outside_boundary_empty():
    qt = QuadTree(AABB2D(0, 0, 10, 10), capacity=4)
    qt.insert("a", AABB2D(1, 1, 2, 2))
    assert qt.query(AABB2D(50, 50, 60, 60)) == []


def test_quadtree_no_duplicate_results_across_quadrants():
    # merkezi kesen bir bounding box birden fazla çeyreğe düşer
    qt = QuadTree(AABB2D(0, 0, 100, 100), capacity=1, max_depth=6)
    for i in range(10):
        qt.insert(i, AABB2D(0, 0, 100, 100))  # her öğe tüm alanı kaplıyor
    result = qt.query(AABB2D(40, 40, 60, 60))
    assert sorted(result) == list(range(10))


# ======================================================================== #
# Octree
# ======================================================================== #

def test_octree_basic_insert_and_query():
    ot = Octree(AABB3D(0, 0, 0, 100, 100, 100), capacity=4)
    for i in range(30):
        ot.insert(f"obj-{i}", AABB3D(i, i, i, i + 1, i + 1, i + 1))
    assert ot.count() == 30
    result = ot.query(AABB3D(0, 0, 0, 3, 3, 3))
    assert "obj-0" in result
    assert "obj-29" not in result


def test_octree_subdivides_into_eight_children():
    ot = Octree(AABB3D(0, 0, 0, 10, 10, 10), capacity=1, max_depth=5)
    for i in range(10):
        ot.insert(i, AABB3D(i % 9, i % 9, i % 9, i % 9 + 0.5, i % 9 + 0.5, i % 9 + 0.5))
    assert ot._children is not None
    assert len(ot._children) == 8


# ======================================================================== #
# KDTree
# ======================================================================== #

def test_kdtree_nearest_2d():
    points = [(0, 0), (5, 5), (10, 10), (1, 1), (9, 9)]
    tree = KDTree(points)
    result = tree.nearest((0.5, 0.5))
    assert result is not None
    point, data, dist = result
    assert point == (0, 0) or point == (1, 1)


def test_kdtree_nearest_k_sorted_by_distance():
    points = [(0, 0), (1, 0), (2, 0), (3, 0), (10, 0)]
    tree = KDTree(points)
    results = tree.nearest_k((0, 0), 3)
    assert len(results) == 3
    dists = [r[2] for r in results]
    assert dists == sorted(dists)
    assert results[0][0] == (0, 0)


def test_kdtree_range_search():
    points = [(0, 0), (1, 0), (2, 0), (5, 0), (10, 0)]
    tree = KDTree(points)
    results = tree.range_search((0, 0), radius=2.5)
    found_points = {r[0] for r in results}
    assert found_points == {(0, 0), (1, 0), (2, 0)}


def test_kdtree_3d_points():
    points = [(0, 0, 0), (1, 1, 1), (5, 5, 5), (-1, -1, -1)]
    tree = KDTree(points)
    result = tree.nearest((0.1, 0.1, 0.1))
    assert result[0] == (0, 0, 0)


def test_kdtree_empty():
    tree = KDTree([])
    assert len(tree) == 0
    assert tree.nearest((0, 0)) is None
    assert tree.nearest_k((0, 0), 3) == []


def test_kdtree_with_associated_data():
    points = [(0, 0), (10, 10)]
    data = ["building_a", "building_b"]
    tree = KDTree(points, data)
    result = tree.nearest((0, 1))
    assert result[1] == "building_a"


# ======================================================================== #
# BVH
# ======================================================================== #

def _flat_quad_mesh() -> Mesh3D:
    """z=0 düzleminde 4x4'lük 16 karelik (32 üçgenlik) düz bir mesh."""
    mesh = Mesh3D(name="ground")
    size = 4
    for y in range(size + 1):
        for x in range(size + 1):
            mesh.vertices.append(Vertex3D(float(x), float(y), 0.0))

    def idx(x, y):
        return y * (size + 1) + x

    for y in range(size):
        for x in range(size):
            a, b, c, d = idx(x, y), idx(x + 1, y), idx(x + 1, y + 1), idx(x, y + 1)
            mesh.triangles.append((a, b, c))
            mesh.triangles.append((a, c, d))
    return mesh


def test_bvh_ray_hits_flat_mesh():
    mesh = _flat_quad_mesh()
    bvh = BVH(mesh, leaf_size=2)
    hit = bvh.intersect_ray(origin=(2.0, 2.0, 10.0), direction=(0.0, 0.0, -1.0))
    assert hit is not None
    assert isinstance(hit, RayHit)
    assert hit.point[2] == pytest.approx(0.0, abs=1e-6)
    assert hit.point[0] == pytest.approx(2.0, abs=1e-6)


def test_bvh_ray_misses_outside_mesh():
    mesh = _flat_quad_mesh()
    bvh = BVH(mesh, leaf_size=2)
    hit = bvh.intersect_ray(origin=(100.0, 100.0, 10.0), direction=(0.0, 0.0, -1.0))
    assert hit is None


def test_bvh_ray_finds_nearest_of_multiple_triangles():
    mesh = _flat_quad_mesh()
    bvh = BVH(mesh, leaf_size=1)
    # yukarıdan aşağı doğru dikey ışın; en yakın (ve tek) kesişim z=0'da olmalı
    hit = bvh.intersect_ray(origin=(1.5, 1.5, 50.0), direction=(0.0, 0.0, -1.0))
    assert hit is not None
    assert hit.t == pytest.approx(50.0, abs=1e-6)


def test_bvh_query_aabb():
    mesh = _flat_quad_mesh()
    bvh = BVH(mesh, leaf_size=2)
    tris = bvh.query_aabb(AABB3D(0, 0, -1, 1, 1, 1))
    assert len(tris) > 0
    for ti in tris:
        assert 0 <= ti < len(mesh.triangles)


# ======================================================================== #
# RTree
# ======================================================================== #

def test_rtree_basic_search():
    rt = RTree(max_entries=4)
    rt.insert("building_a", AABB2D(0, 0, 10, 10))
    rt.insert("building_b", AABB2D(20, 20, 30, 30))
    rt.insert("building_c", AABB2D(5, 5, 15, 15))
    assert len(rt) == 3

    result = rt.search(AABB2D(0, 0, 12, 12))
    assert "building_a" in result
    assert "building_c" in result
    assert "building_b" not in result


def test_rtree_forces_split_with_many_entries():
    rt = RTree(max_entries=4)
    for i in range(100):
        x = float(i % 10) * 10
        y = float(i // 10) * 10
        rt.insert(f"item-{i}", AABB2D(x, y, x + 5, y + 5))
    assert len(rt) == 100

    # her öğe tam olarak kendi bölgesinde bulunabilmeli
    result = rt.search(AABB2D(0, 0, 5, 5))
    assert "item-0" in result

    result_all = rt.search(AABB2D(0, 0, 200, 200))
    assert len(result_all) == 100


def test_rtree_no_overlap_returns_empty():
    rt = RTree(max_entries=4)
    rt.insert("a", AABB2D(0, 0, 5, 5))
    result = rt.search(AABB2D(100, 100, 110, 110))
    assert result == []


def test_rtree_search_finds_footprint_like_overlaps():
    rt = RTree(max_entries=8)
    footprints = {
        "bina_1": AABB2D(0, 0, 20, 15),
        "bina_2": AABB2D(25, 0, 40, 12),
        "bina_3": AABB2D(0, 20, 18, 35),
        "bina_4": AABB2D(50, 50, 70, 70),
    }
    for name, box in footprints.items():
        rt.insert(name, box)

    # bina_1 ve bina_2'nin bulunduğu genel bölgeyi sorgula
    result = rt.search(AABB2D(0, 0, 45, 15))
    assert set(result) == {"bina_1", "bina_2"}


# ======================================================================== #
# ObjectCache / SceneCache
# ======================================================================== #

def test_object_cache_lru_eviction():
    cache: ObjectCache[str] = ObjectCache(capacity=3)
    cache.put("a", "A")
    cache.put("b", "B")
    cache.put("c", "C")
    cache.get("a")  # 'a'yı en-son-kullanılan yap
    cache.put("d", "D")  # 'b' tahliye edilmeli (en eski kullanılmayan)
    assert "b" not in cache
    assert "a" in cache
    assert "d" in cache
    assert len(cache) == 3


def test_object_cache_get_or_create():
    cache: ObjectCache[int] = ObjectCache(capacity=10)
    calls = []

    def factory():
        calls.append(1)
        return 42

    v1 = cache.get_or_create("key", factory)
    v2 = cache.get_or_create("key", factory)
    assert v1 == v2 == 42
    assert len(calls) == 1  # yalnızca ilk çağrıda üretildi


def test_object_cache_hit_rate():
    cache: ObjectCache[str] = ObjectCache(capacity=5)
    cache.put("a", "A")
    cache.get("a")
    cache.get("a")
    cache.get("missing")
    assert cache.hit_rate() == pytest.approx(2 / 3)


def test_object_cache_invalidate_and_clear():
    cache: ObjectCache[str] = ObjectCache(capacity=5)
    cache.put("a", "A")
    assert cache.invalidate("a")
    assert "a" not in cache
    assert not cache.invalidate("a")  # zaten yok
    cache.put("b", "B")
    cache.clear()
    assert len(cache) == 0


def test_object_cache_rejects_invalid_capacity():
    with pytest.raises(ValueError):
        ObjectCache(capacity=0)


def test_scene_cache_buckets_are_independent():
    scene = SceneCache()
    scene.bucket("mesh", capacity=2)
    scene.bucket("digital_twin", capacity=5)

    scene.put("mesh", "b1", "mesh-data-1")
    scene.put("digital_twin", "b1", "twin-data-1")

    assert scene.get("mesh", "b1") == "mesh-data-1"
    assert scene.get("digital_twin", "b1") == "twin-data-1"
    assert scene.get("mesh", "missing") is None
    assert scene.get("nonexistent_bucket", "b1") is None


def test_scene_cache_get_or_create_and_stats():
    scene = SceneCache()
    result = scene.get_or_create("mesh", "b1", lambda: "computed")
    assert result == "computed"
    stats = scene.stats()
    assert "mesh" in stats
    assert stats["mesh"]["size"] == 1


def test_scene_cache_invalidate_bucket():
    scene = SceneCache()
    scene.put("mesh", "b1", "m1")
    scene.put("mesh", "b2", "m2")
    scene.invalidate_bucket("mesh")
    assert scene.total_size() == 0


def test_scene_cache_bucket_capacity_preserved_on_reaccess():
    scene = SceneCache()
    scene.bucket("mesh", capacity=2)
    scene.bucket("mesh", capacity=999)  # ignore edilmeli
    assert scene.bucket("mesh").capacity == 2


# ======================================================================== #
# History
# ======================================================================== #

def test_history_records_do_undo_redo():
    history = History()
    state = {"value": 0}

    def make_command(delta):
        def do():
            state["value"] += delta
        def undo():
            state["value"] -= delta
        return FunctionCommand(do, undo, label=f"add({delta})")

    history.execute(make_command(5))
    history.execute(make_command(3))
    assert state["value"] == 8

    history.undo()
    assert state["value"] == 5

    history.redo()
    assert state["value"] == 8

    labels_actions = [(e.label, e.action) for e in history.entries()]
    assert labels_actions == [
        ("add(5)", "do"), ("add(3)", "do"), ("add(3)", "undo"), ("add(3)", "redo"),
    ]


def test_history_survives_stack_undo_redo_state_wipe():
    """Editördeki normal undo/redo akışı (stack içeriği silinse bile)
    History günlüğü kalıcıdır - denetim amaçlı."""
    history = History()
    cmd = FunctionCommand(lambda: None, lambda: None, label="noop")
    history.execute(cmd)
    history.undo()
    assert len(history.entries()) == 2
    # stack'in kendi undo/redo state'i tükendi ama History günlüğü duruyor
    assert history.stack.can_undo() is False
    assert len(history.entries()) == 2


def test_history_entries_since():
    history = History()
    cmd1 = FunctionCommand(lambda: None, lambda: None, label="c1")
    cmd2 = FunctionCommand(lambda: None, lambda: None, label="c2")
    history.execute(cmd1)
    seq_after_first = history.last_sequence()
    history.execute(cmd2)
    recent = history.entries_since(seq_after_first)
    assert len(recent) == 1
    assert recent[0].label == "c2"


def test_history_uses_provided_stack():
    stack = UndoRedoStack(max_history=5)
    history = History(stack=stack)
    cmd = FunctionCommand(lambda: None, lambda: None, label="x")
    history.execute(cmd)
    assert stack.can_undo() is True


def test_history_noop_undo_redo_not_recorded():
    history = History()
    assert history.undo() is None
    assert history.redo() is None
    assert history.entries() == []


# ======================================================================== #
# Versioning
# ======================================================================== #

def test_versioning_snapshot_and_restore():
    v = Versioning()
    v.snapshot({"height": 10, "floors": 3}, label="ilk_hal")
    v.snapshot({"height": 15, "floors": 4}, label="kat_eklendi")

    restored_v0 = v.restore(0)
    restored_v1 = v.restore(1)
    assert restored_v0 == {"height": 10, "floors": 3}
    assert restored_v1 == {"height": 15, "floors": 4}


def test_versioning_restore_is_deep_copy_independent():
    v = Versioning()
    original = {"nested": {"value": 1}}
    v.snapshot(original)
    restored = v.restore(0)
    restored["nested"]["value"] = 999
    # geri döndürülen kopya değiştirildi, ama saklanan sürüm etkilenmemeli
    assert v.restore(0) == {"nested": {"value": 1}}


def test_versioning_unknown_version_raises():
    v = Versioning()
    v.snapshot({"a": 1})
    with pytest.raises(KeyError):
        v.restore(999)


def test_versioning_max_versions_keeps_baseline():
    v = Versioning(max_versions=3)
    for i in range(10):
        v.snapshot({"i": i})
    assert len(v) == 3
    versions = v.list_versions()
    assert versions[0].version == 0  # baseline her zaman korunur
    assert v.restore(0) == {"i": 0}


def test_versioning_latest():
    v = Versioning()
    v.snapshot({"i": 0})
    v.snapshot({"i": 1})
    latest = v.latest()
    assert latest is not None
    assert latest.state == {"i": 1}


def test_versioning_diff_keys():
    v = Versioning()
    v.snapshot({"height": 10, "floors": 3, "name": "A"})
    v.snapshot({"height": 15, "floors": 3, "name": "A"})
    diffs = v.diff_keys(0, 1)
    assert diffs == {"height": (10, 15)}


def test_versioning_diff_keys_requires_dict_state():
    v = Versioning()
    v.snapshot([1, 2, 3])
    v.snapshot([1, 2, 4])
    with pytest.raises(TypeError):
        v.diff_keys(0, 1)
