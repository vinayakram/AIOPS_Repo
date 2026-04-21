"""
Thin client that forwards completed MedicalRAG traces to the AIops
Telemetry server (http://localhost:7000).

Called from main.py after each /api/query finishes — non-blocking,
never raises so it can never break the main request.
"""
import uuid
import logging
import threading
from datetime import datetime, timezone

import requests

from ..config import settings

logger = logging.getLogger("aiops.client")

APP_NAME = "medical-rag"

# Span colours are purely cosmetic — the span_type drives dashboard colouring
STEP_TYPE_MAP = {
    "query_validation":   "chain",
    "pubmed_fetch":       "retriever",
    "embedding":          "chain",
    "pagerank":           "chain",
    "faiss_retrieval":    "retriever",
    "openai_generation":  "llm",
}


def send_trace(ctx, result: dict, user_id: str = None, error: str = None):
    """
    Fire-and-forget: build the ingest payload from a TraceContext
    and post it to the AIops server in a background thread.

    Pass error=<message> when the pipeline raised an exception so the
    trace and failing span are marked with status='error'.
    """
    if not settings.AIOPS_ENABLED:
        return
    payload = _build_payload(ctx, result, user_id, error=error)
    threading.Thread(target=_post, args=(payload,), daemon=True).start()


def send_pod_threshold_breach(
    reason: str,
    cpu_percent: float,
    cpu_threshold_percent: float,
    memory_percent: float | None,
    memory_threshold_percent: float,
) -> None:
    """Emit a synthetic trace so AIops can ticket repeated pod breaches."""
    if not settings.AIOPS_ENABLED:
        return

    now = datetime.now(timezone.utc)
    trace_id = f"pod-threshold-{uuid.uuid4().hex}"
    metadata = {
        "scenario": "pod_threshold_breach",
        "pod_name": "medical-rag-agent",
        "cpu_percent": cpu_percent,
        "cpu_threshold_percent": cpu_threshold_percent,
        "memory_percent": memory_percent,
        "memory_threshold_percent": memory_threshold_percent,
        "recommended_fix": "Change the pod threshold config to raise POD_CPU_THRESHOLD_PERCENT and redeploy.",
    }
    payload = {
        "id": trace_id,
        "app_name": APP_NAME,
        "status": "error",
        "started_at": now.isoformat(),
        "ended_at": now.isoformat(),
        "total_duration_ms": 0,
        "input_preview": "pod resource guard",
        "output_preview": "application is not reachable",
        "metadata": metadata,
        "spans": [
            {
                "id": str(uuid.uuid4()),
                "trace_id": trace_id,
                "name": "pod_resource_guard",
                "span_type": "tool",
                "status": "error",
                "started_at": now.isoformat(),
                "ended_at": now.isoformat(),
                "duration_ms": 0,
                "input_preview": "cgroup pod resource sample",
                "output_preview": f"cpu={cpu_percent:.1f}% threshold={cpu_threshold_percent:.1f}%",
                "error_message": "application is not reachable",
                "metadata": metadata,
            }
        ],
        "logs": [
            {
                "trace_id": trace_id,
                "level": "ERROR",
                "logger": "pod_resource_guard",
                "message": "application is not reachable",
                "timestamp": now.isoformat(),
                "metadata": metadata,
            }
        ],
    }
    threading.Thread(target=_post, args=(payload,), daemon=True).start()


# ── Payload builder ───────────────────────────────────────────────────────────

def _build_payload(ctx, result: dict, user_id: str, error: str = None) -> dict:
    started_at = _ms_to_iso(ctx.start_ms)
    ended_at   = _ms_to_iso(ctx.start_ms + ctx.total_duration_ms)

    spans = []
    for step in ctx.steps.values():
        span_started = _ms_to_iso(step.start_ms)
        span_ended   = _ms_to_iso(step.end_ms) if step.end_ms else ended_at

        # A step with no end_ms was interrupted by an exception — mark it as error
        span_status = "error" if (error and not step.end_ms) else "ok"
        span_error_msg = error if span_status == "error" else None

        # pull token counts from openai_generation step output if present
        tokens_in = tokens_out = None
        model_name = None
        if step.name == "openai_generation":
            model_name = settings.OPENAI_MODEL
            usage = step.output.get("usage", {})
            tokens_in  = usage.get("prompt_tokens")
            tokens_out = usage.get("completion_tokens")

        spans.append({
            "id":             str(uuid.uuid4()),
            "trace_id":       ctx.trace_id,
            "name":           step.name,
            "span_type":      STEP_TYPE_MAP.get(step.name, "chain"),
            "status":         span_status,
            "started_at":     span_started,
            "ended_at":       span_ended,
            "duration_ms":    round(step.duration_ms, 1),
            "input_preview":  _preview(step.input),
            "output_preview": _preview(step.output),
            "error_message":  span_error_msg,
            "tokens_input":   tokens_in,
            "tokens_output":  tokens_out,
            "model_name":     model_name,
        })

    answer = result.get("answer", "")
    status = "error" if (error or not answer) else "ok"

    return {
        "id":                ctx.trace_id,
        "app_name":          APP_NAME,
        "user_id":           user_id,
        "status":            status,
        "started_at":        started_at,
        "ended_at":          ended_at,
        "total_duration_ms": round(ctx.total_duration_ms, 1),
        "input_preview":     ctx.query[:300],
        "output_preview":    answer[:300] if answer else f"ERROR: {error[:200]}" if error else None,
        "spans":             spans,
    }


# ── HTTP post ─────────────────────────────────────────────────────────────────

def _post(payload: dict):
    url = settings.AIOPS_SERVER_URL.rstrip("/") + "/api/ingest/trace"
    try:
        resp = requests.post(url, json=payload, timeout=5)
        if resp.status_code >= 400:
            logger.warning("AIops ingest failed: %s %s", resp.status_code, resp.text[:200])
        else:
            logger.debug("AIops trace %s sent", payload["id"][:8])
    except Exception as e:
        logger.debug("AIops send error (non-fatal): %s", e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ms_to_iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _preview(obj, max_len: int = 300) -> str:
    if not obj:
        return None
    import json
    try:
        s = json.dumps(obj, default=str)
    except Exception:
        s = str(obj)
    return s[:max_len]
