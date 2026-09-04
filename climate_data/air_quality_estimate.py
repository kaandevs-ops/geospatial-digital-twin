"""
climate_data.air_quality_estimate - Trafik Kaynaklı Hava Kalitesi Göstergesi
==============================================================================
(Katman 5 madde 2)

ROADMAP_V9.md / Faz VIII / Katman 5:

    "Hava kalitesi / trafik-kaynaklı kirlilik: Katman 3'teki araç
    yoğunluğundan kaba emisyon/kirlilik tahmini (yol bazlı araç-km ×
    emisyon faktörü)."

Yöntem: `mobility.traffic_simulation`'ın ürettiği (veya doğrudan girdi
olarak verilen) yol-bazlı saatlik araç akışı (veh/saat) × yol uzunluğu
(km) = araç-km/saat; bu, ortalama Euro-sınıfı bir benzinli/dizel filo
karışımı için **gösterge niteliğinde** EEA/COPERT tarzı ortalama emisyon
faktörleriyle (g/araç-km) çarpılarak kaba bir kirletici yükü (g/saat) ve
ondan basit bir 0-100 "gösterge indeksi" üretilir.

GÖSTERGE NİTELİĞİ (risk_scoring.py ile aynı disiplin): burada kullanılan
emisyon faktörleri tek bir ortalama Avrupa filo karışımını temsil eden
KABA yuvarlak sayılardır (gerçek COPERT veritabanının yerini tutmaz - araç
yaşı, hız profili, sıcaklık, yol eğimi gibi COPERT'in gerçekte kullandığı
onlarca değişken burada yoktur). Sonuç bir hava kalitesi izleme istasyonu
ölçümü DEĞİLDİR, yalnızca "bu yol kesiminde trafik kaynaklı kirlilik
göreli olarak nerede daha yüksek" sorusuna kaba bir işaret verir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

DISCLAIMER = (
    "Gösterge niteliğindedir; ortalama bir Avrupa filo karışımı için kaba "
    "emisyon faktörleri kullanılır (COPERT/EEA tarzı, ama COPERT'in kendisi "
    "değildir). Gerçek bir hava kalitesi izleme istasyonu ölçümünün veya "
    "dispersiyon (yayılım) modelinin yerini TUTMAZ."
)

#: g / araç-km, ortalama karma filo (benzin+dizel+az miktarda elektrikli) -
#: EEA "Air pollutant emission inventory guidebook" tarzı raporlardaki
#: mertebeyle tutarlı KABA yuvarlak sayılar (gerçek COPERT çıktısı değil).
EMISSION_FACTORS_G_PER_VEHICLE_KM: dict[str, float] = {
    "nox": 0.35,
    "pm2_5": 0.02,
    "co": 0.50,
}

#: Gösterge indeksi için ölçekleme - bu tavan değerler, indeksin 0-100
#: aralığında kalması için seçilmiş keyfi ama sabit referans noktalarıdır
#: (resmi bir AQI eşiği değildir).
_INDEX_REFERENCE_G_PER_HOUR = {"nox": 2000.0, "pm2_5": 120.0, "co": 3000.0}


@dataclass(slots=True)
class RoadSegmentTraffic:
    """Tek bir yol kesiminin saatlik trafik özeti - `mobility.
    traffic_simulation.GreenshieldsModel.flow_at_density()` veya doğrudan
    gözlem/senaryo verisinden çağıran taraf tarafından doldurulur (bu
    modül trafik simülasyonunu yeniden çalıştırmaz)."""

    segment_id: str
    length_m: float
    vehicles_per_hour: float


@dataclass(slots=True)
class AirQualitySegmentReport:
    segment_id: str
    vehicle_km_per_hour: float
    emissions_g_per_hour: dict[str, float]
    indicative_index_0_100: float
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "vehicle_km_per_hour": round(self.vehicle_km_per_hour, 2),
            "emissions_g_per_hour": {k: round(v, 2) for k, v in self.emissions_g_per_hour.items()},
            "indicative_index_0_100": round(self.indicative_index_0_100, 1),
            "disclaimer": self.disclaimer,
        }


def estimate_segment_air_quality(segment: RoadSegmentTraffic) -> AirQualitySegmentReport:
    vehicle_km_per_hour = (segment.length_m / 1000.0) * max(0.0, segment.vehicles_per_hour)

    emissions = {
        pollutant: vehicle_km_per_hour * factor
        for pollutant, factor in EMISSION_FACTORS_G_PER_VEHICLE_KM.items()
    }

    # Gösterge indeksi: her kirleticinin kendi referans tavanına göre
    # normalize edilmiş payının en büyüğü (limit-faktörü mantığı - resmi
    # AQI hesaplarında da "en kötü kirletici belirleyicidir" ilkesi
    # yaygındır, ama burada resmi bir AQI formülü uygulanmamaktadır).
    ratios = [
        min(1.0, emissions[p] / _INDEX_REFERENCE_G_PER_HOUR[p])
        for p in emissions
    ]
    index = max(ratios) * 100.0 if ratios else 0.0

    return AirQualitySegmentReport(
        segment_id=segment.segment_id,
        vehicle_km_per_hour=vehicle_km_per_hour,
        emissions_g_per_hour=emissions,
        indicative_index_0_100=index,
    )


def estimate_network_air_quality(
    segments: Iterable[RoadSegmentTraffic],
) -> list[AirQualitySegmentReport]:
    """Birden çok yol kesimi için toplu koşum - kod tekrarı yaratmadan
    `estimate_segment_air_quality`'yi tekrar tekrar çağırır (roadmap'in
    diğer batch-runner'larıyla - `CapacityAnalyzer`, `FireEvacuationComparator`
    - aynı desen)."""
    return [estimate_segment_air_quality(seg) for seg in segments]


__all__ = [
    "DISCLAIMER",
    "EMISSION_FACTORS_G_PER_VEHICLE_KM",
    "RoadSegmentTraffic",
    "AirQualitySegmentReport",
    "estimate_segment_air_quality",
    "estimate_network_air_quality",
]
