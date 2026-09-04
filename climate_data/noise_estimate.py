"""
climate_data.noise_estimate - Trafik/Yaya Gürültü Göstergesi (Katman 5 madde 3)
==================================================================================

ROADMAP_V9.md / Faz VIII / Katman 5:

    "Gürültü haritası: Trafik + yaya yoğunluğundan kaba ses seviyesi
    tahmini - `analysis_engine`'e doğal bir ek."

Yöntem (trafik bileşeni): İngiltere Ulaştırma Bakanlığı'nın klasik "CRTN"
(Calculation of Road Traffic Noise, 1988) yönteminin TEMEL akış terimi -

    L10(1 saat) = 42.2 + 10*log10(Q)   [dBA, Q = saatlik araç akışı]

- yaygın olarak bilinen, basit ve tekrarlanabilir bir formüldür. CRTN'nin
  tam yöntemi ayrıca hız, ağır araç yüzdesi, yol eğimi, yüzey tipi, mesafe
  yayılımı ve bariyer düzeltmeleri de içerir; bu modül yalnızca temel akış
  terimini uygular (GÖSTERGE amaçlı, tam CRTN raporu değil).

Yöntem (yaya/kalabalık bileşeni): trafik kadar iyi bilinen tek bir kaynak
yoktur - bu modül, `mobility.crowd_simulation.OccupancyHeatmap` yoğunluğunu
(kişi/m²) basit, adı açık bir logaritmik artışla (insan kalabalığı sesi de
kaynak-sayısı arttıkça yaklaşık logaritmik olarak artar - akustikte "N
bağımsız kaynağın toplam SPL'i 10*log10(N) ile ölçeklenir" ilkesinin aynı
ailesinden, CRTN ile aynı matematiksel aile) bir dB katkısına çevirir.

GÖSTERGE NİTELİĞİ: risk_scoring.py ile aynı disiplin - resmi bir gürültü
haritalama direktifi (örn. AB END 2002/49/EC, CNOSSOS-EU) hesabının yerini
TUTMAZ; yalnızca göreli karşılaştırma için kaba bir işaret verir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DISCLAIMER = (
    "Gösterge niteliğindedir; CRTN (1988) temel akış terimi + basit "
    "logaritmik kalabalık-kaynağı yaklaşıklaması kullanılır. AB END "
    "2002/49/EC / CNOSSOS-EU gibi resmi gürültü haritalama yöntemlerinin "
    "yerini TUTMAZ (hız/ağır araç/yüzey/bariyer/mesafe düzeltmeleri yoktur)."
)

#: CRTN (1988) temel akış terimi sabiti - L10(1hr) = 42.2 + 10*log10(Q).
_CRTN_BASE_CONSTANT_DB = 42.2
#: Kalabalık kaynağı için referans - 1 kişi/m² başına göreli katkı sabiti
#: (kalibre bir akustik ölçüm değil, "N kaynak -> 10*log10(N)" ailesinden
#: türetilmiş göstergedir).
_CROWD_REFERENCE_DB = 50.0


@dataclass(slots=True)
class NoiseEstimateReport:
    traffic_db: float | None
    crowd_db: float | None
    combined_db: float | None
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "traffic_db": round(self.traffic_db, 1) if self.traffic_db is not None else None,
            "crowd_db": round(self.crowd_db, 1) if self.crowd_db is not None else None,
            "combined_db": round(self.combined_db, 1) if self.combined_db is not None else None,
            "disclaimer": self.disclaimer,
        }


def _combine_db(levels: list[float]) -> float:
    """İki bağımsız ses kaynağının enerji-toplamı (dB toplama gerçek
    akustik kuralı - basitleştirme değil, standart formül):
    L_toplam = 10*log10(sum(10^(Li/10)))."""
    if not levels:
        return 0.0
    energy_sum = sum(10 ** (level / 10.0) for level in levels)
    return 10.0 * math.log10(energy_sum) if energy_sum > 0 else 0.0


def traffic_noise_db(vehicles_per_hour: float) -> float:
    """CRTN (1988) temel akış terimi. `vehicles_per_hour <= 0` için 0.0
    döner (log(0) tanımsızlığından kaçınmak amacıyla, sessizce değil -
    trafik yoksa gürültü katkısı da yoktur, bu fiziksel olarak tutarlıdır).
    """
    if vehicles_per_hour <= 0:
        return 0.0
    return _CRTN_BASE_CONSTANT_DB + 10.0 * math.log10(vehicles_per_hour)


def crowd_noise_db(density_people_per_m2: float) -> float:
    """Kalabalık yoğunluğundan (kişi/m²) göreli dB katkısı - `mobility.
    crowd_simulation.OccupancyHeatmap.compute()` hücre yoğunluğu doğrudan
    beslenebilir."""
    if density_people_per_m2 <= 0:
        return 0.0
    return _CROWD_REFERENCE_DB + 10.0 * math.log10(max(density_people_per_m2, 0.01))


def estimate_noise(
    vehicles_per_hour: float | None = None,
    density_people_per_m2: float | None = None,
) -> NoiseEstimateReport:
    traffic_db = traffic_noise_db(vehicles_per_hour) if vehicles_per_hour is not None else None
    crowd_db = crowd_noise_db(density_people_per_m2) if density_people_per_m2 is not None else None

    levels = [lvl for lvl in (traffic_db, crowd_db) if lvl is not None and lvl > 0]
    combined = _combine_db(levels) if levels else None

    return NoiseEstimateReport(traffic_db=traffic_db, crowd_db=crowd_db, combined_db=combined)


__all__ = [
    "DISCLAIMER",
    "NoiseEstimateReport",
    "traffic_noise_db",
    "crowd_noise_db",
    "estimate_noise",
]
