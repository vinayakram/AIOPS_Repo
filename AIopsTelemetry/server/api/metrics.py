from typing import Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from server.database.engine import get_db
from server.database.models import Trace, Span, Issue

router = APIRouter(prefix="/metrics", tags=["metrics"])

# ── Cost table: (model key, price_per_1M_input, price_per_1M_output) ─────────
_COST_TABLE = [
    ("gpt-5-nano",     0.15,   0.60),
    ("gpt-4o-mini",    0.15,   0.60),
    ("gpt-4o",         2.50,  10.00),
    ("gpt-4-turbo",   10.00,  30.00),
    ("gpt-4",         30.00,  60.00),
    ("gpt-3.5-turbo",  0.50,   1.50),
    ("claude-haiku",   0.80,   4.00),
    ("claude-sonnet",  3.00,  15.00),
    ("claude-opus",   15.00,  75.00),
]


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    m = (model or "").lower()
    pi, po = 2.0, 8.0  # fallback defaults
    for key, price_in, price_out in _COST_TABLE:
        if key in m:
            pi, po = price_in, price_out
            break
    return round((tokens_in * pi + tokens_out * po) / 1_000_000, 5)


def _percentile(sorted_vals: list[float], pct: int) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * pct / 100), len(sorted_vals) - 1)
    return round(sorted_vals[idx], 1)


def _trace_ids_for_app(db: Session, app_name: str) -> list[str]:
    return [r[0] for r in db.query(Trace.id).filter(Trace.app_name == app_name).all()]


# ── Existing endpoints (unchanged) ───────────────────────────────────────────

