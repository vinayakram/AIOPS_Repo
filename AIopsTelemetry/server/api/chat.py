from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from server.database.engine import get_db
from server.database.models import Issue, IssueAnalysis, Trace
from server.engine import mcp_observability
from server.engine.bilingual import app_display_name_ja, issue_description_ja, normalize_lang
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
    # メッセージと issue 情報を元に、MCP へライブ RCA を問い合わせる。
    lang = _message_lang(payload.message, payload.lang)
    issue = None
    analysis = None
    if payload.issue_id:
        issue = db.query(Issue).filter(Issue.id == payload.issue_id).first()
        if not issue:
            raise HTTPException(404, "Issue not found")
        analysis = (
            db.query(IssueAnalysis)
            .filter(IssueAnalysis.issue_id == payload.issue_id)
            .first()
        )

    affected_service_raw = _resolve_affected_service(payload.service, issue)
    root_candidate_raw = payload.root_candidate_service or _infer_root_candidate(
        payload.message,
        affected_service_raw,
        issue=issue,
    )
    affected_service = _service_label(affected_service_raw, lang)
    root_candidate = _service_label(root_candidate_raw, lang)
    timestamp = payload.timestamp or _live_timestamp()
    window = max(1, min(payload.window_minutes or 10, 60))

    steps = [
        {"type": "status", "message": _t(lang, "stored")},
        {"type": "tool_call", "tool": "correlate_cross_service_incident", "message": _t(lang, "mcp")},
    ]
    try:
        # UI にはローカライズ済み表示名を返すが、MCP 呼び出し自体は生の service 名を使う。
        result = mcp_observability.call_tool(
            "correlate_cross_service_incident",
            {
                "root_candidate_service": root_candidate_raw,
                "affected_service": affected_service_raw,
                "timestamp": timestamp,
                "window_minutes": window,
            },
        )
    except Exception as exc:
        return {
            "assistant": _assistant_name(lang),
            "mode": "mcp_live_rca",
            "status": "mcp_unavailable",
            "answer": _t(lang, "mcp_failed", error=str(exc)),
            "confidence": "unknown",
            "steps": steps + [{"type": "error", "message": str(exc)}],
            "evidence": [],
            "suggested_next_action": _t(lang, "fallback"),
        }

    evidence = _top_evidence(result.get("evidence") or [])
    kb_matches = find_matches_for_issue(db, issue, limit=3) if issue is not None else []
    confidence = result.get("confidence") or "unknown"
    answer = _answer_for_question(
        lang=lang,
        message=payload.message,
        issue=issue,
        analysis=analysis,
        root=root_candidate,
        affected=affected_service,
        confidence=confidence,
        evidence=evidence,
        kb_matches=kb_matches,
    )
    steps.extend([
        {"type": "evidence", "message": _t(lang, "evidence", count=len(evidence))},
        {"type": "answer", "message": answer},
    ])
    return {
        "assistant": _assistant_name(lang),
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
            _serialize_knowledge_match(item, lang=lang, issue=issue)
            for item in kb_matches
        ],
        "suggested_next_action": _next_action(lang, confidence),
        "raw_summary": result.get("summary"),
    }


@router.post("/rca/diagram")
def rca_diagram(payload: RCADiagramRequest, db: Session = Depends(get_db)):
    # 保存済み RCA から、画面描画向けの簡易ノード構造を組み立てる。
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


