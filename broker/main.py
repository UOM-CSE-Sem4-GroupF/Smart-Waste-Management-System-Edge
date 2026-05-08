"""
Schema validator.

Reads from Kafka topic waste.bin.telemetry, validates each message
against the telemetry JSON Schema (from Person 1), and exposes
Prometheus metrics including edge_schema_violations_total.

Endpoints:
  GET /metrics  — Prometheus scrape
  GET /health   — always 200 if process alive
  GET /ready    — 200 once Kafka consumer is connected
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jsonschema
from confluent_kafka import Consumer, KafkaError, KafkaException
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","component":"validator","msg":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("validator")

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
KAFKA_TOPIC     = os.getenv("KAFKA_TOPIC", "waste.bin.telemetry")
KAFKA_GROUP_ID  = os.getenv("KAFKA_GROUP_ID", "schema-validator")
SCHEMA_PATH     = os.getenv("SCHEMA_PATH", "/app/telemetry.schema.json")
METRICS_PORT    = int(os.getenv("METRICS_PORT", "9101"))

msgs_consumed   = Counter("edge_validator_consumed_total",   "Messages consumed from Kafka")
msgs_valid      = Counter("edge_validator_valid_total",      "Messages that passed schema validation")
msgs_violations = Counter("edge_schema_violations_total",    "Messages that failed schema validation", ["reason"])
msgs_parse_err  = Counter("edge_validator_parse_errors_total","Messages with unparseable JSON")
validate_latency = Histogram("edge_validator_latency_seconds", "Validation processing latency")
kafka_lag        = Gauge("edge_validator_kafka_lag",          "Approximate consumer lag")
ready_flag       = False


def load_schema(path: str) -> dict:
    with open(path) as f:
        schema = json.load(f)
    log.info(f"Loaded schema from {path}")
    return schema


def validate_message(envelope: dict, schema: dict) -> tuple[bool, str]:
    """
    Validate the payload inside the envelope against the telemetry schema.
    Returns (is_valid, reason_if_invalid).
    """
    if "payload" not in envelope:
        return False, "missing_envelope_payload"

    payload = envelope["payload"]

    try:
        jsonschema.validate(instance=payload, schema=schema)
        return True, ""
    except jsonschema.ValidationError as exc:
        reason = exc.validator  # e.g. "required", "type", "minimum"
        return False, str(reason)
    except jsonschema.SchemaError as exc:
        log.error(f"Schema itself is invalid: {exc}")
        return False, "invalid_schema"


class ValidatorConsumer:
    def __init__(self, schema: dict):
        self.schema = schema
        self.running = False
        self.consumer = Consumer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": KAFKA_GROUP_ID,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
            "session.timeout.ms": 30000,
        })

    def start(self):
        global ready_flag
        self.running = True
        self.consumer.subscribe([KAFKA_TOPIC])
        log.info(f"Subscribed to Kafka topic: {KAFKA_TOPIC}")
        ready_flag = True

        try:
            while self.running:
                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    log.error(f"Kafka error: {msg.error()}")
                    continue

                msgs_consumed.inc()
                start = time.monotonic()

                raw = msg.value()
                try:
                    envelope = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    log.error(f"Cannot parse message: {exc}")
                    msgs_parse_err.inc()
                    continue

                is_valid, reason = validate_message(envelope, self.schema)
                elapsed = time.monotonic() - start
                validate_latency.observe(elapsed)

                if is_valid:
                    msgs_valid.inc()
                    log.debug(f"Valid: bin_id={envelope.get('payload', {}).get('bin_id', '?')}")
                else:
                    msgs_violations.labels(reason=reason).inc()
                    log.warning(
                        f"Schema violation [{reason}]: "
                        f"bin_id={envelope.get('payload', {}).get('bin_id', '?')} | "
                        f"raw={raw.decode('utf-8')[:200]}"
                    )

        except KafkaException as exc:
            log.error(f"Fatal Kafka exception: {exc}")
        finally:
            self.consumer.close()
            log.info("Consumer closed.")

    def stop(self):
        self.running = False


class MetricsHandler(BaseHTTPRequestHandler):
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
    server = HTTPServer(("0.0.0.0", METRICS_PORT), MetricsHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info(f"Metrics/health server on :{METRICS_PORT}")


def main():
    schema = load_schema(SCHEMA_PATH)
    validator = ValidatorConsumer(schema)

    def shutdown(sig, frame):
        log.info("Shutdown signal received.")
        validator.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    start_metrics_server()
    validator.start()


if __name__ == "__main__":
    main()
