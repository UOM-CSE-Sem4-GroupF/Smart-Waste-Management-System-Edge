import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_simulator_entrypoint():
    spec = importlib.util.spec_from_file_location("simulator_entrypoint", ROOT / "simulator.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeEdgeClient:
    def __init__(self):
        self.registered = []
        self.published = []

    def register_sensor(self, sensor):
        self.registered.append(sensor)

    def publish_or_spool(self, topic, payload):
        self.published.append((topic, payload))


def test_run_bin_loop_registers_sensor_and_publishes_current_schema(monkeypatch):
    simulator = load_simulator_entrypoint()
    edge = FakeEdgeClient()

    cfg = {
        "bin_id": "BIN-999",
        "zone_id": 7,
        "waste_category": "general",
        "volume_litres": 240,
    }

    def stop_after_first_publish(_seconds):
        raise StopIteration

    monkeypatch.setattr(simulator, "TOPIC_PREFIX", "sensors")
    monkeypatch.setattr(simulator, "FIRMWARE_VER", "2.1.4")
    monkeypatch.setattr(simulator, "report_device", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(simulator.time, "sleep", stop_after_first_publish)

    with pytest.raises(StopIteration):
        simulator.run_bin_loop(edge, cfg)

    assert edge.registered
    assert edge.registered[0].bin_id == "BIN-999"
    assert len(edge.published) == 1

    topic, payload_text = edge.published[0]
    payload = json.loads(payload_text)

    assert topic == "sensors/bin/BIN-999/telemetry"
    assert payload["bin_id"] == "BIN-999"
    assert payload["firmware_version"] == "2.1.4"
    assert isinstance(payload["fill_level_pct"], float)
    assert isinstance(payload["battery_level_pct"], float)
    assert isinstance(payload["signal_strength_dbm"], int)
    assert isinstance(payload["temperature_c"], float)
    assert isinstance(payload["timestamp"], str)
    assert isinstance(payload["error_flags"], int)
