from __future__ import annotations

import html
import re

from core.schemas import Issue
from services.codex_cli_service import CodexCLI
from services.repo_service import repo_summary, validate_issue_paths
from services.storage import (
    append_progress,
    get_next_plan_version,
    issue_dir,
    save_json,
    save_text,
    set_latest_plan_file,
)


PLAN_START_PATTERN = re.compile(r"(?ms)^# Plan for .*$")


def build_plan_prompt(
    issue: Issue,
    reviewer_comments: list[str] | None = None,
) -> str:
    repo_root, allowed_folder = validate_issue_paths(issue)
    acceptance = "\n".join(f"- {item}" for item in issue.acceptance_criteria) or "- None provided"
    comments_block = "\n".join(f"- {item}" for item in (reviewer_comments or [])) or "- None"
    rca_evidence = "\n".join(f"- {item}" for item in issue.rca_evidence[:8]) or "- None provided"
    rca_block = ""
    if issue.rca_summary or issue.rca_recommended_action or issue.rca_evidence:
        rca_block = f"""
Investigate RCA context:
- Source: {issue.rca_source or issue.source_system or 'upstream telemetry'}
- Trace ID: {issue.rca_trace_id or 'N/A'}
- RCA summary: {issue.rca_summary or 'N/A'}
- RCA recommended action: {issue.rca_recommended_action or 'N/A'}

RCA evidence:
{rca_evidence}
"""

    is_revision = bool(reviewer_comments)

    if is_revision:
        revision_block = f"""
Reviewer comments:
{comments_block}

Revision rules:
- Reviewer comments override prior plan structure and wording.
- Keep the revised plan shorter and clearer than the earlier version.
- Do NOT reproduce or expand old technical detail.
- Do NOT include file-level, function-level, schema-level, class-level, or helper-level details unless absolutely necessary.
- Rewrite the plan cleanly from scratch based on the reviewer comments.
- Explicitly state how the reviewer comments were addressed inside the relevant sections.
- Include at least one explicit line such as "Reviewer comments addressed:" followed by the concrete changes made to the plan.
"""
    else:
        revision_block = """
Revision rules:
- This is the first plan draft.
- Keep the plan concise, implementation-focused, and review-friendly.
- Do NOT include file-level, function-level, schema-level, class-level, or helper-level details unless absolutely necessary.
"""

    return f"""You are running inside Codex CLI in repo: {repo_root}

Task:
Prepare an implementation plan only for the issue below.

Important constraints:
- Do NOT modify any files.
- Do NOT write code.
- Do NOT run mutating git commands.
- Do NOT create commits, branches, or PRs.
- Do NOT ask the user what to work on.
- Do NOT include run transcript details, command output summaries, or internal reasoning.
- Build a concise, implementation-focused plan that is easy for a developer or reviewer to approve.
- Prefer module-level or component-level references over code-level references.
- Keep the output crisp and practical.
- Do NOT output source code, pseudocode, stack traces, or code blocks.
- Do NOT paste function bodies, class definitions, or implementation snippets.
- Write for a reviewer or product stakeholder, not only for an engineer.

Issue details:
- ID: {issue.issue_id}
- Project: {issue.project_name or 'N/A'}
- Title: {issue.title}
- Base branch: {issue.base_branch}
- Allowed folder: {allowed_folder}
- Test command: {issue.test_command}

Issue description:
{issue.description or 'No description provided.'}

{rca_block}

Acceptance criteria:
{acceptance}

Current repo summary:
{repo_summary(issue)}

{revision_block}

Planning rules:
1. Ground the plan in the actual repository.
2. Focus only on what needs to change.
3. Use the Investigate RCA context as primary evidence when it is present.
4. The proposed approach must address the RCA recommended action unless repository inspection proves it is unsafe or irrelevant.
5. Keep the plan short, clear, and implementation-focused.
6. Avoid low-level technical breakdown unless it is essential.
7. Summarize testing as scenarios, not detailed test implementation.
8. Include only the required sections below.
9. Output clean markdown only.
10. The plan should read like a short engineering review note, not a coding walkthrough.
11. Each section should be 2-5 short bullets or short prose lines, not long paragraphs.
12. Keep language specific to this issue and repository context.
13. Avoid vague phrases such as "update relevant files" or "make necessary changes"; name the impacted component or behavior in plain language.

Return the final answer as markdown with exactly these sections only:
# Plan for {issue.issue_id}
## Issue summary
## Repository/component impacted
## Proposed implementation approach
## Test scenarios
## Risks and edge cases
## Approval checklist
"""


def _build_plan_filename(version: int) -> str:
    return f"plan_v{version}.md"


def _build_plan_html_filename(version: int) -> str:
    return f"plan_v{version}.html"


