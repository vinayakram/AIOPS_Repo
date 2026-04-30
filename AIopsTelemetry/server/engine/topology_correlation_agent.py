from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from server.database.models import Issue, IssueAnalysis, Span, TraceLog
from server.engine.bilingual import issue_description_ja


IMPACT_WORDS = (
    "not reachable",
    "unreachable",
    "timeout",
    "timed out",
    "latency",
    "high latency",
    "resource",
    "guard",
    "threshold",
    "application",
)


def _load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _text_blob(issue: Issue, meta: dict[str, Any] | None = None) -> str:
    meta = meta or {}
    return " ".join(
        str(x or "")
        for x in [
            issue.app_name,
            issue.issue_type,
            issue.rule_id,
            issue.title,
            issue.description,
            meta.get("error_message"),
            meta.get("status_message"),
        ]
    ).lower()


def _tokens(value: str | None) -> set[str]:
    raw = re.split(r"[^a-z0-9]+", str(value or "").lower())
    return {x for x in raw if len(x) >= 3 and x not in {"the", "and", "for", "with", "app", "open"}}


def _topology(meta: dict[str, Any]) -> dict[str, Any]:
    topo = meta.get("topology")
    return topo if isinstance(topo, dict) else {}


def _nodes(topo: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = topo.get("nodes")
    return nodes if isinstance(nodes, list) else []


def _is_host_root_issue(issue: Issue, meta: dict[str, Any]) -> bool:
    topo = _topology(meta)
    nodes = _nodes(topo)
    if not nodes:
        return False
    has_host_error = any(n.get("zone") == "host" and n.get("status") in {"error", "warn"} for n in nodes if isinstance(n, dict))
    has_runtime = any(n.get("zone") == "runtime" for n in nodes if isinstance(n, dict))
    rootish = any(
        word in _text_blob(issue, meta)
        for word in ("host", "cpu", "contention", "background", "starvation")
    )
    return has_host_error and has_runtime and rootish


def _is_downstream_symptom(issue: Issue, meta: dict[str, Any]) -> bool:
    correlation = meta.get("correlation") if isinstance(meta.get("correlation"), dict) else {}
    if _topology(meta) and correlation.get("role") != "impact":
        return False
    blob = _text_blob(issue, meta)
    if issue.status not in {"OPEN", "ESCALATED", "ACKNOWLEDGED"}:
        return False
    return issue.severity in {"high", "critical"} and any(word in blob for word in IMPACT_WORDS)


def _runtime_aliases(topo: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    impact = topo.get("impact") if isinstance(topo.get("impact"), dict) else {}
    for app in impact.get("applications") or []:
        aliases |= _tokens(str(app))
    for node in _nodes(topo):
        if not isinstance(node, dict) or node.get("zone") != "runtime":
            continue
        aliases |= _tokens(node.get("id"))
        aliases |= _tokens(node.get("name"))
        aliases |= _tokens(node.get("process_name"))
        aliases |= _tokens(node.get("role"))
        for metric in node.get("metrics") or []:
            if isinstance(metric, dict):
                aliases |= _tokens(metric.get("value"))
    return aliases


def _issue_aliases(issue: Issue) -> set[str]:
    aliases = _tokens(issue.app_name) | _tokens(issue.title) | _tokens(issue.description) | _tokens(issue.issue_type)
    if "sample" in aliases and "agent" in aliases:
        aliases.add("ai")
    if "agent" in aliases:
        aliases.add("agent")
    return aliases


def _score(root: Issue, root_meta: dict[str, Any], child: Issue, child_meta: dict[str, Any], window_minutes: int) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0
    delta = abs((child.created_at - root.created_at).total_seconds()) if child.created_at and root.created_at else 999999
    if delta <= window_minutes * 60:
        score += 35
        reasons.append(f"events occurred within {int(delta)} seconds")
    if child.created_at and root.created_at and child.created_at >= root.created_at:
        score += 10
        reasons.append("sample-agent symptom appeared after the host signal")
    overlap = _runtime_aliases(_topology(root_meta)) & _issue_aliases(child)
    if overlap:
        score += 25
        reasons.append("application/topology alias overlap: " + ", ".join(sorted(overlap)[:5]))
    elif "agent" in _issue_aliases(child) and any((n.get("name") or "").lower().endswith("agent") for n in _nodes(_topology(root_meta)) if isinstance(n, dict)):
        score += 20
        reasons.append("agent symptom maps to the Docker-hosted agent node")
    child_blob = _text_blob(child, child_meta)
    if any(word in child_blob for word in IMPACT_WORDS):
        score += 20
        reasons.append("downstream symptom is reachability/latency/resource related")
    if root_meta.get("process_cpu_percent") or root_meta.get("cpu_percent"):
        score += 10
        reasons.append("root issue has measured host/process CPU evidence")
    return score, reasons


def _log_row(timestamp: Any, level: str, message: str) -> dict[str, str]:
    return {
        "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp or datetime.utcnow().isoformat()),
        "level": level,
        "message": message,
    }


def _collect_issue_logs(db: Session, issue: Issue, limit: int = 8) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = []
    if issue.trace_id:
        for row in (
            db.query(TraceLog)
            .filter(TraceLog.trace_id == issue.trace_id)
            .order_by(TraceLog.timestamp.desc())
            .limit(limit)
            .all()
        ):
            logs.append(_log_row(row.timestamp, row.level, row.message))
        if len(logs) < limit:
            for span in (
                db.query(Span)
                .filter(Span.trace_id == issue.trace_id)
                .order_by(Span.started_at.desc())
                .limit(limit - len(logs))
                .all()
            ):
                status = "ERROR" if span.status == "error" or span.error_message else "INFO"
                msg = span.error_message or f"span {span.name} status={span.status} duration_ms={span.duration_ms}"
                logs.append(_log_row(span.started_at, status, msg))
    if not logs:
        meta = _load_json(issue.metadata_json)
        for key in ("error_message", "status_message", "latest_symptom"):
            if meta.get(key):
                logs.append(_log_row(issue.updated_at or issue.created_at, "ERROR", str(meta[key])))
        if issue.description:
            logs.append(_log_row(issue.created_at, "WARN", issue.description))
    return list(reversed(logs[-limit:]))


def _metric_value(metrics: list[dict[str, Any]], label: str) -> str | None:
    for metric in metrics:
        if str(metric.get("label", "")).lower() == label.lower():
            return str(metric.get("value"))
    return None


def _mark_impacted_node(topo: dict[str, Any], child: Issue, logs: list[dict[str, str]], root: Issue) -> dict[str, Any]:
    enriched = copy.deepcopy(topo)
    child_aliases = _issue_aliases(child)
    nodes = _nodes(enriched)
    target: dict[str, Any] | None = None
    if "agent" in child_aliases:
        target = next(
            (
                node
                for node in nodes
                if isinstance(node, dict)
                and node.get("zone") == "runtime"
                and "agent" in str(" ".join([str(node.get("id") or ""), str(node.get("name") or ""), str(node.get("process_name") or ""), str(node.get("role") or "")])).lower()
            ),
            None,
        )
    for node in nodes:
        if target is not None:
            break
        if not isinstance(node, dict) or node.get("zone") != "runtime":
            continue
        node_aliases = _tokens(node.get("id")) | _tokens(node.get("name")) | _tokens(node.get("process_name")) | _tokens(node.get("role"))
        if node_aliases & child_aliases:
            target = node
            break
    if target is None:
        target = next(
            (
                node
                for node in nodes
                if isinstance(node, dict)
                and node.get("zone") == "runtime"
                and "agent" in (str(node.get("name", "") + " " + node.get("role", "")).lower())
            ),
            None,
        )
    if target is not None:
        target["status"] = "warn"
        target["name"] = target.get("name") or child.app_name
        target["error_message"] = f"{child.title}. Correlated to host-side root issue #{root.id}."
        target["root_cause_chain"] = (
            f"Host background job / VM CPU contention (issue #{root.id}) -> Docker runtime CPU pressure -> "
            f"{child.app_name} reachability symptom (issue #{child.id})."
        )
        metrics = target.setdefault("metrics", [])
        if not _metric_value(metrics, "Correlated issue"):
            metrics.append({"label": "Correlated issue", "value": f"#{child.id}"})
        if not _metric_value(metrics, "Impacted app"):
            metrics.append({"label": "Impacted app", "value": child.app_name})
        target["logs"] = logs or target.get("logs", [])
    impact = enriched.setdefault("impact", {})
    apps = [str(x) for x in impact.get("applications") or []]
    if child.app_name not in apps:
        apps.insert(0, child.app_name)
    impact["applications"] = apps
    impact["application_count"] = len(apps)
    impact["where"] = f"VM host CPU contention -> Docker runtime -> {child.app_name}"
    impact.setdefault("user_count", 0)
    enriched["correlated_from_issue"] = root.id
    enriched["propagation_label"] = "CPU contention impacts Docker agent"
    alerts = enriched.setdefault("alerts", [])
    if not any(a.get("name") == "SampleAgentUnavailable" for a in alerts if isinstance(a, dict)):
        alerts.append({"name": "SampleAgentUnavailable", "severity": "critical", "tone": "critical"})
    traces = enriched.setdefault("traces", [])
    for log in logs[-4:]:
        traces.append({"timestamp": log.get("timestamp"), "status": "fail" if log.get("level") == "ERROR" else "slow", "label": child.app_name})
    return enriched


def _analysis_text(root: Issue, child: Issue, root_meta: dict[str, Any], reasons: list[str]) -> tuple[str, str, str]:
    topo = _topology(root_meta)
    metric_rows = topo.get("metrics") if isinstance(topo.get("metrics"), list) else []
    host_cpu = _metric_value(metric_rows, "Host CPU") or f"{root_meta.get('cpu_percent', 'unknown')}%"
    job_cpu = _metric_value(metric_rows, "Job CPU") or f"{root_meta.get('process_cpu_percent', 'unknown')}%"
    likely = (
        f"The root cause is host-side CPU contention from the background job tracked as issue #{root.id}, "
        f"not an isolated {child.app_name} application failure. The host pressure reduced CPU available to "
        f"the Docker-hosted agent, which then surfaced as {child.title.lower()} in issue #{child.id}."
    )
    evidence = "\n".join(
        [
            f"Root issue #{root.id}: {root.title}",
            f"Impacted issue #{child.id}: {child.title}",
            f"Host CPU: {host_cpu}; background job CPU: {job_cpu}",
            *[f"Correlation: {reason}" for reason in reasons],
        ]
    )
    action = (
        "Throttle, stop, or reschedule the host background job; reserve CPU for the Docker runtime/sample-agent; "
        "then validate sample-agent reachability and p99 latency while Prometheus and Langfuse continue collecting evidence."
    )
    return likely, evidence, action


def correlate_recent_topology_issues(db: Session, window_minutes: int = 30, min_score: int = 55) -> list[dict[str, Any]]:
    since = datetime.utcnow() - timedelta(minutes=window_minutes)
    issues = (
        db.query(Issue)
        .filter(Issue.created_at >= since)
        .filter(Issue.status.in_(["OPEN", "ESCALATED", "ACKNOWLEDGED"]))
        .order_by(Issue.created_at.asc())
        .all()
    )
    metas = {issue.id: _load_json(issue.metadata_json) for issue in issues}
    roots = [issue for issue in issues if _is_host_root_issue(issue, metas[issue.id])]
    children = [issue for issue in issues if _is_downstream_symptom(issue, metas[issue.id])]
    results: list[dict[str, Any]] = []
    for child in children:
        best: tuple[int, float, Issue, list[str]] | None = None
        for root in roots:
            if root.id == child.id:
                continue
            score, reasons = _score(root, metas[root.id], child, metas[child.id], window_minutes)
            delta = abs((child.created_at - root.created_at).total_seconds()) if child.created_at and root.created_at else 999999.0
            if score >= min_score and (best is None or score > best[0] or (score == best[0] and delta < best[1])):
                best = (score, delta, root, reasons)
        if best is None:
            continue
        score, _delta, root, reasons = best
        root_meta = metas[root.id]
        child_meta = metas[child.id]
        logs = _collect_issue_logs(db, child)
        child_meta["correlation"] = {
            "role": "impact",
            "root_issue_id": root.id,
            "confidence": "high" if score >= 80 else "medium",
            "score": score,
            "correlated_at": datetime.utcnow().replace(microsecond=0).isoformat(),
            "reason": "; ".join(reasons),
            "evidence": reasons,
        }
        child_meta["topology"] = _mark_impacted_node(_topology(root_meta), child, logs, root)
        child.metadata_json = _dump_json(child_meta)
        root_corr = root_meta.setdefault("correlation", {"role": "root", "impacted_issue_ids": []})
        ids = set(root_corr.get("impacted_issue_ids") or [])
        ids.add(child.id)
        root_corr["role"] = "root"
        root_corr["impacted_issue_ids"] = sorted(ids)
        root_corr["confidence"] = child_meta["correlation"]["confidence"]
        root_corr["correlated_at"] = child_meta["correlation"]["correlated_at"]
        root_corr["reason"] = child_meta["correlation"]["reason"]
        root.metadata_json = _dump_json(root_meta)
        likely, evidence, action = _analysis_text(root, child, root_meta, reasons)
        analysis = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == child.id).first()
        if analysis is None:
            analysis = IssueAnalysis(issue_id=child.id)
            db.add(analysis)
        analysis.generated_at = datetime.utcnow()
        analysis.model_used = "topology-correlation-agent"
        analysis.status = "done"
        analysis.likely_cause = likely
        analysis.evidence = evidence
        analysis.recommended_action = action
        analysis.remediation_type = "host_cpu_contention"
        analysis.likely_cause_en = likely
        analysis.evidence_en = evidence
        analysis.recommended_action_en = action
        analysis.likely_cause_ja = issue_description_ja(likely, app_name=child.app_name, rule_id=child.rule_id)
        analysis.evidence_ja = issue_description_ja(evidence, app_name=child.app_name, rule_id=child.rule_id)
        analysis.recommended_action_ja = issue_description_ja(action, app_name=child.app_name, rule_id=child.rule_id)
        analysis.language_status = "ready"
        results.append({"root_issue_id": root.id, "impact_issue_id": child.id, "score": score, "confidence": child_meta["correlation"]["confidence"], "reasons": reasons})
    if results:
        db.commit()
    return results
