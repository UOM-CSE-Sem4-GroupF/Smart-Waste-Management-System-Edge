# CLAUDE.md — Group F Smart Waste Management System
# Context document for Claude Code
# This file should be placed at the root of every repo in the group-f-swms organization

---

## What This Project Is

A municipal smart waste management system for a city council. The system autonomously detects when bins are filling up, computes optimal collection routes for a fleet of lorries, dispatches drivers, tracks collections in real time, and records everything on a blockchain for regulatory compliance.

**The core value proposition:** Supervisors monitor dashboards and handle exceptions. The system handles everything else automatically — from sensor reading to driver notification to blockchain audit — with zero manual input for routine operations.

**This is a university group project** built by 20 students across 4 sub-groups. Each sub-group owns a specific layer of the system. All sub-groups work in parallel, connected through Kafka and a shared platform managed by F4.

---

## GitHub Organization

```
Organization: group-f-swms
Repos:
  group-f-platform      F4 owns — all infrastructure, CI/CD, Helm charts
  group-f-edge          F1 owns — sensor firmware, edge gateway
  group-f-data          F2 owns — ML, stream processing, databases, optimization
  group-f-application   F3 owns — business logic services, web dashboard, mobile app
  group-f-docs          All own — SRS, architecture, this document, meeting notes
```

---

## Sub-Group Ownership

```
F1 — Device & Edge Systems
  What they own: ESP32 firmware, Raspberry Pi edge gateway, MQTT setup
  Language: C++ (firmware), Node-RED (flows)
  Key output: sensor readings flowing into Kafka

F2 — Data & Intelligence
  What they own: Flink stream processor, Spark batch jobs, OR-Tools route optimizer,
                 FastAPI ML service, Airflow DAGs, PostgreSQL (f2 schema), InfluxDB, MLflow
  Language: Python
  Key output: enriched bin events, optimized routes, ML predictions

F3 — Application & UI
  What they own: Collection workflow orchestrator, bin-status-service,
                 scheduler-service, notification-service,
                 Next.js dashboard, Flutter mobile app
  Language: Node.js + TypeScript (services), Dart (Flutter)
  Key output: job management, driver dispatch, real-time dashboards

F4 — Platform, Security & Integration
  What they own: Kubernetes cluster, Kong API gateway, Keycloak, Vault,
                 Istio, Prometheus, Grafana, ELK, Jaeger, Argo CD, Terraform,
                 GitHub Actions pipelines, Hyperledger Fabric, Helm charts
  Language: YAML (config), HCL (Terraform), Go (chaincode)
  Key output: the platform everything else runs on
```

---

## Architecture Overview

### Pattern
**Hybrid architecture** — choreography for async flows, orchestration for the collection job lifecycle, sync REST for user-facing APIs.

```
Choreography (Kafka):
  All high-volume async flows between F1/F2/F3 services
  Services don't know each other — they only know Kafka topics
  Example: ESP32 → EMQX → Kafka → Flink → Kafka → OR-Tools → Kafka → Orchestrator

Orchestration (direct calls):
  F3 collection workflow orchestrator explicitly calls:
    bin-status-service → scheduler-service → notification-service → Hyperledger
  Manages the collection job state machine
  Handles driver rejection and compensation logic

Sync REST (via Kong):
  All client-facing requests: dashboard, Flutter app, external integrations
  Kong validates JWT, routes to correct service, logs everything
```

### Communication protocols
```
F1 → Cloud:   MQTT over TLS (ESP32/RPi → EMQX)
F2 internal:  Kafka topics (Python producers/consumers)
F3 internal:  Kafka + direct HTTP (orchestrator to other F3 services)
F3 ↔ clients: REST + WebSocket via Kong
F4 services:  Istio mTLS between all pods
```

---

## Domain Model

### The city
The city is divided into **zones** (Zone-1, Zone-2, etc). Each zone has a collection schedule (day of week + time). Routine collection jobs are generated per zone on schedule.

### Waste categories
```
food_waste    avg_kg_per_litre: 0.90
paper         avg_kg_per_litre: 0.10
glass         avg_kg_per_litre: 2.50
plastic       avg_kg_per_litre: 0.05
general       avg_kg_per_litre: 0.30
e_waste       avg_kg_per_litre: 3.20
```
Weight of bin contents = fill_pct × volume_litres × avg_kg_per_litre
This is used everywhere — weight limits, route planning, analytics.

### Bins
- Fixed volume (e.g. 240 litres)
- One waste category per bin
- GPS location
- Ultrasonic sensor measuring fill level 0-100%
- Named: BIN-001, BIN-002, etc.

### Vehicles (lorries)
- Each has max_cargo_kg weight limit
- Each supports certain waste_categories (glass lorry ≠ food waste lorry)
- Named: LORRY-01, LORRY-02, etc.

