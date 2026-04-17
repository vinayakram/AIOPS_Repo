import json
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from server.database.engine import get_db
from server.database.models import Trace, Span, TraceLog, SystemMetric

router = APIRouter(prefix="/traces", tags=["traces"])


@router.get("")
def list_traces(
    app_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(Trace)
    if app_name:
        q = q.filter(Trace.app_name == app_name)
    if status:
        q = q.filter(Trace.status == status)
    total = q.count()
    traces = q.order_by(desc(Trace.started_at)).offset(offset).limit(limit).all()
    return {"total": total, "traces": [_trace_dict(t) for t in traces]}


@router.get("/stats")
def trace_stats(app_name: Optional[str] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Trace)
    if app_name:
        q = q.filter(Trace.app_name == app_name)
    total = q.count()
    avg_latency = db.query(func.avg(Trace.total_duration_ms)).filter(
        Trace.total_duration_ms != None
    )
    if app_name:
        avg_latency = avg_latency.filter(Trace.app_name == app_name)
    avg_latency = avg_latency.scalar() or 0

    error_count = q.filter(Trace.status == "error").count()
    error_rate = round(error_count / total * 100, 1) if total else 0

    apps = [r[0] for r in db.query(Trace.app_name).distinct().all()]
    return {
        "total_traces": total,
        "avg_latency_ms": round(avg_latency, 1),
        "error_count": error_count,
        "error_rate_pct": error_rate,
        "apps": apps,
    }


@router.get("/{trace_id}")
def get_trace(trace_id: str, db: Session = Depends(get_db)):
    trace = db.query(Trace).filter(Trace.id == trace_id).first()
    if not trace:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Trace not found")
    spans = db.query(Span).filter(Span.trace_id == trace_id).order_by(Span.started_at).all()
    result = _trace_dict(trace)
    result["spans"] = [_span_dict(s) for s in spans]
    return result


@router.get("/{trace_id}/profile")
def get_trace_profile(
    trace_id: str,
    at: Optional[str] = Query(None, description="ISO timestamp fallback when trace not in local DB"),
    window: int = Query(60, ge=5, le=600, description="Seconds before/after trace to include metrics"),
    db: Session = Depends(get_db),
):
    """Return system metric snapshots correlated with a trace's time window.

    Looks up the trace in the local DB to obtain started_at/ended_at.
    If the trace is not in local DB, the caller may supply ?at=<iso> as a
    fallback anchor timestamp (e.g. from Langfuse). Returns snapshots plus
    a summary with min/avg/max for CPU, memory, disk and network.
    """
    trace = db.query(Trace).filter(Trace.id == trace_id).first()

    if trace:
        anchor_start = trace.started_at
        anchor_end = trace.ended_at or trace.started_at
    elif at:
        try:
            anchor_start = datetime.fromisoformat(at.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid 'at' timestamp format")
        anchor_end = anchor_start
    else:
        raise HTTPException(status_code=404, detail="Trace not found")

    pad = timedelta(seconds=window)
    cutoff_start = anchor_start - pad
    cutoff_end = anchor_end + pad

    rows = (
        db.query(SystemMetric)
        .filter(
            SystemMetric.collected_at >= cutoff_start,
            SystemMetric.collected_at <= cutoff_end,
        )
        .order_by(SystemMetric.collected_at)
        .all()
    )

    snapshots = [_metric_dict(r) for r in rows]
    summary = _compute_summary(rows)

    return {
        "trace_id": trace_id,
        "started_at": anchor_start.isoformat() if anchor_start else None,
        "ended_at": anchor_end.isoformat() if anchor_end else None,
        "window_seconds": window,
        "snapshots": snapshots,
        "summary": summary,
    }


def _compute_summary(rows: list) -> dict:
    if not rows:
        return {}

    def _stats(vals: list) -> dict:
        vals = [v for v in vals if v is not None]
        if not vals:
            return {"min": None, "avg": None, "max": None}
        return {
            "min": round(min(vals), 2),
            "avg": round(sum(vals) / len(vals), 2),
            "max": round(max(vals), 2),
        }

    return {
        "cpu": _stats([r.cpu_percent for r in rows]),
        "mem": _stats([r.mem_percent for r in rows]),
        "mem_used_mb": _stats([r.mem_used_mb for r in rows]),
        "disk": {
            "read_mb_s":  _stats([r.disk_read_bytes_sec / 1_048_576 if r.disk_read_bytes_sec else 0 for r in rows]),
            "write_mb_s": _stats([r.disk_write_bytes_sec / 1_048_576 if r.disk_write_bytes_sec else 0 for r in rows]),
        },
        "net": {
            "recv_mb_s": _stats([r.net_bytes_recv_sec / 1_048_576 if r.net_bytes_recv_sec else 0 for r in rows]),
            "sent_mb_s": _stats([r.net_bytes_sent_sec / 1_048_576 if r.net_bytes_sent_sec else 0 for r in rows]),
        },
        "connections": _stats([r.net_active_connections for r in rows]),
        "processes":   _stats([r.process_count for r in rows]),
        "snapshot_count": len(rows),
    }


def _metric_dict(r: SystemMetric) -> dict:
    return {
        "collected_at": r.collected_at.isoformat() if r.collected_at else None,
        "cpu_percent": r.cpu_percent,
        "cpu_per_core": json.loads(r.cpu_per_core_json) if r.cpu_per_core_json else [],
        "cpu_freq_mhz": r.cpu_freq_mhz,
        "mem_percent": r.mem_percent,
        "mem_used_mb": round(r.mem_used_mb, 1) if r.mem_used_mb else None,
        "mem_available_mb": round(r.mem_available_mb, 1) if r.mem_available_mb else None,
        "swap_percent": r.swap_percent,
        "disk_read_mb_s": round(r.disk_read_bytes_sec / 1_048_576, 3) if r.disk_read_bytes_sec else 0,
        "disk_write_mb_s": round(r.disk_write_bytes_sec / 1_048_576, 3) if r.disk_write_bytes_sec else 0,
        "disk_read_iops": r.disk_read_iops,
        "disk_write_iops": r.disk_write_iops,
        "net_recv_mb_s": round(r.net_bytes_recv_sec / 1_048_576, 3) if r.net_bytes_recv_sec else 0,
        "net_sent_mb_s": round(r.net_bytes_sent_sec / 1_048_576, 3) if r.net_bytes_sent_sec else 0,
        "net_active_connections": r.net_active_connections,
        "process_count": r.process_count,
    }


@router.get("/{trace_id}/logs")
def get_trace_logs(trace_id: str, db: Session = Depends(get_db)):
    logs = (
        db.query(TraceLog)
        .filter(TraceLog.trace_id == trace_id)
        .order_by(TraceLog.timestamp)
        .all()
    )
    return {"logs": [_log_dict(l) for l in logs]}


def _log_dict(l: TraceLog) -> dict:
    return {
        "id": l.id,
        "trace_id": l.trace_id,
        "level": l.level,
        "logger": l.logger,
        "message": l.message,
        "timestamp": l.timestamp.isoformat() if l.timestamp else None,
    }


def _trace_dict(t: Trace) -> dict:
    return {
        "id": t.id,
        "app_name": t.app_name,
        "run_id": t.run_id,
        "session_id": t.session_id,
        "user_id": t.user_id,
        "status": t.status,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "ended_at": t.ended_at.isoformat() if t.ended_at else None,
        "total_duration_ms": t.total_duration_ms,
        "input_preview": t.input_preview,
        "output_preview": t.output_preview,
    }


def _span_dict(s: Span) -> dict:
    return {
        "id": s.id,
        "trace_id": s.trace_id,
        "parent_span_id": s.parent_span_id,
        "name": s.name,
        "span_type": s.span_type,
        "status": s.status,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "duration_ms": s.duration_ms,
        "input_preview": s.input_preview,
        "output_preview": s.output_preview,
        "error_message": s.error_message,
        "tokens_input": s.tokens_input,
        "tokens_output": s.tokens_output,
        "model_name": s.model_name,
    }
