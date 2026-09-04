"""
visualization.scenario_visual_bridge — Roadmap V10 / Faz 5 (5.1-5.5)
========================================================================

"Diğer Simülasyonların Derinleştirilmesi" fazının veri/mantık katmanı.
`physics.building_shake` / `physics.building_damage` / `mobility.
crowd_simulation.agent_visuals` ile **aynı disiplin**: bu modül hiçbir
GPU/shader/DOM kodu içermez — mevcut simülasyon motorlarının (zaten var
olan `hazard_data.fire_spread`, `mobility.emergency_response`, `mobility.
traffic_simulation`, `visualization.heatmap_overlay`, `population.
synthetic_population`) çıktısını, `render_engine.scene_bridge.Scene`'in
tüketebileceği düz, JSON-serileştirilebilir sözleşmelere indirger. Yeni
bir simülasyon motoru icat EDİLMEZ (roadmap ilkesi #2) — yalnızca var
olan motorların çıktısı, henüz hiçbir yerde sahneye bağlanmamış olan
görsel katmana köprülenir.

Kapsanan roadmap maddeleri:
- **5.1** — `fire_facade_overlay()`: `hazard_data.fire_spread.
  FireSpreadModel` hücre-yoğunluğunu, bina cephesine bindirilecek
  duman/alev sprite noktalarına (`FacadeFireSprite`) çevirir.
- **5.2** — `emergency_vehicle_icon_frame()`: `mobility.
  emergency_response.DispatchResult`'ı gerçek araç ikonu + rota
  animasyonu sözleşmesine (`EmergencyVehicleFrame`) çevirir.
- **5.3** — `bind_heatmap_to_scene_layer()`: `visualization.
  heatmap_overlay`'in (V9'dan beri var) ürettiği `HeatmapCell`
  listesini, `Scene`'in "overlay katmanı" biçimine (düz dict listesi)
  indirger — roadmap'in "heatmap overlay bağlanması" maddesinin
  eksik kalan tek parçası buydu (hesaplama zaten vardı, sahneye
  bağlanmıyordu).
- **5.4** — `daily_routine_visual_tag()`: `population.
  synthetic_population.DailyRoutineType` + saat bilgisinden, ajanın o
  anki günlük-rutin durumuna göre görsel etiket (ör. "işte", "evde",
  "yolda") üretir; `mobility.crowd_simulation.agent_visuals`'ın
  tükettiği sözleşmeyle aynı ailede (yeni bir görsel kategori icat
  edilmedi, mevcut ajan görsel durumuna eklenen bir alan).
- **5.5** — `traffic_vehicle_scene_frame()`: `mobility.
  traffic_simulation.TrafficAgent`'ı sahne-hazır araç kare verisine
  (`VehicleSceneFrame`) çevirir; yoğunluk/tıkanıklık `Greenshields
  Model.is_congested()` (zaten var) ile hız-tint'ine eşlenir.

Dürüstlük notu: bu modülün ürettiği tüm sözleşmeler render-agnostiktir;
gerçek sprite/partikül/GLSL kodu `render_engine`'in (varsa) tarafında
yaşar. Burada üretilen veri, o katmanın "ne çizeceğini" belirleyen
girdidir — "nasıl çizeceğini" değil (Faz 2 STATUS notundaki ayrımla
birebir tutarlı).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from ..core_engine.geometry_engine import Point2D
from ..hazard_data.fire_spread import FireCellState, FireSpreadModel, CellId
from ..mobility.emergency_response import DispatchResult, EmergencyUnitType
from ..mobility.traffic_simulation import TrafficAgent, VehicleType, GreenshieldsModel
from ..population.synthetic_population import DailyRoutineType
from .heatmap_overlay import HeatmapCell

__all__ = [
    "FacadeFireSprite",
    "fire_facade_overlay",
    "EmergencyVehicleFrame",
    "emergency_vehicle_icon_frame",
    "bind_heatmap_to_scene_layer",
    "RoutineVisualState",
    "daily_routine_visual_tag",
    "VehicleSceneFrame",
    "traffic_vehicle_scene_frame",
    "FIRE_OVERLAY_HONESTY_NOTE",
]


# ======================================================================== #
# 5.1 — Yangın → 3D bina cephesine bindirilmiş duman/alev sprite
# ======================================================================== #

#: Roadmap'in kendi dürüstlük ilkesiyle tutarlı, `physics.building_damage.
#: DAMAGE_HONESTY_NOTE` ile aynı üslupta - bu overlay bir CFD sonucu değil,
#: hücre-otomat yoğunluğunun basit bir sprite-yerleşim haritasıdır (Faz 6
#: CFD'nin "Deneysel" öncesi, ucuz ve varsayılan yaklaşımı).
FIRE_OVERLAY_HONESTY_NOTE = (
    "Bu duman/alev görselleştirmesi, hücre-otomat tabanlı bir yoğunluk "
    "yaklaşımından türetilmiş sprite yerleşimidir; gerçek bir akışkanlar "
    "dinamiği (CFD) simülasyonu değildir. Hava akımına duyarlı gerçekçi "
    "duman şekli için Faz 6 (deneysel) gereklidir."
)


class FireSpriteKind(str, Enum):
    """`FireCellState`'in render tarafındaki sprite karşılığı - 1:1 eşleme,
    yeni bir sınıflandırma icat edilmedi."""

    SMOKE = "smoke"
    FLAME = "flame"


@dataclass(slots=True, frozen=True)
class FacadeFireSprite:
    """Bina cephesi üzerinde tek bir duman/alev sprite noktası -
    render-agnostik: konum + yoğunluk + tür, gerçek sprite/partikül
    dokusu render motorunun tarafındadır."""

    building_id: str
    world_position: Point2D
    height_m: float
    kind: FireSpriteKind
    intensity: float          # 0.0-1.0, sprite opaklığı/ölçeği için
    honesty_note: str = FIRE_OVERLAY_HONESTY_NOTE


def fire_facade_overlay(
    model: FireSpreadModel,
    building_id: str,
    *,
    cell_to_world: "callable[[CellId], Point2D]",
    floor_height_m: float = 3.2,
    cell_to_floor: Optional["callable[[CellId], int]"] = None,
) -> list[FacadeFireSprite]:
    """`FireSpreadModel.intensity`'yi (zaten var, roadmap 5.1'in tek girdisi)
    bir binanın cephesine bindirilecek `FacadeFireSprite` listesine çevirir.

    Yalnızca `SMOKE`/`FIRE` eşiğini aşan hücreler sprite üretir (`CLEAR`
    hücreler sessizce atlanır — roadmap'in "gösterge niteliğinde ama
    performanslı" ilkesiyle tutarlı, boş sahneye gereksiz sprite
    basılmaz). `cell_to_floor` verilmezse tüm sprite'lar zemin
    yüksekliğinde (0. kat) varsayılır — çağıran taraf isterse gerçek
    kat eşlemesini sağlayabilir (bu modül kat-hücre eşlemesini icat
    etmez, `building_reconstruction`'ın sorumluluğundadır).
    """
    sprites: list[FacadeFireSprite] = []
    for cell, intensity in model.intensity.items():
        state = model.state_of(cell)
        if state is FireCellState.CLEAR:
            continue
        floor_index = cell_to_floor(cell) if cell_to_floor is not None else 0
        sprites.append(FacadeFireSprite(
            building_id=building_id,
            world_position=cell_to_world(cell),
            height_m=floor_index * floor_height_m,
            kind=FireSpriteKind.FLAME if state is FireCellState.FIRE else FireSpriteKind.SMOKE,
            intensity=intensity,
        ))
    return sprites


# ======================================================================== #
# 5.2 — Acil müdahale → gerçek araç ikonu + rota animasyonu
# ======================================================================== #

class EmergencyVehicleIcon(str, Enum):
    """`EmergencyUnitType`'ın render tarafındaki ikon karşılığı - 1:1
    eşleme, yeni bir taksonomi icat edilmedi."""

    FIRE_TRUCK = "fire_truck_icon"
    AMBULANCE = "ambulance_icon"
    POLICE = "police_icon"


_ICON_BY_UNIT_TYPE: dict[EmergencyUnitType, EmergencyVehicleIcon] = {
    EmergencyUnitType.FIRE_TRUCK: EmergencyVehicleIcon.FIRE_TRUCK,
    EmergencyUnitType.AMBULANCE: EmergencyVehicleIcon.AMBULANCE,
    EmergencyUnitType.POLICE: EmergencyVehicleIcon.POLICE,
}


@dataclass(slots=True, frozen=True)
class EmergencyVehicleFrame:
    """`DispatchResult`'ın sahneye-hazır karşılığı: ikon + rota (polyline,
    A*'ın bulduğu düğüm dizisinin dünya-koordinat karşılığı) + tahmini
    varış süresi (UI'da geri sayım için, `estimated_response_seconds`
    zaten `emergency_response`'ta hesaplanıyor, burada yeniden hesap
    yapılmaz)."""

    icon: EmergencyVehicleIcon
    station_id: str
    route_polyline: list[Point2D]
    estimated_response_seconds: float
    found: bool


def emergency_vehicle_icon_frame(
    dispatch: DispatchResult,
    *,
    node_to_world: "callable[[object], Point2D]",
) -> EmergencyVehicleFrame:
    """`DispatchResult.path_result.path`'i (A*'ın bulduğu düğüm dizisi,
    zaten var) `node_to_world` ile dünya koordinatlarına çevirip
    `EmergencyVehicleFrame`'e sarar. `dispatch.found=False` ise boş
    polyline döner (çağıran taraf ikon çizmemeli) — sessizce hata
    fırlatmaz, roadmap'in "hiçbir istasyon uygun değilse None döner"
    disipliniyle tutarlı bir devam."""
    if not dispatch.found:
        return EmergencyVehicleFrame(
            icon=_ICON_BY_UNIT_TYPE[dispatch.unit_type],
            station_id=dispatch.station.station_id,
            route_polyline=[],
            estimated_response_seconds=dispatch.estimated_response_seconds,
            found=False,
        )
    polyline = [node_to_world(n) for n in dispatch.path_result.path]
    return EmergencyVehicleFrame(
        icon=_ICON_BY_UNIT_TYPE[dispatch.unit_type],
        station_id=dispatch.station.station_id,
        route_polyline=polyline,
        estimated_response_seconds=dispatch.estimated_response_seconds,
        found=True,
    )


# ======================================================================== #
# 5.3 — Bölgesel yığılma → heatmap overlay bağlanması
# ======================================================================== #

def bind_heatmap_to_scene_layer(cells: Iterable[HeatmapCell]) -> list[dict]:
    """`visualization.heatmap_overlay` (V9'dan beri var, hesaplama katmanı
    zaten tamamdı) çıktısını `render_engine.scene_bridge.Scene`'in
    `overlay_layers` alanına (bu oturumda eklendi) doğrudan yazılabilecek
    düz dict listesine indirger. Yeni bir renk/yoğunluk hesabı YAPILMAZ -
    `HeatmapCell.color_rgb`/`density_ratio` olduğu gibi taşınır; roadmap
    5.3'ün eksik kalan tek parçası (hesaplama var, sahne-bağlantısı
    yoktu) burada kapanır."""
    return [
        {
            "type": "heatmap_cell",
            "center": [cell.center.x, cell.center.y],
            "cell_size": cell.cell_size,
            "density_ratio": cell.density_ratio,
            "color_rgb": list(cell.color_rgb),
        }
        for cell in cells
    ]


# ======================================================================== #
# 5.4 — Sentetik nüfus → günlük rutin motorunun görsel tüketicisi
# ======================================================================== #

class RoutineVisualState(str, Enum):
    """`DailyRoutineType` + saat bilgisinden türeyen kaba görsel durum -
    ajanın o anki sahne-görünürlüğü/animasyon havuzu için (ör. "evde"
    ajanı sahnede hiç göstermemek, "yolda" ajanını normal crowd
    simülasyonuna vermek). Yeni bir davranış modeli icat edilmedi -
    `agent_visuals.AnimationClip`/`AgentBehavior` ile aynı ailede,
    yalnızca "günün bu saatinde bu ajan nerede olmalı" sorusuna kaba
    bir cevap."""

    AT_HOME = "at_home"
    AT_WORK_OR_SCHOOL = "at_work_or_school"
    COMMUTING = "commuting"


#: Roadmap 5.4 — kaba, gösterge niteliğinde saat aralıkları (TÜİK/OECD
#: tipik "iş günü" örüntüsüyle tutarlı, belirli bir kişinin gerçek
#: takvimi değil). `synthetic_population`'ın kendi "gösterge niteliğinde"
#: disipliniyle (bkz. `DEFAULT_AGE_GROUP_DISTRIBUTION` notu) aynı.
_COMMUTE_WINDOWS: dict[DailyRoutineType, tuple[tuple[int, int], tuple[int, int]]] = {
    DailyRoutineType.SCHOOL_CHILD: ((7, 8), (15, 16)),
    DailyRoutineType.WORKER_OFFICE: ((7, 9), (17, 19)),
    DailyRoutineType.WORKER_SHIFT: ((5, 6), (13, 14)),
}
_AWAY_WINDOWS: dict[DailyRoutineType, tuple[int, int]] = {
    DailyRoutineType.SCHOOL_CHILD: (8, 15),
    DailyRoutineType.WORKER_OFFICE: (9, 17),
    DailyRoutineType.WORKER_SHIFT: (6, 13),
}


def daily_routine_visual_tag(routine_type: DailyRoutineType, hour_of_day: int) -> RoutineVisualState:
    """`hour_of_day` (0-23) bir `_COMMUTE_WINDOWS` aralığına düşerse
    `COMMUTING`, `_AWAY_WINDOWS` aralığındaysa `AT_WORK_OR_SCHOOL`,
    aksi halde `AT_HOME` döner. `HOMEMAKER`/`RETIRED`/
    `UNEMPLOYED_OR_FLEXIBLE` gibi sabit-ev rutinleri için hiçbir pencere
    tanımlı değildir → her zaman `AT_HOME` (roadmap'in "kaba sınıflama"
    notuyla tutarlı, aşırı detaylandırılmadı)."""
    hour_of_day = hour_of_day % 24
    commute = _COMMUTE_WINDOWS.get(routine_type)
    if commute is not None:
        for start, end in commute:
            if start <= hour_of_day < end:
                return RoutineVisualState.COMMUTING
    away = _AWAY_WINDOWS.get(routine_type)
    if away is not None:
        start, end = away
        if start <= hour_of_day < end:
            return RoutineVisualState.AT_WORK_OR_SCHOOL
    return RoutineVisualState.AT_HOME


# ======================================================================== #
# 5.5 — Trafik/araç simülasyonu entegrasyonu
# ======================================================================== #

class VehicleSpeedTint(str, Enum):
    """`GreenshieldsModel.is_congested()` (zaten var, Faz D6) çıktısının
    görsel karşılığı - yeni bir tıkanıklık modeli icat edilmedi."""

    FREE_FLOW = "green"
    SLOWING = "yellow"
    CONGESTED = "red"


@dataclass(slots=True, frozen=True)
class VehicleSceneFrame:
    """`TrafficAgent`'ın sahneye-hazır tek kare karşılığı: konum +
    yön (rota üzerindeki bir sonraki noktaya bakan birim vektör açısı,
    derece) + tıkanıklık-tint'i."""

    agent_id: int
    vehicle_type: VehicleType
    position: Point2D
    heading_deg: float
    speed_tint: VehicleSpeedTint


