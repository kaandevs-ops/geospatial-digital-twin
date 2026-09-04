"""
visualization.heatmap_overlay - Yoğunluk Heatmap Görselleştirme Köprüsü
===========================================================================

Roadmap V9 / Faz VI:

- Katman 3.1 madde 4: "Heatmap görselleştirme: OccupancyHeatmap.compute()
  çıktısı 3D sahne üzerine renk gradyanlı overlay (kırmızı=yoğun,
  yeşil=boş) olarak visualization/'a basılır - 'görünürlük analizi
  heatmap'i' ile aynı altyapı paylaşılır."
- Katman 3.2 madde 3: "İstasyon-bazlı heatmap: 3.1'deki OccupancyHeatmap
  altyapısı istasyon/peron poligonlarına uygulanır."

Bu modül yeni bir heatmap hesaplama motoru YAZMAZ - `mobility.
crowd_simulation.OccupancyHeatmap` ve `mobility.transit_osm_bridge.
hourly_occupancy_report`'un çıktısını, `render_engine.scene_bridge`'in
tükettiği düz, render-agnostik bir hücre listesine (renk + konum + yoğunluk)
indirger. Gerçek renk blend/shader işi backend'e (viewer) ait - bu modül
yalnızca "hangi hücre ne renk" kararını üretir (roadmap'in `xray.py`
XRayState'iyle aynı ayrım: render-state, render değil).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core_engine.geometry_engine import Point2D
from ..mobility.crowd_simulation import OccupancyHeatmap


@dataclass(slots=True, frozen=True)
class HeatmapCell:
    """Tek bir renk-gradyanlı overlay hücresi - render-agnostik.

    `color_rgb` 0.0-1.0 aralığında (r, g, b) - roadmap'in "kırmızı=yoğun,
    yeşil=boş" notuyla birebir: `density_ratio=0.0` -> yeşil,
    `density_ratio=1.0` -> kırmızı, arası doğrusal interpolasyon.
    """

    center: Point2D
    cell_size: float
    density_ratio: float  # 0.0 (boş) - 1.0 (en yoğun hücreye göre normalize)
    raw_count: int
    color_rgb: tuple[float, float, float]


def _density_to_color(ratio: float) -> tuple[float, float, float]:
    """0.0 (yeşil) -> 1.0 (kırmızı) doğrusal interpolasyon - roadmap'in
    kendi renk sözleşmesi. Sarı ara-renk olarak doğal biçimde ortaya
    çıkar (yeşil+kırmızı karışımı), ayrıca kodlanmadı."""
    ratio = max(0.0, min(1.0, ratio))
    r = ratio
    g = 1.0 - ratio
    b = 0.0
    return (r, g, b)


def crowd_heatmap_overlay(
    heatmap: dict[tuple[int, int], int], *, cell_size: float = 1.0,
) -> list[HeatmapCell]:
    """`OccupancyHeatmap.compute()` çıktısını (`{(cell_x, cell_y): count}`)
    render-agnostik `HeatmapCell` listesine çevirir. Boş heatmap için boş
    liste döner (sessizce hata fırlatmaz - overlay'in yokluğu geçerli bir
    durumdur, ör. henüz hiçbir agent spawn edilmemiş bir sahne)."""
    if not heatmap:
        return []
    max_count = max(heatmap.values())
    cells: list[HeatmapCell] = []
    for (cell_x, cell_y), count in heatmap.items():
        ratio = count / max_count if max_count > 0 else 0.0
        center = Point2D((cell_x + 0.5) * cell_size, (cell_y + 0.5) * cell_size)
        cells.append(HeatmapCell(
            center=center, cell_size=cell_size, density_ratio=ratio,
            raw_count=count, color_rgb=_density_to_color(ratio),
        ))
    return cells


def crowd_heatmap_overlay_from_agents(agents: list, *, cell_size: float = 1.0) -> list[HeatmapCell]:
    """`crowd_heatmap_overlay(OccupancyHeatmap.compute(agents, cell_size))`
    kısayolu - roadmap'in "aynı altyapı paylaşılır" notuyla tutarlı, yeni
    hesap yazılmadı, yalnızca iki adım tek çağrıya indirgendi."""
    heatmap = OccupancyHeatmap.compute(agents, cell_size=cell_size)
    return crowd_heatmap_overlay(heatmap, cell_size=cell_size)


@dataclass(slots=True, frozen=True)
class StationHeatmapCell:
    """Katman 3.2 madde 3 — istasyon/peron bazlı yoğunluk overlay hücresi.
    `mobility.transit_osm_bridge.hourly_occupancy_report()`'un çıktısını
    (saat -> doluluk oranı) bir istasyon konumuna damgalayarak üretir."""

    station_position: Point2D
    hour: int
    avg_occupancy_ratio: float
    is_dense: bool
    color_rgb: tuple[float, float, float]


def station_heatmap_overlay(
    station_position: Point2D,
    hourly_report: dict[int, dict[str, float]],
) -> list[StationHeatmapCell]:
    """`transit_osm_bridge.hourly_occupancy_report()` çıktısını tek bir
    istasyon konumu için saat-bazlı `StationHeatmapCell` listesine çevirir
    (Katman 3.1'deki aynı kırmızı/yeşil renk sözleşmesi yeniden kullanılır
    - roadmap ilkesi #2)."""
    cells: list[StationHeatmapCell] = []
    for hour, stats in sorted(hourly_report.items()):
        ratio = stats.get("avg_occupancy_ratio", 0.0)
        cells.append(StationHeatmapCell(
            station_position=station_position,
            hour=hour,
            avg_occupancy_ratio=ratio,
            is_dense=bool(stats.get("is_dense", False)),
            color_rgb=_density_to_color(ratio),
        ))
    return cells
