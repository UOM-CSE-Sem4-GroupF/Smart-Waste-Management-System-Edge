"""Unit tests for BinSensor, the SQLite spool, and the telemetry schema.

Covers the four MVP areas from TEAM_TASKS.md § Person 1:
    1. Sleep-interval transitions (the four adaptive bands)
    2. Fill calculation (advance, clipping, collection, rapid-fill detect)
    3. Buffer / replay (enqueue, FIFO flush, eviction, restart durability)
    4. Schema validation (every generated payload conforms; bad payloads fail)

Tests are deterministic: every BinSensor receives a seeded `random.Random`
and `tick()` is called with an explicit `now=` so timestamp / temperature
assertions don't drift with the wall clock.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from bin_sensor import (
    BASE_FILL_RATES,
    BinSensor,
    ERR_SENSOR_READ,
    ERR_LID_OPEN,
    ERR_TILT,
    ERR_TOF_SUNLIGHT,
    publish_interval_seconds,
    time_of_day_multiplier,
)
from buffer import TelemetryBuffer


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "telemetry.schema.json"
FIXED_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def validator(schema: dict) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def make_sensor(
    *,
    bin_id: str = "BIN-001",
    category: str = "general",
    seed: int = 42,
    volume_litres: int = 240,
) -> BinSensor:
    return BinSensor(
        bin_id=bin_id,
        zone_id=1,
        waste_category=category,
        volume_litres=volume_litres,
        rng=random.Random(seed),
    )


# ===========================================================================
# 1. Sleep-interval transitions
# ===========================================================================

class TestSleepIntervals:
    """Adaptive sleep cycle from service-specifications.md §SERVICE 1."""

    @pytest.mark.parametrize(
        "fill_pct, expected_seconds",
        [
            (0.0,   600),   # empty           → 10 min
            (10.0,  600),
            (49.99, 600),   # just under low band
            (50.0,  300),   # band boundary   → 5 min
            (74.99, 300),
            (75.0,  120),   # band boundary   → 2 min
            (89.99, 120),
            (90.0,   30),   # urgent          → 30 sec
            (95.0,   30),
            (100.0,  30),
        ],
    )
    def test_band_boundaries(self, fill_pct: float, expected_seconds: int) -> None:
        assert publish_interval_seconds(fill_pct, speed=1.0) == expected_seconds

    def test_speed_factor_compresses_real_time(self) -> None:
        # 10 minutes at speed=60 means 10 real seconds elapse between publishes.
        assert publish_interval_seconds(10.0, speed=60.0) == pytest.approx(10.0)
        assert publish_interval_seconds(95.0, speed=60.0) == pytest.approx(0.5)

    def test_speed_zero_or_negative_rejected(self) -> None:
        with pytest.raises(ValueError):
            publish_interval_seconds(50.0, speed=0)
        with pytest.raises(ValueError):
            publish_interval_seconds(50.0, speed=-1)

    def test_method_matches_function(self) -> None:
        sensor = make_sensor()
        sensor.fill_pct = 80.0
        assert sensor.publish_interval_seconds(speed=1.0) == 120
        assert sensor.publish_interval_seconds(speed=2.0) == 60

    def test_interval_decreases_monotonically_with_fill(self) -> None:
        prev = publish_interval_seconds(0.0)
        for fill in (40.0, 50.0, 70.0, 75.0, 85.0, 90.0, 99.0):
            curr = publish_interval_seconds(fill)
            assert curr <= prev
            prev = curr


# ===========================================================================
# 2. Fill calculation
# ===========================================================================

class TestFillCalculation:
    def test_initial_fill_within_documented_range(self) -> None:
        # Each new bin starts between 5% and 40% per __post_init__.
        for seed in range(20):
            sensor = make_sensor(seed=seed)
            assert 5.0 <= sensor.fill_pct <= 40.0

    def test_fill_advances_with_simulated_hours(self) -> None:
        sensor = make_sensor(category="paper", seed=1)
        start = sensor.fill_pct
        # 10 simulated hours of paper waste should accumulate measurable fill.
        sensor.tick(elapsed_sim_hours=10.0, now=FIXED_NOW)
        assert sensor.fill_pct > start

    def test_fill_clamped_to_0_100(self) -> None:
        sensor = make_sensor(category="food_waste", seed=7)
        # Drive a large simulated time forward; even with the random collection
        # event firing, fill must never be negative or above 100%.
        for _ in range(50):
            sensor.tick(elapsed_sim_hours=2.0, now=FIXED_NOW)
            assert 0.0 <= sensor.fill_pct <= 100.0

    def test_collection_event_resets_above_95(self) -> None:
        sensor = make_sensor(seed=3)
        sensor.fill_pct = 96.0
        sensor.tick(elapsed_sim_hours=0.0, now=FIXED_NOW)
        # _maybe_collect drops fill back to 2–8% per spec.
        assert 0.0 <= sensor.fill_pct <= 10.0

    def test_negative_elapsed_rejected(self) -> None:
        sensor = make_sensor()
        with pytest.raises(ValueError):
            sensor.tick(elapsed_sim_hours=-1.0)

    def test_unknown_category_rejected(self) -> None:
        with pytest.raises(ValueError):
            BinSensor(
                bin_id="BIN-999",
                zone_id=1,
                waste_category="radioactive",   # not in BASE_FILL_RATES
                volume_litres=240,
            )

    def test_food_waste_fills_faster_than_glass(self) -> None:
        # Average over many seeded bins to prove the rate ordering holds.
        food_total = glass_total = 0.0
        for seed in range(30):
            food = make_sensor(category="food_waste", seed=seed)
            glass = make_sensor(category="glass", seed=seed)
            food.fill_pct = glass.fill_pct = 20.0   # equal start
            food.tick(elapsed_sim_hours=5.0, now=FIXED_NOW)
            glass.tick(elapsed_sim_hours=5.0, now=FIXED_NOW)
            food_total += food.fill_pct
            glass_total += glass.fill_pct
        assert food_total > glass_total

    def test_rapid_fill_detection(self) -> None:
        sensor = make_sensor(seed=11)
        sensor.fill_pct = 30.0
        sensor.last_fill_pct = 30.0
        # Simulate a sudden dump: spike fill_pct by >10% before the next tick.
        sensor.fill_pct = 45.0
        assert sensor.rapid_fill_detected(threshold_pct=10.0) is True
        # After publishing the spike, last_fill catches up.
        sensor.tick(elapsed_sim_hours=0.0, now=FIXED_NOW)
        assert sensor.rapid_fill_detected(threshold_pct=10.0) is False

    def test_time_of_day_multiplier_evening_peak(self) -> None:
        assert time_of_day_multiplier("food_waste", hour=19) == 2.0
        assert time_of_day_multiplier("food_waste", hour=3) == 0.3
        # Categories not driven by human rhythm stay flat.
        assert time_of_day_multiplier("glass", hour=19) == 1.0
        assert time_of_day_multiplier("paper", hour=3) == 1.0

    def test_all_known_categories_construct(self) -> None:
        for category in BASE_FILL_RATES:
            sensor = make_sensor(category=category, seed=0)
            payload = sensor.tick(elapsed_sim_hours=0.5, now=FIXED_NOW)
            assert payload["bin_id"] == "BIN-001"


# ===========================================================================
# 3. Buffer / replay
# ===========================================================================

class TestBuffer:
    def test_enqueue_and_drain_in_order(self, tmp_path: Path) -> None:
        with TelemetryBuffer(tmp_path / "spool.db", max_size=100) as buf:
            for i in range(5):
                buf.enqueue("sensors/bin/BIN-001/telemetry", f'{{"i":{i}}}')
            assert buf.depth() == 5

            received: list[str] = []

            def publish(_topic: str, payload: str) -> bool:
                received.append(payload)
                return True

            sent = buf.flush(publish)
            assert sent == 5
            assert buf.depth() == 0
            assert received == [f'{{"i":{i}}}' for i in range(5)]

    def test_flush_stops_on_failure_and_keeps_remainder(self, tmp_path: Path) -> None:
        with TelemetryBuffer(tmp_path / "spool.db", max_size=100) as buf:
            for i in range(10):
                buf.enqueue("t", f"msg-{i}")

            calls = {"n": 0}

            def publish(_topic: str, _payload: str) -> bool:
                calls["n"] += 1
                # First three succeed, fourth "fails" (broker still down).
                return calls["n"] <= 3

            sent = buf.flush(publish)
            assert sent == 3
            assert buf.depth() == 7
            # The next message in the queue must be msg-3 (FIFO preserved).
            head = buf.peek(1)
            assert head[0].payload == "msg-3"

    def test_disconnect_publish_50_reconnect_drains_in_order(
        self, tmp_path: Path
    ) -> None:
        """Mirrors the MVP demo: broker offline → 50 readings → broker up → all 50 arrive."""
        with TelemetryBuffer(tmp_path / "spool.db", max_size=200) as buf:
            payloads = [json.dumps({"seq": i}) for i in range(50)]
            for p in payloads:
                buf.enqueue("sensors/bin/BIN-001/telemetry", p)

            # Broker comes back online; everything drains in order.
            received: list[str] = []
            buf.flush(lambda _t, p: (received.append(p), True)[1])
            assert received == payloads
            assert buf.is_empty()

    def test_eviction_drops_oldest_when_full(self, tmp_path: Path) -> None:
        with TelemetryBuffer(tmp_path / "spool.db", max_size=3) as buf:
            for i in range(5):
                buf.enqueue("t", f"msg-{i}")
            assert buf.depth() == 3
            head = buf.peek(3)
            # Oldest two (msg-0, msg-1) evicted; remaining are 2,3,4 in order.
            assert [m.payload for m in head] == ["msg-2", "msg-3", "msg-4"]

    def test_persistence_across_restart(self, tmp_path: Path) -> None:
        db = tmp_path / "spool.db"
        with TelemetryBuffer(db) as buf:
            buf.enqueue("t", "before-restart-1")
            buf.enqueue("t", "before-restart-2")

        # Re-open the same file — messages must still be there in order.
        with TelemetryBuffer(db) as buf2:
            assert buf2.depth() == 2
            received: list[str] = []
            buf2.flush(lambda _t, p: (received.append(p), True)[1])
            assert received == ["before-restart-1", "before-restart-2"]

    def test_publish_exception_keeps_message_queued(self, tmp_path: Path) -> None:
        with TelemetryBuffer(tmp_path / "spool.db") as buf:
            buf.enqueue("t", "will-fail")

            def publish(_t: str, _p: str) -> bool:
                raise ConnectionError("broker offline")

            with pytest.raises(ConnectionError):
                buf.flush(publish)
            # Row must survive — no acks were issued.
            assert buf.depth() == 1

    def test_clear_empties_spool(self, tmp_path: Path) -> None:
        with TelemetryBuffer(tmp_path / "spool.db") as buf:
            for i in range(4):
                buf.enqueue("t", str(i))
            removed = buf.clear()
            assert removed == 4
            assert buf.is_empty()

    def test_invalid_construction_args_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            TelemetryBuffer(tmp_path / "x.db", max_size=0)
        with TelemetryBuffer(tmp_path / "x.db") as buf:
            with pytest.raises(ValueError):
                buf.enqueue("", "payload")
            with pytest.raises(ValueError):
                buf.enqueue("topic", "")


# ===========================================================================
# 4. Schema validation
# ===========================================================================

class TestSchemaConformance:
    def test_schema_itself_is_valid_draft_2020_12(self, schema: dict) -> None:
        # check_schema raises if the meta-schema rejects it.
        Draft202012Validator.check_schema(schema)

    def test_canonical_example_validates(
        self, schema: dict, validator: Draft202012Validator
    ) -> None:
        for example in schema.get("examples", []):
            validator.validate(example)

    def test_every_generated_payload_validates(
        self, validator: Draft202012Validator
    ) -> None:
        # Run ten bins through 50 ticks each across a range of conditions and
        # assert every emitted payload conforms to the contract.
        for seed, category in enumerate(BASE_FILL_RATES, start=1):
            sensor = make_sensor(
                bin_id=f"BIN-{seed:03d}", category=category, seed=seed
            )
            for tick_n in range(50):
                payload = sensor.tick(elapsed_sim_hours=0.25, now=FIXED_NOW)
                validator.validate(payload)
                # JSON-serialisable on every tick (paho would call this).
                json.dumps(payload)
                del tick_n

    def test_missing_required_field_fails(
        self, validator: Draft202012Validator
    ) -> None:
        sensor = make_sensor()
        payload = sensor.tick(elapsed_sim_hours=0.1, now=FIXED_NOW)
        del payload["fill_level_pct"]
        with pytest.raises(ValidationError):
            validator.validate(payload)

    def test_extra_field_rejected(self, validator: Draft202012Validator) -> None:
        sensor = make_sensor()
        payload = sensor.tick(elapsed_sim_hours=0.1, now=FIXED_NOW)
        payload["unexpected"] = "should-fail"
        with pytest.raises(ValidationError):
            validator.validate(payload)

    @pytest.mark.parametrize(
        "field, bad_value",
        [
            ("fill_level_pct", -1.0),       # below minimum
            ("fill_level_pct", 101.0),      # above maximum
            ("battery_level_pct", 150.0),
            ("signal_strength_dbm", 10),    # positive dBm not allowed
            ("temperature_c", 200.0),
            ("bin_id", "not-a-bin"),        # fails regex
            ("firmware_version", "v2"),     # not semver
            ("error_flags", -1),            # negative bitmask
            ("timestamp", "yesterday"),     # not ISO 8601
        ],
    )
    def test_out_of_range_values_rejected(
        self,
        validator: Draft202012Validator,
        field: str,
        bad_value: object,
    ) -> None:
        sensor = make_sensor()
        payload = sensor.tick(elapsed_sim_hours=0.1, now=FIXED_NOW)
        payload[field] = bad_value
        with pytest.raises(ValidationError):
            validator.validate(payload)

    def test_error_flag_bits_documented_match_payload(
        self, validator: Draft202012Validator
    ) -> None:
        # All four documented bits combined still fits within max=65535 and
        # validates — guards against someone narrowing the range later.
        sensor = make_sensor()
        payload = sensor.tick(elapsed_sim_hours=0.1, now=FIXED_NOW)
        payload["error_flags"] = (
            ERR_SENSOR_READ | ERR_LID_OPEN | ERR_TILT | ERR_TOF_SUNLIGHT
        )
        validator.validate(payload)
