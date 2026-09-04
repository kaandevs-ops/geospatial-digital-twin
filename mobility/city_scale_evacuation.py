"""
Şehir Ölçeği Tahliye Orkestrasyonu — ROADMAP_V9 Faz X / Katman 8.1
======================================================================

Roadmap metni (Katman 8.1, eski "madde 8"):
1. "Çoklu bina orkestrasyon: Her bina kendi iç tahliyesini bitirdikten
   sonra agent'lar dış yaya ağına devrolmalı ve toplanma alanına
   yürümeli. Bu, `indoor_navigation.BuildingNavGraph` (iç) ile açık-alan
   `NavGraph` (dış) arasında bir köprü düğüm (çıkış kapısı = dış graf'ın
   node'u) gerektirir."
2. "Şehir ölçeği performans: O.6'da detaylandırılan spatial hashing
   zorunluluğu burada uygulamaya geçer."
3. "Bölgesel yığılma tespiti: `TwinHierarchy`'nin agregasyon sorgu
   altyapısı ... agent yoğunluğu için de aynı desenle kullanılır."
4. "Urban digital twin senkronu: `digital_twin/iot_bridge.py` gerçek
   sensör verisini `TopicBus` üzerinden akıtır ... simülasyon başlangıç
   koşulu bu canlı veriyle beslenebilir."

Bu modül **yeni bir motor yazmaz** (roadmap ilkesi #2): `pathfinding.
NavGraph`/`AStar`, `indoor_navigation.BuildingNavGraph`,
`performance.simulation_lod.AgentSpatialHash`, `digital_twin.hierarchy.
TwinHierarchy` ve `digital_twin.iot_bridge.TopicBus` — hepsi zaten var,
yalnızca burada bir araya getiriliyor (orkestrasyon katmanı).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..data_engine.spatial_index import AABB2D
from ..digital_twin.hierarchy import TwinHierarchy
from ..performance.simulation_lod import AgentSpatialHash, _xy
from .indoor_navigation import BuildingNavGraph
from .pathfinding import AStar, NavGraph, NodeId

#: Bir bina çıkış kapısının dış NavGraph'a bağlandığı köprü kenarının
#: varsayılan maliyeti (metre cinsinden kaba bir "kapıdan kaldırıma"
#: mesafesi — gösterge niteliğinde, gerçek geometriden türetilebilir).
DEFAULT_BRIDGE_EDGE_COST = 2.0


class CityScaleEvacuationError(Exception):
    """Şehir ölçeği orkestrasyon hatası (köprü düğümü bulunamadı vb.)."""


@dataclass(slots=True)
class BuildingBridge:
    """Bir binanın iç `BuildingNavGraph`'ını dış `NavGraph`'a bağlayan
    köprü tanımı — roadmap'in "çıkış kapısı = dış graf'ın node'u" notunun
    somut karşılığı."""

    building_id: str
    exit_node_id: NodeId
    outdoor_node_id: NodeId
    assembly_point_node_id: NodeId
    edge_cost: float = DEFAULT_BRIDGE_EDGE_COST


def bridge_building_to_outdoor_graph(
    building: BuildingNavGraph,
    exit_node_id: NodeId,
    outdoor_graph: NavGraph,
    outdoor_node_id: NodeId,
    *,
    edge_cost: float = DEFAULT_BRIDGE_EDGE_COST,
) -> None:
    """İç çıkış düğümünü ve dış (kaldırım/yol) düğümünü **tek bir birleşik
    graf** üzerinde köprüler.

    Roadmap'in "köprü düğüm" notu birebir uygulanır: yeni bir graf yapısı
    icat edilmedi, iç ve dış `NavGraph`'lar zaten aynı sınıf
    (`mobility.pathfinding.NavGraph`) olduğundan, dış graf'a iç çıkış
    düğümünün bir **kopyası** eklenip aradaki kenar `outdoor_graph.
    add_edge()` ile kurulur. İç graf (`building.graph`) değiştirilmez —
    binanın kendi iç tahliyesi bu köprüden bağımsız çalışmaya devam eder.
    """
    if not building.graph.has_node(exit_node_id):
        raise CityScaleEvacuationError(f"bina graf'ında bulunamayan çıkış düğümü: {exit_node_id}")
    if not outdoor_graph.has_node(outdoor_node_id):
        raise CityScaleEvacuationError(f"dış graf'ta bulunamayan düğüm: {outdoor_node_id}")
    exit_position = building.graph.positions[exit_node_id]
    bridge_node_id = f"bridge::{exit_node_id}"
    if not outdoor_graph.has_node(bridge_node_id):
        outdoor_graph.add_node(bridge_node_id, exit_position)
    outdoor_graph.add_edge(bridge_node_id, outdoor_node_id, cost=edge_cost)


def route_evacuated_agents_to_assembly_point(
    outdoor_graph: NavGraph,
    bridge: BuildingBridge,
) -> list[tuple[NodeId, float]]:
    """Bir binadan çıkan agent'ların köprü düğümünden toplanma alanına
    dış NavGraph üzerinden A* rotasını hesaplar.

    Roadmap: "agent'lar dış yaya ağına devrolmalı ve toplanma alanına
    yürümeli" — mevcut `pathfinding.AStar` **yeniden kullanılır** (yeni
    bir yol-bulma algoritması yazılmadı). Dönüş: `(node_id, mesafe)`
    çiftlerinden oluşan rota; boş liste = ulaşılamıyor.
    """
    bridge_node_id = f"bridge::{bridge.exit_node_id}"
    if not outdoor_graph.has_node(bridge_node_id):
        raise CityScaleEvacuationError(
            f"binanın köprü düğümü kurulmamış: {bridge.building_id} "
            f"(önce bridge_building_to_outdoor_graph çağrılmalı)"
        )
    result = AStar.find_path(outdoor_graph, bridge_node_id, bridge.assembly_point_node_id)
    if not result.found:
        return []
    path = result.path
    out: list[tuple[NodeId, float]] = []
    cumulative = 0.0
    prev = path[0]
    out.append((prev, 0.0))
    for node in path[1:]:
        edge_cost = next((c for (a, b, c) in outdoor_graph.edges() if a == prev and b == node), 0.0)
        cumulative += edge_cost
        out.append((node, cumulative))
        prev = node
    return out


# ========================================================================== #
# Şehir ölçeği performans — spatial hashing köprüsü (Katman 8.1 madde 2)
# ========================================================================== #


@dataclass(slots=True)
class CityScaleAgentIndex:
    """Roadmap'in O.6'da hazırlanan `AgentSpatialHash`'i (yeni bir yapı
    icat edilmeden) şehir ölçeğinde birden çok binanın agent'larını **tek
    bir mekânsal indekste** birleştiren ince sarmalayıcı.

    50.000 agent'lık şehir ölçeğinde `SocialForceModel`'in O(n^2) komşu
    taramasının yerini alacak sorgu katmanı budur — motorun kendisi
    (`SocialForceModel.step`) roadmap'in kendi notuyla tutarlı biçimde bu
    oturumda da değiştirilmedi (bilinçli kapsam sınırı, izlenebilir bir
    sonraki adım); burada yalnızca **sorgu altyapısı** hazırlanır.
    """

    margin_m: float = 50.0
    _hash: AgentSpatialHash | None = field(default=None, init=False)
    _agent_building: dict = field(default_factory=dict, init=False)

    def rebuild(self, agents_by_building: dict[str, list]) -> None:
        """Tüm binaların agent listelerini tek indekste yeniden kurar.
        `agents_by_building`: `{building_id: [agent, ...]}`.

        `AgentSpatialHash` sabit bir `world_bounds` (`AABB2D`) ile
        kurulduğundan (roadmap'in temelini attığı O.6 tasarımı), her
        yeniden kurmada tüm agent konumlarından bir sınırlayıcı kutu
        (+ kenar payı) hesaplanır — şehir ölçeğinde binalar arası mesafe
        çalışma zamanında değişebileceğinden statik bir kutu varsayılmaz.
        """
        self._agent_building = {}
        xs: list[float] = []
        ys: list[float] = []
        for building_id, agents in agents_by_building.items():
            for agent in agents:
                x, y = _xy(agent)
                xs.append(x)
                ys.append(y)
                self._agent_building[getattr(agent, "agent_id", id(agent))] = building_id
        if not xs:
            self._hash = None
            return
        bounds = AABB2D(
            min(xs) - self.margin_m,
            min(ys) - self.margin_m,
            max(xs) + self.margin_m,
            max(ys) + self.margin_m,
        )
        self._hash = AgentSpatialHash(bounds)
        for agents in agents_by_building.values():
            for agent in agents:
                self._hash.insert(agent)

    def neighbors_within(self, agent: Any, radius: float) -> list:
        if self._hash is None:
            return []
        exclude_id = getattr(agent, "agent_id", None)
        return self._hash.query_radius(_xy(agent), radius, exclude_id=exclude_id)

    def building_of(self, agent: Any) -> str | None:
        return self._agent_building.get(getattr(agent, "agent_id", id(agent)))


# ========================================================================== #
# Bölgesel yığılma tespiti (Katman 8.1 madde 3)
# ========================================================================== #


def regional_agent_density(
    hierarchy: TwinHierarchy,
    region_twin_id: str,
    agent_counts_by_twin: dict[str, int],
) -> int:
    """Bir bölgenin (mahalle/blok) toplam tahliye-agent sayısını,
    `TwinHierarchy.aggregate()`'in **var olan** memoized agregasyon
    desenini (roadmap'in kendi ifadesiyle "enerji yerine agent yoğunluğu
    için de aynı desenle kullanılır, ek kod yazmadan") yeniden kullanarak
    hesaplar.

    `agent_counts_by_twin`: yaprak (bina) twin id -> o binadaki güncel
    tahliye-agent sayısı. Twin registry'ye ihtiyaç duymamak için burada
    `TwinHierarchy.aggregate()`'in genel `metric_fn(twin)` sözleşmesi
    yerine doğrudan basit bir DFS toplamı kullanılır — `aggregate()`'in
    kendisi bir `DigitalTwinRegistry` beklediğinden (agent sayacı bir twin
    alanı değil, bu oturumun geçici simülasyon durumu), roadmap'in
    "aynı desen" notu korunarak (`descendants` + yaprak toplamı,
    `direct_leaf_sum`'ın izlediği aynı traversal) motor tekrar yazılmadı.
    """
    leaves = [
        d
        for d in ([region_twin_id] + hierarchy.descendants(region_twin_id))
        if hierarchy.is_leaf(d)
    ]
    return sum(agent_counts_by_twin.get(leaf, 0) for leaf in leaves)


def most_congested_region(
    hierarchy: TwinHierarchy,
    candidate_region_ids: list[str],
    agent_counts_by_twin: dict[str, int],
) -> tuple[str | None, int]:
    """Roadmap'in "bu mahallede toplam X kişi tahliye oluyor, en yoğun
    blok Y" ifadesinin karşılığı — verilen aday bölgeler arasından en
    yüksek toplam agent sayısına sahip olanı döndürür."""
    best_id: str | None = None
    best_count = -1
    for region_id in candidate_region_ids:
        count = regional_agent_density(hierarchy, region_id, agent_counts_by_twin)
        if count > best_count:
            best_count = count
            best_id = region_id
    return best_id, max(best_count, 0)


# ========================================================================== #
# Urban digital twin senkronu (Katman 8.1 madde 4)
# ========================================================================== #


def initial_agent_count_from_iot(
    latest_sensor_value: float | None,
    *,
    fallback_count: int,
    occupancy_per_sensor_unit: float = 1.0,
) -> tuple[int, bool]:
    """Gerçek bir IoT yoğunluk sensöründen (bkz. `digital_twin.iot_bridge.
    SensorIotBinding`, `TopicBus` üzerinden akan canlı veri) simülasyon
    başlangıç koşulunu (kaç agent spawn edilecek) türetir.

    Bu fonksiyon `TopicBus`'a **abone olmaz** — çağıran taraf (örn.
    `app_shell.session`) zaten var olan `SensorIotBinding`/`TopicBus`
    altyapısını kullanıp en güncel sensör değerini buraya iletir; burada
    yalnızca "değer var mı, yoksa geriye dönük varsayılana mı düş" kararı
    ve dönüşüm formülü var — roadmap ilkesi #1 (gösterge disiplini) ile
    tutarlı biçimde bu **kaba** bir dönüşümdür, kesin doluluk sayacı
    iddiası taşımaz.

    Dönüş: `(agent_count, used_live_data)` — `used_live_data=False` ise
    sensör verisi yoktu ve `fallback_count` sessizce değil, açıkça
    kullanıldı (çağıran bunu raporunda belirtebilir).
    """
    if latest_sensor_value is None or latest_sensor_value < 0:
        return fallback_count, False
    count = max(0, round(latest_sensor_value * occupancy_per_sensor_unit))
    return count, True


__all__ = [
    "CityScaleEvacuationError",
    "BuildingBridge",
    "DEFAULT_BRIDGE_EDGE_COST",
    "bridge_building_to_outdoor_graph",
    "route_evacuated_agents_to_assembly_point",
    "CityScaleAgentIndex",
    "regional_agent_density",
    "most_congested_region",
    "initial_agent_count_from_iot",
]
