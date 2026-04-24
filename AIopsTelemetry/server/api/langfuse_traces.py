"""Proxy endpoint: fetch traces directly from Langfuse cloud API."""
import base64
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

from server.config import settings
from server.database.engine import SessionLocal
from server.database.models import Trace, Span

router = APIRouter(prefix="/langfuse", tags=["langfuse"])

_LF_BASE = "https://cloud.langfuse.com/api/public"


def _local_recent_traces(limit: int) -> list[dict]:
    """Recent local traces keep the dashboard useful when Langfuse is stale."""
    db = SessionLocal()
    try:
        rows = (
            db.query(Trace)
            .order_by(Trace.started_at.desc())
            .limit(limit)
            .all()
        )
        trace_ids = [row.id for row in rows]
        span_counts = {}
        if trace_ids:
            for trace_id, count in (
                db.query(Span.trace_id, Span.id)
                .filter(Span.trace_id.in_(trace_ids))
                .all()
            ):
                span_counts[trace_id] = span_counts.get(trace_id, 0) + 1

        normalized = []
        for trace in rows:
            normalized.append({
                "id": trace.id,
                "name": trace.app_name or "sample-agent",
                "timestamp": trace.started_at.isoformat() if trace.started_at else None,
                "latency_ms": round(trace.total_duration_ms or 0, 1),
                "status": trace.status or "ok",
                "user_id": trace.user_id,
                "session_id": trace.session_id,
                "tags": ["aiops-local"],
                "total_cost": None,
                "input": trace.input_preview,
                "output": trace.output_preview,
                "scores": [],
                "html_path": None,
                "observations_count": span_counts.get(trace.id, 0),
            })
        return normalized
    finally:
        db.close()


def _auth_header() -> str:
    token = base64.b64encode(
        f"{settings.LANGFUSE_PUBLIC_KEY}:{settings.LANGFUSE_SECRET_KEY}".encode()
    ).decode()
    return f"Basic {token}"


@router.get("/traces")
async def get_langfuse_traces(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    name: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
):
    if not settings.LANGFUSE_SECRET_KEY or not settings.LANGFUSE_PUBLIC_KEY:
        raise HTTPException(503, "Langfuse credentials not configured")

    params = {"page": page, "limit": limit}
    if name:
        params["name"] = name
    if user_id:
        params["userId"] = user_id

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_LF_BASE}/traces",
            params=params,
            headers={"Authorization": _auth_header()},
        )

    if resp.status_code != 200:
        local = _local_recent_traces(limit)
        if local:
            return {
                "traces": local,
                "meta": {
                    "page": page,
                    "limit": limit,
                    "total": len(local),
                    "total_pages": 1,
                    "source": "aiops-local-fallback",
                    "langfuse_error": resp.text[:200],
                },
            }
        raise HTTPException(resp.status_code, f"Langfuse error: {resp.text[:200]}")

    data = resp.json()
    traces = data.get("data", [])
    meta = data.get("meta", {})

    # Normalise into a shape the dashboard can consume
    normalized = []
    for t in traces:
        # derive status from scores or name prefix
        sev_in_name = t.get("name", "").startswith("[SEV")
        status = "error" if sev_in_name else "ok"
        normalized.append({
            "id": t["id"],
            "name": t.get("name") or "—",
            "timestamp": t.get("timestamp"),
            "latency_ms": round((t.get("latency") or 0) * 1000, 1),
            "status": status,
            "user_id": t.get("userId"),
            "session_id": t.get("sessionId"),
            "tags": t.get("tags", []),
            "total_cost": t.get("totalCost"),
            "input": t.get("input"),
            "output": t.get("output"),
            "scores": t.get("scores", []),
            "html_path": t.get("htmlPath"),
            "observations_count": len(t.get("observations", [])),
        })

    # Merge recent local traces first. This keeps the dashboard current for
    # synthetic pod-threshold traces while retaining Langfuse traces/links.
    merged = []
    seen = set()
    for trace in _local_recent_traces(min(limit, 25)) + normalized:
        trace_id = trace.get("id")
        if trace_id in seen:
            continue
        seen.add(trace_id)
        merged.append(trace)
        if len(merged) >= limit:
            break

    return {
        "traces": merged,
        "meta": {
            "page": meta.get("page", page),
            "limit": meta.get("limit", limit),
            "total": meta.get("totalItems", len(normalized)),
            "total_pages": meta.get("totalPages", 1),
            "source": "langfuse+aiops-local",
        },
    }


@router.get("/traces/{trace_id}")
async def get_langfuse_trace(trace_id: str):
    if not settings.LANGFUSE_SECRET_KEY or not settings.LANGFUSE_PUBLIC_KEY:
        raise HTTPException(503, "Langfuse credentials not configured")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_LF_BASE}/traces/{trace_id}",
            headers={"Authorization": _auth_header()},
        )

    if resp.status_code == 404:
        raise HTTPException(404, "Trace not found")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Langfuse error: {resp.text[:200]}")

    return resp.json()
