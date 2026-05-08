"""
Issue lifecycle management API covering create, list, get, update, and state
transitions (acknowledge, escalate, resolve) for detected issues. Deduplicates
by fingerprint hash, supports bilingual title and description fields, and provides
seed endpoints for demo and MCP cross-service topology scenarios.

検出イシューの作成・一覧・取得・更新・状態遷移（承認・エスカレーション・解決）を
管理するイシューライフサイクルAPIモジュール。フィンガープリントハッシュによる重複排除、
英日両言語タイトル・説明フィールドのサポート、デモおよびMCPクロスサービス
トポロジーシナリオ向けシードエンドポイントを提供する。
"""
import json
import hashlib
from datetime import datetime, timedelta
from typing import Optional
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc
import logging

from server.config import settings
from server.database.engine import get_db
from server.database.models import Issue, IssueAnalysis
from server.engine import rca_client
from server.engine.bilingual import (
    app_display_name_ja,
    issue_description_ja,
    issue_title_ja,
    normalize_lang,
)


logger = logging.getLogger("aiops.issues")

router = APIRouter(prefix="/issues", tags=["issues"])

VALID_STATUSES = {"OPEN", "ACKNOWLEDGED", "ESCALATED", "RESOLVED"}
VALID_SEVERITIES = {"low", "medium", "high", "critical"}

# Display mapping: internal severity → SEV label
SEV_LABEL = {"critical": "SEV1", "high": "SEV2", "medium": "SEV3", "low": "SEV4"}


class IssueCreate(BaseModel):
    app_name: str
    issue_type: str
    severity: str
    title: str
    description: Optional[str] = None
    title_en: Optional[str] = None
    title_ja: Optional[str] = None
    description_en: Optional[str] = None
    description_ja: Optional[str] = None
    span_name: Optional[str] = None
    trace_id: Optional[str] = None
    metadata: Optional[dict] = None


