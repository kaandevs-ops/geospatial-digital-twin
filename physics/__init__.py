"""
Physics (Roadmap V4 - Track E / Faz E17)
==========================================

"*(Yeni Alt Sistem)* Physics: Rijit Cisim Fiziği Temeli".

Basit bir rijit-cisim simülasyon çekirdeği:

- `RigidBox`: eksene-hizalı dikdörtgenler prizması gövde (kütle, konum,
  hız, açısal hız - basitleştirilmiş "blok" modeli).
- Çarpışma tespiti: `data_engine.spatial_index.AABB3D` yeniden kullanılır
  (roadmap'in öngördüğü gibi) - her adımda kaba-faz (broad-phase) AABB
  kesişim testi.
- İmpuls-tabanlı çözümleyici: penetrasyon-düzeltmeli (Baumgarte
  stabilizasyonu) basit impuls çözümü, yerçekimi + zemin/blok teması.
- `GroundShakeForceModel`: literatür-tabanlı (sinüzoidal taban ivmesi,
  basit bir "pseudo-static" deprem yükü yaklaşımı - Newmark-beta değil,
  tam bir sismik analiz motoru değil, roadmap'in belirttiği kapsam
  sınırlaması budur) yatay taban hareketi kuvvet üreteci.
- `TowerStabilityScenario`: basit bir kule/blok yığını üzerinde, artan
  taban ivmesi altında stabilite/devrilme testi için hazır senaryo.

Bağımlılık yok (stdlib-only, `data_engine.spatial_index` hariç - o da
stdlib-only), Phase 1 Geometry Engine ile aynı prensip.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..data_engine.spatial_index import AABB3D

GRAVITY = 9.81  # m/s^2


# ============================================================================ #
# Rijit gövde
# ============================================================================ #

@dataclass
class RigidBox:
    """Eksene-hizalı dikdörtgenler prizması rijit gövde.

    `position` kutunun merkezi; `half_extents` = (hx, hy, hz) - her
    eksende yarı-boyut. `is_static=True` ise gövde asla hareket etmez
    (zemin/temel için).
    """

    body_id: str
    position: Tuple[float, float, float]
    half_extents: Tuple[float, float, float]
    mass: float = 1.0
    velocity: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    angular_velocity: float = 0.0  # basitleştirme: yalnızca z-ekseni etrafında (2D devrilme)
    orientation: float = 0.0  # radyan, z-ekseni etrafında
    is_static: bool = False
    restitution: float = 0.1
    friction: float = 0.6

    def __post_init__(self) -> None:
        if self.mass <= 0 and not self.is_static:
            raise ValueError("Statik olmayan gövdenin kütlesi pozitif olmalı")

    def aabb(self) -> AABB3D:
        px, py, pz = self.position
        hx, hy, hz = self.half_extents
        # Basitleştirilmiş "devrilme" - küçük açılarda AABB'yi eğime göre
        # genişletiyoruz (tam OBB değil, kaba-faz için yeterli).
        expand = abs(math.sin(self.orientation)) * max(hx, hy)
        return AABB3D(
            px - hx - expand, py - hy - expand, pz - hz,
            px + hx + expand, py + hy + expand, pz + hz,
        )

    def inverse_mass(self) -> float:
        return 0.0 if self.is_static else 1.0 / self.mass

    def top_center(self) -> Tuple[float, float, float]:
        px, py, pz = self.position
        return (px, py, pz + self.half_extents[2])

    def base_center(self) -> Tuple[float, float, float]:
        px, py, pz = self.position
        return (px, py, pz - self.half_extents[2])

    def is_toppled(self, tolerance_rad: float = math.radians(30)) -> bool:
        """Basit devrilme kriteri: orientation açısı, gövdenin taban
        genişliği/yüksekliği oranına göre belirlenen kritik açıyı aşarsa
        (klasik "devrilme, ağırlık merkezi taban dışına çıkınca olur"
        yaklaşımının açısal karşılığı) devrilmiş sayılır."""
        hx, hy, hz = self.half_extents
        # Kritik açı: taban yarı-genişliği / gövde yarı-yüksekliği (yaklaşık).
        critical = math.atan2(min(hx, hy), hz)
        return abs(self.orientation) > min(critical, tolerance_rad) * 1.0 and \
            abs(self.orientation) > critical


# ============================================================================ #
# Kuvvet üreteçleri
# ============================================================================ #

@dataclass
class GroundShakeForceModel:
    """Sinüzoidal yatay taban ivmesi üreteci - basit bir "pseudo-static"
    deprem yükü modeli (literatür: yapısal mühendislikte ön-tasarım için
    kullanılan basitleştirilmiş sinüzoidal taban hareketi yaklaşımı; tam
    bir zaman-tanım alanlı sismik analiz DEĞİLDİR - roadmap E17'nin
    açıkça belirttiği kapsam sınırı).

    `peak_acceleration_g` tepe ivmesi (yerçekimi ivmesinin katı olarak,
    örn. 0.3 -> 0.3*9.81 m/s^2), `frequency_hz` sarsıntı frekansı.
    """

    peak_acceleration_g: float
    frequency_hz: float = 2.0
    phase: float = 0.0

    def acceleration_at(self, t: float) -> float:
        omega = 2.0 * math.pi * self.frequency_hz
        return self.peak_acceleration_g * GRAVITY * math.sin(omega * t + self.phase)

    def force_on(self, body: RigidBox, t: float) -> Tuple[float, float, float]:
        if body.is_static:
            return (0.0, 0.0, 0.0)
        a = self.acceleration_at(t)
        return (body.mass * a, 0.0, 0.0)


# ============================================================================ #
# Çarpışma tespiti + impuls-tabanlı çözümleyici
# ============================================================================ #

@dataclass
class ContactManifold:
    body_a: RigidBox
    body_b: RigidBox
    penetration: float
    normal: Tuple[float, float, float]


def detect_collisions(bodies: List[RigidBox]) -> List[ContactManifold]:
    """Kaba-faz: `AABB3D.intersects` (`data_engine.spatial_index`).
    N^2 basit tarama - roadmap'in hedeflediği "temel" seviye için yeterli;
    büyük gövde sayılarında `data_engine.spatial_index.Octree` ile
    genişletilebilir (aynı `AABB3D` sözleşmesi üzerinden, gelecekteki bir
    optimizasyon - bu oturumun kapsamı dışında)."""
    contacts: List[ContactManifold] = []
    n = len(bodies)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = bodies[i], bodies[j]
            if a.is_static and b.is_static:
                continue
            box_a, box_b = a.aabb(), b.aabb()
            if not box_a.intersects(box_b):
                continue
            # Penetrasyon derinliği: her eksendeki örtüşmenin minimumu
            # (basit SAT - AABB'ler için yeterli).
            overlaps = [
                min(box_a.max_x, box_b.max_x) - max(box_a.min_x, box_b.min_x),
                min(box_a.max_y, box_b.max_y) - max(box_a.min_y, box_b.min_y),
                min(box_a.max_z, box_b.max_z) - max(box_a.min_z, box_b.min_z),
            ]
            axis = overlaps.index(min(overlaps))
            penetration = overlaps[axis]
            normal = [0.0, 0.0, 0.0]
            ca, cb = box_a.center(), box_b.center()
            normal[axis] = 1.0 if ca[axis] <= cb[axis] else -1.0
            contacts.append(ContactManifold(a, b, penetration, tuple(normal)))
    return contacts


def _resolve_contact(contact: ContactManifold, restitution_override: Optional[float] = None) -> None:
    a, b = contact.body_a, contact.body_b
    inv_mass_a, inv_mass_b = a.inverse_mass(), b.inverse_mass()
    total_inv_mass = inv_mass_a + inv_mass_b
    if total_inv_mass == 0:
        return

    nx, ny, nz = contact.normal
    vax, vay, vaz = a.velocity
    vbx, vby, vbz = b.velocity
    rel_vel = (vbx - vax) * nx + (vby - vay) * ny + (vbz - vaz) * nz
    if rel_vel > 0:
        return  # zaten ayrılıyorlar

    restitution = restitution_override if restitution_override is not None else min(a.restitution, b.restitution)
    j = -(1 + restitution) * rel_vel / total_inv_mass

    impulse = (j * nx, j * ny, j * nz)
    if not a.is_static:
        a.velocity = (
            vax - impulse[0] * inv_mass_a,
            vay - impulse[1] * inv_mass_a,
            vaz - impulse[2] * inv_mass_a,
        )
    if not b.is_static:
        b.velocity = (
            vbx + impulse[0] * inv_mass_b,
            vby + impulse[1] * inv_mass_b,
            vbz + impulse[2] * inv_mass_b,
        )

    # Baumgarte-tarzı konum düzeltmesi (penetrasyonu doğrudan gider,
    # sızıntıyı önler - tam bir sequential-impulse solver değil ama
    # "temel" seviye için yeterli ve kararlı).
    correction_percent = 0.2
    slop = 0.01
    correction_mag = max(contact.penetration - slop, 0.0) / total_inv_mass * correction_percent
    correction = (nx * correction_mag, ny * correction_mag, nz * correction_mag)
    if not a.is_static:
        a.position = (
            a.position[0] - correction[0] * inv_mass_a,
            a.position[1] - correction[1] * inv_mass_a,
            a.position[2] - correction[2] * inv_mass_a,
        )
    if not b.is_static:
        b.position = (
            b.position[0] + correction[0] * inv_mass_b,
            b.position[1] + correction[1] * inv_mass_b,
            b.position[2] + correction[2] * inv_mass_b,
        )


@dataclass
class PhysicsWorld:
    """Basit sabit-adımlı rijit-cisim dünyası - yerçekimi + AABB çarpışma
    + impuls çözümü + (opsiyonel) `GroundShakeForceModel`.

    Devrilme/açısal davranış tam bir 3D dönme dinamiği değildir
    (basitleştirme: yatay net kuvvetin taban merkezine göre torku,
    `angular_velocity`/`orientation`'a basit bir ters-sarkaç modeliyle
    entegre edilir) - roadmap'in "basit bir sarsıntı-kuvveti modeli, tam
    bir sismik analiz motoru değil" hedefine bilinçli olarak uygun.
    """

    bodies: List[RigidBox] = field(default_factory=list)
    gravity: float = GRAVITY
    shake_model: Optional[GroundShakeForceModel] = None
    time: float = 0.0

    def add_body(self, body: RigidBox) -> None:
        self.bodies.append(body)

    def step(self, dt: float) -> None:
        # 1) Kuvvetler: yerçekimi + (varsa) taban sarsıntısı -> hız/açısal hız.
        for body in self.bodies:
            if body.is_static:
                continue
            vx, vy, vz = body.velocity
            vz -= self.gravity * dt
            if self.shake_model is not None:
                fx, fy, fz = self.shake_model.force_on(body, self.time)
                ax = fx / body.mass
                vx += ax * dt
                # Basitleştirilmiş devrilme torku: yatay kuvvet, gövdenin
                # yarı-yüksekliğiyle orantılı bir açısal ivme üretir
                # (ters-sarkaç yaklaşımı - taban etrafında dönme).
                hz = body.half_extents[2]
                if hz > 0:
                    angular_accel = ax / hz
                    body.angular_velocity += angular_accel * dt
            body.velocity = (vx, vy, vz)

        # 2) Entegrasyon: hız -> konum, açısal hız -> yönelim.
        for body in self.bodies:
            if body.is_static:
                continue
            px, py, pz = body.position
            vx, vy, vz = body.velocity
            body.position = (px + vx * dt, py + vy * dt, pz + vz * dt)
            body.orientation += body.angular_velocity * dt
            # Açısal sönümleme (numerik kararlılık için hafif damping).
            body.angular_velocity *= 0.995

        # 3) Çarpışma tespiti + çözüm (birkaç iterasyon - sequential impulse
        #    yaklaşımının basitleştirilmiş hali).
        for _ in range(4):
            contacts = detect_collisions(self.bodies)
            for contact in contacts:
                _resolve_contact(contact)

        self.time += dt

    def run(self, duration_s: float, dt: float = 1.0 / 60.0) -> None:
        steps = max(1, int(round(duration_s / dt)))
        for _ in range(steps):
            self.step(dt)


# ============================================================================ #
# Hazır senaryo: kule/blok yığını stabilite testi
# ============================================================================ #

@dataclass
class TowerStabilityScenario:
    """Basit bir kule (blok yığını) - kabul kriteri senaryosu: yeterince
    güçlü taban ivmesi altında devrilir, düşük ivmede stabil kalır."""

    num_blocks: int = 4
    block_half_extents: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    block_mass: float = 500.0

    def build_world(self, peak_acceleration_g: float, frequency_hz: float = 1.5) -> PhysicsWorld:
        world = PhysicsWorld()
        ground = RigidBox(
            body_id="ground",
            position=(0.0, 0.0, -1.0),
            half_extents=(50.0, 50.0, 1.0),
            is_static=True,
            friction=0.9,
        )
        world.add_body(ground)

        hz = self.block_half_extents[2]
        for i in range(self.num_blocks):
            z = 2 * hz * i + hz
            block = RigidBox(
                body_id=f"block_{i}",
                position=(0.0, 0.0, z),
                half_extents=self.block_half_extents,
                mass=self.block_mass,
                restitution=0.05,
                friction=0.7,
            )
            world.add_body(block)

        if peak_acceleration_g > 0:
            world.shake_model = GroundShakeForceModel(
                peak_acceleration_g=peak_acceleration_g, frequency_hz=frequency_hz,
            )
        return world

    def run_stability_test(
        self, peak_acceleration_g: float, duration_s: float = 6.0,
        frequency_hz: float = 1.5,
    ) -> bool:
        """`True` dönerse en az bir blok test süresi içinde devrildi."""
        world = self.build_world(peak_acceleration_g, frequency_hz)
        world.run(duration_s)
        return any(b.is_toppled() for b in world.bodies if not b.is_static)


__all__ = [
    "GRAVITY",
    "RigidBox",
    "GroundShakeForceModel",
    "ContactManifold",
    "detect_collisions",
    "PhysicsWorld",
    "TowerStabilityScenario",
]
