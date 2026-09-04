"""
PyTorch Eğitim Backend'i
==========================

ROADMAP_V5 - Faz T1.

`onnx_predictor.ImageBasedPredictor`'ın beklediği çıktı sözleşmesiyle
(giriş: NCHW float32 görüntü; çıkış[0]: skaler yükseklik; çıkış[1]
(opsiyonel): çatı-tipi ham logit'leri) birebir uyumlu bir model üretir.

İki mod:
  - `mode="scratch"`  : küçük, sıfırdan tanımlı bir CNN (`_ScratchCNN`) —
    az veriyle bile aşırı öğrenmeye daha dayanıklı, hızlı eğitilir.
  - `mode="finetune"` : `torchvision.models` üzerinden ImageNet ön-eğitimli
    bir backbone (`config.pretrained_backbone`, örn. "mobilenet_v3_small",
    "resnet18", "efficientnet_b0") + üzerine yükseklik/çatı başlıkları.
    `config.freeze_backbone=True` ise yalnızca başlıklar eğitilir;
    `False` ise backbone'un son `unfreeze_last_n_blocks` bloğu da düşük
    öğrenme oranıyla (`config.finetune_learning_rate`) çözülür.

Bu modül yalnızca `torch`/`torchvision` gerçekten import edilebiliyorsa
yüklenir (bkz. `backends/base.py::_load_torch`); aksi halde
`resolve_backend()` `BackendUnavailable` fırlatır.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from dataclasses import asdict

from ..config import TrainingConfig
from ..dataset import ManifestDataset, dataset_summary, load_manifest, split_manifest
from .base import TrainingResult

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset
    import torchvision
    from torchvision import transforms
    from torchvision.models import get_model

    _TORCH_AVAILABLE = True
except ImportError as _exc:  # pragma: no cover - ortam bağımlı
    raise ImportError(
        "torch/torchvision kurulu değil. `pip install harita-modelleme[train-torch]`"
    ) from _exc


_BACKBONE_OUT_FEATURES = {
    "mobilenet_v3_small": 576,
    "mobilenet_v3_large": 960,
    "resnet18": 512,
    "resnet34": 512,
    "efficientnet_b0": 1280,
}


class _ScratchCNN(nn.Module):
    """Sıfırdan, küçük bir konvolüsyonel ağ. Az veri / hızlı iterasyon
    içindir; büyük veri setlerinde `mode="finetune"` genelde daha iyi
    genelleme sağlar."""

    def __init__(self, n_roof_types: int) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(3, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(64, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(inplace=True),
            nn.MaxPool2d(2),
            nn.Conv2d(128, 128, 3, padding=1), nn.BatchNorm2d(128), nn.ReLU(inplace=True),
            nn.AdaptiveAvgPool2d(1),
        )
        self.height_head = nn.Linear(128, 1)
        self.roof_head = nn.Linear(128, n_roof_types) if n_roof_types > 0 else None

    def forward(self, x: "torch.Tensor") -> tuple["torch.Tensor", "torch.Tensor | None"]:
        feat = self.features(x).flatten(1)
        height = self.height_head(feat).squeeze(-1)
        roof_logits = self.roof_head(feat) if self.roof_head is not None else None
        return height, roof_logits


class _FinetuneModel(nn.Module):
    """Ön-eğitimli bir `torchvision` backbone'unun üstüne yükseklik/çatı
    başlıkları ekleyen sarmalayıcı."""

    def __init__(self, backbone_name: str, n_roof_types: int, freeze_backbone: bool) -> None:
        super().__init__()
        weights_enum_name = {
            "mobilenet_v3_small": "MobileNet_V3_Small_Weights",
            "mobilenet_v3_large": "MobileNet_V3_Large_Weights",
            "resnet18": "ResNet18_Weights",
            "resnet34": "ResNet34_Weights",
            "efficientnet_b0": "EfficientNet_B0_Weights",
        }.get(backbone_name)
        weights = "DEFAULT" if weights_enum_name else None
        self.backbone = get_model(backbone_name, weights=weights)
        # Sınıflandırma başlığını kaldırıp özellik çıkışını al.
        if hasattr(self.backbone, "classifier"):
            self.backbone.classifier = nn.Identity()
        elif hasattr(self.backbone, "fc"):
            self.backbone.fc = nn.Identity()

        if freeze_backbone:
            for p in self.backbone.parameters():
                p.requires_grad = False

        out_features = _BACKBONE_OUT_FEATURES.get(backbone_name)
        if out_features is None:
            raise ValueError(
                f"Bilinmeyen backbone '{backbone_name}'. Desteklenenler: "
                f"{sorted(_BACKBONE_OUT_FEATURES)}"
            )
        self.height_head = nn.Linear(out_features, 1)
        self.roof_head = nn.Linear(out_features, n_roof_types) if n_roof_types > 0 else None

    def unfreeze_last_n_blocks(self, n: int) -> None:
        children = list(self.backbone.children())
        for module in children[-n:]:
            for p in module.parameters():
                p.requires_grad = True

    def forward(self, x: "torch.Tensor") -> tuple["torch.Tensor", "torch.Tensor | None"]:
        feat = self.backbone(x)
        if feat.ndim > 2:
            feat = torch.flatten(feat, 1)
        height = self.height_head(feat).squeeze(-1)
        roof_logits = self.roof_head(feat) if self.roof_head is not None else None
        return height, roof_logits


class _TorchManifestDataset(Dataset):
    """`ManifestDataset`'i saran, torch tensor'ları döndüren ince katman."""

    def __init__(self, manifest_ds: ManifestDataset, image_size: tuple[int, int], augment: bool):
        self.ds = manifest_ds
        tfs = [transforms.Resize(image_size)]
        if augment:
            tfs += [
                transforms.RandomHorizontalFlip(0.5),
                transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.1),
            ]
        tfs += [
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
        self.transform = transforms.Compose(tfs)

    def __len__(self) -> int:
        return len(self.ds)

    def __getitem__(self, idx: int):
        image, targets = self.ds[idx]
        tensor = self.transform(image)
        height = targets["height_m"]
        height_tensor = torch.tensor(float(height) if height is not None else float("nan"))
        return tensor, height_tensor, targets.get("roof_type") or ""


def _make_model(config: TrainingConfig) -> nn.Module:
    n_roof = len(config.roof_types) if config.task in ("roof_classification", "multi_task") else 0
    if config.mode == "scratch":
        return _ScratchCNN(n_roof_types=n_roof)
    model = _FinetuneModel(
        config.pretrained_backbone, n_roof_types=n_roof, freeze_backbone=config.freeze_backbone,
    )
    if not config.freeze_backbone:
        model.unfreeze_last_n_blocks(config.unfreeze_last_n_blocks)
    return model


class TorchTrainerBackend:
    name = "torch"

    def fit(self, config: TrainingConfig) -> TrainingResult:
        torch.manual_seed(config.seed)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        rows = load_manifest(config.manifest_path)
        summary = dataset_summary(rows)
        train_rows, val_rows, _test_rows = split_manifest(
            rows, config.val_split, config.test_split, seed=config.seed,
        )

        train_ds = _TorchManifestDataset(
            ManifestDataset(train_rows, config.images_root), config.image_size, config.augment,
        )
        val_ds = _TorchManifestDataset(
            ManifestDataset(val_rows, config.images_root), config.image_size, augment=False,
        ) if val_rows else None

        train_loader = DataLoader(train_ds, batch_size=config.batch_size, shuffle=True)
        val_loader = DataLoader(val_ds, batch_size=config.batch_size) if val_ds else None

        model = _make_model(config).to(device)

        if config.mode == "finetune" and not config.freeze_backbone:
            head_params = list(model.height_head.parameters())
            if model.roof_head is not None:
                head_params += list(model.roof_head.parameters())
            backbone_params = [p for p in model.backbone.parameters() if p.requires_grad]
            optimizer = torch.optim.AdamW([
                {"params": head_params, "lr": config.learning_rate},
                {"params": backbone_params, "lr": config.finetune_learning_rate},
            ], weight_decay=config.weight_decay)
        else:
            optimizer = torch.optim.AdamW(
                [p for p in model.parameters() if p.requires_grad],
                lr=config.learning_rate, weight_decay=config.weight_decay,
            )

        height_loss_fn = nn.SmoothL1Loss()
        roof_loss_fn = nn.CrossEntropyLoss()
        roof_to_idx = {name: i for i, name in enumerate(config.roof_types)}

        out_dir = Path(config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        history: list[dict[str, float]] = []
        best_val_loss = float("inf")
        best_state = None
        patience_left = config.early_stopping_patience

        for epoch in range(1, config.epochs + 1):
            model.train()
            train_loss_sum, n_batches = 0.0, 0
            for images, heights, roof_names in train_loader:
                images, heights = images.to(device), heights.to(device)
                optimizer.zero_grad()
                pred_h, pred_roof_logits = model(images)

                loss = torch.tensor(0.0, device=device)
                mask = ~torch.isnan(heights)
                if mask.any():
                    loss = loss + height_loss_fn(pred_h[mask], heights[mask])
                if pred_roof_logits is not None:
                    idxs = [roof_to_idx.get(n, -1) for n in roof_names]
                    valid = [i for i, v in enumerate(idxs) if v >= 0]
                    if valid:
                        target_idx = torch.tensor([idxs[i] for i in valid], device=device)
                        loss = loss + roof_loss_fn(pred_roof_logits[valid], target_idx)

                loss.backward()
                optimizer.step()
                train_loss_sum += float(loss.item())
                n_batches += 1

            train_loss = train_loss_sum / max(n_batches, 1)

            val_loss = train_loss
            if val_loader is not None:
                model.eval()
                val_loss_sum, n_val_batches = 0.0, 0
                with torch.no_grad():
                    for images, heights, roof_names in val_loader:
                        images, heights = images.to(device), heights.to(device)
                        pred_h, pred_roof_logits = model(images)
                        vloss = torch.tensor(0.0, device=device)
                        mask = ~torch.isnan(heights)
                        if mask.any():
                            vloss = vloss + height_loss_fn(pred_h[mask], heights[mask])
                        val_loss_sum += float(vloss.item())
                        n_val_batches += 1
                val_loss = val_loss_sum / max(n_val_batches, 1)

            history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
                patience_left = config.early_stopping_patience
            else:
                patience_left -= 1
                if patience_left <= 0:
                    break

        if best_state is not None:
            model.load_state_dict(best_state)

        checkpoint_path = str(out_dir / "model.pt")
        torch.save({"state_dict": model.state_dict(), "config": asdict(config)}, checkpoint_path)
        (out_dir / "dataset_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        (out_dir / "history.json").write_text(json.dumps(history, indent=2, ensure_ascii=False))

        onnx_path = None
        if config.export_onnx:
            onnx_path = str(out_dir / "model.onnx")
            _export_onnx(model, config, onnx_path, device)

        return TrainingResult(
            backend=self.name, epochs_run=len(history), best_val_loss=best_val_loss,
            checkpoint_path=checkpoint_path, onnx_path=onnx_path, history=history,
        )

    def load_and_predict(self, model_path: str, images: list[Any]) -> list[dict]:
        checkpoint = torch.load(model_path, map_location="cpu")
        config = TrainingConfig(**checkpoint["config"])
        model = _make_model(config)
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()

        tf_ = transforms.Compose([
            transforms.Resize(config.image_size), transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        results = []
        with torch.no_grad():
            for img in images:
                x = tf_(img).unsqueeze(0)
                pred_h, pred_roof_logits = model(x)
                entry = {"height_m": float(pred_h.item())}
                if pred_roof_logits is not None:
                    idx = int(torch.argmax(pred_roof_logits, dim=1).item())
                    entry["roof_type"] = config.roof_types[idx]
                results.append(entry)
        return results


def _export_onnx(model: nn.Module, config: TrainingConfig, onnx_path: str, device) -> None:
    """Eğitilmiş modeli, `onnx_predictor.ImageBasedPredictor`'ın beklediği
    NCHW-giriş / (height, [roof_logits])-çıkış sözleşmesiyle export eder."""
    model.eval()
    h, w = config.image_size
    dummy = torch.randn(1, 3, h, w, device=device)
    output_names = ["height_m"]
    has_roof = config.task in ("roof_classification", "multi_task")
    if has_roof:
        output_names.append("roof_logits")

    class _OnnxExportWrapper(nn.Module):
        def __init__(self, inner: nn.Module) -> None:
            super().__init__()
            self.inner = inner

        def forward(self, x):
            height, roof_logits = self.inner(x)
            if roof_logits is None:
                return height
            return height, roof_logits

    wrapped = _OnnxExportWrapper(model)
    export_kwargs: dict[str, Any] = dict(
        input_names=["image"], output_names=output_names,
        dynamic_axes={"image": {0: "batch"}, **{n: {0: "batch"} for n in output_names}},
        opset_version=config.onnx_opset,
    )
    try:
        # PyTorch >= 2.5 varsayılan olarak dynamo tabanlı exporter'a geçti;
        # bu, ek `onnxscript` bağımlılığı gerektirir. `dynamo=False` ile
        # klasik (TorchScript tabanlı) exporter'a düşülür — bu modülün
        # gerektirdiği tek bağımlılık `torch`/`torchvision` olarak kalır.
        torch.onnx.export(wrapped, dummy, onnx_path, dynamo=False, **export_kwargs)
    except TypeError:
        # Eski torch sürümlerinde `dynamo` argümanı yok — zaten klasik
        # exporter kullanılıyor demektir.
        torch.onnx.export(wrapped, dummy, onnx_path, **export_kwargs)
