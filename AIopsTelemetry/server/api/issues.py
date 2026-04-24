import json
from datetime import datetime
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc
import logging

from server.config import settings
from server.database.engine import get_db
from server.database.models import Issue


logger = logging.getLogger("aiops.issues")

router = APIRouter(prefix="/issues", tags=["issues"])

VALID_STATUSES = {"OPEN", "ACKNOWLEDGED", "ESCALATED", "RESOLVED"}
VALID_SEVERITIES = {"low", "medium", "high", "critical"}

# Display mapping: internal severity → SEV label
SEV_LABEL = {"critical": "SEV1", "high": "SEV2", "medium": "SEV3", "low": "SEV4"}


class IssueCreate(BaseModel):
    app_name: str
    issue_type: str
    severity: str
    title: str
    description: Optional[str] = None
    span_name: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Optional[dict] = None


class IssueUpdate(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    description: Optional[str] = None


@router.get("")
def list_issues(
    app_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    logger.info("inside issues")
    q = db.query(Issue)
    if app_name:
        q = q.filter(Issue.app_name == app_name)
    if status:
        q = q.filter(Issue.status == status)
    if severity:
        q = q.filter(Issue.severity == severity)
    total = q.count()
    issues = q.order_by(desc(Issue.created_at)).offset(offset).limit(limit).all()
    _hydrate_remediation_metadata(db, issues)
    return {"total": total, "issues": [_issue_dict(i) for i in issues]}


@router.post("", status_code=201)
def create_issue(payload: IssueCreate, db: Session = Depends(get_db)):
    if payload.severity not in VALID_SEVERITIES:
        raise HTTPException(400, f"severity must be one of {VALID_SEVERITIES}")
    import hashlib
    from datetime import datetime
    fp_key = f"{payload.app_name}:{payload.issue_type}:{payload.span_name or ''}"
    base_fp = hashlib.sha256(fp_key.encode()).hexdigest()[:16]

    # Dedup: return existing open issue without modification
    open_existing = (
        db.query(Issue)
        .filter(Issue.base_fingerprint == base_fp, Issue.status != "RESOLVED")
        .first()
    )
    if open_existing:
        return {"id": open_existing.id, "created": False, "message": "Duplicate open issue"}

    # Find prior resolved issue for recurrence linkage
    prior = (
        db.query(Issue)
        .filter(Issue.base_fingerprint == base_fp, Issue.status == "RESOLVED")
        .order_by(Issue.id.desc())
        .first()
    )
    recurrence_count = (prior.recurrence_count + 1) if prior else 0
    occurrence_fp = hashlib.sha256(
        f"{base_fp}:{recurrence_count}".encode()
    ).hexdigest()[:16]

    issue = Issue(
        app_name=payload.app_name,
        issue_type=payload.issue_type,
        severity=payload.severity,
        title=payload.title,
        description=payload.description,
        span_name=payload.span_name,
        trace_id=payload.trace_id,
        fingerprint=occurrence_fp,
        base_fingerprint=base_fp,
        previous_issue_id=prior.id if prior else None,
        recurrence_count=recurrence_count,
        metadata_json=json.dumps(payload.metadata) if payload.metadata else None,
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    return {"id": issue.id, "created": True}


@router.get("/{issue_id}")
def get_issue(issue_id: int, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(404, "Issue not found")
    _hydrate_remediation_metadata(db, [issue])
    return _issue_dict(issue)


@router.patch("/{issue_id}")
def update_issue(issue_id: int, payload: IssueUpdate, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(404, "Issue not found")
    if payload.status:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(400, f"status must be one of {VALID_STATUSES}")
        if payload.status == "ACKNOWLEDGED" and not issue.acknowledged_at:
            issue.acknowledged_at = datetime.utcnow()
        if payload.status == "RESOLVED" and not issue.resolved_at:
            issue.resolved_at = datetime.utcnow()
        issue.status = payload.status
    if payload.severity:
        if payload.severity not in VALID_SEVERITIES:
            raise HTTPException(400, f"severity must be one of {VALID_SEVERITIES}")
        issue.severity = payload.severity
    if payload.description is not None:
        issue.description = payload.description
    issue.updated_at = datetime.utcnow()
    db.commit()
    return _issue_dict(issue)


@router.post("/{issue_id}/acknowledge")
def acknowledge_issue(issue_id: int, db: Session = Depends(get_db)):
    return _transition(issue_id, "ACKNOWLEDGED", db)


@router.post("/{issue_id}/escalate")
def escalate_issue(issue_id: int, db: Session = Depends(get_db)):
    return _transition(issue_id, "ESCALATED", db)


@router.post("/{issue_id}/resolve")
def resolve_issue(issue_id: int, db: Session = Depends(get_db)):
    return _transition(issue_id, "RESOLVED", db)


def _transition(issue_id: int, new_status: str, db: Session):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(404, "Issue not found")
    if new_status == "RESOLVED":
        _hydrate_remediation_metadata(db, [issue], force=True)
    issue.status = new_status
    issue.updated_at = datetime.utcnow()
    if new_status == "ACKNOWLEDGED":
        issue.acknowledged_at = datetime.utcnow()
    elif new_status == "ESCALATED":
        issue.escalation_count += 1
    elif new_status == "RESOLVED":
        issue.resolved_at = datetime.utcnow()
    db.commit()
    return _issue_dict(issue)


def _read_issue_meta(issue: Issue) -> dict:
    try:
        return json.loads(issue.metadata_json) if issue.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _remediation_meta_from_status(data: dict, run_id: str) -> dict:
    status = data.get("status")
    if not status:
        return {}
    meta = {
        "remediation_run_id": run_id,
        "remediation_status": status,
    }
    for key in ("pr_url", "pr_number", "job_phase", "job_error", "current_screen"):
        if key in data:
            meta[f"remediation_{key}"] = data.get(key)
    return meta


def _hydrate_remediation_metadata(db: Session, issues: list[Issue], force: bool = False) -> None:
    """Recover remediation status from AIOPS when telemetry metadata is stale.

    The dashboard relies on issue.metadata_json for its button state. Demo flows
    can bypass the telemetry proxy fallback path, so this lightweight repair keeps
    the board truthful without changing the existing frontend flow.
    """
    changed = False
    base_url = settings.AIOPS_REMEDIATION_URL.rstrip("/")
    if not base_url:
        return
    with httpx.Client(timeout=0.8) as client:
        for issue in issues:
            meta = _read_issue_meta(issue)
            current = meta.get("remediation_status")
            if not force and current == "PR_CREATED":
                continue
            run_id = str(meta.get("remediation_run_id") or f"AIOPS-{issue.id}")
            try:
                response = client.get(f"{base_url}/api/issues/{run_id}/status")
            except httpx.HTTPError:
                continue
            if response.status_code == 404:
                continue
            if not response.is_success:
                continue
            updates = _remediation_meta_from_status(response.json(), run_id)
            if not updates:
                continue
            meta.update(updates)
            issue.metadata_json = json.dumps(meta)
            changed = True
    if changed:
        db.commit()


def _issue_dict(i: Issue) -> dict:
    # Parse metadata_json so the dashboard gets a live object (not a raw string).
    # This carries remediation_run_id / remediation_status written by the proxy.
    meta = _read_issue_meta(i)
    return {
        "id": i.id,
        "app_name": i.app_name,
        "issue_type": i.issue_type,
        "rule_id": i.rule_id,
        "severity": i.severity,
        "sev_label": SEV_LABEL.get(i.severity, i.severity.upper()),
        "status": i.status,
        "fingerprint": i.fingerprint,
        "title": i.title,
        "description": i.description,
        "span_name": i.span_name,
        "trace_id": i.trace_id,
        "escalation_count": i.escalation_count,
        "recurrence_count": i.recurrence_count or 0,
        "previous_issue_id": i.previous_issue_id,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
        "acknowledged_at": i.acknowledged_at.isoformat() if i.acknowledged_at else None,
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "metadata_json": meta,
    }
