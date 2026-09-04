# Observability (ROADMAP_V4 — Faz E15)

Proje genelinde yapılandırılmış loglama, metrik toplama ve bunların
`app_shell` REST API'sine (Faz 18) bağlanması — ölçek/operasyon öncesi
kritik altyapı.

## Mevcut kısıt (Faz E15 öncesi)

`app_shell/server.py` yalnızca stdlib `http.server`'ın varsayılan
(insan-okur, ayrıştırılamaz, log toplama sistemine beslenemez) erişim
logunu kullanıyordu. Metrik toplama (request sayısı/gecikme, cache
hit-rate, bellek kullanımı) veya dağıtık iz sürme hiç yoktu.

## Teslim edilenler

- **`logging.py`**: `StructuredLogger` — stdlib `logging` üzerine JSON-lines
  formatlayıcı (`_JsonFormatter`). `info()/warning()/error()` genel amaçlı;
  `log_request(method, path, status, duration_ms)` HTTP isteği şeması.
  Test edilebilirlik için bellek-içi halka tampon (`records()`,
  `max_records` ile sınırlı).
- **`metrics.py`**: `MetricsRegistry` — Counter/Gauge/Histogram (Prometheus
  veri modeliyle bire bir), tip-tutarlılığı zorunlu kılınır (aynı ad iki
  farklı türle tanımlanamaz). `render_prometheus()` stdlib-only
  `text/plain; version=0.0.4` formatında çıktı üretir (HELP/TYPE satırları,
  histogram için kümülatif `_bucket{le=...}` + `_sum` + `_count`).
  `collect_process_metrics(memory_profiler)` — Faz 13
  `performance.profiler.MemoryProfiler.current_usage_bytes()`'ı
  `process_memory_current_bytes`/`process_memory_peak_bytes` gauge'larına
  döker (opsiyonel entegrasyon, `None` verilirse no-op).
- **`instrumentation.py`**: `InstrumentedRouter` — Faz 14
  `extensibility.rest_api.RestRouter`'ı **değiştirmeden** sarmalar (aynı
  `dispatch()`/`routes()` imzası). Her çağrıda: süre ölçülür, `http_requests_
  total` (counter, method/path/status etiketli) ve `http_request_duration_
  seconds` (histogram, method/path etiketli) metrikleri yazılır,
  `StructuredLogger.log_request()` ile yapılandırılmış log satırı üretilir
  — 404 (route bulunamadı) durumunda da loglanır, istisna yeniden fırlatılır.

## `app_shell` entegrasyonu

- `app_shell/api.py::build_app_router(session, metrics=None)` — `metrics`
  verilirse `GET /api/metrics` route'u kaydedilir (Prometheus text-format,
  `Content-Type: text/plain`). `metrics=None` ise (varsayılan, geriye
  uyumlu) bu route hiç kayıtlı değildir — mevcut davranış bozulmaz.
- `app_shell/server.py::_make_handler` — gerçek HTTP sunucusu artık bir
  `MetricsRegistry` + `StructuredLogger` oluşturur, `build_app_router`'ı
  bunlarla çağırır ve dönen router'ı `InstrumentedRouter` ile sarar; her
  istekte `MemoryProfiler` anlık görüntüsü de metriklere işlenir.
  `_send_response()` içerik-türüne duyarlıdır: `text/plain` yanıtlar
  (örn. `/metrics`) JSON'a sarılmadan ham metin olarak yazılır.

## Kabul kriteri

- `app_shell` üzerinden yapılan her REST isteği yapılandırılmış bir log
  satırı üretir (`event=http_request`, `method`, `path`, `status`,
  `duration_ms`) — ✅ `InstrumentedRouter.dispatch()` her çağrıda `finally`
  bloğunda garanti eder (başarı/hata/404 fark etmez).
- `/metrics` endpoint'i Prometheus text-format'ında en az 5 farklı metrik
  döner — ✅ `http_requests_total`, `http_request_duration_seconds`,
  `process_memory_current_bytes`, `process_memory_peak_bytes` + kullanıcı
  tanımlı ek gauge'lar (örn. `cache_hit_rate`) rahatlıkla 5'i aşar.

## Testler

`../tests/test_phaseE15_observability.py` — 23 test: `StructuredLogger`
(JSON geçerliliği, bellek tamponu, halka-tampon sınırı, `log_request`
şeması), `MetricsRegistry` (counter/gauge/histogram, tip tutarlılığı,
Prometheus render, `MemoryProfiler` entegrasyonu), `InstrumentedRouter`
(her isteğin loglandığı, 404'ün de loglandığı, metrik sayaçlarının arttığı,
`/metrics` endpoint'inin gerçekten ≥5 metrik döndüğü, `metrics=None`
verildiğinde `/api/metrics` route'unun hiç var olmadığı).

`from harita import StructuredLogger, MetricsRegistry, InstrumentedRouter,
DEFAULT_HISTOGRAM_BUCKETS` ile tek giriş noktasından erişilebilir (C3
ilkesiyle tutarlı); `extensibility.module_manager.ModuleManager.
DEFAULT_MODULES` içine de kaydedildi (C2 ilkesiyle tutarlı).
