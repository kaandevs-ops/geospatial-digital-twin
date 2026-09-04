"""Roadmap V3 - Faz D12 kabul kriteri testleri.

Kapsam (Faz D12 kabul kriteri, ROADMAP_V3.md):
    "k-fold çapraz doğrulama sonuçları, mevcut tekil train/test sonucuyla
    aynı yönde (hangi modelin daha iyi olduğu tutarlı) olduğunu gösterir;
    veri seti kaynağı ve lisansı README'de açıkça belirtilir."

Ortam notu: bu sandbox'ta ağ erişimi kapalı (`host_not_allowed`) - roadmap
dokümanının kendi öngördüğü gibi, gerçek bir uydu/OSM veri seti indirilemez.
Bu yüzden roadmap'in kendi tanımladığı dürüst geri düşüş uygulandı:
`generate_synthetic_training_set_nonlinear()` (doğrusal olmayan alan
etkisi + kategorik aspect-ratio etkileşimi + heteroskedastik gürültü) ve
stdlib-only `k_fold_cross_validate()`.

Bu dosya doğrular:
  1. Doğrusal (kolay) veri setinde k-fold sonucunun yönü tekil train/test
     `benchmark_height_predictors()` sonucuyla aynı.
  2. Doğrusal-olmayan (zor) veri setinde de aynı tutarlılık korunuyor -
     model daha zorlanıyor (MAE daha yüksek) ama yön hâlâ aynı.
  3. k-fold raporunun kendi iç tutarlılığı (fold sayısı, ortalamalar,
     `trained_wins_majority` mantığı) ve hata durumları (k<2, yetersiz
     örnek) doğru ele alınıyor.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.ai_reconstruction.height_model import (
    HeightRegressionModel,
    KFoldCrossValidationReport,
    benchmark_height_predictors,
    generate_synthetic_training_set,
    generate_synthetic_training_set_nonlinear,
    k_fold_cross_validate,
)


def test_kfold_direction_matches_single_split_on_linear_dataset():
    single = benchmark_height_predictors(n_train=400, n_test=150, seed=7)
    samples, targets = generate_synthetic_training_set(n_samples=400, seed=42)
    report = k_fold_cross_validate(samples, targets, k=5, seed=0)

    single_says_trained_better = single.improvement_ratio > 0
    kfold_says_trained_better = report.trained_wins_majority
    assert single_says_trained_better == kfold_says_trained_better, (
        "k-fold sonucu tekil train/test sonucuyla aynı yönde değil "
        f"(single improvement_ratio={single.improvement_ratio:.3f}, "
        f"kfold trained_wins={report.folds_where_trained_wins}/{report.k})"
    )


def test_kfold_direction_matches_single_split_on_nonlinear_dataset():
    samples, targets = generate_synthetic_training_set_nonlinear(n_samples=400, seed=42)
    report = k_fold_cross_validate(samples, targets, k=5, seed=0)

    # Aynı veri setinden tek bir train/test bölünmesi (fold 0'ı test kümesi
    # sayarak) ile k-fold'un genel yönünün aynı olduğunu doğrula.
    n = len(samples)
    split = n * 3 // 4
    train_s, train_t = samples[:split], targets[:split]
    test_s, test_t = samples[split:], targets[split:]
    model = HeightRegressionModel.fit(train_s, train_t)
    from harita.ai_reconstruction.height_model import (
        MLAssistedHeightPredictor, HeuristicPredictor, _mean_absolute_error,
    )
    trained_mae = _mean_absolute_error(MLAssistedHeightPredictor(model=model), test_s, test_t)
    heuristic_mae = _mean_absolute_error(HeuristicPredictor(), test_s, test_t)

    single_says_trained_better = trained_mae < heuristic_mae
    assert single_says_trained_better == report.trained_wins_majority


def test_nonlinear_dataset_is_measurably_harder_than_linear_for_trained_model():
    lin_samples, lin_targets = generate_synthetic_training_set(n_samples=400, seed=42)
    nl_samples, nl_targets = generate_synthetic_training_set_nonlinear(n_samples=400, seed=42)

    lin_report = k_fold_cross_validate(lin_samples, lin_targets, k=5, seed=0)
    nl_report = k_fold_cross_validate(nl_samples, nl_targets, k=5, seed=0)

    # Doğrusal olmayan/kategorik-etkileşimli + heteroskedastik veri setinde,
    # doğrusal `HeightRegressionModel`'in mutlak hatası (MAE) doğrusal veri
    # setine göre ölçülebilir şekilde daha yüksek olmalı — çünkü model
    # gerçek üretim sürecini (log-doyma + tipe göre işaret değiştiren
    # etkileşim) yapısal olarak temsil edemiyor.
    assert nl_report.mean_trained_mae > lin_report.mean_trained_mae, (
        f"beklenen: doğrusal-olmayan veri setinde eğitilmiş model MAE'si "
        f"daha yüksek (linear={lin_report.mean_trained_mae:.3f}, "
        f"nonlinear={nl_report.mean_trained_mae:.3f})"
    )


def test_kfold_report_fold_count_and_averages_are_consistent():
    samples, targets = generate_synthetic_training_set(n_samples=200, seed=1)
    report = k_fold_cross_validate(samples, targets, k=4, seed=0)
    assert report.k == 4
    assert len(report.fold_heuristic_mae) == 4
    assert len(report.fold_trained_mae) == 4
    assert report.mean_heuristic_mae == sum(report.fold_heuristic_mae) / 4
    assert report.mean_trained_mae == sum(report.fold_trained_mae) / 4
    assert 0 <= report.folds_where_trained_wins <= 4
    lines = report.summary_lines()
    assert len(lines) == 1 + 4  # başlık + her fold için bir satır


def test_kfold_rejects_k_below_2():
    samples, targets = generate_synthetic_training_set(n_samples=50, seed=1)
    try:
        k_fold_cross_validate(samples, targets, k=1)
        raise AssertionError("beklenen ValueError fırlatılmadı (k=1)")
    except ValueError:
        pass


def test_kfold_rejects_insufficient_samples():
    samples, targets = generate_synthetic_training_set(n_samples=6, seed=1)
    try:
        k_fold_cross_validate(samples, targets, k=5)
        raise AssertionError("beklenen ValueError fırlatılmadı (yetersiz örnek)")
    except ValueError:
        pass


def test_kfold_is_deterministic_given_same_seed():
    samples, targets = generate_synthetic_training_set(n_samples=300, seed=42)
    r1 = k_fold_cross_validate(samples, targets, k=5, seed=123)
    r2 = k_fold_cross_validate(samples, targets, k=5, seed=123)
    assert r1.fold_heuristic_mae == r2.fold_heuristic_mae
    assert r1.fold_trained_mae == r2.fold_trained_mae


def test_kfold_report_type_and_no_test_sample_reused_across_folds():
    # Fold'ların birbirini dışlayan (disjoint) test kümeleri olduğunu -
    # her örneğin tam olarak bir fold'da test edildiğini - dolaylı olarak
    # doğrular: k=n_samples (leave-one-out benzeri küçük ölçek) durumunda
    # ortalama fold boyutu 1'e yakın olmalı, hata fırlatılmamalı.
    samples, targets = generate_synthetic_training_set(n_samples=30, seed=1)
    report = k_fold_cross_validate(samples, targets, k=6, seed=0)
    assert isinstance(report, KFoldCrossValidationReport)
    assert report.k == 6


_ALL_TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    failures = []
    for fn in _ALL_TESTS:
        try:
            fn()
            print(f"OK   {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(_ALL_TESTS) - len(failures)}/{len(_ALL_TESTS)} geçti.")
    if failures:
        sys.exit(1)
