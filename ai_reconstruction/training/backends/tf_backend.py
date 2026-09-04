"""
TensorFlow/Keras Eğitim Backend'i
====================================

ROADMAP_V5 - Faz T1.

`torch_backend.py` ile **aynı ortak sözleşmeyi** (Predictor-uyumlu ONNX
çıktısı: giriş NCHW float32, çıkış[0] skaler yükseklik, çıkış[1]
opsiyonel çatı-tipi logit'leri) sağlayan TensorFlow/Keras karşılığı.

Notlar
------
- `mode="scratch"`: küçük bir Keras `Sequential` CNN.
- `mode="finetune"`: `tf.keras.applications` üzerinden ImageNet
  ön-eğitimli bir backbone (varsayılan eşleme aşağıda `_KERAS_APPLICATIONS`)
  + yükseklik/çatı başlıkları. `freeze_backbone=True` ise backbone
  `trainable=False`; `False` ise son `unfreeze_last_n_blocks` katmanı
  `trainable=True` yapılır ve backbone için ayrı (daha düşük) öğrenme
  oranı (`finetune_learning_rate`) kullanılır (iki optimizer'lı iki-aşamalı
  eğitim yerine, Keras'ta bunun pratik yolu: backbone ve head'i ayrı
  `Model` alt-grafikleri olarak tutup tek bir özel `train_step` yazmaktır;
  burada basitlik için tek optimizer + `LearningRateMultiplier` yerine iki
  ayrı eğitim aşaması kullanılır: önce yalnızca head, sonra (istenirse)
  düşük LR ile backbone dahil).
- ONNX export'u `tf2onnx` gerektirir (`pip install
  harita-modelleme[train-tensorflow]` içinde). Kurulu değilse `fit()`
  eğitimi tamamlar ama `onnx_path=None` döner ve bir uyarı yazar —
  eğitim, ONNX export'a bağımlı değildir.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..config import TrainingConfig
from ..dataset import ManifestDataset, dataset_summary, load_manifest, split_manifest
from .base import TrainingResult

try:
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers

    _TF_AVAILABLE = True
except ImportError as _exc:  # pragma: no cover - ortam bağımlı
    raise ImportError(
        "tensorflow kurulu değil. `pip install harita-modelleme[train-tensorflow]`"
    ) from _exc

try:
    import tf2onnx

    _TF2ONNX_AVAILABLE = True
except ImportError:  # pragma: no cover - opsiyonel
    tf2onnx = None
    _TF2ONNX_AVAILABLE = False


_KERAS_APPLICATIONS = {
    "mobilenet_v3_small": (keras.applications.MobileNetV3Small, keras.applications.mobilenet_v3.preprocess_input),
    "resnet18": None,  # keras.applications'ta yok; en yakın: resnet50 ile değiştir
    "resnet50": (keras.applications.ResNet50, keras.applications.resnet.preprocess_input),
    "efficientnet_b0": (keras.applications.EfficientNetB0, keras.applications.efficientnet.preprocess_input),
}


def _build_scratch_model(image_size: tuple[int, int], n_roof_types: int) -> keras.Model:
    inputs = keras.Input(shape=(*image_size, 3))
    x = layers.Conv2D(32, 3, padding="same", activation="relu")(inputs)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPooling2D()(x)
    x = layers.Conv2D(64, 3, padding="same", activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPooling2D()(x)
    x = layers.Conv2D(128, 3, padding="same", activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.MaxPooling2D()(x)
    x = layers.Conv2D(128, 3, padding="same", activation="relu")(x)
    x = layers.GlobalAveragePooling2D()(x)

    height_out = layers.Dense(1, name="height_m")(x)
    outputs = [height_out]
    if n_roof_types > 0:
        roof_out = layers.Dense(n_roof_types, name="roof_logits")(x)
        outputs.append(roof_out)
    return keras.Model(inputs, outputs)


def _build_finetune_model(
    backbone_name: str, image_size: tuple[int, int], n_roof_types: int, freeze_backbone: bool,
) -> tuple[keras.Model, Any]:
    entry = _KERAS_APPLICATIONS.get(backbone_name)
    if entry is None:
        raise ValueError(
            f"Bilinmeyen/desteklenmeyen backbone '{backbone_name}' (tf backend). "
            f"Desteklenenler: {[k for k, v in _KERAS_APPLICATIONS.items() if v]}"
        )
    app_cls, preprocess_fn = entry
    backbone = app_cls(include_top=False, weights="imagenet", input_shape=(*image_size, 3), pooling="avg")
    backbone.trainable = not freeze_backbone

    inputs = keras.Input(shape=(*image_size, 3))
    x = preprocess_fn(inputs)
    x = backbone(x, training=not freeze_backbone)
    height_out = layers.Dense(1, name="height_m")(x)
    outputs = [height_out]
    if n_roof_types > 0:
        roof_out = layers.Dense(n_roof_types, name="roof_logits")(x)
        outputs.append(roof_out)
    return keras.Model(inputs, outputs), backbone


def _make_model(config: TrainingConfig) -> tuple[keras.Model, Any]:
    n_roof = len(config.roof_types) if config.task in ("roof_classification", "multi_task") else 0
    if config.mode == "scratch":
        return _build_scratch_model(config.image_size, n_roof), None
    model, backbone = _build_finetune_model(
        config.pretrained_backbone, config.image_size, n_roof, config.freeze_backbone,
    )
    if not config.freeze_backbone:
        for layer in backbone.layers[:-config.unfreeze_last_n_blocks]:
            layer.trainable = False
    return model, backbone


def _load_rgb_array(image, image_size: tuple[int, int]) -> np.ndarray:
    image = image.convert("RGB").resize(image_size[::-1])
    return np.asarray(image, dtype=np.float32)


def _to_numpy_dataset(rows, images_root, image_size, roof_to_idx: dict[str, int]):
    ds = ManifestDataset(rows, images_root)
    xs, heights, roof_idxs, roof_masks = [], [], [], []
    for image, targets in ds:
        xs.append(_load_rgb_array(image, image_size))
        h = targets["height_m"]
        heights.append(float(h) if h is not None else np.nan)
        rt = targets.get("roof_type")
        if rt and rt in roof_to_idx:
            roof_idxs.append(roof_to_idx[rt])
            roof_masks.append(True)
        else:
            roof_idxs.append(0)
            roof_masks.append(False)
    return (
        np.stack(xs) if xs else np.zeros((0, *image_size, 3), dtype=np.float32),
        np.asarray(heights, dtype=np.float32),
        np.asarray(roof_idxs, dtype=np.int64),
        np.asarray(roof_masks, dtype=bool),
    )


class TensorFlowTrainerBackend:
    name = "tensorflow"

    def fit(self, config: TrainingConfig) -> TrainingResult:
        tf.random.set_seed(config.seed)

        rows = load_manifest(config.manifest_path)
        summary = dataset_summary(rows)
        train_rows, val_rows, _test_rows = split_manifest(
            rows, config.val_split, config.test_split, seed=config.seed,
        )
        roof_to_idx = {name: i for i, name in enumerate(config.roof_types)}
        has_roof = config.task in ("roof_classification", "multi_task")

        x_train, h_train, r_train, m_train = _to_numpy_dataset(
            train_rows, config.images_root, config.image_size, roof_to_idx,
        )
        val_data = None
        if val_rows:
            x_val, h_val, r_val, m_val = _to_numpy_dataset(
                val_rows, config.images_root, config.image_size, roof_to_idx,
            )
            val_targets = [h_val] + ([r_val] if has_roof else [])
            val_data = (x_val, val_targets)

        model, backbone = _make_model(config)

        losses = {"height_m": keras.losses.Huber()}
        loss_weights = {"height_m": 1.0}
        if has_roof:
            losses["roof_logits"] = keras.losses.SparseCategoricalCrossentropy(from_logits=True)
            loss_weights["roof_logits"] = 1.0

        model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=config.learning_rate),
            loss=losses, loss_weights=loss_weights,
        )

        train_targets = {"height_m": h_train}
        if has_roof:
            train_targets["roof_logits"] = r_train

        callbacks = [
            keras.callbacks.EarlyStopping(
                monitor="val_loss" if val_data else "loss",
                patience=config.early_stopping_patience, restore_best_weights=True,
            ),
        ]

        history_obj = model.fit(
            x_train, train_targets,
            validation_data=(val_data[0], {"height_m": val_data[1][0], **(
                {"roof_logits": val_data[1][1]} if has_roof else {}
            )}) if val_data else None,
            epochs=config.epochs, batch_size=config.batch_size, callbacks=callbacks, verbose=0,
        )

        # Fine-tune ikinci aşama: backbone'u düşük LR ile çöz.
        if config.mode == "finetune" and not config.freeze_backbone and backbone is not None:
            model.compile(
                optimizer=keras.optimizers.Adam(learning_rate=config.finetune_learning_rate),
                loss=losses, loss_weights=loss_weights,
            )
            history_obj = model.fit(
                x_train, train_targets,
                validation_data=(val_data[0], {"height_m": val_data[1][0], **(
                    {"roof_logits": val_data[1][1]} if has_roof else {}
                )}) if val_data else None,
                epochs=max(1, config.epochs // 2), batch_size=config.batch_size,
                callbacks=callbacks, verbose=0,
            )

        raw_history = history_obj.history
        best_val_loss = min(raw_history.get("val_loss", raw_history.get("loss", [float("inf")])))
        n_epochs = len(raw_history.get("loss", []))
        history = [
            {"epoch": i + 1, "train_loss": raw_history["loss"][i],
             "val_loss": raw_history.get("val_loss", raw_history["loss"])[i]}
            for i in range(n_epochs)
        ]

        out_dir = Path(config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = str(out_dir / "model.keras")
        model.save(checkpoint_path)
        (out_dir / "dataset_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        (out_dir / "history.json").write_text(json.dumps(history, indent=2, ensure_ascii=False))
        config.to_json(out_dir / "config.json")

        onnx_path = None
        if config.export_onnx:
            if not _TF2ONNX_AVAILABLE:
                print(
                    "[uyarı] tf2onnx kurulu değil, ONNX export atlandı. "
                    "`pip install tf2onnx` ile sonradan dönüştürebilirsiniz."
                )
            else:
                onnx_path = str(out_dir / "model.onnx")
                _export_onnx(model, config, onnx_path)

        return TrainingResult(
            backend=self.name, epochs_run=n_epochs, best_val_loss=float(best_val_loss),
            checkpoint_path=checkpoint_path, onnx_path=onnx_path, history=history,
        )

    def load_and_predict(self, model_path: str, images: list[Any]) -> list[dict]:
        model = keras.models.load_model(model_path)
        config_path = Path(model_path).parent / "config.json"
        config = TrainingConfig.from_json(config_path) if config_path.exists() else None
        image_size = config.image_size if config else (224, 224)

        batch = np.stack([_load_rgb_array(img, image_size) for img in images])
        outputs = model.predict(batch, verbose=0)
        if not isinstance(outputs, list):
            outputs = [outputs]

        results = []
        for i in range(len(images)):
            entry = {"height_m": float(np.asarray(outputs[0][i]).reshape(-1)[0])}
            if len(outputs) > 1 and config:
                idx = int(np.argmax(outputs[1][i]))
                entry["roof_type"] = config.roof_types[idx]
            results.append(entry)
        return results


def _export_onnx(model: "keras.Model", config: TrainingConfig, onnx_path: str) -> None:
    """`onnx_predictor.ImageBasedPredictor` sözleşmesine uygun ONNX export
    (NCHW giriş — Keras'ın doğal NHWC'sinden dönüştürülür)."""
    h, w = config.image_size

    class _NCHWWrapper(tf.Module):
        def __init__(self, inner: "keras.Model") -> None:
            super().__init__()
            self.inner = inner

        @tf.function(input_signature=[tf.TensorSpec([None, 3, h, w], tf.float32)])
        def __call__(self, x):
            x_nhwc = tf.transpose(x, [0, 2, 3, 1])
            return self.inner(x_nhwc)

    wrapped = _NCHWWrapper(model)
    tf2onnx.convert.from_function(
        wrapped.__call__,
        input_signature=[tf.TensorSpec([None, 3, h, w], tf.float32, name="image")],
        opset=config.onnx_opset,
        output_path=onnx_path,
    )
