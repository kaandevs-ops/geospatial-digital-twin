"""
GIS Core
=========

Roadmap Phase 1 - "GIS Core" - desteklenen formatlar:
GeoJSON, Shapefile, KML, KMZ, GPKG, DXF, OBJ, STL, GLTF, DEM/DSM/HeightMap
(ESRI ASCII Grid + ham binary raster). Tamamı stdlib-only (bağımlılıksız):

    - GeoJSON        : RFC 7946 uyumlu tam okuyucu/yazıcı.
    - Shapefile       : .shp binary format (Point/PolyLine/Polygon/Multipoint,
                         Z-varyantları dahil) + opsiyonel .dbf öznitelik tablosu.
    - KML / KMZ        : xml.etree tabanlı <Placemark>/<Point|LineString|Polygon>
                          + <ExtendedData> okuyucu; KMZ, zipfile ile açılıp
                          içindeki .kml'e devredilir.
    - GeoPackage (GPKG) : sqlite3 + OGC GPKG binary header + WKB geometri çözücü
                           (Point/LineString/Polygon/Multi* - 2D ve Z).
    - DXF               : ASCII DXF grup-kodu okuyucu (LINE/LWPOLYLINE/POLYLINE+
                           VERTEX/POINT/3DFACE entity'leri).
    - Mesh3D (OBJ/STL/GLTF) : OBJ (ASCII), STL (ASCII + binary), GLTF (JSON,
                               gömülü/base64 buffer) okuyucuları; `GeoFeature`
                               içine `geometry_type="Mesh3D"`, `coordinates`
                               içine `{"vertices": [...], "faces": [...]}`
                               olarak yazılır (Phase 2 mesh_engine.Mesh3D'ye
                               bir üst adaptör katmanında dönüştürülür).
                               FBX ve GLB (binary-glTF), kapalı/ikili tescilli
                               format karmaşıklığı nedeniyle bilinçli olarak
                               kapsam dışı bırakılmıştır (bkz. sınıf docstring'i).
    - Heightmap (DEM/DSM) : ESRI ASCII Grid (.asc/.grd, tam) + basit ham
                             binary raster (float32 satır-major + JSON header).
                             Sıkıştırılmış GeoTIFF, harici bir codec
                             gerektirdiğinden kapsam dışıdır.

ROADMAP_V4 - Faz E4: nokta bulutu (LiDAR) desteği ayrı bir alt modülde -
`core_engine.gis_core.point_cloud` - eklendi: `LASPointCloudParser`
(LAS 1.2/1.3/1.4 baseline, Point Data Format 0-3, stdlib `struct`-only) ve
opsiyonel `laspy`/`lazrs` (`[cloud]` extra) üzerinden LAZ. `PointCloud.
to_heightmap_grid()` ile nokta bulutundan `terrain_engine.HeightmapGrid`'e
gridleme köprüsü sağlanır (bkz. `point_cloud.py` modül docstring'i).
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..geometry_engine import LineString, Point2D, Polygon
from . import tiff_lzw as _tiff_lzw


class GISParseError(ValueError):
    """Bozuk/hatalı biçimlendirilmiş bir GIS dosyası ayrıştırılırken
    fırlatılır (Roadmap V2, A1 - "fuzz testi": parser'lar ham
    exception/traceback fırlatmak yerine bu anlamlı hatayı döndürür)."""


class UnsupportedFormatError(GISParseError):
    """Roadmap V3 - D14 / Roadmap V7: dosya sözdizimsel olarak geçerli bir
    TIFF/GeoTIFF ama gerçekten desteklenmeyen bir sıkıştırma ya da örnek
    biçimi kullanıyor (DEFLATE/Adobe-Deflate ve artık LZW da desteklenir -
    bkz. `tiff_lzw.py`; tiled TIFF ve bilinmeyen sıkıştırma kodları hâlâ
    kapsam dışıdır). Sessizce yanlış sonuç üretmek yerine açıkça bu hata
    fırlatılır."""


def _reraise_as_parse_error(exc: Exception, context: str) -> None:
    """Düşük seviye ayrıştırma hatalarını (struct/IndexError/XML/zip/sqlite)
    anlamlı bir `GISParseError`'a çevirir. Zaten `GISParseError` ise
    (örn. bilinçli olarak fırlatılmış bir `ValueError`) olduğu gibi bırakır."""
    if isinstance(exc, GISParseError):
        raise exc
    raise GISParseError(f"{context}: {type(exc).__name__}: {exc}") from exc


# ======================================================================== #
# Ortak veri modeli
# ======================================================================== #


@dataclass
class GeoFeature:
    """Tek bir coğrafi öğe: geometri + özellik (attribute) sözlüğü."""

    geometry_type: str  # "Point" | "LineString" | "Polygon" | "MultiPolygon" ...
    coordinates: Any  # ham koordinat verisi (lon, lat[, elev]) yapısında
    properties: dict[str, Any] = field(default_factory=dict)

    def to_polygon(self) -> Polygon:
        if self.geometry_type != "Polygon":
            raise ValueError(f"'{self.geometry_type}' bir Polygon'a çevrilemez.")
        ring = self.coordinates[0]  # dış halka (iç halkalar/delikler şimdilik yok sayılır)
        return Polygon([Point2D(c[0], c[1]) for c in ring])

    def to_linestring(self) -> LineString:
        if self.geometry_type != "LineString":
            raise ValueError(f"'{self.geometry_type}' bir LineString'e çevrilemez.")
        return LineString([Point2D(c[0], c[1]) for c in self.coordinates])

    def to_point(self) -> Point2D:
        if self.geometry_type != "Point":
            raise ValueError(f"'{self.geometry_type}' bir Point'e çevrilemez.")
        return Point2D(self.coordinates[0], self.coordinates[1])


@dataclass
class GeoFeatureCollection:
    features: list[GeoFeature] = field(default_factory=list)
    crs: str = "EPSG:4326"

    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self):
        return iter(self.features)

    def filter(self, **props) -> GeoFeatureCollection:
        """properties eşleşmesine göre filtreleme (ör. filter(bina_tipi='ofis'))."""
        matched = [
            f for f in self.features if all(f.properties.get(k) == v for k, v in props.items())
        ]
        return GeoFeatureCollection(matched, crs=self.crs)


# ======================================================================== #
# GeoJSON Parser (tam uygulama)
# ======================================================================== #


class GeoJSONParser:
    """RFC 7946 uyumlu temel GeoJSON okuyucu/yazıcı."""

    @staticmethod
    def parse(text: str) -> GeoFeatureCollection:
        try:
            data = json.loads(text)
            return GeoJSONParser._from_dict(data)
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            _reraise_as_parse_error(e, "Bozuk GeoJSON")

    @staticmethod
    def parse_file(path: str) -> GeoFeatureCollection:
        with open(path, encoding="utf-8") as fh:
            return GeoJSONParser.parse(fh.read())

    @staticmethod
    def _from_dict(data: dict) -> GeoFeatureCollection:
        gtype = data.get("type")
        features: list[GeoFeature] = []

        if gtype == "FeatureCollection":
            for feat in data.get("features", []):
                features.append(GeoJSONParser._parse_feature(feat))
        elif gtype == "Feature":
            features.append(GeoJSONParser._parse_feature(data))
        elif gtype in (
            "Point",
            "LineString",
            "Polygon",
            "MultiPoint",
            "MultiLineString",
            "MultiPolygon",
            "GeometryCollection",
        ):
            features.append(
                GeoFeature(geometry_type=gtype, coordinates=data.get("coordinates"), properties={})
            )
        else:
            raise ValueError(f"Desteklenmeyen/eksik GeoJSON 'type': {gtype!r}")

        crs = "EPSG:4326"
        if "crs" in data:
            crs = data["crs"].get("properties", {}).get("name", crs)

        return GeoFeatureCollection(features, crs=crs)

    @staticmethod
    def _parse_feature(feat: dict) -> GeoFeature:
        geometry = feat.get("geometry") or {}
        return GeoFeature(
            geometry_type=geometry.get("type", "Unknown"),
            coordinates=geometry.get("coordinates"),
            properties=feat.get("properties", {}) or {},
        )

    @staticmethod
    def to_geojson(collection: GeoFeatureCollection) -> str:
        return json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": f.geometry_type,
                            "coordinates": f.coordinates,
                        },
                        "properties": f.properties,
                    }
                    for f in collection.features
                ],
            },
            ensure_ascii=False,
            indent=2,
        )


# ======================================================================== #
# Ortak format arayüzü + diğer format iskeletleri (Faz-1 spesifikasyonu)
# ======================================================================== #


class FormatParser(ABC):
    """Tüm GIS/3D format parser'larının uyacağı ortak arayüz."""

    @abstractmethod
    def parse_file(self, path: str) -> GeoFeatureCollection: ...


