"""Faz 15 — Render Engine (scene_bridge) testleri."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import MeshBuilder, Mesh3D, Vertex3D
from harita.material_engine import PBRMaterial
from harita.lighting import SunLight, AmbientLight, SolarPosition
from harita.render_engine import (
    Scene,
    SceneLight,
    SCENE_SCHEMA_VERSION,
    scene_from_meshes,
    sun_light_to_scene_light,
    ambient_light_to_scene_light,
)


def _square_mesh(height: float = 5.0) -> Mesh3D:
    poly = Polygon(points=[Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10)])
    return MeshBuilder.extrude_polygon(poly, base_z=0.0, height=height, name="kutu")


class TestSceneConstruction:
    def test_add_mesh_registers_node_and_material(self):
        scene = Scene(name="s")
        mesh = _square_mesh()
        mat = PBRMaterial(name="beton", albedo=(0.5, 0.5, 0.5))
        node = scene.add_mesh(mesh, material=mat)

        assert node in scene.nodes
        assert "beton" in scene.materials
        assert node.material_name == "beton"

    def test_add_mesh_without_material(self):
        scene = Scene()
        node = scene.add_mesh(_square_mesh())
        assert node.material_name is None
        assert scene.materials == {}

    def test_scene_from_meshes_adds_default_lights(self):
        scene = scene_from_meshes([_square_mesh()])
        kinds = {light.kind for light in scene.lights}
        assert kinds == {"ambient", "directional"}

    def test_scene_from_meshes_pairs_materials(self):
        mat = PBRMaterial(name="cam", albedo=(0.1, 0.2, 0.9))
        scene = scene_from_meshes([_square_mesh(), _square_mesh(3.0)], materials=[mat, None])
        assert scene.nodes[0].material_name == "cam"
        assert scene.nodes[1].material_name is None


class TestBoundingBoxAndCamera:
    def test_bounding_box_matches_mesh_extent(self):
        scene = Scene()
        scene.add_mesh(_square_mesh(height=5.0))
        (lx, ly, lz), (hx, hy, hz) = scene.bounding_box()
        assert lx == pytest.approx(0.0)
        assert hx == pytest.approx(10.0)
        assert hz == pytest.approx(5.0)

    def test_bounding_box_applies_translation(self):
        scene = Scene()
        scene.add_mesh(_square_mesh(), translation=(100.0, 0.0, 0.0))
        (lx, _, _), (hx, _, _) = scene.bounding_box()
        assert lx == pytest.approx(100.0)
        assert hx == pytest.approx(110.0)

    def test_empty_scene_bounding_box_is_zero(self):
        scene = Scene()
        assert scene.bounding_box() == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

    def test_default_camera_frames_scene_center(self):
        scene = Scene()
        scene.add_mesh(_square_mesh())
        cam = scene.default_camera()
        assert cam["target"][0] == pytest.approx(5.0)
        assert cam["distance"] > 0

    def test_rotation_changes_bounding_box(self):
        scene = Scene()
        scene.add_mesh(_square_mesh(height=5.0), rotation_deg=(0.0, 45.0, 0.0))
        (lx, _, lz), (hx, _, hz) = scene.bounding_box()
        # 45 derece Y rotasyonuyla köşegen genişler
        assert (hx - lx) > 10.0
        assert (hz - lz) > 10.0


class TestSerialization:
    def test_to_dict_schema_version(self):
        scene = scene_from_meshes([_square_mesh()])
        d = scene.to_dict()
        assert d["schema_version"] == SCENE_SCHEMA_VERSION

    def test_to_dict_node_geometry_counts(self):
        mesh = _square_mesh()
        scene = scene_from_meshes([mesh])
        node_json = scene.to_dict()["nodes"][0]
        assert node_json["vertex_count"] == mesh.vertex_count()
        assert node_json["triangle_count"] == mesh.triangle_count()
        assert len(node_json["positions"]) == mesh.vertex_count() * 3
        assert len(node_json["indices"]) == mesh.triangle_count() * 3

    def test_to_json_is_valid_json(self):
        scene = scene_from_meshes([_square_mesh()])
        parsed = json.loads(scene.to_json())
        assert parsed["name"] == "scene"

    def test_write_creates_file(self, tmp_path):
        scene = scene_from_meshes([_square_mesh()])
        out = scene.write(tmp_path / "scene.json")
        assert out.exists()
        parsed = json.loads(out.read_text(encoding="utf-8"))
        assert "nodes" in parsed

    def test_materials_serialized_with_expected_fields(self):
        mat = PBRMaterial(name="tugla", albedo=(0.6, 0.3, 0.2), roughness=0.8, metallic=0.0)
        scene = scene_from_meshes([_square_mesh()], materials=[mat])
        mat_json = scene.to_dict()["materials"]["tugla"]
        assert mat_json["albedo"] == [0.6, 0.3, 0.2]
        assert mat_json["roughness"] == pytest.approx(0.8)

    def test_lights_serialized(self):
        scene = Scene()
        scene.add_light(SceneLight(kind="ambient", intensity=0.4))
        scene.add_light(SceneLight(kind="directional", direction=(0.0, -1.0, 0.0)))
        lights_json = scene.to_dict()["lights"]
        assert lights_json[0]["kind"] == "ambient"
        assert lights_json[1]["direction"] == [0.0, -1.0, 0.0]

    def test_missing_normals_serialized_as_none(self):
        mesh = Mesh3D(
            vertices=[Vertex3D(0, 0, 0), Vertex3D(1, 0, 0), Vertex3D(0, 1, 0)],
            triangles=[(0, 1, 2)],
            name="normalsiz",
        )
        scene = scene_from_meshes([mesh])
        node_json = scene.to_dict()["nodes"][0]
        assert node_json["normals"] is None


class TestLightingBridge:
    def test_sun_light_to_scene_light_normalizes_intensity(self):
        sun = SunLight(color=(1.0, 1.0, 1.0), intensity_lux=120_000.0,
                        position=SolarPosition(azimuth_deg=180.0, elevation_deg=45.0))
        scene_light = sun_light_to_scene_light(sun)
        assert scene_light.kind == "directional"
        assert scene_light.intensity == pytest.approx(1.0)
        assert scene_light.direction is not None

    def test_sun_direction_is_negated_source_direction(self):
        sun = SunLight(position=SolarPosition(azimuth_deg=90.0, elevation_deg=30.0))
        scene_light = sun_light_to_scene_light(sun)
        src_dir = sun.direction()
        assert scene_light.direction == pytest.approx(tuple(-c for c in src_dir))

    def test_ambient_light_bridge(self):
        amb = AmbientLight(color=(0.5, 0.5, 0.6), intensity=0.3)
        scene_light = ambient_light_to_scene_light(amb)
        assert scene_light.kind == "ambient"
        assert scene_light.intensity == pytest.approx(0.3)


class TestMultiNodeScene:
    def test_multiple_nodes_independent_transforms(self):
        scene = Scene()
        scene.add_mesh(_square_mesh(), translation=(0.0, 0.0, 0.0))
        scene.add_mesh(_square_mesh(), translation=(50.0, 0.0, 0.0))
        d = scene.to_dict()
        assert d["nodes"][0]["translation"] == [0.0, 0.0, 0.0]
        assert d["nodes"][1]["translation"] == [50.0, 0.0, 0.0]

    def test_large_scene_bounding_box_encloses_all_nodes(self):
        scene = Scene()
        for i in range(20):
            scene.add_mesh(_square_mesh(), translation=(i * 15.0, 0.0, 0.0))
        (lx, _, _), (hx, _, _) = scene.bounding_box()
        assert lx == pytest.approx(0.0)
        assert hx == pytest.approx(19 * 15.0 + 10.0)
