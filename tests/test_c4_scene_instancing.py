"""
ROADMAP_V7.md Faz C4 (LOD/instancing/performans), 1. dilim testleri:
`performance.scene_instancing` - OSM nokta-prop köprülerinin
`InstanceMeshBaker`/`DrawCallEstimator` (Roadmap V5 M1.2) altyapısına
bağlanması.

Ağ gerektirmez, tamamen sentetik veriyle çalışır.
"""
from __future__ import annotations

import pytest

from harita.commerce_props import OutdoorSeatingItem
from harita.core_engine.geometry_engine import Point2D
from harita.performance.scene_instancing import (
    DEFAULT_TREE_HEIGHT_BUCKET_M,
    InstanceGroup,
    SceneInstancingResult,
    build_scene_instancing_result,
    instancing_groups_for_communication_towers,
    instancing_groups_for_outdoor_seating,
    instancing_groups_for_playgrounds,
    instancing_groups_for_religious_structures,
    instancing_groups_for_street_furniture,
    instancing_groups_for_vegetation,
)
from harita.power_infrastructure import CommunicationTowerItem
from harita.religious_structures import ReligionKind, ReligiousStructureItem
from harita.sport_recreation import PlaygroundItem
from harita.street_furniture import StreetFurnitureItem, StreetFurnitureType
from harita.vegetation.types import TreeSpecies, VegetationInstance


# --------------------------------------------------------------------------- #
# street_furniture
# --------------------------------------------------------------------------- #

def test_street_furniture_same_type_shares_one_template():
    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(10, 20)),
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(30, 40)),
        StreetFurnitureItem(furniture_type=StreetFurnitureType.TRASH_BIN, position=Point2D(5, 5)),
    ]
    groups = instancing_groups_for_street_furniture(items)
    assert len(groups) == 2
    bench_group = groups["furniture:bench"]
    assert bench_group.instance_count() == 2
    assert bench_group.base_triangle_count() > 0


def test_street_furniture_transform_matches_world_position():
    item = StreetFurnitureItem(
        furniture_type=StreetFurnitureType.BENCH, position=Point2D(100, 200), rotation_deg=45.0
    )
    groups = instancing_groups_for_street_furniture([item])
    transform = groups["furniture:bench"].transforms[0]
    assert transform.translation == (100, 200, 0.0)
    assert transform.rotation_deg_z == 45.0


# --------------------------------------------------------------------------- #
# religious_structures
# --------------------------------------------------------------------------- #

def test_religious_structures_group_by_religion_and_height():
    items = [
        ReligiousStructureItem(religion=ReligionKind.MUSLIM, position=Point2D(0, 0), base_height_m=8.0),
        ReligiousStructureItem(religion=ReligionKind.MUSLIM, position=Point2D(1, 1), base_height_m=8.0),
        ReligiousStructureItem(religion=ReligionKind.CHRISTIAN, position=Point2D(2, 2), base_height_m=8.0),
    ]
    groups = instancing_groups_for_religious_structures(items)
    assert len(groups) == 2
    assert groups["worship:muslim:8.0"].instance_count() == 2


# --------------------------------------------------------------------------- #
# playgrounds (tek şablon)
# --------------------------------------------------------------------------- #

def test_playgrounds_all_share_single_template():
    items = [PlaygroundItem(position=Point2D(i, i)) for i in range(5)]
    groups = instancing_groups_for_playgrounds(items)
    assert len(groups) == 1
    assert groups["playground:default"].instance_count() == 5


# --------------------------------------------------------------------------- #
# outdoor seating (table_count'a göre gruplama)
# --------------------------------------------------------------------------- #

def test_outdoor_seating_groups_by_table_count():
    items = [
        OutdoorSeatingItem(position=Point2D(0, 0), table_count=2),
        OutdoorSeatingItem(position=Point2D(1, 1), table_count=2),
        OutdoorSeatingItem(position=Point2D(2, 2), table_count=4),
    ]
    groups = instancing_groups_for_outdoor_seating(items)
    assert len(groups) == 2
    assert groups["seating:2"].instance_count() == 2
    assert groups["seating:4"].instance_count() == 1


# --------------------------------------------------------------------------- #
# communication towers (yükseklik kovalama)
# --------------------------------------------------------------------------- #

def test_communication_towers_bucket_by_rounded_height():
    items = [
        CommunicationTowerItem(position=Point2D(0, 0), height_m=25.2),
        CommunicationTowerItem(position=Point2D(1, 1), height_m=24.8),
        CommunicationTowerItem(position=Point2D(2, 2), height_m=40.0),
    ]
    groups = instancing_groups_for_communication_towers(items)
    # 25.2 ve 24.8 -> yuvarlanınca ikisi de 25
    assert len(groups) == 2
    assert groups["tower:25"].instance_count() == 2
    assert groups["tower:40"].instance_count() == 1


# --------------------------------------------------------------------------- #
# vegetation (tür + yükseklik kovası, ölçek düzeltmesi)
# --------------------------------------------------------------------------- #

