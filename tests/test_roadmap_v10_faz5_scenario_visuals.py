"""Roadmap V10 / Faz 5 — Diğer Simülasyonların Derinleştirilmesi.

5.1 (yangın->cephe overlay) -> 5.2 (acil müdahale ikon+rota) -> 5.3
(heatmap->sahne bağlantısı) -> 5.4 (günlük rutin görsel etiketi) -> 5.5
(trafik sahne karesi) -> 5.6 (ses/işitsel katman) kapsar.
"""

from __future__ import annotations

from harita.core_engine.geometry_engine import Point2D
from harita.hazard_data.fire_spread import FireSpreadModel
from harita.mobility.crowd_simulation.agent_visuals import CrowdPressureLevel
from harita.mobility.emergency_response import (
    DispatchResult,
    EmergencyStation,
    EmergencyUnitType,
)
from harita.mobility.pathfinding import NavGraph, PathResult
from harita.mobility.traffic_simulation import GreenshieldsModel, TrafficAgent, VehicleType
from harita.population.synthetic_population import DailyRoutineType
from harita.render_engine.scene_bridge import Scene
from harita.visualization.heatmap_overlay import crowd_heatmap_overlay
from harita.visualization.scenario_visual_bridge import (
    FireSpriteKind,
    RoutineVisualState,
    VehicleSpeedTint,
    bind_heatmap_to_scene_layer,
    daily_routine_visual_tag,
    emergency_vehicle_icon_frame,
    fire_facade_overlay,
    traffic_vehicle_scene_frame,
)
from harita.visualization.spatial_audio import (
    SCENARIO_SOUND_SIGNATURE,
    CrowdAmbienceLayer,
    PositionalAudioSource,
    ScenarioKind,
    SoundEffectId,
    crowd_ambience_mix,
    positional_gain,
    trigger_event_sound,
)

# --------------------------------------------------------------------------- #
# 5.1 — Yangın -> cephe overlay
# --------------------------------------------------------------------------- #


class TestFaz5_1FireFacadeOverlay:
    def _model(self) -> FireSpreadModel:
        return FireSpreadModel(width=5, height=5, ignition_cells=[(2, 2)], seed=1)

    def test_clear_cells_produce_no_sprite(self):
        model = self._model()
        sprites = fire_facade_overlay(
            model,
            "b1",
            cell_to_world=lambda c: Point2D(c[0], c[1]),
        )
        # yalnızca ignition hücresi (FIRE) sprite üretir, geri kalan CLEAR
        assert len(sprites) == 1
        assert sprites[0].kind is FireSpriteKind.FLAME

    def test_spread_produces_smoke_and_flame(self):
        model = self._model()
        model.run(duration_s=6.0, dt=1.0)
        sprites = fire_facade_overlay(
            model,
            "b1",
            cell_to_world=lambda c: Point2D(c[0], c[1]),
        )
        kinds = {s.kind for s in sprites}
        assert FireSpriteKind.FLAME in kinds
        assert len(sprites) > 1

    def test_floor_mapping_sets_height(self):
        model = self._model()
        sprites = fire_facade_overlay(
            model,
            "b1",
            cell_to_world=lambda c: Point2D(c[0], c[1]),
            floor_height_m=3.0,
            cell_to_floor=lambda c: 2,
        )
        assert all(s.height_m == 6.0 for s in sprites)

    def test_honesty_note_present(self):
        model = self._model()
        sprites = fire_facade_overlay(model, "b1", cell_to_world=lambda c: Point2D(c[0], c[1]))
        assert "CFD" in sprites[0].honesty_note or "akışkanlar" in sprites[0].honesty_note


# --------------------------------------------------------------------------- #
# 5.2 — Acil müdahale -> ikon + rota
# --------------------------------------------------------------------------- #


