"""
population
==========

Roadmap V9 / Faz VI (Çekirdek Gündelik Yaşam Döngüsü) — Katman 2
("İnsan / Nüfus / Gündelik Davranış").

Bu paket iki alt modülden oluşur:

- `synthetic_population.py` — Katman 2.1: bina başına gerçekçi ama kimliksiz
  sentetik hane/birey üretimi (yaş grubu, hareket kabiliyeti, günlük rutin
  tipi). Gerçek kişi verisi DEĞİLDİR — TÜİK/WorldPop tarzı açık istatistiksel
  dağılımlarla üretilen göstergedir (roadmap'in "gösterge disiplini" ilkesi).
- `activity_model.py` — Katman 2.2: her sentetik birey için günlük aktivite
  zinciri (ev->okul/iş->öğlen->ev->akşam) ve bundan türeyen OD (Origin-
  Destination) yolculuk talebi — Katman 3'ün (trafik/transit/yaya) girdisi.

Roadmap'in "tekrar yazma yok" ilkesiyle tutarlı: bu paket yeni bir agent
motoru icat etmez, `mobility.crowd_simulation.Agent` / `MobilityProfile`
ile aynı sözlüğü kullanır ve `mobility.crowd_simulation.spawn_random_agents`
tarafından tüketilebilecek profil dağılımları üretir.
"""

from __future__ import annotations

from .activity_model import (
    Activity,
    ActivityModel,
    ActivityType,
    ODDemandEntry,
)
from .synthetic_population import (
    DEFAULT_AGE_GROUP_DISTRIBUTION,
    DEFAULT_ROUTINE_DISTRIBUTION,
    AgeGroup,
    DailyRoutineType,
    Household,
    SyntheticIndividual,
    SyntheticPopulationGenerator,
)

__all__ = [
    "AgeGroup",
    "DailyRoutineType",
    "Household",
    "SyntheticIndividual",
    "SyntheticPopulationGenerator",
    "DEFAULT_AGE_GROUP_DISTRIBUTION",
    "DEFAULT_ROUTINE_DISTRIBUTION",
    "Activity",
    "ActivityType",
    "ODDemandEntry",
    "ActivityModel",
]
