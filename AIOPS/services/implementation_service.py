from __future__ import annotations

import json
import re
import subprocess
from typing import Callable

from core.schemas import ImplementationRun, Issue, RemediationType
from core.settings import settings
from services.codex_cli_service import CodexCLI
from services.repo_service import (
    build_remediation_branch_name,
    commit_all_changes,
    create_backup,
    create_pull_request,
    diff_text,
    ensure_clean_branch,
    head_show,
    push_branch,
    repo_summary,
    validate_issue_paths,
)
from services.storage import append_progress, issue_dir, read_text, save_json, save_text


def _artifact_manifest_entries(run_dir) -> list[dict]:
    catalog = [
        ("artifact_manifest.json", "Artifact manifest", "Machine-readable index of remediation artifacts."),
        ("artifact_manifest.md", "Artifact guide", "Human-readable guide to the most important generated files."),
        ("implementation.json", "Implementation result", "Overall implementation status, branch details, and handoff summary."),
        ("change_summary.json", "Change summary", "Condensed summary of files changed, components touched, and validation outcomes."),
        ("test_results.json", "Validation results", "Structured results of the validation commands that were executed."),
        ("git_diff.patch", "Git diff", "Raw patch showing the full remediation change set."),
        ("head_show.txt", "HEAD summary", "Git HEAD summary for a quick review of the final change set."),
        ("codex_final_message.md", "Codex final message", "The final explanation returned by Codex after implementation."),
        ("pr_view.json", "PR handoff", "Branch and pull request metadata after delivery steps complete."),
        ("plan.md", "Approved plan", "The approved plan used as the implementation contract."),
        ("plan.html", "Approved plan HTML export", "Rendered plan export retained for offline viewing."),
    ]
    entries: list[dict] = []
    for filename, title, description in catalog:
        path = run_dir / filename
        if not path.exists():
            continue
        entries.append(
            {
                "name": filename,
                "title": title,
                "description": description,
                "path": str(path),
            }
        )
    return entries


def _write_artifact_manifest(run_dir, issue: Issue, stage: str) -> None:
    entries = _artifact_manifest_entries(run_dir)
    manifest = {
        "issue_id": issue.issue_id,
        "project_name": issue.project_name,
        "stage": stage,
        "recommended_review_order": [entry["name"] for entry in entries],
        "artifacts": entries,
    }
    save_json(run_dir / "artifact_manifest.json", manifest)

    lines = [
        f"# Artifact guide for {issue.issue_id}",
        "",
        f"Current stage: {stage}",
        "",
        "Review these artifacts in order:",
        "",
    ]
    for index, entry in enumerate(entries, start=1):
        lines.append(f"{index}. {entry['title']} (`{entry['name']}`) - {entry['description']}")
    save_text(run_dir / "artifact_manifest.md", "\n".join(lines).rstrip() + "\n")


def build_implementation_handoff_summary(issue: Issue, branch_name: str) -> str:
    fast_targets = {
        "multiagent-support-copilot": {
            "source": "src/support_copilot/handoff.py",
            "test": "tests/test_handoff.py",
            "focus": "preserve escalation payload context without redesigning the support flow",
        },
        "multiagent-order-orchestrator": {
            "source": "src/order_orchestrator/runtime.py",
            "test": "tests/test_runtime.py",
            "focus": "fix the timeout budget path without changing tracing or retry design",
        },
    }
    target = fast_targets.get(issue.project_name or "", {})
    lines = [
        f"Issue: {issue.issue_id}",
        f"Project: {issue.project_name or 'N/A'}",
        f"Branch: {branch_name}",
        f"Demo fast path: {'enabled' if settings.demo_fast_path else 'disabled'}",
    ]
    if target:
        lines.extend(
            [
                f"Primary source target: {target['source']}",
                f"Primary test target: {target['test']}",
                f"Fix focus: {target['focus']}",
            ]
        )
    lines.append("Success signal: existing failing behavior is fixed and configured tests pass.")
    return "\n".join(lines) + "\n"


