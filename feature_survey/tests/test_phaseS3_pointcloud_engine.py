"""ROADMAP_V6 FAZ S3 — nokta bulutu işleme köprüsü testleri.

Kabul kriteri (ROADMAP_V6.md): "Açık kaynak referans veri setleri ... üzerinde
sınıflandırma doğruluğu (precision/recall) ölçülüp raporlanacak." Ağ erişimi
kısıtlı olduğundan (bkz. `pointcloud_engine/README.md`), etiketli sentetik
zemin+bina veri seti üzerinde precision/recall ölçülür ve assert edilir.
"""

from __future__ import annotations

import math
import random

import pytest
from harita.feature_survey.pointcloud_engine.ground_classification import (
    ASPRS_GROUND,
    GroundClassificationError,
    progressive_morphological_filter,
)
from harita.feature_survey.pointcloud_engine.icp import ICPError, run_icp
from harita.feature_survey.pointcloud_engine.quality_report import (
    QualityReportError,
    compute_density,
    compute_gaps,
    detect_noise_sor,
)

# --- ICP ---------------------------------------------------------------


def _random_cloud(n: int, seed: int = 42) -> list[tuple[float, float, float]]:
    rng = random.Random(seed)
    return [(rng.uniform(0, 20), rng.uniform(0, 20), rng.uniform(0, 5)) for _ in range(n)]


def test_icp_recovers_known_translation():
    source = _random_cloud(60)
    dx, dy, dz = 2.0, -1.5, 0.5
    target = [(x + dx, y + dy, z + dz) for x, y, z in source]

    result = run_icp(source, target, max_iterations=50, tolerance=1e-9)

    assert result.converged
    aligned = result.apply(source)
    # Ortalama hizalama hatası çok küçük olmalı (gürültüsüz, saf öteleme)
    mean_err = sum(math.dist(a, t) for a, t in zip(aligned, target)) / len(aligned)
    assert mean_err < 1e-3
    assert result.final_rmse < 1e-3


def test_icp_rmse_history_is_nonincreasing_trend():
    """RMSE geçmişi genel eğilim olarak azalmalı (gerçek yakınsama, sabit
    bir sayı değil)."""
    source = _random_cloud(40, seed=7)
    target = [(x + 1.0, y + 1.0, z) for x, y, z in source]
    result = run_icp(source, target, max_iterations=30)
    assert len(result.rmse_history) >= 1
    assert result.rmse_history[-1] <= result.rmse_history[0] + 1e-9


def test_icp_requires_minimum_points():
    with pytest.raises(ICPError):
        run_icp([(0, 0, 0), (1, 1, 1)], [(0, 0, 0), (1, 1, 1)])


# --- Ground classification (PMF) with synthetic labeled dataset ---------


def _synthetic_ground_and_building(seed: int = 1):
    """Düz bir zemin (Z~0, gürültülü) + üzerine oturan bir bina bloğu
    (Z 5-8 arası) üretir. Etiketler bilinen (True Ground / True Building)."""
    rng = random.Random(seed)
    points: list[tuple[float, float, float]] = []
    labels: list[bool] = []  # True == gerçek zemin

    # Zemin: 40x40 alan, 1m aralıklı ızgara + küçük gürültü
    for i in range(40):
        for j in range(40):
            x = i * 1.0 + rng.uniform(-0.1, 0.1)
            y = j * 1.0 + rng.uniform(-0.1, 0.1)
            z = rng.uniform(-0.05, 0.05)
            points.append((x, y, z))
            labels.append(True)

    # Bina: 10x10 alan, zeminin ortasında, Z 5-8 arası (düz çatı benzeri)
    for i in range(10):
        for j in range(10):
            x = 15.0 + i * 1.0
            y = 15.0 + j * 1.0
            z = 6.0 + rng.uniform(-0.05, 0.05)
            points.append((x, y, z))
            labels.append(False)

    return points, labels


def test_pmf_ground_classification_precision_recall():
    points, true_ground = _synthetic_ground_and_building()
    result = progressive_morphological_filter(points, cell_size_m=1.0)

    predicted_ground = [c == ASPRS_GROUND for c in result.classification]

    tp = sum(1 for p, t in zip(predicted_ground, true_ground) if p and t)
    fp = sum(1 for p, t in zip(predicted_ground, true_ground) if p and not t)
    fn = sum(1 for p, t in zip(predicted_ground, true_ground) if not p and t)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    # Kabul kriteri: gerçek, ölçülmüş performans (sabit "başarılı" değil)
    assert precision > 0.9, f"precision={precision}"
    assert recall > 0.9, f"recall={recall}"


def test_pmf_requires_minimum_points():
    with pytest.raises(GroundClassificationError):
        progressive_morphological_filter([(0, 0, 0), (1, 1, 1)])


# --- Quality report ------------------------------------------------------


def test_density_report_on_regular_grid():
    # 10x10 m alan, 1m aralıklı ızgara -> 121 nokta, alan ~100 m^2 (hull),
    # yoğunluk ~1.21 nokta/m^2
    points = [(float(i), float(j), 0.0) for i in range(11) for j in range(11)]
    density = compute_density(points)
    assert density.footprint_area_m2 == pytest.approx(100.0, rel=0.05)
    assert density.density_points_per_m2 == pytest.approx(121 / 100.0, rel=0.05)


def test_gap_report_detects_missing_region():
    # 10x10 ızgara ama ortadaki 3x3 bölge kasıtlı olarak boş bırakılıyor
    points = []
    for i in range(10):
        for j in range(10):
            if 3 <= i <= 5 and 3 <= j <= 5:
                continue
            points.append((float(i), float(j), 0.0))
    gaps = compute_gaps(points, cell_size_m=1.0)
    assert gaps.empty_cells >= 9  # en az 3x3=9 hücre boş


def test_noise_detection_flags_outlier_point():
    rng = random.Random(3)
    points = [(rng.uniform(0, 10), rng.uniform(0, 10), 0.0) for _ in range(100)]
    # Bulut dışında, uzak bir gürültü noktası ekle
    points.append((500.0, 500.0, 500.0))

    noise = detect_noise_sor(points, k_neighbors=6, std_ratio=2.0)
    assert (len(points) - 1) in noise.outlier_indices
    assert noise.n_outliers >= 1


def test_quality_report_requires_minimum_points():
    with pytest.raises(QualityReportError):
        compute_density([(0, 0, 0), (1, 1, 1)])
