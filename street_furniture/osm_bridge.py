"""
street_furniture.osm_bridge - OSM Kategori Feature'larından Kentsel Mobilya
==============================================================================

ROADMAP_V7.md Faz C3 (3. dilim) — B1'in "kentsel mobilya" alt kümesini
(sokak lambası, elektrik direği, çöp kutusu, bank, otobüs durağı/durağı) ve
ROADMAP_V8 Bölüm 2 Faz 2.1/2.3/2.5'in eklediği ulaşım + kentsel mobilya +
anıt alt kategorilerini `core_engine.gis_core.osm_client`'ın ürettiği
kategori damgalı `GeoFeature`'lara bağlar.

`vegetation/osm_bridge.py` ve `religious_structures/osm_bridge.py` ile aynı
desen izlenir: mevcut mimari (`street_furniture.StreetFurnitureGenerator` /
`StreetFurnitureType` / `OSM_TAG_MAP` — Faz M2.5/V8 bileşenleri)
DEĞİŞTİRİLMEDEN, yalnızca OSM verisiyle beslenir (roadmap'in "mevcut mimari
korunacak" ilkesi).

`CATEGORY_KEY_TO_OSM_TAG`: `osm_client.DEFAULT_CATEGORIES`'teki kentsel
mobilya/ulaşım/anıt anahtarlarını (`"street_lamp"`, `"power_pole"`, ...)
`street_furniture.OSM_TAG_MAP`'in beklediği `"key=value"` tag string'ine
eşler. Bu iki tablo bilinçli olarak aynı isimlendirmeyi paylaşır (bkz.
`osm_client.py`'deki yorum), bu yüzden eşleme çoğunlukla `f"{filter}"`
birebir kopyasıdır; yalnız `bus_stop` (OSM `highway=bus_stop`) ve
`bus_station` (OSM `amenity=bus_station`) ayrık tag'lere sahip olduğundan
açıkça yazılır.
"""

from __future__ import annotations

from ..core_engine.geometry_engine import Point2D
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection

#: Kategori anahtarı (`osm_client.DEFAULT_CATEGORIES`) -> OSM tag string'i
#: (`street_furniture.OSM_TAG_MAP`'in anahtarı). Yalnızca bu köprünün
#: kapsadığı (nokta-tabanlı, `StreetFurnitureType`'a sahip) kategoriler
#: burada yer alır; yol/su/bina gibi diğer kategoriler kendi köprülerinde
#: (`editor/osm_bridge.py` vb.) ele alınır.
CATEGORY_KEY_TO_OSM_TAG: dict[str, str] = {
    "street_lamp": "highway=street_lamp",
    "power_pole": "power=pole",
    "waste_basket": "amenity=waste_basket",
    "bench": "amenity=bench",
    "bus_stop": "highway=bus_stop",
    "bus_station": "amenity=bus_station",
    # ROADMAP_V8 Faz 2.1 — ulaşımın tamamlanması (yalnızca nokta alt tipler;
    # `railway_rail`/`railway_subway`/`railway_tram`/`parking` çizgi/alan
    # geometrisine sahip olduğundan bu köprünün kapsamı dışında kalır ve
    # ayrı bir altyapı köprüsü gerektirir).
    "railway_station": "railway=station",
    "subway_entrance": "railway=subway_entrance",
    "bicycle_parking": "amenity=bicycle_parking",
    "taxi": "amenity=taxi",
    "traffic_signals": "highway=traffic_signals",
    "crossing": "highway=crossing",
    # ROADMAP_V8 Faz 2.3 — kentsel mobilyanın tamamlanması.
    "drinking_water": "amenity=drinking_water",
    "fountain": "amenity=fountain",
    "bicycle_rental": "amenity=bicycle_rental",
    # ROADMAP_V8 Faz 2.5 — anıt/heykel kategorisi.
    "monument": "historic=monument",
    "artwork": "tourism=artwork",
}


def furniture_item_from_point(feature: GeoFeature):
    """Tekil bir OSM nokta feature'ını (`__category__` bu köprünün kapsadığı
    bir kentsel-mobilya/ulaşım/anıt anahtarıysa) `StreetFurnitureItem`'a
    çevirir. Geometry "Point" değilse veya kategori eşleşmiyorsa/bilinmiyorsa
    `None` döner (çağıran taraf sessizce atlar — B4 "eksik/çelişkili tag"
    ilkesiyle tutarlı, tanınmayan OSM elemanları sahneyi bozmaz)."""
    from . import StreetFurnitureGenerator  # gecikmeli import (döngüsel import önlemi)

    if feature.geometry_type != "Point":
        return None
    category_key = feature.properties.get("__category__")
    tag = CATEGORY_KEY_TO_OSM_TAG.get(category_key)
    if tag is None:
        return None
    x, y = feature.coordinates
    return StreetFurnitureGenerator.from_osm_tag(tag, Point2D(x, y), ground_z=0.0)


def generate_street_furniture_for_collection(collection: GeoFeatureCollection) -> list:
    """Faz C3 (3. dilim) uçtan uca giriş noktası: bir `GeoFeatureCollection`
    içindeki tüm kentsel-mobilya/ulaşım/anıt nokta feature'larını
    `StreetFurnitureItem` listesine indirger (mesh üretimi çağıran tarafa
    bırakılır — `vegetation`/`religious_structures` köprüleriyle aynı
    tasarım kararı, Faz C4 instancing/batching için erken mesh üretimini
    dayatmaz)."""
    items = []
    for feature in collection.features:
        item = furniture_item_from_point(feature)
        if item is not None:
            items.append(item)
    return items


__all__ = [
    "CATEGORY_KEY_TO_OSM_TAG",
    "furniture_item_from_point",
    "generate_street_furniture_for_collection",
]
