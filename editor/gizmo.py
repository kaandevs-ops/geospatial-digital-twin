"""
Gizmo Matematiği
================

Roadmap Phase 8 (Object Editor) + Roadmap V3 - Faz D5.

Translate/Rotate/Scale gizmo'larının **render-bağımsız** matematiği: bir
mouse ray'i (kamera projeksiyonundan viewer/JS tarafında üretilir), gizmo
merkezine ve seçilen eksene göre tek-eksenli bir hareket miktarına
(öteleme skaleri / rotasyon açısı / ölçek çarpanı) çevirir.

Bu modül kasıtlı olarak hiçbir kamera/projeksiyon/DOM bilgisi içermez -
yalnızca 3B vektör geometrisi (skew-line en-yakın-nokta, ray-plane
kesişimi). Bu sayede tamamen headless test edilebilir: bir test, gerçek
bir fare hareketi simüle etmeden, yalnızca iki `Ray` vererek "eksen boyunca
5 birim sürüklendi" senaryosunu doğrulayabilir.

Roadmap Faz D2/D3'te kurulan "render-agnostic pass tanımı" ilkesiyle
tutarlıdır (bkz. `lighting.ShadowMapPass`, `render_engine` Scene köprüsü):
gerçek fare/DOM event -> `Ray` dönüşümü viewer'ın sorumluluğundadır, burada
yalnızca ray'den sonraki saf geometri hesaplanır.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Vec3 = tuple[float, float, float]

#: Standart gizmo eksenleri (dünya-uzayı, birim vektör).
AXES: dict[str, Vec3] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _normalize(a: Vec3) -> Vec3:
    length = _length(a)
    return _scale(a, 1.0 / length) if length > 1e-12 else a


@dataclass(slots=True)
class Ray:
    """Bir mouse-picking ray'i (dünya-uzayında). `direction` her zaman
    birim vektördür (normalize edilir)."""

    origin: Vec3
    direction: Vec3

    @staticmethod
    def create(origin: Vec3, direction: Vec3) -> "Ray":
        return Ray(origin, _normalize(direction))

    def point_at(self, t: float) -> Vec3:
        return _add(self.origin, _scale(self.direction, t))


class TranslateGizmo:
    """Roadmap A8 kabul kriteri: tek eksenli sürükleme.

    Yöntem: ray ile gizmo ekseni (bir doğru) arasındaki **en yakın nokta
    çifti** (klasik "skew lines closest point" formülü) hesaplanır; bu en
    yakın noktanın eksen üzerindeki parametresi `t`'dir. Sürükleme
    sırasında `t_current - t_start` farkı, eksen boyunca kayan mesafeye
    (dünya birimi cinsinden) karşılık gelir - fare imlecinin ekseni tam
    olarak takip ettiği durumda birebir, aksi halde en-yakın-nokta
    yaklaşıklamasıyla (standart 3D DCC yazılımlarının kullandığı yöntemin
    aynısı)."""

    @staticmethod
    def axis_drag_delta(gizmo_origin: Vec3, axis: str, ray_start: Ray, ray_current: Ray) -> float:
        axis_dir = AXES[axis]
        t_start = TranslateGizmo.closest_point_on_axis(gizmo_origin, axis_dir, ray_start)
        t_current = TranslateGizmo.closest_point_on_axis(gizmo_origin, axis_dir, ray_current)
        return t_current - t_start

    @staticmethod
    def closest_point_on_axis(gizmo_origin: Vec3, axis_dir: Vec3, ray: Ray) -> float:
        """`gizmo_origin + t*axis_dir` doğrusu ile `ray` arasındaki en
        yakın nokta çiftinin, eksen doğrusu üzerindeki `t` parametresini
        döndürür (skew lines closest-point formülü)."""
        d1 = axis_dir
        d2 = ray.direction
        w0 = _sub(gizmo_origin, ray.origin)
        a = _dot(d1, d1)
        b = _dot(d1, d2)
        c = _dot(d2, d2)
        d = _dot(d1, w0)
        e = _dot(d2, w0)
        denom = a * c - b * b
        if abs(denom) < 1e-9:
            # ray, eksene (neredeyse) paralel: eksen üzerine izdüşüm kullan.
            return -d / a if a > 1e-12 else 0.0
        return (b * e - c * d) / denom


class RotateGizmo:
    """Tek eksenli rotasyon: eksene dik, gizmo merkezinden geçen düzlemle
    ray'in kesişim noktası hesaplanır; sürükleme başı/anındaki iki
    kesişim noktası arasındaki (eksen etrafında) imzalı açı farkı
    döndürülür (derece, saat yönünün tersi pozitif - sağ-el kuralı)."""

    @staticmethod
    def axis_drag_angle_deg(gizmo_origin: Vec3, axis: str, ray_start: Ray, ray_current: Ray) -> float:
        axis_dir = AXES[axis]
        p_start = RotateGizmo._plane_intersection(gizmo_origin, axis_dir, ray_start)
        p_current = RotateGizmo._plane_intersection(gizmo_origin, axis_dir, ray_current)
        if p_start is None or p_current is None:
            return 0.0
        v1 = _sub(p_start, gizmo_origin)
        v2 = _sub(p_current, gizmo_origin)
        if _length(v1) < 1e-9 or _length(v2) < 1e-9:
            return 0.0
        cross = _cross(v1, v2)
        sin_theta = _dot(cross, axis_dir)
        cos_theta = _dot(v1, v2)
        return math.degrees(math.atan2(sin_theta, cos_theta))

    @staticmethod
    def _plane_intersection(plane_point: Vec3, plane_normal: Vec3, ray: Ray) -> Vec3 | None:
        denom = _dot(plane_normal, ray.direction)
        if abs(denom) < 1e-9:
            return None  # ray düzleme paralel
        t = _dot(_sub(plane_point, ray.origin), plane_normal) / denom
        if t < 0:
            return None  # kesişim ray'in arkasında
        return ray.point_at(t)


class ScaleGizmo:
    """Tek eksenli ölçekleme: `TranslateGizmo` ile aynı en-yakın-nokta
    parametresi (`t`) kullanılır, ama fark yerine **oran**
    (`t_current / t_start`) alınır - gizmo merkezinden ne kadar uzağa
    sürüklendiği, o oranda ölçek çarpanı üretir (standart DCC davranışı:
    handle'ı merkeze yaklaştırmak küçültür, uzaklaştırmak büyütür)."""

    @staticmethod
    def axis_drag_factor(
        gizmo_origin: Vec3, axis: str, ray_start: Ray, ray_current: Ray,
        min_factor: float = 0.01,
    ) -> float:
        axis_dir = AXES[axis]
        t_start = TranslateGizmo.closest_point_on_axis(gizmo_origin, axis_dir, ray_start)
        t_current = TranslateGizmo.closest_point_on_axis(gizmo_origin, axis_dir, ray_current)
        if abs(t_start) < 1e-6:
            return 1.0
        factor = t_current / t_start
        return max(min_factor, factor)
