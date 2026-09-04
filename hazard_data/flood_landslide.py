"""Sel / Heyelan riski — roadmap iş fikri #2.

    "Sel/heyelan riski görselleştirme — terrain_engine + hazard verisiyle
    eğim/drenaj analizi (şu an terrain var ama hazard ile hiç
    birleşmemiş, yeni bağlanacak bir yön)."

`terrain_engine.FlowAccumulation` (D8 akış yönü + akümülasyon/drenaj) zaten
vardı ama hiçbir "risk" kavramıyla birleştirilmemişti. Bu modül tam olarak
o bağlantıyı kurar:

- **Eğim (slope)**: merkezi fark (central difference) ile her hücre için
  yüzde eğim. Dik yamaçlar (roadmap/jeoteknik literatürde yaygın kaba eşik:
  >~30-35% = önemli heyelan potansiyeli) heyelan alt-skorunu yükseltir.
- **Drenaj (flow accumulation)**: `FlowAccumulation.accumulate` çıktısı —
  çok sayıda yukarı-havza hücresinin suyunu toplayan (dere yatağı/vadi
  tabanı gibi) DÜŞÜK eğimli hücreler sel alt-skorunu yükseltir.
- **Yağış (opsiyonel)**: `climate_data.open_meteo_client` ile gerçek/son
  yağış verisi enjekte edilebilir; verilmezse o faktör "veri yok" olarak
  işaretlenip skora dahil edilmez (aynı `risk_scoring` ilkesi: eksik veri
  varsayımla doldurulmaz).

`risk_scoring.py` ile AYNI dürüstlük ilkesi: bu bir GÖSTERGE indeksidir,
resmi bir hidrolojik/jeoteknik etüdün yerine geçmez — her raporda açıkça
belirtilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..terrain_engine import FlowAccumulation, HeightmapGrid

DISCLAIMER = (
    "GÖSTERGE NİTELİĞİNDEDİR — resmi bir hidrolojik (sel) veya jeoteknik "
    "(heyelan/şev stabilitesi) etüdün YERİNE GEÇMEZ. Bağlayıcı bir karar "
    "için mutlaka yetkili bir jeoloji/inşaat mühendisinden resmi bir "
    "değerlendirme alınmalıdır."
)


class RiskLevel(str, Enum):
    LOW = "dusuk"
    MODERATE = "orta"
    HIGH = "yuksek"
    VERY_HIGH = "cok_yuksek"


def _level_from_index(index: float) -> RiskLevel:
    if index < 25:
        return RiskLevel.LOW
    if index < 50:
        return RiskLevel.MODERATE
    if index < 75:
        return RiskLevel.HIGH
    return RiskLevel.VERY_HIGH


@dataclass(frozen=True, slots=True)
class SlopeSample:
    slope_percent: float
    slope_degrees: float


def compute_slope_grid(grid: HeightmapGrid) -> list[list[SlopeSample]]:
    """Her hücre için merkezi-fark (central difference) eğim.

    Kenar hücrelerde tek yönlü fark kullanılır (grid dışına taşmamak için).
    `resolution_m` gerçek hücre boyutunu verdiği için sonuç gerçek
    yüzde-eğim (rise/run * 100) olarak yorumlanabilir.
    """
    import math

    res = grid.resolution_m
    out: list[list[SlopeSample]] = []
    for row in range(grid.height):
        row_out: list[SlopeSample] = []
        for col in range(grid.width):
            left = grid.elevation_at(row, max(0, col - 1))
            right = grid.elevation_at(row, min(grid.width - 1, col + 1))
            up = grid.elevation_at(max(0, row - 1), col)
            down = grid.elevation_at(min(grid.height - 1, row + 1), col)
            dx = (right - left) / (2 * res) if 0 < col < grid.width - 1 else (right - left) / res
            dy = (down - up) / (2 * res) if 0 < row < grid.height - 1 else (down - up) / res
            slope_ratio = math.hypot(dx, dy)
            row_out.append(
                SlopeSample(
                    slope_percent=slope_ratio * 100.0,
                    slope_degrees=math.degrees(math.atan(slope_ratio)),
                )
            )
        out.append(row_out)
    return out


def _landslide_subscore(slope_percent: float) -> float:
    # 0% -> 0, ~35% -> ~75, 60%+ -> 100 (kaba, parçalı-doğrusal; yaygın
    # jeoteknik kaba eşiklerle uyumlu kalması için 35%'lik dönüm noktası).
    if slope_percent <= 0:
        return 0.0
    return max(0.0, min(100.0, (slope_percent / 60.0) * 100.0))


def _flood_subscore_from_accumulation(
    accumulation: float, max_accumulation: float, slope_percent: float
) -> float:
    if max_accumulation <= 0:
        return 0.0
    drainage_norm = min(1.0, accumulation / max_accumulation) * 100.0
    # Düz + yüksek drenaj = vadi tabanı/dere yatağı (en riskli). Dik +
    # yüksek drenaj bile olsa su hızla tahliye olur, riski biraz düşürürüz.
    flatness_multiplier = max(0.3, 1.0 - min(slope_percent, 40.0) / 40.0 * 0.7)
    return max(0.0, min(100.0, drainage_norm * flatness_multiplier))


@dataclass(frozen=True, slots=True)
class TerrainHazardFactor:
    name: str
    subscore_0_100: float | None
    weight: float
    note: str


@dataclass(frozen=True, slots=True)
class TerrainHazardReport:
    row: int
    col: int
    x_m: float
    y_m: float
    slope_percent: float
    slope_degrees: float
    flow_accumulation: float
    landslide_index_0_100: float
    landslide_level: RiskLevel
    landslide_factors: tuple[TerrainHazardFactor, ...]
    flood_index_0_100: float
    flood_level: RiskLevel
    flood_factors: tuple[TerrainHazardFactor, ...]
    disclaimer: str = DISCLAIMER


class TerrainHazardAnalyzer:
    """`HeightmapGrid` üzerinde eğim + drenaj + (opsiyonel) yağış
    verisini birleştirip hücre bazlı sel/heyelan risk raporu üretir.

    Bir kere `TerrainHazardAnalyzer(grid)` oluşturulur (eğim + akümülasyon
    grid'leri bir kez hesaplanır), sonra `assess_point`/`assess_grid`/
    `top_risk_cells` ile sorgulanır — tüm grid için tekrar tekrar eğim
    hesaplamamak için (özellikle 512x512 üst sınırında maliyetli olabilir).
    """

    def __init__(self, grid: HeightmapGrid, *, rainfall_mm_24h: float | None = None):
        self.grid = grid
        self.rainfall_mm_24h = rainfall_mm_24h
        self._slope = compute_slope_grid(grid)
        self._accumulation = FlowAccumulation.accumulate(grid)
        self._max_accumulation = max(
            (v for row in self._accumulation for v in row),
            default=0.0,
        )

    def assess_cell(self, row: int, col: int) -> TerrainHazardReport:
        row = max(0, min(self.grid.height - 1, row))
        col = max(0, min(self.grid.width - 1, col))
        slope = self._slope[row][col]
        accumulation = self._accumulation[row][col]

        landslide_sub = _landslide_subscore(slope.slope_percent)
        landslide_factors = [
            TerrainHazardFactor("Eğim", landslide_sub, 0.85, f"eğim=%{slope.slope_percent:.1f}"),
        ]
        if self.rainfall_mm_24h is not None:
            # Doygun zeminde şev stabilitesi düşer — yağış varsa ikincil
            # (küçük ağırlıklı) bir çarpan faktör olarak eklenir.
            rain_sub = max(0.0, min(100.0, (self.rainfall_mm_24h / 100.0) * 100.0))
            landslide_factors.append(
                TerrainHazardFactor(
                    "Son 24s yağış (zemin doygunluğu)",
                    rain_sub,
                    0.15,
                    f"{self.rainfall_mm_24h:.1f} mm/24s",
                )
            )
        else:
            landslide_factors.append(
                TerrainHazardFactor(
                    "Son 24s yağış (zemin doygunluğu)", None, 0.15, "yağış verisi verilmedi"
                )
            )
        l_weighted, l_weight_total = 0.0, 0.0
        for f_ in landslide_factors:
            if f_.subscore_0_100 is not None:
                l_weighted += f_.subscore_0_100 * f_.weight
                l_weight_total += f_.weight
        landslide_index = (l_weighted / l_weight_total) if l_weight_total > 0 else landslide_sub

        flood_sub = _flood_subscore_from_accumulation(
            accumulation, self._max_accumulation, slope.slope_percent
        )
        flood_factors = [
            TerrainHazardFactor(
                "Drenaj/akış birikimi",
                flood_sub,
                0.7,
                f"akümülasyon={accumulation:.1f} hücre (havza), düzlük çarpanı uygulanmış",
            ),
        ]
        if self.rainfall_mm_24h is not None:
            rain_sub = max(0.0, min(100.0, (self.rainfall_mm_24h / 50.0) * 100.0))
            flood_factors.append(
                TerrainHazardFactor(
                    "Son 24s yağış", rain_sub, 0.3, f"{self.rainfall_mm_24h:.1f} mm/24s"
                )
            )
        else:
            flood_factors.append(
                TerrainHazardFactor("Son 24s yağış", None, 0.3, "yağış verisi verilmedi")
            )
        f_weighted, f_weight_total = 0.0, 0.0
        for f_ in flood_factors:
            if f_.subscore_0_100 is not None:
                f_weighted += f_.subscore_0_100 * f_.weight
                f_weight_total += f_.weight
        flood_index = (f_weighted / f_weight_total) if f_weight_total > 0 else flood_sub

        return TerrainHazardReport(
            row=row,
            col=col,
            x_m=col * self.grid.resolution_m,
            y_m=row * self.grid.resolution_m,
            slope_percent=round(slope.slope_percent, 2),
            slope_degrees=round(slope.slope_degrees, 2),
            flow_accumulation=round(accumulation, 2),
            landslide_index_0_100=round(landslide_index, 1),
            landslide_level=_level_from_index(landslide_index),
            landslide_factors=tuple(landslide_factors),
            flood_index_0_100=round(flood_index, 1),
            flood_level=_level_from_index(flood_index),
            flood_factors=tuple(flood_factors),
        )

    def assess_point(self, x_m: float, y_m: float) -> TerrainHazardReport:
        col = round(x_m / self.grid.resolution_m)
        row = round(y_m / self.grid.resolution_m)
        return self.assess_cell(row, col)

    def top_risk_cells(
        self, *, kind: str = "landslide", limit: int = 20
    ) -> list[TerrainHazardReport]:
        """En riskli `limit` hücreyi döner — mahalle/parsel bazlı rapor
        için ("bu bölgede en riskli N nokta") kullanışlı, tüm grid'i
        (512x512'ye kadar olabilir) tek tek istemciye göndermek yerine."""
        if kind not in ("landslide", "flood"):
            raise ValueError("kind 'landslide' ya da 'flood' olmalı.")
        reports = [
            self.assess_cell(row, col)
            for row in range(self.grid.height)
            for col in range(self.grid.width)
        ]
        key = (
            (lambda r: r.landslide_index_0_100)
            if kind == "landslide"
            else (lambda r: r.flood_index_0_100)
        )
        reports.sort(key=key, reverse=True)
        return reports[:limit]

    def summary(self) -> dict[str, float]:
        """Tüm grid için hızlı özet istatistik (ortalama/maks eğim,
        maksimum drenaj akümülasyonu) — tam hücre listesi göndermeden
        önce web arayüzünde genel bir gösterge için."""
        all_slopes = [s.slope_percent for row in self._slope for s in row]
        return {
            "mean_slope_percent": round(sum(all_slopes) / len(all_slopes), 2)
            if all_slopes
            else 0.0,
            "max_slope_percent": round(max(all_slopes), 2) if all_slopes else 0.0,
            "max_flow_accumulation": round(self._max_accumulation, 2),
            "grid_width": self.grid.width,
            "grid_height": self.grid.height,
            "resolution_m": self.grid.resolution_m,
        }