class IssueUpdate(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    description: Optional[str] = None


@router.get("")
def list_issues(
    app_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    lang: str = Query("ja", pattern="^(ja|en)$"),
    db: Session = Depends(get_db),
):
    # 一覧 API は軽量なフィルタだけを受け、表示用整形は最後にまとめて行う。
    logger.info("inside issues")
    q = db.query(Issue)
    if app_name:
        q = q.filter(Issue.app_name == app_name)
    if status:
        q = q.filter(Issue.status == status)
    if severity:
        q = q.filter(Issue.severity == severity)
    total = q.count()
    issues = q.order_by(desc(Issue.created_at)).offset(offset).limit(limit).all()
    _hydrate_remediation_metadata(db, issues)
    return {"total": total, "lang": normalize_lang(lang), "issues": [_issue_dict(i, lang=lang) for i in issues]}


@router.post("", status_code=201)
def create_issue(
    payload: IssueCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if payload.severity not in VALID_SEVERITIES:
        raise HTTPException(400, f"severity must be one of {VALID_SEVERITIES}")
    import hashlib
    from datetime import datetime
    fp_key = f"{payload.app_name}:{payload.issue_type}:{payload.span_name or ''}"
    base_fp = hashlib.sha256(fp_key.encode()).hexdigest()[:16]

    # 同系統の未解決 Issue があれば新規作成せず既存を返す。
    open_existing = (
        db.query(Issue)
        .filter(Issue.base_fingerprint == base_fp, Issue.status != "RESOLVED")
        .first()
    )
    if open_existing:
        return {"id": open_existing.id, "created": False, "message": "Duplicate open issue"}

    # 解決済みの直近 Issue とつなぎ、再発回数を追跡できるようにする。
    prior = (
        db.query(Issue)
        .filter(Issue.base_fingerprint == base_fp, Issue.status == "RESOLVED")
        .order_by(Issue.id.desc())
        .first()
    )
    recurrence_count = (prior.recurrence_count + 1) if prior else 0
    occurrence_fp = hashlib.sha256(
        f"{base_fp}:{recurrence_count}".encode()
    ).hexdigest()[:16]

    issue = Issue(
        app_name=payload.app_name,
        issue_type=payload.issue_type,
        severity=payload.severity,
        title=payload.title,
        description=payload.description,
        title_en=payload.title_en or payload.title,
        title_ja=payload.title_ja or issue_title_ja(
            payload.title, app_name=payload.app_name
        ),
        description_en=payload.description_en or payload.description,
        description_ja=payload.description_ja or issue_description_ja(
            payload.description,
            app_name=payload.app_name,
        ),
        span_name=payload.span_name,
        trace_id=payload.trace_id,
        fingerprint=occurrence_fp,
        base_fingerprint=base_fp,
        previous_issue_id=prior.id if prior else None,
        recurrence_count=recurrence_count,
        metadata_json=json.dumps(payload.metadata) if payload.metadata else None,
    )
    db.add(issue)
    db.commit()
    db.refresh(issue)
    # RCA は非同期で走らせ、Issue 作成 API 自体はすぐ返す。
    background_tasks.add_task(rca_client.request_rca, issue.id)
    return {"id": issue.id, "created": True}


@router.post("/seed/demo")
def seed_demo_issues(db: Session = Depends(get_db)):
    """Seed realistic open issues for exercising the RCA chat interface."""
    cases = [
        {
            "key": "oom-sample-agent",
            "app_name": "sample-agent",
            "issue_type": "container_oom_kill",
            "rule_id": "NFR-33",
            "severity": "critical",
            "title": "Sample agent container killed by memory pressure",
            "description": "The sample-agent container restarted after memory usage crossed its limit. Dependent triage calls saw 503 responses during the restart window.",
            "cause": "Container memory allocation is too low for the current request burst, with possible cache growth increasing the working set.",
            "evidence": "Prometheus container memory working set approached the configured limit.\nDependent triage-agent spans failed on upstream sample-agent calls.\nContainer restart count increased in the same incident window.",
            "action": "Increase memory headroom or replicas first, then inspect cache/batch growth and validate under the same load profile.",
        },
        {
            "key": "disk-full-medical-agent",
            "app_name": "medical-agent",
            "issue_type": "disk_full",
            "rule_id": "NFR-41",
            "severity": "critical",
            "title": "Medical agent log volume is almost full",
            "description": "The medical-agent host volume has less than 5 percent free space and write operations are intermittently failing.",
            "cause": "Log retention is not rotating quickly enough for the current request volume.",
            "evidence": "Filesystem usage crossed the critical threshold.\nApplication logs show write failures with no space left on device.\nLog directory growth accelerated after debug logging was enabled.",
            "action": "Free or expand storage, restore log rotation, then verify disk and inode headroom remains below alert thresholds.",
        },
        {
            "key": "db-pool-triage",
            "app_name": "triage-agent",
            "issue_type": "database_connection_pool_exhaustion",
            "rule_id": "NFR-22",
            "severity": "high",
            "title": "Triage agent database connection pool exhausted",
            "description": "Requests are timing out while waiting for database connections during concurrent RCA lookups.",
            "cause": "The app pool size and timeout are not aligned with current concurrency, and some sessions appear long-lived.",
            "evidence": "Connection wait time increased before request timeouts.\nDatabase active connections stayed close to max.\nTraces show long spans around incident lookup queries.",
            "action": "Tune pool size/timeouts within DB limits and close leaked or long-held sessions before increasing concurrency further.",
        },
        {
            "key": "dns-langfuse",
            "app_name": "observability-gateway",
            "issue_type": "dns_resolution_failure",
            "rule_id": "NFR-18",
            "severity": "high",
            "title": "Langfuse endpoint DNS resolution is failing",
            "description": "The observability gateway cannot resolve the Langfuse hostname, so traces are buffered and then dropped.",
            "cause": "DNS resolver or service discovery path is unstable for the Langfuse endpoint.",
            "evidence": "Trace export logs show getaddrinfo temporary failure.\nPrometheus export failure counter increased.\nPrometheus metrics still scrape locally, isolating the issue to the Langfuse route.",
            "action": "Restore DNS resolution from the affected container and validate trace export succeeds without buffering.",
        },
        {
            "key": "tls-expiry-prometheus",
            "app_name": "prometheus-bridge",
            "issue_type": "tls_certificate_expiry",
            "rule_id": "NFR-19",
            "severity": "critical",
            "title": "Prometheus bridge certificate expired",
            "description": "TLS handshakes to the metrics bridge are failing after a certificate expiry.",
            "cause": "The mounted certificate expired and the service did not reload a renewed secret.",
            "evidence": "Client errors include x509 certificate has expired.\nMetrics scrape success dropped to zero after the expiry timestamp.\nNo matching application deployment occurred in the same window.",
            "action": "Rotate the certificate or secret, restart/reload the bridge if required, and add expiry monitoring.",
        },
        {
            "key": "deploy-regression-chat",
            "app_name": "joshu-chat",
            "issue_type": "deployment_regression",
            "rule_id": "NFR-8a",
            "severity": "high",
            "title": "Chat interface started failing after latest deployment",
            "description": "Users report blank responses after the latest frontend deployment.",
            "cause": "A JavaScript change introduced a missing handler path for implementation results.",
            "evidence": "Browser console shows handler is not defined.\nFailures began immediately after the deployment timestamp.\nBackend status endpoints continue to return healthy responses.",
            "action": "Rollback if impact is active, then patch the handler and add a UI smoke check for result buttons.",
        },
        {
            "key": "queue-backlog-rca",
            "app_name": "rca-worker",
            "issue_type": "queue_backlog",
            "rule_id": "NFR-26",
            "severity": "high",
            "title": "RCA worker queue backlog is growing",
            "description": "RCA jobs are waiting longer than the SLO because worker throughput is below arrival rate.",
            "cause": "Worker capacity is saturated and downstream LLM calls are increasing job duration.",
            "evidence": "Oldest job age crossed the alert threshold.\nWorker CPU remains high while queue depth increases.\nLLM latency rose during the same time window.",
            "action": "Scale workers, protect downstream limits, and validate backlog drain rate returns to normal.",
        },
        {
            "key": "cache-stampede",
            "app_name": "medical-search-api",
            "issue_type": "cache_stampede",
            "rule_id": "NFR-14",
            "severity": "medium",
            "title": "Medical search API latency spike from cache misses",
            "description": "Search latency increased sharply when many cached keys expired at the same time.",
            "cause": "Synchronized TTL expiry caused a cache stampede and overloaded the origin search dependency.",
            "evidence": "Cache hit rate dropped while origin request rate spiked.\nP95 latency increased only on cacheable search routes.\nRedis remained reachable but miss rate was abnormal.",
            "action": "Warm critical keys, add TTL jitter/request coalescing, and verify origin load returns to baseline.",
        },
        {
            "key": "autoscale-not-triggered",
            "app_name": "sample-agent",
            "issue_type": "autoscaling_failure",
            "rule_id": "NFR-33",
            "severity": "high",
            "title": "Sample agent did not scale during CPU pressure",
            "description": "CPU stayed above threshold but no additional capacity was added before availability degraded.",
            "cause": "Autoscaling policy is using the wrong metric or replica limits are preventing scale-out.",
            "evidence": "CPU stayed above threshold for the alert window.\nReplica count remained unchanged.\nDependent service saw upstream 503 responses.",
            "action": "Review scaling metrics, min/max replicas and cooldowns; increase capacity immediately if impact is active.",
        },
        {
            "key": "storage-io-saturation",
            "app_name": "trace-store",
            "issue_type": "storage_io_saturation",
            "rule_id": "NFR-42",
            "severity": "high",
            "title": "Trace store write latency is saturated",
            "description": "Trace ingestion is delayed because storage write latency and IOPS utilization are high.",
            "cause": "The trace store is I/O bound during burst ingestion.",
            "evidence": "Write latency increased with IOPS utilization.\nTrace ingestion queue depth increased.\nCPU remained below saturation, pointing away from app CPU pressure.",
            "action": "Increase storage IOPS/throughput or reduce write amplification, then verify ingestion latency recovers.",
        },
        {
            "key": "thread-pool-exhausted",
            "app_name": "gateway-api",
            "issue_type": "worker_pool_exhaustion",
            "rule_id": "NFR-11",
            "severity": "medium",
            "title": "Gateway worker pool is exhausted",
            "description": "Requests are queued because all workers are busy during long upstream waits.",
            "cause": "Worker pool size is too small for the current blocking upstream call pattern.",
            "evidence": "In-flight requests stayed at worker limit.\nQueue wait increased before response latency.\nUpstream spans show long blocking calls.",
            "action": "Tune worker limits and remove or isolate blocking calls from the request path.",
        },
        {
            "key": "llm-rate-limit",
            "app_name": "rca-assistant",
            "issue_type": "llm_rate_limit",
            "rule_id": "NFR-51",
            "severity": "high",
            "title": "RCA assistant is hitting LLM rate limits",
            "description": "RCA generation is failing intermittently with provider 429 responses.",
            "cause": "The RCA assistant is exceeding provider request or token throughput limits.",
            "evidence": "LLM spans show 429 rate limit errors.\nRetry attempts increase total latency.\nQueue time rises during concurrent RCA generation.",
            "action": "Add request shaping/backoff and adjust quota or concurrency limits before increasing retries.",
        },
    ]
    created = 0
    refreshed = 0
    ids: list[int] = []
    for idx, case in enumerate(cases):
        base_fp = hashlib.sha256(f"demo-open-issue:{case['key']}".encode()).hexdigest()[:16]
        issue = db.query(Issue).filter(Issue.base_fingerprint == base_fp).first()
        if issue:
            refreshed += 1
        else:
            issue = Issue(
                fingerprint=base_fp,
                base_fingerprint=base_fp,
                recurrence_count=0,
            )
            db.add(issue)
            created += 1
        issue.app_name = case["app_name"]
        issue.issue_type = case["issue_type"]
        issue.rule_id = case["rule_id"]
        issue.severity = case["severity"]
        issue.status = "OPEN" if idx % 4 else "ESCALATED"
        issue.title = case["title"]
        issue.description = case["description"]
        issue.title_en = case["title"]
        issue.title_ja = issue_title_ja(case["title"], app_name=case["app_name"])
        issue.description_en = case["description"]
        issue.description_ja = issue_description_ja(case["description"], app_name=case["app_name"])
        issue.span_name = f"demo.{case['issue_type']}"
        issue.trace_id = f"demo-{case['key']}"
        issue.updated_at = datetime.utcnow()
        issue.resolved_at = None
        issue.metadata_json = json.dumps({"seed": "demo-open-issues", "seed_key": case["key"]})
        db.flush()

        analysis = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue.id).first()
        if not analysis:
            analysis = IssueAnalysis(issue_id=issue.id)
            db.add(analysis)
        analysis.status = "done"
        analysis.model_used = "seeded-rca-knowledge"
        analysis.likely_cause = case["cause"]
        analysis.evidence = case["evidence"]
        analysis.recommended_action = case["action"]
        analysis.remediation_type = "infra_change"
        analysis.likely_cause_en = case["cause"]
        analysis.evidence_en = case["evidence"]
        analysis.recommended_action_en = case["action"]
        analysis.likely_cause_ja = issue_description_ja(case["cause"], app_name=case["app_name"])
        analysis.evidence_ja = issue_description_ja(case["evidence"], app_name=case["app_name"])
        analysis.recommended_action_ja = issue_description_ja(case["action"], app_name=case["app_name"])
        analysis.language_status = "ready"
        analysis.generated_at = datetime.utcnow()
        ids.append(issue.id)
    db.commit()
    return {"created": created, "refreshed": refreshed, "total": len(cases), "issue_ids": ids}


def _upsert_seed_issue(
    db: Session,
    *,
    namespace: str,
    key: str,
    app_name: str,
    issue_type: str,
    rule_id: str,
    severity: str,
    status: str,
    title_en: str,
    title_ja: str,
    description_en: str,
    description_ja: str,
    span_name: str,
    trace_id: str,
    created_at: datetime,
) -> tuple[Issue, bool]:
    # デモデータは固定 fingerprint を使い、再投入時は idempotent に更新する。
    base_fp = hashlib.sha256(f"{namespace}:{key}".encode()).hexdigest()[:16]
    issue = db.query(Issue).filter(Issue.base_fingerprint == base_fp).first()
    created = False
    if issue is None:
        issue = Issue(
            fingerprint=base_fp,
            base_fingerprint=base_fp,
            recurrence_count=0,
        )
        db.add(issue)
        created = True
    issue.app_name = app_name
    issue.issue_type = issue_type
    issue.rule_id = rule_id
    issue.severity = severity
    issue.status = status
    issue.title = title_en
    issue.description = description_en
    issue.title_en = title_en
    issue.title_ja = title_ja
    issue.description_en = description_en
    issue.description_ja = description_ja
    issue.span_name = span_name
    issue.trace_id = trace_id
    issue.created_at = created_at
    issue.updated_at = datetime.utcnow()
    issue.resolved_at = None
    db.flush()
    return issue, created


def _upsert_seed_analysis(
    db: Session,
    *,
    issue_id: int,
    model_used: str,
    remediation_type: str,
    likely_cause_en: str,
    likely_cause_ja: str,
    evidence_en: str,
    evidence_ja: str,
    action_en: str,
    action_ja: str,
) -> None:
    # seeded issue と対になる RCA 結果も同じ issue_id に上書きする。
    analysis = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue_id).first()
    if analysis is None:
        analysis = IssueAnalysis(issue_id=issue_id)
        db.add(analysis)
    analysis.status = "done"
    analysis.model_used = model_used
    analysis.likely_cause = likely_cause_en
    analysis.evidence = evidence_en
    analysis.recommended_action = action_en
    analysis.remediation_type = remediation_type
    analysis.likely_cause_en = likely_cause_en
    analysis.likely_cause_ja = likely_cause_ja
    analysis.evidence_en = evidence_en
    analysis.evidence_ja = evidence_ja
    analysis.recommended_action_en = action_en
    analysis.recommended_action_ja = action_ja
    analysis.language_status = "ready"
    analysis.generated_at = datetime.utcnow()


