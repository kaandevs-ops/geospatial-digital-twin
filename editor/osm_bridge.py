"""
editor.osm_bridge - OSM Kategori Feature'larından Yol/Su Üretimi
====================================================================

ROADMAP_V7.md Faz C3 (2. dilim) — Bölüm B2'nin "çizgi feature" stratejisini
(yol, hat, nehir: "merkez hattı boyunca genişliğe göre extrude/şerit
üretimi, tip bazlı doku/renk") ve "alan feature" stratejisini (su: "poligon
offset + extrude, yüzey doku ataması") `core_engine.gis_core.osm_client`'ın
ürettiği kategori damgalı `GeoFeature`'lara bağlar.

`vegetation/osm_bridge.py` ile aynı desen izlenir: mevcut mimari (bu kez
`editor.road_editor.Road` — zaten "tape extrusion" ile şerit üreten Faz 8
bileşeni, ve `mesh_engine.MeshBuilder.extrude_polygon` — Faz 2 bileşeni)
DEĞİŞTİRİLMEDEN, yalnızca OSM verisiyle beslenir (roadmap'in "mevcut mimari
korunacak" ilkesi).

Kapsam
------
- `roads` (`highway=*`, LineString) -> `Road` (tip bazlı varsayılan genişlik
  tablosu, B1: "her tip için farklı genişlik/kaplama/renk" — kaplama/renk
  malzeme katmanına ait olduğundan burada yalnızca genişlik + `road_type`
  properties'e damgalanır, `material_engine` tarafında tüketilebilir).
- `waterway` (`waterway=*`, LineString) -> `Road`'a benzer şerit, ama düz bir
  su yüzeyi olarak (`elevation_z` hafif negatif — B2 "alan feature" için
  zemine gömülü su mantığıyla tutarlı, terrain ile z-fighting'i önler).
- `water_area` (`natural=water`, Polygon) -> `MeshBuilder.extrude_polygon`
  ile ince (varsayılan 0.2 m) düz bir su yüzeyi prizması.

ROADMAP_V8 Faz 2.1/2.4 eklentisi
---------------------------------
- `railway_rail`/`railway_subway`/`railway_tram` (`railway=*`, LineString)
  -> mevcut `Road` tape-extrusion altyapısı, yol tablosuyla aynı desende
  ayrı bir demiryolu genişlik tablosuyla (`DEFAULT_RAILWAY_WIDTH_M`).
- `parking`/`grass`/`park`/`flowerbed`/`farmland` (Polygon) ->
  `mesh_for_water_area` ile aynı `extrude_polygon` deseni, kategoriye özel
  ince zemin katmanı (`AREA_CATEGORY_THICKNESS_M`) — B1'in "zemin dokusu"
  isteğinin minimum viable karşılığı (gerçek doku ataması `material_engine`
  katmanına ait).
- `coastline` (`natural=coastline`, LineString) -> `waterway` ile aynı ince
  gömülü şerit deseni (B1: "kıyı hizalama" için kaba yaklaşıklama).
- `administrative_boundary` (`boundary=administrative`, LineString) -> B1'de
  açıkça "3D model değil, etiket/overlay" denmiş; bu yüzden mesh üretmez,
  yalnızca harita katmanı (Leaflet/GeoJSON) için `administrative_boundary_
  geojson` ile ham koordinat listesi döner.

Girdi olarak (vegetation/osm_bridge.py ile aynı önkoşul) zaten
`osm_client.project_to_local_meters` ile metreye projekte edilmiş bir
`GeoFeatureCollection` beklenir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core_engine.geometry_engine import Point2D, Polygon
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection
from ..mesh_engine import Mesh3D, MeshBuilder
from .road_editor import Road

#: B1 — "yol tipine göre farklı genişlik" (OSM Wiki `highway=*` tipik
#: genişlik kılavuzuna göre kaba, metre cinsinden varsayılanlar). OSM'de
#: `width` tag'i doğrudan verilmişse o değer bu tablonun önüne geçer (bkz.
#: `_road_width_m`).
DEFAULT_ROAD_WIDTH_M: dict[str, float] = {
    "motorway": 11.0,
    "trunk": 9.0,
    "primary": 8.0,
    "secondary": 7.0,
    "tertiary": 6.0,
    "residential": 5.5,
    "living_street": 4.5,
    "service": 3.5,
    "pedestrian": 3.0,
    "footway": 1.8,
    "path": 1.2,
    "cycleway": 2.0,
    "track": 3.0,
    "steps": 1.5,
}
DEFAULT_ROAD_WIDTH_FALLBACK_M = 5.0

#: `waterway=*` alt tipine göre kaba genişlik varsayılanı (dere/nehir ayrımı
#: — gerçek genişlik nadiren tag'li olduğu için tür bazlı ortalama, B4'ün
#: "tür bazlı ortalama tablo" heuristiğiyle tutarlı).
DEFAULT_WATERWAY_WIDTH_M: dict[str, float] = {
    "river": 12.0,
    "canal": 8.0,
    "stream": 2.0,
    "drain": 1.5,
    "ditch": 1.0,
}
DEFAULT_WATERWAY_WIDTH_FALLBACK_M = 2.0

#: Su yüzeyleri terrain'in az altına gömülür (z-fighting önlemi + görsel
#: olarak "kıyı hizalama" hissi — ROADMAP_V7 A/B'nin doğrudan çözmediği,
#: ama B2'nin "kıyı çizgisi hizalama" notuyla tutarlı kaba bir yaklaşım).
DEFAULT_WATER_DEPTH_OFFSET_M = 0.3
DEFAULT_WATER_AREA_THICKNESS_M = 0.2

#: B1 — "Kavşak/köprü/tünel ayrımı (`bridge=yes`, `tunnel=yes`) — farklı Z
#: seviyesinde render." `Road.elevation_z` OSM `bridge`/`tunnel` tag'ine
#: göre kaldırılır/gömülür (terrain ile z-fighting/gömülme çakışmasını
#: önler). ROADMAP_V8 Faz 6.2 öncesinde gerçek köprü ayağı/korkuluk
#: geometrisi bilinçli olarak üretilmiyordu; Faz 6.2 ile `bridge=yes`
#: yollar artık `street_furniture.infrastructure.BridgeGenerator`
#: (tabliye + korkuluk + ayak) üzerinden üretiliyor — bkz.
#: `mesh_for_bridge_road` / Faz 6.2 notu aşağıda.
DEFAULT_BRIDGE_CLEARANCE_M = 5.0
DEFAULT_TUNNEL_DEPTH_M = -4.0

#: ROADMAP_V8 Faz 2.1 — demiryolu tipine göre görsel genişlik (gerçek ray
#: aralığı değil, `Road` şerit-extrude'unun görsel olarak ayırt edilebilir
#: olması için kaba varsayılan — tramvay/metro/normal hat arasında görünür
#: fark yaratacak şekilde seçildi).
DEFAULT_RAILWAY_WIDTH_M: dict[str, float] = {
    "railway_rail": 1.8,
    "railway_subway": 4.0,
    "railway_tram": 3.0,
}
DEFAULT_RAILWAY_WIDTH_FALLBACK_M = 1.8

#: ROADMAP_V8 Faz 2.4 — alan (Polygon) kategorilerinin zemin katmanı
#: kalınlığı, `water_area`'nın `DEFAULT_WATER_AREA_THICKNESS_M` deseniyle
#: aynı mantık (ince, düz prizma — gerçek doku/malzeme `material_engine`'e
#: bırakılır).
AREA_CATEGORY_THICKNESS_M: dict[str, float] = {
    "parking": 0.05,
    "grass": 0.05,
    "park": 0.05,
    "flowerbed": 0.06,
    "farmland": 0.04,
}
DEFAULT_AREA_CATEGORY_THICKNESS_FALLBACK_M = 0.05


#: ROADMAP_V8 Faz 5.6a — "her tip için farklı genişlik/kaplama/renk"
#: isteğinin kaplama/renk kısmı (genişlik zaten `DEFAULT_ROAD_WIDTH_M`
#: ile karşılanıyordu). `material_engine.ProceduralMaterials` Faz 5.1'de
#: eklenen preset'lere (`asfalt`, `beton_parke`, `kum`, `toprak`)
#: doğrudan işaret eder — yeni bir malzeme sistemi icat edilmez, mevcut
#: preset kütüphanesi yeniden kullanılır. `_road_surface_material` bu
#: tabloyu okuyup `properties["__surface_material__"]`'a damgalar; asıl
#: malzeme ataması (materyal → mesh eşlemesi) render/session katmanında
#: (`app_shell/session.py`) `extra` metadata olarak taşınır — B1'in
#: `vegetation` köprüsünde zaten kullanılan "kategori + extra metadata"
#: deseniyle tutarlı (yeni bir mimari kavram eklenmez).
ROAD_SURFACE_STYLE: dict[str, str] = {
    "motorway": "asfalt",
    "trunk": "asfalt",
    "primary": "asfalt",
    "secondary": "asfalt",
    "tertiary": "asfalt",
    "residential": "asfalt",
    "living_street": "beton_parke",
    "service": "asfalt",
    "pedestrian": "beton_parke",
    "footway": "beton_parke",
    "path": "toprak",
    "cycleway": "beton_parke",
    "track": "toprak",
    "steps": "beton_parke",
}
DEFAULT_ROAD_SURFACE_FALLBACK = "asfalt"

#: Şerit çizgisi yalnızca ana yollarda üretilir (B3 performans ilkesi:
#: patika/yaya yoluna uygulanmaz — roadmap'in kendi notu).
LANE_MARKING_HIGHWAY_TYPES: frozenset[str] = frozenset(
    {"motorway", "trunk", "primary", "secondary", "tertiary"}
)


def _road_surface_material(tags: dict) -> str:
    """OSM `surface` tag'i varsa (gerçek veri önceliği) onu değilse
    `highway` tipine göre `ROAD_SURFACE_STYLE`'a düşer."""
    raw_surface = str(tags.get("surface", "")).lower()
    _OSM_SURFACE_TO_PRESET = {
        "asphalt": "asfalt",
        "paved": "asfalt",
        "concrete": "beton_parke",
        "paving_stones": "beton_parke",
        "sett": "beton_parke",
        "gravel": "toprak",
        "dirt": "toprak",
        "ground": "toprak",
        "sand": "kum",
        "unpaved": "toprak",
    }
    if raw_surface in _OSM_SURFACE_TO_PRESET:
        return _OSM_SURFACE_TO_PRESET[raw_surface]
    highway_type = tags.get("highway", "")
    return ROAD_SURFACE_STYLE.get(highway_type, DEFAULT_ROAD_SURFACE_FALLBACK)