class ShapefileParser(FormatParser):
    """
    .shp (+ opsiyonel eşlenik .dbf) binary okuyucu.

    Desteklenen shape type kodları (ESRI Shapefile Technical Description):
        1  Point            11 PointZ
        3  PolyLine         13 PolyLineZ
        5  Polygon          15 PolygonZ
        8  MultiPoint       18 MultiPointZ
    `.shx` index dosyası gerekmez (kayıtlar `.shp` içinde sırayla okunur).
    `.dbf` varsa (aynı temel isimle yan yana) her kaydın özniteliklerini
    `GeoFeature.properties`'e eşler; yoksa properties boş kalır.
    """

    _POINT_TYPES = {1, 11, 21}
    _POLYLINE_TYPES = {3, 13, 23}
    _POLYGON_TYPES = {5, 15, 25}
    _MULTIPOINT_TYPES = {8, 18, 28}

    def parse_file(self, path: str) -> GeoFeatureCollection:
        try:
            return self._parse_file_impl(path)
        except (ValueError, struct.error, IndexError, OSError) as e:
            _reraise_as_parse_error(e, f"Bozuk Shapefile ({path})")

    def _parse_file_impl(self, path: str) -> GeoFeatureCollection:
        with open(path, "rb") as fh:
            data = fh.read()

        # --- .shp ana başlık (100 byte, big-endian file code + little-endian gövde) ---
        if len(data) < 100:
            raise GISParseError(
                f"Shapefile başlığı için çok kısa dosya ({len(data)} byte, en az 100 gerekli)"
            )
        file_code = struct.unpack(">i", data[0:4])[0]
        if file_code != 9994:
            raise ValueError(f"Geçersiz Shapefile: file code {file_code} (9994 bekleniyordu).")
        shape_type = struct.unpack("<i", data[32:36])[0]

        records: list[tuple[str, Any]] = []
        offset = 100
        n = len(data)
        while offset < n - 8:
            rec_number, content_len = struct.unpack(">ii", data[offset : offset + 8])
            if content_len < 0:
                raise GISParseError(f"Geçersiz negatif record uzunluğu: {content_len}")
            content_start = offset + 8
            content_end = content_start + content_len * 2  # word -> byte
            content = data[content_start:content_end]
            records.append(self._parse_record(content, shape_type))
            offset = content_end

        attrs = self._read_dbf(path)

        features = []
        for i, (gtype, coords) in enumerate(records):
            if gtype is None:
                continue
            props = attrs[i] if i < len(attrs) else {}
            features.append(GeoFeature(geometry_type=gtype, coordinates=coords, properties=props))
        return GeoFeatureCollection(features, crs="EPSG:4326")

    def _parse_record(self, content: bytes, shape_type: int) -> tuple[str | None, Any]:
        if len(content) < 4:
            return None, None
        rec_shape_type = struct.unpack("<i", content[0:4])[0]
        if rec_shape_type == 0:  # Null shape
            return None, None

        if rec_shape_type in self._POINT_TYPES:
            x, y = struct.unpack("<dd", content[4:20])
            return "Point", [x, y]

        if rec_shape_type in self._MULTIPOINT_TYPES:
            num_points = struct.unpack("<i", content[36:40])[0]
            remaining = len(content) - 40
            if num_points < 0 or num_points > remaining // 16:
                raise GISParseError(
                    f"Geçersiz/aşırı büyük num_points beyanı: {num_points} "
                    f"(kalan veri yalnızca {remaining} byte)"
                )
            pts = []
            base = 40
            for i in range(num_points):
                x, y = struct.unpack("<dd", content[base : base + 16])
                pts.append([x, y])
                base += 16
            return "MultiPoint", pts

        if rec_shape_type in self._POLYLINE_TYPES or rec_shape_type in self._POLYGON_TYPES:
            num_parts, num_points = struct.unpack("<ii", content[36:44])
            parts_start = 44
            # Güvenlik: num_parts/num_points, dev bir 32-bit değer olarak
            # beyan edilirse (bozuk/kötü niyetli dosya) `f"<{num_parts}i"`
            # gibi bir format string kurmak veya `range(num_points)` üzerinde
            # dönmek, gerçek dosya bu kadar veri içermese bile bellek/CPU
            # tüketen bir DoS'a yol açabilir. Beyan edilen sayı, kalan
            # gerçek byte miktarına göre mantıksal olarak imkansızsa
            # (her part 4 byte, her point en az 16 byte gerektirir),
            # verinin gerçekten bu kadar olamayacağı hemen anlaşılır ve
            # dev yapılar kurulmadan önce anlamlı bir hata fırlatılır.
            remaining = len(content) - parts_start
            if num_parts < 0 or num_parts > remaining // 4:
                raise GISParseError(
                    f"Geçersiz/aşırı büyük num_parts beyanı: {num_parts} "
                    f"(kalan veri yalnızca {remaining} byte)"
                )
            if num_points < 0 or num_points > (remaining - 4 * num_parts) // 16:
                raise GISParseError(
                    f"Geçersiz/aşırı büyük num_points beyanı: {num_points} "
                    f"(kalan veri yalnızca {remaining - 4 * num_parts} byte)"
                )
            parts = list(
                struct.unpack(f"<{num_parts}i", content[parts_start : parts_start + 4 * num_parts])
            )
            points_start = parts_start + 4 * num_parts
            all_points = []
            base = points_start
            for _ in range(num_points):
                x, y = struct.unpack("<dd", content[base : base + 16])
                all_points.append([x, y])
                base += 16

            rings: list[list[list[float]]] = []
            for pi, start_idx in enumerate(parts):
                end_idx = parts[pi + 1] if pi + 1 < len(parts) else num_points
                rings.append(all_points[start_idx:end_idx])

            if rec_shape_type in self._POLYGON_TYPES:
                return "Polygon", rings
            # PolyLine: birden fazla part varsa MultiLineString olarak dön
            if len(rings) > 1:
                return "MultiLineString", rings
            return "LineString", rings[0] if rings else []

        # Bilinmeyen/desteklenmeyen alt tip (ör. MultiPatch=31) - atla.
        return None, None

    @staticmethod
    def _read_dbf(shp_path: str) -> list[dict[str, Any]]:
        dbf_path = re.sub(r"\.shp$", ".dbf", str(shp_path), flags=re.IGNORECASE)
        if not Path(dbf_path).exists():
            return []
        with open(dbf_path, "rb") as fh:
            header = fh.read(32)
            num_records = struct.unpack("<i", header[4:8])[0]
            header_len, record_len = struct.unpack("<hh", header[8:12])
            field_area = fh.read(header_len - 32)
            fields = []
            for i in range(0, len(field_area) - 1, 32):
                chunk = field_area[i : i + 32]
                if not chunk or chunk[0:1] == b"\r":
                    break
                name = chunk[0:11].split(b"\x00")[0].decode("ascii", errors="replace")
                ftype = chr(chunk[11])
                flen = chunk[16]
                fields.append((name, ftype, flen))

            records = []
            for _ in range(num_records):
                raw = fh.read(record_len)
                if not raw or raw[0:1] == b"*":
                    records.append({})
                    continue
                row: dict[str, Any] = {}
                pos = 1  # ilk byte: silinme bayrağı
                for name, ftype, flen in fields:
                    raw_val = raw[pos : pos + flen].decode("ascii", errors="replace").strip()
                    pos += flen
                    if ftype in ("N", "F"):
                        try:
                            row[name] = float(raw_val) if raw_val else None
                            if row[name] is not None and row[name] == int(row[name]):
                                row[name] = int(row[name])
                        except ValueError:
                            row[name] = raw_val
                    else:
                        row[name] = raw_val
                records.append(row)
            return records


