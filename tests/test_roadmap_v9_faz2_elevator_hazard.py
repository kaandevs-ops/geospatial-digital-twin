"""
Roadmap V9 / Faz II — Katman 2.4 madde 2: "Asansör kullanma kısıtı"
====================================================================

`ROADMAP_V9.md`'nin uygulama notunda "Sırada" olarak işaretlenen kalan
madde: gerçek `indoor_navigation.BuildingNavGraph` üzerinden asansör
kenarlarının deprem senaryosunda devre dışı bırakılması
(`HazardScenarioRules.disable_elevators`).

Bu testler yalnızca `indoor_navigation` modülünü (bina üretim pipeline'ı
olmadan, doğrudan `Room`/`Stair`/`ElevatorCore` nesneleriyle) hedefler —
"input-binding olmadan headless mantık testi" ilkesiyle aynı yaklaşım.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.building_reconstruction.building_elements import ElevatorCore, Stair
from harita.building_reconstruction.room_generator import Room, RoomType
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mobility.indoor_navigation import (
    Floor, HazardScenarioRules, IndoorNavigationBuilder,
)


def _square_room(room_id: int, cx: float, cy: float, neighbors: list[int]) -> Room:
    half = 2.0
    poly = Polygon(points=[
        Point2D(cx - half, cy - half), Point2D(cx + half, cy - half),
        Point2D(cx + half, cy + half), Point2D(cx - half, cy + half),
    ])
    return Room(polygon=poly, room_type=RoomType.SALON.value, room_id=room_id, neighbors=neighbors)


def _two_floor_building_with_stair_and_elevator() -> list[Floor]:
    """Zemin + 1. kat, her katta İKİ oda: biri merdiven biri asansör
    kuyusuna en yakın oda (farklı düğümler -> farklı dikey kenarlar,
    tek-oda-per-kat senaryosunun kenar çakışması olmadan test edilebilir)."""
    ground_stair_room = _square_room(0, -5.0, 0.0, neighbors=[1])
    ground_elev_room = _square_room(1, 5.0, 0.0, neighbors=[0])
    upper_stair_room = _square_room(0, -5.0, 0.0, neighbors=[1])
    upper_elev_room = _square_room(1, 5.0, 0.0, neighbors=[0])

    stair = Stair(position=Point2D(-5.0, 0.0), width=1.2, run_length=3.0,
                   step_count=16, step_height=0.18, step_depth=0.28)
    elevator = ElevatorCore(position=Point2D(5.0, 0.0), width=1.5, depth=1.5,
                             shaft_top_z=6.0, shaft_bottom_z=0.0)

    ground = Floor(floor_index=0, rooms=[ground_stair_room, ground_elev_room],
                    stairs=[stair], elevators=[elevator])
    upper = Floor(floor_index=1, rooms=[upper_stair_room, upper_elev_room],
                   stairs=[], elevators=[])
    return [ground, upper]


def _two_floor_building_elevator_only() -> list[Floor]:
    """Zemin + 1. kat, aralarında YALNIZCA asansör bağlantısı olan bina —
    "engelli tahliye planı eksik" uyarısının pozitif (uyarı üretilmesi
    gereken) örneği."""
    ground_room = _square_room(0, 0.0, 0.0, neighbors=[])
    upper_room = _square_room(0, 0.0, 0.0, neighbors=[])
    elevator = ElevatorCore(position=Point2D(1.0, 1.0), width=1.5, depth=1.5,
                             shaft_top_z=6.0, shaft_bottom_z=0.0)
    ground = Floor(floor_index=0, rooms=[ground_room], stairs=[], elevators=[elevator])
    upper = Floor(floor_index=1, rooms=[upper_room], stairs=[], elevators=[])
    return [ground, upper]


def test_build_without_hazard_rules_elevator_stays_usable():
    floors = _two_floor_building_with_stair_and_elevator()
    bg = IndoorNavigationBuilder.build(floors)
    assert bg.elevators_disabled is False
    assert len(bg.elevator_edges) == 1
    # Asansör kenarının maliyeti okunabilir olmalı (bloke değil).
    a, b = bg.elevator_edges[0]
    assert bg.graph.edge_cost(a, b) is not None


def test_build_with_hazard_rules_disables_elevator_edge():
    floors = _two_floor_building_with_stair_and_elevator()
    rules = HazardScenarioRules(disable_elevators=True)
    bg = IndoorNavigationBuilder.build(floors, hazard_rules=rules)
    assert bg.elevators_disabled is True
    a, b = bg.elevator_edges[0]
    # Kenar graf'tan silinmedi (roadmap ilkesi), yalnızca bloke edildi.
    assert bg.graph.has_node(a) and bg.graph.has_node(b)
    assert bg.graph.edge_cost(a, b) is None


def test_disable_then_enable_elevators_is_reversible():
    floors = _two_floor_building_with_stair_and_elevator()
    bg = IndoorNavigationBuilder.build(floors)
    a, b = bg.elevator_edges[0]
    original_cost = bg.graph.edge_cost(a, b)

    bg.disable_elevators()
    assert bg.graph.edge_cost(a, b) is None

    bg.enable_elevators()
    assert bg.graph.edge_cost(a, b) == original_cost


def test_stair_edge_unaffected_by_elevator_disable():
    floors = _two_floor_building_with_stair_and_elevator()
    bg = IndoorNavigationBuilder.build(floors)
    elevator_pair = set(bg.elevator_edges[0])
    # Merdiven kenarı: iki düğüm de kat 0/1'deki tek oda -> aynı node id'ler
    # olabilir stair vs elevator farklı hedef odaya bağlanmadığı için bu
    # minimal senaryoda ayırt etmek üzere ikinci bir bağlantı ekleyelim.
    bg.disable_elevators()
    # Merdiven üzerinden hâlâ 0->1 rotası bulunabilmeli (stair_edges bloke
    # edilmedi).
    reachable = bg.unreachable_rooms_without_elevator(ground_floor_index=0)
    assert reachable == [], (
        "Merdiven mevcutken asansör kapatılınca hiçbir oda erişilemez "
        "olmamalı"
    )


def test_unreachable_rooms_flags_elevator_only_building():
    floors = _two_floor_building_elevator_only()
    bg = IndoorNavigationBuilder.build(floors)
    unreachable = bg.unreachable_rooms_without_elevator(ground_floor_index=0)
    # Yalnızca asansörle bağlı üst kat odası, asansör devre dışı kalınca
    # erişilemez olmalı -- bu, Katman 9.6 erişilebilirlik uyarı motorunun
    # dayanacağı somut sinyaldir.
    assert (1, 0) in unreachable
    # Fonksiyon çağrısı yan etkisiz olmalı (elevators_disabled orijinal
    # duruma geri dönmüş olmalı).
    assert bg.elevators_disabled is False
