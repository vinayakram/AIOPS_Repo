from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests


PROMETHEUS_URL = os.getenv("MCP_PROMETHEUS_URL", "http://localhost:9092").rstrip("/")
PROM_TIMEOUT = float(os.getenv("MCP_PROMETHEUS_TIMEOUT_SECONDS", "10"))
PROM_MAX_RANGE_MINUTES = int(os.getenv("MCP_PROMETHEUS_MAX_RANGE_MINUTES", "120"))

LANGFUSE_HOST = os.getenv("MCP_LANGFUSE_HOST", os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")).rstrip("/")
LANGFUSE_PUBLIC_KEY = os.getenv("MCP_LANGFUSE_PUBLIC_KEY", os.getenv("LANGFUSE_PUBLIC_KEY", ""))
LANGFUSE_SECRET_KEY = os.getenv("MCP_LANGFUSE_SECRET_KEY", os.getenv("LANGFUSE_SECRET_KEY", ""))
LANGFUSE_REDACT_INPUTS = os.getenv("MCP_LANGFUSE_REDACT_INPUTS", "true").lower() in {"1", "true", "yes", "on"}

SERVER_INFO = {"name": "observability-mcp", "version": "0.1.0"}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "prometheus_query",
        "description": "Run a safe instant PromQL query and return normalized evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "prometheus_query_range",
        "description": "Run a bounded PromQL range query and return normalized evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "step": {"type": "string", "default": "15s"},
            },
            "required": ["query", "start", "end"],
        },
    },
    {
        "name": "prometheus_window_for_incident",
        "description": "Fetch common service metrics around an incident timestamp.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "timestamp": {"type": "string"},
                "window_minutes": {"type": "integer", "default": 10},
            },
            "required": ["service", "timestamp"],
        },
    },
    {
        "name": "langfuse_get_trace",
        "description": "Fetch a Langfuse trace by trace id.",
        "inputSchema": {
            "type": "object",
            "properties": {"trace_id": {"type": "string"}},
            "required": ["trace_id"],
        },
    },
    {
        "name": "langfuse_list_traces",
        "description": "List recent Langfuse traces, optionally filtered by name and time.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "name": {"type": "string"},
                "from_timestamp": {"type": "string"},
                "to_timestamp": {"type": "string"},
            },
        },
    },
    {
        "name": "langfuse_trace_summary",
        "description": "Summarize errors, failed observations, model usage, and timings for one Langfuse trace.",
        "inputSchema": {
            "type": "object",
            "properties": {"trace_id": {"type": "string"}},
            "required": ["trace_id"],
        },
    },
    {
        "name": "correlate_cross_service_incident",
        "description": "Correlate whether one service likely caused another service failure using Prometheus evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root_candidate_service": {"type": "string"},
                "affected_service": {"type": "string"},
                "timestamp": {"type": "string"},
                "window_minutes": {"type": "integer", "default": 10},
            },
            "required": ["root_candidate_service", "affected_service", "timestamp"],
        },
    },
]


def main() -> None:
    while True:
        msg = _read_message()
        if msg is None:
            return
        response = _handle_message(msg)
        if response is not None:
            _write_message(response)


def _handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    req_id = msg.get("id")
    try:
        if method == "initialize":
            return _result(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            })
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return _result(req_id, {"tools": TOOLS})
        if method == "tools/call":
            params = msg.get("params") or {}
            return _result(req_id, _call_tool(params.get("name"), params.get("arguments") or {}))
        if method == "ping":
            return _result(req_id, {})
        return _error(req_id, -32601, f"Unknown method: {method}")
    except Exception as exc:
        return _error(req_id, -32000, str(exc))


def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    handlers = {
        "prometheus_query": tool_prometheus_query,
        "prometheus_query_range": tool_prometheus_query_range,
        "prometheus_window_for_incident": tool_prometheus_window_for_incident,
        "langfuse_get_trace": tool_langfuse_get_trace,
        "langfuse_list_traces": tool_langfuse_list_traces,
        "langfuse_trace_summary": tool_langfuse_trace_summary,
        "correlate_cross_service_incident": tool_correlate_cross_service_incident,
    }
    if name not in handlers:
        raise ValueError(f"Unknown tool: {name}")
    payload = handlers[name](args)
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}


