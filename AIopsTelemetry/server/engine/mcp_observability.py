from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from server.config import settings


ROOT_DIR = Path(__file__).resolve().parents[3]


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call the local stdio MCP server once and return the parsed tool payload."""
    if not settings.MCP_OBSERVABILITY_ENABLED:
        raise RuntimeError("Observability MCP is disabled")

    server_path = Path(settings.MCP_OBSERVABILITY_SERVER_PATH)
    if not server_path.is_absolute():
        server_path = (ROOT_DIR / server_path).resolve()
    if not server_path.exists():
        raise RuntimeError(f"Observability MCP server not found: {server_path}")

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    body = json.dumps(request, separators=(",", ":")).encode()
    wire = b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body

    env = os.environ.copy()
    env.setdefault("MCP_PROMETHEUS_URL", settings.MCP_PROMETHEUS_URL)
    if settings.LANGFUSE_PUBLIC_KEY:
        env.setdefault("MCP_LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY)
    if settings.LANGFUSE_SECRET_KEY:
        env.setdefault("MCP_LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY)
    env.setdefault("MCP_LANGFUSE_HOST", settings.LANGFUSE_HOST)

    proc = subprocess.run(
        [sys.executable, str(server_path)],
        input=wire,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=settings.MCP_OBSERVABILITY_TIMEOUT_SECONDS,
        env=env,
        check=False,
    )
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(proc.stderr.decode(errors="replace")[:500] or "MCP server failed")

    message = _parse_content_length_message(proc.stdout)
    if "error" in message:
        raise RuntimeError(message["error"].get("message", "MCP tool failed"))
    content = (message.get("result") or {}).get("content") or []
    if not content:
        return {}
    text = content[0].get("text") or "{}"
    return json.loads(text)


def _parse_content_length_message(data: bytes) -> dict[str, Any]:
    marker = b"\r\n\r\n"
    idx = data.find(marker)
    if idx < 0:
        raise RuntimeError("Invalid MCP response framing")
    header = data[:idx].decode(errors="replace")
    length = None
    for line in header.splitlines():
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
            break
    if length is None:
        raise RuntimeError("MCP response missing Content-Length")
    raw = data[idx + len(marker):idx + len(marker) + length]
    return json.loads(raw.decode())
