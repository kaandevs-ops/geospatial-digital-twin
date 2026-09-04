"""Roadmap V3 - Faz D6 kabul kriteri testleri.

Kapsam:
  1. `GreenshieldsModel` - klasik hız-yoğunluk temel akış diyagramı
     (Greenshields, 1935): v(k) doğrusal azalma, q(k) parabolik, kapasite
     k_j/2'de maksimum.
  2. `TrafficSignalPhase` + `TrafficSimulator` - sabit-zamanlı sinyalizasyon,
     kırmızıda kuyruk oluşumu / yeşilde boşalma (IDM'in "sanal hareketsiz
     öncü araç" mekanizmasıyla).
  3. `EvacuationBenchmark` - A7'nin zaten yazılı kabul kriteri: bilinen
     bir tahliye-süresi referans formülüyle (SFPE/Predtechenskii-Milinskii
     darboğaz akış hızı, 1.3 kişi/(m*s)) **%20 sapma içinde** sonuç.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D
from harita.mobility.pathfinding import NavGraph
from harita.mobility.traffic_simulation import (
    GreenshieldsModel, IDMParams, TrafficAgent, TrafficSignalPhase,
    TrafficSimulator, VehicleType,
)
from harita.mobility.crowd_simulation import (
    EvacuationBenchmark, ReferenceEvacuationScenario,
)


# ============================================================================ #
# GreenshieldsModel
# ============================================================================ #

def test_greenshields_speed_decreases_linearly_with_density():
    model = GreenshieldsModel(free_flow_speed=100.0, jam_density=200.0)
    assert math.isclose(model.speed_at_density(0.0), 100.0)
    assert math.isclose(model.speed_at_density(200.0), 0.0)
    assert math.isclose(model.speed_at_density(100.0), 50.0)


def test_greenshields_flow_is_zero_at_boundaries():
    model = GreenshieldsModel(free_flow_speed=90.0, jam_density=150.0)
    assert math.isclose(model.flow_at_density(0.0), 0.0)
    assert math.isclose(model.flow_at_density(150.0), 0.0, abs_tol=1e-9)


def test_greenshields_capacity_is_maximum_flow_at_half_jam_density():
    model = GreenshieldsModel(free_flow_speed=100.0, jam_density=200.0)
    critical_k = model.critical_density()
    assert math.isclose(critical_k, 100.0)

    q_at_critical = model.flow_at_density(critical_k)
    assert math.isclose(q_at_critical, model.capacity())

    # Kritik yoğunluğun her iki yanında akış daha düşük olmalı (parabolik tepe)
    q_below = model.flow_at_density(critical_k - 20.0)
    q_above = model.flow_at_density(critical_k + 20.0)
    assert q_below < q_at_critical
    assert q_above < q_at_critical

    # Analitik formül: q_max = v_f * k_j / 4
    assert math.isclose(model.capacity(), 100.0 * 200.0 / 4.0)


def test_greenshields_congestion_flag():
    model = GreenshieldsModel(free_flow_speed=80.0, jam_density=160.0)
    assert not model.is_congested(50.0)   # serbest akış rejimi
    assert model.is_congested(120.0)      # sıkışık rejim


def test_greenshields_clamps_out_of_range_density():
    model = GreenshieldsModel(free_flow_speed=60.0, jam_density=100.0)
    # negatif veya k_j üstü yoğunluk fiziksel olarak geçersiz - clamp edilir
    assert math.isclose(model.speed_at_density(-10.0), 60.0)
    assert math.isclose(model.speed_at_density(1000.0), 0.0)


# ============================================================================ #
# TrafficSignalPhase
# ============================================================================ #

def test_signal_phase_cycles_correctly():
    signal = TrafficSignalPhase(stop_line_distance=50.0, green_duration_s=20.0,
                                  red_duration_s=30.0)
    assert signal.cycle_length_s == 50.0
    assert signal.is_green(0.0)
    assert signal.is_green(19.9)
    assert not signal.is_green(20.1)
    assert not signal.is_green(49.9)
    assert signal.is_green(50.1)  # yeni döngü başladı


def test_signal_phase_time_to_next_green():
    signal = TrafficSignalPhase(stop_line_distance=10.0, green_duration_s=10.0,
                                  red_duration_s=10.0)
    assert signal.time_to_next_green(5.0) == 0.0  # zaten yeşil
    remaining = signal.time_to_next_green(15.0)
    assert math.isclose(remaining, 5.0)


def _build_straight_route_agent(agent_id: int, route_len: float,
                                  vehicle_type: VehicleType = VehicleType.ARAC,
                                  start_distance: float = 0.0) -> TrafficAgent:
    positions = [Point2D(0.0, 0.0), Point2D(route_len, 0.0)]
    agent = TrafficAgent(agent_id=agent_id, vehicle_type=vehicle_type,
                          route_nodes=[0, 1], route_positions=positions)
    agent.distance_along_route = start_distance
    return agent


def test_red_signal_forms_queue_and_green_releases_it():
    """A7/D6 kabul kriterinin çekirdek davranışı: kırmızı ışıkta araçlar
    durma çizgisinin gerisinde birikir (hız -> 0'a yaklaşır), yeşile
    geçince kuyruk çözülür (hız tekrar artar)."""
    sim = TrafficSimulator()
    route_key = "route-1"

    # Durma çizgisinden hemen önce 3 araç, sırayla.
    for i, start_dist in enumerate([70.0, 60.0, 50.0]):
        agent = _build_straight_route_agent(agent_id=i, route_len=200.0,
                                              start_distance=start_dist)
        agent.speed = 10.0
        sim.add_agent(agent, route_key)

    # Durma çizgisi x=80'de. offset_s = green_duration_s -> döngü t=0'da
    # doğrudan kırmızı fazda başlar; kırmızı 10s sürer, sonra 5s yeşil
    # (döngü uzunluğu 15s) - test penceresi içinde en az bir yeşil geçişi
    # gözlemlenebilecek şekilde kısa tutuldu.
    signal = TrafficSignalPhase(stop_line_distance=80.0, green_duration_s=5.0,
                                  red_duration_s=10.0, offset_s=5.0)
    sim.add_signal(route_key, signal)

    for _ in range(80):  # 8 saniye (dt=0.1) - hâlâ kırmızı (kırmızı t=[0,10))
        sim.step(dt=0.1)

    agents_after_red = sorted(sim.all_agents(), key=lambda a: a.agent_id)
    for agent in agents_after_red:
        assert agent.distance_along_route < 80.0  # kimse durma çizgisini geçmedi
        assert agent.speed < 2.0  # kuyrukta yavaşlamış/durmuş olmalı
    # Nesneler mutable olduğundan, karşılaştırma için değerleri (referans
    # değil) burada anlık görüntü olarak kopyala.
    max_progress_before = max(a.distance_along_route for a in agents_after_red)

    # Şimdi sinyal döngüsünün yeşile geçtiği ana (t=[10,15)) ilerlet ve
    # kuyruğun boşaldığını doğrula.
    for _ in range(60):  # 6 saniye daha -> toplam t=14s, yeşil pencere içinde
        sim.step(dt=0.1)

    agents_after_green = sorted(sim.all_agents(), key=lambda a: a.agent_id)
    max_progress_after = max(a.distance_along_route for a in agents_after_green)
    assert max_progress_after > max_progress_before  # en azından biri ilerledi


def test_signal_free_route_behaves_like_plain_idm():
    """Sinyal atanmamış bir rota grubu, eski (D6 öncesi) davranışla
    birebir aynı şekilde ilerlemeli - geriye dönük uyumluluk."""
    sim = TrafficSimulator()
    agent = _build_straight_route_agent(agent_id=0, route_len=100.0)
    agent.speed = 5.0
    sim.add_agent(agent, "no-signal-route")

    for _ in range(50):
        sim.step(dt=0.1)

    assert agent.distance_along_route > 0.0
    assert agent.speed > 0.0


# ============================================================================ #
# EvacuationBenchmark (A7 / D6 kabul kriteri)
# ============================================================================ #

def test_reference_scenario_expected_time_matches_analytic_formula():
    scenario = ReferenceEvacuationScenario(agent_count=130, exit_width_m=1.0,
                                             room_depth_m=10.0)
    # 130 kişi / (1.3 kişi/m/s * 1.0 m) = 100s (darboğaz baskın olmalı)
    assert math.isclose(scenario.expected_evacuation_time_s(), 100.0, rel_tol=1e-6)


def test_reference_scenario_travel_time_dominates_for_small_crowds():
    # Çok az kişi + geniş kapı -> darboğaz süresi ihmal edilebilir, oda
    # derinliği / yürüme hızı baskın olur.
    scenario = ReferenceEvacuationScenario(agent_count=2, exit_width_m=3.0,
                                             room_depth_m=13.4, walking_speed_ms=1.34)
    expected = scenario.expected_evacuation_time_s()
    assert math.isclose(expected, 13.4 / 1.34, rel_tol=1e-6)


def test_evacuation_benchmark_within_tolerance_of_reference():
    """A7'nin kendi kabul kriteri: bilinen tahliye-süresi referans
    çalışmasıyla (SFPE/Predtechenskii-Milinskii darboğaz akış formülü)
    **%20 sapma içinde** simülasyon sonucu."""
    result = EvacuationBenchmark.run_and_compare(
        agent_count=30, room_width_m=10.0, room_depth_m=8.0,
        exit_width_m=1.2, dt=0.1, max_time_s=180.0, seed=7,
    )

    assert result["total_agents"] == 30
    assert result["evacuated_count"] == result["total_agents"], (
        "Zaman aşımı olmadan tüm ajanlar tahliye edilmeli"
    )
    assert not result["timed_out"]
    assert result["within_tolerance"], (
        f"Simüle: {result['simulated_time_s']:.1f}s, "
        f"Beklenen: {result['expected_time_s']:.1f}s, "
        f"Sapma: {result['deviation_ratio']:.2%}"
    )
    assert result["deviation_ratio"] <= 0.20


def test_evacuation_benchmark_deterministic_with_seed():
    """Aynı `seed` ile iki koşu birebir aynı sonucu vermeli (regresyon
    testlerinin kararlılığı için)."""
    r1 = EvacuationBenchmark.run_and_compare(agent_count=15, room_width_m=8.0,
                                               room_depth_m=6.0, exit_width_m=1.0,
                                               seed=99)
    r2 = EvacuationBenchmark.run_and_compare(agent_count=15, room_width_m=8.0,
                                               room_depth_m=6.0, exit_width_m=1.0,
                                               seed=99)
    assert math.isclose(r1["simulated_time_s"], r2["simulated_time_s"])
    assert r1["evacuated_count"] == r2["evacuated_count"]


def test_evacuation_benchmark_wider_exit_evacuates_faster():
    """Fiziksel tutarlılık: daha geniş bir çıkış, aynı kalabalık için
    tahliyeyi hızlandırmalı (darboğaz teorisiyle uyumlu)."""
    narrow = EvacuationBenchmark.run_and_compare(
        agent_count=25, room_width_m=10.0, room_depth_m=8.0,
        exit_width_m=0.8, max_time_s=240.0, seed=3,
    )
    wide = EvacuationBenchmark.run_and_compare(
        agent_count=25, room_width_m=10.0, room_depth_m=8.0,
        exit_width_m=2.0, max_time_s=240.0, seed=3,
    )
    assert wide["expected_time_s"] < narrow["expected_time_s"]
    assert wide["simulated_time_s"] <= narrow["simulated_time_s"] + 5.0  # küçük stokastik tolerans