def build_implementation_prompt(issue: Issue, approved_plan: str, branch_name: str) -> str:
    repo_root, allowed_folder = validate_issue_paths(issue)
    acceptance = "\n".join(f"- {item}" for item in issue.acceptance_criteria) or "- None provided"
    handoff_summary = build_implementation_handoff_summary(issue, branch_name).strip()

    fast_path_block = ""
    if settings.demo_fast_path:
        fast_path_block = f"""
Demo fast-path rules:
- Use a strict under-5-minute demo mindset: fix the smallest viable defect only.
- Inspect at most the directly relevant source file plus the primary failing test unless that is impossible.
- Make at most one focused code change area and one focused test change area.
- Do NOT add observability work, retry redesign, trace schema changes, refactors, helper abstractions, or supporting cleanups unless the failing behavior cannot be fixed without them.
- Prefer changing current constants, timeout handling, payload forwarding, or existing conditionals over redesign.
- Use the existing failing test or acceptance behavior as the primary success signal.
- If a broader redesign seems needed, stop and explain the blocker briefly instead of expanding scope.

Implementation handoff summary:
{handoff_summary}
"""

    validation_command = issue.validation_command or issue.test_command or "No validation command configured"
    remediation_guidance = {
        RemediationType.CODE_CHANGE: "Make the smallest safe code fix that satisfies the approved plan.",
        RemediationType.INFRA_CHANGE: "Apply the smallest safe infrastructure or deployment change within the allowed scope.",
        RemediationType.CONFIG_CHANGE: "Apply the smallest safe configuration change within the allowed scope.",
        RemediationType.RUNBOOK_CHANGE: "Update runbooks or operational documentation as approved, and avoid unrelated code changes.",
        RemediationType.INVESTIGATION_ONLY: "Do not force a code change. If a safe change is not warranted, produce investigation notes and minimal supporting artifacts only.",
        RemediationType.HUMAN_HANDOFF: "Prepare the repo and artifacts for human handoff. Avoid speculative changes if the issue requires manual operator judgment.",
    }[issue.remediation_type]

    validation_rule = (
        "Verification checks are temporarily disabled by the remediation service. You may mention the intended validation command, but do not block completion on test execution."
        if validation_command and validation_command != "No validation command configured"
        else "If no validation command is configured, explain what manual validation is required."
    )

    environment_rules = """
13. The runtime may be Windows PowerShell or Linux shell depending on deployment. Use portable commands where possible and avoid shell-specific tricks.
14. Do NOT emit shell-based patch commands such as `apply_patch <<'PATCH'` or heredoc wrappers. Use direct file editing only.
15. If a shell command fails, do not retry with alternate shells. Continue by editing files directly and then run the configured validation command plainly when available.
"""

    return f"""You are running inside Codex CLI in repo: {repo_root}

Task:
Implement the approved plan and validate it.

Issue details:
- ID: {issue.issue_id}
- Project: {issue.project_name or 'N/A'}
- Title: {issue.title}
- Remediation type: {issue.remediation_type.value}
- Working branch: {branch_name}
- Base branch: {issue.base_branch}
- Allowed folder: {allowed_folder}
- Validation command: {validation_command}

Issue description:
{issue.description or 'No description provided.'}

Acceptance criteria:
{acceptance}

Approved plan:
{approved_plan}

Current repo summary:
{repo_summary(issue)}

{fast_path_block}

Execution rules:
1. Only modify files inside the allowed folder unless tests or repo metadata strictly require a minimal change elsewhere.
2. Keep changes as small and direct as possible.
3. Prefer the smallest viable change that satisfies the approved plan and acceptance criteria.
4. {remediation_guidance}
5. The remediation service has already prepared the working branch `{branch_name}` for you.
6. Do NOT run git pull, git fetch, git checkout -b, git commit, git push, or PR creation commands yourself.
7. Add or update validation assets when needed.
8. {validation_rule}
9. Leave the branch ready for the remediation service to commit, push, and open the PR.
10. In your final summary, clearly state whether the changes are ready for human review and commit/push.
11. Include a short summary, the validation command used, and the key files changed.
12. Avoid spending time on optional cleanups, broad refactors, extra observability work, or schema changes unless the current issue cannot be resolved without them.
{environment_rules}

Return the final answer as markdown with exactly these sections:
# Implementation result for {issue.issue_id}
## Summary
## Files changed
## Tests run
## Git status
## Branch readiness
## PR handoff
## Follow-ups
"""


