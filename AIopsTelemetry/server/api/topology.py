"""
Topology API building VM-level service topology views for individual issues.
Overlays RCA analysis results onto template nodes, fetches component logs from
Prometheus, Langfuse, and the local database, and returns a Mermaid-compatible
JSON structure suitable for graph rendering in the operations dashboard.

個別イシューに対するVMレベルのサービストポロジーを構築するAPIモジュール。
RCA分析結果をテンプレートノードに重ね合わせ、Prometheus・Langfuse・ローカルDBから
コンポーネントログを収集し、オペレーションダッシュボードのグラフ描画に適した
Mermaid互換のJSON構造を返す。
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from server.config import settings
from server.database.engine import get_db
from server.database.models import Issue, IssueAnalysis, Span, TraceLog
from server.engine.bilingual import (
    app_display_name_ja,
    localize_observability_text,
    normalize_lang,
    select_text,
)


router = APIRouter(prefix="/topology", tags=["topology"])

_TOPOLOGY_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "vm-topology.json"
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "onto",
    "this",
    "that",
    "issue",
    "error",
    "warn",
    "warning",
    "status",
    "open",
    "high",
    "low",
    "medium",
    "critical",
}
_RESOURCE_HINTS = {"cpu", "memory", "disk", "storage", "host", "vm", "contention", "background", "worker", "pool"}
_HUMAN_TEXT_KEYS = (
    "analysis_summary",
    "full_summary",
    "likely_cause",
    "reason",
    "error_summary",
    "summary",
    "message",
    "recommended_action",
    "root_cause_chain",
    "component",
    "service",
)
_SKIP_TEXT_KEYS = {
    "trace_id",
    "timestamp",
    "confidence",
    "processing_time_ms",
    "raw_log_count",
    "occurrence_count",
    "total_logs_analyzed",
    "data_sources",
    "analysis_target",
}


def _load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_template() -> dict[str, Any]:
    with _TOPOLOGY_TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _tokens(*values: Any) -> set[str]:
    blob = " ".join(str(value or "") for value in values).lower()
    parts = re.split(r"[^a-z0-9]+", blob)
    return {part for part in parts if len(part) >= 2 and part not in _STOP_WORDS}


def _normalize_status(value: Any, default: str = "ok") -> str:
    mapping = {
        "ok": "ok",
        "healthy": "ok",
        "success": "ok",
        "warning": "warn",
        "warn": "warn",
        "degraded": "warn",
        "slow": "warn",
        "error": "error",
        "critical": "error",
        "failed": "error",
        "down": "error",
    }
    return mapping.get(str(value or "").strip().lower(), default)


def _severity_to_status(severity: str | None) -> str:
    severity = str(severity or "").lower()
    if severity in {"critical", "high"}:
        return "error"
    if severity == "medium":
        return "warn"
    return "ok"


def _issue_meta(issue: Issue) -> dict[str, Any]:
    return _load_json(issue.metadata_json)


def _node_aliases(node: dict[str, Any]) -> set[str]:
    aliases = _tokens(
        node.get("id"),
        node.get("name"),
        node.get("process_name"),
        node.get("role"),
    )
    for alias in node.get("aliases") or []:
        aliases |= _tokens(alias)
    for metric in node.get("metrics") or []:
        if isinstance(metric, dict):
            aliases |= _tokens(metric.get("label"), metric.get("value"))
    return aliases


def _issue_aliases(issue: Issue, analysis: IssueAnalysis | None, meta: dict[str, Any]) -> set[str]:
    aliases = _tokens(
        issue.app_name,
        issue.issue_type,
        issue.rule_id,
        issue.title,
        issue.description,
        meta.get("error_message"),
        meta.get("status_message"),
        analysis.likely_cause if analysis else "",
        analysis.evidence if analysis else "",
    )
    display_name = meta.get("display_app_name")
    if display_name:
        aliases |= _tokens(display_name)
    return aliases


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "")
    if not text:
        return datetime.min
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.min


def _coerce_log(row: dict[str, Any], default_level: str = "INFO") -> dict[str, str]:
    return {
        "timestamp": str(row.get("timestamp") or datetime.utcnow().replace(microsecond=0).isoformat()),
        "level": str(row.get("level") or default_level).upper(),
        "message": str(row.get("message") or ""),
        "source": str(row.get("source") or ""),
        "source_label": str(row.get("source_label") or row.get("source") or ""),
    }


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text).strip()


def _looks_structured_text(value: Any) -> bool:
    text = _clean_text(value)
    return text.startswith("{") or text.startswith("[")


def _human_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = _clean_text(value)
        if not text:
            return ""
        if re.fullmatch(r"[a-f0-9]{24,}", text, re.IGNORECASE) or re.fullmatch(r"[a-f0-9-]{30,}", text, re.IGNORECASE):
            return ""
        if _looks_structured_text(text):
            try:
                return _human_text(json.loads(text))
            except json.JSONDecodeError:
                return ""
        return text
    if isinstance(value, list):
        for item in value:
            candidate = _human_text(item)
            if candidate:
                return candidate
        return ""
    if isinstance(value, dict):
        for key in _HUMAN_TEXT_KEYS:
            candidate = value.get(key)
            text = _human_text(candidate)
            if text:
                return text
        incident = value.get("incident")
        if isinstance(incident, dict):
            for key in ("error_summary", "summary", "message"):
                text = _human_text(incident.get(key))
                if text:
                    return text
        correlation = value.get("correlation")
        if isinstance(correlation, dict):
            text = _human_text(correlation.get("reason"))
            if text:
                return text
        root_candidate = value.get("root_cause_candidate")
        if isinstance(root_candidate, dict):
            component = _human_text(root_candidate.get("component"))
            reason = _human_text(root_candidate.get("reason"))
            if component and reason:
                return f"{component}: {reason}"
            if reason:
                return reason
        peer_components = value.get("peer_components")
        if isinstance(peer_components, list):
            first_peer = _human_text(peer_components)
            if first_peer:
                return first_peer
        errors = value.get("errors")
        if isinstance(errors, list):
            first_error = _human_text(errors)
            if first_error:
                return first_error
        for key, candidate in value.items():
            if key in _SKIP_TEXT_KEYS or key.endswith("_id") or key.endswith("_ids") or "timestamp" in key:
                continue
            text = _human_text(candidate)
            if text:
                return text
    return _clean_text(value)


def _fallback_issue_logs(issue: Issue, analysis: IssueAnalysis | None) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = []
    if issue.description:
        logs.append(
            {
                "timestamp": issue.created_at.isoformat() if issue.created_at else datetime.utcnow().isoformat(),
                "level": "ERROR" if issue.status != "RESOLVED" else "INFO",
                "message": issue.description,
            }
        )
    if analysis and analysis.evidence:
        for line in str(analysis.evidence).splitlines():
            line = line.strip()
            if line:
                logs.append(
                    {
                        "timestamp": analysis.generated_at.isoformat() if analysis.generated_at else datetime.utcnow().isoformat(),
                        "level": "WARN",
                        "message": line,
                    }
                )
    return logs[-8:]


def _find_template_match(template_nodes: list[dict[str, Any]], source_node: dict[str, Any]) -> dict[str, Any] | None:
    source_id = _slug(source_node.get("id"))
    source_aliases = _node_aliases(source_node)
    best_match: tuple[int, dict[str, Any]] | None = None
    for node in template_nodes:
        template_id = _slug(node.get("id"))
        if source_id and source_id == template_id:
            return node
        overlap = source_aliases & _node_aliases(node)
        score = len(overlap)
        if score and (best_match is None or score > best_match[0]):
            best_match = (score, node)
    return best_match[1] if best_match else None


def _should_keep_distinct_source_node(source_node: dict[str, Any]) -> bool:
    zone = str(source_node.get("zone") or "").lower()
    source_id = _slug(source_node.get("id") or source_node.get("name"))
    if zone != "runtime" or not source_id:
        return False
    return source_id not in {
        "ai-agent",
        "docker-runtime",
        "prometheus",
        "langfuse",
        "pgvector",
        "vm-host",
        "host-background-job",
    }


def _node_summary(node: dict[str, Any]) -> str:
    status = _normalize_status(node.get("status"))
    summary = _human_text(node.get("summary")) or _human_text(node.get("error_message")) or _human_text(node.get("role")) or ""
    if not summary:
        summary = {"ok": "Healthy", "warn": "Needs attention", "error": "Impacted"}[status]
    return str(summary)


def _status_rank(value: Any) -> int:
    status = _normalize_status(value)
    if status == "error":
        return 2
    if status == "warn":
        return 1
    return 0


def _zone_rank(value: Any) -> int:
    zone = str(value or "").lower()
    if zone in {"platform", "host"}:
        return 0
    if zone == "runtime":
        return 1
    if zone == "application":
        return 2
    if zone == "observability":
        return 4
    return 3


def _is_observability_node(node: dict[str, Any]) -> bool:
    return str(node.get("zone") or "").lower() == "observability" or str(node.get("id") or "") in {"prometheus", "langfuse"}


def _node_source_descriptors(node: dict[str, Any], issue: Issue) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []

    def add(source_id: str, label: str, kind: str) -> None:
        if any(item["id"] == source_id for item in sources):
            return
        sources.append({"id": source_id, "label": label, "kind": kind})

    node_id = str(node.get("id") or "")
    zone = str(node.get("zone") or "").lower()
    has_metrics = bool(node.get("metrics"))
    has_logs = bool(node.get("logs"))

    if node_id == "prometheus":
        add("prometheus", "Prometheus", "metrics")
    if node_id == "langfuse":
        add("langfuse", "Langfuse", "traces")
    if zone in {"platform", "runtime"} or has_metrics:
        add("prometheus", "Prometheus", "metrics")
    if issue.trace_id and (node_id in {"ai-agent", "langfuse", "pgvector"} or zone in {"observability", "runtime"}):
        add("langfuse", "Langfuse", "traces")
    if has_logs:
        add("aiops-local", "Local issue logs", "logs")
    return sources


def _match_path_nodes(nodes: list[dict[str, Any]], raw_path: str) -> list[str]:
    matched: list[str] = []
    for segment in re.split(r"->|→", str(raw_path or "")):
        segment_tokens = _tokens(segment)
        if not segment_tokens:
            continue
        best_score = 0
        best_node_id = None
        for node in nodes:
            overlap = len(segment_tokens & _node_aliases(node))
            if overlap > best_score:
                best_score = overlap
                best_node_id = str(node.get("id") or "")
        if best_score and best_node_id and best_node_id not in matched:
            matched.append(best_node_id)
    return matched


def _topology_projection(
    issue: Issue,
    meta: dict[str, Any],
    impact: dict[str, Any],
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    issue_app_tokens = _tokens(issue.app_name, meta.get("display_app_name"))
    impacted = [node for node in nodes if _normalize_status(node.get("status")) in {"warn", "error"}]
    symptom_candidates = [
        node
        for node in nodes
        if issue_app_tokens and issue_app_tokens & _node_aliases(node)
    ]
    symptom = None
    if symptom_candidates:
        symptom = sorted(
            symptom_candidates,
            key=lambda node: (-_status_rank(node.get("status")), _zone_rank(node.get("zone")), str(node.get("id") or "")),
        )[0]
    if symptom is None:
        symptom = next((node for node in impacted if not _is_observability_node(node)), None)
    if symptom is None:
        symptom = next((node for node in nodes if str(node.get("id") or "") == "ai-agent"), None)
    if symptom is None and nodes:
        symptom = nodes[0]

    def root_sort_key(node: dict[str, Any]) -> tuple[int, int, int, str]:
        node_id = str(node.get("id") or "")
        hint_score = 0 if (_tokens(node_id, node.get("name"), node.get("role")) & _RESOURCE_HINTS) else 1
        preferred = 0 if node_id in {"host-background-job", "vm-host", "docker-runtime"} else 1
        return (-_status_rank(node.get("status")), preferred, hint_score, str(node.get("id") or ""))

    root_candidates = [
        node
        for node in impacted
        if symptom is None or node.get("id") != symptom.get("id")
    ]
    root = sorted(root_candidates, key=root_sort_key)[0] if root_candidates else symptom

    path_ids = _match_path_nodes(nodes, impact.get("where"))
    if not path_ids:
        ordered_nodes = sorted(
            [node for node in impacted if not _is_observability_node(node)],
            key=lambda node: (_zone_rank(node.get("zone")), -_status_rank(node.get("status")), str(node.get("id") or "")),
        )
        path_ids = [str(node.get("id") or "") for node in ordered_nodes if node.get("id")]
    if root and root.get("id") and root.get("id") not in path_ids:
        path_ids.insert(0, str(root.get("id")))
    runtime = next((node for node in nodes if str(node.get("id") or "") == "docker-runtime"), None)
    if runtime and runtime.get("id") not in path_ids and root and symptom and root.get("id") != symptom.get("id"):
        insertion_index = 1 if path_ids else 0
        path_ids.insert(insertion_index, str(runtime.get("id")))
    if symptom and symptom.get("id") and symptom.get("id") not in path_ids:
        path_ids.append(str(symptom.get("id")))

    deduped_path: list[str] = []
    for node_id in path_ids:
        if node_id and node_id not in deduped_path:
            deduped_path.append(node_id)

    evidence_ids = [
        str(node.get("id") or "")
        for node in nodes
        if _is_observability_node(node)
    ]
    affected_ids = [
        str(node.get("id") or "")
        for node in impacted
        if node.get("id")
    ]
    return {
        "root_component_id": str(root.get("id") or "") if root else None,
        "symptom_component_id": str(symptom.get("id") or "") if symptom else None,
        "affected_component_ids": affected_ids,
        "path_component_ids": deduped_path,
        "evidence_component_ids": evidence_ids,
    }


def _overlay_node(target: dict[str, Any], source: dict[str, Any]) -> None:
    for field in ("name", "process_name", "role", "timestamp", "root_cause_chain", "error_message"):
        if source.get(field):
            target[field] = _human_text(source[field]) if field in {"role", "root_cause_chain", "error_message"} else source[field]
    target["status"] = _normalize_status(source.get("status"), _normalize_status(target.get("status"), "ok"))
    source_metrics = [metric for metric in source.get("metrics") or [] if isinstance(metric, dict)]
    if source_metrics:
        target["metrics"] = source_metrics
    source_logs = [_coerce_log(log) for log in source.get("logs") or [] if isinstance(log, dict)]
    if source_logs:
        target["logs"] = source_logs
    if source.get("aliases"):
        target["aliases"] = list(dict.fromkeys([*(target.get("aliases") or []), *source.get("aliases")]))


def _localize_node_content(node: dict[str, Any], lang: str, issue: Issue) -> None:
    dependency = str(node.get("service_name") or node.get("name") or "").strip() or None
    for field in ("summary", "error_message", "root_cause_chain"):
        if node.get(field):
            node[field] = localize_observability_text(
                str(node.get(field)),
                lang,
                app_name=issue.app_name,
                dependency=dependency,
            )
    localized_logs = []
    for log in node.get("logs") or []:
        row = _coerce_log(log)
        row["message"] = localize_observability_text(
            row.get("message"),
            lang,
            app_name=issue.app_name,
            dependency=dependency,
        ) or row.get("message", "")
        localized_logs.append(row)
    node["logs"] = localized_logs


def _prometheus_query(query: str) -> list[dict[str, Any]]:
    base_url = str(settings.MCP_PROMETHEUS_URL or "").rstrip("/")
    if not base_url:
        return []
    url = f"{base_url}/api/v1/query?query={quote_plus(query)}"
    try:
        with urlopen(url, timeout=2.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    data = payload.get("data") if isinstance(payload, dict) else {}
    result = data.get("result") if isinstance(data, dict) else []
    return result if isinstance(result, list) else []


def _prometheus_scalar(query: str) -> tuple[float | None, dict[str, Any]]:
    results = _prometheus_query(query)
    if not results:
        return None, {}
    first = results[0] if isinstance(results[0], dict) else {}
    value = first.get("value") if isinstance(first, dict) else None
    try:
        number = float(value[1]) if isinstance(value, list) and len(value) >= 2 else None
    except (TypeError, ValueError):
        number = None
    metric = first.get("metric") if isinstance(first, dict) and isinstance(first.get("metric"), dict) else {}
    return number, metric


def _prometheus_rows_for_component(issue: Issue, node: dict[str, Any]) -> list[dict[str, str]]:
    issue_meta = _issue_meta(issue)
    issue_dependency = str(
        issue_meta.get("dependency")
        or issue_meta.get("cascade_candidate_root_cause")
        or ""
    ).strip()
    service_name = str(
        node.get("service_name")
        or (issue.app_name if str(node.get("id") or "") == "ai-agent" else "")
        or node.get("id")
        or ""
    ).strip()
    rows: list[dict[str, str]] = []
    timestamp = datetime.utcnow().replace(microsecond=0).isoformat()

    def add(level: str, message: str) -> None:
        rows.append(
            _coerce_log(
                {
                    "timestamp": timestamp,
                    "level": level,
                    "message": message,
                    "source": "prometheus",
                    "source_label": "Prometheus",
                }
            )
        )

    if service_name == "sample-agent" or node.get("id") == "prometheus":
        cpu_percent, _cpu_metric = _prometheus_scalar('sample_agent_pod_cpu_utilisation_percent{app="sample-agent"}')
        cpu_threshold, _ = _prometheus_scalar('sample_agent_pod_cpu_threshold_percent{app="sample-agent"}')
        memory_percent, _ = _prometheus_scalar('sample_agent_pod_memory_utilisation_percent{app="sample-agent"}')
        memory_threshold, _ = _prometheus_scalar('sample_agent_pod_memory_threshold_percent{app="sample-agent"}')
        breach_total, _ = _prometheus_scalar('sum(sample_agent_pod_threshold_breaches_total{app="sample-agent"})')
        if cpu_percent is not None:
            threshold_text = f" / threshold {cpu_threshold:.1f}%" if cpu_threshold is not None else ""
            add(
                "WARN" if cpu_threshold is not None and cpu_percent >= cpu_threshold else "INFO",
                f"sample-agent CPU utilisation is {cpu_percent:.1f}%{threshold_text}.",
            )
        if memory_percent is not None:
            threshold_text = f" / threshold {memory_threshold:.1f}%" if memory_threshold is not None else ""
            add(
                "WARN" if memory_threshold is not None and memory_percent >= memory_threshold else "INFO",
                f"sample-agent memory utilisation is {memory_percent:.1f}%{threshold_text}.",
            )
        if breach_total is not None:
            add(
                "WARN" if breach_total > 0 else "INFO",
                f"sample-agent pod threshold breaches observed: {int(breach_total)}.",
            )

    if service_name == issue.app_name or node.get("id") == "prometheus":
        upstream = issue_dependency or "sample-agent"
        cascade_total, _ = _prometheus_scalar(
            f'sum(dependent_agent_cascade_failures_total{{upstream="{upstream}"}})'
        )
        last_status, _ = _prometheus_scalar(
            f'dependent_agent_last_upstream_status{{upstream="{upstream}"}}'
        )
        if cascade_total is not None:
            add(
                "WARN" if cascade_total > 0 else "INFO",
                f"{issue.app_name} recorded {int(cascade_total)} cascade failure(s) against {upstream}.",
            )
        if last_status is not None:
            upstream_state = "healthy" if last_status >= 1 else "failed"
            level = "INFO" if upstream_state == "healthy" else "ERROR"
            add(level, f"Latest upstream status for {upstream} is {upstream_state}.")

    return rows


def _make_runtime_focus(issue: Issue, analysis: IssueAnalysis | None, meta: dict[str, Any], nodes: list[dict[str, Any]]) -> None:
    primary = next((node for node in nodes if node.get("id") == "ai-agent"), None)
    runtime = next((node for node in nodes if node.get("id") == "docker-runtime"), None)
    host = next((node for node in nodes if node.get("id") == "vm-host"), None)
    background = next((node for node in nodes if node.get("id") == "host-background-job"), None)
    desired = _severity_to_status(issue.severity)
    issue_logs = _fallback_issue_logs(issue, analysis)

    if primary:
        primary["name"] = meta.get("display_app_name") or issue.app_name or primary.get("name")
        primary["service_name"] = issue.app_name or primary.get("service_name") or primary.get("name")
        primary["status"] = desired
        primary["error_message"] = issue.title
        primary["root_cause_chain"] = (analysis.likely_cause if analysis and analysis.likely_cause else issue.description) or primary.get("role")
        primary["logs"] = issue_logs or primary.get("logs", [])
        primary["metrics"] = [
            {"label": "Severity", "value": (issue.sev_label if hasattr(issue, "sev_label") else issue.severity.upper())},
            {"label": "Issue", "value": f"#{issue.id}"},
            {"label": "Status", "value": issue.status},
        ]
    if runtime and desired != "ok":
        runtime["status"] = "warn" if desired == "error" else desired
        runtime["error_message"] = "Runtime path is affected by the selected issue."
    text = " ".join(str(value or "") for value in [issue.issue_type, issue.title, issue.description]).lower()
    if any(token in text for token in _RESOURCE_HINTS):
        if host:
            host["status"] = desired
            host["error_message"] = issue.title
            host["logs"] = issue_logs or host.get("logs", [])
        if background and desired != "ok":
            background["status"] = "warn" if desired == "error" else desired
            background["error_message"] = "Host-side workload or resource pressure is implicated."


def _build_topology(issue: Issue, analysis: IssueAnalysis | None, lang: str = "ja") -> dict[str, Any]:
    template = copy.deepcopy(_read_template())
    meta = _issue_meta(issue)
    source_topology = meta.get("topology") if isinstance(meta.get("topology"), dict) else {}
    lang = normalize_lang(lang)
    localized_cause = _human_text(select_text(analysis, "likely_cause", lang)) if analysis else None
    localized_action = _human_text(select_text(analysis, "recommended_action", lang)) if analysis else None
    nodes = template.get("nodes") or []
    for node in nodes:
        node["status"] = _normalize_status(node.get("default_status"), "ok")
        node["summary"] = _node_summary(node)
        node.setdefault("metrics", [])
        node.setdefault("logs", [])

    if source_topology.get("nodes"):
        for source_node in source_topology.get("nodes") or []:
            if not isinstance(source_node, dict):
                continue
            target = None if _should_keep_distinct_source_node(source_node) else _find_template_match(nodes, source_node)
            if target is None:
                target = copy.deepcopy(source_node)
                target.setdefault("layout", {"x": 69, "y": min(80, 22 + len(nodes) * 8)})
                target.setdefault("zone", "runtime")
                target.setdefault("aliases", [])
                target.setdefault("metrics", [])
                target.setdefault("logs", [])
                nodes.append(target)
            _overlay_node(target, source_node)
    else:
        _make_runtime_focus(issue, analysis, meta, nodes)

    issue_aliases = _issue_aliases(issue, analysis, meta)
    for node in nodes:
        if node.get("id") == "ai-agent" and issue.app_name:
            node["name"] = meta.get("display_app_name") or issue.app_name or node.get("name")
            node["service_name"] = issue.app_name
            node["aliases"] = list(dict.fromkeys([*(node.get("aliases") or []), issue.app_name]))
        if not node.get("logs") and issue_aliases & _node_aliases(node):
            node["logs"] = _fallback_issue_logs(issue, analysis)
        node["status"] = _normalize_status(node.get("status"), "ok")
        node["summary"] = _node_summary(node)
        node["evidence_sources"] = _node_source_descriptors(node, issue)
        _localize_node_content(node, lang, issue)

    health_counts = {"ok": 0, "warn": 0, "error": 0}
    for node in nodes:
        health_counts[_normalize_status(node.get("status"), "ok")] += 1

    impact = source_topology.get("impact") if isinstance(source_topology.get("impact"), dict) else {}
    if not impact:
        impacted_nodes = [node.get("name") for node in nodes if node.get("status") in {"warn", "error"}]
        impact = {
            "where": "VM runtime component path",
            "applications": impacted_nodes[:4],
            "application_count": len(impacted_nodes),
            "user_count": 0,
        }
    if lang == "ja":
        if impact.get("where") == "VM runtime component path":
            impact["where"] = "仮想マシンの実行経路"
        elif "->" in str(impact.get("where") or ""):
            parts = [part.strip() for part in str(impact.get("where") or "").split("->")]
            impact["where"] = " -> ".join(app_display_name_ja(part) for part in parts if part)
        applications = impact.get("applications") if isinstance(impact.get("applications"), list) else []
        if applications:
            impact["applications"] = [app_display_name_ja(item) if isinstance(item, str) else item for item in applications]

    metrics = source_topology.get("metrics") if isinstance(source_topology.get("metrics"), list) else []
    if not metrics:
        metrics = [
            {"label": "Issue", "value": f"#{issue.id}", "tone": "ok"},
            {"label": "Severity", "value": issue.severity.upper(), "tone": "warning" if issue.severity in {"medium", "high"} else "critical" if issue.severity == "critical" else "ok"},
            {"label": "Status", "value": issue.status, "tone": "warning" if issue.status != "RESOLVED" else "ok"},
        ]
    if lang == "ja":
        metric_labels = {"Issue": "問題", "Severity": "重大度", "Status": "状態"}
        for metric in metrics:
            label = metric.get("label")
            if label in metric_labels:
                metric["label"] = metric_labels[label]

    alerts = source_topology.get("alerts") if isinstance(source_topology.get("alerts"), list) else []
    traces = source_topology.get("traces") if isinstance(source_topology.get("traces"), list) else []
    projection = _topology_projection(issue, meta, impact, nodes)
    default_component = projection.get("symptom_component_id")
    if not default_component:
        default_component = next((node.get("id") for node in nodes if node.get("status") == "error"), None)
    if not default_component:
        default_component = next((node.get("id") for node in nodes if node.get("status") == "warn"), None)
    if not default_component:
        default_component = "ai-agent"

    return {
        "template_path": "/static/vm-topology.json",
        "title": "エンタープライズ VM トポロジー" if lang == "ja" else (source_topology.get("title") or template.get("title")),
        "description": "影響を受けた VM コンポーネントと関連シグナルを表示します。" if lang == "ja" else (source_topology.get("description") or template.get("description")),
        "timestamp": source_topology.get("timestamp") or (issue.updated_at.isoformat() if issue.updated_at else datetime.utcnow().isoformat()),
        "zones": template.get("zones") or [],
        "nodes": nodes,
        "edges": template.get("edges") or [],
        "impact": impact,
        "metrics": metrics,
        "alerts": alerts,
        "traces": traces,
        "projection": projection,
        "root_cause_chain": localize_observability_text(
            localized_cause or _human_text(source_topology.get("root_cause_chain")) or _human_text(analysis.likely_cause if analysis and analysis.likely_cause else issue.description),
            lang,
            app_name=issue.app_name,
        ),
        "recommended_action": localize_observability_text(
            localized_action or _human_text(source_topology.get("recommended_action")) or _human_text(analysis.recommended_action if analysis and analysis.recommended_action else ""),
            lang,
            app_name=issue.app_name,
        ),
        "health_counts": health_counts,
        "default_component_id": default_component,
    }


def _component_logs(
    db: Session,
    issue: Issue,
    topology: dict[str, Any],
    component_id: str,
    limit: int,
    lang: str,
    source_filter: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    node = next((item for item in topology.get("nodes") or [] if item.get("id") == component_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="Topology component not found")

    requested_source = str(source_filter or "").strip().lower() or None
    aliases = _node_aliases(node)
    if issue.app_name and component_id == "ai-agent":
        aliases |= _tokens(issue.app_name)

    rows: list[dict[str, str]] = []
    if requested_source in {None, "all", "aiops-local"}:
        for log in node.get("logs") or []:
            if isinstance(log, dict):
                payload = dict(log)
                payload.setdefault("source", "aiops-local")
                payload.setdefault("source_label", "Local")
                rows.append(_coerce_log(payload))

    if requested_source in {None, "all", "prometheus"} and node.get("metrics") and any(source.get("id") == "prometheus" for source in node.get("evidence_sources") or []):
        for metric in node.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            rows.append(
                _coerce_log(
                    {
                        "timestamp": issue.updated_at.isoformat() if issue.updated_at else datetime.utcnow().isoformat(),
                        "level": "INFO" if _normalize_status(node.get("status")) == "ok" else "WARN",
                        "message": f"{metric.get('label') or metric.get('name') or 'Metric'}: {metric.get('value') or '--'}",
                        "source": "prometheus",
                        "source_label": "Prometheus",
                    }
                )
            )
    if requested_source in {None, "all", "prometheus"}:
        rows.extend(_prometheus_rows_for_component(issue, node))

    if issue.trace_id and requested_source in {None, "all", "langfuse"}:
        trace_logs = (
            db.query(TraceLog)
            .filter(TraceLog.trace_id == issue.trace_id)
            .order_by(desc(TraceLog.timestamp))
            .limit(max(40, limit * 5))
            .all()
        )
        for entry in trace_logs:
            haystack = " ".join([entry.logger or "", entry.message or ""]).lower()
            if requested_source != "langfuse" and aliases and not any(token in haystack for token in aliases):
                continue
            rows.append(
                _coerce_log(
                    {
                    "timestamp": entry.timestamp.isoformat() if entry.timestamp else datetime.utcnow().isoformat(),
                    "level": str(entry.level or "INFO").upper(),
                    "message": entry.message or "",
                    "source": "langfuse",
                    "source_label": "Langfuse",
                    }
                )
            )

        spans = (
            db.query(Span)
            .filter(Span.trace_id == issue.trace_id)
            .order_by(desc(Span.started_at))
            .limit(max(30, limit * 4))
            .all()
        )
        for span in spans:
            haystack = " ".join([span.name or "", span.error_message or "", span.span_type or ""]).lower()
            if requested_source != "langfuse" and aliases and not any(token in haystack for token in aliases):
                continue
            level = "ERROR" if span.status == "error" or span.error_message else "INFO"
            message = span.error_message or f"span={span.name} status={span.status} duration_ms={span.duration_ms}"
            rows.append(
                _coerce_log(
                    {
                    "timestamp": span.started_at.isoformat() if span.started_at else datetime.utcnow().isoformat(),
                    "level": level,
                    "message": message,
                    "source": "langfuse",
                    "source_label": "Langfuse",
                    }
                )
            )

    observability_requested = component_id in {"prometheus", "langfuse"} or any(
        str(source.get("id") or "") in {"prometheus", "langfuse"}
        for source in node.get("evidence_sources") or []
        if isinstance(source, dict)
    )

    if not rows:
        rows = [] if observability_requested else _fallback_issue_logs(issue, None)

    deduped: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        key = (row.get("timestamp", ""), row.get("level", ""), row.get("message", ""), row.get("source", ""))
        deduped[key] = row
    ordered = sorted(deduped.values(), key=lambda item: _parse_timestamp(item.get("timestamp")))
    sources = node.get("evidence_sources") or []
    seen_source_ids = {item.get("id") for item in sources if isinstance(item, dict)}
    for row in ordered:
        source_id = row.get("source")
        if not source_id or source_id in seen_source_ids:
            continue
        sources.append(
            {
                "id": source_id,
                "label": row.get("source_label") or source_id.title(),
                "kind": "logs",
            }
        )
        seen_source_ids.add(source_id)
    localized = ordered[-limit:]
    dependency = str(node.get("service_name") or node.get("name") or "").strip() or None
    for row in localized:
        row["message"] = localize_observability_text(
            row.get("message"),
            lang,
            app_name=issue.app_name,
            dependency=dependency,
        ) or row.get("message", "")
    return node, localized, sources


@router.get("/template")
def get_topology_template():
    return _read_template()


@router.get("/issues/{issue_id}")
def get_issue_topology(issue_id: int, lang: str = Query("ja"), db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    analysis = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue_id).first()
    topology = _build_topology(issue, analysis, lang)
    return {
        "issue": {
            "id": issue.id,
            "app_name": issue.app_name,
            "severity": issue.severity,
            "status": issue.status,
            "title": issue.title,
            "description": issue.description,
            "rule_id": issue.rule_id,
            "trace_id": issue.trace_id,
            "created_at": issue.created_at.isoformat() if issue.created_at else None,
            "updated_at": issue.updated_at.isoformat() if issue.updated_at else None,
        },
        "analysis": {
            "status": analysis.status if analysis else "missing",
            "likely_cause": _human_text(analysis.likely_cause) if analysis else None,
            "likely_cause_en": _human_text(analysis.likely_cause_en) if analysis else None,
            "likely_cause_ja": _human_text(analysis.likely_cause_ja) if analysis else None,
            "evidence": analysis.evidence if analysis else None,
            "recommended_action": _human_text(analysis.recommended_action) if analysis else None,
            "recommended_action_en": _human_text(analysis.recommended_action_en) if analysis else None,
            "recommended_action_ja": _human_text(analysis.recommended_action_ja) if analysis else None,
            "full_summary_en": _human_text(analysis.full_summary_en) if analysis else None,
            "full_summary_ja": _human_text(analysis.full_summary_ja) if analysis else None,
            "generated_at": analysis.generated_at.isoformat() if analysis and analysis.generated_at else None,
            "model_used": analysis.model_used if analysis else None,
        },
        "topology": topology,
    }


@router.get("/issues/{issue_id}/components/{component_id}/logs")
def get_component_logs(
    issue_id: int,
    component_id: str,
    limit: int = Query(40, ge=1, le=200),
    lang: str = Query("ja"),
    source: str | None = Query(None),
    db: Session = Depends(get_db),
):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")
    analysis = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue_id).first()
    topology = _build_topology(issue, analysis, lang)
    node, logs, sources = _component_logs(db, issue, topology, component_id, limit, lang, source)
    return {
        "issue_id": issue_id,
        "component_id": component_id,
        "component": {
            "id": node.get("id"),
            "name": node.get("name"),
            "status": node.get("status"),
            "summary": node.get("summary"),
            "role": node.get("role"),
            "evidence_sources": node.get("evidence_sources") or [],
        },
        "live": issue.status != "RESOLVED",
        "refresh_interval_ms": 5000,
        "sources": sources,
        "logs": logs,
    }
