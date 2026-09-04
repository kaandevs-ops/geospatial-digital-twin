"""
Religious Structures
======================

ROADMAP_V7.md Bölüm B1 — "Dini ve kültürel yapılar":

    "amenity=place_of_worship` + `religion` tag'i (cami, kilise, sinagog
    vb.) — minare/kubbe gibi tipik siluet elemanları için basit
    parametrik ekler."

`street_furniture` ile aynı desen izlenir: OSM tag (`religion` değeri) ->
tip -> basit parametrik mesh üretici. stdlib-only, yalnızca
`mesh_engine` primitifleri (`build_cylinder`/`build_cone`/`build_dome`/
`build_box`) kullanılır — harici model/asset dosyası yok.

Kapsam bilinçli olarak dar tutuldu: gerçek bir mimari kütüphane değil,
B1'in kendi ifadesiyle "tipik siluet elemanları" için görsel olarak
tanınabilir, kaba parametrik ekler (bir caminin kubbesi + minaresi, bir
kilisenin çan kulesi + sivri çatısı, bilinmeyen/diğer dinler için genel
bir kubbe). Ana ibadet yapısının kendisi (duvarlar) zaten mevcut bina
boru hattı (`ai_reconstruction`/`building_reconstruction`) tarafından
`building=*` olarak üretiliyor — bu modül yalnızca *ek* siluet
elemanlarını üretir, bina gövdesinin yerine geçmez.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ..core_engine.geometry_engine import Point2D, Polygon
from ..mesh_engine import Mesh3D, MeshBuilder, MeshMerger


class ReligionKind(str, Enum):
    MUSLIM = "muslim"
    CHRISTIAN = "christian"
    JEWISH = "jewish"
    GENERIC = "generic"


#: OSM `religion` tag değeri (OSM Wiki standart değerleri) -> `ReligionKind`.
#: Eşlenmeyen/eksik değerler `GENERIC`'e düşer (B4 "eksik/çelişkili tag
#: kombinasyonlarında varsayılan/heuristic kurallar" ilkesiyle tutarlı).
OSM_RELIGION_TAG_MAP: dict[str, ReligionKind] = {
    "muslim": ReligionKind.MUSLIM,
    "christian": ReligionKind.CHRISTIAN,
    "jewish": ReligionKind.JEWISH,
}


def classify_religion(tags: dict) -> ReligionKind:
    """OSM `religion` tag'inden `ReligionKind` sınıflandırması. `tags`
    içinde `amenity=place_of_worship` olduğu varsayılır (bu fonksiyon
    yalnızca alt tipi belirler); `religion` yoksa/eşlenmemişse GENERIC."""
    religion = str(tags.get("religion", "")).lower()
    return OSM_RELIGION_TAG_MAP.get(religion, ReligionKind.GENERIC)


@dataclass(slots=True)
class ReligiousStructureItem:
    religion: ReligionKind
    position: Point2D
    ground_z: float = 0.0
    base_height_m: float = 8.0  # ana bina yüksekliği (varsa OSM `height`, yoksa varsayılan)


class ReligiousStructureGenerator:
    """`ReligionKind` (+ konum + taban yüksekliği) -> siluet eleman
    `Mesh3D`'si. Her tip için ayrı statik üretici + tek bir `generate`
    dispatch metodu (`street_furniture.StreetFurnitureGenerator` ile
    aynı şablon)."""

    @staticmethod
    def from_osm_tags(
        tags: dict, position: Point2D, ground_z: float = 0.0
    ) -> ReligiousStructureItem | None:
        """Bir OSM feature'ının tag sözlüğünü `ReligiousStructureItem`'a
        çevirir. `amenity=place_of_worship` yoksa `None` döner (çağıran
        taraf sessizce atlamalı — roadmap'in "eksik veri sahneyi
        bozmasın" felsefesi)."""
        if tags.get("amenity") != "place_of_worship":
            return None
        religion = classify_religion(tags)
        base_height_m = _parse_height(tags.get("height")) or 8.0
        return ReligiousStructureItem(
            religion=religion,
            position=position,
            ground_z=ground_z,
            base_height_m=base_height_m,
        )

    @staticmethod
    def generate(item: ReligiousStructureItem) -> Mesh3D:
        dispatch = {
            ReligionKind.MUSLIM: ReligiousStructureGenerator.mosque_silhouette,
            ReligionKind.CHRISTIAN: ReligiousStructureGenerator.church_silhouette,
            ReligionKind.JEWISH: ReligiousStructureGenerator.synagogue_silhouette,
            ReligionKind.GENERIC: ReligiousStructureGenerator.generic_dome_silhouette,
        }
        return dispatch[item.religion](item.position, item.ground_z, item.base_height_m)

    @staticmethod
    def mosque_silhouette(
        position: Point2D, ground_z: float = 0.0, base_height_m: float = 8.0
    ) -> Mesh3D:
        """Kubbe (bina çatısı üstünde) + tek minare (silindir gövde +
        koni külah). Konum minarenin/kubbenin bina merkezine göreli
        yerleşimidir; gerçek yerleşim (minare genelde köşede) mimari
        kesinlik iddiası taşımadığı için basitçe bina merkezine yakın,
        sabit bir ofsetle yerleştirilir."""
        dome_radius = 3.0
        dome = MeshBuilder.build_dome(
            radius=dome_radius,
            height=dome_radius * 0.75,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + base_height_m,
            segments=16,
            rings=6,
            name="mosque_dome",
        )
        minaret_x, minaret_y = position.x + dome_radius + 1.5, position.y
        shaft_height = base_height_m + 6.0
        shaft = MeshBuilder.build_cylinder(
            radius=0.5,
            height=shaft_height,
            center_x=minaret_x,
            center_y=minaret_y,
            base_z=ground_z,
            segments=10,
            name="minaret_shaft",
        )
        cap = MeshBuilder.build_cone(
            radius=0.6,
            height=2.5,
            center_x=minaret_x,
            center_y=minaret_y,
            base_z=ground_z + shaft_height,
            segments=10,
            name="minaret_cap",
        )
        return MeshMerger.merge([dome, shaft, cap], name="mosque_silhouette")

    @staticmethod
    def church_silhouette(
        position: Point2D, ground_z: float = 0.0, base_height_m: float = 8.0
    ) -> Mesh3D:
        """Çan kulesi (kutu gövde) + sivri çatı (koni) — B1'in "tipik
        siluet elemanları" için kilise varyantı."""
        tower_width = 3.0
        tower_height = base_height_m + 5.0
        tower = MeshBuilder.build_box(
            tower_width,
            tower_width,
            tower_height,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            name="church_tower",
        )
        spire = MeshBuilder.build_cone(
            radius=tower_width * 0.75,
            height=4.0,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + tower_height,
            segments=4,
            name="church_spire",
        )
        return MeshMerger.merge([tower, spire], name="church_silhouette")

    @staticmethod
    def synagogue_silhouette(
        position: Point2D, ground_z: float = 0.0, base_height_m: float = 8.0
    ) -> Mesh3D:
        """ROADMAP_V8 Faz 5.6b — sinagog için ayrı, kendine özgü bir
        siluet: dörtgen kule + alçak (basık) kubbe/çatı kombinasyonu +
        kule ön yüzüne monte edilmiş düşük-poly bir Davut Yıldızı
        (hexagram) süs plakası. Önceden `JEWISH` doğrudan
        `generic_dome_silhouette`'e yönlendiriliyordu (cami/kilise gibi
        kendine özgü bir eleman yoktu); bu, B1'in "her dinin kendi tipik
        silüeti" ilkesinin yalnızca 2/3 uygulandığı anlamına geliyordu.
        Mimari kesinlik iddia edilmez — `mosque_silhouette`/
        `church_silhouette` ile aynı "basit parametrik ek" felsefesi
        korunur, yeni bir primitif gerekmez."""
        tower_width = 3.0
        tower_height = base_height_m + 3.5
        tower = MeshBuilder.build_box(
            tower_width,
            tower_width,
            tower_height,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z,
            name="synagogue_tower",
        )
        # Alçak/basık kubbe (camininkinden belirgin şekilde daha basık
        # oranlı — height=radius*0.45 — görsel olarak ayırt edilebilir
        # olması için, roadmap kabul kriteri).
        dome_radius = tower_width * 0.62
        low_dome = MeshBuilder.build_dome(
            radius=dome_radius,
            height=dome_radius * 0.45,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + tower_height,
            segments=12,
            rings=4,
            name="synagogue_low_dome",
        )
        star = ReligiousStructureGenerator._star_of_david_plate(
            center_x=position.x,
            center_y=position.y - tower_width / 2.0 - 0.02,
            z=ground_z + tower_height * 0.6,
            outer_radius=tower_width * 0.32,
        )
        return MeshMerger.merge([tower, low_dome, star], name="synagogue_silhouette")

    @staticmethod
    def _star_of_david_plate(
        center_x: float, center_y: float, z: float, outer_radius: float
    ) -> Mesh3D:
        """İki üst üste bindirilmiş eşkenar üçgenden oluşan 12 köşeli
        hexagram anahat poligonunun ince (0.12m) bir dikey plakaya
        ekstrüzyonu — kule ön cephesine monte edilen düşük-poly süs
        elemanı. `extrude_polygon` XY düzleminde çalıştığı için plaka
        önce yatay üretilip, döngü sonunda y<->z koordinatları takas
        edilerek dikey (kuleye monte) konuma getirilir."""
        inner_radius = outer_radius * (1.0 / math.sqrt(3))
        points: list[Point2D] = []
        for i in range(12):
            angle = math.pi / 2.0 + i * (math.pi / 6.0)
            r = outer_radius if i % 2 == 0 else inner_radius
            points.append(Point2D(r * math.cos(angle), r * math.sin(angle)))
        mesh = MeshBuilder.extrude_polygon(Polygon(points), 0.0, 0.12, name="star_of_david")
        for v in mesh.vertices:
            local_x, local_thick, local_z = v.x, v.z, v.y
            v.x = center_x + local_x
            v.y = center_y + local_thick
            v.z = z + local_z
        return mesh

    @staticmethod
    def generic_dome_silhouette(
        position: Point2D, ground_z: float = 0.0, base_height_m: float = 8.0
    ) -> Mesh3D:
        """Yalnızca gerçekten sınıflandırılamayan (`religion` tag'i
        eksik/tanınmayan) durumlar için genel bir kubbe — belirli bir
        dinin mimarisini iddia etmeyen, yalnızca "burada dini bir yapı
        var" görsel işaretini veren en genel varyant."""
        dome_radius = 2.5
        dome = MeshBuilder.build_dome(
            radius=dome_radius,
            height=dome_radius * 0.7,
            center_x=position.x,
            center_y=position.y,
            base_z=ground_z + base_height_m,
            segments=14,
            rings=5,
            name="generic_dome",
        )
        return dome

    @staticmethod
    def generate_batch(items: list[ReligiousStructureItem]) -> Mesh3D:
        if not items:
            return Mesh3D(vertices=[], triangles=[], name="religious_structures_batch")
        meshes = [ReligiousStructureGenerator.generate(item) for item in items]
        return MeshMerger.merge(meshes, name="religious_structures_batch")


def _parse_height(raw) -> float | None:
    if raw is None:
        return None
    try:
        value = float(str(raw).strip().rstrip("m").strip())
        return value if value > 0 else None
    except ValueError:
        return None


# ROADMAP_V7.md Faz C3 (4. dilim) — OSM kategori köprüsü, modülün kendi
# tanımları hazır olduktan sonra en altta import edilir (`street_furniture`
# paketiyle aynı döngüsel-import çözümü).
from .osm_bridge import (  # noqa: E402
    generate_religious_structures_for_collection,
    religious_structure_item_from_point,
)

__all__ = [
    "ReligionKind",
    "OSM_RELIGION_TAG_MAP",
    "classify_religion",
    "ReligiousStructureItem",
    "ReligiousStructureGenerator",
    "religious_structure_item_from_point",
    "generate_religious_structures_for_collection",
]