@router.post("/seed/mcp-demo")
def seed_mcp_demo_issues(db: Session = Depends(get_db)):
    """Seed a clear cross-service MCP demo across sample-agent and triage-agent."""
    now = datetime.utcnow().replace(microsecond=0)
    namespace = "mcp-cross-service-demo"
    root_created_at = now - timedelta(minutes=4)
    impact_created_at = now - timedelta(minutes=3, seconds=15)

    root_issue, root_created = _upsert_seed_issue(
        db,
        namespace=namespace,
        key="sample-agent-root",
        app_name="sample-agent",
        issue_type="cross_service_upstream_latency",
        rule_id="MCP-DEMO-ROOT",
        severity="critical",
        status="ESCALATED",
        title_en="Sample-agent retrieval latency is cascading into triage-agent failures",
        title_ja="sample-agent の検索遅延が triage-agent の障害に波及しています",
        description_en=(
            "The sample-agent retrieval path slowed sharply, and dependent triage-agent requests started timing out "
            "while waiting on upstream search and answer generation."
        ),
        description_ja=(
            "sample-agent の検索処理が急激に遅くなり、依存している triage-agent のリクエストが "
            "上流の検索・回答生成待ちでタイムアウトし始めています。"
        ),
        span_name="demo.mcp.sample_agent.retrieval",
        trace_id="mcp-demo-sample-root",
        created_at=root_created_at,
    )
    impact_issue, impact_created = _upsert_seed_issue(
        db,
        namespace=namespace,
        key="triage-agent-impact",
        app_name="triage-agent",
        issue_type="upstream_dependency_timeout",
        rule_id="MCP-DEMO-IMPACT",
        severity="high",
        status="OPEN",
        title_en="Triage-agent is timing out on sample-agent upstream calls",
        title_ja="triage-agent が sample-agent への上流呼び出しでタイムアウトしています",
        description_en=(
            "Triage-agent requests are failing because calls to sample-agent exceed the upstream timeout budget "
            "and return 503/504-style failures."
        ),
        description_ja=(
            "triage-agent のリクエストは、sample-agent への呼び出しが上流タイムアウトの許容時間を超え、"
            "503 / 504 系の失敗として返るため処理に失敗しています。"
        ),
        span_name="demo.mcp.triage_agent.upstream",
        trace_id="mcp-demo-triage-impact",
        created_at=impact_created_at,
    )

    root_topology = {
        "title": "Cross-service incident path",
        "description": "sample-agent degradation is propagating to triage-agent through the live service path.",
        "timestamp": root_created_at.isoformat(),
        "propagation_label": "sample-agent -> triage-agent",
        "impact": {
            "where": "sample-agent -> triage-agent",
            "user_count": 24,
            "applications": ["sample-agent", "triage-agent"],
            "application_count": 2,
        },
        "root_cause_chain": (
            f"sample-agent retrieval latency spike (issue #{root_issue.id}) -> "
            "upstream wait expansion -> triage-agent timeout symptoms"
        ),
        "recommended_action": (
            "Stabilize sample-agent retrieval first, inspect PGVector latency and queue depth, "
            "then confirm triage-agent upstream timeouts return to baseline."
        ),
        "nodes": [
            {
                "id": "vm-host",
                "zone": "platform",
                "name": "VM Host",
                "status": "ok",
                "role": "Compute, OS scheduler, and network stack",
                "metrics": [{"label": "CPU", "value": "58%"}],
                "logs": [
                    {
                        "timestamp": root_created_at.isoformat(),
                        "level": "INFO",
                        "message": "Host capacity remains stable; contention is localized to the application path.",
                    }
                ],
            },
            {
                "id": "docker-runtime",
                "zone": "runtime",
                "name": "Docker Runtime",
                "status": "warn",
                "role": "Container runtime and service isolation",
                "metrics": [{"label": "Container CPU", "value": "76%"}],
                "logs": [
                    {
                        "timestamp": root_created_at.isoformat(),
                        "level": "WARN",
                        "message": "Container runtime shows pressure from rising upstream retries.",
                    }
                ],
            },
            {
                "id": "sample-agent",
                "zone": "runtime",
                "name": "sample-agent",
                "service_name": "sample-agent",
                "status": "error",
                "role": "Primary retrieval and answer generation service",
                "aliases": ["sample-agent", "retrieval", "search"],
                "layout": {"x": 65, "y": 24},
                "metrics": [
                    {"label": "Upstream p99", "value": "3.8s"},
                    {"label": "5xx rate", "value": "18%"},
                    {"label": "Queue depth", "value": "41"},
                ],
                "logs": [
                    {
                        "timestamp": root_created_at.isoformat(),
                        "level": "ERROR",
                        "message": "sample-agent p99 retrieval latency climbed above 3.8s during live traffic.",
                    },
                    {
                        "timestamp": (root_created_at + timedelta(seconds=25)).isoformat(),
                        "level": "ERROR",
                        "message": "Upstream retrieval queue depth exceeded the safe window and 5xx rate increased.",
                    },
                ],
            },
            {
                "id": "triage-agent",
                "zone": "runtime",
                "name": "triage-agent",
                "service_name": "triage-agent",
                "status": "warn",
                "role": "Dependent triage workflow calling sample-agent",
                "aliases": ["triage-agent", "triage", "caller"],
                "layout": {"x": 65, "y": 58},
                "metrics": [
                    {"label": "Timeout rate", "value": "14%"},
                    {"label": "Failed requests", "value": "12"},
                ],
                "logs": [
                    {
                        "timestamp": impact_created_at.isoformat(),
                        "level": "WARN",
                        "message": "triage-agent observed growing upstream wait time against sample-agent.",
                    }
                ],
            },
            {
                "id": "prometheus",
                "zone": "observability",
                "name": "Prometheus",
                "status": "ok",
                "role": "Metrics scraping and alert evaluation",
                "metrics": [{"label": "Alert", "value": "SampleAgentLatencyHigh"}],
                "logs": [
                    {
                        "timestamp": root_created_at.isoformat(),
                        "level": "INFO",
                        "message": "Prometheus captured sample-agent latency and triage-agent timeout counters in the same window.",
                    }
                ],
            },
            {
                "id": "langfuse",
                "zone": "observability",
                "name": "Langfuse",
                "status": "ok",
                "role": "Trace capture and LLM observability",
                "logs": [
                    {
                        "timestamp": impact_created_at.isoformat(),
                        "level": "INFO",
                        "message": "Langfuse traces show triage-agent waiting on sample-agent before failure.",
                    }
                ],
            },
            {
                "id": "pgvector",
                "zone": "observability",
                "name": "PGVector DB",
                "status": "warn",
                "role": "Vector store / retrieval dependency",
                "metrics": [{"label": "Latency", "value": "420ms"}],
                "logs": [
                    {
                        "timestamp": root_created_at.isoformat(),
                        "level": "WARN",
                        "message": "PGVector query latency increased during the sample-agent incident window.",
                    }
                ],
            },
        ],
        "metrics": [
            {"label": "Issue", "value": f"#{root_issue.id}", "tone": "critical"},
            {"label": "Severity", "value": "CRITICAL", "tone": "critical"},
            {"label": "Status", "value": "ESCALATED", "tone": "warning"},
        ],
        "alerts": [
            {"name": "SampleAgentLatencyHigh", "severity": "critical", "tone": "critical"},
            {"name": "TriageAgentUpstreamTimeout", "severity": "warning", "tone": "warning"},
        ],
        "traces": [
            {"timestamp": root_created_at.isoformat(), "status": "fail", "label": "sample-agent"},
            {"timestamp": impact_created_at.isoformat(), "status": "slow", "label": "triage-agent"},
        ],
    }
    impact_topology = {
        **root_topology,
        "timestamp": impact_created_at.isoformat(),
        "root_cause_chain": (
            f"sample-agent retrieval slowdown (issue #{root_issue.id}) is the upstream contributor; "
            f"triage-agent timeout symptoms are tracked in issue #{impact_issue.id}."
        ),
        "recommended_action": (
            "Check sample-agent first, validate upstream latency recovery, then confirm triage-agent timeout counters and "
            "user-visible failures fall back to normal."
        ),
        "correlated_from_issue": root_issue.id,
    }
    impact_topology["nodes"] = [dict(node) for node in root_topology["nodes"]]
    for node in impact_topology["nodes"]:
        if node.get("id") == "sample-agent":
            node["status"] = "error"
            node["error_message"] = f"Upstream source for triage-agent issue #{impact_issue.id}."
        elif node.get("id") == "triage-agent":
            node["status"] = "error"
            node["error_message"] = "Visible customer impact from upstream sample-agent degradation."
            node["logs"] = [
                {
                    "timestamp": impact_created_at.isoformat(),
                    "level": "ERROR",
                    "message": "triage-agent timed out while waiting for sample-agent and returned a failed response.",
                },
                {
                    "timestamp": (impact_created_at + timedelta(seconds=20)).isoformat(),
                    "level": "ERROR",
                    "message": "Retries against sample-agent did not complete within the upstream timeout budget.",
                },
            ]
    root_meta = {
        "seed": namespace,
        "seed_key": "sample-agent-root",
        "display_app_name": "sample-agent",
        "demo_kind": "mcp_cross_service",
        "correlation": {
            "role": "root",
            "confidence": "high",
            "impacted_issue_ids": [impact_issue.id],
            "reason": "sample-agent degradation aligns with triage-agent timeout symptoms in the same window",
            "correlated_at": now.isoformat(),
        },
        "topology": root_topology,
    }
    impact_meta = {
        "seed": namespace,
        "seed_key": "triage-agent-impact",
        "display_app_name": "triage-agent",
        "demo_kind": "mcp_cross_service",
        "correlation": {
            "role": "impact",
            "root_issue_id": root_issue.id,
            "confidence": "high",
            "score": 98,
            "reason": "triage-agent timeout symptoms align with sample-agent latency and 5xx spikes in the same live window",
            "correlated_at": now.isoformat(),
        },
        "topology": impact_topology,
    }
    root_issue.metadata_json = json.dumps(root_meta, ensure_ascii=False)
    impact_issue.metadata_json = json.dumps(impact_meta, ensure_ascii=False)

    _upsert_seed_analysis(
        db,
        issue_id=root_issue.id,
        model_used="seeded-mcp-cross-service-demo",
        remediation_type="config_change",
        likely_cause_en=(
            "sample-agent is the upstream source of the cascade. Retrieval latency and 5xx spikes expanded inside "
            "sample-agent first, and triage-agent started failing only after those signals appeared."
        ),
        likely_cause_ja=(
            "今回の波及障害の起点は sample-agent です。まず sample-agent 内で検索遅延と 5xx の増加が発生し、"
            "その後に triage-agent の失敗が表面化しました。"
        ),
        evidence_en=(
            "Prometheus shows sample-agent latency and 5xx growth before triage-agent timeout counters increase.\n"
            "Langfuse traces place triage-agent waits behind sample-agent spans.\n"
            f"Topology view marks issue #{root_issue.id} as the upstream root affecting issue #{impact_issue.id}."
        ),
        evidence_ja=(
            "Prometheus では、triage-agent のタイムアウト増加より前に sample-agent の遅延と 5xx 増加が確認できます。\n"
            "Langfuse トレースでも、triage-agent の待ち時間は sample-agent のスパンの後ろに現れています。\n"
            f"トポロジービューでも、問題 #{root_issue.id} が問題 #{impact_issue.id} に影響する上流起点として示されています。"
        ),
        action_en=(
            "Reduce sample-agent retrieval latency first, inspect PGVector and queue saturation, and then verify triage-agent "
            "timeouts stop without changing the triage workflow itself."
        ),
        action_ja=(
            "まず sample-agent の検索遅延を解消し、PGVector とキューの飽和を確認したうえで、"
            "triage-agent 側の処理を変えずにタイムアウトが止まることを確認してください。"
        ),
    )
    _upsert_seed_analysis(
        db,
        issue_id=impact_issue.id,
        model_used="seeded-mcp-cross-service-demo",
        remediation_type="investigation_only",
        likely_cause_en=(
            "The triage-agent timeout is downstream impact, not the primary fault. Current topology and timing point to "
            "sample-agent as the upstream contributor."
        ),
        likely_cause_ja=(
            "triage-agent のタイムアウトは主障害ではなく下流影響です。現在のトポロジーと発生時刻の並びから、"
            "上流要因は sample-agent だと判断できます。"
        ),
        evidence_en=(
            "Triage-agent failures start after sample-agent latency and 5xx metrics move.\n"
            "Topology shows a direct sample-agent -> triage-agent dependency path.\n"
            "Langfuse traces show triage-agent waiting on sample-agent before failing."
        ),
        evidence_ja=(
            "triage-agent の失敗は、sample-agent の遅延と 5xx 指標が悪化した後に始まっています。\n"
            "トポロジーでも sample-agent -> triage-agent の直接依存経路が確認できます。\n"
            "Langfuse トレースでも、失敗前に triage-agent が sample-agent を待っていることが分かります。"
        ),
        action_en=(
            "Use MCP against sample-agent as the root candidate and triage-agent as the affected service, then verify "
            "live evidence before making changes in triage-agent."
        ),
        action_ja=(
            "MCP では root candidate を sample-agent、affected service を triage-agent として確認し、"
            "triage-agent を変更する前にライブ証拠で上流影響を確かめてください。"
        ),
    )

    db.commit()
    return {
        "created": int(root_created) + int(impact_created),
        "refreshed": 2 - int(root_created) - int(impact_created),
        "total": 2,
        "issue_ids": [root_issue.id, impact_issue.id],
        "root_issue_id": root_issue.id,
        "impact_issue_id": impact_issue.id,
        "recommended_issue_id": impact_issue.id,
        "demo": {
            "root_service": "sample-agent",
            "affected_service": "triage-agent",
            "path": "sample-agent -> triage-agent",
        },
    }


