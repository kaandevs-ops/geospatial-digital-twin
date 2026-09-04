"""
Trained Height Model
=====================

Roadmap V2 - A4 - "AI Reconstruction derinleştirme".

`AIBuildingAnalyzer`'ın varsayılan `HeuristicPredictor`'ı kural tabanlıdır.
Bu modül, `Predictor` Protocol'üne bağlanabilecek **gerçekten eğitilen**
(training verisinden katsayı çıkaran) hafif bir doğrusal regresyon modeli
sağlar — harici bir ML kütüphanesi (scikit-learn/ONNX) gerektirmeden,
yalnızca stdlib ile en-küçük-kareler (ordinary least squares) çözümü.

Neden scikit-learn/ONNX değil: roadmap'in "sıfır harici bağımlılık
zorunluluğu yok ama minimalizm esas" ilkesi + bu ortamda gerçek uydu/ortofoto
görüntüsünden bina yüksekliği etiketli bir veri seti (ör. YOLO/DSM tabanlı)
mevcut değil. Bu nedenle:

    1. `HeightRegressionModel` — genel amaçlı, gerçekten `fit()` edilen
       (katsayıları veriden öğrenen) çok değişkenli doğrusal regresyon.
       API'si, ileride gerçek bir etiketli veri setiyle (veya bir
       ONNX/sklearn sarmalayıcısıyla) *yer değiştirilebilecek* şekilde
       tasarlanmıştır — `Predictor` Protocol'üne uyar.
    2. `generate_synthetic_training_set()` — footprint geometrisinden
       (alan, çevre, aspect ratio, kompaktlık) + bina tipinden fiziksel
       olarak motive edilmiş bir kural + gürültü ile üretilen sentetik
       etiketli veri. **Gerçek uydu verisi değildir** — bu açıkça
       belgelenmiştir (bkz. modül sonundaki dürüst sınırlama notu).
       Roadmap'in "Etiketli test seti üzerinde heuristic'e göre ölçülebilir
       doğruluk artışı" kabul kriterini, gerçek veri erişilebilir olana
       kadar bu sentetik-ama-tutarlı proxy ile karşılar.
    3. `MLAssistedHeightPredictor` — `HeuristicPredictor`'ı sarar; eğitilmiş
       bir `HeightRegressionModel` varsa yükseklik/kat sayısı/güven/
       belirsizlik alanlarını onunla override eder, yoksa saf heuristic'e
       düşer (roadmap'in "yoksa heuristic'e düşer" gereksinimi).
    4. `benchmark_height_predictors()` — held-out test seti üzerinde
       heuristic vs. eğitilmiş model MAE karşılaştırması (kabul kriteri
       raporu, bkz. `AI_RECONSTRUCTION_BENCHMARK.md`).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .building_analyzer import HeuristicPredictor, _TYPE_STATS
from .predictor import Predictor

FEATURE_NAMES: tuple[str, ...] = (
    "bias", "area_m2", "perimeter_m", "aspect_ratio", "compactness",
)


def _compactness(area_m2: float, perimeter_m: float) -> float:
    if perimeter_m <= 0:
        return 0.0
    return (4.0 * math.pi * area_m2) / (perimeter_m ** 2)


def _feature_vector(features: dict) -> list[float]:
    area = float(features.get("area_m2", 0.0))
    perimeter = float(features.get("perimeter_m", 0.0))
    aspect = float(features.get("aspect_ratio", 1.0))
    compactness = _compactness(area, perimeter)
    return [1.0, area, perimeter, aspect, compactness]


# ---------------------------------------------------------------------- #
# Stdlib-only en-küçük-kareler (normal denklemler + Gauss-Jordan)
# ---------------------------------------------------------------------- #

def _solve_linear_system(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """`matrix @ x = rhs` sistemini Gauss-Jordan eleme ile çözer (kare
    matris varsayımıyla; numpy'siz, sadece stdlib)."""
    n = len(matrix)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]

    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            continue
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot_val = aug[col][col]
        aug[col] = [v / pivot_val for v in aug[col]]
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor != 0.0:
                aug[r] = [a - factor * b for a, b in zip(aug[r], aug[col])]

    return [aug[i][n] for i in range(n)]


