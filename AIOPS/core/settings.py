from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.base_dir = Path(__file__).resolve().parents[1]
        self.runs_dir = Path(os.getenv("RUNS_DIR", str(self.base_dir / "runs"))).expanduser().resolve()
        self.config_dir = self.base_dir / "config"
        self.static_dir = self.base_dir / "static"
        self.templates_dir = self.base_dir / "templates"
        self.managed_repos_dir = Path(os.getenv("MANAGED_REPOS_DIR", str(self.base_dir / "managed_repos"))).expanduser().resolve()
        self.default_test_command = os.getenv("DEFAULT_TEST_COMMAND", "pytest -q")
        self.default_validation_command = os.getenv("DEFAULT_VALIDATION_COMMAND", self.default_test_command)
        self.codex_command = os.getenv("CODEX_COMMAND", "codex")
        self.codex_model = os.getenv("CODEX_MODEL", "gpt-5-codex")
        self.codex_sandbox_fallback_enabled = os.getenv("CODEX_SANDBOX_FALLBACK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.codex_sandbox_fallback_mode = os.getenv("CODEX_SANDBOX_FALLBACK_MODE", "danger-full-access")
        self.gh_command = os.getenv("GH_COMMAND", "gh")
        self.github_remote = os.getenv("GITHUB_REMOTE", "origin")
        self.github_owner = os.getenv("GITHUB_OWNER", "")
        self.github_repo = os.getenv("GITHUB_REPO", "")
        self.github_base_url = os.getenv("GITHUB_BASE_URL", "https://github.com")
        self.github_api_base_url = os.getenv("GITHUB_API_BASE_URL", "https://api.github.com")
        self.github_token = os.getenv("GITHUB_TOKEN", "")
        self.default_pr_base = os.getenv("DEFAULT_PR_BASE", "")
        self.codex_extra_args = os.getenv("CODEX_EXTRA_ARGS", "")
        self.demo_fast_path = os.getenv("DEMO_FAST_PATH", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.demo_dummy_pr_on_failure = os.getenv("DEMO_DUMMY_PR_ON_FAILURE", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.implementation_watchdog_seconds = int(os.getenv("IMPLEMENTATION_WATCHDOG_SECONDS", "150"))
        self.implementation_hard_timeout_seconds = int(os.getenv("IMPLEMENTATION_HARD_TIMEOUT_SECONDS", "180"))
        self.project_map = self._load_project_map()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.managed_repos_dir.mkdir(parents=True, exist_ok=True)

    def _load_project_map(self) -> dict:
        for name in ["project_map.json", "project_map.example.json"]:
            p = self.config_dir / name
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    return {}
        return {}

    def default_github_repo(self) -> str:
        if self.github_owner and self.github_repo:
            return f"{self.github_owner}/{self.github_repo}"
        return self.github_repo

    def enrich_issue_paths(self, issue: dict) -> dict:
        project_name = issue.get("project_name", "")
        if project_name and project_name in self.project_map:
            cfg = self.project_map[project_name]
            issue["repo_root"] = issue.get("repo_root") or cfg.get("repo_root", "")
            issue["allowed_folder"] = issue.get("allowed_folder") or cfg.get("allowed_folder", "")
            issue["test_command"] = issue.get("test_command") or cfg.get("test_command", self.default_test_command)
            issue["base_branch"] = issue.get("base_branch") or cfg.get("base_branch", "main")
            issue["github_repo"] = issue.get("github_repo") or cfg.get("github_repo", "")
        issue["test_command"] = issue.get("test_command") or self.default_test_command
        issue["base_branch"] = issue.get("base_branch") or self.default_pr_base or "main"
        issue["github_repo"] = issue.get("github_repo") or self.default_github_repo()
        return issue


settings = Settings()
