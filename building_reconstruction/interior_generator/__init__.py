"""
Interior Generator
===================

Roadmap Faz 2 (iç mekan derinleştirme).

Önceki durum: `RoomGenerator` odaları, `StairGenerator`/`ElevatorCoreGenerator`
merdiven/asansör *verisini* (konum, ölçü) üretiyordu, ama hiçbiri gerçek 3D
geometriye dönüşmüyordu; `Floor` sadece `Point2D` konumları saklıyordu ve
`Building.full_mesh()` yalnızca cephe + çatıyı birleştiriyordu - iç mekan
tamamen görünmezdi.

Bu modül dört şeyi gerçek mesh'e çevirir:
  - `InteriorWallBuilder`  : oda sınırlarından, yalnızca *iç* (dış cepheye
    denk gelmeyen) kenarlarda ince bölme duvarları, iç kapı boşlukları
    kesilerek.
  - `StairMeshBuilder`     : `Stair` veri modelinden gerçek basamak basamak
    3D merdiven mesh'i.
  - `ElevatorShaftMeshBuilder` : `ElevatorCore` veri modelinden, taban-tavan
    arası içi boş bir asansör kuyusu (4 duvar) mesh'i.
  - `FurnitureGenerator`   : sabit iç mekan donatıları (yangın söndürücü,
    tabela, çöp kutusu, masa/sandalye blok-model) - `FurnitureItem` listesi
    + karşılık gelen kutu/silindir mesh'leri.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ...core_engine.geometry_engine import Point2D, Polygon
from ...mesh_engine import Mesh3D, MeshBuilder, MeshMerger, WallOpening, WallOpeningMeshBuilder
from ..building_elements import Door, ElevatorCore, Stair
from ..room_generator import Room, RoomType

# ============================================================================ #
# Interior Wall Builder
# ============================================================================ #

_INTERIOR_WALL_THICKNESS = 0.10
_INTERIOR_WALL_HEIGHT_MARGIN = 0.02  # kat yüksekliğinden hafif düşük (tavan boşluğu)


class InteriorWallBuilder:
    """Roadmap Faz 2: oda poligonlarından gerçek bölme duvarı mesh'i."""

    @staticmethod
    def build_floor_walls(
        rooms: list[Room],
        footprint_polygon: Polygon,
        base_z: float,
        floor_height: float,
        interior_doors: list[Door] | None = None,
        wall_thickness: float = _INTERIOR_WALL_THICKNESS,
        name_prefix: str = "interior_wall",
    ) -> Mesh3D:
        """Her odanın her kenarı için: kenar dış footprint sınırına denk
        geliyorsa atla (cephe zaten `FacadeGenerator` tarafından üretiliyor),
        değilse ince bir bölme duvarı ekle. Aynı kenar iki komşu odada da
        üretileceğinden basit bir "görülen kenar" anahtar kümesiyle
        çiftlenme (duplicate) önlenir. İç kapı konumuna denk gelen
        kenarlarda kapı boşluğu kesilir.
        """
        interior_doors = interior_doors or []
        boundary_edges = _footprint_edge_set(footprint_polygon)
        seen_edges: set[tuple[tuple[float, float], tuple[float, float]]] = set()
        wall_height = max(0.1, floor_height - _INTERIOR_WALL_HEIGHT_MARGIN)

        segments: list[Mesh3D] = []
        wall_id = 0
        for room in rooms:
            ring = room.polygon.closed_ring()
            for a, b in zip(ring, ring[1:]):
                edge_key = _edge_key(a, b)
                if edge_key in boundary_edges or edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)

                openings = _door_openings_on_edge(a, b, interior_doors, wall_height)
                wall_mesh = WallOpeningMeshBuilder.build_wall_segment(
                    a,
                    b,
                    base_z,
                    wall_height,
                    openings,
                    thickness=wall_thickness,
                    name=f"{name_prefix}_{wall_id}",
                )
                wall_id += 1
                if wall_mesh.triangle_count() > 0:
                    segments.append(wall_mesh)

        if not segments:
            return Mesh3D(name=name_prefix)
        return MeshMerger.merge(segments, name=name_prefix)