def _ols_fit(rows: list[list[float]], targets: list[float]) -> list[float]:
    """`rows` (her satır bir feature-vector) ve `targets` (etiket) verilen
    çok değişkenli doğrusal regresyon: normal denklemler `(XtX) w = Xt y`
    çözülerek katsayı vektörü `w` döner."""
    n_features = len(rows[0])
    xtx = [[0.0] * n_features for _ in range(n_features)]
    xty = [0.0] * n_features
    for row, y in zip(rows, targets):
        for i in range(n_features):
            xty[i] += row[i] * y
            for j in range(n_features):
                xtx[i][j] += row[i] * row[j]
    # Sayısal kararlılık için hafif ridge (L2) düzenlileştirme.
    for i in range(n_features):
        xtx[i][i] += 1e-6
    return _solve_linear_system(xtx, xty)


@dataclass(slots=True)
class HeightRegressionModel:
    """Roadmap V2 - A4: `fit()` ile veriden öğrenen doğrusal regresyon
    modeli. `Predictor` Protocol'üne uyar (`predict(features) -> dict`)."""

    coefficients: list[float] = field(default_factory=list)
    residual_std: float = 0.0
    n_training_samples: int = 0

    @property
    def is_trained(self) -> bool:
        return bool(self.coefficients) and self.n_training_samples > 0

    @classmethod
    def fit(cls, samples: list[dict], targets: list[float]) -> "HeightRegressionModel":
        if len(samples) != len(targets):
            raise ValueError("samples ve targets ayni uzunlukta olmali")
        if len(samples) < len(FEATURE_NAMES):
            raise ValueError(
                f"en az {len(FEATURE_NAMES)} egitim ornegi gerekli, {len(samples)} verildi"
            )
        rows = [_feature_vector(s) for s in samples]
        coefficients = _ols_fit(rows, targets)

        residuals = [
            targets[i] - sum(c * v for c, v in zip(coefficients, rows[i]))
            for i in range(len(rows))
        ]
        mean_sq = sum(r ** 2 for r in residuals) / len(residuals)
        residual_std = math.sqrt(mean_sq)

        return cls(coefficients=coefficients, residual_std=residual_std, n_training_samples=len(samples))

    def predict_raw(self, features: dict) -> tuple[float, float]:
        """`(height_m, uncertainty_m)` döner. Eğitilmemiş modelde
        `ModelNotTrainedError` fırlatır."""
        if not self.is_trained:
            raise ModelNotTrainedError("HeightRegressionModel henuz fit() edilmedi")
        vec = _feature_vector(features)
        height = sum(c * v for c, v in zip(self.coefficients, vec))
        return max(2.0, height), self.residual_std

    def predict(self, features: dict) -> dict:
        """`Predictor` Protocol uyumluluğu — yalnızca yükseklik/belirsizlik
        döner (kat sayısı/güven `MLAssistedHeightPredictor` tarafından
        türetilir)."""
        height, uncertainty = self.predict_raw(features)
        return {"height_m": round(height, 2), "uncertainty_m": round(uncertainty, 2)}


class ModelNotTrainedError(RuntimeError):
    """`HeightRegressionModel.predict()` eğitilmeden önce çağrılırsa."""


# ---------------------------------------------------------------------- #
# Hibrit predictor: eğitilmiş model varsa kullanır, yoksa heuristic'e düşer
# ---------------------------------------------------------------------- #

