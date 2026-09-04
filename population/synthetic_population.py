"""
population.synthetic_population - Sentetik Nüfus (Katman 2.1)
================================================================

Roadmap V9 / Faz VI / Katman 2.1: "Her bina için (konut/işyeri/okul/AVM)
gerçekçi ama tam kimlik taşımayan sentetik hane/birey üretimi: yaş grubu,
hareket kabiliyeti, günlük rutin tipi. TÜİK açık istatistikleriyle beslenir
- gerçek kişi verisi değil, istatistiksel dağılım."

Bu modül `mobility.crowd_simulation.MobilityProfile` ve
`DEFAULT_MOBILITY_PROFILE_DISTRIBUTION`'ı **yeniden kullanır** (roadmap
ilkesi #2 - tekrar yazma yok); yeni bir mobilite sınıflandırması icat
etmez, yalnızca bunun üstüne yaş grubu + günlük rutin tipi ekler.

Gösterge disiplini: buradaki dağılımlar `risk_scoring.py` ile aynı
titizlikle "kesin nüfus sayımı değildir" notunu taşır - TÜİK/WorldPop genel
eğilimlerinin kaba bir yaklaşımıdır, belirli bir binanın gerçek sakinlerini
temsil etmez.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..mobility.crowd_simulation import (
    MobilityProfile,
    DEFAULT_MOBILITY_PROFILE_DISTRIBUTION,
)


class AgeGroup(str, Enum):
    """Kaba yaş grubu sınıflandırması (TÜİK yaş grubu raporlarıyla aynı
    granülaritede - gösterge amaçlı)."""

    CHILD = "child"              # 0-14
    YOUTH = "youth"              # 15-24
    ADULT = "adult"              # 25-64
    ELDERLY = "elderly"          # 65+


#: TÜİK Türkiye geneli kaba yaş dağılımı yaklaşımı (gösterge niteliğinde,
#: belirli bir mahallenin gerçek sayımı değildir - roadmap 2.1 notu).
DEFAULT_AGE_GROUP_DISTRIBUTION: dict[AgeGroup, float] = {
    AgeGroup.CHILD: 0.22,
    AgeGroup.YOUTH: 0.15,
    AgeGroup.ADULT: 0.48,
    AgeGroup.ELDERLY: 0.15,
}


class DailyRoutineType(str, Enum):
    """Katman 2.2 (activity_model) tarafından tüketilecek günlük rutin
    tipi - "kim, ne zaman, nereye gider" sorusunun kaba sınıflandırması."""

    SCHOOL_CHILD = "school_child"          # ev <-> okul
    WORKER_OFFICE = "worker_office"        # ev <-> iş (sabit mesai)
    WORKER_SHIFT = "worker_shift"          # ev <-> iş (vardiyalı, farklı saat)
    HOMEMAKER = "homemaker"                # evde, kısa yerel gündelik çıkışlar
    RETIRED = "retired"                    # evde, düzensiz kısa çıkışlar
    UNEMPLOYED_OR_FLEXIBLE = "flexible"    # düzensiz


#: Yaş grubuna göre en olası rutin tipi dağılımı - `activity_model`'in
#: aktivite zinciri seçiminde kullanılır. Bilinçli olarak basit/açık bir
#: kural tablosu (kara kutu değil, roadmap ilkesi #5 ile tutarlı).
ROUTINE_BY_AGE_GROUP: dict[AgeGroup, dict[DailyRoutineType, float]] = {
    AgeGroup.CHILD: {DailyRoutineType.SCHOOL_CHILD: 1.0},
    AgeGroup.YOUTH: {
        DailyRoutineType.SCHOOL_CHILD: 0.55,
        DailyRoutineType.WORKER_OFFICE: 0.25,
        DailyRoutineType.WORKER_SHIFT: 0.10,
        DailyRoutineType.UNEMPLOYED_OR_FLEXIBLE: 0.10,
    },
    AgeGroup.ADULT: {
        DailyRoutineType.WORKER_OFFICE: 0.50,
        DailyRoutineType.WORKER_SHIFT: 0.20,
        DailyRoutineType.HOMEMAKER: 0.15,
        DailyRoutineType.UNEMPLOYED_OR_FLEXIBLE: 0.15,
    },
    AgeGroup.ELDERLY: {
        DailyRoutineType.RETIRED: 0.85,
        DailyRoutineType.HOMEMAKER: 0.15,
    },
}

#: `activity_model`'in bağımsız kullanabilmesi için düz (yaş-grubu
#: bağımsız) varsayılan dağılım - `mobility.scenario.AgentProfileMix`'e
#: benzer amaçla, `AppSession`'da hızlı senaryo kurulumu için.
DEFAULT_ROUTINE_DISTRIBUTION: dict[DailyRoutineType, float] = {
    DailyRoutineType.SCHOOL_CHILD: 0.22,
    DailyRoutineType.WORKER_OFFICE: 0.30,
    DailyRoutineType.WORKER_SHIFT: 0.12,
    DailyRoutineType.HOMEMAKER: 0.13,
    DailyRoutineType.RETIRED: 0.13,
    DailyRoutineType.UNEMPLOYED_OR_FLEXIBLE: 0.10,
}


def _weighted_choice(rng: random.Random, distribution: dict) -> object:
    keys = list(distribution.keys())
    weights = list(distribution.values())
    return rng.choices(keys, weights=weights, k=1)[0]


@dataclass(slots=True)
class SyntheticIndividual:
    """Tek bir sentetik birey - kimliksiz, yalnızca istatistiksel
    özellikler taşır (roadmap 2.1: "gerçek kişi verisi değil")."""

    individual_id: str
    age_group: AgeGroup
    mobility_profile: MobilityProfile
    routine_type: DailyRoutineType


@dataclass(slots=True)
class Household:
    """Bir binaya (konut) bağlı sentetik hane - bireylerin kabaca
    gruplanması. `building_ref` mevcut bina deposundaki bir anahtara
    işaret eder, yeni bir bina modeli icat edilmez."""

    household_id: str
    building_ref: str
    individuals: list[SyntheticIndividual] = field(default_factory=list)

    def size(self) -> int:
        return len(self.individuals)


class SyntheticPopulationGenerator:
    """Roadmap 2.1'in üreteci. Bina başına hane sayısı + hane başına birey
    sayısı basit, ayarlanabilir parametrelerle kontrol edilir - deterministik
    (seed'li) böylece aynı bina için tekrarlanabilir sonuç üretilebilir.
    """

    def __init__(
        self,
        *,
        age_group_distribution: dict[AgeGroup, float] | None = None,
        mobility_profile_distribution: dict[MobilityProfile, float] | None = None,
        avg_household_size: float = 2.6,
        seed: int | None = None,
    ) -> None:
        self.age_group_distribution = age_group_distribution or DEFAULT_AGE_GROUP_DISTRIBUTION
        self.mobility_profile_distribution = (
            mobility_profile_distribution or DEFAULT_MOBILITY_PROFILE_DISTRIBUTION
        )
        if avg_household_size <= 0:
            raise ValueError("avg_household_size pozitif olmalı")
        self.avg_household_size = avg_household_size
        self._rng = random.Random(seed)

    def _make_individual(self, individual_id: str) -> SyntheticIndividual:
        age_group = _weighted_choice(self._rng, self.age_group_distribution)
        mobility_profile = _weighted_choice(self._rng, self.mobility_profile_distribution)
        routine_pool = ROUTINE_BY_AGE_GROUP.get(age_group, DEFAULT_ROUTINE_DISTRIBUTION)
        routine_type = _weighted_choice(self._rng, routine_pool)
        return SyntheticIndividual(
            individual_id=individual_id,
            age_group=age_group,
            mobility_profile=mobility_profile,
            routine_type=routine_type,
        )

    def generate_household(self, building_ref: str, household_index: int) -> Household:
        """Tek bir hane üretir. Hane büyüklüğü `avg_household_size`
        etrafında ±1 taban alınarak (asgari 1) yuvarlanır - basit ama
        açık bir kural (kara kutu değil)."""
        size = max(1, round(self._rng.gauss(self.avg_household_size, 0.8)))
        household_id = f"{building_ref}:hh{household_index}"
        individuals = [
            self._make_individual(f"{household_id}:p{i}") for i in range(size)
        ]
        return Household(household_id=household_id, building_ref=building_ref, individuals=individuals)

    def generate_for_building(self, building_ref: str, household_count: int) -> list[Household]:
        """Bir bina için `household_count` adet hane üretir (ör. bir konut
        binasının kaç daireye sahip olduğu tahmini - çağıran taraf bunu
        bina metrekaresi/kat sayısından kabaca türetebilir, bu modül
        yalnızca hane_sayısı parametresini alır, kendi tahmin yapmaz -
        roadmap ilkesi #1 ile tutarlı: "yanlış kesinlik hissi vermemek",
        bu yüzden bina->daire sayısı çıkarımı çağırana bırakılmıştır)."""
        if household_count < 0:
            raise ValueError("household_count negatif olamaz")
        return [
            self.generate_household(building_ref, i) for i in range(household_count)
        ]

    def all_individuals(self, households: Sequence[Household]) -> list[SyntheticIndividual]:
        result: list[SyntheticIndividual] = []
        for hh in households:
            result.extend(hh.individuals)
        return result

    def individual_group_ids(self, households: Sequence[Household]) -> dict[str, str]:
        """Roadmap V10 / Faz 2.7 ("Grup/aile bağı davranışı") köprüsü:
        `individual_id -> household_id` eşlemesi döner. Çağıran taraf
        (senaryo kurulumu), her `SyntheticIndividual`'dan bir `Agent`
        türettiğinde bu eşlemeyle `Agent.group_id = household_id` atar -
        `SocialForceModel._group_cohesion_force` bu değeri doğrudan
        tüketir. Burada kasıtlı olarak `Agent` import edilmiyor (bu modül
        zaten `mobility.crowd_simulation`'ı içe aktarıyor; döngüsel bağımlı
        hale getirmemek ve tek sorumluluk ilkesini korumak için `Agent`
        nesnesi burada değil, çağıran senaryo katmanında oluşturulur)."""
        mapping: dict[str, str] = {}
        for hh in households:
            for ind in hh.individuals:
                mapping[ind.individual_id] = hh.household_id
        return mapping

    def profile_distribution_from(
        self, individuals: Sequence[SyntheticIndividual]
    ) -> dict[MobilityProfile, float]:
        """Üretilen bireylerden gerçekleşen (fiili) mobilite profili
        dağılımını hesaplar - `mobility.crowd_simulation.spawn_random_agents`'a
        `profile_distribution=` olarak doğrudan verilebilir (roadmap'in
        "tekrar yazma yok" ilkesi: yeni bir spawn fonksiyonu icat edilmedi,
        mevcut olan bu üretecin çıktısıyla beslenir)."""
        if not individuals:
            return dict(self.mobility_profile_distribution)
        counts: dict[MobilityProfile, int] = {}
        for ind in individuals:
            counts[ind.mobility_profile] = counts.get(ind.mobility_profile, 0) + 1
        total = len(individuals)
        return {profile: count / total for profile, count in counts.items()}
