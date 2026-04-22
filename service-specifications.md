# Group F — Smart Waste Management System
# Complete Service Specification Document
# Version 1.0

---

## System Overview

The Smart Waste Management System serves a municipal council managing waste collection across multiple city zones. The system operates in two modes simultaneously:

**Routine mode** — pre-scheduled collection jobs per zone, optimised weekly as ML models retrain on newer data.

**Emergency mode** — automatic detection of urgent bin fill-ups triggering immediate job creation and driver dispatch with minimum supervisor intervention.

The system handles categorised waste (food, paper, glass, plastic, general) with weight metadata per waste type, a heterogeneous lorry fleet with different cargo weight limits, and a full job lifecycle for both routine and emergency collections.

---

## Domain Model — Core Entities

Before reading service specifications, understand what the system manages.

### City & Zones
The city is divided into zones (Zone-1 through Zone-N). Each zone contains a set of bin locations. Routine collection schedules are defined per zone.

### Waste Categories
```
food_waste    avg weight per litre: 0.9 kg/L   colour code: #8B4513
paper         avg weight per litre: 0.1 kg/L   colour code: #4169E1
glass         avg weight per litre: 2.5 kg/L   colour code: #228B22
plastic       avg weight per litre: 0.05 kg/L  colour code: #FF6347
general       avg weight per litre: 0.3 kg/L   colour code: #808080
e_waste       avg weight per litre: 3.2 kg/L   colour code: #FFD700
```

This metadata allows the system to calculate estimated cargo weight before dispatching a lorry — ensuring the assigned vehicle can handle the load.

### Bins
Each bin has a fixed volume capacity (e.g. 240 litres), a waste category, and a GPS location. The fill level sensor reads percentage full (0-100%).

Estimated weight of contents = fill_level% × volume_litres × avg_kg_per_litre

### Lorries
Each lorry has a maximum cargo weight capacity in kg. The system never assigns a lorry to a route whose total estimated weight exceeds this limit.

### Jobs
Two types:
- **Routine job** — scheduled by zone, generated weekly by Airflow
- **Emergency job** — triggered when bin urgency score exceeds threshold

Both job types go through the same workflow orchestrator and maintain the same state machine.

---

## Database Schema — Ground Truth

All sub-groups must respect these canonical schemas. No service writes to another service's tables.

### F2 owns — PostgreSQL (waste_db, f2 schema)

```sql
-- Waste category metadata
CREATE TABLE waste_categories (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) UNIQUE NOT NULL,  -- food_waste, paper, etc
    avg_kg_per_litre DECIMAL(5,3) NOT NULL,
    colour_code     VARCHAR(7),
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- City zones
CREATE TABLE city_zones (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,  -- Zone-1, North District, etc
    boundary_geojson JSONB,                 -- polygon of zone boundary
    collection_day   VARCHAR(20),           -- Monday, Tuesday, etc
    collection_time  TIME,                  -- 08:00, 14:00, etc
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Bin registry
CREATE TABLE bins (
    id              VARCHAR(20) PRIMARY KEY,   -- BIN-047
    zone_id         INTEGER REFERENCES city_zones(id),
    waste_category_id INTEGER REFERENCES waste_categories(id),
    volume_litres   DECIMAL(8,2) NOT NULL,     -- physical capacity
    lat             DECIMAL(10,7) NOT NULL,
    lng             DECIMAL(10,7) NOT NULL,
    address         TEXT,
    installed_at    TIMESTAMPTZ,
    last_maintained TIMESTAMPTZ,
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Current bin state (upserted by Flink on every reading)
CREATE TABLE bin_current_state (
    bin_id              VARCHAR(20) PRIMARY KEY REFERENCES bins(id),
    fill_level_pct      DECIMAL(5,2) NOT NULL,   -- 0.00 to 100.00
    estimated_weight_kg DECIMAL(8,2),             -- calculated field
    status              VARCHAR(20) NOT NULL,     -- normal, monitor, urgent, critical
    urgency_score       INTEGER,                  -- 0-100
    predicted_full_at   TIMESTAMPTZ,
    fill_rate_pct_per_hour DECIMAL(6,3),
    battery_level_pct   DECIMAL(5,2),
    last_reading_at     TIMESTAMPTZ NOT NULL,
    last_collected_at   TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- Vehicle fleet
CREATE TABLE vehicles (
    id              VARCHAR(20) PRIMARY KEY,    -- LORRY-01
    registration    VARCHAR(20) UNIQUE NOT NULL,
    max_cargo_kg    DECIMAL(8,2) NOT NULL,       -- weight limit
    volume_m3       DECIMAL(6,2),
    waste_categories_supported VARCHAR[],        -- which waste types it accepts
    active          BOOLEAN DEFAULT TRUE,
    last_service_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Route plans (written by OR-Tools)
CREATE TABLE route_plans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID,                        -- links to collection_jobs
    vehicle_id      VARCHAR(20) REFERENCES vehicles(id),
    route_type      VARCHAR(20) NOT NULL,        -- routine, emergency
    zone_id         INTEGER REFERENCES city_zones(id),
    waypoints       JSONB NOT NULL,              -- ordered array of bin stops
    total_bins      INTEGER,
    estimated_weight_kg DECIMAL(8,2),
    estimated_distance_km DECIMAL(8,2),
    estimated_minutes INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    valid_for_date  DATE,
    status          VARCHAR(20) DEFAULT 'planned'  -- planned, active, completed
);

-- Zone analytics snapshots (written by Flink windowing)
CREATE TABLE zone_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    zone_id         INTEGER REFERENCES city_zones(id),
    snapshot_at     TIMESTAMPTZ NOT NULL,
    avg_fill_level  DECIMAL(5,2),
    urgent_bin_count INTEGER,
    total_bins      INTEGER,
    dominant_waste_category VARCHAR(50),
    total_estimated_kg DECIMAL(10,2),
    window_minutes  INTEGER
);

-- ML model performance tracking
CREATE TABLE model_performance (
    id              BIGSERIAL PRIMARY KEY,
    model_version   VARCHAR(50) NOT NULL,
    trained_at      TIMESTAMPTZ NOT NULL,
    training_records INTEGER,
    mae_hours       DECIMAL(6,3),   -- mean absolute error in hours
    promoted_to_prod BOOLEAN DEFAULT FALSE,
    promoted_at     TIMESTAMPTZ
);
```

### F2 owns — InfluxDB measurements

```
bin_readings_raw
  tags:    bin_id, zone_id, waste_category
  fields:  fill_level_pct, battery_level_pct, signal_strength, temperature_c
  retention: 1 year

bin_readings_processed
  tags:    bin_id, zone_id, waste_category, status
  fields:  fill_level_pct, urgency_score, estimated_weight_kg,
           fill_rate_pct_per_hour, predicted_full_hours
  retention: 90 days

vehicle_positions
  tags:    vehicle_id, driver_id, job_id, zone_id
  fields:  lat, lng, speed_kmh, heading_degrees, cargo_weight_kg
  retention: 1 year

zone_statistics
  tags:    zone_id, waste_category
  fields:  avg_fill_level, urgent_count, total_bins, total_weight_kg
  retention: 2 years (aggregated data kept longer)

waste_generation_trends
  tags:    zone_id, waste_category, day_of_week
  fields:  avg_daily_kg, avg_fill_rate, peak_hour
  retention: forever (used for long-term planning)
```

### F3 owns — PostgreSQL (waste_db, f3 schema)

