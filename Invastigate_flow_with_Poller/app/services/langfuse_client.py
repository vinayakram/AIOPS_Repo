from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core import get_settings, logger

# Keywords that indicate an error when found in output/input/metadata
_ERROR_INDICATORS = re.compile(
    r"\b(error|fail|failed|failure|exception|timeout|refused|denied|"
    r"crash|unavailable|rejected|abort|panic|disabled|unauthorized)\b",
    re.IGNORECASE,
)


def _safe_parse_json(value: Any) -> Any:
    """Try to parse a JSON string; return as-is if not JSON."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def _contains_error(value: Any) -> bool:
    """
    Recursively check if a value (str, dict, list) contains error indicators.
    Handles JSON strings that need parsing first.
    """
    if value is None:
        return False

    parsed = _safe_parse_json(value)

    if isinstance(parsed, str):
        return bool(_ERROR_INDICATORS.search(parsed))

    if isinstance(parsed, dict):
        # Check if there's an explicit "error" key with a truthy value
        if parsed.get("error"):
            return True
        # Check all string values recursively
        for v in parsed.values():
            if _contains_error(v):
                return True

    if isinstance(parsed, list):
        for item in parsed:
            if _contains_error(item):
                return True

    return False


def _detect_level(obs: dict[str, Any]) -> str:
    """
    Determine log level for a Langfuse observation/trace by checking
    multiple fields in priority order:
      1. Explicit status field (ERROR, FAIL, etc.)
      2. statusMessage field
      3. output field (may contain JSON with "error" key)
      4. input field
      5. Default to INFO
    """
    # 1. Explicit status
    status = (obs.get("status") or "").upper()
    if status in {"ERROR", "FAIL", "FAILED"}:
        return "ERROR"

    # 2. statusMessage
    status_msg = obs.get("statusMessage") or ""
    if status_msg and _ERROR_INDICATORS.search(status_msg):
        return "ERROR"

    # 3. Output field — often contains error payloads as JSON strings
    output = obs.get("output")
    if output and _contains_error(output):
        return "ERROR"

    # 4. Input field (rare but possible)
    input_val = obs.get("input")
    if input_val and _contains_error(input_val):
        return "WARN"

    return "INFO"


class LangfuseClient:
    """
    Fetches trace and observation data from Langfuse using the REST API.

    Docs: https://langfuse.com/docs/api
    Authentication: Basic auth with public_key:secret_key
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._host = settings.langfuse_host.rstrip("/")
        self._auth = (settings.langfuse_public_key, settings.langfuse_secret_key)

    @staticmethod
    def extract_timespan(
        logs: list[dict[str, Any]],
    ) -> tuple[str | None, str | None]:
        """
        Extract the trace start and end timestamps from already-fetched Langfuse logs.

        The first log entry is the trace-level entry whose timestamp is trace.startTime
        and whose metadata contains latency_ms (trace duration from Langfuse).
        End time = startTime + latency_ms.

        Falls back to (min_timestamp, max_timestamp) across all observations if
        latency is unavailable.

        Returns:
            (trace_start_iso, trace_end_iso) — both None if logs are empty.
        """
        if not logs:
            return None, None

        trace_entry = logs[0]
        trace_start: str | None = trace_entry.get("timestamp")
        latency_ms = (trace_entry.get("metadata") or {}).get("latency_ms")

        if trace_start and latency_ms is not None:
            try:
                ts = trace_start.replace("Z", "+00:00")
                dt_start = datetime.fromisoformat(ts)
                if dt_start.tzinfo is None:
                    dt_start = dt_start.replace(tzinfo=timezone.utc)
                dt_end = dt_start + timedelta(milliseconds=float(latency_ms))
                return trace_start, dt_end.isoformat()
            except (ValueError, TypeError):
                pass

        # Fallback: derive span from min/max observation timestamps
        all_timestamps = [l.get("timestamp") for l in logs if l.get("timestamp")]
        if all_timestamps:
            return min(all_timestamps), max(all_timestamps)

        return trace_start, None

    async def fetch_trace(self, trace_id: str) -> list[dict[str, Any]]:
        """
        Fetch a trace and its observations from Langfuse, then flatten
        into a list of log-like dicts for the normalization agent.
        """
        logger.info("Langfuse | fetching trace_id=%s", trace_id)

        trace_data = await self._get_trace(trace_id)
        observations = await self._get_observations(trace_id)

        logs = self._trace_to_logs(trace_data, observations)
        logger.info("Langfuse | got %d log entries for trace_id=%s", len(logs), trace_id)
        return logs

    # ── HTTP helpers ───────────────────────────────────────────────────

    async def _get_trace(self, trace_id: str) -> dict[str, Any]:
        """GET /api/public/traces/:traceId"""
        url = f"{self._host}/api/public/traces/{trace_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, auth=self._auth)
            resp.raise_for_status()
            return resp.json()

    async def _get_observations(self, trace_id: str) -> list[dict[str, Any]]:
        """GET /api/public/observations?traceId=..."""
        url = f"{self._host}/api/public/observations"
        params = {"traceId": trace_id, "limit": 100}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, auth=self._auth, params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])

    # ── Transform to log format ────────────────────────────────────────

    @staticmethod
    def _trace_to_logs(
        trace: dict[str, Any],
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Convert Langfuse trace + observations into flat log entries
        that the normalization agent can consume.

        Detects errors from:
          - status field
          - statusMessage field
          - output field (JSON strings with "error" keys)
          - input field
        """
        logs: list[dict[str, Any]] = []

        # ── Top-level trace entry ──────────────────────────────────────
        trace_level = _detect_level(trace)
        trace_output = trace.get("output")
        trace_output_parsed = _safe_parse_json(trace_output)

        message_parts = [f"Trace '{trace.get('name', 'unknown')}'"]

        # Include status if present
        if trace.get("status"):
            message_parts.append(f"status: {trace['status']}")

        # Extract error from output
        if isinstance(trace_output_parsed, dict) and trace_output_parsed.get("error"):
            message_parts.append(f"Error: {trace_output_parsed['error']}")
        elif trace.get("statusMessage"):
            message_parts.append(f"Error: {trace['statusMessage']}")
        elif trace_output and isinstance(trace_output, str) and len(trace_output) < 300:
            message_parts.append(f"Output: {trace_output}")

        # Parse metadata if it's a JSON string
        trace_metadata = _safe_parse_json(trace.get("metadata")) or {}
        if isinstance(trace_metadata, str):
            trace_metadata = {}

        logs.append({
            "timestamp": trace.get("startTime") or trace.get("timestamp", ""),
            "source": "langfuse",
            "service": trace.get("name", "unknown_agent"),
            "message": " | ".join(message_parts),
            "level": trace_level,
            "metadata": {
                "trace_id": trace.get("id"),
                "session_id": trace.get("sessionId"),
                "user_id": trace_metadata.get("user_id") or trace.get("userId"),
                "tags": trace_metadata.get("tags") or trace.get("tags", []),
                "latency_ms": trace.get("latency"),
                "total_cost": trace.get("calculatedTotalCost"),
                "input": trace.get("input"),
                "output": trace.get("output"),
            },
        })

        # ── Individual observations (spans, generations, events) ───────
        for obs in observations:
            obs_type = obs.get("type", "SPAN")
            obs_name = obs.get("name", "unknown")
            obs_level = _detect_level(obs)

            msg_parts = [f"{obs_type} '{obs_name}'"]

            # Status
            if obs.get("status"):
                msg_parts.append(f"status: {obs['status']}")

            # Error from output field
            obs_output = obs.get("output")
            obs_output_parsed = _safe_parse_json(obs_output)
            if isinstance(obs_output_parsed, dict) and obs_output_parsed.get("error"):
                msg_parts.append(f"Error: {obs_output_parsed['error']}")
            elif obs.get("statusMessage"):
                msg_parts.append(f"Error: {obs['statusMessage']}")

            # Model info for GENERATION type
            if obs_type == "GENERATION":
                model = obs.get("model", "unknown")
                msg_parts.append(f"Model: {model}")
                usage = obs.get("usage") or {}
                if usage:
                    msg_parts.append(
                        f"Tokens: in={usage.get('input', 0)} out={usage.get('output', 0)}"
                    )

            # Input context
            obs_input = obs.get("input")
            obs_input_parsed = _safe_parse_json(obs_input)
            if isinstance(obs_input_parsed, dict):
                # Include key info from input without dumping everything
                input_summary = {
                    k: v for k, v in obs_input_parsed.items()
                    if k in {"query", "model", "articles_count", "mode", "top_k", "max_articles"}
                }
                if input_summary:
                    msg_parts.append(f"Input: {json.dumps(input_summary)}")

            # Parse metadata
            obs_metadata = _safe_parse_json(obs.get("metadata")) or {}
            if isinstance(obs_metadata, str):
                obs_metadata = {}

            logs.append({
                "timestamp": obs.get("startTime", ""),
                "source": "langfuse",
                "service": obs_name,
                "message": " | ".join(msg_parts),
                "level": obs_level,
                "metadata": {
                    "observation_id": obs.get("id"),
                    "observation_type": obs_type,
                    "model": obs.get("model"),
                    "latency_ms": obs.get("latency"),
                    "completion_start_time": obs.get("completionStartTime"),
                    "cost": obs.get("calculatedTotalCost"),
                    "depth": obs.get("depth"),
                    "input": obs.get("input"),
                    "output": obs.get("output"),
                },
            })

        return logs