@router.get("/{issue_id}")
def get_issue(
    issue_id: int,
    lang: str = Query("ja", pattern="^(ja|en)$"),
    db: Session = Depends(get_db),
):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(404, "Issue not found")
    _hydrate_remediation_metadata(db, [issue])
    return _issue_dict(issue, lang=lang)


@router.patch("/{issue_id}")
def update_issue(issue_id: int, payload: IssueUpdate, db: Session = Depends(get_db)):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(404, "Issue not found")
    if payload.status:
        if payload.status not in VALID_STATUSES:
            raise HTTPException(400, f"status must be one of {VALID_STATUSES}")
        if payload.status == "ACKNOWLEDGED" and not issue.acknowledged_at:
            issue.acknowledged_at = datetime.utcnow()
        if payload.status == "RESOLVED" and not issue.resolved_at:
            issue.resolved_at = datetime.utcnow()
        issue.status = payload.status
    if payload.severity:
        if payload.severity not in VALID_SEVERITIES:
            raise HTTPException(400, f"severity must be one of {VALID_SEVERITIES}")
        issue.severity = payload.severity
    if payload.description is not None:
        issue.description = payload.description
    issue.updated_at = datetime.utcnow()
    db.commit()
    return _issue_dict(issue)


@router.post("/{issue_id}/acknowledge")
def acknowledge_issue(issue_id: int, db: Session = Depends(get_db)):
    return _transition(issue_id, "ACKNOWLEDGED", db)


