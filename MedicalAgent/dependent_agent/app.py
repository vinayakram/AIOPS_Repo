from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

try:
    from langfuse import Langfuse
    try:
        from langfuse.types import TraceContext as LFTraceContext
        LANGFUSE_V4 = True
    except ImportError:
        LANGFUSE_V4 = False
    LANGFUSE_AVAILABLE = True
except ImportError:
    LANGFUSE_AVAILABLE = False
    LANGFUSE_V4 = False


APP_NAME = os.getenv("DEPENDENT_AGENT_NAME", "triage-agent")
UPSTREAM_SERVICE = os.getenv("UPSTREAM_SERVICE_NAME", "sample-agent")
SAMPLE_AGENT_URL = os.getenv("SAMPLE_AGENT_URL", "http://medical-rag-pod:8002").rstrip("/")
AIOPS_SERVER_URL = os.getenv("AIOPS_SERVER_URL", "http://host.docker.internal:7000").rstrip("/")
AIOPS_ENABLED = os.getenv("AIOPS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "3"))

LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY")

CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.http_requests: dict[tuple[str, str, str], int] = {}
        self.upstream_requests: dict[tuple[str, str, str], int] = {}
        self.upstream_durations: dict[tuple[str, str], list[float]] = {}
        self.cascade_failures: dict[tuple[str], int] = {}
        self.last_upstream_status: float = 1

    @contextmanager
    def http(self, method: str, path: str) -> Iterator[dict[str, int]]:
        state = {"status": 500}
        try:
            yield state
        finally:
            with self._lock:
                key = (method, self._normalize_path(path), str(state["status"]))
                self.http_requests[key] = self.http_requests.get(key, 0) + 1

    def observe_upstream(self, status_class: str, result: str, elapsed: float) -> None:
        with self._lock:
            req_key = (UPSTREAM_SERVICE, status_class, result)
            self.upstream_requests[req_key] = self.upstream_requests.get(req_key, 0) + 1
            self.upstream_durations.setdefault((UPSTREAM_SERVICE, result), []).append(elapsed)
            self.last_upstream_status = 1 if result == "ok" else 0
            if result != "ok":
                self.cascade_failures[(UPSTREAM_SERVICE,)] = self.cascade_failures.get((UPSTREAM_SERVICE,), 0) + 1

    def response(self) -> Response:
        with self._lock:
            http_requests = dict(self.http_requests)
            upstream_requests = dict(self.upstream_requests)
            upstream_durations = {k: list(v) for k, v in self.upstream_durations.items()}
            cascade_failures = dict(self.cascade_failures)
            last_upstream_status = self.last_upstream_status

        lines: list[str] = []
        _append_counter(
            lines,
            "dependent_agent_http_requests_total",
            "Total HTTP requests served by the dependent agent.",
            http_requests,
            ("method", "path", "status"),
        )
        _append_counter(
            lines,
            "dependent_agent_upstream_requests_total",
            "Total calls from the dependent agent to upstream services.",
            upstream_requests,
            ("upstream", "status_class", "result"),
        )
        _append_histogram(
            lines,
            "dependent_agent_upstream_request_duration_seconds",
            "Latency for dependent-agent upstream calls.",
            upstream_durations,
            (0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13),
            ("upstream", "result"),
        )
        _append_counter(
            lines,
            "dependent_agent_cascade_failures_total",
            "Failures in the dependent agent attributed to an upstream dependency.",
            cascade_failures,
            ("upstream",),
        )
        _append_gauge(
            lines,
            "dependent_agent_last_upstream_status",
            "Last observed upstream status, 1 for healthy and 0 for failed.",
            {"app": APP_NAME, "upstream": UPSTREAM_SERVICE},
            last_upstream_status,
        )
        return Response("\n".join(lines) + "\n", media_type=CONTENT_TYPE_LATEST)

    @staticmethod
    def _normalize_path(path: str) -> str:
        if path.startswith("/api/"):
            return path
        if path == "/metrics":
            return path
        return "/page"


metrics = Metrics()


class LangfuseRecorder:
    def __init__(self) -> None:
        self.enabled = False
        self._client = None
        if not (LANGFUSE_AVAILABLE and LANGFUSE_V4):
            print("[DependentAgent] Langfuse SDK v4 not available; remote tracing disabled")
            return
        if not (LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY):
            print("[DependentAgent] Langfuse keys not configured; remote tracing disabled")
            return
        try:
            self._client = Langfuse(
                secret_key=LANGFUSE_SECRET_KEY,
                public_key=LANGFUSE_PUBLIC_KEY,
                host=LANGFUSE_HOST,
            )
            self.enabled = True
            print(f"[DependentAgent] Langfuse connected to {LANGFUSE_HOST}")
        except Exception as exc:
            print(f"[DependentAgent] Langfuse init failed: {exc}")

    def record_upstream_check(
        self,
        *,
        trace_id: str,
        upstream_status: int | None,
        result: str,
        error: str | None,
        duration_ms: float,
        scenario: str,
    ) -> None:
        if not (self.enabled and self._client):
            return
        metadata = {
            "app": APP_NAME,
            "scenario": scenario,
            "dependency": UPSTREAM_SERVICE,
            "upstream_url": SAMPLE_AGENT_URL,
            "upstream_status": upstream_status,
            "duration_ms": round(duration_ms, 1),
        }
        root_cm = span_cm = None
        try:
            root_cm = self._client.start_as_current_observation(
                trace_context=LFTraceContext(trace_id=trace_id),
                name=APP_NAME,
                as_type="span",
                input={"operation": "upstream_health_dependency_check"},
                metadata={**metadata, "tags": [APP_NAME, "cascade", "mcp-evidence"]},
            )
            root_obs = root_cm.__enter__()
            span_cm = self._client.start_as_current_observation(
                name=f"call_{UPSTREAM_SERVICE}",
                as_type="span",
                input={"url": f"{SAMPLE_AGENT_URL}/chat"},
                metadata=metadata,
            )
            span_obs = span_cm.__enter__()
            level = "ERROR" if result != "ok" else "DEFAULT"
            status_message = error or f"{UPSTREAM_SERVICE} returned HTTP {upstream_status}"
            if hasattr(span_obs, "update"):
                span_obs.update(
                    level=level,
                    status_message=status_message if result != "ok" else None,
                    output={"result": result, "upstream_status": upstream_status, "error": error},
                )
            span_cm.__exit__(None, None, None)
            span_cm = None
            if hasattr(root_obs, "update"):
                root_obs.update(
                    level=level,
                    status_message=status_message if result != "ok" else None,
                    output={"result": result, "dependency": UPSTREAM_SERVICE},
                    metadata=metadata,
                )
            root_cm.__exit__(None, None, None)
            root_cm = None
            self._client.flush()
        except Exception as exc:
            print(f"[DependentAgent] Langfuse record failed: {exc}")
            for cm in (span_cm, root_cm):
                if cm:
                    try:
                        cm.__exit__(None, None, None)
                    except Exception:
                        pass


langfuse = LangfuseRecorder()


app = FastAPI(
    title="TriageAgent",
    description="Dependent agent used to demonstrate cross-service AIOps RCA.",
    version="1.0.0",
)


@app.middleware("http")
async def count_http_requests(request, call_next):
    with metrics.http(request.method, request.url.path) as state:
        response = await call_next(request)
        state["status"] = response.status_code
        return response


@app.get("/")
async def root():
    return {
        "app": APP_NAME,
        "upstream": UPSTREAM_SERVICE,
        "sample_agent_url": SAMPLE_AGENT_URL,
        "langfuse_enabled": langfuse.enabled,
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "app": APP_NAME,
        "upstream": UPSTREAM_SERVICE,
        "langfuse_enabled": langfuse.enabled,
    }


@app.post("/api/run-cascade")
async def run_cascade(
    scenario: str = Query("sample-agent-threshold-cascade"),
    fail_on_upstream_error: bool = Query(True),
):
    started = time.perf_counter()
    trace_id = uuid.uuid4().hex
    upstream_status: int | None = None
    upstream_body = ""
    error: str | None = None
    result = "ok"

    try:
        response = requests.get(f"{SAMPLE_AGENT_URL}/chat", timeout=UPSTREAM_TIMEOUT_SECONDS)
        upstream_status = response.status_code
        upstream_body = response.text[:500]
        if response.status_code >= 500:
            result = "upstream_error"
            error = f"{UPSTREAM_SERVICE} returned HTTP {response.status_code}: {upstream_body[:180]}"
        elif response.status_code >= 400:
            result = "upstream_rejected"
            error = f"{UPSTREAM_SERVICE} returned HTTP {response.status_code}: {upstream_body[:180]}"
    except requests.Timeout:
        result = "upstream_timeout"
        error = f"{UPSTREAM_SERVICE} timed out after {UPSTREAM_TIMEOUT_SECONDS}s"
    except requests.RequestException as exc:
        result = "upstream_unreachable"
        error = f"{UPSTREAM_SERVICE} request failed: {exc}"

    elapsed = time.perf_counter() - started
    status_class = f"{int(upstream_status / 100)}xx" if upstream_status else "network"
    metrics.observe_upstream(status_class, result, elapsed)
    langfuse.record_upstream_check(
        trace_id=trace_id,
        upstream_status=upstream_status,
        result=result,
        error=error,
        duration_ms=elapsed * 1000,
        scenario=scenario,
    )
    _send_aiops_trace(
        trace_id=trace_id,
        scenario=scenario,
        started_at=time.time() - elapsed,
        duration_ms=elapsed * 1000,
        upstream_status=upstream_status,
        result=result,
        error=error,
        upstream_body=upstream_body,
    )

    payload = {
        "trace_id": trace_id,
        "result": result,
        "upstream": UPSTREAM_SERVICE,
        "upstream_status": upstream_status,
        "duration_ms": round(elapsed * 1000, 1),
        "error": error,
        "langfuse_url": f"{LANGFUSE_HOST.rstrip('/')}/trace/{trace_id}" if langfuse.enabled else None,
    }
    if error and fail_on_upstream_error:
        raise HTTPException(status_code=502, detail=payload)
    return payload


@app.get("/metrics")
async def metrics_endpoint():
    return metrics.response()


def _send_aiops_trace(
    *,
    trace_id: str,
    scenario: str,
    started_at: float,
    duration_ms: float,
    upstream_status: int | None,
    result: str,
    error: str | None,
    upstream_body: str,
) -> None:
    if not AIOPS_ENABLED:
        return
    status = "error" if error else "ok"
    started = datetime.fromtimestamp(started_at, tz=timezone.utc).isoformat()
    ended = datetime.fromtimestamp(started_at + duration_ms / 1000, tz=timezone.utc).isoformat()
    metadata = {
        "scenario": scenario,
        "dependency": UPSTREAM_SERVICE,
        "upstream_url": SAMPLE_AGENT_URL,
        "upstream_status": upstream_status,
        "cascade_candidate_root_cause": UPSTREAM_SERVICE if error else None,
    }
    payload = {
        "id": trace_id,
        "app_name": APP_NAME,
        "status": status,
        "started_at": started,
        "ended_at": ended,
        "total_duration_ms": round(duration_ms, 1),
        "input_preview": f"call dependency {UPSTREAM_SERVICE}",
        "output_preview": error or f"{UPSTREAM_SERVICE} dependency healthy",
        "metadata": metadata,
        "spans": [
            {
                "id": str(uuid.uuid4()),
                "trace_id": trace_id,
                "name": f"call_{UPSTREAM_SERVICE}",
                "span_type": "tool",
                "status": status,
                "started_at": started,
                "ended_at": ended,
                "duration_ms": round(duration_ms, 1),
                "input_preview": f"GET {SAMPLE_AGENT_URL}/chat",
                "output_preview": (error or upstream_body or "ok")[:300],
                "error_message": error,
                "metadata": metadata,
            }
        ],
        "logs": [
            {
                "trace_id": trace_id,
                "level": "ERROR" if error else "INFO",
                "logger": "dependent_agent.cascade",
                "message": error or f"{UPSTREAM_SERVICE} dependency healthy",
                "timestamp": ended,
                "metadata": metadata,
            }
        ],
    }
    threading.Thread(target=_post_aiops, args=(payload,), daemon=True).start()


def _post_aiops(payload: dict[str, Any]) -> None:
    try:
        resp = requests.post(f"{AIOPS_SERVER_URL}/api/ingest/trace", json=payload, timeout=5)
        if resp.status_code >= 400:
            print(f"[DependentAgent] AIops ingest failed: {resp.status_code} {resp.text[:200]}")
    except Exception as exc:
        print(f"[DependentAgent] AIops ingest error: {exc}")


def _append_counter(
    lines: list[str],
    name: str,
    help_text: str,
    values: dict[tuple[str, ...], int],
    label_names: tuple[str, ...],
) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} counter")
    for label_values, value in values.items():
        lines.append(f"{name}{_labels(dict(zip(label_names, label_values)))} {value}")


def _append_gauge(lines: list[str], name: str, help_text: str, labels: dict[str, str], value: float) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name}{_labels(labels)} {value}")


def _append_histogram(
    lines: list[str],
    name: str,
    help_text: str,
    values: dict[tuple[str, ...], list[float]],
    buckets: tuple[float, ...],
    label_names: tuple[str, ...],
) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} histogram")
    for label_values, samples in values.items():
        base_labels = dict(zip(label_names, label_values))
        sorted_samples = sorted(samples)
        for bucket in buckets:
            count = sum(1 for sample in sorted_samples if sample <= bucket)
            lines.append(f'{name}_bucket{_labels({**base_labels, "le": _fmt(bucket)})} {count}')
        lines.append(f'{name}_bucket{_labels({**base_labels, "le": "+Inf"})} {len(sorted_samples)}')
        lines.append(f"{name}_count{_labels(base_labels)} {len(sorted_samples)}")
        lines.append(f"{name}_sum{_labels(base_labels)} {sum(sorted_samples) if sorted_samples else 0}")


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape(str(value))}"' for key, value in labels.items()) + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)
