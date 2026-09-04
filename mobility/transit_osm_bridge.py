"""
mobility.transit_osm_bridge - OSM Durak Verisi -> TransitStop Köprüsü (Katman 3.2)
=====================================================================================

Roadmap V9 / Faz VI / Katman 3.2 (Metro / Otobüs / İstasyon Yoğunluğu):

"Gerçek hat/durak verisi: mobility/transit_osm_bridge.py - OSM'den
route=bus/route=subway ve public_transport=stop_position çeker
(osm_client.py tekrar yazılmaz, sorgu genişletilir)."

`street_furniture/osm_bridge.py` / `mobility/osm_demand_bridge.py` ile aynı
desen: `core_engine.gis_core.osm_client`'ın kategori damgalı `GeoFeature`
çıktısını `mobility.transit_simulation.TransitStop`'a eşler.

**Dürüstlük notu (bilinçli kapsam sınırı, roadmap'in kendi ilkesiyle
tutarlı):** OSM'de `route=bus`/`route=subway` gerçek güzergahlar bir
*relation* (üye-sıralı ilişki) olarak modellenir; mevcut
`core_engine.gis_core.osm_client` yalnızca düz node/way/relation geometri
sorgusu (`DEFAULT_CATEGORIES` -> `Point`/`LineString`/`Polygon`) üretir,
relation üyelik sırasını (bir hattın durak sırasını) çözümlemez - bu, ayrı
ve önemli ölçüde daha karmaşık bir Overpass relation-üyelik ayrıştırıcısı
gerektirir. Bu modül bu yüzden **durakları** (`bus_stop`, `railway_station`,
`subway_entrance`, `transit_stop_position` kategorileri - hepsi zaten
`osm_client.DEFAULT_CATEGORIES`'te var) gerçek konumlarıyla `TransitStop`'a
çevirir; bir `TransitLine`'ın durak *sırasını* ise (roadmap'in kendi
sözünde bıraktığı "iskelet" ölçeğinde) basit en-yakın-komşu sıralamasıyla
**tahmini olarak** kurar ve bunu sonuçta `is_estimated_sequence=True` ile
açıkça işaretler - sessizce gerçek güzergah gibi sunulmaz.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core_engine.geometry_engine import Point2D
from ..core_engine.gis_core import GeoFeatureCollection
from .transit_simulation import TransitLine, TransitStop

#: Roadmap 3.2 durak kategorileri - hepsi `osm_client.DEFAULT_CATEGORIES`'te
#: zaten mevcut (Faz 2.1 + Faz VI eklemesi), yeni kategori icat edilmedi.
TRANSIT_STOP_CATEGORY_KEYS: frozenset[str] = frozenset({
    "bus_stop",
    "bus_station",
    "railway_station",
    "subway_entrance",
    "transit_stop_position",
})


def build_transit_stops(collection: GeoFeatureCollection) -> list[TransitStop]:
    """`GeoFeatureCollection`'daki durak-kategorili noktaları
    `TransitStop` listesine çevirir (`walk_node=None` - yürüme grafiğine
    bağlanma `mobility.osm_demand_bridge`/açık-alan NavGraph'ın işi,
    Katman 3.1'in "köprü düğüm" notuyla aynı ayrım)."""
    stops: list[TransitStop] = []
    seen_ids: set[str] = set()
    for feature in collection:
        category_key = feature.properties.get("category")
        if category_key not in TRANSIT_STOP_CATEGORY_KEYS:
            continue
        if feature.geometry_type != "Point":
            continue
        osm_id = feature.properties.get("id") or feature.properties.get("osm_id")
        stop_id = str(osm_id) if osm_id is not None else f"{category_key}:{len(stops)}"
        if stop_id in seen_ids:
            continue
        seen_ids.add(stop_id)
        stops.append(TransitStop(stop_id=stop_id, position=feature.to_point()))
    return stops


@dataclass(slots=True)
class EstimatedTransitLine:
    """`build_transit_stops`'un çıktısından, en-yakın-komşu sıralamasıyla
    tahmini olarak kurulan bir hat taslağı - `TransitLine` üretmek için
    gereken `travel_times_s`/`schedule_headway_s` gibi tarife bilgisi
    OSM nokta sorgusunda yoktur, bu yüzden `TransitLine`'a çevrilmeden
    önce çağıran tarafın bu alanları (gerçek tarife verisi veya makul bir
    varsayım) doldurması gerekir - sessizce uydurma sefer sıklığı
    atanmaz."""

    line_id: str
    ordered_stop_ids: list[str]
    is_estimated_sequence: bool = True

    def to_transit_line(
        self,
        *,
        travel_times_s: list[float],
        schedule_headway_s: float,
        first_departure_s: float = 0.0,
        capacity: int = 60,
    ) -> TransitLine:
        return TransitLine(
            line_id=self.line_id,
            stops=list(self.ordered_stop_ids),
            travel_times_s=travel_times_s,
            schedule_headway_s=schedule_headway_s,
            first_departure_s=first_departure_s,
            capacity=capacity,
        )


def estimate_line_from_stops(
    line_id: str, stops: list[TransitStop], *, start_stop_id: str | None = None,
) -> EstimatedTransitLine:
    """En-yakın-komşu (greedy nearest-neighbour) sıralamasıyla bir durak
    kümesinden tahmini hat sırası üretir. Gerçek güzergah topolojisi
    bilinmediğinden (yukarıdaki dürüstlük notu) bu yalnızca bir başlangıç
    taslağıdır - `EstimatedTransitLine.is_estimated_sequence` her zaman
    `True`'dur."""
    if len(stops) < 2:
        raise ValueError("estimate_line_from_stops: en az 2 durak gerekli")

    remaining = list(stops)
    if start_stop_id is not None:
        start = next((s for s in remaining if s.stop_id == start_stop_id), remaining[0])
    else:
        start = remaining[0]
    remaining.remove(start)

    ordered = [start]
    current = start
    while remaining:
        nearest = min(remaining, key=lambda s: _distance(current.position, s.position))
        ordered.append(nearest)
        remaining.remove(nearest)
        current = nearest

    return EstimatedTransitLine(
        line_id=line_id,
        ordered_stop_ids=[s.stop_id for s in ordered],
    )


def _distance(a: Point2D, b: Point2D) -> float:
    return a.distance_to(b)


def hourly_occupancy_report(
    vehicle_occupancy_samples: list[tuple[float, int, int]], *, dense_threshold: float = 0.75,
) -> dict[int, dict[str, float]]:
    """Roadmap 3.2 madde 1 ("Saatlik yoğunluk raporu"): `(t_saniye,
    dolu_koltuk, kapasite)` örneklerinden (ör. `O.1 SimulationRecorder`
    ile toplanmış `TransitVehicle` doluluk zaman serisi) saat başına
    ortalama doluluk oranı + "yoğun" etiketi üretir. Yeni bir kayıt
    mekanizması icat edilmez - mevcut zaman-damgalı örnekleme
    `mobility.simulation_recorder`'ın deseniyle tutarlıdır, burada
    yalnızca saatlik özetlenir.
    """
    buckets: dict[int, list[float]] = {}
    for t_s, occupied, capacity in vehicle_occupancy_samples:
        if capacity <= 0:
            continue
        hour = int((t_s / 3600.0) % 24)
        buckets.setdefault(hour, []).append(occupied / capacity)

    report: dict[int, dict[str, float]] = {}
    for hour, ratios in buckets.items():
        avg_ratio = sum(ratios) / len(ratios)
        report[hour] = {
            "avg_occupancy_ratio": avg_ratio,
            "sample_count": len(ratios),
            "is_dense": avg_ratio >= dense_threshold,
        }
    return report
