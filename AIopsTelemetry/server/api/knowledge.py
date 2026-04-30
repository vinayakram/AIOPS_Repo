from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from server.database.engine import get_db
from server.database.models import Issue, RCAIncidentPattern, RCAResolutionPlaybook
from server.engine.knowledge_base import find_matches_for_issue, init_knowledge_base, record_feedback


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


class FeedbackPayload(BaseModel):
    issue_id: Optional[int] = None
    was_helpful: Optional[bool] = None
    was_correct: Optional[bool] = None
    actual_root_cause: str = ""
    actual_fix: str = ""
    notes: str = ""
    created_by: str = ""


@router.get("/patterns")
def list_patterns(db: Session = Depends(get_db)):
    rows = db.query(RCAIncidentPattern).order_by(RCAIncidentPattern.name.asc()).all()
    return {
        "patterns": [
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "affected_layer": row.affected_layer,
                "industry_category": row.industry_category,
                "default_remediation_type": row.default_remediation_type,
            }
            for row in rows
        ]
    }


@router.get("/playbooks")
def list_playbooks(db: Session = Depends(get_db)):
    rows = db.query(RCAResolutionPlaybook).order_by(RCAResolutionPlaybook.priority.asc()).all()
    return {
        "playbooks": [
            {
                "id": row.id,
                "pattern_id": row.pattern_id,
                "title": row.title,
                "remediation_type": row.remediation_type,
                "recommended_action": row.recommended_action,
                "source": row.source,
                "priority": row.priority,
            }
            for row in rows
        ]
    }


@router.get("/matches/issues/{issue_id}")
def issue_matches(issue_id: int, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(404, "Issue not found")
    matches = find_matches_for_issue(db, issue)
    return {
        "issue_id": issue_id,
        "matches": [
            {
                "source": item.source,
                "title": item.title,
                "remediation_type": item.remediation_type,
                "confidence": item.confidence,
                "reason": item.reason,
                "recommended_action": item.recommended_action,
                "validation_steps": item.validation_steps,
                "prior_outcome": item.prior_outcome,
            }
            for item in matches
        ],
    }


@router.post("/seed")
def seed_knowledge():
    init_knowledge_base()
    return {"ok": True, "message": "RCA knowledge base seeded"}


@router.post("/feedback")
def create_feedback(payload: FeedbackPayload, db: Session = Depends(get_db)):
    row = record_feedback(db, **payload.model_dump())
    return {"id": row.id, "created": True}
