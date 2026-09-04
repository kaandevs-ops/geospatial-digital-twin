"""
Sklearn Model Wrapper — Opsiyonel Gerçek ML Kütüphanesi Entegrasyonu
======================================================================

ROADMAP_V2 A4'ün son kalan maddesi: "opsiyonel ONNX/sklearn sarmalayıcı
örneği".

`height_model.HeightRegressionModel`, stdlib-only, sıfırdan yazılmış bir
en-küçük-kareler çözücüsüdür (bkz. o modülün docstring'i) — bağımlılıksız
çalışır ama yalnızca doğrusal regresyona sınırlıdır.

Bu modül, opsiyonel `scikit-learn` bağımlılığı (`pip install
harita-modelleme[ml]`) kuruluysa, `Predictor` Protocol'üne uyan, **gerçek
bir sklearn modeliyle** (varsayılan: `GradientBoostingRegressor` — doğrusal
olmayan ilişkileri de yakalayabilen bir ensemble model) eğitim/tahmin
sağlar. `scikit-learn` yoksa `SklearnBackendUnavailable` fırlatılır; çağıran
kod `MLAssistedHeightPredictor(model=None)` ile stdlib heuristic'e
bilinçli olarak düşebilir.

Tasarım kararları
------------------
- Aynı `_feature_vector`/`FEATURE_NAMES` şemasını `height_model`'dan
  yeniden kullanır — iki backend (stdlib OLS vs. sklearn) aynı feature
  setiyle doğrudan karşılaştırılabilir (`benchmark_height_predictors`'a
  benzer bir `benchmark_sklearn_vs_heuristic()` sağlanır).
- `SklearnHeightModel`, `HeightRegressionModel` ile **aynı `Predictor`
  arayüzüne** uyar (`predict(features) -> {"height_m", "uncertainty_m"}`),
  dolayısıyla `MLAssistedHeightPredictor`'a doğrudan enjekte edilebilir
  hale getirmek için ince bir köprü (`as_ml_assisted_predictor`) sağlanır.
- Belirsizlik tahmini, ensemble modelin ağaçları arası tahmin varyansından
  (bootstrap benzeri) türetilir — tek bir nokta tahmini yerine kalibre
  edilmiş bir güven aralığı verir.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .height_model import (
    MLAssistedHeightPredictor,
    ModelNotTrainedError,
    _feature_vector,
    generate_synthetic_training_set,
)

try:
    from sklearn.ensemble import GradientBoostingRegressor  # type: ignore[import-untyped]

    _SKLEARN_AVAILABLE = True
    import sklearn  # type: ignore[import-untyped]

    _SKLEARN_VERSION = sklearn.__version__
except ImportError:  # pragma: no cover - ortam bağımlı
    GradientBoostingRegressor = None  # type: ignore[assignment,misc]
    _SKLEARN_AVAILABLE = False
    _SKLEARN_VERSION = None


class SklearnBackendUnavailable(RuntimeError):
    """`scikit-learn` kurulu değilken gerçek ML model isteğinde fırlatılır."""


def is_available() -> bool:
    """`scikit-learn` bu ortamda kurulu mu?"""
    return _SKLEARN_AVAILABLE


def sklearn_version() -> str | None:
    """Kurulu scikit-learn sürümü, yoksa None."""
    return _SKLEARN_VERSION


def _require_sklearn() -> None:
    if not _SKLEARN_AVAILABLE:
        raise SklearnBackendUnavailable(
            "scikit-learn kurulu değil. Gerçek ML tahmin modeli için "
            "`pip install harita-modelleme[ml]` (veya doğrudan "
            "`pip install scikit-learn>=1.3`) gerekir. Alternatif: "
            "`height_model.HeightRegressionModel` (stdlib-only doğrusal "
            "regresyon) veya `HeuristicPredictor`'a bilinçli olarak düşün."
        )


@dataclass(slots=True)
class SklearnHeightModel:
    """`GradientBoostingRegressor` tabanlı, gerçekten `fit()` edilen bina
    yüksekliği tahmincisi. `Predictor` Protocol'üne uyar."""

    n_estimators: int = 120
    max_depth: int = 3
    learning_rate: float = 0.05
    random_state: int = 42

    _model: object | None = field(default=None, repr=False, compare=False)
    _train_residual_std: float = field(default=0.0, repr=False, compare=False)
    n_training_samples: int = 0

    @property
    def is_trained(self) -> bool:
        return self._model is not None and self.n_training_samples > 0

    def fit(self, samples: list[dict], targets: list[float]) -> SklearnHeightModel:
        if len(samples) != len(targets):
            raise ValueError("samples ve targets ayni uzunlukta olmali")
        if len(samples) < 10:
            raise ValueError(f"en az 10 egitim ornegi gerekli, {len(samples)} verildi")
        _require_sklearn()

        rows = [_feature_vector(s)[1:] for s in samples]  # bias sütununu at
        model = GradientBoostingRegressor(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            random_state=self.random_state,
        )
        model.fit(rows, targets)

        predictions = model.predict(rows)
        residuals = [t - p for t, p in zip(targets, predictions)]
        residual_std = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0

        self._model = model
        self._train_residual_std = residual_std
        self.n_training_samples = len(samples)
        return self

    def predict_raw(self, features: dict) -> tuple[float, float]:
        """`(height_m, uncertainty_m)` döner.

        Belirsizlik, ensemble'daki her ağacın kümülatif tahminine (staged
        predict) bakılarak, modelin kendi öğrenme eğrisindeki son birkaç
        aşama arasındaki varyanstan türetilir — tam bir Bayesian aralık
        değildir ama eğitim-artığı sabit değerinden daha girdiye-duyarlı
        bir belirsizlik sinyali verir.
        """
        if not self.is_trained:
            raise ModelNotTrainedError("SklearnHeightModel henuz fit() edilmedi")
        vec = [_feature_vector(features)[1:]]
        staged = list(self._model.staged_predict(vec))  # type: ignore[union-attr]
        final_height = float(staged[-1][0])
        # Son %20'lik aşamadaki tahmin dalgalanması -> girdiye özgü belirsizlik.
        tail = [float(s[0]) for s in staged[-max(2, len(staged) // 5) :]]
        local_spread = statistics.pstdev(tail) if len(tail) > 1 else 0.0
        uncertainty = max(self._train_residual_std, local_spread)
        return max(2.0, final_height), uncertainty

    def predict(self, features: dict) -> dict:
        height, uncertainty = self.predict_raw(features)
        return {"height_m": round(height, 2), "uncertainty_m": round(uncertainty, 2)}


def train_default_sklearn_model(
    n_samples: int = 400,
    seed: int = 42,
) -> SklearnHeightModel:
    """Sentetik veri setiyle (aynı `height_model.generate_synthetic_training_set`)
    varsayılan bir `SklearnHeightModel` eğitir."""
    _require_sklearn()
    samples, targets = generate_synthetic_training_set(n_samples=n_samples, seed=seed)
    return SklearnHeightModel().fit(samples, targets)


def as_ml_assisted_predictor(model: SklearnHeightModel) -> MLAssistedHeightPredictor:
    """`SklearnHeightModel`'i `MLAssistedHeightPredictor`'a takılabilecek
    hale getiren ince köprü — `HeightRegressionModel` ile aynı
    `predict_raw()` arayüzüne uyduğu için doğrudan enjekte edilebilir."""
    return MLAssistedHeightPredictor(model=model)  # type: ignore[arg-type]


@dataclass(slots=True)
class SklearnBenchmarkReport:
    n_train: int
    n_test: int
    heuristic_mae: float
    stdlib_ols_mae: float
    sklearn_mae: float

    @property
    def sklearn_vs_heuristic_improvement(self) -> float:
        if self.heuristic_mae <= 0:
            return 0.0
        return (self.heuristic_mae - self.sklearn_mae) / self.heuristic_mae

    @property
    def sklearn_vs_stdlib_ols_improvement(self) -> float:
        if self.stdlib_ols_mae <= 0:
            return 0.0
        return (self.stdlib_ols_mae - self.sklearn_mae) / self.stdlib_ols_mae


def benchmark_sklearn_vs_heuristic(
    n_train: int = 400,
    n_test: int = 150,
    seed: int = 7,
) -> SklearnBenchmarkReport:
    """Aynı sentetik veri/held-out test seti üzerinde üç yaklaşımı
    karşılaştırır: `HeuristicPredictor`, stdlib `HeightRegressionModel`
    (OLS) ve `SklearnHeightModel` (gradient boosting).

    A4 kabul kriterinin genişletilmiş hali: gerçek bir ML kütüphanesinin
    (doğrusal olmayan ilişkileri yakalayabilen) stdlib doğrusal regresyona
    göre ek katkısı ölçülebilir şekilde raporlanır.
    """
    _require_sklearn()
    from .building_analyzer import HeuristicPredictor
    from .height_model import HeightRegressionModel

    train_samples, train_targets = generate_synthetic_training_set(n_samples=n_train, seed=seed)
    test_samples, test_targets = generate_synthetic_training_set(n_samples=n_test, seed=seed + 1)

    heuristic = HeuristicPredictor()
    ols_model = HeightRegressionModel.fit(train_samples, train_targets)
    ols_predictor = MLAssistedHeightPredictor(model=ols_model)
    sk_model = SklearnHeightModel().fit(train_samples, train_targets)
    sk_predictor = as_ml_assisted_predictor(sk_model)

    def _mae(predictor) -> float:
        errors = [
            abs(predictor.predict(s)["height_m"] - y) for s, y in zip(test_samples, test_targets)
        ]
        return sum(errors) / len(errors)

    return SklearnBenchmarkReport(
        n_train=n_train,
        n_test=n_test,
        heuristic_mae=_mae(heuristic),
        stdlib_ols_mae=_mae(ols_predictor),
        sklearn_mae=_mae(sk_predictor),
    )


__all__ = [
    "SklearnBackendUnavailable",
    "SklearnHeightModel",
    "SklearnBenchmarkReport",
    "is_available",
    "sklearn_version",
    "train_default_sklearn_model",
    "as_ml_assisted_predictor",
    "benchmark_sklearn_vs_heuristic",
]
