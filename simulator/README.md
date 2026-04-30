# simulator/ — Person 1: ESP32 Firmware Engineer

Pretends to be the ESP32 chip on a bin. Reads the (simulated) ToF distance, runs the adaptive sleep cycle, publishes telemetry over MQTT, buffers to local storage on outage. The future C++ firmware copies this design.

> Full task spec: see [`../TEAM_TASKS.md` § Person 1](../TEAM_TASKS.md#person-1--esp32-firmware-engineer)

## MVP milestones (in order)

1. **Schema fix** — flatten the simulator payload to match the firmware spec (`bin_id`, `fill_level_pct`, etc.). The current `../simulator.py` uses a nested `metadata` / `sensors` shape — flagged in `../CLAUDE.md`.
2. **`BinSensor` class** — extract from `simulator.py`, no module-level state.
3. **Adaptive sleep cycle + JSON Schema** — four interval bands + `schemas/telemetry.schema.json`.
4. **Local buffering** — SQLite spool; resends on reconnect.

## Files to create

```
simulator/
├── bin_sensor.py
├── buffer.py
├── leshan_client.py        # added by Person 4 — coordinate
├── schemas/
│   └── telemetry.schema.json
└── tests/
    └── test_bin_sensor.py
```

Then update the existing `../simulator.py` (or move it under `simulator/`) to use `BinSensor`.

## Getting started

```bash
cd simulator/
python -m venv .venv && source .venv/bin/activate
pip install -r ../requirements.txt
pytest tests/ -v
```

## Branch convention

`p1/<short-task>` — e.g., `p1/schema-fix`, `p1/bin-sensor-class`.
