#!/usr/bin/env python3
"""Local launcher for the AIOps preview demo.

The browser cannot execute shell scripts directly, so this tiny localhost-only
server exposes one button-safe endpoint for the presenter page.
"""

from __future__ import annotations

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "MedicalAgent" / "scripts" / "test_pod_cpu_threshold.sh"
HOST = "0.0.0.0"
PORT = 8765
DEFAULT_LOAD_SECONDS = 60
ALLOWED_LOAD_SECONDS = {10, 12, 30, 60, 90, 120}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "script": str(SCRIPT)})
            return
        self._send_json(404, {"ok": False, "error": "Use POST /run-simulation"})

    def do_POST(self) -> None:
        if self.path != "/run-simulation":
            self._send_json(404, {"ok": False, "error": "Unknown endpoint"})
            return

        if not SCRIPT.exists():
            self._send_json(500, {"ok": False, "error": f"Script not found: {SCRIPT}"})
            return

        load_seconds = DEFAULT_LOAD_SECONDS
        length_header = self.headers.get("Content-Length")
        if length_header:
            try:
                body = self.rfile.read(int(length_header)).decode("utf-8")
                payload = json.loads(body or "{}")
                load_seconds = int(payload.get("load_seconds", DEFAULT_LOAD_SECONDS))
            except Exception:
                self._send_json(400, {"ok": False, "error": "Invalid JSON body"})
                return

        if load_seconds not in ALLOWED_LOAD_SECONDS:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": f"load_seconds must be one of {sorted(ALLOWED_LOAD_SECONDS)}",
                },
            )
            return

        env = os.environ.copy()
        env["LOAD_SECONDS"] = str(load_seconds)

        try:
            process = subprocess.Popen(
                [str(SCRIPT)],
                cwd=str(SCRIPT.parent.parent),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": str(exc)})
            return

        self._send_json(
            202,
            {
                "ok": True,
                "pid": process.pid,
                "command": f"LOAD_SECONDS={load_seconds} {SCRIPT}",
                "message": "CPU spike simulation started.",
            },
        )

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[aiops-preview] {self.address_string()} - {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"AIOps preview launcher listening on http://{HOST}:{PORT}")
    print(f"Endpoint: POST http://localhost:{PORT}/run-simulation")
    print(f"Script:   LOAD_SECONDS in {sorted(ALLOWED_LOAD_SECONDS)} {SCRIPT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
