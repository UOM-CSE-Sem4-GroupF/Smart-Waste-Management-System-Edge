# Edge Demo Runbook

## Start and Stop

Start:

```bash
docker compose -f ops/docker-compose.full.yaml up --build
```

Stop and clear volumes:

```bash
docker compose -f ops/docker-compose.full.yaml down -v
```

## Health Checks

```bash
curl http://localhost:1880/health
curl http://localhost:9100/health
curl http://localhost:9101/health
curl http://localhost:9102/health
curl http://localhost:8090/health
```

## Kafka Topic Empty

1. Check Node-RED is up: http://localhost:1880.
2. Check EMQX is up: http://localhost:18083.
3. Check bridge metrics:

```bash
curl http://localhost:9100/metrics | grep edge_bridge
```

4. Check Kafka UI: http://localhost:8080.

Expected topic: `waste.bin.telemetry`.

## Gateway Not Forwarding

1. Confirm local Mosquitto is reachable on host port `1884`.
2. Confirm Node-RED flow is loaded.
3. Publish a manual test message:

```bash
mosquitto_pub -h localhost -p 1884 \
  -t sensors/bin/BIN-001/telemetry \
  -m '{"bin_id":"BIN-001","fill_level_pct":42,"battery_level_pct":90,"signal_strength_dbm":-60,"temperature_c":28,"timestamp":"2026-05-04T10:00:00Z","firmware_version":"2.1.4","error_flags":0}'
```

If the timestamp is older than five minutes, the gateway will intentionally drop it.

## Simulator Spooling

Check buffer depth:

```bash
curl http://localhost:9102/metrics | grep edge_buffer_depth
```

If EMQX or Mosquitto is unavailable, simulator publishes are stored in `spool.db` inside the simulator container and replayed after reconnect.

## OTA Not Acknowledged

Use local Mosquitto port `1884` for simulated bins:

```bash
python3 leshan/cli/push_update.py --bin BIN-001 --version 2.2.0 --broker localhost --port 1884 --failure-rate 0
```

Then check:

```bash
curl http://localhost:8090/devices
curl http://localhost:9102/metrics | grep edge_ota_updates
```

If acks are missing, confirm the simulator is connected to Mosquitto and has registered bins.

## Grafana Empty

1. Open Prometheus targets: http://localhost:9090/targets.
2. Confirm these targets are up:
   - `simulator:9102`
   - `emqx-bridge:9100`
   - `validator:9101`
   - `registry:8090`
3. Open Grafana: http://localhost:3000, login `admin` / `admin`.
4. Dashboard folder: `Edge`, dashboard: `SWMS Edge Overview`.

## Demo Checklist

1. Start full stack.
2. Open Node-RED and show the gateway flow.
3. Open Kafka UI and show `waste.bin.telemetry`.
4. Open Grafana and show live throughput/MQTT/buffer panels.
5. Open registry and show bin firmware versions.
6. Run OTA command with `--failure-rate 0`.
7. Refresh registry and show firmware `2.2.0`.
8. Stop EMQX briefly and show simulator buffer depth rising, then restart and show drain.