def _heading_degrees(agent: TrafficAgent) -> float:
    """Rota üzerindeki mevcut segmentin yönünü derece cinsinden verir;
    ilerleme yoksa (henüz hareket etmemiş/tek noktalık rota) 0.0."""
    import math
    pos = agent.current_position()
    positions = agent.route_positions
    if len(positions) < 2:
        return 0.0
    remaining = agent.distance_along_route
    for i in range(len(positions) - 1):
        a, b = positions[i], positions[i + 1]
        seg_len = a.distance_to(b)
        if remaining <= seg_len or i == len(positions) - 2:
            dx, dy = b.x - a.x, b.y - a.y
            if dx == 0 and dy == 0:
                return 0.0
            return math.degrees(math.atan2(dy, dx))
        remaining -= seg_len
    return 0.0


def traffic_vehicle_scene_frame(
    agent: TrafficAgent,
    *,
    density_veh_per_km: Optional[float] = None,
    flow_model: Optional[GreenshieldsModel] = None,
) -> VehicleSceneFrame:
    """`TrafficAgent`'ı (zaten var, IDM ile hareket ettirilir) sahne
    karesine çevirir. `density_veh_per_km` + `flow_model` verilirse
    `GreenshieldsModel.is_congested()` (zaten var, yeni bir tıkanıklık
    eşiği icat edilmedi) ile tint belirlenir; verilmezse aracın kendi
    anlık hızı ile `desired_speed`'i (`params()`, zaten var)
    karşılaştırılarak kaba bir tint türetilir - iki yol da tutarlı,
    çağıran taraf hangi bilgiye sahipse onu kullanır."""
    if flow_model is not None and density_veh_per_km is not None:
        congested = flow_model.is_congested(density_veh_per_km)
        tint = VehicleSpeedTint.CONGESTED if congested else VehicleSpeedTint.FREE_FLOW
    else:
        desired = agent.params().desired_speed
        ratio = agent.speed / desired if desired > 0 else 1.0
        if ratio < 0.35:
            tint = VehicleSpeedTint.CONGESTED
        elif ratio < 0.75:
            tint = VehicleSpeedTint.SLOWING
        else:
            tint = VehicleSpeedTint.FREE_FLOW

    return VehicleSceneFrame(
        agent_id=agent.agent_id,
        vehicle_type=agent.vehicle_type,
        position=agent.current_position(),
        heading_deg=_heading_degrees(agent),
        speed_tint=tint,
    )