class KMLParser(FormatParser):
    """XML tabanlı KML; `<Placemark>` içindeki `<Point>/<LineString>/<Polygon>/
    <MultiGeometry>` geometrilerini ve `<ExtendedData><SimpleData>` /
    düz `<name>`/`<description>` alanlarını `GeoFeature.properties`'e okur."""

    _NS = "{http://www.opengis.net/kml/2.2}"

    def parse_file(self, path: str) -> GeoFeatureCollection:
        try:
            tree = ET.parse(path)
            return self._parse_tree(tree)
        except (ET.ParseError, ValueError, IndexError) as e:
            _reraise_as_parse_error(e, f"Bozuk KML ({path})")

    def parse_string(self, text: str) -> GeoFeatureCollection:
        try:
            root = ET.fromstring(text)
            return self._parse_tree(ET.ElementTree(root))
        except (ET.ParseError, ValueError, IndexError) as e:
            _reraise_as_parse_error(e, "Bozuk KML (string)")

    def _parse_tree(self, tree: ET.ElementTree) -> GeoFeatureCollection:
        root = tree.getroot()
        features: list[GeoFeature] = []
        for placemark in self._findall_any_ns(root, "Placemark"):
            props = self._extract_properties(placemark)
            for tag, gtype in (
                ("Point", "Point"),
                ("LineString", "LineString"),
                ("Polygon", "Polygon"),
                ("Track", "LineString"),
            ):
                for geom_el in self._findall_any_ns(placemark, tag):
                    coords = self._parse_geometry(geom_el, tag)
                    if coords is not None:
                        features.append(GeoFeature(gtype, coords, dict(props)))
            for multi in self._findall_any_ns(placemark, "MultiGeometry"):
                for tag, gtype in (
                    ("Point", "Point"),
                    ("LineString", "LineString"),
                    ("Polygon", "Polygon"),
                ):
                    for geom_el in self._findall_any_ns(multi, tag):
                        coords = self._parse_geometry(geom_el, tag)
                        if coords is not None:
                            features.append(GeoFeature(gtype, coords, dict(props)))
        return GeoFeatureCollection(features, crs="EPSG:4326")

    @staticmethod
    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _findall_any_ns(self, el: ET.Element, local_name: str) -> list[ET.Element]:
        return [c for c in el.iter() if self._local(c.tag) == local_name]

    def _parse_geometry(self, geom_el: ET.Element, tag: str):
        if tag == "Point":
            coords_el = next(iter(self._findall_any_ns(geom_el, "coordinates")), None)
            if coords_el is None or not coords_el.text:
                return None
            return self._parse_coord_tuple(coords_el.text.strip())
        if tag == "LineString":
            coords_el = next(iter(self._findall_any_ns(geom_el, "coordinates")), None)
            if coords_el is None or not coords_el.text:
                return None
            return self._parse_coord_list(coords_el.text)
        if tag == "Polygon":
            rings = []
            outer = next(iter(self._findall_any_ns(geom_el, "outerBoundaryIs")), None)
            if outer is not None:
                coords_el = next(iter(self._findall_any_ns(outer, "coordinates")), None)
                if coords_el is not None and coords_el.text:
                    rings.append(self._parse_coord_list(coords_el.text))
            for inner in self._findall_any_ns(geom_el, "innerBoundaryIs"):
                coords_el = next(iter(self._findall_any_ns(inner, "coordinates")), None)
                if coords_el is not None and coords_el.text:
                    rings.append(self._parse_coord_list(coords_el.text))
            return rings if rings else None
        return None

    @staticmethod
    def _parse_coord_tuple(text: str) -> list[float]:
        parts = [p for p in text.strip().split(",") if p != ""]
        return [float(p) for p in parts]

    @classmethod
    def _parse_coord_list(cls, text: str) -> list[list[float]]:
        tokens = text.strip().split()
        return [cls._parse_coord_tuple(tok) for tok in tokens if tok.strip()]

    def _extract_properties(self, placemark: ET.Element) -> dict[str, Any]:
        props: dict[str, Any] = {}
        name_el = next(iter(self._findall_any_ns(placemark, "name")), None)
        if name_el is not None and name_el.text:
            props["name"] = name_el.text.strip()
        desc_el = next(iter(self._findall_any_ns(placemark, "description")), None)
        if desc_el is not None and desc_el.text:
            props["description"] = desc_el.text.strip()
        for sd in self._findall_any_ns(placemark, "SimpleData"):
            key = sd.get("name")
            if key:
                props[key] = (sd.text or "").strip()
        return props


