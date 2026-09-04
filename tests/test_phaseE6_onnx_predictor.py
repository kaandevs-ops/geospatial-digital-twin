"""ROADMAP_V4 — Track E / Faz E6: AI Reconstruction, gerçek ONNX Runtime
entegrasyonu ve görüntü-tabanlı çıkarım.

`onnxruntime` bu ortamda kurulu (bkz. `pyproject.toml` `[ml]` extra) —
bu testler gerçek bir `.onnx` model dosyası yükleyip gerçek çıkarım
çalıştırır. Model dosyası, bu test modülü içinde `onnx` paketiyle (yalnızca
test-zamanı fixture üretimi için, üretim kodu asla import etmez) küçük bir
sentetik "yükseklik + çatı-tipi" ağı olarak inşa edilir. `onnx` paketi
mevcut değilse (model *üretimi* için gerekli), bu testler `pytest.skip`
ile atlanır; `onnxruntime` (çalışma zamanı) mevcut değilse ayrı bir test
sınıfı `OnnxBackendUnavailable`'ın doğru fırlatıldığını doğrular.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.ai_reconstruction import onnx_predictor
from harita.ai_reconstruction.height_model import MLAssistedHeightPredictor
from harita.ai_reconstruction.onnx_predictor import (
    ImageBasedPredictor,
    OnnxBackendUnavailable,
    OnnxModelShapeError,
)

try:
    import onnx
    from onnx import TensorProto, helper

    _ONNX_BUILDER_AVAILABLE = True
except ImportError:  # pragma: no cover - ortam bağımlı
    _ONNX_BUILDER_AVAILABLE = False

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]


IMG_SHAPE = (1, 3, 8, 8)  # NCHW - küçük sentetik "uydu görüntüsü" kırpması
N_ROOF_CLASSES = 3  # flat / gable / hip alt-kümesi


def _build_synthetic_height_roof_model(path: str) -> None:
    """Girdi: (1,3,8,8) görüntü tensor'u.
    Çıktı[0]: skaler yükseklik (piksel ortalaması * sabit ölçek + bias).
    Çıktı[1]: 3 sınıflık çatı-tipi logit'leri (basit lineer projeksiyon).
    Tamamen stdlib+onnx ile elle kurulmuş bir hesaplama grafiği - eğitim
    gerektirmez, yalnızca ONNX Runtime çıkarım yolunun (gerçek graph
    execution) uçtan uca çalıştığını kanıtlamak için tasarlanmıştır.
    """
    input_tensor = helper.make_tensor_value_info(
        "image", TensorProto.FLOAT, list(IMG_SHAPE)
    )
    height_out = helper.make_tensor_value_info(
        "height", TensorProto.FLOAT, [1, 1]
    )
    roof_out = helper.make_tensor_value_info(
        "roof_logits", TensorProto.FLOAT, [1, N_ROOF_CLASSES]
    )

    # height = mean(image) * 30.0 + 3.0
    mean_node = helper.make_node("ReduceMean", ["image"], ["mean_all"], keepdims=1)
    scale_init = helper.make_tensor("scale", TensorProto.FLOAT, [1], [30.0])
    bias_init = helper.make_tensor("bias", TensorProto.FLOAT, [1], [3.0])
    scale_node = helper.make_node("Mul", ["mean_all", "scale"], ["scaled"])
    bias_node = helper.make_node("Add", ["scaled", "bias"], ["height_pre"])
    reshape_h_init = helper.make_tensor("height_shape", TensorProto.INT64, [2], [1, 1])
    reshape_h_node = helper.make_node(
        "Reshape", ["height_pre", "height_shape"], ["height"]
    )

    # roof_logits = flatten(mean-pooled-per-channel) @ small fixed weight matrix
    gap_node = helper.make_node(
        "GlobalAveragePool", ["image"], ["gap"]
    )  # (1,3,1,1)
    flat_shape_init = helper.make_tensor("flat_shape", TensorProto.INT64, [2], [1, 3])
    flat_node = helper.make_node("Reshape", ["gap", "flat_shape"], ["gap_flat"])
    weight_vals = [0.1, 0.2, 0.3, 0.4, 0.1, 0.2, -0.3, 0.5, 0.05]
    weight_init = helper.make_tensor(
        "roof_weight", TensorProto.FLOAT, [3, N_ROOF_CLASSES], weight_vals
    )
    matmul_node = helper.make_node(
        "MatMul", ["gap_flat", "roof_weight"], ["roof_logits"]
    )

    graph = helper.make_graph(
        [
            mean_node, scale_node, bias_node, reshape_h_node,
            gap_node, flat_node, matmul_node,
        ],
        "synthetic_height_roof_net",
        [input_tensor],
        [height_out, roof_out],
        initializer=[scale_init, bias_init, reshape_h_init, flat_shape_init, weight_init],
    )
    model = helper.make_model(graph, producer_name="harita-e6-test-fixture")
    model.opset_import[0].version = 13
    onnx.checker.check_model(model)
    onnx.save(model, path)


@pytest.fixture()
def synthetic_model_path(tmp_path) -> str:
    if not _ONNX_BUILDER_AVAILABLE:
        pytest.skip("onnx paketi kurulu değil (yalnızca test-fixture üretimi için gerekli)")
    path = str(tmp_path / "synthetic_height_roof.onnx")
    _build_synthetic_height_roof_model(path)
    return path


class TestOnnxAvailability:
    def test_is_available_matches_import(self) -> None:
        try:
            import onnxruntime  # noqa: F401
            expected = True
        except ImportError:
            expected = False
        assert onnx_predictor.is_available() is expected

    def test_version_reported_when_available(self) -> None:
        if onnx_predictor.is_available():
            assert onnx_predictor.onnxruntime_version() is not None
        else:
            assert onnx_predictor.onnxruntime_version() is None


class TestImageBasedPredictorWithoutModel:
    """model_path=None: yalnızca fallback (öznitelik-tabanlı) davranışı."""

    def test_loaded_is_false(self) -> None:
        predictor = ImageBasedPredictor(model_path=None)
        assert predictor.loaded is False
        assert predictor.input_shape is None

    def test_predict_image_raises_without_model(self) -> None:
        predictor = ImageBasedPredictor(model_path=None)
        with pytest.raises(OnnxBackendUnavailable):
            predictor.predict_image([[0.0]])

    def test_predict_falls_back_to_heuristic(self) -> None:
        predictor = ImageBasedPredictor(model_path=None)
        features = {
            "footprint_area_m2": 200.0,
            "footprint_perimeter_m": 60.0,
            "num_vertices": 4,
            "num_neighbors": 2,
            "avg_neighbor_height_m": 12.0,
        }
        result = predictor.predict(features)
        assert "height_m" in result
        assert result["source"] == "fallback_heuristic"

    def test_custom_fallback_is_used(self) -> None:
        custom = MLAssistedHeightPredictor()
        predictor = ImageBasedPredictor(model_path=None, fallback=custom)
        features = {
            "footprint_area_m2": 150.0,
            "footprint_perimeter_m": 50.0,
            "num_vertices": 4,
            "num_neighbors": 1,
            "avg_neighbor_height_m": 9.0,
        }
        result = predictor.predict(features)
        assert isinstance(result, dict)
        assert "height_m" in result


@pytest.mark.skipif(not onnx_predictor.is_available(), reason="onnxruntime kurulu değil")
class TestImageBasedPredictorWithModel:
    def test_loaded_true_and_shape_reported(self, synthetic_model_path: str) -> None:
        predictor = ImageBasedPredictor(model_path=synthetic_model_path)
        assert predictor.loaded is True
        assert predictor.input_shape is not None
        assert tuple(predictor.input_shape) == IMG_SHAPE

    def test_predict_image_runs_real_inference(self, synthetic_model_path: str) -> None:
        predictor = ImageBasedPredictor(model_path=synthetic_model_path)
        image = np.full(IMG_SHAPE, 0.5, dtype=np.float32)
        result = predictor.predict_image(image)
        # mean(image) = 0.5 -> height = 0.5*30 + 3 = 18.0
        assert result.height_m == pytest.approx(18.0, rel=1e-4)
        assert result.roof_type in onnx_predictor.DEFAULT_ROOF_TYPES
        assert result.roof_confidence is not None
        assert 0.0 <= result.roof_confidence <= 1.0
        assert result.source == "onnx"

    def test_predict_image_varies_with_input(self, synthetic_model_path: str) -> None:
        predictor = ImageBasedPredictor(model_path=synthetic_model_path)
        dark = predictor.predict_image(np.zeros(IMG_SHAPE, dtype=np.float32))
        bright = predictor.predict_image(np.ones(IMG_SHAPE, dtype=np.float32))
        assert dark.height_m == pytest.approx(3.0, rel=1e-4)
        assert bright.height_m == pytest.approx(33.0, rel=1e-4)
        assert bright.height_m > dark.height_m

    def test_shape_mismatch_raises(self, synthetic_model_path: str) -> None:
        predictor = ImageBasedPredictor(model_path=synthetic_model_path)
        wrong_shape_image = np.zeros((1, 3, 4, 4), dtype=np.float32)
        with pytest.raises(OnnxModelShapeError):
            predictor.predict_image(wrong_shape_image)

    def test_custom_roof_types_mapping(self, synthetic_model_path: str) -> None:
        custom_labels = ("a", "b", "c")
        predictor = ImageBasedPredictor(
            model_path=synthetic_model_path, roof_types=custom_labels
        )
        result = predictor.predict_image(np.full(IMG_SHAPE, 0.2, dtype=np.float32))
        assert result.roof_type in custom_labels

    def test_predict_protocol_still_uses_fallback_not_model(
        self, synthetic_model_path: str
    ) -> None:
        # predict() Predictor protokolüne uyumluluk içindir (öznitelik
        # tabanlı) - yüklü ONNX modeli görüntü almadığı için burada
        # kullanılmaz, her zaman fallback devreye girer.
        predictor = ImageBasedPredictor(model_path=synthetic_model_path)
        features = {
            "footprint_area_m2": 100.0,
            "footprint_perimeter_m": 40.0,
            "num_vertices": 4,
            "num_neighbors": 0,
            "avg_neighbor_height_m": 0.0,
        }
        result = predictor.predict(features)
        assert result["source"] == "fallback_heuristic"


class TestOnnxBackendUnavailableWhenNotInstalled:
    """onnxruntime kurulu DEĞİLKEN beklenen davranış - monkeypatch ile
    kurulu-değil senaryosunu simüle eder (gerçek ortamda kurulu olsa bile)."""

    def test_require_onnxruntime_raises_when_patched_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(onnx_predictor, "_ONNXRUNTIME_AVAILABLE", False)
        with pytest.raises(OnnxBackendUnavailable):
            onnx_predictor._require_onnxruntime()

    def test_model_path_given_but_unavailable_raises(
        self, monkeypatch: pytest.MonkeyPatch, synthetic_model_path: str
    ) -> None:
        monkeypatch.setattr(onnx_predictor, "_ONNXRUNTIME_AVAILABLE", False)
        with pytest.raises(OnnxBackendUnavailable):
            ImageBasedPredictor(model_path=synthetic_model_path)
