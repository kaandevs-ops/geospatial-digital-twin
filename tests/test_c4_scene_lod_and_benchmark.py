"""
ROADMAP_V7.md Faz C4 (LOD/instancing/performans), 2. dilim testleri:
`performance.scene_lod` (mesafe bazlı LOD seçimi) +
`performance.mixed_scene_benchmark` (B3'ün "1000+ karışık feature" kabul
kriteri ölçümü).

Ağ gerektirmez, tamamen sentetik veriyle çalışır.
"""

from __future__ import annotations

from harita.commerce_props import OutdoorSeatingItem
from harita.core_engine.geometry_engine import Point2D
from harita.data_engine.spatial_index import AABB3D
from harita.mesh_engine import Mesh3D, MeshBuilder
from harita.mesh_engine.batching import InstanceTransform
from harita.performance.culling import FrustumCulling, LODLevel, LODManager, OcclusionCulling
from harita.performance.mixed_scene_benchmark import (
    DEFAULT_MIXED_FEATURE_COUNT,
    generate_synthetic_mixed_scene,
    run_mixed_scene_benchmark,
)
from harita.performance.scene_instancing import InstanceGroup, build_scene_instancing_result
from harita.performance.scene_lod import (
    CULLED,
    FRUSTUM_CULLED,
    FULL,
    IMPOSTOR,
    IMPOSTOR_TRIANGLE_FACTOR,
    OCCLUDED,
    apply_lod_to_group,
    apply_scene_lod,
    build_cross_billboard_impostor,
    building_occluder_aabbs,
    default_instance_lod_manager,
    occluder_aabbs_from_scene,
)
from harita.street_furniture import StreetFurnitureItem, StreetFurnitureType
from harita.vegetation.types import TreeSpecies, VegetationInstance
from harita.visualization.camera_rig import Camera

# --------------------------------------------------------------------------- #
# default_instance_lod_manager
# --------------------------------------------------------------------------- #


def test_default_lod_manager_has_three_levels_in_order():
    manager = default_instance_lod_manager()
    assert [lvl.mesh_key for lvl in manager.levels] == [FULL, IMPOSTOR, CULLED]


def test_default_lod_manager_selects_full_at_zero_distance():
    manager = default_instance_lod_manager()
    assert manager.select(0.0).mesh_key == FULL


def test_default_lod_manager_selects_culled_beyond_impostor_distance():
    manager = default_instance_lod_manager(full_distance_m=10.0, impostor_distance_m=50.0)
    assert manager.select(1000.0).mesh_key == CULLED


# --------------------------------------------------------------------------- #
# apply_lod_to_group
# --------------------------------------------------------------------------- #


def _street_furniture_group() -> InstanceGroup:
    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(5, 0)),
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(500, 0)),
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(150, 0)),
    ]
    groups = build_scene_instancing_result(street_furniture=items).groups
    assert len(groups) == 1
    return next(iter(groups.values()))


def test_apply_lod_buckets_by_distance():
    group = _street_furniture_group()
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=60.0, impostor_distance_m=250.0)
    lod_group = apply_lod_to_group(group, camera, manager)
    assert lod_group.instance_count(FULL) == 1
    assert lod_group.instance_count(IMPOSTOR) == 1
    assert lod_group.instance_count(CULLED) == 1
    assert lod_group.total_instance_count() == 3


def test_apply_lod_preserves_total_instance_count():
    group = _street_furniture_group()
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    lod_group = apply_lod_to_group(group, camera)
    assert lod_group.total_instance_count() == group.instance_count()


def test_rendered_triangle_count_excludes_culled_and_discounts_impostor():
    manager = LODManager(
        [LODLevel(max_distance=10.0, mesh_key=FULL), LODLevel(max_distance=1e9, mesh_key=IMPOSTOR)]
    )
    group = InstanceGroup(
        template_key="t",
        base_mesh=_street_furniture_group().base_mesh,
        transforms=[
            InstanceTransform(translation=(0.0, 0.0, 0.0)),  # full
            InstanceTransform(translation=(100.0, 0.0, 0.0)),  # impostor
        ],
    )
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    lod_group = apply_lod_to_group(group, camera, manager)
    base_tris = group.base_triangle_count()
    expected = int(base_tris * 1 + base_tris * 1 * IMPOSTOR_TRIANGLE_FACTOR)
    assert lod_group.rendered_triangle_count() == expected


