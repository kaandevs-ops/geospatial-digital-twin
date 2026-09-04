"""
physics.fluid_sim — Roadmap V10 / Faz 6 (Deneysel — teslim tarihi
garanti edilmez)
========================================================================

"Gerçek Zamanlı Akışkan/Duman CFD Simülasyonu" fazının veri/mantık
katmanı. `physics.building_shake` / `physics.building_damage` ile aynı
disiplin: bu modül GPU/compute-shader kodu İÇERMEZ — CPU üzerinde,
stdlib-only (roadmap'in proje geneli kısıtı — `mesh_engine.
MeshSplitter`'daki "gerçek Voronoi değil, stdlib-only" notuyla aynı
disiplin) çalışan, düşük çözünürlüklü bir "kararlı akışkan" (Stable
Fluids / Jos Stam) çözücüsüdür.

Kapsanan roadmap maddeleri:
- **6.2.1** — `StableFluidsSimulation`: advection (semi-Lagrangian geri
  izleme + trilinear örnekleme) + diffusion (Gauss-Seidel gevşetme) +
  basınç projeksiyonu (kütle korunumu, ayrık Poisson denklemi) — Jos
  Stam'ın "Stable Fluids" (1999) yönteminin standart üç adımı.
- **6.2.2** — `is_cfd_eligible()`: `physics.building_damage.
  is_fragmentation_eligible()` ile AYNI kademeli-devreye-alma deseni —
  yalnızca kamera-odaklı TEK bina için çalıştırılabilir; şehir geneli
  ASLA değil. Diğer binalarda `visualization.scenario_visual_bridge.
  fire_facade_overlay()` (Faz 5.1) devam eder — bu bir "eksiklik" değil,
  roadmap 6.3'ün "zorunlu fallback" maddesinin doğrudan uygulamasıdır.
- **6.2.3** — `inject_fire_heat_source()`: `hazard_data.fire_spread.
  FireSpreadModel` (V9, DEĞİŞTİRİLMEDEN) hücre-yoğunluğunu ısı
  kaynağı + kaldırma kuvveti (buoyancy) olarak akışkan ızgarasına
  besler; `open_boundary_cells` (varsa bina modelinden açık pencere/
  kapı) sınır koşulu olarak `boundary_mask`'e işlenir.
- **6.2.4** — Dürüstlük: bu modül CPU'da, küçük ızgaralarda (varsayılan
  16×16×8) çalışır. Roadmap'in öngördüğü GERÇEK gerçek-zamanlı GPU
  compute-shader (WebGPU/WebGL2) sürümü bu depoda YOKTUR — proje bir
  render motoru içermez (Faz 2 STATUS notuyla aynı sınır). Burada
  üretilen `voxel_frame()` sözleşmesi, o compute-shader'ı yazacak
  render katmanının tüketeceği REFERANS/PROTOTİP çözücüdür; roadmap
  6.3'ün kendi notuyla ("tek bina, tek senaryo prototipiyle sınırlı
  tutulmalı") birebir tutarlıdır.

Dürüstlük notu (roadmap 6.3, kalıcı — silinmeyecek): Bu, akademik
hassasiyette bir CFD çözücüsü DEĞİLDİR; sayısal ıraksama (patlama)
riski taşıyan deneysel bir görsel yaklaşıklıktır. Varsayılan/kalıcı
güvenli mod her zaman Faz 5.1'in sprite tabanlıdır — `cfd_or_sprite_
frame()` bu düşüşü otomatik uygular.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..hazard_data.fire_spread import CellId, FireCellState, FireSpreadModel

__all__ = [
    "CFD_EXPERIMENTAL_HONESTY_NOTE",
    "FluidGridConfig",
    "StableFluidsSimulation",
    "is_cfd_eligible",
    "SmokeVoxel",
    "cfd_or_sprite_frame",
]


#: Roadmap 6.3 — kalıcı, proje boyunca korunacak dürüstlük notu (aynı
#: üslup: `physics.building_damage.DAMAGE_HONESTY_NOTE`,
#: `visualization.scenario_visual_bridge.FIRE_OVERLAY_HONESTY_NOTE`).
CFD_EXPERIMENTAL_HONESTY_NOTE = (
    "Bu duman görselleştirmesi DENEYSEL bir CPU 'kararlı akışkan' "
    "(Stable Fluids) yaklaşıklığıdır; gerçek zamanlı GPU compute-shader "
    "CFD DEĞİLDİR ve akademik hassasiyette bir akışkanlar dinamiği "
    "sonucu değildir. Yalnızca kameranın odaklandığı TEK bina için "
    "çalışır; sayısal kararsızlık ihtimaline karşı her zaman Faz 5.1 "
    "sprite tabanlı yaklaşım güvenli varsayılan (fallback) olarak kalır."
)


# ======================================================================== #
# 6.2.1 — Basitleştirilmiş grid-tabanlı sıvı simülasyonu (Stable Fluids)
# ======================================================================== #


@dataclass(slots=True, frozen=True)
class FluidGridConfig:
    """Bina-ölçekli akışkan ızgarasının boyutları/katsayıları.

    Roadmap'in kendi önerisi "bina başına 32x32x16" — varsayılan burada
    daha küçük (16x16x8) tutuldu çünkü çözücü saf Python'da çalışıyor
    (stdlib-only proje kısıtı); çağıran taraf isterse 32x32x16'ya
    yükseltebilir (maliyet/gerçekçilik takası açıkça çağıranın kararı).
    """

    nx: int = 16
    ny: int = 16
    nz: int = 8
    cell_size_m: float = 0.4
    diffusion: float = 0.0008
    viscosity: float = 0.0006
    buoyancy: float = 3.2          # ısı -> yukarı itki katsayısı
    dissipation: float = 0.985     # her adımda yoğunluğun sönümlenmesi

    def __post_init__(self) -> None:
        if self.nx < 2 or self.ny < 2 or self.nz < 2:
            raise ValueError("FluidGridConfig: nx/ny/nz en az 2 olmalı")
        if self.cell_size_m <= 0:
            raise ValueError("FluidGridConfig: cell_size_m > 0 olmalı")

    @property
    def cell_count(self) -> int:
        return self.nx * self.ny * self.nz


def _idx(x: int, y: int, z: int, nx: int, ny: int) -> int:
    return x + y * nx + z * nx * ny


def _clampi(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else (hi if v > hi else v)


class StableFluidsSimulation:
    """Jos Stam'ın "Stable Fluids" yönteminin 3B, saf-Python (stdlib-only)
    uygulaması: her `step()` çağrısı sırasıyla

        1. kaynak ekleme (`add_density_source` / `add_velocity_source`
           çağrılarıyla önceden biriktirilmiş kaynaklar),
        2. difüzyon (Gauss-Seidel gevşetme, koşulsuz kararlı — büyük
           `dt` değerlerinde de patlamaz, Stam'ın yönteminin temel
           avantajı),
        3. adveksiyon (semi-Lagrangian geri izleme + trilinear
           örnekleme),
        4. projeksiyon (kütle korunumu — ayrık Poisson denklemi,
           Gauss-Seidel ile yaklaşık çözüm)

    adımlarını uygular. Isı kaynağı ayrıca `+z` yönünde kaldırma
    kuvveti (buoyancy) üretir (6.2.3).
    """

    def __init__(self, config: FluidGridConfig) -> None:
        self.config = config
        n = config.cell_count
        self.density: list[float] = [0.0] * n
        self.temperature: list[float] = [0.0] * n
        self.vx: list[float] = [0.0] * n
        self.vy: list[float] = [0.0] * n
        self.vz: list[float] = [0.0] * n
        # Sınır koşulu maskesi: True = kapalı hücre (duvar), False = açık
        # (pencere/kapı, roadmap 6.2.4 "sınır koşulu" maddesi).
        self.boundary_mask: list[bool] = [False] * n
        self._pending_density: dict[int, float] = {}
        self._pending_heat: dict[int, float] = {}
        self.elapsed_s: float = 0.0
        self.step_count: int = 0
        self.diverged: bool = False

    # -- Yardımcılar -------------------------------------------------- #

    def _in_bounds(self, x: int, y: int, z: int) -> bool:
        c = self.config
        return 0 <= x < c.nx and 0 <= y < c.ny and 0 <= z < c.nz

    def index(self, x: int, y: int, z: int) -> int:
        c = self.config
        if not self._in_bounds(x, y, z):
            raise IndexError(f"grid dışı hücre: ({x},{y},{z})")
        return _idx(x, y, z, c.nx, c.ny)

    def set_boundary(self, x: int, y: int, z: int, *, closed: bool) -> None:
        """Bir hücreyi duvar (`closed=True`) ya da açık pencere/kapı
        (`closed=False`, varsayılan) olarak işaretler (roadmap 6.2.3
        "sınır koşulu" maddesi)."""
        self.boundary_mask[self.index(x, y, z)] = closed

    # -- Kaynak ekleme (6.2.3) ----------------------------------------- #

    def add_density_source(self, x: int, y: int, z: int, amount: float) -> None:
        if not self._in_bounds(x, y, z) or amount <= 0.0:
            return
        i = self.index(x, y, z)
        self._pending_density[i] = self._pending_density.get(i, 0.0) + amount

    def add_heat_source(self, x: int, y: int, z: int, amount: float) -> None:
        if not self._in_bounds(x, y, z) or amount <= 0.0:
            return
        i = self.index(x, y, z)
        self._pending_heat[i] = self._pending_heat.get(i, 0.0) + amount

    def inject_fire_heat_source(
        self,
        fire_model: FireSpreadModel,
        *,
        cell_to_grid_xy: Callable[[CellId], tuple[int, int]],
        source_z: int = 0,
        density_gain: float = 6.0,
        heat_gain: float = 9.0,
    ) -> int:
        """`hazard_data.fire_spread.FireSpreadModel` (DEĞİŞTİRİLMEDEN)
        yanan/duman hücrelerini akışkan ızgarasına ısı+yoğunluk kaynağı
        olarak besler (roadmap 6.2.3, birebir). `CLEAR` hücreler
        atlanır (Faz 5.1'in `fire_facade_overlay()` ile aynı eşik
        disiplini). Kaç kaynak hücre enjekte edildiğini döner (0 ise
        çağıran taraf `step()` çağırmadan önce bunu bilebilir)."""
        injected = 0
        for cell, intensity in fire_model.intensity.items():
            if fire_model.state_of(cell) is FireCellState.CLEAR:
                continue
            gx, gy = cell_to_grid_xy(cell)
            if not self._in_bounds(gx, gy, source_z):
                continue
            self.add_density_source(gx, gy, source_z, density_gain * intensity)
            self.add_heat_source(gx, gy, source_z, heat_gain * intensity)
            injected += 1
        return injected

    # -- Çözücü adımları ------------------------------------------------ #

    def _apply_pending_sources(self, dt: float) -> None:
        for i, amount in self._pending_density.items():
            self.density[i] = min(1.0, self.density[i] + amount * dt)
        for i, amount in self._pending_heat.items():
            self.temperature[i] = min(1.0, self.temperature[i] + amount * dt)
            # Isı -> +z kaldırma kuvveti (basitleştirilmiş Boussinesq terimi).
            self.vz[i] += self.config.buoyancy * amount * dt
        self._pending_density.clear()
        self._pending_heat.clear()

    def _diffuse(self, field: list[float], diff: float, dt: float, iterations: int = 12) -> list[float]:
        c = self.config
        a = dt * diff * c.nx * c.ny * c.nz
        out = list(field)
        denom = 1.0 + 6.0 * a
        for _ in range(iterations):
            for z in range(c.nz):
                for y in range(c.ny):
                    for x in range(c.nx):
                        i = _idx(x, y, z, c.nx, c.ny)
                        if self.boundary_mask[i]:
                            out[i] = 0.0
                            continue
                        s = 0.0
                        for (dx, dy, dz) in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                            nx_, ny_, nz_ = x + dx, y + dy, z + dz
                            if self._in_bounds(nx_, ny_, nz_):
                                ni = _idx(nx_, ny_, nz_, c.nx, c.ny)
                                if not self.boundary_mask[ni]:
                                    s += out[ni]
                        out[i] = (field[i] + a * s) / denom
        return out

    def _sample_trilinear(self, field: list[float], fx: float, fy: float, fz: float) -> float:
        c = self.config
        fx = max(0.5, min(c.nx - 1.5, fx))
        fy = max(0.5, min(c.ny - 1.5, fy))
        fz = max(0.5, min(c.nz - 1.5, fz))
        x0, y0, z0 = int(fx), int(fy), int(fz)
        x1, y1, z1 = x0 + 1, y0 + 1, z0 + 1
        tx, ty, tz = fx - x0, fy - y0, fz - z0

        def g(xi: int, yi: int, zi: int) -> float:
            xi = _clampi(xi, 0, c.nx - 1)
            yi = _clampi(yi, 0, c.ny - 1)
            zi = _clampi(zi, 0, c.nz - 1)
            return field[_idx(xi, yi, zi, c.nx, c.ny)]

        c00 = g(x0, y0, z0) * (1 - tx) + g(x1, y0, z0) * tx
        c10 = g(x0, y1, z0) * (1 - tx) + g(x1, y1, z0) * tx
        c01 = g(x0, y0, z1) * (1 - tx) + g(x1, y0, z1) * tx
        c11 = g(x0, y1, z1) * (1 - tx) + g(x1, y1, z1) * tx
        c0 = c00 * (1 - ty) + c10 * ty
        c1 = c01 * (1 - ty) + c11 * ty
        return c0 * (1 - tz) + c1 * tz

    def _advect(self, field: list[float], dt: float) -> list[float]:
        c = self.config
        out = [0.0] * c.cell_count
        for z in range(c.nz):
            for y in range(c.ny):
                for x in range(c.nx):
                    i = _idx(x, y, z, c.nx, c.ny)
                    if self.boundary_mask[i]:
                        out[i] = 0.0
                        continue
                    # semi-Lagrangian geri izleme
                    bx = x - dt * c.nx * self.vx[i]
                    by = y - dt * c.ny * self.vy[i]
                    bz = z - dt * c.nz * self.vz[i]
                    out[i] = self._sample_trilinear(field, bx, by, bz)
        return out

    def _project(self, iterations: int = 16) -> None:
        """Ayrık basınç-projeksiyonu: hız alanını yaklaşık olarak
        sıkıştırılamaz (kütle korunumlu) hale getirir — Stam'ın yönteminin
        üçüncü adımı."""
        c = self.config
        n = c.cell_count
        div = [0.0] * n
        p = [0.0] * n
        h = 1.0 / max(c.nx, c.ny, c.nz)
        for z in range(c.nz):
            for y in range(c.ny):
                for x in range(c.nx):
                    i = _idx(x, y, z, c.nx, c.ny)
                    if self.boundary_mask[i]:
                        continue
                    x1 = self.vx[_idx(min(x + 1, c.nx - 1), y, z, c.nx, c.ny)]
                    x0 = self.vx[_idx(max(x - 1, 0), y, z, c.nx, c.ny)]
                    y1 = self.vy[_idx(x, min(y + 1, c.ny - 1), z, c.nx, c.ny)]
                    y0 = self.vy[_idx(x, max(y - 1, 0), z, c.nx, c.ny)]
                    z1 = self.vz[_idx(x, y, min(z + 1, c.nz - 1), c.nx, c.ny)]
                    z0 = self.vz[_idx(x, y, max(z - 1, 0), c.nx, c.ny)]
                    div[i] = -0.5 * h * (x1 - x0 + y1 - y0 + z1 - z0)

        for _ in range(iterations):
            for z in range(c.nz):
                for y in range(c.ny):
                    for x in range(c.nx):
                        i = _idx(x, y, z, c.nx, c.ny)
                        if self.boundary_mask[i]:
                            p[i] = 0.0
                            continue
                        s = 0.0
                        cnt = 0
                        for (dx, dy, dz) in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                            nx_, ny_, nz_ = x + dx, y + dy, z + dz
                            if self._in_bounds(nx_, ny_, nz_):
                                ni = _idx(nx_, ny_, nz_, c.nx, c.ny)
                                if not self.boundary_mask[ni]:
                                    s += p[ni]
                                    cnt += 1
                        p[i] = (div[i] + s) / max(1, cnt)

        for z in range(c.nz):
            for y in range(c.ny):
                for x in range(c.nx):
                    i = _idx(x, y, z, c.nx, c.ny)
                    if self.boundary_mask[i]:
                        self.vx[i] = self.vy[i] = self.vz[i] = 0.0
                        continue
                    px1 = p[_idx(min(x + 1, c.nx - 1), y, z, c.nx, c.ny)]
                    px0 = p[_idx(max(x - 1, 0), y, z, c.nx, c.ny)]
                    py1 = p[_idx(x, min(y + 1, c.ny - 1), z, c.nx, c.ny)]
                    py0 = p[_idx(x, max(y - 1, 0), z, c.nx, c.ny)]
                    pz1 = p[_idx(x, y, min(z + 1, c.nz - 1), c.nx, c.ny)]
                    pz0 = p[_idx(x, y, max(z - 1, 0), c.nx, c.ny)]
                    self.vx[i] -= 0.5 * (px1 - px0) / h
                    self.vy[i] -= 0.5 * (py1 - py0) / h
                    self.vz[i] -= 0.5 * (pz1 - pz0) / h

    def step(self, dt: float = 0.1) -> None:
        """Tek bir çözücü adımı: kaynak -> difüzyon -> projeksiyon ->
        adveksiyon -> projeksiyon (Stam'ın önerdiği sıra) + sönümleme.
        Sayısal ıraksama (NaN/aşırı değer) tespit edilirse `self.diverged`
        `True` olur ve çağıran taraf (roadmap 6.3 dürüstlük ilkesi
        gereği) güvenli fallback'e (`cfd_or_sprite_frame`) düşmelidir."""
        c = self.config
        self._apply_pending_sources(dt)

        self.vx = self._diffuse(self.vx, c.viscosity, dt)
        self.vy = self._diffuse(self.vy, c.viscosity, dt)
        self.vz = self._diffuse(self.vz, c.viscosity, dt)
        self._project()

        self.vx = self._advect(self.vx, dt)
        self.vy = self._advect(self.vy, dt)
        self.vz = self._advect(self.vz, dt)
        self._project()

        self.density = self._diffuse(self.density, c.diffusion, dt)
        self.density = self._advect(self.density, dt)
        self.density = [min(1.0, max(0.0, d * c.dissipation)) for d in self.density]
        self.temperature = [min(1.0, max(0.0, t * c.dissipation)) for t in self.temperature]

        self.elapsed_s += dt
        self.step_count += 1
        self._check_divergence()

    def _check_divergence(self) -> None:
        for v in self.density:
            if v != v or v > 1.0001 or v < -0.0001:  # NaN veya sınır dışı
                self.diverged = True
                return
        for v in (self.vx, self.vy, self.vz):
            for comp in v:
                if comp != comp or abs(comp) > 1e4:
                    self.diverged = True
                    return

    def density_at(self, x: int, y: int, z: int) -> float:
        return self.density[self.index(x, y, z)]


# ======================================================================== #
# 6.2.2 — Kademeli devreye alma: yalnızca kamera-odaklı TEK bina
# ======================================================================== #


def is_cfd_eligible(
    *,
    is_camera_focused: bool,
    active_cfd_building_count: int,
    distance_to_camera_m: float,
    focus_distance_threshold_m: float = 25.0,
    max_concurrent_buildings: int = 1,
) -> bool:
    """`physics.building_damage.is_fragmentation_eligible()` (Faz 4.3.3)
    ile AYNI kademeli-devreye-alma deseni: CFD yalnızca kameranın
    odaklandığı, eşik mesafesi içindeki VE eşzamanlı CFD-çalışan bina
    sayısı sınırını aşmayan sahnede çalışabilir (roadmap 6.2.2, "şehir
    genelinde değil, yalnızca aktif/odaklanılan bina"). Diğer her
    durumda çağıran taraf Faz 5.1 sprite fallback'ine düşmelidir."""
    if not is_camera_focused:
        return False
    if distance_to_camera_m > focus_distance_threshold_m:
        return False
    if active_cfd_building_count >= max_concurrent_buildings:
        return False
    return True


# ======================================================================== #
# Render sözleşmesi + zorunlu fallback (roadmap 6.3)
# ======================================================================== #


@dataclass(slots=True, frozen=True)
class SmokeVoxel:
    """Tek bir duman/ısı voksel örneği - render-agnostik: ızgara indeksi +
    dünya konumu + yoğunluk/sıcaklık, gerçek volumetric render/ray-march
    kodu render motorunun tarafındadır (roadmap 6.2.4 GPU notuyla
    tutarlı)."""

    grid_xyz: tuple[int, int, int]
    world_position: tuple[float, float, float]
    density: float
    temperature: float
    honesty_note: str = CFD_EXPERIMENTAL_HONESTY_NOTE


def voxel_frame(
    sim: StableFluidsSimulation,
    *,
    origin_world: tuple[float, float, float] = (0.0, 0.0, 0.0),
    density_threshold: float = 0.03,
) -> list[SmokeVoxel]:
    """`StableFluidsSimulation` durumunu, sahnenin çizeceği voksel
    listesine indirger. Eşik altı yoğunluktaki hücreler atlanır (Faz
    5.1'in `fire_facade_overlay()` ile aynı "boş sahneye gereksiz veri
    basma" disiplini)."""
    c = sim.config
    ox, oy, oz = origin_world
    voxels: list[SmokeVoxel] = []
    for z in range(c.nz):
        for y in range(c.ny):
            for x in range(c.nx):
                i = _idx(x, y, z, c.nx, c.ny)
                d = sim.density[i]
                if d < density_threshold:
                    continue
                voxels.append(SmokeVoxel(
                    grid_xyz=(x, y, z),
                    world_position=(
                        ox + x * c.cell_size_m,
                        oy + y * c.cell_size_m,
                        oz + z * c.cell_size_m,
                    ),
                    density=d,
                    temperature=sim.temperature[i],
                ))
    return voxels


def cfd_or_sprite_frame(
    fire_model: FireSpreadModel,
    building_id: str,
    *,
    sim: Optional[StableFluidsSimulation],
    is_camera_focused: bool,
    active_cfd_building_count: int,
    distance_to_camera_m: float,
    cell_to_world: Callable[[CellId], "object"],
    cell_to_floor: Optional[Callable[[CellId], int]] = None,
) -> dict:
    """Roadmap 6.3'ün "zorunlu fallback" maddesinin doğrudan uygulaması:
    uygunluk koşulları (6.2.2) sağlanıyorsa VE `sim` sağlanmışsa VE
    `sim.diverged` değilse CFD voksel karesi döner; aksi HER durumda
    (uygun değil / sim verilmemiş / sayısal ıraksama) Faz 5.1'in
    `fire_facade_overlay()` sprite fallback'ine sessizce düşer — bu bir
    hata değil, roadmap'in kendi planladığı düşüş noktasıdır.

    Dönüş: `{"mode": "cfd", "voxels": [...]}` ya da
    `{"mode": "sprite_fallback", "sprites": [...]}`.
    """
    # Döngüsel import'tan kaçınmak için burada, kullanım anında import
    # edilir (visualization paketi physics'e bağımlı değil, tersi de
    # olmamalı — modül-seviyesinde import döngüsel bağımlılık yaratırdı).
    from ..visualization.scenario_visual_bridge import fire_facade_overlay

    eligible = is_cfd_eligible(
        is_camera_focused=is_camera_focused,
        active_cfd_building_count=active_cfd_building_count,
        distance_to_camera_m=distance_to_camera_m,
    )
    if eligible and sim is not None and not sim.diverged:
        return {"mode": "cfd", "voxels": voxel_frame(sim)}

    sprites = fire_facade_overlay(
        fire_model, building_id, cell_to_world=cell_to_world, cell_to_floor=cell_to_floor,
    )
    return {"mode": "sprite_fallback", "sprites": sprites}
