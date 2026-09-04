"""
simulation_core — Omurga (Roadmap V9 / OMURGA)
================================================

Şehir-geneli simülasyon katmanlarının ortak zaman otoritesi.
Şu an: `city_clock.py` (O.1). Sonraki omurga bileşenleri (senaryo şeması,
event bus genişletmesi vb.) mevcut ilgili modüllere (`mobility/scenario.py`,
`extensibility/event_system.py`) eklenir — burada tekrarlanmaz.
"""

from __future__ import annotations

from .city_clock import CityClock, ClockEventType, ClockState

__all__ = ["CityClock", "ClockEventType", "ClockState"]