def sanitize_plan_markdown(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""

    codex_answer_match = re.search(r"(?ms)^codex\s+# Plan for .*$", raw)
    if codex_answer_match:
        raw = raw[codex_answer_match.start() :].replace("codex\n", "", 1).strip()

    matches = list(PLAN_START_PATTERN.finditer(raw))
    if matches:
        candidates: list[str] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
            candidate = raw[start:end].strip()
            stop_markers = [
                "\ntokens used",
                "\nOpenAI Codex",
                "\nuser\n",
                "\nexec\n",
                "\ncodex\n# Plan for ",
                "\n# Plan for ",
            ]
            stop_positions = [candidate.find(marker) for marker in stop_markers if candidate.find(marker) > 0]
            if stop_positions:
                candidate = candidate[: min(stop_positions)].strip()
            if candidate:
                candidates.append(candidate)

        def score(candidate: str) -> tuple[int, int, int]:
            bullet_count = len(re.findall(r"(?m)^- ", candidate))
            paragraph_count = len(re.findall(r"(?m)^[A-Za-z0-9].+", candidate))
            section_count = len(re.findall(r"(?m)^## ", candidate))
            return (bullet_count, paragraph_count, section_count)

        candidates.sort(key=score, reverse=True)
        return candidates[0]

    fallback = raw.find("Plan for ")
    if fallback >= 0:
        candidate = raw[fallback:].strip()
        if not candidate.startswith("#"):
            candidate = "# " + candidate
        return candidate

    return raw


def plan_has_substantive_content(text: str) -> bool:
    candidate = sanitize_plan_markdown(text)
    return bool(re.search(r"(?m)^- ", candidate) or re.search(r"(?m)^[A-Za-z0-9].{20,}$", candidate))


def render_plan_html(markdown: str) -> str:
    text = sanitize_plan_markdown(markdown)
    if not text:
        body = "<p>No plan available.</p>"
    else:
        lines = text.splitlines()
        parts: list[str] = []
        in_list = False
        section_index = 0

        def close_list() -> None:
            nonlocal in_list
            if in_list:
                parts.append("</ul>")
                in_list = False

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                close_list()
                continue
            if line.startswith("# "):
                close_list()
                parts.append(f"<h1>{html.escape(line[2:])}</h1>")
                continue
            if line.startswith("## "):
                close_list()
                section_index += 1
                parts.append(
                    "<h2><span class=\"plan-section-index\">"
                    f"{section_index}.</span> {html.escape(line[3:])}</h2>"
                )
                continue
            if line.startswith("- "):
                if not in_list:
                    parts.append("<ul>")
                    in_list = True
                parts.append(f"<li>{html.escape(line[2:])}</li>")
                continue
            close_list()
            parts.append(f"<p>{html.escape(line)}</p>")

        close_list()
        body = "\n".join(parts)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Plan</title>
  <style>
    body {{
      margin: 0;
      padding: 32px;
      background: #f4efe8;
      color: #1f1a17;
      font-family: "Segoe UI", Arial, sans-serif;
      line-height: 1.6;
    }}
    main {{
      max-width: 900px;
      margin: 0 auto;
      background: #fffdf9;
      border: 1px solid rgba(118, 80, 58, 0.14);
      border-radius: 18px;
      padding: 28px 32px;
      box-shadow: 0 10px 30px rgba(15, 65, 57, 0.08);
    }}
    h1 {{
      font-size: 1.4rem;
      margin: 0 0 20px;
      color: #174f46;
      font-weight: 800;
    }}
    h2 {{
      font-size: 1.08rem;
      margin: 20px 0 8px;
      color: #2b241f;
      font-weight: 800;
    }}
    .plan-section-index {{
      color: #174f46;
      font-weight: 900;
      display: inline-block;
      min-width: 1.6rem;
    }}
    p, li {{
      font-size: 0.98rem;
    }}
    ul {{
      padding-left: 22px;
      margin: 0 0 10px;
    }}
  </style>
</head>
<body>
  <main>
    {body}
  </main>
</body>
</html>
"""


def generate_plan(
    issue: Issue,
    reviewer_comments: list[str] | None = None,
) -> str:
    run_dir = issue_dir(issue.issue_id)
    version = get_next_plan_version(issue.issue_id)
    plan_filename = _build_plan_filename(version)
    plan_html_filename = _build_plan_html_filename(version)
    plan_path = run_dir / plan_filename
    plan_html_path = run_dir / plan_html_filename

    prompt = build_plan_prompt(
        issue,
        reviewer_comments=reviewer_comments,
    )
    save_text(run_dir / "codex_plan_prompt.md", prompt)

    repo_root, _ = validate_issue_paths(issue)
    append_progress(issue.issue_id, f"Plan prompt prepared for {plan_filename}.")
    result = CodexCLI().exec(
        cwd=repo_root,
        prompt=prompt,
        sandbox="read-only",
        on_event=lambda msg: append_progress(issue.issue_id, f"[plan] {msg}"),
    )

    save_json(
        run_dir / "codex_plan_exec.json",
        {
            "command": result.command,
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "plan_file": plan_filename,
            "version": version,
        },
    )

    if result.return_code != 0:
        raise RuntimeError(f"Codex plan generation failed.\n{result.stderr or result.stdout}")

    md = sanitize_plan_markdown(result.stdout)
    save_text(plan_path, md)
    save_text(plan_html_path, render_plan_html(md))
    set_latest_plan_file(issue.issue_id, plan_filename)
    append_progress(issue.issue_id, f"{plan_filename} saved as latest plan.")
    append_progress(issue.issue_id, f"{plan_html_filename} saved as formatted plan.")

    if reviewer_comments:
        comments_path = run_dir / f"review_comments_v{version}.txt"
        save_text(comments_path, "\n".join(reviewer_comments))
        append_progress(issue.issue_id, f"{comments_path.name} saved.")

    return md
