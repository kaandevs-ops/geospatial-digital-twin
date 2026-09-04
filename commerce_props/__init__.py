"""
Commerce Props
==================

ROADMAP_V7.md Bölüm B1 - "Ticaret ve gündelik yaşam":

    "Pazar yeri (`amenity=marketplace`) ... açık pazar alanı için basit
    tezgah/gölgelik dizilimi (prosedürel, satır-sütun düzeninde)."
    "Restoran/kafe (`amenity=restaurant`, `amenity=cafe`) ... dış mekan
    oturma alanı (varsa `outdoor_seating=yes`) için basit masa-sandalye
    prop'ları."

Roadmap'in "Önerilen bir sonraki adım" notunda belirtildiği gibi, bu modül
`street_furniture/__init__.py`'daki `build_box`/`build_cylinder` primitifi
+ `from_osm_tags`/`generate` dispatch şablonunu yeniden kullanır; pazar
tezgahları için düzenli satır-sütun grid dizilimi (Poisson-disc'ten daha
basit, B1'in kendi ifadesiyle yeterli), restoran/kafe için tekil masa-
sandalye seti üretir. stdlib-only prensibi korunur, harici asset yok.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core_engine.geometry_engine import Point2D, Polygon
from ..mesh_engine import Mesh3D, MeshBuilder, MeshMerger


class CommercePropType(str, Enum):
    MARKET_STALL = "market_stall"
    OUTDOOR_SEATING = "outdoor_seating"
    # ROADMAP_V8 Faz 2.2 — ticaret ve gündelik yaşamın tamamlanması.
    SHOPPING_MALL = "shopping_mall"
    SUPERMARKET = "supermarket"


@dataclass(slots=True)
class MarketStallLayout:
    """Bir `amenity=marketplace` poligonu içine yerleştirilen tezgah
    grid'i - B1: "satır-sütun düzeninde" dizilim."""

    positions: list[Point2D] = field(default_factory=list)
    rotation_deg: float = 0.0
    ground_z: float = 0.0


@dataclass(slots=True)
class OutdoorSeatingItem:
    """Bir restoran/kafe noktası için tekil masa-sandalye seti - B1:
    "basit masa-sandalye prop'ları"."""

    position: Point2D
    table_count: int = 1
    rotation_deg: float = 0.0
    ground_z: float = 0.0


@dataclass(slots=True)
class BuildingVolume:
    """ROADMAP_V8 Faz 2.2 — `shop=mall`/`shop=supermarket` (Polygon) için
    büyük/orta ölçek hacim gövdesi. B1: "AVM ... büyük hacim kutu +
    varsayılan cephe stili (`ArchitecturalStyle.MODERN` ile eşlenebilir)"
    ve "market ... orta ölçek kutu hacim". Gerçek cephe/pencere detayı
    bilinçli olarak üretilmez — bu, `ai_reconstruction.building_analyzer`
    boru hattının (`ArchitecturalStyle`) sorumluluğunda; burası yalnızca
    OSM poligonundan minimum viable bir hacim üretir (roadmap'in "mevcut
    mimari korunacak" ilkesi — `ai_reconstruction` DEĞİŞTİRİLMEZ)."""

    footprint: Polygon
    height_m: float
    ground_z: float = 0.0
    #: `ai_reconstruction.building_analyzer.ArchitecturalStyle` ile aynı
    #: string sözleşmesi (döngüsel importu önlemek için burada yalnızca
    #: etiket olarak taşınır, çağıran taraf isterse eşleyebilir).
    style_hint: str = "MODERN"


