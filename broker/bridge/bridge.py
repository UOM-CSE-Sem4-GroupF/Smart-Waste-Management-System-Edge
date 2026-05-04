"""
MQTT → Kafka bridge.

Subscribes to EMQX on:
  sensors/bin/+/telemetry  → Kafka: waste.bin.telemetry
  vehicle/+/location       → Kafka: waste.vehicle.location

Wraps each payload in the standard project envelope before publishing.
Exposes Prometheus metrics on /metrics (port 9100).
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import paho.mqtt.client as mqtt
from confluent_kafka import Producer, KafkaException
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("emqx-bridge")

EMQX_HOST     = os.getenv("EMQX_HOST", "localhost")
EMQX_PORT     = int(os.getenv("EMQX_PORT", "1883"))
EMQX_USERNAME = os.getenv("EMQX_USERNAME", "sensor-device")
EMQX_PASSWORD = os.getenv("EMQX_PASSWORD", "sensorpass")
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
METRICS_PORT  = int(os.getenv("METRICS_PORT", "9100"))

TOPIC_MAP = {
    "sensors/bin": "waste.bin.telemetry",
    "vehicle":     "waste.vehicle.location",
}

MQTT_SUBSCRIPTIONS = [
    ("sensors/bin/+/telemetry", 1),
    ("vehicle/+/location", 1),
]

msgs_received  = Counter("edge_bridge_received_total",  "MQTT messages received",  ["mqtt_topic"])
msgs_forwarded = Counter("edge_bridge_forwarded_total", "Messages forwarded to Kafka", ["kafka_topic"])
msgs_dropped   = Counter("edge_bridge_dropped_total",   "Messages dropped (bad JSON, routing error)", ["reason"])
kafka_connected = Gauge("edge_mqtt_connected", "1 if bridge is connected to MQTT", ["component"])
publish_latency = Histogram("edge_publish_latency_seconds", "Kafka publish latency", ["kafka_topic"])
ready_flag = False


def build_envelope(payload_dict: dict) -> dict:
    return {
        "version": "1.0",
        "source_service": "emqx",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "payload": payload_dict,
    }


def resolve_kafka_topic(mqtt_topic: str) -> Optional[str]:
    for prefix, kafka_topic in TOPIC_MAP.items():
        if mqtt_topic.startswith(prefix):
            return kafka_topic
    return None


class KafkaBridge:
    def __init__(self):
        self.producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "acks": "all",
            "retries": 5,
            "retry.backoff.ms": 300,
            "enable.idempotence": True,
        })

    def _delivery_report(self, err, msg):
        if err:
            log.error(f"Kafka delivery failed: {err}")
            msgs_dropped.labels(reason="kafka_delivery_error").inc()
        else:
            log.debug(f"Delivered to {msg.topic()} [{msg.partition()}] @ {msg.offset()}")

    def send(self, kafka_topic: str, envelope: dict):
        start = time.monotonic()
        value = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        self.producer.produce(
            topic=kafka_topic,
            value=value,
            callback=self._delivery_report,
        )
        self.producer.poll(0)
        elapsed = time.monotonic() - start
        publish_latency.labels(kafka_topic=kafka_topic).observe(elapsed)
        msgs_forwarded.labels(kafka_topic=kafka_topic).inc()

    def flush(self):
        self.producer.flush(timeout=10)


class MQTTBridge:
    def __init__(self, kafka: KafkaBridge):
        self.kafka = kafka
        self.client = mqtt.Client(client_id="emqx-kafka-bridge", protocol=mqtt.MQTTv5)
        self.client.username_pw_set(EMQX_USERNAME, EMQX_PASSWORD)
        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message    = self._on_message

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        global ready_flag
        if rc == 0:
            log.info(f"Connected to EMQX at {EMQX_HOST}:{EMQX_PORT}")
            kafka_connected.labels(component="emqx-bridge").set(1)
            ready_flag = True
            client.subscribe(MQTT_SUBSCRIPTIONS)
            log.info(f"Subscribed to {[t for t, _ in MQTT_SUBSCRIPTIONS]}")
        else:
            log.error(f"MQTT connect failed with code {rc}")
            kafka_connected.labels(component="emqx-bridge").set(0)
            ready_flag = False

    def _on_disconnect(self, client, userdata, rc, properties=None):
        global ready_flag
        log.warning(f"Disconnected from EMQX (rc={rc}), will reconnect...")
        kafka_connected.labels(component="emqx-bridge").set(0)
        ready_flag = False

    def _on_message(self, client, userdata, msg):
        mqtt_topic = msg.topic
        msgs_received.labels(mqtt_topic=mqtt_topic).inc()

        kafka_topic = resolve_kafka_topic(mqtt_topic)
        if not kafka_topic:
            log.warning(f"No Kafka mapping for topic '{mqtt_topic}' — dropping")
            msgs_dropped.labels(reason="no_route").inc()
            return

        try:
            payload_dict = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.error(f"Cannot decode payload on {mqtt_topic}: {exc}")
            msgs_dropped.labels(reason="bad_json").inc()
            return

        envelope = build_envelope(payload_dict)

        try:
            self.kafka.send(kafka_topic, envelope)
            log.info(f"{mqtt_topic} → {kafka_topic}")
        except KafkaException as exc:
            log.error(f"Kafka send failed: {exc}")
            msgs_dropped.labels(reason="kafka_error").inc()

    def start(self):
        self.client.connect(EMQX_HOST, EMQX_PORT, keepalive=60)
        self.client.loop_forever(retry_first_connection=True)

    def stop(self):
        self.client.disconnect()
        self.kafka.flush()


class MetricsServer(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body = generate_latest()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        elif self.path == "/ready":
            if ready_flag:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ready")
            else:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"not ready")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def start_metrics_server():
    server = HTTPServer(("0.0.0.0", METRICS_PORT), MetricsServer)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info(f"Metrics server listening on :{METRICS_PORT}")


def main():
    kafka = KafkaBridge()
    bridge = MQTTBridge(kafka)

    def shutdown(sig, frame):
        log.info("Shutting down...")
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    start_metrics_server()
    log.info(f"Bridge starting — EMQX={EMQX_HOST}:{EMQX_PORT}, Kafka={KAFKA_BOOTSTRAP}")
    bridge.start()


if __name__ == "__main__":
    main()
