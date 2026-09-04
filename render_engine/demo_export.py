"""
Faz 15 uçtan uca demo: Phase 3 (Building Reconstruction) ile prosedürel
olarak üretilen gerçek bir binayı, Phase 15 render_engine köprüsünden
geçirip `scene.json` olarak diske yazar.

Çalıştırma:
    python -m harita.render_engine.demo_export [çıktı_yolu.json]

Üretilen dosya, `viewer/index.html` içinde "Sahne dosyası" alanından
doğrudan açılabilir.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.building_reconstruction import (
    BuildingType,
    Footprint,
    ProceduralBuildingGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.material_engine import PBRMaterial
from harita.render_engine import Scene


def build_demo_city_block() -> Scene:
    scene = Scene(name="demo_bina_adasi")

    footprints = [
        (
            Polygon([Point2D(0, 0), Point2D(20, 0), Point2D(20, 15), Point2D(0, 15)]),
            BuildingType.APARTMAN,
            5,
            15.0,
            (30.0, 0.0, 0.0),
        ),
        (
            Polygon([Point2D(0, 0), Point2D(14, 0), Point2D(14, 14), Point2D(0, 14)]),
            BuildingType.OFIS,
            8,
            28.0,
            (-5.0, 0.0, 0.0),
        ),
        (
            Polygon([Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)]),
            BuildingType.VILLA,
            2,
            6.0,
            (0.0, 0.0, 30.0),
        ),
    ]

    concrete = PBRMaterial(name="beton_cephe", albedo=(0.68, 0.66, 0.62), roughness=0.8)
    glass = PBRMaterial(name="cam_cephe", albedo=(0.25, 0.35, 0.45), roughness=0.15, metallic=0.1)

    for i, (poly, btype, floors, height, offset) in enumerate(footprints):
        footprint = Footprint(
            polygon=poly,
            building_type=btype.value if hasattr(btype, "value") else str(btype),
            floor_count=floors,
            height_m=height,
        )
        building = ProceduralBuildingGenerator.generate(footprint, building_type=btype, seed=11 + i)
        mesh = building.full_mesh()
        mesh.name = f"bina_{i}_{btype}"
        material = glass if btype == BuildingType.OFIS else concrete
        scene.add_mesh(mesh, material=material, translation=offset)

    return scene


if __name__ == "__main__":
    out_path = sys.argv[1] if len(sys.argv) > 1 else "scene.json"
    scene = build_demo_city_block()
    path = scene.write(out_path)
    total_tris = sum(n.mesh.triangle_count() for n in scene.nodes)
    print(f"Yazıldı: {path}  ({len(scene.nodes)} bina, {total_tris} üçgen)")
