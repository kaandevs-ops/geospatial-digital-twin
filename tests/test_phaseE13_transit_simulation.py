"""Roadmap V4 - Track E / Faz E13: Transit Simulation testleri."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.core_engine.geometry_engine import Point2D
from harita.mobility.pathfinding import NavGraph
from harita.mobility.transit_simulation import (
    MultiModalRoute,
    TransitLine,
    TransitStop,
    TransitVehicle,
)


def _simple_walk_graph() -> NavGraph:
    g = NavGraph()
    g.add_node("home", Point2D(0, 0))
    g.add_node("stopA_walk", Point2D(10, 0))
    g.add_node("stopB_walk", Point2D(10, 200))
    g.add_node("work", Point2D(20, 200))
    g.add_edge("home", "stopA_walk", cost=60)
    g.add_edge("stopB_walk", "work", cost=60)
    return g


class TestTransitLine:
    def test_rejects_short_line(self):
        with pytest.raises(ValueError):
            TransitLine("L1", stops=["A"], travel_times_s=[], schedule_headway_s=600)

    def test_rejects_mismatched_travel_times(self):
        with pytest.raises(ValueError):
            TransitLine("L1", stops=["A", "B", "C"], travel_times_s=[100],
                        schedule_headway_s=600)

    def test_cumulative_time(self):
        line = TransitLine("L1", stops=["A", "B", "C"],
                            travel_times_s=[100, 150], schedule_headway_s=600)
        assert line.cumulative_time_s(0, 1) == 100
        assert line.cumulative_time_s(0, 2) == 250
        assert line.cumulative_time_s(1, 2) == 150

    def test_next_departure_tariff_shift(self):
        line = TransitLine("L1", stops=["A", "B", "C"],
                            travel_times_s=[100, 150], schedule_headway_s=600,
                            first_departure_s=0)
        # Durak A'dan ilk kalkış t=0, sonraki t=600.
        assert line.next_departure(0, after_time_s=0) == 0
        assert line.next_departure(0, after_time_s=1) == 600
        # Durak B'ye kaydırılmış tarife: base = 0 + 100.
        assert line.next_departure(1, after_time_s=0) == 100
        assert line.next_departure(1, after_time_s=150) == 700


class TestTransitVehicle:
    def test_capacity_limited_boarding(self):
        line = TransitLine("L1", stops=["A", "B"], travel_times_s=[100],
                            schedule_headway_s=600, capacity=5)
        vehicle = TransitVehicle(line=line, departure_time_s=0)
        boarded = vehicle.board(3)
        assert boarded == 3
        assert vehicle.onboard == 3
        boarded2 = vehicle.board(10)
        assert boarded2 == 2  # yalnızca kapasiteye kadar
        assert vehicle.onboard == 5
        assert not vehicle.can_board(1)
        vehicle.alight(2)
        assert vehicle.onboard == 3
        assert vehicle.can_board(2)

    def test_arrival_time(self):
        line = TransitLine("L1", stops=["A", "B", "C"],
                            travel_times_s=[100, 150], schedule_headway_s=600)
        vehicle = TransitVehicle(line=line, departure_time_s=1000)
        assert vehicle.arrival_time_s(0) == 1000
        assert vehicle.arrival_time_s(1) == 1100
        assert vehicle.arrival_time_s(2) == 1250


class TestMultiModalRoute:
    def test_walk_transit_walk_chain(self):
        wg = _simple_walk_graph()
        mm = MultiModalRoute(walk_graph=wg)
        mm.add_stop(TransitStop("A", Point2D(10, 0), walk_node="stopA_walk"))
        mm.add_stop(TransitStop("B", Point2D(10, 200), walk_node="stopB_walk"))
        line = TransitLine("L1", stops=["A", "B"], travel_times_s=[300],
                            schedule_headway_s=600)
        mm.add_line(line)

        result = mm.plan("home", "work")
        assert result.found
        modes = [leg.mode for leg in result.legs]
        # Zincir: walk -> wait(transfer) -> transit -> wait(transfer) -> walk
        assert modes[0] == "walk"
        assert "transit" in modes
        assert modes[-1] == "walk"

        # Toplam süre, bacakların toplamına eşit olmalı (tutarlılık).
        assert result.total_duration_s == pytest.approx(
            sum(leg.duration_s for leg in result.legs)
        )
        # Transit yolculuğunun hat kimliği doğru atanmalı.
        transit_legs = [leg for leg in result.legs if leg.mode == "transit"]
        assert len(transit_legs) == 1
        assert transit_legs[0].line_id == "L1"
        assert transit_legs[0].duration_s == 300

    def test_transit_faster_than_pure_walk_for_long_distance(self):
        """Toplu taşıma, uzun mesafede yürümekten daha hızlı olmalı -
        rota planlayıcı bunu doğru şekilde tercih eder (A* en-hızlı rotayı
        seçtiği için, transit kenarları eklendiğinde toplam süre düşer)."""
        wg = _simple_walk_graph()
        # Doğrudan yürüme kenarı ekle (rakip yol) - çok uzun/yavaş.
        wg.add_node("home2", Point2D(0, 0))
        wg2_direct = NavGraph()
        for node, pos in wg.positions.items():
            wg2_direct.add_node(node, pos)
        for node in wg.positions:
            for nb, cost in wg.neighbors(node):
                if nb not in dict(wg2_direct.neighbors(node)):
                    wg2_direct.add_edge(node, nb, cost=cost, bidirectional=False)
        wg2_direct.add_edge("home", "work", cost=100000, bidirectional=True)

        mm = MultiModalRoute(walk_graph=wg2_direct)
        mm.add_stop(TransitStop("A", Point2D(10, 0), walk_node="stopA_walk"))
        mm.add_stop(TransitStop("B", Point2D(10, 200), walk_node="stopB_walk"))
        mm.add_line(TransitLine("L1", stops=["A", "B"], travel_times_s=[300],
                                 schedule_headway_s=600))

        result = mm.plan("home", "work")
        assert result.found
        assert result.total_duration_s < 100000

    def test_no_route_when_disconnected(self):
        wg = NavGraph()
        wg.add_node("isolated1", Point2D(0, 0))
        wg.add_node("isolated2", Point2D(100, 100))
        mm = MultiModalRoute(walk_graph=wg)
        result = mm.plan("isolated1", "isolated2")
        assert not result.found
        assert result.legs == []

    def test_missing_node_returns_not_found(self):
        wg = _simple_walk_graph()
        mm = MultiModalRoute(walk_graph=wg)
        result = mm.plan("home", "nonexistent")
        assert not result.found
