import os
import time
import json
import random
import logging
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("swms-edge-sim")

load_dotenv()

BROKER       = os.getenv("MQTT_BROKER")
PORT         = int(os.getenv("MQTT_PORT", 1883))
USER         = os.getenv("MQTT_USER")
PASS         = os.getenv("MQTT_PASSWORD")
TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "sensors")
FIRMWARE_VER = "2.1.4"

# SIM_SPEED_FACTOR compresses time so intervals behave realistically at desk-demo speed.
# 1.0 = real time (10-min sleep between low-fill readings).
# 60  = 1 real second equals 1 simulated minute (good for demos).
SPEED = float(os.getenv("SIM_SPEED_FACTOR", "60"))

# ---------------------------------------------------------------------------
# Bin fleet definition
# Each entry: bin_id, zone_id, waste_category, volume_litres
# ---------------------------------------------------------------------------
BINS = [
    {"bin_id": "BIN-001", "zone_id": 1, "waste_category": "food_waste",  "volume_litres": 240},
    {"bin_id": "BIN-002", "zone_id": 1, "waste_category": "general",     "volume_litres": 240},
    {"bin_id": "BIN-003", "zone_id": 2, "waste_category": "paper",       "volume_litres": 360},
    {"bin_id": "BIN-004", "zone_id": 2, "waste_category": "plastic",     "volume_litres": 240},
    {"bin_id": "BIN-005", "zone_id": 2, "waste_category": "glass",       "volume_litres": 120},
    {"bin_id": "BIN-006", "zone_id": 3, "waste_category": "food_waste",  "volume_litres": 240},
    {"bin_id": "BIN-007", "zone_id": 3, "waste_category": "general",     "volume_litres": 360},
    {"bin_id": "BIN-008", "zone_id": 3, "waste_category": "plastic",     "volume_litres": 240},
    {"bin_id": "BIN-009", "zone_id": 4, "waste_category": "glass",       "volume_litres": 120},
    {"bin_id": "BIN-010", "zone_id": 4, "waste_category": "general",     "volume_litres": 240},
]

# Base fill rate per hour (%) by waste category.
# Food waste fills fastest (high turnover near markets/restaurants).
# Glass fills slowest (heavy per litre but low volume disposal rate).
BASE_FILL_RATES = {
    "food_waste": (4.0, 8.0),   # min, max %/hour
    "general":    (2.0, 4.0),
    "paper":      (1.0, 3.0),
    "plastic":    (0.8, 2.0),
    "glass":      (0.3, 1.0),
}

def time_of_day_multiplier(category: str) -> float:
    """Return a fill-rate multiplier based on current hour and waste category."""
    hour = datetime.now().hour
    # Only food_waste and general react strongly to human activity rhythms
    if category in ("food_waste", "general"):
        if 7 <= hour < 10:    return 1.8   # morning rush
        if 12 <= hour < 14:   return 1.5   # lunch
        if 18 <= hour < 21:   return 2.0   # evening peak
        if hour >= 23 or hour < 6: return 0.3  # overnight quiet
    return 1.0

def publish_interval_seconds(fill_pct: float) -> float:
    """Return the real sleep interval per the firmware spec, divided by SPEED."""
    if fill_pct < 50:
        raw = 600   # 10 minutes
    elif fill_pct < 75:
        raw = 300   # 5 minutes
    elif fill_pct < 90:
        raw = 120   # 2 minutes
    else:
        raw = 30    # 30 seconds — urgent
    return raw / SPEED

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info(f"Connected to EMQX at {BROKER}:{PORT}")
    else:
        logger.error(f"MQTT connection failed (rc={rc})")

