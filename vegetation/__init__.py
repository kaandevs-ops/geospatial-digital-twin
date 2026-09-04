"""
vegetation - Prosedürel Bitki Örtüsü
=====================================

Roadmap V4 - Faz E18 (Yeni Alt Sistem). Önceki durumda arazi
(`terrain_engine`) ve bina (`building_reconstruction`) prosedürel
üretimi güçlüydü ama şehir sahnelerinde ağaç/çalı gibi bitki örtüsü
hiç üretilmiyordu.

Bu paket:
  - `tree_generator.TreeGenerator` — mevcut `mesh_engine` altyapısı
    (extrusion + koni primitifleri + `MeshMerger`) üzerine kurulu,
    deterministik düşük-poli ağaç/çalı mesh üretimi.
  - `scatter.VegetationScatterer` — `terrain_engine.FlowAccumulation`
    (nem vekili) + yerel eğim temelli, ağırlıklı-örneklemeli yerleşim;
    ayrıca (Faz C3) OSM poligonları için Poisson-disc dağıtım.
  - `osm_bridge` — (ROADMAP_V7.md Faz C3) `core_engine.gis_core.
    osm_client.OSMCategoryParser` çıktısındaki `trees`/`forest`/`wood`
    feature'larını `VegetationInstance`/`Mesh3D`'ye bağlayan köprü.
  - `types` — `TreeSpecies`, `VegetationInstance`.
"""
from __future__ import annotations

from .types import TreeSpecies, VegetationInstance
from .tree_generator import TreeGenerator
from .scatter import VegetationScatterer
from .osm_bridge import (
    classify_species,
    estimate_height_m,
    generate_vegetation_for_collection,
    mesh_for_tree_instance,
    scatter_forest_polygon,
    tree_instance_from_point,
)

__all__ = [
    "TreeSpecies",
    "VegetationInstance",
    "TreeGenerator",
    "VegetationScatterer",
    "classify_species",
    "estimate_height_m",
    "tree_instance_from_point",
    "mesh_for_tree_instance",
    "scatter_forest_polygon",
    "generate_vegetation_for_collection",
]
