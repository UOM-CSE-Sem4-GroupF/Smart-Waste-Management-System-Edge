import json
import os
import queue
import time
import uuid

import paho.mqtt.client as mqtt
import pytest


def _connect_client(host: str, port: int, username: str | None, password: str | None) -> mqtt.Client:
    client = mqtt.Client(client_id=f"swms-test-{uuid.uuid4().hex[:8]}")
    if username:
        client.username_pw_set(username, password or "")
    client.connect(host, port, keepalive=30)
    client.loop_start()
    return client


def test_mqtt_roundtrip_when_broker_is_available():
    host = os.getenv("MQTT_BROKER")
    if not host:
        pytest.skip("Set MQTT_BROKER to run the live MQTT integration test")

    port = int(os.getenv("MQTT_PORT", "1883"))
    username = os.getenv("MQTT_USER") or None
    password = os.getenv("MQTT_PASSWORD") or None
    topic = "sensors/bin/BIN-997/telemetry"
    received: queue.Queue[dict] = queue.Queue()

    def on_message(_client, _userdata, message):
        received.put(json.loads(message.payload.decode("utf-8")))

    try:
        subscriber = _connect_client(host, port, username, password)
        publisher = _connect_client(host, port, username, password)
    except OSError as exc:
        pytest.skip(f"MQTT broker is not reachable at {host}:{port}: {exc}")

    subscriber.on_message = on_message

    try:
        result, _mid = subscriber.subscribe(topic, qos=1)
        assert result == mqtt.MQTT_ERR_SUCCESS
        time.sleep(0.2)

        payload = {
            "bin_id": "BIN-997",
            "fill_level_pct": 42.0,
            "battery_level_pct": 88.5,
            "signal_strength_dbm": -62,
            "temperature_c": 29.4,
            "timestamp": "2026-01-01T00:00:00Z",
            "firmware_version": "2.1.4",
            "error_flags": 0,
        }
        publish_info = publisher.publish(topic, json.dumps(payload), qos=1)
        publish_info.wait_for_publish(timeout=5)
        assert publish_info.is_published()

        echoed = received.get(timeout=5)
        assert echoed["bin_id"] == "BIN-997"
        assert echoed["fill_level_pct"] == 42.0
        assert echoed["firmware_version"] == "2.1.4"
    finally:
        publisher.loop_stop()
        publisher.disconnect()
        subscriber.loop_stop()
        subscriber.disconnect()
