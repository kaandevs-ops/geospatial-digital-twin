"""
Export — IFC (BIM)
===================

Roadmap V3 - Faz D7 (IFC bölümü). İnşaat sektörü standardı IFC4'ün STEP
(ISO 10303-21, "Simple Physical File") metin formatında **minimal** bir alt
kümesini üretir — tam IFC4 şeması değil, `IfcProject` → `IfcSite` →
`IfcBuilding` → `IfcBuildingStorey` → (`IfcSpace` | `IfcWallStandardCase`)
hiyerarşisiyle temel geometriyi (`IfcExtrudedAreaSolid`, taban poligonundan
dikey ekstrüzyon) taşıyan, stdlib-only bir yazıcı.

Girdi: `core_engine.geometry_engine.Polygon` tabanlı basit oda/duvar
tanımları (`IFCRoom`, `IFCWall`) — `building_reconstruction`'a sıkı bağımlı
olmadan, roadmap'in "temel varlıklarla stdlib-only bir alt küme" hedefine
uygun bağımsız bir veri modeli.

Kapsam dışı (bilinçli): malzeme/katman (`IfcMaterialLayerSet`), IFC2x3,
property set (`Pset_*`) şemaları, quantity take-off. Bunlar tam IFC şeması
gerektirir ve roadmap D7'nin "minimal alt küme" ilkesiyle çelişir.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core_engine.geometry_engine import Point2D, Polygon


class IFCValidationError(ValueError):
    """Üretilen STEP dosyası kendi söz dizimi kurallarını ihlal ediyorsa
    fırlatılır (sessizce bozuk dosya üretmek yerine)."""


def _ifc_guid() -> str:
    """IFC'nin base64-benzeri 22 karakterlik sıkıştırılmış GUID formatı
    (compressed GUID, IFC'ye özgü alfabe). Stdlib `uuid` + özel kod çözücü."""
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
    raw = uuid.uuid4().bytes  # 16 bayt = 128 bit
    num = int.from_bytes(raw, "big")
    chars = []
    # IFC compressed GUID: 4 blok (2,4,4,4,4,4,4 bit gruplarının base64'ü),
    # basitleştirilmiş ama geçerli-uzunlukta (22 karakter) bir kodlama:
    # değer tabanı 64'e çevrilir, 22 haneye tamamlanır.
    for _ in range(22):
        num, rem = divmod(num, 64)
        chars.append(alphabet[rem])
    return "".join(reversed(chars))


@dataclass(slots=True)
class IFCRoom:
    name: str
    polygon: Polygon
    floor_z: float
    height: float
    room_type: str = "SPACE"


@dataclass(slots=True)
class IFCWall:
    name: str
    start: Point2D
    end: Point2D
    floor_z: float
    height: float
    thickness: float = 0.2


@dataclass(slots=True)
class IFCBuildingModel:
    """Bir binayı IFC'ye aktarmak için minimal, framework-bağımsız girdi
    modeli: isim + kat listesi (her katta oda/duvar listesi)."""

    name: str
    rooms: list[IFCRoom] = field(default_factory=list)
    walls: list[IFCWall] = field(default_factory=list)


class _StepWriter:
    """STEP/SPF satırları biriktiren, artan `#id` atayan basit yardımcı."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._next_id = 1

    def add(self, entity: str) -> int:
        eid = self._next_id
        self._next_id += 1
        self._lines.append(f"#{eid}={entity};")
        return eid

    @property
    def lines(self) -> list[str]:
        return list(self._lines)


class IFCExporter:
    """`IFCBuildingModel` → minimal IFC4 STEP (.ifc) dosyası."""

    @staticmethod
    def _write_polyline_profile(w: _StepWriter, polygon: Polygon) -> int:
        ring = polygon.closed_ring()
        point_ids = []
        for p in ring:
            pid = w.add(f"IFCCARTESIANPOINT(({p.x:.4f},{p.y:.4f}))")
            point_ids.append(pid)
        pts_ref = ",".join(f"#{pid}" for pid in point_ids)
        polyline_id = w.add(f"IFCPOLYLINE(({pts_ref}))")
        profile_id = w.add(f"IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,$,#{polyline_id})")
        return profile_id

    @classmethod
    def _write_extruded_solid(
        cls, w: _StepWriter, polygon: Polygon, base_z: float, height: float
    ) -> int:
        origin_id = w.add(f"IFCCARTESIANPOINT((0.0,0.0,{base_z:.4f}))")
        dir_z_id = w.add("IFCDIRECTION((0.0,0.0,1.0))")
        dir_x_id = w.add("IFCDIRECTION((1.0,0.0,0.0))")
        axis2_id = w.add(f"IFCAXIS2PLACEMENT3D(#{origin_id},#{dir_z_id},#{dir_x_id})")
        profile_id = cls._write_polyline_profile(w, polygon)
        extrude_dir_id = w.add("IFCDIRECTION((0.0,0.0,1.0))")
        solid_id = w.add(
            f"IFCEXTRUDEDAREASOLID(#{profile_id},#{axis2_id},#{extrude_dir_id},{height:.4f})"
        )
        return solid_id

    @classmethod
    def _write_local_placement(cls, w: _StepWriter, parent_id: int | None = None) -> int:
        origin_id = w.add("IFCCARTESIANPOINT((0.0,0.0,0.0))")
        axis2_id = w.add(f"IFCAXIS2PLACEMENT3D(#{origin_id},$,$)")
        parent_ref = f"#{parent_id}" if parent_id is not None else "$"
        return w.add(f"IFCLOCALPLACEMENT({parent_ref},#{axis2_id})")

    @classmethod
    def export(cls, model: IFCBuildingModel, path: str) -> ExportResultIFC:
        w = _StepWriter()

        person_id = w.add("IFCPERSON($,$,'harita',$,$,$,$,$)")
        org_id = w.add("IFCORGANIZATION($,'harita.export',$,$,$)")
        person_org_id = w.add(f"IFCPERSONANDORGANIZATION(#{person_id},#{org_id},$)")
        app_id = w.add("IFCAPPLICATION(#%d,'1.0','harita.export.IFCExporter','harita')" % org_id)
        owner_history_id = w.add(f"IFCOWNERHISTORY(#{person_org_id},#{app_id},$,.ADDED.,$,$,$,0)")

        length_unit_id = w.add("IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)")
        units_id = w.add(f"IFCUNITASSIGNMENT((#{length_unit_id}))")

        world_placement_id = cls._write_local_placement(w)
        site_placement_id = cls._write_local_placement(w, world_placement_id)
        building_placement_id = cls._write_local_placement(w, site_placement_id)
        storey_placement_id = cls._write_local_placement(w, building_placement_id)

        project_guid = _ifc_guid()
        site_guid = _ifc_guid()
        building_guid = _ifc_guid()
        storey_guid = _ifc_guid()

        project_id = w.add(
            f"IFCPROJECT('{project_guid}',#{owner_history_id},'{model.name}',$,$,$,$,$,#{units_id})"
        )
        site_id = w.add(
            f"IFCSITE('{site_guid}',#{owner_history_id},'Site',$,$,#{site_placement_id},$,$,.ELEMENT.,$,$,$,$,$)"
        )
        building_id = w.add(
            f"IFCBUILDING('{building_guid}',#{owner_history_id},'{model.name}',$,$,#{building_placement_id},$,$,.ELEMENT.,$,$,$)"
        )
        storey_id = w.add(
            f"IFCBUILDINGSTOREY('{storey_guid}',#{owner_history_id},'Storey 0',$,$,#{storey_placement_id},$,$,.ELEMENT.,0.0)"
        )

        w.add(
            f"IFCRELAGGREGATES('{_ifc_guid()}',#{owner_history_id},$,$,#{project_id},(#{site_id}))"
        )
        w.add(
            f"IFCRELAGGREGATES('{_ifc_guid()}',#{owner_history_id},$,$,#{site_id},(#{building_id}))"
        )
        w.add(
            f"IFCRELAGGREGATES('{_ifc_guid()}',#{owner_history_id},$,$,#{building_id},(#{storey_id}))"
        )

        contained_ids: list[int] = []

        for room in model.rooms:
            solid_id = cls._write_extruded_solid(w, room.polygon, room.floor_z, room.height)
            shape_rep_id = w.add(f"IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#{solid_id}))")
            prod_shape_id = w.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{shape_rep_id}))")
            space_placement_id = cls._write_local_placement(w, storey_placement_id)
            space_id = w.add(
                f"IFCSPACE('{_ifc_guid()}',#{owner_history_id},'{room.name}',$,$,"
                f"#{space_placement_id},#{prod_shape_id},$,.ELEMENT.,.INTERNAL.,$)"
            )
            contained_ids.append(space_id)

        for wall in model.walls:
            dx = wall.end.x - wall.start.x
            dy = wall.end.y - wall.start.y
            length = max((dx**2 + dy**2) ** 0.5, 1e-6)
            nx, ny = -dy / length * (wall.thickness / 2.0), dx / length * (wall.thickness / 2.0)
            ring = Polygon(
                points=[
                    Point2D(wall.start.x + nx, wall.start.y + ny),
                    Point2D(wall.end.x + nx, wall.end.y + ny),
                    Point2D(wall.end.x - nx, wall.end.y - ny),
                    Point2D(wall.start.x - nx, wall.start.y - ny),
                ]
            )
            solid_id = cls._write_extruded_solid(w, ring, wall.floor_z, wall.height)
            shape_rep_id = w.add(f"IFCSHAPEREPRESENTATION($,'Body','SweptSolid',(#{solid_id}))")
            prod_shape_id = w.add(f"IFCPRODUCTDEFINITIONSHAPE($,$,(#{shape_rep_id}))")
            wall_placement_id = cls._write_local_placement(w, storey_placement_id)
            wall_id = w.add(
                f"IFCWALLSTANDARDCASE('{_ifc_guid()}',#{owner_history_id},'{wall.name}',$,$,"
                f"#{wall_placement_id},#{prod_shape_id},$)"
            )
            contained_ids.append(wall_id)

        if contained_ids:
            refs = ",".join(f"#{cid}" for cid in contained_ids)
            w.add(
                f"IFCRELCONTAINEDINSPATIALSTRUCTURE('{_ifc_guid()}',#{owner_history_id},$,$,({refs}),#{storey_id})"
            )

        header = _build_step_header(model.name)
        body = "\n".join(w.lines)
        content = f"{header}\nDATA;\n{body}\nENDSEC;\nEND-ISO-10303-21;\n"

        out_path = Path(path)
        out_path.write_text(content, encoding="utf-8")

        cls.validate_step(content)

        return ExportResultIFC(
            path=str(out_path),
            format="ifc",
            bytes_written=len(content.encode("utf-8")),
            entity_count=w._next_id - 1,
            room_count=len(model.rooms),
            wall_count=len(model.walls),
        )

    @staticmethod
    def validate_step(content: str) -> None:
        """Üretilen STEP dosyasının söz dizimi asgarilerini doğrular:
        ISO-10303-21 header/footer, dengeli `#id=...;` satırları, her
        referansın (`#N`) tanımlı bir entity'ye işaret etmesi."""
        if not content.startswith("ISO-10303-21;"):
            raise IFCValidationError("STEP dosyası 'ISO-10303-21;' ile başlamalı")
        if not content.rstrip().endswith("END-ISO-10303-21;"):
            raise IFCValidationError("STEP dosyası 'END-ISO-10303-21;' ile bitmeli")
        if "HEADER;" not in content or "ENDSEC;" not in content:
            raise IFCValidationError("HEADER/ENDSEC bölümleri eksik")
        if "DATA;" not in content:
            raise IFCValidationError("DATA bölümü eksik")

        import re

        defined_ids: set[int] = set()
        referenced_ids: set[int] = set()
        data_section = content.split("DATA;", 1)[1]
        for line in data_section.splitlines():
            line = line.strip()
            m = re.match(r"^#(\d+)=", line)
            if m:
                defined_ids.add(int(m.group(1)))
            for ref in re.findall(r"#(\d+)", line):
                referenced_ids.add(int(ref))

        dangling = referenced_ids - defined_ids
        if dangling:
            raise IFCValidationError(
                f"Tanımlanmamış entity referansları bulundu: {sorted(dangling)[:5]}"
            )


def _build_step_header(project_name: str) -> str:
    import datetime

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    safe_name = project_name.replace("'", "\\'")
    return (
        "ISO-10303-21;\n"
        "HEADER;\n"
        f"FILE_DESCRIPTION(('{safe_name}'),'2;1');\n"
        f"FILE_NAME('{safe_name}.ifc','{ts}',('harita.export'),('harita'),"
        "'harita.export.IFCExporter','harita','');\n"
        "FILE_SCHEMA(('IFC4'));\n"
        "ENDSEC;"
    )


@dataclass(slots=True)
class ExportResultIFC:
    path: str
    format: str
    bytes_written: int
    entity_count: int
    room_count: int
    wall_count: int
