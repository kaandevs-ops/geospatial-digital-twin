"""FAZ S5 — Kalite kontrol & doğruluk raporu (`feature_survey/qc/`).

Bkz. `README.md` bu klasörde: kapsam, tasarım ilkesi ve kabul kriteri.
"""

from __future__ import annotations

from .pointcloud_mesh_comparison import (
    ComparisonError,
    SurfaceComparisonReport,
    compare_pointcloud_to_mesh,
)
from .technical_report import (
    InsufficientDataError,
    TechnicalReportMeta,
    build_report_lines,
    export_technical_report_json,
    export_technical_report_pdf,
)

__all__ = [
    "ComparisonError",
    "SurfaceComparisonReport",
    "compare_pointcloud_to_mesh",
    "InsufficientDataError",
    "TechnicalReportMeta",
    "build_report_lines",
    "export_technical_report_json",
    "export_technical_report_pdf",
]
