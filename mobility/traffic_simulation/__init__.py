"""
Traffic Simulation
==================

Roadmap Phase 7 - "Traffic Simulation": Araç / Otobüs / Kamyon / Bisiklet /
Yaya, `NavGraph` üzerinde IDM (Intelligent Driver Model) tabanlı hareket.

IDM (Treiber, Hennecke, Helbing 2000), takip eden aracın öndeki araca göre
ivmesini belirleyen, endüstri standardı mikroskobik trafik akış modelidir.
Her araç tipi (bisiklet/yaya dahil - roadmap'in listelediği "Yaya" burada
düşük hız/uzunluklu bir VehicleType olarak modellenir) kendi IDM
parametreleriyle aynı yol ağını (`NavGraph`) paylaşır.
Roadmap V3 - Faz D6 ("Mobility: Trafik Kapasite Modeli"): `GreenshieldsModel`
(Greenshields, 1935 - klasik hız-yoğunluk temel akış diyagramı) yol
kapasitesini (araç/saat) hesaplar; `TrafficSignalPhase` sabit-zamanlı
kavşak sinyalizasyonu ekler. `TrafficSimulator`, kırmızı ışıkta duraklama
noktasını IDM'e "sanal, hareketsiz bir öncü araç" olarak besleyerek kuyruk
oluşumunu (ve yeşile geçince kuyruğun boşalmasını) doğal olarak üretir -
IDM zaten öncü-araç takibi üzerine kurulu olduğundan, ek bir kuyruk modeli
yazmaya gerek kalmaz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from ...core_engine.geometry_engine import Point2D
from ..pathfinding import AStar, NavGraph, NodeId


class VehicleType(str, Enum):
    ARAC = "arac"  # otomobil
    OTOBUS = "otobus"
    KAMYON = "kamyon"
    BISIKLET = "bisiklet"
    YAYA = "yaya"


@dataclass(slots=True)
class IDMParams:
    """Intelligent Driver Model parametreleri (araç tipine göre varsayılan
    değerler `VEHICLE_IDM_DEFAULTS`'ta tanımlıdır)."""

    desired_speed: float  # v0 (m/s) - serbest akış hızı
    safe_time_headway: float  # T (s)
    max_acceleration: float  # a (m/s^2)
    comfortable_braking: float  # b (m/s^2)
    min_gap: float  # s0 (m) - dur/dur mesafesi
    vehicle_length: float  # m


VEHICLE_IDM_DEFAULTS: dict[VehicleType, IDMParams] = {
    VehicleType.ARAC: IDMParams(
        desired_speed=15.0,
        safe_time_headway=1.5,
        max_acceleration=1.5,
        comfortable_braking=2.0,
        min_gap=2.0,
        vehicle_length=4.5,
    ),
    VehicleType.OTOBUS: IDMParams(
        desired_speed=11.0,
        safe_time_headway=2.0,
        max_acceleration=1.0,
        comfortable_braking=1.5,
        min_gap=3.0,
        vehicle_length=12.0,
    ),
    VehicleType.KAMYON: IDMParams(
        desired_speed=12.0,
        safe_time_headway=2.2,
        max_acceleration=0.8,
        comfortable_braking=1.5,
        min_gap=3.5,
        vehicle_length=8.0,
    ),
    VehicleType.BISIKLET: IDMParams(
        desired_speed=5.5,
        safe_time_headway=1.2,
        max_acceleration=1.2,
        comfortable_braking=2.5,
        min_gap=1.0,
        vehicle_length=1.8,
    ),
    VehicleType.YAYA: IDMParams(
        desired_speed=1.34,
        safe_time_headway=1.0,
        max_acceleration=1.0,
        comfortable_braking=2.0,
        min_gap=0.5,
        vehicle_length=0.5,
    ),
}


@dataclass(slots=True)
class TrafficAgent:
    agent_id: int
    vehicle_type: VehicleType
    route_nodes: list[NodeId]  # NavGraph düğüm dizisi (A* çıktısı)
    route_positions: list[Point2D] = field(default_factory=list)
    distance_along_route: float = 0.0  # metre cinsinden rota üzerindeki konum
    speed: float = 0.0
    arrived: bool = False

    def params(self) -> IDMParams:
        return VEHICLE_IDM_DEFAULTS[self.vehicle_type]

    def route_length(self) -> float:
        return sum(
            self.route_positions[i].distance_to(self.route_positions[i + 1])
            for i in range(len(self.route_positions) - 1)
        )

    def current_position(self) -> Point2D:
        if not self.route_positions:
            return Point2D(0.0, 0.0)
        remaining = self.distance_along_route
        for i in range(len(self.route_positions) - 1):
            seg_len = self.route_positions[i].distance_to(self.route_positions[i + 1])
            if remaining <= seg_len or i == len(self.route_positions) - 2:
                if seg_len < 1e-9:
                    return self.route_positions[i]
                t = max(0.0, min(1.0, remaining / seg_len))
                a, b = self.route_positions[i], self.route_positions[i + 1]
                return Point2D(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
            remaining -= seg_len
        return self.route_positions[-1]


def build_route(
    graph: NavGraph, start: NodeId, goal: NodeId, vehicle_type: VehicleType, agent_id: int
) -> TrafficAgent | None:
    result = AStar.find_path(graph, start, goal)
    if not result.found:
        return None
    positions = [graph.positions[n] for n in result.path]
    return TrafficAgent(
        agent_id=agent_id,
        vehicle_type=vehicle_type,
        route_nodes=result.path,
        route_positions=positions,
    )


class IDMModel:
    """Intelligent Driver Model - tek şeritli, sıralı (aynı rotayı takip
    eden) araçlar için ivme hesaplar.

    a = a_max * [1 - (v/v0)^4 - (s*/s)^2]
    s* = s0 + v*T + v*dv / (2*sqrt(a_max*b))

    Burada `s` önündeki araca olan boşluk (gap), `dv` hız farkıdır. Farklı
    rotalardaki araçlar (çakışmayan) IDM'den etkilenmez; bu implementasyon,
    roadmap'in kapsamına uygun şekilde 'aynı yol/şerit üzerinde araç
    takibi' senaryosuna odaklanır.
    """

    @staticmethod
    def acceleration(agent: TrafficAgent, leader: TrafficAgent | None) -> float:
        p = agent.params()
        v = agent.speed
        v0 = p.desired_speed
        free_road_term = 1.0 - (v / v0) ** 4 if v0 > 0 else 0.0

        if leader is None:
            interaction_term = 0.0
        else:
            gap = (
                leader.distance_along_route
                - leader.params().vehicle_length
                - agent.distance_along_route
                - agent.params().vehicle_length / 2
            )
            gap = max(gap, 0.01)
            dv = v - leader.speed
            s_star = p.min_gap + max(
                0.0,
                v * p.safe_time_headway
                + (v * dv) / (2 * math.sqrt(p.max_acceleration * p.comfortable_braking)),
            )
            interaction_term = (s_star / gap) ** 2

        accel = p.max_acceleration * (free_road_term - interaction_term)
        return max(-8.0, min(p.max_acceleration, accel))

    @staticmethod
    def step(agents: list[TrafficAgent], dt: float = 0.1) -> None:
        """`agents` aynı rotayı (aynı NavGraph yolunu) paylaşan, rotadaki
        ilerleme sırasına göre önden arkaya dizilmiş kabul edilir. Liste,
        `distance_along_route`'a göre azalan sırada (en öndeki ilk) olmalı;
        fonksiyon bunu otomatik sıralar."""
        ordered = sorted(
            [a for a in agents if not a.arrived], key=lambda a: -a.distance_along_route
        )

        accelerations: dict[int, float] = {}
        for i, agent in enumerate(ordered):
            leader = ordered[i - 1] if i > 0 else None
            accelerations[agent.agent_id] = IDMModel.acceleration(agent, leader)

        for agent in ordered:
            accel = accelerations[agent.agent_id]
            new_speed = max(0.0, agent.speed + accel * dt)
            agent.speed = new_speed
            agent.distance_along_route += new_speed * dt
            total_len = agent.route_length()
            if agent.distance_along_route >= total_len:
                agent.distance_along_route = total_len
                agent.speed = 0.0
                agent.arrived = True


@dataclass(slots=True)
class GreenshieldsModel:
    """Greenshields (1935) klasik doğrusal hız-yoğunluk temel akış modeli.

    v(k) = v_f * (1 - k/k_j)
    q(k) = k * v(k) = v_f * k * (1 - k/k_j)

    Burada `v_f` serbest-akış hızı, `k_j` tıkanıklık yoğunluğu (jam
    density), `q` akış (kapasite). Model, akışın k_j/2'de maksimum
    olduğunu öngörür (q_max = v_f * k_j / 4) - roadmap D6'nın "yol
    kapasitesi (araç/saat)" hedefinin doğrudan karşılığıdır.

    Referans: B.D. Greenshields, "A Study of Traffic Capacity",
    Highway Research Board Proceedings, 14, 1935, s. 448-477.
    """

    free_flow_speed: float  # v_f (km/h)
    jam_density: float  # k_j (araç/km/şerit)

    def speed_at_density(self, density: float) -> float:
        k = max(0.0, min(density, self.jam_density))
        return self.free_flow_speed * (1.0 - k / self.jam_density)

    def flow_at_density(self, density: float) -> float:
        k = max(0.0, min(density, self.jam_density))
        return k * self.speed_at_density(k)

    def capacity(self) -> float:
        """q_max - k_j/2 yoğunluğunda ulaşılan maksimum akış (araç/saat).
        Bu değerin üzerinde talep varsa kuyruklanma oluşur."""
        return self.free_flow_speed * self.jam_density / 4.0

    def critical_density(self) -> float:
        return self.jam_density / 2.0

    def is_congested(self, density: float) -> bool:
        return density > self.critical_density()


@dataclass(slots=True)
class TrafficSignalPhase:
    """Sabit-zamanlı (fixed-time) kavşak sinyalizasyonu.

    `stop_line_distance`, bu sinyalin etkilediği rota üzerindeki durma
    çizgisi konumudur. `TrafficSimulator`, kırmızı ışıkta bu konumda
    IDM'e "sanal, hareketsiz bir öncü araç" besler; IDM zaten öncü-araç
    takibi üzerine kurulu olduğundan kuyruk oluşumu (ve yeşile geçince
    boşalması) ek bir model yazmadan doğal olarak üretilir.
    """

    stop_line_distance: float
    green_duration_s: float
    red_duration_s: float
    yellow_duration_s: float = 0.0
    offset_s: float = 0.0

    @property
    def cycle_length_s(self) -> float:
        return self.green_duration_s + self.yellow_duration_s + self.red_duration_s

    def is_green(self, t: float) -> bool:
        cycle = self.cycle_length_s
        if cycle <= 0:
            return True
        phase_t = (t + self.offset_s) % cycle
        return phase_t < self.green_duration_s

    def time_to_next_green(self, t: float) -> float:
        cycle = self.cycle_length_s
        if cycle <= 0 or self.is_green(t):
            return 0.0
        phase_t = (t + self.offset_s) % cycle
        return cycle - phase_t


@dataclass(slots=True)
class _VirtualStopLeader:
    """Kırmızı ışıkta IDM'e beslenen, durma çizgisinde hareketsiz duran
    sanal öncü araç. Gerçek bir `TrafficAgent` değil, IDM arayüzünün
    ihtiyaç duyduğu minimum alanları taşır."""

    distance_along_route: float
    speed: float = 0.0

    def params(self) -> IDMParams:
        return IDMParams(
            desired_speed=0.0,
            safe_time_headway=0.0,
            max_acceleration=0.0,
            comfortable_braking=0.0,
            min_gap=0.0,
            vehicle_length=0.0,
        )


class TrafficSimulator:
    """Birden çok bağımsız rota grubunu (her grup kendi IDM sırasıyla)
    aynı anda simüle eden üst seviye orkestratör.

    Roadmap V3 - Faz D6: her rota grubuna isteğe bağlı bir
    `TrafficSignalPhase` listesi atanabilir (`add_signal`); simülasyon
    saati ilerledikçe kırmızı fazdaki sinyaller, durma çizgisinin
    gerisindeki araçlara sanal-hareketsiz-öncü olarak uygulanır.
    """

    def __init__(self) -> None:
        self._route_groups: dict[str, list[TrafficAgent]] = {}
        self._signals: dict[str, list[TrafficSignalPhase]] = {}
        self._t: float = 0.0

    def add_agent(self, agent: TrafficAgent, route_key: str) -> None:
        self._route_groups.setdefault(route_key, []).append(agent)

    def add_signal(self, route_key: str, signal: TrafficSignalPhase) -> None:
        self._signals.setdefault(route_key, []).append(signal)

    def _active_stop_leader(self, route_key: str, agent: TrafficAgent) -> _VirtualStopLeader | None:
        best: _VirtualStopLeader | None = None
        for signal in self._signals.get(route_key, []):
            if signal.stop_line_distance <= agent.distance_along_route:
                continue
            if signal.is_green(self._t):
                continue
            if best is None or signal.stop_line_distance < best.distance_along_route:
                best = _VirtualStopLeader(distance_along_route=signal.stop_line_distance)
        return best

    def step(self, dt: float = 0.1) -> None:
        for route_key, group in self._route_groups.items():
            if route_key not in self._signals:
                IDMModel.step(group, dt=dt)
                continue

            ordered = sorted(
                [a for a in group if not a.arrived], key=lambda a: -a.distance_along_route
            )
            accelerations: dict[int, float] = {}
            for i, agent in enumerate(ordered):
                real_leader = ordered[i - 1] if i > 0 else None
                stop_leader = self._active_stop_leader(route_key, agent)
                if stop_leader is not None and (
                    real_leader is None
                    or stop_leader.distance_along_route < real_leader.distance_along_route
                ):
                    accelerations[agent.agent_id] = IDMModel.acceleration(agent, stop_leader)  # type: ignore[arg-type]
                else:
                    accelerations[agent.agent_id] = IDMModel.acceleration(agent, real_leader)

            for agent in ordered:
                accel = accelerations[agent.agent_id]
                new_speed = max(0.0, agent.speed + accel * dt)
                agent.speed = new_speed
                agent.distance_along_route += new_speed * dt
                total_len = agent.route_length()
                if agent.distance_along_route >= total_len:
                    agent.distance_along_route = total_len
                    agent.speed = 0.0
                    agent.arrived = True

        self._t += dt

    def all_agents(self) -> list[TrafficAgent]:
        return [a for group in self._route_groups.values() for a in group]

    def arrived_count(self) -> int:
        return sum(1 for a in self.all_agents() if a.arrived)
