"""Phase 9 (Visualization) için birim testleri."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.mesh_engine import Mesh3D, Vertex3D
from harita.visualization import (
    Camera,
    CameraMode,
    CameraRig,
    CinematicKeyframe,
    ExplosionView,
    FloorBand,
    RenderPass,
    RenderPassType,
    RenderPipeline,
    SectionPlane,
    SectionView,
    XRayState,
    floor_bands_from_heights,
    occlusion_ratio,
)

# ============================================================================ #
# render_passes.py
# ============================================================================ #


def test_render_pass_default_parameters():
    p = RenderPass(pass_type=RenderPassType.BLOOM)
    assert p.parameters["threshold"] == 1.0
    assert p.enabled is True


def test_render_pass_with_parameter_override():
    p = RenderPass(pass_type=RenderPassType.FOG, parameters={"density": 0.1})
    assert p.parameters["density"] == 0.1
    assert "color" in p.parameters  # default korunur

    p2 = p.with_parameter("density", 0.5)
    assert p2.parameters["density"] == 0.5
    assert p.parameters["density"] == 0.1  # orijinal değişmez


def test_render_pipeline_default_order_and_toggle():
    pipeline = RenderPipeline.default_pbr_pipeline()
    types = [p.pass_type for p in pipeline.passes]
    assert types[0] == RenderPassType.PBR
    assert RenderPassType.FXAA in types

    pipeline.set_enabled(RenderPassType.BLOOM, False)
    active_types = [p.pass_type for p in pipeline.active_passes()]
    assert RenderPassType.BLOOM not in active_types
    assert RenderPassType.PBR in active_types


def test_render_pipeline_reorder_and_remove():
    pipeline = RenderPipeline.default_pbr_pipeline()
    pipeline.reorder([RenderPassType.FXAA, RenderPassType.PBR])
    assert pipeline.passes[0].pass_type == RenderPassType.FXAA
    assert pipeline.passes[1].pass_type == RenderPassType.PBR

    pipeline.remove(RenderPassType.MSAA)
    assert pipeline.get(RenderPassType.MSAA) is None


# ============================================================================ #
# camera_rig.py
# ============================================================================ #


def test_camera_orbit_preserves_distance():
    cam = Camera(position=(10.0, 0.0, 0.0), target=(0.0, 0.0, 0.0))
    rig = CameraRig(camera=cam, mode=CameraMode.ORBIT)
    before = math.dist(cam.position, cam.target)
    rig.orbit(delta_yaw_deg=45, delta_pitch_deg=10)
    after = math.dist(rig.camera.position, rig.camera.target)
    assert abs(before - after) < 1e-6
    assert rig.camera.position != (10.0, 0.0, 0.0)


def test_camera_orbit_zoom_changes_distance():
    cam = Camera(position=(10.0, 0.0, 0.0), target=(0.0, 0.0, 0.0))
    rig = CameraRig(camera=cam, mode=CameraMode.ORBIT)
    rig.orbit(delta_yaw_deg=0, delta_pitch_deg=0, delta_distance=5.0)
    after = math.dist(rig.camera.position, rig.camera.target)
    assert abs(after - 15.0) < 1e-6


def test_camera_fps_move_translates_both_position_and_target():
    cam = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    rig = CameraRig(camera=cam, mode=CameraMode.FPS)
    rig.fps_move(forward_amount=2.0, strafe_amount=0.0)
    assert rig.camera.position[0] == pytest.approx(2.0)
    assert rig.camera.target[0] == pytest.approx(3.0)


def test_camera_drone_altitude_hold():
    cam = Camera(position=(0.0, 0.0, 5.0), target=(1.0, 0.0, 5.0))
    rig = CameraRig(camera=cam, mode=CameraMode.DRONE)
    rig.drone_move(forward_amount=1.0, strafe_amount=0.0, altitude_delta=2.0)
    assert rig.camera.position[2] == pytest.approx(7.0)


def test_camera_cinematic_interpolation():
    rig = CameraRig()
    kf1 = CinematicKeyframe(time_s=0.0, position=(0, 0, 0), target=(1, 0, 0), fov_deg=60)
    kf2 = CinematicKeyframe(time_s=10.0, position=(10, 0, 0), target=(11, 0, 0), fov_deg=90)
    rig.set_cinematic_track([kf2, kf1])  # ters sırada verildi, sort edilmeli

    cam_mid = rig.cinematic_at(5.0)
    assert cam_mid.position[0] == pytest.approx(5.0)
    assert cam_mid.fov_deg == pytest.approx(75.0)

    cam_before = rig.cinematic_at(-5.0)
    assert cam_before.position[0] == pytest.approx(0.0)

    cam_after = rig.cinematic_at(100.0)
    assert cam_after.position[0] == pytest.approx(10.0)


def test_camera_free_fly_combines_look_and_move():
    cam = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    rig = CameraRig(camera=cam, mode=CameraMode.FREE_FLY)
    rig.free_fly(
        forward_amount=1.0, strafe_amount=0.0, up_amount=0.5, yaw_delta_deg=90, pitch_delta_deg=0
    )
    assert rig.camera.position[2] == pytest.approx(0.5)


# ============================================================================ #
# section_view.py
# ============================================================================ #


def _cube_mesh() -> Mesh3D:
    """[0,1]^3 birim küp, 12 üçgen (6 yüz x 2)."""
    pts = [
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
    ]
    verts = [Vertex3D(*p) for p in pts]
    faces = [
        (0, 1, 2),
        (0, 2, 3),  # bottom
        (4, 6, 5),
        (4, 7, 6),  # top
        (0, 4, 5),
        (0, 5, 1),  # front
        (1, 5, 6),
        (1, 6, 2),  # right
        (2, 6, 7),
        (2, 7, 3),  # back
        (3, 7, 4),
        (3, 4, 0),  # left
    ]
    return Mesh3D(vertices=verts, triangles=faces, name="cube")


def test_section_view_splits_cube_at_midplane():
    cube = _cube_mesh()
    plane = SectionPlane(point=(0.5, 0.5, 0.5), normal=(0, 0, 1))
    keep, away = SectionView.cut(cube, plane)

    # Üst yarı (z>=0.5) 'keep' tarafında olmalı, tüm vertexlerin z'si >= 0.5 - eps
    assert all(v.z >= 0.5 - 1e-9 for v in keep.vertices)
    assert all(v.z <= 0.5 + 1e-9 for v in away.vertices)
    assert keep.triangle_count() > 0
    assert away.triangle_count() > 0


def test_section_view_full_mesh_on_one_side():
    cube = _cube_mesh()
    plane = SectionPlane(point=(0.0, 0.0, -1.0), normal=(0, 0, 1))
    keep, away = SectionView.cut(cube, plane)
    assert keep.triangle_count() == cube.triangle_count()
    assert away.triangle_count() == 0


# ============================================================================ #
# xray.py
# ============================================================================ #


def test_xray_state_material_opacity():
    xray = XRayState()
    assert xray.opacity_for("glass") == 1.0  # disabled iken her zaman opak

    xray.enabled = True
    xray.default_opacity = 0.3
    xray.set_material_opacity("glass", 0.1)
    assert xray.opacity_for("glass") == pytest.approx(0.1)
    assert xray.opacity_for("concrete") == pytest.approx(0.3)


def test_xray_toggle():
    xray = XRayState()
    assert xray.toggle() is True
    assert xray.toggle() is False


def test_occlusion_ratio_range():
    cube = _cube_mesh()
    ratio = occlusion_ratio(cube, view_point=(0.5, 0.5, 5.0))
    assert 0.0 <= ratio <= 1.0


# ============================================================================ #
# explosion_view.py
# ============================================================================ #


def _two_floor_mesh() -> tuple[Mesh3D, list]:
    """İki kat: kat0 z=[0,3), kat1 z=[3,6). Her kat basit bir üçgen."""
    verts = [
        Vertex3D(0, 0, 1),
        Vertex3D(1, 0, 1),
        Vertex3D(0, 1, 1),  # floor 0
        Vertex3D(0, 0, 4),
        Vertex3D(1, 0, 4),
        Vertex3D(0, 1, 4),  # floor 1
    ]
    tris = [(0, 1, 2), (3, 4, 5)]
    mesh = Mesh3D(vertices=verts, triangles=tris, name="two_floor")
    bands = floor_bands_from_heights([3.0, 3.0])
    return mesh, bands


def test_floor_bands_from_heights():
    bands = floor_bands_from_heights([3.0, 2.5, 4.0])
    assert bands[0] == FloorBand(level=0, z_min=0.0, z_max=3.0)
    assert bands[1].z_min == pytest.approx(3.0)
    assert bands[2].z_max == pytest.approx(9.5)


def test_explosion_view_splits_by_floor():
    mesh, bands = _two_floor_mesh()
    view = ExplosionView(mesh, bands, gap_m=2.0)
    assert view.floor_mesh(0).triangle_count() == 1
    assert view.floor_mesh(1).triangle_count() == 1


def test_explosion_view_progress_zero_is_original_positions():
    mesh, bands = _two_floor_mesh()
    view = ExplosionView(mesh, bands, gap_m=2.0)
    state = view.state_at(0.0)
    assert state[0].offset_z == pytest.approx(0.0)
    assert state[1].offset_z == pytest.approx(0.0)
    assert state[1].mesh.vertices[0].z == pytest.approx(4.0)


def test_explosion_view_progress_one_applies_full_gap():
    mesh, bands = _two_floor_mesh()
    view = ExplosionView(mesh, bands, gap_m=2.0)
    state = view.state_at(1.0)
    assert state[0].offset_z == pytest.approx(0.0)
    assert state[1].offset_z == pytest.approx(2.0)
    # ikinci kat vertexi orijinal z(4) + 2 offset = 6
    assert state[1].mesh.vertices[0].z == pytest.approx(6.0)


def test_explosion_view_combined_mesh_has_all_triangles():
    mesh, bands = _two_floor_mesh()
    view = ExplosionView(mesh, bands, gap_m=1.0)
    combined = view.combined_mesh_at(0.5)
    assert combined.triangle_count() == mesh.triangle_count()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK: {t.__name__}")
    print(f"\n{len(tests)} test PASSED")
