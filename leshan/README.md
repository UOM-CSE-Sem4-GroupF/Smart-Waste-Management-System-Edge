# leshan/ — Device Management and Simulated OTA

This directory provides the Person 4 demo path while hardware work is handled separately:

- Eclipse Leshan demo server in Docker.
- A lightweight HTTP device registry for simulator heartbeats and firmware versions.
- An MQTT OTA CLI that sends firmware commands and waits for simulator acknowledgements.

## Run

```bash
cd leshan
docker compose up
```

Local URLs:

- Leshan demo UI: http://localhost:8081
- Simulated device registry: http://localhost:8090/devices

The simulator reports to the registry when `LESHAN_REGISTRY_URL` is set:

```bash
LESHAN_REGISTRY_URL=http://localhost:8090 python ../simulator.py
```

## Push Firmware

Install CLI dependencies:

```bash
pip install -r cli/requirements.txt
```

Single bin:

```bash
python cli/push_update.py --bin BIN-001 --version 2.2.0
```

Default simulated fleet:

```bash
python cli/push_update.py --all --version 2.2.0
```

Use `--failure-rate 0` for a clean demo, or keep the default `0.05` to show failure handling.

## MQTT Topics

```text
commands/bin/<BIN_ID>/firmware
commands/bin/<BIN_ID>/firmware/ack
```

Command payload:

```json
{
  "version": "2.2.0",
  "url": "http://leshan.local/firmware/2.2.0.bin",
  "requested_at": "2026-05-04T10:00:00Z",
  "failure_rate": 0.05
}
```

Ack payload:

```json
{
  "bin_id": "BIN-001",
  "version": "2.2.0",
  "status": "ok",
  "reason": null,
  "timestamp": "2026-05-04T10:00:05Z"
}
```