### Drivers
- Assigned to a primary zone
- Have shift hours
- Use Flutter app on their phone for job management
- Named: DRV-001, DRV-002, etc.

### Jobs — two types
```
routine:   pre-scheduled by zone, generated nightly by Airflow
           whole zone collected on schedule regardless of fill level
           routes can change weekly as ML model retrains

emergency: triggered when bin urgency_score >= 80
           created automatically, no supervisor action needed
           OR-Tools recomputes route in real time
```

Both types go through the same state machine in the orchestrator.

---

## Kafka Topics — Complete List

F4 creates all topics. All message schemas are in group-f-docs/kafka-schemas.json.

```
waste.bin.telemetry             Raw sensor readings from ESP32 via EMQX
waste.bin.processed             Flink-enriched readings with urgency score
waste.bin.status.changed        Business state changes from bin-status-service
waste.collection.jobs           New collection jobs (triggers OR-Tools)
waste.routes.optimized          OR-Tools output: ordered stops per vehicle
waste.routine.schedule.trigger  Airflow triggers routine job creation
waste.job.completed             Orchestrator publishes on job completion
waste.driver.responses          Driver accept/reject from Flutter via Kong
waste.vehicle.location          GPS positions from Flutter app via EMQX
waste.vehicle.deviation         Flink detects lorry off planned route
waste.zone.statistics           Zone-level aggregations from Flink windowing
waste.audit.events              All events to record on Hyperledger
waste.model.retrained           Airflow publishes when new ML model promoted
```

Message format (all topics):
```json
{
  "version": "1.0",
  "source_service": "flink-processor",
  "timestamp": "2026-04-15T09:14:22Z",
  "payload": { ... }
}
```

---

## Database Ownership

### F2 owns — PostgreSQL (f2 schema)
```
waste_categories      Waste type metadata including avg_kg_per_litre
city_zones           Zone definitions with boundaries and schedules
bins                 Bin registry: location, volume, waste category, zone
bin_current_state    Latest state per bin (upserted by Flink in real time)
vehicles             Lorry fleet with max_cargo_kg and supported waste categories
route_plans          OR-Tools output: optimised routes with waypoints
zone_snapshots       Flink windowed aggregations per zone
model_performance    MLflow model version tracking
```

### F2 owns — InfluxDB
```
bin_readings_raw          Every raw sensor reading (1 year retention)
bin_readings_processed    Flink-enriched readings (90 days)
vehicle_positions         GPS ping per active vehicle (1 year)
zone_statistics           Zone-level time-series (2 years)
waste_generation_trends   Long-term waste patterns (forever)
```

### F3 owns — PostgreSQL (f3 schema)
```
drivers                  Driver registry linked to Keycloak
collection_jobs          All jobs (routine + emergency) with full state
bin_collection_records   Individual bin pickups within a job
job_state_transitions    Complete audit of every state change
job_step_results         Log of every service call made by orchestrator
routine_schedules        Zone collection schedules
vehicle_weight_logs      Actual cargo weight per job
```

### Rule: Never read another service's database tables directly
If you need data from another service, call their API or consume their Kafka topic.

---

## Service Inventory

### F1 Services
```
ESP32 firmware (C++)
  - Ultrasonic fill level reading
  - Sleep cycle management (frequency depends on fill level)
  - MQTT publish to local Mosquitto
  - NVS buffering on network failure

Edge gateway (Raspberry Pi + Node-RED)
  - Aggregates local sensor data
  - Deduplication and sanity checks
  - Forwards to cloud EMQX

EMQX broker (deployed by F4 in Kubernetes)
  - Authenticates devices via Vault certificates
  - Bridges MQTT to Kafka waste.bin.telemetry
  - Also bridges vehicle GPS to waste.vehicle.location
```

### F2 Services
```
Flink stream processor (Python/PyFlink)
  - Consumes waste.bin.telemetry
  - Classifies urgency (normal/monitor/urgent/critical)
  - Calculates fill rate and predicted full time
  - Calculates estimated_weight_kg using waste category metadata
  - Detects anomalies (rapid fill, sensor offline)
  - Zone aggregation with sliding 10-min windows
  - Detects vehicle route deviations
  - Writes to InfluxDB and PostgreSQL
  - Publishes to waste.bin.processed and waste.zone.statistics

OR-Tools route optimizer (Python)
  - Consumes waste.collection.jobs and waste.routine.schedule.trigger
  - Solves CVRPTW (Capacitated VRP with Time Windows)
  - Respects vehicle max_cargo_kg weight limits
  - Respects waste category compatibility per vehicle
  - Time windows derived from urgency scores
  - 30-second time limit for emergency, 5-min for routine
  - Writes to PostgreSQL route_plans
  - Publishes to waste.routes.optimized

FastAPI ML service (Python)
  - GET /api/v1/ml/predict/fill-time
  - GET /api/v1/ml/predict/zone-generation
  - GET /api/v1/ml/trends/waste-generation
  - POST /api/v1/ml/score/route
  - Loads model from MLflow at startup
  - Called by F3 dashboard via Kong

Airflow DAGs (Python)
  - nightly_ml_retraining: runs every Sunday 00:00
    validate → extract training data → train model →
    promote if better → publish waste.model.retrained
  - routine_job_generator: runs daily 23:00
    generate tomorrow's routine jobs per zone schedule
  - data_quality_checks: runs every 6 hours
    Great Expectations validation suite

MLflow server
  - Tracks all ML experiments
  - Model registry: dev → staging → production
  - FastAPI reads production model at startup

PostgreSQL + InfluxDB
  - StatefulSets in Kubernetes
  - F2 writes, F3 reads via API only
```