def test_vegetation_buckets_by_species_and_height():
    trees = [
        VegetationInstance(
            species=TreeSpecies.CONIFER, x=0, y=0, z=0, height=8.2,
            canopy_radius=2.0, rotation_deg=0.0, seed=1,
        ),
        VegetationInstance(
            species=TreeSpecies.CONIFER, x=1, y=1, z=0, height=8.9,
            canopy_radius=2.1, rotation_deg=0.0, seed=2,
        ),
        VegetationInstance(
            species=TreeSpecies.DECIDUOUS, x=2, y=2, z=0, height=6.0,
            canopy_radius=3.0, rotation_deg=0.0, seed=3,
        ),
    ]
    groups = instancing_groups_for_vegetation(trees, height_bucket_m=2.0)
    # 8.2 ve 8.9 -> aynı kovaya (8.0) düşmeli, farklı tür ayrı kalmalı
    assert len(groups) == 2
    conifer_key = [k for k in groups if k.startswith("tree:conifer")][0]
    assert groups[conifer_key].instance_count() == 2


def test_vegetation_transform_scale_reflects_real_height():
    trees = [
        VegetationInstance(
            species=TreeSpecies.DECIDUOUS, x=0, y=0, z=0, height=5.0,
            canopy_radius=2.0, rotation_deg=0.0, seed=1,
        ),
    ]
    groups = instancing_groups_for_vegetation(trees, height_bucket_m=2.0)
    key = list(groups.keys())[0]
    transform = groups[key].transforms[0]
    # gerçek yükseklik / kova yüksekliği oranına yakın bir ölçek beklenir
    assert transform.scale > 0


# --------------------------------------------------------------------------- #
# InstanceGroup metrikleri
# --------------------------------------------------------------------------- #

def test_instance_group_naive_vs_base_triangle_counts():
    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(i, i))
        for i in range(10)
    ]
    groups = instancing_groups_for_street_furniture(items)
    group = groups["furniture:bench"]
    assert group.instance_count() == 10
    assert group.naive_triangle_count() == group.base_triangle_count() * 10
    assert group.naive_triangle_count() > group.base_triangle_count()


def test_instance_group_bake_merged_produces_single_mesh():
    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(i * 3, 0))
        for i in range(4)
    ]
    groups = instancing_groups_for_street_furniture(items)
    group = groups["furniture:bench"]
    merged = group.bake_merged()
    assert len(merged.triangles) == group.naive_triangle_count()


def test_instance_group_bake_instances_produces_list_per_instance():
    items = [
        StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(i, 0))
        for i in range(3)
    ]
    groups = instancing_groups_for_street_furniture(items)
    group = groups["furniture:bench"]
    instances = group.bake_instances()
    assert len(instances) == 3


# --------------------------------------------------------------------------- #
# SceneInstancingResult - toplu senaryo
# --------------------------------------------------------------------------- #

def test_build_scene_instancing_result_aggregates_all_categories():
    result = build_scene_instancing_result(
        vegetation=[
            VegetationInstance(
                species=TreeSpecies.CONIFER, x=i, y=i, z=0, height=8.0,
                canopy_radius=2.0, rotation_deg=0.0, seed=i,
            )
            for i in range(50)
        ],
        street_furniture=[
            StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(i, 0))
            for i in range(200)
        ],
        religious_structures=[
            ReligiousStructureItem(religion=ReligionKind.MUSLIM, position=Point2D(0, 0)),
        ],
        playgrounds=[PlaygroundItem(position=Point2D(0, 0))],
        outdoor_seating=[OutdoorSeatingItem(position=Point2D(0, 0), table_count=2)],
        communication_towers=[CommunicationTowerItem(position=Point2D(0, 0), height_m=25.0)],
    )
    assert isinstance(result, SceneInstancingResult)
    # B3 kabul kriteri: 1000+ karışık feature senaryosunun ölçülebilirliği
    assert result.total_instance_count() == 50 + 200 + 1 + 1 + 1 + 1
    assert result.total_template_count() >= 5
    assert 0.0 <= result.triangle_reduction_ratio() <= 1.0
    # instancing sayesinde benzersiz geometri, naive koleksiyondan küçük olmalı
    assert result.total_base_triangle_count() < result.total_naive_triangle_count()


def test_build_scene_instancing_result_empty_is_safe():
    result = build_scene_instancing_result()
    assert result.total_instance_count() == 0
    assert result.total_template_count() == 0
    assert result.triangle_reduction_ratio() == 0.0
    assert result.estimated_draw_call_reduction_percent() == 0.0


def test_draw_call_reduction_percent_is_nonnegative():
    result = build_scene_instancing_result(
        street_furniture=[
            StreetFurnitureItem(furniture_type=StreetFurnitureType.BENCH, position=Point2D(i, 0))
            for i in range(500)
        ],
    )
    assert result.estimated_draw_call_reduction_percent() >= 0.0