def _edge_key(
    a: Point2D, b: Point2D, eps: float = 1e-4
) -> tuple[tuple[float, float], tuple[float, float]]:
    pa = (round(a.x / eps), round(a.y / eps))
    pb = (round(b.x / eps), round(b.y / eps))
    return (pa, pb) if pa <= pb else (pb, pa)


def _footprint_edge_set(polygon: Polygon) -> set[tuple[tuple[float, float], tuple[float, float]]]:
    ring = polygon.closed_ring()
    return {_edge_key(a, b) for a, b in zip(ring, ring[1:])}


def _door_openings_on_edge(
    a: Point2D,
    b: Point2D,
    doors: list[Door],
    wall_height: float,
    tolerance: float = 0.35,
) -> list[WallOpening]:
    """Bir kapı konumu, verilen duvar kenarına (segmente) yeterince
    yakınsa (dik mesafe < tolerance) o kenarda bir kapı boşluğu açılır."""
    length = a.distance_to(b)
    if length < 1e-9:
        return []
    dx, dy = (b.x - a.x) / length, (b.y - a.y) / length
    openings: list[WallOpening] = []
    for door in doors:
        px, py = door.position.x - a.x, door.position.y - a.y
        u = px * dx + py * dy  # a'dan itibaren kenar boyunca izdüşüm
        perp = abs(px * dy - py * dx)  # kenara dik mesafe
        if -0.05 <= u <= length + 0.05 and perp < tolerance:
            u_clamped = max(door.width / 2.0, min(length - door.width / 2.0, u))
            openings.append(
                WallOpening(
                    u_start=u_clamped - door.width / 2.0,
                    u_end=u_clamped + door.width / 2.0,
                    v_start=0.0,
                    v_end=min(2.1, wall_height),
                    kind="door",
                )
            )
    return openings


# ============================================================================ #
# Stair Mesh Builder
# ============================================================================ #


class StairMeshBuilder:
    """Roadmap Faz 2: `Stair` veri modelinden gerçek basamak-basamak 3D mesh."""

    @staticmethod
    def build(stair: Stair, base_z: float) -> Mesh3D:
        rad = math.radians(stair.rotation_deg)
        run_dx, run_dy = math.cos(rad), math.sin(rad)  # merdiven ilerleme yönü
        # basamak genişliği ekseni, ilerleme yönüne dik
        perp_dx, perp_dy = -run_dy, run_dx

        steps: list[Mesh3D] = []
        for i in range(stair.step_count):
            step_z = base_z + i * stair.step_height
            # kümülatif basamak derinliği ortası (her basamak bir öncekinin
            # üstüne, ilerleme yönünde step_depth kadar öteye biner)
            center_offset = (i + 0.5) * stair.step_depth
            cx = stair.position.x + run_dx * center_offset
            cy = stair.position.y + run_dy * center_offset
            box = _build_rotated_box(
                width_along_run=stair.step_depth,
                width_across=stair.width,
                height=stair.step_height + (i * 0.0),  # her basamak kendi rise'ı kadar yüksek
                center_x=cx,
                center_y=cy,
                base_z=base_z,
                run_dx=run_dx,
                run_dy=run_dy,
                top_z=step_z + stair.step_height,
                name=f"stair_step_{i}",
            )
            steps.append(box)
        if not steps:
            return Mesh3D(name="stair")
        return MeshMerger.merge(steps, name="stair")


def _build_rotated_box(
    width_along_run: float,
    width_across: float,
    height: float,
    center_x: float,
    center_y: float,
    base_z: float,
    top_z: float,
    run_dx: float,
    run_dy: float,
    name: str,
) -> Mesh3D:
    """Yükselen basamak kutusu: taban `base_z`den, üst yüzü `top_z`'de,
    ilerleme yönüne göre döndürülmüş dikdörtgen taban."""
    hl, hw = width_along_run / 2.0, width_across / 2.0
    perp_dx, perp_dy = -run_dy, run_dx
    corners = [
        Point2D(center_x - run_dx * hl - perp_dx * hw, center_y - run_dy * hl - perp_dy * hw),
        Point2D(center_x + run_dx * hl - perp_dx * hw, center_y + run_dy * hl - perp_dy * hw),
        Point2D(center_x + run_dx * hl + perp_dx * hw, center_y + run_dy * hl + perp_dy * hw),
        Point2D(center_x - run_dx * hl + perp_dx * hw, center_y - run_dy * hl + perp_dy * hw),
    ]
    return MeshBuilder.extrude_polygon(
        Polygon(corners), base_z, max(0.02, top_z - base_z), name=name
    )