def _linestring_points(feature: GeoFeature) -> list[Point2D]:
    if feature.geometry_type != "LineString":
        raise ValueError(f"LineString geometrisi beklenir, gelen: {feature.geometry_type!r}")
    return [Point2D(x, y) for x, y in feature.coordinates]


def _road_width_m(tags: dict) -> float:
    """OSM `width` tag'i varsa onu kullanır (B1/B2 gerçek veri önceliği),
    yoksa `highway` alt tipine göre `DEFAULT_ROAD_WIDTH_M` tablosuna düşer."""
    raw = tags.get("width")
    if raw is not None:
        try:
            value = float(str(raw).strip().rstrip("m").strip())
            if value > 0:
                return value
        except ValueError:
            pass
    highway_type = tags.get("highway", "")
    return DEFAULT_ROAD_WIDTH_M.get(highway_type, DEFAULT_ROAD_WIDTH_FALLBACK_M)


def _waterway_width_m(tags: dict) -> float:
    raw = tags.get("width")
    if raw is not None:
        try:
            value = float(str(raw).strip().rstrip("m").strip())
            if value > 0:
                return value
        except ValueError:
            pass
    waterway_type = tags.get("waterway", "")
    return DEFAULT_WATERWAY_WIDTH_M.get(waterway_type, DEFAULT_WATERWAY_WIDTH_FALLBACK_M)