```sql
-- Drivers
CREATE TABLE drivers (
    id              VARCHAR(20) PRIMARY KEY,    -- DRV-001
    name            VARCHAR(100) NOT NULL,
    phone           VARCHAR(20),
    keycloak_user_id VARCHAR(100) UNIQUE,       -- links to Keycloak
    zone_id         INTEGER REFERENCES f2.city_zones(id),
    current_vehicle_id VARCHAR(20),
    status          VARCHAR(20) DEFAULT 'off_duty',  -- available, on_job, off_duty
    shift_start     TIME,
    shift_end       TIME,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Collection jobs (both routine and emergency)
CREATE TABLE collection_jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type        VARCHAR(20) NOT NULL,        -- routine, emergency
    zone_id         INTEGER,
    state           VARCHAR(50) NOT NULL DEFAULT 'CREATED',
    priority        INTEGER DEFAULT 5,           -- 1=highest, 10=lowest

    -- For emergency jobs — the triggering bin
    trigger_bin_id  VARCHAR(20),
    trigger_urgency_score INTEGER,

    -- For routine jobs — the zone schedule
    scheduled_date  DATE,
    scheduled_time  TIME,
    schedule_id     UUID,                        -- links to routine_schedules

    -- Assignment
    assigned_vehicle_id  VARCHAR(20),
    assigned_driver_id   VARCHAR(20),
    route_plan_id        UUID,

    -- Weight tracking
    planned_weight_kg    DECIMAL(8,2),           -- from OR-Tools
    actual_weight_kg     DECIMAL(8,2),           -- from completion

    -- Timing
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    confirmed_at         TIMESTAMPTZ,
    assigned_at          TIMESTAMPTZ,
    accepted_at          TIMESTAMPTZ,
    started_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    cancelled_at         TIMESTAMPTZ,

    -- Failure tracking
    failure_reason       TEXT,
    retry_count          INTEGER DEFAULT 0,
    escalated_at         TIMESTAMPTZ,

    -- Audit
    kafka_offset         BIGINT,
    hyperledger_tx_id    VARCHAR(200)
);

-- Individual bin collections within a job
CREATE TABLE bin_collection_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES collection_jobs(id),
    bin_id          VARCHAR(20) NOT NULL,
    sequence_number INTEGER NOT NULL,            -- order in route
    planned_at      TIMESTAMPTZ,                 -- estimated arrival
    arrived_at      TIMESTAMPTZ,
    collected_at    TIMESTAMPTZ,                 -- driver tapped "Collected"
    skipped_at      TIMESTAMPTZ,                 -- driver marked as skip
    skip_reason     TEXT,                        -- bin locked, inaccessible, etc
    fill_level_at_collection DECIMAL(5,2),
    estimated_weight_kg DECIMAL(8,2),
    actual_weight_kg DECIMAL(8,2),               -- if weighed
    driver_notes    TEXT,
    photo_url       TEXT,                        -- optional photo evidence
    gps_lat         DECIMAL(10,7),
    gps_lng         DECIMAL(10,7)
);

-- State transition audit log
CREATE TABLE job_state_transitions (
    id              BIGSERIAL PRIMARY KEY,
    job_id          UUID REFERENCES collection_jobs(id),
    from_state      VARCHAR(50),
    to_state        VARCHAR(50) NOT NULL,
    reason          TEXT,
    actor           VARCHAR(100),                -- system, driver-id, supervisor-id
    metadata        JSONB,
    transitioned_at TIMESTAMPTZ DEFAULT NOW()
);

-- Step execution log
CREATE TABLE job_step_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES collection_jobs(id),
    step_name       VARCHAR(100) NOT NULL,
    attempt_number  INTEGER DEFAULT 1,
    success         BOOLEAN NOT NULL,
    request_payload JSONB,
    response_payload JSONB,
    duration_ms     INTEGER,
    executed_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Routine collection schedules
CREATE TABLE routine_schedules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    zone_id         INTEGER NOT NULL,
    waste_category_id INTEGER,                   -- null = all categories
    frequency       VARCHAR(20) NOT NULL,        -- weekly, daily, biweekly
    day_of_week     VARCHAR(20),                 -- Monday, etc
    time_of_day     TIME NOT NULL,
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Vehicle weight log per job
CREATE TABLE vehicle_weight_logs (
    id              BIGSERIAL PRIMARY KEY,
    job_id          UUID REFERENCES collection_jobs(id),
    vehicle_id      VARCHAR(20) NOT NULL,
    weight_before_kg DECIMAL(8,2),              -- tare weight at start
    weight_after_kg  DECIMAL(8,2),              -- gross weight at end
    net_cargo_kg     DECIMAL(8,2),              -- actual waste collected
    recorded_at      TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Kafka Topics — Complete Registry

F4 owns all topics. F4 creates them before any service runs.

```
Topic                           Publisher        Consumers                    Retention
─────────────────────────────────────────────────────────────────────────────────────────
waste.bin.telemetry             F1 EMQX          F2 Flink, F2 raw writer      7 days
waste.bin.processed             F2 Flink         F3 orchestrator, F3 notif    3 days
waste.bin.status.changed        F3 bin-status    F3 notification, F4 audit    3 days
waste.collection.jobs           F3 orchestrator  F2 OR-Tools, F3 scheduler    7 days
waste.routes.optimized          F2 OR-Tools      F3 orchestrator, F3 notif    1 day
waste.routine.schedule.trigger  F4 Airflow       F3 orchestrator              1 day
waste.job.completed             F3 orchestrator  F2 Spark, F4 Hyperledger     30 days
waste.driver.responses          F3 Flutter→Kong  F3 orchestrator              1 day
waste.vehicle.location          F1/F3 Flutter    F3 notif, F2 Flink, F3 sched 7 days
waste.vehicle.deviation         F2 Flink         F3 notification              1 day
waste.zone.statistics           F2 Flink         F3 dashboard, Grafana        7 days
waste.audit.events              F3 orchestrator  F4 Hyperledger               365 days
waste.model.retrained           F2 Spark         F3 orchestrator (routes)     1 day
```

---

## Service Specifications

---

### SERVICE 1 — Bin Sensor Firmware
**Owner:** F1 | **Language:** C++ (ESP32) | **Repo:** group-f-edge/firmware

#### Responsibility
Read bin fill level using an ultrasonic sensor. Manage power efficiently using deep sleep cycles. Publish readings via MQTT. Handle network failures with local buffering.

#### How it works

The ESP32 wakes from deep sleep every 5 minutes. It fires the ultrasonic sensor, calculates fill percentage based on bin depth, and publishes to EMQX. If the network is unavailable, it stores the reading in NVS (non-volatile storage) and retries on next wake. If fill level changes rapidly (more than 10% in one cycle), it wakes more frequently.

```
Measurement logic:
  distance_cm = ultrasonic.measure()
  bin_depth_cm = 120  (configured per bin type)
  fill_pct = ((bin_depth_cm - distance_cm) / bin_depth_cm) × 100
  fill_pct = clamp(fill_pct, 0, 100)

Sleep cycle:
  fill < 50%:   sleep 10 minutes  (low priority)
  fill 50-75%:  sleep 5 minutes   (monitor)
  fill 75-90%:  sleep 2 minutes   (elevated)
  fill > 90%:   sleep 30 seconds  (urgent — frequent updates)
```

#### MQTT Message Published

```json
Topic: sensors/bin/BIN-047/telemetry

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

#### Integration with other services
- Publishes to EMQX (F1 internal)
- EMQX bridges to Kafka `waste.bin.telemetry` (F2 consumes)
- Eclipse Leshan (F1) manages device registration and OTA updates

---

### SERVICE 2 — Edge Gateway
**Owner:** F1 | **Platform:** Raspberry Pi | **Runtime:** Node-RED | **Repo:** group-f-edge/gateway

#### Responsibility
Aggregate sensor data from multiple ESP32 devices in a zone. Apply edge filtering to remove duplicate or clearly erroneous readings. Buffer data during network outages. Forward valid readings to EMQX cloud broker.

#### How it works

Node-RED flow subscribes to all local MQTT topics (`sensors/bin/+/telemetry`). Each incoming message passes through:

1. **Deduplication filter** — reject reading if same bin_id sent identical value within 60 seconds
2. **Sanity check** — reject if fill_level outside 0-100 range or timestamp more than 5 minutes old
3. **Buffering** — if cloud EMQX unavailable, store in local queue (max 1000 messages)
4. **Forward** — publish to cloud EMQX on `sensors/bin/{bin_id}/telemetry`

#### Integration with other services
- Receives from local ESP32 sensors via Mosquitto (F1 local broker)
- Forwards to cloud EMQX — which is deployed by F4 in Kubernetes
- Leshan client on Pi reports device health to Leshan server

---

### SERVICE 3 — EMQX MQTT Broker
**Owner:** F4 (deployed) / F1 (configured) | **Repo:** group-f-platform/messaging

#### Responsibility
Cloud-side MQTT broker. Receives sensor data from edge gateways. Authenticates devices using certificates managed by Vault. Bridges incoming MQTT messages to Kafka topics.

#### Bridge configuration

```yaml
bridges:
  - name: kafka_bridge
    type: kafka
    bootstrap_servers: kafka.messaging.svc.cluster.local:9092
    topic_mapping:
      - mqtt_topic: "sensors/bin/+/telemetry"
        kafka_topic: "waste.bin.telemetry"
        qos: 1
      - mqtt_topic: "vehicle/+/location"
        kafka_topic: "waste.vehicle.location"
        qos: 1
```

#### Integration with other services
- Receives from F1 edge gateways (MQTT)
- Publishes to Kafka topics (F2 consumes)
- Device authentication via Vault certificates (F4)

---

### SERVICE 4 — Flink Stream Processor
**Owner:** F2 | **Language:** Python (PyFlink) | **Repo:** group-f-data/flink-processor

#### Responsibility
Real-time stream processing of all sensor and vehicle data. Classifies bin urgency. Calculates fill rates and predicted full times. Detects anomalies. Computes zone-level aggregations. Detects vehicle route deviations. Writes all results to InfluxDB and PostgreSQL.

#### Processing pipelines

**Pipeline 1 — Bin telemetry processor**

```
Source: Kafka waste.bin.telemetry
        │
        ├── Raw writer → InfluxDB bin_readings_raw
        │
        ├── Enrichment:
        │     load bin metadata from PostgreSQL
        │     load waste_category for weight calculation
        │     calculate estimated_weight_kg:
        │       fill_pct × volume_litres × avg_kg_per_litre
        │
        ├── Urgency classification:
        │     fill < 50%:    status=normal,  urgency_score=0-30
        │     fill 50-75%:   status=monitor, urgency_score=30-60
        │     fill 75-90%:   status=urgent,  urgency_score=60-85
        │     fill > 90%:    status=critical,urgency_score=85-100
        │
        ├── Fill rate calculation (keyed state per bin):
        │     fill_rate = (current_fill - previous_fill) / time_diff_hours
        │     predicted_full_at = NOW() + hours_until_full
        │     hours_until_full = (100 - fill_pct) / fill_rate
        │
        ├── Anomaly detection:
        │     fill rate > 15%/hour → RAPID_FILLING alert
        │     fill rate < -5%/hour → POSSIBLE_TAMPERING alert
        │     no reading for 30 min → SENSOR_OFFLINE alert
        │     battery < 10% → LOW_BATTERY alert
        │
        ├── Write to InfluxDB bin_readings_processed
        │
        ├── Upsert PostgreSQL bin_current_state
        │     (updates estimated_weight_kg from waste category metadata)
        │
        └── Publish to Kafka waste.bin.processed

```

**Pipeline 2 — Zone aggregation (sliding 10-min window)**

```
Source: Kafka waste.bin.processed
        │
        Key by zone_id
        │
        Sliding window: 10 minutes, slide every 2 minutes
        │
        Aggregate:
          avg_fill_level per zone
          urgent_bin_count (urgency_score > 60)
          critical_bin_count (urgency_score > 85)
          total_estimated_weight_kg (sum across all bins)
          breakdown by waste_category
        │
        Write to PostgreSQL zone_snapshots
        Publish to Kafka waste.zone.statistics
        Write to InfluxDB zone_statistics
```

**Pipeline 3 — Vehicle deviation detector**

```
Source: Kafka waste.vehicle.location
        │
        Key by vehicle_id
        │
        Load planned route from PostgreSQL route_plans
        │
        Compare GPS position to planned waypoints
        Calculate deviation distance (Haversine)
        │
        Maintain 5-minute deviation history in keyed state
        │
        If deviation > 500m for > 2 minutes:
          Publish to Kafka waste.vehicle.deviation
          {vehicle_id, job_id, deviation_m, duration_s}
```

**Pipeline 4 — Vehicle position historian**

```
Source: Kafka waste.vehicle.location
        │
        Write directly to InfluxDB vehicle_positions
        (every GPS ping stored, no processing)
```

#### Integration with other services
- Reads from Kafka (F4 Kafka, F1 data)
- Writes to InfluxDB and PostgreSQL (F2 owns these)
- Publishes enriched events to Kafka for F3 consumption

---

### SERVICE 5 — FastAPI ML Service
**Owner:** F2 | **Language:** Python | **Repo:** group-f-data/ml-service

#### Responsibility
Expose trained ML models as REST APIs. Serve fill level predictions, waste generation forecasts, and route quality scoring. Load the current production model from MLflow at startup.

#### Endpoints

```
GET  /api/v1/ml/predict/fill-time
     Query: bin_id, current_fill_level
     Returns: predicted_full_at, confidence_interval
     Used by: F3 bin-status-service, supervisor dashboard

GET  /api/v1/ml/predict/zone-generation
     Query: zone_id, date_range
     Returns: predicted_kg_per_day, by_waste_category
     Used by: F3 dashboard trend charts, OR-Tools planning

POST /api/v1/ml/score/route
     Body: route_plan with stops and weights
     Returns: efficiency_score, suggestions
     Used by: OR-Tools to compare candidate solutions

GET  /api/v1/ml/trends/waste-generation
     Query: zone_id, period (week/month/quarter)
     Returns: time-series of waste generated by category
     Used by: supervisor dashboard trend view

GET  /health
     Returns: {status, model_version, loaded_at}
```

#### Model architecture

```
Fill time prediction model:
  Input features:
    current_fill_level_pct
    fill_rate_pct_per_hour (last 3 readings)
    waste_category (one-hot encoded)
    day_of_week
    hour_of_day
    weather_temperature_c (if available)
    days_since_last_collection
  
  Output:
    hours_until_full (regression)
    confidence: low/medium/high

  Architecture: LightGBM gradient boosting
  Retrained: every Sunday midnight via Airflow
  Training data: last 90 days of bin readings
                 + collection completion records

Waste generation trend model:
  Input: zone_id, waste_category, historical daily kg
  Output: predicted kg for next 7 days
  Architecture: LSTM time-series model (PyTorch)
  Retrained: every Sunday midnight
```

#### Integration with other services
- Called by F3 bin-status-service (sync REST via Kong)
- Called by F3 dashboard (sync REST via Kong)
- Loads model from MLflow (F2 internal)
- OR-Tools calls /score/route during optimization

---

### SERVICE 6 — OR-Tools Route Optimizer
**Owner:** F2 | **Language:** Python | **Repo:** group-f-data/route-optimizer

#### Responsibility
Solve the Capacitated VRP with Time Windows for both emergency and routine collection jobs. Respect lorry weight limits. Prioritise bins by urgency and waste category. Re-optimise when new urgent bins appear mid-shift. Consider waste category compatibility per vehicle.

#### When it runs

