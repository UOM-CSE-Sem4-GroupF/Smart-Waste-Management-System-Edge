# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

This is the **edge simulator** for the Group F Smart Waste Management System — a university project. It simulates an ESP32 IoT smart bin device by publishing realistic telemetry to an EMQX MQTT broker. Real ESP32 firmware lives in `group-f-edge/firmware`; this simulator lets the rest of the system run without physical hardware.

The simulator publishes to `{MQTT_TOPIC_PREFIX}/{MQTT_BIN_ID}/telemetry` every 10 seconds. EMQX then bridges that to Kafka topic `waste.bin.telemetry`, which the Flink stream processor (F2) consumes.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the simulator directly
python simulator.py

# Run with Docker Compose (recommended — loads .env automatically)
docker compose up

# Build image only
docker build -t swms-edge-simulator .
```

There are no tests in this repo. Stop the simulator with `Ctrl+C`.

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```
MQTT_BROKER=<EMQX broker hostname or NLB URL>
MQTT_PORT=1883
MQTT_USER=sensor-device
MQTT_PASSWORD=<password>
MQTT_TOPIC_PREFIX=sensors
MQTT_BIN_ID=bin-node-001
```

The production `.env` targets the AWS NLB fronting EMQX deployed in the Kubernetes cluster (`messaging` namespace). The `sensor-device` Keycloak role is the machine account for MQTT device authentication.

## Telemetry Payload

Each publish sends a JSON payload mimicking ESP32 sensor output:

```json
{
  "metadata": { "node_id": "bin-node-001", "hw_version": "esp32-v2.1", "uptime_seconds": 12345 },
  "sensors": {
    "fill_level_percent": 0–95,
    "battery_voltage": 3.3–4.2,
    "temperature_c": 20.0–35.0,
    "tilted": false  (true ~5% of the time)
  },
  "status": "online"  (or "maintenance" ~1% of the time)
}
```

**Note:** The real EMQX-expected schema (from the firmware spec) uses a flat structure with `bin_id`, `fill_level_pct`, `battery_level_pct`, `signal_strength_dbm`, `temperature_c`, `timestamp`, `firmware_version`, and `error_flags`. This simulator uses a slightly different nested structure — be aware of this mismatch when wiring to downstream services.

## System Context

This repo is part of a larger 18-service system. The simulator's data flows:

```
simulator.py → EMQX (F4, Kubernetes messaging namespace)
            → Kafka waste.bin.telemetry
            → Flink stream processor (F2)
            → waste.bin.processed (Kafka)
            → Collection workflow orchestrator (F3)
```

**Urgency score thresholds** (calculated by Flink, not here):
- `fill_level_percent` < 50 → `normal`
- 50–75 → `monitor`
- 75–90 → `urgent`
- > 90 → `critical`; urgency_score ≥ 80 triggers an emergency collection job

**Weight calculation** used across the system (never hardcode):
```
bin_weight_kg = (fill_level_pct / 100) × volume_litres × avg_kg_per_litre
```
`avg_kg_per_litre` is loaded from the `waste_categories` PostgreSQL table (F2 owns it).

## Key Platform Rules

- All Kafka messages must include `version`, `source_service`, and `timestamp` at the top level.
- Secrets go in `.env` (gitignored). Never commit credentials.
- The Docker image must not run as root and must expose a `/health` endpoint if it becomes a long-lived service.
- Internal Kubernetes service calls use `/internal/*` prefix and bypass Kong; all external calls go through Kong with a Keycloak JWT.

## Full System Reference

For complete service specs, Kafka topic registry, database schemas, and coding standards, see `service-specifications.md` and `CLAUDE_guide.md` in this repo. The authoritative context document for all sub-groups is `CLAUDE_guide.md`.