def _road_elevation_z(tags: dict) -> float:
    """B1: "Kavşak/köprü/tünel ayrımı ... farklı Z seviyesinde render."
    OSM `bridge=yes` -> zeminin üstünde (`DEFAULT_BRIDGE_CLEARANCE_M`),
    `tunnel=yes` -> zeminin altında (`DEFAULT_TUNNEL_DEPTH_M`), ikisi de
    yoksa/`no` ise zemin seviyesi (`0.0`, önceki davranışla birebir
    aynı — geriye dönük uyumluluk). `bridge` ve `tunnel` aynı anda
    `yes` olması OSM'de anlamsız bir kombinasyondur (B4 "eksik/çelişkili
    tag" ilkesi); bu durumda köprü önceliklidir (daha görünür/yaygın
    hata modu, sahneyi bozmayan bir seçim)."""
    if str(tags.get("bridge", "")).lower() == "yes":
        return DEFAULT_BRIDGE_CLEARANCE_M
    if str(tags.get("tunnel", "")).lower() == "yes":
        return DEFAULT_TUNNEL_DEPTH_M
    return 0.0


def road_from_linestring_feature(feature: GeoFeature, name: str | None = None) -> Road:
    """Tekil bir OSM `highway=*` (`__category__ == "roads"`, LineString)
    feature'ını, kontrol noktaları doğrudan OSM koordinatları olan bir
    `Road`'a çevirir (B2 "çizgi feature" stratejisi).

    Not: OSM yol geometrisi zaten yeterince yoğun örneklenmiş olduğundan
    `samples_per_segment=1` kullanılır — `Road.to_mesh()`'in Catmull-Rom
    düzeltmesi burada istenmeyen bir yumuşatma/kestirme etkisi yaratmasın
    diye (spline sadece editörde elle çizilen az sayıda kontrol noktası
    için gereklidir)."""
    points = _linestring_points(feature)
    tags = feature.properties
    osm_id = tags.get("osm_id", "road")
    return Road(
        road_id=f"osm_{osm_id}",
        control_points=points,
        width_m=_road_width_m(tags),
        elevation_z=_road_elevation_z(tags),
        samples_per_segment=1,
        name=name or f"osm_road_{osm_id}",
    )


def mesh_for_road(road: Road) -> Mesh3D:
    """`Road` -> `Mesh3D` (mevcut `Road.to_mesh()` tape-extrusion'ı,
    değiştirilmeden tüketilir)."""
    return road.to_mesh()


def _is_bridge_tag(tags: dict) -> bool:
    return str(tags.get("bridge", "")).lower() == "yes"


def _is_tunnel_tag(tags: dict) -> bool:
    """`bridge=yes` önceliklidir (bkz. `_road_elevation_z` docstring'i,
    B4 'çelişkili tag' ilkesi) — bu yüzden burada da aynı öncelik
    korunur: `bridge=yes` ise tünel yolu tetiklenmez."""
    if _is_bridge_tag(tags):
        return False
    return str(tags.get("tunnel", "")).lower() == "yes"


def mesh_for_bridge_road(feature: GeoFeature) -> Mesh3D:
    """ROADMAP_V8 Faz 6.2 — köprü/su yüzeyi/peyzaj tamamlaması (köprü ayağı).

    V7/erken V8'de `bridge=yes` yollar yalnızca `Road.elevation_z` ile
    kaldırılıyordu (düz, tek şerit tabliye — ayak/korkuluk yok). Bu
    fonksiyon, önceki bir oturumda (`street_furniture/infrastructure.py`,
    ROADMAP_V5 M2.5) yazılmış ama gerçek OSM verisine hiç bağlanmamış
    `BridgeGenerator`'ı (tabliye + korkuluk + `pier_spacing_m` aralıklarla
    ayak) gerçek `bridge=yes` OSM feature'larına bağlar — mevcut mimari
    (`BridgeGenerator`, `_road_width_m`) değiştirilmeden, yalnızca köprüye
    özel bir üretim yolu olarak `generate_infrastructure_for_collection`'a
    eklenir (geriye dönük uyumlu: `bridge=yes` olmayan yollar hâlâ eski
    `road_from_linestring_feature`/`mesh_for_road` yolunu kullanır)."""
    from ..street_furniture.infrastructure import BridgeGenerator, BridgeSpec

    points = _linestring_points(feature)
    tags = feature.properties
    osm_id = tags.get("osm_id", "bridge")
    spec = BridgeSpec(
        path=points,
        deck_width_m=_road_width_m(tags),
        deck_z=DEFAULT_BRIDGE_CLEARANCE_M,
        pier_ground_z=0.0,
    )
    mesh = BridgeGenerator.generate(spec)
    mesh.name = f"osm_bridge_{osm_id}"
    return mesh


