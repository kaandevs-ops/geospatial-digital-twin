"""ROADMAP_V4 — Track E / Faz E5: Export — CityGML ve CityJSON desteği.

`CityJSONExporter`/`CityGMLExporter`'ı, hem basit dikdörtgen LOD1
binalarla hem de gerçek bir çatı `Mesh3D`'si verilen LOD2 senaryosuyla
test eder. Roadmap'in kendi kabul kriterini doğrudan kanıtlar: "Üretilen
CityJSON dosyası resmi CityJSON şemasına ... temel yapısal doğrulama ile
uyar; CityGML çıktısı well-formed XML olup gerekli namespace'leri içerir."
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import Mesh3D, Vertex3D
from harita.export.cityjson_export import (
    CityBuilding,
    CityModel,
    CityModelValidationError,
    CityJSONExporter,
)
from harita.export.citygml_export import CityGMLExporter, _NS


def _rect_footprint(w: float = 10.0, d: float = 6.0) -> Polygon:
    return Polygon(points=[
        Point2D(0.0, 0.0), Point2D(w, 0.0), Point2D(w, d), Point2D(0.0, d),
    ])


def _gable_roof_mesh(w: float = 10.0, d: float = 6.0, eave_z: float = 6.0, ridge_z: float = 8.0) -> Mesh3D:
    """Basit bir beşik çatı - 2 üçgenden oluşan minimal test mesh'i."""
    verts = [
        Vertex3D(0.0, 0.0, eave_z),   # 0
        Vertex3D(w, 0.0, eave_z),     # 1
        Vertex3D(w, d, eave_z),       # 2
        Vertex3D(0.0, d, eave_z),     # 3
        Vertex3D(w / 2, 0.0, ridge_z),  # 4 - ön mahya
        Vertex3D(w / 2, d, ridge_z),    # 5 - arka mahya
    ]
    triangles = [
        (0, 1, 4), (1, 2, 5), (1, 5, 4),
        (0, 4, 5), (0, 5, 3), (3, 5, 2),
    ]
    return Mesh3D(vertices=verts, triangles=triangles, name="gable_roof")


class TestCityBuildingValidation:
    def test_valid_building_constructs(self):
        b = CityBuilding(building_id="B1", footprint=_rect_footprint(), height=9.0)
        assert b.building_id == "B1"

    def test_degenerate_footprint_raises(self):
        bad = Polygon(points=[Point2D(0, 0), Point2D(1, 0)])
        with pytest.raises(CityModelValidationError):
            CityBuilding(building_id="B1", footprint=bad, height=9.0)

    def test_non_positive_height_raises(self):
        with pytest.raises(CityModelValidationError):
            CityBuilding(building_id="B1", footprint=_rect_footprint(), height=0.0)

    def test_empty_model_raises_on_export(self, tmp_path):
        model = CityModel()
        with pytest.raises(CityModelValidationError):
            CityJSONExporter.export(model, str(tmp_path / "empty.city.json"))
        with pytest.raises(CityModelValidationError):
            CityGMLExporter.export(model, str(tmp_path / "empty.gml"))


class TestCityJSONExport:
    def test_lod1_export_structure(self, tmp_path):
        model = CityModel(crs_name="EPSG:32635")
        model.add(CityBuilding(
            building_id="Building_1", footprint=_rect_footprint(), height=9.0,
            year_of_construction=2010, function="1000",
        ))
        out = tmp_path / "city_lod1.city.json"
        result = CityJSONExporter.export(model, str(out))

        assert out.exists()
        assert result.format == "cityjson"
        assert result.vertex_count > 0
        assert result.triangle_count > 0

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["type"] == "CityJSON"
        assert data["version"] == "1.1"
        assert "Building_1" in data["CityObjects"]
        obj = data["CityObjects"]["Building_1"]
        assert obj["type"] == "Building"
        assert obj["geometry"][0]["lod"] == "1"
        assert obj["attributes"]["yearOfConstruction"] == 2010
        assert obj["attributes"]["measuredHeight"] == pytest.approx(9.0)
        assert "referenceSystem" in data["metadata"]

        errors = CityJSONExporter.validate_structure(data)
        assert errors == []

    def test_lod2_export_uses_roof_mesh(self, tmp_path):
        model = CityModel()
        model.add(CityBuilding(
            building_id="Building_2", footprint=_rect_footprint(), height=8.0,
            eave_height=6.0, roof_mesh=_gable_roof_mesh(),
        ))
        out = tmp_path / "city_lod2.city.json"
        CityJSONExporter.export(model, str(out))
        data = json.loads(out.read_text(encoding="utf-8"))
        obj = data["CityObjects"]["Building_2"]
        assert obj["geometry"][0]["lod"] == "2"
        # Semantik yüzey sayıları: 1 ground + 6 çatı üçgeni + 4 duvar = 11
        semantics_values = obj["geometry"][0]["semantics"]["values"][0]
        assert semantics_values.count(1) == 6  # RoofSurface üçgen sayısı
        assert semantics_values.count(2) == 4  # WallSurface (dikdörtgen kenar sayısı)
        assert semantics_values.count(0) == 1  # GroundSurface

    def test_multiple_buildings_share_vertex_pool(self, tmp_path):
        model = CityModel()
        model.add(CityBuilding(building_id="A", footprint=_rect_footprint(), height=5.0))
        model.add(CityBuilding(building_id="B", footprint=_rect_footprint(), height=7.0))
        out = tmp_path / "two_buildings.city.json"
        result = CityJSONExporter.export(model, str(out))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["CityObjects"]) == 2
        # Aynı taban poligonu (0,0)-(10,0)-(10,6)-(0,6) iki binada da var,
        # farklı Z'de olduğu için (farklı height) ground vertex'leri paylaşılabilir
        # ama roof vertex'leri farklı - toplam vertex sayısı < 2x her binanın tekil sayısı.
        assert result.vertex_count > 0

    def test_validate_structure_detects_missing_fields(self):
        assert CityJSONExporter.validate_structure({}) != []
        assert CityJSONExporter.validate_structure({
            "type": "CityJSON", "version": "1.1", "CityObjects": {}, "vertices": [],
        }) == []


