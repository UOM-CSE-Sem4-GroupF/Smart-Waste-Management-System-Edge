"""SWMS Edge Simulator — entrypoint.

Per-bin state and payload generation now live in `simulator/bin_sensor.py`;
durable spooling for failed publishes lives in `simulator/buffer.py`. This
module wires those together with paho-mqtt and the bin fleet definition.

Behaviour preserved from the previous version:
  * Loads MQTT settings from .env
  * Spawns one thread per bin with staggered starts
  * SIM_SPEED_FACTOR compresses time for desk demos
  * Ctrl+C exits cleanly

New behaviour:
  * If a publish is rejected because the broker is unreachable, the
    payload is appended to a SQLite spool. On reconnect, the spool is
    drained in FIFO order so no readings are lost across short outages.
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from jsonschema import Draft202012Validator
import paho.mqtt.client as mqtt
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from dotenv import load_dotenv

# Make the in-repo simulator/ package importable whether the script is run
# from the repo root (`python simulator.py`) or via Docker (WORKDIR=/app).

from simulator.bin_sensor import BinSensor   # noqa: E402
from simulator.buffer import TelemetryBuffer   # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("swms-edge-sim")

load_dotenv()

BROKER       = os.getenv("MQTT_BROKER")
PORT         = int(os.getenv("MQTT_PORT", "1883"))
USER         = os.getenv("MQTT_USER")
PASS         = os.getenv("MQTT_PASSWORD")
TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "sensors")
FIRMWARE_VER = os.getenv("FIRMWARE_VERSION", "2.1.4")

# SIM_SPEED_FACTOR compresses time so intervals behave realistically at desk-demo speed.
# 1.0 = real time (10-min sleep between low-fill readings).
# 60  = 1 real second equals 1 simulated minute (good for demos).
SPEED = float(os.getenv("SIM_SPEED_FACTOR", "60"))

# Durable spool — survives process restarts so a crash mid-outage doesn't lose readings.
SPOOL_PATH     = os.getenv("SIM_SPOOL_PATH", "spool.db")
SPOOL_MAX_SIZE = int(os.getenv("SIM_SPOOL_MAX_SIZE", "10000"))
METRICS_PORT   = int(os.getenv("SIM_METRICS_PORT", "9102"))
SCHEMA_PATH    = Path(os.getenv("SIM_SCHEMA_PATH", "simulator/schemas/telemetry.schema.json"))
LESHAN_REGISTRY_URL = os.getenv("LESHAN_REGISTRY_URL")
OTA_FAILURE_RATE = float(os.getenv("OTA_FAILURE_RATE", "0.05"))

publishes_total = Counter(
    "edge_publishes_total",
    "Telemetry publishes attempted by the simulator",
    ["component", "bin_id", "zone"],
)
publish_failures_total = Counter(
    "edge_publish_failures_total",
    "Telemetry publishes spooled after MQTT failure",
    ["component"],
)
publish_latency_seconds = Histogram(
    "edge_publish_latency_seconds",
    "Simulator MQTT publish latency",
    ["component"],
)
buffer_depth = Gauge(
    "edge_buffer_depth",
    "Number of telemetry messages currently spooled",
    ["component"],
)
mqtt_connected = Gauge(
    "edge_mqtt_connected",
    "1 when the simulator MQTT client is connected",
    ["component"],
)
schema_violations_total = Counter(
    "edge_schema_violations_total",
    "Simulator payloads rejected by the telemetry schema",
    ["component", "reason"],
)
ota_updates_total = Counter(
    "edge_ota_updates_total",
    "OTA update attempts handled by the simulator",
    ["status"],
)

# ---------------------------------------------------------------------------
# Bin fleet definition
# Each entry: bin_id, zone_id, waste_category, volume_litres
# ---------------------------------------------------------------------------
BINS: list[dict] = [
    {"bin_id": "BIN-001", "zone_id": 1, "waste_category": "food_waste", "volume_litres": 240},
    {"bin_id": "BIN-002", "zone_id": 1, "waste_category": "general",    "volume_litres": 240},
    {"bin_id": "BIN-003", "zone_id": 2, "waste_category": "paper",      "volume_litres": 360},
    {"bin_id": "BIN-004", "zone_id": 2, "waste_category": "plastic",    "volume_litres": 240},
    {"bin_id": "BIN-005", "zone_id": 2, "waste_category": "glass",      "volume_litres": 120},
    {"bin_id": "BIN-006", "zone_id": 3, "waste_category": "food_waste", "volume_litres": 240},
    {"bin_id": "BIN-007", "zone_id": 3, "waste_category": "general",    "volume_litres": 360},
    {"bin_id": "BIN-008", "zone_id": 3, "waste_category": "plastic",    "volume_litres": 240},
    {"bin_id": "BIN-009", "zone_id": 4, "waste_category": "glass",      "volume_litres": 120},
    {"bin_id": "BIN-010", "zone_id": 4, "waste_category": "general",    "volume_litres": 240},
]


class EdgeClient:
    """Wraps the paho client + the durable spool.

    `publish_or_spool()` is the single call site bin threads use; it tries
    the broker first and falls back to disk if the broker isn't reachable.
    On reconnect, the spool drains in order on a background thread so the
    paho network loop is never blocked by replay I/O.
    """

    def __init__(self, client: mqtt.Client, buffer: TelemetryBuffer) -> None:
        self.client = client
        self.buffer = buffer
        self._connected = threading.Event()
        self._flush_lock = threading.Lock()   # one drain at a time
        self._sensor_lock = threading.RLock()
        self._sensors: dict[str, BinSensor] = {}

    # -- paho callbacks ----------------------------------------------------

    def on_connect(self, _client: mqtt.Client, _userdata, _flags, rc: int) -> None:
        if rc != 0:
            logger.error(f"MQTT connection failed (rc={rc})")
            mqtt_connected.labels(component="simulator").set(0)
            return
        logger.info(f"Connected to EMQX at {BROKER}:{PORT}")
        self._connected.set()
        mqtt_connected.labels(component="simulator").set(1)
        _client.subscribe("commands/bin/+/firmware", qos=1)
        depth = self.buffer.depth()
        buffer_depth.labels(component="simulator").set(depth)
        if depth:
            logger.info(f"Spool has {depth} message(s); replaying in background")
            threading.Thread(target=self._drain_spool, daemon=True).start()

    def on_disconnect(self, _client: mqtt.Client, _userdata, rc: int) -> None:
        self._connected.clear()
        mqtt_connected.labels(component="simulator").set(0)
        if rc != 0:
            logger.warning(f"Unexpected MQTT disconnect (rc={rc}); spooling locally")
        else:
            logger.info("MQTT disconnected cleanly")

    def on_message(self, _client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage) -> None:
        topic_parts = msg.topic.split("/")
        if len(topic_parts) != 4 or topic_parts[:2] != ["commands", "bin"]:
            return
        bin_id = topic_parts[2]
        with self._sensor_lock:
            sensor = self._sensors.get(bin_id)
        if sensor is None:
            logger.warning(f"OTA command for unknown bin {bin_id}; ignoring")
            return

        try:
            command = json.loads(msg.payload.decode("utf-8"))
            target_version = str(command["version"])
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning(f"Bad OTA command on {msg.topic}: {exc}")
            self._publish_ota_ack(bin_id, "unknown", "failed", "bad_command")
            return

        failure_rate = float(command.get("failure_rate", OTA_FAILURE_RATE))
        if random.random() < failure_rate:
            ota_updates_total.labels(status="failed").inc()
            self._publish_ota_ack(bin_id, target_version, "failed", "simulated_failure")
            logger.warning(f"[{bin_id}] OTA update to {target_version} failed")
            return

        sensor.firmware_version = target_version
        ota_updates_total.labels(status="ok").inc()
        self._publish_ota_ack(bin_id, target_version, "ok", None)
        logger.info(f"[{bin_id}] OTA update applied: firmware={target_version}")

    def register_sensor(self, sensor: BinSensor) -> None:
        with self._sensor_lock:
            self._sensors[sensor.bin_id] = sensor

    def _publish_ota_ack(
        self,
        bin_id: str,
        version: str,
        status: str,
        reason: str | None,
    ) -> None:
        ack = {
            "bin_id": bin_id,
            "version": version,
            "status": status,
            "reason": reason,
            "timestamp": _utc_now(),
        }
        self.client.publish(
            f"commands/bin/{bin_id}/firmware/ack",
            json.dumps(ack),
            qos=1,
        )

    # -- bin-thread interface ---------------------------------------------

    def publish_or_spool(self, topic: str, payload: str) -> None:
        """Try the broker; on any failure persist to the spool for replay."""
        if self._connected.is_set():
            start = time.monotonic()
            info = self.client.publish(topic, payload, qos=1)
            publish_latency_seconds.labels(component="simulator").observe(
                time.monotonic() - start
            )
            if info.rc == mqtt.MQTT_ERR_SUCCESS:
                return
            logger.warning(f"Publish rejected (rc={info.rc}); spooling")
        self.buffer.enqueue(topic, payload)
        publish_failures_total.labels(component="simulator").inc()
        buffer_depth.labels(component="simulator").set(self.buffer.depth())

    # -- internals ---------------------------------------------------------

    def _drain_spool(self) -> None:
        # _flush_lock keeps two on_connect events (e.g. brief flap) from
        # racing each other through the same rows.
        if not self._flush_lock.acquire(blocking=False):
            return
        try:
            sent = self.buffer.flush(self._publish_one)
            remaining = self.buffer.depth()
            buffer_depth.labels(component="simulator").set(remaining)
            logger.info(f"Spool drain complete: sent={sent}, remaining={remaining}")
        except Exception as exc:
            logger.warning(f"Spool drain interrupted: {exc}")
        finally:
            self._flush_lock.release()

    def _publish_one(self, topic: str, payload: str) -> bool:
        if not self._connected.is_set():
            return False
        info = self.client.publish(topic, payload, qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS


# ---------------------------------------------------------------------------
# Per-bin loop
# ---------------------------------------------------------------------------

def run_bin_loop(edge: EdgeClient, cfg: dict) -> None:
    sensor = BinSensor(
        bin_id=cfg["bin_id"],
        zone_id=cfg["zone_id"],
        waste_category=cfg["waste_category"],
        volume_litres=cfg["volume_litres"],
        firmware_version=FIRMWARE_VER,
        rng=random.Random(),
    )
    edge.register_sensor(sensor)
    topic = sensor.telemetry_topic(prefix=TOPIC_PREFIX)

    logger.info(
        f"[{sensor.bin_id}] Starting — category={sensor.waste_category} "
        f"zone={sensor.zone_id} fill={sensor.fill_pct:.1f}% "
        f"rate={sensor.base_fill_rate:.2f}%/hr"
    )

    last_publish_time = time.monotonic()
    while True:
        now = time.monotonic()
        elapsed_real_seconds = now - last_publish_time
        elapsed_sim_hours = (elapsed_real_seconds * SPEED) / 3600.0

        payload = sensor.tick(elapsed_sim_hours=elapsed_sim_hours)
        report_device(payload, sensor.zone_id, sensor.waste_category)
        try:
            TELEMETRY_VALIDATOR.validate(payload)
        except Exception as exc:
            schema_violations_total.labels(
                component="simulator",
                reason=exc.__class__.__name__,
            ).inc()
            logger.warning(f"[{sensor.bin_id}] Payload failed schema validation: {exc}")
            last_publish_time = time.monotonic()
            time.sleep(sensor.publish_interval_seconds(speed=SPEED))
            continue

        edge.publish_or_spool(topic, _serialise(payload))
        publishes_total.labels(
            component="simulator",
            bin_id=sensor.bin_id,
            zone=str(sensor.zone_id),
        ).inc()

        # Rapid-fill detection: if the bin jumped >10% this tick, drop straight
        # to the next-shorter interval rather than waiting out the band default.
        interval = sensor.publish_interval_seconds(speed=SPEED)
        if sensor.rapid_fill_detected():
            interval = min(interval, 30.0 / SPEED)
            logger.info(f"[{sensor.bin_id}] Rapid fill detected — shortening interval")

        logger.info(
            f"[{sensor.bin_id}] fill={payload['fill_level_pct']:5.1f}% "
            f"bat={payload['battery_level_pct']:4.1f}% "
            f"sig={payload['signal_strength_dbm']}dBm  →  "
            f"next in {interval:.0f}s  (topic: {topic})"
        )

        last_publish_time = time.monotonic()
        time.sleep(interval)


def _serialise(payload: dict) -> str:
    return json.dumps(payload)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def report_device(payload: dict, zone_id: int, waste_category: str) -> None:
    if not LESHAN_REGISTRY_URL:
        return
    body = json.dumps({
        "bin_id": payload["bin_id"],
        "firmware_version": payload["firmware_version"],
        "last_seen": payload["timestamp"],
        "battery_level_pct": payload["battery_level_pct"],
        "zone_id": zone_id,
        "waste_category": waste_category,
        "online": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LESHAN_REGISTRY_URL.rstrip('/')}/devices/{payload['bin_id']}",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=1).close()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug(f"Device registry update failed: {exc}")


def _load_validator() -> Draft202012Validator:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


TELEMETRY_VALIDATOR = _load_validator()


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
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
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ready")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def start_metrics_server() -> None:
    server = HTTPServer(("0.0.0.0", METRICS_PORT), MetricsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info(f"Simulator metrics/health listening on :{METRICS_PORT}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    if not BROKER:
        logger.error("MQTT_BROKER is not set — see .env.example")
        return

    buffer = TelemetryBuffer(SPOOL_PATH, max_size=SPOOL_MAX_SIZE)
    buffer_depth.labels(component="simulator").set(buffer.depth())
    if buffer.depth():
        logger.info(f"Resuming with {buffer.depth()} message(s) already spooled")

    client = mqtt.Client(client_id="swms-edge-simulator")
    if USER:
        client.username_pw_set(USER, PASS)

    edge = EdgeClient(client, buffer)
    client.on_connect = edge.on_connect
    client.on_disconnect = edge.on_disconnect
    client.on_message = edge.on_message

    logger.info(f"Connecting to EMQX at {BROKER}:{PORT} ...")
    start_metrics_server()
    try:
        client.connect(BROKER, PORT, keepalive=60)
    except Exception as e:
        # Even if the initial connect fails, the loop_start below + paho's
        # auto-reconnect will keep retrying; bin threads spool in the meantime.
        logger.warning(f"Initial broker connect failed: {e} — bins will spool until reconnect")

    client.loop_start()
    time.sleep(2)   # let CONNACK arrive before bin threads start publishing

    threads: list[threading.Thread] = []
    for cfg in BINS:
        t = threading.Thread(target=run_bin_loop, args=(edge, cfg), daemon=True)
        t.start()
        threads.append(t)
        # Stagger so all 10 bins don't publish simultaneously on tick 0.
        time.sleep(random.uniform(0.3, 1.2))

    logger.info(f"All {len(BINS)} bin simulators running (SPEED={SPEED}x, spool={SPOOL_PATH}).")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Simulator stopped.")
    finally:
        client.loop_stop()
        client.disconnect()
        buffer.close()


if __name__ == "__main__":
    main()