```
Trigger 1 — Emergency re-optimisation
  Consumes: Kafka waste.bin.processed
  Condition: urgency_score >= 80 AND no active job for this bin
  Action: re-run VRP for affected zone including new urgent bin
  Time limit: 30 seconds
  Result: publishes waste.routes.optimized

Trigger 2 — Pre-shift routine planning
  Consumes: Kafka waste.routine.schedule.trigger
  Triggered by: Airflow at 23:00 night before each schedule
  Action: run VRP for all zones scheduled for tomorrow
  Time limit: 5 minutes (can be thorough — not urgent)
  Result: writes to PostgreSQL route_plans for tomorrow

Trigger 3 — Post-retraining route refresh
  Consumes: Kafka waste.model.retrained
  Action: re-run weekly route plans using updated predictions
  Time limit: 10 minutes
  Result: updates PostgreSQL routine route plans
```

#### VRP configuration

```python
data model per optimisation run:

locations:
  [depot] + [bin_location for each bin to collect]

vehicles:
  filtered to those available and capable of
  handling the waste categories in the bin set

constraints:
  1. Capacity: sum(estimated_weight_kg for bins in route)
               <= vehicle.max_cargo_kg

  2. Time windows: each bin must be visited before
                   its deadline (derived from urgency_score)
     urgency >= 90 → deadline 60 minutes
     urgency >= 80 → deadline 120 minutes
     urgency >= 70 → deadline 240 minutes
     routine bins → deadline = end of scheduled window

  3. Waste category: vehicle must support bin's waste category
                     (e.g. glass lorry only collects glass bins)

  4. Weight estimation uses waste category metadata:
     bin_weight_kg = fill_pct × volume_litres × avg_kg_per_litre
     (avg_kg_per_litre loaded from waste_categories table)

objective:
  minimise total distance driven
  with heavy penalty for missing urgent deadlines

output per vehicle:
  ordered list of bin_ids with:
    sequence_number
    estimated_arrival_time
    estimated_weight_at_stop (cumulative)
    bin fill level at time of planning
```

#### Integration with other services
- Reads bin data from PostgreSQL bin_current_state (F2)
- Reads vehicle data from PostgreSQL vehicles (F2)
- Reads waste_categories metadata for weight calculation (F2)
- Calls FastAPI /score/route to evaluate solutions (F2 internal)
- Writes to PostgreSQL route_plans (F2)
- Publishes to Kafka waste.routes.optimized (F3 consumes)

---

### SERVICE 7 — Airflow Batch Orchestrator
**Owner:** F2 | **Platform:** Apache Airflow | **Repo:** group-f-data/airflow-dags

#### Responsibility
Schedule and sequence all batch jobs. Nightly model retraining. Weekly route pre-computation. Data quality validation. Historical analytics. Routine schedule generation.

#### DAG definitions

**DAG 1 — nightly_ml_retraining (runs every Sunday 00:00)**

```
Task 1: validate_training_data
  Great Expectations checks last 7 days of
  bin_readings_raw in InfluxDB
  Fails if: < 10,000 readings, > 5% null values,
            fill levels outside 0-100 range

Task 2: extract_training_dataset
  Joins bin readings with collection completion records
  Creates labeled dataset:
    features: fill levels, fill rates, waste category,
              day/hour, weather
    labels: actual hours until collection was done
  Saves to Parquet in object storage

Task 3: train_fill_prediction_model
  Trains LightGBM on labeled dataset
  Logs experiment to MLflow
  Evaluates on held-out validation set
  Records MAE, RMSE, R² metrics

Task 4: train_generation_trend_model
  Trains LSTM on weekly waste generation data per zone
  per waste category
  Logs to MLflow separately

Task 5: promote_models_if_better
  Compares new model metrics vs current production model
  If new model MAE improved by > 5%:
    Promotes to production in MLflow
    Publishes waste.model.retrained to Kafka

Task 6: update_zone_analytics
  Runs Spark job to compute:
    Average daily waste per zone per category
    Peak fill rate times by zone
    Seasonal patterns
  Writes to InfluxDB waste_generation_trends

Task 7: generate_weekly_route_schedule
  For each zone with scheduled collection next week:
    Calls OR-Tools with predicted bin fill levels
    Writes pre-computed routes to route_plans table
  (OR-Tools also re-runs when waste.model.retrained fires)
```

**DAG 2 — routine_job_generator (runs daily at 23:00)**

```
Task 1: check_tomorrows_schedules
  Queries routine_schedules for zones scheduled tomorrow

Task 2: generate_routine_jobs
  For each scheduled zone:
    Query all active bins in zone
    Get latest fill levels from bin_current_state
    Create collection_job records (job_type = routine)
    Publish waste.routine.schedule.trigger to Kafka

Task 3: notify_operations_team
  Send summary to supervisor dashboard:
    "Tomorrow: 5 routine jobs, 127 bins, 3 zones"
```

**DAG 3 — data_quality_checks (runs every 6 hours)**

```
Great Expectations suite:
  Check bin_current_state freshness
    (all active bins should have reading < 2 hours old)
  Check for stuck fill levels
    (same exact fill_pct for > 4 hours = sensor issue)
  Check vehicle_positions continuity
    (active job vehicles should have GPS ping < 10 min)
  Alert F4 monitoring if any check fails
```

#### Integration with other services
- Triggers Spark jobs internally
- Writes to PostgreSQL and InfluxDB (F2)
- Publishes to Kafka topics (F3 orchestrator consumes)
- Logs models to MLflow (F2)

---

### SERVICE 8 — Bin Status Service
**Owner:** F3 | **Language:** Node.js + TypeScript | **Repo:** group-f-application/bin-status-service

#### Responsibility
Apply business rules to bin sensor data. Maintain the business view of bin state. Expose bin status APIs to the dashboard. Consume Kafka for real-time state updates. Respond to orchestrator calls confirming bin urgency.

#### Key business rules

```
Rule 1 — Status classification with waste weight context:
  The service considers both fill_level AND estimated_weight_kg
  A small glass bin at 80% may be more critical than a
  large food waste bin at 80% due to weight implications

Rule 2 — Collection request trigger:
  urgency_score >= 80 AND
  no active collection job for this bin AND
  bin not collected in last 2 hours
  → publish waste.bin.status.changed with action: REQUEST_COLLECTION

Rule 3 — Mark bin as collected:
  Called by orchestrator when driver taps "Collected"
  Updates bin_current_state.last_collected_at
  Updates fill_level_pct to 0 (or GPS-confirmed level)
  Publishes waste.bin.status.changed with action: COLLECTED
```

#### API endpoints

```
GET  /api/v1/bins
     Query: zone_id, status, waste_category, page, limit
     Returns: paginated list of bins with current state
     Auth: supervisor, fleet-operator, viewer

GET  /api/v1/bins/:bin_id
     Returns: full bin detail with state and history
     Auth: all authenticated roles

GET  /api/v1/bins/:bin_id/history
     Query: from, to, interval
     Returns: fill level time-series from InfluxDB
     Auth: supervisor, viewer

GET  /api/v1/bins/zone/:zone_id/summary
     Returns: zone overview with fill distribution,
              total weight, urgent count by waste category
     Auth: supervisor, fleet-operator, viewer

POST /internal/bins/:bin_id/confirm-urgency
     Called by: collection workflow orchestrator (step 1)
     Returns: { still_urgent, current_fill, estimated_weight_kg }
     Auth: internal service account only

POST /internal/bins/:bin_id/mark-collected
     Called by: orchestrator when driver confirms collection
     Body: { job_id, driver_id, collected_at, fill_level_at_time }
     Auth: internal service account only

GET  /health
     Returns: { status, last_kafka_message_at }
```

#### Kafka consumption

```
Consumes: waste.bin.processed
  On each message:
    If urgency_score changed significantly (> 10 points):
      Publish waste.bin.status.changed
    If urgency_score >= 80 and no active job:
      Publish waste.bin.status.changed with REQUEST_COLLECTION
```

