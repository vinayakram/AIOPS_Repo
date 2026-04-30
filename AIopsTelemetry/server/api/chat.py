from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.database.engine import get_db
from server.database.models import Issue, IssueAnalysis, Trace
from server.engine import mcp_observability
from server.engine.knowledge_base import find_matches_for_issue


router = APIRouter(prefix="/chat", tags=["chat"])


class LiveRCARequest(BaseModel):
    message: str = ""
    issue_id: Optional[int] = None
    service: Optional[str] = None
    root_candidate_service: Optional[str] = None
    timestamp: Optional[str] = None
    window_minutes: int = 10
    lang: str = "ja"


class RCADiagramRequest(BaseModel):
    issue_id: int
    lang: str = "ja"


@router.post("/rca/live")
def live_rca(payload: LiveRCARequest, db: Session = Depends(get_db)):
    issue = None
    if payload.issue_id:
        issue = db.query(Issue).filter(Issue.id == payload.issue_id).first()
        if not issue:
            raise HTTPException(404, "Issue not found")

    affected_service = payload.service or (issue.app_name if issue else None) or "service"
    root_candidate = payload.root_candidate_service or _infer_root_candidate(payload.message, affected_service)
    timestamp = payload.timestamp or _live_timestamp()
    window = max(1, min(payload.window_minutes or 10, 60))

    steps = [
        {"type": "status", "message": _t(payload.lang, "stored")},
        {"type": "tool_call", "tool": "correlate_cross_service_incident", "message": _t(payload.lang, "mcp")},
    ]
    try:
        result = mcp_observability.call_tool(
            "correlate_cross_service_incident",
            {
                "root_candidate_service": root_candidate,
                "affected_service": affected_service,
                "timestamp": timestamp,
                "window_minutes": window,
            },
        )
    except Exception as exc:
        return {
            "assistant": _assistant_name(payload.lang),
            "mode": "mcp_live_rca",
            "status": "mcp_unavailable",
            "answer": _t(payload.lang, "mcp_failed", error=str(exc)),
            "confidence": "unknown",
            "steps": steps + [{"type": "error", "message": str(exc)}],
            "evidence": [],
            "suggested_next_action": _t(payload.lang, "fallback"),
        }

    evidence = _top_evidence(result.get("evidence") or [])
    kb_matches = find_matches_for_issue(db, issue, limit=3) if issue is not None else []
    confidence = result.get("confidence") or "unknown"
    answer = _answer(payload.lang, root_candidate, affected_service, confidence, evidence, kb_matches)
    steps.extend([
        {"type": "evidence", "message": _t(payload.lang, "evidence", count=len(evidence))},
        {"type": "answer", "message": answer},
    ])
    return {
        "assistant": _assistant_name(payload.lang),
        "mode": "mcp_live_rca",
        "status": "ok",
        "answer": answer,
        "confidence": confidence,
        "root_candidate_service": root_candidate,
        "affected_service": affected_service,
        "timestamp": timestamp,
        "window_minutes": window,
        "steps": steps,
        "evidence": evidence,
        "knowledge_matches": [
            {
                "source": item.source,
                "title": item.title,
                "remediation_type": item.remediation_type,
                "confidence": item.confidence,
                "reason": item.reason,
                "recommended_action": item.recommended_action,
                "validation_steps": item.validation_steps,
                "prior_outcome": item.prior_outcome,
            }
            for item in kb_matches
        ],
        "suggested_next_action": _next_action(payload.lang, confidence),
        "raw_summary": result.get("summary"),
    }


@router.post("/rca/diagram")
def rca_diagram(payload: RCADiagramRequest, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == payload.issue_id).first()
    if not issue:
        raise HTTPException(404, "Issue not found")
    analysis = (
        db.query(IssueAnalysis)
        .filter(IssueAnalysis.issue_id == payload.issue_id)
        .first()
    )
    rca_data = _analysis_json(analysis)
    diagram = _build_diagram(db, issue, analysis, rca_data, payload.lang)
    return {
        "assistant": _assistant_name(payload.lang),
        "mode": "rca_diagram_agent",
        "status": "ok",
        **diagram,
    }


def _infer_root_candidate(message: str, affected_service: str) -> str:
    text = (message or "").strip()
    return text or affected_service


