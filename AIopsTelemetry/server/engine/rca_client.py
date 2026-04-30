"""
RCA Client
==========
Calls the external Invastigate_flow_with_Poller service and stores the
full pipeline response in IssueAnalysis.rca_json.

Flow
----
1.  request_rca(issue_id)  → creates/resets IssueAnalysis row (status=pending),
                             fires _run_rca as an asyncio background task,
                             returns immediately.
2.  _run_rca               → resolves trace_id / agent_name / timestamp from the
                             Issue row, POSTs to the external service, stores
                             the response in the DB (status → done | failed).
3.  get_rca_analysis(id)   → read path, returns dict with all fields including
                             rca_data (parsed JSON from rca_json column).

Fallback
--------
If the issue has no trace_id the client logs a warning and delegates to the
legacy reason_analyzer so the dashboard always gets a result.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

import httpx

from server.config import settings
from server.database.engine import SessionLocal
from server.database.models import Issue, IssueAnalysis, Trace
from server.engine.bilingual import bilingual_analysis_fields, normalize_lang, select_text

logger = logging.getLogger("aiops.rca_client")

_ANALYZE_URL: str = f"{settings.RCA_SERVICE_URL}/api/v1/analyze"
_CONNECT_TIMEOUT: float = 10.0    # seconds to establish TCP connection
_READ_TIMEOUT: float = 300.0      # external pipeline can take 90–150 s


# ── Public API ────────────────────────────────────────────────────────────────

async def request_rca(issue_id: int) -> dict:
    """
    Trigger external RCA for issue_id.
    Creates/resets the IssueAnalysis row and fires a background task.
    Returns immediately with the current row state.
    """
    db = SessionLocal()
    try:
        issue: Optional[Issue] = (
            db.query(Issue).filter(Issue.id == issue_id).first()
        )
        if not issue:
            raise ValueError(f"Issue {issue_id} not found")

        existing: Optional[IssueAnalysis] = (
            db.query(IssueAnalysis)
            .filter(IssueAnalysis.issue_id == issue_id)
            .first()
        )

        # Return cached result immediately if already done
        if existing and existing.status == "done":
            return _to_dict(existing)

        # Create or reset to pending
        if not existing:
            existing = IssueAnalysis(issue_id=issue_id, status="pending")
            db.add(existing)
            db.commit()
            db.refresh(existing)
        else:
            existing.status = "pending"
            db.commit()

        analysis_id: int = existing.id
    finally:
        db.close()

    asyncio.create_task(_run_rca(analysis_id, issue_id))
    return {"id": analysis_id, "issue_id": issue_id, "status": "pending"}


def get_rca_analysis(
    issue_id: int,
    *,
    lang: str = "ja",
    summary: bool = False,
) -> Optional[dict]:
    """Read path — returns dict with rca_data key, or None if no row exists."""
    db = SessionLocal()
    try:
        row: Optional[IssueAnalysis] = (
            db.query(IssueAnalysis)
            .filter(IssueAnalysis.issue_id == issue_id)
            .first()
        )
        return _to_dict(row, lang=lang, summary=summary) if row else None
    finally:
        db.close()


# ── Background Task ───────────────────────────────────────────────────────────

async def _run_rca(analysis_id: int, issue_id: int) -> None:
    """Background task: call external service, persist result."""
    logger.info("RCA background task started — analysis #%d issue #%d", analysis_id, issue_id)
    db = SessionLocal()
    try:
        analysis = db.query(IssueAnalysis).filter(
            IssueAnalysis.id == analysis_id
        ).first()
        issue = db.query(Issue).filter(Issue.id == issue_id).first()
        if not analysis or not issue:
            logger.error(
                "RCA task: analysis #%d or issue #%d not found",
                analysis_id, issue_id,
            )
            return

        analysis.status = "running"
        db.commit()

        trace_id, agent_name, timestamp = _extract_params(db, issue)

        # ── No trace_id → fall back to legacy reason_analyzer ────────────
        if not trace_id:
            logger.warning(
                "Issue #%d has no trace_id — falling back to reason_analyzer",
                issue_id,
            )
            try:
                from server.engine.reason_analyzer import _run_analysis
                await _run_analysis(analysis_id, issue_id)
            except Exception as exc:
                logger.exception(
                    "Legacy reason_analyzer failed for issue #%d: %s",
                    issue_id, exc,
                )
                _mark_failed(analysis_id, str(exc))
            return

        # ── Call external RCA service ─────────────────────────────────────
        payload = {
            "timestamp": timestamp,
            "trace_id": trace_id,
            "agent_name": agent_name,
            "issue_type": issue.issue_type,
            "rule_id": issue.rule_id,
            "severity": issue.severity,
            "title": issue.title,
            "description": issue.description or "",
            "deployment_context": _deployment_context_for_issue(issue),
        }
        logger.info(
            "External RCA request — issue #%d trace=%s agent=%s",
            issue_id, trace_id, agent_name,
        )

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_CONNECT_TIMEOUT, read=_READ_TIMEOUT)
        ) as client:
            # force=true bypasses the Invastigate cache so a previously
            # cached NO_ERROR result doesn't block re-analysis
            resp = await client.post(_ANALYZE_URL, json=payload, params={"force": "true"})
            resp.raise_for_status()
            rca_data: dict = resp.json()

        _store_result(db, analysis, rca_data)
        logger.info("External RCA done — issue #%d", issue_id)

    except httpx.HTTPStatusError as exc:
        _mark_failed(
            analysis_id,
            f"External RCA service returned HTTP {exc.response.status_code}: "
            f"{exc.response.text[:300]}",
        )
    except httpx.RequestError as exc:
        _mark_failed(
            analysis_id,
            f"Cannot reach RCA service at {_ANALYZE_URL}: {exc}",
        )
    except Exception as exc:
        logger.exception("Unexpected error in RCA task for issue #%d", issue_id)
        _mark_failed(analysis_id, str(exc))
    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_params(db, issue: Issue) -> tuple[Optional[str], str, str]:
    """
    Return (trace_id, agent_name, timestamp) for the external service.

    trace_id  : issue.trace_id — may be None
    agent_name: trace.app_name if available, else issue.app_name
    timestamp : issue.created_at as ISO-8601 string
    """
    trace_id: Optional[str] = issue.trace_id
    agent_name: str = issue.app_name or "unknown"

    # Backfill missing trace_id for rules that were raised from aggregate signals
    # (for example NFR-2) so external RCA can still run.
    if not trace_id and issue.app_name:
        candidates = (
            db.query(Trace.id, Trace.app_name)
            .filter(
                Trace.app_name == issue.app_name,
                Trace.status == "error",
                Trace.started_at <= (issue.created_at or datetime.utcnow()),
            )
            .order_by(Trace.started_at.desc())
            .limit(1)
            .all()
        )
        if not candidates:
            candidates = (
                db.query(Trace.id, Trace.app_name)
                .filter(Trace.app_name == issue.app_name)
                .order_by(Trace.started_at.desc())
                .limit(1)
                .all()
            )
        if candidates:
            trace_id = candidates[0][0]
            # Persist once so future RCA requests use the same representative trace.
            issue.trace_id = trace_id
            db.flush()

    if trace_id:
        trace: Optional[Trace] = (
            db.query(Trace).filter(Trace.id == trace_id).first()
        )
        if trace and trace.app_name:
            agent_name = trace.app_name

    ts = (
        issue.created_at.isoformat()
        if issue.created_at
        else datetime.utcnow().isoformat()
    )
    return trace_id, agent_name, ts


def _deployment_context_for_issue(issue: Issue) -> Optional[dict]:
    """Attach deployment hints so external RCA can recommend the right config file."""
    app_name = (issue.app_name or "").lower()
    issue_type = (issue.issue_type or "").lower()
    title = (issue.title or "").lower()
    trace_id = (issue.trace_id or "").lower()

    is_sample_agent = app_name in {"sample-agent", "sample_agent", "sampleagent"}
    is_pod_threshold = (
        "pod_resource_threshold" in issue_type
        or "pod resource threshold" in title
        or trace_id.startswith("pod-threshold-")
    )

    if not is_sample_agent and not is_pod_threshold:
        return None

    return {
        "application": "sample-agent",
        "runtime": "docker",
        "orchestrator": "docker compose",
        "service_name": "sample-agent-pod",
        "container_name": "sample-agent-pod",
        "ports": ["8002:8002"],
        "config_files": [
            "SampleAgent/Dockerfile",
            "SampleAgent/docker-compose.yml",
            "SampleAgent/.env",
        ],
        "threshold_env_vars": [
            "POD_CPU_THRESHOLD_PERCENT",
            "POD_MEMORY_THRESHOLD_PERCENT",
        ],
        "current_cpu_threshold_percent": 90,
        "current_memory_threshold_percent": 90,
        "required_fix": (
            "Adjust the Docker-managed runtime threshold configuration for "
            "sample-agent, then rebuild and restart the affected service."
        ),
        "validation": (
            "Rerun the bounded availability-guard scenario and verify the app "
            "only returns `application is not reachable` when the configured "
            "runtime guardrail is actually crossed."
        ),
    }


def _store_result(db, analysis: IssueAnalysis, rca_data: dict) -> None:
    """
    Persist the external service response.

    Stores the full JSON in rca_json and also populates the legacy
    likely_cause / evidence / recommended_action columns so that any
    code reading the old format continues to work without modification.
    """
    analysis.rca_json = json.dumps(rca_data)
    analysis.model_used = "rca-external-pipeline"
    analysis.generated_at = datetime.utcnow()

    # ── Unwrap data envelope ({source, message, data: {...}}) ─────────────
    inner = rca_data.get("data") or rca_data

    # Invastigate nested paths:
    #   normalization → { incident: NormalizedIncident }
    #   rca           → { rca: RCAResult }
    #   recommendations → { recommendations: RecommendationResult }
    norm_response = inner.get("normalization") or {}
    norm = norm_response.get("incident") or norm_response  # NormalizedIncident

    rca_response = inner.get("rca") or {}
    rca_block = rca_response.get("rca") or rca_response    # RCAResult

    rec_response = inner.get("recommendations") or {}
    rec_block = rec_response.get("recommendations") or rec_response  # RecommendationResult

    # ── no_error short-circuit ────────────────────────────────────────────
    error_type = (norm.get("error_type") or "").upper()
    if error_type == "NO_ERROR":
        analysis.likely_cause = (
            "No error detected — normalization agent found no anomaly."
        )
        analysis.evidence = norm.get("error_summary") or ""
        analysis.recommended_action = "No action required."
        analysis.full_summary = json.dumps(inner, indent=2)
        _apply_bilingual_fields(analysis, rca_data=inner)
        analysis.status = "done"
        db.commit()
        return

    # ── Likely cause from RCA block ───────────────────────────────────────
    root_cause = rca_block.get("root_cause") or {}
    five_why = rca_block.get("five_why_analysis") or {}
    analysis.likely_cause = (
        rca_block.get("rca_summary")
        or five_why.get("fundamental_root_cause")
        or root_cause.get("description")
        or norm.get("error_summary")
        or "See full pipeline result below."
    )

    # ── Evidence: causal chain + contributing factors ─────────────────────
    chain = rca_block.get("causal_chain") or []
    factors = rca_block.get("contributing_factors") or []
    rc_evidence = root_cause.get("evidence") or []
    five_whys = five_why.get("whys") or []

    ev_lines: list[str] = []
    for item in rc_evidence[:3]:
        ev_lines.append(f"• {item}" if isinstance(item, str) else f"• {item}")
    if five_why.get("fundamental_root_cause"):
        ev_lines.append(f"• Five Whys root cause: {five_why.get('fundamental_root_cause')}")
    for why in five_whys[:2]:
        if isinstance(why, dict):
            ev_lines.append(
                f"• Why {why.get('step','')}: {why.get('answer') or why.get('why') or why}"
            )
        else:
            ev_lines.append(f"• {why}")
    for link in chain[:3]:
        if isinstance(link, dict):
            ev_lines.append(
                f"• {link.get('source_event','')} → {link.get('target_event','')}"
            )
        else:
            ev_lines.append(f"• {link}")
    for f in factors[:2]:
        if isinstance(f, dict):
            ev_lines.append(f"• Contributing ({f.get('severity','')}): {f.get('factor','')}")
        else:
            ev_lines.append(f"• {f}")

    analysis.evidence = "\n".join(ev_lines) or analysis.likely_cause

    # ── Top recommendation ────────────────────────────────────────────────
    solutions = rec_block.get("solutions") or []
    top_sol = next(
        (s for s in solutions if s.get("addresses_root_cause")),
        solutions[0] if solutions else {},
    )
    analysis.recommended_action = (
        top_sol.get("description")
        or top_sol.get("title")
        or rec_block.get("root_cause_addressed")
        or rec_block.get("recommendation_summary")
        or "See recommendations section."
    )
    analysis.recommended_action = _soften_recommendation(analysis.recommended_action)

    analysis.full_summary = json.dumps(inner, indent=2)
    _apply_bilingual_fields(analysis, rca_data=inner)
    analysis.status = "done"
    db.commit()


def _apply_bilingual_fields(
    analysis: IssueAnalysis,
    *,
    rca_data: dict | None = None,
) -> None:
    fields = bilingual_analysis_fields(
        likely_cause=analysis.likely_cause,
        evidence=analysis.evidence,
        recommended_action=analysis.recommended_action,
        full_summary=analysis.full_summary,
        rca_data=rca_data,
    )
    for key, value in fields.items():
        setattr(analysis, key, value)


def _soften_recommendation(text: str) -> str:
    """Keep dashboard RCA actions operational without file-level instructions."""
    if not text:
        return text

    lowered = text.lower()
    if (
        "pod_cpu_threshold_percent" in lowered
        or "pod_memory_threshold_percent" in lowered
        or "sampleagent/dockerfile" in lowered
        or "sampleagent/docker-compose.yml" in lowered
        or "docker compose up -d --build sample-agent-pod" in lowered
    ):
        return (
            "Adjust the sample-agent runtime threshold configuration so the "
            "availability guardrail matches expected workload behavior, then "
            "rebuild/restart the affected service and rerun the bounded guardrail "
            "scenario to confirm the symptom only appears during a real breach."
        )
    return text


def _mark_failed(analysis_id: int, msg: str) -> None:
    """Set status=failed with error message in full_summary (opens a fresh session)."""
    fresh = SessionLocal()
    try:
        row = fresh.query(IssueAnalysis).filter(
            IssueAnalysis.id == analysis_id
        ).first()
        if row:
            row.status = "failed"
            row.full_summary = msg[:2000]
            fresh.commit()
            logger.info(
                "Analysis #%d marked failed: %s", analysis_id, msg[:120]
            )
    except Exception:
        logger.exception("Failed to mark analysis #%d as failed", analysis_id)
        fresh.rollback()
    finally:
        fresh.close()


def _to_dict(
    row: IssueAnalysis,
    *,
    lang: str = "ja",
    summary: bool = False,
) -> dict:
    """Serialise IssueAnalysis row to a dict, including parsed rca_data."""
    lang = normalize_lang(lang)
    d = {
        "id": row.id,
        "issue_id": row.issue_id,
        "status": row.status,
        "lang": lang,
        "available_languages": ["ja", "en"],
        "language_status": row.language_status or "pending",
        "model_used": row.model_used,
        "generated_at": (
            row.generated_at.isoformat() if row.generated_at else None
        ),
        "likely_cause": select_text(row, "likely_cause", lang),
        "evidence": select_text(row, "evidence", lang),
        "recommended_action": select_text(row, "recommended_action", lang),
        "full_summary": select_text(row, "full_summary", lang),
        "likely_cause_en": row.likely_cause_en or row.likely_cause,
        "likely_cause_ja": row.likely_cause_ja or row.likely_cause,
        "evidence_en": row.evidence_en or row.evidence,
        "evidence_ja": row.evidence_ja or row.evidence,
        "recommended_action_en": row.recommended_action_en or row.recommended_action,
        "recommended_action_ja": row.recommended_action_ja or row.recommended_action,
        "full_summary_en": row.full_summary_en or row.full_summary,
        "full_summary_ja": row.full_summary_ja or row.full_summary,
        "rca_data": None,
    }
    if summary:
        return d
    if row.rca_json:
        try:
            raw = json.loads(row.rca_json)
            # Unwrap data envelope if present
            d["rca_data"] = raw.get("data") or raw
        except (json.JSONDecodeError, AttributeError):
            d["rca_data"] = None
    return d