class KMZParser(FormatParser):
    """KMZ = zip arşivi içinde `doc.kml` (+ opsiyonel resim/model varlıkları).
    Arşivi açar, içindeki ilk `.kml` dosyasını bulur ve `KMLParser`'a devreder.

    **Faz 21 güvenlik sertleştirmesi (zip-bomb koruması):** `ZipFile.read()`
    çağırmadan önce hem arşivdeki toplam üye sayısı hem de hedef `.kml`
    üyesinin *beyan edilen* sıkıştırılmamış boyutu (`ZipInfo.file_size`)
    kontrol edilir. Kötü niyetli bir KMZ, birkaç KB'lık sıkıştırılmış veriyle
    gigabaytlarca sıkıştırılmamış içerik beyan edebilir (klasik zip-bomb);
    bu durumda dosya hiç `read()` edilmeden (belleğe açılmadan) reddedilir.
    """

    #: Tek bir .kml üyesi için izin verilen azami sıkıştırılmamış boyut (128 MB).
    #: Gerçek dünyadaki en büyük KML dosyaları bile birkaç MB'ı geçmez; bu sınır
    #: meşru kullanımı etkilemeden zip-bomb'ları engellemek için cömertçe seçildi.
    MAX_UNCOMPRESSED_KML_BYTES = 128 * 1024 * 1024
    #: Arşivdeki azami üye (entry) sayısı — "many small files" tarzı zip-bomb'lara
    #: (milyonlarca sıfır-byte'lık üye ile ZipFile.namelist()'i şişirme) karşı.
    MAX_ARCHIVE_MEMBERS = 10_000

    def parse_file(self, path: str) -> GeoFeatureCollection:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                infos = zf.infolist()
                if len(infos) > self.MAX_ARCHIVE_MEMBERS:
                    raise ValueError(
                        f"KMZ arşivi çok fazla üye içeriyor ({len(infos)} > "
                        f"{self.MAX_ARCHIVE_MEMBERS}) — olası zip-bomb, reddedildi."
                    )
                kml_infos = [zi for zi in infos if zi.filename.lower().endswith(".kml")]
                if not kml_infos:
                    raise ValueError("KMZ arşivi içinde .kml dosyası bulunamadı.")
                # Genellikle 'doc.kml' kök dizinde olur; yoksa ilk bulunanı kullan.
                preferred = [zi for zi in kml_infos if Path(zi.filename).name.lower() == "doc.kml"]
                target = preferred[0] if preferred else kml_infos[0]
                if target.file_size > self.MAX_UNCOMPRESSED_KML_BYTES:
                    raise ValueError(
                        f"KMZ içindeki '{target.filename}' beyan edilen "
                        f"sıkıştırılmamış boyutu ({target.file_size} byte) izin "
                        f"verilen azami boyutu ({self.MAX_UNCOMPRESSED_KML_BYTES} "
                        "byte) aşıyor — olası zip-bomb, dosya açılmadan reddedildi."
                    )
                text = zf.read(target).decode("utf-8", errors="replace")
            return KMLParser().parse_string(text)
        except (zipfile.BadZipFile, ValueError) as e:
            _reraise_as_parse_error(e, f"Bozuk KMZ ({path})")


class GeoPackageParser(FormatParser):
    """GPKG = SQLite container + OGC standart binary geometri (GPB) formatı.

    Her satır: 'GP' magic (2 byte) + version (1 byte) + flags (1 byte) +
    [opsiyonel envelope] + standart WKB geometri. WKB: byte-order (1) +
    geom-type uint32 (little/big-endian'a göre) + koordinatlar.
    """

    _WKB_TYPE_NAMES = {
        1: "Point",
        2: "LineString",
        3: "Polygon",
        4: "MultiPoint",
        5: "MultiLineString",
        6: "MultiPolygon",
    }

    def parse_file(self, path: str) -> GeoFeatureCollection:
        conn = sqlite3.connect(path)
        try:
            try:
                return self._parse_file_impl(conn)
            except (ValueError, TypeError, KeyError, IndexError, struct.error, sqlite3.Error) as e:
                _reraise_as_parse_error(e, f"Bozuk GeoPackage ({path})")
        finally:
            conn.close()

    def _parse_file_impl(self, conn: sqlite3.Connection) -> GeoFeatureCollection:
        cur = conn.cursor()
        cur.execute("SELECT table_name, column_name FROM gpkg_geometry_columns")
        geom_columns = cur.fetchall()
        if not geom_columns:
            raise ValueError("GPKG içinde gpkg_geometry_columns kaydı bulunamadı.")

        features: list[GeoFeature] = []
        for table_name, column_name in geom_columns:
            # Tablo adları teorik olarak kötü niyetli bir dosyada tırnak/özel
            # karakter içerebilir (SQL identifier kaçış denemesi); çift
            # tırnağı kaçırarak sorguyu tabloya sabitliyoruz.
            safe_table = str(table_name).replace('"', '""')
            cur.execute(f'SELECT * FROM "{safe_table}"')
            col_names = [d[0] for d in cur.description]
            geom_idx = col_names.index(column_name)
            for row in cur.fetchall():
                blob = row[geom_idx]
                if blob is None:
                    continue
                gtype, coords = self._parse_gpb(blob)
                if gtype is None:
                    continue
                props = {col_names[i]: row[i] for i in range(len(row)) if i != geom_idx}
                features.append(GeoFeature(gtype, coords, props))
        return GeoFeatureCollection(features, crs="EPSG:4326")

    def _parse_gpb(self, blob: bytes):
        if len(blob) < 8 or blob[0:2] != b"GP":
            return None, None
        flags = blob[3]
        envelope_indicator = (flags >> 1) & 0x07
        envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
        env_len = envelope_sizes.get(envelope_indicator, 0)
        wkb_offset = 8 + env_len
        wkb = blob[wkb_offset:]
        return self._parse_wkb(wkb)

    def _parse_wkb(self, wkb: bytes):
        if len(wkb) < 5:
            return None, None
        byte_order = "<" if wkb[0] == 1 else ">"
        geom_type_raw = struct.unpack(byte_order + "I", wkb[1:5])[0]
        has_z = geom_type_raw >= 1000
        base_type = geom_type_raw % 1000
        gtype = self._WKB_TYPE_NAMES.get(base_type)
        if gtype is None:
            return None, None
        dim = 3 if has_z else 2
        pos = 5

        def read_point():
            nonlocal pos
            vals = struct.unpack(byte_order + "d" * dim, wkb[pos : pos + 8 * dim])
            pos += 8 * dim
            return list(vals)

        def _check_count(count: int, min_bytes_per_item: int) -> None:
            # Güvenlik: WKB'deki count alanları 32-bit'tir ve kötü niyetli/
            # bozuk bir blobda gerçek veriyle uyumsuz devasa bir değer
            # beyan edebilir; bu durumda `range(count)` üzerinde dönmek
            # (veya bir liste/format string kurmak) gerçek veri yokken bile
            # bellek/CPU tüketen bir DoS'a yol açar. Kalan byte'a göre
            # mantıksal olarak imkansızsa hemen anlamlı bir hata fırlatılır.
            remaining = len(wkb) - pos
            if count < 0 or count > remaining // max(min_bytes_per_item, 1):
                raise GISParseError(
                    f"GPKG WKB: geçersiz/aşırı büyük count beyanı: {count} "
                    f"(kalan veri yalnızca {remaining} byte)"
                )

        def read_ring():
            nonlocal pos
            (count,) = struct.unpack(byte_order + "I", wkb[pos : pos + 4])
            pos += 4
            _check_count(count, 8 * dim)
            return [read_point() for _ in range(count)]

        if base_type == 1:  # Point
            return gtype, read_point()
        if base_type == 2:  # LineString
            return gtype, read_ring()
        if base_type == 3:  # Polygon
            (num_rings,) = struct.unpack(byte_order + "I", wkb[pos : pos + 4])
            pos += 4
            _check_count(num_rings, 4)  # her ring en az kendi count'u (4 byte) kadar yer kaplar
            return gtype, [read_ring() for _ in range(num_rings)]
        if base_type == 4:  # MultiPoint
            (count,) = struct.unpack(byte_order + "I", wkb[pos : pos + 4])
            pos += 4
            _check_count(count, 5 + 8 * dim)
            pts = []
            for _ in range(count):
                pos += 5  # nested geometri byte-order + type
                pts.append(read_point())
            return gtype, pts
        if base_type == 5:  # MultiLineString
            (count,) = struct.unpack(byte_order + "I", wkb[pos : pos + 4])
            pos += 4
            _check_count(count, 5 + 4)
            lines = []
            for _ in range(count):
                pos += 5
                lines.append(read_ring())
            return gtype, lines
        if base_type == 6:  # MultiPolygon
            (count,) = struct.unpack(byte_order + "I", wkb[pos : pos + 4])
            pos += 4
            _check_count(count, 5 + 4)
            polys = []
            for _ in range(count):
                pos += 5
                (num_rings,) = struct.unpack(byte_order + "I", wkb[pos : pos + 4])
                pos += 4
                _check_count(num_rings, 4)
                polys.append([read_ring() for _ in range(num_rings)])
            return gtype, polys
        return None, None


