# gateway/ — Person 2: Edge Gateway Developer

Node-RED flow that runs on a Raspberry Pi. Subscribes to local Mosquitto, applies dedup + sanity checks, buffers when the cloud broker is down, forwards surviving readings to cloud EMQX. Built and tested in Docker on a laptop until the Pi arrives.

> Full task spec: see [`../TEAM_TASKS.md` § Person 2](../TEAM_TASKS.md#person-2--edge-gateway-developer-node-red)

## MVP milestones (in order)

1. **Local stack up** — Mosquitto + Node-RED via `docker compose up`.
2. **Subscribe + forward** — receive simulator publishes, forward to Person 3's EMQX.
3. **Deduplication + sanity check** — drop duplicates within 60s; reject out-of-range / stale messages.
4. **Buffering on outage** — queue up to 1000 messages when EMQX is down; drain in order on reconnect.

## Files to create

```
gateway/
├── docker-compose.yaml      # Mosquitto + Node-RED
├── flows.json               # exported Node-RED flow (version-controlled)
├── settings.js              # Node-RED config (auth, persistence)
└── tests/
    └── test_gateway.py      # publishes scenarios, asserts forwarded counts
```

## Getting started

```bash
cd gateway/
docker compose up
# Open Node-RED at http://localhost:1880
# Import flows.json after creating it
```

## Branch convention

`p2/<short-task>` — e.g., `p2/dedup-node`, `p2/buffer-on-outage`.

## Dependencies

- Soft: Person 1's flat-schema fix (Day 2 of timeline) — needed for sanity-check accuracy.
- Hard: Person 3's local EMQX stack to forward to (Day 4 of timeline).
