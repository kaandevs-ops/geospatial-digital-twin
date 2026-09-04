"""
Roadmap Faz 5.4 — Rate Limiting.

Genel denetimde tespit edilen gerçek bir boşluk: proje genelinde hiçbir
rate-limiting mekanizması yoktu (ne login brute-force koruması, ne de
Overpass/AI gibi harici çağrılar için bir kota). Bu dosya yeni
`security.rate_limiter.SlidingWindowRateLimiter`'ı, `collaboration.auth`
brute-force entegrasyonunu ve `app_shell/api.py` OSM-import 429 davranışını
doğrular.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tempfile

import pytest

from harita.app_shell.api import build_app_router
from harita.app_shell.session import AppSession
from harita.collaboration.auth import (
    AuthService,
    InvalidCredentialsError,
    TooManyLoginAttemptsError,
)
from harita.security.rate_limiter import RateLimitExceededError, SlidingWindowRateLimiter


class _FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestSlidingWindowRateLimiter:
    def test_allows_up_to_max_requests(self):
        clock = _FakeClock()
        rl = SlidingWindowRateLimiter(max_requests=3, window_seconds=10, time_fn=clock)
        assert rl.allow("a") is True
        assert rl.allow("a") is True
        assert rl.allow("a") is True
        assert rl.allow("a") is False

    def test_window_expires_and_frees_capacity(self):
        clock = _FakeClock()
        rl = SlidingWindowRateLimiter(max_requests=2, window_seconds=5, time_fn=clock)
        assert rl.allow("a") is True
        assert rl.allow("a") is True
        assert rl.allow("a") is False
        clock.advance(5.01)
        assert rl.allow("a") is True

    def test_keys_are_independent(self):
        clock = _FakeClock()
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=10, time_fn=clock)
        assert rl.allow("a") is True
        assert rl.allow("b") is True
        assert rl.allow("a") is False
        assert rl.allow("b") is False

    def test_allow_or_raise(self):
        clock = _FakeClock()
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=10, time_fn=clock)
        rl.allow_or_raise("a")
        with pytest.raises(RateLimitExceededError):
            rl.allow_or_raise("a")

    def test_reset_clears_specific_key(self):
        clock = _FakeClock()
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=100, time_fn=clock)
        rl.allow("a")
        assert rl.allow("a") is False
        rl.reset("a")
        assert rl.allow("a") is True

    def test_retry_after_reports_positive_when_limited(self):
        clock = _FakeClock()
        rl = SlidingWindowRateLimiter(max_requests=1, window_seconds=10, time_fn=clock)
        rl.allow("a")
        assert rl.retry_after("a") > 0

    def test_purge_stale_removes_old_keys(self):
        clock = _FakeClock()
        rl = SlidingWindowRateLimiter(max_requests=5, window_seconds=10, time_fn=clock)
        rl.allow("a")
        clock.advance(50)
        removed = rl.purge_stale()
        assert removed == 1

    def test_rejects_invalid_config(self):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(max_requests=0, window_seconds=10)
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(max_requests=1, window_seconds=0)


class TestAuthBruteForceLockout:
    def test_lockout_after_max_failed_attempts(self):
        auth = AuthService()
        auth.register("alice", "correct-horse")
        for _ in range(5):
            with pytest.raises(InvalidCredentialsError):
                auth.login("alice", "wrong")
        with pytest.raises(TooManyLoginAttemptsError):
            auth.login("alice", "wrong")

    def test_successful_login_resets_counter(self):
        auth = AuthService()
        auth.register("bob", "correct-horse")
        for _ in range(4):
            with pytest.raises(InvalidCredentialsError):
                auth.login("bob", "wrong")
        # Doğru parolayla giriş - sayaç sıfırlanmalı.
        auth.login("bob", "correct-horse")
        for _ in range(4):
            with pytest.raises(InvalidCredentialsError):
                auth.login("bob", "wrong")
        # Hâlâ 5. deneme olmadığı için TooManyLoginAttemptsError fırlatılmamalı.

    def test_lockout_is_per_username(self):
        auth = AuthService()
        auth.register("carol", "pw1")
        auth.register("dave", "pw2")
        for _ in range(5):
            with pytest.raises(InvalidCredentialsError):
                auth.login("carol", "wrong")
        # dave için hâlâ girişe izin verilmeli.
        token = auth.login("dave", "pw2")
        assert token.token


class TestOsmImportRateLimitHttp:
    def _router_with_project(self):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("rl-test", path=str(tmp / "proj"))
        router = build_app_router(session)
        return router, info["project_id"]

    def test_returns_429_after_limit(self, monkeypatch):
        router, pid = self._router_with_project()

        import harita.core_engine.gis_core.osm_client as osm_mod

        def _fail_client(*a, **k):
            client = osm_mod.OverpassClient()
            client.fetch_raw = lambda bbox: (_ for _ in ()).throw(
                osm_mod.OverpassError("kasıtlı test hatası")
            )
            return client

        monkeypatch.setattr(
            "harita.app_shell.session.OverpassClient", _fail_client,
        )
        body = dict(south=39.9198, west=32.8539, north=39.9215, east=32.8557)
        statuses = []
        for _ in range(11):
            resp = router.dispatch("POST", f"/api/projects/{pid}/osm/import", body=body)
            statuses.append(resp.status)
        assert 429 in statuses
