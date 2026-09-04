"""
Building Elements
==================

Roadmap Phase 3: Window Generator, Balcony Generator, Stair Generator,
Elevator Core, Corridor Generator, Door Generator.

Her biri bağımsız, `Floor` şemasına eleman ekleyen fonksiyonlardır. Girdi/
çıktı olarak `core_engine.geometry_engine.Point2D` yerleşim noktaları
kullanılır (üst seviye 3D mesh üretimi `mesh_engine` ile facade/roof
katmanında birleştirilir).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum

from ..core_engine.geometry_engine import Point2D, Polygon
from ..mesh_engine import Mesh3D, MeshBuilder, MeshMerger, NormalGenerator, Vertex3D, _ear_clip_triangulate


# ============================================================================ #
# Roadmap V-yeni, Faz 1.3: Pencere / Kapı Tipolojisi
# ============================================================================ #

class WindowType(str, Enum):
    """Roadmap 1.3: 'Pencere tipolojisi: sabit, açılır, sürgülü, balkon kapısı'."""

    FIXED = "sabit"
    CASEMENT = "acilir"
    SLIDING = "surgulu"
    BALCONY_DOOR = "balkon_kapisi"


class DoorType(str, Enum):
    """Roadmap 1.3: 'kapı tipolojisi: giriş kapısı, yangın kapısı, garaj kapısı'."""

    ENTRANCE = "giris_kapisi"
    FIRE = "yangin_kapisi"
    GARAGE = "garaj_kapisi"
    INTERIOR = "ic_kapi"


# Tipe göre gerçekçi varsayılan en/boy (m) - cephe ritmi/typology üretiminde
# kullanılır. Balkon kapısı zemine kadar iner (sill_height=0).
WINDOW_TYPE_DEFAULTS: dict[WindowType, dict[str, float]] = {
    WindowType.FIXED: {"width": 1.0, "height": 1.2, "sill_height_ratio": 0.4},
    WindowType.CASEMENT: {"width": 1.2, "height": 1.4, "sill_height_ratio": 0.35},
    WindowType.SLIDING: {"width": 1.8, "height": 1.4, "sill_height_ratio": 0.35},
    WindowType.BALCONY_DOOR: {"width": 1.4, "height": 2.1, "sill_height_ratio": 0.0},
}

DOOR_TYPE_DEFAULTS: dict[DoorType, dict[str, float]] = {
    DoorType.ENTRANCE: {"width": 1.2, "height": 2.1},
    DoorType.FIRE: {"width": 1.0, "height": 2.1},
    DoorType.GARAGE: {"width": 3.0, "height": 2.4},
    DoorType.INTERIOR: {"width": 0.9, "height": 2.1},
}


# ============================================================================ #
# Window Generator
# ============================================================================ #

@dataclass(slots=True)
class WindowPlacement:
    position: Point2D
    width: float
    height: float
    sill_height: float
    wall_edge_index: int
    window_type: WindowType = WindowType.CASEMENT


class WindowGenerator:
    """Roadmap: 'Window Generator' - spacing / alignment / symmetry /
    randomization / facade pattern."""

    @staticmethod
    def place_on_wall(
        wall_start: Point2D,
        wall_end: Point2D,
        wall_edge_index: int,
        window_width: float = 1.2,
        window_height: float = 1.4,
        sill_height: float = 0.9,
        spacing: float = 2.5,
        margin: float = 0.6,
        symmetric: bool = True,
        seed: int | None = None,
        jitter: float = 0.0,
        window_type: WindowType = WindowType.CASEMENT,
    ) -> list[WindowPlacement]:
        """Bir duvar segmenti üzerine, kenar boyunca eşit aralıklı (grid)
        pencereler yerleştirir. `symmetric=True` iken yerleşim duvar
        ortasına göre simetriktir; `jitter` ile hafif rastgele varyasyon
        (facade pattern çeşitliliği) eklenebilir."""
        wall_length = wall_start.distance_to(wall_end)
        usable = wall_length - 2 * margin
        if usable <= window_width:
            return []

        count = max(1, int(usable // spacing) + 1)
        rng = random.Random(seed)

        dx = (wall_end.x - wall_start.x) / wall_length
        dy = (wall_end.y - wall_start.y) / wall_length

        if symmetric:
            total_span = (count - 1) * spacing
            start_offset = (wall_length - total_span) / 2.0
        else:
            start_offset = margin

        placements: list[WindowPlacement] = []
        for i in range(count):
            offset = start_offset + i * spacing
            if jitter:
                offset += rng.uniform(-jitter, jitter)
            offset = max(margin, min(wall_length - margin, offset))
            pos = Point2D(wall_start.x + dx * offset, wall_start.y + dy * offset)
            placements.append(WindowPlacement(
                position=pos, width=window_width, height=window_height,
                sill_height=sill_height, wall_edge_index=wall_edge_index,
                window_type=window_type,
            ))
        return placements

    @staticmethod
    def place_on_footprint(
        polygon: Polygon,
        window_width: float = 1.2,
        window_height: float = 1.4,
        sill_height: float = 0.9,
        spacing: float = 2.5,
        seed: int | None = None,
        window_type: WindowType = WindowType.CASEMENT,
    ) -> list[WindowPlacement]:
        """Tüm footprint çevresine (her kenara) pencere dizer."""
        ring = polygon.closed_ring()
        result: list[WindowPlacement] = []
        for i, (a, b) in enumerate(zip(ring, ring[1:])):
            result.extend(WindowGenerator.place_on_wall(
                a, b, i, window_width, window_height, sill_height, spacing, seed=seed,
                window_type=window_type,
            ))
        return result

    @staticmethod
    def place_facade_rhythm(
        polygon: Polygon,
        floor_index: int,
        floor_height: float,
        seed: int | None = None,
        has_balcony_door: bool = False,
        balcony_edge_index: int | None = None,
    ) -> list[WindowPlacement]:
        """Roadmap 1.3: 'cephe ritmi (window rhythm) algoritması' - eski
        `place_on_footprint` tamamen mekanik eşit-aralıklı bir gride
        dayanıyordu (roadmap'in şikayet ettiği tam da bu). Bu metod kat
        bazlı gerçekçi bir tipoloji karışımı üretir:

        - Zemin katta (floor_index == 0) daha büyük sürgülü/vitrin
          pencereler (ticari cephe hissi).
        - Üst katlarda standart açılır pencereler.
        - has_balcony_door=True ise, belirtilen (veya en uzun) kenarın
          ortasındaki pencere BALCONY_DOOR tipine çevrilir (zemine iner).
        """
        is_ground = floor_index == 0
        w_type = WindowType.SLIDING if is_ground else WindowType.CASEMENT
        width = 1.6 if is_ground else 1.2
        height = 1.6 if is_ground else 1.4
        sill_ratio = WINDOW_TYPE_DEFAULTS[w_type]["sill_height_ratio"]

        windows = WindowGenerator.place_on_footprint(
            polygon, window_width=width, window_height=height,
            sill_height=floor_height * sill_ratio, spacing=2.5,
            seed=(None if seed is None else seed + floor_index),
            window_type=w_type,
        )

        if has_balcony_door and windows:
            ring = polygon.closed_ring()
            if balcony_edge_index is None:
                lengths = [ring[i].distance_to(ring[i + 1]) for i in range(len(ring) - 1)]
                balcony_edge_index = max(range(len(lengths)), key=lambda i: lengths[i])
            candidates = [w for w in windows if w.wall_edge_index == balcony_edge_index]
            if candidates:
                target = candidates[len(candidates) // 2]
                target.window_type = WindowType.BALCONY_DOOR
                target.width = WINDOW_TYPE_DEFAULTS[WindowType.BALCONY_DOOR]["width"]
                target.height = WINDOW_TYPE_DEFAULTS[WindowType.BALCONY_DOOR]["height"]
                target.sill_height = 0.0
        return windows


# ============================================================================ #
# Balcony Generator
# ============================================================================ #

@dataclass(slots=True)
class Balcony:
    position: Point2D
    width: float
    depth: float
    wall_edge_index: int


class BalconyGenerator:
    """Roadmap: 'Balcony Generator'."""

    @staticmethod
    def place_on_windows(
        windows: list[WindowPlacement],
        depth: float = 1.2,
        every_nth: int = 1,
        floor_level: int = 0,
        min_floor_for_balcony: int = 1,
    ) -> list[Balcony]:
        """Belirli pencerelerin önüne balkon yerleştirir (yer katında
        güvenlik amaçlı varsayılan olarak balkon yerleştirilmez)."""
        if floor_level < min_floor_for_balcony:
            return []
        balconies = []
        for i, w in enumerate(windows):
            if i % every_nth != 0:
                continue
            balconies.append(Balcony(
                position=w.position, width=w.width + 0.4, depth=depth,
                wall_edge_index=w.wall_edge_index,
            ))
        return balconies


# ============================================================================ #
# Stair Generator
# ============================================================================ #

@dataclass(slots=True)
class Stair:
    position: Point2D
    width: float
    run_length: float
    step_count: int
    step_height: float
    step_depth: float
    rotation_deg: float = 0.0


class StairGenerator:
    """Roadmap: 'Stair Generator'. Standart adım oranları (yükseklik
    17-18cm, derinlik 28-30cm - konfor formülü 2*rise + run ~ 63cm)."""

    @staticmethod
    def generate(
        position: Point2D,
        floor_height: float,
        width: float = 1.2,
        max_step_height: float = 0.18,
        step_depth: float = 0.29,
        rotation_deg: float = 0.0,
    ) -> Stair:
        step_count = max(1, math.ceil(floor_height / max_step_height))
        actual_step_height = floor_height / step_count
        run_length = step_count * step_depth
        return Stair(
            position=position, width=width, run_length=run_length,
            step_count=step_count, step_height=actual_step_height,
            step_depth=step_depth, rotation_deg=rotation_deg,
        )


# ============================================================================ #
# Elevator Core
# ============================================================================ #

@dataclass(slots=True)
class ElevatorCore:
    position: Point2D
    width: float
    depth: float
    shaft_top_z: float
    shaft_bottom_z: float
    car_count: int = 1


class ElevatorCoreGenerator:
    """Roadmap: 'Elevator Core'."""

    @staticmethod
    def generate(
        position: Point2D,
        total_building_height: float,
        base_z: float = 0.0,
        width: float = 2.0,
        depth: float = 2.0,
        car_count: int = 1,
        overrun_m: float = 3.0,
    ) -> ElevatorCore:
        return ElevatorCore(
            position=position, width=width, depth=depth,
            shaft_bottom_z=base_z, shaft_top_z=base_z + total_building_height + overrun_m,
            car_count=car_count,
        )


# ============================================================================ #
# Corridor Generator
# ============================================================================ #

class CorridorGenerator:
    """Roadmap: 'Corridor Generator'. `RoomGenerator`'ın koridor-tipi
    odalarını birleştirerek bir dolaşım (circulation) polygon zinciri
    üretir."""

    @staticmethod
    def from_rooms(rooms: list, corridor_room_type: str = "koridor") -> list[Polygon]:
        return [r.polygon for r in rooms if getattr(r, "room_type", None) == corridor_room_type]

    @staticmethod
    def centerline(corridor_polygons: list[Polygon]) -> list[Point2D]:
        """Her koridor dikdörtgeninin merkez noktasından basit bir
        dolaşım-hattı (centerline) polyline'ı üretir."""
        centers = []
        for poly in corridor_polygons:
            xs = [p.x for p in poly.points]
            ys = [p.y for p in poly.points]
            centers.append(Point2D(sum(xs) / len(xs), sum(ys) / len(ys)))
        return centers


# ============================================================================ #
# Door Generator
# ============================================================================ #

@dataclass(slots=True)
class Door:
    position: Point2D
    width: float
    is_exterior: bool
    wall_edge_index: int
    connects_room_ids: tuple[int, int] | None = None
    door_type: DoorType = DoorType.ENTRANCE
    height: float = 2.1


class DoorGenerator:
    """Roadmap: 'Door Generator'."""

    @staticmethod
    def exterior_entrance(
        polygon: Polygon, width: float | None = None, door_type: DoorType = DoorType.ENTRANCE,
    ) -> Door:
        """En uzun dış duvar kenarının orta noktasına ana giriş kapısı
        yerleştirir. `door_type` ile yangın kapısı/garaj kapısı gibi diğer
        tipoloji varyantları da üretilebilir (Roadmap 1.3). `width=None`
        (varsayılan) iken tipe uygun gerçekçi genişlik kullanılır
        (`DOOR_TYPE_DEFAULTS`); açıkça bir genişlik verilirse o kullanılır."""
        ring = polygon.closed_ring()
        best_len, best_idx, best_mid = -1.0, 0, Point2D(0, 0)
        for i, (a, b) in enumerate(zip(ring, ring[1:])):
            length = a.distance_to(b)
            if length > best_len:
                best_len = length
                best_idx = i
                best_mid = Point2D((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        defaults = DOOR_TYPE_DEFAULTS[door_type]
        actual_width = width if width is not None else defaults["width"]
        return Door(
            position=best_mid, width=actual_width, is_exterior=True, wall_edge_index=best_idx,
            door_type=door_type, height=defaults["height"],
        )

    @staticmethod
    def secondary_exit(
        polygon: Polygon, exclude_edge_index: int, width: float | None = None,
        door_type: DoorType = DoorType.FIRE,
    ) -> Door:
        """Roadmap 1.3 + 2.3 (kaçış rotası) ön koşulu: ana girişten farklı
        bir kenara ikinci bir çıkış (varsayılan: yangın kapısı) yerleştirir.
        `FacadeGenerator.check_compliance`'daki `has_second_egress`
        kontrolünü gerçek bir geometriyle karşılamak için kullanılabilir."""
        ring = polygon.closed_ring()
        defaults = DOOR_TYPE_DEFAULTS[door_type]
        w = width if width is not None else defaults["width"]
        best_len, best_idx, best_mid = -1.0, None, Point2D(0, 0)
        for i, (a, b) in enumerate(zip(ring, ring[1:])):
            if i == exclude_edge_index:
                continue
            length = a.distance_to(b)
            if length > best_len:
                best_len = length
                best_idx = i
                best_mid = Point2D((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        if best_idx is None:
            best_idx = 0 if exclude_edge_index != 0 else (1 % max(1, len(ring) - 1))
            a, b = ring[best_idx], ring[(best_idx + 1) % (len(ring) - 1)]
            best_mid = Point2D((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        return Door(
            position=best_mid, width=w, is_exterior=True, wall_edge_index=best_idx,
            door_type=door_type, height=defaults["height"],
        )

    @staticmethod
    def interior_doors(rooms: list) -> list[Door]:
        """Komşu (adjacency graph'te bağlı) her oda çifti arasına bir iç
        kapı yerleştirir (paylaşılan kenarın orta noktasına en yakın
        yaklaşık konum: iki oda merkezinin ortası)."""
        doors: list[Door] = []
        seen: set[tuple[int, int]] = set()
        by_id = {r.room_id: r for r in rooms}
        for room in rooms:
            for neighbor_id in room.neighbors:
                key = tuple(sorted((room.room_id, neighbor_id)))
                if key in seen:
                    continue
                seen.add(key)
                neighbor = by_id[neighbor_id]
                a_center = _polygon_center(room.polygon)
                b_center = _polygon_center(neighbor.polygon)
                mid = Point2D((a_center.x + b_center.x) / 2.0, (a_center.y + b_center.y) / 2.0)
                doors.append(Door(
                    position=mid, width=0.9, is_exterior=False,
                    wall_edge_index=-1, connects_room_ids=key,
                ))
        return doors


# ============================================================================ #
# Bay Window (çıkma) Generator - Roadmap 1.3
# ============================================================================ #

@dataclass(slots=True)
class BayWindow:
    """Cepheden dışa taşan çıkma (bay window) - `window`in oturduğu duvar
    kenarından `protrusion` kadar dışarı çıkan üç yüzlü bir hacim olarak
    modellenir (mesh üretimi `mesh_engine.BayWindowMeshBuilder`'da)."""

    window: WindowPlacement
    protrusion: float = 0.6
    side_width: float = 1.6


class BayWindowGenerator:
    """Roadmap 1.3: 'Balkon, çıkma (bay window) ... gibi cephe
    zenginleştirme elemanları'."""

    @staticmethod
    def place_on_windows(
        windows: list[WindowPlacement],
        every_nth: int = 3,
        protrusion: float = 0.6,
        side_width: float = 1.6,
        eligible_types: tuple[WindowType, ...] = (WindowType.FIXED, WindowType.CASEMENT),
    ) -> list[BayWindow]:
        """Belirli aralıklarla uygun tipteki pencereleri çıkmaya çevirir
        (balkon kapıları/sürgülü vitrin pencereleri çıkma yapılmaz)."""
        result: list[BayWindow] = []
        eligible = [w for w in windows if w.window_type in eligible_types]
        for i, w in enumerate(eligible):
            if i % every_nth != 0:
                continue
            result.append(BayWindow(window=w, protrusion=protrusion, side_width=side_width))
        return result


# ============================================================================ #
# Entrance Canopy (giriş sundurması) Generator - Roadmap 1.3
# ============================================================================ #

@dataclass(slots=True)
class EntranceCanopy:
    """Ana giriş kapısının üzerine, duvardan dışa taşan yatay bir
    sundurma/saçak plakası."""

    door: "Door"
    width: float
    depth: float = 1.2
    thickness: float = 0.15
    height_above_door: float = 2.3


class EntranceCanopyGenerator:
    """Roadmap 1.3: '... giriş sundurması gibi cephe zenginleştirme
    elemanları'."""

    @staticmethod
    def for_entrance(door: "Door", extra_width: float = 0.6, depth: float = 1.2) -> EntranceCanopy:
        return EntranceCanopy(
            door=door, width=door.width + extra_width, depth=depth,
            height_above_door=door.height + 0.2,
        )


def _polygon_center(polygon: Polygon) -> Point2D:
    xs = [p.x for p in polygon.points]
    ys = [p.y for p in polygon.points]
    return Point2D(sum(xs) / len(xs), sum(ys) / len(ys))


# ============================================================================ #
# ROADMAP_V5 M2.2: "Kat tipi ayrışması ... çekme katın (setback floor)
# footprint'inin üst kata göre otomatik içe ofsetlenmesi"
# ============================================================================ #

class SetbackFloorGenerator:
    """Çekme kat (setback floor) için footprint içe-ofsetleme.

    `RoofGenerator._offset_footprint` (roof_generator/__init__.py) ile aynı
    centroid-radyal ofsetleme yöntemini kullanır — çatı saçağı için DIŞA
    (pozitif), çekme kat için İÇE (negatif/inset) uygulanır. Aynı yöntemin
    tekrar kullanılması, farklı iki modülde iki ayrı offset algoritmasının
    birbirinden sapmasını (ve tutarsız görünmesini) önler."""

    @staticmethod
    def offset_footprint(polygon: Polygon, inset_m: float) -> Polygon:
        """`polygon`'u merkeze doğru `inset_m` metre içe ofsetler.

        Dışbükey olmayan (concave/L-U-T) poligonlarda merkez-radyal
        ofsetleme köşelerde tam paralel bir kenar üretmeyebilir (bu,
        `RoofGenerator._offset_footprint`'in saçak için DIŞA ofsette
        kullandığı yöntemle aynı bilinen sınırlamadır) — kabul kriteri
        açısından yeterlidir çünkü ölçülen şey "üst kat en az 1m içeride
        mi" (bkz. `min_inset_m`), kenarların tam paralelliği değil."""
        if inset_m <= 0:
            return polygon
        ring = polygon.closed_ring()[:-1]
        cx = sum(p.x for p in ring) / len(ring)
        cy = sum(p.y for p in ring) / len(ring)
        offset_pts = []
        for p in ring:
            dx, dy = p.x - cx, p.y - cy
            dist = math.hypot(dx, dy) or 1e-6
            # Kenarın merkeze mesafesinin tamamını içe çekmemek için,
            # normal yönünde basit bir öteleme uygularız (dist üzerinden
            # oranlamak küçük poligonlarda inset_m'i aşırı büyütebilir).
            offset_pts.append(Point2D(p.x - dx / dist * inset_m, p.y - dy / dist * inset_m))
        return Polygon(offset_pts)

    @staticmethod
    def min_inset_m(base_polygon: Polygon, setback_polygon: Polygon) -> float:
        """İki poligonun köşeleri arasındaki en küçük radyal (merkezden
        uzaklık farkı) mesafeyi döndürür — M2.2 kabul kriterinin
        ("üst kat footprint'i alt kata göre en az 1 metre içeride olmalı")
        geometrik doğrulaması için kullanılır."""
        base_ring = base_polygon.closed_ring()[:-1]
        setback_ring = setback_polygon.closed_ring()[:-1]
        if len(base_ring) != len(setback_ring):
            return 0.0
        cx = sum(p.x for p in base_ring) / len(base_ring)
        cy = sum(p.y for p in base_ring) / len(base_ring)
        diffs = []
        for bp, sp in zip(base_ring, setback_ring):
            base_r = math.hypot(bp.x - cx, bp.y - cy)
            setback_r = math.hypot(sp.x - cx, sp.y - cy)
            diffs.append(base_r - setback_r)
        return min(diffs) if diffs else 0.0


# ============================================================================ #
# ROADMAP_V5 — M2.2 (kalan madde): Cift Kabuk Cephe (Double-Skin Facade)
# ============================================================================ #

@dataclass(slots=True)
class DoubleSkinFacade:
    """Bir binanin birincil (ic) cephesinin disina eklenen ikinci bir cam
    kabuk + golgeleme paneli sisteminin sonucu."""

    outer_skin_mesh: Mesh3D
    shading_fin_mesh: Mesh3D | None
    gap_m: float
    fin_count: int


class DoubleSkinFacadeGenerator:
    """ROADMAP_V5 M2.2: 'Cift kabuk cephe (double-skin facade) - enerji
    verimli modern ofis binalari icin, iki katmanli cam+golgeleme mesh'i.'

    Yaklasim: birincil cephe (`FacadeGenerator` ciktisi) hic degismeden
    korunur; bu jenerator, `SetbackFloorGenerator.offset_footprint` ile
    ayni merkez-radyal ofsetleme yontemini **negatif** (disa dogru,
    `-gap_m`) kullanarak birincil footprint'in `gap_m` kadar disinda
    ikinci, ince bir cam kabuk (mullion citali, katlar arasi yatay bant)
    uretir; opsiyonel olarak duesey golgeleme kanatlari (fins) ekler.
    Sonuc, birincil cephe mesh'inden tamamen ayri, opt-in bir ek mesh'tir
    - mevcut `FacadeGenerator`/`ProceduralBuildingGenerator` mimarisine
    dokunmadan ust seviyede birlestirilir (bkz. `Building.double_skin`).
    """

    @staticmethod
    def _offset_footprint_outward(polygon: Polygon, gap_m: float) -> Polygon:
        """`SetbackFloorGenerator.offset_footprint` ile ayni yontem, ters
        yonde (disa dogru) - iki modulun offset algoritmasinin birbirinden
        sapmamasini saglamak icin merkez-radyal yaklasim tekrar kullanilir."""
        if gap_m <= 0:
            return polygon
        ring = polygon.closed_ring()[:-1]
        cx = sum(p.x for p in ring) / len(ring)
        cy = sum(p.y for p in ring) / len(ring)
        offset_pts = []
        for p in ring:
            dx, dy = p.x - cx, p.y - cy
            dist = math.hypot(dx, dy) or 1e-6
            offset_pts.append(Point2D(p.x + dx / dist * gap_m, p.y + dy / dist * gap_m))
        return Polygon(offset_pts)

    @staticmethod
    def _build_lofted_shell(
        base_polygon: Polygon, zs: list[float], gaps: list[float], name: str,
    ) -> Mesh3D:
        """RFC_FAZ6_1 'Ilk asama': `zs`/`gaps` ayni uzunlukta bir dizi -
        her yukseklikte `base_polygon`'un `gaps[i]` kadar disa ofsetlenmis
        halkasini uretir, ardisik halkalar arasinda yanal yuzey (frustum
        lateral surface, `extrude_polygon`'daki quad-yan-duvar mantiginin
        cok-segmentli hali) olusturur, en alt ve en ust halkayi taban/tavan
        olarak kapatir. `gaps` sabitse (tum degerler ayni), sonuc geometrik
        olarak tek bir `extrude_polygon` ile ayni disaridan gorunumu verir
        (yalnizca ic kesitlerde fazladan, gorunmez seam'ler olur) - ama bu
        yol yalnizca `gap_profile` acikca verildiginde kullanilir, varsayilan
        (`gap_profile=None`) davranis hala orijinal tek-extrude yolunu
        kullanir (bkz. cagiran `generate` govdesi)."""
        rings = [
            DoubleSkinFacadeGenerator._offset_footprint_outward(base_polygon, g).closed_ring()[:-1]
            for g in gaps
        ]
        n = len(rings[0])
        vertices: list[Vertex3D] = []
        for ring, z in zip(rings, zs):
            for p in ring:
                vertices.append(Vertex3D(p.x, p.y, z))

        triangles = []
        for seg in range(len(zs) - 1):
            base_off = seg * n
            top_off = (seg + 1) * n
            for i in range(n):
                i2 = (i + 1) % n
                bl, br = base_off + i, base_off + i2
                tl, tr = top_off + i, top_off + i2
                triangles.append((bl, br, tr))
                triangles.append((bl, tr, tl))

        bottom_indices = list(range(n))
        cap_tris = _ear_clip_triangulate(rings[0], bottom_indices)
        for (a, b, c) in cap_tris:
            triangles.append((a, c, b))
        top_base = (len(zs) - 1) * n
        cap_tris_top = _ear_clip_triangulate(rings[-1], bottom_indices)
        for (a, b, c) in cap_tris_top:
            triangles.append((a + top_base, b + top_base, c + top_base))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def generate(
        polygon: Polygon,
        base_z: float,
        floor_count: int,
        floor_height: float,
        gap_m: float = 0.9,
        gap_profile=None,
        mullion_thickness: float = 0.06,
        mullion_spacing_m: float = 1.5,
        add_shading_fins: bool = True,
        fin_depth_m: float = 0.4,
        fin_thickness_m: float = 0.05,
        fin_every_nth_mullion: int = 2,
    ) -> DoubleSkinFacade:
        """`polygon` (binanin birincil footprint'i, henuz `gap_m` kadar
        disa ofsetlenmemis - bu fonksiyon ofsetlemeyi kendi yapar) ->
        cift-kabuk dis cam zarfi + (opsiyonel) golgeleme kanatlari.

        Dis kabuk, tum bina yuksekligi boyunca (tum katlar) tek bir ince
        (`mullion_thickness` kalinliginda) cam prizma olarak uretilir -
        gercek bir cift-kabuk cephenin iki katmani arasindaki "buffer
        zone" havalandirma boslugunu (`gap_m`) temsil eder. Yatay mullion
        bantlari her kat hizasinda ince kutu (box) olarak eklenir; duesey
        golgeleme kanatlari `mullion_spacing_m` araliklarla, her
        `fin_every_nth_mullion` dikey hatta bir yerlestirilir.

        `gap_profile` (RFC_FAZ6_1 'Ilk asama', varsayilan `None` - opt-in,
        geriye donuk tam uyumlu): `None` iken davranis tamamen degismez
        (tek `extrude_polygon` cagrisi, sabit `gap_m`). Bir
        `Callable[[float], float]` verilirse (parametre: taban-goreli
        yukseklik `0..total_height` metre), dis kabuk artik sabit bir
        offsetle degil, her kat sinirinda `gap_profile(z)` ile hesaplanan
        degisken offsetle, kat-kat lofting (`_build_lofted_shell`) ile
        uretilir - bina yukseldikce daralan/genisleyen ('twisted'/
        'tapered') bir dis kabuk silueti elde edilebilir (RFC'nin
        onerdigi 'Secenek C': yalnizca gorsel dis kabuk eğrilir, ana
        yapisal kat/oda/pencere sistemi hic etkilenmez).
        """
        outer_polygon = DoubleSkinFacadeGenerator._offset_footprint_outward(polygon, gap_m)
        total_height = max(1e-6, floor_count * floor_height)

        if gap_profile is None:
            outer_mesh = MeshBuilder.extrude_polygon(
                outer_polygon, base_z, total_height, name="double_skin_outer_glass",
            )
        else:
            zs = [base_z + idx * floor_height for idx in range(floor_count + 1)]
            gaps = [gap_profile(z - base_z) for z in zs]
            outer_mesh = DoubleSkinFacadeGenerator._build_lofted_shell(
                polygon, zs, gaps, name="double_skin_outer_glass",
            )

        mesh_parts = [outer_mesh]

        ring = outer_polygon.closed_ring()[:-1]
        n_edges = len(ring)

        for floor_idx in range(floor_count + 1):
            z = base_z + floor_idx * floor_height
            if gap_profile is None:
                floor_ring = ring
            else:
                floor_ring = DoubleSkinFacadeGenerator._offset_footprint_outward(
                    polygon, gap_profile(z - base_z),
                ).closed_ring()[:-1]
            for i in range(n_edges):
                a, b = floor_ring[i], floor_ring[(i + 1) % n_edges]
                edge_len = a.distance_to(b)
                if edge_len < 1e-6:
                    continue
                mx, my = (a.x + b.x) / 2.0, (a.y + b.y) / 2.0
                angle = math.atan2(b.y - a.y, b.x - a.x)
                band = MeshBuilder.build_box(
                    width=edge_len, depth=mullion_thickness * 2, height=mullion_thickness,
                    center_x=mx, center_y=my, base_z=z - mullion_thickness / 2.0,
                    name=f"mullion_band_f{floor_idx}_e{i}",
                )
                mesh_parts.append(_rotate_mesh_xy(band, angle, mx, my))

        outer_skin_mesh = MeshMerger.merge(mesh_parts, name="double_skin_outer")

        shading_fin_mesh = None
        fin_count = 0
        if add_shading_fins:
            fin_parts: list[Mesh3D] = []
            for i in range(n_edges):
                a, b = ring[i], ring[(i + 1) % n_edges]
                edge_len = a.distance_to(b)
                if edge_len < 1e-6:
                    continue
                n_fins_this_edge = max(1, int(edge_len / max(0.1, mullion_spacing_m)))
                angle = math.atan2(b.y - a.y, b.x - a.x)
                for k in range(0, n_fins_this_edge, max(1, fin_every_nth_mullion)):
                    t = (k + 0.5) / n_fins_this_edge
                    fx, fy = a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t
                    fin = MeshBuilder.build_box(
                        width=fin_thickness_m, depth=fin_depth_m, height=total_height,
                        center_x=fx, center_y=fy, base_z=base_z,
                        name=f"shading_fin_e{i}_{k}",
                    )
                    fin_parts.append(_rotate_mesh_xy(fin, angle, fx, fy))
                    fin_count += 1
            if fin_parts:
                shading_fin_mesh = MeshMerger.merge(fin_parts, name="double_skin_fins")

        return DoubleSkinFacade(
            outer_skin_mesh=outer_skin_mesh, shading_fin_mesh=shading_fin_mesh,
            gap_m=gap_m, fin_count=fin_count,
        )


def _rotate_mesh_xy(mesh: Mesh3D, angle_rad: float, pivot_x: float, pivot_y: float) -> Mesh3D:
    """Bir mesh'i XY duzleminde `pivot` etrafinda `angle_rad` kadar
    dondurur (yalnizca konum, normaller de ayni aciyla dondurulur).
    `MeshBuilder.build_box` eksen-hizali urettigi icin, mullion/fin
    kutularini duvar kenarinin yonune hizalamak icin kullanilir."""
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    new_vertices = []
    for v in mesh.vertices:
        dx, dy = v.x - pivot_x, v.y - pivot_y
        rx = dx * cos_a - dy * sin_a
        ry = dx * sin_a + dy * cos_a
        new_normal = v.normal
        if v.normal is not None:
            nx, ny = v.normal[0], v.normal[1]
            nrx = nx * cos_a - ny * sin_a
            nry = nx * sin_a + ny * cos_a
            new_normal = (nrx, nry, v.normal[2])
        new_vertices.append(type(v)(
            pivot_x + rx, pivot_y + ry, v.z,
            normal=new_normal, tangent=v.tangent, uv=v.uv,
        ))
    return Mesh3D(vertices=new_vertices, triangles=list(mesh.triangles), uvs=list(mesh.uvs), name=mesh.name)
