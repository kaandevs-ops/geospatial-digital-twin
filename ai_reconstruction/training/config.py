"""
Eğitim Konfigürasyonu
=======================

ROADMAP_V5 - Faz T1: "Sıfırdan eğitim + fine-tuning altyapısı".

Bu modül, hangi framework (PyTorch/TensorFlow) veya hangi mimari
kullanılırsa kullanılsın, tüm eğitim çalıştırmalarının paylaştığı ortak
konfigürasyon şemasını tanımlar. `train.py` bu config'i bir backend'e
(`backends/torch_backend.py` veya `backends/tf_backend.py`) iletir;
backend'ler kendi framework'lerine özgü kodu bu ortak alanlardan üretir.

Tasarım kararı: config stdlib-only (dataclass + json/yaml okuma) kalır —
`torch`/`tensorflow` importu yalnızca gerçekten seçilen backend içinde
yapılır (bkz. backends/base.py docstring'i), böylece hangi framework'ün
kurulu olduğunu kontrol eden/CLI'ı çalıştıran kod hiçbir ML kütüphanesi
gerektirmez.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

TaskType = Literal["height_regression", "roof_classification", "multi_task"]
BackendName = Literal["torch", "tensorflow", "auto"]
Mode = Literal["scratch", "finetune"]


@dataclass(slots=True)
class TrainingConfig:
    # --- Veri ---
    manifest_path: str
    """`DATASET_GUIDE.md`'de tanımlanan manifest.csv/.jsonl dosyasının yolu."""
    images_root: str = "."
    """Manifest'teki göreli görüntü yollarının çözüleceği kök dizin."""
    val_split: float = 0.15
    test_split: float = 0.10
    image_size: tuple[int, int] = (224, 224)
    seed: int = 42

    # --- Görev ---
    task: TaskType = "height_regression"
    roof_types: tuple[str, ...] = (
        "flat",
        "gable",
        "hip",
        "mansard",
        "shed",
        "gambrel",
        "dome",
    )

    # --- Model / mod ---
    backend: BackendName = "auto"
    """'auto' -> torch kuruluysa torch, yoksa tensorflow, o da yoksa hata."""
    mode: Mode = "scratch"
    """'scratch': sıfırdan küçük CNN. 'finetune': `pretrained_backbone`'u
    dondurup/kısmen çözerek (bkz. `freeze_backbone`) yeniden eğitir."""
    pretrained_backbone: str = "mobilenet_v3_small"
    """Yalnızca mode='finetune' iken kullanılır. Desteklenenler için
    `model_zoo.py`'a bakın (her iki backend için de karşılıkları var)."""
    freeze_backbone: bool = True
    """True: yalnızca yeni eklenen regresyon/sınıflandırma başlığı eğitilir
    (az veriyle güvenli). False: backbone'un son katmanları da çözülür
    (daha çok veri gerektirir, `unfreeze_last_n_blocks` ile kontrol edilir)."""
    unfreeze_last_n_blocks: int = 2

    # --- Optimizasyon ---
    epochs: int = 30
    batch_size: int = 16
    learning_rate: float = 1e-3
    finetune_learning_rate: float = 1e-4
    """`mode='finetune'` ve `freeze_backbone=False` iken çözülen katmanlar
    için ayrı (daha düşük) öğrenme oranı — yakalanmış ön-eğitimli
    ağırlıkların bozulmaması için."""
    weight_decay: float = 1e-4
    early_stopping_patience: int = 5
    augment: bool = True

    # --- Çıktı ---
    output_dir: str = "runs/default"
    export_onnx: bool = True
    onnx_opset: int = 17

    def to_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @classmethod
    def from_json(cls, path: str | Path) -> TrainingConfig:
        data = json.loads(Path(path).read_text())
        if "image_size" in data:
            data["image_size"] = tuple(data["image_size"])
        if "roof_types" in data:
            data["roof_types"] = tuple(data["roof_types"])
        return cls(**data)