def test_all_culled_group_has_zero_rendered_triangles():
    group = _street_furniture_group()
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    culled_manager = LODManager(
        [LODLevel(max_distance=0.001, mesh_key=FULL), LODLevel(max_distance=0.002, mesh_key=CULLED)]
    )
    lod_group = apply_lod_to_group(group, camera, culled_manager)
    assert lod_group.instance_count(FULL) == 0
    assert lod_group.rendered_triangle_count() == 0


# --------------------------------------------------------------------------- #
# apply_scene_lod (coklu kategori)
# --------------------------------------------------------------------------- #


def test_apply_scene_lod_covers_all_categories():
    vegetation = [
        VegetationInstance(
            species=TreeSpecies.CONIFER,
            x=0.0,
            y=0.0,
            z=0.0,
            height=10.0,
            canopy_radius=3.0,
            rotation_deg=0.0,
            seed=1,
        ),
        VegetationInstance(
            species=TreeSpecies.CONIFER,
            x=1000.0,
            y=0.0,
            z=0.0,
            height=10.0,
            canopy_radius=3.0,
            rotation_deg=0.0,
            seed=2,
        ),
    ]
    furniture = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(0, 0))
    ]
    result = build_scene_instancing_result(vegetation=vegetation, street_furniture=furniture)
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    lod_aware = apply_scene_lod(result, camera)
    assert set(lod_aware.groups.keys()) == set(result.groups.keys())
    assert lod_aware.total_instance_count() == result.total_instance_count()
    # Cok uzak agac culled kovasina dusmeli (varsayilan impostor esigi 250m).
    assert lod_aware.total_instance_count(CULLED) >= 1


def test_culled_ratio_is_between_zero_and_one():
    vegetation = [
        VegetationInstance(
            species=TreeSpecies.CONIFER,
            x=float(i * 50),
            y=0.0,
            z=0.0,
            height=8.0,
            canopy_radius=2.0,
            rotation_deg=0.0,
            seed=i,
        )
        for i in range(20)
    ]
    result = build_scene_instancing_result(vegetation=vegetation)
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    lod_aware = apply_scene_lod(result, camera)
    assert 0.0 <= lod_aware.culled_ratio() <= 1.0


def test_empty_scene_instancing_result_yields_empty_lod_result():
    result = build_scene_instancing_result()
    camera = Camera()
    lod_aware = apply_scene_lod(result, camera)
    assert lod_aware.groups == {}
    assert lod_aware.total_instance_count() == 0
    assert lod_aware.culled_ratio() == 0.0


# --------------------------------------------------------------------------- #
# mixed_scene_benchmark - B3 kabul kriteri
# --------------------------------------------------------------------------- #


def test_generate_synthetic_mixed_scene_reaches_requested_scale():
    scene = generate_synthetic_mixed_scene(feature_count=1200, seed=7)
    total = sum(len(v) for v in scene.values())
    assert total >= 1000


def test_generate_synthetic_mixed_scene_is_deterministic():
    a = generate_synthetic_mixed_scene(feature_count=300, seed=99)
    b = generate_synthetic_mixed_scene(feature_count=300, seed=99)
    assert len(a["vegetation"]) == len(b["vegetation"])
    assert [v.x for v in a["vegetation"]] == [v.x for v in b["vegetation"]]


def test_generate_synthetic_mixed_scene_covers_all_categories():
    scene = generate_synthetic_mixed_scene(feature_count=1000, seed=1)
    for key in (
        "vegetation",
        "street_furniture",
        "religious_structures",
        "playgrounds",
        "outdoor_seating",
        "communication_towers",
    ):
        assert len(scene[key]) > 0


def test_run_mixed_scene_benchmark_meets_b3_criterion():
    result = run_mixed_scene_benchmark(feature_count=DEFAULT_MIXED_FEATURE_COUNT, seed=42)
    assert result.meets_b3_criterion(min_feature_count=1000)
    assert result.feature_count >= 1000


def test_run_mixed_scene_benchmark_reduces_triangles_vs_naive():
    result = run_mixed_scene_benchmark(feature_count=1200, seed=42)
    assert result.lod_rendered_triangle_count() <= result.naive_triangle_count()
    assert 0.0 <= result.triangle_reduction_ratio_vs_naive() <= 1.0


