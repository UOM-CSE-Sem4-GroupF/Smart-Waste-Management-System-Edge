# ops/ — Person 5: Integration, Observability & CI Lead

The plumbing that connects all four other components. CI pipelines (GitHub Actions), Prometheus + Grafana for live metrics, and end-to-end integration tests that exercise the full simulator → gateway → EMQX → Kafka path. Also owns the top-level documentation.

> Full task spec: see [`../TEAM_TASKS.md` § Person 5](../TEAM_TASKS.md#person-5--integration-observability--ci-lead)

## MVP milestones (in order)

1. **Lint + test CI green on `main`** — every PR triggers; syntax errors fail the check.
2. **`/metrics` on all Python services + Prometheus scraping** — targets page green.
3. **Grafana dashboard** — single dashboard: publishes/min, latency, buffer depth, MQTT state.
4. **Happy-path e2e test in CI** — full stack via Docker Compose; 100 messages → 100 in Kafka.
5. **Outage + dedup e2e tests** — the two key failure scenarios.
6. **Top-level README + RUNBOOK** — new person clones → stack running in < 15 min.

## Files to create

```
ops/
├── docker-compose.full.yaml         # composes simulator + gateway + broker + leshan
├── prometheus/
│   └── prometheus.yml               # scrape config
├── grafana/
│   └── dashboards/
│       └── edge-overview.json
└── integration/
    ├── test_e2e.py                  # the 5 e2e scenarios
    └── conftest.py
```

Plus, at the repo root:
```
../.github/workflows/
├── lint.yaml
├── test.yaml
└── e2e.yaml
../RUNBOOK.md
```

## Getting started

```bash
cd ops/
docker compose -f docker-compose.full.yaml up
# Prometheus at http://localhost:9090
# Grafana at http://localhost:3000  (admin/admin)
pytest integration/ -v
```

## Branch convention

`p5/<short-task>` — e.g., `p5/ci-lint`, `p5/grafana-dashboard`, `p5/e2e-happy-path`.

## Dependencies

- All four other components must expose `/metrics` and run via Docker Compose.
- Coordinates: agrees on standard metric names with Persons 1–4 on Day 1.