def mesh_for_tunnel_road(feature: GeoFeature) -> Mesh3D:
    """ROADMAP_V8 Faz 6.2 (tünel tarafı) — `mesh_for_bridge_road` ile aynı
    desen: önceden yalnızca `Road.elevation_z` ile gömülen `tunnel=yes`
    yollar artık `street_furniture.infrastructure.TunnelGenerator`
    (taban döşemesi + iki yan duvar) üzerinden üretiliyor. Mevcut mimari
    (`TunnelGenerator`, `_road_width_m`, `DEFAULT_TUNNEL_DEPTH_M`)
    değiştirilmeden, yalnızca tünele özel bir üretim yolu olarak
    `generate_infrastructure_for_collection`'a eklenir (geriye dönük
    uyumlu: `tunnel=yes` olmayan yollar eski yolu kullanmaya devam
    eder)."""
    from ..street_furniture.infrastructure import TunnelGenerator, TunnelSpec

    points = _linestring_points(feature)
    tags = feature.properties
    osm_id = tags.get("osm_id", "tunnel")
    spec = TunnelSpec(
        path=points,
        road_width_m=_road_width_m(tags),
        road_z=DEFAULT_TUNNEL_DEPTH_M,
        ground_z=0.0,
    )
    mesh = TunnelGenerator.generate(spec)
    mesh.name = f"osm_tunnel_{osm_id}"
    return mesh


def _point_along_polyline(points: list[Point2D], t: float) -> Point2D:
    """ROADMAP_V8 Faz 5.6a — `TunnelGenerator`/`BridgeGenerator`'daki
    `_point_along_path` ile aynı yöntemin bağımsız kopyası (`editor.
    osm_bridge`'in `street_furniture`'a döngüsel bağımlılık kurmaması
    için — mevcut mimarideki modüller-arası bağımlılık yönü korunur)."""
    t = min(1.0, max(0.0, t))
    n = len(points)
    if n == 1:
        return points[0]
    lengths = [points[i].distance_to(points[i + 1]) for i in range(n - 1)]
    total = sum(lengths) or 1e-6
    target = t * total
    accum = 0.0
    for i, seg_len in enumerate(lengths):
        if accum + seg_len >= target or i == n - 2:
            local_t = (target - accum) / seg_len if seg_len > 1e-9 else 0.0
            local_t = min(1.0, max(0.0, local_t))
            a, b = points[i], points[i + 1]
            return Point2D(a.x + (b.x - a.x) * local_t, a.y + (b.y - a.y) * local_t)
        accum += seg_len
    return points[-1]


def _rotate_translate_mesh_xy(mesh: Mesh3D, angle_rad: float, cx: float, cy: float) -> Mesh3D:
    """`street_furniture.infrastructure._rotate_translate_mesh_xy` ile
    aynı dönüştürme mantığının bağımsız kopyası (bkz. `_point_along_
    polyline` docstring'i)."""
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    new_vertices = []
    for v in mesh.vertices:
        rx = v.x * cos_a - v.y * sin_a
        ry = v.x * sin_a + v.y * cos_a
        new_normal = v.normal
        if v.normal is not None:
            nx, ny = v.normal[0], v.normal[1]
            new_normal = (nx * cos_a - ny * sin_a, nx * sin_a + ny * cos_a, v.normal[2])
        new_vertices.append(
            type(v)(cx + rx, cy + ry, v.z, normal=new_normal, tangent=v.tangent, uv=v.uv)
        )
    return Mesh3D(
        vertices=new_vertices, triangles=list(mesh.triangles), uvs=list(mesh.uvs), name=mesh.name
    )


def lane_marking_for_road_feature(feature: GeoFeature) -> Mesh3D | None:
    """ROADMAP_V8 Faz 5.6a — ana yollarda (bkz. `LANE_MARKING_HIGHWAY_
    TYPES`) kesikli orta çizgi şerit işareti. `street_furniture.
    infrastructure.LandscapeDetailGenerator`'daki "polyline boyunca ince
    şerit dizisi" desenini (yeni bir extrusion algoritması icat etmeden)
    yeniden uygular. Patika/yaya yolu gibi düşük öncelikli tiplerde
    `None` döner (B3 performans ilkesi, roadmap'in kendi notu)."""
    tags = feature.properties
    highway_type = tags.get("highway", "")
    if highway_type not in LANE_MARKING_HIGHWAY_TYPES:
        return None
    points = _linestring_points(feature)
    if len(points) < 2:
        return None
    osm_id = tags.get("osm_id", "lane")
    dash_length_m, gap_m = 3.0, 3.0
    total = sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1))
    if total <= 1e-6:
        return None
    step = dash_length_m + gap_m
    n_dashes = max(1, int(total / step))
    segments: list[Mesh3D] = []
    for k in range(n_dashes):
        t0 = (k * step) / total
        t1 = min(1.0, (k * step + dash_length_m) / total)
        if t1 <= t0:
            continue
        p0 = _point_along_polyline(points, t0)
        p1 = _point_along_polyline(points, t1)
        seg_len = p0.distance_to(p1)
        if seg_len < 0.2:
            continue
        mx, my = (p0.x + p1.x) / 2.0, (p0.y + p1.y) / 2.0
        angle = math.atan2(p1.y - p0.y, p1.x - p0.x)
        dash = MeshBuilder.build_box(
            width=seg_len,
            depth=0.15,
            height=0.02,
            center_x=0.0,
            center_y=0.0,
            base_z=_road_elevation_z(tags) + 0.01,
            name=f"osm_lane_marking_{osm_id}_{k}",
        )
        segments.append(_rotate_translate_mesh_xy(dash, angle, mx, my))
    if not segments:
        return None
    from ..mesh_engine import MeshMerger, UVGenerator

    merged = MeshMerger.merge(segments, name=f"osm_lane_marking_{osm_id}")
    return UVGenerator.box_mapping(merged)