class DXFParser(FormatParser):
    """AutoCAD DXF (ASCII, grup-kod formatı). LINE, LWPOLYLINE, POLYLINE+VERTEX,
    POINT ve 3DFACE entity'lerini `ENTITIES` bölümünden okur. Binary DXF
    (DXB) desteklenmez."""

    def parse_file(self, path: str) -> GeoFeatureCollection:
        try:
            return self._parse_file_impl(path)
        except (ValueError, IndexError) as e:
            _reraise_as_parse_error(e, f"Bozuk DXF ({path})")

    def _parse_file_impl(self, path: str) -> GeoFeatureCollection:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\r\n") for ln in fh]
        pairs = list(zip(lines[0::2], lines[1::2]))
        codes = [(int(c.strip()), v) for c, v in pairs if c.strip().lstrip("-").isdigit()]

        features: list[GeoFeature] = []
        i = 0
        n = len(codes)
        in_entities = False
        while i < n:
            code, value = codes[i]
            if code == 2 and value.strip().upper() == "ENTITIES":
                in_entities = True
                i += 1
                continue
            if code == 0 and in_entities and value.strip().upper() == "ENDSEC":
                in_entities = False
                i += 1
                continue
            if in_entities and code == 0:
                etype = value.strip().upper()
                entity_codes, i = self._collect_entity(codes, i + 1)
                feat = self._build_feature(etype, entity_codes)
                if feat is not None:
                    features.append(feat)
                continue
            i += 1
        return GeoFeatureCollection(features, crs="EPSG:4326")

    @staticmethod
    def _collect_entity(codes: list[tuple[int, str]], start: int):
        j = start
        n = len(codes)
        collected = []
        while j < n and codes[j][0] != 0:
            collected.append(codes[j])
            j += 1
        return collected, j

    def _build_feature(self, etype: str, codes: list[tuple[int, str]]):
        props = {"layer": None}
        for code, value in codes:
            if code == 8:
                props["layer"] = value

        if etype == "POINT":
            x = y = None
            for code, value in codes:
                if code == 10:
                    x = float(value)
                elif code == 20:
                    y = float(value)
            if x is not None and y is not None:
                return GeoFeature("Point", [x, y], props)
            return None

        if etype == "LINE":
            x1 = y1 = x2 = y2 = None
            for code, value in codes:
                if code == 10:
                    x1 = float(value)
                elif code == 20:
                    y1 = float(value)
                elif code == 11:
                    x2 = float(value)
                elif code == 21:
                    y2 = float(value)
            if None not in (x1, y1, x2, y2):
                return GeoFeature("LineString", [[x1, y1], [x2, y2]], props)
            return None

        if etype in ("LWPOLYLINE", "POLYLINE"):
            points: list[list[float]] = []
            closed = False
            cur_x = None
            for code, value in codes:
                if code == 70:
                    try:
                        closed = bool(int(value) & 1)
                    except ValueError:
                        pass
                elif code == 10:
                    cur_x = float(value)
                elif code == 20 and cur_x is not None:
                    points.append([cur_x, float(value)])
                    cur_x = None
            if not points:
                return None
            if closed and points[0] != points[-1]:
                points.append(points[0])
            return GeoFeature(
                "Polygon" if closed else "LineString", [points] if closed else points, props
            )

        if etype == "3DFACE":
            # 3DFACE dört köşe (10/20/30, 11/21/31, 12/22/32, 13/23/33)
            corners: dict[int, list[float]] = {
                0: [0.0, 0.0, 0.0],
                1: [0.0, 0.0, 0.0],
                2: [0.0, 0.0, 0.0],
                3: [0.0, 0.0, 0.0],
            }
            code_to_corner = {
                10: (0, 0),
                20: (0, 1),
                30: (0, 2),
                11: (1, 0),
                21: (1, 1),
                31: (1, 2),
                12: (2, 0),
                22: (2, 1),
                32: (2, 2),
                13: (3, 0),
                23: (3, 1),
                33: (3, 2),
            }
            for code, value in codes:
                if code in code_to_corner:
                    ci, axis = code_to_corner[code]
                    corners[ci][axis] = float(value)
            ring = [corners[0], corners[1], corners[2], corners[3], corners[0]]
            return GeoFeature("Polygon", [ring], props)

        return None


