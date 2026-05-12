# F1 Edge Team — Task Breakdown (2-Week Sprint)

This document divides the F1 (Edge layer) work across 5 team members. Each person owns one sub-system end-to-end, has clearly demonstrable deliverables for evaluation, and can build software now while hardware (ESP32 boards, Raspberry Pi) is still being procured.

> **Timeline: 14 days total.** Milestones marked **MVP** are required for the demo. Milestones marked **STRETCH** only get done if MVP is finished early — these are the first to cut.

> **Authoritative references** — read these before starting:
> - `CLAUDE_guide.md` — full project context (F1–F4 ownership, Kafka topics, schemas)
> - `service-specifications.md` — detailed spec for SERVICE 1 (firmware), SERVICE 2 (gateway), SERVICE 3 (EMQX)
> - `KAFKA_TOPIC_REGISTRY.md.resolved` — message contracts
> - `CLAUDE.md` — repo-specific notes (flags simulator-vs-spec schema mismatch)

---

## The system in one paragraph

A bin has an ESP32 chip with a **Time-of-Flight (ToF) sensor** that measures fill level by timing how long an infrared light pulse takes to bounce off the trash surface. The chip publishes the reading over MQTT to a **Raspberry Pi gateway** running Node-RED. The Pi cleans the data (deduplication, sanity check, buffering on outage) and forwards it to a cloud **EMQX broker**. EMQX bridges the message into a Kafka topic (`waste.bin.telemetry`) that the F2 data team consumes. Eclipse **Leshan** is a separate server we run to track which firmware version each bin is on and push **OTA** (over-the-air) updates. Our team (F1) owns everything from the bin up to the EMQX → Kafka boundary.

```
[ESP32 + ToF sensor]              <-- Person 1 (firmware + simulator)
        |  MQTT (sensors/bin/<id>/telemetry)
        v
[Raspberry Pi: Node-RED gateway]  <-- Person 2 (gateway flows)
        |  MQTT (forwarded after dedup/sanity-check/buffer)
        v
[EMQX broker]                     <-- Person 3 (broker + Kafka bridge)
        |  Kafka (waste.bin.telemetry)
        v
[F2 Flink stream processor]
                                  <-- Person 4 (Leshan + OTA, parallel control plane)
                                  <-- Person 5 (CI/CD, metrics, integration tests, end-to-end glue)
```

---

## Roles at a glance

| # | Owner role                                  | Final platform           | Software task we build now (laptop only) |
|---|---------------------------------------------|--------------------------|-------------------------------------------|
| 1 | **ESP32 Firmware Engineer**                 | ESP32 (C++, later)       | Refactor `simulator.py` into a spec-compliant Python firmware twin |
| 2 | **Edge Gateway Developer**                  | Raspberry Pi + Node-RED  | Build the Node-RED flow on a laptop using Docker |
| 3 | **Broker / Bridge Engineer**                | EMQX cluster (later F4)  | Run EMQX + Kafka locally via Docker Compose; configure the MQTT→Kafka bridge |
| 4 | **Device Management & OTA Engineer**        | Eclipse Leshan + ESP32   | Run Leshan in Docker; build registration + OTA command path against the simulator |
| 5 | **Integration / Observability / CI Lead** | GitHub Actions, Prometheus, Grafana | Wire up CI, metrics, end-to-end integration tests across the four other tracks |

> All five tasks are **software-only and laptop-runnable**. Hardware (ESP32, Pi) only matters at final demo, and even then the same flows/configs deploy unchanged.

---

## Shared conventions (everyone reads this)

These are non-negotiable and come from `CLAUDE_guide.md`. Sticking to them is what makes the five tracks plug together.

### Message envelope (every Kafka message)
```json
{
  "version": "1.0",
  "source_service": "<who-published-it>",
  "timestamp": "2026-04-15T09:14:22Z",
  "payload": { ... }
}
```

### Bin telemetry payload (flat schema — fix the simulator if it's still nested!)
```json
{
  "bin_id": "BIN-047",
  "fill_level_pct": 85.3,
  "battery_level_pct": 72.1,
  "signal_strength_dbm": -67,
  "temperature_c": 28.4,
  "timestamp": "2026-04-15T09:14:22Z",
  "firmware_version": "2.1.4",
  "error_flags": 0
}
```

