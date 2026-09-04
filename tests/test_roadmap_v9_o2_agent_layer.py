"""
Roadmap V9 / OMURGA / O.2 testleri
====================================

Kapsam: `Scene.push_agent_frame()`, `Scene.push_agent_recording()`,
`Scene.clear_agent_frames()`, `to_dict()`'in `agent_frames` alanı ve
`SimulationRecorder` ile uçtan-uca entegrasyon.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from harita.core_engine.geometry_engine import Point2D
from harita.mobility.crowd_simulation import (
    EvacuationSimulator,
    SocialForceModel,
    spawn_random_agents,
)
from harita.mobility.simulation_recorder import AgentFrameState, SimulationRecorder
from harita.render_engine import SCENE_SCHEMA_VERSION, Scene


def test_schema_version_bumped_for_agent_frames():
    assert SCENE_SCHEMA_VERSION == "1.4"


def test_empty_scene_agent_frames_backward_compatible():
    scene = Scene(name="s")
    d = scene.to_dict()
    assert d["agent_frames"] == []
    json.dumps(d)  # tam serileştirilebilir olmalı


def test_push_agent_frame_from_dicts():
    scene = Scene(name="s")
    frame = scene.push_agent_frame(
        1.5,
        [
            {"agent_id": 0, "x": 1.0, "y": 2.0, "state": "moving"},
            {"id": 1, "x": 3.0, "y": 4.0, "state": "panic"},
        ],
    )
    assert frame["t"] == 1.5
    assert frame["agents"] == [
        {"id": 0, "x": 1.0, "y": 2.0, "state": "moving"},
        {"id": 1, "x": 3.0, "y": 4.0, "state": "panic"},
    ]
    assert scene.agent_frames == [frame]


def test_push_agent_frame_from_agent_snapshot_objects():
    scene = Scene(name="s")
    recorder = SimulationRecorder(keyframe_interval_s=1.0)
    agents = spawn_random_agents(2, Point2D(0, 0), Point2D(1, 1), Point2D(10, 0), seed=1)
    kf = recorder.record_frame(0.0, agents)

    scene.push_agent_frame(kf.t, kf.agents)
    assert len(scene.agent_frames) == 1
    pushed = scene.agent_frames[0]["agents"]
    assert len(pushed) == 2
    assert pushed[0]["state"] == "moving"  # AgentFrameState.value serileşti


def test_push_agent_recording_bulk_transfers_all_keyframes():
    scene = Scene(name="s")
    agents = spawn_random_agents(6, Point2D(0, 0), Point2D(5, 5), Point2D(20, 3), seed=9)
    recorder = SimulationRecorder(keyframe_interval_s=0.5)
    sim = EvacuationSimulator(SocialForceModel())
    sim.run(agents, dt=0.1, max_time_s=20.0, recorder=recorder)

    count = scene.push_agent_recording(recorder)
    assert count == len(recorder.keyframes)
    assert len(scene.agent_frames) == len(recorder.keyframes)
    # zaman damgaları sırayla korunmalı
    assert [f["t"] for f in scene.agent_frames] == [kf.t for kf in recorder.keyframes]


def test_scene_to_dict_agent_frames_is_json_serializable_and_complete():
    scene = Scene(name="s")
    agents = spawn_random_agents(4, Point2D(0, 0), Point2D(3, 3), Point2D(15, 3), seed=5)
    recorder = SimulationRecorder(keyframe_interval_s=0.5)
    sim = EvacuationSimulator(SocialForceModel())
    sim.run(agents, dt=0.1, max_time_s=15.0, recorder=recorder)
    scene.push_agent_recording(recorder)

    d = scene.to_dict()
    payload = json.dumps(d)
    reparsed = json.loads(payload)
    assert len(reparsed["agent_frames"]) == len(recorder.keyframes)
    for frame in reparsed["agent_frames"]:
        for a in frame["agents"]:
            assert set(a.keys()) == {"id", "x", "y", "state"}
            assert a["state"] in {s.value for s in AgentFrameState}


def test_clear_agent_frames_resets_between_scenario_runs():
    scene = Scene(name="s")
    scene.push_agent_frame(0.0, [{"agent_id": 0, "x": 0, "y": 0, "state": "moving"}])
    assert len(scene.agent_frames) == 1
    scene.clear_agent_frames()
    assert scene.agent_frames == []