class Mesh3DParser(FormatParser):
    """OBJ / STL / GLTF için 3D mesh içe aktarma.

    Çıktı `GeoFeature(geometry_type="Mesh3D", coordinates={"vertices": [[x,y,z],...],
    "faces": [[i0,i1,i2], ...]})` biçimindedir (üçgenlere ayrıştırılmış).
    FBX ve GLB (binary glTF), ikili/tescilli/karmaşık chunk format nedeniyle
    kapsam dışıdır; `parse_file` bu formatlar için açık bir hata verir.
    """

    _SUPPORTED = {"OBJ", "STL", "GLTF"}

    def __init__(self, fmt: str):
        self.fmt = fmt.upper()

    def parse_file(self, path: str) -> GeoFeatureCollection:
        if self.fmt not in self._SUPPORTED:
            raise NotImplementedError(
                f"Mesh3DParser[{self.fmt}]: bu format (FBX/GLB gibi ikili/tescilli "
                "olanlar) bilinçli olarak kapsam dışı bırakıldı - bkz. sınıf docstring'i."
            )
        try:
            if self.fmt == "OBJ":
                return self._parse_obj(path)
            if self.fmt == "STL":
                return self._parse_stl(path)
            return self._parse_gltf(path)
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            struct.error,
            json.JSONDecodeError,
            OSError,
        ) as e:
            _reraise_as_parse_error(e, f"Bozuk Mesh3D/{self.fmt} ({path})")

    def _parse_obj(self, path: str) -> GeoFeatureCollection:
        vertices: list[list[float]] = []
        faces: list[list[int]] = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                tag = parts[0]
                if tag == "v" and len(parts) >= 4:
                    vertices.append([float(parts[1]), float(parts[2]), float(parts[3])])
                elif tag == "f" and len(parts) >= 4:
                    idxs = [int(p.split("/")[0]) for p in parts[1:]]
                    idxs = [(i - 1) if i > 0 else (len(vertices) + i) for i in idxs]
                    # n-gon -> fan triangulation
                    for k in range(1, len(idxs) - 1):
                        faces.append([idxs[0], idxs[k], idxs[k + 1]])
        feature = GeoFeature("Mesh3D", {"vertices": vertices, "faces": faces}, {"format": "OBJ"})
        return GeoFeatureCollection([feature], crs="LOCAL")

    def _parse_stl(self, path: str) -> GeoFeatureCollection:
        with open(path, "rb") as fh:
            head = fh.read(5)
            fh.seek(0)
            if head == b"solid":
                return self._parse_stl_ascii(fh)
            return self._parse_stl_binary(fh)

    def _parse_stl_ascii(self, fh) -> GeoFeatureCollection:
        text = fh.read().decode("utf-8", errors="replace")
        vertices: list[list[float]] = []
        faces: list[list[int]] = []
        current: list[list[float]] = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("vertex"):
                _, x, y, z = line.split()
                current.append([float(x), float(y), float(z)])
            elif line.startswith("endfacet"):
                if len(current) == 3:
                    base = len(vertices)
                    vertices.extend(current)
                    faces.append([base, base + 1, base + 2])
                current = []
        feature = GeoFeature(
            "Mesh3D", {"vertices": vertices, "faces": faces}, {"format": "STL-ASCII"}
        )
        return GeoFeatureCollection([feature], crs="LOCAL")

    def _parse_stl_binary(self, fh) -> GeoFeatureCollection:
        fh.read(80)  # header
        (num_triangles,) = struct.unpack("<I", fh.read(4))
        vertices: list[list[float]] = []
        faces: list[list[int]] = []
        for _ in range(num_triangles):
            data = fh.read(50)
            if len(data) < 50:
                break
            # 3x normal float + 3x(3x vertex float) + 2 byte attr
            floats = struct.unpack("<12f", data[0:48])
            tri = [list(floats[3:6]), list(floats[6:9]), list(floats[9:12])]
            base = len(vertices)
            vertices.extend(tri)
            faces.append([base, base + 1, base + 2])
        feature = GeoFeature(
            "Mesh3D", {"vertices": vertices, "faces": faces}, {"format": "STL-BINARY"}
        )
        return GeoFeatureCollection([feature], crs="LOCAL")

    def _parse_gltf(self, path: str) -> GeoFeatureCollection:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)

        buffers_raw: list[bytes] = []
        base_dir = Path(path).parent
        for buf in doc.get("buffers", []):
            uri = buf.get("uri", "")
            if uri.startswith("data:"):
                b64 = uri.split(",", 1)[1]
                buffers_raw.append(base64.b64decode(b64))
            else:
                with open(base_dir / uri, "rb") as bfh:
                    buffers_raw.append(bfh.read())

        buffer_views = doc.get("bufferViews", [])
        accessors = doc.get("accessors", [])

        comp_fmt = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
        comp_size = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
        type_count = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

        def read_accessor(acc_idx: int) -> list:
            acc = accessors[acc_idx]
            bv = buffer_views[acc["bufferView"]]
            raw = buffers_raw[bv.get("buffer", 0)]
            offset = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
            comp_type = acc["componentType"]
            count = acc["count"]
            n_comp = type_count.get(acc["type"], 1)
            fmt = "<" + comp_fmt[comp_type] * n_comp
            stride = comp_size[comp_type] * n_comp
            out = []
            for i in range(count):
                chunk = raw[offset + i * stride : offset + i * stride + stride]
                vals = struct.unpack(fmt, chunk)
                out.append(list(vals) if n_comp > 1 else vals[0])
            return out

        all_vertices: list[list[float]] = []
        all_faces: list[list[int]] = []
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                attrs = prim.get("attributes", {})
                if "POSITION" not in attrs:
                    continue
                positions = read_accessor(attrs["POSITION"])
                base = len(all_vertices)
                all_vertices.extend(positions)
                if "indices" in prim:
                    idxs = read_accessor(prim["indices"])
                    for k in range(0, len(idxs) - 2, 3):
                        all_faces.append([base + idxs[k], base + idxs[k + 1], base + idxs[k + 2]])
                else:
                    for k in range(0, len(positions) - 2, 3):
                        all_faces.append([base + k, base + k + 1, base + k + 2])

        feature = GeoFeature(
            "Mesh3D", {"vertices": all_vertices, "faces": all_faces}, {"format": "GLTF"}
        )
        return GeoFeatureCollection([feature], crs="LOCAL")