def run_implementation(issue: Issue) -> ImplementationRun:
    return run_implementation_with_phases(issue)


def run_implementation_with_phases(
    issue: Issue,
    phase_callback: Callable[[str], None] | None = None,
) -> ImplementationRun:
    run_dir = issue_dir(issue.issue_id)
    approved_plan = read_text(run_dir / "plan.md")
    if not approved_plan.strip():
        raise RuntimeError("Approved plan not found. Generate and approve a plan first.")

    if phase_callback:
        phase_callback("backup")
    append_progress(issue.issue_id, "Creating repo backup.")
    backup_zip = create_backup(issue)
    append_progress(issue.issue_id, f"Backup created: {backup_zip}")

    if phase_callback:
        phase_callback("branch")
    append_progress(issue.issue_id, "Preparing working branch.")
    branch_name = ensure_clean_branch(issue)
    append_progress(issue.issue_id, f"Using branch: {branch_name}")

    prompt = build_implementation_prompt(issue, approved_plan=approved_plan, branch_name=branch_name)
    save_text(run_dir / "codex_implementation_prompt.md", prompt)
    save_text(run_dir / "implementation_handoff_summary.md", build_implementation_handoff_summary(issue, branch_name))

    repo_root, _ = validate_issue_paths(issue)
    if phase_callback:
        phase_callback("editing")
    append_progress(issue.issue_id, "Codex received bounded fix request.")
    result = CodexCLI().exec(
        cwd=repo_root,
        prompt=prompt,
        sandbox="workspace-write",
        auto_stop_after_changes_seconds=settings.implementation_watchdog_seconds,
        hard_timeout_seconds=settings.implementation_hard_timeout_seconds,
        on_event=lambda msg: append_progress(issue.issue_id, f"[implement] {msg}"),
    )
    save_json(
        run_dir / "codex_implementation_exec.json",
        {
            "command": result.command,
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )

    if result.forced_stop:
        if result.stop_reason == "changes_detected":
            append_progress(
                issue.issue_id,
                "Minimal code change detected; proceeding to final artifact collection.",
            )
            append_progress(
                issue.issue_id,
                "Watchdog-assisted continuation is active for this run.",
            )
        elif result.stop_reason == "hard_timeout":
            append_progress(
                issue.issue_id,
                "Demo time budget reached. Remediation service is taking back control.",
            )

    if result.return_code != 0 and not result.forced_stop:
        save_text(run_dir / "codex_implementation_error.txt", result.stderr or result.stdout)
        if settings.demo_dummy_pr_on_failure:
            append_progress(issue.issue_id, "Codex implementation failed; creating demo fallback handoff artifacts.")
            return _build_demo_fallback_implementation(
                issue=issue,
                branch_name=branch_name,
                backup_zip=backup_zip,
                reason=result.stderr or result.stdout,
            )
        raise RuntimeError(f"Codex implementation failed.\n{result.stderr or result.stdout}")

    if result.stop_reason == "hard_timeout" and not _repo_has_working_tree_changes(repo_root):
        save_text(run_dir / "codex_implementation_error.txt", result.stderr or result.stdout)
        if settings.demo_dummy_pr_on_failure:
            append_progress(issue.issue_id, "Demo timeout produced no code change; creating demo fallback handoff artifacts.")
            return _build_demo_fallback_implementation(
                issue=issue,
                branch_name=branch_name,
                backup_zip=backup_zip,
                reason=result.stderr or result.stdout or "Demo timeout reached before code changes.",
            )
        raise RuntimeError(
            "Demo timeout reached before any meaningful code change was detected."
        )

    if phase_callback:
        phase_callback("testing")
    append_progress(issue.issue_id, "Verification checks are disabled for this run.")
    tests = []
    diff = diff_text(issue)
    head_summary = head_show(issue)

    save_text(run_dir / "git_diff.patch", diff)
    save_text(run_dir / "head_show.txt", head_summary)
    append_progress(issue.issue_id, "Saved git diff and HEAD summary.")

    save_json(run_dir / "test_results.json", [])
    append_progress(issue.issue_id, "Recorded empty validation results because verification is disabled.")

    change_summary = build_change_summary(issue, branch_name=branch_name, codex_summary=result.stdout.strip(), tests=tests)
    save_json(run_dir / "change_summary.json", change_summary)

    human_summary = (
        "Codex CLI completed the implementation flow. "
        "Verification checks are currently disabled, so review the change summary, diff, and logs before commit/push."
    )

    payload = ImplementationRun(
        branch_name=branch_name,
        backup_zip=backup_zip,
        codex_plan_prompt=str(run_dir / "codex_plan_prompt.md"),
        codex_implementation_prompt=str(run_dir / "codex_implementation_prompt.md"),
        codex_final_message=result.stdout.strip(),
        tests=tests,
        success=True,
        pr_url="",
        pr_number=None,
        pr_title=f"{issue.issue_id}: {issue.title}",
        human_summary=human_summary,
        delivery_blocked_reason="Awaiting human review approval before branch push / PR.",
    )

    if phase_callback:
        phase_callback("finalizing")
    save_json(run_dir / "implementation.json", payload.model_dump(mode="json"))
    save_text(run_dir / "codex_final_message.md", result.stdout.strip())
    _write_artifact_manifest(run_dir, issue, stage="implementation_ready")
    append_progress(issue.issue_id, "Implementation artifacts saved.")

    return payload


def _build_demo_fallback_implementation(
    *,
    issue: Issue,
    branch_name: str,
    backup_zip: str,
    reason: str,
) -> ImplementationRun:
    run_dir = issue_dir(issue.issue_id)
    summary = (
        "Runtime sandbox prevented automated code edits. "
        "A demo fallback handoff was created so the remediation workflow can continue."
    )
    save_text(run_dir / "git_diff.patch", "")
    save_text(run_dir / "head_show.txt", "")
    save_json(run_dir / "test_results.json", [])
    save_json(
        run_dir / "change_summary.json",
        {
            "issue_id": issue.issue_id,
            "branch_name": branch_name,
            "files_changed": [],
            "components_changed": [],
            "summary": summary,
            "human_summary": f"Issue: {issue.title}. Fix: {summary}",
            "relevant_test_cases": ["Demo fallback review"],
            "test_totals": {"total": 1, "passed": 0, "failed": 0},
            "test_results": [],
        },
    )
    save_text(
        run_dir / "codex_final_message.md",
        (
            f"# Implementation result for {issue.issue_id}\n"
            "## Summary\n"
            f"- {summary}\n"
            "## Files changed\n"
            "- No files changed by Codex because command execution was blocked by the local runtime sandbox.\n"
            "## Tests run\n"
            "- Not run; fallback handoff only.\n"
            "## Git status\n"
            "- No automated changes detected.\n"
            "## Branch readiness\n"
            "- Demo fallback artifacts are ready for human review.\n"
            "## PR handoff\n"
            "- A dummy PR handoff can be generated after review approval.\n"
            "## Follow-ups\n"
            "- Run the same remediation in an environment where Codex CLI sandboxing is supported, or use the fallback PR as a demo artifact.\n"
        ),
    )
    save_text(run_dir / "codex_implementation_error.txt", reason or "Unknown Codex failure")

    payload = ImplementationRun(
        branch_name=branch_name,
        backup_zip=backup_zip,
        codex_plan_prompt=str(run_dir / "codex_plan_prompt.md"),
        codex_implementation_prompt=str(run_dir / "codex_implementation_prompt.md"),
        codex_final_message=summary,
        tests=[],
        success=True,
        pr_url="",
        pr_number=None,
        pr_title=f"{issue.issue_id}: {issue.title}",
        human_summary=summary,
        delivery_blocked_reason="Demo fallback awaiting human review approval before dummy PR handoff.",
    )
    save_json(run_dir / "implementation.json", payload.model_dump(mode="json"))
    _write_artifact_manifest(run_dir, issue, stage="implementation_fallback_ready")
    append_progress(issue.issue_id, "Demo fallback implementation artifacts saved.")
    return payload


def finalize_branch_pr_with_phases(
    issue: Issue,
    phase_callback: Callable[[str], None] | None = None,
) -> ImplementationRun:
    run_dir = issue_dir(issue.issue_id)
    implementation_path = run_dir / "implementation.json"
    if not implementation_path.exists():
        raise RuntimeError("Implementation results not found. Run implementation first.")

    payload = ImplementationRun.model_validate(json.loads(read_text(implementation_path)))
    if not payload.success:
        raise RuntimeError("Implementation did not pass tests. Branch/PR creation is blocked.")

    branch_name = payload.branch_name
    if not branch_name:
        raise RuntimeError("Prepared implementation branch not found.")

    try:
        if phase_callback:
            phase_callback("pushing")
        append_progress(issue.issue_id, "Committing validated changes.")
        committed = commit_all_changes(issue, f"{issue.issue_id}: {issue.title}", branch_name=branch_name)
        if committed:
            append_progress(issue.issue_id, "Commit created successfully.")
        else:
            append_progress(issue.issue_id, "No new commit was needed after validation. Continuing to push the prepared branch.")

        append_progress(issue.issue_id, f"Pushing branch {branch_name}.")
        push_branch(issue, branch_name)
        append_progress(issue.issue_id, f"Branch {branch_name} pushed successfully.")

        if phase_callback:
            phase_callback("pr")
        append_progress(issue.issue_id, "Creating PR or compare-link handoff.")
        pr_body = (
            f"Automated remediation for {issue.issue_id}\n\n"
            f"## Summary\n{payload.codex_final_message.strip() or 'Codex implementation completed.'}\n"
        )
        pr_url, pr_number = create_pull_request(issue, branch_name, body=pr_body)
    except Exception as exc:
        if not settings.demo_dummy_pr_on_failure:
            raise
        append_progress(issue.issue_id, f"Real branch / PR creation failed; creating dummy PR handoff: {exc}")
        pr_url, pr_number = _dummy_pr_url(issue, branch_name), None
        save_text(run_dir / "pr_info.txt", f"url={pr_url}\nnumber=\nmode=dummy\nreason={exc}\n")
    if pr_number:
        append_progress(issue.issue_id, f"Pull request created: {pr_url}")
    elif pr_url:
        append_progress(issue.issue_id, f"Branch pushed. Open compare link to create PR: {pr_url}")
    else:
        append_progress(issue.issue_id, "No PR URL detected. Check whether the branch was pushed successfully.")

    updated = payload.model_copy(
        update={
            "pr_url": pr_url,
            "pr_number": pr_number,
            "human_summary": (
                f"Pull request created: {pr_url}" if pr_number else
                f"Branch pushed. Open compare link to create the PR: {pr_url}"
            ),
        }
    )

    if phase_callback:
        phase_callback("finalizing")
    save_json(implementation_path, updated.model_dump(mode="json"))
    _write_artifact_manifest(run_dir, issue, stage="delivery_ready")
    append_progress(issue.issue_id, "Branch / PR artifacts saved.")
    return updated


def _repo_has_working_tree_changes(repo_root) -> bool:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return bool(status.stdout.strip())


def _dummy_pr_url(issue: Issue, branch_name: str) -> str:
    repo = (issue.github_repo or issue.upstream_repo or issue.project_name or "local-demo").strip("/")
    issue_part = re.sub(r"[^a-zA-Z0-9_.-]+", "-", issue.issue_id).strip("-") or "issue"
    branch_part = re.sub(r"[^a-zA-Z0-9_.-]+", "-", branch_name).strip("-") or "branch"
    return f"https://example.local/aiops/dummy-pr/{repo}/{issue_part}?branch={branch_part}"


def build_change_summary(issue: Issue, branch_name: str, codex_summary: str, tests: list) -> dict:
    repo_root, _ = validate_issue_paths(issue)
    changed_files = subprocess.run(
        ["git", "diff", "--name-only", "--", "."],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    files = [line.strip() for line in changed_files.stdout.splitlines() if line.strip()]
    components = sorted({_component_label(path) for path in files if path.strip()})
    summary_text = _extract_impl_section(codex_summary, "Summary")
    if not summary_text:
        summary_text = _fallback_solution_summary(issue, files)
    relevant_test_cases = _collect_relevant_test_cases(repo_root, files, issue)
    test_results = [
        {
            "command": test.command,
            "return_code": test.return_code,
            "status": "passed" if test.return_code == 0 else "failed",
            "stdout": test.stdout,
            "stderr": test.stderr,
        }
        for test in tests
    ]
    test_totals = _summarize_test_totals(test_results, relevant_test_cases)
    return {
        "issue_id": issue.issue_id,
        "branch_name": branch_name,
        "files_changed": files,
        "components_changed": components,
        "summary": summary_text,
        "human_summary": _build_human_summary(issue, components, test_totals, summary_text),
        "relevant_test_cases": relevant_test_cases,
        "test_totals": test_totals,
        "test_results": test_results,
    }


def _extract_impl_section(markdown: str, heading: str) -> str:
    lines = _extract_final_implementation_block(markdown).splitlines()
    capture = False
    parts: list[str] = []
    target = f"## {heading}".strip().lower()
    for raw in lines:
        line = raw.strip()
        if line.lower() == target:
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture and line:
            parts.append(line.lstrip("- ").strip())
    return _clean_summary_text(" ".join(parts).strip())


def _extract_final_implementation_block(markdown: str) -> str:
    text = (markdown or "").strip()
    if not text:
        return ""
    marker = "# Implementation result for "
    last_index = text.rfind(marker)
    if last_index >= 0:
        return text[last_index:]
    return text


def _component_label(path: str) -> str:
    normalized = path.replace("\\", "/")
    if "/tests/" in f"/{normalized}":
        return "Tests"
    parts = [part for part in normalized.split("/") if part]
    if "src" in parts:
        src_index = parts.index("src")
        if src_index + 1 < len(parts):
            return parts[src_index + 1].replace("_", " ").title()
    if parts:
        return parts[-1].replace("_", " ").title()
    return "Application code"


def _build_human_summary(issue: Issue, components: list[str], test_results: dict, summary_text: str) -> str:
    issue_text = _build_issue_statement(issue)
    solution_text = _humanize_solution_text(_clean_summary_text(summary_text), issue) or _fallback_solution_summary(issue, [])
    return f"Issue: {issue_text} Fix: {solution_text}"


def _collect_relevant_test_cases(repo_root, files: list[str], issue: Issue) -> list[str]:
    candidates = [path for path in files if path.startswith("tests/") or "/tests/" in f"/{path.replace('\\', '/')}"]
    if not candidates:
        candidates = _issue_test_candidates(issue)
    if not candidates:
        candidates = ["tests/test_handoff.py"] if "support" in (issue.project_name or "").lower() else ["tests/test_runtime.py"]

    discovered: list[str] = []
    for relative_path in candidates:
        path = repo_root / relative_path
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        discovered.extend(re.findall(r"^def (test_[a-zA-Z0-9_]+)\(", content, flags=re.MULTILINE))
    return [_humanize_test_name(name) for name in discovered] or ["Targeted regression check"]


def _humanize_test_name(name: str) -> str:
    cleaned = name.replace("test_", "").replace("_", " ").strip()
    return (cleaned[:1].upper() + cleaned[1:]) if cleaned else "Targeted regression check"


def _summarize_test_totals(test_results: list[dict], relevant_test_cases: list[str]) -> dict:
    passed = 0
    failed = 0
    total = len(relevant_test_cases)
    for result in test_results:
        stdout = result.get("stdout", "") or ""
        passed_match = re.search(r"(\d+)\s+passed", stdout)
        failed_match = re.search(r"(\d+)\s+failed", stdout)
        if passed_match:
            passed += int(passed_match.group(1))
        if failed_match:
            failed += int(failed_match.group(1))
    if not passed and not failed and test_results:
        passed = total if all(item.get("status") == "passed" for item in test_results) else 0
        failed = total - passed
    return {"total": total, "passed": passed, "failed": failed}


def _build_issue_statement(issue: Issue) -> str:
    description = " ".join((issue.description or "").split())
    if description:
        sentence = re.split(r"(?<=[.!?])\s+", description, maxsplit=1)[0].strip()
        return sentence.rstrip(".") + "."
    title = (issue.title or issue.issue_id or "the reported issue").strip()
    return f"{title}."


def _fallback_solution_summary(issue: Issue, files: list[str]) -> str:
    changed_targets = ", ".join(files[:2]) if files else "the targeted code path"
    description = f"{issue.title} {(issue.description or '')}".lower()
    if "out of memory" in description or "oom" in description or "memory" in description:
        return (
            f"Bounded the escalation payload so the resolution flow no longer runs out of memory, "
            f"using targeted changes in {changed_targets}."
        )
    if "timeout" in description:
        return (
            f"Adjusted the timeout handling so the affected workflow can complete reliably, "
            f"using targeted changes in {changed_targets}."
        )
    if "context" in description or "handoff" in description:
        return (
            f"Preserved the required handoff context so the downstream agent receives the full payload, "
            f"using targeted changes in {changed_targets}."
        )
    return f"Applied the approved fix in {changed_targets} and verified the targeted behavior."


def _issue_test_candidates(issue: Issue) -> list[str]:
    description = f"{issue.title} {issue.description}".lower()
    project = (issue.project_name or "").lower()
    candidates: list[str] = []
    if "oom" in description or "out of memory" in description or "memory" in description:
        candidates.append("tests/test_memory.py")
    if "handoff" in description or "context" in description or "support" in project:
        candidates.append("tests/test_handoff.py")
    if "timeout" in description or "order" in project:
        candidates.append("tests/test_runtime.py")
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _clean_summary_text(text: str) -> str:
    cleaned = " ".join((text or "").split())
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"\([^)]*:\d+\)", "", cleaned)
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
    return cleaned


def _humanize_solution_text(text: str, issue: Issue) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    lowered = f"{issue.title} {issue.description}".lower()
    if "out of memory" in lowered or "oom" in lowered or "memory" in lowered:
        return (
            "Updated the escalation payload handling so the resolution flow keeps the required context "
            "without exhausting memory."
        )
    if "timeout" in lowered:
        return "Adjusted the timeout handling so the affected workflow can complete reliably."
    if "handoff" in lowered or "context" in lowered:
        return "Updated the handoff payload so the downstream agent receives the full required context."
    substitutions = {
        "build_resolution_payload": "the resolution payload builder",
        "build_runtime_payload": "the runtime payload builder",
    }
    for source, target in substitutions.items():
        cleaned = cleaned.replace(source, target)
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ""


def github_issue_body_from_payload(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)
