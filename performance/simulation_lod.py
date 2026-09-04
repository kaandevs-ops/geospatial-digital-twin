"""
performance.simulation_lod — Faz I / OMURGA / O.6 "Performans Omurgası +
Simülasyon LOD (şehir ölçeği için zorunlu)"
================================================================================

ROADMAP_V9.md O.6 metni birebir:

    "Yeni: `performance/simulation_lod.py` — sadece geometri değil,
    davranış için de seviye-of-detail: kamera uzaktaysa bir mahalledeki
    500 agent'ı tek tek simüle etmek yerine istatistiksel yoğunluk
    modeliyle temsil et (aggregate), kamera yakınlaştıkça gerçek agent
    simülasyonuna geç ('LOD for AI')."

    "Şehir ölçesi performans notu: 1000 bina × ortalama 50 agent = 50.000
    agent aynı anda. `SocialForceModel` O(n²) çarpışma kontrolü bu ölçekte
    çöker — spatial hashing / grid tabanlı komşu arama şart,
    `data_engine/spatial_index.py` bu iş için tekrar yazılmadan yeniden
    kullanılır."

Bu modül iki bağımsız ama tamamlayıcı parça sağlar:

1. **`SimulationLODManager`** — `performance.culling.LODManager` ile aynı
   mesafe-eşikli desen (roadmap ilkesi #2: tekrar yazma yok, aynı desen
   yeniden kullanıldı) ama mesh yerine "davranış modu" seçer:
   `FULL` (bireysel `SocialForceModel` adımı) / `AGGREGATE` (istatistiksel
   yoğunluk temsili) / `CULLED` (hiç güncellenmez — kamera görüş alanı
   dışında ve çok uzak).
2. **`AgentSpatialHash`** — `data_engine.spatial_index.QuadTree` üzerine
   ince bir cephe: `SocialForceModel`'in O(n²) komşu taramasını grid
   hücresi bazlı O(n·k) sorguya indirger (yalnızca komşu hücrelerdeki
   agent'lar karşılaştırılır). `QuadTree` **tekrar yazılmadı**, yalnızca
   agent bounding-box'larıyla besleniyor.

Bu modül bilinçli olarak `mobility.crowd_simulation`'ı import etmez —
roadmap ilkesi #5 (olay-güdümlü/gevşek bağlı mimari) gereği `Agent`
nesnesine sıkı bağımlı olmak yerine gevşek (duck-typed) bir protokol
kullanır: `position.x`, `position.y`, `agent_id` alanlarına sahip herhangi
bir nesne kabul edilir (tıpkı `render_engine.scene_bridge.push_agent_frame`
tasarımının O.2'de yaptığı gibi).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Protocol, Sequence

from ..data_engine.spatial_index import AABB2D, QuadTree

Vec2 = tuple[float, float]


# ========================================================================== #
# 1) Davranış LOD (agent simülasyon detay seviyesi)
# ========================================================================== #

class SimulationLODMode(str, Enum):
    FULL = "full"            # bireysel SocialForceModel adımı
    AGGREGATE = "aggregate"  # istatistiksel yoğunluk temsili
    CULLED = "culled"        # hiç güncellenmez


@dataclass(frozen=True)
class SimulationLODLevel:
    """`performance.culling.LODLevel` ile aynı desen: artan mesafe eşiği +
    o eşikte seçilecek mod."""

    max_distance: float
    mode: SimulationLODMode


#: Roadmap örneğiyle tutarlı, makul varsayılan üç kademe. Proje çağıranı
#: kendi eşiklerini (`SimulationLODManager(levels=...)`) verebilir; bu
#: yalnızca varsayılandır, sabit/zorunlu değildir.
DEFAULT_SIMULATION_LOD_LEVELS: tuple[SimulationLODLevel, ...] = (
    SimulationLODLevel(max_distance=150.0, mode=SimulationLODMode.FULL),
    SimulationLODLevel(max_distance=600.0, mode=SimulationLODMode.AGGREGATE),
    SimulationLODLevel(max_distance=math.inf, mode=SimulationLODMode.CULLED),
)


class SimulationLODManager:
    """Mesafeye göre davranış LOD modu seçimi (`LODManager`'ın "AI için"
    genellemesi — `performance.culling.LODManager` **değiştirilmedi**,
    burada bağımsız ama aynı desende yeniden uygulandı çünkü dönüş tipi
    farklıdır: mesh anahtarı değil, `SimulationLODMode`).
    """

    def __init__(self, levels: Sequence[SimulationLODLevel] = DEFAULT_SIMULATION_LOD_LEVELS) -> None:
        self.levels = sorted(levels, key=lambda lv: lv.max_distance)
        if not self.levels:
            raise ValueError("SimulationLODManager: en az bir LOD seviyesi gerekli")

    def select(self, distance: float) -> SimulationLODMode:
        for level in self.levels:
            if distance <= level.max_distance:
                return level.mode
        return self.levels[-1].mode

    def select_for_position(self, camera_position: Vec2, object_position: Vec2) -> SimulationLODMode:
        dx = object_position[0] - camera_position[0]
        dy = object_position[1] - camera_position[1]
        distance = math.sqrt(dx * dx + dy * dy)
        return self.select(distance)


# ========================================================================== #
# 2) İstatistiksel yoğunluk temsili (AGGREGATE modunda kullanılan çıktı)
# ========================================================================== #

@dataclass
class AggregateAgentCluster:
    """Bir hücredeki (veya mahalledeki) agent grubunun bireysel simülasyon
    yerine kullanılan özet temsili. `mobility.crowd_simulation.
    OccupancyHeatmap` ile aynı "yoğunluk hücresi" fikrini paylaşır — burada
    tekrar hesaplanmaz, agent listesinden doğrudan türetilir.
    """

    cell_key: tuple[int, int]
    agent_count: int
    centroid: Vec2
    mean_speed: float
    waiting_fraction: float   # [0, 1] — bekleyen/panikte olan agent oranı


class _PositionedAgent(Protocol):
    agent_id: int

    @property
    def position(self) -> "object": ...  # x, y niteliği olan herhangi bir nesne


def _xy(agent) -> Vec2:
    pos = agent.position
    return (float(pos.x), float(pos.y))


def build_aggregate_clusters(
    agents: Iterable,
    *,
    cell_size_m: float = 25.0,
) -> list[AggregateAgentCluster]:
    """Agent'ları grid hücrelerine göre gruplayıp her hücre için özet
    istatistik üretir (roadmap: "istatistiksel yoğunluk modeliyle temsil
    et"). `waiting_fraction`, agent'ın `waiting` niteliği varsa (bkz.
    `mobility.crowd_simulation.Agent`) kullanılır; yoksa hız sıfıra
    yakınsa "bekliyor" kabul edilir (gevşek/duck-typed uyum).
    """
    buckets: dict[tuple[int, int], list] = {}
    for agent in agents:
        x, y = _xy(agent)
        cell = (math.floor(x / cell_size_m), math.floor(y / cell_size_m))
        buckets.setdefault(cell, []).append(agent)

    clusters: list[AggregateAgentCluster] = []
    for cell, members in buckets.items():
        n = len(members)
        sum_x = sum_y = sum_speed = 0.0
        waiting_count = 0
        for agent in members:
            x, y = _xy(agent)
            sum_x += x
            sum_y += y
            vel = getattr(agent, "velocity", None)
            speed = math.hypot(vel.x, vel.y) if vel is not None else 0.0
            sum_speed += speed
            is_waiting = getattr(agent, "waiting", None)
            if is_waiting is None:
                is_waiting = speed < 1e-3
            if is_waiting:
                waiting_count += 1

        clusters.append(AggregateAgentCluster(
            cell_key=cell,
            agent_count=n,
            centroid=(sum_x / n, sum_y / n),
            mean_speed=sum_speed / n,
            waiting_fraction=waiting_count / n,
        ))
    return clusters


# ========================================================================== #
# 3) Spatial hashing — SocialForceModel O(n²) komşu taraması için
# ========================================================================== #

class AgentSpatialHash:
    """`data_engine.spatial_index.QuadTree` üzerine ince cephe: agent'ları
    konumlarına göre indeksler, yalnızca `radius` içindeki komşuları
    döndürür (roadmap: "spatial hashing / grid tabanlı komşu arama şart").

    Kullanım (roadmap notu — `SocialForceModel.step()` içine entegrasyon,
    mevcut O(n²) döngü bu sınıfla değiştirilebilir, motor bu oturumda
    **değiştirilmedi** — köprü hazır, çağıran nokta ayrı bir entegrasyon
    adımıdır):

        hash_ = AgentSpatialHash(world_bounds, cell_size_m=5.0)
        for a in agents:
            hash_.insert(a)
        for a in agents:
            neighbors = hash_.query_radius(_xy(a), radius=3.0, exclude_id=a.agent_id)
    """

    def __init__(self, world_bounds: AABB2D, capacity: int = 8, max_depth: int = 10) -> None:
        self._tree: QuadTree = QuadTree(world_bounds, capacity=capacity, max_depth=max_depth)
        self._by_id: dict[int, object] = {}

    def insert(self, agent) -> None:
        x, y = _xy(agent)
        # Nokta agent'lar için sıfır-alanlı (bulk-safe) küçük bir AABB
        # kullanılır — `QuadTree.insert` bounding-box beklediği için.
        point_box = AABB2D(x, y, x, y)
        self._tree.insert(agent, point_box)
        self._by_id[agent.agent_id] = agent

    def query_radius(self, center: Vec2, radius: float, *, exclude_id: int | None = None) -> list:
        cx, cy = center
        region = AABB2D(cx - radius, cy - radius, cx + radius, cy + radius)
        candidates = self._tree.query(region)
        result = []
        r2 = radius * radius
        for candidate in candidates:
            if exclude_id is not None and getattr(candidate, "agent_id", None) == exclude_id:
                continue
            x, y = _xy(candidate)
            dx, dy = x - cx, y - cy
            if dx * dx + dy * dy <= r2:
                result.append(candidate)
        return result

    def count(self) -> int:
        return self._tree.count()
