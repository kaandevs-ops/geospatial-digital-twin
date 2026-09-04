"""
Digital Twin - IoT Protokol Köprüsü
====================================

ROADMAP_V4 - Faz E12 (Digital Twin: Gerçek Zamanlı IoT Protokol Entegrasyonu).

D11 ile gerçekçi (sinüzoidal + gürültü) sensör zaman serisi üretimi ve twin
hiyerarşisi eklendi ama bu tamamen kütüphane-içi simülasyondu — gerçek bir
IoT mesajlaşma protokolüne (MQTT, CoAP vb.) bağlanma yoktu. Bu modül iki
katman sağlar:

1. **`TopicBus`** — stdlib-only, MQTT'nin temel topic/payload semantiğini
   (hiyerarşik `"bina/1/kat/2/sicaklik"` konuları, `+`/`#` joker karakterleri,
   QoS 0 "en fazla bir kez" teslimat) taklit eden bir pub/sub veri yolu.
   `extensibility.event_system.EventSystem` üzerine inşa edilmez (o düz
   isim eşleşmesi yapar, MQTT joker karakter semantiğini desteklemez) —
   bağımsız, hafif bir uygulamadır; stdlib-only ilkesini korur.

2. **`MqttBridge`** — opsiyonel `paho-mqtt` bağımlılığı (`[iot]` extra)
   kuruluysa gerçek bir MQTT broker'ına bağlanıp mesajları `TopicBus`'a
   köprüler; kurulu değilse veya bağlantı başarısız olursa açık
   `MqttBackendUnavailable` fırlatır (roadmap ilkesi: sessiz sahte-başarı
   yok, açık ve anlaşılır hata).

3. **`SensorIotBinding`** — bir `digital_twin.DigitalTwin`'in `SensorBinding`
   nesnesini `TopicBus`'daki bir konuya abone eder; gelen her mesaj otomatik
   olarak `DigitalTwin.update_sensor()`'ı çağırır (ve dolayısıyla bir
   `TwinEvent` günlüğe yansır — roadmap'in kendi yazılı kabul kriteri).

Bağımlılık: yalnızca stdlib (`re`, `dataclasses`, `queue`, `threading`);
`paho-mqtt` tamamen opsiyonel ve yalnızca `MqttBridge.connect()` gerçekten
çağrıldığında import edilir (import-zamanında değil, çağrı-zamanında hata).
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import DigitalTwin

# ========================================================================== #
# Hatalar
# ========================================================================== #


class MqttBackendUnavailable(Exception):
    """`paho-mqtt` kurulu değil veya gerçek bir broker'a bağlanılamadı."""


# ========================================================================== #
# TopicBus - MQTT-benzeri topic/payload pub/sub (stdlib-only)
# ========================================================================== #


def _topic_pattern_to_regex(pattern: str) -> re.Pattern:
    """MQTT joker karakterlerini (`+` = tek seviye, `#` = çoklu seviye,
    yalnızca sonda geçerli) bir regex'e çevirir."""
    segments = pattern.split("/")
    regex_parts: list[str] = []
    for i, seg in enumerate(segments):
        if seg == "#":
            if i != len(segments) - 1:
                raise ValueError("'#' yalnızca konu deseninin sonunda geçerli")
            regex_parts.append(r".*")
            break
        elif seg == "+":
            regex_parts.append(r"[^/]+")
        else:
            regex_parts.append(re.escape(seg))
    return re.compile("^" + "/".join(regex_parts) + "$")


@dataclass(slots=True)
class IotMessage:
    topic: str
    payload: Any
    timestamp: float = field(default_factory=time.time)
    qos: int = 0


