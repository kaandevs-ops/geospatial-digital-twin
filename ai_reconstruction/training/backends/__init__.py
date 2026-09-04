"""Eğitim backend'leri (torch/tensorflow) — bkz. `base.py::resolve_backend`."""

from .base import BackendUnavailable, TrainerBackend, TrainingResult, resolve_backend

__all__ = ["BackendUnavailable", "TrainerBackend", "TrainingResult", "resolve_backend"]
