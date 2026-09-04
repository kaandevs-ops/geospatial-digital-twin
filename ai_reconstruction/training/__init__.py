"""
ai_reconstruction.training
============================

ROADMAP_V5 - Faz T1: "Sıfırdan eğitim + fine-tuning altyapısı".

Bu paket, projenin `ai_reconstruction` bileşenlerinin (özellikle
`onnx_predictor.ImageBasedPredictor`) kullanacağı gerçek modelleri
**eğitmek** için kod sağlar (mevcut `onnx_predictor.py`/`sklearn_wrapper.py`
yalnızca hazır bir modeli *çalıştırır* — eğitim kodu içermezdi).

Hızlı başlangıç
----------------
    pip install harita-modelleme[train-torch]   # veya [train-tensorflow]

    python -m harita.ai_reconstruction.training.train init-config cfg.json
    # cfg.json içindeki manifest_path/images_root'u kendi veri setinize göre düzenleyin
    python -m harita.ai_reconstruction.training.train fit cfg.json

Detaylar için `README.md` (bu paket) ve `DATASET_GUIDE.md`'ye bakın.
"""

from .config import TrainingConfig

__all__ = ["TrainingConfig"]
