"""Phase 2 (3D World Engine) için birim testleri."""

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.core_engine.coordinate_systems import GeoPoint

from harita.mesh_engine import (
    MeshBuilder, MeshOptimizer, MeshSimplifier, MeshSplitter, MeshMerger,
    MeshRepair, UVGenerator, NormalGenerator, TangentGenerator, Mesh3D, Vertex3D,
)
from harita.terrain_engine import (
    HeightmapGrid, DEMImporter, TerrainMeshGenerator, AdaptiveTerrain,
    TerrainLOD, TerrainChunking, TerrainStreaming,
)
from harita.material_engine import PBRMaterial, MaterialCache, ProceduralMaterials
from harita.lighting import (
    SolarPositionCalculator, SunLight, MoonLight, HDRSky, AmbientLight,
    ShadowCalculator, AmbientOcclusionBaker,
)


# ------------------------------------------------------------------ #
# Mesh Engine
# ------------------------------------------------------------------ #

def _box_polygon(w: float, d: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


def test_extrude_polygon_box_metrics():
    poly = _box_polygon(10, 8)
    mesh = MeshBuilder.extrude_polygon(poly, base_z=0.0, height=15.0)
    assert mesh.vertex_count() == 8
    # 2 taban + 2 tavan üçgen + 4 kenar * 2 üçgen = 12
    assert mesh.triangle_count() == 12
    assert math.isclose(mesh.surface_area(), 2 * 10 * 8 + 2 * (10 * 15) + 2 * (8 * 15), rel_tol=1e-6)
    assert math.isclose(mesh.volume(), 10 * 8 * 15, rel_tol=1e-6)


def test_extrude_polygon_concave_l_shape():
    # L şekli (konkav) - ear clipping'in konkav köşeyi doğru işlediğini doğrular
    poly = Polygon([
        Point2D(0, 0), Point2D(10, 0), Point2D(10, 5),
        Point2D(5, 5), Point2D(5, 10), Point2D(0, 10),
    ])
    mesh = MeshBuilder.extrude_polygon(poly, base_z=0.0, height=3.0)
    assert mesh.vertex_count() == 12
    expected_footprint_area = 10 * 5 + 5 * 5
    assert math.isclose(mesh.volume(), expected_footprint_area * 3.0, rel_tol=1e-6)


def test_mesh_optimizer_weld_removes_duplicates():
    verts = [Vertex3D(0, 0, 0), Vertex3D(0, 0, 0), Vertex3D(1, 0, 0)]
    mesh = Mesh3D(vertices=verts, triangles=[(0, 1, 2)])
    welded = MeshOptimizer.weld_vertices(mesh)
    assert welded.vertex_count() == 2
    # (0,1,2) dejenere hale gelir (0 ve 1 aynı noktaya kaynaklanır) -> üçgen atılır
    assert welded.triangle_count() == 0


def test_mesh_simplifier_reduces_triangle_count():
    poly = _box_polygon(10, 8)
    mesh = MeshBuilder.extrude_polygon(poly, base_z=0.0, height=5.0)
    simplified = MeshSimplifier.simplify(mesh, 0.5)
    assert simplified.triangle_count() <= max(1, int(mesh.triangle_count() * 0.5)) + 1
    assert simplified.triangle_count() >= 1


def test_mesh_splitter_connectivity_separates_disjoint_boxes():
    box1 = MeshBuilder.extrude_polygon(_box_polygon(2, 2), 0, 2)
    box2 = MeshBuilder.extrude_polygon(_box_polygon(2, 2), 0, 2)
    for v in box2.vertices:
        v.x += 100  # ayrık konuma taşı
    merged = MeshMerger.merge([box1, box2])
    parts = MeshSplitter.split_by_connectivity(merged)
    assert len(parts) == 2


def test_mesh_splitter_by_plane_conserves_triangles():
    mesh = MeshBuilder.extrude_polygon(_box_polygon(4, 4), 0, 10)
    below, above = MeshSplitter.split_by_plane(mesh, 5.0)
    assert below.triangle_count() + above.triangle_count() == mesh.triangle_count()


def test_mesh_repair_manifold_box():
    mesh = MeshBuilder.extrude_polygon(_box_polygon(3, 3), 0, 3)
    assert MeshRepair.is_manifold(mesh)
    assert MeshRepair.find_boundary_edges(mesh) == []


def test_uv_generator_planar_mapping_range():
    mesh = MeshBuilder.extrude_polygon(_box_polygon(5, 5), 0, 5)
    mapped = UVGenerator.planar_mapping(mesh, axis="z")
    for v in mapped.vertices:
        assert 0.0 <= v.uv[0] <= 1.0
        assert 0.0 <= v.uv[1] <= 1.0


def test_normal_generator_unit_length():
    mesh = MeshBuilder.extrude_polygon(_box_polygon(5, 5), 0, 5)
    NormalGenerator.compute_face_averaged_normals(mesh)
    for v in mesh.vertices:
        length = math.sqrt(sum(c * c for c in v.normal))
        assert math.isclose(length, 1.0, abs_tol=1e-6)


def test_tangent_generator_produces_unit_tangents():
    mesh = MeshBuilder.extrude_polygon(_box_polygon(5, 5), 0, 5)
    mesh = UVGenerator.box_mapping(mesh)
    mesh = TangentGenerator.compute_tangents(mesh)
    for v in mesh.vertices:
        assert v.tangent is not None
        tx, ty, tz, w = v.tangent
        length = math.sqrt(tx * tx + ty * ty + tz * tz)
        assert math.isclose(length, 1.0, abs_tol=1e-4) or length == 0.0
        assert w in (1.0, -1.0)


# ------------------------------------------------------------------ #
# Terrain Engine
# ------------------------------------------------------------------ #

def test_heightmap_grid_bilinear_sample_matches_grid_points():
    origin = GeoPoint(39.9334, 32.8597)
    grid = DEMImporter.from_matrix([[0, 1], [2, 3]], resolution_m=10.0, origin=origin)
    assert math.isclose(grid.sample_bilinear(0, 0), 0.0, abs_tol=1e-9)
    assert math.isclose(grid.sample_bilinear(10, 0), 1.0, abs_tol=1e-9)
    assert math.isclose(grid.sample_bilinear(0, 10), 2.0, abs_tol=1e-9)
    assert math.isclose(grid.sample_bilinear(5, 5), 1.5, abs_tol=1e-9)


def test_terrain_mesh_generator_grid_dimensions():
    origin = GeoPoint(39.9334, 32.8597)
    grid = DEMImporter.flat_terrain(5, 4, resolution_m=2.0, elevation=10.0, origin=origin)
    mesh = TerrainMeshGenerator.generate(grid)
    assert mesh.vertex_count() == 5 * 4
    assert mesh.triangle_count() == (5 - 1) * (4 - 1) * 2


def test_terrain_mesh_flat_terrain_is_planar():
    origin = GeoPoint(39.9334, 32.8597)
    grid = DEMImporter.flat_terrain(4, 4, resolution_m=1.0, elevation=7.0, origin=origin)
    mesh = TerrainMeshGenerator.generate(grid)
    assert all(math.isclose(v.z, 7.0) for v in mesh.vertices)


def test_adaptive_terrain_reduces_triangles_on_flat_area():
    origin = GeoPoint(39.9334, 32.8597)
    flat = DEMImporter.flat_terrain(16, 16, resolution_m=1.0, elevation=0.0, origin=origin)
    adaptive = AdaptiveTerrain(flat, variance_threshold=0.1, min_cell=2)
    full_mesh = TerrainMeshGenerator.generate(flat)
    adaptive_mesh = adaptive.to_mesh()
    assert adaptive_mesh.triangle_count() < full_mesh.triangle_count()


def test_terrain_lod_levels_decrease_triangle_count():
    origin = GeoPoint(39.9334, 32.8597)
    grid = DEMImporter.synthetic_hills(32, 32, 1.0, origin)
    levels = TerrainLOD.generate_all_levels(grid)
    assert levels["full"].triangle_count() > levels["half"].triangle_count()
    assert levels["half"].triangle_count() > levels["quarter"].triangle_count()
    assert levels["quarter"].triangle_count() > levels["eighth"].triangle_count()


def test_terrain_lod_select_level_by_distance():
    assert TerrainLOD.select_level(50) == "full"
    assert TerrainLOD.select_level(500) == "half"
    assert TerrainLOD.select_level(1500) == "quarter"
    assert TerrainLOD.select_level(5000) == "eighth"


def test_terrain_chunking_covers_full_grid():
    origin = GeoPoint(39.9334, 32.8597)
    grid = DEMImporter.flat_terrain(20, 20, 1.0, 0.0, origin)
    chunks = TerrainChunking.chunk_grid(grid, chunk_size=10, zoom=10)
    total_cells = sum(c.grid.width * c.grid.height for c in chunks)
    assert total_cells == grid.width * grid.height


def test_terrain_streaming_loads_and_unloads():
    origin = GeoPoint(39.9334, 32.8597)
    grid = DEMImporter.flat_terrain(40, 40, 1.0, 0.0, origin)
    chunks = TerrainChunking.chunk_grid(grid, chunk_size=10, zoom=10)
    streaming = TerrainStreaming(chunks, load_radius_chunks=1)
    loaded, _ = streaming.update(0, 0)
    assert len(loaded) > 0
    assert all(c.mesh is not None for c in loaded)
    # kamera çok uzağa taşınınca eski chunk'lar boşalmalı
    _, unloaded = streaming.update(100, 100)
    assert len(unloaded) > 0


# ------------------------------------------------------------------ #
# Material Engine
# ------------------------------------------------------------------ #

def test_pbr_material_clamps_ranges():
    mat = PBRMaterial(name="test", roughness=2.0, metallic=-1.0, opacity=5.0)
    assert mat.roughness == 1.0
    assert mat.metallic == 0.0
    assert mat.opacity == 1.0


def test_material_cache_deduplicates_identical_materials():
    cache = MaterialCache()
    m1 = PBRMaterial(name="a", albedo=(0.5, 0.5, 0.5))
    m2 = PBRMaterial(name="a", albedo=(0.5, 0.5, 0.5))
    cache.get_or_add(m1)
    cache.get_or_add(m2)
    assert len(cache) == 1


def test_procedural_materials_known_presets():
    for material_type in ProceduralMaterials.available_types():
        mat = ProceduralMaterials.create(material_type)
        assert mat.name == material_type


def test_procedural_materials_unknown_type_raises():
    try:
        ProceduralMaterials.create("bilinmeyen_tip")
        assert False, "ValueError bekleniyordu"
    except ValueError:
        pass


def test_procedural_materials_variation_is_deterministic():
    a = ProceduralMaterials.create("beton", variation_seed=7)
    b = ProceduralMaterials.create("beton", variation_seed=7)
    assert a.albedo == b.albedo
    assert a.roughness == b.roughness


# ------------------------------------------------------------------ #
# Lighting
# ------------------------------------------------------------------ #

def test_solar_position_noon_near_equator_high_elevation():
    location = GeoPoint(lat=0.0, lon=0.0)
    when = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)  # ekinoks
    pos = SolarPositionCalculator.compute(location, when)
    assert pos.elevation_deg > 60.0  # öğlen, ekvator, ekinoks -> güneş neredeyse tepede


