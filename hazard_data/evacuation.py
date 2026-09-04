"""Risk skoru -> tahliye/denetim önceliklendirmesi — roadmap Faz 2.3.

    "Kaçış rotası / tahliye protokolü: mobility/pathfinding,
    crowd_simulation algoritmik olarak hazır — gerçek bina planı + gerçek
    çıkış kapısı sayısı + gerçek nüfus yoğunluğu tahminiyle beslenmeli."

`mobility.crowd_simulation.EvacuationSimulator` ve
`mobility.pathfinding.NavGraph` zaten var (roadmap'in kendi notu da bunu
doğruluyor) — bu modül onları TEKRAR YAZMAZ. Sadece `risk_scoring`
çıktısını (birden fazla bina için) bir önceliklendirme listesine çevirir:
en yüksek risk indeksine sahip binalar önce denetlenmeli/tahliye
planlaması önce yapılmalı.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .risk_scoring import BuildingRiskReport


@dataclass(frozen=True, slots=True)
class EvacuationPriority:
    """Bir binanın tahliye/denetim öncelik sırasındaki yeri."""

    building_id: str
    rank: int  # 1 = en yüksek öncelik (en riskli)
    risk_report: BuildingRiskReport
    occupant_estimate: Optional[int]
    note: str


def prioritize_evacuation(
    buildings: Sequence[tuple[str, BuildingRiskReport]],
    *,
    occupant_estimates: Optional[dict[str, int]] = None,
) -> list[EvacuationPriority]:
    """Birden fazla binayı risk indeksine göre azalan sırada sıralar.

    `occupant_estimates` verilirse (örn. WorldPop tabanlı nüfus yoğunluğu
    tahmini — roadmap Faz 2.4), eşit risk indeksinde daha kalabalık bina
    öne alınır (ikincil sıralama anahtarı). Sonuç, `mobility.crowd_simulation.
    EvacuationSimulator` senaryolarını hangi sırayla çalıştıracağınızı
    (veya gerçek dünyada hangi binayı önce denetleyeceğinizi) belirlemek
    için kullanılabilir — simülasyonun kendisini bu fonksiyon çalıştırmaz.
    """
    occupant_estimates = occupant_estimates or {}

    def sort_key(item: tuple[str, BuildingRiskReport]) -> tuple[float, int]:
        building_id, report = item
        occ = occupant_estimates.get(building_id, 0)
        return (-report.risk_index_0_100, -occ)

    ordered = sorted(buildings, key=sort_key)

    results: list[EvacuationPriority] = []
    for rank, (building_id, report) in enumerate(ordered, start=1):
        occ = occupant_estimates.get(building_id)
        note = (
            f"{report.risk_level.value} risk seviyesi"
            + (f", tahmini {occ} kişi" if occ is not None else ", nüfus tahmini yok")
        )
        results.append(EvacuationPriority(
            building_id=building_id, rank=rank, risk_report=report,
            occupant_estimate=occ, note=note,
        ))
    return results
