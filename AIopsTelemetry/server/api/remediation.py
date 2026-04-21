"""
Remediation Proxy API
=====================
Thin proxy layer that bridges AIopsTelemetry issues to the external AIOPS
remediation service (Documents/AIOPS, running on port 8005).

All routes follow the pattern /api/remediation/issues/{aiops_issue_id}/...
where `aiops_issue_id` is the AIopsTelemetry integer issue ID.

The proxy:
  - Enriches the initial POST /start with issue data from our DB so the
    caller doesn't have to re-type it.
  - Stores the AIOPS run_id (string) back into issues.metadata_json so
    the dashboard can track per-issue remediation state.
  - Translates AIopsTelemetry issue_id → AIOPS run_id for all subsequent calls.
"""
import json
import logging
from typing import Any
from urllib.parse import urlparse, parse_qs, urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.config import settings
from server.database.engine import SessionLocal
from server.database.models import Issue

logger = logging.getLogger("aiops.remediation_proxy")

router = APIRouter(prefix="/remediation", tags=["remediation"])

_TIMEOUT = 30.0  # seconds for proxy calls


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _aiops_url(path: str) -> str:
    return f"{settings.AIOPS_REMEDIATION_URL.rstrip('/')}{path}"


def _get_issue(db: Session, issue_id: int) -> Issue:
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail=f"Issue #{issue_id} not found")
    return issue


def _get_run_id(issue: Issue) -> str:
    """Return the AIOPS run_id stored in metadata_json, or raise 404."""
    meta = _read_meta(issue)
    run_id = meta.get("remediation_run_id")
    if not run_id:
        raise HTTPException(
            status_code=404,
            detail=f"Issue #{issue.id} has no active remediation run. POST /start first.",
        )
    return run_id


