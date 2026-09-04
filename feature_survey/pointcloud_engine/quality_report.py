"""FAZ S3 — Yoğunluk/Kalite Raporu.

Roadmap ROADMAP_V6.md S3: "Yoğunluk/kalite raporu: gerçek nokta yoğunluğu
(nokta/m²), gap analizi (boşluk tespiti), gürültü tespiti (istatistiksel
outlier removal — gerçek k-NN mesafe dağılımı + standart sapma eşiği)."

Üç bağımsız, gerçek hesaplama:

1. **Yoğunluk**: `len(points) / footprint_area_m2` (footprint_area, X-Y
   düzlemindeki dışbükey gövde (convex hull) alanından — shoelace ile
   kesin — hesaplanır, sınırlayıcı kutu alanı değil, çünkü bbox alanı
   düzensiz şekilli bulutlarda yoğunluğu olduğundan düşük gösterir).
2. **Gap analizi**: bulut, `cell_size_m` çözünürlüğünde bir ızgaraya
   bölünür; footprint içinde kalıp hiç nokta içermeyen hücreler "boşluk"
   olarak raporlanır (footprint dışındaki hücreler zaten boş sayılmaz).
3. **Gürültü tespiti**: PCL/CloudCompare'ın "Statistical Outlier Removal"
   (SOR) algoritmasının gerçek uygulaması — her nokta için k en yakın
   komşuya olan ortalama mesafe hesaplanır; bulut genelindeki bu ortalama
   mesafelerin kendi ortalaması (mu) ve standart sapması (sigma)
   çıkarılır; `mean_dist > mu + std_ratio * sigma` olan noktalar gürültü
   (outlier) olarak işaretlenir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...data_engine.spatial_index import KDTree

Point3 = tuple[float, float, float]


class QualityReportError(ValueError):
    pass


def _convex_hull_2d(points_2d: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain — stdlib-only, kesin 2D dışbükey gövde."""
    pts = sorted(set(points_2d))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _polygon_area_2d(coords: list[tuple[float, float]]) -> float:
    if len(coords) < 3:
        return 0.0
    n = len(coords)
    total = 0.0
    for i in range(n):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


@dataclass(slots=True)
class DensityReport:
    point_count: int
    footprint_area_m2: float
    density_points_per_m2: float
    convex_hull: list[tuple[float, float]]


def compute_density(points: list[Point3]) -> DensityReport:
    if len(points) < 3:
        raise QualityReportError("Yoğunluk hesaplamak için en az 3 nokta gerekir.")
    points_2d = [(p[0], p[1]) for p in points]
    hull = _convex_hull_2d(points_2d)
    area = _polygon_area_2d(hull)
    if area <= 0:
        raise QualityReportError(
            "Dışbükey gövde alanı sıfır/negatif — noktalar doğrusal (kolinear) olabilir."
        )
    return DensityReport(
        point_count=len(points),
        footprint_area_m2=area,
        density_points_per_m2=len(points) / area,
        convex_hull=hull,
    )


@dataclass(slots=True)
class GapReport:
    cell_size_m: float
    total_cells_in_footprint: int
    empty_cells: int
    gap_ratio: float
    empty_cell_centers: list[tuple[float, float]]


