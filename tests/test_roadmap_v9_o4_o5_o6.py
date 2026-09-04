"""
Roadmap V9 / OMURGA / O.4 + O.5 + O.6 testleri
=================================================

O.4 — `CityEventType` enum + `emit_city_event`/`subscribe_city_event`
      ince sarmalayıcıları, mevcut `EventSystem` üzerinde.
O.5 — `RealityFeed`: AFAD/USGS/Open-Meteo'yu sahte (fake) istemcilerle
      sorgulayıp Event Bus'a doğru olayları bastığını, hata durumunda
      çökmediğini ve deduplikasyon yaptığını doğrular (gerçek ağ
      erişimi YOK — testler tamamen sahte istemcilerle çalışır).
O.6 — `SimulationLODManager` mesafe eşiği seçimi, `build_aggregate_clusters`
      istatistiksel özet doğruluğu, `AgentSpatialHash` komşu sorgusunun
      O(n²) taramayla aynı sonucu (küçük ölçekte) verdiğini doğrular.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.climate_data.open_meteo_client import ClimateNetworkError, HourlyClimateSample
from harita.core_engine.geometry_engine import Point2D
from harita.data_engine.spatial_index import AABB2D
from harita.digital_twin.reality_feed import RealityFeed, RealityFeedConfig, RealityFeedRegion
from harita.extensibility.city_events import (
    HAZARD_PATTERN,
    CityEventType,
    emit_city_event,
    subscribe_city_event,
)
from harita.extensibility.event_system import EventSystem
from harita.hazard_data.afad_client import AFADEarthquake, HazardNetworkError
from harita.hazard_data.usgs_client import USGSEarthquake
from harita.performance.simulation_lod import (
    AgentSpatialHash,
    SimulationLODManager,
    SimulationLODMode,
    build_aggregate_clusters,
)

# ========================================================================== #
# O.4 — CityEventType
# ========================================================================== #


class TestCityEvents:
    def test_enum_values_are_plain_strings(self) -> None:
        assert CityEventType.HAZARD_STARTED == "hazard.started"
        assert CityEventType.POWER_OUTAGE.value == "power.outage"

    def test_emit_city_event_reaches_exact_subscriber(self) -> None:
        bus = EventSystem()
        received = []
        subscribe_city_event(bus, CityEventType.HAZARD_STARTED, received.append)
        emit_city_event(bus, CityEventType.HAZARD_STARTED, hazard_type="earthquake", magnitude=6.1)
        assert len(received) == 1
        assert received[0].payload == {"hazard_type": "earthquake", "magnitude": 6.1}

    def test_hazard_pattern_glob_catches_all_hazard_subtypes(self) -> None:
        bus = EventSystem()
        received = []
        bus.subscribe(HAZARD_PATTERN, received.append)
        emit_city_event(bus, CityEventType.HAZARD_STARTED)
        emit_city_event(bus, CityEventType.FIRE_IGNITED)
        emit_city_event(bus, CityEventType.POWER_OUTAGE)  # eşleşmemeli
        names = {e.name for e in received}
        assert names == {"hazard.started", "hazard.fire_ignited"}

    def test_unrelated_pattern_does_not_receive_hazard_events(self) -> None:
        bus = EventSystem()
        received = []
        bus.subscribe("power.*", received.append)
        emit_city_event(bus, CityEventType.HAZARD_STARTED)
        assert received == []


# ========================================================================== #
# O.5 — RealityFeed (sahte istemcilerle, ağ erişimi olmadan)
# ========================================================================== #


@dataclass
class _FakeAfadClient:
    quakes: list
    should_fail: bool = False

    def fetch_earthquakes(self, **kwargs):
        if self.should_fail:
            raise HazardNetworkError("simulated afad outage")
        return self.quakes


@dataclass
class _FakeUsgsClient:
    quakes: list
    should_fail: bool = False

    def fetch_earthquakes(self, **kwargs):
        if self.should_fail:
            raise HazardNetworkError("simulated usgs outage")
        return self.quakes


@dataclass
class _FakeOpenMeteoClient:
    samples: list
    should_fail: bool = False

    def fetch_hourly(self, **kwargs):
        if self.should_fail:
            raise ClimateNetworkError("simulated open-meteo outage")
        return self.samples


def _make_afad_quake(event_id: str = "afad-1", magnitude: float = 5.4) -> AFADEarthquake:
    return AFADEarthquake(
        event_id=event_id,
        time_utc=datetime.now(timezone.utc),
        latitude=39.9,
        longitude=32.8,
        depth_km=7.0,
        magnitude=magnitude,
        magnitude_type="Mw",
        location_name="Test Bölgesi",
    )


def _make_region() -> RealityFeedRegion:
    return RealityFeedRegion(
        name="ankara_test",
        min_lat=39.0,
        max_lat=40.5,
        min_lon=32.0,
        max_lon=33.5,
        min_magnitude=3.0,
    )


class TestRealityFeed:
    def test_afad_success_emits_earthquake_and_hazard_started(self) -> None:
        bus = EventSystem()
        quake = _make_afad_quake()
        config = RealityFeedConfig(
            regions=[_make_region()],
            fetch_weather=False,
            afad_client=_FakeAfadClient(quakes=[quake]),
            usgs_client=_FakeUsgsClient(quakes=[]),
        )
        feed = RealityFeed(config, bus=bus)
        feed.poll_once()

        quake_events = bus.history(CityEventType.REALITY_FEED_EARTHQUAKE.value)
        hazard_events = bus.history(CityEventType.HAZARD_STARTED.value)
        assert len(quake_events) == 1
        assert quake_events[0].payload["magnitude"] == 5.4
        assert len(hazard_events) == 1
        assert hazard_events[0].payload["hazard_type"] == "earthquake"

    def test_afad_failure_falls_back_to_usgs(self) -> None:
        bus = EventSystem()
        quake = USGSEarthquake(
            event_id="usgs-1",
            time_utc=datetime.now(timezone.utc),
            latitude=38.0,
            longitude=27.0,
            depth_km=10.0,
            magnitude=4.8,
            magnitude_type="mb",
            place="Test",
        )
        config = RealityFeedConfig(
            regions=[_make_region()],
            fetch_weather=False,
            afad_client=_FakeAfadClient(quakes=[], should_fail=True),
            usgs_client=_FakeUsgsClient(quakes=[quake]),
        )
        feed = RealityFeed(config, bus=bus)
        feed.poll_once()

        error_events = bus.history(CityEventType.REALITY_FEED_SOURCE_ERROR.value)
        quake_events = bus.history(CityEventType.REALITY_FEED_EARTHQUAKE.value)
        assert len(error_events) == 1
        assert error_events[0].payload["region"] == "ankara_test"
        assert len(quake_events) == 1
        assert quake_events[0].source == "reality_feed:usgs"

    def test_both_sources_failing_does_not_raise(self) -> None:
        bus = EventSystem()
        config = RealityFeedConfig(
            regions=[_make_region()],
            fetch_weather=False,
            afad_client=_FakeAfadClient(quakes=[], should_fail=True),
            usgs_client=_FakeUsgsClient(quakes=[], should_fail=True),
        )
        feed = RealityFeed(config, bus=bus)
        feed.poll_once()  # çökmemeli
        error_events = bus.history(CityEventType.REALITY_FEED_SOURCE_ERROR.value)
        assert len(error_events) == 2  # afad + usgs

    def test_duplicate_quake_not_reemitted_on_second_poll(self) -> None:
        bus = EventSystem()
        quake = _make_afad_quake(event_id="dup-1")
        config = RealityFeedConfig(
            regions=[_make_region()],
            fetch_weather=False,
            afad_client=_FakeAfadClient(quakes=[quake]),
            usgs_client=_FakeUsgsClient(quakes=[]),
        )
        feed = RealityFeed(config, bus=bus)
        feed.poll_once()
        feed.poll_once()
        quake_events = bus.history(CityEventType.REALITY_FEED_EARTHQUAKE.value)
        assert len(quake_events) == 1

    def test_weather_sample_emitted_when_enabled(self) -> None:
        bus = EventSystem()
        sample = HourlyClimateSample(
            time_iso="2026-08-07T12:00",
            temperature_c=28.5,
            cloud_cover_pct=10.0,
            shortwave_radiation_wm2=600.0,
            direct_radiation_wm2=500.0,
            diffuse_radiation_wm2=100.0,
        )
        config = RealityFeedConfig(
            regions=[_make_region()],
            fetch_weather=True,
            afad_client=_FakeAfadClient(quakes=[]),
            usgs_client=_FakeUsgsClient(quakes=[]),
            open_meteo_client=_FakeOpenMeteoClient(samples=[sample]),
        )
        feed = RealityFeed(config, bus=bus)
        feed.poll_once()
        weather_events = bus.history(CityEventType.REALITY_FEED_WEATHER.value)
        assert len(weather_events) == 1
        assert weather_events[0].payload["temperature_c"] == 28.5

    def test_start_stop_background_thread(self) -> None:
        bus = EventSystem()
        config = RealityFeedConfig(
            regions=[_make_region()],
            fetch_weather=False,
            poll_interval_s=0.05,
            afad_client=_FakeAfadClient(quakes=[]),
            usgs_client=_FakeUsgsClient(quakes=[]),
        )
        feed = RealityFeed(config, bus=bus)
        feed.start()
        feed.start()  # ikinci start no-op olmalı, ikinci thread açmamalı
        import time

        time.sleep(0.15)
        feed.stop()
        assert feed._thread is None


# ========================================================================== #
# O.6 — Simulation LOD
# ========================================================================== #


@dataclass
class _FakeAgent:
    agent_id: int
    position: Point2D
    velocity: Point2D
    waiting: bool = False


class TestSimulationLODManager:
    def test_close_distance_selects_full(self) -> None:
        mgr = SimulationLODManager()
        assert mgr.select(50.0) == SimulationLODMode.FULL

    def test_mid_distance_selects_aggregate(self) -> None:
        mgr = SimulationLODManager()
        assert mgr.select(300.0) == SimulationLODMode.AGGREGATE

    def test_far_distance_selects_culled(self) -> None:
        mgr = SimulationLODManager()
        assert mgr.select(10_000.0) == SimulationLODMode.CULLED

    def test_select_for_position_uses_euclidean_distance(self) -> None:
        mgr = SimulationLODManager()
        mode = mgr.select_for_position((0.0, 0.0), (100.0, 0.0))
        assert mode == SimulationLODMode.FULL
        mode_far = mgr.select_for_position((0.0, 0.0), (1000.0, 0.0))
        assert mode_far == SimulationLODMode.CULLED


class TestAggregateClusters:
    def test_agents_grouped_by_cell(self) -> None:
        agents = [
            _FakeAgent(1, Point2D(1.0, 1.0), Point2D(1.0, 0.0), waiting=False),
            _FakeAgent(2, Point2D(2.0, 2.0), Point2D(0.0, 0.0), waiting=True),
            _FakeAgent(3, Point2D(100.0, 100.0), Point2D(0.5, 0.5), waiting=False),
        ]
        clusters = build_aggregate_clusters(agents, cell_size_m=25.0)
        # agent 1,2 aynı hücrede (0,0); agent 3 uzak, farklı hücrede
        assert len(clusters) == 2
        near_cluster = next(c for c in clusters if c.agent_count == 2)
        assert near_cluster.waiting_fraction == pytest.approx(0.5)
        assert near_cluster.centroid == pytest.approx((1.5, 1.5))

    def test_empty_agent_list_yields_no_clusters(self) -> None:
        assert build_aggregate_clusters([]) == []


class TestAgentSpatialHash:
    def test_query_radius_matches_brute_force(self) -> None:
        bounds = AABB2D(-500.0, -500.0, 500.0, 500.0)
        hash_ = AgentSpatialHash(bounds, capacity=4)
        agents = [_FakeAgent(i, Point2D(float(i), float(i)), Point2D(0.0, 0.0)) for i in range(30)]
        for a in agents:
            hash_.insert(a)

        center = (10.0, 10.0)
        radius = 5.0
        expected_ids = {
            a.agent_id
            for a in agents
            if math.hypot(a.position.x - center[0], a.position.y - center[1]) <= radius
        }
        found = hash_.query_radius(center, radius)
        assert {a.agent_id for a in found} == expected_ids

    def test_exclude_id_omits_self(self) -> None:
        bounds = AABB2D(-10.0, -10.0, 10.0, 10.0)
        hash_ = AgentSpatialHash(bounds)
        a1 = _FakeAgent(1, Point2D(0.0, 0.0), Point2D(0.0, 0.0))
        a2 = _FakeAgent(2, Point2D(0.5, 0.5), Point2D(0.0, 0.0))
        hash_.insert(a1)
        hash_.insert(a2)
        found = hash_.query_radius((0.0, 0.0), radius=2.0, exclude_id=1)
        assert {a.agent_id for a in found} == {2}

    def test_count_matches_inserted_agents(self) -> None:
        bounds = AABB2D(-10.0, -10.0, 10.0, 10.0)
        hash_ = AgentSpatialHash(bounds)
        for i in range(5):
            hash_.insert(_FakeAgent(i, Point2D(float(i) * 0.1, 0.0), Point2D(0.0, 0.0)))
        assert hash_.count() == 5