def test_run_mixed_scene_benchmark_has_positive_instance_and_template_counts():
    result = run_mixed_scene_benchmark(feature_count=1200, seed=42)
    assert result.total_instance_count() >= 1000
    assert result.total_template_count() > 0


def test_run_mixed_scene_benchmark_timings_are_nonnegative():
    result = run_mixed_scene_benchmark(feature_count=1200, seed=42)
    assert result.build_seconds >= 0.0
    assert result.lod_seconds >= 0.0


def test_run_mixed_scene_benchmark_small_scale_fails_b3_criterion():
    result = run_mixed_scene_benchmark(feature_count=50, seed=42)
    assert not result.meets_b3_criterion(min_feature_count=1000)


# ======================================================================== #
# Bu oturumda eklendi: build_cross_billboard_impostor (gerçek impostor mesh
# üretimi - önceki dilimin dürüstlük notundaki eksiğin kısmi kapanışı).
# ======================================================================== #


def test_build_cross_billboard_impostor_produces_real_geometry():
    from harita.mesh_engine import Mesh3D, Vertex3D

    base = Mesh3D(
        vertices=[Vertex3D(0.0, 0.0, 0.0), Vertex3D(2.0, 0.0, 0.0), Vertex3D(1.0, 1.0, 6.0)],
        triangles=[(0, 1, 2)],
        name="tree_conifer_8m",
    )
    impostor = build_cross_billboard_impostor(base)
    assert impostor.triangle_count() == 4
    assert impostor.vertex_count() == 8
    assert impostor.name == "tree_conifer_8m_impostor"
    # Impostor'un dikey aralığı taban mesh'in bounding box'ıyla tutarlı olmalı.
    (_, _, base_min_z), (_, _, base_max_z) = base.bounding_box()
    (_, _, imp_min_z), (_, _, imp_max_z) = impostor.bounding_box()
    assert imp_min_z == base_min_z
    assert imp_max_z == base_max_z


def test_build_cross_billboard_impostor_empty_mesh_is_safe():
    from harita.mesh_engine import Mesh3D

    empty = Mesh3D(name="empty")
    impostor = build_cross_billboard_impostor(empty)
    assert impostor.triangle_count() == 0
    assert impostor.vertex_count() == 0


def test_lod_instance_group_build_impostor_mesh_uses_base_mesh():
    manager = default_instance_lod_manager(full_distance_m=60.0, impostor_distance_m=250.0)
    base_group = _street_furniture_group()
    group = InstanceGroup(
        template_key=base_group.template_key,
        base_mesh=base_group.base_mesh,
        transforms=[InstanceTransform(translation=(100.0, 0.0, 0.0))],  # impostor bucket
    )
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    lod_group = apply_lod_to_group(group, camera, manager)
    impostor_mesh = lod_group.build_impostor_mesh()
    assert impostor_mesh is not None
    assert impostor_mesh.triangle_count() == 4


def test_lod_instance_group_without_base_mesh_returns_none_for_impostor():
    from harita.performance.scene_lod import LODInstanceGroup

    lod_group = LODInstanceGroup(template_key="x", base_mesh_triangle_count=10, buckets={})
    assert lod_group.build_impostor_mesh() is None


# ======================================================================== #
# Bu oturumda eklendi: impostor_material_for (kategori-uygun PBRMaterial -
# B3'ün "doku/materyal ataması" maddesinin kapanışı).
# ======================================================================== #


def test_impostor_material_for_known_categories():
    from harita.material_engine import PBRMaterial
    from harita.performance.scene_lod import impostor_material_for

    for prefix, expected_name in [
        ("tree:conifer:8", "impostor_tree"),
        ("furniture:bench", "impostor_furniture"),
        ("religious:minaret", "impostor_religious"),
        ("commerce:seating", "impostor_commerce"),
        ("power:tower", "impostor_power"),
        ("sport:playground", "impostor_sport"),
    ]:
        mat = impostor_material_for(prefix)
        assert isinstance(mat, PBRMaterial)
        assert mat.name == expected_name
        assert 0.0 <= mat.roughness <= 1.0
        assert 0.0 <= mat.opacity <= 1.0


def test_impostor_material_for_unknown_category_falls_back_to_neutral_default():
    from harita.performance.scene_lod import impostor_material_for

    mat = impostor_material_for("unknown_category:x")
    assert mat.name == "impostor_unknown_category"
    assert mat.albedo == (0.6, 0.6, 0.6)