def tool_prometheus_query(args: dict[str, Any]) -> dict[str, Any]:
    query = _safe_promql(args["query"])
    data = _prom_get("/api/v1/query", {"query": query})
    return {"evidence": _prom_result_to_evidence(data, query_name="ad_hoc", promql=query)}


def tool_prometheus_query_range(args: dict[str, Any]) -> dict[str, Any]:
    query = _safe_promql(args["query"])
    start = _parse_time(args["start"])
    end = _parse_time(args["end"])
    _check_range(start, end)
    data = _prom_get("/api/v1/query_range", {
        "query": query,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "step": args.get("step") or "15s",
    })
    return {"evidence": _prom_result_to_evidence(data, query_name="ad_hoc_range", promql=query)}


def tool_prometheus_window_for_incident(args: dict[str, Any]) -> dict[str, Any]:
    service = _safe_service(args["service"])
    timestamp = _parse_time(args["timestamp"])
    window = min(int(args.get("window_minutes") or 10), PROM_MAX_RANGE_MINUTES)
    start = timestamp - timedelta(minutes=window)
    end = timestamp + timedelta(minutes=window)
    queries = _service_queries(service, window)
    evidence: list[dict[str, Any]] = []
    for query_name, promql in queries:
        try:
            data = _prom_get("/api/v1/query_range", {
                "query": promql,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step": "15s",
            })
            evidence.extend(_prom_result_to_evidence(data, query_name=query_name, promql=promql))
        except Exception as exc:
            evidence.append(_evidence("prometheus", "metric", service, "warning", f"{query_name} failed: {exc}", values={"query": promql}))
    return {"service": service, "window": {"start": start.isoformat(), "end": end.isoformat()}, "evidence": evidence}


def tool_langfuse_get_trace(args: dict[str, Any]) -> dict[str, Any]:
    trace_id = _safe_trace_id(args["trace_id"])
    trace = _langfuse_get(f"/api/public/traces/{trace_id}")
    return {"trace": _redact(trace)}


