"""
AutoFix API that triggers and polls Claude Code CLI autofix jobs for detected
issues. Provides endpoints to start a new autofix run against an affected agent's
source code, retrieve job status, list all past jobs, and query registered
agent process statuses managed by the process_manager.

検出されたイシューに対してClaude Code CLIを起動し、影響を受けるエージェントのソースコードを
自動修正するAutoFix APIモジュール。新規ジョブの開始・ステータス取得・ジョブ一覧照会・
プロセスマネージャが管理するエージェントプロセスの状態確認エンドポイントを提供する。
修正完了後はプロセスマネージャ経由でエージェントを自動再起動する。
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
