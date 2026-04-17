"""Langfuse → local SQLite sync.

Pulls recent traces from Langfuse and upserts them into the local database
so that issue detectors and metrics endpoints work from a complete, consistent
dataset regardless of whether the AIops SDK was used directly.

Flow per tick
─────────────
1. Fetch the most-recent page of trace summaries from Langfuse.
2. Identify IDs not yet fully synced (observations not fetched).
3. For each new trace, fetch the full detail record (includes observations).
4. Upsert trace + all observations into local Trace / Span tables.

The in-memory _synced_ids set prevents redundant detail fetches across ticks.
It is capped at MAX_CACHED_IDS entries; once exceeded, the oldest half is
evicted so the set never grows without bound.
"""
import base64
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from server.config import settings
from server.database.engine import SessionLocal
from server.database.models import (
    Trace, Span, Issue, IssueAnalysis, EscalationLog, TraceLog,
)

logger = logging.getLogger("aiops.langfuse_sync")

_LF_BASE = "https://cloud.langfuse.com/api/public"
MAX_CACHED_IDS = 2000

# IDs whose full observations have already been fetched this server session.
# Avoids re-calling the detail endpoint on every tick.
_synced_ids: set[str] = set()
_synced_order: list[str] = []          # insertion-order tracking for eviction


# ── Auth ──────────────────────────────────────────────────────────────────────

def _auth_headers() -> dict[str, str]:
    token = base64.b64encode(
        f"{settings.LANGFUSE_PUBLIC_KEY}:{settings.LANGFUSE_SECRET_KEY}".encode()
    ).decode()
    return {"Authorization": f"Basic {token}"}


# ── Type / status mapping ─────────────────────────────────────────────────────

def _span_type(lf_type: str) -> str:
    return {"GENERATION": "llm", "SPAN": "chain", "EVENT": "tool"}.get(lf_type, "chain")