def _infer_root_candidate(message: str, affected_service: str, *, issue: Issue | None = None) -> str:
    # ユーザー文面に別サービス名が含まれていれば、根本原因候補として優先する。
    text = str(message or "").strip()
    if not text:
        return affected_service

    known_services = _known_issue_services(issue, affected_service)
    lowered = text.lower()
    affected_lower = str(affected_service or "").strip().lower()

    for service in known_services:
        if service.lower() != affected_lower and service.lower() in lowered:
            return service

    token_pattern = re.compile(r"\b[A-Za-z0-9_.-]{2,80}\b")
    generic_tokens = {
        "why", "what", "when", "where", "how", "did", "this", "that", "happen", "happened",
        "issue", "problem", "incident", "service", "services", "root", "cause", "caused",
        "failure", "failing", "broken", "explain", "reason", "because", "and", "the", "for",
        "whydidthishappen", "tell", "show", "me",
    }
    for token in token_pattern.findall(text):
        candidate = token.strip()
        candidate_lower = candidate.lower()
        if candidate_lower in generic_tokens:
            continue
        if candidate_lower == affected_lower:
            return affected_service
        if not re.search(r"[-_.\d]", candidate):
            continue
        return candidate

    return affected_service


def _resolve_affected_service(payload_service: str | None, issue: Issue | None) -> str:
    # 明示入力、Issue 本体、topology 情報の順で影響サービス名を解決する。
    for candidate in (
        str(payload_service or "").strip(),
        str(issue.app_name if issue else "").strip(),
        *list(_known_issue_services(issue)),
        "service",
    ):
        if _is_valid_service_name(candidate):
            return candidate
    return "service"


def _known_issue_services(issue: Issue | None, affected_service: str | None = None) -> list[str]:
    values: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if not text or not _is_valid_service_name(text):
            return
        if text not in values:
            values.append(text)

    add(affected_service)
    if issue is None:
        return values

    add(issue.app_name)
    metadata = _issue_metadata(issue)
    topology = metadata.get("topology") if isinstance(metadata, dict) else {}
    nodes = topology.get("nodes") if isinstance(topology, dict) else []
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            add(node.get("id"))
            add(node.get("name"))
            add(node.get("service"))
            add(node.get("service_name"))
            add(node.get("app_name"))
    return values


def _is_valid_service_name(value: str | None) -> bool:
    text = str(value or "").strip()
    return bool(text) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", text) is not None


def _message_lang(message: str | None, requested_lang: str | None) -> str:
    if requested_lang and str(requested_lang).strip():
        return normalize_lang(requested_lang)
    text = str(message or "").strip()
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text):
        return "ja"
    if len(re.findall(r"[A-Za-z]", text)) >= 4:
        return "en"
    return normalize_lang(requested_lang)


def _analysis_json(analysis: IssueAnalysis | None) -> dict[str, Any]:
    if not analysis:
        return {}
    # 保存形式が複数あるため、読めた JSON を順に採用する。
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
    # 外部 RCA サービスのレスポンス揺れをここで吸収し、後段は固定キーで扱う。
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
    # 原因・影響・推奨対応を 5 ノードのフローに正規化して UI に返す。
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
    # issue 近傍 15 分の Trace を集計し、影響範囲の概算を作る。
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

    # 同種の証拠をある程度まとめ、上位だけを UI に返す。
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


def _service_label(name: str | None, lang: str) -> str:
    if normalize_lang(lang) == "ja":
        return app_display_name_ja(name)
    return (name or "service").strip() or "service"


def _localized_remediation_type(value: str | None, lang: str) -> str:
    key = str(value or "").strip().lower()
    if normalize_lang(lang) != "ja":
        return key or "unknown"
    labels = {
        "infra_change": "インフラ対応",
        "config_change": "設定変更",
        "code_change": "コード修正",
        "runbook_change": "運用手順変更",
        "investigation_only": "追加調査",
        "human_handoff": "担当引き継ぎ",
        "unknown": "未分類",
    }
    return labels.get(key, "追加調査")


