"""
Indoor Navigation
==================

Roadmap Phase 7 - "Indoor Navigation": katlar arası (merdiven/asansör)
graf birleştirme.

`Room` (Phase 3 `building_reconstruction.room_generator`) düğümlerinden bir
kat-içi `NavGraph` kurar (komşuluk listesi -> kenar), sonra katları
`Stair`/`ElevatorCore` (Phase 3 `building_elements`) bağlantı noktalarıyla
tek bir çok-katlı `NavGraph`'a birleştirir. Düğüm kimlikleri
`(floor_index, room_id)` biçimindedir - böylece aynı `room_id` farklı
katlarda çakışmaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...building_reconstruction.building_elements import ElevatorCore, Stair
from ...building_reconstruction.room_generator import Room
from ...core_engine.geometry_engine import Point2D
from ..pathfinding import NavGraph

FloorNodeId = tuple[int, int]  # (floor_index, room_id)

# Merdiven/asansör kullanım maliyet çarpanları (düz yürüyüşe göre) -
# roadmap'in "tahliye süresi" hesaplarında kullanılabilecek, gerçekçi
# varsayılan katsayılar.
STAIR_COST_MULTIPLIER = 3.0
ELEVATOR_COST_MULTIPLIER = 1.5
FLOOR_HEIGHT_DEFAULT = 3.0


@dataclass(slots=True)
class Floor:
    """Tek bir katın oda grafiği + o kata ait dikey erişim noktaları."""

    floor_index: int
    rooms: list[Room]
    stairs: list[Stair] = field(default_factory=list)
    elevators: list[ElevatorCore] = field(default_factory=list)

    def room_by_id(self, room_id: int) -> Room | None:
        for r in self.rooms:
            if r.room_id == room_id:
                return r
        return None

    def to_nav_graph(self) -> NavGraph:
        """Kat içi oda komşuluk grafiğinden bir `NavGraph` üretir. Düğüm
        pozisyonu, odanın polygon centroid'idir."""
        graph = NavGraph()
        for room in self.rooms:
            centroid = _centroid(room.polygon.points)
            graph.add_node(room.room_id, centroid)
        for room in self.rooms:
            for nb_id in room.neighbors:
                if graph.has_node(nb_id):
                    graph.add_edge(room.room_id, nb_id)
        return graph


def _centroid(points: list[Point2D]) -> Point2D:
    if not points:
        return Point2D(0.0, 0.0)
    n = len(points)
    return Point2D(sum(p.x for p in points) / n, sum(p.y for p in points) / n)


def _nearest_room(rooms: list[Room], position: Point2D) -> Room | None:
    if not rooms:
        return None
    return min(rooms, key=lambda r: _centroid(r.polygon.points).distance_to(position))


@dataclass(slots=True)
class BuildingNavGraph:
    """Çok katlı bina için birleşik navigasyon grafiği + kaynağa erişim
    metadata'sı (hangi kat/hangi eleman üzerinden bağlandığı)."""

    graph: NavGraph
    floor_count: int
    floor_height: float
    # Katman 2.4 madde 2 -- "Asansor kullanma kisiti": dikey gecis
    # kenarlarindan hangilerinin asansor (merdiven degil) uzerinden
    # kuruldugu. `IndoorNavigationBuilder.build()` tarafindan doldurulur;
    # deprem senaryosunda bu kenarlar `NavGraph.set_blocked()` ile devre
    # disi birakilir (graf'tan silinmez -- roadmap'in "geri acilabilir"
    # ilkesiyle tutarli, `enable_elevators()` ile geri alinabilir).
    elevator_edges: list = field(default_factory=list)
    elevators_disabled: bool = False
    # Roadmap V10 / Faz 1.3 ("MobilityProfile gerçek rota kısıtı"): dikey
    # geçiş kenarlarının merdiven olanları (asansör olmayanlar) - tekerlekli
    # sandalye gibi `requires_elevator_or_ramp()` profillerinin bu
    # kenarları hiç KULLANAMAMASI (yalnızca deprem senaryosunda asansörün
    # kapanması değil, normal zamanda da bu profillerin merdiven
    # kullanamaması) için ayrı takip edilir - `elevator_edges` ile aynı
    # desende (roadmap ilkesi #2: tekrar yazma yok).
    stair_edges: list = field(default_factory=list)
    stairs_blocked: bool = False

    def disable_elevators(self) -> None:
        """Katman 2.4 madde 2: deprem senaryosunda asansor kenarlarini
        graf'ta bloke eder (A*/Dijkstra bu kenarlari atlar). Merdiven
        kenarlari etkilenmez."""
        for a, b in self.elevator_edges:
            self.graph.set_blocked(a, b, True)
            self.graph.set_blocked(b, a, True)
        self.elevators_disabled = True

    def enable_elevators(self) -> None:
        """`disable_elevators()`'i geri alir (ayni graf'i iki modda da
        kullanmak -- ornegin once/sonra karsilastirmasi -- icin)."""
        for a, b in self.elevator_edges:
            self.graph.set_blocked(a, b, False)
            self.graph.set_blocked(b, a, False)
        self.elevators_disabled = False

    def block_stairs(self) -> None:
        """Roadmap V10 / Faz 1.3: merdiven kenarlarını geçici olarak
        kapatır - `requires_elevator_or_ramp()` profili için rota
        hesaplarken kullanılır (bu profil merdivenle inip çıkamaz, bu
        yüzden A*'ın merdiven kenarlarını hiç görmemesi gerekir).
        `elevator_edges` ile aynı `NavGraph.set_blocked()` mekanizması
        yeniden kullanılır - yeni bir bloke türü icat edilmedi."""
        for a, b in self.stair_edges:
            self.graph.set_blocked(a, b, True)
            self.graph.set_blocked(b, a, True)
        self.stairs_blocked = True

    def unblock_stairs(self) -> None:
        for a, b in self.stair_edges:
            self.graph.set_blocked(a, b, False)
            self.graph.set_blocked(b, a, False)
        self.stairs_blocked = False

    def unreachable_rooms_without_elevator(self, ground_floor_index: int = 0) -> list:
        """Erisilebilirlik uyari motoru (Katman 9.6) icin temel veri:
        asansorler devre disiyken zemin kata (varsayilan
        `ground_floor_index`) merdiven/rota uzerinden hic ulasamayan oda
        dugumlerini doner. Bu listenin bos-olmamasi "bu binada engelli
        tahliye plani eksik" uyarisinin somut kanitidir (bkz.
        ROADMAP_V9.md Katman 2.4 madde 2 celiski uyarisi)."""
        was_disabled = self.elevators_disabled
        if not was_disabled:
            self.disable_elevators()
        try:
            ground_nodes = [n for n in self.graph.positions if n[0] == ground_floor_index]
            if not ground_nodes:
                return []
            reachable = {ground_nodes[0]}
            frontier = [ground_nodes[0]]
            while frontier:
                node = frontier.pop()
                for nb, _cost in self.graph.neighbors(node):
                    if nb not in reachable:
                        reachable.add(nb)
                        frontier.append(nb)
            return [n for n in self.graph.positions if n not in reachable]
        finally:
            if not was_disabled:
                self.enable_elevators()


