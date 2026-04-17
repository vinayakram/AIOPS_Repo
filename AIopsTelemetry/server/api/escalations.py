import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from server.database.engine import get_db
from server.database.models import EscalationRule, EscalationLog

router = APIRouter(prefix="/escalations", tags=["escalations"])

VALID_CONDITIONS = {
    "duration_ms_gt", "error_rate_gt", "repeated_error_count_gte",
    "open_issue_age_gt", "severity_gte", "nfr_detection"
}
VALID_ACTIONS = {"webhook", "log", "escalate_issue"}


class RuleCreate(BaseModel):
    app_name: Optional[str] = None
    name: str
    enabled: bool = True
    condition_type: str
    condition_value: float
    condition_span_name: Optional[str] = None
    action_type: str
    action_config: Optional[dict] = None


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    condition_type: Optional[str] = None
    condition_value: Optional[float] = None
    condition_span_name: Optional[str] = None
    action_type: Optional[str] = None
    action_config: Optional[dict] = None


@router.get("/rules")
def list_rules(
    app_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(EscalationRule)
    if app_name:
        q = q.filter(
            (EscalationRule.app_name == app_name) | (EscalationRule.app_name == None)
        )
    return {"rules": [_rule_dict(r) for r in q.all()]}


@router.post("/rules", status_code=201)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)):
    if payload.condition_type not in VALID_CONDITIONS:
        raise HTTPException(400, f"condition_type must be one of {VALID_CONDITIONS}")
    if payload.action_type not in VALID_ACTIONS:
        raise HTTPException(400, f"action_type must be one of {VALID_ACTIONS}")
    rule = EscalationRule(
        app_name=payload.app_name,
        name=payload.name,
        enabled=payload.enabled,
        condition_type=payload.condition_type,
        condition_value=payload.condition_value,
        condition_span_name=payload.condition_span_name,
        action_type=payload.action_type,
        action_config=json.dumps(payload.action_config) if payload.action_config else None,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return {"id": rule.id}


@router.get("/rules/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(EscalationRule).filter(EscalationRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    return _rule_dict(rule)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, payload: RuleUpdate, db: Session = Depends(get_db)):
    rule = db.query(EscalationRule).filter(EscalationRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    for field in ("name", "enabled", "condition_type", "condition_value",
                  "condition_span_name", "action_type"):
        val = getattr(payload, field)
        if val is not None:
            setattr(rule, field, val)
    if payload.action_config is not None:
        rule.action_config = json.dumps(payload.action_config)
    db.commit()
    return _rule_dict(rule)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(EscalationRule).filter(EscalationRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    db.delete(rule)
    db.commit()


@router.get("/logs")
def list_logs(
    issue_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    q = db.query(EscalationLog)
    if issue_id:
        q = q.filter(EscalationLog.issue_id == issue_id)
    logs = q.order_by(desc(EscalationLog.fired_at)).limit(limit).all()
    return {"logs": [_log_dict(l) for l in logs]}


def _rule_dict(r: EscalationRule) -> dict:
    return {
        "id": r.id,
        "nfr_id": r.nfr_id,
        "app_name": r.app_name,
        "name": r.name,
        "description": r.description,
        "enabled": r.enabled,
        "condition_type": r.condition_type,
        "condition_value": r.condition_value,
        "condition_span_name": r.condition_span_name,
        "action_type": r.action_type,
        "action_config": json.loads(r.action_config) if r.action_config else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _log_dict(l: EscalationLog) -> dict:
    return {
        "id": l.id,
        "issue_id": l.issue_id,
        "rule_id": l.rule_id,
        "action_type": l.action_type,
        "status": l.status,
        "detail": l.detail,
        "fired_at": l.fired_at.isoformat() if l.fired_at else None,
    }