def _localized_kb_title(title: str | None, lang: str) -> str:
    text = (title or "").strip()
    if normalize_lang(lang) != "ja":
        return text
    lowered = text.lower()
    mappings = {
        "scale container capacity before tuning thresholds": "しきい値調整の前にコンテナ容量を確保する",
        "stabilize the upstream and protect the downstream": "上流を安定化し、下流を保護する",
        "throttle, queue, and configure provider capacity": "スロットリング、キュー制御、プロバイダー容量設定を行う",
        "normalize inputs and preserve valid user intent": "入力を正規化し、正しい利用者意図を保つ",
        "restore memory headroom and check for leaks": "メモリ余力を回復し、リークを確認する",
        "free space, expand volume, and stop uncontrolled growth": "空き容量を回復し、ボリュームを拡張して異常増加を止める",
        "tune pool limits and close leaked connections": "プール上限を調整し、解放漏れ接続をなくす",
        "restore dns path and dependency discovery": "DNS経路と依存先解決を復旧する",
        "rotate credentials and validate reload behavior": "認証情報を更新し、再読み込み動作を確認する",
        "rollback first when blast radius is active": "影響拡大中はまずロールバックする",
        "scale consumers and control producer rate": "コンシューマを増やし、投入レートを制御する",
        "restore cache health and reduce miss storms": "キャッシュ状態を回復し、ミス集中を抑える",
        "isolate noisy neighbors and restore reserved capacity": "ノイジーネイバーを隔離し、予約容量を回復する",
        "fix autoscaling inputs before raising thresholds": "しきい値を上げる前にオートスケール条件を修正する",
        "increase storage throughput and reduce hot writes": "ストレージ性能を上げ、集中書き込みを緩和する",
        "increase worker capacity and remove blocking work": "ワーカー能力を上げ、ブロッキング処理を除く",
    }
    for source, translated in mappings.items():
        if source in lowered:
            return translated
    return text or "関連ナレッジ"


def _localized_kb_reason(reason: str | None, lang: str) -> str:
    text = (reason or "").strip()
    if normalize_lang(lang) != "ja":
        return text
    lowered = text.lower()
    if "matched industry pattern" in lowered:
        return "業界パターンに一致するため、参考対応を提示します。"
    if "similar past incident resolved successfully" in lowered:
        return "過去の類似事象で解決実績があるため、再利用候補です。"
    if "similar past incident found" in lowered:
        return "過去の類似事象が見つかりましたが、解決結果は限定的です。"
    return text or "関連ナレッジに一致しました。"


def _localized_kb_action(action: str | None, lang: str, issue: Issue | None) -> str:
    text = (action or "").strip()
    if normalize_lang(lang) != "ja":
        return text
    translated = issue_description_ja(
        text,
        app_name=issue.app_name if issue else None,
        rule_id=issue.rule_id if issue else None,
        allow_live_translate=False,
    )
    if translated and translated != text:
        return translated
    lowered = text.lower()
    action_map = {
        "increase cpu/memory allocation": "CPUやメモリ割り当てを増やし、同じ負荷で再確認してください。",
        "restore upstream health first": "まず上流サービスを復旧し、その後に下流保護設定を確認してください。",
        "apply request shaping": "リクエスト量を整形し、バックオフとキュー制御を有効にしてください。",
        "fix validation and normalization rules": "入力検証と正規化ルールを修正し、再発防止テストを追加してください。",
        "restore free disk/inode capacity immediately": "ディスクまたは inode の空きを回復し、必要なら容量を拡張してください。",
        "check dns resolver health": "DNS リゾルバ、サービスディスカバリ、ネットワーク設定を確認してください。",
        "rotate or restore the expired credential": "期限切れ認証情報を更新し、アプリが再読み込みできているか確認してください。",
        "roll back or disable the change first": "影響が継続している間は、まず変更を戻すか無効化してください。",
        "scale worker/consumer capacity": "ワーカーやコンシューマの処理能力を引き上げてください。",
        "move the affected workload": "影響中のワークロードを分離し、重要サービスの予約容量を確保してください。",
    }
    for source, translated_text in action_map.items():
        if source in lowered:
            return translated_text
    return text or "推奨対応を確認してください。"