def crosswalk_for_crossing_feature(feature: GeoFeature, road_width_m: float = 6.0) -> Mesh3D | None:
    """ROADMAP_V8 Faz 5.6a — `highway=crossing` (Point) feature'ında
    yaya geçidi (zebra) doku yaması. `street_furniture.infrastructure.
    LandscapeDetailGenerator.crosswalk_stripes` (mevcut, değiştirilmedi)
    yeniden kullanılır. Dürüst sınırlama: bir nokta feature'ından yolun
    gerçek yönü/genişliği bilinmez (OSM `crossing` node'u yön tag'i
    taşımaz) — bu yüzden sabit `direction_deg=0.0` ve çağıranın verdiği
    `road_width_m` varsayımı kullanılır (B4: eksik veri sahneyi
    bozmasın — yaklaşık doğru yönlü bir doku yaması, hiç olmamasından
    iyidir)."""
    if feature.geometry_type != "Point":
        return None
    x, y = feature.coordinates[0], feature.coordinates[1]
    from ..street_furniture.infrastructure import LandscapeDetailGenerator

    osm_id = feature.properties.get("osm_id", "crossing")
    return LandscapeDetailGenerator.crosswalk_stripes(
        center=Point2D(x, y),
        direction_deg=0.0,
        road_width_m=road_width_m,
        name_prefix=f"osm_crosswalk_{osm_id}",
    )


def waterway_from_linestring_feature(feature: GeoFeature, name: str | None = None) -> Road:
    """Tekil bir OSM `waterway=*` (`__category__ == "waterway"`, LineString)
    feature'ını, dere/nehir genişliğine göre bir `Road` (yeniden kullanılan
    şerit-extrude altyapısı) olarak üretir; `elevation_z` terrain'e göre
    hafif gömülü verilir (B2 "kıyı hizalama" kaygısına kaba bir yaklaşım) -
    `tunnel=yes` (menfez/kapalı dere kesimi) OSM'de nadiren de olsa
    görülebildiği için B1'in köprü/tünel Z-ayrımı burada da uygulanır;
    `bridge` tag'i suyollarında anlamlı olmadığından yalnızca `tunnel`
    kontrol edilir (B4 "eksik/çelişkili tag" - anlamsız kombinasyon
    sessizce yok sayılır, varsayılan gömülü su davranışına düşülür)."""
    points = _linestring_points(feature)
    tags = feature.properties
    osm_id = tags.get("osm_id", "waterway")
    elevation_z = (
        DEFAULT_TUNNEL_DEPTH_M
        if str(tags.get("tunnel", "")).lower() == "yes"
        else -DEFAULT_WATER_DEPTH_OFFSET_M
    )
    return Road(
        road_id=f"osm_waterway_{osm_id}",
        control_points=points,
        width_m=_waterway_width_m(tags),
        elevation_z=elevation_z,
        samples_per_segment=1,
        name=name or f"osm_waterway_{osm_id}",
    )


#: ROADMAP_V8 Faz 5.5 — kıyı şeridi (`natural=coastline`) hizalama toleransı.
#: Bir su alanı (`water_area`) poligon köşesi, bir kıyı şeridi (`coastline`)
#: segmentine bu mesafenin (metre) içindeyse, o segmente izdüşürülerek tam
#: örtüşme sağlanır — OSM'de `natural=water` poligonlarının kıyı boyunca
#: `natural=coastline` çizgisiyle *neredeyse* ama tam örtüşmemesi (ayrı
#: çizilmiş iki geometri, digitizing hatası/farklı kaynak) yaygın bir veri
#: kalitesi sorunudur; roadmap'in "kıyı çizgisi ile su poligonu görsel
#: olarak örtüşüyor" kabul kriteri bunu kapatır.
DEFAULT_COASTLINE_SNAP_TOLERANCE_M: float = 5.0


def _closest_point_on_segment(p: Point2D, a: Point2D, b: Point2D) -> Point2D:
    """`p` noktasının `a`-`b` doğru parçası üzerindeki en yakın izdüşümü."""
    dx, dy = b.x - a.x, b.y - a.y
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return a
    t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return Point2D(a.x + t * dx, a.y + t * dy)


