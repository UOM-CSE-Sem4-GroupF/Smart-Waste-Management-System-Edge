"""BinSensor — single-bin telemetry generator extracted from simulator.py.

One instance models one ESP32 + ToF-equipped bin. No module-level mutable
state; every value the bin needs lives on the instance, so tests can spin
up many bins in parallel and assert against deterministic seeded RNGs.

Public surface:
    sensor = BinSensor(bin_id="BIN-001", zone_id=1,
                       waste_category="food_waste", volume_litres=240)
    payload = sensor.tick(elapsed_sim_hours=0.5)   # advances state, returns dict
    interval_s = sensor.publish_interval_seconds(speed=60)
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


DEFAULT_FIRMWARE_VERSION = "2.1.4"

# Base fill rate per hour (%) by waste category.
# Food waste fills fastest (high turnover near markets/restaurants).
# Glass fills slowest (heavy per litre but low disposal volume).
BASE_FILL_RATES: dict[str, tuple[float, float]] = {
    "food_waste": (4.0, 8.0),
    "general":    (2.0, 4.0),
    "paper":      (1.0, 3.0),
    "plastic":    (0.8, 2.0),
    "glass":      (0.3, 1.0),
}

# Adaptive sleep cycle (seconds of real wall-clock between publishes at SPEED=1).
# Mirrors service-specifications.md §SERVICE 1.
PUBLISH_INTERVAL_BANDS: tuple[tuple[float, float], ...] = (
    (50.0, 600.0),    # fill < 50%   → 10 min
    (75.0, 300.0),    # fill < 75%   → 5 min
    (90.0, 120.0),    # fill < 90%   → 2 min
    (float("inf"), 30.0),  # fill ≥ 90%   → 30 sec (urgent)
)

# Error flag bits (kept in sync with telemetry.schema.json doc).
ERR_SENSOR_READ      = 1 << 0
ERR_LID_OPEN         = 1 << 1
ERR_TILT             = 1 << 2
ERR_TOF_SUNLIGHT     = 1 << 3


def publish_interval_seconds(fill_pct: float, speed: float = 1.0) -> float:
    """Return real-wall-clock seconds until the next publish.

    `speed` compresses simulated time: speed=60 means 1 real second models
    1 simulated minute, so a 10-minute spec interval becomes 10 real seconds.
    """
    if speed <= 0:
        raise ValueError("speed must be positive")
    for upper, raw in PUBLISH_INTERVAL_BANDS:
        if fill_pct < upper:
            return raw / speed
    return PUBLISH_INTERVAL_BANDS[-1][1] / speed


def time_of_day_multiplier(category: str, hour: Optional[int] = None) -> float:
    """Fill-rate multiplier reflecting human activity rhythm for the category.

    `hour` defaults to the current local hour; tests pass an explicit hour to
    pin behaviour.
    """
    if hour is None:
        hour = datetime.now().hour
    if category in ("food_waste", "general"):
        if 7 <= hour < 10:    return 1.8   # morning rush
        if 12 <= hour < 14:   return 1.5   # lunch
        if 18 <= hour < 21:   return 2.0   # evening peak
        if hour >= 23 or hour < 6: return 0.3  # overnight quiet
    return 1.0


@dataclass
class BinSensor:
    """Stateful model of a single ESP32 + ToF bin.

    Each `tick()` advances the bin's internal state by `elapsed_sim_hours`
    and returns a payload conforming to telemetry.schema.json.
    """

    bin_id: str
    zone_id: int
    waste_category: str
    volume_litres: int
    firmware_version: str = DEFAULT_FIRMWARE_VERSION
    rng: random.Random = field(default_factory=random.Random)

    # Instance state (initialised in __post_init__)
    fill_pct: float = field(init=False)
    battery_pct: float = field(init=False)
    signal_base_dbm: int = field(init=False)
    base_fill_rate: float = field(init=False)   # %/hour at 1.0 multiplier
    last_fill_pct: float = field(init=False)

    def __post_init__(self) -> None:
        if self.waste_category not in BASE_FILL_RATES:
            raise ValueError(
                f"Unknown waste_category {self.waste_category!r}; "
                f"expected one of {sorted(BASE_FILL_RATES)}"
            )
        self.fill_pct = self.rng.uniform(5.0, 40.0)
        self.battery_pct = self.rng.uniform(85.0, 100.0)
        self.signal_base_dbm = self.rng.randint(-78, -55)
        lo, hi = BASE_FILL_RATES[self.waste_category]
        self.base_fill_rate = self.rng.uniform(lo, hi)
        self.last_fill_pct = self.fill_pct

    # -- core lifecycle ----------------------------------------------------

    def tick(
        self,
        elapsed_sim_hours: float,
        *,
        now: Optional[datetime] = None,
    ) -> dict:
        """Advance the bin state and return one telemetry payload."""
        if elapsed_sim_hours < 0:
            raise ValueError("elapsed_sim_hours must be non-negative")
        if now is None:
            now = datetime.now(timezone.utc)

        self._advance_fill(elapsed_sim_hours, hour=now.astimezone().hour)
        collected = self._maybe_collect()
        self._drain_battery()
        signal = self._sample_signal()
        temperature = self._sample_temperature(now.astimezone().hour)
        error_flags = self._sample_error_flags()

        payload = {
            "bin_id":              self.bin_id,
            "fill_level_pct":      round(self.fill_pct, 2),
            "battery_level_pct":   round(self.battery_pct, 1),
            "signal_strength_dbm": signal,
            "temperature_c":       temperature,
            "timestamp":           now.astimezone(timezone.utc)
                                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "firmware_version":    self.firmware_version,
            "error_flags":         error_flags,
        }
        # Track last fill *after* publish so rapid-fill detection compares
        # against the previous reading, not the current one.
        self.last_fill_pct = self.fill_pct
        # `collected` is intentionally not part of the wire payload — it's
        # internal-only state for tests/log lines to consult via the return
        # of had_collection_event() if needed in the future.
        del collected
        return payload

    def publish_interval_seconds(self, speed: float = 1.0) -> float:
        """Adaptive sleep before the next publish, scaled by `speed`."""
        return publish_interval_seconds(self.fill_pct, speed=speed)

    def rapid_fill_detected(self, threshold_pct: float = 10.0) -> bool:
        """True if fill rose more than `threshold_pct` since the previous tick.

        Per spec: when this fires, the caller should drop to the next-shorter
        publish interval immediately rather than honouring the band default.
        """
        return (self.fill_pct - self.last_fill_pct) > threshold_pct

    # -- internal helpers --------------------------------------------------

    def _advance_fill(self, elapsed_sim_hours: float, *, hour: int) -> None:
        rate = self.base_fill_rate * time_of_day_multiplier(
            self.waste_category, hour=hour
        )
        self.fill_pct += rate * elapsed_sim_hours
        self.fill_pct += self.rng.gauss(0, 0.2)   # ToF sensor noise
        self.fill_pct = max(0.0, min(self.fill_pct, 100.0))

    def _maybe_collect(self) -> bool:
        """Driver empties the bin once it tops 95%. Returns True on collection."""
        if self.fill_pct >= 95.0:
            self.fill_pct = self.rng.uniform(2.0, 8.0)
            return True
        return False

    def _drain_battery(self) -> None:
        self.battery_pct -= self.rng.uniform(0.003, 0.008)
        if self.battery_pct < 15.0:
            # Field tech replaces the battery
            self.battery_pct = self.rng.uniform(92.0, 100.0)

    def _sample_signal(self) -> int:
        return self.signal_base_dbm + self.rng.randint(-3, 3)

    def _sample_temperature(self, hour: int) -> float:
        # Diurnal curve peaking near 14:00, plus Gaussian jitter.
        base = 22.0 + 8.0 * (1 - abs(hour - 14) / 14.0)
        temp = base + self.rng.gauss(0, 1.5)
        return round(max(15.0, min(45.0, temp)), 1)

    def _sample_error_flags(self) -> int:
        flags = 0
        # ~1% sensor read error
        if self.rng.random() < 0.01:
            flags |= ERR_SENSOR_READ
        # ~5% lid open (ToF reads near-zero distance)
        if self.rng.random() < 0.05:
            flags |= ERR_LID_OPEN
        # ~5% tilt event
        if self.rng.random() < 0.05:
            flags |= ERR_TILT
        # Sunlight-induced ToF dropout: only at midday (10:00–15:00)
        # per the VL53L1X datasheet sensitivity to ambient IR.
        hour = datetime.now().hour
        if 10 <= hour < 15 and self.rng.random() < 0.02:
            flags |= ERR_TOF_SUNLIGHT
        return flags

    # -- topic helper ------------------------------------------------------

    def telemetry_topic(self, prefix: str = "sensors") -> str:
        """Return the MQTT topic this bin publishes to."""
        return f"{prefix}/bin/{self.bin_id}/telemetry"
