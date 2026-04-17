"""Proxy endpoint: fetch traces directly from Langfuse cloud API."""
import base64
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

from server.config import settings

router = APIRouter(prefix="/langfuse", tags=["langfuse"])

_LF_BASE = "https://cloud.langfuse.com/api/public"


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

    return {
        "traces": normalized,
        "meta": {
            "page": meta.get("page", page),
            "limit": meta.get("limit", limit),
            "total": meta.get("totalItems", len(normalized)),
            "total_pages": meta.get("totalPages", 1),
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
