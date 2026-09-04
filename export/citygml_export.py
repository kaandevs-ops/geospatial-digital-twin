"""
Export — CityGML
==================

ROADMAP_V4 — Track E / Faz E5 (CityGML kısmı). `cityjson_export.py` ile
aynı `CityModel`/`CityBuilding` veri modelini girdi alan, OGC CityGML 2.0
(`bldg:Building`/`bldg:BoundarySurface`) XML/GML üreten, stdlib
`xml.etree.ElementTree`-only bir yazıcı.

CityGML 2.0 seçildi (3.0 henüz endüstride CityJSON kadar yaygın
desteklenmiyor, 2.0 hâlâ fiili şehir-veri-paylaşım standardı - roadmap'in
"büyük ölçekli akıllı şehir veri paylaşımı için endüstri standardı"
gerekçesiyle tutarlı). Üretilen `bldg:Building` her zaman LOD1
`lod1Solid` içerir; `roof_mesh` verilmişse ayrıca LOD2 `lod2Solid` +
`boundedBy` altında `bldg:WallSurface`/`bldg:RoofSurface`/
`bldg:GroundSurface` semantik yüzeyleri eklenir.

Kapsam dışı (bilinçli): LOD3/4, `app:Appearance` (tekstür), CityGML'in
ADE (Application Domain Extension) mekanizması.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .cityjson_export import CityModel, CityModelValidationError
from .geometry_3d import ExportResult

_NS = {
    "core": "http://www.opengis.net/citygml/2.0",
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "gml": "http://www.opengis.net/gml",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

_SCHEMA_LOCATION = (
    "http://www.opengis.net/citygml/2.0 http://schemas.opengis.net/citygml/2.0/cityGMLBase.xsd "
    "http://www.opengis.net/citygml/building/2.0 "
    "http://schemas.opengis.net/citygml/building/2.0/building.xsd"
)


def _pos_list(coords: list[tuple[float, float, float]]) -> str:
    return " ".join(f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in coords)


def _ring_element(parent: ET.Element, tag: str, gml_id: str, ring: list[tuple[float, float, float]]) -> None:
    """`gml:Polygon` içine bir `gml:exterior`/`gml:LinearRing` ekler.
    `ring` kapalı olmalı (ilk == son)."""
    polygon = ET.SubElement(parent, f"{{{_NS['gml']}}}Polygon", {f"{{{_NS['gml']}}}id": gml_id})
    exterior = ET.SubElement(polygon, f"{{{_NS['gml']}}}exterior")
    linear_ring = ET.SubElement(exterior, f"{{{_NS['gml']}}}LinearRing")
    pos_list_el = ET.SubElement(linear_ring, f"{{{_NS['gml']}}}posList")
    pos_list_el.text = _pos_list(ring)


class CityGMLExporter:
    """CityGML 2.0 `bldg:Building` yazıcı - stdlib `xml.etree`-only."""

    @classmethod
    def export(cls, model: CityModel, path: str) -> ExportResult:
        if not model.buildings:
            raise CityModelValidationError("CityModel en az bir bina içermeli.")

        root = ET.Element(f"{{{_NS['core']}}}CityModel", {
            f"{{{_NS['xsi']}}}schemaLocation": _SCHEMA_LOCATION,
        })
        for prefix, uri in _NS.items():
            ET.register_namespace(prefix if prefix != "core" else "", uri)

        vertex_count = 0
        triangle_count = 0
        surface_counter = 0

        for building in model.buildings:
            ring2d = building.footprint.closed_ring()
            base_z = building.ground_z
            top_z = building.ground_z + building.height

            member = ET.SubElement(root, f"{{{_NS['core']}}}cityObjectMember")
            bldg_el = ET.SubElement(member, f"{{{_NS['bldg']}}}Building", {
                f"{{{_NS['gml']}}}id": building.building_id,
            })

            name_el = ET.SubElement(bldg_el, f"{{{_NS['gml']}}}name")
            name_el.text = building.building_id

            if building.function is not None:
                fn_el = ET.SubElement(bldg_el, f"{{{_NS['bldg']}}}function")
                fn_el.text = str(building.function)
            if building.year_of_construction is not None:
                yr_el = ET.SubElement(bldg_el, f"{{{_NS['bldg']}}}yearOfConstruction")
                yr_el.text = str(building.year_of_construction)
            height_el = ET.SubElement(bldg_el, f"{{{_NS['bldg']}}}measuredHeight", {"uom": "m"})
            height_el.text = f"{building.height:.3f}"

            # --- LOD1: tek Solid, ground + roof (düz kopya) + duvarlar --- #
            lod1_solid_prop = ET.SubElement(bldg_el, f"{{{_NS['bldg']}}}lod1Solid")
            solid1 = ET.SubElement(lod1_solid_prop, f"{{{_NS['gml']}}}Solid")
            exterior1 = ET.SubElement(solid1, f"{{{_NS['gml']}}}exterior")
            shell1 = ET.SubElement(exterior1, f"{{{_NS['gml']}}}CompositeSurface")

            ground_ring = [(p.x, p.y, base_z) for p in ring2d]
            roof_ring = [(p.x, p.y, top_z) for p in ring2d]

            surface_counter += 1
            member_ground = ET.SubElement(shell1, f"{{{_NS['gml']}}}surfaceMember")
            _ring_element(member_ground, "ground", f"{building.building_id}_gnd_{surface_counter}",
                          list(reversed(ground_ring)))
            vertex_count += len(ground_ring)
            triangle_count += max(0, len(ground_ring) - 3)

            surface_counter += 1
            member_roof = ET.SubElement(shell1, f"{{{_NS['gml']}}}surfaceMember")
            _ring_element(member_roof, "roof", f"{building.building_id}_roof_{surface_counter}", roof_ring)
            vertex_count += len(roof_ring)
            triangle_count += max(0, len(roof_ring) - 3)

            n = len(ring2d) - 1
            for i in range(n):
                a, b = i, (i + 1) % n
                wall_ring = [ground_ring[a], ground_ring[b], roof_ring[b], roof_ring[a], ground_ring[a]]
                surface_counter += 1
                member_wall = ET.SubElement(shell1, f"{{{_NS['gml']}}}surfaceMember")
                _ring_element(member_wall, "wall", f"{building.building_id}_wall_{surface_counter}", wall_ring)
                vertex_count += 4
                triangle_count += 2

            # --- LOD2 (opsiyonel): gerçek çatı mesh'i + semantik boundedBy --- #
            if building.roof_mesh is not None:
                eave_z = building.ground_z + building.resolved_eave_height()
                lod2_solid_prop = ET.SubElement(bldg_el, f"{{{_NS['bldg']}}}lod2Solid")
                solid2 = ET.SubElement(lod2_solid_prop, f"{{{_NS['gml']}}}Solid")
                exterior2 = ET.SubElement(solid2, f"{{{_NS['gml']}}}exterior")
                shell2 = ET.SubElement(exterior2, f"{{{_NS['gml']}}}CompositeSurface")

                # GroundSurface - boundedBy semantiği ile
                surface_counter += 1
                gnd_bounded = ET.SubElement(bldg_el, f"{{{_NS['bldg']}}}boundedBy")
                gnd_surface = ET.SubElement(gnd_bounded, f"{{{_NS['bldg']}}}GroundSurface", {
                    f"{{{_NS['gml']}}}id": f"{building.building_id}_GroundSurface",
                })
                gnd_lod2 = ET.SubElement(gnd_surface, f"{{{_NS['bldg']}}}lod2MultiSurface")
                gnd_ms = ET.SubElement(gnd_lod2, f"{{{_NS['gml']}}}MultiSurface")
                gnd_member = ET.SubElement(gnd_ms, f"{{{_NS['gml']}}}surfaceMember")
                _ring_element(gnd_member, "ground2", f"{building.building_id}_gnd2_{surface_counter}",
                              list(reversed(ground_ring)))
                shell2_ground = ET.SubElement(shell2, f"{{{_NS['gml']}}}surfaceMember")
                _ring_element(shell2_ground, "ground2s", f"{building.building_id}_gnd2s_{surface_counter}",
                              list(reversed(ground_ring)))

                # WallSurface'lar (eave yüksekliğine kadar)
                eave_ring = [(p.x, p.y, eave_z) for p in ring2d]
                for i in range(n):
                    a, b = i, (i + 1) % n
                    wall_ring = [ground_ring[a], ground_ring[b], eave_ring[b], eave_ring[a], ground_ring[a]]
                    surface_counter += 1
                    wall_bounded = ET.SubElement(bldg_el, f"{{{_NS['bldg']}}}boundedBy")
                    wall_surface = ET.SubElement(wall_bounded, f"{{{_NS['bldg']}}}WallSurface", {
                        f"{{{_NS['gml']}}}id": f"{building.building_id}_WallSurface_{i}",
                    })
                    wall_lod2 = ET.SubElement(wall_surface, f"{{{_NS['bldg']}}}lod2MultiSurface")
                    wall_ms = ET.SubElement(wall_lod2, f"{{{_NS['gml']}}}MultiSurface")
                    wall_member = ET.SubElement(wall_ms, f"{{{_NS['gml']}}}surfaceMember")
                    _ring_element(wall_member, "wall2", f"{building.building_id}_wall2_{surface_counter}", wall_ring)
                    shell2_wall = ET.SubElement(shell2, f"{{{_NS['gml']}}}surfaceMember")
                    _ring_element(shell2_wall, "wall2s", f"{building.building_id}_wall2s_{surface_counter}", wall_ring)
                    vertex_count += 4
                    triangle_count += 2

                # RoofSurface - her mesh üçgeni ayrı yüzey
                mesh = building.roof_mesh
                for ti, tri in enumerate(mesh.triangles):
                    v0, v1, v2 = (mesh.vertices[i] for i in tri)
                    tri_ring = [
                        (v0.x, v0.y, v0.z), (v1.x, v1.y, v1.z), (v2.x, v2.y, v2.z), (v0.x, v0.y, v0.z),
                    ]
                    surface_counter += 1
                    roof_bounded = ET.SubElement(bldg_el, f"{{{_NS['bldg']}}}boundedBy")
                    roof_surface = ET.SubElement(roof_bounded, f"{{{_NS['bldg']}}}RoofSurface", {
                        f"{{{_NS['gml']}}}id": f"{building.building_id}_RoofSurface_{ti}",
                    })
                    roof_lod2 = ET.SubElement(roof_surface, f"{{{_NS['bldg']}}}lod2MultiSurface")
                    roof_ms = ET.SubElement(roof_lod2, f"{{{_NS['gml']}}}MultiSurface")
                    roof_member = ET.SubElement(roof_ms, f"{{{_NS['gml']}}}surfaceMember")
                    _ring_element(roof_member, "roof2", f"{building.building_id}_roof2_{surface_counter}", tri_ring)
                    shell2_roof = ET.SubElement(shell2, f"{{{_NS['gml']}}}surfaceMember")
                    _ring_element(shell2_roof, "roof2s", f"{building.building_id}_roof2s_{surface_counter}", tri_ring)
                    vertex_count += 3
                    triangle_count += 1

        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        path_obj = Path(path)
        tree.write(path_obj, encoding="utf-8", xml_declaration=True)
        bytes_written = path_obj.stat().st_size

        return ExportResult(
            path=str(path_obj),
            format="citygml",
            bytes_written=bytes_written,
            vertex_count=vertex_count,
            triangle_count=triangle_count,
        )

    @staticmethod
    def validate_well_formed(path: str) -> list[str]:
        """Dosyanın well-formed XML olduğunu ve zorunlu CityGML/GML
        namespace'lerini içerdiğini doğrular (tam XSD doğrulaması harici
        bir kütüphane gerektirir - roadmap kabul kriteri "well-formed XML
        olup gerekli namespace'leri içerir" ile sınırlıdır). Hata mesajı
        listesi döner (boşsa geçerli)."""
        errors: list[str] = []
        try:
            tree = ET.parse(path)
        except ET.ParseError as exc:
            return [f"XML well-formed değil: {exc}"]

        root = tree.getroot()
        if root.tag != f"{{{_NS['core']}}}CityModel":
            errors.append(f"Kök eleman 'core:CityModel' olmalı, bulunan: {root.tag}")

        text = Path(path).read_text(encoding="utf-8")
        for prefix, uri in (("bldg", _NS["bldg"]), ("gml", _NS["gml"])):
            if uri not in text:
                errors.append(f"Zorunlu namespace eksik: {prefix} ({uri})")
        return errors
