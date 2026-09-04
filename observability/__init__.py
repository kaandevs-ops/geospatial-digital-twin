"""
Observability (ROADMAP_V4 — Faz E15)
=======================================

Yapılandırılmış (JSON-lines) loglama, metrik toplama (counter/gauge/
histogram) ve bunların `app_shell` REST API'sine (Faz 18) bağlanması.

Alt modüller:
    logging          - StructuredLogger (JSON-lines, bellek-içi kayıt tamponu)
    metrics           - MetricsRegistry (Counter/Gauge/Histogram + Prometheus export)
    instrumentation     - InstrumentedRouter (RestRouter için şeffaf log+metrik sarmalayıcı)

Teknik spesifikasyon: `ROADMAP_V4.md` Faz E15 bölümü.
"""

from __future__ import annotations

from .instrumentation import InstrumentedRouter
from .logging import StructuredLogger
from .metrics import DEFAULT_HISTOGRAM_BUCKETS, MetricsRegistry
from .scenario_audit import (
    SCENARIO_AUDIT_EVENT,
    query_scenario_audit_trail,
    record_scenario_event,
)

__all__ = [
    "StructuredLogger",
    "MetricsRegistry",
    "DEFAULT_HISTOGRAM_BUCKETS",
    "InstrumentedRouter",
    "SCENARIO_AUDIT_EVENT",
    "record_scenario_event",
    "query_scenario_audit_trail",
]