### F3 Services
```
Collection workflow orchestrator (Node.js + TypeScript)
  ENTRY POINT 1 — Emergency (Kafka):
    Consumes waste.bin.processed where urgency_score >= 80
    Creates emergency job, runs full state machine

  ENTRY POINT 2 — Routine (Kafka):
    Consumes waste.routine.schedule.trigger
    Creates routine job, loads pre-computed route

  STATE MACHINE:
    CREATED → BIN_CONFIRMING → BIN_CONFIRMED
    → ROUTE_LOADING → ROUTE_LOADED
    → ASSIGNING_DRIVER → DRIVER_ASSIGNED
    → NOTIFYING_DRIVER → DRIVER_NOTIFIED
    → AWAITING_ACCEPTANCE → DRIVER_ACCEPTED
    → IN_PROGRESS → COMPLETING → COLLECTION_DONE
    → RECORDING_AUDIT → AUDIT_RECORDED → COMPLETED
    Failure paths: FAILED, ESCALATED, CANCELLED, DRIVER_REASSIGNMENT

  CALLS (as orchestrator):
    Step 1: bin-status-service POST /internal/bins/:id/confirm-urgency
    Step 2: scheduler-service  POST /internal/scheduler/assign
    Step 3: notification-service POST /internal/notify/job-assigned
    Step 4 (completion): bin-status-service POST /internal/bins/:id/mark-collected
    Step 5 (audit): Hyperledger via Kong

  API:
    GET  /api/v1/collection-jobs (paginated, filterable)
    GET  /api/v1/collection-jobs/:id (full detail + history)
    POST /api/v1/collection-jobs/:id/accept (driver)
    POST /api/v1/collection-jobs/:id/cancel (supervisor)

Bin status service (Node.js + TypeScript)
  - Consumes waste.bin.processed from Kafka
  - Applies business rules for collection triggers
  - Calculates estimated_weight_kg using waste_categories metadata
  - Called by orchestrator to confirm urgency
  - Called by orchestrator to mark bin as collected
  - Exposes bin status APIs for dashboard
  - API: GET /api/v1/bins, GET /api/v1/bins/:id,
         GET /api/v1/bins/:id/history, GET /api/v1/zones/:id/summary

Scheduler service (Node.js + TypeScript)
  - Called by orchestrator to assign driver + vehicle
  - Validates vehicle weight capacity vs planned route weight
  - Tracks bin-by-bin collection progress
  - Monitors vehicle cargo accumulation
  - Handles driver location and availability
  - API: POST /internal/scheduler/assign, POST /internal/scheduler/release
         POST /api/v1/collections/:job_id/bins/:bin_id/collected
         POST /api/v1/collections/:job_id/bins/:bin_id/skip
         GET  /api/v1/vehicles/active, GET /api/v1/drivers/available
         GET  /api/v1/jobs/:job_id/progress

Notification service (Node.js + TypeScript)
  DUAL-CHANNEL SERVICE — receives from both orchestrator AND Kafka:

  Channel 1 — Orchestrator calls (sync HTTP):
    POST /internal/notify/job-assigned
    POST /internal/notify/job-cancelled
    POST /internal/notify/route-updated
    POST /internal/notify/job-escalated

  Channel 2 — Kafka consumption (async):
    waste.bin.processed → urgent alerts to supervisors
    waste.vehicle.deviation → deviation alerts to fleet-operators
    waste.zone.statistics → live zone stats to dashboards
    waste.bin.status.changed → bin marker updates on map
    waste.job.completed → completion notifications

  Socket.IO rooms:
    dashboard-all, dashboard-zone-{id}, driver-{id}, fleet-ops

  Socket.IO events emitted:
    bin:update, vehicle:position, job:status, alert:urgent,
    alert:deviation, zone:stats

Next.js dashboard (TypeScript + React)
  5 main views:
  1. Live operations map: Mapbox with bins (colour by status),
     vehicles, routes, real-time Socket.IO updates
  2. Job management: active jobs, completed jobs, job details
  3. Bin detail panel: fill gauge, history chart, collection log
  4. Analytics: waste generation charts, fill heatmaps,
     collection efficiency, vehicle utilisation
  5. Historical retrieval: search by bin/job/driver/vehicle/date

  Initial load pattern:
    REST call for current state of all bins + active vehicles
    Then Socket.IO keeps it live (delta updates only)

Flutter mobile app (Dart)
  Driver-facing:
  - Login via Keycloak OAuth2
  - View assigned route on Mapbox
  - Accept or reject job (with reason)
  - Mark bins as collected (with GPS, optional photo, optional weight)
  - Mark bins as skipped (with reason)
  - View cargo weight accumulation + limit warning
  - Publish GPS every 5 seconds when on active job
  - Receive push notifications (Socket.IO active / FCM background)
  - View personal job history and stats
```