### MQTT topic structure
- Bin telemetry: `sensors/bin/<BIN_ID>/telemetry`
- Vehicle GPS:   `vehicle/<VEHICLE_ID>/location`
- OTA commands:  `commands/bin/<BIN_ID>/firmware`
- OTA acks:      `commands/bin/<BIN_ID>/firmware/ack`

### Repo layout (decided: single monorepo, one subdirectory per component)
All five components live as subdirectories in this single repo (`Smart-Waste-Management-System-Edge`). This overrides the spec's separate-repo suggestion (`group-f-edge/firmware`, `/gateway`, etc.) — easier coordination across a 2-week sprint, single CI pipeline, single `docker compose up` for the full stack.

```
Smart-Waste-Management-System-Edge/
├── simulator/          # Person 1
├── gateway/            # Person 2 (Node-RED flow JSON + helpers)
├── broker/             # Person 3 (docker-compose for EMQX + Kafka, bridge config)
├── leshan/             # Person 4 (Leshan docker config + OTA CLI)
├── ops/                # Person 5 (Prometheus, Grafana JSON, integration tests)
└── .github/workflows/  # Person 5 (CI)
```

Each component subdirectory owns its own `README.md`, `Dockerfile`, and (where applicable) `docker-compose.yaml`. The top-level `ops/docker-compose.full.yaml` composes everything for end-to-end runs.