# ============================================================================ #
# Elevator Shaft Mesh Builder
# ============================================================================ #


class ElevatorShaftMeshBuilder:
    """Roadmap Faz 2: `ElevatorCore` veri modelinden taban-tavan arası içi
    boş asansör kuyusu (4 duvar panel) + her katta kabin kapısı boşluğu."""

    @staticmethod
    def build(
        core: ElevatorCore,
        floor_height: float,
        floor_count: int,
        wall_thickness: float = 0.15,
        door_width: float = 1.1,
        door_height: float = 2.1,
        name: str = "elevator_shaft",
    ) -> Mesh3D:
        hw, hd = core.width / 2.0, core.depth / 2.0
        cx, cy = core.position.x, core.position.y
        ring = [
            Point2D(cx - hw, cy - hd),
            Point2D(cx + hw, cy - hd),
            Point2D(cx + hw, cy + hd),
            Point2D(cx - hw, cy + hd),
        ]
        shaft_height = core.shaft_top_z - core.shaft_bottom_z
        panels: list[Mesh3D] = []
        for i, (a, b) in enumerate(zip(ring, ring[1:] + ring[:1])):
            # kabin kapısı önyüzü (ilk kenar, +x yönüne bakan) her katta kesilir
            openings: list[WallOpening] = []
            if i == 0:
                length = a.distance_to(b)
                for f in range(max(1, floor_count)):
                    v0 = f * floor_height
                    openings.append(
                        WallOpening(
                            u_start=length / 2.0 - door_width / 2.0,
                            u_end=length / 2.0 + door_width / 2.0,
                            v_start=v0,
                            v_end=min(v0 + door_height, shaft_height),
                            kind="door",
                        )
                    )
            wall = WallOpeningMeshBuilder.build_wall_segment(
                a,
                b,
                core.shaft_bottom_z,
                shaft_height,
                openings,
                thickness=wall_thickness,
                name=f"{name}_wall_{i}",
            )
            if wall.triangle_count() > 0:
                panels.append(wall)
        if not panels:
            return Mesh3D(name=name)
        return MeshMerger.merge(panels, name=name)


# ============================================================================ #
# Furniture / Fixed Equipment Generator
# ============================================================================ #


class FurnitureKind(str, Enum):
    YANGIN_SONDURUCU = "yangin_sonduruculer"
    ACIL_CIKIS_TABELASI = "acil_cikis_tabelasi"
    COP_KUTUSU = "cop_kutusu"
    MASA = "masa"
    SANDALYE = "sandalye"
    DOLAP = "dolap"
    LAVABO = "lavabo"
    ASANSOR_PANELI = "asansor_paneli"


@dataclass(slots=True)
class FurnitureItem:
    kind: FurnitureKind
    position: Point2D
    base_z: float
    rotation_deg: float = 0.0
    floor_level: int = 0
    room_id: int | None = None


# Basit blok-model boyut tablosu: (genişlik, derinlik, yükseklik) metre.
_FURNITURE_DIMS: dict[FurnitureKind, tuple[float, float, float]] = {
    FurnitureKind.YANGIN_SONDURUCU: (0.15, 0.15, 0.5),
    FurnitureKind.ACIL_CIKIS_TABELASI: (0.3, 0.05, 0.2),
    FurnitureKind.COP_KUTUSU: (0.35, 0.35, 0.6),
    FurnitureKind.MASA: (1.2, 0.7, 0.75),
    FurnitureKind.SANDALYE: (0.45, 0.45, 0.85),
    FurnitureKind.DOLAP: (0.9, 0.5, 2.0),
    FurnitureKind.LAVABO: (0.55, 0.45, 0.85),
    FurnitureKind.ASANSOR_PANELI: (0.2, 0.05, 1.2),
}