class MLAssistedHeightPredictor:
    """`Predictor` Protocol'ü — `HeuristicPredictor`'ı sarar; opsiyonel
    `HeightRegressionModel` eğitilmişse yükseklik/kat-sayısı/güven/
    belirsizlik alanlarını onunla override eder.

    Roadmap V2 - A4 gereksinimi: "hafif bir eğitilmiş model, opsiyonel,
    yoksa heuristic'e düşer" — burada model `None` ya da eğitilmemişse
    davranış tam olarak saf `HeuristicPredictor` ile aynıdır (regresyon
    testi: bkz. `test_a4_ai_reconstruction_deepening.py`).
    """

    def __init__(self, model: HeightRegressionModel | None = None) -> None:
        self._heuristic = HeuristicPredictor()
        self._model = model

    @property
    def model(self) -> HeightRegressionModel | None:
        return self._model

    def predict(self, features: dict) -> dict:
        base = self._heuristic.predict(features)
        base.setdefault("uncertainty_m", base["height_m"] * (0.35 if base["confidence"] < 0.6 else 0.15))

        if self._model is None or not self._model.is_trained:
            return base

        height, uncertainty = self._model.predict_raw(features)
        floor_h = _TYPE_STATS.get(
            (features.get("building_type") or "_default").lower(), _TYPE_STATS["_default"]
        )["floor_h"]
        floor_count = max(1, round(height / floor_h))

        # Kalibrasyon: göreli belirsizlik (uncertainty / height) düştükçe
        # güven artar; asla heuristic'in ürettiği tabandan düşük olmaz
        # (eğitilmiş model, ek veri katkısı sağladığı için en kötü ihtimalle
        # heuristic kadar güvenilir kabul edilir).
        relative_uncertainty = uncertainty / height if height > 0 else 1.0
        calibrated_confidence = max(base["confidence"], 1.0 / (1.0 + relative_uncertainty))

        base["height_m"] = round(height, 2)
        base["floor_count"] = floor_count
        base["confidence"] = round(min(0.98, calibrated_confidence), 3)
        base["uncertainty_m"] = round(uncertainty, 2)
        return base


# ---------------------------------------------------------------------- #
# Sentetik eğitim/test verisi (gerçek uydu verisi DEĞİLDİR - bkz. modül
# başındaki dürüst sınırlama notu)
# ---------------------------------------------------------------------- #

def generate_synthetic_training_set(
    n_samples: int = 400, seed: int = 42,
) -> tuple[list[dict], list[float]]:
    """Footprint geometrisinden + bina tipinden fiziksel olarak motive
    edilmiş bir kural (`gercek_yukseklik = f(alan, cevre, tip) + gurultu`)
    ile sentetik etiketli veri üretir.

    **Gerçek uydu/ortofoto verisi değildir.** Bu ortamda erişilebilir
    etiketli bir bina-yüksekliği veri seti bulunmadığından, modelin
    "veriden gerçekten öğrenip öğrenmediğini" ve kalibrasyon/belirsizlik
    mekanizmasını doğrulamak için tutarlı, tekrarlanabilir bir proxy
    olarak kullanılır. Gerçek veri sağlandığında `fit()` doğrudan
    kullanılabilir; arayüz değişmez.
    """
    rng = random.Random(seed)
    building_types = list(_TYPE_STATS.keys())
    building_types.remove("_default")

    samples: list[dict] = []
    targets: list[float] = []
    for _ in range(n_samples):
        bt = rng.choice(building_types)
        floor_h = _TYPE_STATS[bt]["floor_h"]
        side_a = rng.uniform(6.0, 60.0)
        side_b = rng.uniform(6.0, 60.0)
        area = side_a * side_b
        perimeter = 2 * (side_a + side_b)
        aspect_ratio = max(side_a, side_b) / min(side_a, side_b)

        # "Gerçek" kat sayısı: alanla hafifçe artan + tipe bağlı taban +
        # gürültü (log-normal benzeri kaba yaklaşım).
        base_floors = {
            "apartments": 6, "house": 2, "office": 8, "commercial": 2,
            "industrial": 1, "warehouse": 1, "hospital": 5, "school": 3,
        }.get(bt, 3)
        floor_count = max(1, round(base_floors + (area ** 0.5) / 25 + rng.gauss(0, 1.2)))
        true_height = floor_count * floor_h + rng.gauss(0, floor_h * 0.25)
        true_height = max(2.5, true_height)

        samples.append({
            "area_m2": area, "perimeter_m": perimeter, "aspect_ratio": aspect_ratio,
            "building_type": bt,
        })
        targets.append(true_height)

    return samples, targets