@router.get("/latency")
def latency_metrics(
    app_name: Optional[str] = Query(None),
    span_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """p50 / p95 / p99 / avg latency per span."""
    q = db.query(Span.name, Span.duration_ms).filter(Span.duration_ms.isnot(None))
    if app_name:
        q = q.filter(Span.trace_id.in_(_trace_ids_for_app(db, app_name)))
    if span_name:
        q = q.filter(Span.name == span_name)

    by_span: dict[str, list[float]] = {}
    for name, dur in q.all():
        by_span.setdefault(name, []).append(dur)

    result = []
    for name, durations in sorted(by_span.items()):
        durations.sort()
        n = len(durations)
        result.append({
            "span_name": name,
            "count": n,
            "avg_ms": round(sum(durations) / n, 1),
            "p50_ms": _percentile(durations, 50),
            "p95_ms": _percentile(durations, 95),
            "p99_ms": _percentile(durations, 99),
        })
    return {"latency": result}


@router.get("/errors")
def error_metrics(
    app_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Error count and rate by span."""
    q_total  = db.query(Span.name, func.count(Span.id)).group_by(Span.name)
    q_errors = db.query(Span.name, func.count(Span.id)).filter(
        Span.status == "error"
    ).group_by(Span.name)

    if app_name:
        ids = _trace_ids_for_app(db, app_name)
        q_total  = q_total.filter(Span.trace_id.in_(ids))
        q_errors = q_errors.filter(Span.trace_id.in_(ids))

    totals = {name: cnt for name, cnt in q_total.all()}
    errors = {name: cnt for name, cnt in q_errors.all()}

    result = []
    for name, total in sorted(totals.items()):
        err = errors.get(name, 0)
        result.append({
            "span_name": name,
            "total": total,
            "errors": err,
            "error_rate_pct": round(err / total * 100, 1) if total else 0,
        })
    return {"errors": result}


@router.get("/issues-summary")
def issues_summary(db: Session = Depends(get_db)):
    rows = db.query(Issue.severity, Issue.status, func.count(Issue.id)).group_by(
        Issue.severity, Issue.status
    ).all()
    return {
        "summary": [
            {"severity": sev, "status": stat, "count": cnt}
            for sev, stat, cnt in rows
        ]
    }


# ── New endpoints ─────────────────────────────────────────────────────────────

@router.get("/overview")
def overview(
    app_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Top-level KPIs: traces, latency, error rate, active issues, token usage, cost."""
    q = db.query(Trace)
    if app_name:
        q = q.filter(Trace.app_name == app_name)
    traces = q.all()

    total     = len(traces)
    errors    = sum(1 for t in traces if t.status == "error")
    durations = sorted(t.total_duration_ms for t in traces if t.total_duration_ms)

    span_q = db.query(Span.tokens_input, Span.tokens_output, Span.model_name).filter(
        Span.span_type == "llm"
    )
    if app_name:
        span_q = span_q.filter(Span.trace_id.in_([t.id for t in traces]))
    llm_spans  = span_q.all()
    tokens_in  = sum(s[0] or 0 for s in llm_spans)
    tokens_out = sum(s[1] or 0 for s in llm_spans)
    total_cost = sum(_estimate_cost(s[2], s[0] or 0, s[1] or 0) for s in llm_spans)

    active_issues = db.query(func.count(Issue.id)).filter(
        Issue.status.in_(["OPEN", "ACKNOWLEDGED", "ESCALATED"])
    ).scalar() or 0

    return {
        "total_traces":      total,
        "avg_latency_ms":    round(sum(durations) / len(durations), 1) if durations else 0,
        "p95_latency_ms":    _percentile(durations, 95),
        "error_rate_pct":    round(errors / total * 100, 1) if total else 0,
        "active_issues":     active_issues,
        "tokens_in":         tokens_in,
        "tokens_out":        tokens_out,
        "estimated_cost_usd": round(total_cost, 4),
    }


@router.get("/per-app")
def per_app(db: Session = Depends(get_db)):
    """Per-app health: traces, latency, error rate, tokens, cost, open issues."""
    apps = sorted({r[0] for r in db.query(Trace.app_name).distinct().all() if r[0]})
    result = []
    for app in apps:
        traces = db.query(Trace).filter(Trace.app_name == app).all()
        total  = len(traces)
        if not total:
            continue
        errors    = sum(1 for t in traces if t.status == "error")
        durations = sorted(t.total_duration_ms for t in traces if t.total_duration_ms)
        ids       = [t.id for t in traces]

        llm_spans = db.query(
            Span.tokens_input, Span.tokens_output, Span.model_name
        ).filter(Span.trace_id.in_(ids), Span.span_type == "llm").all()
        tokens_in  = sum(s[0] or 0 for s in llm_spans)
        tokens_out = sum(s[1] or 0 for s in llm_spans)
        cost       = sum(_estimate_cost(s[2], s[0] or 0, s[1] or 0) for s in llm_spans)

        open_issues = db.query(func.count(Issue.id)).filter(
            Issue.app_name == app,
            Issue.status.in_(["OPEN", "ACKNOWLEDGED", "ESCALATED"]),
        ).scalar() or 0

        result.append({
            "app_name":           app,
            "total_traces":       total,
            "error_count":        errors,
            "error_rate_pct":     round(errors / total * 100, 1),
            "avg_ms":             round(sum(durations) / len(durations), 1) if durations else 0,
            "p95_ms":             _percentile(durations, 95),
            "tokens_in":          tokens_in,
            "tokens_out":         tokens_out,
            "estimated_cost_usd": round(cost, 4),
            "open_issues":        open_issues,
        })
    return {"apps": result}


@router.get("/throughput")
def throughput(
    app_name: Optional[str] = Query(None),
    bucket_minutes: int = Query(5, ge=1, le=60),
    limit_buckets:  int = Query(24, ge=6, le=100),
    db: Session = Depends(get_db),
):
    """Time-bucketed trace count, error rate and avg latency for trend charts."""
    cutoff = datetime.utcnow() - timedelta(minutes=bucket_minutes * limit_buckets)
    q = db.query(Trace.started_at, Trace.total_duration_ms, Trace.status).filter(
        Trace.started_at >= cutoff
    )
    if app_name:
        q = q.filter(Trace.app_name == app_name)

    bucket_sec = bucket_minutes * 60
    buckets: dict[int, dict] = {}
    for started_at, duration_ms, status in q.all():
        if not started_at:
            continue
        ts = int(started_at.timestamp() // bucket_sec) * bucket_sec
        b  = buckets.setdefault(ts, {"count": 0, "errors": 0, "durations": []})
        b["count"] += 1
        if status == "error":
            b["errors"] += 1
        if duration_ms:
            b["durations"].append(duration_ms)

    result = []
    for ts in sorted(buckets.keys()):
        b    = buckets[ts]
        durs = b["durations"]
        result.append({
            "ts":             ts,
            "label":          datetime.utcfromtimestamp(ts).strftime("%H:%M"),
            "count":          b["count"],
            "errors":         b["errors"],
            "error_rate_pct": round(b["errors"] / b["count"] * 100, 1) if b["count"] else 0,
            "avg_ms":         round(sum(durs) / len(durs), 1) if durs else 0,
        })
    return {"buckets": result, "bucket_minutes": bucket_minutes}


@router.get("/tokens")
def token_metrics(
    app_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Token usage and cost estimate grouped by model."""
    q = db.query(
        Span.model_name,
        func.count(Span.id),
        func.sum(Span.tokens_input),
        func.sum(Span.tokens_output),
    ).filter(Span.span_type == "llm").group_by(Span.model_name)

    if app_name:
        q = q.filter(Span.trace_id.in_(_trace_ids_for_app(db, app_name)))

    result = []
    for model_name, calls, tin, tout in q.all():
        tin  = tin  or 0
        tout = tout or 0
        result.append({
            "model":              model_name or "unknown",
            "calls":              calls,
            "tokens_in":          tin,
            "tokens_out":         tout,
            "estimated_cost_usd": _estimate_cost(model_name, tin, tout),
        })
    result.sort(key=lambda r: r["tokens_in"] + r["tokens_out"], reverse=True)
    return {"models": result}


@router.get("/system/current")
def system_current():
    """Latest system health snapshot averaged over the last ~1 minute of samples."""
    try:
        from server.engine.metrics_collector import get_recent_snapshots
        snapshots = get_recent_snapshots(n=6)
    except Exception:
        return {"available": False}

    if not snapshots:
        return {"available": False}

    def _avg(key: str):
        vals = [s.get(key) for s in snapshots if s.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    latest = snapshots[-1]
    return {
        "available":              True,
        "collected_at":           latest.get("collected_at"),
        "cpu_percent":            _avg("cpu_percent"),
        "mem_percent":            _avg("mem_percent"),
        "mem_used_mb":            _avg("mem_used_mb"),
        "swap_percent":           _avg("swap_percent"),
        "disk_read_mb_s":         _avg("disk_read_mb_s"),
        "disk_write_mb_s":        _avg("disk_write_mb_s"),
        "net_recv_mb_s":          _avg("net_recv_mb_s"),
        "net_sent_mb_s":          _avg("net_sent_mb_s"),
        "net_active_connections":  _avg("net_active_connections"),
        "process_count":          latest.get("process_count"),
    }