class HeightmapParser(FormatParser):
    """DEM / DSM / generic HeightMap okuyucu.

    Desteklenen biçimler:
      * ESRI ASCII Grid (`.asc`/`.grd`, tam OGC/ESRI spesifikasyonu: ncols,
        nrows, xllcorner/xllcenter, yllcorner/yllcenter, cellsize, NODATA_value
        + satır-major yükseklik matrisi).
      * Ham binary raster: aynı isimli bir `.json` sidecar header
        (`{"width":..,"height":..,"resolution_m":..,"origin":[lon,lat]}`) +
        `.bin` içinde satır-major float32 yükseklik dizisi.

      * GeoTIFF / TIFF (`.tif`/`.tiff`, Roadmap V3 - D14, LZW: Roadmap V7):
        baseline TIFF 6.0 IFD ayrıştırıcısı + stdlib `zlib` ile
        **DEFLATE/Adobe-Deflate** (Compression=8/32946) VE saf-Python
        **LZW** (Compression=5, bkz. `tiff_lzw.py` - harici bağımlılık
        gerektirmez) sıkıştırma çözümü, isteğe bağlı yatay (horizontal,
        Predictor=2) fark-kodlama geri alma dahil. Tanınmayan diğer
        sıkıştırma kodları için açık `UnsupportedFormatError` fırlatılır
        (sessizce yanlış sonuç üretmez).

    Çıktı: `GeoFeature(geometry_type="Heightmap", coordinates={"width":,
    "height":, "resolution_m":, "origin":[x,y], "elevations": [[...]]})`.
    """

    def parse_file(self, path: str) -> GeoFeatureCollection:
        suffix = Path(path).suffix.lower()
        if suffix not in (".asc", ".grd", ".bin", ".tif", ".tiff"):
            raise NotImplementedError(
                f"HeightmapParser: '{suffix}' desteklenmiyor. Desteklenenler: "
                ".asc/.grd (ESRI ASCII Grid), .bin (+ .json header), "
                ".tif/.tiff (baseline GeoTIFF, DEFLATE)."
            )
        try:
            if suffix in (".asc", ".grd"):
                return self._parse_esri_ascii(path)
            if suffix in (".tif", ".tiff"):
                return self._parse_geotiff(path)
            return self._parse_raw_binary(path)
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            struct.error,
            json.JSONDecodeError,
            OSError,
            zlib.error,
        ) as e:
            _reraise_as_parse_error(e, f"Bozuk Heightmap ({path})")

    def _parse_esri_ascii(self, path: str) -> GeoFeatureCollection:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()

        header: dict[str, float] = {}
        data_start = 0
        keys = {
            "ncols",
            "nrows",
            "xllcorner",
            "yllcorner",
            "xllcenter",
            "yllcenter",
            "cellsize",
            "nodata_value",
        }
        for i, line in enumerate(lines):
            parts = line.strip().split()
            if len(parts) == 2 and parts[0].lower() in keys:
                header[parts[0].lower()] = float(parts[1])
                data_start = i + 1
            else:
                break

        ncols = int(header["ncols"])
        nrows = int(header["nrows"])
        cellsize = header.get("cellsize", 1.0)
        nodata = header.get("nodata_value", -9999.0)
        origin_x = header.get("xllcorner", header.get("xllcenter", 0.0))
        origin_y = header.get("yllcorner", header.get("yllcenter", 0.0))

        elevations: list[list[float]] = []
        for line in lines[data_start : data_start + nrows]:
            values = [float(v) for v in line.strip().split()]
            values = [None if v == nodata else v for v in values]
            elevations.append(values[:ncols])

        coords = {
            "width": ncols,
            "height": nrows,
            "resolution_m": cellsize,
            "origin": [origin_x, origin_y],
            "elevations": elevations,
        }
        feature = GeoFeature("Heightmap", coords, {"format": "ESRI-ASCII-GRID"})
        return GeoFeatureCollection([feature], crs="EPSG:4326")

    # ------------------------------------------------------------------ #
    # Roadmap V3 - D14: baseline TIFF 6.0 IFD ayrıştırıcısı + DEFLATE çözücü
    # ------------------------------------------------------------------ #
    _TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

    def _parse_geotiff(self, path: str) -> GeoFeatureCollection:
        with open(path, "rb") as fh:
            data = fh.read()

        if len(data) < 8:
            raise GISParseError("TIFF dosyası çok kısa (8 bayt header eksik).")

        byte_order = data[0:2]
        if byte_order == b"II":
            endian = "<"
        elif byte_order == b"MM":
            endian = ">"
        else:
            raise GISParseError(f"Geçersiz TIFF byte-order işareti: {byte_order!r}")

        magic = struct.unpack_from(endian + "H", data, 2)[0]
        if magic != 42:
            raise GISParseError(f"Geçersiz TIFF magic sayısı: {magic} (42 bekleniyordu)")

        ifd_offset = struct.unpack_from(endian + "I", data, 4)[0]
        tags = self._read_ifd(data, ifd_offset, endian)

        width = tags.get(256)
        height = tags.get(257)
        if width is None or height is None:
            raise GISParseError("TIFF: ImageWidth/ImageLength etiketi eksik.")

        bits_per_sample = (
            tags.get(258, [32])[0] if isinstance(tags.get(258), list) else tags.get(258, 32)
        )
        compression = tags.get(259, 1)
        samples_per_pixel = tags.get(277, 1)
        rows_per_strip = tags.get(278, height)
        sample_format = tags.get(339, [1])
        sample_format = sample_format[0] if isinstance(sample_format, list) else sample_format
        predictor = tags.get(317, 1)

        strip_offsets = tags.get(273)
        strip_byte_counts = tags.get(279)
        if strip_offsets is None or strip_byte_counts is None:
            raise GISParseError(
                "TIFF: StripOffsets/StripByteCounts etiketi eksik (tiled TIFF desteklenmiyor)."
            )
        if not isinstance(strip_offsets, list):
            strip_offsets = [strip_offsets]
        if not isinstance(strip_byte_counts, list):
            strip_byte_counts = [strip_byte_counts]

        if compression not in (1, 5, 8, 32946):
            raise UnsupportedFormatError(f"TIFF: desteklenmeyen sıkıştırma kodu {compression}.")

        raw_strips = []
        for off, cnt in zip(strip_offsets, strip_byte_counts):
            chunk = data[off : off + cnt]
            if compression in (8, 32946):
                chunk = zlib.decompress(chunk)
            elif compression == 5:
                # Roadmap V7 - LZW GeoTIFF desteği: TIFF 6.0 §13 LZW
                # varyantı (early-change), stdlib-only saf-Python çözücü.
                # Her strip kendi bağımsız LZW akışıdır (kod tablosu
                # CLEAR koduyla strip başında sıfırlanır).
                try:
                    chunk = _tiff_lzw.lzw_decode(chunk)
                except _tiff_lzw.LZWDecodeError as exc:
                    raise GISParseError(f"TIFF: LZW çözme hatası: {exc}") from exc
            raw_strips.append(chunk)
        raw = b"".join(raw_strips)

        bytes_per_sample = bits_per_sample // 8
        if bytes_per_sample not in (1, 2, 4, 8):
            raise UnsupportedFormatError(f"TIFF: desteklenmeyen BitsPerSample={bits_per_sample}.")

        # Predictor=2 (horizontal differencing) geri alma: her satırda
        # değerler öncekine göre fark olarak kodlanmıştır, kümülatif
        # toplamla orijinale döndürülür.
        row_stride = width * samples_per_pixel * bytes_per_sample
        if predictor == 2:
            raw = bytearray(raw)
            fmt_map = {1: "B", 2: "H", 4: "I"}
            if bytes_per_sample in fmt_map:
                code = fmt_map[bytes_per_sample]
                per_row = width * samples_per_pixel
                for r in range(height):
                    start = r * row_stride
                    row_vals = list(struct.unpack_from(f"{endian}{per_row}{code}", raw, start))
                    for i in range(samples_per_pixel, per_row):
                        row_vals[i] = (row_vals[i] + row_vals[i - samples_per_pixel]) & (
                            (1 << bits_per_sample) - 1
                        )
                    struct.pack_into(f"{endian}{per_row}{code}", raw, start, *row_vals)
            raw = bytes(raw)

        elevations = self._decode_samples(
            raw,
            width,
            height,
            samples_per_pixel,
            bytes_per_sample,
            sample_format,
            endian,
        )

        # GeoTIFF geo-referans etiketleri (opsiyonel): ModelPixelScaleTag
        # (33550) + ModelTiepointTag (33922) -> origin + resolution.
        pixel_scale = tags.get(33550)
        tiepoint = tags.get(33922)
        resolution_m = pixel_scale[0] if pixel_scale else 1.0
        origin_x = tiepoint[3] if tiepoint and len(tiepoint) >= 4 else 0.0
        origin_y = tiepoint[4] if tiepoint and len(tiepoint) >= 5 else 0.0

        coords = {
            "width": width,
            "height": height,
            "resolution_m": resolution_m,
            "origin": [origin_x, origin_y],
            "elevations": elevations,
        }
        feature = GeoFeature(
            "Heightmap",
            coords,
            {
                "format": "GEOTIFF",
                "compression": compression,
                "predictor": predictor,
            },
        )
        return GeoFeatureCollection([feature], crs="EPSG:4326")

    def _read_ifd(self, data: bytes, offset: int, endian: str) -> dict[int, Any]:
        entry_count = struct.unpack_from(endian + "H", data, offset)[0]
        tags: dict[int, Any] = {}
        entry_base = offset + 2
        for i in range(entry_count):
            entry_off = entry_base + i * 12
            tag_id, field_type, count = struct.unpack_from(endian + "HHI", data, entry_off)
            value_offset_bytes = data[entry_off + 8 : entry_off + 12]
            size = self._TIFF_TYPE_SIZES.get(field_type, 1)
            total_size = size * count

            if total_size <= 4:
                values = self._unpack_values(value_offset_bytes, field_type, count, endian)
            else:
                value_offset = struct.unpack_from(endian + "I", value_offset_bytes, 0)[0]
                values = self._unpack_values(
                    data[value_offset : value_offset + total_size], field_type, count, endian
                )

            tags[tag_id] = values[0] if count == 1 and field_type not in (2,) else values
        return tags

    @staticmethod
    def _unpack_values(buf: bytes, field_type: int, count: int, endian: str) -> list:
        type_codes = {1: "B", 3: "H", 4: "I", 5: "II", 11: "f", 12: "d", 6: "b", 8: "h", 9: "i"}
        if field_type == 2:  # ASCII
            return [buf.rstrip(b"\x00").decode("ascii", errors="replace")]
        if field_type == 5:  # RATIONAL: count pairs of (num, den)
            out = []
            for i in range(count):
                num, den = struct.unpack_from(endian + "II", buf, i * 8)
                out.append(num / den if den else 0.0)
            return out
        code = type_codes.get(field_type, "B")
        size = struct.calcsize(endian + code)
        return [struct.unpack_from(endian + code, buf, i * size)[0] for i in range(count)]

    @staticmethod
    def _decode_samples(
        raw: bytes,
        width: int,
        height: int,
        samples_per_pixel: int,
        bytes_per_sample: int,
        sample_format: int,
        endian: str,
    ) -> list[list[float]]:
        if sample_format == 3:  # IEEE float
            code = {4: "f", 8: "d"}.get(bytes_per_sample)
            if code is None:
                raise UnsupportedFormatError(
                    f"Desteklenmeyen float BitsPerSample={bytes_per_sample * 8}"
                )
        else:  # unsigned/signed integer
            code = {1: "B", 2: "H", 4: "I"}.get(bytes_per_sample)
            if sample_format == 2:  # signed
                code = {1: "b", 2: "h", 4: "i"}.get(bytes_per_sample)
            if code is None:
                raise UnsupportedFormatError(
                    f"Desteklenmeyen integer BitsPerSample={bytes_per_sample * 8}"
                )

        per_row = width * samples_per_pixel
        row_stride = per_row * bytes_per_sample
        elevations: list[list[float]] = []
        for r in range(height):
            start = r * row_stride
            row_vals = struct.unpack_from(f"{endian}{per_row}{code}", raw, start)
            if samples_per_pixel == 1:
                elevations.append([float(v) for v in row_vals])
            else:
                elevations.append([float(row_vals[c * samples_per_pixel]) for c in range(width)])
        return elevations

    def _parse_raw_binary(self, path: str) -> GeoFeatureCollection:
        json_path = str(Path(path).with_suffix(".json"))
        if not Path(json_path).exists():
            raise ValueError(
                f"Ham binary heightmap için '{json_path}' sidecar header dosyası bulunamadı."
            )
        with open(json_path, encoding="utf-8") as fh:
            meta = json.load(fh)

        width = int(meta["width"])
        height = int(meta["height"])
        resolution_m = float(meta.get("resolution_m", 1.0))
        origin = meta.get("origin", [0.0, 0.0])

        with open(path, "rb") as fh:
            raw = fh.read()
        expected = width * height * 4
        if len(raw) < expected:
            raise ValueError(
                f"Binary heightmap boyutu header ile uyuşmuyor: {len(raw)} < {expected} byte."
            )
        flat = struct.unpack(f"<{width * height}f", raw[:expected])
        elevations = [list(flat[r * width : (r + 1) * width]) for r in range(height)]

        coords = {
            "width": width,
            "height": height,
            "resolution_m": resolution_m,
            "origin": origin,
            "elevations": elevations,
        }
        feature = GeoFeature("Heightmap", coords, {"format": "RAW-BINARY-F32"})
        return GeoFeatureCollection([feature], crs=meta.get("crs", "EPSG:4326"))