@router.post("/{issue_id}/escalate")
def escalate_issue(issue_id: int, db: Session = Depends(get_db)):
    return _transition(issue_id, "ESCALATED", db)


@router.post("/{issue_id}/resolve")
def resolve_issue(issue_id: int, db: Session = Depends(get_db)):
    return _transition(issue_id, "RESOLVED", db)


def _transition(issue_id: int, new_status: str, db: Session):
    issue = db.query(Issue).filter(Issue.id == issue_id).first()
    if not issue:
        raise HTTPException(404, "Issue not found")
    if new_status == "RESOLVED":
        _hydrate_remediation_metadata(db, [issue], force=True)
    issue.status = new_status
    issue.updated_at = datetime.utcnow()
    if new_status == "ACKNOWLEDGED":
        issue.acknowledged_at = datetime.utcnow()
    elif new_status == "ESCALATED":
        issue.escalation_count += 1
    elif new_status == "RESOLVED":
        issue.resolved_at = datetime.utcnow()
    db.commit()
    return _issue_dict(issue)


def _read_issue_meta(issue: Issue) -> dict:
    try:
        return json.loads(issue.metadata_json) if issue.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _remediation_meta_from_status(data: dict, run_id: str) -> dict:
    status = data.get("status")
    if not status:
        return {}
    meta = {
        "remediation_run_id": run_id,
        "remediation_status": status,
    }
    for key in ("pr_url", "pr_number", "job_phase", "job_error", "current_screen"):
        if key in data:
            meta[f"remediation_{key}"] = data.get(key)
    return meta