def _analysis_json(analysis: IssueAnalysis | None) -> dict[str, Any]:
    if not analysis:
        return {}
    for raw in (analysis.rca_json, analysis.full_summary, analysis.full_summary_en, analysis.full_summary_ja):
        if not raw:
            continue
        try:
            data = json.loads(raw)
            return data.get("data") or data
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
    return {}


def _unwrap_rca(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "norm": (data.get("normalization") or {}).get("incident") or data.get("normalization") or {},
        "corr": (data.get("correlation") or {}).get("correlation") or data.get("correlation") or {},
        "err": (data.get("error_analysis") or {}).get("analysis") or data.get("error_analysis") or {},
        "rca": (data.get("rca") or {}).get("rca") or data.get("rca") or {},
        "rec": (data.get("recommendations") or {}).get("recommendations") or data.get("recommendations") or {},
    }


def _build_diagram(
    db: Session,
    issue: Issue,
    analysis: IssueAnalysis | None,
    data: dict[str, Any],
    lang: str,
) -> dict[str, Any]:
    p = _unwrap_rca(data)
    root = p["rca"].get("root_cause") or {}
    corr_root = p["corr"].get("root_cause_candidate") or {}
    first_error = (p["err"].get("errors") or [{}])[0]
    impact = (p["err"].get("error_impacts") or [{}])[0]
    solution = (p["rec"].get("solutions") or [{}])[0]

    layer = root.get("component") or corr_root.get("component") or first_error.get("component") or issue.app_name
    trigger = (
        p["norm"].get("error_summary")
        or first_error.get("error_message")
        or issue.issue_type
        or "incident signal"
    )
    affected = impact.get("affected_service") or issue.app_name
    failure = impact.get("impact_description") or issue.title
    cause = (
        p["rca"].get("rca_summary")
        or root.get("description")
        or (analysis.likely_cause if analysis else "")
        or issue.description
        or issue.title
    )
    action = (
        solution.get("description")
        or p["rec"].get("recommendation_summary")
        or (analysis.recommended_action if analysis else "")
        or "Review evidence and select remediation."
    )
    impact_snapshot = _impact_snapshot(db, issue, p, root_service=layer, affected_service=affected, lang=lang)

    labels = (
        {
            "title": f"Impact flow for issue #{issue.id}",
            "layer": "Layer where signal appeared",
            "trigger": "Trigger",
            "affected": "Impacted service",
            "cause": "Likely cause",
            "action": "Recommended action",
        }
        if lang.startswith("en")
        else {
            "title": f"問題 #{issue.id} の影響フロー",
            "layer": "発生レイヤー",
            "trigger": "きっかけ",
            "affected": "影響先",
            "cause": "推定原因",
            "action": "推奨対応",
        }
    )
    nodes = [
        {"id": "A", "kind": "layer", "title": labels["layer"], "body": layer},
        {"id": "B", "kind": "trigger", "title": labels["trigger"], "body": trigger},
        {"id": "C", "kind": "affected", "title": labels["affected"], "body": _impact_node_body(affected, failure, impact_snapshot, lang)},
        {"id": "D", "kind": "cause", "title": labels["cause"], "body": cause},
        {"id": "E", "kind": "action", "title": labels["action"], "body": action},
    ]
    mermaid = _diagram_mermaid(nodes)
    return {
        "title": labels["title"],
        "summary": cause,
        "impact_snapshot": impact_snapshot,
        "diagram_type": "mermaid",
        "nodes": nodes,
        "edges": [["A", "B"], ["B", "C"], ["C", "D"], ["D", "E"]],
        "mermaid": mermaid,
        "recommended_view": "inline_issue_panel",
    }