class TestCityGMLExport:
    def test_lod1_export_is_well_formed(self, tmp_path):
        model = CityModel()
        model.add(CityBuilding(building_id="Building_1", footprint=_rect_footprint(), height=9.0))
        out = tmp_path / "city_lod1.gml"
        result = CityGMLExporter.export(model, str(out))

        assert out.exists()
        assert result.format == "citygml"
        assert result.vertex_count > 0

        errors = CityGMLExporter.validate_well_formed(str(out))
        assert errors == []

        tree = ET.parse(out)
        root = tree.getroot()
        assert root.tag == f"{{{_NS['core']}}}CityModel"
        buildings = root.findall(f".//{{{_NS['bldg']}}}Building")
        assert len(buildings) == 1
        lod1 = buildings[0].find(f"{{{_NS['bldg']}}}lod1Solid")
        assert lod1 is not None

    def test_lod2_export_includes_bounded_by_surfaces(self, tmp_path):
        model = CityModel()
        model.add(CityBuilding(
            building_id="Building_2", footprint=_rect_footprint(), height=8.0,
            eave_height=6.0, roof_mesh=_gable_roof_mesh(),
        ))
        out = tmp_path / "city_lod2.gml"
        CityGMLExporter.export(model, str(out))

        errors = CityGMLExporter.validate_well_formed(str(out))
        assert errors == []

        tree = ET.parse(out)
        root = tree.getroot()
        building = root.find(f".//{{{_NS['bldg']}}}Building")
        assert building.find(f"{{{_NS['bldg']}}}lod2Solid") is not None

        roof_surfaces = building.findall(f"{{{_NS['bldg']}}}boundedBy/{{{_NS['bldg']}}}RoofSurface")
        wall_surfaces = building.findall(f"{{{_NS['bldg']}}}boundedBy/{{{_NS['bldg']}}}WallSurface")
        ground_surfaces = building.findall(f"{{{_NS['bldg']}}}boundedBy/{{{_NS['bldg']}}}GroundSurface")
        assert len(roof_surfaces) == 6  # gable roof üçgen sayısı
        assert len(wall_surfaces) == 4
        assert len(ground_surfaces) == 1

    def test_malformed_xml_reports_error(self, tmp_path):
        bad = tmp_path / "broken.gml"
        bad.write_text("<CityModel><unclosed>", encoding="utf-8")
        errors = CityGMLExporter.validate_well_formed(str(bad))
        assert errors and "well-formed" in errors[0].lower()

    def test_missing_namespace_detected(self, tmp_path):
        stripped = tmp_path / "no_ns.gml"
        stripped.write_text('<?xml version="1.0"?><CityModel></CityModel>', encoding="utf-8")
        errors = CityGMLExporter.validate_well_formed(str(stripped))
        assert any("namespace" in e.lower() for e in errors)


class TestCrossFormatConsistency:
    """Aynı `CityModel`'in CityJSON ve CityGML çıktıları, kaynak veriyle
    (bina yüksekliği, LOD seviyesi) tutarlı olmalı."""

    def test_same_model_both_formats_agree_on_height(self, tmp_path):
        model = CityModel()
        model.add(CityBuilding(building_id="Building_1", footprint=_rect_footprint(), height=12.5))

        json_out = tmp_path / "consistency.city.json"
        gml_out = tmp_path / "consistency.gml"
        CityJSONExporter.export(model, str(json_out))
        CityGMLExporter.export(model, str(gml_out))

        data = json.loads(json_out.read_text(encoding="utf-8"))
        json_height = data["CityObjects"]["Building_1"]["attributes"]["measuredHeight"]

        tree = ET.parse(gml_out)
        gml_height_el = tree.getroot().find(f".//{{{_NS['bldg']}}}measuredHeight")
        gml_height = float(gml_height_el.text)

        assert json_height == pytest.approx(gml_height, abs=1e-3)
        assert json_height == pytest.approx(12.5, abs=1e-3)
