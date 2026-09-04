"""
Uygulama Kabuğu HTTP Sunucusu
===============================

`api.build_app_router()`'ı gerçek bir soket üzerinden servis eden, yalnızca
stdlib `http.server` kullanan minimal bir sunucu (Faz 15'in "harici
bağımlılık yok" ilkesiyle aynı yaklaşım). İki görevi var:

1. `/api/*` isteklerini `RestRouter.dispatch()`'e yönlendirmek (JSON gövde
   encode/decode dahil).
2. `web/` klasöründeki statik uygulama kabuğu dosyalarını (index.html, vb.)
   servis etmek.

Kullanım::

    python -m harita.app_shell.server --port 8765 --registry ~/.harita/registry.hprojreg

Sunucu tek-tenant'tır: tek bir `AppSession` (dolayısıyla tek bir registry)
üzerinde çalışır — çoklu kullanıcı/kimlik doğrulama Faz 19 kapsamındadır
(bkz. ROADMAP_V2.md).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .api import build_app_router
from .multipart import MultipartParseError, parse_multipart
from .session import AppSession, AppSessionError
from ..observability.instrumentation import InstrumentedRouter
from ..observability.logging import StructuredLogger
from ..observability.metrics import MetricsRegistry
from ..performance.profiler import GPUProfiler, MemoryProfiler

_WEB_ROOT = Path(__file__).parent / "web"

#: Faz 21 güvenlik sertleştirmesi — DoS koruması.
#: `Content-Length` başlığı doğrulanmadan `rfile.read(length)` çağrılırsa,
#: kötü niyetli bir istemci dev bir değer (örn. 10**12) beyan ederek sunucuyu
#: o kadar byte okumaya/ayırmaya zorlayabilir (bellek tükenmesi / bloke thread).
#: Bu sınır gerçek istek gövdelerinin (JSON proje/bina komutları) makul ölçüde
#: küçük olduğu varsayımıyla cömertçe seçildi; aşan istekler hiç
#: `rfile.read()` çağrılmadan 413 ile reddedilir.
MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024  # 16 MB

#: Roadmap V7 - "gerçek tarayıcı dosya yükleme" fazı: fotoğraf/nokta
#: bulutu yüklemeleri JSON gövdelerden çok daha büyük olabilir (drone
#: setleri yüzlerce MB olabilir) — bu yüzden yükleme endpoint'i için ayrı,
#: daha cömert bir üst sınır tanımlanır. Yine de sınırsız değil: DoS
#: korumasının amacı bozulmasın diye 512 MB'da kesiliyor.
MAX_UPLOAD_BODY_BYTES = 512 * 1024 * 1024  # 512 MB

#: Tek bir dosyanın izin verilen azami boyutu (tek bir kötü niyetli/bozuk
#: dosyanın tüm isteği tüketmesini önlemek için).
MAX_UPLOAD_FILE_BYTES = 128 * 1024 * 1024  # 128 MB

#: Yavaş/asılı istemcilere (slowloris tarzı) karşı soket okuma zaman aşımı.
REQUEST_TIMEOUT_SECONDS = 30.0

#: `POST /api/projects/<id>/feature-survey/uploads` — multipart dosya
#: yükleme uç noktası. JSON router'ın dışında, ham gövde olarak ayrıca
#: işlenir (bkz. `Handler._dispatch_upload`).
_UPLOAD_PATH_RE = re.compile(r"^/api/projects/([^/]+)/feature-survey/uploads/?$")


class RequestTooLarge(Exception):
    """`Content-Length`, sınırı aşıyor (JSON isteği ya da dosya yükleme)."""


def _make_handler(session: AppSession) -> type[BaseHTTPRequestHandler]:
    metrics = MetricsRegistry()
    logger = StructuredLogger(name="harita.app_shell")
    gpu_profiler = GPUProfiler()  # Faz E10: gercek GPU zamanlama kopru hedefi
    inner_router = build_app_router(session, metrics=metrics, gpu_profiler=gpu_profiler)
    router = InstrumentedRouter(inner_router, logger=logger, metrics=metrics)
    memory_profiler = MemoryProfiler()
    memory_profiler.start()

    class Handler(BaseHTTPRequestHandler):
        server_version = "HaritaAppShell/1.0"
        timeout = REQUEST_TIMEOUT_SECONDS

        def log_message(self, fmt: str, *args: Any) -> None:  # sessiz stdout, testlerde gürültü yapmasın
            pass

        # -- ortak yardımcılar ------------------------------------------
        def _send_json(self, status: int, body: Any) -> None:
            payload = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def _send_response(self, response: Any) -> None:
            content_type = response.headers.get("Content-Type", "application/json")
            if isinstance(response.body, (bytes, bytearray)):
                # ROADMAP_V7.md Faz C5 (offline mod, A4): tile servis uçları
                # ham PNG/JPEG byte'ları döner - JSON'a çevrilmemeli.
                payload = bytes(response.body)
            elif content_type.startswith("text/plain") and isinstance(response.body, str):
                payload = response.body.encode("utf-8")
            else:
                payload = json.dumps(response.body, ensure_ascii=False, default=str).encode("utf-8")
                content_type = "application/json; charset=utf-8"
            self.send_response(response.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def _read_json_body(self) -> Any:
            raw_length = self.headers.get("Content-Length", 0) or 0
            try:
                length = int(raw_length)
            except ValueError:
                return None
            if length == 0:
                return None
            if length < 0 or length > MAX_REQUEST_BODY_BYTES:
                raise RequestTooLarge(
                    f"İstek gövdesi çok büyük: {length} byte "
                    f"(azami {MAX_REQUEST_BODY_BYTES} byte)"
                )
            raw = self.rfile.read(length)
            if not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return None

        def _dispatch_api(self, method: str) -> None:
            try:
                body = self._read_json_body() if method in ("POST", "PUT") else None
            except RequestTooLarge as exc:
                self._send_json(413, {"error": str(exc)})
                return
            metrics.collect_process_metrics(memory_profiler)
            try:
                response = router.dispatch(method, self.path, body=body)
            except Exception as exc:  # noqa: BLE001 - route bulunamadı vb.
                self._send_json(404, {"error": str(exc)})
                return
            self._send_response(response)

        def _serve_static(self) -> None:
            rel = self.path.split("?", 1)[0].lstrip("/") or "index.html"
            file_path = (_WEB_ROOT / rel).resolve()
            try:
                file_path.relative_to(_WEB_ROOT.resolve())
            except ValueError:
                self.send_error(403, "Forbidden")
                return
            if not file_path.is_file():
                file_path = _WEB_ROOT / "index.html"
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # -- Faz S1.5: multipart dosya yükleme ----------------------------
        def _dispatch_upload(self, project_id: str) -> None:
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith("multipart/form-data"):
                self._send_json(415, {
                    "error": "Content-Type multipart/form-data olmalı "
                             f"(alınan: {content_type!r})."
                })
                return

            raw_length = self.headers.get("Content-Length", 0) or 0
            try:
                length = int(raw_length)
            except ValueError:
                self._send_json(400, {"error": "Geçersiz Content-Length başlığı."})
                return
            if length <= 0:
                self._send_json(400, {"error": "Boş yükleme gövdesi."})
                return
            if length > MAX_UPLOAD_BODY_BYTES:
                # Kötü niyetli/hatalı bir dev boyut beyanı varsa, hiç
                # rfile.read() çağırmadan reddet (bellek tükenmesini önle).
                self._send_json(413, {
                    "error": f"Yükleme çok büyük: {length} byte "
                             f"(azami {MAX_UPLOAD_BODY_BYTES} byte)."
                })
                return

            body = self.rfile.read(length)
            try:
                form = parse_multipart(body, content_type)
            except MultipartParseError as exc:
                self._send_json(400, {"error": f"multipart ayrıştırma hatası: {exc}"})
                return

            oversized = [f.filename for f in form.files if len(f.data) > MAX_UPLOAD_FILE_BYTES]
            if oversized:
                self._send_json(413, {
                    "error": f"Şu dosyalar azami {MAX_UPLOAD_FILE_BYTES} byte sınırını "
                             f"aşıyor: {', '.join(oversized)}"
                })
                return

            try:
                result = session.feature_survey_upload_photos(
                    project_id,
                    files=[(f.filename, f.data) for f in form.files],
                )
            except AppSessionError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": f"beklenmeyen sunucu hatası: {exc}"})
                return
            self._send_json(200, result)

        # -- HTTP fiilleri -------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/api/"):
                self._dispatch_api("GET")
            else:
                self._serve_static()

        def do_POST(self) -> None:  # noqa: N802
            path_only = self.path.split("?", 1)[0]
            upload_match = _UPLOAD_PATH_RE.match(path_only)
            if upload_match is not None:
                self._dispatch_upload(upload_match.group(1))
                return
            self._dispatch_api("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch_api("PUT")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch_api("DELETE")

    return Handler


def make_server(session: AppSession, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    handler_cls = _make_handler(session)
    return ThreadingHTTPServer((host, port), handler_cls)


def main() -> None:
    parser = argparse.ArgumentParser(description="Harita Modelleme - Uygulama Kabuğu Sunucusu")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--registry", default=str(Path.home() / ".harita" / "registry.hprojreg"),
        help="Çoklu proje registry dosya yolu",
    )
    args = parser.parse_args()

    session = AppSession(args.registry)
    server = make_server(session, host=args.host, port=args.port)
    print(f"Harita uygulama kabuğu http://{args.host}:{args.port} adresinde çalışıyor")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        session.close()


if __name__ == "__main__":
    main()
