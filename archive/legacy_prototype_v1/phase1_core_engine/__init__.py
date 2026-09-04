"""
harita_modelleme.phase1_core_engine
====================================
FAZ 1 — CORE ENGINE

Roadmap'teki "Harita Motoru", "GIS Core", "Coordinate Systems" ve
"Geometry Engine" başlıklarının uygulanmış hali.

Alt modüller:
    coordinate_systems  -> WGS84 / UTM / Web Mercator / Local CS dönüşümleri
    tile_engine          -> Tile matematiği, zoom seviyeleri, cache, async loader
    geojson_gis           -> GeoJSON parser/writer + format registry
    geometry_engine       -> Polygon / Line / Point algoritmaları (sıfırdan)
"""

from __future__ import annotations

from .coordinate_systems import (
    WGS84,
    LocalCoordinateSystem,
    UTMCoordinate,
    WebMercatorCoordinate,
    haversine_distance_m,
    utm_to_wgs84,
    web_mercator_to_wgs84,
    wgs84_to_utm,
    wgs84_to_web_mercator,
)
from .geojson_gis import (
    Feature,
    FeatureCollection,
    FormatRegistry,
    GeoJSONParseError,
    parse_geojson,
    write_geojson,
)
from .geometry_engine import (
    LineOps,
    Point2D,
    PointOps,
    PolygonOps,
)
from .tile_engine import (
    DiskTileCache,
    MemoryTileCache,
    TileCoordinate,
    TileEngine,
    lonlat_to_tile,
    tile_to_lonlat_bounds,
)

__all__ = [
    "WGS84",
    "UTMCoordinate",
    "WebMercatorCoordinate",
    "LocalCoordinateSystem",
    "wgs84_to_web_mercator",
    "web_mercator_to_wgs84",
    "wgs84_to_utm",
    "utm_to_wgs84",
    "haversine_distance_m",
    "TileCoordinate",
    "TileEngine",
    "MemoryTileCache",
    "DiskTileCache",
    "lonlat_to_tile",
    "tile_to_lonlat_bounds",
    "GeoJSONParseError",
    "Feature",
    "FeatureCollection",
    "parse_geojson",
    "write_geojson",
    "FormatRegistry",
    "Point2D",
    "PolygonOps",
    "LineOps",
    "PointOps",
]

__version__ = "0.1.0-phase1"