class TestFaz5_2EmergencyVehicleFrame:
    def _graph(self) -> NavGraph:
        g = NavGraph()
        g.add_node("station", Point2D(0.0, 0.0))
        g.add_node("mid", Point2D(5.0, 0.0))
        g.add_node("incident", Point2D(10.0, 0.0))
        g.add_edge("station", "mid", 5.0)
        g.add_edge("mid", "incident", 5.0)
        return g

    def test_found_dispatch_produces_polyline(self):
        graph = self._graph()
        station = EmergencyStation(
            station_id="s1",
            node_id="station",
            position=Point2D(0.0, 0.0),
            unit_types=frozenset({EmergencyUnitType.FIRE_TRUCK}),
        )
        path_result = PathResult(
            path=["station", "mid", "incident"], cost=10.0, expanded_nodes=3, found=True
        )
        dispatch = DispatchResult(
            station=station,
            unit_type=EmergencyUnitType.FIRE_TRUCK,
            path_result=path_result,
            estimated_response_seconds=42.0,
            found=True,
        )
        frame = emergency_vehicle_icon_frame(dispatch, node_to_world=lambda n: graph.positions[n])
        assert frame.found is True
        assert len(frame.route_polyline) == 3
        assert frame.estimated_response_seconds == 42.0

    def test_not_found_dispatch_produces_empty_polyline(self):
        station = EmergencyStation(
            station_id="s1",
            node_id="station",
            position=Point2D(0.0, 0.0),
            unit_types=frozenset({EmergencyUnitType.AMBULANCE}),
        )
        path_result = PathResult(path=[], cost=0.0, expanded_nodes=0, found=False)
        dispatch = DispatchResult(
            station=station,
            unit_type=EmergencyUnitType.AMBULANCE,
            path_result=path_result,
            estimated_response_seconds=float("inf"),
            found=False,
        )
        frame = emergency_vehicle_icon_frame(dispatch, node_to_world=lambda n: Point2D(0.0, 0.0))
        assert frame.found is False
        assert frame.route_polyline == []


# --------------------------------------------------------------------------- #
# 5.3 — Heatmap -> sahne bağlantısı
# --------------------------------------------------------------------------- #


class TestFaz5_3HeatmapSceneBinding:
    def test_empty_heatmap_produces_empty_layer(self):
        assert bind_heatmap_to_scene_layer([]) == []

    def test_heatmap_cells_map_to_flat_dicts(self):
        cells = crowd_heatmap_overlay({(0, 0): 4, (1, 0): 1}, cell_size=2.0)
        layer = bind_heatmap_to_scene_layer(cells)
        assert len(layer) == 2
        assert all(entry["type"] == "heatmap_cell" for entry in layer)
        densest = max(layer, key=lambda e: e["density_ratio"])
        assert densest["density_ratio"] == 1.0

    def test_scene_push_overlay_layer_roundtrip(self):
        cells = crowd_heatmap_overlay({(0, 0): 2}, cell_size=1.0)
        layer = bind_heatmap_to_scene_layer(cells)
        scene = Scene()
        count = scene.push_overlay_layer(layer)
        assert count == 1
        as_dict = scene.to_dict()
        assert as_dict["overlay_layers"] == layer
        assert as_dict["schema_version"] == "1.5"


# --------------------------------------------------------------------------- #
# 5.4 — Sentetik nüfus günlük rutini -> görsel etiket
# --------------------------------------------------------------------------- #


class TestFaz5_4DailyRoutineVisualTag:
    def test_office_worker_commutes_in_morning(self):
        assert (
            daily_routine_visual_tag(DailyRoutineType.WORKER_OFFICE, 8)
            == RoutineVisualState.COMMUTING
        )

    def test_office_worker_at_work_midday(self):
        assert (
            daily_routine_visual_tag(DailyRoutineType.WORKER_OFFICE, 12)
            == RoutineVisualState.AT_WORK_OR_SCHOOL
        )

    def test_office_worker_at_home_at_night(self):
        assert (
            daily_routine_visual_tag(DailyRoutineType.WORKER_OFFICE, 23)
            == RoutineVisualState.AT_HOME
        )

    def test_retired_always_at_home(self):
        for hour in (8, 12, 18, 2):
            assert (
                daily_routine_visual_tag(DailyRoutineType.RETIRED, hour)
                == RoutineVisualState.AT_HOME
            )

    def test_hour_wraps_modulo_24(self):
        assert (
            daily_routine_visual_tag(DailyRoutineType.WORKER_OFFICE, 8 + 24)
            == RoutineVisualState.COMMUTING
        )


# --------------------------------------------------------------------------- #
# 5.5 — Trafik/araç sahne karesi
# --------------------------------------------------------------------------- #


