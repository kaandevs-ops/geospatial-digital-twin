"""
commerce_props.osm_bridge - OSM Kategori Feature'larından Ticaret Prop'ları
==============================================================================

ROADMAP_V7.md Faz C3 (5. dilim) — B1'in "ticaret ve gündelik yaşam" alt
kümesini (pazar yeri, restoran/kafe dış mekan oturma) `core_engine.gis_core.
osm_client`'a önceki dilimlerle (`vegetation/osm_bridge.py`,
`editor/osm_bridge.py`, `street_furniture/osm_bridge.py`,
`religious_structures/osm_bridge.py`) aynı desende bağlar.

- `marketplace` (`amenity=marketplace`, Polygon) -> `MarketStallLayout`
  (satır-sütun grid, `CommercePropsGenerator.layout_stalls_in_polygon`).
- `outdoor_seating` (`amenity=restaurant`/`amenity=cafe` +
  `outdoor_seating=yes`, Point) -> `OutdoorSeatingItem`.

Mevcut kategori sistemi yalnızca *geometriye* göre dallandığı için
(bkz. `OSMCategory.geometry`), `outdoor_seating=yes` gibi ikincil bir
tag'e bağlı filtreleme `DEFAULT_CATEGORIES` seviyesinde değil, bu köprüde
(feature.properties üzerinden) yapılır — roadmap B4'ün "eksik/çelişkili
tag kombinasyonlarında heuristik" ilkesiyle tutarlı: `outdoor_seating`
tag'i yoksa/`yes` değilse feature sessizce atlanır (sahneyi bozmaz).
"""

from __future__ import annotations

from ..core_engine.geometry_engine import Point2D
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection
from . import CommercePropsGenerator, MarketStallLayout, OutdoorSeatingItem

#: `osm_client.DEFAULT_CATEGORIES`'e Faz C3 (5. dilim) ile eklenen ticaret
#: kategori anahtarları. `restaurant`/`cafe` aynı `OutdoorSeatingItem`
#: üretim yoluna düşer (B1: "restoran/kafe ... dış mekan oturma alanı").
RESTAURANT_CATEGORY_KEYS = frozenset({"restaurant", "cafe"})
MARKETPLACE_CATEGORY_KEY = "marketplace"


def commerce_prop_from_feature(
    feature: GeoFeature,
) -> MarketStallLayout | OutdoorSeatingItem | None:
    """Tekil bir OSM feature'ını (`__category__` damgalı) uygun ticaret
    prop veri yapısına çevirir. Eşlenmeyen kategori/geometri veya
    `outdoor_seating` şartı sağlanmayan restoran/kafe için `None` döner
    (önceki köprülerle aynı "eksik veri sahneyi bozmasın" felsefesi)."""
    category_key = feature.properties.get("__category__")

    if category_key == MARKETPLACE_CATEGORY_KEY:
        if feature.geometry_type != "Polygon":
            return None
        polygon = feature.to_polygon()
        return CommercePropsGenerator.layout_stalls_in_polygon(polygon, ground_z=0.0)

    if category_key in RESTAURANT_CATEGORY_KEYS:
        if feature.geometry_type != "Point":
            return None
        # B1: "varsa `outdoor_seating=yes`" — bu tag olmadan dış mekan
        # prop'u üretmek OSM'de karşılığı olmayan varsayım eklemek olur.
        if str(feature.properties.get("outdoor_seating", "")).lower() != "yes":
            return None
        x, y = feature.coordinates
        return OutdoorSeatingItem(position=Point2D(x, y), table_count=2, ground_z=0.0)

    return None


def generate_commerce_props_for_collection(
    collection: GeoFeatureCollection,
) -> list[MarketStallLayout | OutdoorSeatingItem]:
    """Faz C3 (5. dilim) uçtan uca giriş noktası: `fetch_category_features`
    + `project_to_local_meters` çıktısındaki bir `GeoFeatureCollection`'daki
    tüm ticaret/gündelik-yaşam feature'larını tek bir prop listesine
    indirger.

    Önceki köprülerle aynı tasarım kararı — `Mesh3D` değil ara veri
    yapısı (`MarketStallLayout`/`OutdoorSeatingItem`) döner; mesh isteyen
    çağıran taraf `CommercePropsGenerator.generate_market(layout)` veya
    `CommercePropsGenerator.outdoor_seating_set(item)` kullanır (Faz C4
    LOD/instancing için erken mesh üretimi dayatılmaz)."""
    props: list[MarketStallLayout | OutdoorSeatingItem] = []
    for feature in collection.features:
        prop = commerce_prop_from_feature(feature)
        if prop is not None:
            props.append(prop)
    return props


__all__ = [
    "RESTAURANT_CATEGORY_KEYS",
    "MARKETPLACE_CATEGORY_KEY",
    "commerce_prop_from_feature",
    "generate_commerce_props_for_collection",
]
