"""
AutoFix Agent — invokes Claude Code CLI to analyse and fix an issue
in the affected agent's source code, then restarts the agent.
"""
import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from server.database.models import Issue
from server.engine import process_manager

logger = logging.getLogger(__name__)

# ── App-name → source-code folder mapping ────────────────────────────────────
# __file__ = …/AIopsTelemetry/server/engine/autofix_agent.py
#   parents[2] = AIopsTelemetry root
#   parents[3] = Documents folder
_DOCS = Path(__file__).resolve().parents[3]

APP_FOLDERS: dict[str, str] = {
    "web-search-agent": str(_DOCS / "WebSearchAgent"),
    "medical-rag":      str(_DOCS / "MedicalAgent"),
    "medical-agent":    str(_DOCS / "MedicalAgent"),
}

# ── In-memory job store ───────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def list_jobs() -> list[dict]:
    return list(_jobs.values())


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(issue: dict, folder: str) -> str:
    span_line = f"\n- **Affected Span:** {issue['span_name']}" if issue.get("span_name") else ""
    trace_line = f"\n- **Trace ID:** {issue['trace_id']}" if issue.get("trace_id") else ""

    return f"""You are an expert AI-agent engineer. An automated telemetry system has detected \
a production issue in one of your agents and needs you to fix it.

## Detected Issue
- **App:** {issue['app_name']}
- **Severity:** {issue['severity']} ({issue.get('sev_label', '')})
- **Issue Type:** {issue['issue_type']}
- **Title:** {issue['title']}
- **Description:** {issue.get('description') or 'No further description provided.'}{span_line}{trace_line}
- **Detected At:** {issue.get('created_at', 'unknown')}

## Agent Code Location
All source files are in: `{folder}`

## Instructions
1. Read the relevant source files to understand the agent's code.
2. Identify the **root cause** of the issue based on the issue type and description.
3. Apply a **targeted, minimal fix** — do not refactor unrelated code.
4. Save the changed files.
5. Do NOT attempt to restart or run the service; the platform will handle that.

Common root causes by issue type:
- `high_latency` / `timeout` → missing timeouts, blocking calls, large prompts
- `high_error_rate` / `exception` → unhandled exceptions, missing try/except, bad API calls
- `token_spike` → unbounded context, prompt construction bugs
- `health_check_failure` → broken health endpoint, startup errors
- `consecutive_failures` → transient error not retried, hard crash on bad input
"""


# ── AutoFix orchestration ─────────────────────────────────────────────────────

async def start_autofix(issue_id: int, db: Session) -> str:
    """Start an async autofix job. Returns the job_id immediately."""
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise ValueError(f"Issue {issue_id} not found")

    app_name = issue.app_name
    folder = APP_FOLDERS.get(app_name)
    if not folder:
        raise ValueError(
            f"No agent folder registered for app '{app_name}'. "
            f"Known apps: {list(APP_FOLDERS.keys())}"
        )

    issue_dict = {
        "id": issue.id,
        "app_name": app_name,
        "issue_type": issue.issue_type,
        "severity": issue.severity,
        "sev_label": {"critical": "SEV1", "high": "SEV2", "medium": "SEV3", "low": "SEV4"}.get(
            issue.severity, ""
        ),
        "title": issue.title,
        "description": issue.description,
        "status": issue.status,
        "span_name": issue.span_name,
        "trace_id": issue.trace_id,
        "created_at": issue.created_at.isoformat() if issue.created_at else None,
    }

    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {
        "job_id": job_id,
        "issue_id": issue_id,
        "app_name": app_name,
        "status": "running",
        "output": "",
        "started_at": datetime.utcnow().isoformat(),
        "finished_at": None,
    }

    asyncio.create_task(_run_claude_fix(job_id, issue_dict, folder))
    return job_id


async def _run_claude_fix(job_id: str, issue: dict, folder: str):
    """Background task: call Claude Code CLI, capture output, restart agent."""
    job = _jobs[job_id]
    prompt = _build_prompt(issue, folder)

    job["output"] += (
        f"[autofix] Issue #{issue['id']} — {issue['title']}\n"
        f"[autofix] App: {issue['app_name']}  Folder: {folder}\n"
        f"[autofix] Invoking Claude Code...\n\n"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude",
            "--dangerously-skip-permissions",
            "-p", prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=folder,
        )

        async for raw_line in proc.stdout:
            job["output"] += raw_line.decode(errors="replace")

        await proc.wait()
        rc = proc.returncode

        if rc == 0:
            job["output"] += f"\n[autofix] Claude Code finished (exit {rc}).\n"
            job["output"] += f"[autofix] Restarting agent '{issue['app_name']}'...\n"
            restarted = await asyncio.to_thread(process_manager.restart, issue["app_name"])
            if restarted:
                job["output"] += "[autofix] Agent restarted successfully.\n"
            else:
                job["output"] += (
                    "[autofix] Agent not managed by process manager — "
                    "please restart it manually.\n"
                )
            job["status"] = "done"
        else:
            job["output"] += f"\n[autofix] Claude Code exited with non-zero code {rc}.\n"
            job["status"] = "failed"

    except FileNotFoundError:
        job["output"] += (
            "\n[autofix] ERROR: 'claude' binary not found. "
            "Ensure Claude Code CLI is installed and in PATH.\n"
        )
        job["status"] = "failed"
    except Exception as exc:
        logger.exception("AutoFix error for job %s", job_id)
        job["output"] += f"\n[autofix] Unexpected error: {exc}\n"
        job["status"] = "failed"
    finally:
        job["finished_at"] = datetime.utcnow().isoformat()