#### Integration with other services
- Consumed by: F3 notification service (for supervisor alerts)
- Called by: F3 collection workflow orchestrator (steps 1 and completion)
- Reads from: F2 InfluxDB (for history queries) via FastAPI
- Reads from: F2 PostgreSQL bin_current_state (via direct DB read — same cluster)

---

### SERVICE 9 — Scheduler Service
**Owner:** F3 | **Language:** Node.js + TypeScript | **Repo:** group-f-application/scheduler-service

#### Responsibility
Assign available drivers and vehicles to collection jobs. Track vehicle cargo weight accumulation during a job. Monitor job progress as drivers collect bins. Handle driver shifts. Maintain driver availability status.

#### Driver assignment logic

```
When called by orchestrator to assign a driver:

Step 1 — Find candidate vehicles:
  SELECT v.* FROM vehicles v
  WHERE v.active = true
  AND v.id NOT IN (
    SELECT assigned_vehicle_id FROM collection_jobs
    WHERE state IN ('ASSIGNED','IN_PROGRESS')
  )
  AND $1 = ANY(v.waste_categories_supported)
  AND v.max_cargo_kg >= $planned_weight_kg
  ORDER BY v.max_cargo_kg ASC  -- smallest sufficient vehicle first

Step 2 — Find candidate drivers for that vehicle:
  SELECT d.* FROM drivers d
  WHERE d.status = 'available'
  AND d.current_vehicle_id = $vehicle_id
  AND d.zone_id = $zone_id
  AND CURRENT_TIME BETWEEN d.shift_start AND d.shift_end
  ORDER BY d.zone_id = $zone_id DESC,  -- prefer zone match
           last_job_completed_at ASC    -- prefer less recent (rest)

Step 3 — Weight validation:
  total_planned_kg = sum(bin.estimated_weight_kg for bin in route)
  if total_planned_kg > vehicle.max_cargo_kg:
    split route into sub-routes (call OR-Tools again with weight limit)

Step 4 — Assign:
  UPDATE drivers SET status = 'on_job'
  UPDATE collection_jobs SET
    assigned_vehicle_id, assigned_driver_id, assigned_at
```

#### Progress tracking

```
Consumes: Kafka waste.vehicle.location
  Compares vehicle GPS to next planned stop
  If within 50m of a bin stop:
    Update bin_collection_records.arrived_at
    Push live update via Socket.IO to dashboard

When driver marks bin collected (via Flutter API):
  Update bin_collection_records.collected_at
  Update bin_collection_records.fill_level_at_collection
  Calculate cumulative vehicle weight so far
  Check if vehicle approaching weight limit
  If vehicle > 90% capacity:
    Notify orchestrator — vehicle needs to return to depot
  Update route_plans.status for this stop

When all bins collected:
  Calculate actual vs planned route comparison
  Update vehicle weight log
  Update driver status to 'available'
  Notify orchestrator: job complete
```

#### API endpoints

```
POST /internal/scheduler/assign
     Called by: collection workflow orchestrator (step 2)
     Body: { job_id, zone_id, waste_category, planned_weight_kg,
             route_plan_id, exclude_driver_ids }
     Returns: { success, driver_id, vehicle_id, estimated_start }
     Auth: internal

POST /internal/scheduler/release
     Called by: orchestrator on job cancellation
     Body: { job_id, driver_id, vehicle_id }
     Auth: internal

POST /api/v1/collections/:job_id/bins/:bin_id/collected
     Called by: Flutter driver app (driver taps Collected)
     Body: { fill_level_at_collection, gps_lat, gps_lng,
             actual_weight_kg, notes, photo_url }
     Auth: driver role only

POST /api/v1/collections/:job_id/bins/:bin_id/skip
     Called by: Flutter driver app
     Body: { reason: 'locked' | 'inaccessible' | 'already_empty' }
     Auth: driver role

GET  /api/v1/vehicles/active
     Returns: all vehicles currently on jobs with last GPS position
     Auth: supervisor, fleet-operator

GET  /api/v1/drivers/available
     Returns: list of available drivers with their zones
     Auth: supervisor, fleet-operator

GET  /api/v1/jobs/:job_id/progress
     Returns: job with all bin stops, collected vs pending,
              vehicle cargo weight, estimated completion time
     Auth: supervisor, fleet-operator, driver (own job only)
```

#### Integration with other services
- Called by: F3 collection workflow orchestrator
- Calls: F3 notification service (on progress updates)
- Reads from: F2 PostgreSQL (vehicles, bins)
- Writes to: F3 PostgreSQL (assignments, progress)
- Consumes: Kafka waste.vehicle.location

---

### SERVICE 10 — Collection Workflow Orchestrator
**Owner:** F3 | **Language:** Node.js + TypeScript | **Repo:** group-f-application/workflow-orchestrator

#### Responsibility
Manage the complete lifecycle of both routine and emergency collection jobs. Maintain explicit state machine. Handle driver rejection with retry. Escalate to supervisor when automation cannot resolve. Expose job status API for dashboard. Record completions on blockchain via F4.

#### State machine

```
CREATED → BIN_CONFIRMING → BIN_CONFIRMED
       → ROUTE_LOADING → ROUTE_LOADED
       → ASSIGNING_DRIVER → DRIVER_ASSIGNED
       → NOTIFYING_DRIVER → DRIVER_NOTIFIED
       → AWAITING_ACCEPTANCE → DRIVER_ACCEPTED
       → IN_PROGRESS (driver collecting)
       → COMPLETING → COLLECTION_DONE
       → RECORDING_AUDIT → AUDIT_RECORDED
       → COMPLETED

Failure paths:
       → FAILED (unrecoverable)
       → ESCALATED (needs supervisor)
       → CANCELLED (supervisor manually cancelled)
       → DRIVER_REASSIGNMENT (driver rejected, finding new)
```

#### Two entry points

**Emergency job entry (Kafka):**
```
Consumes waste.bin.processed
Condition: urgency_score >= 80 AND no active job for bin
Creates job with job_type = 'emergency'
Runs full workflow
```

**Routine job entry (Kafka):**
```
Consumes waste.routine.schedule.trigger
Creates job with job_type = 'routine'
Loads pre-computed route from route_plans table
Skips BIN_CONFIRMING step (whole zone collected on schedule)
Runs workflow from ROUTE_LOADED state
```

#### Workflow execution — Emergency job

```
Step 1 — Confirm bin urgency
  Calls: bin-status-service POST /internal/bins/:id/confirm-urgency
  Timeout: 5 seconds
  If bin no longer urgent → CANCELLED
  If still urgent → BIN_CONFIRMED

Step 2 — Load optimised route
  Reads: latest route from PostgreSQL route_plans
         where job_id matches this job
         (written by OR-Tools after consuming waste.bin.processed)
  If no route yet → wait up to 60 seconds for OR-Tools
  Publish: waste.collection.jobs (triggers OR-Tools if not run)
  → ROUTE_LOADED

Step 3 — Assign driver and vehicle
  Calls: scheduler-service POST /internal/scheduler/assign
  Parameters include: planned_weight_kg (from route_plans),
                      waste_category of triggering bin,
                      zone_id
  Retry: 3 attempts, 2 minute wait between attempts
  If all fail → ESCALATED, notify supervisor
  → DRIVER_ASSIGNED

Step 4 — Notify driver
  Calls: notification-service POST /internal/notify/job-assigned
  Sends push notification to driver's Flutter app
  → DRIVER_NOTIFIED → AWAITING_ACCEPTANCE

Step 5 — Wait for acceptance (10 minute timeout)
  Driver taps Accept on Flutter app
  Flutter calls: POST /api/v1/collections/:job_id/accept
  → DRIVER_ACCEPTED → IN_PROGRESS

  If driver rejects:
    Record rejection reason
    Return to step 3 with exclude_driver_ids
    Max 3 driver rejections before ESCALATED

  If timeout:
    Release current driver
    Return to step 3
    If second timeout → ESCALATED

Step 6 — Monitor in progress
  Job remains IN_PROGRESS while driver collects
  Scheduler service tracks bin-by-bin progress
  System monitors vehicle weight accumulation
  If vehicle reaches weight limit mid-route:
    Split remaining bins into new job automatically

Step 7 — Complete
  Triggered when scheduler reports all bins collected
  Calls: bin-status-service to mark each bin collected
  Calculates: actual vs planned metrics
  → COLLECTION_DONE

Step 8 — Record audit
  Calls: Hyperledger service via Kong
  Records: job_id, all bin_ids, driver_id, vehicle_id,
           timestamps, actual weights, GPS trail
  Returns: transaction_id
  Stored in: collection_jobs.hyperledger_tx_id
  → AUDIT_RECORDED → COMPLETED

Step 9 — Publish completion
  Publishes waste.job.completed to Kafka
  F2 Spark uses this for model retraining labels
  F4 Hyperledger has secondary consumer for archival
```

