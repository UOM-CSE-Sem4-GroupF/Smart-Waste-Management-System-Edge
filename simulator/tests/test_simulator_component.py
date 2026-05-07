import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import simulator


class FakeClient:
    def __init__(self) -> None:
        self.published = []

    def publish(self, topic: str, payload: str, qos: int = 0) -> None:
        self.published.append((topic, payload, qos))


def test_run_bin_loop_publishes_flat_payload(monkeypatch) -> None:
    client = FakeClient()
    cfg = {
        "bin_id": "BIN-999",
        "zone_id": 1,
        "waste_category": "general",
        "volume_litres": 240,
    }

    def stop_sleep(_seconds: float) -> None:
        raise StopIteration()

    monkeypatch.setattr(simulator.time, "sleep", stop_sleep)
    monkeypatch.setattr(simulator, "TOPIC_PREFIX", "sensors")

    with pytest.raises(StopIteration):
        simulator.run_bin_loop(client, cfg)

    assert client.published
    topic, payload, qos = client.published[0]
    assert topic == "sensors/bin/BIN-999/telemetry"
    assert qos == 1

    data = json.loads(payload)
    for key in (
        "bin_id",
        "fill_level_pct",
        "battery_level_pct",
        "signal_strength_dbm",
        "temperature_c",
        "timestamp",
        "firmware_version",
        "error_flags",
    ):
        assert key in data
