"""
population.activity_model - Günlük Rutin Motoru (Katman 2.2)
================================================================

Roadmap V9 / Faz VI / Katman 2.2: "'Activity-based travel demand model'
yaklaşımı: her sentetik birey için günlük aktivite zinciri (ev->okul/iş->
öğlen molası->eve dönüş->akşam sosyal aktivite). Bu, Katman 3'ü besler:
her aktivite geçişi bir yolculuk talebi üretir -> trafik/transit/yaya
simülasyonuna girdi olur."

"OD (Origin-Destination) matrisi ... sabah agent'lar konut alanlarından
okul/iş/durak noktalarına, akşam tersine akar - 4 adımlı ulaşım talep
modelinin basitleştirilmişi."

Bu modül yeni bir simülasyon motoru YAZMAZ - `SyntheticIndividual`'ların
`routine_type`'ına göre saat-damgalı bir aktivite zinciri üretir ve bunu
düz `ODDemandEntry` listesine (kim, ne zaman, nereden, nereye) indirger.
Bu liste `mobility.osm_demand_bridge` (POI konumları) ve
`mobility.transit_simulation`/`mobility.traffic_simulation` (gerçek
yolculuk simülasyonu) tarafından tüketilmek üzere tasarlanmıştır - roadmap
notu: "mode choice model" ve gerçek rota hesaplaması Katman 3.3'ün işidir,
bu modül yalnızca TALEBİ üretir (ayrım bilinçli - tek sorumluluk ilkesi).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .synthetic_population import DailyRoutineType, SyntheticIndividual


class ActivityType(str, Enum):
    HOME = "home"
    SCHOOL = "school"
    WORK = "work"
    LUNCH_BREAK = "lunch_break"
    SOCIAL_EVENING = "social_evening"
    LOCAL_ERRAND = "local_errand"  # ev hanımı/emekli kısa yerel çıkış


@dataclass(slots=True)
class Activity:
    """Bir bireyin günün bir anındaki aktivitesi - "nerede olması
    gerektiği" (POI/konut tipi olarak, somut koordinat değil - bu bilinçli:
    gerçek konum ataması `osm_demand_bridge`'in işi)."""

    activity_type: ActivityType
    start_hour: float  # 0-24 arası ondalık saat


#: Rutin tipine göre günlük aktivite zinciri şablonu - roadmap'in kendi
#: örneğiyle tutarlı basit, açık, düzenlenebilir bir tablo (kara kutu
#: değil - roadmap ilkesi #5).
ACTIVITY_CHAIN_TEMPLATES: dict[DailyRoutineType, list[Activity]] = {
    DailyRoutineType.SCHOOL_CHILD: [
        Activity(ActivityType.HOME, 0.0),
        Activity(ActivityType.SCHOOL, 8.0),
        Activity(ActivityType.LUNCH_BREAK, 12.0),
        Activity(ActivityType.SCHOOL, 13.0),
        Activity(ActivityType.HOME, 16.0),
    ],
    DailyRoutineType.WORKER_OFFICE: [
        Activity(ActivityType.HOME, 0.0),
        Activity(ActivityType.WORK, 9.0),
        Activity(ActivityType.LUNCH_BREAK, 12.5),
        Activity(ActivityType.WORK, 13.5),
        Activity(ActivityType.HOME, 18.0),
        Activity(ActivityType.SOCIAL_EVENING, 20.0),
        Activity(ActivityType.HOME, 22.5),
    ],
    DailyRoutineType.WORKER_SHIFT: [
        Activity(ActivityType.HOME, 0.0),
        Activity(ActivityType.WORK, 14.0),
        Activity(ActivityType.HOME, 22.0),
    ],
    DailyRoutineType.HOMEMAKER: [
        Activity(ActivityType.HOME, 0.0),
        Activity(ActivityType.LOCAL_ERRAND, 10.0),
        Activity(ActivityType.HOME, 12.0),
        Activity(ActivityType.LOCAL_ERRAND, 17.0),
        Activity(ActivityType.HOME, 19.0),
    ],
    DailyRoutineType.RETIRED: [
        Activity(ActivityType.HOME, 0.0),
        Activity(ActivityType.LOCAL_ERRAND, 11.0),
        Activity(ActivityType.HOME, 13.0),
    ],
    DailyRoutineType.UNEMPLOYED_OR_FLEXIBLE: [
        Activity(ActivityType.HOME, 0.0),
        Activity(ActivityType.LOCAL_ERRAND, 15.0),
        Activity(ActivityType.HOME, 18.0),
    ],
}


