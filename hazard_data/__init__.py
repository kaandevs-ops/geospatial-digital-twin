"""Hazard Data — Roadmap Faz 2.3 (Deprem ve afet verisi)

AFAD (Turkiye) ve USGS (uluslararasi) acik API'lerinden deprem verisi +
bolgesel PGA (Peak Ground Acceleration) tahmini + bina risk skorlama.

Bu paket, `core_engine.gis_core.osm_client` ile ayni durustluk ilkesini
izler: gercek ag erisimi varsa gercek veri ceker; bu ortamda (izin verilen
alan-adi listesi AFAD/USGS'i kapsamadigi icin) sessizce sahte veri
uretmez — acikca `HazardNetworkError` firlatir. `tests/` sadece gercek ag
erisimi varsa calisir, yoksa acikca skip edilir.

Bilesenler
----------
- `AFADClient` / `USGSClient`: gercek zamanli deprem kataloglari.
- `RegionalPGAEstimate`: bolgesel PGA degeri icin basit lookup + kullanicinin
  kendi (resmi TDTH / USGS ShakeMap) verisini enjekte edebilecegi arayuz.
- `BuildingRiskScorer`: yapim yili, kat sayisi, `structural_validation`
  narinlik orani ve bolgesel PGA'yi birlestirerek 0-100 arasi bir
  GOSTERGE NITELIGINDE risk indeksi hesaplar — kesin muhendislik raporu
  DEGILDIR, bu her raporda acikca belirtilir (roadmap'in kendi sarti).
- `evacuation`: risk skoru + mevcut `mobility.pathfinding` /
  `mobility.crowd_simulation` ile kacis rotasi onceliklendirmesi.
"""

from .afad_client import (
    AFADClient,
    AFADEarthquake,
    HazardError,
    HazardNetworkError,
    HazardParseError,
)
from .usgs_client import USGSClient, USGSEarthquake
from .pga_estimate import RegionalPGAEstimate, PGAZone, DEFAULT_TURKEY_PGA_ZONES
from .risk_scoring import (
    BuildingRiskFactor,
    BuildingRiskReport,
    RiskLevel,
    SoilType,
    score_building_risk,
)
from .evacuation import EvacuationPriority, prioritize_evacuation
from .flood_landslide import (
    TerrainHazardAnalyzer,
    TerrainHazardFactor,
    TerrainHazardReport,
)
from .flood_landslide import RiskLevel as TerrainRiskLevel
from .hazard_event import HazardEvent, HazardRegistry, HazardType
from .cascade_rules import CascadeEngine, CascadeRule, DEFAULT_CASCADE_RULES
from .resilience_timeline import (
    RecoveryRecord,
    ResilienceReport,
    SystemRecoveryPair,
    SYSTEM_EVENT_PAIRS,
    build_resilience_report,
)

__all__ = [
    "AFADClient", "AFADEarthquake", "HazardError", "HazardNetworkError", "HazardParseError",
    "USGSClient", "USGSEarthquake",
    "RegionalPGAEstimate", "PGAZone", "DEFAULT_TURKEY_PGA_ZONES",
    "BuildingRiskFactor", "BuildingRiskReport", "RiskLevel", "SoilType", "score_building_risk",
    "EvacuationPriority", "prioritize_evacuation",
    "TerrainHazardAnalyzer", "TerrainHazardFactor", "TerrainHazardReport", "TerrainRiskLevel",
    "HazardEvent", "HazardRegistry", "HazardType",
    "CascadeEngine", "CascadeRule", "DEFAULT_CASCADE_RULES",
    "RecoveryRecord", "ResilienceReport", "SystemRecoveryPair", "SYSTEM_EVENT_PAIRS",
    "build_resilience_report",
]

__version_note__ = "Faz 2.3 - hazard_data / Faz VII - Katman 7.1 + 7.4 + 7.6"