def test_solar_position_midnight_is_below_horizon():
    location = GeoPoint(lat=39.9334, lon=32.8597)
    when = datetime(2026, 6, 21, 0, 0, tzinfo=timezone.utc)
    pos = SolarPositionCalculator.compute(location, when)
    assert not pos.is_daylight


def test_sun_direction_vector_is_unit_length():
    location = GeoPoint(lat=39.9334, lon=32.8597)
    sun = SunLight.at(location, datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc))
    dx, dy, dz = sun.direction()
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    assert math.isclose(length, 1.0, abs_tol=1e-6)


def test_moon_light_intensity_scales_with_phase():
    new_moon = MoonLight(phase=0.0)
    full_moon = MoonLight(phase=1.0)
    assert new_moon.intensity_lux() < full_moon.intensity_lux()


def test_hdr_sky_gradient_endpoints():
    sky = HDRSky(zenith_color=(0, 0, 1), horizon_color=(1, 0, 0))
    horizon = sky.sample_direction(0.0)
    zenith = sky.sample_direction(90.0)
    assert math.isclose(horizon[0], 1.0, abs_tol=1e-6)
    assert math.isclose(zenith[2], 1.0, abs_tol=1e-6)


def test_ambient_light_from_sky_is_average():
    sky = HDRSky(zenith_color=(0.2, 0.4, 0.6), horizon_color=(0.8, 0.6, 0.4))
    amb = AmbientLight.from_sky(sky)
    assert math.isclose(amb.color[0], 0.5, abs_tol=1e-6)


def test_shadow_calculator_detects_occlusion_below_box():
    poly = _box_polygon(10, 10)
    box = MeshBuilder.extrude_polygon(poly, base_z=0.0, height=10.0)
    for v in box.vertices:
        v.x -= 5
        v.y -= 5
    # doğrudan yukarıdan gelen bir güneş (tam tepede) ile kutunun altındaki
    # nokta gölgede olmalı
    from harita.lighting import SolarPosition
    straight_down_sun = SunLight(position=SolarPosition(azimuth_deg=0.0, elevation_deg=90.0))
    point_under_box = (0.0, 0.0, -1.0)
    in_shadow = ShadowCalculator.point_in_shadow(point_under_box, straight_down_sun, [box], max_distance=50.0)
    assert in_shadow


def test_ambient_occlusion_baker_returns_values_in_range():
    mesh = MeshBuilder.extrude_polygon(_box_polygon(4, 4), 0, 4)
    ao = AmbientOcclusionBaker.bake_vertex_ao(mesh, sample_count=4)
    assert len(ao) == mesh.vertex_count()
    assert all(0.0 <= value <= 1.0 for value in ao)