def _issue_metadata(issue: Issue) -> dict[str, Any]:
    if not issue.metadata_json:
        return {}
    try:
        data = json.loads(issue.metadata_json)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _impact_snapshot(
    db: Session,
    issue: Issue,
    p: dict[str, Any],
    *,
    root_service: str,
    affected_service: str,
    lang: str,
) -> dict[str, Any]:
    metadata = _issue_metadata(issue)
    corr_root = p.get("corr", {}).get("root_cause_candidate") or {}
    impact = (p.get("err", {}).get("error_impacts") or [{}])[0]

    applications: list[str] = []

    def add_app(value: Any) -> None:
        app = str(value or "").strip()
        if not app or app.lower() in {"all", "unknown"}:
            return
        if app not in applications:
            applications.append(app)

    add_app(issue.app_name)
    add_app(affected_service)
    add_app(root_service)
    add_app(corr_root.get("component"))
    add_app(impact.get("affected_service"))
    since = (issue.created_at or datetime.utcnow()) - timedelta(minutes=15)
    base = db.query(Trace).filter(Trace.started_at >= since)
    if applications:
        base = base.filter(Trace.app_name.in_(applications))

    user_count = (
        base.filter(Trace.user_id.isnot(None), Trace.user_id != "")
        .with_entities(func.count(func.distinct(Trace.user_id)))
        .scalar()
        or 0
    )
    basis = "user_id"
    if user_count == 0:
        user_count = (
            base.filter(Trace.session_id.isnot(None), Trace.session_id != "")
            .with_entities(func.count(func.distinct(Trace.session_id)))
            .scalar()
            or 0
        )
        basis = "session_id"
    if user_count == 0:
        user_count = base.with_entities(func.count(Trace.id)).scalar() or metadata.get("breach_count_window") or 0
        basis = "trace_count"

    where = f"{affected_service} <- {root_service}"
    if lang.startswith("ja"):
        impact_line = f"影響箇所: {where} / 影響ユーザー: {user_count} / 影響アプリ: {len(applications)}件"
    else:
        impact_line = f"Impact: {where} / users: {user_count} / apps: {len(applications)}"

    return {
        "where": where,
        "user_count": int(user_count or 0),
        "user_count_basis": basis,
        "applications": applications,
        "application_count": len(applications),
        "impact_line": impact_line,
    }


def _impact_node_body(affected: str, failure: str, snapshot: dict[str, Any], lang: str) -> str:
    apps = ", ".join(snapshot.get("applications") or [])
    impact_line = snapshot.get("impact_line", "")
    if lang.startswith("ja"):
        return f"{impact_line}. 対象アプリ: {apps or affected}. 症状: {failure}"
    return f"{impact_line}. Applications: {apps or affected}. Symptom: {failure}"

def _diagram_mermaid(nodes: list[dict[str, str]]) -> str:
    def safe(text: str) -> str:
        value = str(text or "").replace('"', "'").replace("\n", " ")
        return value[:140]

    lines = ["flowchart LR"]
    for node in nodes:
        lines.append(f'  {node["id"]}["{safe(node["title"])}<br/>{safe(node["body"])}"]')
    for left, right in [["A", "B"], ["B", "C"], ["C", "D"], ["D", "E"]]:
        lines.append(f"  {left} --> {right}")
    lines.extend([
        "  classDef layer fill:#f4f7ff,stroke:#155eef,color:#111827;",
        "  classDef trigger fill:#fffbf4,stroke:#b54708,color:#111827;",
        "  classDef affected fill:#fff5f4,stroke:#d92d20,color:#111827;",
        "  classDef cause fill:#f4f3ff,stroke:#6941c6,color:#111827;",
        "  classDef action fill:#f6fef9,stroke:#067647,color:#111827;",
        "  class A layer;",
        "  class B trigger;",
        "  class C affected;",
        "  class D cause;",
        "  class E action;",
    ])
    return "\n".join(lines)


def _live_timestamp() -> str:
    dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _top_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def rank(ev: dict[str, Any]) -> int:
        sev = ev.get("severity")
        return {"critical": 4, "error": 3, "warning": 2, "info": 1}.get(sev, 0)

    cleaned = []
    seen = set()
    for ev in sorted(items, key=rank, reverse=True):
        values = ev.get("values") or {}
        labels = values.get("labels") if isinstance(values, dict) else {}
        key = (
            ev.get("source"),
            ev.get("service"),
            values.get("query_name") if isinstance(values, dict) else None,
            values.get("value") if isinstance(values, dict) else None,
            tuple(sorted((labels or {}).items())) if isinstance(labels, dict) else (),
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "source": ev.get("source"),
            "kind": ev.get("kind"),
            "timestamp": ev.get("timestamp"),
            "service": ev.get("service"),
            "severity": ev.get("severity"),
            "summary": ev.get("summary"),
            "query_name": values.get("query_name") if isinstance(values, dict) else None,
            "value": values.get("value") if isinstance(values, dict) else None,
            "labels": labels if isinstance(labels, dict) else {},
            "evidence_url": ev.get("evidence_url"),
        })
    return cleaned[:8]