class CommercePropsGenerator:
    """OSM ticaret/gündelik-yaşam feature'larından prosedürel mesh üretimi.

    `street_furniture.StreetFurnitureGenerator` ile aynı tasarım kararı:
    stdlib-only, `mesh_engine` primitifleri (box/cylinder) + `MeshMerger`
    dışında bağımlılık yok.
    """

    #: Tek bir tezgah biriminin (gölgelik dahil) kapladığı alan - grid
    #: aralığı hesaplaması bu değere göre yapılır.
    STALL_SPACING_M = 2.2

    @staticmethod
    def market_stall(position: Point2D, rotation_deg: float = 0.0, ground_z: float = 0.0) -> Mesh3D:
        """Tek bir pazar tezgahı: masa gövdesi + gölgelik (basit prizma
        çatı yerine düz eğik levha - düşük-poly, roadmap'in "basit"
        vurgusuyla tutarlı)."""
        table = MeshBuilder.build_box(
            1.4,
            0.8,
            0.85,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            name="market_stall_table",
        )
        post_a = MeshBuilder.build_cylinder(
            radius=0.03,
            height=2.1,
            center_x=position.x - 0.6,
            center_y=position.y - 0.35,
            base_z=ground_z,
            segments=6,
            name="market_stall_post_a",
        )
        post_b = MeshBuilder.build_cylinder(
            radius=0.03,
            height=2.1,
            center_x=position.x + 0.6,
            center_y=position.y - 0.35,
            base_z=ground_z,
            segments=6,
            name="market_stall_post_b",
        )
        canopy = MeshBuilder.build_box(
            1.6,
            1.0,
            0.05,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + 2.05,
            name="market_stall_canopy",
        )
        return MeshMerger.merge([table, post_a, post_b, canopy], name="market_stall")

    @staticmethod
    def outdoor_seating_set(item: OutdoorSeatingItem) -> Mesh3D:
        """Bir restoran/kafe için `table_count` adet masa + 2'şer sandalye
        (basit kutu prop'lar), masa aralığı `STALL_SPACING_M`'e göre
        satır dizilimi."""
        meshes: list[Mesh3D] = []
        for i in range(max(1, item.table_count)):
            offset = (i - (item.table_count - 1) / 2.0) * CommercePropsGenerator.STALL_SPACING_M
            tx = item.position.x + offset
            ty = item.position.y
            table = MeshBuilder.build_cylinder(
                radius=0.4,
                height=0.75,
                center_x=tx,
                center_y=ty,
                base_z=item.ground_z,
                segments=10,
                name=f"outdoor_table_{i}",
            )
            chair_a = MeshBuilder.build_box(
                0.4,
                0.4,
                0.45,
                center_x=tx - 0.55,
                center_y=ty,
                base_z=item.ground_z,
                name=f"outdoor_chair_{i}_a",
            )
            chair_b = MeshBuilder.build_box(
                0.4,
                0.4,
                0.45,
                center_x=tx + 0.55,
                center_y=ty,
                base_z=item.ground_z,
                name=f"outdoor_chair_{i}_b",
            )
            meshes.extend([table, chair_a, chair_b])
        return MeshMerger.merge(meshes, name="outdoor_seating_set")

    @staticmethod
    def layout_stalls_in_polygon(polygon: Polygon, ground_z: float = 0.0) -> MarketStallLayout:
        """`amenity=marketplace` poligonu içine, B1'in istediği "satır-
        sütun düzeninde" tezgah grid'i yerleştirir. Poligonun eksen hizalı
        sınır kutusu (`bounding box`) üzerinde `STALL_SPACING_M` aralıklı
        grid taranır, yalnızca poligon içinde kalan noktalar (mevcut
        `Polygon.contains_point`, C3/1. dilimde eklendi - değiştirilmeden
        tüketildi) tutulur - `vegetation.scatter`'daki Poisson-disc'ten
        bilinçli olarak daha basit bir yöntem (B1: düzenli grid yeterli)."""
        xs = [p.x for p in polygon.points]
        ys = [p.y for p in polygon.points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        spacing = CommercePropsGenerator.STALL_SPACING_M

        positions: list[Point2D] = []
        y = min_y + spacing / 2.0
        while y < max_y:
            x = min_x + spacing / 2.0
            while x < max_x:
                candidate = Point2D(x, y)
                if polygon.contains_point(candidate):
                    positions.append(candidate)
                x += spacing
            y += spacing
        return MarketStallLayout(positions=positions, ground_z=ground_z)

    @staticmethod
    def generate_market(layout: MarketStallLayout) -> Mesh3D:
        """Bir `MarketStallLayout`'taki tüm tezgahları tek mesh'te
        birleştirir (bina/mobilya köprüleriyle aynı `generate_batch`
        deseni)."""
        if not layout.positions:
            return Mesh3D(vertices=[], triangles=[], name="market_stalls_batch")
        meshes = [
            CommercePropsGenerator.market_stall(pos, layout.rotation_deg, layout.ground_z)
            for pos in layout.positions
        ]
        return MeshMerger.merge(meshes, name="market_stalls_batch")

    # ------------------------------------------------------------------ #
    # ROADMAP_V8 Faz 2.2 — AVM/market hacim primitifleri.
    # ------------------------------------------------------------------ #

    #: B1: "AVM ... büyük hacim kutu". Poligondan yükseklik bilgisi
    #: gelmiyorsa (`building:levels` tag'i yok) bu varsayılana düşülür —
    #: `ai_reconstruction`'daki kat-yüksekliği tahmin mantığıyla aynı
    #: "eksik veri sahneyi bozmasın" felsefesi.
    DEFAULT_MALL_HEIGHT_M = 12.0
    DEFAULT_SUPERMARKET_HEIGHT_M = 6.0

    @staticmethod
    def shopping_mall(volume: BuildingVolume) -> Mesh3D:
        """`shop=mall` (Polygon) -> büyük hacim kutu (`extrude_polygon`).
        Cephe/pencere detayı bilinçli olarak üretilmez (bkz. `BuildingVolume`
        docstring'i)."""
        from ..mesh_engine import MeshBuilder as _MB  # local: döngüsel import yok, tutarlılık için

        return _MB.extrude_polygon(
            volume.footprint,
            base_z=volume.ground_z,
            height=volume.height_m,
            name="shopping_mall",
        )

    @staticmethod
    def supermarket(volume: BuildingVolume) -> Mesh3D:
        """`shop=supermarket` (Polygon) -> orta ölçek kutu hacim, aynı
        `extrude_polygon` deseni, yalnızca varsayılan yükseklik farklı."""
        return MeshBuilder.extrude_polygon(
            volume.footprint,
            base_z=volume.ground_z,
            height=volume.height_m,
            name="supermarket",
        )


# ROADMAP_V7.md Faz C3 (5. dilim) - OSM kategori köprüsü, bu modülün
# tanımları hazır olduktan sonra en altta import edilir (döngüsel importu
# önlemek için `osm_bridge` bu paketten `CommercePropsGenerator`/ilgili
# dataclass'ları `from . import ...` ile geriye alıyor - `street_furniture`
# ve `religious_structures` ile aynı desen).
from .osm_bridge import (  # noqa: E402
    commerce_prop_from_feature,
    generate_commerce_props_for_collection,
)

__all__ = [
    "CommercePropType",
    "MarketStallLayout",
    "OutdoorSeatingItem",
    "CommercePropsGenerator",
    "commerce_prop_from_feature",
    "generate_commerce_props_for_collection",
]
