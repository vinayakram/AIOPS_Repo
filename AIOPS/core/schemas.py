from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class Status(str, Enum):
    PROJECT_REVIEW_PENDING = "PROJECT_REVIEW_PENDING"
    PROJECT_APPROVED = "PROJECT_APPROVED"
    ISSUE_SAVED = "ISSUE_SAVED"
    PLAN_DRAFTED = "PLAN_DRAFTED"
    PLAN_REVISED = "PLAN_REVISED"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    IMPLEMENTATION_RUNNING = "IMPLEMENTATION_RUNNING"
    IMPLEMENTATION_READY = "IMPLEMENTATION_READY"
    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEW_APPROVED = "REVIEW_APPROVED"
    HANDOFF_READY = "HANDOFF_READY"
    PR_CREATED = "PR_CREATED"
    FAILED = "FAILED"


class RemediationType(str, Enum):
    CODE_CHANGE = "code_change"
    INFRA_CHANGE = "infra_change"
    CONFIG_CHANGE = "config_change"
    RUNBOOK_CHANGE = "runbook_change"
    INVESTIGATION_ONLY = "investigation_only"
    HUMAN_HANDOFF = "human_handoff"


class Issue(BaseModel):
    issue_id: str
    project_name: str = ""
    title: str
    description: str = ""
    acceptance_criteria: List[str] = Field(default_factory=list)
    repo_root: str = ""
    allowed_folder: str = ""
    test_command: str = "pytest -q"
    base_branch: str = "main"
    github_repo: str = ""
    github_issue_number: int | None = None
    github_issue_url: str = ""
    remediation_type: RemediationType = RemediationType.CODE_CHANGE
    source_system: str = "manual_ui"
    source_issue_id: str = ""
    source_issue_url: str = ""
    upstream_repo: str = ""
    requested_by: str = ""
    environment: str = ""
    validation_command: str = ""
    rca_summary: str = ""
    rca_evidence: List[str] = Field(default_factory=list)
    rca_recommended_action: str = ""
    rca_source: str = ""
    rca_trace_id: str = ""


class GitHubIncident(BaseModel):
    repo: str
    issue_number: int
    title: str
    body: str = ""
    labels: List[str] = Field(default_factory=list)
    url: str = ""
    logs: str = ""


class RCAReport(BaseModel):
    summary: str
    likely_failure_mode: str
    probable_root_cause: str
    evidence: List[str] = Field(default_factory=list)
    remediation_recommendations: List[str] = Field(default_factory=list)
    suggested_issue_title: str = ""
    suggested_issue_body: str = ""


class ProjectResolution(BaseModel):
    project_name: str = ""
    repo_root: str = ""
    allowed_folder: str = ""
    test_command: str = "pytest -q"
    base_branch: str = "main"
    github_repo: str = ""
    confidence: float = 0.0
    reasoning: str = ""
    source: str = "config"


class TestRunResult(BaseModel):
    command: str
    return_code: int
    stdout: str = ""
    stderr: str = ""


class ImplementationRun(BaseModel):
    branch_name: str
    backup_zip: str
    codex_plan_prompt: str = ""
    codex_implementation_prompt: str = ""
    codex_final_message: str = ""
    tests: List[TestRunResult] = Field(default_factory=list)
    success: bool = False
    pr_url: str = ""
    pr_number: int | None = None
    pr_title: str = ""
    human_summary: str = ""
    delivery_blocked_reason: str = ""
