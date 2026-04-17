from __future__ import annotations

import json
import os
import re
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from core.schemas import Issue
from core.settings import settings
from services.storage import append_progress, issue_dir, save_text

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "runs"}


def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _slugify_branch_part(value: str, fallback: str = "work") -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or fallback


def build_remediation_branch_name(issue: Issue) -> str:
    issue_part = _slugify_branch_part(issue.issue_id, fallback="issue")
    app_part = _slugify_branch_part(issue.project_name or issue.title, fallback="app")
    return f"codex/{issue_part}-{app_part}".rstrip("-")


def is_git_reference(value: str) -> bool:
    candidate = (value or "").strip()
    return candidate.startswith(("http://", "https://", "git@", "ssh://")) or candidate.endswith(".git")


def _managed_repo_dir(repo_ref: str) -> Path:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", repo_ref.strip().rstrip("/"))
    slug = slug.strip("-") or "repo"
    return settings.managed_repos_dir / slug


def _run_git(args: list[str], cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"git {' '.join(args)} timed out after {timeout} seconds. "
            "The repository may be locked by another git process."
        ) from exc
    if completed.returncode != 0:
        output = completed.stderr or completed.stdout or f"git {' '.join(args)} failed"
        if "Permission denied" in output and ".lock" in output:
            raise RuntimeError(
                "Repository is locked by another git process or Windows file permission. "
                f"Git could not acquire its lock file while running: git {' '.join(args)}.\n{output}"
            )
        raise RuntimeError(output)
    return completed


def _repo_has_head_commit(repo_root: Path) -> bool:
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode == 0


def _branch_exists(repo_root: Path, branch_name: str) -> bool:
    completed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        cwd=str(repo_root),
    )
    return completed.returncode == 0


def sync_repo_reference(repo_ref: str, base_branch: str = "main") -> str:
    if not repo_ref:
        return ""

    if is_git_reference(repo_ref):
        managed_dir = _managed_repo_dir(repo_ref)
        if managed_dir.exists() and (managed_dir / ".git").exists():
            _run_git(["fetch", "origin", base_branch], cwd=managed_dir)
        else:
            managed_dir.parent.mkdir(parents=True, exist_ok=True)
            _run_git(["clone", repo_ref, str(managed_dir)])
        _run_git(["checkout", base_branch], cwd=managed_dir)
        _run_git(["merge", "--ff-only", f"origin/{base_branch}"], cwd=managed_dir)
        return str(managed_dir.resolve())

    repo_path = _resolve(repo_ref)
    if repo_path.exists() and (repo_path / ".git").exists():
        try:
            _run_git(["fetch", "origin", base_branch], cwd=repo_path)
            _run_git(["checkout", base_branch], cwd=repo_path)
            _run_git(["merge", "--ff-only", f"origin/{base_branch}"], cwd=repo_path)
        except RuntimeError:
            return str(repo_path)
    return str(repo_path)


def normalize_issue_repo_inputs(issue: Issue) -> Issue:
    repo_ref = (issue.repo_root or "").strip()
    allowed_ref = (issue.allowed_folder or "").strip()
    base_branch = issue.base_branch or "main"

    if repo_ref:
        normalized_repo_root = sync_repo_reference(repo_ref, base_branch=base_branch)
    else:
        normalized_repo_root = ""

    normalized_allowed_folder = allowed_ref
    if normalized_repo_root:
        repo_root_path = _resolve(normalized_repo_root)
        if not allowed_ref:
            normalized_allowed_folder = str(repo_root_path)
        elif is_git_reference(allowed_ref):
            normalized_allowed_folder = str(repo_root_path)
        else:
            allowed_path = Path(allowed_ref)
            if not allowed_path.is_absolute():
                normalized_allowed_folder = str((repo_root_path / allowed_path).resolve())
            else:
                normalized_allowed_folder = str(_resolve(allowed_ref))

    return issue.model_copy(
        update={
            "repo_root": normalized_repo_root or issue.repo_root,
            "allowed_folder": normalized_allowed_folder or issue.allowed_folder,
        }
    )


