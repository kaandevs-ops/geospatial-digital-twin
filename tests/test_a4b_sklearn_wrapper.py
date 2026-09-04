"""Roadmap V2 - A4 (kalan madde) - Opsiyonel scikit-learn model sarmalayıcısı.

`ai_reconstruction.sklearn_wrapper`'ı kapsar. `scikit-learn` bu ortamda
kuruluysa gerçek eğitim/tahmin doğrulanır; kurulu değilse
`SklearnBackendUnavailable`'ın açıkça fırlatıldığı doğrulanır.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.ai_reconstruction import sklearn_wrapper
from harita.ai_reconstruction.building_analyzer import HeuristicPredictor
from harita.ai_reconstruction.height_model import (
    MLAssistedHeightPredictor,
)


def test_is_available_matches_import():
    try:
        import sklearn  # noqa: F401

        assert sklearn_wrapper.is_available() is True
    except ImportError:
        assert sklearn_wrapper.is_available() is False


def test_unavailable_backend_raises_clear_error(monkeypatch):
    monkeypatch.setattr(sklearn_wrapper, "_SKLEARN_AVAILABLE", False)
    with pytest.raises(sklearn_wrapper.SklearnBackendUnavailable):
        sklearn_wrapper.train_default_sklearn_model(n_samples=20)


def test_predict_raw_before_fit_raises_model_not_trained():
    model = sklearn_wrapper.SklearnHeightModel()
    from harita.ai_reconstruction.height_model import ModelNotTrainedError

    with pytest.raises(ModelNotTrainedError):
        model.predict_raw({"area_m2": 100.0, "perimeter_m": 40.0})


def test_fit_requires_minimum_samples():
    model = sklearn_wrapper.SklearnHeightModel()
    with pytest.raises(ValueError):
        model.fit([{"area_m2": 1.0}] * 3, [3.0] * 3)


@pytest.mark.skipif(not sklearn_wrapper.is_available(), reason="scikit-learn kurulu değil")
class TestRealSklearnModel:
    def test_train_default_model_is_trained(self):
        model = sklearn_wrapper.train_default_sklearn_model(n_samples=60)
        assert model.is_trained
        assert model.n_training_samples == 60

    def test_predict_raw_returns_reasonable_height(self):
        model = sklearn_wrapper.train_default_sklearn_model(n_samples=200)
        height, uncertainty = model.predict_raw(
            {
                "area_m2": 400.0,
                "perimeter_m": 80.0,
                "aspect_ratio": 1.2,
                "building_type": "apartments",
            }
        )
        assert 2.0 <= height <= 200.0
        assert uncertainty >= 0.0

    def test_as_ml_assisted_predictor_matches_predictor_protocol(self):
        model = sklearn_wrapper.train_default_sklearn_model(n_samples=100)
        predictor = sklearn_wrapper.as_ml_assisted_predictor(model)
        assert isinstance(predictor, MLAssistedHeightPredictor)
        result = predictor.predict(
            {"area_m2": 300.0, "perimeter_m": 70.0, "aspect_ratio": 1.5, "building_type": "office"}
        )
        assert "height_m" in result
        assert "floor_count" in result
        assert "confidence" in result
        assert "uncertainty_m" in result
        assert result["confidence"] <= 0.98

    def test_untrained_model_falls_back_to_heuristic_via_wrapper(self):
        untrained = sklearn_wrapper.SklearnHeightModel()
        # is_trained False oldugundan MLAssistedHeightPredictor heuristic'e duser
        predictor = MLAssistedHeightPredictor(model=untrained)  # type: ignore[arg-type]
        heuristic = HeuristicPredictor()
        features = {
            "area_m2": 150.0,
            "perimeter_m": 50.0,
            "aspect_ratio": 1.1,
            "building_type": "house",
        }
        h_result = heuristic.predict(features)
        p_result = predictor.predict(features)
        assert p_result["height_m"] == h_result["height_m"]
        assert p_result["floor_count"] == h_result["floor_count"]

    def test_benchmark_report_has_all_three_maes(self):
        report = sklearn_wrapper.benchmark_sklearn_vs_heuristic(n_train=200, n_test=60)
        assert report.n_train == 200
        assert report.n_test == 60
        assert report.heuristic_mae >= 0.0
        assert report.stdlib_ols_mae >= 0.0
        assert report.sklearn_mae >= 0.0

    def test_sklearn_version_reported(self):
        assert sklearn_wrapper.sklearn_version() is not None
