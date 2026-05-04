from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer


HTML = b"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SWMS Leshan Demo</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; color: #17202a; }
    code { background: #eef2f5; padding: .15rem .3rem; border-radius: 4px; }
    .panel { border: 1px solid #d8dee4; border-radius: 6px; padding: 1rem; max-width: 760px; }
  </style>
</head>
<body>
  <h1>SWMS Leshan Demo</h1>
  <div class="panel">
    <p>This local demo replaces the unavailable public Leshan Docker image.</p>
    <p>Use the simulated registry for live device state:</p>
    <p><code>http://localhost:8090/devices</code></p>
    <p>Use the OTA CLI to push firmware commands to simulated bins.</p>
  </div>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/health", "/ready"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path == "/" or self.path.startswith("/index"):
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def main() -> None:
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
