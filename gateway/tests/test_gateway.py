import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_flow_file_is_non_empty_valid_json():
    flow = json.loads((ROOT / "flows.json").read_text(encoding="utf-8"))
    assert isinstance(flow, list)
    assert any(node.get("type") == "mqtt in" for node in flow)
    assert any(node.get("type") == "mqtt out" for node in flow)
    assert any(node.get("url") == "/health" for node in flow)
    assert any(node.get("url") == "/ready" for node in flow)


def test_gateway_compose_file_exists():
    compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "eclipse-mosquitto" in compose
    assert "nodered/node-red" in compose
    assert "flows.json:/data/flows.json" in compose
