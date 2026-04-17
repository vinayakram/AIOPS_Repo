from __future__ import annotations

from pathlib import Path

from core.schemas import ProjectResolution
from core.settings import settings


def _score_project(config_name: str, config: dict, haystack: str, github_repo: str = "") -> float:
    score = 0.0
    if github_repo and github_repo == config.get("github_repo", ""):
        score += 0.7
    if config_name.lower() in haystack:
        score += 0.35
    for matcher in config.get("matchers", []):
        if matcher.lower() in haystack:
            score += 0.2
    return score


def _to_resolution(project_name: str, config: dict, score: float, reasoning: str) -> ProjectResolution:
    return ProjectResolution(
        project_name=project_name,
        repo_root=config.get("repo_root", ""),
        allowed_folder=config.get("allowed_folder", ""),
        test_command=config.get("test_command", settings.default_test_command),
        base_branch=config.get("base_branch", "main"),
        github_repo=config.get("github_repo", ""),
        confidence=min(score, 1.0),
        reasoning=reasoning,
        source="config",
    )


def suggest_projects(*, application_name: str = "", title: str = "", description: str = "", github_repo: str = "") -> list[ProjectResolution]:
    haystack = " ".join([application_name, title, description, github_repo]).lower()
    suggestions: list[ProjectResolution] = []

    for project_name, config in settings.project_map.items():
        score = _score_project(project_name, config, haystack, github_repo=github_repo)
        if score <= 0:
            continue
        suggestions.append(
            _to_resolution(
                project_name,
                config,
                score,
                f"Matched '{project_name}' using application name, title/description, or configured matchers.",
            )
        )

    suggestions.sort(key=lambda item: item.confidence, reverse=True)
    return suggestions


def direct_repo_resolution(
    *,
    project_name: str = "",
    repo_root: str = "",
    allowed_folder: str = "",
    github_repo: str = "",
    base_branch: str = "main",
    test_command: str = "",
) -> ProjectResolution:
    resolved_repo = (repo_root or "").strip()
    resolved_allowed = (allowed_folder or "").strip()
    if resolved_repo and not resolved_allowed:
        resolved_allowed = resolved_repo
    if not project_name:
        if github_repo:
            project_name = github_repo.split("/")[-1]
        elif resolved_repo:
            project_name = Path(resolved_repo).name or "upstream-agent-repo"
        else:
            project_name = "upstream-agent-repo"
    return ProjectResolution(
        project_name=project_name,
        repo_root=resolved_repo,
        allowed_folder=resolved_allowed,
        test_command=test_command or settings.default_test_command,
        base_branch=base_branch or "main",
        github_repo=github_repo,
        confidence=0.95 if (github_repo or resolved_repo) else 0.0,
        reasoning="Using repository details supplied by the upstream remediation trigger.",
        source="upstream",
    )


def resolve_project_selection(project_name: str) -> ProjectResolution:
    config = settings.project_map.get(project_name)
    if not config:
        return ProjectResolution(reasoning=f"Project '{project_name}' is not configured.")
    return _to_resolution(
        project_name,
        config,
        1.0,
        f"Loaded configured project '{project_name}'.",
    )


def resolve_project(*, application_name: str = "", title: str = "", description: str = "", github_repo: str = "") -> ProjectResolution:
    suggestions = suggest_projects(
        application_name=application_name,
        title=title,
        description=description,
        github_repo=github_repo,
    )
    if not suggestions:
        if github_repo:
            return direct_repo_resolution(github_repo=github_repo, project_name=application_name)
        return ProjectResolution(reasoning="No confident project match found.")
    return suggestions[0]