def tool_langfuse_list_traces(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": min(int(args.get("limit") or 20), 100)}
    if args.get("name"):
        params["name"] = str(args["name"])
    if args.get("from_timestamp"):
        params["fromTimestamp"] = args["from_timestamp"]
    if args.get("to_timestamp"):
        params["toTimestamp"] = args["to_timestamp"]
    data = _langfuse_get(f"/api/public/traces?{urlencode(params)}")
    return {"traces": _redact(data)}


def tool_langfuse_trace_summary(args: dict[str, Any]) -> dict[str, Any]:
    trace = tool_langfuse_get_trace(args)["trace"]
    observations = trace.get("observations") or []
    evidence: list[dict[str, Any]] = []
    for obs in observations:
        level = str(obs.get("level") or "").upper()
        status = obs.get("statusMessage") or obs.get("status_message")
        if level == "ERROR" or status:
            evidence.append(_evidence(
                "langfuse",
                obs.get("type") or "span",
                obs.get("name") or trace.get("name") or "unknown",
                "error",
                status or f"Langfuse observation {obs.get('name')} marked ERROR",
                timestamp=obs.get("startTime") or trace.get("timestamp"),
                raw_ref=trace.get("id"),
                values={"observation_id": obs.get("id"), "level": level},
                evidence_url=f"{LANGFUSE_HOST}/trace/{trace.get('id')}" if trace.get("id") else None,
            ))
    if not evidence:
        evidence.append(_evidence(
            "langfuse",
            "trace",
            trace.get("name") or "unknown",
            "info",
            "Trace fetched with no failed observations.",
            timestamp=trace.get("timestamp"),
            raw_ref=trace.get("id"),
            evidence_url=f"{LANGFUSE_HOST}/trace/{trace.get('id')}" if trace.get("id") else None,
        ))
    return {"trace_id": trace.get("id"), "evidence": evidence}


def tool_correlate_cross_service_incident(args: dict[str, Any]) -> dict[str, Any]:
    root = _safe_service(args["root_candidate_service"])
    affected = _safe_service(args["affected_service"])
    timestamp = _parse_time(args["timestamp"])
    window = min(int(args.get("window_minutes") or 10), PROM_MAX_RANGE_MINUTES)
    root_ev = tool_prometheus_window_for_incident({"service": root, "timestamp": timestamp.isoformat(), "window_minutes": window})["evidence"]
    affected_ev = tool_prometheus_window_for_incident({"service": affected, "timestamp": timestamp.isoformat(), "window_minutes": window})["evidence"]
    all_ev = root_ev + affected_ev

    root_signals = [
        ev for ev in all_ev
        if ev["severity"] in {"warning", "error", "critical"}
        and (
            ev["service"] in {root, "sample-agent"}
            or str(ev.get("values", {}).get("query_name", "")).startswith("sample_agent_")
        )
    ]
    affected_signals = [
        ev for ev in all_ev
        if (
            ev["service"] in {affected, "triage-agent"}
            or "dependent_agent" in str(ev.get("values", {}).get("query_name", ""))
        )
        and (
            "dependent_agent" in str(ev.get("values", {}).get("query_name", ""))
            or ev["severity"] == "error"
        )
    ]
    confidence = "low"
    if root_signals and affected_signals:
        confidence = "medium"
    if any(
        ("threshold" in (ev.get("values", {}).get("query_name", "") or "")
         or "cpu_utilisation" in (ev.get("values", {}).get("query_name", "") or ""))
        for ev in root_signals
    ) and affected_signals:
        confidence = "high"

    return {
        "root_candidate_service": root,
        "affected_service": affected,
        "confidence": confidence,
        "summary": (
            f"{root} is a likely upstream contributor to {affected} failures."
            if confidence in {"medium", "high"}
            else f"Insufficient metric evidence to attribute {affected} failures to {root}."
        ),
        "evidence": all_ev,
    }


def _service_queries(service: str, window_minutes: int) -> list[tuple[str, str]]:
    window = f"{max(1, window_minutes)}m"
    queries = [
        ("target_up", f'up{{app="{service}"}}'),
        ("http_5xx", f'sum(increase({service.replace("-", "_")}_http_requests_total{{status=~"5.."}}[{window}]))'),
    ]
    if service == "sample-agent":
        queries.extend([
            ("sample_agent_pod_threshold_breaches", f'increase(sample_agent_pod_threshold_breaches_total[{window}])'),
            ("sample_agent_http_503", f'sum(increase(sample_agent_http_requests_total{{app="sample-agent",status="503"}}[{window}]))'),
            ("sample_agent_cpu_utilisation", f'max_over_time(sample_agent_pod_cpu_utilisation_percent{{app="sample-agent"}}[{window}])'),
        ])
    if service == "triage-agent":
        queries.extend([
            ("dependent_agent_upstream_errors", f'sum(increase(dependent_agent_upstream_requests_total{{upstream="sample-agent",result=~"upstream_error|upstream_timeout|upstream_unreachable"}}[{window}]))'),
            ("dependent_agent_cascade_failures", f'sum(increase(dependent_agent_cascade_failures_total{{upstream="sample-agent"}}[{window}]))'),
            ("dependent_agent_last_upstream_status", f'min_over_time(dependent_agent_last_upstream_status{{app="triage-agent",upstream="sample-agent"}}[{window}])'),
        ])
    return queries


def _prom_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = requests.get(f"{PROMETHEUS_URL}{path}", params=params, timeout=PROM_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise ValueError(f"Prometheus returned status={data.get('status')}")
    return data.get("data") or {}


def _prom_result_to_evidence(data: dict[str, Any], *, query_name: str, promql: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for series in data.get("result", []) or []:
        metric = series.get("metric") or {}
        sample = None
        if series.get("value"):
            sample = series["value"]
        elif series.get("values"):
            sample = _representative_range_sample(query_name, series["values"])
        if not sample:
            continue
        ts, value = sample
        service = metric.get("app") or metric.get("job") or metric.get("service") or "unknown"
        severity = _metric_severity(query_name, value)
        out.append(_evidence(
            "prometheus",
            "metric",
            service,
            severity,
            f"{query_name}={value}",
            timestamp=datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(),
            values={"query_name": query_name, "value": value, "labels": metric, "promql": promql},
        ))
    return out


def _metric_severity(query_name: str, value: Any) -> str:
    try:
        numeric = float(value)
    except Exception:
        return "info"
    if query_name in {"target_up", "dependent_agent_last_upstream_status"} and numeric == 0:
        return "error"
    if query_name in {
        "http_5xx",
        "sample_agent_http_503",
        "dependent_agent_upstream_errors",
        "dependent_agent_cascade_failures",
    } and numeric > 0:
        return "error"
    if "threshold" in query_name and numeric > 0:
        return "warning"
    if "cpu_utilisation" in query_name and numeric >= 90:
        return "warning"
    return "info"


def _representative_range_sample(query_name: str, samples: list[list[Any]]) -> list[Any] | None:
    numeric_samples: list[tuple[float, list[Any]]] = []
    for sample in samples or []:
        if len(sample) < 2:
            continue
        try:
            numeric_samples.append((float(sample[1]), sample))
        except Exception:
            continue
    if not numeric_samples:
        return samples[-1] if samples else None
    if query_name in {"target_up", "dependent_agent_last_upstream_status"}:
        return min(numeric_samples, key=lambda item: item[0])[1]
    return max(numeric_samples, key=lambda item: item[0])[1]


def _langfuse_get(path: str) -> dict[str, Any]:
    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        raise ValueError("Langfuse credentials are not configured")
    token = base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()
    resp = requests.get(
        f"{LANGFUSE_HOST}{path}",
        headers={"Authorization": f"Basic {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _redact(obj: Any) -> Any:
    if not LANGFUSE_REDACT_INPUTS:
        return obj
    if isinstance(obj, dict):
        redacted = {}
        for key, value in obj.items():
            if key.lower() in {"input", "output", "prompt", "completion"}:
                redacted[key] = "[redacted]"
            else:
                redacted[key] = _redact(value)
        return redacted
    if isinstance(obj, list):
        return [_redact(item) for item in obj]
    return obj


def _evidence(
    source: str,
    kind: str,
    service: str,
    severity: str,
    summary: str,
    *,
    timestamp: str | None = None,
    raw_ref: str | None = None,
    values: dict[str, Any] | None = None,
    evidence_url: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "kind": kind,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "service": service,
        "severity": severity,
        "summary": summary,
        "raw_ref": raw_ref,
        "labels": {},
        "values": values or {},
        "evidence_url": evidence_url,
    }


def _safe_promql(query: str) -> str:
    q = str(query).strip()
    if not q:
        raise ValueError("query is required")
    if len(q) > 4000:
        raise ValueError("query too long")
    forbidden = re.compile(r"\b(drop|delete|insert|update|alter|admin)\b", re.I)
    if forbidden.search(q):
        raise ValueError("query contains forbidden keyword")
    return q


def _safe_service(service: str) -> str:
    value = str(service).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
        raise ValueError(f"invalid service: {service}")
    return value


def _safe_trace_id(trace_id: str) -> str:
    value = str(trace_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", value):
        raise ValueError("invalid trace_id")
    return value


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _check_range(start: datetime, end: datetime) -> None:
    if end <= start:
        raise ValueError("end must be after start")
    if end - start > timedelta(minutes=PROM_MAX_RANGE_MINUTES):
        raise ValueError(f"range exceeds {PROM_MAX_RANGE_MINUTES} minutes")


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\r\n", b"\n"}:
            break
        key, _, value = line.decode().partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode())


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    sys.stdout.buffer.flush()


def _result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()