def snap_water_area_to_coastline(
    feature: GeoFeature,
    coastline_features: list[GeoFeature],
    tolerance_m: float = DEFAULT_COASTLINE_SNAP_TOLERANCE_M,
) -> GeoFeature:
    """ROADMAP_V8 Faz 5.5: `water_area` poligonunun her köşesini, en yakın
    `coastline` segmentine `tolerance_m` içindeyse o segmentin üzerine
    izdüşürerek yeni bir `GeoFeature` döner (girdi değiştirilmez —
    `mesh_for_water_area`'nın deterministik/yan-etkisiz mimarisiyle
    tutarlı). Yakında hiçbir kıyı segmenti yoksa köşe olduğu gibi kalır
    (dürüst sınırlama: gerçek bir OSM kıyı hattı olmayan iç göl/gölet
    poligonları bu fonksiyondan etkilenmemeli, ki etkilenmezler)."""
    if feature.geometry_type != "Polygon":
        raise ValueError(
            f"snap_water_area_to_coastline yalnızca Polygon geometrisi kabul eder, "
            f"gelen: {feature.geometry_type!r}"
        )
    segments: list[tuple[Point2D, Point2D]] = []
    for cf in coastline_features:
        if cf.geometry_type != "LineString":
            continue
        pts = [Point2D(x, y) for x, y in cf.coordinates]
        for i in range(len(pts) - 1):
            segments.append((pts[i], pts[i + 1]))
    if not segments:
        return feature

    new_rings: list[list[tuple[float, float]]] = []
    for ring in feature.coordinates:
        new_ring: list[tuple[float, float]] = []
        for x, y in ring:
            p = Point2D(x, y)
            best_point = p
            best_dist = tolerance_m
            for a, b in segments:
                candidate = _closest_point_on_segment(p, a, b)
                dist = math.hypot(candidate.x - p.x, candidate.y - p.y)
                if dist <= best_dist:
                    best_dist = dist
                    best_point = candidate
            new_ring.append((best_point.x, best_point.y))
        new_rings.append(new_ring)

    return GeoFeature(
        geometry_type=feature.geometry_type,
        coordinates=new_rings,
        properties=dict(feature.properties),
    )


def mesh_for_water_area(
    feature: GeoFeature,
    thickness_m: float = DEFAULT_WATER_AREA_THICKNESS_M,
    name: str | None = None,
    coastline_features: list[GeoFeature] | None = None,
    coastline_snap_tolerance_m: float = DEFAULT_COASTLINE_SNAP_TOLERANCE_M,
) -> Mesh3D:
    """Bir OSM `natural=water` (`__category__ == "water_area"`, Polygon)
    feature'ını ince, düz bir su yüzeyi prizmasına çevirir (B2 "alan
    feature" stratejisi: "poligon offset + extrude" — burada offset yok,
    doğrudan mevcut `MeshBuilder.extrude_polygon` kullanılır, roadmap'in
    "mevcut mimari korunacak" ilkesi).

    ROADMAP_V8 Faz 5.5: `coastline_features` verilirse (aynı koleksiyondaki
    `natural=coastline` feature'ları), extrude edilmeden önce poligon
    `snap_water_area_to_coastline` ile kıyı hattına hizalanır — verilmezse
    (varsayılan `None`) eski davranış birebir korunur (geriye dönük tam
    uyumlu, iç göl/gölet senaryoları regresyonsuz)."""
    if feature.geometry_type != "Polygon":
        raise ValueError(
            f"mesh_for_water_area yalnızca Polygon geometrisi kabul eder, "
            f"gelen: {feature.geometry_type!r}"
        )
    if coastline_features:
        feature = snap_water_area_to_coastline(
            feature, coastline_features, coastline_snap_tolerance_m
        )
    ring = feature.coordinates[0]
    polygon = Polygon([Point2D(x, y) for x, y in ring])
    osm_id = feature.properties.get("osm_id", "water_area")
    return MeshBuilder.extrude_polygon(
        polygon,
        base_z=-DEFAULT_WATER_DEPTH_OFFSET_M,
        height=thickness_m,
        name=name or f"osm_water_area_{osm_id}",
    )


def railway_from_linestring_feature(feature: GeoFeature, name: str | None = None) -> Road:
    """Tekil bir OSM `railway=*` (`__category__` ∈ demiryolu anahtarları,
    LineString) feature'ını `Road` şerit-extrude'una çevirir — B1: "her tip
    için farklı genişlik" ilkesiyle, ROADMAP_V8 Faz 2.1'in demiryolu
    genişlik tablosunu kullanır (yol genişlik mantığından bilinçli olarak
    ayrı tutulur, `road_from_linestring_feature` DEĞİŞTİRİLMEZ)."""
    points = _linestring_points(feature)
    tags = feature.properties
    category_key = tags.get("__category__", "railway_rail")
    osm_id = tags.get("osm_id", "railway")
    width = DEFAULT_RAILWAY_WIDTH_M.get(category_key, DEFAULT_RAILWAY_WIDTH_FALLBACK_M)
    return Road(
        road_id=f"osm_{osm_id}",
        control_points=points,
        width_m=width,
        elevation_z=_road_elevation_z(tags),
        samples_per_segment=1,
        name=name or f"osm_{category_key}_{osm_id}",
    )


def coastline_from_linestring_feature(feature: GeoFeature, name: str | None = None) -> Road:
    """`natural=coastline` (LineString) -> ince, gömülü bir şerit (B1'in
    "kıyı hizalama" isteğine kaba yaklaşıklama — `waterway_from_linestring_
    feature` ile aynı gömme mantığı, ama sabit ince genişlik)."""
    points = _linestring_points(feature)
    tags = feature.properties
    osm_id = tags.get("osm_id", "coastline")
    return Road(
        road_id=f"osm_coastline_{osm_id}",
        control_points=points,
        width_m=1.0,
        elevation_z=-DEFAULT_WATER_DEPTH_OFFSET_M,
        samples_per_segment=1,
        name=name or f"osm_coastline_{osm_id}",
    )


