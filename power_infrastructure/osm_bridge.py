"""
power_infrastructure.osm_bridge - OSM Kategori Feature'larından Altyapı
==============================================================================

ROADMAP_V7.md Faz C3 (7. dilim, B1'in son dilimi) — B1'in "altyapı" alt
kümesinin geri kalanını (`power=line`, `power=substation`, `man_made=tower`
+ `tower:type=communication`) `core_engine.gis_core.osm_client`'a önceki
dilimlerle aynı desende bağlar.

`man_made=tower` tek başına iletişim kulesi anlamına gelmez (su kulesi,
gözlem kulesi vb. de aynı ana tag'i kullanır) — kategori sistemi yalnızca
*geometriye* göre dallandığından (`OSMCategory.geometry`), ikincil
`tower:type=communication` filtresi bu köprüde (properties üzerinden)
uygulanır; tıpkı `commerce_props.osm_bridge`'in `outdoor_seating=yes`
filtresini aynı şekilde ele alması gibi (B4 "eksik/çelişkili tag" ilkesi:
eşleşmeyen tower tipi sessizce atlanır, sahneyi bozmaz).
"""
from __future__ import annotations

from ..core_engine.geometry_engine import Point2D, Polygon
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection
from ..editor.road_editor import Road
from . import CommunicationTowerItem, PowerInfrastructureGenerator, SubstationItem

POWER_LINE_CATEGORY_KEY = "power_line"
SUBSTATION_CATEGORY_KEY = "substation"
COMMUNICATION_TOWER_CATEGORY_KEY = "communication_tower"

#: Baz istasyonu yüksekliği için OSM `height` tag'i önceliklidir (B4:
#: "gerçek veri önceliği"); yoksa tipik bir kafes kule yüksekliğine düşülür.
DEFAULT_COMMUNICATION_TOWER_HEIGHT_M = 25.0


def _tower_height_m(tags: dict) -> float:
    raw = tags.get("height")
    if raw is not None:
        try:
            value = float(str(raw).strip().rstrip("m").strip())
            if value > 0:
                return value
        except ValueError:
            pass
    return DEFAULT_COMMUNICATION_TOWER_HEIGHT_M


def infra_item_from_feature(
    feature: GeoFeature,
) -> Road | SubstationItem | CommunicationTowerItem | None:
    """Tekil bir OSM feature'ını (`__category__` damgalı) uygun altyapı
    veri yapısına çevirir. Eşlenmeyen kategori/geometri veya
    `tower:type != communication` durumunda `None` döner (önceki
    köprülerle aynı "eksik veri sahneyi bozmasın" felsefesi)."""
    category_key = feature.properties.get("__category__")

    if category_key == POWER_LINE_CATEGORY_KEY:
        if feature.geometry_type != "LineString":
            return None
        if len(feature.coordinates) < 2:
            return None
        points = [Point2D(x, y) for x, y in feature.coordinates]
        osm_id = feature.properties.get("osm_id", "power_line")
        return PowerInfrastructureGenerator.power_line_from_points(points, osm_id=str(osm_id))

    if category_key == SUBSTATION_CATEGORY_KEY:
        if feature.geometry_type != "Polygon":
            return None
        ring = feature.coordinates[0]
        if len(ring) < 4:
            return None
        polygon = Polygon([Point2D(x, y) for x, y in ring])
        return SubstationItem(polygon=polygon)

    if category_key == COMMUNICATION_TOWER_CATEGORY_KEY:
        if feature.geometry_type != "Point":
            return None
        if str(feature.properties.get("tower:type", "")).lower() != "communication":
            return None
        x, y = feature.coordinates
        height = _tower_height_m(feature.properties)
        return CommunicationTowerItem(position=Point2D(x, y), height_m=height, ground_z=0.0)

    return None


def generate_power_infrastructure_for_collection(
    collection: GeoFeatureCollection,
) -> list[Road | SubstationItem | CommunicationTowerItem]:
    """Faz C3 (7. dilim) uçtan uca giriş noktası: `fetch_category_features`
    + `project_to_local_meters` çıktısındaki bir `GeoFeatureCollection`'daki
    tüm altyapı feature'larını tek bir öğe listesine indirger.

    Önceki köprülerle aynı tasarım kararı — `Mesh3D` değil ara veri
    yapısı döner (`power_line` için doğrudan `Road`, diğerleri için
    kendi dataclass'ları); mesh isteyen çağıran taraf
    `PowerInfrastructureGenerator.mesh_for_power_line`/`substation`/
    `communication_tower` kullanır (Faz C4 LOD/instancing için erken
    mesh üretimi dayatılmaz)."""
    items: list[Road | SubstationItem | CommunicationTowerItem] = []
    for feature in collection.features:
        item = infra_item_from_feature(feature)
        if item is not None:
            items.append(item)
    return items


__all__ = [
    "POWER_LINE_CATEGORY_KEY",
    "SUBSTATION_CATEGORY_KEY",
    "COMMUNICATION_TOWER_CATEGORY_KEY",
    "DEFAULT_COMMUNICATION_TOWER_HEIGHT_M",
    "infra_item_from_feature",
    "generate_power_infrastructure_for_collection",
]