@dataclass(slots=True)
class HazardScenarioRules:
    """ROADMAP_V9.md Katman 2.4 madde 2'de tanimlanan kisit kumesi --
    `IndoorNavigationBuilder.build()`'a verilir, mevcut `indoor_navigation`
    kodu bozulmadan senaryo-bazli davranis degisikligi saglar."""

    disable_elevators: bool = False


class IndoorNavigationBuilder:
    """Roadmap: "Katlar arası / Merdiven / Asansör" birleştirme mantığı."""

    @staticmethod
    def build(floors: list[Floor], floor_height: float = FLOOR_HEIGHT_DEFAULT,
              hazard_rules: "HazardScenarioRules | None" = None) -> BuildingNavGraph:
        floors_sorted = sorted(floors, key=lambda f: f.floor_index)
        combined = NavGraph()

        # 1) her katın oda grafiğini (floor_index, room_id) kimlikleriyle içeri al
        per_floor_graph: dict[int, NavGraph] = {}
        elevator_edges: list = []
        stair_edges: list = []
        for floor in floors_sorted:
            fg = floor.to_nav_graph()
            per_floor_graph[floor.floor_index] = fg
            for room_id, pos in fg.positions.items():
                combined.add_node((floor.floor_index, room_id), pos)
            for room in floor.rooms:
                for nb_id in room.neighbors:
                    if fg.has_node(nb_id):
                        combined.add_edge((floor.floor_index, room.room_id),
                                           (floor.floor_index, nb_id))

        # 2) merdiven/asansör bağlantı noktalarını ardışık katlar arasında
        #    en yakın odaya bağla (dikey geçiş kenarı)
        for i in range(len(floors_sorted) - 1):
            lower, upper = floors_sorted[i], floors_sorted[i + 1]

            for stair in lower.stairs:
                room_lower = _nearest_room(lower.rooms, stair.position)
                room_upper = _nearest_room(upper.rooms, stair.position)
                if room_lower is None or room_upper is None:
                    continue
                vertical_cost = floor_height * STAIR_COST_MULTIPLIER
                stair_edge = ((lower.floor_index, room_lower.room_id),
                              (upper.floor_index, room_upper.room_id))
                combined.add_edge(stair_edge[0], stair_edge[1], cost=vertical_cost)
                stair_edges.append(stair_edge)

            for elevator in lower.elevators:
                room_lower = _nearest_room(lower.rooms, elevator.position)
                room_upper = _nearest_room(upper.rooms, elevator.position)
                if room_lower is None or room_upper is None:
                    continue
                vertical_cost = floor_height * ELEVATOR_COST_MULTIPLIER
                edge = ((lower.floor_index, room_lower.room_id),
                        (upper.floor_index, room_upper.room_id))
                combined.add_edge(edge[0], edge[1], cost=vertical_cost)
                elevator_edges.append(edge)

        building_graph = BuildingNavGraph(graph=combined, floor_count=len(floors_sorted),
                                           floor_height=floor_height, elevator_edges=elevator_edges,
                                           stair_edges=stair_edges)
        if hazard_rules is not None and hazard_rules.disable_elevators:
            building_graph.disable_elevators()
        return building_graph

    @staticmethod
    def nearest_node(building_graph: BuildingNavGraph, floor_index: int,
                      position: Point2D) -> FloorNodeId | None:
        """Verilen kattaki en yakın oda düğümünü bulur (ör. bir agent'ın
        başlangıç konumundan en yakın navigasyon düğümüne 'snap' etmesi
        için)."""
        candidates = [n for n in building_graph.graph.positions if n[0] == floor_index]
        if not candidates:
            return None
        return min(candidates, key=lambda n: building_graph.graph.positions[n].distance_to(position))

    @staticmethod
    def z_of_floor(building_graph: BuildingNavGraph, floor_index: int) -> float:
        """Roadmap V10 / Faz 1.2: `floor_index` -> dünya-Z (metre)
        dönüşümü. Basit doğrusal varsayım (her kat `floor_height` kadar
        yüksek, zemin kat z=0) - gerçek kat kotu verisi varsa (ör.
        `building_elements` içinde) ileride buradan okunacak şekilde tek
        bir dönüşüm noktasına toplanmıştır (roadmap ilkesi #2)."""
        return floor_index * building_graph.floor_height
