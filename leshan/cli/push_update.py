from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


DEFAULT_BINS = [f"BIN-{idx:03d}" for idx in range(1, 11)]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Push simulated firmware updates to smart bins.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--bin", dest="bin_id", help="Single bin id, e.g. BIN-001")
    target.add_argument("--all", action="store_true", help="Update the default simulated fleet")
    parser.add_argument("--version", required=True, help="Firmware version to apply, e.g. 2.2.0")
    parser.add_argument("--url", default=None, help="Firmware URL shown in command payload")
    parser.add_argument("--broker", default=os.getenv("MQTT_BROKER", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MQTT_PORT", "1883")))
    parser.add_argument("--user", default=os.getenv("MQTT_USER"))
    parser.add_argument("--password", default=os.getenv("MQTT_PASSWORD"))
    parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait for acks")
    parser.add_argument(
        "--failure-rate",
        type=float,
        default=float(os.getenv("OTA_FAILURE_RATE", "0.05")),
        help="Failure rate sent to the simulator for deterministic demos",
    )
    return parser.parse_args()


class AckCollector:
    def __init__(self, expected: set[str]) -> None:
        self.expected = expected
        self.acks: dict[str, dict] = {}

    def on_message(self, _client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        bin_id = payload.get("bin_id")
        if bin_id in self.expected:
            self.acks[bin_id] = payload


def main() -> int:
    args = parse_args()
    bins = DEFAULT_BINS if args.all else [args.bin_id]
    expected = set(bins)
    collector = AckCollector(expected)

    client = mqtt.Client(client_id=f"swms-ota-cli-{int(time.time())}")
    if args.user:
        client.username_pw_set(args.user, args.password)
    client.on_message = collector.on_message
    client.connect(args.broker, args.port, keepalive=60)
    client.loop_start()
    client.subscribe("commands/bin/+/firmware/ack", qos=1)

    url = args.url or f"http://leshan.local/firmware/{args.version}.bin"
    command = {
        "version": args.version,
        "url": url,
        "requested_at": utc_now(),
        "failure_rate": args.failure_rate,
    }
    for bin_id in bins:
        client.publish(
            f"commands/bin/{bin_id}/firmware",
            json.dumps(command),
            qos=1,
        )
        print(f"sent update command to {bin_id}: {args.version}")

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline and set(collector.acks) != expected:
        time.sleep(0.1)

    client.loop_stop()
    client.disconnect()

    ok = failed = missing = 0
    for bin_id in bins:
        ack = collector.acks.get(bin_id)
        if not ack:
            missing += 1
            print(f"{bin_id}: missing ack")
        elif ack.get("status") == "ok":
            ok += 1
            print(f"{bin_id}: ok -> {ack.get('version')}")
        else:
            failed += 1
            print(f"{bin_id}: failed -> {ack.get('reason')}")

    print(f"summary: ok={ok} failed={failed} missing={missing}")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