def generate_synthetic_training_set_nonlinear(
    n_samples: int = 400, seed: int = 42,
) -> tuple[list[dict], list[float]]:
    """Roadmap V3 - Faz D12: `generate_synthetic_training_set()`'in daha
    zorlu bir varyantı.

    Ağ erişimi bu ortamda kapalı olduğundan (`host_not_allowed` - gerçek
    bir uydu/ortofoto veri seti indirilemiyor), roadmap'in kendi
    öngördüğü dürüst geri düşüş (fallback) uygulanır: doğrusal `HeightRegressionModel`'in
    *gerçekten* zorlanacağı, doğrusal olmayan bileşenler + kategorik
    etkileşim + heteroskedastik (alana bağlı büyüyen) gürültü içeren bir
    üretici.

    Doğrusal versiyondan farkları:
      1. **Kategorik etkileşim:** `aspect_ratio`'nun yükseklik üzerindeki
         etkisi bina tipine göre işaret değiştirir (ofis binalarında ince/
         uzun taban daha fazla kat = daha yüksek; konutta tam tersi bir
         eğilim) — tek bir global `aspect_ratio` katsayısıyla yakalanamaz.
      2. **Doğrusal olmayan terim:** yükseklik `sqrt(area)` yerine
         `area`'nın logaritmasıyla ölçekleniyor (küçük binalarda hızlı,
         büyük binalarda yavaşlayan artış — gerçek şehir dokusunda
         gözlenen doyma etkisine benzer).
      3. **Heteroskedastik gürültü:** gürültü standart sapması alanla
         birlikte büyüyor (büyük binalarda yükseklik tahmini doğası gereği
         daha belirsizdir) — sabit varyanslı gürültü yerine.

    Amaç: `HeightRegressionModel`'in (doğrusal) bu veri setinde
    `generate_synthetic_training_set()`'e göre görece daha az kazanç
    sağladığını, ama yine de MAE karşılaştırmasının k-fold CV'de tutarlı
    kaldığını göstermek (bkz. `k_fold_cross_validate`).
    """
    rng = random.Random(seed)
    building_types = list(_TYPE_STATS.keys())
    building_types.remove("_default")

    # Bina tipine göre aspect_ratio etkisinin işareti (kategorik etkileşim).
    _aspect_sign = {
        "office": +1.0, "commercial": +1.0, "warehouse": +1.0,
        "house": -1.0, "apartments": -0.3, "hospital": -0.2,
        "school": -0.4, "industrial": +0.6,
    }

    samples: list[dict] = []
    targets: list[float] = []
    for _ in range(n_samples):
        bt = rng.choice(building_types)
        floor_h = _TYPE_STATS[bt]["floor_h"]
        side_a = rng.uniform(6.0, 60.0)
        side_b = rng.uniform(6.0, 60.0)
        area = side_a * side_b
        perimeter = 2 * (side_a + side_b)
        aspect_ratio = max(side_a, side_b) / min(side_a, side_b)

        base_floors = {
            "apartments": 6, "house": 2, "office": 8, "commercial": 2,
            "industrial": 1, "warehouse": 1, "hospital": 5, "school": 3,
        }.get(bt, 3)

        # Doğrusal olmayan alan etkisi: log-doyma.
        area_effect = math.log1p(area) * 0.9

        # Kategorik etkileşim: aspect_ratio etkisi tipe göre işaret değiştirir.
        aspect_effect = _aspect_sign.get(bt, 0.0) * (aspect_ratio - 1.0) * 0.5

        # Heteroskedastik gürültü: std, alanla birlikte büyür (ama sınırlı).
        noise_std = 0.6 + min(area, 2000.0) * 0.004
        floor_count = max(
            1, round(base_floors + area_effect + aspect_effect + rng.gauss(0, noise_std))
        )
        true_height = floor_count * floor_h + rng.gauss(0, floor_h * 0.25)
        true_height = max(2.5, true_height)

        samples.append({
            "area_m2": area, "perimeter_m": perimeter, "aspect_ratio": aspect_ratio,
            "building_type": bt,
        })
        targets.append(true_height)

    return samples, targets