def _read_meta(issue: Issue) -> dict:
    try:
        return json.loads(issue.metadata_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _write_meta(db: Session, issue: Issue, updates: dict) -> None:
    meta = _read_meta(issue)
    meta.update(updates)
    issue.metadata_json = json.dumps(meta)
    db.commit()


def _split_evidence(text: str, limit: int = 8) -> list[str]:
    lines = []
    for raw in (text or "").splitlines():
        item = raw.strip().lstrip("-• ").strip()
        if item:
            lines.append(item)
        if len(lines) >= limit:
            break
    return lines


def _latest_rca_handoff(db: Session, issue_id: int) -> dict:
    try:
        from server.database.models import IssueAnalysis
        analysis = (
            db.query(IssueAnalysis)
            .filter(IssueAnalysis.issue_id == issue_id, IssueAnalysis.status == "done")
            .order_by(IssueAnalysis.generated_at.desc(), IssueAnalysis.id.desc())
            .first()
        )
        if not analysis:
            return {}

        handoff = {
            "rca_summary": (analysis.likely_cause or "").strip(),
            "rca_evidence": _split_evidence(analysis.evidence or ""),
            "rca_recommended_action": (analysis.recommended_action or "").strip(),
            "rca_source": analysis.model_used or "investigate-rca",
            "rca_trace_id": "",
        }

        if analysis.rca_json:
            raw = json.loads(analysis.rca_json)
            inner = raw.get("data") or raw
            handoff["rca_trace_id"] = str(inner.get("trace_id") or raw.get("trace_id") or "")

            norm = (inner.get("normalization") or {}).get("incident") or inner.get("normalization") or {}
            rca_block = (inner.get("rca") or {}).get("rca") or inner.get("rca") or {}
            rec_block = (inner.get("recommendations") or {}).get("recommendations") or inner.get("recommendations") or {}
            root_cause = rca_block.get("root_cause") or {}
            five_why = rca_block.get("five_why_analysis") or {}

            summary = (
                rca_block.get("rca_summary")
                or five_why.get("fundamental_root_cause")
                or root_cause.get("description")
                or norm.get("error_summary")
            )
            if summary and not handoff["rca_summary"]:
                handoff["rca_summary"] = summary

            nested_evidence = root_cause.get("evidence") or []
            if five_why.get("fundamental_root_cause"):
                nested_evidence = [
                    f"Five Whys root cause: {five_why.get('fundamental_root_cause')}",
                    *nested_evidence,
                ]
            for why in (five_why.get("whys") or [])[:2]:
                if isinstance(why, dict):
                    nested_evidence.append(
                        f"Why {why.get('step','')}: {why.get('answer') or why.get('why') or why}"
                    )
            if nested_evidence:
                handoff["rca_evidence"] = [
                    str(item).strip() for item in nested_evidence if str(item).strip()
                ][:8] + handoff["rca_evidence"]
                handoff["rca_evidence"] = handoff["rca_evidence"][:8]

            solutions = rec_block.get("solutions") or []
            top_solution = next(
                (s for s in solutions if s.get("addresses_root_cause")),
                solutions[0] if solutions else {},
            )
            recommendation = (
                top_solution.get("description")
                or top_solution.get("title")
                or rec_block.get("root_cause_addressed")
                or rec_block.get("recommendation_summary")
            )
            if recommendation and not handoff["rca_recommended_action"]:
                handoff["rca_recommended_action"] = recommendation

        return handoff
    except Exception as exc:
        logger.warning("Failed to build RCA handoff for issue #%s: %s", issue_id, exc)
        return {}


def _rca_context_text(handoff: dict) -> str:
    if not handoff:
        return ""
    parts = ["Investigate RCA context:"]
    if handoff.get("rca_summary"):
        parts.append(f"RCA summary: {handoff['rca_summary']}")
    if handoff.get("rca_recommended_action"):
        parts.append(f"Recommended action: {handoff['rca_recommended_action']}")
    if handoff.get("rca_trace_id"):
        parts.append(f"RCA trace ID: {handoff['rca_trace_id']}")
    evidence = handoff.get("rca_evidence") or []
    if evidence:
        parts.append("RCA evidence:")
        parts.extend(f"- {item}" for item in evidence[:8])
    return "\n".join(parts)


async def _proxy(method: str, url: str, body: dict | None = None) -> Any:
    """Forward a request to the AIOPS service and return the parsed JSON."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            kwargs: dict = {}
            if body is not None:
                kwargs["json"] = body
            resp = await client.request(method, url, **kwargs)
            if not resp.is_success:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"AIOPS service error: {resp.text[:500]}",
                )
            return resp.json()
    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot reach AIOPS remediation service at {settings.AIOPS_REMEDIATION_URL}. "
                   "Is it running? Start it with: uvicorn app.web:app --port 8005",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AIOPS service timed out")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/issues/{issue_id}/start")
async def start_remediation(issue_id: int, body: dict = {}, db: Session = Depends(get_db)):
    logger.warning("inside start remediation")
    """
    Create a new remediation run for this issue.

    Body (all optional — sensible defaults are pulled from the issue record):
      acceptance_criteria: list[str]
      remediation_type: str  (code_change | infra_change | config_change | ...)
      requested_by: str
    """
    issue = _get_issue(db, issue_id)
    meta  = _read_meta(issue)
    logger.warning("inside start remediation 2")
    # If a run already exists return its current status instead of creating another
    # if meta.get("remediation_run_id"):
    #     run_id = meta["remediation_run_id"]
    #     status_data = await _proxy("GET", _aiops_url(f"/api/issues/{run_id}/status"))
    #     return {
    #         "already_exists": True,
    #         "run_id": run_id,
    #         "status": status_data.get("status"),
    #         **status_data,
    #     }

    # Build AIOPS run_id from our issue id — must be a unique string
    run_id = f"AIOPS-{issue_id}"

    # Build description enriched with RCA context if available
    base_description = issue.description or ""
    rca_handoff = _latest_rca_handoff(db, issue_id)
    rca_context = _rca_context_text(rca_handoff)

    description = (base_description + rca_context).strip() or f"{issue.issue_type} detected in {issue.app_name}"

    
    remediation_type = body.get("remediation_type", "code_change")
    if issue.rule_id == "NFR-33" or issue.issue_type == "nfr_pod_resource_threshold_breach":
        remediation_type = "config_change"

    payload = {
        "application_name": issue.app_name,
        "issue_id":         run_id,
        "title":            issue.title,
        "description":      description,
        "source_system":    "aiops-telemetry",
        "source_issue_id":  str(issue_id),
        "remediation_type": remediation_type,
        "requested_by":     body.get("requested_by", ""),
        "environment":      "production",
        **rca_handoff,
    }
    if body.get("acceptance_criteria"):
        payload["acceptance_criteria"] = body["acceptance_criteria"]

    data = await _proxy("POST", _aiops_url("/api/issues"), payload)

    # Persist run_id + initial status into our issue record
    _write_meta(db, issue, {
        "remediation_run_id":    run_id,
        "remediation_status":    data.get("status", "PROJECT_REVIEW_PENDING"),
    })
    return data


@router.get("/issues/{issue_id}/resolution")
async def get_resolution(issue_id: int, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    return await _proxy("GET", _aiops_url(f"/api/issues/{run_id}/resolution"))


@router.post("/issues/{issue_id}/project/approve")
async def approve_project(issue_id: int, body: dict = {}, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data   = await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/project/approve"), body)
    _write_meta(db, issue, {"remediation_status": data.get("status", "PROJECT_APPROVED")})
    return data


@router.post("/issues/{issue_id}/project/reject")
async def reject_project(issue_id: int, body: dict = {}, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    return await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/project/reject"), body)


@router.post("/issues/{issue_id}/plan/start")
async def start_plan(issue_id: int, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data   = await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/plan"))
    _write_meta(db, issue, {"remediation_status": "PLAN_DRAFTING"})
    return data


@router.get("/issues/{issue_id}/plan")
async def get_plan(issue_id: int, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data   = await _proxy("GET", _aiops_url(f"/api/issues/{run_id}/plan"))
    if data.get("status"):
        _write_meta(db, issue, {"remediation_status": data["status"]})
    return data


@router.post("/issues/{issue_id}/plan/revise")
async def revise_plan(issue_id: int, body: dict = {}, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    comments = str((body or {}).get("review_comments", "")).strip()
    if not comments:
        raise HTTPException(status_code=400, detail="Revision comments are required.")
    data = await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/plan/revise"), {"review_comments": comments})
    _write_meta(db, issue, {"remediation_status": "PLAN_REVISION_RUNNING"})
    return {
        **data,
        "status": "PLAN_REVISION_RUNNING",
        "message": data.get("message", "Plan revision started in background."),
    }


@router.post("/issues/{issue_id}/plan/approve")
async def approve_plan(issue_id: int, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    plan = await _proxy("GET", _aiops_url(f"/api/issues/{run_id}/plan"))
    if plan.get("status") in {"PLAN_RUNNING", "PLAN_REVISION_RUNNING", "PLAN_DRAFTING", "PROJECT_APPROVED"}:
        _write_meta(db, issue, {"remediation_status": plan.get("status")})
        raise HTTPException(status_code=409, detail="Latest plan is still being generated. Review it before approving.")
    data   = await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/plan/approve"))
    _write_meta(db, issue, {"remediation_status": "IMPLEMENTATION_RUNNING"})
    return data


@router.post("/issues/{issue_id}/plan/reject")
async def reject_plan(issue_id: int, body: dict = {}, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data   = await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/plan/reject"), body)
    _write_meta(db, issue, {"remediation_status": data.get("status", "PLAN_REJECTED")})
    return data


@router.get("/issues/{issue_id}/status")
async def get_status(issue_id: int, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data   = await _proxy("GET", _aiops_url(f"/api/issues/{run_id}/status"))
    if data.get("status"):
        _write_meta(db, issue, {"remediation_status": data["status"]})
    return data


@router.get("/issues/{issue_id}/implementation/summary")
async def get_impl_summary(issue_id: int, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data   = await _proxy("GET", _aiops_url(f"/api/issues/{run_id}/implementation/summary"))
    if data.get("status"):
        _write_meta(db, issue, {"remediation_status": data["status"]})
    return data


def _rewrite_artifact_url(issue_id: int, url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    path = (query.get("path") or [""])[0]
    download = (query.get("download") or ["0"])[0]
    if not path:
        return url
    params = {"path": path}
    if str(download) not in {"", "0"}:
        params["download"] = download
    return f"/api/remediation/issues/{issue_id}/artifact?{urlencode(params)}"


@router.get("/issues/{issue_id}/artifacts")
async def get_artifacts(issue_id: int, db: Session = Depends(get_db)):
    issue = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data = await _proxy("GET", _aiops_url(f"/api/issues/{run_id}/artifacts"))
    artifacts = []
    for item in data.get("artifacts", []) or []:
        artifacts.append({
            **item,
            "view_url": _rewrite_artifact_url(issue_id, item.get("view_url", "")),
            "download_url": _rewrite_artifact_url(issue_id, item.get("download_url", "")),
        })
    return {**data, "artifacts": artifacts}


@router.get("/issues/{issue_id}/artifact")
async def get_artifact(issue_id: int, path: str, download: int = 0, db: Session = Depends(get_db)):
    issue = _get_issue(db, issue_id)
    _get_run_id(issue)
    url = _aiops_url(f"/artifact?{urlencode({'path': path, 'download': download})}")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url)
            if not resp.is_success:
                raise HTTPException(status_code=resp.status_code, detail=f"AIOPS service error: {resp.text[:500]}")
            headers = {}
            content_disposition = resp.headers.get("content-disposition")
            if content_disposition:
                headers["content-disposition"] = content_disposition
            return Response(content=resp.content, media_type=resp.headers.get("content-type"), headers=headers)
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"Cannot reach AIOPS remediation service at {settings.AIOPS_REMEDIATION_URL}.")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AIOPS service timed out")


@router.post("/issues/{issue_id}/review/approve")
async def approve_review(issue_id: int, body: dict = {}, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data   = await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/review/approve"), body)
    _write_meta(db, issue, {"remediation_status": "REVIEW_APPROVED"})
    return data


@router.post("/issues/{issue_id}/review/reject")
async def reject_review(issue_id: int, body: dict = {}, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    return await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/review/reject"), body)


@router.post("/issues/{issue_id}/pr")
async def create_pr(issue_id: int, db: Session = Depends(get_db)):
    issue  = _get_issue(db, issue_id)
    run_id = _get_run_id(issue)
    data   = await _proxy("POST", _aiops_url(f"/api/issues/{run_id}/pr"))
    _write_meta(db, issue, {"remediation_status": "PR_CREATED"})
    return data


@router.delete("/issues/{issue_id}/run")
async def clear_run(issue_id: int, db: Session = Depends(get_db)):
    """Reset the remediation run mapping so a fresh run can be started."""
    issue = _get_issue(db, issue_id)
    _write_meta(db, issue, {"remediation_run_id": None, "remediation_status": None})
    return {"ok": True, "message": f"Remediation run cleared for issue #{issue_id}"}
