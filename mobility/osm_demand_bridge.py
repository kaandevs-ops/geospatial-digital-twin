"""
mobility.osm_demand_bridge - OSM POI -> Yolculuk Talebi Köprüsü (Katman 3.1)
==============================================================================

Roadmap V9 / Faz VI / Katman 3.1 (Şehir Ölçeği Yaya Akışı):

"POI kaynağı: OSM'den amenity=school, shop=*, public_transport=stop_position
tag'leri - street_furniture/osm_bridge.py, power_infrastructure/osm_bridge.py
örnek alınarak mobility/osm_demand_bridge.py eklenir."

Bu modül `street_furniture/osm_bridge.py` ile birebir aynı deseni izler:
mevcut mimari (`core_engine.gis_core.osm_client` kategori damgalı
`GeoFeature`'lar, `population.activity_model.ActivityType`) DEĞİŞTİRİLMEDEN,
yalnızca ikisi arasında bir eşleme/köprü kurar - yeni bir OSM istemcisi
veya yeni bir aktivite tipi icat edilmez.

Çıktısı: `POIPoint` listesi (aktivite tipine damgalı gerçek koordinat) +
bunları `population.activity_model.ODDemandEntry`'lerle eşleştirip somut
`(origin_xy, destination_xy)` çiftine indirgeyen `resolve_demand_locations`.
Bu, Katman 2.2'nin ürettiği soyut talebi Katman 3'ün (traffic/transit/
crowd) tüketebileceği somut koordinatlara çeviren tek adımdır.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..core_engine.geometry_engine import Point2D
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection
from ..population.activity_model import ActivityType, ODDemandEntry

#: Roadmap 3.1 metnindeki OSM tag'lerinin `osm_client.DEFAULT_CATEGORIES`
#: anahtarlarına eşlemesi - `street_furniture/osm_bridge.py`'deki
#: `CATEGORY_KEY_TO_OSM_TAG` deseniyle aynı fikir, yön tersine çevrilmiş
#: (kategori anahtarı -> aktivite tipi).
CATEGORY_KEY_TO_ACTIVITY_TYPE: dict[str, ActivityType] = {
    "school": ActivityType.SCHOOL,
    "shopping_mall": ActivityType.LOCAL_ERRAND,
    "supermarket": ActivityType.LOCAL_ERRAND,
    "marketplace": ActivityType.LOCAL_ERRAND,
    "restaurant": ActivityType.LUNCH_BREAK,
    "cafe": ActivityType.LUNCH_BREAK,
}

#: İşyeri/ofis aktivitesi için tek bir açık nokta kategorisi OSM'de yoktur
#: (roadmap metninde de açıkça "iş" için özel bir tag anılmaz) - bu bilinçli
#: bir sınır: `ActivityType.WORK` hedefleri bu köprüde POI'den değil,
#: çağıran tarafın ticari/ofis bina envanterinden (ör. `commerce_props`,
#: mevcut bina deposu) çözülmelidir. `resolve_demand_locations` bu durumda
#: `POIResolutionWarning` biriktirir, sessizce yanlış konum üretmez.


@dataclass(slots=True)
class POIPoint:
    """Tek bir aktivite-hedefi POI'si - gerçek OSM konumu + kategori."""

    activity_type: ActivityType
    position: Point2D
    category_key: str
    name: str | None = None


def build_poi_points(collection: GeoFeatureCollection) -> list[POIPoint]:
    """Bir `GeoFeatureCollection`'daki (osm_client çıktısı) noktaları/
    poligonları `POIPoint` listesine çevirir. Yalnızca
    `CATEGORY_KEY_TO_ACTIVITY_TYPE`'ta bilinen kategoriler işlenir; diğer
    özellikler (yol, bina vb.) bu köprünün kapsamı dışındadır (mevcut
    kategori damgası `properties["category"]` üzerinden okunur - osm_client
    ile aynı sözleşme)."""
    points: list[POIPoint] = []
    for feature in collection:
        category_key = feature.properties.get("category")
        activity_type = CATEGORY_KEY_TO_ACTIVITY_TYPE.get(category_key)
        if activity_type is None:
            continue
        position = _feature_representative_point(feature)
        if position is None:
            continue
        points.append(
            POIPoint(
                activity_type=activity_type,
                position=position,
                category_key=category_key,
                name=feature.properties.get("name"),
            )
        )
    return points


