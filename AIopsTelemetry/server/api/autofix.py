"""
AutoFix API — trigger and poll Claude Code autofix jobs.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from server.database.engine import get_db
from server.engine.autofix_agent import start_autofix, get_job, list_jobs
from server.engine import process_manager

router = APIRouter(prefix="/issues", tags=["autofix"])


@router.post("/{issue_id}/autofix", status_code=202)
async def trigger_autofix(issue_id: int, db: Session = Depends(get_db)):
    """Start a Claude Code autofix job for the given issue. Returns a job_id immediately."""
    try:
        job_id = await start_autofix(issue_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"job_id": job_id, "status": "running"}


@router.get("/autofix/{job_id}")
def get_autofix_job(job_id: str):
    """Poll an autofix job for its current output and status."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/autofix-jobs")
def list_autofix_jobs():
    """List all autofix jobs (most recent work)."""
    return {"jobs": list_jobs()}


@router.get("/agent-statuses")
def agent_statuses():
    """Return the running/stopped status of all registered agent processes."""
    return {"agents": process_manager.all_statuses()}