#### Job Status API

```
GET  /api/v1/collection-jobs
     Query: job_type, state, zone_id, date_from, date_to, page
     Returns: paginated job list with current state
     Auth: supervisor, fleet-operator

GET  /api/v1/collection-jobs/:job_id
     Returns: full job detail including:
       current state, all bins with collection status,
       driver and vehicle assigned, route map data,
       state transition history, step execution log,
       actual vs planned comparison
     Auth: supervisor, fleet-operator, driver (own jobs)

GET  /api/v1/collection-jobs/stats
     Query: date_from, date_to, zone_id
     Returns: completion rate, avg duration, emergency vs routine ratio
     Auth: supervisor

POST /api/v1/collection-jobs/:job_id/cancel
     Supervisor manually cancels a job
     Auth: supervisor only

POST /api/v1/collection-jobs/:job_id/accept
     Driver accepts assigned job
     Auth: driver role — own job only

GET  /health
     Auth: none
```

#### Integration with other services
- Consumes: Kafka waste.bin.processed, waste.routine.schedule.trigger,
            waste.routes.optimized, waste.driver.responses
- Calls: bin-status-service, scheduler-service, notification-service
- Publishes: Kafka waste.job.completed, waste.audit.events
- Reads: F2 PostgreSQL route_plans (to load optimised routes)
- Writes: F3 PostgreSQL collection_jobs, job_state_transitions, job_step_results

---

### SERVICE 11 — Notification Service
**Owner:** F3 | **Language:** Node.js + TypeScript | **Repo:** group-f-application/notification-service

#### Responsibility
Deliver the right message to the right person at the right time via two channels — pushed by the orchestrator (sync) or consumed from Kafka (async). Manage Socket.IO connections for live dashboard updates. Stream bin fill levels and vehicle positions to connected dashboards.

#### Dual-channel design

```
Channel 1 — Called directly by orchestrator (sync HTTP):
  POST /internal/notify/job-assigned
  POST /internal/notify/job-cancelled
  POST /internal/notify/job-escalated
  POST /internal/notify/route-updated
  Immediate delivery to specific driver or supervisor

Channel 2 — Consumes from Kafka (async):
  waste.bin.processed → urgent bin alerts to supervisors
  waste.vehicle.deviation → deviation alerts to fleet-operators
  waste.zone.statistics → live dashboard updates
  waste.bin.status.changed → bin marker updates on map
  waste.job.completed → completion notifications
```

#### Smart filtering for dashboard streaming

```
For waste.bin.processed events:
  Always stream: urgency_score >= 80
  Throttle to 1/minute: status unchanged AND status = 'normal'
  Always stream: status changed (normal→monitor→urgent→critical)

For waste.vehicle.location events:
  Stream every update: vehicle on active job
  Stream every 30 seconds: vehicle not on active job
  Never stream: vehicle off duty
```

#### Socket.IO room structure

```
Rooms:
  dashboard-zone-{zone_id}  → supervisors watching specific zone
  dashboard-all             → supervisors watching full city
  driver-{driver_id}        → individual driver notifications
  fleet-ops                 → fleet operator notifications

Events emitted:
  bin:update        → { bin_id, fill_level, status, urgency_score,
                        estimated_weight_kg, waste_category,
                        predicted_full_at, zone_id }
  vehicle:position  → { vehicle_id, driver_id, job_id, lat, lng,
                        speed_kmh, cargo_weight_kg }
  job:status        → { job_id, state, bin_id, driver_id }
  alert:urgent      → { bin_id, urgency_score, zone_id, message }
  alert:deviation   → { vehicle_id, deviation_m, message }
  zone:stats        → { zone_id, avg_fill, urgent_count, weight_kg }
```

#### Mobile push notifications (Flutter)

```
When driver is not connected via Socket.IO:
  Use Firebase Cloud Messaging (FCM)
  Send to driver's device token stored in Keycloak attributes

Notification types:
  JOB_ASSIGNED  → "New collection job — BIN-047 is 92% full"
  ROUTE_UPDATED → "Your route has been updated — 2 bins added"
  JOB_CANCELLED → "Job 4471 has been cancelled"
  LOW_BATTERY   → "Your vehicle LORRY-03 requires attention"
```

#### Integration with other services
- Called by: F3 collection workflow orchestrator (sync)
- Consumes: multiple Kafka topics (async)
- Reads: Keycloak for user device tokens and roles (via F4)
- Connected to: Next.js dashboards and Flutter apps via Socket.IO

---

### SERVICE 12 — Next.js Dashboard
**Owner:** F3 | **Language:** TypeScript + React | **Repo:** group-f-application/web-dashboard

#### Responsibility
Provide municipality supervisors and fleet operators with real-time operational visibility and historical analytics. Display live bin fill levels and vehicle positions on a Mapbox map. Allow retrieval of historical data and audit trails. Show waste generation trends.

#### Dashboard views

**View 1 — Live operations map**
```
Mapbox map showing entire city:
  Bin markers coloured by status:
    Green:  normal (0-50%)
    Yellow: monitor (50-75%)
    Orange: urgent (75-90%)
    Red:    critical (90%+)
  Bin marker size proportional to estimated_weight_kg
  Active lorry markers with directional arrow
  Route polylines colour-coded per vehicle
  Zone boundary overlays (toggleable)
  Filter panel: by zone, waste category, status

Data sources:
  Initial load: GET /api/v1/bins (current state of all bins)
                GET /api/v1/vehicles/active (all active lorries)
  Live updates: Socket.IO bin:update and vehicle:position events
```

**View 2 — Job management**
```
Split view: active jobs left, completed jobs right

Active jobs panel:
  Each job card shows:
    Job type badge (ROUTINE / EMERGENCY)
    Zone and waste category
    Driver name and vehicle ID
    Progress bar: X of N bins collected
    Estimated completion time
    Current state with colour coding
    Weight: X kg of Y kg capacity
  Click job → detail drawer with full timeline

Completed jobs panel:
  Filterable by: date, zone, job_type, driver
  Each row: job_id, zone, driver, bins, actual_kg,
            duration, planned vs actual comparison
  Export to CSV button (for municipality reporting)
```

**View 3 — Bin detail**
```
Sidebar panel opens when supervisor clicks any bin on map:
  Current fill level (large gauge)
  Waste category badge
  Estimated weight of contents
  Predicted full time
  Battery level of sensor
  Fill level chart — last 7 days (from InfluxDB via F2 FastAPI)
  Collection history table (from F3 PostgreSQL)
  Last 5 collections: date, driver, weight, job_id
```

