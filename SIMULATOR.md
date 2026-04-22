# SWMS Edge Simulator

Group F — Smart Waste Management System
F4 Platform / F1 Edge

Simulates the ESP32 bin sensor firmware for local development and integration testing. Runs 10 virtual bins across 4 city zones, each filling up gradually and publishing telemetry to EMQX via MQTT — exactly as real hardware would.

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Start all 10 bin simulators
docker compose up
```

Telemetry starts flowing to Kafka topic `waste.bin.telemetry` immediately via the EMQX bridge.

To consume live in a terminal:
```bash
kcat -F ../Smart-Waste-Management-System-Platform/terraform/client.properties \
     -t waste.bin.telemetry -C -o end -q
```

---

## Configuration

All config lives in `.env`:

| Variable | Default | Description |
|---|---|---|
| `MQTT_BROKER` | *(NLB DNS)* | EMQX broker hostname |
| `MQTT_PORT` | `1883` | MQTT port |
| `MQTT_USER` | `sensor-device` | MQTT username (provisioned in EMQX) |
| `MQTT_PASSWORD` | `swms-sensor-dev-2026` | MQTT password |
| `MQTT_TOPIC_PREFIX` | `sensors` | Topic prefix — publishes to `sensors/bin/{bin_id}/telemetry` |
| `SIM_SPEED_FACTOR` | `60` | Time compression. `60` = 1 real second equals 1 simulated minute. Set to `1` for real-time. |

`SIM_SPEED_FACTOR` is the key tuning knob. At `60` you'll see bins fill up over minutes at your desk. At `1` the simulator matches actual ESP32 sleep cycles (bins take hours to fill).

---

## Bin Fleet

10 bins are simulated simultaneously, each on its own thread:

| Bin ID | Zone | Waste Category | Volume |
|---|---|---|---|
| BIN-001 | Zone-1 | food_waste | 240 L |
| BIN-002 | Zone-1 | general | 240 L |
| BIN-003 | Zone-2 | paper | 360 L |
| BIN-004 | Zone-2 | plastic | 240 L |
| BIN-005 | Zone-2 | glass | 120 L |
| BIN-006 | Zone-3 | food_waste | 240 L |
| BIN-007 | Zone-3 | general | 360 L |
| BIN-008 | Zone-3 | plastic | 240 L |
| BIN-009 | Zone-4 | glass | 120 L |
| BIN-010 | Zone-4 | general | 240 L |

---

## How Bins Behave

### Fill progression

Each bin fills gradually based on its waste category. Fill level accumulates between readings — it never jumps randomly.

Base fill rates (% per simulated hour):

| Waste Category | Min | Max | Why |
|---|---|---|---|
| food_waste | 4.0 | 8.0 | High turnover near restaurants and markets |
| general | 2.0 | 4.0 | Mixed use, moderate volume |
| paper | 1.0 | 3.0 | Office/retail areas, bulky but light |
| plastic | 0.8 | 2.0 | Slow disposal rate |
| glass | 0.3 | 1.0 | Heavy per litre, infrequent disposal |

Each bin is assigned a random rate within its category's range at startup — reflecting real-world variation between locations.

### Time-of-day variation

`food_waste` and `general` bins follow human activity patterns:

| Period | Hours | Multiplier |
|---|---|---|
| Morning rush | 07:00 – 10:00 | ×1.8 |
| Lunch | 12:00 – 14:00 | ×1.5 |
| Evening peak | 18:00 – 21:00 | ×2.0 |
| Overnight | 23:00 – 06:00 | ×0.3 |
| Other hours | — | ×1.0 |

`paper`, `plastic`, and `glass` bins use a flat rate (no time-of-day variation).

### Publish interval

Matches the ESP32 firmware sleep cycle from the service spec:

| Fill level | Interval (real time) | Simulated at 60× |
|---|---|---|
| < 50% | 10 minutes | ~10 seconds |
| 50 – 75% | 5 minutes | ~5 seconds |
| 75 – 90% | 2 minutes | ~2 seconds |
| > 90% | 30 seconds | ~0.5 seconds |

Urgent and critical bins publish much more frequently — downstream consumers (Flink, orchestrator) see near-real-time updates when action is needed.

### Collection simulation

When a bin reaches **95% full**, it is automatically "collected":
- Fill level resets to 2 – 8% (simulates the driver emptying the bin)
- A `COLLECTED` log line is emitted
- The bin resumes normal filling from the reset level

### Battery

- Starts at 85 – 100% per bin
- Drains ~0.003 – 0.008% per reading cycle
- When it falls below 15%, it resets to 92 – 100% (simulates a battery replacement)

### Sensor noise

A small Gaussian jitter (±0.2%) is added to fill level each reading — matching the noise profile of a real ultrasonic sensor. The resulting time-series looks organic rather than perfectly linear.

---

## Message Format

Each bin publishes to `sensors/bin/{bin_id}/telemetry` as flat JSON:

```json
{
  "bin_id": "BIN-001",
  "fill_level_pct": 67.3,
  "battery_level_pct": 81.4,
  "signal_strength_dbm": -68,
  "temperature_c": 29.1,
  "timestamp": "2026-04-22T09:14:22Z",
  "firmware_version": "2.1.4",
  "error_flags": 0
}
```

| Field | Type | Description |
|---|---|---|
| `bin_id` | string | Bin identifier — matches `bins` table primary key |
| `fill_level_pct` | float | 0.00 – 100.00, ultrasonic sensor reading |
| `battery_level_pct` | float | 0.0 – 100.0 |
| `signal_strength_dbm` | int | RSSI, typically –55 to –85 dBm |
| `temperature_c` | float | Ambient temperature at sensor |
| `timestamp` | string | ISO 8601 UTC |
| `firmware_version` | string | Hardcoded `2.1.4` |
| `error_flags` | int | `0` = normal; `1` = sensor read error (~1% of readings) |

EMQX bridges this to Kafka `waste.bin.telemetry` wrapped in the standard envelope:

```json
{
  "version": "1.0",
  "source_service": "emqx-kafka-bridge",
  "timestamp": "...",
  "payload": { ...the flat JSON above... }
}
```

Downstream consumers (Flink, kafka_consumer.py) read `message.payload.fill_level_pct`.

---

## Example Log Output

```
2026-04-22 09:00:01 [BIN-001] Starting — category=food_waste zone=1 fill=18.4% rate=6.31%/hr
2026-04-22 09:00:02 [BIN-002] Starting — category=general zone=1 fill=31.0% rate=3.12%/hr
...
2026-04-22 09:00:12 [BIN-001] fill= 18.4% bat=94.2% sig=-67dBm  →  next in 10s
2026-04-22 09:00:22 [BIN-001] fill= 19.5% bat=94.2% sig=-69dBm  →  next in 10s
...
2026-04-22 09:03:10 [BIN-001] fill= 51.2% bat=93.8% sig=-66dBm  →  next in 5s
...
2026-04-22 09:05:45 [BIN-001] fill= 76.1% bat=93.5% sig=-68dBm  →  next in 2s
...
2026-04-22 09:07:12 [BIN-001] fill= 91.8% bat=93.3% sig=-67dBm  →  next in 0s
2026-04-22 09:07:13 [BIN-001] *** COLLECTED *** fill 95.3% → reset to 5.7%
2026-04-22 09:07:13 [BIN-001] fill=  5.7% bat=93.3% sig=-66dBm  →  next in 10s
```

---

## Integration

```
simulator.py
    │  MQTT  sensors/bin/{bin_id}/telemetry
    ▼
EMQX (messaging namespace)
    │  Kafka bridge
    ▼
waste.bin.telemetry
    │
    ├── F2 Flink — urgency scoring, fill rate calculation, InfluxDB write
    └── Application kafka_consumer.py — live monitoring terminal
```

The simulator is the only source of data for `waste.bin.telemetry` in development. All downstream services (Flink, OR-Tools, orchestrator) react to this data exactly as they would to real ESP32 sensors.
