from __future__ import annotations

import json
import subprocess

from core.schemas import GitHubIncident
from core.settings import settings


def _run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [settings.gh_command, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or "GitHub CLI command failed.")
    return completed


def fetch_github_issue(repo: str, issue_number: int) -> GitHubIncident:
    completed = _run_gh(
        [
            "issue",
            "view",
            str(issue_number),
            "--repo",
            repo,
            "--json",
            "number,title,body,url,labels",
        ]
    )
    payload = json.loads(completed.stdout)
    labels = [item.get("name", "") for item in payload.get("labels", []) if item.get("name")]
    return GitHubIncident(
        repo=repo,
        issue_number=payload["number"],
        title=payload.get("title", ""),
        body=payload.get("body", ""),
        url=payload.get("url", ""),
        labels=labels,
    )


def create_github_issue(repo: str, title: str, body: str, labels: list[str] | None = None) -> str:
    args = [
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body",
        body,
    ]
    for label in labels or []:
        args.extend(["--label", label])
    completed = _run_gh(args)
    return completed.stdout.strip().splitlines()[-1].strip()
