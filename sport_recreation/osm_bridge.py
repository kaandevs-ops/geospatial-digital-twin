"""
sport_recreation.osm_bridge - OSM Kategori Feature'larından Spor/Rekreasyon
==============================================================================

ROADMAP_V7.md Faz C3 (6. dilim) — B1'in "spor ve rekreasyon" alt kümesini
(spor sahası, stadyum, yüzme havuzu, çocuk oyun alanı) `core_engine.
gis_core.osm_client`'a önceki dilimlerle aynı desende bağlar.

- `pitch`/`stadium`/`swimming_pool` (Polygon) -> `SportAreaItem`.
- `playground` (Point) -> `PlaygroundItem`.
"""
from __future__ import annotations

from ..core_engine.geometry_engine import Point2D, Polygon
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection
from . import PlaygroundItem, SportAreaItem, SportAreaType

#: `osm_client.DEFAULT_CATEGORIES`'teki spor/rekreasyon kategori
#: anahtarları -> `SportAreaType` (Polygon feature'lar için).
CATEGORY_KEY_TO_AREA_TYPE: dict[str, SportAreaType] = {
    "pitch": SportAreaType.PITCH,
    "stadium": SportAreaType.STADIUM,
    "swimming_pool": SportAreaType.SWIMMING_POOL,
}

PLAYGROUND_CATEGORY_KEY = "playground"


def sport_recreation_item_from_feature(
    feature: GeoFeature,
) -> SportAreaItem | PlaygroundItem | None:
    """Tekil bir OSM feature'ını (`__category__` damgalı) uygun spor/
    rekreasyon veri yapısına çevirir. Eşlenmeyen kategori/geometri
    kombinasyonları için `None` döner (önceki köprülerle aynı "eksik
    veri sahneyi bozmasın" felsefesi)."""
    category_key = feature.properties.get("__category__")

    area_type = CATEGORY_KEY_TO_AREA_TYPE.get(category_key)
    if area_type is not None:
        if feature.geometry_type != "Polygon":
            return None
        ring = feature.coordinates[0]
        polygon = Polygon([Point2D(x, y) for x, y in ring])
        return SportAreaItem(area_type=area_type, polygon=polygon)

    if category_key == PLAYGROUND_CATEGORY_KEY:
        if feature.geometry_type != "Point":
            return None
        x, y = feature.coordinates
        return PlaygroundItem(position=Point2D(x, y), ground_z=0.0)

    return None


def generate_sport_recreation_for_collection(
    collection: GeoFeatureCollection,
) -> list[SportAreaItem | PlaygroundItem]:
    """Faz C3 (6. dilim) uçtan uca giriş noktası: `fetch_category_features`
    + `project_to_local_meters` çıktısındaki bir `GeoFeatureCollection`'daki
    tüm spor/rekreasyon feature'larını tek bir öğe listesine indirger.

    Önceki köprülerle aynı tasarım kararı — `Mesh3D` değil ara veri
    yapısı döner; mesh isteyen çağıran taraf
    `SportRecreationGenerator.generate_batch(areas, playgrounds)`
    kullanır (Faz C4 LOD/instancing için erken mesh üretimi dayatılmaz)."""
    items: list[SportAreaItem | PlaygroundItem] = []
    for feature in collection.features:
        item = sport_recreation_item_from_feature(feature)
        if item is not None:
            items.append(item)
    return items


__all__ = [
    "CATEGORY_KEY_TO_AREA_TYPE",
    "PLAYGROUND_CATEGORY_KEY",
    "sport_recreation_item_from_feature",
    "generate_sport_recreation_for_collection",
]
