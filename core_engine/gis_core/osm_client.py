"""
OSM Overpass Client — Roadmap V2 A3 / Roadmap V4 R5
=====================================================

"A3: Gerçek OSM Verisiyle Uçtan Uca Entegrasyon" — bu ortamda (izin
verilen ağ alan-adı listesi `overpass-api.de` gibi kamu OSM sunucularını
kapsamadığı için) doğrulanamamış, ama roadmap R5'in gerektirdiği gibi
**kodu tam olarak yazılmış** bir modül. Ağ erişimi olan bir ortamda
(kullanıcının kendi makinesi) doğrudan çalışır — stdlib-only
(`urllib.request`), harici bağımlılık yok.

Bileşenler
----------
- `BBox`: enlem/boylam sınırlayıcı kutu (Overpass QL "bbox" formatı).
- `OverpassClient`: birden fazla kamu Overpass aynasına (mirror) sırayla
  deneyen, `way["building"]`/`relation["building"]` sorgusu üreten ve
  ham Overpass JSON'u çeken istemci. Bir ayna başarısız olursa (ağ hatası,
  zaman aşımı, HTTP hata kodu) sırayla bir sonrakine düşer; hepsi
  başarısız olursa açık bir `OverpassNetworkError` fırlatır (sessizce
  boş sonuç dönmez).
- `OSMBuildingParser.parse_overpass_json`: ham Overpass JSON'u (node +
  way elemanları) mevcut `core_engine.gis_core.GeoFeatureCollection`'a
  çevirir — `way.nodes` referanslarını node koordinatlarıyla çözüp kapalı
  bir `Polygon` halkası kurar; `relation` (multipolygon, örn. içi boş
  binalar) için dış halkayı (`role=outer`) kullanır, iç halkaları
  (`role=inner`) bilinçli olarak dışarıda bırakır (mevcut `GeoFeature.
  to_polygon()` tek-halka modeliyle tutarlı — roadmap kapsamı dışı).
- `project_to_local_meters`: WGS84 (derece) footprint'lerini, mevcut
  `core_engine.coordinate_systems.CoordinateConverter.wgs84_to_local`
  (yerel teğet düzlem projeksiyonu) ile metre cinsinden yerel koordinat
  sistemine çevirir — `Polygon.unsigned_area()`/`ProceduralBuildingGenerator`
  gibi metre varsayan tüm alt sistemlerle doğrudan uyumlu hale getirir.
- `fetch_and_generate_buildings`: uçtan uca boru hattı - bbox -> Overpass
  sorgusu -> `GeoFeatureCollection` -> yerel metre projeksiyonu ->
  `FootprintParser.parse` -> `ProceduralBuildingGenerator.generate` ->
  `Building` listesi. Roadmap R5'in "gerçek OSM verisiyle uçtan uca
  entegrasyon" kabul kriterinin somut karşılığı.

Dürüst sınırlama
----------------
Bu modül **test edilmemiştir** (bu oturumun ağ erişimi Overpass
sunucularını kapsamıyor) — kod, Overpass API'nin belgelenmiş (resmi
`overpass-api.de` dokümantasyonu) JSON şemasına göre yazılmıştır ve
`tests/test_r5_osm_live_integration.py` içindeki testler gerçek ağ
erişimi varsa çalışır, yoksa (bu ortamdaki gibi) açıkça `skip` edilir -
sahte biçimde "yeşil" göstermez.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..coordinate_systems import CoordinateConverter, CoordinateSystem, GeoPoint, ProjectedPoint
from . import GeoFeature, GeoFeatureCollection, GISParseError

#: Bilinen, genel kullanıma açık Overpass API aynaları (resmi + topluluk
#: işletmeli). Sırayla denenir - biri kapalı/rate-limited olursa diğerine
#: geçilir. Kullanıcı kendi/ticari bir Overpass instance'ı da verebilir
#: (`OverpassClient(endpoints=[...])`).
DEFAULT_OVERPASS_ENDPOINTS: tuple[str, ...] = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)

DEFAULT_USER_AGENT = "harita-modelleme-platformu/0.16 (roadmap-r5-osm-integration)"


class OverpassError(GISParseError):
    """Overpass sorgusu/yanıtıyla ilgili genel hata."""


class OverpassNetworkError(OverpassError):
    """Tüm aynalar denendi ve hiçbirine ulaşılamadı (ağ/HTTP hatası)."""


# ============================================================================ #
# BBox
# ============================================================================ #


@dataclass(frozen=True, slots=True)
class BBox:
    """Enlem/boylam sınırlayıcı kutu (WGS84, derece)."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    def __post_init__(self) -> None:
        if not (-90.0 <= self.min_lat < self.max_lat <= 90.0):
            raise ValueError(f"Geçersiz enlem aralığı: [{self.min_lat}, {self.max_lat}]")
        if not (-180.0 <= self.min_lon < self.max_lon <= 180.0):
            raise ValueError(f"Geçersiz boylam aralığı: [{self.min_lon}, {self.max_lon}]")

    def center(self) -> GeoPoint:
        return GeoPoint(
            lat=(self.min_lat + self.max_lat) / 2.0,
            lon=(self.min_lon + self.max_lon) / 2.0,
        )

    def overpass_bbox_str(self) -> str:
        # Overpass QL bbox sırası: south,west,north,east (lat,lon,lat,lon).
        return f"{self.min_lat},{self.min_lon},{self.max_lat},{self.max_lon}"


