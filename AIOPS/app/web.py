from __future__ import annotations

import json
import traceback
from pathlib import Path
from threading import Thread
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core.schemas import Issue, RemediationType, Status
from core.settings import settings
from services.implementation_service import finalize_branch_pr_with_phases, run_implementation_with_phases
from services.plan_service import generate_plan, plan_has_substantive_content, render_plan_html, sanitize_plan_markdown
from services.project_resolution_service import direct_repo_resolution, resolve_project, resolve_project_selection, suggest_projects
from services.repo_service import normalize_issue_repo_inputs
from services.storage import (
    append_progress,
    get_latest_plan_path,
    issue_dir,
    load_issue_state,
    load_state,
    read_text,
    reset_for_issue,
    reset_session_state,
    save_json,
    save_issue_state,
    save_state,
    save_text,
    update_issue_state,
    update_state,
)

TITLE = "Remediation POC"

app = FastAPI(title=TITLE)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
templates = Jinja2Templates(directory=str(settings.templates_dir))


def _current_issue() -> Issue | None:
    state = load_state()
    data = state.get("issue")
    if not data:
        return None
    return Issue.model_validate(data)


def _issue_by_id(issue_id: str) -> Issue | None:
    path = issue_dir(issue_id) / "issue.json"
    if not path.exists():
        return None
    try:
        return Issue.model_validate(json.loads(read_text(path)))
    except Exception:
        return None


def _state_for_issue(issue: Issue | None) -> dict:
    if not issue:
        return load_state()
    issue_state = load_issue_state(issue.issue_id)
    return issue_state or load_state()


def _set_issue(issue: Issue, status: str) -> None:
    payload = issue.model_dump(mode="json")
    reset_for_issue(issue.issue_id, payload, status)
    save_json(issue_dir(issue.issue_id) / "issue.json", payload)


def _set_current_issue(issue: Issue) -> None:
    state = load_issue_state(issue.issue_id)
    if state:
        save_state(state)


def _persist_issue(issue: Issue, status: str) -> Issue:
    issue = normalize_issue_repo_inputs(issue)
    _set_issue(issue, status)
    append_progress(issue.issue_id, "Issue saved. Waiting for next action.")
    return issue


def _save_issue_without_reset(issue: Issue, status: str, message: str = "") -> Issue:
    payload = issue.model_dump(mode="json")
    save_json(issue_dir(issue.issue_id) / "issue.json", payload)
    state = update_issue_state(issue.issue_id, issue=payload, status=status)
    save_state(state)
    if message:
        append_progress(issue.issue_id, message)
    return issue


def _require_project_approval_ready(issue: Issue) -> None:
    state = load_issue_state(issue.issue_id)
    if state.get("resolution_status") != "approved":
        raise HTTPException(status_code=409, detail="Project resolution approval is required before continuing.")
    if not issue.repo_root or not issue.allowed_folder:
        raise HTTPException(status_code=409, detail="Resolved repository details are not ready yet.")


def _require_approved_plan(issue: Issue) -> None:
    if not _approved_plan_path(issue):
        raise HTTPException(status_code=409, detail="An approved plan is required before implementation.")


def _review_payload(issue: Issue | None, state: dict) -> dict:
    if not issue:
        return {
            "review_status": "not_requested",
            "review_decision": "",
            "review_notes": "",
            "resolution_status": "not_requested",
            "resolution_message": "",
        }
    return {
        "review_status": state.get("review_status", "not_requested"),
        "review_decision": state.get("review_decision", ""),
        "review_notes": state.get("review_notes", ""),
        "resolution_status": state.get("resolution_status", "not_requested"),
        "resolution_message": state.get("resolution_message", ""),
    }


def _approve_review(issue: Issue, notes: str = "") -> dict:
    if not (issue_dir(issue.issue_id) / "implementation.json").exists():
        raise HTTPException(status_code=409, detail="Implementation artifacts do not exist yet. Run implementation before review approval.")
    append_progress(issue.issue_id, "Human reviewer approved the generated remediation artifacts.")
    state = update_issue_state(
        issue.issue_id,
        status=Status.REVIEW_APPROVED.value,
        review_status="approved",
        review_decision="approve",
        review_notes=notes.strip(),
        job_error="",
        active_job=None,
    )
    save_state(state)
    return state


def _reject_review(issue: Issue, notes: str = "") -> dict:
    if not (issue_dir(issue.issue_id) / "implementation.json").exists():
        raise HTTPException(status_code=409, detail="Implementation artifacts do not exist yet. Run implementation before review rejection.")
    append_progress(issue.issue_id, "Human reviewer requested changes after reviewing remediation artifacts.")
    state = update_issue_state(
        issue.issue_id,
        status=Status.REVIEW_PENDING.value,
        review_status="changes_requested",
        review_decision="reject",
        review_notes=notes.strip(),
        job_error="",
        active_job=None,
    )
    save_state(state)
    save_text(issue_dir(issue.issue_id) / "review_rejection.txt", notes.strip() + "\n")
    return state


def _require_review_approval(issue: Issue) -> None:
    state = load_issue_state(issue.issue_id)
    if state.get("review_status") != "approved":
        raise HTTPException(status_code=409, detail="Human review approval is required before branch push / PR.")


