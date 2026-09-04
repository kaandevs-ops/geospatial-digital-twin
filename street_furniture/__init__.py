"""
Street Furniture
==================

ROADMAP_V5 M2.5 - "Bina dışı meshler (yeni kapsam alanı)":

    "Altyapı/mobilya: sokak lambası, elektrik direği, çöp kutusu, otobüs
    durağı, bank - `vegetation` modülüne paralel yeni bir
    `street_furniture` alt modülü (OSM'de `amenity`, `highway=street_lamp`
    gibi tag'lerle beslenebilir)."

`vegetation/scatter.py` ile aynı desen izlenir: her eleman tipi için basit
parametrik bir mesh üretici + OSM tag -> tip eşlemesi yapan bir dispatch
sınıfı. stdlib-only prensibi korunur (yalnız `mesh_engine` primitifleri:
box/cylinder kullanılır, harici model/asset dosyası yok).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import Enum

from ..core_engine.geometry_engine import Point2D
from ..mesh_engine import Mesh3D, MeshBuilder, MeshMerger, Vertex3D


class StreetFurnitureType(str, Enum):
    STREET_LAMP = "street_lamp"
    POWER_POLE = "power_pole"
    TRASH_BIN = "trash_bin"
    BUS_STOP = "bus_stop"
    BENCH = "bench"
    # ROADMAP_V8 Faz 2.1/2.3/2.5 — B1'in daha önce hiç eklenmemiş kalan
    # nokta-tabanlı alt kategorileri (kentsel mobilya + ulaşım + anıt).
    DRINKING_WATER = "drinking_water"
    FOUNTAIN = "fountain"
    BICYCLE_RENTAL = "bicycle_rental"
    BICYCLE_PARKING = "bicycle_parking"
    TAXI_STAND = "taxi_stand"
    TRAFFIC_SIGNALS = "traffic_signals"
    PEDESTRIAN_CROSSING = "pedestrian_crossing"
    TRAFFIC_SIGN = "traffic_sign"
    RAILWAY_STATION = "railway_station"
    SUBWAY_ENTRANCE = "subway_entrance"
    MONUMENT = "monument"
    ARTWORK = "artwork"


# OSM tag (key=value string) -> StreetFurnitureType. Roadmap M2.5: "OSM'de
# `amenity`, `highway=street_lamp` gibi tag'lerle beslenebilir".
OSM_TAG_MAP: dict[str, StreetFurnitureType] = {
    "highway=street_lamp": StreetFurnitureType.STREET_LAMP,
    "power=pole": StreetFurnitureType.POWER_POLE,
    "amenity=waste_basket": StreetFurnitureType.TRASH_BIN,
    "amenity=bus_station": StreetFurnitureType.BUS_STOP,
    "highway=bus_stop": StreetFurnitureType.BUS_STOP,
    "amenity=bench": StreetFurnitureType.BENCH,
    # ROADMAP_V8 Faz 2.1 — ulaşım kategorilerinin tamamlanması.
    "amenity=bicycle_parking": StreetFurnitureType.BICYCLE_PARKING,
    "amenity=taxi": StreetFurnitureType.TAXI_STAND,
    "highway=traffic_signals": StreetFurnitureType.TRAFFIC_SIGNALS,
    "highway=crossing": StreetFurnitureType.PEDESTRIAN_CROSSING,
    "railway=station": StreetFurnitureType.RAILWAY_STATION,
    "railway=subway_entrance": StreetFurnitureType.SUBWAY_ENTRANCE,
    # ROADMAP_V8 Faz 2.3 — kentsel mobilyanın tamamlanması.
    "amenity=drinking_water": StreetFurnitureType.DRINKING_WATER,
    "amenity=fountain": StreetFurnitureType.FOUNTAIN,
    "amenity=bicycle_rental": StreetFurnitureType.BICYCLE_RENTAL,
    # `traffic_sign=*` B1'in kendi notuyla "düşük öncelik, opsiyonel" —
    # DEFAULT_CATEGORIES dışında tutulur (bkz. osm_client.OPTIONAL_CATEGORIES)
    # ama tip eşlemesi burada zaten hazır olsun diye tanımlı.
    "traffic_sign=yes": StreetFurnitureType.TRAFFIC_SIGN,
    # ROADMAP_V8 Faz 2.5 — anıt/heykel kategorisi.
    "historic=monument": StreetFurnitureType.MONUMENT,
    "tourism=artwork": StreetFurnitureType.ARTWORK,
}


# ROADMAP_V8 Faz 5.6 — Prosedürel prop çeşitliliği (rastgelelik).
#
# B2'nin ilkesi: "parametrik: yükseklik, ölçek varyasyonu, rastgele
# rotasyon - tek tip görünmesin diye". Konum bazlı deterministik (md5
# tabanlı, `PYTHONHASHSEED`'den bağımsız - `hash()` yerine `hashlib`
# kullanılır ki aynı konum her çalıştırmada aynı varyasyonu üretsin,
# tekrarlanabilirlik korunur) bir ±%12.5 ölçek + 0-360° serbest Z
# rotasyonu uygulanır.
def _variation_params(position: Point2D, ftype: StreetFurnitureType) -> tuple[float, float]:
    key = f"{ftype.value}:{position.x:.4f}:{position.y:.4f}".encode()
    digest = hashlib.md5(key).hexdigest()
    n1 = int(digest[:8], 16) / 0xFFFFFFFF
    n2 = int(digest[8:16], 16) / 0xFFFFFFFF
    scale = 1.0 + (n1 - 0.5) * 0.25  # [0.875, 1.125] -> +/- %12.5
    rotation_deg = n2 * 360.0
    return scale, rotation_deg


def _apply_variation(
    mesh: Mesh3D,
    position: Point2D,
    ground_z: float,
    scale: float,
    rotation_deg: float,
) -> Mesh3D:
    """`mesh`'i `(position.x, position.y, ground_z)` pivot noktası etrafında
    tekdüze ölçekler ve Z ekseninde döndürür. `scale==1.0 and rotation_deg==0.0`
    durumunda mesh değişmeden döner (no-op fast path)."""
    if scale == 1.0 and rotation_deg == 0.0:
        return mesh
    rad = math.radians(rotation_deg)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    new_vertices: list[Vertex3D] = []
    for v in mesh.vertices:
        lx = (v.x - position.x) * scale
        ly = (v.y - position.y) * scale
        lz = (v.z - ground_z) * scale
        rx = lx * cos_r - ly * sin_r
        ry = lx * sin_r + ly * cos_r
        new_normal = v.normal
        if new_normal is not None:
            nx, ny, nz = new_normal
            new_normal = (nx * cos_r - ny * sin_r, nx * sin_r + ny * cos_r, nz)
        new_vertices.append(
            Vertex3D(
                rx + position.x,
                ry + position.y,
                lz + ground_z,
                new_normal,
                v.tangent,
                v.uv,
            )
        )
    return Mesh3D(
        vertices=new_vertices, triangles=list(mesh.triangles), uvs=list(mesh.uvs), name=mesh.name
    )


@dataclass(slots=True)
class StreetFurnitureItem:
    furniture_type: StreetFurnitureType
    position: Point2D
    rotation_deg: float = 0.0
    ground_z: float = 0.0


class StreetFurnitureGenerator:
    """`StreetFurnitureType` (+ konum) -> `Mesh3D`. Her eleman tek bir
    statik parametrik mesh'tir (instancing/batching `mesh_engine.batching`
    katmanına bırakılır - roadmap V5 M1.2'de zaten genelleştirildi, bu
    modül yalnız *tek eleman geometrisini* tanımlar)."""

    @staticmethod
    def from_osm_tag(
        tag: str, position: Point2D, ground_z: float = 0.0
    ) -> StreetFurnitureItem | None:
        """`"amenity=bench"` gibi bir OSM tag string'ini `StreetFurnitureItem`'a
        çevirir. Bilinmeyen/eşlenmemiş tag'ler için `None` döner - çağıran
        taraf bunu sessizce atlamalı (roadmap'in "eksik veri" felsefesiyle
        tutarlı: tanınmayan OSM elemanları sahneyi bozmamalı)."""
        ftype = OSM_TAG_MAP.get(tag)
        if ftype is None:
            return None
        return StreetFurnitureItem(furniture_type=ftype, position=position, ground_z=ground_z)

    @staticmethod
    def generate(item: StreetFurnitureItem, apply_variation: bool = True) -> Mesh3D:
        """`item` -> `Mesh3D`. ROADMAP_V8 Faz 5.6: `apply_variation=True`
        (varsayılan) iken konum-tabanlı deterministik ±%12.5 ölçek + serbest
        Z-rotasyonu uygulanır (aynı yan yana dizilmiş bank/direk dizisi
        "kopyala-yapıştır" hissi vermesin diye). Eski çağrı şekli
        (`generate(item)`) hâlâ çalışır - imza geriye dönük uyumlu, yalnızca
        varsayılan çıktı artık varyasyonlu (performans-kritik/karşılaştırma
        senaryoları için `apply_variation=False` ile eski davranış korunur).
        """
        dispatch = {
            StreetFurnitureType.STREET_LAMP: StreetFurnitureGenerator.street_lamp,
            StreetFurnitureType.POWER_POLE: StreetFurnitureGenerator.power_pole,
            StreetFurnitureType.TRASH_BIN: StreetFurnitureGenerator.trash_bin,
            StreetFurnitureType.BUS_STOP: StreetFurnitureGenerator.bus_stop,
            StreetFurnitureType.BENCH: StreetFurnitureGenerator.bench,
            StreetFurnitureType.DRINKING_WATER: StreetFurnitureGenerator.drinking_water,
            StreetFurnitureType.FOUNTAIN: StreetFurnitureGenerator.fountain,
            StreetFurnitureType.BICYCLE_RENTAL: StreetFurnitureGenerator.bicycle_rental,
            StreetFurnitureType.BICYCLE_PARKING: StreetFurnitureGenerator.bicycle_parking,
            StreetFurnitureType.TAXI_STAND: StreetFurnitureGenerator.taxi_stand,
            StreetFurnitureType.TRAFFIC_SIGNALS: StreetFurnitureGenerator.traffic_signals,
            StreetFurnitureType.PEDESTRIAN_CROSSING: StreetFurnitureGenerator.pedestrian_crossing,
            StreetFurnitureType.TRAFFIC_SIGN: StreetFurnitureGenerator.traffic_sign,
            StreetFurnitureType.RAILWAY_STATION: StreetFurnitureGenerator.railway_station,
            StreetFurnitureType.SUBWAY_ENTRANCE: StreetFurnitureGenerator.subway_entrance,
            StreetFurnitureType.MONUMENT: StreetFurnitureGenerator.monument,
            StreetFurnitureType.ARTWORK: StreetFurnitureGenerator.artwork,
        }
        mesh = dispatch[item.furniture_type](item.position, item.ground_z)
        if not apply_variation:
            return mesh
        scale, rotation_deg = _variation_params(item.position, item.furniture_type)
        # item.rotation_deg (OSM'den gelen açık yön bilgisi, örn. bench/sign)
        # varsa varyasyon rotasyonuna eklenir; şu an üreticiler bunu
        # kullanmıyor olsa da gelecekteki yönlendirilebilir tipler için korunur.
        return _apply_variation(
            mesh, item.position, item.ground_z, scale, rotation_deg + item.rotation_deg
        )

    @staticmethod
    def street_lamp(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        pole = MeshBuilder.build_cylinder(
            radius=0.06,
            height=4.5,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=8,
            name="street_lamp_pole",
        )
        head = MeshBuilder.build_box(
            0.3,
            0.3,
            0.25,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 4.5,
            name="street_lamp_head",
        )
        return MeshMerger.merge([pole, head], name="street_lamp")

    @staticmethod
    def power_pole(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        pole = MeshBuilder.build_cylinder(
            radius=0.12,
            height=8.0,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=8,
            name="power_pole_shaft",
        )
        crossarm = MeshBuilder.build_box(
            1.8,
            0.1,
            0.1,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 7.5,
            name="power_pole_crossarm",
        )
        return MeshMerger.merge([pole, crossarm], name="power_pole")

    @staticmethod
    def trash_bin(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        return MeshBuilder.build_cylinder(
            radius=0.25,
            height=0.75,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=10,
            name="trash_bin",
        )

    @staticmethod
    def bench(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        seat = MeshBuilder.build_box(
            1.6,
            0.45,
            0.05,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 0.45,
            name="bench_seat",
        )
        leg_a = MeshBuilder.build_box(
            0.06,
            0.45,
            0.45,
            center_x=position.x - 0.7,
            center_y=position.y,
            base_z=ground_z,
            name="bench_leg_a",
        )
        leg_b = MeshBuilder.build_box(
            0.06,
            0.45,
            0.45,
            center_x=position.x + 0.7,
            center_y=position.y,
            base_z=ground_z,
            name="bench_leg_b",
        )
        return MeshMerger.merge([seat, leg_a, leg_b], name="bench")

    @staticmethod
    def bus_stop(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        roof = MeshBuilder.build_box(
            3.0,
            1.2,
            0.08,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 2.2,
            name="bus_stop_roof",
        )
        back_wall = MeshBuilder.build_box(
            3.0,
            0.05,
            2.2,
            center_x=position.x,
            center_y=position.y - 0.55,
            base_z=ground_z,
            name="bus_stop_wall",
        )
        bench = StreetFurnitureGenerator.bench(
            Point2D(position.x, position.y - 0.3),
            ground_z=ground_z,
        )
        return MeshMerger.merge([roof, back_wall, bench], name="bus_stop")

    # ------------------------------------------------------------------ #
    # ROADMAP_V8 Faz 2.1/2.3/2.5 — yeni primitifler (mevcut basit
    # box/cylinder primitif felsefesiyle tutarlı, yeni asset/harici model
    # gerektirmez).
    # ------------------------------------------------------------------ #

    @staticmethod
    def drinking_water(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        column = MeshBuilder.build_cylinder(
            radius=0.1,
            height=0.9,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=8,
            name="drinking_water_column",
        )
        basin = MeshBuilder.build_cylinder(
            radius=0.22,
            height=0.08,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 0.85,
            segments=10,
            name="drinking_water_basin",
        )
        return MeshMerger.merge([column, basin], name="drinking_water")

    @staticmethod
    def fountain(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        basin = MeshBuilder.build_cylinder(
            radius=1.5,
            height=0.4,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=16,
            name="fountain_basin",
        )
        water = MeshBuilder.build_cylinder(
            radius=1.3,
            height=0.05,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 0.4,
            segments=16,
            name="fountain_water_surface",
        )
        centerpiece = MeshBuilder.build_cylinder(
            radius=0.15,
            height=1.2,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 0.4,
            segments=8,
            name="fountain_centerpiece",
        )
        return MeshMerger.merge([basin, water, centerpiece], name="fountain")

    @staticmethod
    def bicycle_rental(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        stand = MeshBuilder.build_box(
            2.4,
            0.6,
            0.9,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            name="bicycle_rental_stand",
        )
        kiosk = MeshBuilder.build_box(
            0.6,
            0.4,
            1.3,
            center_x=position.x - 1.4,
            center_y=position.y,
            base_z=ground_z,
            name="bicycle_rental_kiosk",
        )
        return MeshMerger.merge([stand, kiosk], name="bicycle_rental")

    @staticmethod
    def bicycle_parking(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        rail_a = MeshBuilder.build_box(
            1.6,
            0.04,
            0.6,
            center_x=position.x,
            center_y=position.y - 0.3,
            base_z=ground_z,
            name="bicycle_parking_rail_a",
        )
        rail_b = MeshBuilder.build_box(
            1.6,
            0.04,
            0.6,
            center_x=position.x,
            center_y=position.y + 0.3,
            base_z=ground_z,
            name="bicycle_parking_rail_b",
        )
        return MeshMerger.merge([rail_a, rail_b], name="bicycle_parking")

    @staticmethod
    def taxi_stand(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        pole = MeshBuilder.build_cylinder(
            radius=0.05,
            height=2.2,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=8,
            name="taxi_stand_pole",
        )
        sign = MeshBuilder.build_box(
            0.4,
            0.08,
            0.3,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 2.0,
            name="taxi_stand_sign",
        )
        return MeshMerger.merge([pole, sign], name="taxi_stand")

    @staticmethod
    def traffic_signals(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        pole = MeshBuilder.build_cylinder(
            radius=0.06,
            height=2.8,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=8,
            name="traffic_signals_pole",
        )
        head = MeshBuilder.build_box(
            0.25,
            0.25,
            0.7,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 2.4,
            name="traffic_signals_head",
        )
        return MeshMerger.merge([pole, head], name="traffic_signals")

    @staticmethod
    def pedestrian_crossing(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        # Basit yaya geçidi doku yaması — B1'in "kesişimde yaya geçidi
        # doku yaması" isteğinin minimum viable karşılığı: ince, geniş bir
        # zemin yaması (gerçek zebra deseni malzeme/doku katmanına ait).
        return MeshBuilder.build_box(
            3.0,
            2.0,
            0.02,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            name="pedestrian_crossing_patch",
        )

    @staticmethod
    def traffic_sign(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        pole = MeshBuilder.build_cylinder(
            radius=0.04,
            height=2.0,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=6,
            name="traffic_sign_pole",
        )
        plate = MeshBuilder.build_box(
            0.5,
            0.05,
            0.5,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 1.7,
            name="traffic_sign_plate",
        )
        return MeshMerger.merge([pole, plate], name="traffic_sign")

    @staticmethod
    def railway_station(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        # B1: "istasyon -> basit kutu hacim + tabela primitifi".
        building = MeshBuilder.build_box(
            8.0,
            4.0,
            3.5,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            name="railway_station_building",
        )
        canopy = MeshBuilder.build_box(
            9.0,
            5.0,
            0.15,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 3.5,
            name="railway_station_canopy",
        )
        sign = MeshBuilder.build_box(
            1.2,
            0.08,
            0.5,
            center_x=position.x,
            center_y=position.y - 2.5,
            base_z=ground_z + 3.6,
            name="railway_station_sign",
        )
        return MeshMerger.merge([building, canopy, sign], name="railway_station")

    @staticmethod
    def subway_entrance(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        canopy = MeshBuilder.build_box(
            2.5,
            2.0,
            0.15,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 2.2,
            name="subway_entrance_canopy",
        )
        posts = [
            MeshBuilder.build_cylinder(
                radius=0.08,
                height=2.2,
                center_x=position.x + dx,
                center_y=position.y + dy,
                base_z=ground_z,
                segments=6,
                name="subway_entrance_post",
            )
            for dx, dy in ((-1.1, -0.9), (1.1, -0.9), (-1.1, 0.9), (1.1, 0.9))
        ]
        return MeshMerger.merge([canopy, *posts], name="subway_entrance")

    @staticmethod
    def monument(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        base = MeshBuilder.build_box(
            1.4,
            1.4,
            0.5,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            name="monument_base",
        )
        obelisk = MeshBuilder.build_box(
            0.5,
            0.5,
            3.5,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 0.5,
            name="monument_obelisk",
        )
        return MeshMerger.merge([base, obelisk], name="monument")

    @staticmethod
    def artwork(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        base = MeshBuilder.build_cylinder(
            radius=0.6,
            height=0.4,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            segments=12,
            name="artwork_base",
        )
        sculpture = MeshBuilder.build_cylinder(
            radius=0.25,
            height=2.0,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 0.4,
            segments=10,
            name="artwork_sculpture",
        )
        return MeshMerger.merge([base, sculpture], name="artwork")

    @staticmethod
    def generate_batch(items: list[StreetFurnitureItem]) -> Mesh3D:
        """Bir mahalle ölçeğindeki tüm sokak mobilyası öğelerini tek bir
        mesh'te birleştirir - roadmap M2.5 kabul kriteri: "bina dışı en az
        4 kategori (yol/köprü/su/mobilya) otomatik üretilip sahneye
        yerleştirilmeli" maddesinin "mobilya" ayağı."""
        if not items:
            return Mesh3D(vertices=[], triangles=[], name="street_furniture_batch")
        meshes = [StreetFurnitureGenerator.generate(item) for item in items]
        return MeshMerger.merge(meshes, name="street_furniture_batch")


# ROADMAP_V7.md Faz C3 (3. dilim) — OSM kategori köprüsü, bu modülün
# tanımları hazır olduktan sonra en altta import edilir (döngüsel importu
# önlemek için `osm_bridge` bu paketten `StreetFurnitureGenerator`/
# `StreetFurnitureItem`'ı `from . import ...` ile geriye alıyor).
from .osm_bridge import (  # noqa: E402
    CATEGORY_KEY_TO_OSM_TAG,
    furniture_item_from_point,
    generate_street_furniture_for_collection,
)