def _localized_validation_steps(steps: list[str], lang: str) -> list[str]:
    if normalize_lang(lang) != "ja":
        return steps
    if not steps:
        return []
    translated: list[str] = []
    for step in steps:
        text = (step or "").strip()
        lowered = text.lower()
        if not text:
            continue
        if "cpu and memory headroom" in lowered:
            translated.append("同じ負荷条件で CPU とメモリの余力を確認してください。")
        elif "health checks remain ok" in lowered:
            translated.append("ヘルスチェックが正常に戻り、HTTP 503 が止まったことを確認してください。")
        elif "threshold breach" in lowered:
            translated.append("検証時間枠で同じしきい値超過が再発していないことを確認してください。")
        elif "error rate and latency recover" in lowered:
            translated.append("上流のエラー率と遅延が回復したことを確認してください。")
        else:
            translated.append(text)
    return translated


def _localized_prior_outcome(value: str | None, lang: str) -> str:
    text = (value or "").strip()
    if normalize_lang(lang) != "ja":
        return text
    labels = {
        "succeeded": "成功",
        "failed": "失敗",
        "unknown": "不明",
        "partial": "一部成功",
    }
    return labels.get(text.lower(), text or "不明")


def _serialize_knowledge_match(item: Any, *, lang: str, issue: Issue | None) -> dict[str, Any]:
    return {
        "source": item.source,
        "title": _localized_kb_title(item.title, lang),
        "remediation_type": _localized_remediation_type(item.remediation_type, lang),
        "confidence": item.confidence,
        "reason": _localized_kb_reason(item.reason, lang),
        "recommended_action": _localized_kb_action(item.recommended_action, lang, issue),
        "validation_steps": _localized_validation_steps(item.validation_steps, lang),
        "prior_outcome": _localized_prior_outcome(item.prior_outcome, lang),
    }


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
            f"ナレッジベースでは「{_localized_kb_title(kb.title, lang)}」に一致し、推奨対応タイプは {_localized_remediation_type(kb.remediation_type, lang)} です。"
        )
    if confidence in {"high", "medium"}:
        return f"リアルタイムMCP証拠では、{affected} の障害原因は上流の {root} である可能性が高いです。"
    return f"AIOPSでは、{root} が {affected} に影響を与えたと断定できるだけのライブ証拠はまだ十分に確認できていません。"


def _answer_for_question(
    lang: str,
    message: str,
    issue: Issue | None,
    analysis: IssueAnalysis | None,
    root: str,
    affected: str,
    confidence: str,
    evidence: list[dict[str, Any]],
    kb_matches: list[Any],
) -> str:
    text = (message or "").strip().lower()
    has_hazard = any((ev.get("severity") in {"warning", "error", "critical"}) for ev in evidence)
    preferred_cause = ""
    preferred_action = ""
    preferred_summary = ""
    if analysis:
        if normalize_lang(lang) == "en":
            preferred_cause = analysis.likely_cause_en or ""
            preferred_action = analysis.recommended_action_en or ""
            preferred_summary = analysis.full_summary_en or ""
        else:
            preferred_cause = analysis.likely_cause_ja or ""
            preferred_action = analysis.recommended_action_ja or ""
            preferred_summary = analysis.full_summary_ja or ""
    cause = _analysis_text(
        preferred_cause,
        analysis.likely_cause if analysis else None,
    )
    action = _analysis_text(
        preferred_action,
        analysis.recommended_action if analysis else None,
    )
    summary = _analysis_text(
        preferred_summary,
        analysis.full_summary if analysis else None,
    )
    kb = kb_matches[0] if kb_matches else None

    if _asks_for_status(text):
        return _status_answer(lang, issue, affected, confidence, has_hazard, evidence)
    if _asks_for_service(text):
        return _service_answer(lang, issue, root, affected, confidence)
    if _asks_for_evidence(text):
        return _evidence_answer(lang, evidence, confidence)
    if _asks_for_action(text):
        if action:
            return action
        if kb and kb.recommended_action:
            return _localized_kb_action(kb.recommended_action, lang, issue)
        return _next_action(lang, confidence)
    if _asks_for_cause(text):
        if cause:
            return cause
        if summary:
            return summary
        return _answer(lang, root, affected, confidence, evidence, kb_matches)
    if issue and _asks_for_ticket_details(text):
        return _ticket_answer(lang, issue)

    parts = []
    if issue:
        parts.append(_ticket_answer(lang, issue))
    if cause:
        parts.append(cause)
    elif summary:
        parts.append(summary)
    else:
        parts.append(_answer(lang, root, affected, confidence, evidence, kb_matches))
    if action:
        parts.append(action)
    elif kb and kb.recommended_action:
        parts.append(_localized_kb_action(kb.recommended_action, lang, issue))
    elif evidence:
        parts.append(_evidence_answer(lang, evidence, confidence))
    return " ".join(part for part in parts if part)