def _status(level: str, status_message: Optional[str]) -> str:
    return "error" if level == "ERROR" else "ok"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _preview(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        return val[:500]
    try:
        return json.dumps(val)[:500]
    except Exception:
        return str(val)[:500]


def _mark_synced(trace_id: str) -> None:
    if trace_id in _synced_ids:
        return
    _synced_ids.add(trace_id)
    _synced_order.append(trace_id)
    # Evict oldest half when cap is reached
    if len(_synced_ids) > MAX_CACHED_IDS:
        evict = _synced_order[: MAX_CACHED_IDS // 2]
        for eid in evict:
            _synced_ids.discard(eid)
        del _synced_order[: MAX_CACHED_IDS // 2]


# ── Main sync entry point ─────────────────────────────────────────────────────

async def sync_langfuse(limit: int = 50) -> int:
    """Fetch recent Langfuse traces and upsert into local SQLite.

    Returns the number of traces newly synced.
    Silently returns 0 if Langfuse credentials are not configured.
    """
    if not (settings.LANGFUSE_SECRET_KEY and settings.LANGFUSE_PUBLIC_KEY):
        return 0

    headers = _auth_headers()
    synced = 0

    try:
        async with httpx.AsyncClient(timeout=15) as client:

            # 1. Fetch the most recent page of trace summaries
            resp = await client.get(
                f"{_LF_BASE}/traces",
                params={"page": 1, "limit": limit},
                headers=headers,
            )
            if resp.status_code != 200:
                logger.warning("Langfuse list request failed: HTTP %s", resp.status_code)
                return 0

            trace_summaries = resp.json().get("data", [])

            # 2. Filter out platform-generated issue notification traces
            #    (langfuse_reporter writes these with names like "[SEV1] NFR-11")
            #    and skip any already fully synced this session.
            for t in trace_summaries:
                if t.get("name", "").startswith("[SEV"):
                    _mark_synced(t["id"])   # suppress forever, never fetch detail

            to_fetch = [
                t for t in trace_summaries
                if t["id"] not in _synced_ids
            ]

            # 3. Fetch full detail for each new trace and upsert
            for summary in to_fetch:
                trace_id = summary["id"]
                try:
                    detail_resp = await client.get(
                        f"{_LF_BASE}/traces/{trace_id}",
                        headers=headers,
                    )
                    if detail_resp.status_code != 200:
                        logger.debug(
                            "Skipping trace %s — Langfuse returned %s",
                            trace_id, detail_resp.status_code,
                        )
                        continue

                    _upsert_trace(detail_resp.json())
                    _mark_synced(trace_id)
                    synced += 1

                except Exception:
                    logger.exception("Failed to sync trace %s", trace_id)

            # 4. Detect and cascade-delete any traces removed from Langfuse
            live_ids = {t["id"] for t in trace_summaries}
            await _cleanup_deleted_traces(client, live_ids)

    except Exception:
        logger.exception("Langfuse sync error")

    if synced:
        logger.info("Langfuse sync: upserted %d new trace(s)", synced)

    return synced


# ── DB upsert logic ───────────────────────────────────────────────────────────

def _upsert_trace(detail: dict) -> None:
    """Write one Langfuse trace (with its observations) into local SQLite."""
    db = SessionLocal()
    try:
        trace_id = detail.get("id")
        if not trace_id:
            return

        # Derive app_name from the trace name (agent name set at instrumentation time)
        app_name = detail.get("name") or "unknown"

        # Derive status: error if any observation has level ERROR,
        # OR if the root span set output={"error": ...} (set by finish_trace),
        # OR if any observation has a statusMessage (non-empty error text).
        observations = detail.get("observations") or []
        trace_output = detail.get("output") or {}
        has_error = (
            any(o.get("level") == "ERROR" for o in observations)
            or (isinstance(trace_output, dict) and "error" in trace_output)
            or any(o.get("statusMessage") for o in observations)
        )
        status = "error" if has_error else "ok"

        started_at = _parse_dt(detail.get("timestamp"))

        # Langfuse latency is in seconds
        latency_s = detail.get("latency")
        duration_ms = round(latency_s * 1000, 1) if latency_s else None
        ended_at = (
            started_at + timedelta(milliseconds=duration_ms)
            if started_at and duration_ms
            else None
        )

        existing = db.query(Trace).filter(Trace.id == trace_id).first()
        if existing:
            # Refresh mutable fields (SDK-ingested rows take precedence for
            # app_name; only overwrite if it is still the default "unknown")
            if existing.app_name in (None, "unknown"):
                existing.app_name = app_name
            # Never downgrade: if the direct-ingest path already marked this
            # trace as error (e.g. from an exception), keep that status even
            # if Langfuse reports ok (open spans auto-flushed without error level).
            if status == "error" or existing.status != "error":
                existing.status = status
            if duration_ms is not None:
                existing.total_duration_ms = duration_ms
            if ended_at:
                existing.ended_at = ended_at
            if not existing.input_preview:
                existing.input_preview = _preview(detail.get("input"))
            if not existing.output_preview:
                existing.output_preview = _preview(detail.get("output"))
        else:
            db.add(Trace(
                id=trace_id,
                app_name=app_name,
                session_id=detail.get("sessionId"),
                user_id=detail.get("userId"),
                status=status,
                started_at=started_at or datetime.utcnow(),
                ended_at=ended_at,
                total_duration_ms=duration_ms,
                input_preview=_preview(detail.get("input")),
                output_preview=_preview(detail.get("output")),
            ))

        # Upsert every observation as a Span
        for obs in observations:
            _upsert_observation(db, obs, trace_id)

        db.commit()

    except Exception:
        logger.exception("DB error upserting Langfuse trace %s", detail.get("id"))
        db.rollback()
    finally:
        db.close()


def _cascade_delete_trace(db, trace_id: str) -> None:
    """
    Remove a trace and everything that references it from the local DB:
    spans, trace logs, issues (+ their analyses and escalation logs).
    """
    issue_ids = [
        row[0]
        for row in db.query(Issue.id).filter(Issue.trace_id == trace_id).all()
    ]
    if issue_ids:
        db.query(EscalationLog).filter(EscalationLog.issue_id.in_(issue_ids)).delete(
            synchronize_session=False
        )
        db.query(IssueAnalysis).filter(IssueAnalysis.issue_id.in_(issue_ids)).delete(
            synchronize_session=False
        )
        db.query(Issue).filter(Issue.trace_id == trace_id).delete(
            synchronize_session=False
        )
    db.query(TraceLog).filter(TraceLog.trace_id == trace_id).delete(
        synchronize_session=False
    )
    db.query(Span).filter(Span.trace_id == trace_id).delete(
        synchronize_session=False
    )
    db.query(Trace).filter(Trace.id == trace_id).delete(
        synchronize_session=False
    )
    # Remove from in-memory cache so it can be re-synced if re-created
    _synced_ids.discard(trace_id)
    if trace_id in _synced_order:
        _synced_order.remove(trace_id)
    logger.info(
        "Cascade-deleted trace %s…: %d issue(s) removed",
        trace_id[:8],
        len(issue_ids),
    )


async def _cleanup_deleted_traces(
    client: httpx.AsyncClient,
    live_lf_ids: set[str],
) -> int:
    """
    Detect issues whose trace no longer exists in Langfuse and cascade-delete them.

    Candidates are the distinct trace_ids referenced by rows in the issues table
    that are absent from the current Langfuse page.  Each candidate is verified
    with an individual GET — only a genuine 404 triggers deletion, so traces that
    are simply older than the current sync page are never wrongly removed.

    This approach is restart-safe: it does not rely on the in-memory _synced_ids
    set, so it works correctly even after a server restart.
    """
    db = SessionLocal()
    try:
        # All trace_ids that have at least one issue attached
        issue_trace_ids: set[str] = {
            row[0]
            for row in db.query(Issue.trace_id)
                         .filter(Issue.trace_id.isnot(None))
                         .distinct()
                         .all()
        }

        # Fast path: everything is already in the current Langfuse page
        candidates = issue_trace_ids - live_lf_ids
        if not candidates:
            return 0

        headers = _auth_headers()
        deleted = 0

        for trace_id in candidates:
            try:
                resp = await client.get(
                    f"{_LF_BASE}/traces/{trace_id}",
                    headers=headers,
                )
                if resp.status_code == 404:
                    _cascade_delete_trace(db, trace_id)
                    deleted += 1
            except Exception:
                logger.debug("Could not verify trace %s against Langfuse", trace_id[:8])

        if deleted:
            db.commit()
            logger.info(
                "Cascade-deleted %d trace(s) (and their issues) no longer in Langfuse",
                deleted,
            )
        return deleted

    except Exception:
        logger.exception("Error during deleted-trace cleanup")
        db.rollback()
        return 0
    finally:
        db.close()


def _upsert_observation(db, obs: dict, trace_id: str) -> None:
    obs_id = obs.get("id")
    if not obs_id:
        return

    span_type = _span_type(obs.get("type", "SPAN"))
    level = obs.get("level", "DEFAULT")
    status = _status(level, obs.get("statusMessage"))

    started_at = _parse_dt(obs.get("startTime"))
    ended_at = _parse_dt(obs.get("endTime"))

    latency_s = obs.get("latency")
    duration_ms = round(latency_s * 1000, 1) if latency_s else None

    usage = obs.get("usage") or {}
    tokens_in = usage.get("input")
    tokens_out = usage.get("output")

    existing = db.query(Span).filter(Span.id == obs_id).first()
    if existing:
        existing.status = status
        if duration_ms is not None:
            existing.duration_ms = duration_ms
        if tokens_in is not None:
            existing.tokens_input = tokens_in
        if tokens_out is not None:
            existing.tokens_output = tokens_out
        if obs.get("model"):
            existing.model_name = obs["model"]
        if obs.get("statusMessage"):
            existing.error_message = obs["statusMessage"]
    else:
        db.add(Span(
            id=obs_id,
            trace_id=trace_id,
            parent_span_id=obs.get("parentObservationId"),
            name=obs.get("name") or "unknown",
            span_type=span_type,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            input_preview=_preview(obs.get("input")),
            output_preview=_preview(obs.get("output")),
            error_message=obs.get("statusMessage"),
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            model_name=obs.get("model"),
        ))
