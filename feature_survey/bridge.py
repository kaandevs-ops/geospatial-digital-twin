"""Feature survey verisini mevcut `core_engine.gis_core` yapılarına bağlar.

Bu köprü sayesinde bu modül, mesh/terrain/BIM ardılını hiç değiştirmeden
yeniden kullanır (roadmap'teki "PointCloud.to_heightmap_grid ile mevcut
TerrainMeshGenerator hiç değiştirilmeden yeniden kullanılabilir" ilkesiyle
birebir aynı desen)."""

from __future__ import annotations

from pathlib import Path

from ..core_engine.coordinate_systems import CoordinateConverter, CoordinateSystem, GeoPoint, ProjectedPoint
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection
from ..core_engine.gis_core.point_cloud import LASPointCloudParser, PointCloud
from .field_point import FieldPoint, FieldSurveySession
from .pipeline import PhotogrammetryResult


def _points_to_ring(points: list[FieldPoint]) -> list[list[float]]:
    ring = [[p.easting, p.northing, p.elevation] for p in points]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])  # poligonu kapat
    return ring


def session_to_geofeatures(session: FieldSurveySession) -> GeoFeatureCollection:
    """`FieldSurveySession`'ı vektör katmanlara (`GeoFeatureCollection`)
    dönüştürür — arayüzde QGIS/harita katmanı olarak veya `gis_core`'un
    zaten desteklediği GeoJSON/DXF/GPKG exportlarına doğrudan aktarılabilir.

    Kural:
        - `code.geometry_hint == "Point"`  → her nokta ayrı bir Point feature.
        - `code.geometry_hint in ("LineString", "Polygon")` ve noktanın
          `string_id`'si varsa → aynı `string_id`'ye sahip noktalar sıraya
          göre birleştirilir (Polygon ise halka kapatılır).
        - `string_id`'si olmayan çizgi/poligon-kodlu noktalar tek başına
          Point olarak düşer (veri kaybetmemek için) — bu, sahada henüz
          string'lenmemiş ham noktalar için beklenen davranıştır.
    """
    features: list[GeoFeature] = []
    strings = session.strings()
    stringed_ids = {p.point_id for pts in strings.values() for p in pts}

    for point in session.points:
        if point.point_id in stringed_ids:
            continue  # aşağıda grup olarak işlenecek
        features.append(
            GeoFeature(
                geometry_type="Point",
                coordinates=[point.easting, point.northing, point.elevation],
                properties={
                    "point_id": point.point_id,
                    "code": point.code.value,
                    "category": point.code.category.value,
                    "description": point.description,
                    "instrument": point.instrument,
                },
            )
        )

    for string_id, pts in strings.items():
        hint = pts[0].code.geometry_hint
        if hint == "Polygon":
            features.append(
                GeoFeature(
                    geometry_type="Polygon",
                    coordinates=[_points_to_ring(pts)],
                    properties={
                        "string_id": string_id,
                        "code": pts[0].code.value,
                        "category": pts[0].code.category.value,
                        "point_count": len(pts),
                    },
                )
            )
        else:
            features.append(
                GeoFeature(
                    geometry_type="LineString",
                    coordinates=[[p.easting, p.northing, p.elevation] for p in pts],
                    properties={
                        "string_id": string_id,
                        "code": pts[0].code.value,
                        "category": pts[0].code.category.value,
                        "point_count": len(pts),
                    },
                )
            )

    return GeoFeatureCollection(features=features, crs=session.crs)


def session_to_wgs84_geofeatures(
    session: FieldSurveySession, origin: GeoPoint
) -> GeoFeatureCollection:
    """`session_to_geofeatures` ile aynı geometriyi üretir, ancak yerel/
    projeksiyonlu (metre) `easting`/`northing` koordinatlarını verilen bir
    `origin` (WGS84 referans noktası — sahada genelde ilk kontrol noktası
    veya proje merkezi) üzerinden `CoordinateConverter.local_to_wgs84` ile
    lat/lon'a çevirir. Bu sayede sonuç doğrudan Leaflet/OSM tabanlı harita
    katmanına çizilebilir (arayüzdeki 'sadece JSON önizlemesi' kısıtı
    burada kalkar).

    Not: `FieldPoint.easting/northing`, `CoordinateConverter.wgs84_to_local`
    ile üretilen `ProjectedPoint`'le aynı sözleşmeye sahiptir (x=doğu,
    y=kuzey, orijine göre metre) — bu yüzden saha koordinatları doğrudan
    `ProjectedPoint(x=easting, y=northing, system=LOCAL, elevation=...)`
    olarak yorumlanabilir.
    """
    local_collection = session_to_geofeatures(session)

    def _convert_coord(coord: list) -> list[float]:
        x, y = coord[0], coord[1]
        z = coord[2] if len(coord) > 2 else 0.0
        projected = ProjectedPoint(x=x, y=y, system=CoordinateSystem.LOCAL, elevation=z)
        geo = CoordinateConverter.local_to_wgs84(projected, origin)
        return [geo.lon, geo.lat, geo.elevation]

    def _convert_geometry(geometry_type: str, coordinates):
        if geometry_type == "Point":
            return _convert_coord(coordinates)
        if geometry_type == "LineString":
            return [_convert_coord(c) for c in coordinates]
        if geometry_type == "Polygon":
            return [[_convert_coord(c) for c in ring] for ring in coordinates]
        raise ValueError(f"Bilinmeyen geometri tipi: {geometry_type}")

    wgs84_features = [
        GeoFeature(
            geometry_type=f.geometry_type,
            coordinates=_convert_geometry(f.geometry_type, f.coordinates),
            properties=f.properties,
        )
        for f in local_collection.features
    ]
    return GeoFeatureCollection(features=wgs84_features, crs="EPSG:4326")


def photogrammetry_result_to_point_cloud(result: PhotogrammetryResult) -> PointCloud:
    """WebODM/Meshroom çıktısındaki nokta bulutunu (`.las`) mevcut
    `PointCloud` temsiline okur, böylece `PointCloud.to_heightmap_grid()`
    üzerinden terrain_engine'e doğrudan akar.

    Not: yalnızca `.las` desteklenir (proje genelindeki stdlib-only ilkesiyle
    tutarlı); `.ply`/`.laz` çıktısı için önce `.las`'a dönüştürülmeli
    (örn. CloudCompare/PDAL ile, roadmap'teki önerilen açık kaynak araçlar).
    """
    if result.point_cloud_path is None:
        raise ValueError(
            f"'{result.engine}' sonucunda nokta bulutu yolu yok — "
            "önce fotogrametri koşusunun bir point cloud ürettiğinden emin olun."
        )
    path = Path(result.point_cloud_path)
    if path.suffix.lower() != ".las":
        raise ValueError(
            f"Sadece .las destekleniyor, alınan: {path.suffix}. "
            "PDAL/CloudCompare ile .las'a dönüştürün (bkz. modül docstring'i)."
        )
    return LASPointCloudParser.parse_file(path)
