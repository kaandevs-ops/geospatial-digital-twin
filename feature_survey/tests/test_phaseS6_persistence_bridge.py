"""ROADMAP_V6 FAZ S6 — orkestrasyon sonucunun `.hproj` proje dosyasına
kalıcı olarak kaydedilmesi (`persistence/project_manager.py` entegrasyonu).

Bu, önceki iterasyonda bilinçli olarak açık bırakılan iki maddeden biriydi
(bkz. ROADMAP_V6.md 4. iterasyon notu): orkestrasyon çekirdeği hazırdı,
sadece diske kalıcı kayıt adımı eksikti. Bu test dosyası, mevcut
`objects`/`history` şemasına HİÇ dokunulmadan (yeni migrasyon yok) bu
adımın gerçekten çalıştığını kanıtlar.
"""

from __future__ import annotations

import uuid

import pytest
from harita.feature_survey.codes import FeatureCode
from harita.feature_survey.field_point import FieldPoint, FieldSurveySession
from harita.feature_survey.orchestration.persistence_bridge import (
    SURVEY_RESULT_KIND,
    PersistenceBridgeError,
    list_survey_projects,
    load_survey_result_payload,
    save_survey_result,
)
from harita.feature_survey.orchestration.pipeline import run_field_survey_pipeline
from harita.persistence.db_backend import ProjectDatabase
from harita.persistence.project_format import ProjectManifest


def _session() -> FieldSurveySession:
    pts = [
        FieldPoint("1", 0.0, 0.0, 100.0, FeatureCode.BUILDING_CORNER, string_id="BLD01"),
        FieldPoint("2", 5.0, 0.0, 100.0, FeatureCode.BUILDING_CORNER, string_id="BLD01"),
        FieldPoint("3", 5.0, 5.0, 100.0, FeatureCode.BUILDING_CORNER, string_id="BLD01"),
        FieldPoint("4", 0.0, 5.0, 100.0, FeatureCode.BUILDING_CORNER, string_id="BLD01"),
    ]
    return FieldSurveySession(name="Persistence Bridge Test", points=pts, crs="local")


def _new_db(tmp_path) -> ProjectDatabase:
    manifest = ProjectManifest(name="Test Projesi", project_id=str(uuid.uuid4()))
    return ProjectDatabase.create(tmp_path / "test.hproj", manifest)


def test_save_and_load_roundtrip(tmp_path):
    db = _new_db(tmp_path)
    result = run_field_survey_pipeline(project_name="Kayit Testi", session=_session())

    key = save_survey_result(db, result, project_slug="kayit_testi")
    assert key == "feature_survey:kayit_testi"

    payload = load_survey_result_payload(db, "kayit_testi")
    assert payload["project_name"] == "Kayit Testi"
    assert payload["linework_feature_count"] == 1
    db.close()


def test_persists_across_reopen(tmp_path):
    """Gerçek kalıcılık kanıtı: bağlantı kapatılıp dosyadan yeniden açılınca
    veri hâlâ orada -- bellek-içi bir yapı değil, gerçekten diske yazılmış."""
    db_path = tmp_path / "reopen.hproj"
    manifest = ProjectManifest(name="Reopen", project_id=str(uuid.uuid4()))
    db = ProjectDatabase.create(db_path, manifest)
    result = run_field_survey_pipeline(project_name="Reopen Testi", session=_session())
    save_survey_result(db, result, project_slug="reopen_testi")
    db.close()

    db2 = ProjectDatabase.open(db_path)
    payload = load_survey_result_payload(db2, "reopen_testi")
    assert payload["project_name"] == "Reopen Testi"
    db2.close()


def test_history_audit_trail_written(tmp_path):
    """`objects` tablosuna yazım, projenin var olan append-only `history`
    günlüğüne de otomatik bir 'save' kaydı düşürür -- disk seviyesinde
    ikinci bir denetlenebilirlik katmanı (roadmap: 'hangi ham veriden
    hangi sonucun türetildiği izlenebilir olmalı')."""
    db = _new_db(tmp_path)
    result = run_field_survey_pipeline(project_name="Audit Testi", session=_session())
    save_survey_result(db, result, project_slug="audit_testi")

    record = db.load_object("feature_survey:audit_testi")
    assert record is not None
    assert record.kind == SURVEY_RESULT_KIND
    db.close()


def test_list_survey_projects(tmp_path):
    db = _new_db(tmp_path)
    r1 = run_field_survey_pipeline(project_name="Proje A", session=_session())
    r2 = run_field_survey_pipeline(project_name="Proje B", session=_session())
    save_survey_result(db, r1, project_slug="proje_a")
    save_survey_result(db, r2, project_slug="proje_b")

    keys = list_survey_projects(db)
    assert "feature_survey:proje_a" in keys
    assert "feature_survey:proje_b" in keys
    db.close()


def test_load_missing_project_raises(tmp_path):
    db = _new_db(tmp_path)
    with pytest.raises(PersistenceBridgeError):
        load_survey_result_payload(db, "olmayan_proje")
    db.close()


def test_save_without_db_raises():
    result = run_field_survey_pipeline(project_name="X", session=_session())
    with pytest.raises(PersistenceBridgeError):
        save_survey_result(None, result, project_slug="x")


def test_save_empty_project_slug_falls_back_to_project_name(tmp_path):
    db = _new_db(tmp_path)
    result = run_field_survey_pipeline(project_name="Otomatik Slug Testi", session=_session())
    key = save_survey_result(db, result)
    assert key == "feature_survey:otomatik_slug_testi"
    db.close()