def _hydrate_issue_from_payload(payload: dict) -> Issue:
    normalized = {
        "issue_id": str(payload.get("issue_id", "")).strip(),
        "project_name": str(payload.get("project_name", "")).strip(),
        "title": str(payload.get("title", "")).strip(),
        "description": str(payload.get("description", "")).strip(),
        "acceptance_criteria": payload.get("acceptance_criteria", []) or [],
        "repo_root": str(payload.get("repo_root", "")).strip(),
        "allowed_folder": str(payload.get("allowed_folder", "")).strip(),
        "test_command": str(payload.get("test_command", "")).strip(),
        "base_branch": str(payload.get("base_branch", "main")).strip(),
        "github_repo": str(payload.get("github_repo", "")).strip(),
        "github_issue_number": payload.get("github_issue_number"),
        "github_issue_url": str(payload.get("github_issue_url", "")).strip(),
        "source_system": str(payload.get("source_system", "manual_ui")).strip() or "manual_ui",
        "source_issue_id": str(payload.get("source_issue_id", "")).strip(),
        "source_issue_url": str(payload.get("source_issue_url", "")).strip(),
        "upstream_repo": str(payload.get("upstream_repo", payload.get("agent_repo", ""))).strip(),
        "requested_by": str(payload.get("requested_by", "")).strip(),
        "environment": str(payload.get("environment", "")).strip(),
        "validation_command": str(payload.get("validation_command", "")).strip(),
        "remediation_type": str(payload.get("remediation_type", RemediationType.CODE_CHANGE.value)).strip() or RemediationType.CODE_CHANGE.value,
        "rca_summary": str(payload.get("rca_summary", "")).strip(),
        "rca_evidence": payload.get("rca_evidence", []) or [],
        "rca_recommended_action": str(payload.get("rca_recommended_action", "")).strip(),
        "rca_source": str(payload.get("rca_source", "")).strip(),
        "rca_trace_id": str(payload.get("rca_trace_id", "")).strip(),
    }
    if isinstance(normalized["acceptance_criteria"], str):
        normalized["acceptance_criteria"] = [x.strip() for x in normalized["acceptance_criteria"].splitlines() if x.strip()]
    if isinstance(normalized["rca_evidence"], str):
        normalized["rca_evidence"] = [x.strip().lstrip("-• ").strip() for x in normalized["rca_evidence"].splitlines() if x.strip()]

    github_repo = normalized["github_repo"] or normalized["upstream_repo"]
    if not normalized["project_name"] or not normalized["repo_root"]:
        resolution = resolve_project(
            application_name=normalized["project_name"],
            title=normalized["title"],
            description=normalized["description"],
            github_repo=github_repo,
        )
        if not resolution.project_name and (normalized["repo_root"] or github_repo):
            resolution = direct_repo_resolution(
                project_name=normalized["project_name"],
                repo_root=normalized["repo_root"] or normalized["upstream_repo"],
                allowed_folder=normalized["allowed_folder"],
                github_repo=github_repo,
                base_branch=normalized["base_branch"],
                test_command=normalized["test_command"] or normalized["validation_command"],
            )
        normalized["project_name"] = normalized["project_name"] or resolution.project_name
        normalized["repo_root"] = normalized["repo_root"] or resolution.repo_root
        normalized["allowed_folder"] = normalized["allowed_folder"] or resolution.allowed_folder
        normalized["test_command"] = normalized["test_command"] or resolution.test_command
        normalized["base_branch"] = normalized["base_branch"] or resolution.base_branch
        normalized["github_repo"] = normalized["github_repo"] or resolution.github_repo

    if not normalized["title"]:
        normalized["title"] = normalized["issue_id"] or "Remediation request"

    normalized = settings.enrich_issue_paths(normalized)
    if not normalized["validation_command"]:
        normalized["validation_command"] = normalized.get("test_command", "") or settings.default_validation_command
    issue = Issue.model_validate(normalized)
    if not issue.repo_root or not issue.allowed_folder:
        raise HTTPException(
            status_code=400,
            detail="Repository root and allowed folder are required. Provide them directly or supply an upstream repository for resolution.",
        )
    return issue


def _title_from_description(description: str, issue_id: str) -> str:
    text = " ".join((description or "").split())
    if not text:
        return issue_id or "Remediation request"
    sentence = text.split(".")[0].strip()
    return sentence[:120] if sentence else (issue_id or "Remediation request")


def _resolution_candidates_path(issue_id: str) -> Path:
    return issue_dir(issue_id) / "project_resolutions.json"


def _save_resolution_candidates(issue_id: str, suggestions: list) -> None:
    save_json(
        _resolution_candidates_path(issue_id),
        {"suggestions": [item.model_dump(mode="json") for item in suggestions]},
    )


def _load_resolution_candidates(issue_id: str) -> list[dict]:
    payload = json.loads(read_text(_resolution_candidates_path(issue_id)) or "{}")
    return payload.get("suggestions", [])


def _build_minimal_issue(payload: dict) -> Issue:
    issue_id = str(payload.get("issue_id", "")).strip()
    description = str(payload.get("description", "")).strip()
    application_name = str(payload.get("application_name", payload.get("project_name", ""))).strip()
    title = str(payload.get("title", "")).strip() or _title_from_description(description, issue_id)
    return Issue.model_validate(
        {
            "issue_id": issue_id,
            "project_name": application_name,
            "title": title,
            "description": description,
            "acceptance_criteria": payload.get("acceptance_criteria", []) or [],
            "repo_root": str(payload.get("repo_root", "")).strip(),
            "allowed_folder": str(payload.get("allowed_folder", "")).strip(),
            "test_command": str(payload.get("test_command", "")).strip(),
            "base_branch": str(payload.get("base_branch", "main")).strip() or "main",
            "github_repo": str(payload.get("github_repo", "")).strip(),
            "github_issue_url": str(payload.get("github_issue_url", "")).strip(),
            "source_system": str(payload.get("source_system", "upstream_api")).strip() or "upstream_api",
            "source_issue_id": str(payload.get("source_issue_id", issue_id)).strip() or issue_id,
            "source_issue_url": str(payload.get("source_issue_url", "")).strip(),
            "upstream_repo": str(payload.get("upstream_repo", payload.get("agent_repo", ""))).strip(),
            "requested_by": str(payload.get("requested_by", "")).strip(),
            "environment": str(payload.get("environment", "")).strip(),
            "validation_command": str(payload.get("validation_command", "")).strip(),
            "remediation_type": str(payload.get("remediation_type", RemediationType.CODE_CHANGE.value)).strip()
            or RemediationType.CODE_CHANGE.value,
            "rca_summary": str(payload.get("rca_summary", "")).strip(),
            "rca_evidence": payload.get("rca_evidence", []) or [],
            "rca_recommended_action": str(payload.get("rca_recommended_action", "")).strip(),
            "rca_source": str(payload.get("rca_source", "")).strip(),
            "rca_trace_id": str(payload.get("rca_trace_id", "")).strip(),
        }
    )


def _resolve_issue_candidates(issue: Issue) -> list:
    suggestions = suggest_projects(
        application_name=issue.project_name,
        title=issue.title,
        description=issue.description,
        github_repo=issue.github_repo or issue.upstream_repo,
    )
    if not suggestions and (issue.repo_root or issue.upstream_repo or issue.github_repo):
        suggestions = [
            direct_repo_resolution(
                project_name=issue.project_name,
                repo_root=issue.repo_root or issue.upstream_repo,
                allowed_folder=issue.allowed_folder,
                github_repo=issue.github_repo or issue.upstream_repo,
                base_branch=issue.base_branch,
                test_command=issue.validation_command or issue.test_command,
            )
        ]
    return suggestions


