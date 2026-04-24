#!/usr/bin/env python3
"""Generate a small, steady background load against SampleAgent.

This is meant for demo environments where Grafana should show a stable,
non-zero CPU baseline before the threshold-breach spike is triggered.
It deliberately hits a dedicated demo endpoint so it does not create
trace noise, extra AIops tickets, or RCA churn.
"""

from __future__ import annotations

import argparse
import json
import signal
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def _post_once(url: str, work_ms: int, timeout: float) -> tuple[bool, float, str]:
    body = json.dumps({"work_ms": work_ms}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "X-AIOPS-DEMO": "steady_background_load"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            return 200 <= response.status < 300, (time.perf_counter() - started) * 1000, ""
    except urllib.error.HTTPError as exc:
        return False, (time.perf_counter() - started) * 1000, f"http {exc.code}"
    except Exception as exc:
        return False, (time.perf_counter() - started) * 1000, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Steady background load generator for SampleAgent.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8002", help="SampleAgent base URL")
    parser.add_argument("--users", type=int, default=1, help="Concurrent steady-load workers")
    parser.add_argument("--work-ms", type=int, default=250, help="CPU work per request in milliseconds")
    parser.add_argument("--pause-ms", type=int, default=1250, help="Pause between requests per worker")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds")
    parser.add_argument("--startup-delay", type=float, default=0.0, help="Optional delay before starting")
    args = parser.parse_args()

    stop_event = threading.Event()
    url = args.base_url.rstrip("/") + "/api/demo/background-load"
    lock = threading.Lock()
    stats = {
        "ok": 0,
        "errors": 0,
        "last_error": "",
        "last_latency_ms": 0.0,
    }

    def _handle_signal(signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if args.startup_delay > 0:
        time.sleep(args.startup_delay)

    def worker() -> None:
        while not stop_event.is_set():
            ok, latency_ms, error = _post_once(url, args.work_ms, args.timeout)
            with lock:
                if ok:
                    stats["ok"] += 1
                else:
                    stats["errors"] += 1
                    stats["last_error"] = error
                stats["last_latency_ms"] = latency_ms
            stop_event.wait(args.pause_ms / 1000.0)

    print(
        f"Starting steady SampleAgent background load: url={url} users={args.users} "
        f"work_ms={args.work_ms} pause_ms={args.pause_ms}",
        flush=True,
    )

    with ThreadPoolExecutor(max_workers=max(1, args.users)) as executor:
        for _ in range(max(1, args.users)):
            executor.submit(worker)
        try:
            while not stop_event.wait(15):
                with lock:
                    print(
                        "steady-load heartbeat "
                        f"ok={stats['ok']} errors={stats['errors']} "
                        f"last_latency_ms={stats['last_latency_ms']:.1f} "
                        f"last_error={stats['last_error'] or '-'}",
                        flush=True,
                    )
        finally:
            stop_event.set()

    with lock:
        print(
            f"Steady load stopped. ok={stats['ok']} errors={stats['errors']} "
            f"last_error={stats['last_error'] or '-'}",
            flush=True,
        )
    return 0 if stats["ok"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
