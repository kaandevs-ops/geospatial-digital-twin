"""
mobility.open_area_navgraph - Açık-Alan Yaya Erişilebilirlik Graf'ı (Katman 3.1)
====================================================================================

Roadmap V9 / Faz VI / Katman 3.1 madde 3:

"Açık-alan NavGraph üretimi: pathfinding.NavGraph şu an kapalı bina grafiği
veya yol ağı için kullanılıyor; kaldırım/meydan gibi açık alanlar için
'yaya erişilebilirlik graf'ı' gerekiyor - core_engine/geometry_engine'deki
poligon/alan verisinden türetilir (yol ağının kaldırım genişliği/eğim
bilgisiyle zenginleştirilmiş hali)."

Bu modül `mobility.pathfinding.NavGraph`'ı **yeniden kullanır** (roadmap
ilkesi #2) — yeni bir graf veri yapısı icat etmez, yalnızca
`GeoFeatureCollection`'daki `LineString` (kaldırım/yol) ve `Polygon`
(meydan/park) geometrisini `NavGraph` düğüm/kenarlarına çevirir.

**Dürüstlük notu (bilinçli kapsam sınırı):** Roadmap metni "kaldırım
genişliği/eğim bilgisiyle zenginleştirilmiş" der - mevcut `osm_client`
kategorileri (`roads`, `park`, `grass` vb.) OSM'den `width`/`incline` tag
değerlerini `GeoFeature.properties`'e zaten aktarıyor (ham Overpass
`tags` sözlüğü korunuyor), ama bu modül şu an yalnızca **geometriyi**
(düğüm/kenar) işliyor; genişlik/eğim kenar maliyetine henüz **bilinçli
olarak** yansıtılmadı (`edge_cost_multiplier_from_properties` adında ayrı,
isteğe bağlı bir kanca bırakıldı - çağıran taraf `properties` sözlüğünü
kullanarak kendi çarpanını uygulayabilir, sessizce "eğim=0" varsayılmaz).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core_engine.geometry_engine import Point2D
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection
from .pathfinding import NavGraph, NodeId

#: Katman 3.1'in kapsadığı "açık alan / yaya erişimi" kategorileri -
#: `osm_client.DEFAULT_CATEGORIES`'te zaten var olan anahtarlar
#: (yeni kategori icat edilmedi). `roads` dahil edilmiyor - araç yolu ayrı
#: bir ağ (`traffic_simulation`), burada yalnızca yaya-öncelikli/açık-alan
#: kategorileri işlenir.
OPEN_AREA_LINE_CATEGORIES: frozenset[str] = frozenset({"roads"})
OPEN_AREA_POLYGON_CATEGORIES: frozenset[str] = frozenset({"park", "grass", "pitch", "playground"})


def _snap(point: Point2D, precision: float) -> NodeId:
    """Koordinatı `precision` çözünürlüğüne yuvarlayarak düğüm kimliğine
    çevirir - farklı feature'lardan gelen (kayan-nokta hassasiyeti yüzünden
    birebir eşleşmeyen) ama gerçekte aynı olan uç noktaları birleştirmek
    için (ör. bir kaldırımın bir meydana değdiği köşe)."""
    return (round(point.x / precision) * precision, round(point.y / precision) * precision)


@dataclass(slots=True)
class OpenAreaNavGraphBuilder:
    """`GeoFeatureCollection`'dan açık-alan yaya `NavGraph`'ı kurar.

    `snap_precision`: metre cinsinden (veya kaynak CRS biriminde) uç-nokta
    birleştirme toleransı - iki ayrı feature'ın uçları bu mesafe içindeyse
    aynı düğüm sayılır (basit ızgara-tabanlı snapping, tam topoloji
    düzeltmesi değil - roadmap'in gösterge disipliniyle tutarlı bir
    basitleştirme).
    """

    snap_precision: float = 0.5
    line_categories: frozenset[str] = OPEN_AREA_LINE_CATEGORIES
    polygon_categories: frozenset[str] = OPEN_AREA_POLYGON_CATEGORIES

    def build(self, collection: GeoFeatureCollection) -> NavGraph:
        graph = NavGraph()
        for feature in collection:
            category_key = feature.properties.get("category")
            if feature.geometry_type == "LineString" and category_key in self.line_categories:
                self._add_linestring(graph, feature)
            elif feature.geometry_type == "Polygon" and category_key in self.polygon_categories:
                self._add_polygon_perimeter(graph, feature)
        return graph

    def _ensure_node(self, graph: NavGraph, point: Point2D) -> NodeId:
        node_id = _snap(point, self.snap_precision)
        if not graph.has_node(node_id):
            graph.add_node(node_id, point)
        return node_id

    def _add_linestring(self, graph: NavGraph, feature: GeoFeature) -> None:
        line = feature.to_linestring()
        if len(line.points) < 2:
            return
        prev_id = self._ensure_node(graph, line.points[0])
        for point in line.points[1:]:
            node_id = self._ensure_node(graph, point)
            if node_id != prev_id:
                graph.add_edge(prev_id, node_id)
            prev_id = node_id

    def _add_polygon_perimeter(self, graph: NavGraph, feature: GeoFeature) -> None:
        polygon = feature.to_polygon()
        ring = polygon.closed_ring()
        if len(ring) < 3:
            return
        prev_id = self._ensure_node(graph, ring[0])
        for point in ring[1:]:
            node_id = self._ensure_node(graph, point)
            if node_id != prev_id:
                graph.add_edge(prev_id, node_id)
            prev_id = node_id


def edge_cost_multiplier_from_properties(
    properties: dict,
    *,
    default: float = 1.0,
) -> float:
    """Roadmap'in "kaldırım genişliği/eğim bilgisiyle zenginleştirilmiş"
    notunun isteğe bağlı kancası. OSM `width` (metre) ve `incline`
    (yüzde ya da 'up'/'down' string'i olabilir - OSM şeması tutarsız
    olduğundan yalnızca sayısal olanlar işlenir) tag'lerinden kaba bir
    maliyet çarpanı üretir - dar kaldırım/dik eğim -> daha yüksek maliyet.
    Değer bulunamazsa `default` döner (sessizce 1.0 varsayılmaz, çağıran
    taraf `default` parametresiyle bunu bilinçli olarak seçer).
    """
    multiplier = default
    width = properties.get("width")
    try:
        width_val = float(width) if width is not None else None
    except (TypeError, ValueError):
        width_val = None
    if width_val is not None and width_val > 0:
        # 2m referans genişlik - dar kaldırımlarda maliyet artar (kaba oran).
        multiplier *= max(1.0, 2.0 / width_val)

    incline = properties.get("incline")
    try:
        incline_val = abs(float(str(incline).rstrip("%"))) if incline is not None else None
    except (TypeError, ValueError):
        incline_val = None
    if incline_val is not None:
        # her %5 eğim için ~%10 ek maliyet - kaba, gösterge niteliğinde bir yaklaşım.
        multiplier *= 1.0 + (incline_val / 5.0) * 0.1

    return multiplier


def find_nearest_node(graph: NavGraph, point: Point2D) -> NodeId | None:
    """Verilen koordinata en yakın graf düğümünü döner (köprü-düğüm
    ataması için - Katman 8.1'in "çıkış kapısı = dış graf'ın node'u"
    notuyla aynı ihtiyaç, burada açık-alan tarafı için). Boş graf için
    `None` döner."""
    best_id: NodeId | None = None
    best_dist = float("inf")
    for node_id, position in graph.positions.items():
        dist = position.distance_to(point)
        if dist < best_dist:
            best_dist = dist
            best_id = node_id
    return best_id
