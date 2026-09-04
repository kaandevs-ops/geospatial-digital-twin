"""Roadmap V4 - Faz E10 kabul kriteri testleri.

Kapsam (Faz E10, ROADMAP_V4.md):
    "Viewer'in destekledigi bir tarayicida gercek GPU zamanlamasi alinip
    `GPUProfiler` raporuna yansir; desteklenmiyorsa mevcut
    yazilim-simulasyonuna sessizce dusulur (regresyon yok)."

Bu ortamda gercek bir tarayici/WebGL2 baglami calistirilamadigindan,
`render_engine/viewer/index.html`'in JS tarafi (EXT_disjoint_timer_query_webgl2
algilama + `beginGpuTimer/endGpuTimer/pollGpuTimer` + `/api/performance/gpu-timing`
POST kopru cagrisi) statik olarak (kaynak icinde beklenen desenlerin varligi)
dogrulanir; Python tarafi (`GPUProfiler.record_gpu_timing/average_gpu_time_ms`
+ `app_shell/api.py` REST uclari) ise gercekten calistirilarak test edilir.

Bu dosya, projedeki diger `test_phase*.py` dosyalariyla ayni desende:
fixture'siz duz `test_*()` fonksiyonlari + `__main__` calistirici.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.performance.profiler import GPUProfiler
from harita.app_shell.api import build_app_router
from harita.app_shell.session import AppSession


def _new_session() -> AppSession:
    tmp = tempfile.mkdtemp()
    reg = os.path.join(tmp, "registry.json")
    return AppSession(reg)


def test_gpu_profiler_ignores_unsupported_timing():
    prof = GPUProfiler()
    prof.begin_frame()
    prof.draw_call(100)
    prof.record_gpu_timing(5.0, supported=False)
    frame = prof.end_frame()
    assert frame.gpu_timing_supported is False
    assert frame.gpu_time_ms is None
    assert prof.average_gpu_time_ms() is None  # yazilim-simulasyonuna sessiz dusme


def test_gpu_profiler_records_supported_timing():
    prof = GPUProfiler()
    prof.begin_frame()
    prof.draw_call(200)
    prof.record_gpu_timing(3.2, supported=True)
    frame = prof.end_frame()
    assert frame.gpu_timing_supported is True
    assert frame.gpu_time_ms == 3.2
    assert prof.average_gpu_time_ms() == 3.2


def test_gpu_profiler_average_over_multiple_frames():
    prof = GPUProfiler()
    for t in (2.0, 4.0, 6.0):
        prof.begin_frame()
        prof.record_gpu_timing(t, supported=True)
        prof.end_frame()
    assert prof.average_gpu_time_ms() == 4.0


def test_gpu_profiler_record_without_active_frame_is_noop():
    prof = GPUProfiler()
    prof.record_gpu_timing(9.0, supported=True)  # begin_frame() cagrilmadi
    assert prof.average_gpu_time_ms() is None


def _make_router():
    session = _new_session()
    gpu_profiler = GPUProfiler()
    router = build_app_router(session, gpu_profiler=gpu_profiler)
    return router, gpu_profiler


def test_rest_endpoint_reports_gpu_timing():
    router, gpu_profiler = _make_router()
    gpu_profiler.begin_frame()
    resp = router.dispatch(
        "POST", "/api/performance/gpu-timing",
        body={"gpu_time_ms": 4.5, "supported": True},
    )
    assert resp.status == 200
    assert resp.body["recorded"] is True
    gpu_profiler.end_frame()

    get_resp = router.dispatch("GET", "/api/performance/gpu-timing")
    assert get_resp.status == 200
    assert get_resp.body["hardware_timing_supported"] is True
    assert get_resp.body["average_gpu_time_ms"] == 4.5


def test_rest_endpoint_rejects_non_numeric_timing():
    router, _ = _make_router()
    resp = router.dispatch(
        "POST", "/api/performance/gpu-timing",
        body={"gpu_time_ms": "not-a-number"},
    )
    assert resp.status == 422


def test_rest_endpoint_absent_without_profiler():
    session = _new_session()
    router = build_app_router(session)  # gpu_profiler verilmedi
    try:
        router.dispatch("GET", "/api/performance/gpu-timing")
        raise AssertionError("route kayitli olmamali (gpu_profiler verilmedi)")
    except Exception as exc:  # noqa: BLE001 - route bulunamadi hatasi bekleniyor
        assert "bulunamadı" in str(exc) or "not found" in str(exc).lower()


def test_viewer_js_declares_timer_query_bridge():
    viewer = Path(__file__).resolve().parents[1] / "render_engine" / "viewer" / "index.html"
    src = viewer.read_text(encoding="utf-8")
    assert "EXT_disjoint_timer_query_webgl2" in src
    assert "beginGpuTimer" in src
    assert "endGpuTimer" in src
    assert "pollGpuTimer" in src
    assert "/api/performance/gpu-timing" in src


_ALL_TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    failures = []
    for fn in _ALL_TESTS:
        try:
            fn()
            print(f"OK   {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(_ALL_TESTS) - len(failures)}/{len(_ALL_TESTS)} geçti.")
    if failures:
        sys.exit(1)
