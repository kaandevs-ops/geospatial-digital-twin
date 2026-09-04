"""
InstrumentedRouter — `extensibility.rest_api.RestRouter` için Gözlemlenebilirlik Sarmalayıcısı
==================================================================================================

ROADMAP_V4 Faz E15 kabul kriteri: "app_shell üzerinden yapılan her REST
isteği yapılandırılmış bir log satırı üretir (endpoint, süre, durum
kodu)". `RestRouter.dispatch()` (Faz 14) değiştirilmeden — bir dekoratör/
sarmalayıcı (`InstrumentedRouter`) ile aynı `dispatch()` imzasını taklit
eder, gerçek çağrıyı sarar, süreyi ölçer, `StructuredLogger.log_request()`
ile loglar ve `MetricsRegistry`'ye `http_requests_total` (counter) +
`http_request_duration_seconds` (histogram) yazar.

Bu tasarımın `RestRouter`'ı doğrudan değiştirmek yerine sarmalamayı tercih
etmesinin nedeni: Faz 14/21'in mevcut `dispatch()` sözleşmesi (ve ona
bağlı tüm testler/middleware zinciri) hiç dokunulmadan korunur — sıfıra
yakın regresyon riski.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from ..extensibility.rest_api import RestNotFoundError, RestResponse, RestRouter
from .logging import StructuredLogger
from .metrics import MetricsRegistry


class InstrumentedRouter:
    """`RestRouter.dispatch()` ile aynı imzayı sunan, gözlemlenebilirlik
    eklenmiş sarmalayıcı. `routes()` da şeffaf biçimde iletilir."""

    def __init__(
        self,
        router: RestRouter,
        *,
        logger: Optional[StructuredLogger] = None,
        metrics: Optional[MetricsRegistry] = None,
        service_name: str = "harita-app-shell",
    ) -> None:
        self.router = router
        self.logger = logger or StructuredLogger(name=service_name)
        self.metrics = metrics or MetricsRegistry()
        self._service_name = service_name

    def dispatch(
        self,
        method: str,
        path: str,
        body: Any = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> RestResponse:
        clean_path = urlparse(path).path
        start = time.perf_counter()
        status: int = 500
        try:
            response = self.router.dispatch(method, path, body=body, query=query)
            status = response.status
            return response
        except RestNotFoundError:
            status = 404
            raise
        finally:
            elapsed_s = time.perf_counter() - start
            self.metrics.inc_counter(
                "http_requests_total",
                help_text="Toplam işlenen HTTP isteği sayısı (method/path/status etiketli).",
                labels={"method": method.upper(), "path": clean_path, "status": str(status)},
            )
            self.metrics.observe_histogram(
                "http_request_duration_seconds",
                elapsed_s,
                help_text="HTTP isteği işleme süresi (saniye).",
                labels={"method": method.upper(), "path": clean_path},
            )
            self.logger.log_request(
                method=method.upper(),
                path=clean_path,
                status=status,
                duration_ms=elapsed_s * 1000.0,
                service=self._service_name,
            )

    def routes(self):
        return self.router.routes()

    def render_metrics(self) -> str:
        """`/metrics` endpoint'i için Prometheus text-format çıktısı."""
        return self.metrics.render_prometheus()
