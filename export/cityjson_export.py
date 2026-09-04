"""
Export — CityJSON
===================

ROADMAP_V4 — Track E / Faz E5 (CityJSON kısmı). D7 ile 3D Tiles (görsel
akış/LOD) ve IFC (BIM, tek-bina) eklenmişti ama şehir-ölçeği coğrafi-3D
paylaşım standardı olan **CityJSON** (CityGML'in modern, JSON-tabanlı,
OGC onaylı hafif alternatifi) yoktu. Bu modül, CityJSON 1.1 şemasına
uyumlu, tamamen stdlib `json` ile bir yazıcı ekler.

Girdi veri modeli (`CityBuilding`/`CityModel`) bilinçli olarak
`building_reconstruction`'ın somut sınıflarına (`Footprint`, `Room`, ...)
sıkı bağımlı değildir — yalnızca bir taban poligonu (`core_engine.
geometry_engine.Polygon`, `Point2D` listesi) + yükseklik(ler) + opsiyonel
gerçek çatı mesh'i (`mesh_engine.Mesh3D`) alır. Bu, roadmap'in "her faz bir
önceki fazla veri sözleşmesiyle bağlanır, sıkı coupling yok" mimari
ilkesiyle tutarlıdır — `building_reconstruction` çıktısından bir
`CityBuilding` üretmek çağıran tarafın sorumluluğundadır (ileride bir
adaptör eklenebilir, kapsam dışı bırakıldı).

Desteklenen LOD:
    - **LOD1** (her zaman üretilir): taban poligonunun düz yükseklikte
      ekstrüzyonu — tek bir `Solid` (taban + çatı + duvarlar).
    - **LOD2** (opsiyonel, `roof_mesh` verilirse): saçak yüksekliğine kadar
      düz duvar ekstrüzyonu + gerçek çatı mesh'inin üçgenleri ayrı
      `RoofSurface` yüzeyleri olarak.

Kapsam dışı (bilinçli, roadmap E5 metniyle tutarlı): LOD3/LOD4 (pencere/
kapı detay seviyesi — `building_reconstruction.building_elements`'in tam
geometrik detayına iner, ayrı bir genişletme), CityJSONSeq (akış formatı),
Appearance/tekstür bloğu.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core_engine.geometry_engine import Polygon
from ..mesh_engine import Mesh3D
from .geometry_3d import ExportResult

CITYJSON_VERSION = "1.1"


class CityModelValidationError(ValueError):
    """Bir `CityBuilding`/`CityModel` geçersiz geometri içeriyorsa (ör.
    3 noktadan az taban poligonu, sıfır/negatif yükseklik) fırlatılır —
    sessizce bozuk CityJSON/CityGML üretmek yerine."""


@dataclass(slots=True)
class CityBuilding:
    """Tek bir binanın şehir-ölçeği (semantik) veri modeli.

    `footprint` CCW/CW fark etmeksizin kabul edilir (dış içe alan
    hesaplarına göre normalize edilir); `ground_z`, taban poligonunun
    deniz seviyesi/yerel referans üstündeki yüksekliği; `height`, LOD1
    için taban-üstü toplam bina yüksekliği (m). `roof_mesh` verilirse
    (LOD2), `eave_height` duvarların nereye kadar düz ekstrüde edileceğini
    belirtir (verilmezse `height` ile aynı kabul edilir).
    """

    building_id: str
    footprint: Polygon
    height: float
    ground_z: float = 0.0
    roof_mesh: Mesh3D | None = None
    eave_height: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    year_of_construction: int | None = None
    function: str | None = None  # CityGML/CityJSON "function" kodu (serbest metin)

    def __post_init__(self) -> None:
        ring = self.footprint.closed_ring()
        # kapalı halka -1 = benzersiz köşe sayısı
        if len(ring) - 1 < 3:
            raise CityModelValidationError(
                f"Bina '{self.building_id}': taban poligonu en az 3 köşeye sahip olmalı."
            )
        if self.height <= 0:
            raise CityModelValidationError(
                f"Bina '{self.building_id}': height pozitif olmalı (verilen: {self.height})."
            )

    def resolved_eave_height(self) -> float:
        return self.eave_height if self.eave_height is not None else self.height


@dataclass(slots=True)
class CityModel:
    """Bir dizi `CityBuilding`'i tutan üst-seviye şehir modeli konteyneri."""

    buildings: list[CityBuilding] = field(default_factory=list)
    crs_name: str | None = None  # ör. "EPSG:32635" (UTM 35N) - opsiyonel

    def add(self, building: CityBuilding) -> None:
        self.buildings.append(building)


# ============================================================================ #
# CityJSON
# ============================================================================ #


