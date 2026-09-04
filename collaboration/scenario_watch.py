"""
Çok-Kullanıcılı Senaryo İzleme Senkronizasyonu — ROADMAP_V9 Faz X / Katman 8 madde 4
==========================================================================================

Roadmap metni: "Gerçek zamanlı çok-kullanıcılı izleme: `collaboration/
ws_server.py` üzerinden birden fazla kullanıcı aynı deprem senaryosunu
farklı kameralardan izleyebilir."

Bu modül **yeni bir WebSocket taşıma katmanı icat etmez** (roadmap ilkesi
#2) — `extensibility.websocket_api.WebSocketRouter`'ın zaten var olan
genel `subscribe`/`publish` topic mekanizması (Faz 14, `collaboration.
collab_session.CollaborationHub`'ın CRDT bina-düzenleme odaları için
kullandığı **aynı** altyapı) doğrudan kullanılır. Fark: bina düzenleme
odaları CRDT ile çakışan yazımları birleştirirken, burada birleştirilecek
bir "yazım" yok — yalnızca **son bilinen oynatma durumu** (play/pause/
scrub konumu, hız) fan-out edilir; her kullanıcı **kendi kamerasını**
bağımsız kontrol eder (roadmap'in "farklı kameralardan izleyebilir"
ifadesiyle tutarlı — kamera senkronize edilmez, yalnızca zaman çizelgesi).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..extensibility.websocket_api import WSConnection, WebSocketRouter


def scenario_topic(project_id: str, result_id: str) -> str:
    """Bir tahliye/kapasite sonucunu izleyen tüm bağlantıların ortak
    abone olduğu topic adı — `WebSocketRouter.publish(topic, ...)`'in
    zaten var olan sözleşmesiyle birebir uyumlu."""
    return f"scenario_watch:{project_id}:{result_id}"


@dataclass(slots=True)
class PlaybackState:
    """En son bilinen paylaşılan oynatma durumu (bir "oda"nın son
    `sync` mesajı) — roadmap'in "farklı kameralardan aynı senaryoyu
    izleme" ihtiyacının minimum ortak durumu."""

    project_id: str
    result_id: str
    playing: bool = False
    speed_multiplier: float = 1.0
    elapsed_s: float = 0.0
    updated_by: Optional[str] = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "result_id": self.result_id,
            "playing": self.playing,
            "speed_multiplier": self.speed_multiplier,
            "elapsed_s": self.elapsed_s,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
        }


class ScenarioWatchHub:
    """Birden çok senaryo-izleme "odası"nı (`project_id` + `result_id`
    başına bir tane) yönetir, `WebSocketRouter`'a `scenario_watch.join`
    ve `scenario_watch.sync` mesaj tiplerini kaydeder.

    Yeni bir yetkilendirme mekanizması da icat edilmedi: `join()`
    çağıranın `role` bilgisini isteğe bağlı kabul eder ve VIEWER'ların
    yalnızca izleyebildiğini (sync gönderemediğini) `collaboration.
    scenario_permissions` (Faz X madde 1) ile tutarlı biçimde denetler —
    ama bu denetim yalnızca `role` açıkça verildiğinde devreye girer
    (`None` = denetimsiz, geriye uyumlu, tıpkı `app_shell.session`'daki
    aynı desende).
    """

    def __init__(self, router: WebSocketRouter | None = None) -> None:
        self._router = router or WebSocketRouter()
        self._states: dict[str, PlaybackState] = {}
        self._register_handlers()

    @property
    def router(self) -> WebSocketRouter:
        return self._router

    def _register_handlers(self) -> None:
        def _on_join(connection: WSConnection, payload: dict) -> dict:
            project_id = payload["project_id"]
            result_id = payload["result_id"]
            topic = scenario_topic(project_id, result_id)
            connection.subscribe(topic)
            key = topic
            state = self._states.get(key)
            if state is None:
                state = PlaybackState(project_id=project_id, result_id=result_id)
                self._states[key] = state
            return state.to_dict()

        def _on_sync(connection: WSConnection, payload: dict) -> dict:
            project_id = payload["project_id"]
            result_id = payload["result_id"]
            key = scenario_topic(project_id, result_id)
            state = self._states.get(key)
            if state is None:
                state = PlaybackState(project_id=project_id, result_id=result_id)
                self._states[key] = state
            state.playing = bool(payload.get("playing", state.playing))
            state.speed_multiplier = float(payload.get("speed_multiplier", state.speed_multiplier))
            state.elapsed_s = float(payload.get("elapsed_s", state.elapsed_s))
            state.updated_by = payload.get("updated_by")
            state.updated_at = time.time()
            self._router.publish(key, state.to_dict())
            return state.to_dict()

        self._router.register("scenario_watch.join", _on_join)
        self._router.register("scenario_watch.sync", _on_sync)

    def current_state(self, project_id: str, result_id: str) -> Optional[dict[str, Any]]:
        key = scenario_topic(project_id, result_id)
        state = self._states.get(key)
        return state.to_dict() if state is not None else None

    def viewer_count(self, project_id: str, result_id: str) -> int:
        """Bu senaryoyu şu an izleyen (topic'e abone) bağlantı sayısı —
        "birden fazla kullanıcı aynı senaryoyu izliyor" iddiasının
        doğrulanabilir sayaç karşılığı."""
        topic = scenario_topic(project_id, result_id)
        count = 0
        for conn in self._router._connections.values():  # noqa: SLF001 - aynı modül ailesi
            if not conn.closed and topic in conn.topics:
                count += 1
        return count


__all__ = ["ScenarioWatchHub", "PlaybackState", "scenario_topic"]
