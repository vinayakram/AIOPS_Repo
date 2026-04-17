import json
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.config import settings
from server.database.engine import get_db
from server.database.models import Trace, Span, TraceLog

router = APIRouter(prefix="/ingest", tags=["ingest"])


# ── Auth helper ────────────────────────────────────────────────────────────────

def _check_api_key(x_aiops_key: Optional[str] = Header(None)):
    if settings.API_KEY and x_aiops_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-AIops-Key header")


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class SpanIn(BaseModel):
    id: str
    trace_id: str
    parent_span_id: Optional[str] = None
    name: str
    span_type: str = "chain"
    status: str = "ok"
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[float] = None
    input_preview: Optional[str] = None
    output_preview: Optional[str] = None
    error_message: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    model_name: Optional[str] = None
    metadata: Optional[dict] = None


class LogIn(BaseModel):
    trace_id: str
    level: str = "INFO"
    logger: Optional[str] = None
    message: str
    timestamp: Optional[datetime] = None
    metadata: Optional[dict] = None


class TraceIn(BaseModel):
    id: str
    app_name: str
    run_id: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    status: str = "ok"
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    total_duration_ms: Optional[float] = None
    input_preview: Optional[str] = None
    output_preview: Optional[str] = None
    metadata: Optional[dict] = None
    spans: List[SpanIn] = []
    logs: List[LogIn] = []


class BatchIn(BaseModel):
    traces: List[TraceIn]


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/trace", dependencies=[Depends(_check_api_key)])
def ingest_trace(payload: TraceIn, db: Session = Depends(get_db)):
    _upsert_trace(db, payload)
    db.commit()
    return {"ok": True, "trace_id": payload.id}


@router.post("/batch", dependencies=[Depends(_check_api_key)])
def ingest_batch(payload: BatchIn, db: Session = Depends(get_db)):
    if len(payload.traces) > settings.MAX_INGEST_BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"Batch exceeds max size {settings.MAX_INGEST_BATCH_SIZE}")
    for trace in payload.traces:
        _upsert_trace(db, trace)
    db.commit()
    return {"ok": True, "count": len(payload.traces)}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _upsert_trace(db: Session, payload: TraceIn):
    existing = db.query(Trace).filter(Trace.id == payload.id).first()
    meta = json.dumps(payload.metadata) if payload.metadata else None
    if existing:
        for field, val in {
            "status":            payload.status,
            "started_at":        payload.started_at,   # allow refreshing timestamp
            "ended_at":          payload.ended_at,
            "total_duration_ms": payload.total_duration_ms,
            "output_preview":    payload.output_preview,
            "metadata_json":     meta,
        }.items():
            if val is not None:
                setattr(existing, field, val)
    else:
        db.add(Trace(
            id=payload.id,
            app_name=payload.app_name,
            run_id=payload.run_id,
            session_id=payload.session_id,
            user_id=payload.user_id,
            status=payload.status,
            started_at=payload.started_at or datetime.utcnow(),
            ended_at=payload.ended_at,
            total_duration_ms=payload.total_duration_ms,
            input_preview=payload.input_preview,
            output_preview=payload.output_preview,
            metadata_json=meta,
        ))

    for span in payload.spans:
        _upsert_span(db, span)
    for log in payload.logs:
        _insert_log(db, log)


def _insert_log(db: Session, l: LogIn):
    meta = json.dumps(l.metadata) if l.metadata else None
    db.add(TraceLog(
        trace_id=l.trace_id,
        level=l.level.upper(),
        logger=l.logger,
        message=l.message,
        timestamp=l.timestamp or datetime.utcnow(),
        metadata_json=meta,
    ))


def _upsert_span(db: Session, s: SpanIn):
    existing = db.query(Span).filter(Span.id == s.id).first()
    meta = json.dumps(s.metadata) if s.metadata else None
    if existing:
        for field, val in {
            "status": s.status,
            "ended_at": s.ended_at,
            "duration_ms": s.duration_ms,
            "output_preview": s.output_preview,
            "error_message": s.error_message,
            "tokens_input": s.tokens_input,
            "tokens_output": s.tokens_output,
            "metadata_json": meta,
        }.items():
            if val is not None:
                setattr(existing, field, val)
    else:
        db.add(Span(
            id=s.id,
            trace_id=s.trace_id,
            parent_span_id=s.parent_span_id,
            name=s.name,
            span_type=s.span_type,
            status=s.status,
            started_at=s.started_at or datetime.utcnow(),
            ended_at=s.ended_at,
            duration_ms=s.duration_ms,
            input_preview=s.input_preview,
            output_preview=s.output_preview,
            error_message=s.error_message,
            tokens_input=s.tokens_input,
            tokens_output=s.tokens_output,
            model_name=s.model_name,
            metadata_json=meta,
        ))