# ============================================================================ #
# Overpass istemcisi
# ============================================================================ #


@dataclass
class OverpassClient:
    """Overpass API'ye bina footprint sorgusu gönderen stdlib-only istemci."""

    endpoints: Sequence[str] = field(default_factory=lambda: DEFAULT_OVERPASS_ENDPOINTS)
    timeout_s: float = 30.0
    user_agent: str = DEFAULT_USER_AGENT

    def build_query(self, bbox: BBox) -> str:
        """`way["building"]` + `relation["building"]` (multipolygon dahil)
        için standart Overpass QL sorgusu - `out body; >; out skel qt;`
        deseni, way'lerin referans verdiği tüm node'ları da (koordinatlarla
        birlikte) sonuca dahil eder."""
        bbox_str = bbox.overpass_bbox_str()
        return (
            f"[out:json][timeout:{int(self.timeout_s)}];\n"
            f"(\n"
            f'  way["building"]({bbox_str});\n'
            f'  relation["building"]({bbox_str});\n'
            f");\n"
            f"out body;\n"
            f">;\n"
            f"out skel qt;\n"
        )

    def fetch_raw(self, bbox: BBox) -> dict:
        """Ham Overpass JSON yanıtını çeker - aynalar sırayla denenir."""
        query = self.build_query(bbox)
        payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
        last_error: Exception | None = None

        for endpoint in self.endpoints:
            request = urllib.request.Request(
                endpoint,
                data=payload,
                headers={
                    "User-Agent": self.user_agent,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw_bytes = response.read()
                return json.loads(raw_bytes.decode("utf-8"))
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                OSError,
                ValueError,
            ) as exc:
                last_error = exc
                continue

        raise OverpassNetworkError(
            f"Hiçbir Overpass aynasına ulaşılamadı (denenen {len(self.endpoints)} "
            f"uç nokta): {last_error!r}"
        )

    def fetch_building_footprints(self, bbox: BBox) -> GeoFeatureCollection:
        """Bir bbox içindeki tüm bina footprint'lerini gerçek zamanlı
        Overpass sorgusuyla çeker ve `GeoFeatureCollection`'a çevirir."""
        raw = self.fetch_raw(bbox)
        return OSMBuildingParser.parse_overpass_json(raw)


# ============================================================================ #
# Overpass JSON -> GeoFeatureCollection
# ============================================================================ #


class OSMBuildingParser:
    """Ham Overpass JSON'unu (node + way [+ relation] elemanları) mevcut
    `GeoFeatureCollection` veri modeline çevirir."""

    @staticmethod
    def parse_overpass_json(data: dict) -> GeoFeatureCollection:
        try:
            elements = data.get("elements", [])
            if not isinstance(elements, list):
                raise TypeError(f"'elements' bir liste olmalı, {type(elements).__name__} geldi")

            nodes: dict[int, tuple[float, float]] = {}
            ways: dict[int, dict] = {}

            for el in elements:
                el_type = el.get("type")
                if el_type == "node":
                    nodes[el["id"]] = (el["lon"], el["lat"])
                elif el_type == "way":
                    ways[el["id"]] = el

            features: list[GeoFeature] = []

            # -- way["building"] doğrudan poligonlar ------------------------- #
            for way in ways.values():
                tags = way.get("tags", {})
                if "building" not in tags:
                    continue
                ring = OSMBuildingParser._way_to_ring(way, nodes)
                if ring is None:
                    continue
                props = dict(tags)
                props["osm_id"] = way["id"]
                props["osm_type"] = "way"
                features.append(
                    GeoFeature(
                        geometry_type="Polygon",
                        coordinates=[ring],
                        properties=props,
                    )
                )

            # -- relation["building"] (multipolygon) - yalnızca outer halka -- #
            for el in elements:
                if el.get("type") != "relation":
                    continue
                tags = el.get("tags", {})
                if "building" not in tags:
                    continue
                for member in el.get("members", []):
                    if member.get("type") != "way" or member.get("role") != "outer":
                        continue
                    way = ways.get(member.get("ref"))
                    if way is None:
                        continue
                    ring = OSMBuildingParser._way_to_ring(way, nodes)
                    if ring is None:
                        continue
                    props = dict(tags)
                    props["osm_id"] = el["id"]
                    props["osm_type"] = "relation"
                    features.append(
                        GeoFeature(
                            geometry_type="Polygon",
                            coordinates=[ring],
                            properties=props,
                        )
                    )

            return GeoFeatureCollection(features, crs="EPSG:4326")
        except OverpassError:
            raise
        except (AttributeError, TypeError, KeyError, ValueError) as exc:
            raise OverpassError(f"Beklenmeyen Overpass yanıt biçimi: {exc}") from exc

    @staticmethod
    def _way_to_ring(way: dict, nodes: dict[int, tuple[float, float]]) -> list | None:
        node_ids = way.get("nodes", [])
        coords = [nodes[nid] for nid in node_ids if nid in nodes]
        if len(coords) < 3:
            return None  # kapalı bir poligon için en az 3 farklı nokta gerekir
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        return coords


# ============================================================================ #
# WGS84 (derece) -> yerel metre projeksiyonu
# ============================================================================ #


def project_to_local_meters(
    collection: GeoFeatureCollection,
    origin: GeoPoint | None = None,
) -> GeoFeatureCollection:
    """`collection`'daki `Polygon`/`LineString`/`Point` özniteliklerini
    (derece cinsinden WGS84) `CoordinateConverter.wgs84_to_local` ile metre
    cinsinden yerel bir teğet-düzlem koordinat sistemine çevirir.

    `origin` verilmezse, koleksiyondaki tüm noktaların ortalaması
    (centroid) merkez alınır - böylece küçük/orta ölçekli bir bölge
    (bir mahalle/şehir bloğu) için ~cm mertebesinde hata ile düzlemsel
    yaklaşıklık geçerli kalır (roadmap A1/A3'ün zaten test ettiği
    `wgs84_to_local` doğruluk garantisi).

    ROADMAP_V7.md Faz C6 (2. dilim) notu: bu fonksiyon başlangıçta
    (Faz C2) yalnızca `Polygon` geometrisini destekliyordu (bina
    footprint'leri için yeterliydi). C3'ün yol/ağaç/kentsel-mobilya vb.
    OSM köprüleri `Point`/`LineString` feature'lar da üretiyor ve
    kendi testlerinde bu fonksiyonu hiç çağırmadan doğrudan yerel-metre
    `GeoFeatureCollection` inşa ediyorlardı — yani gerçek bir
    bbox->kategori->mesh uçtan uca akışında bu fonksiyon `Point`/
    `LineString` feature'ları sessizce **atıyordu** (mevcut `Polygon`
    testleri bunu yakalamıyordu çünkü hiçbiri karma geometri
    içermiyordu). Bu, C6/2. dilimin "seçilen kategorilerin gerçek mesh/
    proje entegrasyonu" maddesinin önkoşulu olarak burada, geriye dönük
    tam uyumlu şekilde (mevcut `Polygon` davranışı birebir korunarak,
    yalnızca `Point`/`LineString` için ek dallar eklenerek) tamamlandı.
    """
    if origin is None:
        origin = _centroid_of_collection(collection)

    def _project_ring(ring: list[tuple]) -> list:
        local_ring = []
        for lon, lat in ring:
            projected: ProjectedPoint = CoordinateConverter.wgs84_to_local(
                GeoPoint(lat=lat, lon=lon),
                origin,
            )
            local_ring.append((projected.x, projected.y))
        return local_ring

    projected_features: list[GeoFeature] = []
    for feature in collection.features:
        if feature.geometry_type == "Polygon":
            ring = feature.coordinates[0]
            projected_features.append(
                GeoFeature(
                    geometry_type="Polygon",
                    coordinates=[_project_ring(ring)],
                    properties=feature.properties,
                )
            )
        elif feature.geometry_type == "LineString":
            line = feature.coordinates
            projected_features.append(
                GeoFeature(
                    geometry_type="LineString",
                    coordinates=_project_ring(line),
                    properties=feature.properties,
                )
            )
        elif feature.geometry_type == "Point":
            lon, lat = feature.coordinates
            projected: ProjectedPoint = CoordinateConverter.wgs84_to_local(
                GeoPoint(lat=lat, lon=lon),
                origin,
            )
            projected_features.append(
                GeoFeature(
                    geometry_type="Point",
                    coordinates=(projected.x, projected.y),
                    properties=feature.properties,
                )
            )
        # Bilinmeyen/desteklenmeyen geometri tipleri (şu an yok) sessizce
        # atlanır - önceki davranışla tutarlı ("eksik veri sahneyi bozmasın").

    return GeoFeatureCollection(
        projected_features,
        crs=f"local_tangent_plane(origin_lat={origin.lat},origin_lon={origin.lon})",
    )


def local_meters_collection_centroid_to_wgs84(
    x: float,
    y: float,
    origin: GeoPoint,
) -> GeoPoint:
    """`project_to_local_meters`'ın tersi — tek bir yerel-metre noktasını
    (`origin`'e göre) geri WGS84 (lat/lon)'a çevirir. C6/2. dilimin
    içe aktarılan prop'ları haritada (Leaflet, WGS84 bekler) gösterebilmesi
    için gerekli (mesh üretimi yerel metrede kalır, yalnızca konum geri
    projekte edilir)."""
    geo = CoordinateConverter.local_to_wgs84(
        ProjectedPoint(x=x, y=y, system=CoordinateSystem.LOCAL),
        origin,
    )
    return geo


def _centroid_of_collection(collection: GeoFeatureCollection) -> GeoPoint:
    lons: list[float] = []
    lats: list[float] = []
    for feature in collection.features:
        if feature.geometry_type != "Polygon":
            continue
        for lon, lat in feature.coordinates[0]:
            lons.append(lon)
            lats.append(lat)
    if not lons:
        raise OverpassError("Boş GeoFeatureCollection için merkez hesaplanamaz")
    return GeoPoint(lat=sum(lats) / len(lats), lon=sum(lons) / len(lons))


# ============================================================================ #
# Uçtan uca boru hattı: bbox -> gerçek OSM footprint -> Building
# ============================================================================ #


@dataclass
class OSMIntegrationResult:
    """`fetch_and_generate_buildings`'in dönüş değeri - hem ham/izlenebilir
    ara sonuçları hem de üretilen binaları taşır (test/denetim kolaylığı
    için)."""

    raw_feature_count: int
    local_collection: GeoFeatureCollection
    footprints: list
    buildings: list
    skipped_invalid: int


def fetch_and_generate_buildings(
    bbox: BBox,
    client: OverpassClient | None = None,
    origin: GeoPoint | None = None,
) -> OSMIntegrationResult:
    """Roadmap R5'in uçtan uca kabul kriterinin somut karşılığı:

    bbox -> gerçek Overpass sorgusu -> `GeoFeatureCollection` (WGS84) ->
    yerel metre projeksiyonu -> `FootprintParser.parse` -> geçerlilik
    kontrolü (self-intersection yok) -> `ProceduralBuildingGenerator.
    generate` -> `Building` listesi.

    Geometrik olarak geçersiz (self-intersecting, <3 farklı köşe) OSM
    footprint'leri (gerçek dünya verisinde nadir değildir - hatalı çizim/
    editör artığı) sessizce atlanır ve `skipped_invalid` sayacına
    yansıtılır - tek bir bozuk kayıt yüzünden tüm toplu işlemin
    çökmesine izin verilmez (roadmap A1'in "fuzz" felsefesiyle tutarlı).
    """
    # Yerel import - döngüsel bağımlılığı önlemek için (building_reconstruction
    # zaten core_engine'e bağımlı, tersi değil).
    from ...building_reconstruction import FootprintParser, ProceduralBuildingGenerator

    if client is None:
        client = OverpassClient()

    raw_collection = client.fetch_building_footprints(bbox)
    local_collection = project_to_local_meters(raw_collection, origin=origin)

    footprints = []
    buildings = []
    skipped = 0
    generator = ProceduralBuildingGenerator()

    for feature in local_collection.features:
        try:
            footprint = FootprintParser.parse(feature)
            if footprint.area_m2 <= 0.5:
                skipped += 1
                continue
            footprints.append(footprint)
            buildings.append(generator.generate(footprint))
        except (ValueError, ArithmeticError, ZeroDivisionError):
            skipped += 1
            continue

    return OSMIntegrationResult(
        raw_feature_count=len(raw_collection.features),
        local_collection=local_collection,
        footprints=footprints,
        buildings=buildings,
        skipped_invalid=skipped,
    )


# ============================================================================ #
# Faz C2 (ROADMAP_V7.md Bölüm B0-B2) — kategori bazlı Overpass sorguları
# ============================================================================ #
#
# B0 şu satırla özetleniyor: "OSM Overpass sorgusu şu an sadece `building=*`
# çekiyor. Bunu kategori bazlı, açılıp kapanabilir bir 'veri katmanları'
# sistemine dönüştürüyoruz." Faz sıralamasına göre (Bölüm C, madde C2)
# öncelik "en görsel etkisi yüksek + teknik olarak en kolay olanlar: yol +
# ağaç + su" olarak belirtilmiş — bu yüzden ilk uygulanan kategoriler bunlar.
# `building` sorgusu/`OSMBuildingParser` roadmap'in "mevcut mimari korunacak"
# ilkesi gereği DOKUNULMADAN bırakıldı; yeni kategoriler ayrı, ek bir yol
# olarak eklendi (geriye dönük uyumluluk kırılmıyor).


@dataclass(frozen=True, slots=True)
class OSMCategory:
    """Tek bir veri katmanının Overpass filtresi + geometri sınıflandırması.

    `geometry` sabit (kategori bazında tek tip davranış varsayılır — B2'de
    tarif edilen "nokta / çizgi / alan" üretim stratejisiyle bire bir
    örtüşür):
      - "point"   -> yalnızca `node` elemanları kullanılır (ör. ağaç).
      - "line"    -> `way` elemanları kapalı olsa da çizgi (merkez hattı)
                     olarak ele alınır (ör. yol, dere — B2 "çizgi feature").
      - "polygon" -> `way`/`relation` elemanları kapalı halka (alan) olarak
                     ele alınır (ör. orman, göl — B2 "alan feature").
    """

    key: str
    overpass_filter: str  # ör. 'highway' veya 'natural=tree'
    geometry: str  # "point" | "line" | "polygon"


#: B1'de "en görsel etkisi yüksek + en kolay" olarak önceliklendirilen
#: kategoriler (Faz C2 kapsamı). Yeni kategori eklemek (B1'in geri kalanı:
#: dini yapılar, ticaret, kentsel mobilya, altyapı, spor...) buraya bir satır
#: eklemekten ibaret — parser kategoriye göre değil `geometry` alanına göre
#: dallanıyor, bu yüzden genişleme lineer maliyetli.
DEFAULT_CATEGORIES: dict[str, OSMCategory] = {
    "roads": OSMCategory(key="roads", overpass_filter="highway", geometry="line"),
    "trees": OSMCategory(key="trees", overpass_filter="natural=tree", geometry="point"),
    "forest": OSMCategory(key="forest", overpass_filter="landuse=forest", geometry="polygon"),
    "wood": OSMCategory(key="wood", overpass_filter="natural=wood", geometry="polygon"),
    "water_area": OSMCategory(
        key="water_area", overpass_filter="natural=water", geometry="polygon"
    ),
    "waterway": OSMCategory(key="waterway", overpass_filter="waterway", geometry="line"),
    # Faz C3 (3. dilim) — B1 "kentsel mobilya" alt kümesi. Anahtarlar
    # bilinçli olarak `street_furniture.StreetFurnitureType` değerleriyle
    # aynı isimlendirildi (bkz. `street_furniture/osm_bridge.py`
    # `CATEGORY_KEY_TO_OSM_TAG` eşlemesi) — takip kolaylığı için.
    "street_lamp": OSMCategory(
        key="street_lamp", overpass_filter="highway=street_lamp", geometry="point"
    ),
    "power_pole": OSMCategory(key="power_pole", overpass_filter="power=pole", geometry="point"),
    "waste_basket": OSMCategory(
        key="waste_basket", overpass_filter="amenity=waste_basket", geometry="point"
    ),
    "bench": OSMCategory(key="bench", overpass_filter="amenity=bench", geometry="point"),
    "bus_stop": OSMCategory(key="bus_stop", overpass_filter="highway=bus_stop", geometry="point"),
    "bus_station": OSMCategory(
        key="bus_station", overpass_filter="amenity=bus_station", geometry="point"
    ),
    # Faz C3 (4. dilim) — B1 "dini ve kültürel yapılar". Alt tip (cami/
    # kilise/sinagog/genel) `religion` tag'inden `religious_structures.
    # osm_bridge` tarafından çözülür (bkz. o modül).
    "place_of_worship": OSMCategory(
        key="place_of_worship", overpass_filter="amenity=place_of_worship", geometry="point"
    ),
    # Faz C3 (5. dilim) — B1 "ticaret ve gündelik yaşam". `restaurant`/
    # `cafe` her ikisi de nokta olarak çekilir; `outdoor_seating=yes`
    # filtresi geometri seviyesinde değil `commerce_props/osm_bridge.py`
    # içinde (properties üzerinden) uygulanır (bkz. o modülün docstring'i).
    "marketplace": OSMCategory(
        key="marketplace", overpass_filter="amenity=marketplace", geometry="polygon"
    ),
    "restaurant": OSMCategory(
        key="restaurant", overpass_filter="amenity=restaurant", geometry="point"
    ),
    "cafe": OSMCategory(key="cafe", overpass_filter="amenity=cafe", geometry="point"),
    # Faz C3 (6. dilim) — B1 "spor ve rekreasyon". Saha/stadyum/havuz
    # Polygon, oyun alanı Point (bkz. `sport_recreation/osm_bridge.py`).
    "pitch": OSMCategory(key="pitch", overpass_filter="leisure=pitch", geometry="polygon"),
    "stadium": OSMCategory(key="stadium", overpass_filter="leisure=stadium", geometry="polygon"),
    "swimming_pool": OSMCategory(
        key="swimming_pool", overpass_filter="leisure=swimming_pool", geometry="polygon"
    ),
    "playground": OSMCategory(
        key="playground", overpass_filter="leisure=playground", geometry="point"
    ),
    # Faz C3 (7. dilim, B1'in son dilimi) — B1 "altyapı" alt kümesi.
    # `man_made=tower` tek başına iletişim kulesi anlamına gelmez (su
    # kulesi vb. de aynı tag'i kullanır) — `tower:type=communication`
    # ikincil filtresi `power_infrastructure/osm_bridge.py`'de uygulanır.
    "power_line": OSMCategory(key="power_line", overpass_filter="power=line", geometry="line"),
    "substation": OSMCategory(
        key="substation", overpass_filter="power=substation", geometry="polygon"
    ),
    "communication_tower": OSMCategory(
        key="communication_tower", overpass_filter="man_made=tower", geometry="point"
    ),
    # ------------------------------------------------------------------ #
    # ROADMAP_V8 Bölüm 2 — B1'in V7'de eksik bırakılan kalan alt maddeleri.
    # ------------------------------------------------------------------ #
    # Faz 2.1 — ulaşım: demiryolu (line), istasyon/girişler + trafik
    # ışığı/yaya geçidi (point), otopark (polygon), bisiklet park yeri +
    # taksi durağı (point).
    "railway_rail": OSMCategory(
        key="railway_rail", overpass_filter="railway=rail", geometry="line"
    ),
    "railway_subway": OSMCategory(
        key="railway_subway", overpass_filter="railway=subway", geometry="line"
    ),
    "railway_tram": OSMCategory(
        key="railway_tram", overpass_filter="railway=tram", geometry="line"
    ),
    "railway_station": OSMCategory(
        key="railway_station", overpass_filter="railway=station", geometry="point"
    ),
    "subway_entrance": OSMCategory(
        key="subway_entrance", overpass_filter="railway=subway_entrance", geometry="point"
    ),
    "parking": OSMCategory(key="parking", overpass_filter="amenity=parking", geometry="polygon"),
    "bicycle_parking": OSMCategory(
        key="bicycle_parking", overpass_filter="amenity=bicycle_parking", geometry="point"
    ),
    "taxi": OSMCategory(key="taxi", overpass_filter="amenity=taxi", geometry="point"),
    "traffic_signals": OSMCategory(
        key="traffic_signals", overpass_filter="highway=traffic_signals", geometry="point"
    ),
    "crossing": OSMCategory(key="crossing", overpass_filter="highway=crossing", geometry="point"),
    # Faz 2.2 — ticaret ve gündelik yaşamın tamamlanması.
    "shopping_mall": OSMCategory(
        key="shopping_mall", overpass_filter="shop=mall", geometry="polygon"
    ),
    "supermarket": OSMCategory(
        key="supermarket", overpass_filter="shop=supermarket", geometry="polygon"
    ),
    # Faz 2.3 — kentsel mobilyanın tamamlanması (traffic_sign bilinçli
    # olarak burada DEĞİL, `OPTIONAL_CATEGORIES`'te — B1'in kendi notuyla
    # "düşük öncelik, isteğe bağlı katman", varsayılan sorguya dahil
    # edilmez / B3 performans kaygısı).
    "drinking_water": OSMCategory(
        key="drinking_water", overpass_filter="amenity=drinking_water", geometry="point"
    ),
    "fountain": OSMCategory(key="fountain", overpass_filter="amenity=fountain", geometry="point"),
    "bicycle_rental": OSMCategory(
        key="bicycle_rental", overpass_filter="amenity=bicycle_rental", geometry="point"
    ),
    # Faz 2.4 — doğal öğeler ve arazi bilgisinin tamamlanması.
    "coastline": OSMCategory(key="coastline", overpass_filter="natural=coastline", geometry="line"),
    "grass": OSMCategory(key="grass", overpass_filter="landuse=grass", geometry="polygon"),
    "park": OSMCategory(key="park", overpass_filter="leisure=park", geometry="polygon"),
    "flowerbed": OSMCategory(
        key="flowerbed", overpass_filter="landuse=flowerbed", geometry="polygon"
    ),
    "farmland": OSMCategory(key="farmland", overpass_filter="landuse=farmland", geometry="polygon"),
    # `boundary=administrative` B1'de açıkça "3D model değil, etiket/
    # overlay" diye belirtilmiş — mesh üretimi yapılmaz, yalnızca harita
    # katmanı (GeoJSON çizgi + etiket) için veri sağlanır (bkz.
    # `editor.osm_bridge.administrative_boundary_geojson`).
    "administrative_boundary": OSMCategory(
        key="administrative_boundary", overpass_filter="boundary=administrative", geometry="line"
    ),
    # Faz 2.5 — anıt/heykel kategorisi.
    "monument": OSMCategory(key="monument", overpass_filter="historic=monument", geometry="point"),
    "artwork": OSMCategory(key="artwork", overpass_filter="tourism=artwork", geometry="point"),
    # ------------------------------------------------------------------ #
    # ROADMAP_V9 Faz VI / Katman 3.1-3.2 — "POI kaynağı: OSM'den
    # amenity=school, shop=*, public_transport=stop_position tag'leri"
    # (bkz. `mobility/osm_demand_bridge.py`, `mobility/transit_osm_bridge.py`).
    # ------------------------------------------------------------------ #
    "school": OSMCategory(key="school", overpass_filter="amenity=school", geometry="polygon"),
    "transit_stop_position": OSMCategory(
        key="transit_stop_position",
        overpass_filter="public_transport=stop_position",
        geometry="point",
    ),
}

#: ROADMAP_V8 Faz 2.3 — B1'in kendi notuyla "düşük öncelik, isteğe bağlı
#: katman" olarak işaretlenen kategoriler. `DEFAULT_CATEGORIES`'e DAHİL
#: EDİLMEZ (varsayılan sorgu boyutu/performansı büyümesin, B3 kaygısı) —
#: çağıran taraf `fetch_category_features(bbox, category_keys=[*DEFAULT_CATEGORIES, "traffic_sign"])`
#: gibi açıkça isteyerek etkinleştirebilir.
OPTIONAL_CATEGORIES: dict[str, OSMCategory] = {
    "traffic_sign": OSMCategory(
        key="traffic_sign", overpass_filter="traffic_sign", geometry="point"
    ),
}


def _category_query_clause(bbox_str: str, category: OSMCategory) -> str:
    """Bir `OSMCategory` için node/way/relation üç satırlık Overpass QL
    bloğu üretir (`key=value` veya salt `key` filtresini destekler)."""
    if "=" in category.overpass_filter:
        k, v = category.overpass_filter.split("=", 1)
        tag_expr = f'["{k}"="{v}"]'
    else:
        tag_expr = f'["{category.overpass_filter}"]'
    return (
        f"  node{tag_expr}({bbox_str});\n"
        f"  way{tag_expr}({bbox_str});\n"
        f"  relation{tag_expr}({bbox_str});\n"
    )


def build_category_query(
    bbox: BBox,
    categories: Sequence[OSMCategory],
    timeout_s: float = 30.0,
) -> str:
    """Birden çok kategori için tek bir Overpass QL sorgusu üretir (tek
    ağ isteğinde birden fazla katman çekilir — B6'daki rate-limit/mirror
    fallback kaygısıyla tutarlı: kategori arttıkça istek sayısı değil,
    istek başına iş artar)."""
    bbox_str = bbox.overpass_bbox_str()
    body = "".join(_category_query_clause(bbox_str, c) for c in categories)
    return f"[out:json][timeout:{int(timeout_s)}];\n(\n{body});\nout body;\n>;\nout skel qt;\n"


class OSMCategoryParser:
    """`OSMBuildingParser`'a paralel, ama kategoriye göre nokta/çizgi/alan
    ayrımı yapan genel amaçlı parser (B2'nin üç geometri sınıfı).

    Her `GeoFeature.properties` içine `__category__` anahtarıyla hangi
    `OSMCategory.key`'e ait olduğu damgalanır — B5'teki "katman bazlı
    istatistik" (ör. "156 ağaç, 3.2 km yol") bu alan üzerinden tek geçişte
    hesaplanabilir; bkz. `summarize_categories`.
    """

    @staticmethod
    def parse_overpass_json(
        data: dict,
        categories: Sequence[OSMCategory],
    ) -> GeoFeatureCollection:
        try:
            elements = data.get("elements", [])
            if not isinstance(elements, list):
                raise TypeError(f"'elements' bir liste olmalı, {type(elements).__name__} geldi")

            by_key = {c.key: c for c in categories}
            nodes: dict[int, tuple[float, float]] = {}
            ways: dict[int, dict] = {}
            for el in elements:
                el_type = el.get("type")
                if el_type == "node":
                    nodes[el["id"]] = (el["lon"], el["lat"])
                elif el_type == "way":
                    ways[el["id"]] = el

            features: list[GeoFeature] = []

            for el in elements:
                el_type = el.get("type")
                tags = el.get("tags", {})
                if not tags:
                    continue
                category = OSMCategoryParser._match_category(tags, by_key)
                if category is None:
                    continue

                if el_type == "node" and category.geometry == "point":
                    props = dict(tags)
                    props["osm_id"] = el["id"]
                    props["osm_type"] = "node"
                    props["__category__"] = category.key
                    features.append(
                        GeoFeature(
                            geometry_type="Point",
                            coordinates=[el["lon"], el["lat"]],
                            properties=props,
                        )
                    )

                elif el_type == "way" and category.geometry in ("line", "polygon"):
                    node_ids = el.get("nodes", [])
                    coords = [nodes[nid] for nid in node_ids if nid in nodes]
                    if len(coords) < 2:
                        continue
                    props = dict(tags)
                    props["osm_id"] = el["id"]
                    props["osm_type"] = "way"
                    props["__category__"] = category.key
                    if category.geometry == "polygon":
                        if len(coords) < 3:
                            continue
                        if coords[0] != coords[-1]:
                            coords = coords + [coords[0]]
                        features.append(
                            GeoFeature(
                                geometry_type="Polygon",
                                coordinates=[coords],
                                properties=props,
                            )
                        )
                    else:
                        features.append(
                            GeoFeature(
                                geometry_type="LineString",
                                coordinates=coords,
                                properties=props,
                            )
                        )

                elif el_type == "relation" and category.geometry == "polygon":
                    for member in el.get("members", []):
                        if member.get("type") != "way" or member.get("role") != "outer":
                            continue
                        way = ways.get(member.get("ref"))
                        if way is None:
                            continue
                        node_ids = way.get("nodes", [])
                        coords = [nodes[nid] for nid in node_ids if nid in nodes]
                        if len(coords) < 3:
                            continue
                        if coords[0] != coords[-1]:
                            coords = coords + [coords[0]]
                        props = dict(tags)
                        props["osm_id"] = el["id"]
                        props["osm_type"] = "relation"
                        props["__category__"] = category.key
                        features.append(
                            GeoFeature(
                                geometry_type="Polygon",
                                coordinates=[coords],
                                properties=props,
                            )
                        )

            return GeoFeatureCollection(features, crs="EPSG:4326")
        except OverpassError:
            raise
        except (AttributeError, TypeError, KeyError, ValueError) as exc:
            raise OverpassError(f"Beklenmeyen Overpass yanıt biçimi (kategori): {exc}") from exc

    @staticmethod
    def _match_category(
        tags: dict,
        by_key: dict[str, OSMCategory],
    ) -> OSMCategory | None:
        # Belirlilik önceliği: `key=value` filtreleri, salt `key` filtrelerinden
        # önce kontrol edilir (ör. "natural=tree" "natural=water"dan ayrı
        # kategori olsa da ikisi de `natural` anahtarını paylaşıyor).
        exact = [c for c in by_key.values() if "=" in c.overpass_filter]
        loose = [c for c in by_key.values() if "=" not in c.overpass_filter]
        for c in exact:
            k, v = c.overpass_filter.split("=", 1)
            if tags.get(k) == v:
                return c
        for c in loose:
            if c.overpass_filter in tags:
                return c
        return None


def summarize_categories(collection: GeoFeatureCollection) -> dict[str, int]:
    """Faz C2/B5 için basit katman istatistiği: `{kategori_key: adet}`.
    B5'in tarif ettiği "42 bina, 156 ağaç, 12 direk, 3.2 km yol" tarzı
    önizleme özetinin sayım kısmı (uzunluk/alan hesabı bu fonksiyonun
    kapsamı dışında - `analysis_engine/measurement` zaten var)."""
    counts: dict[str, int] = {}
    for feature in collection.features:
        key = feature.properties.get("__category__", "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def fetch_category_features(
    bbox: BBox,
    category_keys: Sequence[str] | None = None,
    client: OverpassClient | None = None,
) -> GeoFeatureCollection:
    """Faz C2 uçtan uca giriş noktası: bbox + kategori listesi ->
    tek Overpass isteği -> `GeoFeatureCollection` (WGS84, her feature'da
    `__category__` damgalı).

    `category_keys` verilmezse `DEFAULT_CATEGORIES`'in tamamı (roads,
    trees, forest, wood, water_area, waterway) çekilir. Bu fonksiyon
    `fetch_and_generate_buildings`'in yanına eklenmiştir, onun yerine
    geçmez — bina boru hattı ayrı ve değişmeden kalır (roadmap "mevcut
    mimari korunacak" ilkesi)."""
    if client is None:
        client = OverpassClient()
    if category_keys is None:
        categories = list(DEFAULT_CATEGORIES.values())
    else:
        # ROADMAP_V8 Faz 2.3 — `OPTIONAL_CATEGORIES` (ör. `traffic_sign`)
        # `DEFAULT_CATEGORIES`'e dahil değil ama çağıran taraf açıkça
        # isteyerek etkinleştirebilmeli (bkz. `OPTIONAL_CATEGORIES` docstring'i).
        # Bu birleşik tablo olmadan `category_keys=["traffic_sign"]` her
        # zaman "Bilinmeyen kategori" hatası fırlatırdı.
        all_known = {**DEFAULT_CATEGORIES, **OPTIONAL_CATEGORIES}
        unknown = [k for k in category_keys if k not in all_known]
        if unknown:
            raise ValueError(f"Bilinmeyen kategori(ler): {unknown}")
        categories = [all_known[k] for k in category_keys]

    query = build_category_query(bbox, categories, timeout_s=client.timeout_s)
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error: Exception | None = None

    for endpoint in client.endpoints:
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "User-Agent": client.user_agent,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=client.timeout_s) as response:
                raw_bytes = response.read()
            raw = json.loads(raw_bytes.decode("utf-8"))
            return OSMCategoryParser.parse_overpass_json(raw, categories)
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            last_error = exc
            continue

    raise OverpassNetworkError(
        f"Hiçbir Overpass aynasına ulaşılamadı (kategori sorgusu, denenen "
        f"{len(client.endpoints)} uç nokta): {last_error!r}"
    )


__all__ = [
    "DEFAULT_OVERPASS_ENDPOINTS",
    "DEFAULT_USER_AGENT",
    "OverpassError",
    "OverpassNetworkError",
    "BBox",
    "OverpassClient",
    "OSMBuildingParser",
    "project_to_local_meters",
    "OSMIntegrationResult",
    "fetch_and_generate_buildings",
    "OSMCategory",
    "DEFAULT_CATEGORIES",
    "OPTIONAL_CATEGORIES",
    "OSMCategoryParser",
    "build_category_query",
    "summarize_categories",
    "fetch_category_features",
]
