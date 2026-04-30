from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psutil


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def http_probe(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, timeout: float = 2.0) -> dict[str, Any]:
    started = time.perf_counter()
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(8192).decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return {
                "ok": 200 <= response.status < 400,
                "status": response.status,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "body": parsed,
                "timestamp": utc_now_iso(),
            }
    except Exception as exc:
        status = getattr(exc, "code", None)
        return {
            "ok": False,
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
            "timestamp": utc_now_iso(),
        }


def tcp_probe(host: str, port: int, timeout: float = 1.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1), "timestamp": utc_now_iso()}
    except OSError as exc:
        return {"ok": False, "latency_ms": round((time.perf_counter() - started) * 1000, 1), "error": str(exc), "timestamp": utc_now_iso()}


def docker_json_lines(args: list[str], timeout: float = 3.0) -> list[dict[str, Any]]:
    try:
        proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def discover_docker_containers() -> list[dict[str, Any]]:
    containers = docker_json_lines(["ps", "--format", "{{json .}}"])
    stats = docker_json_lines(["stats", "--no-stream", "--format", "{{json .}}"], timeout=5.0)
    stats_by_name = {str(row.get("Name") or row.get("Container") or ""): row for row in stats}
    discovered = []
    for row in containers:
        name = str(row.get("Names") or row.get("Name") or "")
        image = str(row.get("Image") or "")
        lower = f"{name} {image}".lower()
        kind = "container"
        if "prometheus" in lower:
            kind = "prometheus"
        elif "langfuse" in lower:
            kind = "langfuse"
        elif "pgvector" in lower or "postgres" in lower:
            kind = "pgvector"
        elif any(token in lower for token in ["agent", "rag", "medical"]):
            kind = "agent"
        stat = stats_by_name.get(name, {})
        discovered.append({
            "name": name,
            "image": image,
            "kind": kind,
            "status": row.get("Status"),
            "ports": row.get("Ports"),
            "cpu": stat.get("CPUPerc"),
            "memory": stat.get("MemPerc"),
        })
    return discovered


def host_snapshot() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "timestamp": utc_now_iso(),
        "cpu_percent": psutil.cpu_percent(interval=0.4),
        "memory_percent": vm.percent,
        "process_count": len(psutil.pids()),
    }


def classify_container_node(container: dict[str, Any]) -> dict[str, Any]:
    kind = container.get("kind") or "container"
    name = container.get("name") or kind
    role_map = {
        "agent": "Docker-hosted AI agent / application service",
        "prometheus": "Metrics scraping and alerting",
        "pgvector": "Vector database dependency",
        "langfuse": "LLM trace collection",
        "container": "Docker runtime component",
    }
    display_map = {
        "agent": "AI Agent",
        "prometheus": "Prometheus",
        "pgvector": "PGVector DB",
        "langfuse": "Langfuse",
    }
    return {
        "id": f"container-{name}".replace(" ", "-"),
        "zone": "runtime",
        "name": display_map.get(kind, name),
        "process_name": name,
        "role": role_map.get(kind, "Docker runtime component"),
        "status": "ok",
        "timestamp": utc_now_iso(),
        "error_message": "Healthy or not directly implicated by the topology collector.",
        "metrics": [
            {"label": "Container", "value": name},
            {"label": "CPU", "value": container.get("cpu") or "n/a"},
            {"label": "Memory", "value": container.get("memory") or "n/a"},
        ],
        "logs": [
            {"timestamp": utc_now_iso(), "level": "INFO", "message": f"discovered container {name} ({container.get('image')})"}
        ],
    }
