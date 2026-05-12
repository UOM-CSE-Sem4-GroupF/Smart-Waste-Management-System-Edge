import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_flow_file_is_non_empty_valid_json():
    flow = json.loads((ROOT / "flows.json").read_text(encoding="utf-8"))
    assert isinstance(flow, list)
    mqtt_in = [node for node in flow if node.get("type") == "mqtt in"]
    mqtt_out = [node for node in flow if node.get("type") == "mqtt out"]
    functions = [node for node in flow if node.get("type") == "function"]
    assert any(node.get("topic") == "sensors/bin/+/telemetry" for node in mqtt_in)
    assert any(node.get("broker") == "cloud-mqtt" for node in mqtt_out)
    assert any(node.get("url") == "/health" for node in flow)
    assert any(node.get("url") == "/ready" for node in flow)
    assert any("dedup" in node.get("name", "") for node in functions)
    assert any("cloudAvailable" in node.get("func", "") for node in functions)
    assert not any(node.get("topic") == "test_out" for node in mqtt_out)


def test_gateway_compose_file_exists():
    compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "eclipse-mosquitto:2" in compose
    assert "nodered/node-red:3.1.0" in compose
    assert "flows.json:/data/flows.json" in compose
