"""Roadmap V10 / Faz 7 — Sinematik Yönetmenlik ve Sunum Katmanı.

7.1 (ilginç an tespiti) -> 7.2 (yumuşak kamera geçişi) -> 7.3 (kullanıcı
kontrolü her zaman öncelikli) kapsar.
"""
from __future__ import annotations

import pytest

from harita.visualization.camera_rig import Camera, CameraRig
from harita.visualization.cinematic_director import (
    AutoCameraController,
    InterestScorer,
    SceneEvent,
    SceneEventKind,
    build_transition_keyframes,
)


# --------------------------------------------------------------------------- #
# 7.1 — Otomatik "ilginç an" tespiti
# --------------------------------------------------------------------------- #

class TestFaz7_1InterestScorer:
    def test_collapse_beats_ordinary_walk(self):
        scorer = InterestScorer()
        collapse = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=1.0, position=(0, 0, 0))
        walk = SceneEvent(SceneEventKind.ORDINARY_WALK, time_s=1.0, position=(0, 0, 0))
        best = scorer.most_interesting([walk, collapse])
        assert best is collapse

    def test_roadmap_example_order_crack_beats_group_beats_walk(self):
        scorer = InterestScorer()
        crack = SceneEvent(SceneEventKind.BUILDING_FIRST_CRACK, time_s=0.0, position=(0, 0, 0))
        group = SceneEvent(SceneEventKind.GROUP_BEHAVIOR, time_s=0.0, position=(0, 0, 0))
        walk = SceneEvent(SceneEventKind.ORDINARY_WALK, time_s=0.0, position=(0, 0, 0))
        ranked = scorer.ranked([walk, group, crack])
        assert ranked == [crack, group, walk]

    def test_empty_events_returns_none(self):
        scorer = InterestScorer()
        assert scorer.most_interesting([]) is None

    def test_magnitude_scales_score(self):
        scorer = InterestScorer()
        weak = SceneEvent(SceneEventKind.CROWD_DENSITY_PEAK, time_s=0.0, position=(0, 0, 0), magnitude=0.1)
        strong = SceneEvent(SceneEventKind.CROWD_DENSITY_PEAK, time_s=0.0, position=(0, 0, 0), magnitude=1.0)
        assert scorer.score(strong) > scorer.score(weak)

    def test_tie_break_prefers_newest_event(self):
        scorer = InterestScorer()
        older = SceneEvent(SceneEventKind.GROUP_BEHAVIOR, time_s=1.0, position=(0, 0, 0))
        newer = SceneEvent(SceneEventKind.GROUP_BEHAVIOR, time_s=5.0, position=(0, 0, 0))
        assert scorer.most_interesting([older, newer]) is newer


# --------------------------------------------------------------------------- #
# 7.2 — Kamera geçiş dili
# --------------------------------------------------------------------------- #

class TestFaz7_2TransitionKeyframes:
    def test_transition_ends_near_target_event(self):
        cam = Camera(position=(0, 0, 10), target=(0, 0, 0))
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=0.0, position=(50, 50, 0))
        keyframes = build_transition_keyframes(cam, event, ease_samples=4)
        last = keyframes[-1]
        assert last.target == event.position

    def test_fov_stays_constant_across_transition(self):
        cam = Camera(position=(0, 0, 10), target=(0, 0, 0))
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=0.0, position=(50, 50, 0))
        keyframes = build_transition_keyframes(cam, event, fov_deg=42.0, ease_samples=4)
        assert all(kf.fov_deg == 42.0 for kf in keyframes)

    def test_easing_is_monotonic_in_time(self):
        cam = Camera(position=(0, 0, 10), target=(0, 0, 0))
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=0.0, position=(50, 50, 0))
        keyframes = build_transition_keyframes(cam, event, ease_samples=6)
        times = [kf.time_s for kf in keyframes]
        assert times == sorted(times)

    def test_hold_keyframe_appended_after_transition(self):
        cam = Camera(position=(0, 0, 10), target=(0, 0, 0))
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=0.0, position=(50, 50, 0))
        keyframes = build_transition_keyframes(
            cam, event, transition_duration_s=2.0, hold_duration_s=3.0, ease_samples=2,
        )
        assert keyframes[-1].time_s == pytest.approx(5.0)

    def test_keyframes_feed_camera_rig_without_modifying_it(self):
        cam = Camera(position=(0, 0, 10), target=(0, 0, 0))
        rig = CameraRig(camera=cam)
        event = SceneEvent(SceneEventKind.CROWD_DENSITY_PEAK, time_s=0.0, position=(10, 0, 0))
        keyframes = build_transition_keyframes(cam, event, ease_samples=3)
        rig.set_cinematic_track(keyframes)
        result = rig.cinematic_at(0.0)
        assert result.position == cam.position


# --------------------------------------------------------------------------- #
# 7.3 — Kullanıcı kontrolü her zaman öncelikli
# --------------------------------------------------------------------------- #

class TestFaz7_3AutoCameraController:
    def _controller(self) -> AutoCameraController:
        rig = CameraRig(camera=Camera(position=(0, 0, 10), target=(0, 0, 0)))
        return AutoCameraController(rig=rig, idle_threshold_s=5.0)

    def test_no_suggestion_while_user_active(self):
        ctrl = self._controller()
        ctrl.notify_user_input(current_t=0.0)
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=1.0, position=(1, 1, 1))
        suggestion = ctrl.maybe_suggest(current_t=2.0, events=[event])
        assert suggestion is None
        assert ctrl.auto_active is False

    def test_suggestion_after_idle_threshold(self):
        ctrl = self._controller()
        ctrl.notify_user_input(current_t=0.0)
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=1.0, position=(1, 1, 1))
        suggestion = ctrl.maybe_suggest(current_t=10.0, events=[event])
        assert suggestion is event
        assert ctrl.auto_active is True

    def test_user_input_immediately_deactivates_auto(self):
        ctrl = self._controller()
        ctrl.notify_user_input(current_t=0.0)
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=1.0, position=(1, 1, 1))
        ctrl.maybe_suggest(current_t=10.0, events=[event])
        assert ctrl.auto_active is True
        ctrl.notify_user_input(current_t=10.1)
        assert ctrl.auto_active is False

    def test_apply_suggestion_requires_active_state(self):
        ctrl = self._controller()
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=1.0, position=(1, 1, 1))
        with pytest.raises(RuntimeError):
            ctrl.apply_suggestion(event, current_t=0.0)

    def test_apply_suggestion_sets_cinematic_track(self):
        ctrl = self._controller()
        ctrl.notify_user_input(current_t=0.0)
        event = SceneEvent(SceneEventKind.BUILDING_COLLAPSE_START, time_s=1.0, position=(30, 0, 0))
        ctrl.maybe_suggest(current_t=10.0, events=[event])
        keyframes = ctrl.apply_suggestion(event, current_t=10.0, ease_samples=3)
        assert len(keyframes) > 0
        assert ctrl.rig._cinematic_track == sorted(keyframes, key=lambda k: k.time_s)