**View 4 — Analytics and trends**
```
Supervisor selects: zone, time period (week/month/quarter/year)

Charts displayed:
  1. Waste generation by category (stacked bar chart, daily)
     Source: InfluxDB waste_generation_trends via F2 FastAPI

  2. Fill rate heatmap (zones × hours of day)
     Shows when bins fill fastest → optimize schedule timing
     Source: F2 FastAPI /trends/waste-generation

  3. Collection efficiency metrics
     Planned vs actual route distance
     On-time collection rate
     Emergency vs routine job ratio
     Source: F3 PostgreSQL collection_jobs aggregation

  4. Vehicle utilisation
     Hours active vs idle per vehicle
     Average cargo weight vs capacity
     Source: F3 PostgreSQL vehicle_weight_logs

  5. Predictive view (next 7 days)
     Which zones predicted to generate most waste
     Recommended schedule adjustments
     Source: F2 FastAPI /predict/zone-generation
```

**View 5 — Historical retrieval**
```
Search panel:
  Search by: bin_id, job_id, driver, vehicle, date range, zone

Bin history:
  Full fill level time-series
  Every collection event with driver, weight, photo
  Hyperledger transaction IDs for audit verification

Job history:
  Every job with complete state machine timeline
  Step-by-step execution log
  Actual vs planned route overlay on map
  Driver acceptance/rejection history

Export: any view exportable to PDF or CSV
```

#### Integration with other services
- Calls: F3 bin-status-service, scheduler-service,
         orchestrator (via Kong REST)
- Calls: F2 FastAPI ML service (for trends and predictions)
- Real-time: Socket.IO from F3 notification service
- Auth: Keycloak OAuth2 login, JWT on all API calls via Kong

---

### SERVICE 13 — Flutter Mobile App
**Owner:** F3 | **Language:** Dart | **Repo:** group-f-application/mobile-app

#### Responsibility
Driver-facing application. Show assigned collection route on map. Allow driver to mark bins as collected or skipped. Accept or reject job assignments. Publish GPS position while on active job. Show job history and personal stats.

#### Core flows

**Job acceptance flow:**
```
Push notification arrives: "New job assigned — Zone 3"
Driver opens app → sees job detail:
  Map with route and all bin stops
  Total bins and estimated weight
  Estimated duration
  Warning if approaching weight limit

Driver taps ACCEPT:
  POST /api/v1/collection-jobs/:id/accept
  Orchestrator notified → state → IN_PROGRESS
  Navigation mode activates

Driver taps REJECT:
  POST /api/v1/collection-jobs/:id/reject
  Body: { reason: 'too_heavy' | 'out_of_zone' | 'personal' }
  Orchestrator finds replacement driver
```

**Collection flow:**
```
Driver arrives at bin stop:
  App detects proximity (< 50m) → bin highlights on map
  Fill level shown (from latest sensor reading)
  Waste category and weight estimate shown

Driver empties the bin:
  Taps COLLECTED button
  Optional: enter actual weight if vehicle has scale
  Optional: add photo evidence
  Optional: add notes

  POST /api/v1/collections/:job_id/bins/:bin_id/collected
  Body: { fill_level_at_collection, gps_lat, gps_lng,
          actual_weight_kg, notes, photo_url }

  App shows next bin on route
  Progress bar updates: 4 of 12 collected

  If vehicle cargo approaching limit (> 85%):
    App shows warning: "Nearing weight limit — return to depot after next 2 bins"
    Orchestrator already notified by scheduler service
```

**GPS publishing:**
```
When on active job:
  Publish to MQTT every 5 seconds:
  Topic: vehicle/{vehicle_id}/location
  {
    vehicle_id, driver_id, job_id,
    lat, lng, speed_kmh, heading_degrees,
    timestamp
  }

When not on active job:
  Stop publishing (battery conservation)
```

**Driver history view:**
```
Personal stats:
  Jobs completed this week/month
  Total bins collected
  Total waste weight handled
  On-time collection rate
  Route efficiency score

Job history:
  List of past jobs with date, zone, bins, duration
  Tap job → map showing route taken vs planned
```

#### Integration with other services
- Authenticates via Keycloak (login on first use, token refresh)
- All API calls via Kong (REST + WebSocket)
- Publishes GPS via MQTT to EMQX
- Receives push via Socket.IO (when active) or FCM (background)

---

### SERVICE 14 — Kong API Gateway
**Owner:** F4 | **Repo:** group-f-platform/gateway

#### Responsibility
Single entry point for all external traffic. Route requests to correct backend services. Enforce authentication on every route. Apply rate limiting. Log all requests to ELK. Handle WebSocket proxying for Socket.IO.

#### Route registry

```
Route                                   Backend service            Auth required
──────────────────────────────────────────────────────────────────────────────────
GET  /api/v1/bins*                      bin-status-service         JWT (any role)
GET  /api/v1/bins/*/history             bin-status-service         JWT (supervisor)
GET  /api/v1/zones*                     bin-status-service         JWT (any role)
GET  /api/v1/collection-jobs*           workflow-orchestrator      JWT (supervisor/operator)
POST /api/v1/collection-jobs/*/accept   workflow-orchestrator      JWT (driver)
POST /api/v1/collection-jobs/*/cancel   workflow-orchestrator      JWT (supervisor)
POST /api/v1/collections/*/bins/*/collected  scheduler-service     JWT (driver)
POST /api/v1/collections/*/bins/*/skip  scheduler-service          JWT (driver)
GET  /api/v1/vehicles*                  scheduler-service          JWT (supervisor/operator)
GET  /api/v1/drivers*                   scheduler-service          JWT (supervisor/operator)
GET  /api/v1/ml/*                       fastapi-ml-service         JWT (supervisor)
WS   /ws                                notification-service       JWT (all roles)
GET  /health/*                          all services               none
```

All `/internal/*` routes are blocked at Kong — they are only accessible within the Kubernetes cluster, not from outside.

---

### SERVICE 15 — Keycloak Identity Server
**Owner:** F4 | **Repo:** group-f-platform/auth/keycloak

#### Realm: waste-management

#### Roles and permissions

```
admin
  Full system access
  User management, system configuration

supervisor
  View all zones, bins, jobs, vehicles
  Cancel jobs (emergency override)
  View all analytics and trends
  Export reports
  Cannot manage users

fleet-operator
  Assign and reassign drivers
  View all vehicle positions
  Modify collection schedules
  Cannot view financial analytics

driver
  View own assigned job only
  Mark bins collected/skipped
  Accept/reject job assignments
  View own history

viewer
  Read-only dashboard access
  No modification capabilities
  Used for municipality officials

sensor-device
  Machine account for ESP32 devices
  Can only publish to specific MQTT topics
  No API access
```

#### Custom user attributes

```
For drivers:
  zone_id        → their primary zone
  vehicle_id     → their assigned vehicle
  employee_id    → HR system reference
  fcm_token      → Firebase push notification token
  shift_start    → shift start time
  shift_end      → shift end time

These attributes are embedded in JWT tokens
so services can use them without database lookups
```

---

### SERVICE 16 — HashiCorp Vault
**Owner:** F4 | **Repo:** group-f-platform/auth/vault

#### Secret paths

```
secret/waste-mgmt/
├── database/
│   ├── bin-status-service      username, password (PostgreSQL)
│   ├── scheduler-service       username, password
│   ├── notification-service    username, password
│   ├── workflow-orchestrator   username, password
│   ├── fastapi-ml-service      username, password
│   └── flink-processor         username, password
├── kafka/
│   bootstrap-servers, username, password, ssl-cert
├── keycloak/
│   admin-password, client-secrets per application
├── influxdb/
│   token, org, bucket names
├── external/
│   mapbox-api-key, fcm-server-key, smtp-credentials
├── hyperledger/
│   admin-cert, admin-key, peer-tlscert
└── ci-cd/
    github-token, registry-password
```

All application pods get secrets injected at startup via Vault agent sidecar. No secrets in code or Kubernetes manifests.

---

### SERVICE 17 — Prometheus + Grafana
**Owner:** F4 | **Repo:** group-f-platform/observability

