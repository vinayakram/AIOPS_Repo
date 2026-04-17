from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import web
from core.schemas import Status
from core.settings import settings
from services import storage


class DummyJsonRequest:
    def __init__(self, payload: dict):
        self._payload = payload
        self.headers = {"content-type": "application/json"}

    async def json(self) -> dict:
        return self._payload


@pytest.fixture()
def isolated_state(monkeypatch):
    root = Path.cwd() / ".test_state" / uuid.uuid4().hex
    runs_dir = root / "runs"
    managed_dir = root / "managed_repos"
    runs_dir.mkdir(parents=True)
    managed_dir.mkdir(parents=True)

    monkeypatch.setattr(settings, "runs_dir", runs_dir)
    monkeypatch.setattr(settings, "managed_repos_dir", managed_dir)
    monkeypatch.setattr(storage, "STATE_FILE", runs_dir / "current_state.json")
    monkeypatch.setattr(settings, "project_map", {})

    try:
        yield {"runs_dir": runs_dir, "managed_dir": managed_dir}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _read_issue_json(issue_id: str) -> dict:
    path = storage.issue_dir(issue_id) / "issue.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_create_issue_minimal_returns_resolution_candidates(isolated_state, monkeypatch):
    monkeypatch.setattr(
        settings,
        "project_map",
        {
            "sample-app": {
                "repo_root": "C:\\repos\\sample-app",
                "allowed_folder": "C:\\repos\\sample-app\\src",
                "test_command": "pytest -q",
                "base_branch": "main",
                "github_repo": "org/sample-app",
                "matchers": ["sample-app", "payload"],
            }
        },
    )

    request = DummyJsonRequest(
        {
            "application_name": "sample-app",
            "issue_id": "API-101",
            "description": "Payload context is dropped during handoff.",
        }
    )

    response = asyncio.run(web.api_create_issue(request))
    payload = json.loads(response.body)

    assert payload["status"] == Status.PROJECT_REVIEW_PENDING.value
    assert payload["issue_id"] == "API-101"
    assert payload["recommended_resolution"]["project_name"] == "sample-app"
    assert payload["resolution_candidates"][0]["github_repo"] == "org/sample-app"


def test_project_approve_persists_repo_details(isolated_state, monkeypatch):
    repo_root = isolated_state["managed_dir"] / "repo"
    allowed_folder = repo_root / "src"
    allowed_folder.mkdir(parents=True)

    settings.project_map = {
        "sample-app": {
            "repo_root": str(repo_root),
            "allowed_folder": str(allowed_folder),
            "test_command": "pytest -q",
            "base_branch": "main",
            "github_repo": "org/sample-app",
            "matchers": ["sample-app"],
        }
    }

    issue = web._build_minimal_issue(
        {
            "application_name": "sample-app",
            "issue_id": "API-102",
            "description": "Investigate memory pressure.",
        }
    )
    web._set_issue(issue, Status.PROJECT_REVIEW_PENDING.value)

    request = DummyJsonRequest(
        {
            "project_name": "sample-app",
            "repo_root": str(repo_root),
            "allowed_folder": str(allowed_folder),
            "validation_command": "pytest -q",
        }
    )

    captured: list[str] = []

    def fake_start_job(job_name, issue_obj, target):
        captured.append(job_name)
        target()

    monkeypatch.setattr(web, "_start_job", fake_start_job)

    response = asyncio.run(web.api_issue_project_approve("API-102", request))
    payload = json.loads(response.body)
    saved = _read_issue_json("API-102")

    assert payload["status"] == Status.PROJECT_APPROVED.value
    assert "background" in payload["message"].lower()
    assert captured == ["project_approve"]
    assert saved["repo_root"] == str(repo_root.resolve())
    assert saved["allowed_folder"] == str(allowed_folder.resolve())
    assert saved["validation_command"] == "pytest -q"


def test_plan_reject_returns_rejected_message(isolated_state):
    issue = web._build_minimal_issue(
        {
            "application_name": "sample-app",
            "issue_id": "API-103",
            "description": "Plan should be rejected.",
        }
    )
    web._set_issue(issue, Status.PLAN_DRAFTED.value)

    request = DummyJsonRequest({"reason": "Plan is too broad."})
    response = asyncio.run(web.api_issue_reject_plan("API-103", request))
    payload = json.loads(response.body)

    assert payload["status"] == Status.PLAN_REJECTED.value
    assert payload["message"] == "Plan rejected"
    assert payload["details"] == "Plan is too broad."