### Standards (from CLAUDE_guide.md)
- All long-lived services expose `/health` and `/ready` HTTP endpoints
- Logs in JSON format
- Docker images run as **non-root**
- No secrets in code or git — everything via `.env`
- Every PR must pass CI (Person 5's job to set this up)

---

## Person 1 — ESP32 Firmware Engineer

### Explanation
You own the bin. You write the code that pretends to be the chip on the bin: it reads the fill sensor, decides when to wake up and sleep, and sends a message every so often. Right now we don't have the real chip, so you do this in Python. When the C++ firmware is written later, it copies your design exactly.

### Technical scope
- Refactor the existing `simulator.py` into a clean, testable `BinSensor` class.
- Conform to the **flat** telemetry schema (the current sim uses a nested schema — this is a known bug flagged in `CLAUDE.md`).
- Sensor model: **VL53L1X-style ToF (Time-of-Flight) infrared distance sensor**, ~4 m max range, ±1 mm typical accuracy. Simulate ±0.2% Gaussian noise on the distance reading and a 5% chance of a "lid open" reading (very small distance for 1–2 cycles) so downstream sanity checks have something realistic to filter. Note: ToF sensors can be sensitive to ambient sunlight on transparent bin liners — simulate occasional dropouts (`error_flags` bit set) at midday.
- Implement the **adaptive sleep/publish cycle** from `service-specifications.md` §SERVICE 1:
  - `fill < 50%`  → publish every **10 min**
  - `fill 50–75%` → every **5 min**
  - `fill 75–90%` → every **2 min**
  - `fill > 90%`  → every **30 sec**
- Implement **rapid-fill detection**: if fill increases > 10% in one cycle, drop to the next-shorter interval immediately.
- Implement **NVS-style local buffering**: if MQTT publish fails, persist to a local SQLite file; flush + retry on next successful connect.
- Generate realistic values: fill level (with bin-category fill-rate curves), battery drain, signal strength, temperature, tilt, error flags.
- Provide a **JSON Schema** file describing the telemetry payload and a validator function.
- Write **pytest unit tests** for: sleep-state transitions, fill calculation, buffering/replay, schema conformance.

### Milestones
| # | Tier | Milestone                                                              | Done means                                                                                          |
|---|------|------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| 1 | MVP  | **Schema fix** — flatten payload to match firmware spec               | Downstream Flink/F2 consumes without changes; diff visible                                         |
| 2 | MVP  | **`BinSensor` class extracted**                                        | One class per bin, no module-level globals; reusable across tests                                   |
| 3 | MVP  | **Adaptive sleep cycle + JSON schema**                                 | Unit tests cover all four interval bands; `payload.schema.json` in repo                            |
| 4 | MVP  | **Local buffering**                                                    | Disconnect EMQX → publish 50 readings → reconnect → all 50 arrive in order                          |
| 5 | STRETCH | **Multi-bin orchestrator with category fill curves**                | Single process spawns N bins across zones with realistic fill rates                                |

### Files you will create / edit
- `simulator/bin_sensor.py` — the class
- `simulator/buffer.py` — SQLite spool
- `simulator/schemas/telemetry.schema.json`
- `simulator/tests/test_bin_sensor.py`
- Update `simulator.py` to use the new class

### Demo evidence at evaluation
- Live: run simulator with the broker stopped → start broker → show all buffered messages flush through.
- Tests: `pytest -v` output, schema validation in CI.
- Diff: show the schema fix.

---

## Person 2 — Edge Gateway Developer (Node-RED)

### Explanation
You own the box that sits between the bins and the cloud. Imagine 30 bins in one neighbourhood — instead of each bin punching through to the cloud, they all talk to one Raspberry Pi nearby running **Node-RED** (a drag-and-drop flow tool). The Pi cleans up the messages and forwards them. Your job is to build that flow. Until the Pi arrives, you run Node-RED on your laptop in Docker.

### Technical scope (per `service-specifications.md` §SERVICE 2)
Build a Node-RED flow that:
1. Subscribes to `sensors/bin/+/telemetry` from a **local Mosquitto** broker.
2. **Deduplication filter** — drop a reading if the same `bin_id` sent the same `fill_level_pct` within the last 60 seconds.
3. **Sanity check** — drop if `fill_level_pct` is outside [0, 100] or `timestamp` is older than 5 minutes.
4. **Buffer** — if the cloud EMQX is unreachable, queue up to **1000 messages** (LRU; drop oldest first).
5. **Forward** — publish surviving messages to cloud EMQX on `sensors/bin/<bin_id>/telemetry`.
6. **Health-check flow** — expose `GET /health` and `GET /ready` HTTP endpoints from Node-RED.
7. **Metrics** — emit counters for: messages received, dropped (dedup), dropped (sanity), buffered, forwarded.

### Milestones
| # | Tier | Milestone                                                  | Done means                                                                                  |
|---|------|------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| 1 | MVP  | **Local stack up**                                         | `docker compose up` brings up Mosquitto + Node-RED; manual MQTT publish appears in flow     |
| 2 | MVP  | **Subscribe + forward**                                    | Simulator → Mosquitto → Node-RED → cloud EMQX (Person 3's stack); flow exported to git      |
| 3 | MVP  | **Deduplication + sanity check**                           | Same-value-within-60s rejected; out-of-range/stale timestamp rejected; counters visible     |
| 4 | MVP  | **Buffering on outage**                                    | Stop cloud EMQX → buffer up to 1000; restart → drains in order; 1001st dropped              |
| 5 | STRETCH | **Multi-zone routing**                                  | Flow tags messages with zone before forwarding                                              |

### Files you will create / edit
- `gateway/docker-compose.yaml` — Mosquitto + Node-RED
- `gateway/flows.json` — the exported flow
- `gateway/README.md` — how to run, how to import flow
- `gateway/tests/` — Python (or shell) test harness that publishes scenarios and asserts forwarded counts

### Demo evidence at evaluation
- Open Node-RED UI → walk through the wired nodes.
- Run a 60-second test that produces dedup, sanity, and buffer events; show counters.
- Show the JSON file in git.

---

## Person 3 — Broker & Bridge Engineer (EMQX + Kafka)

### Explanation
EMQX and Kafka are the "post offices" of the system. EMQX speaks the language sensors speak (MQTT). Kafka speaks the language cloud apps speak. You wire them together so a message from a bin ends up in Kafka without any service writing custom code. F4 will run the production EMQX cluster — but **F1 owns the bridge configuration**. Until that's ready, you run a local stack on your laptop so the rest of us have something to publish to.

### Technical scope (per `service-specifications.md` §SERVICE 3)
- **Local stack via Docker Compose**: EMQX + Kafka + Zookeeper + (optional) Kafka UI like Redpanda Console for inspection.
- **Configure EMQX → Kafka bridge** matching the spec:
  ```
  sensors/bin/+/telemetry  →  Kafka topic  waste.bin.telemetry      QoS 1
  vehicle/+/location       →  Kafka topic  waste.vehicle.location   QoS 1
  ```
- **Wrap each MQTT payload in the standard envelope** before it hits Kafka:
  ```json
  { "version": "1.0", "source_service": "emqx", "timestamp": "...", "payload": <original> }
  ```
- **Authentication**: configure username/password ACL for the `sensor-device` user (matches `.env.example`); document the cert-based path that F4 will plug into Vault later.
- **Schema validator** — a small Kafka consumer (Python, `confluent-kafka` or `aiokafka`) that:
  - Reads `waste.bin.telemetry`
  - Validates each message against the JSON Schema from Person 1
  - Reports schema-violation rate as a Prometheus metric
  - Used by CI as the contract test between F1 and F2
- **Topic creation**: idempotent script that creates required topics with retention from the Kafka registry (`waste.bin.telemetry` 7 days, `waste.vehicle.location` 7 days).

### Milestones
| # | Tier | Milestone                                            | Done means                                                                                  |
|---|------|------------------------------------------------------|---------------------------------------------------------------------------------------------|
| 1 | MVP  | **EMQX + Kafka up locally**                          | `docker compose up` brings everything up; UIs reachable; topics auto-created                |
| 2 | MVP  | **Bridge configured**                                | MQTT publish appears in matching Kafka topic wrapped in envelope                            |
| 3 | MVP  | **End-to-end test through gateway**                  | Simulator → gateway → EMQX → Kafka in a single demo run                                     |
| 4 | MVP  | **Kafka schema validator**                           | Consumer running, exposes `/metrics`; injecting bad message increments violation counter    |
| 5 | STRETCH | **Auth (username/password) + TLS**                | Anonymous publish refused; gateway connects with mTLS to local EMQX                         |

### Files you will create / edit
- `broker/docker-compose.yaml`
- `broker/emqx/config/` — bridge config, ACL
- `broker/scripts/create-topics.sh`
- `broker/validator/main.py`, `broker/validator/Dockerfile`
- `broker/README.md`

### Demo evidence at evaluation
- Open EMQX dashboard → show bridge connected.
- Open Kafka UI → show messages flowing in real time with envelope.
- Inject a bad payload → show validator metric increments.

---

## Person 4 — Device Management & OTA Engineer (Leshan)

### Explanation
When you have 1000 bins out in the city, you can't physically visit each one to fix bugs in their software. **OTA (Over-The-Air) updates** let us push new firmware to all bins from one place. **Eclipse Leshan** is a free open-source server (written in Java, but we don't write Java — we just run it) that tracks which bin is on which firmware version and pushes updates. You stand up Leshan, make the simulator register itself with it, and build the path that says "everyone go to firmware v2.2.0".

### Technical scope
- **Run Leshan server in Docker** — there's an official image `leshan/leshan-server-demo`.
- **Bin registration**: extend the simulator (Person 1's class) so each bin, on startup, registers with Leshan with its `bin_id` and current `firmware_version`. Periodic heartbeat (LwM2M update) every 60s.
- **OTA command path**:
  - Define MQTT topic `commands/bin/<BIN_ID>/firmware` for push commands.
  - Build a CLI tool `leshan/cli/push-update.py --version 2.2.0 --bin BIN-001` (or `--all`) that publishes the command.
  - Simulator subscribes, "downloads" (logs the URL), updates its reported `firmware_version`, publishes ack to `commands/bin/<BIN_ID>/firmware/ack`.
- **Status dashboard**: Leshan's built-in UI shows registered devices and current versions — this *is* the demo artifact.
- **Failure modeling**: simulate 5% of bins fail to update (battery low, network drop) → emit ack with `status: "failed"`.

### Milestones
| # | Tier | Milestone                                              | Done means                                                                            |
|---|------|--------------------------------------------------------|---------------------------------------------------------------------------------------|
| 1 | MVP  | **Leshan up in Docker**                                | `docker compose up leshan` → web UI on `localhost:8080`                               |
| 2 | MVP  | **Many bins register**                                 | All simulator instances appear in Leshan UI with version + heartbeat                  |
| 3 | MVP  | **OTA command end-to-end**                             | CLI → simulator logs receive → Leshan UI shows new version within 30s                 |
| 4 | MVP  | **Bulk update + failure handling**                     | `--all` updates 19/20 bins; 1 fails with logged reason                                |
| 5 | STRETCH | **Rollback CLI**                                    | CLI can roll bins back to previous version                                            |

### Files you will create / edit
- `leshan/docker-compose.yaml`
- `leshan/cli/push_update.py`
- `simulator/leshan_client.py` — added to Person 1's simulator (coordinate with Person 1)
- `leshan/README.md`

### Demo evidence at evaluation
- Live: open Leshan UI, run `push-update --all --version 2.2.0`, watch versions update on screen.
- Show one failure case in logs.

---

## Person 5 — Integration, Observability & CI Lead

### Explanation
This role doesn't own a single component — it owns the **plumbing that connects all four**. It is also the safety net: if someone's change breaks another component, the CI pipeline this person sets up catches it before it reaches `main`. They also build the metrics that let the team prove the system actually works. The scope is comparable to the other four tracks.

### Technical scope

**A. CI/CD with GitHub Actions** (`.github/workflows/`)
- `lint.yaml`: ruff/black on Python, jsonlint on Node-RED flows.
- `test.yaml`: run pytest in each component subdirectory; matrix-build per component.
- `docker.yaml`: build all Docker images, run **Trivy** vulnerability scan, fail on CRITICAL/HIGH.
- `e2e.yaml`: spin up the full stack via Docker Compose (every component) and run the integration suite.
- Required-checks rules on the `main` branch.

**B. Observability** (Prometheus + Grafana)
- Each Python service (simulator, validator) exposes `/metrics` using `prometheus-client`.
- Standard metrics across all components:
  - `edge_publishes_total{component, bin_id, zone}` (Counter)
  - `edge_publish_latency_seconds` (Histogram)
  - `edge_buffer_depth{component}` (Gauge)
  - `edge_mqtt_connected{component}` (Gauge 0/1)
  - `edge_schema_violations_total{component}` (Counter)
- `ops/prometheus/prometheus.yml` — scrape config.
- `ops/grafana/dashboards/edge-overview.json` — pre-built dashboard JSON, imports with one click.

**C. End-to-end integration tests** (`ops/integration/`)
- Test 1 — **Happy path**: simulator publishes 100 messages → assert 100 land in Kafka with valid envelope.
- Test 2 — **Gateway dedup**: simulator sends 5 identical messages in 30s → assert only 1 in Kafka.
- Test 3 — **Cloud outage**: stop EMQX → simulator publishes 50 → start EMQX → assert 50 arrive.
- Test 4 — **OTA**: trigger CLI update → assert all simulator bins report new version in next telemetry.
- Test 5 — **Schema contract**: inject a malformed payload → assert validator flags it.

**D. Documentation**
- Top-level `README.md` rewrite: architecture diagram, "how to run the whole stack", per-component links.
- `RUNBOOK.md`: how to debug each common failure mode.

### Milestones
| # | Tier | Milestone                                                      | Done means                                                                |
|---|------|----------------------------------------------------------------|---------------------------------------------------------------------------|
| 1 | MVP  | **Lint + test CI green on `main`**                             | Every PR triggers; merging a syntax error fails the check                 |
| 2 | MVP  | **`/metrics` on all Python services + Prometheus scraping**    | Targets page green for all components                                     |
| 3 | MVP  | **Grafana dashboard imported & populated**                     | One dashboard shows publishes/min, latency, buffer depth, MQTT state      |
| 4 | MVP  | **Happy-path e2e test passing in CI**                          | Full stack spun up by GH Actions; 100 messages → 100 in Kafka             |
| 5 | MVP  | **Outage + dedup e2e tests passing**                           | The two key failure scenarios are covered                                 |
| 6 | MVP  | **Top-level README + RUNBOOK**                                 | New person clones → stack running in < 15 min                             |
| 7 | STRETCH | **Trivy vulnerability scan in CI**                          | CRITICAL CVEs fail the build                                              |
| 8 | STRETCH | **OTA + schema-violation e2e tests**                        | Tests 4 and 5 from the e2e suite green                                    |

### Files you will create / edit
- `.github/workflows/{lint,test,docker,e2e}.yaml`
- `ops/prometheus/prometheus.yml`
- `ops/grafana/dashboards/edge-overview.json`
- `ops/integration/test_e2e.py`
- `ops/docker-compose.full.yaml` — composes all four components together
- `README.md`, `RUNBOOK.md`

### Demo evidence at evaluation
- Open the GitHub Actions tab → show green CI history.
- Open Grafana → show live metrics during the demo.
- Run `pytest ops/integration/` → show all 5 tests green.
- Show one intentionally broken commit → show CI catching it.

---

## Cross-team integration timeline (14 days)

These are the moments where the five tracks have to work *together*. Hitting these on time is more important than any single person finishing their stretch goals. **If you fall behind on STRETCH milestones, that's fine. If you fall behind on MVP, raise it in standup the same day.**

### Week 1 — build in isolation, first wiring

| Day  | What happens                                                                                              | Owners       |
|------|-----------------------------------------------------------------------------------------------------------|--------------|
| D1   | Repo folders scaffolded; everyone has a working `docker compose up` skeleton; CI lint pipeline green     | Everyone, P5 |
| D2   | P1 schema fix merged; P2 Node-RED + Mosquitto up; P3 EMQX + Kafka up; P4 Leshan up; P5 metrics scaffolds | All          |
| D3   | P1 ↔ P2: simulator messages flow into Node-RED                                                            | P1, P2       |
| D4   | P2 ↔ P3: gateway forwards to local EMQX → Kafka with envelope                                             | P2, P3       |
| D5   | P4: bins register in Leshan; P1 buffering works                                                           | P4, P1       |
| D6   | P5: Grafana dashboard imports and shows live data; P3 schema validator running                           | P5, P3       |
| D7   | **First end-to-end happy path:** simulator → gateway → EMQX → Kafka → Grafana                            | All          |

### Week 2 — failure modes, tests, demo polish

| Day  | What happens                                                                                              | Owners       |
|------|-----------------------------------------------------------------------------------------------------------|--------------|
| D8   | P2 dedup + sanity check; P3 envelope wrapping confirmed against Person 1's schema                         | P2, P3       |
| D9   | P2 buffering on outage; P4 OTA command end-to-end                                                         | P2, P4       |
| D10  | P5 happy-path e2e test green in CI; P4 bulk OTA + failure cases                                           | P5, P4       |
| D11  | P5 outage + dedup e2e tests green in CI                                                                   | P5, all      |
| D12  | Documentation freeze: top-level README, RUNBOOK, per-component READMEs                                    | All          |
| D13  | **Demo dry-run end-to-end** — full timed walkthrough; identify any rough edges                            | All          |
| D14  | Buffer day for fixes; final demo                                                                          | All          |

### Daily rituals (lightweight)
- **15-min standup** every morning: yesterday / today / blocked
- **End-of-day push**: every person pushes a branch with WIP at minimum, even if not ready to merge
- **Integration day** is D7 — block the afternoon, everyone in the same room/call

---

## Demo plan (final evaluation)

A single 10-minute demo that shows everyone's work:

1. **Person 5** opens the GitHub Actions tab → shows recent green CI runs across the four components. (1 min)
2. **Person 5** runs `docker compose -f ops/docker-compose.full.yaml up` → entire stack starts. (1 min)
3. **Person 1** points at simulator logs → 10 bins publishing telemetry. (1 min)
4. **Person 2** opens Node-RED UI → walks through the dedup/sanity/buffer flow; shows the counter rising. (2 min)
5. **Person 3** opens Kafka UI → messages flowing with envelope; opens validator metrics. (1 min)
6. **Person 5** opens Grafana → live dashboard. (1 min)
7. **Person 4** opens Leshan UI → 10 bins registered → runs `push-update --all --version 2.2.0` → versions update on screen. (2 min)
8. **Person 5** kills EMQX in the middle of demo → buffer counter rises in Grafana → restart EMQX → buffer drains. (1 min)

Each person speaks for their slice. Each slice is independently demoable so individual contribution is visible.

---

## Open questions to resolve on Day 1

1. **F4 readiness** — when will F4's production EMQX + Kafka cluster be available? Until then we depend on Person 3's local stack.
2. **Hardware procurement** — confirm with supervisor: does the final demo need a physical Pi, or is Node-RED in Docker acceptable?
3. **Schema authority** — Person 1's JSON Schema is the source of truth; F2 (data team) needs to confirm consumption.
4. **Cert management** — F4 manages Vault; coordinate with them on cert rotation policy for EMQX device auth.