def _answer(
    lang: str,
    root: str,
    affected: str,
    confidence: str,
    evidence: list[dict[str, Any]],
    kb_matches: list[Any],
) -> str:
    kb = kb_matches[0] if kb_matches else None
    has_hazard = any((ev.get("severity") in {"warning", "error", "critical"}) for ev in evidence)
    if lang.startswith("en"):
        if confidence == "low" and not has_hazard:
            if kb:
                return (
                    f"MCP is connected, but the selected live window does not show an active hazard for {affected}. "
                    f"The current Prometheus evidence is healthy or informational. The knowledge base still matches "
                    f"{kb.title}, so use that as the expected remediation pattern when you reproduce the incident."
                )
            return (
                f"MCP is connected, but the selected live window does not show an active hazard for {affected}. "
                "The current Prometheus evidence is healthy or informational."
            )
        if kb:
            return (
                f"Live MCP evidence points to {root} as the likely upstream contributor to {affected}. "
                f"Knowledge base match: {kb.title}. Recommended remediation type: {kb.remediation_type}."
            )
        if confidence in {"high", "medium"}:
            return f"Live MCP evidence points to {root} as the likely upstream cause of {affected} failures."
        return f"AIOPS could not prove that {root} caused {affected}; the live evidence is weak or missing."
    if confidence == "low" and not has_hazard:
        if kb:
            return (
                f"MCPには接続できていますが、選択した時間枠では {affected} のライブ障害は確認できません。"
                "Prometheusの現在値は正常または参考情報です。"
                f"ただしナレッジベースでは「{kb.title}」に一致するため、再現時の対応パターンとして使えます。"
            )
        return (
            f"MCPには接続できていますが、選択した時間枠では {affected} のライブ障害は確認できません。"
            "Prometheusの現在値は正常または参考情報です。"
        )
    if kb:
        return (
            f"リアルタイムMCP証拠では、{affected} の障害に上流の {root} が関与している可能性があります。"
            f"ナレッジベースでは「{kb.title}」に一致し、推奨対応タイプは {kb.remediation_type} です。"
        )
    if confidence in {"high", "medium"}:
        return f"リアルタイムMCP証拠では、{affected} の障害原因は上流の {root} である可能性が高いです。"
    return f"助手 は {root} が {affected} を障害させた証拠を十分には確認できませんでした。"


def _assistant_name(lang: str) -> str:
    return "AIOPS" if lang.startswith("en") else "助手"


def _next_action(lang: str, confidence: str) -> str:
    if lang.startswith("en"):
        if confidence == "low":
            return "Next: run the cascade/load scenario or pick a currently failing issue, then ask why again so MCP can capture warning/error evidence."
        return "Check the root service threshold and recent deploy/config changes, then re-run the MCP RCA after mitigation."
    if confidence == "low":
        return "次に、カスケード負荷シナリオを実行するか、現在発生中の問題を選んでから、もう一度「なぜ起きたの？」と聞いてください。"
    return "上流サービスのしきい値、直近の設定変更、503発生状況を確認し、対応後にMCP RCAを再実行してください。"


def _t(lang: str, key: str, **kwargs) -> str:
    en = lang.startswith("en")
    table = {
        "stored": ("Checking issue context...", "問題コンテキストを確認しています..."),
        "mcp": ("Calling MCP observability tools...", "MCP observability tool を呼び出しています..."),
        "evidence": (f"Found {kwargs.get('count', 0)} evidence item(s).", f"証拠を{kwargs.get('count', 0)}件確認しました。"),
        "mcp_failed": (f"MCP live RCA is unavailable: {kwargs.get('error')}", f"MCPライブRCAを利用できません: {kwargs.get('error')}"),
        "fallback": ("Use stored RCA while MCP is unavailable.", "MCPが復旧するまでは保存済みRCAを利用してください。"),
    }
    left, right = table[key]
    return left if en else right
