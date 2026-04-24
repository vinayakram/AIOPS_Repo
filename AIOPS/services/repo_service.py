from __future__ import annotations

import json
import os
import re
import subprocess
import zipfile
import base64
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse, urlencode
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
    try:
        _run_git(["stash", "push", "--include-untracked", "-m", stash_message], cwd=repo_root)
    except RuntimeError as exc:
        if _is_git_object_permission_error(str(exc)):
            return _snapshot_and_restore_dirty_repo(issue, repo_root, stash_message, str(exc))
        raise
    save_text(issue_dir(issue.issue_id) / "auto_stash.txt", f"message={stash_message}\nrepo_root={repo_root}\n")
    return stash_message


def _is_git_object_permission_error(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "insufficient permission for adding an object to repository database" in lowered
        or ".git/objects" in lowered and "permission" in lowered
        or "cannot save the current worktree state" in lowered
    )


def _snapshot_and_restore_dirty_repo(issue: Issue, repo_root: Path, stash_message: str, reason: str) -> str:
    """Clean a dirty worktree without writing to the Git object database.

    Git stash/commit both write objects. Some demo VMs have root-owned object
    directories, so this fallback saves a patch plus untracked files under the
    run folder and restores tracked files from HEAD by reading blobs only.
    """
    run_dir = issue_dir(issue.issue_id)
    snapshot_dir = run_dir / "worktree_snapshot"
    untracked_dir = snapshot_dir / "untracked"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    untracked_dir.mkdir(parents=True, exist_ok=True)

    diff = subprocess.run(
        ["git", "diff", "--binary", "--", "."],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    save_text(snapshot_dir / "tracked_changes.patch", diff.stdout + ("\nSTDERR:\n" + diff.stderr if diff.stderr else ""))

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if status.returncode != 0:
        raise RuntimeError(status.stderr or status.stdout or "Could not inspect dirty worktree for fallback cleanup.")

    restored: list[str] = []
    moved: list[str] = []
    for raw in status.stdout.splitlines():
        if not raw.strip():
            continue
        code = raw[:2]
        path_text = raw[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1].strip()
        rel_path = Path(path_text)
        full_path = (repo_root / rel_path).resolve()
        if not str(full_path).startswith(str(repo_root)):
            continue

        if code == "??":
            if full_path.exists():
                target = untracked_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(full_path), str(target))
                moved.append(rel_path.as_posix())
            continue

        head_blob = subprocess.run(
            ["git", "show", f"HEAD:{rel_path.as_posix()}"],
            cwd=str(repo_root),
            capture_output=True,
        )
        if head_blob.returncode == 0:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(head_blob.stdout)
            restored.append(rel_path.as_posix())
        elif full_path.exists():
            target = snapshot_dir / "removed_tracked" / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(full_path), str(target))
            moved.append(rel_path.as_posix())

    save_text(
        run_dir / "auto_stash.txt",
        "\n".join(
            [
                f"message={stash_message}",
                f"repo_root={repo_root}",
                "mode=file_snapshot_restore",
                "reason=" + reason.replace("\n", "\\n"),
                f"snapshot_dir={snapshot_dir}",
                "restored_tracked=" + ",".join(restored),
                "moved_untracked=" + ",".join(moved),
            ]
        )
        + "\n",
    )
    append_progress(
        issue.issue_id,
        "Git stash failed because the local object database is not writable; cleaned worktree using file snapshot fallback.",
    )
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
    push_remote = _tokenized_github_push_remote(issue) or settings.github_remote
    completed = subprocess.run(
        ["git", "push", "--force-with-lease", "-u", push_remote, branch_name],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        output = _sanitize_github_token(completed.stderr or completed.stdout)
        raise RuntimeError(f"Failed to push branch {branch_name}.\n{output}")


def _tokenized_github_push_remote(issue: Issue) -> str:
    token = settings.github_token.strip()
    github_repo = (issue.github_repo or "").strip().strip("/")
    if not token or not github_repo:
        return ""

    parsed = urlparse(settings.github_base_url.rstrip("/") or "https://github.com")
    scheme = parsed.scheme or "https"
    host = parsed.netloc or parsed.path
    if not host:
        return ""
    return f"{scheme}://x-access-token:{quote(token, safe='')}@{host}/{github_repo}.git"


def _sanitize_github_token(text: str) -> str:
    token = settings.github_token.strip()
    if not token:
        return text
    return text.replace(token, "***").replace(quote(token, safe=""), "***")


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


def create_branch_and_pull_request_via_api_from_worktree(
    issue: Issue,
    branch_name: str,
    body: str = "",
) -> tuple[str, int | None]:
    """Create the remediation branch/commit/PR through GitHub API.

    This fallback is used when the local Git object database is not writable.
    It still reads the local working-tree diff, but avoids `git add`,
    `git commit`, and `git push`.
    """
    github_repo = (issue.github_repo or "").strip()
    token = settings.github_token.strip()
    if not github_repo or not token:
        return "", None

    repo_root, _ = validate_issue_paths(issue)
    changed = _changed_worktree_files(repo_root)
    if not changed:
        raise RuntimeError("GitHub API fallback found no working-tree changes to commit.")

    base_branch = issue.base_branch or settings.default_pr_base or "main"
    base_ref = _github_api_json(issue, "GET", f"/repos/{github_repo}/git/ref/heads/{quote(base_branch, safe='/')}")
    base_sha = ((base_ref.get("object") or {}).get("sha") or "").strip()
    if not base_sha:
        raise RuntimeError(f"Could not resolve GitHub base branch {base_branch}.")

    base_commit = _github_api_json(issue, "GET", f"/repos/{github_repo}/git/commits/{base_sha}")
    base_tree_sha = ((base_commit.get("tree") or {}).get("sha") or "").strip()
    if not base_tree_sha:
        raise RuntimeError(f"Could not resolve GitHub base tree for {base_branch}.")

    tree_entries = []
    for path, status in changed:
        if status == "D":
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        full_path = (repo_root / path).resolve()
        if not full_path.is_file():
            continue
        content = full_path.read_bytes()
        try:
            decoded = content.decode("utf-8")
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "content": decoded})
        except UnicodeDecodeError:
            blob = _github_api_json(
                issue,
                "POST",
                f"/repos/{github_repo}/git/blobs",
                {"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
            )
            tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob.get("sha")})

    if not tree_entries:
        raise RuntimeError("GitHub API fallback found no readable changed files to commit.")

    tree = _github_api_json(
        issue,
        "POST",
        f"/repos/{github_repo}/git/trees",
        {"base_tree": base_tree_sha, "tree": tree_entries},
    )
    tree_sha = str(tree.get("sha") or "").strip()
    if not tree_sha:
        raise RuntimeError("GitHub API fallback could not create tree.")

    commit = _github_api_json(
        issue,
        "POST",
        f"/repos/{github_repo}/git/commits",
        {
            "message": f"{issue.issue_id}: {issue.title}",
            "tree": tree_sha,
            "parents": [base_sha],
        },
    )
    commit_sha = str(commit.get("sha") or "").strip()
    if not commit_sha:
        raise RuntimeError("GitHub API fallback could not create commit.")

    ref_path = f"/repos/{github_repo}/git/refs"
    ref_payload = {"ref": f"refs/heads/{branch_name}", "sha": commit_sha}
    try:
        _github_api_json(issue, "POST", ref_path, ref_payload)
    except RuntimeError as exc:
        if "Reference already exists" not in str(exc) and "already_exists" not in str(exc):
            raise
        _github_api_json(
            issue,
            "PATCH",
            f"/repos/{github_repo}/git/refs/heads/{quote(branch_name, safe='/')}",
            {"sha": commit_sha, "force": True},
        )

    pr = create_pull_request_via_api(issue, branch_name, body=body)
    if pr[0]:
        save_text(issue_dir(issue.issue_id) / "commit_status.txt", "result=api_committed\n")
        return pr

    existing = _existing_pull_request_via_api(issue, branch_name)
    if existing[0]:
        save_text(issue_dir(issue.issue_id) / "commit_status.txt", "result=api_committed_existing_pr\n")
        return existing

    compare_url = compare_url_from_branch(issue, branch_name)
    if compare_url:
        save_text(issue_dir(issue.issue_id) / "pr_info.txt", f"url={compare_url}\nnumber=\nmode=api_compare_link\n")
        return compare_url, None
    return "", None


