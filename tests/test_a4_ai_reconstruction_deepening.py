"""Roadmap V2 - A4 (AI Reconstruction derinleştirme) testleri.

Kapsam:
    - `HeightRegressionModel.fit()` verinin katsayılarına gerçekten
      duyarlı mı (ör. daha büyük bina -> daha yüksek tahmin)
    - Eğitilmemiş modelde `ModelNotTrainedError`
    - `MLAssistedHeightPredictor` eğitilmemiş/`None` modelde saf
      `HeuristicPredictor` ile birebir aynı davranış (fallback garantisi)
    - Eğitilmiş modelde `AIBuildingAnalyzer` üzerinden uçtan uca çalışma +
      `uncertainty_m`/`confidence` alanlarının varlığı
    - `benchmark_height_predictors()` kabul kriteri: eğitilmiş model MAE'si
      heuristic'ten kötü olamaz (ölçülebilir doğruluk raporu)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction import Footprint
from harita.ai_reconstruction import (
    AIBuildingAnalyzer, HeuristicPredictor,
    HeightRegressionModel, ModelNotTrainedError, MLAssistedHeightPredictor,
    generate_synthetic_training_set, train_default_height_model,
    benchmark_height_predictors, HeightBenchmarkReport,
)


def _rect_footprint(w: float, d: float, building_type: str = "apartments") -> Footprint:
    return Footprint(
        polygon=Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)]),
        building_type=building_type,
    )


# ------------------------------------------------------------------ #
# HeightRegressionModel — gerçekten "eğitiliyor" mu?
# ------------------------------------------------------------------ #

def test_untrained_model_raises_on_predict():
    model = HeightRegressionModel()
    assert model.is_trained is False
    with pytest.raises(ModelNotTrainedError):
        model.predict_raw({"area_m2": 100.0, "perimeter_m": 40.0, "aspect_ratio": 1.0})


def test_fit_requires_minimum_samples():
    with pytest.raises(ValueError):
        HeightRegressionModel.fit([{"area_m2": 1.0, "perimeter_m": 1.0, "aspect_ratio": 1.0}], [3.0])


def test_fit_learns_monotonic_relationship():
    """Sentetik veri: yükseklik = 2 * alan + gürültüsüz -> model katsayısı
    alanla pozitif ilişkili olmalı ve büyük binaya küçükten daha yüksek
    tahmin vermeli (gerçekten veriden öğrendiğinin kanıtı)."""
    samples = []
    targets = []
    for area in range(50, 500, 10):
        samples.append({"area_m2": float(area), "perimeter_m": float(area) ** 0.5 * 4, "aspect_ratio": 1.0})
        targets.append(3.0 + area * 0.05)

    model = HeightRegressionModel.fit(samples, targets)
    assert model.is_trained

    small_pred, _ = model.predict_raw({"area_m2": 60.0, "perimeter_m": 31.0, "aspect_ratio": 1.0})
    large_pred, _ = model.predict_raw({"area_m2": 480.0, "perimeter_m": 88.0, "aspect_ratio": 1.0})
    assert large_pred > small_pred


def test_fit_residual_std_near_zero_for_noiseless_linear_data():
    samples = [{"area_m2": float(a), "perimeter_m": float(a) * 0.4, "aspect_ratio": 1.0} for a in range(10, 200, 5)]
    targets = [5.0 + a * 0.1 for a in range(10, 200, 5)]
    model = HeightRegressionModel.fit(samples, targets)
    assert model.residual_std < 0.5  # gürültüsüz veri -> neredeyse sıfır kalıntı


# ------------------------------------------------------------------ #
# MLAssistedHeightPredictor - fallback garantisi
# ------------------------------------------------------------------ #

def test_fallback_with_none_model_matches_pure_heuristic():
    fp = _rect_footprint(20, 15)
    hybrid = MLAssistedHeightPredictor(model=None)
    analyzer_hybrid = AIBuildingAnalyzer(predictor=hybrid)
    analyzer_plain = AIBuildingAnalyzer(predictor=HeuristicPredictor())

    a_hybrid = analyzer_hybrid.analyze(fp)
    a_plain = analyzer_plain.analyze(fp)

    assert a_hybrid.height_m == a_plain.height_m
    assert a_hybrid.floor_count == a_plain.floor_count
    assert a_hybrid.confidence == a_plain.confidence


def test_fallback_with_untrained_model_matches_pure_heuristic():
    fp = _rect_footprint(20, 15)
    hybrid = MLAssistedHeightPredictor(model=HeightRegressionModel())  # boş, egitilmemis
    a_hybrid = AIBuildingAnalyzer(predictor=hybrid).analyze(fp)
    a_plain = AIBuildingAnalyzer(predictor=HeuristicPredictor()).analyze(fp)
    assert a_hybrid.height_m == a_plain.height_m


# ------------------------------------------------------------------ #
# Uçtan uca: eğitilmiş model + AIBuildingAnalyzer
# ------------------------------------------------------------------ #

def test_trained_model_end_to_end_via_analyzer():
    model = train_default_height_model(n_samples=200, seed=1)
    assert model.is_trained
    predictor = MLAssistedHeightPredictor(model=model)
    fp = _rect_footprint(25, 18)
    analysis = AIBuildingAnalyzer(predictor=predictor).analyze(fp)

    assert analysis.height_m > 0
    assert analysis.floor_count >= 1
    assert 0.0 < analysis.confidence <= 0.98
    assert analysis.uncertainty_m >= 0.0


def test_confidence_calibration_never_below_heuristic_baseline():
    """Eğitilmiş model heuristic'in verdiği güven düzeyinin altına
    düşürülmemeli (kalibrasyon kuralı: en kötü ihtimalle heuristic kadar)."""
    model = train_default_height_model(n_samples=200, seed=2)
    predictor = MLAssistedHeightPredictor(model=model)
    fp = _rect_footprint(30, 20)
    trained_conf = AIBuildingAnalyzer(predictor=predictor).analyze(fp).confidence
    heuristic_conf = AIBuildingAnalyzer(predictor=HeuristicPredictor()).analyze(fp).confidence
    assert trained_conf >= heuristic_conf


# ------------------------------------------------------------------ #
# Kabul kriteri: MAE karşılaştırma raporu
# ------------------------------------------------------------------ #

def test_synthetic_training_set_shapes_match():
    samples, targets = generate_synthetic_training_set(n_samples=50, seed=9)
    assert len(samples) == len(targets) == 50
    assert all(t > 0 for t in targets)
    assert all("area_m2" in s and "building_type" in s for s in samples)


def test_synthetic_training_set_is_deterministic_given_seed():
    s1, t1 = generate_synthetic_training_set(n_samples=30, seed=123)
    s2, t2 = generate_synthetic_training_set(n_samples=30, seed=123)
    assert s1 == s2
    assert t1 == t2


def test_benchmark_report_shows_measurable_improvement():
    report = benchmark_height_predictors(n_train=300, n_test=120, seed=5)
    assert isinstance(report, HeightBenchmarkReport)
    assert report.n_train == 300
    assert report.n_test == 120
    # Kabul kriteri: eğitilmiş model, heuristic'ten daha kötü olmamalı ve
    # ölçülebilir (raporlanabilir) bir MAE farkı üretmeli.
    assert report.trained_mae <= report.heuristic_mae
    assert report.improvement_ratio >= 0.0