def test_plan_approve_starts_implementation_job(isolated_state, monkeypatch):
    repo_root = isolated_state["managed_dir"] / "repo"
    allowed_folder = repo_root / "src"
    allowed_folder.mkdir(parents=True)

    issue = web._build_minimal_issue(
        {
            "application_name": "sample-app",
            "issue_id": "API-104",
            "description": "Approve plan and auto-implement.",
            "repo_root": str(repo_root),
            "allowed_folder": str(allowed_folder),
            "validation_command": "pytest -q",
        }
    )
    web._persist_issue(issue, Status.PROJECT_APPROVED.value)
    plan_path = storage.issue_dir("API-104") / "plan_v1.md"
    plan_path.write_text("# Plan for API-104\n## Issue summary\n- Example\n", encoding="utf-8")
    storage.set_latest_plan_file("API-104", "plan_v1.md")

    captured: list[str] = []

    def fake_start_job(job_name, issue_obj, target):
        captured.append(job_name)

    monkeypatch.setattr(web, "_start_job", fake_start_job)

    response = web.api_issue_approve_plan("API-104")
    payload = json.loads(response.body)

    assert payload["message"] == "Plan approved. Implementation started in background."
    assert captured == ["implement"]


def test_review_approve_requires_implementation_artifacts(isolated_state):
    issue = web._build_minimal_issue(
        {
            "application_name": "sample-app",
            "issue_id": "API-105",
            "description": "Missing implementation artifact.",
        }
    )
    web._set_issue(issue, Status.PLAN_APPROVED.value)

    with pytest.raises(HTTPException) as exc:
        web._approve_review(issue, "Looks good")

    assert exc.value.status_code == 409


def test_implementation_summary_returns_saved_details(isolated_state):
    issue = web._build_minimal_issue(
        {
            "application_name": "sample-app",
            "issue_id": "API-106",
            "description": "Implementation summary request.",
        }
    )
    web._set_issue(issue, Status.REVIEW_PENDING.value)
    run_dir = storage.issue_dir("API-106")
    (run_dir / "implementation.json").write_text('{"success": true}', encoding="utf-8")
    (run_dir / "head_show.txt").write_text("head summary", encoding="utf-8")
    (run_dir / "git_diff.patch").write_text("diff text", encoding="utf-8")
    (run_dir / "test_results.json").write_text('[{"command":"pytest -q","return_code":0,"stdout":"ok","stderr":""}]', encoding="utf-8")
    (run_dir / "change_summary.json").write_text('{"summary":"done"}', encoding="utf-8")
    storage.save_state(storage.update_issue_state("API-106", status=Status.REVIEW_PENDING.value, review_status="pending"))

    response = web.api_issue_implementation_summary("API-106")
    payload = json.loads(response.body)

    assert payload["status"] == Status.REVIEW_PENDING.value
    assert payload["change_summary"]["summary"] == "done"
    assert payload["git_diff_text"] == "diff text"
    assert payload["test_results"][0]["return_code"] == 0


def test_implementation_requires_approved_plan(isolated_state):
    repo_root = isolated_state["managed_dir"] / "repo"
    allowed_folder = repo_root / "src"
    allowed_folder.mkdir(parents=True)

    issue = web._build_minimal_issue(
        {
            "application_name": "sample-app",
            "issue_id": "API-107",
            "description": "Implementation should require an approved plan.",
            "repo_root": str(repo_root),
            "allowed_folder": str(allowed_folder),
            "validation_command": "pytest -q",
        }
    )
    web._save_issue_without_reset(issue, Status.PROJECT_APPROVED.value)
    storage.save_state(
        storage.update_issue_state(
            "API-107",
            status=Status.PROJECT_APPROVED.value,
            resolution_status="approved",
        )
    )

    with pytest.raises(HTTPException) as exc:
        web.api_issue_implementation("API-107")

    assert exc.value.status_code == 409
    assert "approved plan" in str(exc.value.detail).lower()


def test_artifact_html_is_served_as_plain_text(isolated_state):
    issue = web._build_minimal_issue(
        {
            "application_name": "sample-app",
            "issue_id": "API-108",
            "description": "Artifact safety check.",
        }
    )
    web._set_issue(issue, Status.PLAN_DRAFTED.value)
    html_path = storage.issue_dir("API-108") / "plan.html"
    html_path.write_text("<script>alert('x')</script>", encoding="utf-8")

    response = web.artifact(str(html_path))

    assert response.status_code == 200
    assert response.media_type == "text/plain"
    assert b"<script>alert('x')</script>" in response.body


def test_artifacts_endpoint_returns_meaningful_manifest(isolated_state):
    issue = web._build_minimal_issue(
        {
            "application_name": "sample-app",
            "issue_id": "API-109",
            "description": "Artifact manifest request.",
        }
    )
    web._set_issue(issue, Status.REVIEW_PENDING.value)
    run_dir = storage.issue_dir("API-109")
    (run_dir / "implementation.json").write_text('{"success": true}', encoding="utf-8")
    (run_dir / "artifact_manifest.json").write_text('{"ok": true}', encoding="utf-8")

    response = web.api_issue_artifacts("API-109")
    payload = json.loads(response.body)

    assert payload["artifacts"]
    assert payload["artifacts"][0]["title"]
    assert payload["artifacts"][0]["description"]
    assert "view_url" in payload["artifacts"][0]
