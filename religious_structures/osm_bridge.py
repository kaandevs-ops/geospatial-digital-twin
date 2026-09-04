"""
religious_structures.osm_bridge - OSM `place_of_worship` -> Siluet Elemanı
==============================================================================

ROADMAP_V7.md Faz C3 (4. dilim) — B1'in "dini ve kültürel yapılar"
kategorisini `core_engine.gis_core.osm_client`'a Faz C3'ün önceki
dilimleriyle (`editor/osm_bridge.py`, `street_furniture/osm_bridge.py`)
aynı desende bağlar: `osm_client.DEFAULT_CATEGORIES["place_of_worship"]`
-> `ReligiousStructureGenerator.from_osm_tags` (modülün kendi tag ->
tip sınıflandırması, burada tekrarlanmaz).
"""

from __future__ import annotations

from ..core_engine.geometry_engine import Point2D
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection

# NOT: `ReligiousStructureGenerator`/`ReligiousStructureItem` bu paketin
# `__init__.py`'sinden `from . import ...` ile alınmaz (bu dosya
# `__init__.py`'nin kendisi tarafından, tanımlar zaten hazırken en altta
# import edilir) — döngüsel importu önlemek için tip adları yalnızca
# fonksiyon gövdesinde, çağrı anında paket üzerinden çözülür.


def religious_structure_item_from_point(feature: GeoFeature):
    """Tekil bir OSM nokta feature'ını (`__category__ == "place_of_worship"`,
    geometry "Point") `ReligiousStructureItem`'a çevirir. Kategori
    eşleşmiyorsa veya `amenity=place_of_worship` tag'i yoksa `None`
    döner (çağıran taraf sessizce atlar)."""
    from . import ReligiousStructureGenerator  # gecikmeli import (döngüsel import önlemi)

    if feature.geometry_type != "Point":
        return None
    if feature.properties.get("__category__") != "place_of_worship":
        return None
    x, y = feature.coordinates
    return ReligiousStructureGenerator.from_osm_tags(
        feature.properties,
        Point2D(x, y),
        ground_z=0.0,
    )


def generate_religious_structures_for_collection(collection: GeoFeatureCollection) -> list:
    """Faz C3 (4. dilim) uçtan uca giriş noktası: bir
    `GeoFeatureCollection`'daki tüm `place_of_worship` feature'larını
    `ReligiousStructureItem` listesine indirger (mesh üretimi çağıran
    tarafa bırakılır — `vegetation`/`street_furniture` köprüleriyle aynı
    tasarım kararı, Faz C4 instancing/batching için erken mesh
    üretimini dayatmaz)."""
    items = []
    for feature in collection.features:
        item = religious_structure_item_from_point(feature)
        if item is not None:
            items.append(item)
    return items


__all__ = [
    "religious_structure_item_from_point",
    "generate_religious_structures_for_collection",
]
