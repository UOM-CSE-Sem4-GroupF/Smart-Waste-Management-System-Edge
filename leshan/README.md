# leshan/ — Person 4: Device Management & OTA Engineer

Eclipse Leshan server running in Docker, plus the OTA (Over-The-Air) firmware-update command path. Each simulated bin registers with Leshan on startup and reports its current firmware version. A CLI pushes a firmware command to one or all bins; the simulator acknowledges and updates its reported version.

> Full task spec: see [`../TEAM_TASKS.md` § Person 4](../TEAM_TASKS.md#person-4--device-management--ota-engineer-leshan)

## MVP milestones (in order)

1. **Leshan up in Docker** — web UI on `localhost:8080`.
2. **Many bins register** — every simulator instance shows up with version + heartbeat.
3. **OTA command end-to-end** — CLI → simulator receives → Leshan UI shows new version within 30s.
4. **Bulk update + failure handling** — `--all` works; ~5% of bins fail with logged reason.

## Files to create

```
leshan/
├── docker-compose.yaml
├── cli/
│   ├── push_update.py           # ./push_update.py --version 2.2.0 --bin BIN-001
│   └── requirements.txt
└── README.md
```

Plus a small client added to the simulator (coordinate with Person 1):
```
../simulator/leshan_client.py
```

## MQTT topics owned by this component

```
commands/bin/<BIN_ID>/firmware       # push: { "version": "2.2.0", "url": "..." }
commands/bin/<BIN_ID>/firmware/ack   # ack:  { "status": "ok" | "failed", "reason": "..." }
```

## Getting started

```bash
cd leshan/
docker compose up
# Leshan UI at http://localhost:8080
# In another terminal:
cd cli/
python push_update.py --all --version 2.2.0
```

## Branch convention

`p4/<short-task>` — e.g., `p4/leshan-stack`, `p4/ota-cli`, `p4/registration-client`.

## Dependencies

- Hard: Person 1's `BinSensor` class — Person 4 adds an LwM2M client to it.
- Soft: Person 3's MQTT broker to publish OTA commands through.
