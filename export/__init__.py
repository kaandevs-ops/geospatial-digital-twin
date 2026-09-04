"""
Export
======

Roadmap Phase 11 - "Export".

Alt modüller:
    geometry_3d — OBJ, STL (ASCII/binary), PLY, GLTF/GLB, DXF (+ FBX/USD/USDZ/DWG
                  için açık `UnsupportedFormatError` arayüzü)
    vector_2d   — SVG (kat planı/blueprint), PNG (opsiyonel Pillow), PDF
                  (opsiyonel reportlab + bağımlılıksız minimal PDF yazıcı)
    reports     — JSON, CSV, XML, Markdown rapor exporterları + `ReportBuilder`
                  (tüm formatları tek veri modelinden üretir)

Girdi tipleri: Phase 2 `Mesh3D`/`PBRMaterial`, Phase 5 `DigitalTwin` ve genel
dict/list tabanlı rapor verisi. Bağımlılık: yalnızca stdlib (Pillow/reportlab
opsiyoneldir, yoksa zarifçe düşer).
"""

from .citygml_export import CityGMLExporter

# ROADMAP_V4 - Faz E5: CityGML/CityJSON (şehir-ölçeği semantik 3D).
from .cityjson_export import (
    CityBuilding,
    CityJSONExporter,
    CityModel,
    CityModelValidationError,
)
from .geometry_3d import (
    DWGExporter,
    DXFExporter,
    ExportResult,
    FBXExporter,
    GLTFExporter,
    GLTFImporter,
    GLTFParseError,
    OBJExporter,
    PLYExporter,
    STLExporter,
    UnsupportedFormatError,
    USDExporter,
)
from .ifc_export import (
    ExportResultIFC,
    IFCBuildingModel,
    IFCExporter,
    IFCRoom,
    IFCValidationError,
    IFCWall,
)

# Kullanıcı talebi - madde 3: somut manifest scripti (export/manifest.py +
# scripts/generate_manifest.py).
from .manifest import (
    MANIFEST_SCHEMA_VERSION,
    ManifestBuilder,
    ManifestBuildingEntry,
    ManifestFileEntry,
    ManifestValidationError,
    SceneManifest,
    load_manifest,
    verify_manifest_checksums,
)
from .reports import (
    CSVReportExporter,
    JSONReportExporter,
    MarkdownReportExporter,
    ReportBuilder,
    ReportSection,
    XMLReportExporter,
)
from .tiles_3d import (
    BoundingBox3DTiles,
    TileEntry,
    Tiles3DExporter,
    TilesetValidationError,
)
from .vector_2d import (
    FloorPlanSVGExporter,
    PDFExporter,
    PNGExporter,
    SVGCanvas,
    SVGStyle,
)

__all__ = [
    "ExportResult",
    "UnsupportedFormatError",
    "OBJExporter",
    "STLExporter",
    "PLYExporter",
    "GLTFExporter",
    "GLTFImporter",
    "GLTFParseError",
    "DXFExporter",
    "DWGExporter",
    "FBXExporter",
    "USDExporter",
    "SVGStyle",
    "SVGCanvas",
    "FloorPlanSVGExporter",
    "PNGExporter",
    "PDFExporter",
    "JSONReportExporter",
    "CSVReportExporter",
    "XMLReportExporter",
    "MarkdownReportExporter",
    "ReportSection",
    "ReportBuilder",
    "IFCValidationError",
    "IFCRoom",
    "IFCWall",
    "IFCBuildingModel",
    "IFCExporter",
    "ExportResultIFC",
    "TilesetValidationError",
    "BoundingBox3DTiles",
    "TileEntry",
    "Tiles3DExporter",
    "CityModelValidationError",
    "CityBuilding",
    "CityModel",
    "CityJSONExporter",
    "CityGMLExporter",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestBuilder",
    "ManifestBuildingEntry",
    "ManifestFileEntry",
    "ManifestValidationError",
    "SceneManifest",
    "load_manifest",
    "verify_manifest_checksums",
]
