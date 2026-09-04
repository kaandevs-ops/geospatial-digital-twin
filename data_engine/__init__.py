"""
Data Engine
============

Roadmap Phase 10 - "Spatial Database", "Object Cache", "Scene Cache",
"Undo", "Redo", "History", "Versioning".

Alt modüller:
    - `spatial_index` : QuadTree, Octree, KDTree, BVH, RTree (+ AABB2D/AABB3D)
    - `cache`         : ObjectCache, SceneCache
    - `history`       : History, Versioning (+ HistoryEntry, VersionSnapshot)

`UndoRedoStack`/`EditorCommand` Phase 8'de (`editor.commands`) tanımlandı ve
burada `History` tarafından sarmalanır - re-export edilerek `data_engine`
üzerinden de erişilebilir (roadmap'in "Undo"/"Redo" maddeleri).
"""

from __future__ import annotations

from ..editor.commands import CommandGroup, EditorCommand, FunctionCommand, UndoRedoStack
from .benchmark import (
    BenchmarkResult,
    benchmark_kdtree,
    benchmark_octree,
    benchmark_quadtree,
    benchmark_rtree,
    format_report,
    run_all,
)
from .cache import ObjectCache, SceneCache
from .history import History, HistoryEntry, Versioning, VersionSnapshot
from .spatial_index import (
    AABB2D,
    AABB3D,
    BVH,
    KDTree,
    Octree,
    QuadTree,
    RayHit,
    RTree,
)

__all__ = [
    # Spatial Database
    "AABB2D",
    "AABB3D",
    "QuadTree",
    "Octree",
    "KDTree",
    "BVH",
    "RayHit",
    "RTree",
    # Cache
    "ObjectCache",
    "SceneCache",
    # Undo/Redo (Phase 8'den re-export)
    "EditorCommand",
    "FunctionCommand",
    "CommandGroup",
    "UndoRedoStack",
    # History / Versioning
    "History",
    "HistoryEntry",
    "Versioning",
    "VersionSnapshot",
    # Ölçek Benchmark'ı (Roadmap V2, A10)
    "BenchmarkResult",
    "benchmark_rtree",
    "benchmark_quadtree",
    "benchmark_octree",
    "benchmark_kdtree",
    "run_all",
    "format_report",
]