### F4 Services
```
Kong API gateway
  Single entry point for all external traffic
  Routes: /api/v1/bins/* → bin-status-service
          /api/v1/collection-jobs/* → workflow-orchestrator
          /api/v1/collections/* → scheduler-service
          /api/v1/vehicles/* → scheduler-service
          /api/v1/ml/* → fastapi-ml-service
          /ws → notification-service (WebSocket)
  Plugins: JWT auth, rate limiting, request logging
  /internal/* routes blocked — cluster-only access

Keycloak
  Realm: waste-management
  Roles: admin, supervisor, fleet-operator, driver, viewer, sensor-device
  Custom driver attributes: zone_id, vehicle_id, fcm_token, shift times
  All embedded in JWT so services don't need DB lookups

HashiCorp Vault
  Injects secrets into all pods via sidecar agent
  Dynamic DB credentials (rotate hourly)
  Paths: secret/waste-mgmt/database/*, secret/waste-mgmt/kafka,
         secret/waste-mgmt/keycloak, secret/waste-mgmt/external/*

Istio service mesh
  mTLS between all pods automatically
  AuthorizationPolicy: only orchestrator can call scheduler /internal/*
  VirtualService: canary deployments for new service versions

Prometheus + Grafana
  Key metrics: waste_bins_urgent_total, waste_collection_jobs_active,
               waste_lorry_cargo_utilisation, kafka_consumer_lag
  Alerts: bin overflow (100% fill no job), consumer lag > 10000,
          zone sensor outage, job escalation

ELK Stack + Jaeger
  All pods log structured JSON → Logstash → Elasticsearch → Kibana
  Jaeger traces every request across service boundaries

Argo CD
  Watches group-f-platform repo
  Helm charts for every service
  Auto-deploys on merge to main
  Rollback on failed health checks

Terraform
  Provisions the Kubernetes cluster
  Networking, storage, DNS

GitHub Actions (reusable workflow)
  All service repos call: group-f-platform/.github/workflows/service-build.yml
  Steps: checkout → build image → Trivy scan → push GHCR → update Helm values
  Argo CD then auto-deploys the updated chart

Hyperledger Fabric
  Records every collection job completion immutably
  Smart contract: CollectionRecord with all bins, weights, GPS hash, timestamps
  Queried by dashboard for audit verification

Chaos Mesh + k6
  Chaos: pod kills, network delays, CPU stress injection
  k6: load test scripts for all critical API paths
```

---

## Kubernetes Namespaces

```
gateway         Kong ingress controller
auth            Keycloak (2 replicas), Vault (3-node HA), OPA
messaging       Kafka (3 brokers), Zookeeper (3 nodes), EMQX
monitoring      Prometheus, Grafana, ELK stack, Jaeger
cicd            Argo CD, Chaos Mesh, k6
blockchain      Hyperledger peer, orderer, CA nodes
waste-dev       All F2 + F3 services (development environment)
waste-prod      All F2 + F3 services (production environment)
```

---

## Tech Stack Reference