class TestFaz5_5TrafficVehicleSceneFrame:
    def _agent(self, speed: float) -> TrafficAgent:
        agent = TrafficAgent(
            agent_id=1,
            vehicle_type=VehicleType.ARAC,
            route_nodes=["a", "b"],
            route_positions=[Point2D(0.0, 0.0), Point2D(10.0, 0.0)],
        )
        agent.speed = speed
        return agent

    def test_free_flow_tint_at_desired_speed(self):
        agent = self._agent(speed=15.0)  # ARAC desired_speed = 15.0
        frame = traffic_vehicle_scene_frame(agent)
        assert frame.speed_tint == VehicleSpeedTint.FREE_FLOW

    def test_congested_tint_at_low_speed(self):
        agent = self._agent(speed=1.0)
        frame = traffic_vehicle_scene_frame(agent)
        assert frame.speed_tint == VehicleSpeedTint.CONGESTED

    def test_flow_model_overrides_speed_heuristic(self):
        agent = self._agent(speed=15.0)  # kendi hızına göre FREE_FLOW olurdu
        model = GreenshieldsModel(free_flow_speed=50.0, jam_density=100.0)
        frame = traffic_vehicle_scene_frame(agent, density_veh_per_km=90.0, flow_model=model)
        assert frame.speed_tint == VehicleSpeedTint.CONGESTED

    def test_heading_points_along_route(self):
        agent = self._agent(speed=5.0)
        frame = traffic_vehicle_scene_frame(agent)
        assert frame.heading_deg == 0.0  # +x yönünde ilerliyor


# --------------------------------------------------------------------------- #
# 5.6 — Ses/işitsel katman
# --------------------------------------------------------------------------- #


class TestFaz5_6SpatialAudio:
    def test_no_events_when_nothing_triggered(self):
        assert trigger_event_sound() == []

    def test_shake_triggers_rumble_and_creak_above_threshold(self):
        events = trigger_event_sound(shake_intensity=0.6)
        ids = {e.effect for e in events}
        assert SoundEffectId.EARTHQUAKE_RUMBLE in ids
        assert SoundEffectId.STRUCTURE_CREAK in ids

    def test_mild_shake_does_not_trigger_creak(self):
        events = trigger_event_sound(shake_intensity=0.1)
        ids = {e.effect for e in events}
        assert SoundEffectId.EARTHQUAKE_RUMBLE in ids
        assert SoundEffectId.STRUCTURE_CREAK not in ids

    def test_fire_alarm_and_glass_shatter_independent_flags(self):
        events = trigger_event_sound(fire_alarm_active=True, glass_shatter_triggered=True)
        ids = {e.effect for e in events}
        assert ids == {SoundEffectId.FIRE_ALARM, SoundEffectId.GLASS_SHATTER}

    def test_crowd_ambience_weights_sum_to_one(self):
        mix = crowd_ambience_mix(CrowdPressureLevel.SQUEEZE, panic_ratio=0.3)
        assert abs(sum(mix.values()) - 1.0) < 1e-9

    def test_panic_ratio_dominates_mix(self):
        mix = crowd_ambience_mix(CrowdPressureLevel.NONE, panic_ratio=1.0)
        assert mix[CrowdAmbienceLayer.PANIC_SHOUTING] == 1.0

    def test_no_pressure_no_panic_favors_light_chatter(self):
        mix = crowd_ambience_mix(CrowdPressureLevel.NONE, panic_ratio=0.0)
        assert mix[CrowdAmbienceLayer.LIGHT_CHATTER] > mix[CrowdAmbienceLayer.DENSE_MURMUR]

    def test_positional_gain_decreases_with_distance(self):
        source = PositionalAudioSource(position=(0.0, 0.0, 0.0))
        near = positional_gain(source, (1.0, 0.0, 0.0))
        far = positional_gain(source, (100.0, 0.0, 0.0))
        assert near > far

    def test_positional_gain_at_ref_distance_is_max(self):
        source = PositionalAudioSource(position=(0.0, 0.0, 0.0), ref_distance_m=5.0)
        gain_at_ref = positional_gain(source, (5.0, 0.0, 0.0))
        assert abs(gain_at_ref - 1.0) < 1e-9

    def test_positional_gain_never_negative_or_above_one(self):
        source = PositionalAudioSource(position=(0.0, 0.0, 0.0))
        for dist in (0.0, 5.0, 50.0, 500.0, 5000.0):
            gain = positional_gain(source, (dist, 0.0, 0.0))
            assert 0.0 <= gain <= 1.0

    def test_scenario_signatures_are_distinct(self):
        earthquake = set(SCENARIO_SOUND_SIGNATURE[ScenarioKind.EARTHQUAKE])
        fire = set(SCENARIO_SOUND_SIGNATURE[ScenarioKind.FIRE])
        assert earthquake.isdisjoint(fire)

    def test_scene_audio_event_roundtrip(self):
        scene = Scene()
        source = PositionalAudioSource(position=(1.0, 2.0, 3.0))
        events = trigger_event_sound(shake_intensity=0.6, source=source)
        for e in events:
            scene.push_audio_event(
                {
                    "effect": e.effect.value,
                    "source": list(e.source.position) if e.source else None,
                    "intensity": e.intensity,
                }
            )
        as_dict = scene.to_dict()
        assert len(as_dict["audio_events"]) == len(events)