#### Key metrics tracked

```
Business metrics (from application services):
  waste_bins_urgent_total          gauge by zone, category
  waste_collection_jobs_active     gauge by job_type
  waste_collection_jobs_completed  counter by zone, job_type
  waste_collection_duration_hours  histogram
  waste_lorry_cargo_utilisation    gauge by vehicle_id
  waste_route_efficiency_ratio     gauge (actual/planned distance)
  waste_bins_overflowed_total      counter by zone (fill reached 100%)

Platform metrics (from Kubernetes + Flink + Kafka):
  kafka_consumer_lag               by topic, group_id
  flink_checkpoint_duration_ms     histogram
  http_request_duration_seconds    by service, endpoint
  pod_cpu_usage, pod_memory_usage

Alerting rules:
  CRITICAL: bin_overflow (fill_level = 100 AND no active job)
  CRITICAL: kafka_consumer_lag > 10000 for > 5 minutes
  WARNING:  no bin readings from zone for > 30 minutes (sensor outage)
  WARNING:  collection job ESCALATED (no driver found)
  WARNING:  vehicle deviation > 500m for > 3 minutes
  INFO:     model retrained and promoted to production
```

#### Grafana dashboards

```
Dashboard 1 — Operations Overview
  Live bin status counts by zone (good/monitor/urgent/critical)
  Active jobs by type and state
  Vehicle fleet utilisation
  Kafka lag per topic

Dashboard 2 — Collection Performance
  Jobs completed per hour (today vs 7-day average)
  Emergency vs routine ratio
  Average job duration trend
  Driver performance heatmap

Dashboard 3 — Platform Health
  Service uptime per pod
  API response times by endpoint
  Error rates
  Flink checkpoint health
  Vault secret rotation status

Dashboard 4 — Waste Intelligence
  Bin fill rate distribution by waste category
  Zone fill level heatmap (current)
  ML model performance over time (MAE trend)
  Prediction accuracy: predicted vs actual fill time
```

---

### SERVICE 18 — Hyperledger Fabric Blockchain
**Owner:** F4 | **Repo:** group-f-platform/blockchain

#### Responsibility
Maintain an immutable audit trail of every collection event. Municipality can prove to regulators exactly what was collected, when, by whom, with GPS evidence. Smart contracts enforce collection record completeness before finalising.

#### Smart contract — CollectionRecord

```go
// chaincode/collection-record.go

type CollectionRecord struct {
    JobID            string    `json:"job_id"`
    JobType          string    `json:"job_type"`         // routine, emergency
    ZoneID           int       `json:"zone_id"`
    DriverID         string    `json:"driver_id"`
    VehicleID        string    `json:"vehicle_id"`
    BinsCollected    []BinRecord `json:"bins_collected"`
    TotalWeightKg    float64   `json:"total_weight_kg"`
    RouteDistance_km float64   `json:"route_distance_km"`
    StartedAt        string    `json:"started_at"`
    CompletedAt      string    `json:"completed_at"`
    GPSTrailHash     string    `json:"gps_trail_hash"`   // hash of full GPS trail
    CreatedAt        string    `json:"created_at"`
    TxID             string    `json:"tx_id"`
}

type BinRecord struct {
    BinID            string  `json:"bin_id"`
    WasteCategory    string  `json:"waste_category"`
    FillLevelAtTime  float64 `json:"fill_level_at_time"`
    CollectedAt      string  `json:"collected_at"`
    WeightKg         float64 `json:"weight_kg"`
    GPSLat           float64 `json:"gps_lat"`
    GPSLng           float64 `json:"gps_lng"`
}

func (c *CollectionContract) RecordCollection(
    ctx contractapi.TransactionContextInterface,
    recordJSON string) (string, error) {

    var record CollectionRecord
    json.Unmarshal([]byte(recordJSON), &record)

    // Validate completeness before writing
    if len(record.BinsCollected) == 0 {
        return "", fmt.Errorf("no bins recorded")
    }
    if record.DriverID == "" || record.VehicleID == "" {
        return "", fmt.Errorf("driver and vehicle required")
    }

    record.TxID = ctx.GetStub().GetTxID()
    record.CreatedAt = time.Now().UTC().Format(time.RFC3339)

    recordBytes, _ := json.Marshal(record)
    ctx.GetStub().PutState(record.JobID, recordBytes)

    return record.TxID, nil
}

func (c *CollectionContract) QueryRecord(
    ctx contractapi.TransactionContextInterface,
    jobID string) (*CollectionRecord, error) {

    recordBytes, _ := ctx.GetStub().GetState(jobID)
    var record CollectionRecord
    json.Unmarshal(recordBytes, &record)
    return &record, nil
}
```

#### Integration
- Called by: F3 workflow orchestrator (step 8 — audit recording)
- Queried by: F3 dashboard (audit verification, historical jobs)
- Monitored by: F4 Prometheus (transaction throughput, block time)

---

## Integration Points Summary

```
F1 → F4    ESP32 publishes MQTT → EMQX (F4 deployed)
F1 → F2    EMQX bridges to Kafka waste.bin.telemetry
F1 → F2    Flutter GPS → EMQX → Kafka waste.vehicle.location

F2 → F3    Kafka waste.bin.processed consumed by bin-status-service
F2 → F3    Kafka waste.bin.processed consumed by workflow-orchestrator
F2 → F3    Kafka waste.routes.optimized consumed by workflow-orchestrator
F2 → F3    Kafka waste.zone.statistics consumed by notification-service
F2 → F3    REST GET /api/v1/ml/* called by dashboard via Kong

F3 → F2    Kafka waste.job.completed consumed by Spark (retraining)
F3 → F2    Kafka waste.collection.jobs consumed by OR-Tools
F3 → F4    Kafka waste.audit.events consumed by Hyperledger
F3 → F4    REST calls via Kong for all external APIs

F4 → all   Vault injects secrets at pod startup
F4 → all   Keycloak validates JWT on every Kong request
F4 → all   Prometheus scrapes /metrics from every service
F4 → all   Istio injects sidecar for mTLS
F4 → all   Argo CD deploys all services from Helm charts
```

---

## Platform Contract — Rules All Sub-Groups Must Follow

```
1. DOCKERFILE
   Every service must have a Dockerfile at repo root
   Base image: node:20-alpine or python:3.11-slim
   No root user, expose /health endpoint

2. HEALTH ENDPOINT
   GET /health → { status: "ok", service: "name", version: "1.0.0" }
   HTTP 200 when healthy, HTTP 503 when not

3. LOGGING
   Structured JSON to stdout only
   Required fields: timestamp, level, service, message, traceId
   No secrets in logs ever

4. SECRETS
   Never hardcode. Never in env vars committed to git.
   Read from /vault/secrets/ files (injected by F4 Vault agent)

5. KAFKA SCHEMAS
   All Kafka messages must include: timestamp, version, source_service
   Schema registry: /group-f-docs/kafka-schemas.json (F4 maintains)

6. DATABASE ACCESS
   Never read another service's database tables directly
   Always go through that service's API or its Kafka topics

7. KONG ROUTES
   Request new routes via PR to group-f-platform/gateway/kong/routes/
   Internal service calls use /internal/* prefix (not exposed externally)

8. NEW KEYCLOAK ROLES
   Raise issue in group-f-platform labelled 'role-request'
   F4 provisions within 24 hours

9. CI/CD
   Use reusable workflow from group-f-platform/.github/workflows/
   Do not write your own pipeline from scratch

10. WEIGHT CALCULATIONS
    Always use waste_categories.avg_kg_per_litre from PostgreSQL
    Never hardcode weight estimates
    Estimated weight = fill_pct × volume_litres × avg_kg_per_litre
```

---

*Document version 1.0 — Group F Smart Waste Management System*
*Maintained by F4 Platform Team — raise issues in group-f-platform repo*
