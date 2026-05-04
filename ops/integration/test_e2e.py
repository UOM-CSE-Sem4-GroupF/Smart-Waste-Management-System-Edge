from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "ops" / "docker-compose.full.yaml"


def run_cmd(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def get_text(url: str, timeout: float = 5.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8")


def live_e2e_enabled() -> bool:
    return os.getenv("RUN_E2E") == "1"


def require_live_e2e() -> None:
    if not live_e2e_enabled():
        pytest.skip("set RUN_E2E=1 to run live Docker integration scenarios")


def test_full_stack_compose_config_is_valid():
    result = run_cmd("docker", "compose", "-f", str(COMPOSE), "config")
    assert "swms-edge-simulator" in result.stdout
    assert "swms-nodered" in result.stdout
    assert "swms-emqx-bridge" in result.stdout
    assert "swms-schema-validator" in result.stdout


def test_observability_assets_are_valid():
    dashboard = json.loads(
        (ROOT / "ops" / "grafana" / "dashboards" / "edge-overview.json").read_text(
            encoding="utf-8"
        )
    )
    prometheus = (ROOT / "ops" / "prometheus" / "prometheus.yml").read_text(
        encoding="utf-8"
    )
    assert dashboard["uid"] == "swms-edge-overview"
    assert "simulator:9102" in prometheus
    assert "emqx-bridge:9100" in prometheus
    assert "validator:9101" in prometheus


def test_gateway_flow_has_dedup_and_health_nodes():
    flow = json.loads((ROOT / "gateway" / "flows.json").read_text(encoding="utf-8"))
    function_nodes = [node for node in flow if node.get("type") == "function"]
    assert any("dedup" in node.get("name", "") for node in function_nodes)
    assert any(node.get("url") == "/health" for node in flow)
    assert any(node.get("url") == "/ready" for node in flow)


def test_happy_path_telemetry_reaches_metrics_endpoint():
    require_live_e2e()
    wait_for_url("http://localhost:9102/metrics", "edge_publishes_total")
    metrics = get_text("http://localhost:9102/metrics")
    assert "edge_publishes_total" in metrics


def test_gateway_dedup_flow_is_loaded():
    require_live_e2e()
    wait_for_url("http://localhost:1880/health", "gateway")
    health = get_text("http://localhost:1880/health")
    assert "gateway" in health


def test_schema_validator_flags_bad_payload_metric_exists():
    require_live_e2e()
    wait_for_url("http://localhost:9101/metrics", "edge_schema_violations_total")
    metrics = get_text("http://localhost:9101/metrics")
    assert "edge_schema_violations_total" in metrics


def test_ota_update_cli_bulk_path():
    require_live_e2e()
    run_cmd(
        "python3",
        "leshan/cli/push_update.py",
        "--all",
        "--version",
        "2.2.0",
        "--broker",
        "localhost",
        "--port",
        "1884",
        "--failure-rate",
        "0",
        timeout=30,
    )
    wait_for_url("http://localhost:8090/devices", "2.2.0")


def test_outage_buffer_metric_exists():
    require_live_e2e()
    wait_for_url("http://localhost:9102/metrics", "edge_buffer_depth")
    metrics = get_text("http://localhost:9102/metrics")
    assert "edge_buffer_depth" in metrics


def wait_for_url(url: str, contains: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if contains in get_text(url):
                return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(1)
    if last_error:
        raise AssertionError(f"{url} did not become ready: {last_error}") from last_error
    raise AssertionError(f"{url} did not contain {contains!r}")
