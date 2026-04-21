import os
import time
import json
import random
import logging
from datetime import datetime
from dotenv import load_dotenv
import paho.mqtt.client as mqtt

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load config from .env
load_dotenv()

# Configuration
BROKER = os.getenv("MQTT_BROKER")
PORT = int(os.getenv("MQTT_PORT", 1883))
USER = os.getenv("MQTT_USER")
PASS = os.getenv("MQTT_PASSWORD")
TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX")
BIN_ID = os.getenv("MQTT_BIN_ID")

TARGET_TOPIC = f"{TOPIC_PREFIX}/{BIN_ID}/telemetry"

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info(f"✅ Connected to EMQX Broker at {BROKER}")
    else:
        logger.error(f"❌ Connection failed with code {rc}")

def generate_mock_data():
    """Generates a realistic Smart Waste Bin telemetry payload."""
    return {
        "metadata": {
            "node_id": BIN_ID,
            "hw_version": "esp32-v2.1",
            "uptime_seconds": int(time.time()) % 86400
        },
        "sensors": {
            "fill_level_percent": random.randint(0, 95),
            "battery_voltage": round(random.uniform(3.3, 4.2), 2),
            "temperature_c": round(random.uniform(20.0, 35.0), 1),
            "tilted": random.random() > 0.95
        },
        "status": "online" if random.random() > 0.01 else "maintenance"
    }

def run_simulator():
    client = mqtt.Client(client_id=f"swms_sim_{BIN_ID}")
    client.username_pw_set(USER, PASS)
    client.on_connect = on_connect

    logger.info(f"Attempting to connect to {BROKER}:{PORT}...")
    try:
        client.connect(BROKER, PORT, 60)
    except Exception as e:
        logger.error(f"Could not connect: {e}")
        return

    client.loop_start()

    try:
        while True:
            payload = generate_mock_data()
            payload_json = json.dumps(payload)
            
            logger.info(f"Publishing to {TARGET_TOPIC}: {payload_json}")
            client.publish(TARGET_TOPIC, payload_json)
            
            # Wait 10 seconds before next ping
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Simulator stopped by user.")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    run_simulator()
