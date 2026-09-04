"""
harita_modelleme/phase1_core_engine/geojson_gis.py
======================================================
FAZ 1 — GIS Core

Roadmap kapsamı (desteklenecek formatlar):
    GeoJSON, Shapefile, KML, KMZ, GPKG, DXF, OBJ, STL, FBX, GLTF, GLB,
    DEM, DSM, HeightMap

Bu modülde:
    - GeoJSON: RFC 7946'ya uygun TAM parser/writer (sıfırdan, bağımlılıksız).
    - FormatRegistry: diğer tüm formatlar (Shapefile, KML, DXF, OBJ, ...)
      için ortak bir "reader/writer" arayüzü + kayıt mekanizması. Bu
      formatların ikili/özel parser'ları hacimli oldukları için ayrı
      dosyalarda (faz 1 içinde ayrı commit'ler halinde) genişletilecek;
      burada iskelet + GeoJSON tam implementasyonu var.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Union

Geometry = dict[str, Any]
Coordinates = Union[
    tuple[float, float],
    tuple[float, float, float],
    list[Any],
]

_VALID_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}


class GeoJSONParseError(ValueError):
    pass


# ============================================================================
# VERİ MODELİ
# ============================================================================


@dataclass
class Feature:
    geometry: Geometry | None
    properties: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None

    def bbox(self) -> tuple[float, float, float, float] | None:
        if self.geometry is None:
            return None
        coords = _flatten_coordinates(self.geometry)
        if not coords:
            return None
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "Feature",
            "geometry": self.geometry,
            "properties": self.properties,
        }
        if self.id is not None:
            d["id"] = self.id
        return d


@dataclass
class FeatureCollection:
    features: list[Feature] = field(default_factory=list)
    crs_name: str = "EPSG:4326"

    def bbox(self) -> tuple[float, float, float, float] | None:
        boxes = [f.bbox() for f in self.features if f.bbox() is not None]
        if not boxes:
            return None
        xs_min = min(b[0] for b in boxes)
        ys_min = min(b[1] for b in boxes)
        xs_max = max(b[2] for b in boxes)
        ys_max = max(b[3] for b in boxes)
        return (xs_min, ys_min, xs_max, ys_max)

    def filter(self, predicate: Callable[[Feature], bool]) -> FeatureCollection:
        return FeatureCollection(
            features=[f for f in self.features if predicate(f)],
            crs_name=self.crs_name,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "FeatureCollection",
            "features": [f.to_dict() for f in self.features],
        }


# ============================================================================
# YARDIMCI: koordinat doğrulama / düzleştirme
# ============================================================================


def _validate_position(pos: Any) -> None:
    if not isinstance(pos, (list, tuple)) or len(pos) < 2:
        raise GeoJSONParseError(f"Geçersiz position: {pos!r}")
    lon, lat = pos[0], pos[1]
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        raise GeoJSONParseError(f"Position sayısal olmalı: {pos!r}")
    if not (-180.0 <= lon <= 180.0):
        raise GeoJSONParseError(f"lon [-180,180] dışında: {lon}")
    if not (-90.0 <= lat <= 90.0):
        raise GeoJSONParseError(f"lat [-90,90] dışında: {lat}")


def _validate_geometry(geom: Geometry) -> None:
    gtype = geom.get("type")
    if gtype not in _VALID_GEOMETRY_TYPES:
        raise GeoJSONParseError(f"Bilinmeyen geometry type: {gtype!r}")

    if gtype == "GeometryCollection":
        for g in geom.get("geometries", []):
            _validate_geometry(g)
        return

    coords = geom.get("coordinates")
    if coords is None:
        raise GeoJSONParseError(f"{gtype} için 'coordinates' eksik")

    if gtype == "Point":
        _validate_position(coords)
    elif gtype == "MultiPoint" or gtype == "LineString":
        if len(coords) < (2 if gtype == "LineString" else 1):
            raise GeoJSONParseError(f"{gtype} en az gerekli nokta sayısına sahip değil")
        for p in coords:
            _validate_position(p)
    elif gtype == "MultiLineString" or gtype == "Polygon":
        for ring in coords:
            for p in ring:
                _validate_position(p)
        if gtype == "Polygon":
            for ring in coords:
                if len(ring) < 4:
                    raise GeoJSONParseError(
                        "Polygon ring en az 4 koordinat içermeli (kapalı halka)"
                    )
                if ring[0] != ring[-1]:
                    raise GeoJSONParseError("Polygon ring kapalı olmalı (ilk == son nokta)")
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                for p in ring:
                    _validate_position(p)
                if len(ring) < 4 or ring[0] != ring[-1]:
                    raise GeoJSONParseError("MultiPolygon ring geçersiz/kapalı değil")


def _flatten_coordinates(geom: Geometry) -> list[tuple[float, float]]:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    out: list[tuple[float, float]] = []

    def _walk(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            out.append((float(node[0]), float(node[1])))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)

    if gtype == "GeometryCollection":
        for g in geom.get("geometries", []):
            out.extend(_flatten_coordinates(g))
        return out

    _walk(coords)
    return out


# ============================================================================
# PARSE / WRITE
# ============================================================================


def parse_geojson(text_or_dict: str | dict[str, Any]) -> FeatureCollection:
    """
    RFC 7946 GeoJSON metnini (veya zaten parse edilmiş dict'i) doğrulayıp
    `FeatureCollection`'a çevirir. Feature / bare Geometry girdilerini de
    kabul eder (tek elemanlı FeatureCollection'a sarar).
    """
    if isinstance(text_or_dict, str):
        try:
            data = json.loads(text_or_dict)
        except json.JSONDecodeError as exc:
            raise GeoJSONParseError(f"Geçersiz JSON: {exc}") from exc
    else:
        data = text_or_dict

    if not isinstance(data, dict) or "type" not in data:
        raise GeoJSONParseError("Kök obje 'type' alanı içermiyor")

    gtype = data["type"]

    if gtype == "FeatureCollection":
        raw_features = data.get("features")
        if not isinstance(raw_features, list):
            raise GeoJSONParseError("FeatureCollection.features bir liste olmalı")
        features = [_parse_feature(f) for f in raw_features]
        return FeatureCollection(features=features)

    if gtype == "Feature":
        return FeatureCollection(features=[_parse_feature(data)])

    if gtype in _VALID_GEOMETRY_TYPES:
        _validate_geometry(data)
        return FeatureCollection(features=[Feature(geometry=data)])

    raise GeoJSONParseError(f"Desteklenmeyen kök type: {gtype!r}")


def _parse_feature(raw: dict[str, Any]) -> Feature:
    if raw.get("type") != "Feature":
        raise GeoJSONParseError(f"Feature.type 'Feature' olmalı, bulunan: {raw.get('type')!r}")
    geometry = raw.get("geometry")
    if geometry is not None:
        _validate_geometry(geometry)
    properties = raw.get("properties") or {}
    if not isinstance(properties, dict):
        raise GeoJSONParseError("Feature.properties bir obje olmalı")
    return Feature(geometry=geometry, properties=properties, id=raw.get("id"))


def write_geojson(collection: FeatureCollection, *, indent: int | None = None) -> str:
    """FeatureCollection -> RFC 7946 uyumlu GeoJSON metni."""
    return json.dumps(collection.to_dict(), indent=indent, ensure_ascii=False)


# ============================================================================
# FORMAT REGISTRY — diğer GIS formatları için genişletilebilir arayüz
# ============================================================================

ReaderFn = Callable[[bytes], FeatureCollection]
WriterFn = Callable[[FeatureCollection], bytes]


class FormatNotImplementedError(NotImplementedError):
    """Roadmap'te listelenen ama henüz implemente edilmemiş format için."""


@dataclass
class FormatHandler:
    name: str
    extensions: tuple[str, ...]
    reader: ReaderFn | None = None
    writer: WriterFn | None = None
    implemented: bool = False


class FormatRegistry:
    """
    Roadmap'in "GIS Core / Desteklenecek formatlar" listesindeki tüm
    formatlar için tek merkezi kayıt noktası. GeoJSON tam çalışır durumda
    kayıtlıdır; ikili/karmaşık formatlar (Shapefile, DXF, OBJ, GLTF, DEM
    vb.) sonraki faz-1 iterasyonlarında `register()` ile eklenecek şekilde
    burada placeholder olarak tanımlıdır — çağrıldıklarında sessizce
    "çalışıyormuş gibi" davranmak yerine açıkça `FormatNotImplementedError`
    fırlatırlar.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, FormatHandler] = {}
        self._register_geojson()
        self._register_placeholders()

    def _register_geojson(self) -> None:
        self._handlers["geojson"] = FormatHandler(
            name="GeoJSON",
            extensions=(".geojson", ".json"),
            reader=lambda b: parse_geojson(b.decode("utf-8")),
            writer=lambda fc: write_geojson(fc).encode("utf-8"),
            implemented=True,
        )

    def _register_placeholders(self) -> None:
        placeholder_formats = {
            "shapefile": (".shp", ".shx", ".dbf"),
            "kml": (".kml",),
            "kmz": (".kmz",),
            "gpkg": (".gpkg",),
            "dxf": (".dxf",),
            "obj": (".obj",),
            "stl": (".stl",),
            "fbx": (".fbx",),
            "gltf": (".gltf",),
            "glb": (".glb",),
            "dem": (".dem", ".tif", ".asc"),
            "dsm": (".dsm",),
            "heightmap": (".raw", ".r16", ".png"),
        }
        for name, exts in placeholder_formats.items():
            self._handlers[name] = FormatHandler(name=name, extensions=exts, implemented=False)

    def register(self, key: str, handler: FormatHandler) -> None:
        self._handlers[key] = handler

    def get(self, key: str) -> FormatHandler:
        key = key.lower()
        if key not in self._handlers:
            raise KeyError(f"Bilinmeyen format: {key}")
        return self._handlers[key]

    def read(self, key: str, data: bytes) -> FeatureCollection:
        handler = self.get(key)
        if not handler.implemented or handler.reader is None:
            raise FormatNotImplementedError(
                f"'{handler.name}' formatı için reader henüz eklenmedi "
                "(Faz 1 devam ediyor — bkz. ROADMAP.md)."
            )
        return handler.reader(data)

    def write(self, key: str, collection: FeatureCollection) -> bytes:
        handler = self.get(key)
        if not handler.implemented or handler.writer is None:
            raise FormatNotImplementedError(
                f"'{handler.name}' formatı için writer henüz eklenmedi "
                "(Faz 1 devam ediyor — bkz. ROADMAP.md)."
            )
        return handler.writer(collection)

    def supported_formats(self) -> list[str]:
        return sorted(k for k, h in self._handlers.items() if h.implemented)

    def planned_formats(self) -> list[str]:
        return sorted(k for k, h in self._handlers.items() if not h.implemented)
