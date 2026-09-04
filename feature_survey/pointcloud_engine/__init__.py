"""FAZ S3 — Nokta Bulutu İşleme Köprüsü (`feature_survey/pointcloud_engine/`).

Bkz. `README.md` bu klasörde: kapsam, tasarım ilkesi ve kabul kriteri.
"""

from __future__ import annotations

from .ground_classification import (
    ASPRS_GROUND,
    ASPRS_UNCLASSIFIED,
    GroundClassificationError,
    GroundClassificationResult,
    progressive_morphological_filter,
)
from .icp import ICPError, ICPResult, run_icp
from .quality_report import (
    DensityReport,
    GapReport,
    NoiseReport,
    PointCloudQualityReport,
    QualityReportError,
    build_quality_report,
    compute_density,
    compute_gaps,
    detect_noise_sor,
)

__all__ = [
    "ASPRS_GROUND",
    "ASPRS_UNCLASSIFIED",
    "GroundClassificationError",
    "GroundClassificationResult",
    "progressive_morphological_filter",
    "ICPError",
    "ICPResult",
    "run_icp",
    "DensityReport",
    "GapReport",
    "NoiseReport",
    "PointCloudQualityReport",
    "QualityReportError",
    "build_quality_report",
    "compute_density",
    "compute_gaps",
    "detect_noise_sor",
]
