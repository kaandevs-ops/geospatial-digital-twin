"""
WebSocket Support
==================

Roadmap Phase 14 - "WebSocket desteği".

`rest_api.py`'deki `RestRouter` (dispatch-tabanlı, soket-agnostik) deseninin
aynısı burada da izlenir: `WebSocketRouter` gerçek bir soket katmanına
bağımlı değildir, saf stdlib mesaj yönlendirme sağlar (`dispatch()` ile test
edilebilir). Gerçek bir sunucuya bağlamak isteyen taraf (örn. ana projenin
`server_api.py`'si FastAPI/Starlette kullanıyorsa) `WebSocketRouter.dispatch`
metodunu doğrudan kendi `on_message` handler'ından çağırabilir - iki katmanlı
kullanım (Phase 14 REST API'deki ile aynı prensip).

Kapsam:
    * `WSMessage`      - gelen/giden mesaj zarfı (type + payload + id).
    * `WSConnection`   - bağlantı durumu (id, subscribed topics, send buffer).
    * `WebSocketRouter`- type -> handler eşlemesi + pub/sub topic yayını
      (Phase 14 `EventSystem` ile entegre olabilir - `bridge_events`).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .event_system import EventSystem


@dataclass
class WSMessage:
    """Tek bir WebSocket mesajı (JSON-serileştirilebilir)."""

    type: str
    payload: Any = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {"type": self.type, "payload": self.payload, "id": self.id, "timestamp": self.timestamp}
        )

    @staticmethod
    def from_json(raw: str) -> "WSMessage":
        data = json.loads(raw)
        return WSMessage(
            type=data["type"],
            payload=data.get("payload"),
            id=data.get("id", uuid.uuid4().hex),
            timestamp=data.get("timestamp", time.time()),
        )


class WSConnectionClosedError(Exception):
    """Kapalı bir bağlantıya mesaj gönderilmeye çalışıldığında fırlatılır."""


@dataclass
class WSConnection:
    """
    Tek bir istemci bağlantısının durumu.

    `outbox`, gerçek bir soket olmadan (testte) giden mesajları biriktiren
    bir tampon görevi görür; gerçek bir sunucu entegrasyonunda bu, asıl
    `send()` çağrısıyla değiştirilebilir (`send_fn` inject edilerek).
    """

    connection_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    topics: set = field(default_factory=set)
    outbox: List[WSMessage] = field(default_factory=list)
    closed: bool = False
    send_fn: Optional[Callable[[WSMessage], None]] = None

    def send(self, message: WSMessage) -> None:
        if self.closed:
            raise WSConnectionClosedError(f"connection {self.connection_id} is closed")
        if self.send_fn is not None:
            self.send_fn(message)
        else:
            self.outbox.append(message)

    def subscribe(self, topic: str) -> None:
        self.topics.add(topic)

    def unsubscribe(self, topic: str) -> None:
        self.topics.discard(topic)

    def close(self) -> None:
        self.closed = True


class WebSocketRouter:
    """
    Mesaj tipi -> handler eşlemesi yapan, soket-agnostik yönlendirici.

    Handler imzası: ``handler(connection: WSConnection, payload: Any) -> Any``
    Dönen değer (None değilse) otomatik olarak aynı `type` + ``".reply"``
    soneki ile istemciye geri gönderilir.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, Callable[[WSConnection, Any], Any]] = {}
        self._connections: Dict[str, WSConnection] = {}
        self._event_system: Optional[EventSystem] = None

    # -- bağlantı yönetimi ------------------------------------------------
    def connect(self, connection: Optional[WSConnection] = None) -> WSConnection:
        conn = connection or WSConnection()
        self._connections[conn.connection_id] = conn
        return conn

    def disconnect(self, connection_id: str) -> None:
        conn = self._connections.pop(connection_id, None)
        if conn is not None:
            conn.close()

    def connection(self, connection_id: str) -> Optional[WSConnection]:
        return self._connections.get(connection_id)

    # -- handler kaydı ------------------------------------------------------
    def on(self, message_type: str) -> Callable:
        def decorator(fn: Callable[[WSConnection, Any], Any]) -> Callable:
            self._handlers[message_type] = fn
            return fn

        return decorator

    def register(self, message_type: str, handler: Callable[[WSConnection, Any], Any]) -> None:
        self._handlers[message_type] = handler

    # -- dispatch -----------------------------------------------------------
    def dispatch(self, connection: WSConnection, message: WSMessage) -> Optional[WSMessage]:
        if message.type == "subscribe":
            connection.subscribe(message.payload)
            return WSMessage(type="subscribe.ack", payload=message.payload)
        if message.type == "unsubscribe":
            connection.unsubscribe(message.payload)
            return WSMessage(type="unsubscribe.ack", payload=message.payload)

        handler = self._handlers.get(message.type)
        if handler is None:
            return WSMessage(type="error", payload=f"no handler for '{message.type}'")

        result = handler(connection, message.payload)
        if result is None:
            return None
        return WSMessage(type=f"{message.type}.reply", payload=result, id=message.id)

    # -- yayın (broadcast) ---------------------------------------------------
    def publish(self, topic: str, payload: Any) -> int:
        """`topic`'e abone tüm bağlantılara mesaj gönderir. Gönderilen sayıyı döner."""
        message = WSMessage(type=f"topic:{topic}", payload=payload)
        count = 0
        for conn in list(self._connections.values()):
            if conn.closed:
                continue
            if topic in conn.topics:
                conn.send(message)
                count += 1
        return count

    # -- EventSystem köprüsü --------------------------------------------------
    def bridge_events(self, event_system: EventSystem, event_name: str = "*") -> None:
        """
        `EventSystem`'e abone olur; her olayı aynı isimli topic olarak
        broadcast eder (`extensibility` iç modülleri arası entegrasyon).
        """
        self._event_system = event_system

        def _forward(event) -> None:  # noqa: ANN001 - Event tipi event_system.py'de
            self.publish(event.name, event.payload)

        event_system.subscribe(event_name, _forward)