def run_bin_loop(client: mqtt.Client, cfg: dict):
    """Simulate one bin — runs forever in its own thread."""
    bin_id   = cfg["bin_id"]
    category = cfg["waste_category"]
    zone_id  = cfg["zone_id"]

    # Initial state: start bins at varied fill levels so they don't all go urgent at once
    fill = random.uniform(5.0, 40.0)
    battery = random.uniform(85.0, 100.0)
    signal_base = random.randint(-78, -55)   # dBm — stable per device, slight variation

    topic = f"{TOPIC_PREFIX}/bin/{bin_id}/telemetry"

    min_rate, max_rate = BASE_FILL_RATES[category]
    # Each bin gets its own base rate (reflects location-specific usage patterns)
    bin_base_rate = random.uniform(min_rate, max_rate)

    logger.info(f"[{bin_id}] Starting — category={category} zone={zone_id} "
                f"fill={fill:.1f}% rate={bin_base_rate:.2f}%/hr")

    last_publish_time = time.monotonic()

    while True:
        now = time.monotonic()
        elapsed_real_seconds = now - last_publish_time
        elapsed_sim_hours = (elapsed_real_seconds * SPEED) / 3600.0

        # Advance fill level based on elapsed simulated time
        rate = bin_base_rate * time_of_day_multiplier(category)
        fill += rate * elapsed_sim_hours
        fill += random.gauss(0, 0.2)   # sensor noise
        fill = max(0.0, min(fill, 100.0))

        # Collection event: driver empties the bin
        if fill >= 95.0:
            new_fill = random.uniform(2.0, 8.0)
            logger.info(f"[{bin_id}] *** COLLECTED *** fill {fill:.1f}% → reset to {new_fill:.1f}%")
            fill = new_fill

        # Battery drain: ~0.005% per reading cycle, "replaced" when critically low
        battery -= random.uniform(0.003, 0.008)
        if battery < 15.0:
            battery = random.uniform(92.0, 100.0)
            logger.info(f"[{bin_id}] Battery replaced → {battery:.1f}%")

        # Signal strength: stable with small jitter
        signal = signal_base + random.randint(-3, 3)

        # Temperature: ambient variation (warmer midday, cooler at night)
        hour = datetime.now().hour
        base_temp = 22.0 + 8.0 * abs(hour - 14) / 14.0 * -1 + 8.0  # peaks ~14:00
        temperature = round(base_temp + random.gauss(0, 1.5), 1)
        temperature = max(15.0, min(45.0, temperature))

        # error_flags: 0 = normal; bit 0 = sensor read error (~1% of readings)
        error_flags = 1 if random.random() < 0.01 else 0

        payload = {
            "bin_id":               bin_id,
            "fill_level_pct":       round(fill, 2),
            "battery_level_pct":    round(battery, 1),
            "signal_strength_dbm":  signal,
            "temperature_c":        temperature,
            "timestamp":            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "firmware_version":     FIRMWARE_VER,
            "error_flags":          error_flags,
        }

        payload_json = json.dumps(payload)
        client.publish(topic, payload_json, qos=1)

        interval = publish_interval_seconds(fill)
        logger.info(
            f"[{bin_id}] fill={fill:5.1f}% bat={battery:4.1f}% "
            f"sig={signal}dBm  →  next in {interval:.0f}s  (topic: {topic})"
        )

        last_publish_time = time.monotonic()
        time.sleep(interval)


def main():
    client = mqtt.Client(client_id="swms-edge-simulator")
    client.username_pw_set(USER, PASS)
    client.on_connect = on_connect

    logger.info(f"Connecting to EMQX at {BROKER}:{PORT} ...")
    try:
        client.connect(BROKER, PORT, keepalive=60)
    except Exception as e:
        logger.error(f"Cannot connect to broker: {e}")
        return

    client.loop_start()
    # Brief pause to let the CONNACK arrive before threads start publishing
    time.sleep(2)

    threads = []
    for cfg in BINS:
        t = threading.Thread(target=run_bin_loop, args=(client, cfg), daemon=True)
        t.start()
        threads.append(t)
        # Stagger starts so all 10 bins don't publish simultaneously on tick 0
        time.sleep(random.uniform(0.3, 1.2))

    logger.info(f"All {len(BINS)} bin simulators running (SPEED={SPEED}x).")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Simulator stopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
