"""Roadmap V4 - Faz E12, `AppSession` seviyesinde GERÇEK/SİMÜLASYON ayrımı.

Önceki oturumda tespit edilen sorun: `AppSession.iot_digital_twin_tick()`
her zaman sinüzoidal + gürültülü sahte veri üretiyordu, gerçek bir MQTT
broker'ı bağlansa bile bu değişmiyordu (`digital_twin.iot_bridge.MqttBridge`
gerçek bağlantıyı destekliyordu ama `AppSession` hiç kullanmıyordu).

Bu dosya şunu doğrular:
    1. `connect_iot_bridge()` çağrılmadan `iot_digital_twin_tick()`
       dürüstçe `is_live=False` ve SİMÜLASYON disclaimer'ı döner.
    2. `connect_iot_bridge()` broker'a ulaşılamadığında (`paho-mqtt`
       kurulu değil VEYA broker yok) `MqttBackendUnavailable` fırlatır -
       sessizce simülasyona düşmez.
    3. Gerçek bir yerel Mosquitto broker'ı ÇALIŞIYORSA (bu testin
       ortamında `pip install paho-mqtt` + `mosquitto` kurulu ve
       `localhost:1883`'te dinliyorsa), gerçekten yayınlanan bir MQTT
       mesajı twin'e yansır ve `iot_digital_twin_tick()` artık YENİ SAHTE
       VERİ ÜRETMEZ, yalnızca gelen gerçek son değeri döner
       (`is_live=True`). Broker/kütüphane yoksa bu senaryo atlanır.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.app_shell import AppSession
from harita.digital_twin.iot_bridge import MqttBackendUnavailable


def _make_session_with_building(tmp_path):
    session = AppSession(tmp_path / "registry.hprojreg")
    info = session.create_project("Proje", tmp_path / "p.hproj")
    pid = info["project_id"]
    b = session.add_building(
        pid,
        [(0, 0), (10, 0), (10, 10), (0, 10)],
        floor_count=2,
        height_m=6.0,
    )
    return session, pid, b["key"]


def _broker_reachable(host="localhost", port=1883, timeout=0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def test_default_tick_is_simulation_not_live(tmp_path):
    session, pid, key = _make_session_with_building(tmp_path)
    try:
        result = session.iot_digital_twin_tick(pid, key, seed=1)
        assert result["is_live"] is False
        assert "SİMÜLASYON" in result["disclaimer"]
        assert len(result["floors"]) == 2
        assert all(f["has_reading"] for f in result["floors"])
    finally:
        session.close()


def test_connect_iot_bridge_raises_cleanly_when_unreachable(tmp_path, monkeypatch):
    session, pid, key = _make_session_with_building(tmp_path)
    try:
        # Ulaşılamayacağı garanti bir port kullan (roadmap ilkesi: sessiz
        # sahte-başarı yok, gerçek hata çağırana yükselmeli).
        try:
            session.connect_iot_bridge(pid, key, host="localhost", port=1, timeout_s=1.0)
            raise AssertionError("MqttBackendUnavailable beklenirdi")
        except MqttBackendUnavailable:
            pass
        # Bağlantı kurulamadığı için tick hâlâ simülasyon modunda olmalı.
        result = session.iot_digital_twin_tick(pid, key, seed=1)
        assert result["is_live"] is False
    finally:
        session.close()


def test_disconnect_iot_bridge_idempotent_without_connection(tmp_path):
    session, pid, key = _make_session_with_building(tmp_path)
    try:
        assert session.disconnect_iot_bridge(pid, key) is False
    finally:
        session.close()


def test_real_broker_end_to_end_if_available(tmp_path):
    """Gerçek bir yerel Mosquitto broker'ı + paho-mqtt varsa: gerçekten
    MQTT üzerinden yayınlanan bir değerin, hiç sahte veri üretilmeden
    twin'e yansıdığını doğrular. İkisi de yoksa test atlanır (bu ortama
    özgü bir altyapı testi, CI'da broker garanti değildir)."""
    try:
        import paho.mqtt.client  # noqa: F401
    except ImportError:
        return  # paho kurulu değil - atla
    if not _broker_reachable():
        return  # yerel broker çalışmıyor - atla

    session, pid, key = _make_session_with_building(tmp_path)
    try:
        conn = session.connect_iot_bridge(pid, key, host="localhost", port=1883, timeout_s=3.0)
        assert conn["connected"] is True
        prefix = conn["topic_prefix"]

        # Henüz mesaj gelmedi -> gerçek modda ama okuma yok (uydurma yok).
        before = session.iot_digital_twin_tick(pid, key)
        assert before["is_live"] is True
        assert before["floors"][0]["has_reading"] is False

        subprocess.run(
            [
                "mosquitto_pub",
                "-h",
                "localhost",
                "-p",
                "1883",
                "-t",
                f"{prefix}/floor/0/temp",
                "-m",
                "25.3",
            ],
            check=True,
            timeout=5,
        )
        time.sleep(0.5)

        after = session.iot_digital_twin_tick(pid, key)
        assert after["is_live"] is True
        assert after["floors"][0]["temperature_c"] == 25.3
        assert "GERÇEK VERİ" in after["disclaimer"]
    finally:
        session.disconnect_iot_bridge(pid, key)
        session.close()


_ALL_TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    import tempfile

    failures = []
    for fn in _ALL_TESTS:
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(Path(td)) if "monkeypatch" not in fn.__code__.co_varnames else fn(Path(td), None)
                print(f"OK   {fn.__name__}")
            except Exception as exc:  # noqa: BLE001
                failures.append((fn.__name__, exc))
                print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(_ALL_TESTS) - len(failures)}/{len(_ALL_TESTS)} geçti.")
    if failures:
        sys.exit(1)
