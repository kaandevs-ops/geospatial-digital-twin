"""
Sport & Recreation
==================

ROADMAP_V7.md Bölüm B1 - "Spor ve rekreasyon":

    "Spor sahası (`leisure=pitch`), stadyum (`leisure=stadium`), yüzme
    havuzu (`leisure=swimming_pool`) - basit hacim + tipik çizgi dokusu.
    Çocuk oyun alanı (`leisure=playground`) - kaydırak/salıncak gibi
    düşük-poly prop kütüphanesi."

B1'in kendi ifadesiyle alan (Polygon) feature'lar (saha/stadyum/havuz)
için mevcut `mesh_engine.MeshBuilder.extrude_polygon` (Faz 2) yeterli -
`editor/osm_bridge.mesh_for_water_area` ile aynı desen (poligon -> ince/
kalın prizma, malzeme/renk ayrımı yalnızca `material` alanıyla belirtilir,
yeni bir extrude algoritması gerekmez). `playground` (Point) için ise
`street_furniture`'daki gibi düşük-poly prop kütüphanesi (kaydırak,
salıncak) eklenir - stdlib-only, harici asset yok.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ..building_reconstruction.building_elements import SetbackFloorGenerator
from ..core_engine.geometry_engine import Point2D, Polygon
from ..mesh_engine import Mesh3D, MeshBuilder, MeshMerger, UVGenerator


class SportAreaType(str, Enum):
    PITCH = "pitch"
    STADIUM = "stadium"
    SWIMMING_POOL = "swimming_pool"


#: B1: "basit hacim" - her alan tipi için extrude yüksekliği/derinliği.
#: Saha: zemin dokusu (neredeyse düz plaka; ROADMAP_V8.md Faz 5.6d ile
#: üstüne çizgi yaması eklendi, bkz. `_pitch_line_markings`). Stadyum:
#: taban kademesinin extrude parametreleri (Faz 5.6d ile tek kutu yerine
#: `_stadium_tiers`'ın ürettiği kademeli tribün kullanılıyor, bu değer
#: yalnızca taban kademesi için referans). Havuz: hafif gömülü (su
#: yüzeyiyle aynı z-fighting önleme mantığı, bkz. `editor/osm_bridge.py`
#: DEFAULT_WATER_DEPTH_OFFSET_M).
AREA_EXTRUDE_PARAMS: dict[SportAreaType, tuple[float, float]] = {
    # (base_z_offset, height) - base_z_offset zeminin altına/üstüne göre.
    SportAreaType.PITCH: (0.0, 0.05),
    SportAreaType.STADIUM: (0.0, 6.0),
    SportAreaType.SWIMMING_POOL: (-1.4, 1.4),
}


@dataclass(slots=True)
class SportAreaItem:
    """Bir alan (Polygon) tipi spor/rekreasyon feature'ı - saha/stadyum/
    havuz. Mesh üretimi `SportRecreationGenerator.generate_area`'ya
    bırakılır (poligon verisi burada saklanır, erken mesh üretimi
    dayatılmaz - önceki köprülerle aynı tasarım kararı)."""

    area_type: SportAreaType
    polygon: Polygon


@dataclass(slots=True)
class PlaygroundItem:
    """Bir `leisure=playground` noktası - kaydırak + salıncak seti."""

    position: Point2D
    rotation_deg: float = 0.0
    ground_z: float = 0.0


class SportRecreationGenerator:
    """OSM spor/rekreasyon feature'larından prosedürel mesh üretimi.

    `street_furniture.StreetFurnitureGenerator` ile aynı desen:
    stdlib-only, yalnızca `mesh_engine` primitifleri (`extrude_polygon`/
    `build_box`/`build_cylinder`) + `MeshMerger`.
    """

    @staticmethod
    def generate_area(item: SportAreaItem) -> Mesh3D:
        """B1: "basit hacim" - `AREA_EXTRUDE_PARAMS`'a göre tek bir
        `extrude_polygon` çağrısı (mevcut `MeshBuilder`, değiştirilmedi).

        ROADMAP_V8.md Faz 5.6d: `PITCH` için üstüne saha çizgisi
        yaması (`_pitch_line_markings`), `STADIUM` için kaba tek kutu
        yerine kademeli tribün yaklaşıklaması (`_stadium_tiers`)
        eklenir; `SWIMMING_POOL` değişmedi (bu fazın kapsamı dışında).
        """
        base_z_offset, height = AREA_EXTRUDE_PARAMS[item.area_type]
        base_mesh = MeshBuilder.extrude_polygon(
            item.polygon,
            base_z=base_z_offset,
            height=height,
            name=f"sport_area_{item.area_type.value}",
        )
        if item.area_type is SportAreaType.PITCH:
            markings = SportRecreationGenerator._pitch_line_markings(
                item.polygon,
                top_z=base_z_offset + height,
            )
            if markings is not None:
                return MeshMerger.merge(
                    [base_mesh, markings], name=f"sport_area_{item.area_type.value}"
                )
            return base_mesh
        if item.area_type is SportAreaType.STADIUM:
            tiers = SportRecreationGenerator._stadium_tiers(item.polygon, base_z_offset)
            if tiers is not None:
                return tiers
            return base_mesh
        return base_mesh

    #: Faz 5.6d: futbol sahası çizgi dokusu için varsayılan çizgi
    #: kalınlığı/yüksekliği - gerçek boya yerine ince, hafif yükseltilmiş
    #: bir şerit mesh'i (B1 "tipik çizgi dokusu" - harici doku/imaj asseti
    #: gerekmeden, stdlib-only ilkesiyle tutarlı geometrik yaklaşıklama).
    PITCH_LINE_WIDTH_M = 0.12
    PITCH_LINE_HEIGHT_M = 0.02

    @staticmethod
    def _pitch_line_markings(polygon: Polygon, top_z: float) -> Mesh3D | None:
        """Saha poligonunun eksen-hizalı sınır kutusunu kullanarak orta
        çizgi + orta yuvarlağın (basit poligonla yaklaşıklanmış) + iki
        ceza sahası dikdörtgeninin ince şerit mesh'lerini üretir.

        Saha çok küçükse (örn. gerçek bir futbol sahası ölçeğinde
        değilse) orantısız/anlamsız çizgiler üretmemek için `None`
        döner (B4 "eksik/çelişkili veri sahneyi bozmasın" ilkesiyle
        tutarlı bir koruma)."""
        min_x, min_y, max_x, max_y = polygon.bounding_box()
        width, depth = max_x - min_x, max_y - min_y
        if width < 4.0 or depth < 4.0:
            return None
        # Uzun kenar boyunca oyun yönü kabul edilir (B1 "yaklaşıklık
        # yeterli" felsefesi - gerçek `sport` tag'inden çizgi planı
        # türetmek kapsam dışı).
        long_is_x = width >= depth
        cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
        lw = SportRecreationGenerator.PITCH_LINE_WIDTH_M
        lh = SportRecreationGenerator.PITCH_LINE_HEIGHT_M
        pieces: list[Mesh3D] = []

        if long_is_x:
            # Orta çizgi: dikey, y boyunca.
            pieces.append(
                MeshBuilder.build_box(
                    lw,
                    depth,
                    lh,
                    center_x=cx,
                    center_y=cy,
                    base_z=top_z,
                    name="pitch_halfway_line",
                )
            )
            circle_r = min(depth * 0.18, width * 0.12)
        else:
            pieces.append(
                MeshBuilder.build_box(
                    width,
                    lw,
                    lh,
                    center_x=cx,
                    center_y=cy,
                    base_z=top_z,
                    name="pitch_halfway_line",
                )
            )
            circle_r = min(width * 0.18, depth * 0.12)

        # Orta yuvarlak: 12 köşeli ince şerit (poligon değil, kısa kutu
        # segmentlerinin halkası - `communication_tower`'ın kafes
        # yaklaşıklaması gibi düşük-poly bir yaklaşım).
        segments = 12
        for i in range(segments):
            a0 = 2 * math.pi * i / segments
            a1 = 2 * math.pi * (i + 1) / segments
            x0, y0 = cx + circle_r * math.cos(a0), cy + circle_r * math.sin(a0)
            x1, y1 = cx + circle_r * math.cos(a1), cy + circle_r * math.sin(a1)
            seg_len = math.hypot(x1 - x0, y1 - y0)
            seg_cx, seg_cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            angle = math.atan2(y1 - y0, x1 - x0)
            seg = MeshBuilder.build_box(
                seg_len,
                lw,
                lh,
                center_x=0.0,
                center_y=0.0,
                base_z=top_z,
                name="pitch_center_circle_segment",
            )
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            for v in seg.vertices:
                rx = v.x * cos_a - v.y * sin_a
                ry = v.x * sin_a + v.y * cos_a
                v.x, v.y = rx + seg_cx, ry + seg_cy
            pieces.append(seg)

        # İki ceza sahası dikdörtgeni (kısa kenarlara yakın, basit
        # oranlarla - gerçek FIFA ölçüleri değil, görsel yaklaşıklık).
        box_depth = min(depth if long_is_x else width, 16.0) * 0.35
        box_width = min(width if long_is_x else depth, 40.0) * 0.55
        for sign in (-1, 1):
            if long_is_x:
                box_cx = cx + sign * (width / 2.0 - box_depth / 2.0)
                pieces.append(
                    MeshBuilder.build_box(
                        box_depth,
                        box_width,
                        lh,
                        center_x=box_cx,
                        center_y=cy,
                        base_z=top_z,
                        name="pitch_penalty_box",
                    )
                )
            else:
                box_cy = cy + sign * (depth / 2.0 - box_depth / 2.0)
                pieces.append(
                    MeshBuilder.build_box(
                        box_width,
                        box_depth,
                        lh,
                        center_x=cx,
                        center_y=box_cy,
                        base_z=top_z,
                        name="pitch_penalty_box",
                    )
                )

        return MeshMerger.merge(pieces, name="pitch_line_markings")

    #: Faz 5.6d: stadyum için kademe sayısı ve kademe başına yükseklik/
    #: içe-ofset (B1 "basit hacim/yaklaşıklama" felsefesiyle tutarlı -
    #: gerçek tribün geometrisi/koltuk sırası kapsam dışı kalmaya devam
    #: eder, yalnızca "kademeli" siluet hissi hedeflenir).
    STADIUM_TIER_COUNT = 3
    STADIUM_TIER_HEIGHT_M = 2.2
    STADIUM_TIER_INSET_M = 2.5

    @staticmethod
    def _stadium_tiers(polygon: Polygon, base_z_offset: float) -> Mesh3D | None:
        """Tek kutu yerine `STADIUM_TIER_COUNT` kademeli, her kademede
        biraz içe ofsetlenmiş (`SetbackFloorGenerator.offset_footprint`
        - M2.2'de binalar için kullanılan aynı merkez-radyal ofsetleme,
        burada yeniden kullanılıyor) prizmalar üst üste dizilir."""
        min_x, min_y, max_x, max_y = polygon.bounding_box()
        if (max_x - min_x) < 6.0 or (max_y - min_y) < 6.0:
            return None
        tiers: list[Mesh3D] = []
        current_polygon = polygon
        z = base_z_offset
        for tier_index in range(SportRecreationGenerator.STADIUM_TIER_COUNT):
            tiers.append(
                MeshBuilder.extrude_polygon(
                    current_polygon,
                    base_z=z,
                    height=SportRecreationGenerator.STADIUM_TIER_HEIGHT_M,
                    name=f"sport_area_stadium_tier_{tier_index}",
                )
            )
            z += SportRecreationGenerator.STADIUM_TIER_HEIGHT_M
            next_polygon = SetbackFloorGenerator.offset_footprint(
                current_polygon,
                SportRecreationGenerator.STADIUM_TIER_INSET_M,
            )
            # Aşırı içe ofsetin poligonu dejenere hale getirmesini önle
            # (B4 ilkesi - kabaca alan pozitif kalmalı).
            if next_polygon.unsigned_area() < 4.0:
                break
            current_polygon = next_polygon
        return MeshMerger.merge(tiers, name="sport_area_stadium")

    @staticmethod
    def playground(position: Point2D, ground_z: float = 0.0) -> Mesh3D:
        """B1: "kaydırak/salıncak gibi düşük-poly prop kütüphanesi".
        Kaydırak: eğik düzlem (kutu, döndürülmüş görünüm basitleştirmesi
        için basit dikdörtgen rampa) + platform. Salıncak: A-çerçeve +
        oturak."""
        # Kaydırak platformu + rampa (rampa, eksene hizalı basit kutu ile
        # yaklaştırılır - B1'in "düşük-poly" vurgusuyla tutarlı).
        slide_platform = MeshBuilder.build_box(
            0.9,
            0.9,
            0.1,
            center_x=position.x - 1.2,
            center_y=position.y,
            base_z=ground_z + 1.2,
            name="playground_slide_platform",
        )
        slide_support = MeshBuilder.build_box(
            0.15,
            0.15,
            1.2,
            center_x=position.x - 1.2,
            center_y=position.y,
            base_z=ground_z,
            name="playground_slide_support",
        )
        slide_ramp = MeshBuilder.build_box(
            1.6,
            0.6,
            0.08,
            center_x=position.x - 0.3,
            center_y=position.y,
            base_z=ground_z + 0.55,
            name="playground_slide_ramp",
        )

        # Salıncak: iki dikey direk + üst kiriş + tek oturak (temsili).
        swing_post_a = MeshBuilder.build_cylinder(
            radius=0.06,
            height=2.2,
            center_x=position.x + 1.0,
            center_y=position.y - 0.8,
            base_z=ground_z,
            segments=6,
            name="playground_swing_post_a",
        )
        swing_post_b = MeshBuilder.build_cylinder(
            radius=0.06,
            height=2.2,
            center_x=position.x + 1.0,
            center_y=position.y + 0.8,
            base_z=ground_z,
            segments=6,
            name="playground_swing_post_b",
        )
        swing_beam = MeshBuilder.build_box(
            0.1,
            1.7,
            0.1,
            center_x=position.x + 1.0,
            center_y=position.y,
            base_z=ground_z + 2.15,
            name="playground_swing_beam",
        )
        swing_seat = MeshBuilder.build_box(
            0.4,
            0.15,
            0.03,
            center_x=position.x + 1.0,
            center_y=position.y,
            base_z=ground_z + 0.45,
            name="playground_swing_seat",
        )

        # ROADMAP_V8.md Faz 5.6d: oyun alanı prop çeşitliliği en az 3
        # farklı elemana çıkarılmalı (önceden yalnızca kaydırak+salıncak
        # vardı) — üçüncü eleman: tahterevalli (basit çapraz kiriş +
        # merkez pivot + iki uç oturak, düşük-poly).
        seesaw_pivot = MeshBuilder.build_cylinder(
            radius=0.12,
            height=0.45,
            center_x=position.x,
            center_y=position.y - 1.6,
            base_z=ground_z,
            segments=6,
            name="playground_seesaw_pivot",
        )
        seesaw_beam = MeshBuilder.build_box(
            2.4,
            0.18,
            0.06,
            center_x=position.x,
            center_y=position.y - 1.6,
            base_z=ground_z + 0.45,
            name="playground_seesaw_beam",
        )
        seesaw_seat_a = MeshBuilder.build_box(
            0.3,
            0.2,
            0.05,
            center_x=position.x - 1.1,
            center_y=position.y - 1.6,
            base_z=ground_z + 0.5,
            name="playground_seesaw_seat_a",
        )
        seesaw_seat_b = MeshBuilder.build_box(
            0.3,
            0.2,
            0.05,
            center_x=position.x + 1.1,
            center_y=position.y - 1.6,
            base_z=ground_z + 0.5,
            name="playground_seesaw_seat_b",
        )

        return MeshMerger.merge(
            [
                slide_platform,
                slide_support,
                slide_ramp,
                swing_post_a,
                swing_post_b,
                swing_beam,
                swing_seat,
                seesaw_pivot,
                seesaw_beam,
                seesaw_seat_a,
                seesaw_seat_b,
            ],
            name="playground",
        )

    @staticmethod
    def generate_batch(areas: list[SportAreaItem], playgrounds: list[PlaygroundItem]) -> Mesh3D:
        """Bir bbox'taki tüm spor/rekreasyon feature'larını tek mesh'te
        birleştirir (önceki köprülerin `generate_batch`/`generate_market`
        deseniyle tutarlı)."""
        meshes: list[Mesh3D] = [SportRecreationGenerator.generate_area(a) for a in areas]
        meshes.extend(
            SportRecreationGenerator.playground(p.position, p.ground_z) for p in playgrounds
        )
        if not meshes:
            return Mesh3D(vertices=[], triangles=[], name="sport_recreation_batch")
        return MeshMerger.merge(meshes, name="sport_recreation_batch")


# ROADMAP_V7.md Faz C3 (6. dilim) - OSM kategori köprüsü, bu modülün
# tanımları hazır olduktan sonra en altta import edilir (döngüsel importu
# önlemek için önceki köprülerle aynı desen).
from .osm_bridge import (  # noqa: E402
    generate_sport_recreation_for_collection,
    sport_recreation_item_from_feature,
)

__all__ = [
    "SportAreaType",
    "AREA_EXTRUDE_PARAMS",
    "SportAreaItem",
    "PlaygroundItem",
    "SportRecreationGenerator",
    "sport_recreation_item_from_feature",
    "generate_sport_recreation_for_collection",
]