def mesh_for_area_category(feature: GeoFeature, name: str | None = None) -> Mesh3D:
    """ROADMAP_V8 Faz 2.1/2.4 — `parking`/`grass`/`park`/`flowerbed`/
    `farmland` (Polygon) feature'ını `mesh_for_water_area` ile aynı desende
    (`MeshBuilder.extrude_polygon`), kategoriye özel ince bir zemin
    prizmasına çevirir. Gerçek doku/renk ataması `material_engine`
    katmanına bırakılır (B1: "zemin dokusu" isteğinin geometri kısmı)."""
    if feature.geometry_type != "Polygon":
        raise ValueError(
            f"mesh_for_area_category yalnızca Polygon geometrisi kabul eder, "
            f"gelen: {feature.geometry_type!r}"
        )
    category_key = feature.properties.get("__category__", "")
    thickness = AREA_CATEGORY_THICKNESS_M.get(
        category_key, DEFAULT_AREA_CATEGORY_THICKNESS_FALLBACK_M
    )
    ring = feature.coordinates[0]
    polygon = Polygon([Point2D(x, y) for x, y in ring])
    osm_id = feature.properties.get("osm_id", category_key or "area")
    return MeshBuilder.extrude_polygon(
        polygon,
        base_z=0.0,
        height=thickness,
        name=name or f"osm_{category_key}_{osm_id}",
    )


def administrative_boundary_geojson(feature: GeoFeature) -> list[tuple[float, float]] | None:
    """`boundary=administrative` (LineString) -> B1'de açıkça "3D model
    değil, etiket/overlay" diye belirtildiği için mesh ÜRETMEZ; yalnızca
    ham koordinat listesini döner (çağıran taraf harita katmanına
    — Leaflet/GeoJSON çizgi + etiket — bu listeyi doğrudan besleyebilir).
    Beklenmeyen geometri/kategori için `None` döner (diğer köprülerle
    tutarlı "eksik veri sahneyi bozmasın" felsefesi)."""
    if feature.geometry_type != "LineString":
        return None
    if feature.properties.get("__category__") != "administrative_boundary":
        return None
    return [(x, y) for x, y in feature.coordinates]


@dataclass(slots=True)
class InfrastructureResult:
    """`generate_infrastructure_for_collection`'ın dönüş değeri — B5'in
    "katman bazlı istatistik" ile tutarlı, kategori kırılımlı sayım
    (`counts`) + üretilen mesh'ler."""

    road_meshes: list[Mesh3D] = field(default_factory=list)
    waterway_meshes: list[Mesh3D] = field(default_factory=list)
    water_area_meshes: list[Mesh3D] = field(default_factory=list)
    # ROADMAP_V8 Faz 2.1/2.4 eklentisi.
    railway_meshes: list[Mesh3D] = field(default_factory=list)
    coastline_meshes: list[Mesh3D] = field(default_factory=list)
    area_meshes: list[Mesh3D] = field(default_factory=list)
    administrative_boundaries: list[list[tuple[float, float]]] = field(default_factory=list)
    # ROADMAP_V8 Faz 5.6a eklentisi — yol görsel derinliği.
    #: `road_meshes` ile paralel indeksli, her yolun malzeme preset
    #: anahtarı (`material_engine.ProceduralMaterials._PRESETS` ile
    #: eşleşir) — `app_shell/session.py`'nin `extra` metadata deseniyle
    #: tüketilmek üzere.
    road_surface_materials: list[str] = field(default_factory=list)
    #: Ana yollarda üretilen kesikli şerit çizgisi mesh'leri (ayrı bir
    #: kategori — B3 performansı için isteğe bağlı gizlenebilir/LOD
    #: uygulanabilir olması amacıyla `road_meshes`'e karıştırılmaz).
    lane_marking_meshes: list[Mesh3D] = field(default_factory=list)
    #: `highway=crossing` nokta feature'larından üretilen yaya geçidi
    #: doku yamaları.
    crosswalk_meshes: list[Mesh3D] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "roads": len(self.road_meshes),
            "waterway": len(self.waterway_meshes),
            "water_area": len(self.water_area_meshes),
            "railway": len(self.railway_meshes),
            "coastline": len(self.coastline_meshes),
            "area": len(self.area_meshes),
            "administrative_boundary": len(self.administrative_boundaries),
            "lane_marking": len(self.lane_marking_meshes),
            "crosswalk": len(self.crosswalk_meshes),
        }