def test_lod_instance_group_build_impostor_material_matches_template_key():
    from harita.performance.scene_lod import LODInstanceGroup

    lod_group = LODInstanceGroup(
        template_key="tree:conifer:8", base_mesh_triangle_count=10, buckets={}
    )
    mat = lod_group.build_impostor_material()
    assert mat.name == "impostor_tree"


# ======================================================================== #
# Bu oturumda güçlendirildi: gerçek gömülü doku (albedo_map) + scene_bridge
# serileştirme köprüsü - önceki oturumda "yalnızca metaveri, gerçek doku
# dosyası yok" olarak bırakılan zayıf halka.
# ======================================================================== #


def test_build_impostor_texture_produces_real_pixel_data():
    from harita.performance.scene_lod import build_impostor_texture

    tex = build_impostor_texture("tree:conifer:8", size=8)
    assert tex.width == 8 and tex.height == 8 and tex.channels == 3
    assert len(tex.pixels) == 8 * 8 * 3
    # Dama-tahtası deseni: en az iki farklı piksel rengi olmalı (tek-düze değil).
    distinct = {tex.texel(x, y) for x in range(8) for y in range(8)}
    assert len(distinct) >= 2


def test_impostor_material_for_embeds_texture_by_default():
    from harita.performance.scene_lod import decode_impostor_texture_data_uri, impostor_material_for

    mat = impostor_material_for("furniture:bench")
    assert mat.albedo_map is not None
    tex = decode_impostor_texture_data_uri(mat.albedo_map)
    assert tex.width > 0 and tex.height > 0


def test_impostor_material_for_without_texture_flag():
    from harita.performance.scene_lod import impostor_material_for

    mat = impostor_material_for("furniture:bench", with_texture=False)
    assert mat.albedo_map is None


def test_decode_impostor_texture_data_uri_rejects_bad_prefix():
    import pytest
    from harita.performance.scene_lod import decode_impostor_texture_data_uri

    with pytest.raises(ValueError):
        decode_impostor_texture_data_uri("not-a-valid-uri")


def test_scene_bridge_serializes_albedo_map_when_present():
    from harita.material_engine import PBRMaterial
    from harita.mesh_engine import Mesh3D, Vertex3D
    from harita.render_engine.scene_bridge import Scene

    mesh = Mesh3D(
        vertices=[Vertex3D(0, 0, 0), Vertex3D(1, 0, 0), Vertex3D(0, 1, 0)],
        triangles=[(0, 1, 2)],
        name="tri",
    )
    mat = PBRMaterial(name="with_map", albedo_map="data:harita-texture-v1;base64,ABC")
    scene = Scene()
    scene.add_mesh(mesh, material=mat)
    d = scene.to_dict()
    assert d["materials"]["with_map"]["albedo_map"] == "data:harita-texture-v1;base64,ABC"


def test_scene_bridge_omits_map_fields_when_absent_regression():
    from harita.material_engine import PBRMaterial
    from harita.mesh_engine import Mesh3D, Vertex3D
    from harita.render_engine.scene_bridge import Scene

    mesh = Mesh3D(
        vertices=[Vertex3D(0, 0, 0), Vertex3D(1, 0, 0), Vertex3D(0, 1, 0)],
        triangles=[(0, 1, 2)],
        name="tri2",
    )
    mat = PBRMaterial(name="flat", albedo=(0.5, 0.5, 0.5))
    scene = Scene()
    scene.add_mesh(mesh, material=mat)
    entry = scene.to_dict()["materials"]["flat"]
    for key in ("albedo_map", "roughness_map", "metallic_map", "normal_map", "ao_map"):
        assert key not in entry


# --------------------------------------------------------------------------- #
# ROADMAP_V7.md "Kalan": FrustumCulling ile LOD kovalarının birleşimi
# --------------------------------------------------------------------------- #


def test_apply_lod_without_frustum_param_is_unchanged_regression():
    """`frustum=None` (varsayılan) davranışı önceki oturumla birebir aynı
    kalmalı - regresyon garantisi."""
    group = _street_furniture_group()
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=60.0, impostor_distance_m=250.0)
    lod_group = apply_lod_to_group(group, camera, manager)
    assert FRUSTUM_CULLED not in lod_group.buckets
    assert lod_group.instance_count(FULL) == 1
    assert lod_group.instance_count(IMPOSTOR) == 1
    assert lod_group.instance_count(CULLED) == 1


