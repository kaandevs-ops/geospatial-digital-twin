"""
Crowd Simulation
================

Roadmap Phase 7 - "Crowd Simulation": her insan bir agent.
Özellik: Hız, Boy, Davranış, Hedef, Çarpışma, Bekleme, Panik.

Klasik sosyal-kuvvet modeli (Helbing & Molnár, 1995) - basitleştirilmiş,
bağımlılıksız (numpy yok) 2D implementasyon. Roadmap Phase 6'daki
"Tahliye Modelleme" (evacuation) ve "İnsan Akışı & Yoğunluk" (occupancy /
heatmap) özelliklerinin motoru budur.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from ...core_engine.geometry_engine import Point2D
from ...performance.simulation_lod import SimulationLODManager, SimulationLODMode
from ..indoor_navigation import BuildingNavGraph, FloorNodeId, IndoorNavigationBuilder
from ..pathfinding import AStar, NavGraph

if TYPE_CHECKING:  # pragma: no cover - yalnızca tip kontrolü, döngüsel import yok
    from ..simulation_recorder import SimulationRecorder


class AgentBehavior(str, Enum):
    NORMAL = "normal"
    CAUTIOUS = "cautious"
    HURRIED = "hurried"
    PANIC = "panic"
    # Roadmap V9 / Katman 2.4 madde 7 (Faz IV — davranış kuralları,
    # `behavior_rules.py`'nin ürettiği rota-kararı etiketleri). Bunlar
    # hız-mizacı değil, **rota seçim nedeni** etiketleridir; bu yüzden
    # `effective_desired_speed()`'te NORMAL ile aynı çarpanı kullanırlar
    # (davranış motoru hızı değil rotayı değiştirir - roadmap'in
    # "metodolojik disiplin" notuyla tutarlı, aşırı iddialı bir "panik
    # hızı" atfedilmez).
    AVOID_CROWDED_EXIT = "avoid_crowded_exit"
    AVOID_SMOKE = "avoid_smoke"
    SEEK_ALTERNATIVE_EXIT = "seek_alternative_exit"


class MobilityProfile(str, Enum):
    """Roadmap V9 / Katman 2.1 ("Karma popülasyon / erişilebilirlik
    profilleri"): `Agent`'ın hareket kabiliyeti sınıfı. Yalnızca hız
    çarpanı/erişim kısıtı için bir *gösterge* etikettir - gerçek bireysel
    tıbbi/mobilite verisi taşımaz (sentetik nüfus ilkesi, roadmap 2.1)."""

    WALKING = "walking"  # yürüyen (varsayılan)
    WHEELCHAIR = "wheelchair"  # tekerlekli sandalye - yalnızca rampa/asansör
    VISUALLY_IMPAIRED = "visually_impaired"
    HEARING_IMPAIRED = "hearing_impaired"
    CHILD_OR_ELDERLY = "child_or_elderly"


# Roadmap 2.1: "reaction_time_s (deprem/alarm anında tepki gecikmesi -
# literatürde 'pre-movement time', ISO/PD 7974-6 gibi kaynaklarda tipik
# değerler var)" ve mobilite profiline göre hız çarpanı. Bu sayılar
# **gösterge niteliğindedir** (risk_scoring.py ile aynı disiplin) -
# spesifik bir bireyin gerçek performansını temsil etmez, yalnızca
# literatürdeki genel eğilimin kaba bir yaklaşımıdır.
MOBILITY_PROFILE_SPEED_MULTIPLIER: dict[MobilityProfile, float] = {
    MobilityProfile.WALKING: 1.0,
    MobilityProfile.WHEELCHAIR: 0.75,
    MobilityProfile.VISUALLY_IMPAIRED: 0.70,
    MobilityProfile.HEARING_IMPAIRED: 1.0,  # hız etkilenmez, uyarı algılama gecikir (reaction_time_s'e yansır)
    MobilityProfile.CHILD_OR_ELDERLY: 0.65,
}

# Yalnızca merdivenle değil, asansör/rampa ile dikey erişimi zorunlu olan
# profiller - Katman 2.4 madde 2 çelişki uyarısının doğrudan girdisi.
MOBILITY_PROFILE_REQUIRES_ELEVATOR_OR_RAMP: frozenset = frozenset(
    {
        MobilityProfile.WHEELCHAIR,
    }
)

DEFAULT_MOBILITY_PROFILE_DISTRIBUTION: dict[MobilityProfile, float] = {
    # Roadmap 2.1: "%5 tekerlekli sandalye, %10 yaşlı/çocuk gibi gerçekçi
    # nüfus varsayımları - TÜİK/WorldPop yaş dağılımıyla desteklenir"
    # (gösterge amaçlı kaba dağılım, belirli bir bölgenin gerçek sayımı
    # değildir).
    MobilityProfile.WALKING: 0.75,
    MobilityProfile.WHEELCHAIR: 0.05,
    MobilityProfile.VISUALLY_IMPAIRED: 0.03,
    MobilityProfile.HEARING_IMPAIRED: 0.02,
    MobilityProfile.CHILD_OR_ELDERLY: 0.15,
}


@dataclass(slots=True)
class Agent:
    """Roadmap veri modeli: `Agent(speed, height, behavior, goal)`.

    Roadmap V9 / Katman 2.1 genişlemesi (Faz III): `mobility_profile`,
    `reaction_time_s`, `preferred_route` eklendi. `behavior` alanı hem
    gündelik davranışı hem roadmap metnindeki "evacuation_behavior"
    kavramını karşılar (tek alan - roadmap'in "tekrar yazma yok"
    ilkesiyle tutarlı, yinelenen ayrı bir alan icat edilmedi).
    """

    agent_id: int
    position: Point2D
    goal: Point2D
    height_m: float = 1.7
    radius_m: float = 0.25
    desired_speed: float = 1.34  # ortalama yaya hızı (m/s)
    behavior: AgentBehavior = AgentBehavior.NORMAL
    velocity: Point2D = field(default_factory=lambda: Point2D(0.0, 0.0))
    waiting: bool = False
    path: list[Point2D] = field(default_factory=list)
    path_index: int = 0
    evacuated: bool = False
    # -- Roadmap V9 / Katman 2.1 (Faz III) -------------------------------- #
    mobility_profile: MobilityProfile = MobilityProfile.WALKING
    reaction_time_s: float = 0.0
    preferred_route: list[Point2D] | None = None
    # -- Roadmap V10 / Faz 1.1 ("Ortak Omurga") --------------------------- #
    # `AgentSnapshot`'ın (bkz. `simulation_recorder`) `floor_index`, `z_m`,
    # `room_id` alanlarını doldurabilmesi için üç dikey/iç-mekan alanı.
    # Varsayılanlar geriye dönük uyumluluğu korur: 2D/tek-kat senaryolarda
    # (mevcut testler) `floor_index=0`, `room_id=None`, `z_m=0.0` ile eski
    # davranış birebir aynı kalır - hiçbir mevcut çağrı sitesi bozulmaz.
    floor_index: int = 0
    room_id: int | None = None
    z_m: float = 0.0
    # Roadmap V10 / Faz 1.2 ("SocialForceModel <-> BuildingNavGraph gerçek
    # entegrasyonu"): `path` (yalnızca 2D pozisyonlar) ile birebir aynı
    # uzunlukta, her adımın hangi (floor_index, room_id) düğümüne karşılık
    # geldiğini tutan paralel liste. `None` ise (2D/tek-kat senaryo, eski
    # çağrı siteleri) `floor_index`/`room_id`/`z_m` hiç dokunulmaz -
    # geriye dönük uyumluluk birebir korunur.
    path_nodes: list[FloorNodeId] | None = None
    # -- Roadmap V10 / Faz 2 ("İnsan Figürü ve Hareket Kalitesi") --------- #
    # 2.7 — Grup/aile bağı: aynı `group_id`'ye sahip agent'lar
    # `SocialForceModel`'de zayıf bir birlikte-kalma kuvveti hisseder.
    # `None` ise (varsayılan, geriye dönük uyumlu) hiçbir grup kuvveti
    # uygulanmaz - mevcut tüm senaryolar/testler etkilenmez. Değer olarak
    # `population.synthetic_population.Household.household_id` doğrudan
    # kullanılabilir (roadmap notu: "yeni veri kaynağı gerekmez, sadece
    # bağlama") - burada yeni bir aile/grup modeli icat edilmiyor.
    group_id: str | None = None
    # 2.5 — Mikro-etkileşim görsel geri beslemesi: bir önceki adımda bu
    # agent'a uygulanan toplam ajan-ajan itme kuvvetinin büyüklüğü (N
    # cinsinden değil, model birimlerinde ham skaler). Görsel katman
    # (crowd renderer) bunu eşik değerlerle "sıkışma/yavaşlama" duruş
    # animasyonuna eşleyebilir (bkz. `agent_visuals.crowd_pressure_level`).
    # `SocialForceModel.step()` her adımda günceller; başlangıç değeri
    # 0.0 - hiç adım atılmamış/CULLED agent'lar için "baskı yok" anlamına
    # gelir (dürüst varsayılan, sahte bir sıkışma göstermez).
    crowd_pressure: float = 0.0

    def sync_vertical_state_from_path(self, building_graph: BuildingNavGraph) -> None:
        """Roadmap V10 / Faz 1.2: `path_index`'in şu an işaret ettiği
        `path_nodes` düğümünden `floor_index`/`room_id`/`z_m`'i günceller.
        Yalnızca `assign_building_exit_paths` ile rota atanmış (yani
        `path_nodes` dolu) agent'lar için bir şey yapar - `path_nodes`
        `None` olan agent'lar (düz 2D senaryo) hiç etkilenmez."""
        if not self.path_nodes:
            return
        idx = min(self.path_index, len(self.path_nodes) - 1)
        floor_index, room_id = self.path_nodes[idx]
        self.floor_index = floor_index
        self.room_id = room_id
        self.z_m = IndoorNavigationBuilder.z_of_floor(building_graph, floor_index)

    def current_target(self) -> Point2D:
        if self.path and self.path_index < len(self.path):
            return self.path[self.path_index]
        return self.goal

    def effective_desired_speed(self) -> float:
        multiplier = {
            AgentBehavior.NORMAL: 1.0,
            AgentBehavior.CAUTIOUS: 0.7,
            AgentBehavior.HURRIED: 1.3,
            AgentBehavior.PANIC: 1.8,
            # Faz IV rota-karar etiketleri — hız NORMAL ile aynı (yalnızca
            # rota değişir, bkz. AgentBehavior tanımındaki not).
            AgentBehavior.AVOID_CROWDED_EXIT: 1.0,
            AgentBehavior.AVOID_SMOKE: 1.0,
            AgentBehavior.SEEK_ALTERNATIVE_EXIT: 1.0,
        }[self.behavior]
        profile_multiplier = MOBILITY_PROFILE_SPEED_MULTIPLIER.get(self.mobility_profile, 1.0)
        return self.desired_speed * multiplier * profile_multiplier

    def requires_elevator_or_ramp(self) -> bool:
        """Katman 2.4 madde 2 çelişki uyarısı: bu profil merdivenle inip
        çıkamıyorsa (yalnızca asansör/rampa ile) True döner."""
        return self.mobility_profile in MOBILITY_PROFILE_REQUIRES_ELEVATOR_OR_RAMP


@dataclass(slots=True)
class SocialForceParams:
    """Sosyal-kuvvet modelinin klasik parametreleri."""

    relaxation_time: float = 0.5  # hedefe yönelme tepki süresi (s)
    agent_repulsion_a: float = 2.0  # ajan-ajan itme kuvveti genliği
    agent_repulsion_b: float = 0.3  # ajan-ajan itme etki menzili (m)
    obstacle_repulsion_a: float = 5.0
    obstacle_repulsion_b: float = 0.2
    panic_repulsion_multiplier: float = 1.6  # panik halinde itme artışı
    # -- Roadmap V10 / Faz 2.7 ("Grup/aile bağı davranışı") --------------- #
    # `group_id` paylaşan agent'lar bu mesafeyi aşınca birbirine doğru
    # zayıf bir çekim kuvveti hisseder (Helbing modeline eklenen dördüncü,
    # ihtiyari bir terim - grup verisi olmayan senaryolarda hiç devreye
    # girmez). Sabitler kasıtlı olarak küçük tutuldu: amaç "grup dağılmasın"
    # değil, "grup fazla dağılırsa hafifçe toparlansın" - roadmap'in
    # "en yavaş üyeye göre hız ayarlanır" kabul kriteriyle tutarlı, sert
    # bir manyetik çekim değil.
    group_cohesion_distance_m: float = 3.0  # bu mesafeyi aşınca çekim başlar
    group_cohesion_strength: float = 0.8  # çekim kuvveti genliği


class SocialForceModel:
    """Roadmap: "SocialForceModel (çarpışma/bekleme/panik davranışları için
    klasik sosyal-kuvvet modeli)".

    Her adımda 3 kuvvet toplanır:
      1. driving force  -> hedefe doğru ivmelenme
      2. agent-agent repulsion -> çarpışmayı önleyen itme
      3. obstacle repulsion -> duvar/engelden itme

    F_total belirler; v += F_total * dt; pos += v * dt.
    """

    def __init__(self, params: SocialForceParams | None = None) -> None:
        self.params = params or SocialForceParams()

    # -- tekil kuvvetler --------------------------------------------------- #

    def _driving_force(self, agent: Agent) -> Point2D:
        target = agent.current_target()
        dx, dy = target.x - agent.position.x, target.y - agent.position.y
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            desired_vx, desired_vy = 0.0, 0.0
        else:
            speed = agent.effective_desired_speed()
            desired_vx, desired_vy = dx / dist * speed, dy / dist * speed
        fx = (desired_vx - agent.velocity.x) / self.params.relaxation_time
        fy = (desired_vy - agent.velocity.y) / self.params.relaxation_time
        return Point2D(fx, fy)

    def _agent_repulsion(self, agent: Agent, others: list[Agent]) -> tuple[Point2D, float]:
        """`Point2D` kuvveti ile birlikte, Faz 2.5 için toplam itme
        büyüklüğünü (`crowd_pressure` ham girdisi) de döner - ikinci
        değer yalnızca görsel geri besleme amaçlıdır, hareket denklemini
        etkilemez (geriye dönük uyumlu: `step()` dışında hiçbir çağıran
        bu iç metodu doğrudan kullanmıyor)."""
        fx = fy = 0.0
        pressure = 0.0
        a, b = self.params.agent_repulsion_a, self.params.agent_repulsion_b
        if agent.behavior == AgentBehavior.PANIC:
            a *= self.params.panic_repulsion_multiplier
        for other in others:
            if other.agent_id == agent.agent_id or other.evacuated:
                continue
            dx = agent.position.x - other.position.x
            dy = agent.position.y - other.position.y
            dist = math.hypot(dx, dy)
            min_dist = agent.radius_m + other.radius_m
            if dist < 1e-6:
                dist = 1e-6
            gap = min_dist - dist
            magnitude = a * math.exp(gap / b) if gap > -3 * b else 0.0
            fx += magnitude * dx / dist
            fy += magnitude * dy / dist
            pressure += magnitude
        return Point2D(fx, fy), pressure

    def _group_cohesion_force(self, agent: Agent, others: list[Agent]) -> Point2D:
        """Roadmap V10 / Faz 2.7: aynı `group_id`'ye sahip, çekim
        eşiğinden uzaktaki üyelere doğru zayıf bir toparlanma kuvveti.
        `agent.group_id is None` ise (varsayılan) hiçbir maliyet/etki
        oluşmaz - erken çıkış."""
        if agent.group_id is None:
            return Point2D(0.0, 0.0)
        threshold = self.params.group_cohesion_distance_m
        strength = self.params.group_cohesion_strength
        fx = fy = 0.0
        for other in others:
            if other.agent_id == agent.agent_id or other.evacuated:
                continue
            if other.group_id != agent.group_id:
                continue
            dx = other.position.x - agent.position.x
            dy = other.position.y - agent.position.y
            dist = math.hypot(dx, dy)
            if dist <= threshold or dist < 1e-6:
                continue
            # Eşiği aşan mesafeyle orantılı (doğrusal), sınırsız büyümesin
            # diye eşiğin üstündeki fazla mesafeyle sınırlı basit bir kuvvet.
            magnitude = strength * min(dist - threshold, 5.0)
            fx += magnitude * dx / dist
            fy += magnitude * dy / dist
        return Point2D(fx, fy)

    def _obstacle_repulsion(self, agent: Agent, obstacles: list[Point2D]) -> Point2D:
        fx = fy = 0.0
        a, b = self.params.obstacle_repulsion_a, self.params.obstacle_repulsion_b
        for obstacle in obstacles:
            dx = agent.position.x - obstacle.x
            dy = agent.position.y - obstacle.y
            dist = math.hypot(dx, dy)
            if dist < 1e-6:
                dist = 1e-6
            gap = agent.radius_m - dist
            magnitude = a * math.exp(gap / b) if gap > -3 * b else 0.0
            fx += magnitude * dx / dist
            fy += magnitude * dy / dist
        return Point2D(fx, fy)

    # -- adım -------------------------------------------------------------- #

    def step(
        self,
        agents: list[Agent],
        obstacles: list[Point2D] | None = None,
        dt: float = 0.1,
        arrival_radius: float = 0.3,
    ) -> None:
        obstacles = obstacles or []
        forces: dict[int, Point2D] = {}

        for agent in agents:
            if agent.waiting or agent.evacuated:
                forces[agent.agent_id] = Point2D(0.0, 0.0)
                agent.crowd_pressure = 0.0
                continue
            f_drive = self._driving_force(agent)
            f_agents, pressure = self._agent_repulsion(agent, agents)
            f_obstacles = self._obstacle_repulsion(agent, obstacles)
            f_group = self._group_cohesion_force(agent, agents)
            agent.crowd_pressure = pressure
            forces[agent.agent_id] = Point2D(
                f_drive.x + f_agents.x + f_obstacles.x + f_group.x,
                f_drive.y + f_agents.y + f_obstacles.y + f_group.y,
            )

        for agent in agents:
            if agent.waiting or agent.evacuated:
                continue
            f = forces[agent.agent_id]
            new_vx = agent.velocity.x + f.x * dt
            new_vy = agent.velocity.y + f.y * dt
            speed = math.hypot(new_vx, new_vy)
            max_speed = agent.effective_desired_speed() * 1.3
            if speed > max_speed and speed > 1e-9:
                new_vx, new_vy = new_vx / speed * max_speed, new_vy / speed * max_speed
            agent.velocity = Point2D(new_vx, new_vy)
            agent.position = Point2D(agent.position.x + new_vx * dt, agent.position.y + new_vy * dt)

            target = agent.current_target()
            if agent.position.distance_to(target) <= arrival_radius:
                if agent.path and agent.path_index < len(agent.path) - 1:
                    agent.path_index += 1
                elif agent.position.distance_to(agent.goal) <= arrival_radius:
                    agent.evacuated = True


def _aggregate_kinematic_step(agents: list[Agent], dt: float, arrival_radius: float = 0.3) -> None:
    """Roadmap V10 / Faz 1.4: `SimulationLODMode.AGGREGATE` agent'ları
    için ucuz hareket güncellemesi.

    **Dürüstlük notu (roadmap'in "gösterge niteliğinde ama dürüst"
    ilkesiyle tutarlı):** bu, tam `SocialForceModel`'in basitleştirilmiş
    bir yaklaşımıdır - ajan-ajan/ajan-engel itme kuvvetleri hesaplanmaz
    (O(n²) maliyetin kaçınılan kısmı tam olarak budur), yalnızca hedefe
    doğrudan doğrusal hareket uygulanır. Bu, kamera uzaktayken/görünmez
    kalabalıklar için gerçekçilik kaybı kabul edilebilir bir basitleştirme
    olarak tasarlanmıştır (roadmap: "istatistiksel yoğunluk modeliyle
    temsil et") - kamera yaklaştığında agent tekrar `FULL` moda geçer ve
    tam sosyal-kuvvet davranışı devam eder, bu basitleştirme kalıcı bir
    durum bozulmasına yol açmaz."""
    for agent in agents:
        target = agent.current_target()
        dx, dy = target.x - agent.position.x, target.y - agent.position.y
        dist = math.hypot(dx, dy)
        speed = agent.effective_desired_speed()
        if dist > 1e-6:
            step = min(speed * dt, dist)
            agent.position = Point2D(
                agent.position.x + dx / dist * step,
                agent.position.y + dy / dist * step,
            )
            agent.velocity = Point2D(dx / dist * speed, dy / dist * speed)
        if agent.position.distance_to(target) <= arrival_radius:
            if agent.path and agent.path_index < len(agent.path) - 1:
                agent.path_index += 1
            elif agent.position.distance_to(agent.goal) <= arrival_radius:
                agent.evacuated = True


# ============================================================================ #
# Tahliye modelleme (Roadmap Phase 6: "Tahliye Modelleme")
# ============================================================================ #


@dataclass(slots=True)
class EvacuationResult:
    total_agents: int
    evacuated_count: int
    evacuation_time_s: float
    per_agent_time_s: dict[int, float]
    timed_out: bool
    # Roadmap V9 / Katman 2.4 madde 3 ("Darboğaz tespiti"): O.1'deki zaman
    # serisi kaydı (SimulationRecorder) sayesinde artık yalnızca final
    # durumda değil, koşum boyunca en yoğun an/hücre hesaplanabiliyor.
    # Recorder verilmediyse (geriye-uyumluluk) None kalır.
    bottleneck_location: tuple[int, int] | None = None
    bottleneck_peak_time_s: float | None = None
    bottleneck_peak_count: int | None = None


class EvacuationSimulator:
    """`SocialForceModel` + `NavGraph` (A*) birleştirerek tam tahliye
    simülasyonu koşar: her agent en yakın çıkışa A* ile rota bulur, sonra
    sosyal-kuvvet modeliyle o rotayı takip eder."""

    def __init__(self, model: SocialForceModel | None = None) -> None:
        self.model = model or SocialForceModel()

    @staticmethod
    def assign_nearest_exit_paths(
        agents: list[Agent], graph: NavGraph, exits: list, node_of_agent
    ) -> None:
        """Her agent için en yakın çıkışa A* rotası hesaplar ve
        `agent.path`'i doldurur. `node_of_agent(agent)` agent konumunu graf
        düğüm kimliğine eşleyen bir callable'dır (indoor_navigation ile
        entegrasyon noktası)."""
        for agent in agents:
            start_node = node_of_agent(agent)
            best_path: list[Point2D] = []
            best_cost = math.inf
            for exit_node in exits:
                result = AStar.find_path(graph, start_node, exit_node)
                if result.found and result.cost < best_cost:
                    best_cost = result.cost
                    best_path = [graph.positions[n] for n in result.path]
            if best_path:
                agent.path = best_path
                agent.path_index = 0
                agent.goal = best_path[-1]

    @staticmethod
    def assign_building_exit_paths(
        agents: list[Agent], building_graph: BuildingNavGraph, exits: list[FloorNodeId]
    ) -> None:
        """Roadmap V10 / Faz 1.2 ("SocialForceModel <-> BuildingNavGraph
        gerçek entegrasyonu") + Faz 1.3 ("MobilityProfile gerçek rota
        kısıtı").

        `assign_nearest_exit_paths`'in çok-katlı/indoor sürümü: her
        agent'ın `floor_index`/pozisyonundan en yakın oda düğümüne
        (`IndoorNavigationBuilder.nearest_node`) "spawn" edilir, oradan en
        yakın çıkışa `BuildingNavGraph.graph` üzerinde A* ile gerçek bir
        graf yolu (merdiven/asansör düğümlerinden geçen) hesaplanır.
        Sonuç hem `agent.path` (2D pozisyonlar, `SocialForceModel` bunu
        tüketir) hem `agent.path_nodes` (floor/room kimlikleri,
        `sync_vertical_state_from_path` bunu tüketir) olarak yazılır.

        Faz 1.3 kısıtı: `agent.requires_elevator_or_ramp()` True olan
        agent'lar (ör. tekerlekli sandalye) için rota, merdiven kenarları
        **geçici olarak bloke edilerek** hesaplanır - böylece bu profil
        hiçbir zaman merdivenden geçen bir rota alamaz. Asansörler de
        (deprem senaryosunda `disable_elevators()` ile) kapalıysa, bu
        agent için hiçbir yol bulunamaz (`best_path` boş kalır, `agent.path`
        dokunulmaz) - bu, roadmap'in "tekerlekli sandalye profili gerçekten
        sıkışıyor" kabul kriterinin doğrudan karşılığıdır (sessizce
        merdiven kullanmaya "geri düşmez").
        """
        graph = building_graph.graph
        for agent in agents:
            start_node = IndoorNavigationBuilder.nearest_node(
                building_graph, agent.floor_index, agent.position
            )
            if start_node is None:
                continue

            needs_stairs_blocked = (
                agent.requires_elevator_or_ramp() and not building_graph.stairs_blocked
            )
            if needs_stairs_blocked:
                building_graph.block_stairs()
            try:
                best_path: list[Point2D] = []
                best_nodes: list[FloorNodeId] = []
                best_cost = math.inf
                for exit_node in exits:
                    result = AStar.find_path(graph, start_node, exit_node)
                    if result.found and result.cost < best_cost:
                        best_cost = result.cost
                        best_nodes = list(result.path)
                        best_path = [graph.positions[n] for n in result.path]
            finally:
                if needs_stairs_blocked:
                    building_graph.unblock_stairs()

            if best_path:
                agent.path = best_path
                agent.path_nodes = best_nodes
                agent.path_index = 0
                agent.goal = best_path[-1]
                agent.sync_vertical_state_from_path(building_graph)
            # best_path boşsa (Faz 1.3): agent'ın mevcut path'i (varsa)
            # bilerek dokunulmadan bırakılır - "sıkışma" durumu, çağıranın
            # (ör. realism_audit / UI) `agent.path_nodes` ile önceki hedefe
            # ulaşamadığını tespit edebilmesi için sessizce gizlenmez.

    def run(
        self,
        agents: list[Agent],
        obstacles: list[Point2D] | None = None,
        dt: float = 0.1,
        max_time_s: float = 600.0,
        recorder: SimulationRecorder | None = None,
        on_step: Callable[[float, list[Agent]], None] | None = None,
        seed: int | None = None,
        scenario_id: str | None = None,
        building_graph: BuildingNavGraph | None = None,
        lod_manager: SimulationLODManager | None = None,
        camera_position: tuple[float, float] | None = None,
    ) -> EvacuationResult:
        """`recorder` verilirse (Roadmap V9 / OMURGA / O.1), her adımda
        `recorder.maybe_record()` çağrılarak ara kareler keyframe olarak
        kaydedilir — animasyon (O.2) ve darboğaz zaman serisi (Katman 2.4
        madde 3) bunun üzerine kurulur. `recorder=None` ile eski davranış
        (tek `EvacuationResult`, ara kare kaydı yok) birebir korunur —
        geriye dönük uyumluluk bozulmaz.

        `on_step` (Roadmap V9 / Katman 2.4 madde 7, Faz IV) — verilirse
        her adımdan sonra `on_step(elapsed, agents)` çağrılır. Bu, periyodik
        rota yenilemesi (`behavior_rules.CongestionAwareRouter.refresh`)
        ve Faz V'in periyodik duman-kaçınma rota güncellemesi için genel,
        tekrar kullanılabilir bir bağlantı noktasıdır (roadmap'in "periyodik
        olarak ör. her 5 sn yeniden çağrılmalı" notu) - `EvacuationSimulator`
        kendisi hiçbir belirli davranış kuralına bağımlı değildir (motor
        genel kalır, kural katmanı dışarıdan enjekte edilir).

        `seed`/`scenario_id` (Roadmap V10 / Faz 1.5) — verilirse, `recorder`
        üzerinde `set_run_metadata()` çağrılır: bu koşunun "aynı seed ile
        tekrar oynatıldığında bit-bit aynı sonucu üretir" iddiasının
        doğrulanabilmesi için gereken parametre izini bırakır. `seed`
        buradan simülasyonun kendi rastgelelik kaynağını değiştirmez
        (agent spawn/behavior rastgeleliği zaten ayrı `random.Random(seed)`
        çağrılarıyla üretiliyor, bkz. `spawn_random_agents`) — yalnızca o
        çağrılarda kullanılan seed'i meta veri olarak kaydeder.

        `building_graph` (Roadmap V10 / Faz 1.2) — verilirse, her adımdan
        sonra `agent.sync_vertical_state_from_path(building_graph)`
        çağrılarak `path_nodes`'u dolu olan (yani
        `assign_building_exit_paths` ile rota atanmış) agent'ların
        `floor_index`/`room_id`/`z_m` alanları rota ilerledikçe otomatik
        güncellenir - `AgentSnapshot`'a (Faz 1.1) bu bilginin "gerçek"
        (spawn'da bir kere yazılıp sonra bayatlayan değil) kalması bunun
        sayesindedir.

        `lod_manager`/`camera_position` (Roadmap V10 / Faz 1.4 —
        "LOD ile bağlanma") — ikisi de verilirse, her agent kamera
        mesafesine göre `SimulationLODMode`'a ayrılır: `CULLED` agent'lar
        o adımda hiç güncellenmez (ne fizik ne pozisyon), `AGGREGATE`
        agent'lar ucuz bir düz-çizgi kinematik yaklaşımla (tam sosyal-kuvvet
        yerine) hedefe ilerletilir, yalnızca `FULL` agent'lar tam
        `SocialForceModel.step()` alır. Bu, roadmap'in "kamera uzaktaysa
        tek tek simüle etmek yerine ucuz bir temsille ilerlet" ilkesinin
        somut bağlantı noktasıdır - `SimulationLODManager`/
        `SimulationLODMode` bu oturumdan önce de vardı (O.6), burada
        yalnızca `EvacuationSimulator.run()`'a **bağlandı** (yeni bir LOD
        motoru icat edilmedi). Her ikisi de verilmezse (varsayılan), eski
        davranış (tüm agent'lar her zaman `FULL`) birebir korunur."""
        elapsed = 0.0
        per_agent_time: dict[int, float] = {}
        if recorder is not None:
            recorder.set_run_metadata(
                seed=seed,
                dt=dt,
                max_time_s=max_time_s,
                agent_count=len(agents),
                scenario_id=scenario_id,
            )
            recorder.record_frame(elapsed, agents)
        while elapsed < max_time_s:
            still_moving = [a for a in agents if not a.evacuated]
            if not still_moving:
                break
            # Roadmap V9 / Katman 2.1 (Faz III): "reaction_time_s" -
            # pre-movement time. `SocialForceModel.step` zaten
            # `agent.waiting` True olan agent'lara sıfır kuvvet uyguluyor
            # (mevcut mekanik yeniden kullanıldı, yeni bir "freeze" yolu
            # icat edilmedi). Reaction time'ı dolmuş agent'ların `waiting`
            # durumu bu döngüde yönetilmiyorsa (örn. kapı-kapasite
            # bekletmesi) dokunulmaz - yalnızca henüz reaction_time_s'i
            # dolmamış ve şu an "waiting=False" olan agent'lar tutulur.
            for agent in agents:
                if agent.reaction_time_s > 0.0 and elapsed < agent.reaction_time_s:
                    agent.waiting = True
                elif (
                    agent.waiting
                    and agent.reaction_time_s > 0.0
                    and elapsed >= agent.reaction_time_s
                ):
                    agent.waiting = False

            if lod_manager is not None and camera_position is not None:
                full_agents: list[Agent] = []
                aggregate_agents: list[Agent] = []
                for agent in agents:
                    if agent.waiting or agent.evacuated:
                        continue
                    mode = lod_manager.select_for_position(
                        camera_position, (agent.position.x, agent.position.y)
                    )
                    if mode == SimulationLODMode.CULLED:
                        continue
                    elif mode == SimulationLODMode.AGGREGATE:
                        aggregate_agents.append(agent)
                    else:
                        full_agents.append(agent)
                if full_agents:
                    self.model.step(full_agents, obstacles=obstacles, dt=dt)
                if aggregate_agents:
                    _aggregate_kinematic_step(aggregate_agents, dt=dt)
            else:
                self.model.step(agents, obstacles=obstacles, dt=dt)

            elapsed += dt
            if building_graph is not None:
                for agent in agents:
                    agent.sync_vertical_state_from_path(building_graph)
            for agent in agents:
                if agent.evacuated and agent.agent_id not in per_agent_time:
                    per_agent_time[agent.agent_id] = elapsed
            if recorder is not None:
                recorder.maybe_record(elapsed, agents)
            if on_step is not None:
                on_step(elapsed, agents)

        if recorder is not None:
            # Son durumu her zaman kaydet (interval'a denk gelmese bile) —
            # animasyonun son karesi eksik/atlanmış olmasın.
            recorder.record_frame(elapsed, agents)

        evacuated = [a for a in agents if a.evacuated]
        timed_out = len(evacuated) < len(agents)

        bottleneck_location = None
        bottleneck_peak_time_s = None
        bottleneck_peak_count = None
        if recorder is not None:
            peak = recorder.peak_bottleneck()
            if peak is not None:
                bottleneck_peak_time_s, bottleneck_location, bottleneck_peak_count = peak

        return EvacuationResult(
            total_agents=len(agents),
            evacuated_count=len(evacuated),
            evacuation_time_s=elapsed,
            per_agent_time_s=per_agent_time,
            timed_out=timed_out,
            bottleneck_location=bottleneck_location,
            bottleneck_peak_time_s=bottleneck_peak_time_s,
            bottleneck_peak_count=bottleneck_peak_count,
        )


# ============================================================================ #
# Yoğunluk / heatmap (Roadmap Phase 6: "İnsan Akışı & Yoğunluk")
# ============================================================================ #


class OccupancyHeatmap:
    """Ajan pozisyonlarından basit ızgara-tabanlı yoğunluk (occupancy)
    heatmap'i üretir."""

    @staticmethod
    def compute(agents: list[Agent], cell_size: float = 1.0) -> dict[tuple[int, int], int]:
        grid: dict[tuple[int, int], int] = {}
        for agent in agents:
            cell = (int(agent.position.x // cell_size), int(agent.position.y // cell_size))
            grid[cell] = grid.get(cell, 0) + 1
        return grid

    @staticmethod
    def max_density_cell(heatmap: dict[tuple[int, int], int]) -> tuple[tuple[int, int], int] | None:
        if not heatmap:
            return None
        cell = max(heatmap, key=lambda k: heatmap[k])
        return cell, heatmap[cell]


# ============================================================================ #
# Roadmap V3 - Faz D6: Referans tahliye senaryosuyla doğrulama
# ============================================================================ #


@dataclass(slots=True)
class ReferenceEvacuationScenario:
    """Literatürde bilinen, tek-çıkışlı dikdörtgen bir odadan N kişilik
    tahliyenin **beklenen** süre aralığını, yayınlanmış bir darboğaz
    (bottleneck) akış-hızı formülüyle hesaplar.

    Referans: SFPE Handbook of Fire Protection Engineering (Nelson &
    Mowrer, "Emergency Movement" bölümü) ve Predtechenskii & Milinskii
    (1978) - yaya darboğaz (kapı/çıkış) özgül akış hızı yaklaşık
    **1.3 kişi / (m * s)** olarak kabul edilir (normal yoğunlukta, panik
    olmayan koşullarda; roadmap V3 D6, bu adı konmuş ampirik sabiti
    "bilinen referans" olarak kullanır).

    Beklenen tahliye süresi = N / (specific_flow_rate * exit_width_m)
    - kapı önünde kuyruk oluştuğu (N > kapasiteyi aşan durum) varsayımıyla;
    kuyruk yoksa (çok az kişi / çok geniş kapı) süre, odanın uzunluğu /
    yürüme hızıyla sınırlanır - bu yüzden iki terimin **maksimumu** alınır.
    """

    agent_count: int
    exit_width_m: float
    room_depth_m: float = 10.0  # en uzak agent'ın çıkışa mesafesi (yaklaşık)
    walking_speed_ms: float = 1.34  # ortalama serbest yürüme hızı
    specific_flow_rate: float = 1.3  # kişi / (m * s) - SFPE/Predtechenskii-Milinskii

    def expected_evacuation_time_s(self) -> float:
        bottleneck_time = self.agent_count / (self.specific_flow_rate * self.exit_width_m)
        travel_time = self.room_depth_m / self.walking_speed_ms
        return max(bottleneck_time, travel_time)

    def expected_range_s(self, tolerance: float = 0.20) -> tuple[float, float]:
        """Kabul kriteri toleransı: beklenen süre etrafında ±tolerance
        (roadmap D6/A7 kabul kriteri: **%20 sapma içinde**)."""
        expected = self.expected_evacuation_time_s()
        return expected * (1.0 - tolerance), expected * (1.0 + tolerance)


class EvacuationBenchmark:
    """`EvacuationSimulator` çıktısını `ReferenceEvacuationScenario`'nun
    yayınlanmış darboğaz-akış formülüyle karşılaştıran regresyon aracı."""

    @staticmethod
    def build_single_exit_room(
        agent_count: int,
        room_width_m: float,
        room_depth_m: float,
        exit_width_m: float,
        seed: int | None = 42,
    ) -> tuple[list[Agent], Point2D]:
        """Basit bir dikdörtgen oda: agent'lar odanın arka yarısına
        rastgele dağıtılır, tek çıkış odanın ön-orta noktasındadır."""
        exit_point = Point2D(room_width_m / 2.0, 0.0)
        area_min = Point2D(0.0, room_depth_m * 0.4)
        area_max = Point2D(room_width_m, room_depth_m)
        agents = spawn_random_agents(agent_count, area_min, area_max, exit_point, seed=seed)
        return agents, exit_point

    @staticmethod
    def run_and_compare(
        agent_count: int = 40,
        room_width_m: float = 12.0,
        room_depth_m: float = 10.0,
        exit_width_m: float = 1.2,
        dt: float = 0.1,
        max_time_s: float = 300.0,
        seed: int | None = 42,
        specific_flow_rate: float = 1.3,
        arrival_radius: float = 0.5,
    ) -> dict:
        """`SocialForceModel` ile agent'ları çıkışa doğru hareket ettirir;
        çıkış darboğazının kendisi (kapı/dar geçit fiziği yerine) doğrudan
        **SFPE/Predtechenskii-Milinskii özgül akış hızı formülüyle**
        kapasite-kısıtlı bir "geçit" (gate) olarak modellenir - Roadmap
        V3 D6'nın referans aldığı bilinen ampirik model budur.

        Mekanik: her adımda geçit kapasitesi `specific_flow_rate *
        exit_width_m` (kişi/s) oranında dolar (`_gate_budget`). Kapıya
        (arrival_radius içine) ulaşan ama henüz bütçe kalmayan agent'lar
        `agent.waiting = True` ile fiziksel olarak kapıda bekletilir
        (sosyal-kuvvet modeli onları hâlâ birbirine göre konumlandırır,
        yalnızca "evacuated" sayılmaları geciktirilir) - böylece hem
        sosyal-kuvvet hareketi hem de literatürdeki adı konmuş kapasite
        kısıtı aynı anda uygulanmış olur.

        Dönen sözlük: `simulated_time_s`, `expected_time_s`,
        `deviation_ratio` (|simulated-expected|/expected),
        `within_tolerance` (bool, %20 kabul kriteri).
        """
        agents, exit_point = EvacuationBenchmark.build_single_exit_room(
            agent_count, room_width_m, room_depth_m, exit_width_m, seed=seed
        )
        for agent in agents:
            agent.goal = exit_point

        model = SocialForceModel()
        gate_budget = 0.0
        elapsed = 0.0
        per_agent_time: dict[int, float] = {}
        gate_cleared_ids: set[int] = set()

        while elapsed < max_time_s:
            still_moving = [a for a in agents if not a.evacuated]
            if not still_moving:
                break

            model.step(agents, obstacles=None, dt=dt, arrival_radius=arrival_radius)
            elapsed += dt

            # `SocialForceModel.step` hedefe (çıkışa) ulaşan ajanı kendi
            # başına `evacuated=True` yapar - bu, çıkışın fiziksel geometri
            # dışında bir kapasite kısıtı olmadığı varsayımıyla doğrudur.
            # D6'nın kapasite-gate mekaniğini uygulamak için, henüz gate
            # tarafından "serbest bırakılmamış" böyle ajanları burada
            # yakalayıp kapıda bekletiyoruz (waiting=True → bir sonraki
            # `model.step` çağrısında hareketsiz kalır, ama "evacuated"
            # sayılmaz).
            for agent in agents:
                if agent.evacuated and agent.agent_id not in gate_cleared_ids:
                    agent.evacuated = False
                    agent.waiting = True

            # Bütçe, kimse kapıda beklemiyorken bile birikebilir ama
            # gerçekçi bir üst sınırla (1 kişilik) - aksi halde kapı boşken
            # geçen süre "bankalanıp" ilk varışta gerçekçi olmayan bir
            # patlama (burst) tahliyesine yol açar.
            gate_budget = min(gate_budget + dt * specific_flow_rate * exit_width_m, 1.0)

            # Kapıya ulaşmış (waiting) ama henüz gate tarafından serbest
            # bırakılmamış ajanları, kapasite bütçesi izin verdiği ölçüde
            # serbest bırak (FIFO - en erken varanlar önce).
            queued = [a for a in agents if a.waiting and a.agent_id not in gate_cleared_ids]
            queued.sort(key=lambda a: per_agent_time.get(a.agent_id, math.inf))
            while gate_budget >= 1.0 and queued:
                released = queued.pop(0)
                gate_cleared_ids.add(released.agent_id)
                released.evacuated = True
                released.waiting = False
                gate_budget -= 1.0

            for agent in agents:
                if agent.waiting and agent.agent_id not in per_agent_time:
                    per_agent_time[agent.agent_id] = elapsed
                if agent.evacuated and agent.agent_id not in per_agent_time:
                    per_agent_time[agent.agent_id] = elapsed

        evacuated = [a for a in agents if a.evacuated]
        timed_out = len(evacuated) < len(agents)
        result = EvacuationResult(
            total_agents=len(agents),
            evacuated_count=len(evacuated),
            evacuation_time_s=elapsed,
            per_agent_time_s=per_agent_time,
            timed_out=timed_out,
        )

        scenario = ReferenceEvacuationScenario(
            agent_count=agent_count,
            exit_width_m=exit_width_m,
            room_depth_m=room_depth_m,
            specific_flow_rate=specific_flow_rate,
        )
        expected = scenario.expected_evacuation_time_s()
        simulated = result.evacuation_time_s
        deviation_ratio = abs(simulated - expected) / expected if expected > 0 else math.inf
        low, high = scenario.expected_range_s(tolerance=0.20)

        return {
            "simulated_time_s": simulated,
            "expected_time_s": expected,
            "deviation_ratio": deviation_ratio,
            "within_tolerance": low <= simulated <= high,
            "evacuated_count": result.evacuated_count,
            "total_agents": result.total_agents,
            "timed_out": result.timed_out,
        }


def spawn_random_agents(
    count: int,
    area_min: Point2D,
    area_max: Point2D,
    goal: Point2D,
    seed: int | None = None,
    profile_distribution: dict[MobilityProfile, float] | None = None,
    reaction_time_range_s: tuple[float, float] | None = None,
) -> list[Agent]:
    """Test/demo amaçlı: bir dikdörtgen alan içine rastgele agent'lar dağıtır.

    Roadmap V9 / Katman 2.1 (Faz III) genişlemesi: `profile_distribution`
    verilirse (örn. `DEFAULT_MOBILITY_PROFILE_DISTRIBUTION`), her agent'a
    dağılıma göre deterministik-rastgele bir `mobility_profile` atanır;
    `reaction_time_range_s` verilirse (`min_s, max_s`) her agent'a bu
    aralıkta rastgele bir `reaction_time_s` (pre-movement time) atanır.
    Her iki parametre de varsayılan `None` - verilmezse davranış birebir
    eskisiyle aynıdır (geriye dönük uyumlu)."""
    rng = random.Random(seed)
    agents: list[Agent] = []
    profiles: list[MobilityProfile] = (
        list(profile_distribution.keys()) if profile_distribution else []
    )
    weights: list[float] = list(profile_distribution.values()) if profile_distribution else []
    for i in range(count):
        pos = Point2D(rng.uniform(area_min.x, area_max.x), rng.uniform(area_min.y, area_max.y))
        profile = (
            rng.choices(profiles, weights=weights, k=1)[0] if profiles else MobilityProfile.WALKING
        )
        reaction_time = (
            rng.uniform(reaction_time_range_s[0], reaction_time_range_s[1])
            if reaction_time_range_s is not None
            else 0.0
        )
        agents.append(
            Agent(
                agent_id=i,
                position=pos,
                goal=goal,
                mobility_profile=profile,
                reaction_time_s=reaction_time,
            )
        )
    return agents