def _analysis_text(preferred: str | None, fallback: str | None) -> str:
    for value in (preferred, fallback):
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _asks_for_cause(text: str) -> bool:
    return any(token in text for token in ["why", "cause", "root cause", "reason", "what happened", "failure", "issue", "problem", "原因", "なぜ", "理由"])


def _asks_for_action(text: str) -> bool:
    return any(token in text for token in ["next step", "action", "fix", "remed", "mitigat", "resolve", "what should", "対応", "次", "修正", "対処"])


def _asks_for_status(text: str) -> bool:
    return any(token in text for token in ["status", "current", "now", "active", "live", "ongoing", "resolved", "still", "happening", "状態", "現在", "進行", "ライブ"])


def _asks_for_service(text: str) -> bool:
    return any(token in text for token in ["which service", "what service", "service", "app", "affected", "impact", "component", "どの", "サービス", "影響"])


def _asks_for_evidence(text: str) -> bool:
    return any(token in text for token in ["evidence", "metric", "metrics", "log", "logs", "prometheus", "proof", "signal", "trace", "証拠", "ログ", "メトリクス"])


def _asks_for_ticket_details(text: str) -> bool:
    return any(token in text for token in ["severity", "priority", "ticket", "issue id", "id", "status of issue", "重大度", "チケット", "番号"])


def _ticket_answer(lang: str, issue: Issue) -> str:
    if normalize_lang(lang) == "en":
        return (
            f"Issue #{issue.id} is {issue.status} with {issue.severity} severity for {issue.app_name}. "
            f"{issue.title_en or issue.title}"
        )
    return (
        f"問題 #{issue.id} は {_localized_app_name(issue.app_name, lang)} で発生しており、"
        f"重大度は {_localized_severity(issue.severity, lang)}、状態は {_localized_issue_status(issue.status, lang)} です。"
        f"{issue.title_ja or issue.title}"
    )


def _status_answer(
    lang: str,
    issue: Issue | None,
    affected: str,
    confidence: str,
    has_hazard: bool,
    evidence: list[dict[str, Any]],
) -> str:
    if normalize_lang(lang) == "en":
        prefix = (
            f"Issue #{issue.id} is {issue.status} with {issue.severity} severity. "
            if issue else ""
        )
        if has_hazard:
            return prefix + f"Live evidence still shows an active warning/error signal affecting {affected}."
        if evidence:
            return prefix + f"Live evidence is present, but it is informational or healthy for {affected}."
        return prefix + f"No live evidence was returned for {affected} in the selected window."
    prefix = (
        f"問題 #{issue.id} は {_localized_issue_status(issue.status, lang)} で、重大度は {_localized_severity(issue.severity, lang)} です。"
        if issue else ""
    )
    if has_hazard:
        return prefix + f"{affected} では、現在も警告またはエラーを示すライブ証拠が確認されています。"
    if evidence:
        return prefix + f"{affected} に関するライブ証拠は取得できていますが、現時点では正常値または参考情報に留まっています。"
    return prefix + f"{affected} については、選択した時間枠で新しいライブ証拠を確認できませんでした。"


def _service_answer(lang: str, issue: Issue | None, root: str, affected: str, confidence: str) -> str:
    if normalize_lang(lang) == "en":
        if confidence in {"high", "medium"}:
            return f"The affected service is {affected}. Live evidence points to {root} as the likely upstream contributor."
        return f"The affected service is {affected}. The live window does not strongly prove an upstream contributor."
    if confidence in {"high", "medium"}:
        return f"影響を受けているサービスは {affected} です。ライブ証拠からは、上流要因として {root} が有力です。"
    return f"影響を受けているサービスは {affected} ですが、選択したライブ時間枠だけでは上流要因を強く特定できていません。"