def validate_issue_paths(issue: Issue) -> tuple[Path, Path]:
    if not issue.repo_root:
        raise RuntimeError("repo_root is required.")
    if not issue.allowed_folder:
        raise RuntimeError("allowed_folder is required.")
    repo_root = _resolve(issue.repo_root)
    allowed_folder = _resolve(issue.allowed_folder)
    if not repo_root.exists():
        raise RuntimeError(f"repo_root does not exist: {repo_root}")
    if not allowed_folder.exists():
        raise RuntimeError(f"allowed_folder does not exist: {allowed_folder}")
    try:
        allowed_folder.relative_to(repo_root)
    except ValueError as exc:
        raise RuntimeError("allowed_folder must be inside repo_root.") from exc
    return repo_root, allowed_folder


def create_backup(issue: Issue) -> str:
    repo_root, _ = validate_issue_paths(issue)
    run_dir = issue_dir(issue.issue_id)
    backup = run_dir / f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in repo_root.rglob("*"):
            rel = path.relative_to(repo_root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if path.is_file():
                zf.write(path, rel.as_posix())
    return str(backup)


def _auto_stash_dirty_repo(issue: Issue, repo_root: Path) -> str:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    stash_message = f"codex-native-mvp-auto-stash-{_slugify_branch_part(issue.issue_id, 'issue')}-{timestamp}"
    _run_git(["stash", "push", "--include-untracked", "-m", stash_message], cwd=repo_root)
    save_text(issue_dir(issue.issue_id) / "auto_stash.txt", f"message={stash_message}\nrepo_root={repo_root}\n")
    return stash_message


def _git_lock_paths(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in ["*.lock", "refs/**/*.lock", "logs/**/*.lock"]:
        paths.extend((repo_root / ".git").glob(pattern))
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _terminate_git_processes() -> bool:
    if os.name != "nt":
        return False
    completed = subprocess.run(
        ["taskkill", "/IM", "git.exe", "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode == 0


def _clear_git_lock_files(repo_root: Path) -> list[str]:
    removed: list[str] = []
    for path in _git_lock_paths(repo_root):
        try:
            path.unlink()
            removed.append(str(path))
        except OSError:
            continue
    return removed


def _prepare_branch_retry(issue: Issue, repo_root: Path, reason: str) -> None:
    actions: list[str] = []
    if _terminate_git_processes():
        actions.append("terminated_git_processes")
    removed = _clear_git_lock_files(repo_root)
    if removed:
        actions.append(f"removed_lock_files={len(removed)}")
    save_text(
        issue_dir(issue.issue_id) / "branch_retry.txt",
        "reason=" + reason + "\n" + "\n".join(actions) + ("\n" if actions else ""),
    )
    append_progress(issue.issue_id, "Detected stale git state. Attempting demo-safe branch recovery.")


def ensure_clean_branch(issue: Issue) -> str:
    repo_root, _ = validate_issue_paths(issue)
    has_head = _repo_has_head_commit(repo_root)

    status = _run_git(["status", "--porcelain"], cwd=repo_root)
    if status.stdout.strip():
        if has_head:
            stash_message = _auto_stash_dirty_repo(issue, repo_root)
            status = _run_git(["status", "--porcelain"], cwd=repo_root)
            if status.stdout.strip():
                raise RuntimeError(
                    "Repository still has uncommitted changes after automatic self-heal stash. "
                    "Please review the repo state before implementation."
                )
            save_text(
                issue_dir(issue.issue_id) / "branch_self_heal.txt",
                f"action=auto_stash\nmessage={stash_message}\n",
            )
        else:
            append_progress(
                issue.issue_id,
                "Repository has local changes but no initial commit yet; skipping auto-stash."
            )
            save_text(
                issue_dir(issue.issue_id) / "branch_self_heal.txt",
                "action=skip_auto_stash_unborn_head\n",
            )

    branch = build_remediation_branch_name(issue)
    base_branch = issue.base_branch or settings.default_pr_base or "main"

    if has_head:
        current_ref = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)
        current = current_ref.stdout.strip() or "HEAD"
    else:
        current = "UNBORN_HEAD"

    branches = {
        line.strip().lstrip("*").strip()
        for line in _run_git(["branch", "--list"], cwd=repo_root).stdout.splitlines()
        if line.strip()
    }

    def _checkout_branch_sequence() -> None:
        if has_head and _branch_exists(repo_root, base_branch):
            _run_git(["checkout", base_branch], cwd=repo_root)

        if branch in branches:
            if has_head and current == branch and _branch_exists(repo_root, base_branch):
                _run_git(["checkout", base_branch], cwd=repo_root)
            _run_git(["branch", "-D", branch], cwd=repo_root)
            branches.discard(branch)
            append_progress(issue.issue_id, "Stale rerun branch reset from base branch.")
            save_text(
                issue_dir(issue.issue_id) / "branch_reset.txt",
                f"branch={branch}\nbase_branch={base_branch}\naction=recreated_from_base\n",
            )

        if has_head:
            _run_git(["checkout", "-b", branch], cwd=repo_root)
        else:
            # On an unborn repo, create/reset the branch reference directly without checking out a base branch.
            _run_git(["checkout", "-B", branch], cwd=repo_root)

    try:
        _checkout_branch_sequence()
    except RuntimeError as exc:
        error_text = str(exc)
        if settings.demo_fast_path and ("locked by another git process" in error_text or "timed out" in error_text):
            _prepare_branch_retry(issue, repo_root, error_text)
            _checkout_branch_sequence()
        else:
            raise

    save_text(
        issue_dir(issue.issue_id) / "branch_info.txt",
        f"created_from={base_branch if has_head and current != branch else current}\nbranch={branch}\n",
    )
    return branch


def repo_summary(issue: Issue) -> str:
    repo_root, allowed_folder = validate_issue_paths(issue)
    lines = [
        f"repo_root={repo_root}",
        f"allowed_folder={allowed_folder}",
    ]
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        lines.append("git_status=")
        lines.append(result.stdout.strip())
    if not _repo_has_head_commit(repo_root):
        lines.append("head_status=Repository has no initial commit yet.")
    return "\n".join(lines)


def diff_text(issue: Issue) -> str:
    repo_root, _ = validate_issue_paths(issue)
    completed = subprocess.run(["git", "diff", "--", "."], cwd=str(repo_root), capture_output=True, text=True)
    return completed.stdout + ("\nSTDERR:\n" + completed.stderr if completed.stderr else "")


def head_show(issue: Issue) -> str:
    repo_root, _ = validate_issue_paths(issue)
    if not _repo_has_head_commit(repo_root):
        return "Repository has no initial commit yet."
    completed = subprocess.run(["git", "show", "--stat", "HEAD"], cwd=str(repo_root), capture_output=True, text=True)
    return completed.stdout + ("\nSTDERR:\n" + completed.stderr if completed.stderr else "")


def push_branch(issue: Issue, branch_name: str) -> None:
    repo_root, _ = validate_issue_paths(issue)
    _run_git(["checkout", branch_name], cwd=repo_root)
    completed = subprocess.run(
        ["git", "push", "--force-with-lease", "-u", settings.github_remote, branch_name],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Failed to push branch {branch_name}.\n{completed.stderr or completed.stdout}")


def commit_all_changes(issue: Issue, message: str, branch_name: str | None = None) -> bool:
    repo_root, _ = validate_issue_paths(issue)
    if branch_name:
        _run_git(["checkout", branch_name], cwd=repo_root)
    _run_git(["add", "-A"], cwd=repo_root)
    status = subprocess.run(["git", "status", "--short"], cwd=str(repo_root), capture_output=True, text=True)
    if not status.stdout.strip():
        save_text(issue_dir(issue.issue_id) / "commit_status.txt", "result=no_changes\n")
        return False
    try:
        _run_git(["commit", "-m", message], cwd=repo_root)
    except RuntimeError as exc:
        error_text = str(exc)
        if "Author identity unknown" in error_text or "unable to auto-detect email address" in error_text:
            _run_git(["config", "user.name", "Codex Remediation Bot"], cwd=repo_root)
            _run_git(["config", "user.email", "codex-remediation@example.local"], cwd=repo_root)
            _run_git(["commit", "-m", message], cwd=repo_root)
        else:
            raise
    save_text(issue_dir(issue.issue_id) / "commit_status.txt", "result=committed\n")
    return True


def create_pull_request(issue: Issue, branch_name: str, body: str = "") -> tuple[str, int | None]:
    api_created = create_pull_request_via_api(issue, branch_name, body=body)
    if api_created[0]:
        return api_created

    gh_command = (settings.gh_command or "").strip()
    if not gh_command or gh_command.lower() == "disabled":
        compare_url = compare_url_from_branch(issue, branch_name)
        if compare_url:
            save_text(
                issue_dir(issue.issue_id) / "pr_info.txt",
                f"url={compare_url}\nnumber=\nmode=compare_link\n",
            )
            return compare_url, None
        raise RuntimeError("PR creation tooling is unavailable. Configure GITHUB_TOKEN or GH_COMMAND.")

    repo_root, _ = validate_issue_paths(issue)
    title = f"{issue.issue_id}: {issue.title}"
    base_branch = issue.base_branch or settings.default_pr_base or "main"
    body_text = body.strip() or (
        f"Automated remediation for {issue.issue_id}.\n\n"
        f"Issue: {issue.title}\n"
        "Generated by Codex Native MVP."
    )
    try:
        completed = subprocess.run(
            [
                gh_command,
                "pr",
                "create",
                "--base",
                base_branch,
                "--head",
                branch_name,
                "--title",
                title,
                "--body",
                body_text,
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        compare_url = compare_url_from_branch(issue, branch_name)
        if compare_url:
            save_text(
                issue_dir(issue.issue_id) / "pr_info.txt",
                f"url={compare_url}\nnumber=\nmode=compare_link\n",
            )
            return compare_url, None
        raise RuntimeError(f"PR command not found: {gh_command}")
    if completed.returncode != 0:
        compare_url = compare_url_from_branch(issue, branch_name)
        if compare_url:
            save_text(
                issue_dir(issue.issue_id) / "pr_info.txt",
                f"url={compare_url}\nnumber=\nmode=compare_link\n",
            )
            return compare_url, None
        raise RuntimeError(f"Failed to create PR.\n{completed.stderr or completed.stdout}")

    pr_url = completed.stdout.strip().splitlines()[-1].strip()
    pr_number = pr_number_from_url(pr_url)
    save_text(issue_dir(issue.issue_id) / "pr_info.txt", f"url={pr_url}\nnumber={pr_number or ''}\nmode=created\n")
    return pr_url, pr_number


def pr_url_from_branch(issue: Issue, branch_name: str) -> str:
    gh_command = (settings.gh_command or "").strip()
    if not gh_command or gh_command.lower() == "disabled":
        return ""
    repo_root, _ = validate_issue_paths(issue)
    try:
        completed = subprocess.run(
            [gh_command, "pr", "view", branch_name, "--json", "url", "-q", ".url"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def compare_url_from_branch(issue: Issue, branch_name: str) -> str:
    github_repo = (issue.github_repo or "").strip()
    if not github_repo:
        return ""
    base_branch = issue.base_branch or settings.default_pr_base or "main"
    branch_part = quote(branch_name, safe="")
    base_part = quote(base_branch, safe="")
    return f"{settings.github_base_url.rstrip('/')}/{github_repo}/compare/{base_part}...{branch_part}?expand=1"


def create_pull_request_via_api(issue: Issue, branch_name: str, body: str = "") -> tuple[str, int | None]:
    github_repo = (issue.github_repo or "").strip()
    token = settings.github_token.strip()
    if not github_repo or not token:
        return "", None

    title = f"{issue.issue_id}: {issue.title}"
    base_branch = issue.base_branch or settings.default_pr_base or "main"
    body_text = body.strip() or (
        f"Automated remediation for {issue.issue_id}.\n\n"
        f"Issue: {issue.title}\n"
        "Generated by Codex Native MVP."
    )
    payload = json.dumps(
        {
            "title": title,
            "head": branch_name,
            "base": base_branch,
            "body": body_text,
        }
    ).encode("utf-8")
    request = Request(
        f"{settings.github_api_base_url.rstrip('/')}/repos/{github_repo}/pulls",
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "codex-native-mvp",
        },
    )

    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError):
        return "", None

    pr_url = str(data.get("html_url", "")).strip()
    pr_number = data.get("number")
    if pr_url:
        save_text(issue_dir(issue.issue_id) / "pr_info.txt", f"url={pr_url}\nnumber={pr_number or ''}\nmode=api_created\n")
        return pr_url, pr_number if isinstance(pr_number, int) else None
    return "", None


def pr_number_from_url(pr_url: str) -> int | None:
    if not pr_url:
        return None
    try:
        return int(pr_url.rstrip("/").split("/")[-1])
    except (TypeError, ValueError):
        return None


def github_issue_body_from_payload(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
