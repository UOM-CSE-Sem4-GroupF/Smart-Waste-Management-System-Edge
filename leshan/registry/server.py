from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote


PORT = int(os.getenv("REGISTRY_PORT", "8090"))
DEVICES: dict[str, dict] = {}
LOCK = threading.RLock()


class RegistryHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json({"status": "ok"})
            return
        if self.path == "/ready":
            self._json({"status": "ready"})
            return
        if self.path == "/metrics":
            with LOCK:
                registered = len(DEVICES)
            body = (
                "# HELP edge_registered_devices Number of simulated devices in registry\n"
                "# TYPE edge_registered_devices gauge\n"
                f"edge_registered_devices {registered}\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "text/plain; version=0.0.4")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/devices":
            with LOCK:
                devices = sorted(DEVICES.values(), key=lambda item: item["bin_id"])
            self._json({"devices": devices})
            return
        if self.path.startswith("/devices/"):
            bin_id = unquote(self.path.split("/", 2)[2])
            with LOCK:
                device = DEVICES.get(bin_id)
            if device is None:
                self._json({"error": "not_found"}, status=404)
            else:
                self._json(device)
            return
        self._json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:
        if not self.path.startswith("/devices/"):
            self._json({"error": "not_found"}, status=404)
            return
        bin_id = unquote(self.path.split("/", 2)[2])
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (TypeError, ValueError, json.JSONDecodeError):
            self._json({"error": "bad_json"}, status=400)
            return
        payload["bin_id"] = bin_id
        with LOCK:
            DEVICES[bin_id] = payload
        self._json({"status": "ok", "device": payload})

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), RegistryHandler)
    print(f"device registry listening on :{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
