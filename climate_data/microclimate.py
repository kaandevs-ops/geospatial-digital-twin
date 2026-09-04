"""
climate_data.microclimate - Kentsel Isı Adası Göstergesi (Katman 5 madde 1)
==============================================================================

ROADMAP_V9.md / Faz VIII / Katman 5:

    "Mikroklima modeli: Bina yoğunluğu/yükseklik -> 'kentsel ısı adası'
    kaba tahmini - gösterge niteliğinde bir 'ısı adası indeksi'
    (`risk_scoring`'in disipliniyle tutarlı)."

Yöntem (bilinçli olarak basitleştirilmiş, literatür referanslı):
Oke (1981, 1987) - kentsel ısı adası şiddetinin (ΔT_u-r, kent-kırsal fark)
sokak-kanyonu geometrisiyle (H/W - bina yüksekliği / sokak genişliği oranı,
"sky view factor"ın kaba bir vekili) ve bina yoğunluğuyla ilişkili olduğu
iyi bilinen gözlemine dayanır. Bu modül Oke'nin tam ampirik regresyonunu
yeniden üretmez (o, şehre-özgü kalibrasyon ister) - onun yerine, aynı iki
girdiyi (H/W oranı + yapı yoğunluğu) kullanan, **açıkça gösterge
niteliğinde** monoton bir indeks üretir: girdiler arttıkça indeks artar,
tersi de doğrudur; kesin bir °C tahmini iddia edilmez (yalnızca Open-Meteo
tabanlı bir taban sıcaklığa göreli bir "artış göstergesi" eklenir).

`vegetation/` (ağaç örtüsü) pasif soğutma etkisi için **azaltıcı** bir
faktör olarak eklenir - roadmap'in Katman 5 madde 5 ("Yeşil alan / ekosistem
etkisi ... gölgeleme (sıcaklık) ... kaba skorla ifadesi") burada, ayrı bir
modül açmak yerine bilinçli olarak mikroklima indeksine entegre edilmiştir
(iki ayrı gösterge yerine tek tutarlı bir sonuç - tekrar hesaplama yok).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .open_meteo_client import HourlyClimateSample

DISCLAIMER = (
    "Gösterge niteliğindedir; Oke (1981/1987) kentsel ısı adası "
    "literatüründeki H/W (sokak-kanyonu) ve yapı yoğunluğu ilişkisinin kaba "
    "bir vekilidir - şehre özgü kalibre edilmiş bir ampirik model veya CFD "
    "mikroklima simülasyonunun yerini TUTMAZ."
)


@dataclass(slots=True)
class UrbanFabricSample:
    """Bir hücre/mahalle için kaba yapı dokusu özeti - `digital_twin`/
    `core_engine` geometrisinden çağıran taraf tarafından türetilir (bu
    modül geometriyi yeniden taramaz, yalnızca özet istatistik alır)."""

    average_building_height_m: float
    average_street_width_m: float
    building_footprint_ratio: float  # 0-1, hücre alanının kaplanan oranı
    canopy_coverage_ratio: float = 0.0  # 0-1, ağaç örtüsü kaplama oranı (vegetation'dan)

    def height_to_width_ratio(self) -> float:
        if self.average_street_width_m <= 0:
            return 0.0
        return self.average_building_height_m / self.average_street_width_m


#: H/W oranı arttıkça ısı adası şiddeti artar (Oke 1981'in gözlemiyle
#: tutarlı) - katsayılar kalibre bir regresyon değil, monotonluğu garanti
#: eden GÖSTERGE ölçeklendirme sabitleridir.
_HW_WEIGHT = 2.2  # °C-benzeri gösterge birimi / (H/W oranı)
_DENSITY_WEIGHT = 1.6  # °C-benzeri gösterge birimi / (footprint oranı)
_CANOPY_COOLING_WEIGHT = 1.8  # °C-benzeri azaltım / (canopy oranı) - gölgeleme+evapotranspirasyon
#: Gösterge tavanı - gerçekçi olmayan aşırı değerlerin "kesin bir tahmin"
#: gibi görünmesini engellemek için (risk_scoring.py'deki skor sınırlama
#: disipliniyle aynı).
_MAX_INDEX_C = 6.0


@dataclass(slots=True)
class MicroclimateReport:
    heat_island_index_c: float
    baseline_temperature_c: float | None
    estimated_local_temperature_c: float | None
    height_to_width_ratio: float
    canopy_cooling_effect_c: float
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "heat_island_index_c": round(self.heat_island_index_c, 2),
            "baseline_temperature_c": self.baseline_temperature_c,
            "estimated_local_temperature_c": (
                round(self.estimated_local_temperature_c, 2)
                if self.estimated_local_temperature_c is not None
                else None
            ),
            "height_to_width_ratio": round(self.height_to_width_ratio, 2),
            "canopy_cooling_effect_c": round(self.canopy_cooling_effect_c, 2),
            "disclaimer": self.disclaimer,
        }


def estimate_heat_island_index(
    fabric: UrbanFabricSample,
    baseline: HourlyClimateSample | None = None,
) -> MicroclimateReport:
    """`fabric` (yoğunluk/H-W/canopy) + opsiyonel `baseline`
    (`open_meteo_client`'tan gerçek saatlik sıcaklık, değiştirilmedi) ->
    gösterge niteliğinde ısı adası indeksi + (varsa) yerel sıcaklık tahmini.
    """
    hw = fabric.height_to_width_ratio()
    raw_index = hw * _HW_WEIGHT + fabric.building_footprint_ratio * _DENSITY_WEIGHT
    cooling = fabric.canopy_coverage_ratio * _CANOPY_COOLING_WEIGHT
    index = max(0.0, min(_MAX_INDEX_C, raw_index - cooling))

    baseline_temp = baseline.temperature_c if baseline is not None else None
    local_temp = (baseline_temp + index) if baseline_temp is not None else None

    return MicroclimateReport(
        heat_island_index_c=index,
        baseline_temperature_c=baseline_temp,
        estimated_local_temperature_c=local_temp,
        height_to_width_ratio=hw,
        canopy_cooling_effect_c=cooling,
    )


def fabric_from_footprint(
    building_heights_m: Iterable[float],
    cell_area_m2: float,
    building_footprint_area_m2: float,
    average_street_width_m: float,
    canopy_coverage_ratio: float = 0.0,
) -> UrbanFabricSample:
    """Kolaylık yapıcı - bina yüksekliklerinin listesinden (`core_engine`/
    `digital_twin`'den türetilen ham sayılardan, geometri yeniden
    taranmadan) `UrbanFabricSample` üretir."""
    heights = list(building_heights_m)
    avg_height = sum(heights) / len(heights) if heights else 0.0
    ratio = (building_footprint_area_m2 / cell_area_m2) if cell_area_m2 > 0 else 0.0
    return UrbanFabricSample(
        average_building_height_m=avg_height,
        average_street_width_m=average_street_width_m,
        building_footprint_ratio=max(0.0, min(1.0, ratio)),
        canopy_coverage_ratio=max(0.0, min(1.0, canopy_coverage_ratio)),
    )


__all__ = [
    "DISCLAIMER",
    "UrbanFabricSample",
    "MicroclimateReport",
    "estimate_heat_island_index",
    "fabric_from_footprint",
]
