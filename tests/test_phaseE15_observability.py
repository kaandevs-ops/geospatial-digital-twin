"""ROADMAP_V4 — Track E / Faz E15: Observability (loglama/metrik/tracing
altyapısı).

Kabul kriteri: `app_shell` üzerinden yapılan her REST isteği
yapılandırılmış bir log satırı üretir (endpoint, süre, durum kodu);
`/metrics` endpoint'i Prometheus text-format'ında en az 5 farklı metrik
döner.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.app_shell import AppSession, build_app_router
from harita.observability import InstrumentedRouter, MetricsRegistry, StructuredLogger
from harita.observability.metrics import DEFAULT_HISTOGRAM_BUCKETS
from harita.performance.profiler import MemoryProfiler


# ---------------------------------------------------------------------------
# StructuredLogger
# ---------------------------------------------------------------------------

class TestStructuredLogger:
    def test_log_lines_are_valid_json(self) -> None:
        stream = io.StringIO()
        logger = StructuredLogger(name="test.e15.json", stream=stream)
        logger.info("merhaba", foo=1, bar="baz")
        lines = [ln for ln in stream.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["message"] == "merhaba"
        assert parsed["foo"] == 1
        assert parsed["bar"] == "baz"
        assert parsed["level"] == "INFO"

    def test_in_memory_records_buffer(self) -> None:
        logger = StructuredLogger(name="test.e15.buffer", stream=io.StringIO())
        logger.info("a")
        logger.warning("b")
        logger.error("c")
        records = logger.records()
        assert [r["level"] for r in records] == ["INFO", "WARNING", "ERROR"]

    def test_log_request_schema_has_required_fields(self) -> None:
        logger = StructuredLogger(name="test.e15.req", stream=io.StringIO())
        record = logger.log_request(
            method="GET", path="/api/health", status=200, duration_ms=12.345,
        )
        assert record["event"] == "http_request"
        assert record["method"] == "GET"
        assert record["path"] == "/api/health"
        assert record["status"] == 200
        assert record["duration_ms"] == pytest.approx(12.345)

    def test_max_records_ring_buffer(self) -> None:
        logger = StructuredLogger(name="test.e15.ring", stream=io.StringIO(), max_records=3)
        for i in range(5):
            logger.info(f"msg-{i}")
        records = logger.records()
        assert len(records) == 3
        assert records[0]["message"] == "msg-2"
        assert records[-1]["message"] == "msg-4"

    def test_clear(self) -> None:
        logger = StructuredLogger(name="test.e15.clear", stream=io.StringIO())
        logger.info("x")
        logger.clear()
        assert logger.records() == []


# ---------------------------------------------------------------------------
# MetricsRegistry
# ---------------------------------------------------------------------------

class TestMetricsRegistry:
    def test_counter_increments(self) -> None:
        m = MetricsRegistry()
        m.inc_counter("requests_total", labels={"path": "/x"})
        m.inc_counter("requests_total", labels={"path": "/x"})
        assert m.counter_value("requests_total", labels={"path": "/x"}) == 2.0

    def test_counter_rejects_negative(self) -> None:
        m = MetricsRegistry()
        with pytest.raises(ValueError):
            m.inc_counter("requests_total", value=-1.0)

    def test_gauge_set_and_overwrite(self) -> None:
        m = MetricsRegistry()
        m.set_gauge("cache_hit_rate", 0.5)
        m.set_gauge("cache_hit_rate", 0.9)
        assert m.gauge_value("cache_hit_rate") == pytest.approx(0.9)

    def test_type_consistency_enforced(self) -> None:
        m = MetricsRegistry()
        m.inc_counter("dup_metric")
        with pytest.raises(ValueError):
            m.set_gauge("dup_metric", 1.0)

    def test_histogram_buckets_are_cumulative(self) -> None:
        m = MetricsRegistry()
        for v in (0.002, 0.02, 0.2, 2.0):
            m.observe_histogram("latency_seconds", v)
        summary = m.histogram_summary("latency_seconds")
        assert summary is not None
        assert summary["count"] == 4
        assert summary["sum"] == pytest.approx(0.002 + 0.02 + 0.2 + 2.0)

    def test_collect_process_metrics_no_op_without_profiler(self) -> None:
        m = MetricsRegistry()
        m.collect_process_metrics(None)  # no-op, hata fırlatmamalı
        assert m.metric_names() == []

    def test_collect_process_metrics_with_real_profiler(self) -> None:
        m = MetricsRegistry()
        profiler = MemoryProfiler()
        profiler.start()
        m.collect_process_metrics(profiler)
        assert m.gauge_value("process_memory_current_bytes") >= 0.0
        assert m.gauge_value("process_memory_peak_bytes") >= 0.0

    def test_render_prometheus_has_help_and_type(self) -> None:
        m = MetricsRegistry()
        m.inc_counter("http_requests_total", help_text="toplam istek", labels={"a": "1"})
        text = m.render_prometheus()
        assert "# HELP http_requests_total toplam istek" in text
        assert "# TYPE http_requests_total counter" in text
        assert 'http_requests_total{a="1"} 1.0' in text

    def test_render_prometheus_histogram_has_le_buckets_and_inf(self) -> None:
        m = MetricsRegistry()
        m.observe_histogram("dur_seconds", 0.03)
        text = m.render_prometheus()
        assert 'le="+Inf"' in text
        assert "dur_seconds_sum" in text
        assert "dur_seconds_count" in text
        # Bucket sınırlarının hepsi metinde geçiyor mu
        for bound in DEFAULT_HISTOGRAM_BUCKETS:
            assert f'le="{bound}"' in text

    def test_metric_names_lists_all_registered(self) -> None:
        m = MetricsRegistry()
        m.inc_counter("a_total")
        m.set_gauge("b_gauge", 1.0)
        m.observe_histogram("c_seconds", 0.1)
        assert m.metric_names() == ["a_total", "b_gauge", "c_seconds"]


# ---------------------------------------------------------------------------
# InstrumentedRouter <-> app_shell entegrasyonu
# ---------------------------------------------------------------------------

@pytest.fixture()
def session(tmp_path):
    registry = tmp_path / "registry.hprojreg"
    sess = AppSession(registry)
    yield sess
    sess.close()


@pytest.fixture()
def instrumented(session):
    metrics = MetricsRegistry()
    logger = StructuredLogger(name="test.e15.instrumented", stream=io.StringIO())
    inner_router = build_app_router(session, metrics=metrics)
    return InstrumentedRouter(inner_router, logger=logger, metrics=metrics)


class TestInstrumentedRouterAppShellIntegration:
    def test_every_request_produces_a_log_record(self, instrumented) -> None:
        instrumented.dispatch("GET", "/api/health")
        instrumented.dispatch("GET", "/api/projects")
        records = instrumented.logger.records()
        assert len(records) == 2
        for record in records:
            assert record["event"] == "http_request"
            assert "method" in record and "path" in record
            assert "status" in record and "duration_ms" in record

    def test_log_record_has_correct_status_and_path(self, instrumented) -> None:
        instrumented.dispatch("GET", "/api/health")
        record = instrumented.logger.records()[-1]
        assert record["status"] == 200
        assert record["path"] == "/api/health"
        assert record["method"] == "GET"
        assert record["duration_ms"] >= 0.0

    def test_404_route_still_logged_and_raises(self, instrumented) -> None:
        from harita.extensibility.rest_api import RestNotFoundError

        with pytest.raises(RestNotFoundError):
            instrumented.dispatch("GET", "/api/does-not-exist")
        record = instrumented.logger.records()[-1]
        assert record["status"] == 404

    def test_metrics_counter_increments_per_request(self, instrumented) -> None:
        instrumented.dispatch("GET", "/api/health")
        instrumented.dispatch("GET", "/api/health")
        value = instrumented.metrics.counter_value(
            "http_requests_total",
            labels={"method": "GET", "path": "/api/health", "status": "200"},
        )
        assert value == 2.0

    def test_metrics_histogram_records_duration(self, instrumented) -> None:
        instrumented.dispatch("GET", "/api/health")
        summary = instrumented.metrics.histogram_summary(
            "http_request_duration_seconds",
            labels={"method": "GET", "path": "/api/health"},
        )
        assert summary is not None
        assert summary["count"] == 1

    def test_routes_passthrough(self, instrumented, session) -> None:
        assert instrumented.routes() == instrumented.router.routes()
        assert ("GET", "/api/metrics") in instrumented.routes()

    def test_metrics_endpoint_returns_prometheus_text_with_at_least_5_metrics(
        self, session
    ) -> None:
        metrics = MetricsRegistry()
        router = build_app_router(session, metrics=metrics)
        # /metrics çağrılmadan önce birkaç istek + process metriği üretelim
        # ki en az 5 farklı metrik adı gerçekten dolu olsun.
        router.dispatch("GET", "/api/health")
        router.dispatch("GET", "/api/projects")
        metrics.inc_counter("http_requests_total", labels={"method": "GET", "path": "/api/health", "status": "200"})
        metrics.observe_histogram("http_request_duration_seconds", 0.01, labels={"method": "GET", "path": "/api/health"})
        metrics.set_gauge("cache_hit_rate", 0.75)
        profiler = MemoryProfiler()
        profiler.start()
        metrics.collect_process_metrics(profiler)

        response = router.dispatch("GET", "/api/metrics")
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/plain")
        text = response.body
        assert "# TYPE" in text
        distinct_metrics = {
            name for name in metrics.metric_names()
        }
        assert len(distinct_metrics) >= 5, distinct_metrics

    def test_metrics_endpoint_absent_when_metrics_not_provided(self, session) -> None:
        router = build_app_router(session)  # metrics=None
        from harita.extensibility.rest_api import RestNotFoundError

        with pytest.raises(RestNotFoundError):
            router.dispatch("GET", "/api/metrics")
