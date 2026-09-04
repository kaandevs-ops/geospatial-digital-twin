"""ROADMAP_V6 FAZ S6 — web arayüzü entegrasyonu: saha projesi yükleme,
uçtan uca orkestrasyon çalıştırma ve `.hproj` dosyasına kalıcı kayıt.

Bu, ROADMAP_V6.md'nin 4. iterasyon notunda bilinçli olarak açık bırakılan
"S6 web arayüzü ve persistence entegrasyonu" maddesinin kapatıldığının
kanıtıdır: `AppSession.feature_survey_run_orchestration` +
`feature_survey_orchestration_result` metodları ve karşılık gelen
`POST/GET /api/projects/<id>/feature-survey/orchestrate` REST uçları.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.app_shell.api import build_app_router
from harita.app_shell.session import AppSession, AppSessionError

_PENZD_CSV = """PT,E,N,Z,DESC
1,0,0,100,BLD_COR
2,10,0,100,BLD_COR
3,10,10,100,BLD_COR
4,0,10,100,BLD_COR
"""

_CLOSURE_KWARGS = dict(
    angular_closure={
        "n_points": 4,
        "measured_sum_gon": 399.9,
        "theoretical_sum_gon": 400.0,
        "closure_error_gon": 0.1,
    },
    linear_closure={
        "delta_easting_sum_m": 0.02,
        "delta_northing_sum_m": -0.01,
        "closure_distance_m": 0.0223,
        "perimeter_m": 40.0,
        "relative_precision": 40.0 / 0.0223,
    },
    angular_tolerance_gon=0.15,
    max_relative_precision=5000.0,
    closure_standard_reference="TUJJB Topografik Ölçüm Yönetmeliği (test)",
)


def _session_with_project():
    tmp = Path(tempfile.mkdtemp())
    registry = tmp / "registry.hprojreg"
    session = AppSession(registry)
    info = session.create_project("fs-orchestration-test", path=str(tmp / "proj"))
    return session, info["project_id"], tmp


class TestFeatureSurveyOrchestrationSession:
    def test_orchestration_requires_some_input(self):
        session, pid, _ = _session_with_project()
        with pytest.raises(AppSessionError):
            session.feature_survey_run_orchestration(pid)

    def test_full_pipeline_via_imported_session(self):
        session, pid, _ = _session_with_project()
        imp = session.feature_survey_import_penzd(pid, csv_text=_PENZD_CSV, has_header=True)
        assert imp["imported_points"] == 4

        result = session.feature_survey_run_orchestration(pid, **_CLOSURE_KWARGS)
        assert result["linework_feature_count"] == 1
        assert result["qc_overall_passed"] is True
        assert len(result["audit_trail"]) >= 3

    def test_result_persists_to_disk_across_reopen(self):
        session, pid, tmp = _session_with_project()
        session.feature_survey_import_penzd(pid, csv_text=_PENZD_CSV, has_header=True)
        session.feature_survey_run_orchestration(pid, persist=True, **_CLOSURE_KWARGS)
        session.close()

        registry = tmp / "registry.hprojreg"
        session2 = AppSession(registry)
        session2.open_project(project_id=pid)
        loaded = session2.feature_survey_orchestration_result(pid)
        assert loaded["linework_feature_count"] == 1
        assert loaded["qc_overall_passed"] is True
        session2.close()

    def test_result_not_persisted_when_persist_false(self):
        session, pid, _ = _session_with_project()
        session.feature_survey_import_penzd(pid, csv_text=_PENZD_CSV, has_header=True)
        session.feature_survey_run_orchestration(pid, persist=False, **_CLOSURE_KWARGS)
        with pytest.raises(AppSessionError):
            session.feature_survey_orchestration_result(pid)

    def test_negative_scenario_bad_closure_is_reported_not_hidden(self):
        session, pid, _ = _session_with_project()
        session.feature_survey_import_penzd(pid, csv_text=_PENZD_CSV, has_header=True)
        bad_kwargs = dict(_CLOSURE_KWARGS)
        bad_kwargs["angular_closure"] = {
            "n_points": 4,
            "measured_sum_gon": 395.0,
            "theoretical_sum_gon": 400.0,
            "closure_error_gon": 5.0,
        }
        result = session.feature_survey_run_orchestration(pid, **bad_kwargs)
        assert result["qc_overall_passed"] is False


class TestFeatureSurveyOrchestrationRestApi:
    def test_orchestrate_endpoint_roundtrip(self):
        session, pid, _ = _session_with_project()
        router = build_app_router(session)
        session.feature_survey_import_penzd(pid, csv_text=_PENZD_CSV, has_header=True)

        post_resp = router.dispatch(
            "POST", f"/api/projects/{pid}/feature-survey/orchestrate", body=_CLOSURE_KWARGS
        )
        assert post_resp.status == 200
        assert post_resp.body["qc_overall_passed"] is True

        get_resp = router.dispatch("GET", f"/api/projects/{pid}/feature-survey/orchestrate")
        assert get_resp.status == 200
        assert get_resp.body["linework_feature_count"] == 1

    def test_orchestrate_endpoint_no_input_returns_400(self):
        session, pid, _ = _session_with_project()
        router = build_app_router(session)
        resp = router.dispatch("POST", f"/api/projects/{pid}/feature-survey/orchestrate", body={})
        assert resp.status == 400
