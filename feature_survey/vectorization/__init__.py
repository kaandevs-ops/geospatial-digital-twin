"""FAZ S4 — Kod-Tabanlı Otomatik Vektörleştirme (`feature_survey/vectorization/`).

Bkz. `README.md` bu klasörde: kapsam, tasarım ilkesi ve kabul kriteri.
"""

from __future__ import annotations

from .linework import (
    VectorFeature,
    VectorizationError,
    build_linework,
    linework_to_geojson,
)
from .qa import QAError, area_difference_ratio, grid_iou, polygon_area

__all__ = [
    "VectorFeature",
    "VectorizationError",
    "build_linework",
    "linework_to_geojson",
    "QAError",
    "area_difference_ratio",
    "grid_iou",
    "polygon_area",
]