def _feature_representative_point(feature: GeoFeature) -> Point2D | None:
    """Polygon için basit centroid (halka ortalaması - tam alan-ağırlıklı
    centroid değil, POI seçimi için yeterli hassasiyette bir yaklaşım,
    roadmap'in gösterge disipliniyle tutarlı); Point için doğrudan nokta."""
    if feature.geometry_type == "Point":
        return feature.to_point()
    if feature.geometry_type == "Polygon":
        ring = feature.coordinates[0]
        if not ring:
            return None
        avg_x = sum(c[0] for c in ring) / len(ring)
        avg_y = sum(c[1] for c in ring) / len(ring)
        return Point2D(avg_x, avg_y)
    return None


@dataclass(slots=True)
class ResolvedDemand:
    """Somut koordinatlara indirgenmiş yolculuk talebi - `mobility.
    traffic_simulation`/`transit_simulation`/`crowd_simulation`'ın rota
    hesaplaması için doğrudan kullanılabilir girdi."""

    individual_id: str
    departure_hour: float
    origin_activity: ActivityType
    destination_activity: ActivityType
    destination_position: Point2D
    destination_poi_name: str | None = None


class PODemandResolutionWarning(RuntimeError):
    """`resolve_demand_locations`'ın sessizce yanlış konum üretmek yerine
    fırlattığı/biriktirdiği uyarı - roadmap ilkesi #1 (gösterge disiplini,
    yanlış kesinlik hissi vermemek) ile tutarlı."""


def resolve_demand_locations(
    demand: list[ODDemandEntry],
    poi_points: list[POIPoint],
    *,
    home_position_lookup: dict[str, Point2D] | None = None,
    seed: int | None = None,
) -> tuple[list[ResolvedDemand], list[str]]:
    """Her `ODDemandEntry.destination_activity` için uygun bir `POIPoint`
    seçer (aynı tipteki POI'ler arasından seed'li rastgele seçim - gerçek
    en-yakın-POI ataması değil, roadmap'in "kaba yaklaşım" disipliniyle
    tutarlı bir basitleştirme; çağıran taraf isterse `poi_points`'i önceden
    mesafeye göre filtreleyebilir).

    `ActivityType.HOME` hedefleri `home_position_lookup[household_id]`'den
    (verilmişse) çözülür - aksi halde uyarı listesine düşer, sessizce
    atlanmaz.

    Dönüş: `(resolved, warnings)` - `warnings` insan-okunabilir uyarı
    metinleri (ör. "WORK için POI kaynağı yok - iş yeri bina envanterinden
    çözülmeli").
    """
    rng = random.Random(seed)
    by_activity: dict[ActivityType, list[POIPoint]] = {}
    for poi in poi_points:
        by_activity.setdefault(poi.activity_type, []).append(poi)

    resolved: list[ResolvedDemand] = []
    warnings: list[str] = []
    home_lookup = home_position_lookup or {}

    for entry in demand:
        target_activity = entry.destination_activity
        if target_activity == ActivityType.HOME:
            position = home_lookup.get(entry.household_id)
            if position is None:
                warnings.append(
                    f"HOME için konum yok (household_id={entry.household_id}) - "
                    "home_position_lookup'a eklenmeli, sessizce atlanmadı."
                )
                continue
            resolved.append(
                ResolvedDemand(
                    individual_id=entry.individual_id,
                    departure_hour=entry.departure_hour,
                    origin_activity=entry.origin_activity,
                    destination_activity=target_activity,
                    destination_position=position,
                )
            )
            continue

        candidates = by_activity.get(target_activity)
        if not candidates:
            if target_activity == ActivityType.WORK:
                warnings.append(
                    "WORK için POI kaynağı OSM'de doğrudan yok (roadmap'in "
                    "bilinçli sınırı) - iş yeri konumu bina/ticaret "
                    "envanterinden ayrıca çözülmeli."
                )
            else:
                warnings.append(
                    f"{target_activity.value} için eşleşen POI bulunamadı - "
                    "sağlanan GeoFeatureCollection bu kategoriyi içermiyor olabilir."
                )
            continue

        chosen = rng.choice(candidates)
        resolved.append(
            ResolvedDemand(
                individual_id=entry.individual_id,
                departure_hour=entry.departure_hour,
                origin_activity=entry.origin_activity,
                destination_activity=target_activity,
                destination_position=chosen.position,
                destination_poi_name=chosen.name,
            )
        )

    return resolved, warnings