def train_default_height_model(
    n_samples: int = 400, seed: int = 42,
) -> HeightRegressionModel:
    """Sentetik veri setiyle varsayılan bir `HeightRegressionModel` eğitir."""
    samples, targets = generate_synthetic_training_set(n_samples=n_samples, seed=seed)
    return HeightRegressionModel.fit(samples, targets)


# ---------------------------------------------------------------------- #
# Kabul kriteri raporu: heuristic vs eğitilmiş model MAE karşılaştırması
# ---------------------------------------------------------------------- #

@dataclass(slots=True)
class HeightBenchmarkReport:
    n_train: int
    n_test: int
    heuristic_mae: float
    trained_mae: float

    @property
    def improvement_ratio(self) -> float:
        """Pozitif değer = eğitilmiş model heuristic'e göre ne kadar daha
        iyi (MAE azalması oranı)."""
        if self.heuristic_mae <= 0:
            return 0.0
        return (self.heuristic_mae - self.trained_mae) / self.heuristic_mae


def _mean_absolute_error(predictor: Predictor, samples: list[dict], targets: list[float]) -> float:
    errors = []
    for s, y_true in zip(samples, targets):
        result = predictor.predict(s)
        errors.append(abs(result["height_m"] - y_true))
    return sum(errors) / len(errors)


def benchmark_height_predictors(
    n_train: int = 400, n_test: int = 150, seed: int = 7,
) -> HeightBenchmarkReport:
    """Held-out test seti üzerinde saf `HeuristicPredictor` ile eğitilmiş
    `MLAssistedHeightPredictor`'ın MAE'sini karşılaştırır.

    Roadmap V2 - A4 kabul kriteri: "Etiketli test seti üzerinde
    heuristic'e göre ölçülebilir doğruluk artışı raporlanır."
    """
    train_samples, train_targets = generate_synthetic_training_set(n_samples=n_train, seed=seed)
    test_samples, test_targets = generate_synthetic_training_set(n_samples=n_test, seed=seed + 1)

    model = HeightRegressionModel.fit(train_samples, train_targets)
    trained_predictor = MLAssistedHeightPredictor(model=model)
    heuristic_predictor = HeuristicPredictor()

    heuristic_mae = _mean_absolute_error(heuristic_predictor, test_samples, test_targets)
    trained_mae = _mean_absolute_error(trained_predictor, test_samples, test_targets)

    return HeightBenchmarkReport(
        n_train=n_train, n_test=n_test,
        heuristic_mae=heuristic_mae, trained_mae=trained_mae,
    )


# ---------------------------------------------------------------------- #
# Roadmap V3 - Faz D12: k-fold çapraz doğrulama (stdlib-only)
# ---------------------------------------------------------------------- #

