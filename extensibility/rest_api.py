"""
REST API
========

Roadmap Phase 14 - "REST API".

Ana projenin `api/` katmanına yeni bir router olarak eklenebilecek, çerçeve
bağımsız (framework-agnostic) bir istek yönlendirici. Flask/FastAPI'ye değil
yalnızca stdlib'e bağımlıdır: `RestRouter` saf Python fonksiyonlarıyla
route eşler ve `dispatch()` ile test edilebilir (gerçek bir soket açmadan).
İsteğe bağlı olarak `serve()` ile stdlib `http.server` üzerinden gerçek bir
HTTP sunucusu olarak da çalıştırılabilir (ana projenin `server_api.py`
FastAPI/Flask kullanıyorsa, `RestRouter.dispatch` doğrudan onun içinden de
çağrılabilir - iki katmanlı kullanım).

Path parametreleri Flask deseniyle aynıdır: ``/buildings/<id>``.
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

Handler = Callable[..., Any]

_PARAM_RE = re.compile(r"<(\w+)>")


@dataclass
class Route:
    method: str
    pattern: str
    handler: Handler
    regex: re.Pattern = field(init=False)
    param_names: List[str] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.param_names = _PARAM_RE.findall(self.pattern)
        escaped = re.sub(_PARAM_RE, r"(?P<\1>[^/]+)", self.pattern)
        self.regex = re.compile(f"^{escaped}$")

    def match(self, path: str) -> Optional[Dict[str, str]]:
        m = self.regex.match(path)
        return m.groupdict() if m else None

    # -- Roadmap V3 - D19: imza-farkındalıklı çağrı yardımcıları -----------
    def _signature(self) -> inspect.Signature:
        cached = getattr(self, "_sig_cache", None)
        if cached is None:
            cached = inspect.signature(self.handler)
            object.__setattr__(self, "_sig_cache", cached)
        return cached

    def accepts_extra_kwargs(self) -> bool:
        return any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in self._signature().parameters.values()
        )

    def accepts_param(self, name: str) -> bool:
        return name in self._signature().parameters


@dataclass
class RestResponse:
    status: int
    body: Any = None
    headers: Dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})

    def to_json(self) -> str:
        return json.dumps(self.body, ensure_ascii=False, default=str)


class RestNotFoundError(Exception):
    pass


class RestRouter:
    """Framework-agnostic route kayıt/dispatch motoru."""

    def __init__(self) -> None:
        self._routes: List[Route] = []
        self._middlewares: List[Callable[[str, str, dict], Optional[RestResponse]]] = []

    # -- kayıt yardımcıları --------------------------------------------------
    def route(self, method: str, pattern: str) -> Callable[[Handler], Handler]:
        def _decorator(handler: Handler) -> Handler:
            self._routes.append(Route(method.upper(), pattern, handler))
            return handler

        return _decorator

    def get(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.route("GET", pattern)

    def post(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.route("POST", pattern)

    def put(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.route("PUT", pattern)

    def delete(self, pattern: str) -> Callable[[Handler], Handler]:
        return self.route("DELETE", pattern)

    def add_middleware(
        self, middleware: Callable[[str, str, dict], Optional[RestResponse]]
    ) -> None:
        """Middleware `(method, path, query) -> RestResponse | None` imzasında.

        `RestResponse` dönerse istek kısa devre yapılır (örn. auth reddi);
        `None` dönerse zincire devam edilir.
        """
        self._middlewares.append(middleware)

    # -- dispatch -------------------------------------------------------------
    def dispatch(
        self,
        method: str,
        path: str,
        body: Any = None,
        query: Optional[Dict[str, Any]] = None,
    ) -> RestResponse:
        method = method.upper()
        parsed = urlparse(path)
        clean_path = parsed.path
        query = query or {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}

        for middleware in self._middlewares:
            result = middleware(method, clean_path, query)
            if result is not None:
                return result

        for route in self._routes:
            if route.method != method:
                continue
            params = route.match(clean_path)
            if params is None:
                continue
            # Roadmap V3 - D19: handler'ı çağırmadan önce imzasından
            # (inspect.signature) `body`/`query` kabul edip etmediğini tespit
            # et. Eski davranış (`try: handler(**params, body=.., query=..)
            # except TypeError: handler(**params)`) handler *içindeki* bir
            # TypeError'ı da yakalayıp sessizce ikinci kez -yanlış argümanlarla-
            # çağırıyordu; artık handler tam olarak bir kez çağrılıyor.
            call_kwargs = dict(params)
            if route.accepts_extra_kwargs() or route.accepts_param("body"):
                call_kwargs["body"] = body
            if route.accepts_extra_kwargs() or route.accepts_param("query"):
                call_kwargs["query"] = query
            try:
                result = route.handler(**call_kwargs)
            except RestNotFoundError:
                raise
            except Exception as exc:  # noqa: BLE001 - D19: fuzz-güvenli varsayılan
                # Beklenmeyen (validasyondan kaçmış) bir handler hatası, ham
                # traceback/istisna gövdesiyle çağırana sızdırılmadan 500 olarak
                # döndürülür (OWASP API8:2023 "Security Misconfiguration"
                # sınıfında bilgi ifşasını engeller).
                return RestResponse(
                    status=500,
                    body={"error": "internal_error", "detail": type(exc).__name__},
                )
            if isinstance(result, RestResponse):
                return result
            return RestResponse(status=200, body=result)

        raise RestNotFoundError(f"{method} {clean_path} için route bulunamadı")

    def routes(self) -> List[Tuple[str, str]]:
        return [(r.method, r.pattern) for r in self._routes]


def build_default_router(digital_twin_registry: Optional[Dict[str, Any]] = None) -> RestRouter:
    """`DigitalTwin` nesneleri üzerinde CRUD benzeri örnek bir API kurar.

    Ana projeye entegrasyon örneği: `harita/api` katmanı bu fonksiyonu
    genişleterek gerçek route'ları ekleyebilir.
    """
    registry: Dict[str, Any] = digital_twin_registry if digital_twin_registry is not None else {}
    router = RestRouter()

    @router.get("/health")
    def _health(**_: Any) -> Dict[str, str]:
        return {"status": "ok"}

    @router.get("/buildings")
    def _list_buildings(**_: Any) -> Dict[str, Any]:
        return {"buildings": list(registry.keys())}

    @router.get("/buildings/<id>")
    def _get_building(id: str, **_: Any) -> RestResponse:  # noqa: A002
        if id not in registry:
            return RestResponse(status=404, body={"error": "not_found", "id": id})
        return RestResponse(status=200, body={"id": id, "data": registry[id]})

    @router.post("/buildings/<id>")
    def _upsert_building(id: str, body: Any = None, **_: Any) -> Dict[str, Any]:  # noqa: A002
        registry[id] = body
        return {"id": id, "stored": True}

    @router.delete("/buildings/<id>")
    def _delete_building(id: str, **_: Any) -> RestResponse:  # noqa: A002
        existed = registry.pop(id, None) is not None
        return RestResponse(status=200 if existed else 404, body={"deleted": existed})

    return router