def generate_infrastructure_for_collection(
    collection: GeoFeatureCollection,
) -> InfrastructureResult:
    """Faz C3 (2. dilim) uçtan uca giriş noktası:
    `fetch_category_features` + `project_to_local_meters` çıktısındaki
    (yerel metre projeksiyonlu) bir `GeoFeatureCollection`'daki tüm
    `roads`/`waterway`/`water_area` feature'larını mesh'lere indirger.
    Diğer kategoriler (trees, forest, wood) bu fonksiyon tarafından yok
    sayılır (onların üretim yolu `vegetation.osm_bridge`'de).

    ROADMAP_V8 Faz 2.1/2.4: demiryolu (`railway_rail`/`railway_subway`/
    `railway_tram`), kıyı şeridi (`coastline`) ve alan kategorileri
    (`parking`/`grass`/`park`/`flowerbed`/`farmland`) de aynı akışa
    eklendi; `administrative_boundary` bilinçli olarak mesh üretmez
    (yalnızca `administrative_boundaries` ham koordinat listesi doldurulur
    — B1: "3D model değil, etiket/overlay").

    ROADMAP_V8 Faz 5.6a: her yol feature'ı için `road_surface_materials`'a
    (mevcut Faz 5.1 malzeme preset'lerine işaret eden) bir anahtar
    eklenir; ana yollarda ayrıca kesikli şerit çizgisi (`lane_marking_
    meshes`) üretilir; `highway=crossing` (Point) feature'ları yaya
    geçidi doku yamasına (`crosswalk_meshes`) çevrilir."""
    result = InfrastructureResult()
    # ROADMAP_V8 Faz 5.5: su alanlarını kıyı hattına hizalamak için önce
    # tüm `coastline` feature'ları toplanır (sıra bağımsız çalışması için
    # su alanları koleksiyonda kıyı çizgisinden önce gelse bile doğru
    # çalışır — tek geçişli bir yaklaşımda bu garanti edilemezdi).
    coastline_features = [
        f
        for f in collection.features
        if f.properties.get("__category__") == "coastline" and f.geometry_type == "LineString"
    ]
    for feature in collection.features:
        category = feature.properties.get("__category__")
        if category == "roads" and feature.geometry_type == "LineString":
            if len(feature.coordinates) < 2:
                continue
            # ROADMAP_V8 Faz 6.2 — bridge=yes yollar gerçek ayak/korkuluk
            # geometrisiyle (BridgeGenerator) üretilir; diğerleri eski
            # tape-extrusion yolunda kalır (geriye dönük uyumlu).
            if _is_bridge_tag(feature.properties):
                result.road_meshes.append(mesh_for_bridge_road(feature))
            elif _is_tunnel_tag(feature.properties):
                result.road_meshes.append(mesh_for_tunnel_road(feature))
            else:
                road = road_from_linestring_feature(feature)
                result.road_meshes.append(mesh_for_road(road))
            # Faz 5.6a: yüzey malzemesi (her zaman — köprü/tünel için de
            # anlamlı, ör. köprü tabliyesi asfalt kalır) + opsiyonel şerit
            # çizgisi (yalnızca ana yol tiplerinde, `None` dönerse atlanır).
            result.road_surface_materials.append(_road_surface_material(feature.properties))
            lane_mesh = lane_marking_for_road_feature(feature)
            if lane_mesh is not None:
                result.lane_marking_meshes.append(lane_mesh)
        elif category == "waterway" and feature.geometry_type == "LineString":
            if len(feature.coordinates) < 2:
                continue
            waterway = waterway_from_linestring_feature(feature)
            result.waterway_meshes.append(mesh_for_road(waterway))
        elif category == "water_area" and feature.geometry_type == "Polygon":
            if len(feature.coordinates[0]) < 4:
                continue
            result.water_area_meshes.append(
                mesh_for_water_area(feature, coastline_features=coastline_features)
            )
        elif category in DEFAULT_RAILWAY_WIDTH_M and feature.geometry_type == "LineString":
            if len(feature.coordinates) < 2:
                continue
            railway = railway_from_linestring_feature(feature)
            result.railway_meshes.append(mesh_for_road(railway))
        elif category == "crossing" and feature.geometry_type == "Point":
            crosswalk = crosswalk_for_crossing_feature(feature)
            if crosswalk is not None:
                result.crosswalk_meshes.append(crosswalk)
        elif category == "coastline" and feature.geometry_type == "LineString":
            if len(feature.coordinates) < 2:
                continue
            coastline = coastline_from_linestring_feature(feature)
            result.coastline_meshes.append(mesh_for_road(coastline))
        elif category in AREA_CATEGORY_THICKNESS_M and feature.geometry_type == "Polygon":
            if len(feature.coordinates[0]) < 4:
                continue
            result.area_meshes.append(mesh_for_area_category(feature))
        elif category == "administrative_boundary" and feature.geometry_type == "LineString":
            boundary = administrative_boundary_geojson(feature)
            if boundary is not None and len(boundary) >= 2:
                result.administrative_boundaries.append(boundary)
    return result


__all__ = [
    "DEFAULT_ROAD_WIDTH_M",
    "DEFAULT_ROAD_WIDTH_FALLBACK_M",
    "DEFAULT_WATERWAY_WIDTH_M",
    "DEFAULT_WATERWAY_WIDTH_FALLBACK_M",
    "DEFAULT_WATER_DEPTH_OFFSET_M",
    "DEFAULT_WATER_AREA_THICKNESS_M",
    "DEFAULT_BRIDGE_CLEARANCE_M",
    "DEFAULT_TUNNEL_DEPTH_M",
    "DEFAULT_RAILWAY_WIDTH_M",
    "DEFAULT_RAILWAY_WIDTH_FALLBACK_M",
    "AREA_CATEGORY_THICKNESS_M",
    "DEFAULT_AREA_CATEGORY_THICKNESS_FALLBACK_M",
    "road_from_linestring_feature",
    "mesh_for_road",
    "mesh_for_bridge_road",
    "mesh_for_tunnel_road",
    "waterway_from_linestring_feature",
    "mesh_for_water_area",
    "snap_water_area_to_coastline",
    "DEFAULT_COASTLINE_SNAP_TOLERANCE_M",
    "railway_from_linestring_feature",
    "coastline_from_linestring_feature",
    "mesh_for_area_category",
    "administrative_boundary_geojson",
    "InfrastructureResult",
    "generate_infrastructure_for_collection",
    # ROADMAP_V8 Faz 5.6a
    "ROAD_SURFACE_STYLE",
    "DEFAULT_ROAD_SURFACE_FALLBACK",
    "LANE_MARKING_HIGHWAY_TYPES",
    "lane_marking_for_road_feature",
    "crosswalk_for_crossing_feature",
]