class CityJSONExporter:
    """CityJSON 1.1 (https://www.cityjson.org/specs/1.1.1/) yazıcı."""

    @classmethod
    def export(cls, model: CityModel, path: str) -> ExportResult:
        if not model.buildings:
            raise CityModelValidationError("CityModel en az bir bina içermeli.")

        vertices: list[tuple[float, float, float]] = []
        vertex_index: dict[tuple[float, float, float], int] = {}

        def _vidx(x: float, y: float, z: float) -> int:
            key = (round(x, 6), round(y, 6), round(z, 6))
            idx = vertex_index.get(key)
            if idx is None:
                idx = len(vertices)
                vertex_index[key] = idx
                vertices.append(key)
            return idx

        city_objects: dict[str, Any] = {}
        triangle_count = 0

        for building in model.buildings:
            ring = building.footprint.closed_ring()[:-1]  # benzersiz köşeler
            base_z = building.ground_z
            top_z = building.ground_z + building.height

            ground_ring = [_vidx(p.x, p.y, base_z) for p in ring]
            roof_ring = [_vidx(p.x, p.y, top_z) for p in ring]

            boundaries: list[Any] = []
            semantics_values: list[int] = []
            surfaces: list[dict[str, Any]] = [
                {"type": "GroundSurface"},
                {"type": "RoofSurface"},
                {"type": "WallSurface"},
            ]

            # Ground (taş poligonu ters yönde - dışa bakan normal aşağı)
            boundaries.append([list(reversed(ground_ring))])
            semantics_values.append(0)
            triangle_count += max(0, len(ground_ring) - 2)

            has_lod2 = building.roof_mesh is not None
            if not has_lod2:
                # LOD1: düz çatı (taban poligonunun tepe yüksekliğindeki kopyası)
                boundaries.append([roof_ring])
                semantics_values.append(1)
                triangle_count += max(0, len(roof_ring) - 2)
                wall_top_ring = roof_ring
            else:
                eave_z = building.ground_z + building.resolved_eave_height()
                wall_top_ring = [_vidx(p.x, p.y, eave_z) for p in ring]
                # LOD2 gerçek çatı mesh'i - her üçgen ayrı bir RoofSurface yüzeyi
                for tri in building.roof_mesh.triangles:
                    v0, v1, v2 = (building.roof_mesh.vertices[i] for i in tri)
                    face = [
                        _vidx(v0.x, v0.y, v0.z),
                        _vidx(v1.x, v1.y, v1.z),
                        _vidx(v2.x, v2.y, v2.z),
                    ]
                    boundaries.append([face])
                    semantics_values.append(1)
                    triangle_count += 1

            # Duvarlar (her kenar ayrı bir WallSurface yüzeyi)
            n = len(ring)
            for i in range(n):
                a, b = i, (i + 1) % n
                wall_face = [
                    ground_ring[a],
                    ground_ring[b],
                    wall_top_ring[b],
                    wall_top_ring[a],
                ]
                boundaries.append([wall_face])
                semantics_values.append(2)
                triangle_count += 2

            geometry: dict[str, Any] = {
                "type": "Solid",
                "lod": "2" if has_lod2 else "1",
                "boundaries": [boundaries],
                "semantics": {
                    "surfaces": surfaces,
                    "values": [semantics_values],
                },
            }

            attrs: dict[str, Any] = dict(building.attributes)
            if building.year_of_construction is not None:
                attrs["yearOfConstruction"] = building.year_of_construction
            if building.function is not None:
                attrs["function"] = building.function
            attrs["measuredHeight"] = building.height

            city_objects[building.building_id] = {
                "type": "Building",
                "attributes": attrs,
                "geometry": [geometry],
            }

        cityjson: dict[str, Any] = {
            "type": "CityJSON",
            "version": CITYJSON_VERSION,
            "CityObjects": city_objects,
            "vertices": [list(v) for v in vertices],
        }
        if model.crs_name:
            cityjson["metadata"] = {
                "referenceSystem": f"https://www.opengis.net/def/crs/{model.crs_name.replace(':', '/')}"
            }

        text = json.dumps(cityjson, indent=None, separators=(",", ":"))
        path_obj = Path(path)
        path_obj.write_text(text, encoding="utf-8")

        return ExportResult(
            path=str(path_obj),
            format="cityjson",
            bytes_written=len(text.encode("utf-8")),
            vertex_count=len(vertices),
            triangle_count=triangle_count,
        )

    @staticmethod
    def validate_structure(cityjson: dict[str, Any]) -> list[str]:
        """Tam bir JSON-şema doğrulaması değil (harici bağımlılık
        gerektirir) ama CityJSON 1.1'in **temel yapısal zorunluluklarını**
        kontrol eder - roadmap kabul kriteri ("resmi CityJSON şemasına ...
        temel yapısal doğrulama ile uyar") için yeterli. Hata mesajı
        listesi döner (boşsa geçerli)."""
        errors: list[str] = []
        if cityjson.get("type") != "CityJSON":
            errors.append("'type' alanı 'CityJSON' olmalı.")
        if "version" not in cityjson:
            errors.append("'version' alanı zorunlu.")
        if "CityObjects" not in cityjson or not isinstance(cityjson["CityObjects"], dict):
            errors.append("'CityObjects' bir sözlük olmalı.")
        if "vertices" not in cityjson or not isinstance(cityjson["vertices"], list):
            errors.append("'vertices' bir liste olmalı.")
        else:
            for v in cityjson["vertices"]:
                if not (isinstance(v, list) and len(v) == 3):
                    errors.append("Her 'vertices' elemanı [x, y, z] olmalı.")
                    break
        for obj_id, obj in cityjson.get("CityObjects", {}).items():
            if "type" not in obj:
                errors.append(f"CityObject '{obj_id}': 'type' alanı zorunlu.")
            if "geometry" in obj and not isinstance(obj["geometry"], list):
                errors.append(f"CityObject '{obj_id}': 'geometry' bir liste olmalı.")
        return errors
