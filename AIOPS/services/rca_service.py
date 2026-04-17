from __future__ import annotations

from core.schemas import RCAReport


def analyze_logs_for_rca(*, issue_title: str, issue_body: str = "", logs: str = "") -> RCAReport:
    text = "\n".join([issue_title, issue_body, logs]).lower()
    evidence: list[str] = []
    recommendations: list[str] = []

    if "timeout" in text or "timed out" in text:
        failure_mode = "Timeout while calling upstream dependency"
        root_cause = "The agent likely exceeded retry, network, or upstream response windows while calling a remote API."
        evidence.append("Detected timeout-related wording in the source report/logs.")
        recommendations.extend(
            [
                "Review upstream API timeout and retry settings.",
                "Add clearer timeout diagnostics and contextual logging.",
                "Validate circuit-breaker or backoff behavior for transient failures.",
            ]
        )
    elif "openai" in text and ("connection" in text or "429" in text or "rate limit" in text):
        failure_mode = "OpenAI API connectivity or rate limiting issue"
        root_cause = "The agent likely encountered upstream API throttling, connectivity instability, or inadequate retry handling."
        evidence.append("Detected OpenAI API failure indicators in the source report/logs.")
        recommendations.extend(
            [
                "Inspect OpenAI request retry and backoff strategy.",
                "Capture request identifiers and response metadata in logs.",
                "Add safeguards for transient API connectivity failures.",
            ]
        )
    else:
        failure_mode = "General production incident affecting an AI remediation path"
        root_cause = "The logs suggest a production-grade failure but do not contain enough structured evidence to isolate a narrower root cause automatically."
        evidence.append("No dominant failure signature was detected automatically.")
        recommendations.extend(
            [
                "Add structured logs around outbound dependency calls.",
                "Capture correlation IDs and error classes in agent logs.",
                "Expand incident evidence before implementation begins.",
            ]
        )

    summary = f"RCA for incident '{issue_title}' indicates a likely '{failure_mode}' scenario."
    suggested_issue_title = f"Remediate incident: {issue_title}"
    suggested_issue_body = build_remediation_issue_body(
        issue_title=issue_title,
        issue_body=issue_body,
        failure_mode=failure_mode,
        root_cause=root_cause,
        evidence=evidence,
        recommendations=recommendations,
    )

    return RCAReport(
        summary=summary,
        likely_failure_mode=failure_mode,
        probable_root_cause=root_cause,
        evidence=evidence,
        remediation_recommendations=recommendations,
        suggested_issue_title=suggested_issue_title,
        suggested_issue_body=suggested_issue_body,
    )


def build_remediation_issue_body(
    *,
    issue_title: str,
    issue_body: str,
    failure_mode: str,
    root_cause: str,
    evidence: list[str],
    recommendations: list[str],
) -> str:
    evidence_block = "\n".join(f"- {item}" for item in evidence) or "- No evidence captured"
    recommendations_block = "\n".join(f"- {item}" for item in recommendations) or "- No recommendations captured"
    return (
        f"# Issue summary\n"
        f"Production incident derived from GitHub/log intake: {issue_title}\n\n"
        f"## Repository/component impacted\n"
        f"To be resolved from project routing.\n\n"
        f"## Proposed implementation approach\n"
        f"- Address failure mode: {failure_mode}\n"
        f"- Root cause to remediate: {root_cause}\n"
        f"- Preserve production-safe behavior and improve observability.\n\n"
        f"## Test scenarios\n"
        f"- Reproduce the reported failure path\n"
        f"- Validate retry/timeout behavior\n"
        f"- Confirm logging and fallback behavior\n\n"
        f"## Risks and edge cases\n"
        f"{evidence_block}\n\n"
        f"## Approval checklist\n"
        f"{recommendations_block}\n\n"
        f"## Source context\n"
        f"{issue_body or 'No additional body content provided.'}\n"
    )