def _changed_worktree_files(repo_root: Path) -> list[tuple[str, str]]:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or "Could not inspect working-tree changes.")
    files: list[tuple[str, str]] = []
    for raw in completed.stdout.splitlines():
        if not raw.strip():
            continue
        status = raw[:2]
        path = raw[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        change = "D" if "D" in status else "M"
        files.append((path, change))
    return files


def _github_api_json(issue: Issue, method: str, path: str, payload: dict | None = None) -> dict:
    token = settings.github_token.strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not configured.")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{settings.github_api_base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "codex-native-mvp",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_sanitize_github_token(detail)) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(_sanitize_github_token(str(exc))) from exc


def _existing_pull_request_via_api(issue: Issue, branch_name: str) -> tuple[str, int | None]:
    github_repo = (issue.github_repo or "").strip()
    if not github_repo:
        return "", None
    owner = github_repo.split("/", 1)[0]
    query = urlencode({"head": f"{owner}:{branch_name}", "state": "open"})
    pulls = _github_api_json(issue, "GET", f"/repos/{github_repo}/pulls?{query}")
    if isinstance(pulls, list) and pulls:
        pr = pulls[0]
        pr_url = str(pr.get("html_url") or "").strip()
        pr_number = pr.get("number")
        if pr_url:
            save_text(issue_dir(issue.issue_id) / "pr_info.txt", f"url={pr_url}\nnumber={pr_number or ''}\nmode=api_existing\n")
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