def _evidence_answer(lang: str, evidence: list[dict[str, Any]], confidence: str) -> str:
    if not evidence:
        return (
            "No live evidence was returned for this issue in the selected window."
            if normalize_lang(lang) == "en"
            else "選択した時間枠では、この問題に対するライブ証拠は返っていません。"
        )
    top = evidence[:3]
    snippets = []
    for item in top:
        label = item.get("query_name") or item.get("kind") or item.get("source") or "evidence"
        summary = item.get("summary") or ""
        value = item.get("value")
        piece = f"{label}: {summary}".strip(": ")
        if value is not None:
            piece = f"{piece} (value={value})"
        snippets.append(piece)
    joined = "; ".join(snippets)
    if normalize_lang(lang) == "en":
        return f"Live evidence ({confidence} confidence): {joined}"
    return f"ライブ証拠（確度: {_localized_confidence(confidence, lang)}）: {joined}"


def _assistant_name(lang: str) -> str:
    return "AIOPS" if normalize_lang(lang) == "en" else "AIOPSアシスタント"


def _next_action(lang: str, confidence: str) -> str:
    if normalize_lang(lang) == "en":
        if confidence == "low":
            return "Next: run the cascade/load scenario or pick a currently failing issue, then ask why again so MCP can capture warning/error evidence."
        return "Check the root service threshold and recent deploy/config changes, then re-run the MCP RCA after mitigation."
    if confidence == "low":
        return "次は、カスケード負荷シナリオを再現するか、現在発生中の問題を選び直してから、もう一度原因を確認してください。"
    return "上流サービスのしきい値、直近の設定変更、503 の発生状況を確認し、対処後に MCP RCA を再実行してください。"


def _t(lang: str, key: str, **kwargs) -> str:
    en = normalize_lang(lang) == "en"
    table = {
        "stored": ("Checking issue context...", "問題コンテキストを確認しています..."),
        "mcp": ("Calling MCP observability tools...", "MCP 可観測性ツールを呼び出しています..."),
        "evidence": (f"Found {kwargs.get('count', 0)} evidence item(s).", f"証拠を{kwargs.get('count', 0)}件確認しました。"),
        "mcp_failed": (f"MCP live RCA is unavailable: {kwargs.get('error')}", f"MCP ライブ RCA を利用できません: {kwargs.get('error')}"),
        "fallback": ("Use stored RCA while MCP is unavailable.", "MCP が復旧するまでは保存済み RCA を利用してください。"),
    }
    left, right = table[key]
    return left if en else right


def _localized_app_name(app_name: str | None, lang: str) -> str:
    if normalize_lang(lang) == "ja":
        return app_display_name_ja(app_name)
    return (app_name or "service").strip() or "service"


def _localized_issue_status(value: str | None, lang: str) -> str:
    text = str(value or "").strip()
    if normalize_lang(lang) != "ja":
        return text or "unknown"
    return {
        "OPEN": "対応中",
        "ESCALATED": "エスカレーション済み",
        "RESOLVED": "解決済み",
    }.get(text.upper(), text or "不明")


def _localized_severity(value: str | None, lang: str) -> str:
    text = str(value or "").strip()
    if normalize_lang(lang) != "ja":
        return text or "unknown"
    return {
        "critical": "重大",
        "high": "高",
        "medium": "中",
        "low": "低",
    }.get(text.lower(), text or "不明")


def _localized_confidence(value: str | None, lang: str) -> str:
    text = str(value or "").strip()
    if normalize_lang(lang) != "ja":
        return text or "unknown"
    return {
        "high": "高",
        "medium": "中",
        "low": "低",
        "unknown": "不明",
    }.get(text.lower(), text or "不明")
