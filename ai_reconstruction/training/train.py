"""
Eğitim CLI'ı
=============

ROADMAP_V5 - Faz T1.

Kullanım
--------
    # 1) Bir konfigürasyon dosyası oluştur (bkz. örnek: config.example.json)
    python -m harita.ai_reconstruction.training.train init-config myconfig.json

    # 2) Eğit (sıfırdan)
    python -m harita.ai_reconstruction.training.train fit myconfig.json

    # 3) Sonucu kullanılan ONNX modeliyle mevcut ImageBasedPredictor'a bağla
    #    (ayrıca bkz. bu modülün README.md'sindeki entegrasyon örneği)

Fine-tuning için `myconfig.json` içinde `"mode": "finetune"` ve
`"pretrained_backbone": "mobilenet_v3_small"` (veya `resnet18`,
`efficientnet_b0`, ...) ayarlayın.
"""

from __future__ import annotations

import argparse
import sys

from .backends.base import BackendUnavailable, resolve_backend
from .config import TrainingConfig


def _cmd_init_config(args: argparse.Namespace) -> None:
    config = TrainingConfig(
        manifest_path=args.manifest or "manifest.csv",
        mode="finetune" if args.finetune else "scratch",
    )
    config.to_json(args.output)
    print(f"Örnek konfigürasyon yazıldı: {args.output}")
    print("Alanları veri setinize göre düzenleyin (bkz. training/DATASET_GUIDE.md).")


def _cmd_fit(args: argparse.Namespace) -> None:
    config = TrainingConfig.from_json(args.config_path)
    if args.backend:
        config.backend = args.backend

    try:
        backend = resolve_backend(config.backend)
    except BackendUnavailable as exc:
        print(f"[hata] {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Backend: {backend.name} | mod: {config.mode} | görev: {config.task}")
    result = backend.fit(config)

    print(f"\nEğitim tamamlandı ({result.epochs_run} epoch).")
    print(f"En iyi val_loss: {result.best_val_loss:.4f}")
    print(f"Checkpoint: {result.checkpoint_path}")
    if result.onnx_path:
        print(f"ONNX export: {result.onnx_path}")
        print(
            "\nBu modeli mevcut sisteme bağlamak için:\n"
            "    from harita.ai_reconstruction.onnx_predictor import ImageBasedPredictor\n"
            f"    predictor = ImageBasedPredictor(model_path={result.onnx_path!r})"
        )
    else:
        print("ONNX export yapılmadı (config.export_onnx=False ya da tf2onnx eksik).")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="harita.ai_reconstruction.training.train")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init-config", help="Örnek bir TrainingConfig JSON dosyası üretir")
    p_init.add_argument("output", help="Yazılacak config dosyasının yolu")
    p_init.add_argument("--manifest", help="manifest.csv/.jsonl yolu")
    p_init.add_argument("--finetune", action="store_true", help="scratch yerine finetune mod")
    p_init.set_defaults(func=_cmd_init_config)

    p_fit = sub.add_parser("fit", help="Konfigürasyona göre eğitimi başlatır")
    p_fit.add_argument("config_path", help="TrainingConfig JSON dosyası")
    p_fit.add_argument("--backend", choices=["torch", "tensorflow", "auto"], default=None)
    p_fit.set_defaults(func=_cmd_fit)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