def _safe_read_issue_file(issue: Issue | None, name: str) -> str:
    if not issue:
        return ""
    return read_text(issue_dir(issue.issue_id) / name)


def _safe_read_issue_json(issue: Issue | None, name: str) -> dict:
    if not issue:
        return {}
    try:
        return json.loads(read_text(issue_dir(issue.issue_id) / name))
    except Exception:
        return {}


def _latest_plan_path(issue: Issue | None) -> Path | None:
    if not issue:
        return None
    return get_latest_plan_path(issue.issue_id)


def _read_latest_plan(issue: Issue | None) -> str:
    path = _latest_plan_path(issue)
    text = sanitize_plan_markdown(read_text(path)) if path else ""
    if issue and not plan_has_substantive_content(text):
        exec_path = issue_dir(issue.issue_id) / "codex_plan_exec.json"
        if exec_path.exists():
            try:
                payload = json.loads(read_text(exec_path))
                raw_stdout = payload.get("stdout", "")
                recovered = sanitize_plan_markdown(raw_stdout)
                if plan_has_substantive_content(recovered):
                    if path is not None:
                        save_text(path, recovered)
                        html_name = path.with_suffix(".html")
                        save_text(html_name, render_plan_html(recovered))
                    return recovered
            except Exception:
                pass
    return text


def _approved_plan_path(issue: Issue | None) -> Path | None:
    if not issue:
        return None
    path = issue_dir(issue.issue_id) / "plan.md"
    return path if path.exists() else None


