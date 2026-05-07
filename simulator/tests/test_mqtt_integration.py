import json
import os
import queue
import time

import pytest
import paho.mqtt.client as mqtt


def test_mqtt_publish_roundtrip() -> None:
    broker = os.getenv("MQTT_BROKER")
    if not broker:
        pytest.skip("MQTT_BROKER not set")

    port = int(os.getenv("MQTT_PORT", "1883"))
    user = os.getenv("MQTT_USER")
    password = os.getenv("MQTT_PASSWORD") or ""
    prefix = os.getenv("MQTT_TOPIC_PREFIX", "sensors")
    topic = f"{prefix}/bin/BIN-TEST/telemetry"

    received = queue.Queue()

    def on_message(_client, _userdata, msg) -> None:
        received.put(msg.payload.decode("utf-8"))

    sub = mqtt.Client(client_id="edge-test-sub")
    if user:
        sub.username_pw_set(user, password)
    sub.on_message = on_message

    try:
        sub.connect(broker, port, 10)
    except Exception as exc:
        pytest.skip(f"MQTT broker not reachable: {exc}")

    sub.subscribe(topic)
    sub.loop_start()

    pub = mqtt.Client(client_id="edge-test-pub")
    if user:
        pub.username_pw_set(user, password)

    try:
        pub.connect(broker, port, 10)
    except Exception as exc:
        sub.loop_stop()
        sub.disconnect()
        pytest.skip(f"MQTT broker not reachable: {exc}")

    payload = {
        "bin_id": "BIN-TEST",
        "fill_level_pct": 55.0,
        "battery_level_pct": 90.0,
        "signal_strength_dbm": -65,
        "temperature_c": 27.5,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "firmware_version": "2.1.4",
        "error_flags": 0,
    }

    pub.publish(topic, json.dumps(payload), qos=1)

    try:
        raw = received.get(timeout=5)
    except queue.Empty:
        pytest.fail("No MQTT message received")
    finally:
        sub.loop_stop()
        sub.disconnect()
        pub.disconnect()

    data = json.loads(raw)
    assert data["bin_id"] == "BIN-TEST"
    assert data["fill_level_pct"] == 55.0
