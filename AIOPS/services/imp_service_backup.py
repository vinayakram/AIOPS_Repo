from __future__ import annotations

from core.schemas import ImplementationRun, Issue
from services.codex_cli_service import CodexCLI
from services.repo_service import (
    create_backup,
    diff_text,
    ensure_clean_branch,
    head_show,
    pr_url_from_branch,
    repo_summary,
    validate_issue_paths,
)
from services.storage import issue_dir, read_text, save_json, save_text
from services.test_service import run_tests


def build_implementation_prompt(issue: Issue, approved_plan: str, branch_name: str) -> str:
    repo_root, allowed_folder = validate_issue_paths(issue)
    acceptance = "\n".join(f"- {item}" for item in issue.acceptance_criteria) or "- None provided"
    return f"""You are running inside Codex CLI in repo: {repo_root}

Task:
Implement the approved plan, validate it, and raise a pull request.

Issue details:
- ID: {issue.issue_id}
- Project: {issue.project_name or 'N/A'}
- Title: {issue.title}
- Working branch: {branch_name}
- Base branch: {issue.base_branch}
- Allowed folder: {allowed_folder}
- Test command: {issue.test_command}

Issue description:
{issue.description or 'No description provided.'}

Acceptance criteria:
{acceptance}

Approved plan:
{approved_plan}

Current repo summary:
{repo_summary(issue)}

Execution rules:
1. Only modify files inside the allowed folder unless tests or repo metadata strictly require a minimal change elsewhere.
2. Keep changes small, production-ready, and aligned to the approved plan.
3. Add or update tests when needed.
4. Run the test command after edits and fix failures before finishing.
5. When implementation is complete, run git status and review your own diff for obvious issues.
6. Commit the changes with this message: {issue.issue_id}: {issue.title}
7. Push the branch to origin.
8. Create a PR with gh using the base branch {issue.base_branch}. Use the PR title: {issue.issue_id}: {issue.title}
9. In the PR body include a short summary, the test command used, and the key files changed.
10. If gh auth is missing or PR creation fails, explain exactly what worked and what failed.

Return the final answer as markdown with exactly these sections:
# Implementation result for {issue.issue_id}
## Summary
## Files changed
## Tests run
## Git status
## Pull request
## Follow-ups
"""


def run_implementation(issue: Issue) -> ImplementationRun:
    run_dir = issue_dir(issue.issue_id)
    approved_plan = read_text(run_dir / "plan.md")
    if not approved_plan.strip():
        raise RuntimeError("Approved plan not found. Generate and approve a plan first.")

    backup_zip = create_backup(issue)
    branch_name = ensure_clean_branch(issue)
    prompt = build_implementation_prompt(issue, approved_plan=approved_plan, branch_name=branch_name)
    save_text(run_dir / "codex_implementation_prompt.md", prompt)

    repo_root, _ = validate_issue_paths(issue)
    result = CodexCLI().exec(cwd=repo_root, prompt=prompt, sandbox="workspace-write")
    save_json(
        run_dir / "codex_implementation_exec.json",
        {
            "command": result.command,
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )
    if result.return_code != 0:
        save_text(run_dir / "codex_implementation_error.txt", result.stderr or result.stdout)
        raise RuntimeError(f"Codex implementation failed.\n{result.stderr or result.stdout}")

    tests = run_tests(issue)
    diff = diff_text(issue)
    head_summary = head_show(issue)
    pr_url = pr_url_from_branch(issue, branch_name)

    save_text(run_dir / "git_diff.patch", diff)
    save_text(run_dir / "head_show.txt", head_summary)

    payload = ImplementationRun(
        branch_name=branch_name,
        backup_zip=backup_zip,
        codex_plan_prompt=str(run_dir / "codex_plan_prompt.md"),
        codex_implementation_prompt=str(run_dir / "codex_implementation_prompt.md"),
        codex_final_message=result.stdout.strip(),
        tests=tests,
        success=all(t.return_code == 0 for t in tests),
        pr_url=pr_url,
        human_summary="Codex CLI completed the implementation flow. Review the PR, git artifacts, and post-run test results.",
    )
    save_json(run_dir / "implementation.json", payload.model_dump(mode="json"))
    save_json(run_dir / "test_results.json", [x.model_dump(mode="json") for x in tests])
    save_text(run_dir / "codex_final_message.md", result.stdout.strip())
    return payload