```
Layer           Technology              Owner   Purpose
─────────────────────────────────────────────────────────────────
Hardware        ESP32                   F1      Bin fill sensors
Hardware        Raspberry Pi            F1      Edge gateway
Protocol        MQTT / Mosquitto        F1      Local sensor comms
Broker          EMQX                    F4/F1   Cloud MQTT + Kafka bridge
Stream proc     Apache Flink (PyFlink)  F2      Real-time event processing
Batch proc      Apache Spark            F2      Historical analytics
Workflow        Apache Airflow          F2      Batch job scheduling
ML              PyTorch / LightGBM      F2      Prediction models
ML ops          MLflow                  F2      Model registry
Optimization    OR-Tools (Google)       F2      Vehicle routing (VRP)
API             FastAPI                 F2      ML model serving
DB relational   PostgreSQL              F2/F3   Structured data
DB time-series  InfluxDB                F2      Sensor time-series
Data quality    Great Expectations      F2      Pipeline validation
Business logic  Node.js + TypeScript    F3      Backend services
ORM             Prisma                  F3      PostgreSQL access layer
Frontend        Next.js 14              F3      Web dashboard
Mobile          Flutter / Dart          F3      Driver mobile app
State mgmt      Zustand                 F3      React client state
Maps            Mapbox GL JS            F3      GIS + routing maps
Real-time       Socket.IO               F3      WebSocket push
Push notif      Firebase FCM            F3      Mobile background push
Containers      Docker                  F4      Containerization
Orchestration   Kubernetes              F4      Container management
Package mgmt    Helm                    F4      K8s deployment templates
GitOps          Argo CD                 F4      Continuous deployment
IaC             Terraform               F4      Infrastructure provisioning
CI/CD           GitHub Actions          F4      Build + scan pipelines
API gateway     Kong                    F4      Traffic management
Service mesh    Istio / Envoy           F4      mTLS + traffic control
Auth            Keycloak                F4      OAuth2 / OIDC / RBAC
Secrets         HashiCorp Vault         F4      Secret management
Metrics         Prometheus              F4      Metrics collection
Dashboards      Grafana                 F4      Operational dashboards
Logs            ELK Stack               F4      Centralised logging
Tracing         Jaeger                  F4      Distributed tracing
Policy          Open Policy Agent       F4      Fine-grained access control
Security        Trivy + OWASP ZAP       F4      Vulnerability scanning
Chaos           Chaos Mesh              F4      Failure injection testing
Load testing    k6                      F4      Performance testing
Blockchain      Hyperledger Fabric      F4      Immutable audit trail
Event bus       Apache Kafka            F4      Universal message bus
Device mgmt     Eclipse Leshan          F1      IoT device lifecycle
Network debug   Wireshark               F1      Protocol analysis
```

---

## Coding Standards

### All services
```
Every service must have:
  Dockerfile at repo root (no root user)
  GET /health endpoint → { status, service, version }
  Structured JSON logging to stdout
  Fields: timestamp (ISO8601), level, service, message, traceId

Never:
  Hardcode secrets or credentials
  Read another service's database directly
  Call /internal/* endpoints of other services directly
      (except orchestrator — it is the designated caller)
```

### TypeScript services (F3)
```
Node.js 20+ with strict TypeScript
Prisma for PostgreSQL queries
Zod for request validation
Shared types package for Kafka message schemas
Jest for unit tests
Supertest for API tests
```

### Python services (F2)
```
Python 3.11+
Type hints everywhere
Pydantic for data models and validation
pytest for tests
Black + isort for formatting
All Kafka messages validated against schema on consume
```

### Dart / Flutter (F3 mobile)
```
Flutter 3.x
Riverpod for state management
dio for HTTP
flutter_map for Mapbox
Integration tests with Flutter Driver
```

---

## API Conventions

```
Base URL (via Kong): https://api.waste-mgmt.lk

All requests require:
  Authorization: Bearer {keycloak_jwt_token}
  Content-Type: application/json
  X-Trace-Id: {uuid}   (or Kong generates one)

Pagination:
  GET /api/v1/bins?page=1&limit=50&zone_id=2
  Response: { data: [...], total: 247, page: 1, limit: 50 }

Error format:
  { error: "RESOURCE_NOT_FOUND", message: "Bin BIN-999 not found",
    timestamp: "...", traceId: "..." }

Internal calls (within cluster, not via Kong):
  http://service-name.namespace.svc.cluster.local:PORT/internal/...
  No JWT required — Istio mTLS + AuthorizationPolicy controls access
```

---

## Weight Calculation — Critical Business Rule

This is used in multiple services. Always calculate this way:

```
bin_weight_kg = (fill_level_pct / 100) × volume_litres × avg_kg_per_litre

Example:
  BIN-047: glass bin, 240 litres, 85% full
  avg_kg_per_litre for glass = 2.50
  weight = 0.85 × 240 × 2.50 = 510 kg

Always load avg_kg_per_litre from waste_categories table.
Never hardcode weight estimates.

OR-Tools uses this to:
  Sum total planned weight per route
  Compare to vehicle max_cargo_kg
  Reject route if sum > vehicle limit
  Split into multiple routes if needed
```

---

## Job State Machine — Reference

