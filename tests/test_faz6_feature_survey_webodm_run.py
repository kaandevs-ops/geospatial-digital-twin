"""
Roadmap FAZ 6.3 — Feature Survey: sunucu-taraflı klasörden WebODM görevi
başlatma/durum sorgulama/sonuç alma. Bu dosya `AppSession` metodlarının ve
karşılık gelen REST uçlarının, WebODM sunucusu erişilemez olduğunda dahi
açık hatalarla (sessiz mock yok) davrandığını doğrular.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.app_shell.api import build_app_router
from harita.app_shell.session import AppSession, AppSessionError


def _session_with_project():
    tmp = Path(tempfile.mkdtemp())
    registry = tmp / "registry.hprojreg"
    session = AppSession(registry)
    info = session.create_project("fs-webodm-test", path=str(tmp / "proj"))
    return session, info["project_id"], tmp


class TestFeatureSurveyWebODMSubmit:
    def test_submit_requires_existing_directory(self):
        session, pid, tmp = _session_with_project()
        with pytest.raises(AppSessionError):
            session.feature_survey_webodm_submit(
                pid, base_url="http://127.0.0.1:1", image_dir=str(tmp / "nope")
            )

    def test_submit_requires_images_in_directory(self):
        session, pid, tmp = _session_with_project()
        empty_dir = tmp / "photos_empty"
        empty_dir.mkdir()
        with pytest.raises(AppSessionError):
            session.feature_survey_webodm_submit(
                pid, base_url="http://127.0.0.1:1", image_dir=str(empty_dir)
            )

    def test_submit_unreachable_server_raises_clear_error(self):
        session, pid, tmp = _session_with_project()
        photo_dir = tmp / "photos"
        photo_dir.mkdir()
        (photo_dir / "img1.jpg").write_bytes(b"\xff\xd8\xff\xe0fake")
        with pytest.raises(AppSessionError):
            session.feature_survey_webodm_submit(
                pid, base_url="http://127.0.0.1:1", image_dir=str(photo_dir)
            )

    def test_status_without_prior_submit_raises(self):
        session, pid, _tmp = _session_with_project()
        with pytest.raises(AppSessionError):
            session.feature_survey_webodm_status(pid)

    def test_fetch_without_prior_submit_raises(self):
        session, pid, tmp = _session_with_project()
        with pytest.raises(AppSessionError):
            session.feature_survey_webodm_fetch(pid, output_dir=str(tmp / "out"))


class TestFeatureSurveyWebODMRestEndpoints:
    def _router_with_project(self):
        session, pid, tmp = _session_with_project()
        router = build_app_router(session)
        return router, pid, tmp

    def test_submit_endpoint_returns_422_style_error_for_missing_dir(self):
        router, pid, tmp = self._router_with_project()
        resp = router.dispatch(
            "POST",
            f"/api/projects/{pid}/feature-survey/webodm/submit",
            body={"base_url": "http://127.0.0.1:1", "image_dir": str(tmp / "missing")},
        )
        assert resp.status >= 400
        assert "error" in resp.body

    def test_status_endpoint_errors_without_job(self):
        router, pid, _tmp = self._router_with_project()
        resp = router.dispatch("GET", f"/api/projects/{pid}/feature-survey/webodm/status")
        assert resp.status >= 400

    def test_fetch_endpoint_errors_without_job(self):
        router, pid, tmp = self._router_with_project()
        resp = router.dispatch(
            "POST",
            f"/api/projects/{pid}/feature-survey/webodm/fetch",
            body={"output_dir": str(tmp / "out")},
        )
        assert resp.status >= 400
