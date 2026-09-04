"""Roadmap V4 - Faz E12 kabul kriteri testleri.

Kapsam (Faz E12, ROADMAP_V4.md):
    "Bir `SensorBinding`, gerçek bir MQTT-benzeri mesaj akışından (veya
    gerçek `paho-mqtt` kuruluysa gerçek bir broker'dan) canlı güncellenir
    ve bu `TwinEvent` günlüğüne otomatik yansır."

Bu ortamda gerçek bir MQTT broker'ı yok; bu yüzden `TopicBus` (stdlib-only
in-process pub/sub) gerçekten çalıştırılarak test edilir - `MqttBridge`
yalnızca `paho-mqtt` kurulu değilken açık `MqttBackendUnavailable`
fırlattığı doğrulanır (roadmap ilkesi: sessiz sahte-başarı yok).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.digital_twin import DigitalTwinRegistry, SensorBinding
from harita.digital_twin.iot_bridge import (
    MqttBackendUnavailable,
    MqttBridge,
    SensorIotBinding,
    TopicBus,
)


def test_topic_bus_exact_match():
    bus = TopicBus()
    received = []
    bus.subscribe("bina/1/sicaklik", lambda m: received.append(m))
    bus.publish("bina/1/sicaklik", 21.5)
    assert len(received) == 1
    assert received[0].payload == 21.5


def test_topic_bus_single_level_wildcard():
    bus = TopicBus()
    received = []
    bus.subscribe("bina/+/sicaklik", lambda m: received.append(m.topic))
    bus.publish("bina/1/sicaklik", 20.0)
    bus.publish("bina/2/sicaklik", 22.0)
    bus.publish("bina/1/nem", 50.0)  # eşleşmemeli
    assert received == ["bina/1/sicaklik", "bina/2/sicaklik"]


def test_topic_bus_multi_level_wildcard():
    bus = TopicBus()
    received = []
    bus.subscribe("bina/#", lambda m: received.append(m.topic))
    bus.publish("bina/1/kat/2/sicaklik", 19.0)
    bus.publish("disari/sicaklik", 5.0)  # eşleşmemeli
    assert received == ["bina/1/kat/2/sicaklik"]


def test_topic_bus_unsubscribe_stops_delivery():
    bus = TopicBus()
    received = []
    unsub = bus.subscribe("test/topic", lambda m: received.append(m))
    bus.publish("test/topic", 1)
    unsub()
    bus.publish("test/topic", 2)
    assert len(received) == 1


def test_topic_bus_invalid_hash_position_raises():
    bus = TopicBus()
    try:
        bus.subscribe("bina/#/kat", lambda m: None)
        raise AssertionError("ValueError beklenirdi")
    except ValueError:
        pass


def test_sensor_iot_binding_updates_twin_and_logs_event():
    registry = DigitalTwinRegistry()
    twin = registry.create("bina_1")
    twin.bind_sensor(
        SensorBinding(sensor_id="temp_01", sensor_type="temperature", target_ref="roof")
    )
    registry.save(twin)

    bus = TopicBus()
    live_twin = registry.get("bina_1")
    binding = SensorIotBinding(bus, live_twin, sensor_id="temp_01", topic="bina/1/roof/temp_01")
    binding.bind()

    events_before = len(live_twin.events_of_type("sensor_value_updated"))
    bus.publish("bina/1/roof/temp_01", {"value": 27.3})

    assert binding.message_count == 1
    sensor = [s for s in live_twin.sensors if s.sensor_id == "temp_01"][0]
    assert sensor.last_value == 27.3
    events_after = len(live_twin.events_of_type("sensor_value_updated"))
    assert events_after == events_before + 1


def test_sensor_iot_binding_ignores_non_numeric_payload():
    registry = DigitalTwinRegistry()
    twin = registry.create("bina_2")
    twin.bind_sensor(SensorBinding(sensor_id="s1", sensor_type="humidity", target_ref="floor:1"))
    registry.save(twin)
    live_twin = registry.get("bina_2")

    bus = TopicBus()
    binding = SensorIotBinding(bus, live_twin, sensor_id="s1", topic="test/humidity")
    binding.bind()
    bus.publish("test/humidity", {"status": "no numeric value"})
    assert binding.message_count == 0


def test_sensor_iot_binding_unbind_stops_updates():
    registry = DigitalTwinRegistry()
    twin = registry.create("bina_3")
    twin.bind_sensor(SensorBinding(sensor_id="s1", sensor_type="temperature", target_ref="roof"))
    registry.save(twin)
    live_twin = registry.get("bina_3")

    bus = TopicBus()
    binding = SensorIotBinding(bus, live_twin, sensor_id="s1", topic="t")
    binding.bind()
    binding.unbind()
    bus.publish("t", 10.0)
    assert binding.message_count == 0


def test_mqtt_bridge_unavailable_without_paho():
    bus = TopicBus()
    bridge = MqttBridge(bus)
    try:
        import paho.mqtt.client  # noqa: F401

        return  # paho kurulu - bu test bu ortamda uygulanamaz, atlanır
    except ImportError:
        pass
    try:
        bridge.connect(host="localhost", port=1883)
        raise AssertionError("MqttBackendUnavailable beklenirdi")
    except MqttBackendUnavailable:
        pass


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