```
Initial states by job type:
  emergency: created from Kafka event → starts at CREATED
  routine:   created by Airflow → starts at CREATED (skips BIN_CONFIRMING)

Full state machine:
  CREATED
  BIN_CONFIRMING     → calling bin-status-service
  BIN_CONFIRMED      → bin still urgent, proceed
  ROUTE_LOADING      → loading OR-Tools route from PostgreSQL
  ROUTE_LOADED       → route ready
  ASSIGNING_DRIVER   → calling scheduler-service (up to 3 retries)
  DRIVER_ASSIGNED    → driver and vehicle confirmed
  NOTIFYING_DRIVER   → calling notification-service
  DRIVER_NOTIFIED    → push sent to driver app
  AWAITING_ACCEPTANCE → waiting for driver to tap Accept (10min timeout)
  DRIVER_ACCEPTED    → driver confirmed
  IN_PROGRESS        → driver actively collecting
  COMPLETING         → all bins done, wrapping up
  COLLECTION_DONE    → physical collection finished
  RECORDING_AUDIT    → writing to Hyperledger
  AUDIT_RECORDED     → blockchain confirmed
  COMPLETED          → terminal success state

Failure states:
  FAILED             → unrecoverable system error
  ESCALATED          → needs supervisor (no driver after retries)
  CANCELLED          → supervisor manually cancelled
  DRIVER_REASSIGNMENT → driver rejected, finding replacement
```

---

## Urgency Score System

Flink calculates urgency_score (0-100) based on fill level and fill rate.

```
Base score from fill level:
  0-50%:   score 0-30   status: normal
  50-75%:  score 30-60  status: monitor
  75-90%:  score 60-85  status: urgent
  90-100%: score 85-100 status: critical

Modifiers:
  +10 if fill_rate > 5%/hour (filling fast)
  +15 if fill_rate > 10%/hour (filling very fast)
  +5  if waste_category = food_waste (hygiene priority)
  -5  if recently scheduled for routine collection

Thresholds:
  >= 80: create emergency collection job
  >= 90: maximum priority, shortest time window for OR-Tools

Time windows (for OR-Tools VRP):
  urgency >= 90: collect within 60 minutes
  urgency >= 80: collect within 120 minutes
  urgency >= 70: collect within 240 minutes
  routine bins:  collect within scheduled window
```

---

## ML Model Details

### Fill time prediction model
```
Algorithm: LightGBM gradient boosting
Input features:
  current_fill_level_pct
  fill_rate_pct_per_hour (last 3 readings moving average)
  waste_category (one-hot)
  day_of_week (0-6)
  hour_of_day (0-23)
  days_since_last_collection
  zone_id (for local patterns)

Output: hours_until_full (regression)
Retrained: every Sunday midnight via Airflow
Training data: last 90 days InfluxDB readings
               joined with collection_jobs completion times
Label: actual time from reading to collection_done_at
```

### Waste generation trend model
```
Algorithm: LSTM time-series (PyTorch)
Input: historical daily waste kg per zone per category (last 52 weeks)
Output: predicted kg for next 7 days
Retrained: every Sunday midnight
Used for: pre-computing routine route plans for coming week
          and dashboard 7-day forecast charts
```

### Model promotion criteria
```
New model promoted to production if:
  MAE improves by > 5% vs current production model
  Evaluated on 2-week held-out validation set
  No data quality failures in training set

On promotion:
  MLflow updates production alias
  waste.model.retrained published to Kafka
  OR-Tools re-runs weekly route plans with new predictions
  FastAPI reloads model on next startup
```

---

## Deployment Pipeline

```
Developer pushes code to feature branch in any repo
        │
        ▼
GitHub Actions triggers (group-f-platform reusable workflow)
  1. Checkout code
  2. Run tests (pytest / jest)
  3. Build Docker image
  4. Trivy scan — CRITICAL/HIGH CVEs fail the build
  5. Push image to GHCR with commit SHA tag
  6. Update image.tag in Helm values file in group-f-platform
  7. Commit and push the values change
        │
        ▼
Argo CD detects group-f-platform changed
  Compares desired state (Git) vs actual state (cluster)
        │
        ▼
Kubernetes rolling deployment
  New pods start with new image
  Liveness probe must pass before old pods terminate
  If health checks fail → automatic rollback to previous version
        │
        ▼
Service running in waste-dev or waste-prod namespace
```

---

## Local Development Setup

```bash
# Prerequisites: Docker, minikube, kubectl, helm

# Clone platform repo
git clone https://github.com/group-f-swms/group-f-platform
cd group-f-platform

# Start local cluster and install all platform services
./scripts/setup-local.sh

# This installs in order:
# 1. Creates all namespaces
# 2. Installs Kafka (1 broker for dev)
# 3. Installs Kong
# 4. Installs Keycloak (with waste-management realm pre-configured)
# 5. Installs Vault (dev mode)
# 6. Creates all Kafka topics
# 7. Installs Prometheus + Grafana
# 8. Seeds Keycloak with test users

# Verify everything is running
kubectl get pods -A

# Get service URLs
minikube service kong-kong-proxy -n gateway --url  → API gateway
minikube service keycloak -n auth --url            → Auth server
minikube service grafana -n monitoring --url       → Metrics dashboards
```

### Test users (seeded by setup-local.sh)
```
supervisor-user    password: Test1234!    role: supervisor
operator-user      password: Test1234!    role: fleet-operator
driver-user        password: Test1234!    role: driver
admin-user         password: Test1234!    role: admin
```