@dataclass(slots=True)
class KFoldCrossValidationReport:
    """`k_fold_cross_validate()` sonucu: her fold için ayrı MAE çifti +
    tekil train/test bölünmesiyle "aynı yönde" tutarlılık kontrolü."""

    k: int
    fold_heuristic_mae: list[float]
    fold_trained_mae: list[float]

    @property
    def mean_heuristic_mae(self) -> float:
        return sum(self.fold_heuristic_mae) / len(self.fold_heuristic_mae)

    @property
    def mean_trained_mae(self) -> float:
        return sum(self.fold_trained_mae) / len(self.fold_trained_mae)

    @property
    def folds_where_trained_wins(self) -> int:
        """Eğitilmiş modelin heuristic'ten daha düşük MAE verdiği fold sayısı."""
        return sum(
            1 for h, t in zip(self.fold_heuristic_mae, self.fold_trained_mae) if t < h
        )

    @property
    def trained_wins_majority(self) -> bool:
        """Eğitilmiş model fold'ların çoğunluğunda (>%50) heuristic'i geçer mi?
        Tekil train/test bölünmesinin şansa bağlı sonucu yerine, k-fold
        boyunca **tutarlı** bir yön olup olmadığını doğrudan yanıtlar —
        roadmap D12'nin kabul kriteri budur."""
        return self.folds_where_trained_wins > self.k / 2

    def summary_lines(self) -> list[str]:
        lines = [
            f"k={self.k} | ortalama heuristic MAE={self.mean_heuristic_mae:.3f}m "
            f"| ortalama eğitilmiş MAE={self.mean_trained_mae:.3f}m "
            f"| eğitilmiş model {self.folds_where_trained_wins}/{self.k} fold'da daha iyi",
        ]
        for i, (h, t) in enumerate(zip(self.fold_heuristic_mae, self.fold_trained_mae)):
            lines.append(f"  fold {i + 1}: heuristic={h:.3f}m  eğitilmiş={t:.3f}m")
        return lines


def k_fold_cross_validate(
    samples: list[dict], targets: list[float], k: int = 5, seed: int = 0,
) -> KFoldCrossValidationReport:
    """`samples`/`targets` üzerinde stdlib-only k-fold çapraz doğrulama:
    veri `k` eşit parçaya (fold) rastgele karıştırılıp bölünür; her
    fold sırayla test kümesi, kalan `k-1` fold birleşimi eğitim kümesi
    olarak kullanılır. Her fold için hem saf heuristic hem eğitilmiş
    `MLAssistedHeightPredictor`'ın MAE'si ayrı ayrı raporlanır.

    Roadmap V3 - Faz D12 kabul kriteri: "k-fold çapraz doğrulama sonuçları,
    mevcut tekil train/test sonucuyla aynı yönde (hangi modelin daha iyi
    olduğu tutarlı) olduğunu gösterir."
    """
    if k < 2:
        raise ValueError("k en az 2 olmalı")
    n = len(samples)
    if n < k * len(FEATURE_NAMES):
        raise ValueError(
            f"k-fold için yetersiz örnek: n={n}, k={k}, her fold'un eğitim "
            f"kümesi en az {len(FEATURE_NAMES)} örnek gerektirir"
        )

    indices = list(range(n))
    random.Random(seed).shuffle(indices)
    fold_indices = [indices[i::k] for i in range(k)]

    fold_heuristic_mae: list[float] = []
    fold_trained_mae: list[float] = []
    heuristic_predictor = HeuristicPredictor()

    for fold_i in range(k):
        test_idx = set(fold_indices[fold_i])
        train_samples = [samples[i] for i in range(n) if i not in test_idx]
        train_targets = [targets[i] for i in range(n) if i not in test_idx]
        test_samples = [samples[i] for i in fold_indices[fold_i]]
        test_targets = [targets[i] for i in fold_indices[fold_i]]

        model = HeightRegressionModel.fit(train_samples, train_targets)
        trained_predictor = MLAssistedHeightPredictor(model=model)

        fold_heuristic_mae.append(
            _mean_absolute_error(heuristic_predictor, test_samples, test_targets)
        )
        fold_trained_mae.append(
            _mean_absolute_error(trained_predictor, test_samples, test_targets)
        )

    return KFoldCrossValidationReport(
        k=k, fold_heuristic_mae=fold_heuristic_mae, fold_trained_mae=fold_trained_mae,
    )
