# broker — EMQX + Kafka bridge

This component owns the MQTT broker (EMQX), the Kafka message bus, and the bridge that connects them.

## Services

| Service | Port | Purpose |
|---|---|---|
| EMQX broker | 1883 (MQTT), 18083 (dashboard) | Cloud MQTT endpoint |
| Kafka | 9092 | Message bus for F2 |
| Zookeeper | 2181 | Kafka coordination |
| Kafka UI (Redpanda Console) | 8080 | Inspect Kafka topics |
| MQTT→Kafka bridge | 9100 (/metrics) | Bridge process + metrics |
| Schema validator | 9101 (/metrics) | Contract test consumer |

The validator uses the canonical telemetry schema at
`../simulator/schemas/telemetry.schema.json`.

## Quickstart

```bash
cp .env.example .env
docker compose up -d
```

Check everything is up:

```bash
docker compose ps
```

Open the EMQX dashboard: http://localhost:18083 (admin / admin123)
Open the Kafka UI: http://localhost:8080

## Topic mapping

| MQTT topic | Kafka topic | QoS |
|---|---|---|
| `sensors/bin/+/telemetry` | `waste.bin.telemetry` | 1 |
| `vehicle/+/location` | `waste.vehicle.location` | 1 |

## Envelope format

Every Kafka message is wrapped before publishing:

```json
{
  "version": "1.0",
  "source_service": "emqx",
  "timestamp": "2026-04-15T09:14:22Z",
  "payload": { ...original MQTT payload... }
}
```

## Test the bridge manually

Publish a test MQTT message:
```bash
mosquitto_pub -h localhost -p 1883 \
  -u sensor-device -P sensorpass \
  -t "sensors/bin/BIN-001/telemetry" \
  -m '{"bin_id":"BIN-001","fill_level_pct":72.5,"battery_level_pct":88.0,"signal_strength_dbm":-65,"temperature_c":29.1,"timestamp":"2026-04-15T09:14:22Z","firmware_version":"2.1.4","error_flags":0}'
```

Check it arrived in Kafka via the UI at http://localhost:8080, or with:
```bash
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic waste.bin.telemetry \
  --from-beginning \
  --max-messages 1
```

## Inject a bad payload (trigger schema violation)

```bash
mosquitto_pub -h localhost -p 1883 \
  -u sensor-device -P sensorpass \
  -t "sensors/bin/BIN-BAD/telemetry" \
  -m '{"bin_id":"BIN-BAD","fill_level_pct":999}'
```

Then check the validator metric:
```bash
curl http://localhost:9101/metrics | grep edge_schema_violations
```

## Authentication

Currently running with username/password ACL (`emqx/config/acl.conf`).

For F4 cert-based mTLS auth:
1. Generate device certs with Vault PKI secrets engine.
2. Set `EMQX_LISTENERS__SSL__DEFAULT__SSL_OPTIONS__CACERTFILE` to the CA cert path.
3. Set `EMQX_LISTENERS__SSL__DEFAULT__SSL_OPTIONS__VERIFY` to `verify_peer`.
4. Update each device to present its cert on connection.

## File layout

```
broker/
├── docker-compose.yaml
├── .env.example
├── emqx/config/
│   └── acl.conf
├── bridge/
│   └── bridge.py          # MQTT→Kafka bridge process
├── scripts/
│   └── create-topics.sh   # Idempotent topic setup
├── validator/
│   ├── main.py            # Schema validator consumer
│   ├── Dockerfile
│   └── requirements.txt
└── README.md
```