### Environment variables (injected by Vault, available locally via .env.local)
```
DB_HOST=postgresql.waste-prod.svc.cluster.local
DB_PORT=5432
DB_NAME=waste_db
DB_USER=(injected by Vault)
DB_PASSWORD=(injected by Vault)
KAFKA_BROKERS=kafka.messaging.svc.cluster.local:9092
KEYCLOAK_URL=http://keycloak.auth.svc.cluster.local:8080
KEYCLOAK_REALM=waste-management
KEYCLOAK_CLIENT_ID=(per service)
INFLUXDB_URL=http://influxdb.messaging.svc.cluster.local:8086
INFLUXDB_TOKEN=(injected by Vault)
INFLUXDB_ORG=waste-mgmt
VAULT_ADDR=http://vault.auth.svc.cluster.local:8200
```

---

## How to Request Platform Resources

```
New Kong route:
  Add file to: group-f-platform/gateway/kong/routes/
  Raise PR, F4 reviews within 24 hours

New Keycloak role or client:
  Raise issue in group-f-platform labelled 'role-request'
  F4 provisions within 24 hours

New Vault secret:
  Raise issue in group-f-platform labelled 'secret-request'
  Never commit secrets anywhere — F4 provisions in Vault only

New Kafka topic:
  Raise issue in group-f-platform labelled 'topic-request'
  Include: topic name, publisher service, consumer services, retention

Helm chart for your service:
  F4 creates it — you provide: image name, port, env vars needed,
  health endpoint path, resource requirements
  Raise issue in group-f-platform labelled 'helm-chart-request'
```

---

## Project Report Structure

The group report follows this structure (20 students, each writes 2-page individual contribution annex):

```
Section 1:  Complete SRS (functional, non-functional, domain requirements)
Section 2:  Design specification (architecture — use C4 diagrams)
Section 3:  Development details, algorithms, data models
Section 4:  Testing reports (unit, system, UAT)
Section 5:  Deployment plan + security + performance + scalability
Section 6:  Agile Scrum evidence (sprint boards, burndown charts)
Section 7:  Work estimation and delivery plans
Section 8:  User training and operational support
Section 9:  Software evolution plan (implement a Change Request)
Section 10: Annex — 20 individual work reports (2 pages each)
```

---

## Key Architectural Decisions

```
ADR-001: Hybrid choreography + orchestration
  Kafka choreography for all async event flows (F1→F2→F3 pipeline)
  Collection workflow orchestrator for job lifecycle management
  Reason: high-volume async doesn't need coordination; job lifecycle needs
          visibility, compensation, and sequential guarantees

ADR-002: Weight-aware routing
  OR-Tools VRP respects vehicle max_cargo_kg
  Weight estimated from waste category metadata (avg_kg_per_litre)
  Reason: glass bins weigh 50x plastic bins at same fill level —
          ignoring weight would cause overloaded lorries

ADR-003: Dual-mode jobs (routine + emergency)
  Both types go through same orchestrator state machine
  Routine skips BIN_CONFIRMING step
  Reason: code reuse, consistent monitoring, unified audit trail

ADR-004: ML-driven route adaptation
  Routes pre-computed weekly as model retrains
  OR-Tools re-runs automatically when waste.model.retrained fires
  Reason: routes should get smarter as system learns waste patterns

ADR-005: Notification service dual-channel
  Called by orchestrator for job-specific notifications (sync)
  Consumes Kafka for system-wide alerts (async)
  Reason: job assignments need guaranteed delivery in workflow sequence;
          bin alerts should not depend on orchestrator being in the call chain

ADR-006: Five-repo structure
  One repo per team (not one per service)
  Reason: team ownership boundaries, manageable permissions,
          CI/CD complexity proportional to team size
```

---

## Common Development Patterns

### Consuming from Kafka (Python)
```python
from kafka import KafkaConsumer
import json

consumer = KafkaConsumer(
    'waste.bin.processed',
    bootstrap_servers=os.environ['KAFKA_BROKERS'],
    group_id='your-service-name',
    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
    auto_offset_reset='earliest',
    enable_auto_commit=False  # manual commit for reliability
)

for message in consumer:
    try:
        payload = message.value['payload']
        # process...
        consumer.commit()
    except Exception as e:
        logger.error({"message": "Failed to process", "error": str(e),
                      "offset": message.offset})
        # dead letter queue or alert
```

### Publishing to Kafka (Python)
```python
from kafka import KafkaProducer
import json
from datetime import datetime, timezone

producer = KafkaProducer(
    bootstrap_servers=os.environ['KAFKA_BROKERS'],
    value_serializer=lambda v: json.dumps(v).encode('utf-8')
)

producer.send('waste.bin.processed', {
    "version": "1.0",
    "source_service": "flink-processor",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "payload": {
        "bin_id": "BIN-047",
        "fill_level_pct": 85.3,
        "urgency_score": 82,
        "status": "urgent",
        "estimated_weight_kg": 510.0,
        "predicted_full_at": "2026-04-15T11:30:00Z"
    }
})
```