def _files_for_issue(issue: Issue | None) -> list[dict]:
    if not issue:
        return []
    items = []
    for p in sorted(issue_dir(issue.issue_id).glob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
        stat = p.stat()
        items.append(
            {
                "name": p.name,
                "path": str(p),
                "modified_at": stat.st_mtime,
            }
        )
    return items


def _artifact_title(name: str) -> str:
    mapping = {
        "artifact_manifest.json": "Artifact manifest",
        "artifact_manifest.md": "Artifact guide",
        "change_summary.json": "Change summary",
        "implementation.json": "Implementation result",
        "git_diff.patch": "Git diff",
        "head_show.txt": "HEAD summary",
        "test_results.json": "Validation results",
        "pr_view.json": "Pull request handoff",
        "plan.md": "Approved plan",
        "plan.html": "Approved plan HTML export",
    }
    return mapping.get(name, name.replace("_", " ").replace("-", " ").title())


def _artifact_description(name: str) -> str:
    mapping = {
        "artifact_manifest.json": "Machine-readable index of the remediation artifacts and how to use them.",
        "artifact_manifest.md": "Human-readable guide explaining the most important artifacts in review order.",
        "change_summary.json": "Summary of files changed, affected components, and validation outcomes.",
        "implementation.json": "Top-level implementation status, branch details, and human handoff summary.",
        "git_diff.patch": "Raw diff of the remediation changes for review or downstream tooling.",
        "head_show.txt": "Git HEAD summary showing the final change set at a glance.",
        "test_results.json": "Validation command results captured after implementation.",
        "pr_view.json": "Branch and PR metadata returned after delivery steps run.",
        "plan.md": "Approved remediation plan that implementation followed.",
        "plan.html": "Rendered export of the approved plan for offline viewing.",
    }
    return mapping.get(name, "Generated remediation artifact.")


def _artifact_manifest(issue: Issue | None) -> list[dict]:
    if not issue:
        return []
    entries: list[dict] = []
    for item in _files_for_issue(issue):
        name = item["name"]
        path = Path(item["path"])
        is_text = path.suffix.lower() in {".md", ".txt", ".log", ".json", ".patch", ".html", ".htm"}
        entries.append(
            {
                **item,
                "title": _artifact_title(name),
                "description": _artifact_description(name),
                "view_url": f"/artifact?path={quote(str(path))}",
                "download_url": f"/artifact?path={quote(str(path))}&download=1",
                "is_text": is_text,
            }
        )
    return entries


def _get_current_screen(state: dict) -> str:
    status = state.get("status", "")
    active_job = state.get("active_job", "")
    if active_job == "create_branch_pr":
        return "pr"
    if active_job == "implement":
        return "implementation"
    if active_job in {"plan", "revise_plan"}:
        return "plan"
    if status == Status.FAILED.value:
        if state.get("job_phase") in {"pushing", "pr"} or state.get("pr_url"):
            return "pr"
        if state.get("approved_plan_file"):
            return "implementation"
        if state.get("latest_plan_file"):
            return "plan"
    if status in {Status.PROJECT_REVIEW_PENDING.value}:
        return "issue"
    if status in {Status.PROJECT_APPROVED.value}:
        return "plan"
    if status in {Status.ISSUE_SAVED.value, Status.PLAN_DRAFTED.value, Status.PLAN_REVISED.value, Status.PLAN_REJECTED.value}:
        return "plan"
    if status == Status.PR_CREATED.value:
        return "pr"
    if status in {
        Status.PLAN_APPROVED.value,
        Status.IMPLEMENTATION_RUNNING.value,
        Status.IMPLEMENTATION_READY.value,
        Status.REVIEW_PENDING.value,
        Status.REVIEW_APPROVED.value,
        Status.HANDOFF_READY.value,
    }:
        return "implementation"
    return "issue"


def _filter_progress_log(progress_log: str, markers: tuple[str, ...]) -> str:
    lines = [line for line in (progress_log or "").splitlines() if any(marker in line for marker in markers)]
    return "\n".join(lines).strip()


def _summary_context(issue: Issue | None, state: dict) -> dict:
    latest_plan_path = _latest_plan_path(issue)
    approved_plan_path = _approved_plan_path(issue)
    run_files = _files_for_issue(issue)
    return {
        "summary_issue_id": issue.issue_id if issue else "No issue",
        "summary_status": state.get("status") or "IDLE",
        "summary_phase": state.get("job_phase") or "idle",
        "summary_active_job": state.get("active_job") or "no active job",
        "summary_latest_plan": latest_plan_path.name if latest_plan_path else "No draft plan",
        "summary_approved_plan": approved_plan_path.name if approved_plan_path else "Not approved",
        "summary_file_count": len(run_files),
    }


def _blank_context(request: Request, message: str = "") -> dict:
    context = {
        "request": request,
        "title": TITLE,
        "message": message,
        "issue": None,
        "status": "",
        "job_phase": "idle",
        "job_error": "",
        "active_job": None,
        "plan_text": "",
        "implementation_text": "",
        "progress_log": "",
        "run_files": [],
        "default_test_command": settings.default_test_command,
        "latest_plan_file": None,
        "approved_plan_file": None,
        "current_screen": "issue",
        "plan_logs": "",
        "implementation_logs": "",
        "pr_logs": "",
        "change_summary": {},
        "pr_info": {},
        "git_diff_text": "",
        "head_show_text": "",
        "test_results": [],
        "review_status": "not_requested",
        "review_decision": "",
        "review_notes": "",
        "resolution_candidates": [],
    }
    context.update(_summary_context(None, {}))
    return context


def _context(request: Request, message: str = "") -> dict:
    issue = _current_issue()
    state = _state_for_issue(issue)
    progress_log = _safe_read_issue_file(issue, "run_progress.log")
    context = {
        "request": request,
        "title": TITLE,
        "message": message,
        "issue": issue.model_dump(mode="json") if issue else None,
        "status": state.get("status", ""),
        "job_phase": state.get("job_phase", "idle"),
        "job_error": state.get("job_error", ""),
        "active_job": state.get("active_job"),
        "plan_text": _read_latest_plan(issue),
        "implementation_text": _safe_read_issue_file(issue, "implementation.json"),
        "progress_log": progress_log,
        "run_files": _files_for_issue(issue),
        "default_test_command": settings.default_test_command,
        "latest_plan_file": state.get("latest_plan_file"),
        "approved_plan_file": "plan.md" if _approved_plan_path(issue) else None,
        "current_screen": _get_current_screen(state),
        "plan_logs": _filter_progress_log(progress_log, ("[plan]", "Plan ", "review_comments", "plan_v")),
        "implementation_logs": _filter_progress_log(progress_log, ("[implement]", "Implementation ", "Tests ", "Codex received", "Minimal code change", "Watchdog", "Demo time budget")),
        "pr_logs": _filter_progress_log(progress_log, ("Branch ", "Pull request", "Commit ", "Create branch / PR")),
        "change_summary": _safe_read_issue_json(issue, "change_summary.json"),
        "pr_info": _safe_read_issue_json(issue, "pr_view.json"),
        "git_diff_text": _safe_read_issue_file(issue, "git_diff.patch"),
        "head_show_text": _safe_read_issue_file(issue, "head_show.txt"),
        "test_results": _safe_read_issue_json(issue, "test_results.json") if _safe_read_issue_file(issue, "test_results.json") else [],
        "resolution_candidates": _load_resolution_candidates(issue.issue_id) if issue else [],
    }
    context.update(_review_payload(issue, state))
    context.update(_summary_context(issue, state))
    return context


def _state_payload(issue: Issue | None = None) -> dict:
    issue = issue or _current_issue()
    state = _state_for_issue(issue)
    progress_log = _safe_read_issue_file(issue, "run_progress.log")
    payload = {
        "issue": issue.model_dump(mode="json") if issue else None,
        "status": state.get("status", ""),
        "job_phase": state.get("job_phase", "idle"),
        "job_error": state.get("job_error", ""),
        "active_job": state.get("active_job"),
        "plan_text": _read_latest_plan(issue),
        "implementation_text": _safe_read_issue_file(issue, "implementation.json"),
        "progress_log": progress_log,
        "run_files": _files_for_issue(issue),
        "latest_plan_file": state.get("latest_plan_file"),
        "approved_plan_file": "plan.md" if _approved_plan_path(issue) else None,
        "pr_url": state.get("pr_url", ""),
        "current_screen": _get_current_screen(state),
        "plan_logs": _filter_progress_log(progress_log, ("[plan]", "Plan ", "review_comments", "plan_v")),
        "implementation_logs": _filter_progress_log(progress_log, ("[implement]", "Implementation ", "Tests ", "Codex received", "Minimal code change", "Watchdog", "Demo time budget")),
        "pr_logs": _filter_progress_log(progress_log, ("Branch ", "Pull request", "Commit ", "Create branch / PR")),
        "change_summary": _safe_read_issue_json(issue, "change_summary.json"),
        "pr_info": _safe_read_issue_json(issue, "pr_view.json"),
        "git_diff_text": _safe_read_issue_file(issue, "git_diff.patch"),
        "head_show_text": _safe_read_issue_file(issue, "head_show.txt"),
        "test_results": _safe_read_issue_json(issue, "test_results.json") if _safe_read_issue_file(issue, "test_results.json") else [],
        "resolution_candidates": _load_resolution_candidates(issue.issue_id) if issue else [],
    }
    payload.update(_review_payload(issue, state))
    payload.update(_summary_context(issue, state))
    return payload


def _start_job(job_name: str, issue: Issue, target) -> None:
    state = load_issue_state(issue.issue_id) or load_state()
    if state.get("active_job"):
        raise HTTPException(status_code=409, detail="Another job is already running.")

    state = update_issue_state(
        issue.issue_id,
        active_job=job_name,
        job_phase="starting",
        job_error="",
        issue=issue.model_dump(mode="json"),
    )
    save_state(state)
    append_progress(issue.issue_id, f"{job_name} requested from UI.")
    thread = Thread(target=target, daemon=True)
    thread.start()


def _clear_approved_plan_artifacts(issue: Issue) -> None:
    run_dir = issue_dir(issue.issue_id)
    for filename in ("plan.md", "plan.html", "plan_approved.txt"):
        (run_dir / filename).unlink(missing_ok=True)
    update_issue_state(issue.issue_id, approved_plan_file="")


def _require_plan_ready_for_approval(issue: Issue) -> None:
    state = load_issue_state(issue.issue_id) or load_state()
    active_job = state.get("active_job")
    if active_job in {"plan", "revise_plan"}:
        raise HTTPException(
            status_code=409,
            detail="Plan generation or revision is still running. Wait for the latest plan before approval.",
        )

    if state.get("status", "") in {"PLAN_RUNNING", "PLAN_REVISION_RUNNING"}:
        raise HTTPException(
            status_code=409,
            detail="Plan generation or revision is still running. Wait for the latest plan before approval.",
        )


def _plan_worker(issue: Issue) -> None:
    try:
        save_state(update_issue_state(issue.issue_id, status="PLAN_RUNNING", job_phase="preparing"))
        append_progress(issue.issue_id, "Preparing plan prompt.")
        generate_plan(issue, reviewer_comments=None)
        save_state(update_issue_state(issue.issue_id, status=Status.PLAN_DRAFTED.value, active_job=None, job_phase="completed", job_error=""))
        append_progress(issue.issue_id, "Plan completed successfully.")
    except Exception as exc:
        save_state(update_issue_state(issue.issue_id, status=Status.FAILED.value, active_job=None, job_phase="failed", job_error=str(exc)))
        append_progress(issue.issue_id, f"Plan failed: {exc}")
        append_progress(issue.issue_id, traceback.format_exc())


def _revise_plan_worker(issue: Issue, review_comments: str) -> None:
    try:
        _clear_approved_plan_artifacts(issue)
        save_state(update_issue_state(issue.issue_id, status="PLAN_REVISION_RUNNING", job_phase="preparing"))
        append_progress(issue.issue_id, "Preparing revised plan prompt.")
        comments = [x.strip() for x in review_comments.splitlines() if x.strip()]
        generate_plan(issue, reviewer_comments=comments)
        save_state(update_issue_state(issue.issue_id, status=Status.PLAN_REVISED.value, active_job=None, job_phase="completed", job_error=""))
        append_progress(issue.issue_id, "Plan revision completed successfully.")
    except Exception as exc:
        save_state(update_issue_state(issue.issue_id, status=Status.FAILED.value, active_job=None, job_phase="failed", job_error=str(exc)))
        append_progress(issue.issue_id, f"Plan revision failed: {exc}")
        append_progress(issue.issue_id, traceback.format_exc())


def _implementation_worker(issue: Issue) -> None:
    try:
        def set_phase(phase: str) -> None:
            save_state(update_issue_state(issue.issue_id, status=Status.IMPLEMENTATION_RUNNING.value, job_phase=phase))

        save_state(update_issue_state(issue.issue_id, status=Status.IMPLEMENTATION_RUNNING.value, job_phase="preparing", review_status="pending", review_decision="", review_notes="", pr_url=""))
        append_progress(issue.issue_id, "Implementation started.")
        run_implementation_with_phases(issue, phase_callback=set_phase)
        save_state(
            update_issue_state(
                issue.issue_id,
                status=Status.REVIEW_PENDING.value,
                active_job=None,
                job_phase="completed",
                job_error="",
                pr_url="",
                review_status="pending",
                review_decision="",
            )
        )
        append_progress(issue.issue_id, "Implementation completed successfully.")
    except Exception as exc:
        save_state(update_issue_state(issue.issue_id, status=Status.FAILED.value, active_job=None, job_phase="failed", job_error=str(exc)))
        append_progress(issue.issue_id, f"Implementation failed: {exc}")
        append_progress(issue.issue_id, traceback.format_exc())


def _project_approval_worker(issue: Issue) -> None:
    try:
        save_state(
            update_issue_state(
                issue.issue_id,
                status=Status.PROJECT_APPROVED.value,
                resolution_status="syncing",
                resolution_message="Preparing repository details in background.",
                job_phase="normalizing",
            )
        )
        append_progress(issue.issue_id, "Preparing repository details for the approved project.")
        normalized_issue = normalize_issue_repo_inputs(issue)
        _save_issue_without_reset(normalized_issue, Status.PROJECT_APPROVED.value, "Resolved repository details saved.")
        save_state(
            update_issue_state(
                issue.issue_id,
                status=Status.PROJECT_APPROVED.value,
                resolution_status="approved",
                resolution_message="Project and repository approved by user.",
                active_job=None,
                job_phase="completed",
                job_error="",
            )
        )
        append_progress(issue.issue_id, "Project approval completed successfully.")
    except Exception as exc:
        save_state(
            update_issue_state(
                issue.issue_id,
                status=Status.FAILED.value,
                resolution_status="failed",
                resolution_message=str(exc),
                active_job=None,
                job_phase="failed",
                job_error=str(exc),
            )
        )
        append_progress(issue.issue_id, f"Project approval failed: {exc}")
        append_progress(issue.issue_id, traceback.format_exc())


def _create_branch_pr_worker(issue: Issue) -> None:
    try:
        def set_phase(phase: str) -> None:
            save_state(update_issue_state(issue.issue_id, status=Status.REVIEW_APPROVED.value, job_phase=phase))

        save_state(update_issue_state(issue.issue_id, status=Status.REVIEW_APPROVED.value, job_phase="pushing"))
        append_progress(issue.issue_id, "Create branch / PR started.")
        result = finalize_branch_pr_with_phases(issue, phase_callback=set_phase)
        next_status = Status.PR_CREATED.value if result.pr_number else Status.REVIEW_APPROVED.value
        save_json(
            issue_dir(issue.issue_id) / "pr_view.json",
            {
                "project_name": issue.project_name,
                "github_repo": issue.github_repo,
                "branch_name": result.branch_name,
                "base_branch": issue.base_branch,
                "pr_url": result.pr_url,
                "pr_number": result.pr_number,
                "pr_title": result.pr_title,
                "delivery_blocked_reason": result.delivery_blocked_reason,
            },
        )
        save_state(update_issue_state(issue.issue_id, status=next_status, active_job=None, job_phase="completed", job_error="", pr_url=result.pr_url))
        append_progress(issue.issue_id, "Create branch / PR completed successfully.")
    except Exception as exc:
        save_state(update_issue_state(issue.issue_id, status=Status.FAILED.value, active_job=None, job_phase="failed", job_error=str(exc)))
        append_progress(issue.issue_id, f"Create branch / PR failed: {exc}")
        append_progress(issue.issue_id, traceback.format_exc())


@app.get("/", response_class=HTMLResponse)
def index(request: Request, issue_id: str = ""):
    if issue_id:
        issue = _issue_by_id(issue_id)
        if issue:
            _set_current_issue(issue)
    state = load_state()
    if not state.get("issue"):
        return templates.TemplateResponse(request=request, name="index.html", context=_blank_context(request))
    return templates.TemplateResponse(request=request, name="index.html", context=_context(request))


@app.get("/api/state")
def api_state(issue_id: str = ""):
    issue = _issue_by_id(issue_id) if issue_id else None
    if issue:
        _set_current_issue(issue)
    return JSONResponse(_state_payload(issue))


@app.post("/api/reset-session")
def api_reset_session():
    reset_session_state()
    return JSONResponse({"ok": True})


@app.get("/api/projects/suggest")
def api_project_suggest(application_name: str = "", title: str = "", description: str = "", github_repo: str = ""):
    suggestions = suggest_projects(application_name=application_name, title=title, description=description, github_repo=github_repo)
    if not suggestions and github_repo:
        suggestions = [direct_repo_resolution(project_name=application_name, github_repo=github_repo)]
    return JSONResponse({"suggestions": [item.model_dump(mode="json") for item in suggestions]})


@app.post("/api/projects/select")
def api_project_select(project_name: str = Form(...)):
    resolution = resolve_project_selection(project_name)
    if not resolution.project_name:
        raise HTTPException(status_code=404, detail=resolution.reasoning or "Project not found.")
    return JSONResponse(resolution.model_dump(mode="json"))


@app.post("/save-issue", response_class=HTMLResponse)
def save_issue(
    request: Request,
    issue_id: str = Form(...),
    project_name: str = Form(""),
    title_: str | None = Form(None, alias="title"),
    description: str = Form(""),
    acceptance_criteria: str = Form(""),
    repo_root: str = Form(""),
    allowed_folder: str = Form(""),
    test_command: str = Form(""),
    base_branch: str = Form("main"),
    remediation_type: str = Form(RemediationType.CODE_CHANGE.value),
    source_system: str = Form("manual_ui"),
    source_issue_id: str = Form(""),
    source_issue_url: str = Form(""),
    upstream_repo: str = Form(""),
    validation_command: str = Form(""),
):
    issue = _hydrate_issue_from_payload(
        {
        "issue_id": issue_id.strip(),
        "project_name": project_name.strip(),
        "title": (title_ or "").strip() or issue_id.strip(),
        "description": description,
        "acceptance_criteria": [x.strip() for x in acceptance_criteria.splitlines() if x.strip()],
        "repo_root": repo_root.strip(),
        "allowed_folder": allowed_folder.strip(),
        "test_command": test_command.strip(),
        "base_branch": base_branch.strip(),
        "remediation_type": remediation_type.strip(),
        "source_system": source_system.strip(),
        "source_issue_id": source_issue_id.strip(),
        "source_issue_url": source_issue_url.strip(),
        "upstream_repo": upstream_repo.strip(),
        "validation_command": validation_command.strip(),
        }
    )
    issue = _persist_issue(issue, Status.ISSUE_SAVED.value)
    return templates.TemplateResponse(request=request, name="index.html", context=_context(request, "Issue saved."))


@app.post("/plan")
def plan():
    issue = _current_issue()
    if not issue:
        raise HTTPException(status_code=400, detail="Save an issue first.")
    _start_job("plan", issue, lambda: _plan_worker(issue))
    return JSONResponse({"ok": True, "message": "Plan started in background."})


@app.post("/revise-plan")
def revise_plan(review_comments: str = Form(...)):
    issue = _current_issue()
    if not issue:
        raise HTTPException(status_code=400, detail="Save an issue first.")
    _start_job("revise_plan", issue, lambda: _revise_plan_worker(issue, review_comments))
    return JSONResponse({"ok": True, "message": "Plan revision started in background."})


@app.post("/approve-plan", response_class=HTMLResponse)
def approve_plan(request: Request):
    issue = _current_issue()
    if not issue:
        raise HTTPException(status_code=400, detail="No issue loaded.")

    _require_plan_ready_for_approval(issue)
    latest_plan_path = _latest_plan_path(issue)
    if not latest_plan_path:
        raise HTTPException(status_code=400, detail="No generated plan found. Generate a plan first.")

    approved_plan_text = sanitize_plan_markdown(read_text(latest_plan_path))
    if not approved_plan_text.strip():
        raise HTTPException(status_code=400, detail="Latest plan is empty. Generate a plan again before approving.")

    save_text(issue_dir(issue.issue_id) / "plan.md", approved_plan_text)
    save_text(issue_dir(issue.issue_id) / "plan.html", render_plan_html(approved_plan_text))
    save_text(issue_dir(issue.issue_id) / "plan_approved.txt", f"approved=true\nsource={latest_plan_path.name}\n")

    save_state(update_issue_state(issue.issue_id, status=Status.PLAN_APPROVED.value, approved_plan_file="plan.md"))
    append_progress(issue.issue_id, f"Plan approved by user from {latest_plan_path.name}.")
    return templates.TemplateResponse(request=request, name="index.html", context=_context(request, "Plan approved."))


@app.post("/implement")
def implement():
    issue = _current_issue()
    if not issue:
        raise HTTPException(status_code=400, detail="No issue loaded.")
    _start_job("implement", issue, lambda: _implementation_worker(issue))
    return JSONResponse({"ok": True, "message": "Implementation started in background."})


@app.post("/create-branch-pr")
def create_branch_pr():
    issue = _current_issue()
    if not issue:
        raise HTTPException(status_code=400, detail="No issue loaded.")
    _require_review_approval(issue)
    _start_job("create_branch_pr", issue, lambda: _create_branch_pr_worker(issue))
    return JSONResponse({"ok": True, "message": "Branch / PR started in background."})


@app.post("/review/approve")
def review_approve(review_notes: str = Form("")):
    issue = _current_issue()
    if not issue:
        raise HTTPException(status_code=400, detail="No issue loaded.")
    _approve_review(issue, review_notes)
    return JSONResponse({"ok": True, "message": "Review approved."})


@app.post("/review/reject")
def review_reject(review_notes: str = Form(...)):
    issue = _current_issue()
    if not issue:
        raise HTTPException(status_code=400, detail="No issue loaded.")
    _reject_review(issue, review_notes)
    return JSONResponse({"ok": True, "message": "Changes requested."})


@app.post("/api/issues")
async def api_create_issue(request: Request):
    payload = await request.json()
    issue = _build_minimal_issue(payload)
    suggestions = _resolve_issue_candidates(issue)
    _set_issue(issue, Status.PROJECT_REVIEW_PENDING.value)
    if issue.rca_summary or issue.rca_recommended_action or issue.rca_evidence:
        save_json(
            issue_dir(issue.issue_id) / "rca_handoff.json",
            {
                "source": issue.rca_source,
                "trace_id": issue.rca_trace_id,
                "summary": issue.rca_summary,
                "recommended_action": issue.rca_recommended_action,
                "evidence": issue.rca_evidence,
            },
        )
        append_progress(issue.issue_id, "Investigate RCA context received and attached to remediation planning input.")
    _save_resolution_candidates(issue.issue_id, suggestions)
    resolution_message = (
        "Resolved application to candidate repositories. Waiting for user approval."
        if suggestions
        else "No confident repository match found from application name. Provide a project selection or repo details."
    )
    save_state(
        update_issue_state(
            issue.issue_id,
            status=Status.PROJECT_REVIEW_PENDING.value,
            resolution_status="pending",
            resolution_message=resolution_message,
        )
    )
    append_progress(issue.issue_id, resolution_message)
    recommended = suggestions[0].model_dump(mode="json") if suggestions else None
    return JSONResponse(
        {
            "ok": True,
            "issue_id": issue.issue_id,
            "status": Status.PROJECT_REVIEW_PENDING.value,
            "view_url": f"/api/issues/{issue.issue_id}/view",
            "state_url": f"/api/issues/{issue.issue_id}/status",
            "recommended_resolution": recommended,
            "resolution_candidates": [item.model_dump(mode="json") for item in suggestions],
            "message": resolution_message,
        }
    )


@app.get("/api/issues/{issue_id}")
def api_get_issue(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return JSONResponse(_state_payload(issue))


@app.get("/api/issues/{issue_id}/status")
def api_issue_status(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    state = _state_payload(issue)
    return JSONResponse(
        {
            "issue_id": issue.issue_id,
            "status": state.get("status", ""),
            "job_phase": state.get("job_phase", ""),
            "review_status": state.get("review_status", ""),
            "resolution_status": state.get("resolution_status", ""),
            "resolution_message": state.get("resolution_message", ""),
            "job_error": state.get("job_error", ""),
            "pr_url": state.get("pr_url", ""),
            "current_screen": state.get("current_screen", "issue"),
        }
    )


@app.get("/api/issues/{issue_id}/view")
def api_issue_view(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    _set_current_issue(issue)
    return JSONResponse({"issue_id": issue_id, "view_url": f"/?issue_id={issue_id}"})


@app.get("/api/issues/{issue_id}/resolution")
def api_issue_resolution(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    state = load_issue_state(issue_id)
    candidates = _load_resolution_candidates(issue_id)
    return JSONResponse(
        {
            "issue_id": issue_id,
            "status": state.get("status", ""),
            "resolution_status": state.get("resolution_status", "pending"),
            "resolution_message": state.get("resolution_message", ""),
            "selected_project": issue.model_dump(mode="json"),
            "resolution_candidates": candidates,
        }
    )


@app.post("/api/issues/{issue_id}/project/approve")
async def api_issue_project_approve(issue_id: str, request: Request):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    selected_project = str(payload.get("project_name", "")).strip()
    repo_root = str(payload.get("repo_root", "")).strip()
    allowed_folder = str(payload.get("allowed_folder", "")).strip()
    github_repo = str(payload.get("github_repo", "")).strip()
    base_branch = str(payload.get("base_branch", issue.base_branch)).strip() or issue.base_branch
    validation_command = str(payload.get("validation_command", issue.validation_command or issue.test_command)).strip()

    if selected_project:
        resolution = resolve_project_selection(selected_project)
        if not resolution.project_name:
            raise HTTPException(status_code=404, detail=resolution.reasoning or "Project not found.")
    else:
        candidates = _load_resolution_candidates(issue_id)
        if candidates:
            resolution = resolve_project_selection(candidates[0].get("project_name", ""))
            if not resolution.project_name:
                resolution = direct_repo_resolution(
                    project_name=issue.project_name,
                    repo_root=repo_root or issue.repo_root or issue.upstream_repo,
                    allowed_folder=allowed_folder or issue.allowed_folder,
                    github_repo=github_repo or issue.github_repo or issue.upstream_repo,
                    base_branch=base_branch,
                    test_command=validation_command,
                )
        else:
            resolution = direct_repo_resolution(
                project_name=issue.project_name,
                repo_root=repo_root or issue.repo_root or issue.upstream_repo,
                allowed_folder=allowed_folder or issue.allowed_folder,
                github_repo=github_repo or issue.github_repo or issue.upstream_repo,
                base_branch=base_branch,
                test_command=validation_command,
            )

    merged_issue = issue.model_copy(
        update={
            "project_name": resolution.project_name or issue.project_name,
            "repo_root": repo_root or resolution.repo_root or issue.repo_root,
            "allowed_folder": allowed_folder or resolution.allowed_folder or issue.allowed_folder,
            "test_command": validation_command or resolution.test_command or issue.test_command,
            "validation_command": validation_command or issue.validation_command or resolution.test_command,
            "base_branch": base_branch or resolution.base_branch or issue.base_branch,
            "github_repo": github_repo or resolution.github_repo or issue.github_repo,
        }
    )
    _save_issue_without_reset(
        merged_issue,
        Status.PROJECT_APPROVED.value,
        "Project selection approved. Preparing repository details in background.",
    )
    save_state(
        update_issue_state(
            issue_id,
            status=Status.PROJECT_APPROVED.value,
            resolution_status="syncing",
            resolution_message="Project selection approved. Repository preparation started in background.",
        )
    )
    _start_job("project_approve", merged_issue, lambda: _project_approval_worker(merged_issue))
    return JSONResponse(
        {
            "ok": True,
            "issue_id": issue_id,
            "status": Status.PROJECT_APPROVED.value,
            "message": "Project resolution approved. Repository preparation started in background.",
            "issue": merged_issue.model_dump(mode="json"),
        }
    )


@app.post("/api/issues/{issue_id}/project/reject")
async def api_issue_project_reject(issue_id: str, request: Request):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    reason = str(payload.get("reason", "Project resolution rejected by user.")).strip()
    save_state(
        update_issue_state(
            issue_id,
            status=Status.PROJECT_REVIEW_PENDING.value,
            resolution_status="rejected",
            resolution_message=reason,
        )
    )
    append_progress(issue_id, reason)
    return JSONResponse({"ok": True, "issue_id": issue_id, "status": Status.PROJECT_REVIEW_PENDING.value, "message": reason})


@app.post("/api/issues/{issue_id}/plan")
def api_issue_plan(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    _require_project_approval_ready(issue)
    _set_current_issue(issue)
    _start_job("plan", issue, lambda: _plan_worker(issue))
    return JSONResponse({"ok": True, "message": "Plan started in background."})


@app.get("/api/issues/{issue_id}/plan")
def api_issue_get_plan(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    state = load_issue_state(issue_id)
    return JSONResponse(
        {
            "issue_id": issue_id,
            "status": state.get("status", ""),
            "plan_text": _read_latest_plan(issue),
            "approved": bool(_approved_plan_path(issue)),
            "latest_plan_file": state.get("latest_plan_file"),
            "active_job": state.get("active_job"),
            "job_phase": state.get("job_phase", ""),
            "job_error": state.get("job_error", ""),
        }
    )


@app.post("/api/issues/{issue_id}/plan/revise")
async def api_issue_revise_plan(issue_id: str, request: Request):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    payload = await request.json()
    review_comments = str(payload.get("review_comments", "")).strip()
    if not review_comments:
        raise HTTPException(status_code=400, detail="Revision comments are required.")
    _set_current_issue(issue)
    _start_job("revise_plan", issue, lambda: _revise_plan_worker(issue, review_comments))
    return JSONResponse({"ok": True, "message": "Plan revision started in background."})


@app.post("/api/issues/{issue_id}/plan/approve")
def api_issue_approve_plan(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    _require_plan_ready_for_approval(issue)
    latest_plan_path = _latest_plan_path(issue)
    if not latest_plan_path:
        raise HTTPException(status_code=400, detail="No generated plan found. Generate a plan first.")
    approved_plan_text = sanitize_plan_markdown(read_text(latest_plan_path))
    save_text(issue_dir(issue.issue_id) / "plan.md", approved_plan_text)
    save_text(issue_dir(issue.issue_id) / "plan.html", render_plan_html(approved_plan_text))
    save_text(issue_dir(issue.issue_id) / "plan_approved.txt", f"approved=true\nsource={latest_plan_path.name}\n")
    save_state(update_issue_state(issue.issue_id, status=Status.PLAN_APPROVED.value, approved_plan_file="plan.md"))
    append_progress(issue.issue_id, f"Plan approved by API user from {latest_plan_path.name}.")
    _set_current_issue(issue)
    _start_job("implement", issue, lambda: _implementation_worker(issue))
    return JSONResponse({"ok": True, "message": "Plan approved. Implementation started in background."})


@app.post("/api/issues/{issue_id}/plan/reject")
async def api_issue_reject_plan(issue_id: str, request: Request):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    reason = str(payload.get("reason", "Plan rejected")).strip() or "Plan rejected"
    save_state(update_issue_state(issue_id, status=Status.PLAN_REJECTED.value, job_error="", active_job=None))
    save_text(issue_dir(issue_id) / "plan_rejected.txt", reason + "\n")
    append_progress(issue_id, reason)
    return JSONResponse({"ok": True, "issue_id": issue_id, "status": Status.PLAN_REJECTED.value, "message": "Plan rejected", "details": reason})


@app.post("/api/issues/{issue_id}/implementation")
def api_issue_implementation(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    _require_project_approval_ready(issue)
    _require_approved_plan(issue)
    _set_current_issue(issue)
    _start_job("implement", issue, lambda: _implementation_worker(issue))
    return JSONResponse({"ok": True, "message": "Implementation started in background."})


@app.get("/api/issues/{issue_id}/implementation/summary")
def api_issue_implementation_summary(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    state = load_issue_state(issue_id)
    return JSONResponse(
        {
            "issue_id": issue_id,
            "status": state.get("status", ""),
            "job_phase": state.get("job_phase", ""),
            "job_error": state.get("job_error", ""),
            "review_status": state.get("review_status", ""),
            "change_summary": _safe_read_issue_json(issue, "change_summary.json"),
            "implementation_result": _safe_read_issue_file(issue, "implementation.json"),
            "head_show_text": _safe_read_issue_file(issue, "head_show.txt"),
            "git_diff_text": _safe_read_issue_file(issue, "git_diff.patch"),
            "test_results": _safe_read_issue_json(issue, "test_results.json") if _safe_read_issue_file(issue, "test_results.json") else [],
        }
    )


@app.post("/api/issues/{issue_id}/review/approve")
async def api_issue_review_approve(issue_id: str, request: Request):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    state = _approve_review(issue, str(payload.get("review_notes", "")).strip())
    return JSONResponse({"ok": True, "issue_id": issue_id, "review_status": state.get("review_status")})


@app.post("/api/issues/{issue_id}/review/reject")
async def api_issue_review_reject(issue_id: str, request: Request):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    state = _reject_review(issue, str(payload.get("review_notes", "")).strip())
    return JSONResponse({"ok": True, "issue_id": issue_id, "review_status": state.get("review_status")})


@app.post("/api/issues/{issue_id}/pr")
def api_issue_pr(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    _set_current_issue(issue)
    _require_review_approval(issue)
    _start_job("create_branch_pr", issue, lambda: _create_branch_pr_worker(issue))
    return JSONResponse({"ok": True, "message": "Branch / PR started in background."})


@app.get("/api/issues/{issue_id}/diff")
def api_issue_diff(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return PlainTextResponse(_safe_read_issue_file(issue, "git_diff.patch"))


@app.get("/api/issues/{issue_id}/tests")
def api_issue_tests(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return JSONResponse(_safe_read_issue_json(issue, "test_results.json"))


@app.get("/api/issues/{issue_id}/artifacts")
def api_issue_artifacts(issue_id: str):
    issue = _issue_by_id(issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found.")
    return JSONResponse({"issue_id": issue_id, "artifacts": _artifact_manifest(issue)})


@app.get("/artifact")
def artifact(path: str, download: int = 0):
    p = Path(path).resolve()
    if not p.exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    try:
        p.relative_to(settings.runs_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid artifact path.") from exc
    if download:
        return FileResponse(path=str(p), filename=p.name)
    if p.suffix.lower() in {".md", ".txt", ".log", ".json", ".patch", ".html", ".htm"}:
        return PlainTextResponse(p.read_text(encoding="utf-8", errors="ignore"))
    return FileResponse(path=str(p), filename=p.name)
