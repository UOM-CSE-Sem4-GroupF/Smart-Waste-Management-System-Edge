# broker/ — Person 3: Broker & Bridge Engineer

Local EMQX + Kafka stack with the MQTT→Kafka bridge configured. Wraps each MQTT payload in the standard envelope (`version`, `source_service`, `timestamp`, `payload`) before publishing to Kafka. Also runs a schema validator that consumes `waste.bin.telemetry` and reports violation counts as a Prometheus metric.

> Full task spec: see [`../TEAM_TASKS.md` § Person 3](../TEAM_TASKS.md#person-3--broker--bridge-engineer-emqx--kafka)

## MVP milestones (in order)

1. **EMQX + Kafka up locally** — `docker compose up` brings everything up; topics auto-created with correct retention.
2. **Bridge configured** — MQTT publish on `sensors/bin/+/telemetry` → Kafka `waste.bin.telemetry` with envelope.
3. **End-to-end test through gateway** — Person 1's simulator → Person 2's gateway → EMQX → Kafka.
4. **Kafka schema validator** — consumer running, exposes `/metrics`, increments on bad messages (uses Person 1's JSON Schema).

## Files to create

```
broker/
├── docker-compose.yaml          # EMQX + Kafka + Zookeeper + (optional) Kafka UI
├── emqx/
│   ├── emqx.conf                # bridge + ACL config
│   └── acl.conf
├── scripts/
│   └── create-topics.sh         # idempotent Kafka topic creation
└── validator/
    ├── main.py                  # consumes waste.bin.telemetry, validates against schema
    ├── Dockerfile
    └── requirements.txt
```

## Getting started

```bash
cd broker/
docker compose up
# EMQX dashboard at http://localhost:18083 (admin/public)
# Kafka UI at http://localhost:8080
./scripts/create-topics.sh
```

## Branch convention

`p3/<short-task>` — e.g., `p3/emqx-stack`, `p3/kafka-bridge`, `p3/validator`.

## Dependencies

- Hard: Person 1's `telemetry.schema.json` — validator consumes it.
- Output: Person 2 forwards to this stack; Person 5 scrapes its `/metrics`.
