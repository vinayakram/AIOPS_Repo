import json
import hashlib
from datetime import datetime
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

    # Dedup: return existing open issue without modification
    open_existing = (
        db.query(Issue)
        .filter(Issue.base_fingerprint == base_fp, Issue.status != "RESOLVED")
        .first()
    )
    if open_existing:
        return {"id": open_existing.id, "created": False, "message": "Duplicate open issue"}

    # Find prior resolved issue for recurrence linkage
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
    computed_title_ja = issue_title_ja(
        i.title, app_name=i.app_name, rule_id=i.rule_id
    )
    stored_title_ja = i.title_ja or ""
    title_ja = (
        computed_title_ja
        if not stored_title_ja
        or "NFR-" in stored_title_ja
        or i.app_name in stored_title_ja
        or stored_title_ja == title_en
        or _looks_english(stored_title_ja)
        else stored_title_ja
    )
    description_en = i.description_en or i.description
    computed_description_ja = issue_description_ja(
        i.description, app_name=i.app_name, rule_id=i.rule_id
    )
    stored_description_ja = i.description_ja or ""
    description_ja = (
        computed_description_ja
        if not stored_description_ja
        or "application is not reachable" in stored_description_ja.lower()
        or stored_description_ja == description_en
        or _looks_english(stored_description_ja)
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