def test_apply_lod_with_frustum_adds_frustum_culled_bucket():
    group = _street_furniture_group()  # instances at x=5, x=150, x=500
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=1000.0, impostor_distance_m=2000.0)
    lod_group = apply_lod_to_group(group, camera, manager)
    assert lod_group.instance_count(FULL) == 3  # hepsi mesafe icinde full detay

    wide_frustum = FrustumCulling(aspect=16 / 9, near=0.1, far=5000.0)
    lod_group_frustum = apply_lod_to_group(group, camera, manager, frustum=wide_frustum)
    # Kameranin baktigi yonde (forward = +X), hepsi konide -> hala FULL.
    assert lod_group_frustum.instance_count(FULL) == 3
    assert lod_group_frustum.instance_count(FRUSTUM_CULLED) == 0


def test_apply_lod_frustum_culls_instances_behind_camera():
    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(20, 0)),
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(-20, 0)),
    ]
    groups = build_scene_instancing_result(street_furniture=items).groups
    group = next(iter(groups.values()))
    # Kamera +X yonune bakiyor: x=20 konide, x=-20 kameranin arkasinda.
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=1000.0, impostor_distance_m=2000.0)
    frustum = FrustumCulling(aspect=16 / 9, near=0.1, far=5000.0)
    lod_group = apply_lod_to_group(group, camera, manager, frustum=frustum)
    assert lod_group.instance_count(FULL) == 1
    assert lod_group.instance_count(FRUSTUM_CULLED) == 1


def test_apply_lod_frustum_skips_already_distance_culled_instances():
    """Mesafeyle zaten `culled` olan instance frustum testine hic girmez -
    `frustum_culled` kovasina degil `culled` kovasinda kalmali (cifte
    sayim yok)."""
    group = _street_furniture_group()  # x=500 -> distance-culled with tight manager
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    tight_manager = default_instance_lod_manager(full_distance_m=10.0, impostor_distance_m=50.0)
    frustum = FrustumCulling(aspect=16 / 9, near=0.1, far=5000.0)
    lod_group = apply_lod_to_group(group, camera, tight_manager, frustum=frustum)
    assert (
        lod_group.instance_count(CULLED) == 2
    )  # x=150 ve x=500 ikisi de impostor_distance_m=50'yi asiyor
    total = lod_group.total_instance_count()
    assert total == 3
    assert (
        lod_group.instance_count(FULL)
        + lod_group.instance_count(IMPOSTOR)
        + lod_group.instance_count(CULLED)
        + lod_group.instance_count(FRUSTUM_CULLED)
        == total
    )


def test_instance_world_aabb_returns_none_for_empty_mesh():
    """`InstanceGroup.base_mesh` roadmap'in tipinde zorunlu bir alan
    (Optional degil), bu yuzden `apply_lod_to_group` icinde frustum
    testinin atlanmasi gereken tek gercek durum bos (0 vertex) bir
    `base_mesh`'tir - `_instance_world_aabb` bunun icin `None` doner ve
    `apply_lod_to_group` bu durumda mesafe kovasini degistirmeden birakir
    (bkz. fonksiyonun kendi `world_aabb is not None` kontrolu)."""
    from harita.mesh_engine import Mesh3D
    from harita.mesh_engine.batching import InstanceTransform
    from harita.performance.scene_lod import _instance_world_aabb

    empty_mesh = Mesh3D(name="empty")
    assert _instance_world_aabb(empty_mesh, InstanceTransform(translation=(0, 0, 0))) is None


def test_apply_scene_lod_propagates_frustum_to_all_groups():
    from harita.commerce_props import OutdoorSeatingItem

    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(-30, 0)),
    ]
    seating = [
        OutdoorSeatingItem(position=Point2D(-30, 5)),
    ]
    result = build_scene_instancing_result(street_furniture=items, outdoor_seating=seating)
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=1000.0, impostor_distance_m=2000.0)
    frustum = FrustumCulling(aspect=16 / 9, near=0.1, far=5000.0)
    lod_aware = apply_scene_lod(result, camera, manager, frustum=frustum)
    assert len(lod_aware.groups) == 2
    assert lod_aware.total_instance_count(FRUSTUM_CULLED) == len(lod_aware.groups)


