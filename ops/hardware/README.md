# Hardware Integration Mode

The edge stack supports two producers:

- **Demo mode**: `simulator.py` publishes ten synthetic bins.
- **Hardware mode**: physical ESP32 firmware publishes real ToF/DHT/battery telemetry.

Both modes use the same downstream path:

```text
ESP32 or simulator -> local Mosquitto -> Node-RED -> EMQX -> Kafka -> validator/consumers
```

## Start Hardware Mode

Run the edge services without the simulator:

```bash
docker compose -f ops/docker-compose.full.yaml -f ops/docker-compose.hardware.yaml up -d --build
```

The Raspberry Pi or laptop host exposes local Mosquitto on port `1884`. The
hardware firmware should publish to:

```text
MQTT_BROKER=<raspberry-pi-or-laptop-ip>
MQTT_PORT=1884
MQTT_TOPIC_PREFIX=sensors
topic=sensors/bin/<BIN_ID>/telemetry
```

Local Mosquitto is intentionally anonymous for field testing. EMQX and the
cloud-facing path keep their own credentials inside the compose network.

## Current IoT Firmware Contract

The hardware repo is treated as an upstream input. Do not edit it from this Edge
repo. The current commit publishes this payload shape:

```json
{
  "bin_id": "BIN-EDGE-001",
  "fill_level_pct": 67.3,
  "battery_level_pct": 85.4,
  "signal_strength_dbm": -61,
  "temperature_c": 29.1,
  "timestamp": "2026-05-08T00:00:00Z",
  "firmware_version": "2.1.4",
  "error_flags": 0
}
```

Edge-side compatibility added here:

- The telemetry schema accepts both demo ids (`BIN-001`) and hardware ids
  (`BIN-EDGE-001`).
- Hardware mode sets `ALLOW_STALE_HARDWARE_TIMESTAMPS=true` for Node-RED. This
  normalizes the current firmware's fixed timestamp before forwarding to EMQX
  and Kafka. Demo mode keeps strict stale-timestamp rejection.

Remaining hardware-side requirement: the flashed firmware must point its MQTT
broker to the Raspberry Pi/laptop IP on port `1884`. The current committed
firmware has a placeholder broker, so the hardware owner must provide a build
configured for the viva network.

## Verify Data Flow

Subscribe at the Raspberry Pi/local broker:

```bash
mosquitto_sub -h localhost -p 1884 -t 'sensors/bin/+/telemetry' -v
```

Watch Kafka after Node-RED and EMQX forwarding:

```bash
docker compose -f ops/docker-compose.full.yaml -f ops/docker-compose.hardware.yaml exec kafka \
  kafka-console-consumer --bootstrap-server kafka:29092 \
  --topic waste.bin.telemetry --from-beginning
```

Schema failures appear in validator metrics:

```bash
curl http://localhost:9101/metrics | grep edge_schema_violations_total
```

## Return to Demo Mode

Use the original full stack command:

```bash
docker compose -f ops/docker-compose.full.yaml up -d --build
```

This starts the simulator again and does not require physical hardware.
