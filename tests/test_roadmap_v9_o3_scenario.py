"""
Roadmap V9 / OMURGA / O.3 testleri
====================================

Kapsam: `SimulationScenario`/`BuildingSource`/`AgentProfileMix` doğrulama +
serileştirme, ve `ProjectDatabase`'in genel nesne deposu üzerinden
`save_scenario`/`load_scenario`/`list_scenarios`/`delete_scenario`
entegrasyonu (yeni bir tablo/şema icat edilmediğini de dolaylı olarak
doğrular — aynı `.hproj` dosyasında `kind='scenario'` ile saklanır).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.mobility.scenario import (
    AgentProfileMix,
    BuildingSource,
    HazardType,
    ScenarioValidationError,
    SimulationScenario,
    delete_scenario,
    list_scenarios,
    load_scenario,
    save_scenario,
)
from harita.persistence.db_backend import ProjectDatabase
from harita.persistence.project_format import ProjectManifest


def _make_scenario(scenario_id: str = "s1", **overrides) -> SimulationScenario:
    defaults = dict(
        scenario_id=scenario_id,
        name="Test Senaryosu",
        building=BuildingSource(building_ref="bina_1"),
        agents=AgentProfileMix(count=10, seed=1),
        hazard=HazardType.EARTHQUAKE,
    )
    defaults.update(overrides)
    return SimulationScenario(**defaults)


# --------------------------------------------------------------------------- #
# Doğrulama
# --------------------------------------------------------------------------- #


def test_valid_scenario_passes_validation():
    _make_scenario().validate()  # exception atmamalı


def test_empty_scenario_id_rejected():
    with pytest.raises(ScenarioValidationError):
        _make_scenario(scenario_id="").validate()


def test_negative_start_time_rejected():
    with pytest.raises(ScenarioValidationError):
        _make_scenario(start_time_s=-1.0).validate()


def test_building_source_requires_ref():
    with pytest.raises(ScenarioValidationError):
        BuildingSource.from_dict({"building_ref": ""})


def test_agent_profile_negative_count_rejected():
    with pytest.raises(ScenarioValidationError):
        AgentProfileMix.from_dict({"count": -5})


def test_behavior_distribution_must_sum_to_one():
    mix = AgentProfileMix(count=10, behavior_distribution={"normal": 0.5, "panic": 0.4})
    with pytest.raises(ScenarioValidationError):
        mix.validate()


def test_profile_distribution_must_sum_to_one_if_present():
    mix = AgentProfileMix(
        count=10,
        profile_distribution={"wheelchair": 0.05, "elderly_child": 0.5},
    )
    with pytest.raises(ScenarioValidationError):
        mix.validate()


def test_none_hazard_valid_for_daily_routine_scenario():
    sc = _make_scenario(hazard=HazardType.NONE)
    sc.validate()
    assert sc.hazard == HazardType.NONE


# --------------------------------------------------------------------------- #
# Serileştirme
# --------------------------------------------------------------------------- #


def test_to_dict_from_dict_roundtrip():
    sc = _make_scenario(disable_elevators=True, description="acik aciklama")
    restored = SimulationScenario.from_dict(sc.to_dict())
    assert restored.to_dict() == sc.to_dict()


def test_json_roundtrip():
    sc = _make_scenario()
    restored = SimulationScenario.from_json(sc.to_json())
    assert restored.to_dict() == sc.to_dict()


def test_from_dict_missing_required_field_raises_scenario_error():
    with pytest.raises(ScenarioValidationError):
        SimulationScenario.from_dict({"scenario_id": "x"})  # name/building/agents eksik


def test_from_json_invalid_json_raises_scenario_error():
    with pytest.raises(ScenarioValidationError):
        SimulationScenario.from_json("{not valid json")


def test_unknown_hazard_value_raises():
    data = _make_scenario().to_dict()
    data["hazard"] = "flood"  # henüz desteklenmeyen bir tip
    with pytest.raises(ScenarioValidationError):
        SimulationScenario.from_dict(data)


# --------------------------------------------------------------------------- #
# Persistence entegrasyonu (yeni depolama icat edilmedi)
# --------------------------------------------------------------------------- #


def test_save_and_load_scenario_roundtrip(tmp_path):
    db = ProjectDatabase.create(
        tmp_path / "sehir.hproj", ProjectManifest(name="Sehir", project_id="p1")
    )
    sc = _make_scenario()
    save_scenario(db, sc)

    loaded = load_scenario(db, "s1")
    assert loaded is not None
    assert loaded.to_dict() == sc.to_dict()
    db.close()


def test_load_missing_scenario_returns_none(tmp_path):
    db = ProjectDatabase.create(
        tmp_path / "sehir.hproj", ProjectManifest(name="Sehir", project_id="p1")
    )
    assert load_scenario(db, "yok") is None
    db.close()


def test_list_scenarios_only_returns_scenario_kind(tmp_path):
    db = ProjectDatabase.create(
        tmp_path / "sehir.hproj", ProjectManifest(name="Sehir", project_id="p1")
    )
    save_scenario(db, _make_scenario("s1"))
    save_scenario(db, _make_scenario("s2"))
    db.save_object("bina_1", "mesh3d", {"vertices": []})  # farklı kind - listeye girmemeli

    scenarios = list_scenarios(db)
    assert sorted(scenarios) == ["s1", "s2"]
    db.close()


def test_delete_scenario(tmp_path):
    db = ProjectDatabase.create(
        tmp_path / "sehir.hproj", ProjectManifest(name="Sehir", project_id="p1")
    )
    save_scenario(db, _make_scenario())
    assert delete_scenario(db, "s1") is True
    assert load_scenario(db, "s1") is None
    assert delete_scenario(db, "s1") is False  # zaten silinmiş
    db.close()


def test_save_scenario_rejects_invalid_before_persisting(tmp_path):
    db = ProjectDatabase.create(
        tmp_path / "sehir.hproj", ProjectManifest(name="Sehir", project_id="p1")
    )
    invalid = _make_scenario(scenario_id="")
    with pytest.raises(ScenarioValidationError):
        save_scenario(db, invalid)
    assert list_scenarios(db) == []
    db.close()


def test_scenario_persists_across_reopen(tmp_path):
    path = tmp_path / "sehir.hproj"
    db = ProjectDatabase.create(path, ProjectManifest(name="Sehir", project_id="p1"))
    save_scenario(db, _make_scenario())
    db.close()

    db2 = ProjectDatabase.open(path)
    loaded = load_scenario(db2, "s1")
    assert loaded is not None
    assert loaded.name == "Test Senaryosu"
    db2.close()