### Consuming from Kafka (Node.js)
```typescript
import { Kafka } from 'kafkajs'

const kafka = new Kafka({
  clientId: 'bin-status-service',
  brokers: process.env.KAFKA_BROKERS.split(',')
})

const consumer = kafka.consumer({ groupId: 'bin-status-service' })

await consumer.subscribe({ topic: 'waste.bin.processed' })

await consumer.run({
  eachMessage: async ({ message }) => {
    const event = JSON.parse(message.value.toString())
    const payload = event.payload
    // process payload...
  }
})
```

### Calling internal services (Node.js orchestrator)
```typescript
// orchestrator calling scheduler service
const response = await fetch(
  'http://scheduler-service.waste-prod.svc.cluster.local:3000/internal/scheduler/assign',
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Trace-Id': traceId,
      'X-Service-Name': 'workflow-orchestrator'
    },
    body: JSON.stringify({
      job_id: jobId,
      zone_id: job.zone_id,
      waste_category: job.waste_category,
      planned_weight_kg: routePlan.estimated_weight_kg,
      exclude_driver_ids: previouslyRejectedDrivers
    })
  }
)
```

### Reading from Vault secrets (Node.js)
```typescript
import fs from 'fs'

// Vault agent writes secrets as files at /vault/secrets/
// Read them as environment-like config at startup

const dbConfig = fs.readFileSync('/vault/secrets/database', 'utf8')
  .split('\n')
  .reduce((acc, line) => {
    const [key, value] = line.split('=')
    if (key && value) acc[key.trim()] = value.trim()
    return acc
  }, {} as Record<string, string>)

const pool = new Pool({
  host:     dbConfig.DB_HOST,
  port:     parseInt(dbConfig.DB_PORT),
  database: dbConfig.DB_NAME,
  user:     dbConfig.DB_USER,
  password: dbConfig.DB_PASSWORD
})
```

---

## Testing Requirements

```
Unit tests (all services):
  Cover all business logic functions
  Mock all external dependencies (Kafka, DB, other services)
  Minimum 80% code coverage
  Run in GitHub Actions on every PR

Integration tests (F2, F3):
  Test Kafka consumer → processing → DB write flow
  Use testcontainers for PostgreSQL and Kafka
  Run in GitHub Actions on PR to main

System tests (F3 orchestrator):
  Test complete job lifecycle using mocked services
  Test all state machine transitions
  Test compensation logic (driver rejection flow)
  Test routine vs emergency entry points

Load tests (F4 with k6):
  Simulate 1000 concurrent bin events
  Test API gateway throughput under load
  Test WebSocket connection scaling
  Target: 500 req/s sustained, p99 latency < 500ms

Chaos tests (F4 with Chaos Mesh):
  Kill Flink task manager → verify recovery from checkpoint
  Kill notification service → verify Kafka events queue
  Network partition between orchestrator and scheduler
  Kill Kafka broker → verify 2 remaining brokers handle load
```

---

## Monitoring Alerts Reference

```
CRITICAL (immediate action):
  bin fill = 100% AND no active job → bin overflowing
  kafka consumer lag > 10000 → F2 processing falling behind
  service pod crash loop → check ELK for errors
  Vault seal → all secrets unavailable

WARNING (investigate soon):
  no sensor reading from zone for 30+ minutes → sensor outage
  collection job ESCALATED → no driver found, needs supervisor
  vehicle deviation > 500m → driver off route
  ML model accuracy degrading → check training data quality

INFO (awareness):
  new model version promoted → routes may change
  routine job generated → tomorrow's schedule ready
  Hyperledger block committed → audit trail updated
```

---

## Links

```
GitHub org:      https://github.com/group-f-swms
Platform repo:   https://github.com/group-f-swms/group-f-platform
Docs repo:       https://github.com/group-f-swms/group-f-docs
Service specs:   group-f-docs/service-specifications.md
Kafka schemas:   group-f-docs/kafka-schemas.json
Platform contract: group-f-docs/platform-contract.md
Architecture diagrams: group-f-docs/diagrams/

Cluster (dev):   minikube (local)
Kong gateway:    https://api.waste-mgmt.lk (prod) / minikube service url (dev)
Keycloak:        https://auth.waste-mgmt.lk (prod) / minikube service url (dev)
Grafana:         https://grafana.waste-mgmt.lk (prod) / minikube service url (dev)
Kibana:          https://kibana.waste-mgmt.lk (prod) / minikube service url (dev)
```

---

*This document is the authoritative context for Claude Code sessions.*
*When working on any service, read this file first.*
*For service-specific API details see group-f-docs/service-specifications.md*
*Last updated by F4 platform team*
