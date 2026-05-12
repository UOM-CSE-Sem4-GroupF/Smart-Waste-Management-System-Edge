# Smart Waste Management System - Edge

F1 owns the edge path for Group F's Smart Waste Management System:

```text
simulated bins -> local Mosquitto -> Node-RED gateway -> EMQX -> Kafka
                                      OTA commands -> simulated bins
```

This integration branch also includes Person 5 observability/CI work and the simulated Person 4 OTA path.

## Quick Start

Start the full local stack:

```bash
docker compose -f ops/docker-compose.full.yaml up --build
```

Useful URLs:

| Service | URL |
|---|---|
| Node-RED | http://localhost:1880 |
| Kafka UI | http://localhost:8080 |
| Leshan-style demo UI | http://localhost:8081 |
| Device registry | http://localhost:8090/devices |
| EMQX dashboard | http://localhost:18083 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

Grafana login is `admin` / `admin`.

## OTA Demo

The simulator listens for firmware commands on local Mosquitto, exposed on host port `1884` in the full stack.

```bash
python3 -m pip install -r leshan/cli/requirements.txt
python3 leshan/cli/push_update.py --all --version 2.2.0 --broker localhost --port 1884 --failure-rate 0
```

Check device versions:

```bash
curl http://localhost:8090/devices
```

## Development Checks

Static checks:

```bash
python3 -m py_compile simulator.py simulator/bin_sensor.py simulator/buffer.py
python3 -m json.tool gateway/flows.json >/tmp/flows.json
docker compose -f ops/docker-compose.full.yaml config
```

Unit/static tests:

```bash
pip install -r requirements.txt
pytest simulator/tests gateway/tests ops/integration -q
```

Live e2e tests:

```bash
RUN_E2E=1 pytest ops/integration -q
```

## Component Notes

- `simulator.py` runs 10 simulated bins, validates telemetry against JSON Schema, buffers failed publishes in SQLite, exposes `/metrics`, and handles OTA commands.
- `gateway/` contains the Node-RED flow for sanity filtering, deduplication, health endpoints, and MQTT forwarding.
- `broker/` contains EMQX, Kafka, the MQTT-to-Kafka bridge, topic creation, and schema validator.
- `leshan/` contains the Leshan demo server, simulated device registry, and OTA CLI.
- `ops/` contains the full compose stack, Prometheus/Grafana assets, CI integration tests, and baseline notes.

## Branches Integrated

- Person 1 simulator package work from `origin/main` and `origin/nadil`.
- Person 2 gateway scaffold from `origin/p2/initial-setup`.
- Person 3 broker work from `origin/krishna`, normalized into the compose layout.
- Person 4 simulated OTA work implemented in this integration branch.