def test_culled_ratio_includes_frustum_culled():
    group = _street_furniture_group()
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=1000.0, impostor_distance_m=2000.0)
    # Kamerayi -X'e cevir: _street_furniture_group'un tum instance'lari (x=5,150,500 - hepsi +X) konide olmayacak.
    camera_away = Camera(position=(0.0, 0.0, 0.0), target=(-1.0, 0.0, 0.0))
    frustum = FrustumCulling(aspect=16 / 9, near=0.1, far=5000.0)
    lod_group = apply_lod_to_group(group, camera_away, manager, frustum=frustum)
    from harita.performance.scene_lod import LODAwareSceneResult

    scene_result = LODAwareSceneResult(groups={"bench": lod_group})
    assert scene_result.culled_ratio() == 1.0
    assert scene_result.frustum_culled_ratio() == 1.0


def test_frustum_culled_ratio_zero_when_frustum_not_used():
    group = _street_furniture_group()
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=60.0, impostor_distance_m=250.0)
    lod_group = apply_lod_to_group(group, camera, manager)
    from harita.performance.scene_lod import LODAwareSceneResult

    scene_result = LODAwareSceneResult(groups={"bench": lod_group})
    assert scene_result.frustum_culled_ratio() == 0.0


# --------------------------------------------------------------------------- #
# Kapsam dışı bırakılan kalem, kullanıcı isteğiyle işlendi: OcclusionCulling
# ile LOD kovalarının birleşimi (occlusion politikası: bkz. modül docstring'i
# - yalnızca bina bounding box'ları occluder sayılır, `building_occluder_aabbs`)
# --------------------------------------------------------------------------- #


def test_apply_lod_without_occlusion_param_is_unchanged_regression():
    """`occlusion=None` (varsayılan) davranışı önceki oturumla birebir aynı
    kalmalı - regresyon garantisi."""
    group = _street_furniture_group()
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=60.0, impostor_distance_m=250.0)
    lod_group = apply_lod_to_group(group, camera, manager)
    assert OCCLUDED not in lod_group.buckets
    assert lod_group.instance_count(FULL) == 1
    assert lod_group.instance_count(IMPOSTOR) == 1
    assert lod_group.instance_count(CULLED) == 1


def test_apply_lod_with_occlusion_adds_occluded_bucket():
    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(20, 0)),
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(-20, 0)),
    ]
    groups = build_scene_instancing_result(street_furniture=items).groups
    group = next(iter(groups.values()))
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=1000.0, impostor_distance_m=2000.0)
    # Occluder, kameradan +X yönünde (x=20'ye giden yolun üzerinde) - x=-20'ye
    # giden yolu etkilemez (ters yönde).
    occluder = AABB3D(min_x=5.0, min_y=-2.0, min_z=-2.0, max_x=8.0, max_y=2.0, max_z=2.0)
    occlusion = OcclusionCulling([occluder])
    lod_group = apply_lod_to_group(group, camera, manager, occlusion=occlusion)
    assert lod_group.instance_count(OCCLUDED) == 1
    assert lod_group.instance_count(FULL) == 1


def test_apply_lod_occlusion_skips_already_culled_instances():
    """Mesafeyle zaten `culled` olan instance occlusion testine hiç girmez -
    `occluded` kovasına düşmez (çifte sayım yok)."""
    group = _street_furniture_group()  # x=500 -> tight manager ile distance-culled
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    tight_manager = default_instance_lod_manager(full_distance_m=10.0, impostor_distance_m=50.0)
    # Sahnenin tamamını kaplayan dev bir occluder - eğer occlusion testi
    # zaten-culled instance'lara da uygulanıyor olsaydı hepsi occluded
    # kovasına düşerdi.
    huge_occluder = AABB3D(min_x=-1.0, min_y=-1.0, min_z=-1.0, max_x=1000.0, max_y=1.0, max_z=1.0)
    occlusion = OcclusionCulling([huge_occluder])
    lod_group = apply_lod_to_group(group, camera, tight_manager, occlusion=occlusion)
    assert lod_group.instance_count(CULLED) == 2
    total = lod_group.total_instance_count()
    assert (
        lod_group.instance_count(FULL)
        + lod_group.instance_count(IMPOSTOR)
        + lod_group.instance_count(CULLED)
        + lod_group.instance_count(OCCLUDED)
        == total
    )