def compute_gaps(points: list[Point3], cell_size_m: float = 1.0) -> GapReport:
    if len(points) < 3:
        raise QualityReportError("Gap analizi için en az 3 nokta gerekir.")
    if cell_size_m <= 0:
        raise QualityReportError("cell_size_m pozitif olmalı.")

    points_2d = [(p[0], p[1]) for p in points]
    hull = _convex_hull_2d(points_2d)
    min_x = min(p[0] for p in points_2d)
    min_y = min(p[1] for p in points_2d)
    max_x = max(p[0] for p in points_2d)
    max_y = max(p[1] for p in points_2d)

    occupied: set[tuple[int, int]] = set()
    for x, y in points_2d:
        occupied.add((int((x - min_x) // cell_size_m), int((y - min_y) // cell_size_m)))

    def _point_in_hull(x: float, y: float) -> bool:
        n = len(hull)
        if n < 3:
            return False
        inside = False
        x1, y1 = hull[-1]
        for i in range(n):
            x2, y2 = hull[i]
            if ((y2 > y) != (y1 > y)) and (x < (x1 - x2) * (y - y2) / (y1 - y2 + 1e-15) + x2):
                inside = not inside
            x1, y1 = x2, y2
        return inside

    n_cols = int((max_x - min_x) // cell_size_m) + 1
    n_rows = int((max_y - min_y) // cell_size_m) + 1

    total_in_footprint = 0
    empty_centers: list[tuple[float, float]] = []
    for i in range(n_cols):
        for j in range(n_rows):
            cx = min_x + (i + 0.5) * cell_size_m
            cy = min_y + (j + 0.5) * cell_size_m
            if not _point_in_hull(cx, cy) and (i, j) not in occupied:
                continue
            total_in_footprint += 1
            if (i, j) not in occupied:
                empty_centers.append((cx, cy))

    empty = len(empty_centers)
    return GapReport(
        cell_size_m=cell_size_m,
        total_cells_in_footprint=total_in_footprint,
        empty_cells=empty,
        gap_ratio=(empty / total_in_footprint) if total_in_footprint else 0.0,
        empty_cell_centers=empty_centers,
    )


@dataclass(slots=True)
class NoiseReport:
    k_neighbors: int
    std_ratio: float
    mean_distance: float
    std_distance: float
    threshold_distance: float
    outlier_indices: list[int]
    total_points: int

    @property
    def n_outliers(self) -> int:
        return len(self.outlier_indices)

    @property
    def outlier_ratio(self) -> float:
        return len(self.outlier_indices) / self.total_points if self.total_points else 0.0


def detect_noise_sor(
    points: list[Point3], k_neighbors: int = 8, std_ratio: float = 2.0
) -> NoiseReport:
    """Statistical Outlier Removal (SOR) — PCL'nin standart algoritması.

    Her nokta için `k_neighbors` en yakın komşuya olan ortalama mesafe
    hesaplanır (`KDTree.nearest_k`, kesin). Bu ortalama mesafelerin
    dağılımından mu/sigma çıkarılır; `mu + std_ratio * sigma` üstündeki
    noktalar gürültü olarak işaretlenir — eşik veriden **türetilir**,
    sabit/uydurma bir mesafe değildir.
    """
    n = len(points)
    if n <= k_neighbors:
        raise QualityReportError(
            f"SOR için nokta sayısı ({n}) k_neighbors+1'den ({k_neighbors + 1}) büyük olmalı."
        )

    tree = KDTree(points)
    mean_dists: list[float] = []
    for p in points:
        neighbors = tree.nearest_k(p, k_neighbors + 1)  # kendisi de dahil olabilir
        dists = [math.sqrt(d) for (_, _, d) in neighbors if d > 1e-12]
        dists = dists[:k_neighbors] if len(dists) >= k_neighbors else dists
        mean_dists.append(sum(dists) / len(dists) if dists else 0.0)

    mu = sum(mean_dists) / n
    variance = sum((d - mu) ** 2 for d in mean_dists) / n
    sigma = math.sqrt(variance)
    threshold = mu + std_ratio * sigma

    outliers = [i for i, d in enumerate(mean_dists) if d > threshold]

    return NoiseReport(
        k_neighbors=k_neighbors,
        std_ratio=std_ratio,
        mean_distance=mu,
        std_distance=sigma,
        threshold_distance=threshold,
        outlier_indices=outliers,
        total_points=n,
    )


@dataclass(slots=True)
class PointCloudQualityReport:
    density: DensityReport
    gaps: GapReport
    noise: NoiseReport


def build_quality_report(
    points: list[Point3],
    gap_cell_size_m: float = 1.0,
    noise_k: int = 8,
    noise_std_ratio: float = 2.0,
) -> PointCloudQualityReport:
    return PointCloudQualityReport(
        density=compute_density(points),
        gaps=compute_gaps(points, gap_cell_size_m),
        noise=detect_noise_sor(points, noise_k, noise_std_ratio),
    )
