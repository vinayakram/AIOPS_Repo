"""
Incidents API returning recent traces formatted for consumption by the
Invastigate_flow_with_Poller background poller (AIOpsPoller). Exposes
GET /api/v1/incidents with configurable time window and result limit.
The poller performs trace_id deduplication, so returning all recent traces is safe.

Invastigate_flow_with_Pollerバックグラウンドポーラー（AIOpsPoller）が消費する形式で
直近トレースを返すインシデントAPIモジュール。時間ウィンドウと件数上限を指定可能な
GET /api/v1/incidentsエンドポイントを公開する。
ポーラー側でtrace_idの重複排除を行うため、直近の全トレースを返す単純な実装で安全に運用できる。
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