_ROOM_FURNITURE: dict[str, list[FurnitureKind]] = {
    RoomType.OFIS.value: [FurnitureKind.MASA, FurnitureKind.SANDALYE, FurnitureKind.DOLAP],
    RoomType.TOPLANTI.value: [FurnitureKind.MASA, FurnitureKind.SANDALYE],
    RoomType.SALON.value: [FurnitureKind.MASA, FurnitureKind.SANDALYE],
    RoomType.MUTFAK.value: [FurnitureKind.DOLAP, FurnitureKind.LAVABO],
    RoomType.WC.value: [FurnitureKind.LAVABO],
    RoomType.KORIDOR.value: [FurnitureKind.YANGIN_SONDURUCU, FurnitureKind.COP_KUTUSU],
}


class FurnitureGenerator:
    """Roadmap Faz 2: 'sabit donatı' (yangın söndürücü, tabela, temel
    mobilya) yerleşimi - oda tipine göre kural tabanlı, deterministik."""

    @staticmethod
    def place_for_floor(
        rooms: list[Room],
        base_z: float,
        floor_level: int,
        stairs: list[Stair] | None = None,
        exterior_door: Door | None = None,
    ) -> list[FurnitureItem]:
        items: list[FurnitureItem] = []
        for room in rooms:
            kinds = _ROOM_FURNITURE.get(room.room_type, [])
            if not kinds:
                continue
            center = _room_center(room.polygon)
            xs = [p.x for p in room.polygon.points]
            ys = [p.y for p in room.polygon.points]
            w = max(xs) - min(xs)
            d = max(ys) - min(ys)
            for i, kind in enumerate(kinds):
                # basit dağıtım: oda merkezine yakın küçük ofsetlerle (üst üste binmeyi azaltmak için)
                offset_x = (i - (len(kinds) - 1) / 2.0) * min(0.6, w * 0.2)
                offset_y = (i % 2) * min(0.6, d * 0.2)
                pos = Point2D(center.x + offset_x, center.y + offset_y)
                items.append(
                    FurnitureItem(
                        kind=kind,
                        position=pos,
                        base_z=base_z,
                        floor_level=floor_level,
                        room_id=room.room_id,
                    )
                )

        # Kat başına en az bir yangın söndürücü + acil çıkış tabelası:
        # merdiven başına ve (varsa) zemin kattaki ana girişin üstüne.
        for stair in stairs or []:
            items.append(
                FurnitureItem(
                    kind=FurnitureKind.YANGIN_SONDURUCU,
                    position=stair.position,
                    base_z=base_z,
                    floor_level=floor_level,
                )
            )
        if exterior_door is not None:
            items.append(
                FurnitureItem(
                    kind=FurnitureKind.ACIL_CIKIS_TABELASI,
                    position=exterior_door.position,
                    base_z=base_z + 2.1,
                    floor_level=floor_level,
                )
            )
        return items

    @staticmethod
    def build_mesh(items: list[FurnitureItem], name: str = "furniture") -> Mesh3D:
        """Her `FurnitureItem`'ı basit kutu/silindir blok-model olarak
        gerçek mesh'e çevirir (detaylı sanat asseti değil, doğru konum/ölçü/
        etiket taşıyan yer tutucu geometri - export/oyun motoru pipeline'ında
        gerçek modellerle değiştirilebilir)."""
        parts: list[Mesh3D] = []
        for idx, item in enumerate(items):
            w, d, h = _FURNITURE_DIMS.get(item.kind, (0.4, 0.4, 0.8))
            if item.kind == FurnitureKind.YANGIN_SONDURUCU:
                mesh = MeshBuilder.build_cylinder(
                    radius=w / 2.0,
                    height=h,
                    center_x=item.position.x,
                    center_y=item.position.y,
                    base_z=item.base_z,
                    segments=10,
                    name=f"{item.kind.value}_{idx}",
                )
            else:
                mesh = MeshBuilder.build_box(
                    width=w,
                    depth=d,
                    height=h,
                    center_x=item.position.x,
                    center_y=item.position.y,
                    base_z=item.base_z,
                    name=f"{item.kind.value}_{idx}",
                )
            parts.append(mesh)
        if not parts:
            return Mesh3D(name=name)
        return MeshMerger.merge(parts, name=name)


def _room_center(polygon: Polygon) -> Point2D:
    xs = [p.x for p in polygon.points]
    ys = [p.y for p in polygon.points]
    return Point2D(sum(xs) / len(xs), sum(ys) / len(ys))
