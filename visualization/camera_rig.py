"""
Visualization - Camera (Phase 9)
==================================

Roadmap Phase 9 "Camera": Orbit, FPS, Drone, Free Fly, Cinematic.

Hepsi ortak bir `Camera(position, target, fov)` state'i paylaşır;
`CameraRig` moda göre farklı girdi (input) yorumlama kuralları uygular.
Gerçek input handling (mouse/keyboard/gamepad) backend'e ait olduğu için
burada sadece "bu deltalarla kamera state'i nasıl değişir" mantığı var.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

Vec3 = tuple[float, float, float]


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _length(a: Vec3) -> float:
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def _normalize(a: Vec3) -> Vec3:
    l = _length(a)
    if l < 1e-12:
        return (0.0, 0.0, 0.0)
    return (a[0] / l, a[1] / l, a[2] / l)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


class CameraMode(Enum):
    ORBIT = "orbit"
    FPS = "fps"
    DRONE = "drone"
    FREE_FLY = "free_fly"
    CINEMATIC = "cinematic"


@dataclass(slots=True)
class Camera:
    position: Vec3 = (0.0, 0.0, 10.0)
    target: Vec3 = (0.0, 0.0, 0.0)
    fov_deg: float = 60.0
    up: Vec3 = (0.0, 0.0, 1.0)

    def forward(self) -> Vec3:
        return _normalize(_sub(self.target, self.position))

    def right(self) -> Vec3:
        return _normalize(_cross(self.forward(), self.up))


@dataclass(slots=True)
class CinematicKeyframe:
    time_s: float
    position: Vec3
    target: Vec3
    fov_deg: float = 60.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_vec3(a: Vec3, b: Vec3, t: float) -> Vec3:
    return (_lerp(a[0], b[0], t), _lerp(a[1], b[1], t), _lerp(a[2], b[2], t))


class CameraRig:
    """Aktif moda göre girdi deltalarını `Camera` state'ine uygular."""

    def __init__(self, camera: Camera | None = None, mode: CameraMode = CameraMode.ORBIT):
        self.camera = camera or Camera()
        self.mode = mode
        self._cinematic_track: list[CinematicKeyframe] = []

    # -- Orbit --------------------------------------------------------- #
    def orbit(
        self, delta_yaw_deg: float, delta_pitch_deg: float, delta_distance: float = 0.0
    ) -> Camera:
        """Hedef nokta etrafında yörünge (azimuth/elevation küresel koordinat)."""
        offset = _sub(self.camera.position, self.camera.target)
        radius = max(0.01, _length(offset) + delta_distance)

        # mevcut azimuth/elevation'ı geri hesapla
        azimuth = math.atan2(offset[1], offset[0])
        elevation = math.asin(clamp(offset[2] / max(_length(offset), 1e-9), -1.0, 1.0))

        azimuth += math.radians(delta_yaw_deg)
        elevation = clamp(
            elevation + math.radians(delta_pitch_deg), -math.pi / 2 + 0.01, math.pi / 2 - 0.01
        )

        new_offset = (
            radius * math.cos(elevation) * math.cos(azimuth),
            radius * math.cos(elevation) * math.sin(azimuth),
            radius * math.sin(elevation),
        )
        self.camera.position = _add(self.camera.target, new_offset)
        return self.camera

    # -- FPS ------------------------------------------------------------ #
    def fps_move(
        self, forward_amount: float, strafe_amount: float, up_amount: float = 0.0
    ) -> Camera:
        fwd = self.camera.forward()
        right = self.camera.right()
        delta = _add(
            _add(_scale(fwd, forward_amount), _scale(right, strafe_amount)),
            _scale(self.camera.up, up_amount),
        )
        self.camera.position = _add(self.camera.position, delta)
        self.camera.target = _add(self.camera.target, delta)
        return self.camera

    def fps_look(self, delta_yaw_deg: float, delta_pitch_deg: float) -> Camera:
        fwd = self.camera.forward()
        yaw = math.atan2(fwd[1], fwd[0]) + math.radians(delta_yaw_deg)
        pitch = math.asin(clamp(fwd[2], -1.0, 1.0)) + math.radians(delta_pitch_deg)
        pitch = clamp(pitch, -math.pi / 2 + 0.01, math.pi / 2 - 0.01)
        new_fwd = (
            math.cos(pitch) * math.cos(yaw),
            math.cos(pitch) * math.sin(yaw),
            math.sin(pitch),
        )
        self.camera.target = _add(self.camera.position, new_fwd)
        return self.camera

    # -- Drone (altitude-hold + yaw/throttle) ---------------------------- #
    def drone_move(
        self,
        forward_amount: float,
        strafe_amount: float,
        altitude_delta: float,
        yaw_delta_deg: float = 0.0,
    ) -> Camera:
        fwd_flat = _normalize((self.camera.forward()[0], self.camera.forward()[1], 0.0))
        right_flat = _normalize(_cross(fwd_flat, (0, 0, 1)))
        delta = _add(_scale(fwd_flat, forward_amount), _scale(right_flat, strafe_amount))
        delta = _add(delta, (0.0, 0.0, altitude_delta))
        self.camera.position = _add(self.camera.position, delta)
        self.camera.target = _add(self.camera.target, delta)
        if yaw_delta_deg:
            self.fps_look(yaw_delta_deg, 0.0)
        return self.camera

    # -- Free Fly (tam 6DOF) ---------------------------------------------- #
    def free_fly(
        self,
        forward_amount: float,
        strafe_amount: float,
        up_amount: float,
        yaw_delta_deg: float,
        pitch_delta_deg: float,
    ) -> Camera:
        self.fps_look(yaw_delta_deg, pitch_delta_deg)
        return self.fps_move(forward_amount, strafe_amount, up_amount)

    # -- Cinematic (keyframe interpolation) ------------------------------- #
    def set_cinematic_track(self, keyframes: list[CinematicKeyframe]) -> None:
        self._cinematic_track = sorted(keyframes, key=lambda k: k.time_s)

    def cinematic_at(self, time_s: float) -> Camera:
        track = self._cinematic_track
        if not track:
            return self.camera
        if time_s <= track[0].time_s:
            kf = track[0]
            self.camera.position, self.camera.target, self.camera.fov_deg = (
                kf.position,
                kf.target,
                kf.fov_deg,
            )
            return self.camera
        if time_s >= track[-1].time_s:
            kf = track[-1]
            self.camera.position, self.camera.target, self.camera.fov_deg = (
                kf.position,
                kf.target,
                kf.fov_deg,
            )
            return self.camera

        for i in range(len(track) - 1):
            a, b = track[i], track[i + 1]
            if a.time_s <= time_s <= b.time_s:
                span = max(1e-9, b.time_s - a.time_s)
                t = (time_s - a.time_s) / span
                self.camera.position = _lerp_vec3(a.position, b.position, t)
                self.camera.target = _lerp_vec3(a.target, b.target, t)
                self.camera.fov_deg = _lerp(a.fov_deg, b.fov_deg, t)
                return self.camera
        return self.camera


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
