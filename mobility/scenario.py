"""
Senaryo Tanım Şeması (Scenario Schema)
========================================

Roadmap V9 / OMURGA / O.3 — "Senaryo Tanım Şeması".

Sorun (roadmap'te tespit edilen): şu ana kadar her simülasyon Python
kodunda elle kuruluyordu (`spawn_random_agents` çağrısı, manuel `NavGraph`
inşası). Kullanıcının arayüzden "bu binada deprem senaryosu çalıştır"
diyebilmesi için bildirimsel (declarative) bir **JSON senaryo tanımı**
gerekir: bina/kat kaynağı, agent sayısı ve profili, tehlike tipi
(deprem/yangın/yok), başlangıç zamanı.

Depolama: roadmap'in "ayrı bir depolama icat edilmez, mevcut `persistence`
versiyonlamasına entegre edilir" ilkesi harfiyen uygulanıyor — yeni bir
tablo/şema **eklenmedi**. `persistence/db_backend.py`'deki genel
`ProjectDatabase.save_object(key, kind, data)` / `load_object` /
`list_objects(kind=...)` API'si zaten "herhangi bir JSON-serileştirilebilir
nesneyi anahtar+tür ile sakla" desenini sağlıyor; bu modül yalnızca
`kind="scenario"` ile bu deseni kullanan ince bir sarmalayıcı sağlar (bkz.
`save_scenario` / `load_scenario` / `list_scenarios` altta).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - yalnızca tip kontrolü, döngüsel import yok
    from ..persistence.db_backend import ProjectDatabase

__all__ = [
    "ScenarioValidationError",
    "HazardType",
    "BuildingSource",
    "AgentProfileMix",
    "SimulationScenario",
    "save_scenario",
    "load_scenario",
    "list_scenarios",
    "delete_scenario",
]

#: Bu şemanın sürümü. `SimulationScenario.schema_version` alanına yazılır;
#: gelecekte alan eklenir/anlamı değişirse burada takip edilir (aynı
#: `persistence/project_format.FORMAT_VERSION` disiplini, ama senaryo JSON'u
#: proje dosyası şeması değil, `objects` tablosundaki bir `data` payload'ı
#: olduğundan ayrı ve daha hafif bir sürüm sayacı kullanılır).
SCENARIO_SCHEMA_VERSION = 1


class ScenarioValidationError(ValueError):
    """Senaryo tanımı şemaya uymuyor veya değerleri tutarsız."""


class HazardType(str, Enum):
    """Katman 7 / Katman 2.4 ile uyumlu tehlike tipleri.

    `NONE`, Katman 2/3'ün "sıradan bir Salı günü" (Faz VI) senaryolarına
    karşılık gelir — afet yok, yalnızca gündelik rutin.
    """

    NONE = "none"
    EARTHQUAKE = "earthquake"
    FIRE = "fire"


@dataclass(slots=True)
class BuildingSource:
    """Senaryonun hangi bina/kat verisi üzerinde çalışacağını tanımlar.

    `building_ref`, uygulamanın nesne deposundaki (`ProjectDatabase.
    save_object`'la kaydedilmiş bir bina/mesh/`TwinHierarchy` düğümü)
    anahtarına işaret eder — bu modül binanın kendisini yeniden modellemez,
    yalnızca ona referans taşır (roadmap ilkesi: "tekrar yazma yok").
    `floor_ids` boşsa senaryo binanın tüm katlarını kapsar.
    """

    building_ref: str
    floor_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"building_ref": self.building_ref, "floor_ids": list(self.floor_ids)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BuildingSource:
        if "building_ref" not in data or not data["building_ref"]:
            raise ScenarioValidationError("BuildingSource.building_ref zorunlu ve boş olamaz")
        return cls(
            building_ref=data["building_ref"],
            floor_ids=tuple(data.get("floor_ids", [])),
        )


@dataclass(slots=True)
class AgentProfileMix:
    """Agent sayısı ve profil dağılımı.

    Katman 2.1'in tam `mobility_profile`/`reaction_time_s` alanları henüz
    `crowd_simulation.Agent`'a eklenmedi (bkz. ROADMAP_V9.md Katman 2.1 —
    ayrı, sonraki bir iş kalemi); bu yüzden burada **ileriye dönük ama
    bugün de kullanılabilir** bir orta katman tercih edildi:
    `profile_distribution`, Katman 2.1 geldiğinde doğrudan
    `spawn_random_agents`'a genişletilmiş bir profil parametresi olarak
    aktarılabilecek şekilde tasarlandı (anahtar isimleri Katman 2.1
    dokümanındaki örnek yüzdelerle - "%5 tekerlekli sandalye, %10 yaşlı/
    çocuk" - tutarlı), ama bugün yalnızca toplam `count` ve mevcut
    `AgentBehavior` (`normal/cautious/hurried/panic`) dağılımı zorunlu
    kılınır - `crowd_simulation`'da gerçekten var olan tek agent boyutu bu.
    """

    count: int
    behavior_distribution: dict[str, float] = field(default_factory=lambda: {"normal": 1.0})
    # Katman 2.1 ön-tanımı (henüz motor tarafında tüketilmiyor, yalnızca
    # senaryo dosyasında taşınıyor - motor hazır olduğunda buradan okunacak).
    profile_distribution: dict[str, float] = field(default_factory=dict)
    seed: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "behavior_distribution": dict(self.behavior_distribution),
            "profile_distribution": dict(self.profile_distribution),
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentProfileMix:
        if "count" not in data:
            raise ScenarioValidationError("AgentProfileMix.count zorunlu")
        count = int(data["count"])
        if count < 0:
            raise ScenarioValidationError("AgentProfileMix.count negatif olamaz")
        return cls(
            count=count,
            behavior_distribution=dict(data.get("behavior_distribution", {"normal": 1.0})),
            profile_distribution=dict(data.get("profile_distribution", {})),
            seed=data.get("seed"),
        )

    def validate(self) -> None:
        total = sum(self.behavior_distribution.values())
        if self.behavior_distribution and abs(total - 1.0) > 1e-6:
            raise ScenarioValidationError(
                f"behavior_distribution toplamı 1.0 olmalı, {total} bulundu"
            )
        if self.profile_distribution:
            p_total = sum(self.profile_distribution.values())
            if abs(p_total - 1.0) > 1e-6:
                raise ScenarioValidationError(
                    f"profile_distribution toplamı 1.0 olmalı, {p_total} bulundu"
                )


@dataclass(slots=True)
class SimulationScenario:
    """Bir simülasyon koşumunun bildirimsel tanımı.

    Kullanım:
        scenario = SimulationScenario(
            scenario_id="deprem_demo_1",
            name="Merkez Bina — Deprem Tahliyesi",
            building=BuildingSource(building_ref="bina_42"),
            agents=AgentProfileMix(count=87, seed=7),
            hazard=HazardType.EARTHQUAKE,
            start_time_s=0.0,
            disable_elevators=True,   # Katman 2.4 madde 2 ile uyumlu
        )
        save_scenario(db, scenario)
        ...
        loaded = load_scenario(db, "deprem_demo_1")
    """

    scenario_id: str
    name: str
    building: BuildingSource
    agents: AgentProfileMix
    hazard: HazardType = HazardType.NONE
    start_time_s: float = 0.0
    # Katman 2.4 madde 2 ("Asansör kullanma kısıtı") — deprem senaryosunda
    # varsayılan olarak True önerilir, ama motor tarafında henüz tüketilmiyor
    # (indoor_navigation'a bağlanma işi ayrı bir sonraki adım); şimdilik
    # senaryo dosyasında taşınıp gelecekteki tüketiciye hazır bekliyor.
    disable_elevators: bool = False
    description: str = ""
    created_at: float = field(default_factory=time.time)
    schema_version: int = SCENARIO_SCHEMA_VERSION

    def validate(self) -> None:
        if not self.scenario_id:
            raise ScenarioValidationError("scenario_id boş olamaz")
        if not self.name:
            raise ScenarioValidationError("name boş olamaz")
        if self.start_time_s < 0:
            raise ScenarioValidationError("start_time_s negatif olamaz")
        if not isinstance(self.hazard, HazardType):
            raise ScenarioValidationError(f"Geçersiz hazard tipi: {self.hazard!r}")
        self.agents.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "name": self.name,
            "description": self.description,
            "building": self.building.to_dict(),
            "agents": self.agents.to_dict(),
            "hazard": self.hazard.value,
            "start_time_s": self.start_time_s,
            "disable_elevators": self.disable_elevators,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationScenario:
        try:
            hazard_raw = data.get("hazard", HazardType.NONE.value)
            hazard = HazardType(hazard_raw)
            scenario = cls(
                scenario_id=data["scenario_id"],
                name=data["name"],
                description=data.get("description", ""),
                building=BuildingSource.from_dict(data["building"]),
                agents=AgentProfileMix.from_dict(data["agents"]),
                hazard=hazard,
                start_time_s=float(data.get("start_time_s", 0.0)),
                disable_elevators=bool(data.get("disable_elevators", False)),
                created_at=float(data.get("created_at", time.time())),
                schema_version=int(data.get("schema_version", SCENARIO_SCHEMA_VERSION)),
            )
        except KeyError as exc:
            raise ScenarioValidationError(f"Senaryo tanımında eksik alan: {exc}") from exc
        except ValueError as exc:
            raise ScenarioValidationError(f"Bozuk senaryo tanımı: {exc}") from exc
        scenario.validate()
        return scenario

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> SimulationScenario:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ScenarioValidationError(f"Geçersiz JSON: {exc}") from exc
        return cls.from_dict(data)


# ============================================================ #
# Persistence entegrasyonu (yeni depolama icat edilmedi — bkz. modül
# başlığı: mevcut `ProjectDatabase.save_object/load_object/list_objects`
# genel nesne deposu `kind="scenario"` ile yeniden kullanılıyor).
# ============================================================ #

_SCENARIO_KIND = "scenario"


def save_scenario(db: ProjectDatabase, scenario: SimulationScenario) -> None:
    """Senaryoyu doğrulayıp `db`'ye kaydeder (`scenario.scenario_id`
    anahtarıyla — üzerine yazma davranışı `save_object`'inkiyle aynı)."""
    scenario.validate()
    db.save_object(scenario.scenario_id, _SCENARIO_KIND, scenario.to_dict())


def load_scenario(db: ProjectDatabase, scenario_id: str) -> SimulationScenario | None:
    """`scenario_id` ile kayıtlı senaryoyu yükler; yoksa `None`."""
    record = db.load_object(scenario_id)
    if record is None or record.kind != _SCENARIO_KIND:
        return None
    return SimulationScenario.from_dict(record.data)


def list_scenarios(db: ProjectDatabase) -> list[str]:
    """Projedeki tüm senaryo kimliklerini döner (`kind="scenario"` filtreli
    — `list_objects` zaten bunu destekliyor, tekrar kod yazılmadı)."""
    return db.list_objects(kind=_SCENARIO_KIND)


def delete_scenario(db: ProjectDatabase, scenario_id: str) -> bool:
    return db.delete_object(scenario_id)