@dataclass(slots=True)
class ODDemandEntry:
    """Tek bir yolculuk talebi (Origin-Destination) - roadmap 2.2'nin
    "4 adımlı ulaşım talep modelinin basitleştirilmişi" karşılığı. Somut
    koordinat taşımaz (`origin_activity`/`destination_activity` tipiyle
    ifade edilir) - gerçek konum çözümlemesi `osm_demand_bridge`'in POI
    eşlemesiyle, ya da çağıran tarafın kendi bina/POI deposuyla yapılır
    (tek sorumluluk ilkesi - bu modül yalnızca TALEBİ üretir)."""

    individual_id: str
    household_id: str
    departure_hour: float
    origin_activity: ActivityType
    destination_activity: ActivityType


class ActivityModel:
    """Roadmap 2.2'nin üreteci: `SyntheticIndividual.routine_type`'a göre
    günlük aktivite zincirini ardışık `ODDemandEntry` listesine çevirir.
    """

    def __init__(
        self,
        *,
        chain_templates: dict[DailyRoutineType, list[Activity]] | None = None,
    ) -> None:
        self.chain_templates = chain_templates or ACTIVITY_CHAIN_TEMPLATES

    def activity_chain_for(self, individual: SyntheticIndividual) -> list[Activity]:
        return list(
            self.chain_templates.get(
                individual.routine_type,
                self.chain_templates[DailyRoutineType.UNEMPLOYED_OR_FLEXIBLE],
            )
        )

    def od_demand_for_individual(
        self, individual: SyntheticIndividual, household_id: str
    ) -> list[ODDemandEntry]:
        chain = self.activity_chain_for(individual)
        entries: list[ODDemandEntry] = []
        for i in range(len(chain) - 1):
            origin = chain[i]
            destination = chain[i + 1]
            entries.append(
                ODDemandEntry(
                    individual_id=individual.individual_id,
                    household_id=household_id,
                    departure_hour=destination.start_hour,
                    origin_activity=origin.activity_type,
                    destination_activity=destination.activity_type,
                )
            )
        return entries

    def od_demand_for_population(
        self, individuals_with_household: Sequence[tuple[SyntheticIndividual, str]]
    ) -> list[ODDemandEntry]:
        """Toplu üretim - bir `SyntheticPopulationGenerator` çıktısındaki
        tüm hanelerin tüm bireyleri için OD talebi üretir."""
        entries: list[ODDemandEntry] = []
        for individual, household_id in individuals_with_household:
            entries.extend(self.od_demand_for_individual(individual, household_id))
        return entries

    def demand_by_hour(self, entries: Sequence[ODDemandEntry]) -> dict[int, list[ODDemandEntry]]:
        """Saatlik talep dağılımı - Katman 3.2'nin "saatlik yoğunluk
        raporu" ile aynı disiplinle, sabah/akşam pik'in doğrudan
        gözlemlenebilmesi için (ör. `len(demand_by_hour(entries)[8])`
        sabah 08:00 talep hacmini verir)."""
        buckets: dict[int, list[ODDemandEntry]] = {}
        for entry in entries:
            hour = int(entry.departure_hour) % 24
            buckets.setdefault(hour, []).append(entry)
        return buckets

    def peak_hours(self, entries: Sequence[ODDemandEntry], top_n: int = 2) -> list[tuple[int, int]]:
        """En yoğun `top_n` saati (saat, talep_sayısı) olarak döner -
        roadmap'in "08:00-09:00 arası X istasyonunda yoğunluk artıyor"
        cümlesinin şehir-geneli OD talebi karşılığı."""
        buckets = self.demand_by_hour(entries)
        ranked = sorted(((h, len(v)) for h, v in buckets.items()), key=lambda kv: -kv[1])
        return ranked[:top_n]
