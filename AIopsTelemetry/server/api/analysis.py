"""
Analysis API
============
Endpoints to request and retrieve root-cause analysis for issues (now
delegated to the external Invastigate_flow_with_Poller microservice),
and to query recent system metric snapshots.
"""
from fastapi import APIRouter, HTTPException, Query
from server.engine.rca_client import request_rca, get_rca_analysis
from server.engine.metrics_collector import get_recent_snapshots

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/issues/{issue_id}", status_code=202)
async def request_analysis(issue_id: int, force: bool = Query(False)):
    """
    Trigger external 5-agent RCA pipeline for an issue.
    Returns immediately with status='pending'; poll GET to get the result.
    Pass ?force=true to regenerate an existing analysis.
    """
    try:
        if force:
            # Clear existing analysis so it regenerates from scratch
            from server.database.engine import SessionLocal
            from server.database.models import IssueAnalysis
            db = SessionLocal()
            try:
                existing = db.query(IssueAnalysis).filter(
                    IssueAnalysis.issue_id == issue_id
                ).first()
                if existing:
                    existing.status = "pending"
                    existing.likely_cause = None
                    existing.evidence = None
                    existing.recommended_action = None
                    existing.full_summary = None
                    existing.rca_json = None        # clear external result
                    db.commit()
            finally:
                db.close()

        result = await request_rca(issue_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/issues/{issue_id}")
def get_issue_analysis(issue_id: int):
    """
    Return the stored analysis for an issue.
    Response includes rca_data key with the full external pipeline result
    when available, in addition to the legacy cause/evidence/action fields.
    """
    result = get_rca_analysis(issue_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No analysis found. POST to generate one.",
        )
    return result


@router.get("/metrics/recent")
def recent_metrics(n: int = Query(18, ge=1, le=200)):
    """Return the last `n` system metric snapshots (most recent last)."""
    return {"snapshots": get_recent_snapshots(n=n)}