def test_building_occluder_aabbs_skips_empty_meshes():
    empty_mesh = Mesh3D(name="empty")
    real_mesh = MeshBuilder.build_box(width=10.0, depth=10.0, height=5.0, name="building")
    aabbs = building_occluder_aabbs([empty_mesh, real_mesh])
    assert len(aabbs) == 1
    aabb = aabbs[0]
    assert aabb.min_z == 0.0
    assert aabb.max_z == 5.0


def test_occluder_aabbs_from_scene_matches_building_only_when_no_extra_meshes():
    """Geriye dönük uyumluluk: yalnızca bina verildiğinde eski fonksiyonla
    birebir aynı sonucu üretmeli."""
    building = MeshBuilder.build_box(width=10.0, depth=10.0, height=5.0, name="building")
    old = building_occluder_aabbs([building])
    new = occluder_aabbs_from_scene([building])
    assert len(old) == len(new) == 1
    assert (old[0].min_x, old[0].max_z) == (new[0].min_x, new[0].max_z)


def test_occluder_aabbs_from_scene_includes_large_road_and_water_and_excludes_small_props():
    building = MeshBuilder.build_box(width=10.0, depth=10.0, height=5.0, name="building")
    # Buyuk, hacimli bir "kopru govdesi" gibi dusunulebilecek yol mesh'i -
    # yeterince yuksek ve genis, occluder olmali.
    big_road_slab = MeshBuilder.build_box(width=8.0, depth=8.0, height=3.0, name="bridge_deck")
    # Genis ama duz (yuksekligi cok kucuk) bir yol seridi - occluder OLMAMALI.
    flat_road = MeshBuilder.build_box(width=20.0, depth=5.0, height=0.05, name="flat_road")
    # Buyuk bir su yapisi (baraj/rihtim benzeri) - occluder olmali.
    big_water = MeshBuilder.build_box(width=15.0, depth=15.0, height=4.0, name="dam")
    # Ince/uzun bir direk - dar taban alani nedeniyle occluder OLMAMALI.
    pole = MeshBuilder.build_box(width=0.2, depth=0.2, height=6.0, name="street_lamp")

    aabbs = occluder_aabbs_from_scene(
        [building],
        road_meshes=[big_road_slab, flat_road],
        water_meshes=[big_water],
        prop_meshes=[pole],
    )
    # bina + kopru govdesi + baraj = 3; duz yol ve direk elenmis olmali.
    assert len(aabbs) == 3


def test_apply_scene_lod_propagates_occlusion_to_all_groups():
    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(20, 0)),
    ]
    seating = [
        OutdoorSeatingItem(position=Point2D(20, 5)),
    ]
    result = build_scene_instancing_result(street_furniture=items, outdoor_seating=seating)
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=1000.0, impostor_distance_m=2000.0)
    occluder = AABB3D(min_x=5.0, min_y=-10.0, min_z=-10.0, max_x=10.0, max_y=10.0, max_z=10.0)
    occlusion = OcclusionCulling([occluder])
    lod_aware = apply_scene_lod(result, camera, manager, occlusion=occlusion)
    assert len(lod_aware.groups) == 2
    assert lod_aware.total_instance_count(OCCLUDED) == len(lod_aware.groups)


def test_culled_ratio_includes_occluded():
    group = _street_furniture_group()  # x=5, x=150, x=500
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=1000.0, impostor_distance_m=2000.0)
    huge_occluder = AABB3D(min_x=1.0, min_y=-1.0, min_z=-1.0, max_x=2.0, max_y=1.0, max_z=1.0)
    occlusion = OcclusionCulling([huge_occluder])
    lod_group = apply_lod_to_group(group, camera, manager, occlusion=occlusion)
    from harita.performance.scene_lod import LODAwareSceneResult

    scene_result = LODAwareSceneResult(groups={"bench": lod_group})
    assert scene_result.culled_ratio() == 1.0
    assert scene_result.occluded_ratio() == 1.0


def test_occluded_ratio_zero_when_occlusion_not_used():
    group = _street_furniture_group()
    camera = Camera(position=(0.0, 0.0, 0.0), target=(1.0, 0.0, 0.0))
    manager = default_instance_lod_manager(full_distance_m=60.0, impostor_distance_m=250.0)
    lod_group = apply_lod_to_group(group, camera, manager)
    from harita.performance.scene_lod import LODAwareSceneResult

    scene_result = LODAwareSceneResult(groups={"bench": lod_group})
    assert scene_result.occluded_ratio() == 0.0