class TopicBus:
    """MQTT'nin temel topic/payload semantiğini taklit eden, stdlib-only
    bir pub/sub veri yolu. Gerçek bir broker olmadan (in-process) çalışır;
    `MqttBridge` gerçek bir dış broker'dan gelen mesajları buraya köprüler.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, tuple[re.Pattern, Callable[[IotMessage], None]]] = {}
        self._lock = threading.Lock()
        self._history: list[IotMessage] = []
        self._next_id = 0

    def subscribe(
        self, topic_pattern: str, listener: Callable[[IotMessage], None]
    ) -> Callable[[], None]:
        """`topic_pattern` içindeki bir mesaj yayınlandığında `listener` çağrılır.
        Geriye, aboneliği iptal eden bir fonksiyon döner."""
        regex = _topic_pattern_to_regex(topic_pattern)
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subscriptions[sub_id] = (regex, listener)

        def _unsubscribe() -> None:
            with self._lock:
                self._subscriptions.pop(sub_id, None)

        return _unsubscribe

    def publish(self, topic: str, payload: Any, qos: int = 0) -> IotMessage:
        """QoS 0 ("en fazla bir kez") teslimat: eşleşen tüm abonelere
        senkron olarak dağıtılır (gerçek ağ gecikmesi yok - in-process)."""
        msg = IotMessage(topic=topic, payload=payload, qos=qos)
        with self._lock:
            self._history.append(msg)
            matched = [
                listener for (regex, listener) in self._subscriptions.values() if regex.match(topic)
            ]
        for listener in matched:
            listener(msg)
        return msg

    @property
    def history(self) -> list[IotMessage]:
        return list(self._history)

    def subscriber_count(self) -> int:
        return len(self._subscriptions)


# ========================================================================== #
# MqttBridge - opsiyonel gerçek MQTT broker entegrasyonu ([iot] extra)
# ========================================================================== #


class MqttBridge:
    """`paho-mqtt` (opsiyonel `[iot]` extra) kuruluysa gerçek bir MQTT
    broker'ına bağlanır ve gelen mesajları verilen `TopicBus`'a köprüler.

    Kurulu değilse `connect()` açık `MqttBackendUnavailable` fırlatır -
    roadmap ilkesi gereği sessizce "başarılı" görünen sahte bir bağlantı
    asla kurulmaz.
    """

    def __init__(self, bus: TopicBus, client_id: str = "harita-digital-twin") -> None:
        self._bus = bus
        self._client_id = client_id
        self._client: Any = None
        self._connected = False

    def connect(
        self,
        host: str = "localhost",
        port: int = 1883,
        topics: list[str] | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        try:
            import paho.mqtt.client as mqtt  # type: ignore
        except ImportError as exc:
            raise MqttBackendUnavailable(
                "paho-mqtt kurulu değil. `pip install harita[iot]` ile "
                "kurup tekrar deneyin, veya in-process TopicBus'ı doğrudan "
                "kullanın (gerçek broker gerektirmez)."
            ) from exc

        client = mqtt.Client(client_id=self._client_id)

        def _on_message(_client: Any, _userdata: Any, message: Any) -> None:
            try:
                payload = message.payload.decode("utf-8")
            except Exception:  # noqa: BLE001
                payload = message.payload
            self._bus.publish(message.topic, payload)

        client.on_message = _on_message
        try:
            client.connect(host, port, keepalive=int(timeout_s))
        except Exception as exc:  # noqa: BLE001
            raise MqttBackendUnavailable(
                f"MQTT broker'a bağlanılamadı ({host}:{port}): {exc}"
            ) from exc

        for topic in topics or ["#"]:
            client.subscribe(topic)

        client.loop_start()
        self._client = client
        self._connected = True

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected


# ========================================================================== #
# SensorIotBinding - TopicBus <-> DigitalTwin.SensorBinding köprüsü
# ========================================================================== #


class SensorIotBinding:
    """Bir `DigitalTwin`'in belirli bir sensörünü, `TopicBus`'daki bir
    konuya abone eder. Gelen her mesaj (sayısal veya `{"value": ...}`
    JSON-benzeri dict payload'ı) otomatik olarak `DigitalTwin.update_sensor()`
    çağrısına dönüşür - bu da roadmap'in kendi kabul kriteri gereği bir
    `TwinEvent` (`sensor_value_updated`) günlüğe otomatik yazar.
    """

    def __init__(self, bus: TopicBus, twin: DigitalTwin, sensor_id: str, topic: str) -> None:
        self._bus = bus
        self._twin = twin
        self._sensor_id = sensor_id
        self._topic = topic
        self._unsubscribe: Callable[[], None] | None = None
        self._message_count = 0

    def bind(self) -> None:
        def _on_message(msg: IotMessage) -> None:
            value = self._extract_value(msg.payload)
            if value is not None:
                self._twin.update_sensor(self._sensor_id, value, timestamp=msg.timestamp)
                self._message_count += 1

        self._unsubscribe = self._bus.subscribe(self._topic, _on_message)

    def unbind(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @property
    def message_count(self) -> int:
        return self._message_count

    @staticmethod
    def _extract_value(payload: Any) -> float | None:
        if isinstance(payload, (int, float)):
            return float(payload)
        if isinstance(payload, dict) and "value" in payload:
            try:
                return float(payload["value"])
            except (TypeError, ValueError):
                return None
        if isinstance(payload, str):
            try:
                return float(payload)
            except ValueError:
                return None
        return None


__all__ = [
    "TopicBus",
    "IotMessage",
    "MqttBridge",
    "MqttBackendUnavailable",
    "SensorIotBinding",
]
