"""Roadmap V4 - Track C / C6: Spatial Index API isimlendirme tutarlılığı.

`RTree` ve `BVH`, diğer spatial index sınıflarıyla (`QuadTree`, `Octree`,
`KDTree`) ortak bir `query(...)` arayüzüne sahip olmalı. Bu testler:
1. `RTree.query()` ve `RTree.search()` aynı sonucu döndürür (alias).
2. `BVH.query()` ve `BVH.query_aabb()` aynı sonucu döndürür (alias).
3. Mevcut `search()`/`query_aabb()` çağıran eski testler kırılmadan geçer
   (bu dosya yalnızca *ekler*, hiçbir mevcut davranışı değiştirmez).
"""

from harita.data_engine import AABB2D, AABB3D, BVH, RTree
from harita.mesh_engine import Mesh3D, Vertex3D


def test_rtree_query_is_alias_of_search():
    rt = RTree(max_entries=4)
    boxes = [
        AABB2D(0, 0, 1, 1),
        AABB2D(5, 5, 6, 6),
        AABB2D(2, 2, 3, 3),
        AABB2D(0.5, 0.5, 1.5, 1.5),
    ]
    for i, b in enumerate(boxes):
        rt.insert(i, b)

    probe = AABB2D(0, 0, 2, 2)
    via_search = sorted(rt.search(probe))
    via_query = sorted(rt.query(probe))

    assert via_search == via_query
    assert via_search  # sanity: non-empty overlap found


def test_rtree_query_alias_preserves_search_method():
    # search() metodunun hâlâ var olduğunu ve alias eklenmesiyle
    # kaldırılmadığını doğrula (geriye uyumluluk garantisi).
    rt = RTree(max_entries=4)
    rt.insert("a", AABB2D(0, 0, 1, 1))
    assert hasattr(rt, "search")
    assert hasattr(rt, "query")
    assert rt.search(AABB2D(0, 0, 1, 1)) == rt.query(AABB2D(0, 0, 1, 1))


def _simple_triangle_mesh() -> Mesh3D:
    mesh = Mesh3D()
    mesh.vertices = [
        Vertex3D(0, 0, 0),
        Vertex3D(1, 0, 0),
        Vertex3D(0, 1, 0),
        Vertex3D(5, 5, 0),
        Vertex3D(6, 5, 0),
        Vertex3D(5, 6, 0),
    ]
    mesh.triangles = [(0, 1, 2), (3, 4, 5)]
    return mesh


def test_bvh_query_is_alias_of_query_aabb():
    mesh = _simple_triangle_mesh()
    bvh = BVH(mesh, leaf_size=1)

    probe = AABB3D(-1, -1, -1, 2, 2, 2)
    via_query_aabb = sorted(bvh.query_aabb(probe))
    via_query = sorted(bvh.query(probe))

    assert via_query_aabb == via_query
    assert via_query_aabb == [0]  # only first triangle overlaps this box