def _hydrate_remediation_metadata(db: Session, issues: list[Issue], force: bool = False) -> None:
    """Recover remediation status from AIOPS when telemetry metadata is stale.

    The dashboard relies on issue.metadata_json for its button state. Demo flows
    can bypass the telemetry proxy fallback path, so this lightweight repair keeps
    the board truthful without changing the existing frontend flow.
    """
    changed = False
    base_url = settings.AIOPS_REMEDIATION_URL.rstrip("/")
    if not base_url:
        return
    with httpx.Client(timeout=0.8) as client:
        for issue in issues:
            meta = _read_issue_meta(issue)
            current = meta.get("remediation_status")
            if not force and current == "PR_CREATED":
                continue
            run_id = str(meta.get("remediation_run_id") or f"AIOPS-{issue.id}")
            try:
                response = client.get(f"{base_url}/api/issues/{run_id}/status")
            except httpx.HTTPError:
                continue
            if response.status_code == 404:
                continue
            if not response.is_success:
                continue
            updates = _remediation_meta_from_status(response.json(), run_id)
            if not updates:
                continue
            meta.update(updates)
            issue.metadata_json = json.dumps(meta)
            changed = True
    if changed:
        db.commit()


def _issue_dict(i: Issue, lang: str = "ja") -> dict:
    # Parse metadata_json so the dashboard gets a live object (not a raw string).
    # This carries remediation_run_id / remediation_status written by the proxy.
    meta = _read_issue_meta(i)
    lang = normalize_lang(lang)
    app_name_ja = app_display_name_ja(i.app_name)
    app_display_name = app_name_ja if lang == "ja" else i.app_name
    title_en = i.title_en or i.title
    stored_title_ja = i.title_ja or ""
    needs_title_ja = (
        not stored_title_ja
        or "NFR-" in stored_title_ja
        or i.app_name in stored_title_ja
        or stored_title_ja == title_en
        or _looks_english(stored_title_ja)
    )
    title_ja = (
        issue_title_ja(
            i.title,
            app_name=i.app_name,
            rule_id=i.rule_id,
            allow_live_translate=False,
        )
        if needs_title_ja
        else stored_title_ja
    )
    description_en = i.description_en or i.description
    stored_description_ja = i.description_ja or ""
    needs_description_ja = (
        not stored_description_ja
        or "application is not reachable" in stored_description_ja.lower()
        or stored_description_ja == description_en
        or _looks_english(stored_description_ja)
    )
    description_ja = (
        issue_description_ja(
            i.description,
            app_name=i.app_name,
            rule_id=i.rule_id,
            allow_live_translate=False,
        )
        if needs_description_ja
        else stored_description_ja
    )
    title = title_ja if lang == "ja" else title_en
    description = (
        (description_ja if lang == "ja" else description_en)
        or i.description
    )
    return {
        "id": i.id,
        "app_name": i.app_name,
        "app_display_name": app_display_name,
        "app_name_ja": app_name_ja,
        "issue_type": i.issue_type,
        "rule_id": i.rule_id,
        "severity": i.severity,
        "sev_label": SEV_LABEL.get(i.severity, i.severity.upper()),
        "status": i.status,
        "fingerprint": i.fingerprint,
        "lang": lang,
        "title": title,
        "description": description,
        "title_en": title_en,
        "title_ja": title_ja,
        "description_en": description_en,
        "description_ja": description_ja,
        "span_name": i.span_name,
        "trace_id": i.trace_id,
        "escalation_count": i.escalation_count,
        "recurrence_count": i.recurrence_count or 0,
        "previous_issue_id": i.previous_issue_id,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
        "acknowledged_at": i.acknowledged_at.isoformat() if i.acknowledged_at else None,
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "metadata_json": meta,
    }


def _looks_english(text: str) -> bool:
    if not text:
        return False
    ascii_letters = sum(1 for ch in text if ("A" <= ch <= "Z") or ("a" <= ch <= "z"))
    japanese_chars = sum(1 for ch in text if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff")
    return ascii_letters >= 8 and japanese_chars == 0
