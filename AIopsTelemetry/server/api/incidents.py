"""
Incidents API
=============
Returns recent traces in the format consumed by the Invastigate_flow_with_Poller
background poller (AIOpsPoller).

Endpoint:  GET /api/v1/incidents?since_minutes=30&limit=100

Response shape:
  {
    "incidents": [
      {"trace_id": "...", "timestamp": "2026-...", "agent_name": "medical-rag"},
      ...
    ]
  }

The poller deduplicates by trace_id on its side, so returning all recent
traces — not just new ones — is safe and simpler.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from server.database.engine import get_db
from server.database.models import Trace

router = APIRouter(prefix="/v1", tags=["incidents"])


@router.get("/incidents")
def list_incidents(
    since_minutes: int = Query(30, ge=1, le=1440, description="Lookback window in minutes"),
    limit: int = Query(200, ge=1, le=1000, description="Max incidents to return"),
    app_name: str = Query(None, description="Filter by app name"),
    db: Session = Depends(get_db),
):
    """
    Return recent traces as incident records for the Invastigate poller.
    Only traces with a valid started_at within the lookback window are included.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=since_minutes)
    q = (
        db.query(Trace)
        .filter(Trace.started_at >= cutoff)
    )
    if app_name:
        q = q.filter(Trace.app_name == app_name)

    rows = q.order_by(Trace.started_at.desc()).limit(limit).all()

    incidents = [
        {
            "trace_id":   t.id,
            "timestamp":  (
                t.started_at.isoformat() if t.started_at
                else datetime.utcnow().isoformat()
            ),
            "agent_name": t.app_name,
        }
        for t in rows
    ]
    return {"incidents": incidents, "count": len(incidents)}
