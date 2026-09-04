"""
Eğitim Backend Protokolü
==========================

ROADMAP_V5 - Faz T1.

`train.py`, hangi framework'ün kurulu olduğuna bakmaksızın tek bir
`TrainerBackend` arayüzü üzerinden çalışır. İki somut implementasyon:

  - `torch_backend.TorchTrainerBackend`      (torch + torchvision gerekir)
  - `tf_backend.TensorFlowTrainerBackend`    (tensorflow gerekir)

Her ikisi de yalnızca *gerçekten seçildiklerinde* kendi framework'lerini
import eder (bkz. her modülün başındaki `try/except ImportError`) — bu
sayede `pip install harita-modelleme[train-torch]` yapan biri
tensorflow'a hiç ihtiyaç duymaz ve tam tersi.

`resolve_backend(name)` seçim mantığını içerir:
  - "torch"      -> zorunlu torch, yoksa `BackendUnavailable`
  - "tensorflow" -> zorunlu tensorflow, yoksa `BackendUnavailable`
  - "auto"       -> önce torch, yoksa tensorflow, o da yoksa hata
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..config import TrainingConfig


class BackendUnavailable(RuntimeError):
    """İstenen (veya 'auto' ile bulunabilen) hiçbir ML framework'ü kurulu değil."""


@runtime_checkable
class TrainerBackend(Protocol):
    """Her eğitim backend'inin uyması gereken minimal arayüz."""

    name: str

    def fit(self, config: TrainingConfig) -> TrainingResult:
        """Modeli eğitir (scratch ya da finetune, `config.mode`'a göre),
        `config.output_dir` altına checkpoint + metrics.json yazar,
        `config.export_onnx=True` ise ONNX'e de export eder."""
        ...

    def load_and_predict(self, model_path: str, images: list[Any]) -> list[dict]:
        """Eğitilmiş bir checkpoint'i yükleyip verilen görüntü listesi
        üzerinde tahmin döner — hızlı doğrulama/smoke-test için."""
        ...


class TrainingResult:
    """Framework'ten bağımsız, ortak eğitim sonucu özeti."""

    def __init__(
        self,
        backend: str,
        epochs_run: int,
        best_val_loss: float,
        checkpoint_path: str,
        onnx_path: str | None,
        history: list[dict[str, float]],
    ) -> None:
        self.backend = backend
        self.epochs_run = epochs_run
        self.best_val_loss = best_val_loss
        self.checkpoint_path = checkpoint_path
        self.onnx_path = onnx_path
        self.history = history

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "epochs_run": self.epochs_run,
            "best_val_loss": self.best_val_loss,
            "checkpoint_path": self.checkpoint_path,
            "onnx_path": self.onnx_path,
            "history": self.history,
        }


def resolve_backend(name: str) -> TrainerBackend:
    if name == "torch":
        return _load_torch()
    if name == "tensorflow":
        return _load_tf()
    if name == "auto":
        try:
            return _load_torch()
        except BackendUnavailable:
            return _load_tf()
    raise ValueError(f"Bilinmeyen backend: {name!r} (torch|tensorflow|auto)")


def _load_torch() -> TrainerBackend:
    try:
        from .torch_backend import TorchTrainerBackend
    except ImportError as exc:
        raise BackendUnavailable(
            "PyTorch kurulu değil. Kurmak için: pip install harita-modelleme[train-torch]"
        ) from exc
    return TorchTrainerBackend()


def _load_tf() -> TrainerBackend:
    try:
        from .tf_backend import TensorFlowTrainerBackend
    except ImportError as exc:
        raise BackendUnavailable(
            "TensorFlow kurulu değil. Kurmak için: pip install harita-modelleme[train-tensorflow]"
        ) from exc
    return TensorFlowTrainerBackend()
