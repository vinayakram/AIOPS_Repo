from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from server.config import settings
from server.database.engine import engine
from server.database.models import (
    Issue,
    RCAIncidentMemory,
    RCAIncidentPattern,
    RCAKnowledgeFeedback,
    RCAResolutionPlaybook,
)


@dataclass
class KnowledgeMatch:
    source: str
    title: str
    remediation_type: str
    confidence: float
    reason: str
    recommended_action: str
    validation_steps: list[str]
    prior_outcome: str = ""


PLAYBOOK_SEEDS: list[dict[str, Any]] = [
    {
        "name": "Container CPU or memory saturation",
        "description": "A container, pod, or Docker workload becomes unavailable after CPU or memory pressure crosses a guardrail.",
        "signal_type": "resource_pressure",
        "affected_layer": "infra",
        "industry_category": "capacity",
        "default_remediation_type": "infra_change",
        "severity_hint": "high",
        "keywords": ["cpu", "memory", "pod", "container", "docker", "threshold", "guardrail", "unavailable", "503"],
        "playbook": {
            "title": "Scale container capacity before tuning thresholds",
            "recommended_action": (
                "Treat this as a capacity and availability incident first. Increase CPU/memory allocation, "
                "add replicas or autoscaling where supported, and validate under the same load. Tune guardrail "
                "thresholds only after capacity is confirmed. Use code remediation only if evidence shows a leak "
                "or runaway loop."
            ),
            "remediation_type": "infra_change",
            "validation_steps": [
                "Verify CPU and memory headroom under the same load profile.",
                "Confirm health checks remain OK and HTTP 503s stop.",
                "Confirm dependent services no longer report upstream cascade failures.",
                "Check Prometheus for no repeated threshold breach in the validation window.",
            ],
            "rollback_steps": [
                "Revert container resource allocation or replica changes if instability increases.",
                "Restore previous deployment spec after capturing metrics for follow-up analysis.",
            ],
            "risk_notes": "Raising thresholds alone can mask saturation and delay failure detection.",
        },
    },
    {
        "name": "Upstream dependency failure",
        "description": "A service fails because a required upstream service returns errors, times out, or becomes unreachable.",
        "signal_type": "dependency_error",
        "affected_layer": "dependency",
        "industry_category": "cascade_failure",
        "default_remediation_type": "infra_change",
        "severity_hint": "high",
        "keywords": ["upstream", "dependency", "cascade", "timeout", "connection refused", "503"],
        "playbook": {
            "title": "Stabilize the upstream and protect the downstream",
            "recommended_action": (
                "Restore upstream health first, then add or verify downstream protections such as timeouts, "
                "bounded retries, circuit breakers, and clear fallback behavior."
            ),
            "remediation_type": "infra_change",
            "validation_steps": [
                "Confirm upstream error rate and latency recover.",
                "Confirm downstream traces no longer fail on the upstream span.",
                "Validate retry/circuit-breaker behavior under a controlled upstream failure.",
            ],
            "rollback_steps": ["Revert dependency routing or timeout changes if they increase error rate."],
            "risk_notes": "Fixing downstream code alone does not resolve an unhealthy upstream dependency.",
        },
    },
    {
        "name": "LLM provider rate limit",
        "description": "LLM calls fail or degrade because provider rate limits, quota, or throttling are reached.",
        "signal_type": "llm_rate_limit",
        "affected_layer": "llm",
        "industry_category": "quota",
        "default_remediation_type": "config_change",
        "severity_hint": "high",
        "keywords": ["llm", "openai", "rate limit", "429", "quota", "tokens"],
        "playbook": {
            "title": "Throttle, queue, and configure provider capacity",
            "recommended_action": (
                "Apply request shaping, backoff, queueing, and provider quota/capacity changes. Only change "
                "application logic if the traces show missing retry/backoff or unbounded fan-out."
            ),
            "remediation_type": "config_change",
            "validation_steps": [
                "Confirm 429 or rate-limited spans stop.",
                "Confirm queue latency remains inside SLO.",
                "Confirm token usage and request rate stay under provider limits.",
            ],
            "rollback_steps": ["Restore previous rate-limit settings if throughput drops below business requirements."],
            "risk_notes": "Blind retries can amplify provider throttling.",
        },
    },
    {
        "name": "Input validation or preprocessing failure",
        "description": "Requests fail before business logic because input validation or preprocessing rejects valid user input.",
        "signal_type": "validation_error",
        "affected_layer": "app",
        "industry_category": "data_quality",
        "default_remediation_type": "code_change",
        "severity_hint": "medium",
        "keywords": ["validation", "preprocess", "special character", "bad input", "query"],
        "playbook": {
            "title": "Normalize inputs and preserve valid user intent",
            "recommended_action": (
                "Fix validation and normalization rules, add regression tests for the failing input class, "
                "and keep rejection messages explicit for truly invalid input."
            ),
            "remediation_type": "code_change",
            "validation_steps": [
                "Replay the failing input and representative valid inputs.",
                "Add tests for the rejected character or format class.",
                "Confirm no broad input bypass was introduced.",
            ],
            "rollback_steps": ["Revert validation change if it allows unsafe input."],
            "risk_notes": "Overly permissive validation can create security or data quality issues.",
        },
    },
    {
        "name": "Out of memory or container OOM kill",
        "description": "A process or container is killed or degraded because memory usage exceeds allocation or grows without bound.",
        "signal_type": "memory_pressure",
        "affected_layer": "infra",
        "industry_category": "capacity",
        "default_remediation_type": "infra_change",
        "severity_hint": "critical",
        "keywords": ["oom", "out of memory", "memory", "killed", "rss", "heap", "container_memory"],
        "playbook": {
            "title": "Restore memory headroom and check for leaks",
            "recommended_action": "Increase memory allocation or replicas to restore service, then inspect memory growth, cache size, batch size, and leak indicators before changing code.",
            "remediation_type": "infra_change",
            "validation_steps": ["Confirm no OOM kills in the validation window.", "Confirm memory working set stabilizes below limit.", "Replay representative traffic and check p95 latency/error rate."],
            "rollback_steps": ["Revert resource changes if cost or node pressure becomes unacceptable.", "Roll back code only if leak evidence maps to a recent change."],
            "risk_notes": "Only increasing memory can hide a leak; only changing code can prolong outage if capacity is already exhausted.",
        },
    },
    {
        "name": "Disk full or inode exhaustion",
        "description": "Application fails because disk capacity, inode count, temp space, or log volume is exhausted.",
        "signal_type": "storage_capacity",
        "affected_layer": "infra",
        "industry_category": "capacity",
        "default_remediation_type": "infra_change",
        "severity_hint": "critical",
        "keywords": ["disk", "space", "no space left", "inode", "volume", "filesystem", "storage", "log"],
        "playbook": {
            "title": "Free space, expand volume, and stop uncontrolled growth",
            "recommended_action": "Restore free disk/inode capacity immediately, expand the volume if needed, rotate or archive logs, and identify the writer causing unexpected growth.",
            "remediation_type": "infra_change",
            "validation_steps": ["Confirm disk and inode usage are below alert thresholds.", "Confirm write paths and temp directories are healthy.", "Confirm log rotation or retention policy is active."],
            "rollback_steps": ["Revert retention changes only after preserving required audit logs."],
            "risk_notes": "Deleting files without identifying the writer often causes recurrence.",
        },
    },
    {
        "name": "Database connection pool exhaustion",
        "description": "Requests fail or queue because application or database connection pools are exhausted.",
        "signal_type": "connection_pool",
        "affected_layer": "dependency",
        "industry_category": "saturation",
        "default_remediation_type": "config_change",
        "severity_hint": "high",
        "keywords": ["connection pool", "too many connections", "pool exhausted", "database", "db", "sqlalchemy", "postgres"],
        "playbook": {
            "title": "Tune pool limits and close leaked connections",
            "recommended_action": "Confirm active connection count, tune pool size/timeouts within database limits, and inspect traces for long-held or leaked connections.",
            "remediation_type": "config_change",
            "validation_steps": ["Confirm active connections stay below DB max.", "Confirm request latency and timeout rate recover.", "Confirm no sessions remain idle in transaction."],
            "rollback_steps": ["Restore previous pool size if DB saturation or lock contention worsens."],
            "risk_notes": "Blindly increasing pool size can overload the database.",
        },
    },
    {
        "name": "DNS resolution failure",
        "description": "Service calls fail because DNS cannot resolve a dependency or returns unstable results.",
        "signal_type": "dns_failure",
        "affected_layer": "network",
        "industry_category": "network",
        "default_remediation_type": "infra_change",
        "severity_hint": "high",
        "keywords": ["dns", "servfail", "nxdomain", "name resolution", "getaddrinfo", "temporary failure"],
        "playbook": {
            "title": "Restore DNS path and dependency discovery",
            "recommended_action": "Check DNS resolver health, service discovery records, network policy, and recent changes to dependency hostnames or zones.",
            "remediation_type": "infra_change",
            "validation_steps": ["Resolve dependency names from the affected container.", "Confirm DNS error counters stop increasing.", "Replay dependency calls successfully."],
            "rollback_steps": ["Revert DNS/service-discovery changes if they caused resolution instability."],
            "risk_notes": "Hardcoding IPs is a brittle emergency workaround and should not become the permanent fix.",
        },
    },
    {
        "name": "TLS certificate or secret expiry",
        "description": "Calls fail because certificates, keys, tokens, or mounted secrets expired or rotated incorrectly.",
        "signal_type": "auth_tls_secret",
        "affected_layer": "security",
        "industry_category": "certificate",
        "default_remediation_type": "infra_change",
        "severity_hint": "critical",
        "keywords": ["tls", "certificate", "x509", "expired", "secret", "token expired", "unauthorized", "401", "403"],
        "playbook": {
            "title": "Rotate credentials and validate reload behavior",
            "recommended_action": "Rotate or restore the expired credential, confirm applications reload it, and add expiry monitoring if missing.",
            "remediation_type": "infra_change",
            "validation_steps": ["Confirm certificate/token expiry date is valid.", "Confirm successful authenticated dependency calls.", "Confirm expiry alerting exists before the next rotation window."],
            "rollback_steps": ["Rollback to last known valid certificate or secret only if it is still trusted and not compromised."],
            "risk_notes": "Restart may be required if the app does not hot-reload mounted secrets.",
        },
    },
    {
        "name": "Deployment regression",
        "description": "Failures begin shortly after a release, image update, configuration rollout, or dependency version change.",
        "signal_type": "deployment_regression",
        "affected_layer": "app",
        "industry_category": "regression",
        "default_remediation_type": "code_change",
        "severity_hint": "high",
        "keywords": ["deployment", "release", "rollout", "regression", "new version", "image", "commit", "after deploy"],
        "playbook": {
            "title": "Rollback first when blast radius is active",
            "recommended_action": "If customer impact is active and the regression window matches a recent deploy, roll back or disable the change first, then perform root-cause fix with tests.",
            "remediation_type": "code_change",
            "validation_steps": ["Confirm error rate recovers after rollback.", "Compare failing traces before and after deployment.", "Add regression tests for the failing path."],
            "rollback_steps": ["Rollback to the previous known-good image/configuration."],
            "risk_notes": "Debugging in production while impact continues extends MTTR.",
        },
    },
    {
        "name": "Queue backlog or worker saturation",
        "description": "Async jobs, consumers, or workers fall behind and user-facing work times out or becomes stale.",
        "signal_type": "queue_backlog",
        "affected_layer": "infra",
        "industry_category": "saturation",
        "default_remediation_type": "infra_change",
        "severity_hint": "high",
        "keywords": ["queue", "backlog", "consumer lag", "worker", "jobs", "kafka lag", "sqs", "celery"],
        "playbook": {
            "title": "Scale consumers and control producer rate",
            "recommended_action": "Scale worker/consumer capacity, confirm dependency throughput, and apply producer backpressure or priority handling when backlog grows faster than drain rate.",
            "remediation_type": "infra_change",
            "validation_steps": ["Confirm backlog and oldest-message age decline.", "Confirm worker error rate remains low after scaling.", "Confirm end-to-end latency returns to SLO."],
            "rollback_steps": ["Scale back after backlog drains if sustained capacity is not needed."],
            "risk_notes": "Scaling workers can overload downstream dependencies if they are the true bottleneck.",
        },
    },
    {
        "name": "Cache outage or cache stampede",
        "description": "Latency or dependency load spikes because cache is unavailable, cold, or overwhelmed by simultaneous misses.",
        "signal_type": "cache_failure",
        "affected_layer": "dependency",
        "industry_category": "performance",
        "default_remediation_type": "config_change",
        "severity_hint": "high",
        "keywords": ["cache", "redis", "memcached", "stampede", "miss rate", "eviction", "cold cache"],
        "playbook": {
            "title": "Restore cache and protect origin systems",
            "recommended_action": "Restore cache health, warm critical keys, add request coalescing or jittered TTLs, and protect origin dependencies from miss storms.",
            "remediation_type": "config_change",
            "validation_steps": ["Confirm cache hit rate recovers.", "Confirm origin dependency latency/error rate returns to baseline.", "Confirm no synchronized TTL expiry pattern remains."],
            "rollback_steps": ["Revert TTL or cache routing changes if stale data risk becomes unacceptable."],
            "risk_notes": "Disabling cache can amplify load on databases and APIs.",
        },
    },
    {
        "name": "Noisy neighbor or host contention",
        "description": "A service degrades because another process/container on the same host consumes shared CPU, memory, disk, or network resources.",
        "signal_type": "host_contention",
        "affected_layer": "infra",
        "industry_category": "capacity",
        "default_remediation_type": "infra_change",
        "severity_hint": "high",
        "keywords": ["noisy neighbor", "host", "contention", "steal", "shared", "cadvisor", "node pressure"],
        "playbook": {
            "title": "Isolate workload or enforce resource limits",
            "recommended_action": "Move the affected workload, enforce resource reservations/limits, or isolate noisy workloads so critical services retain guaranteed capacity.",
            "remediation_type": "infra_change",
            "validation_steps": ["Confirm host-level contention metrics normalize.", "Confirm affected service latency/error rate recovers.", "Confirm resource limits are enforced for noisy workloads."],
            "rollback_steps": ["Move workloads back only after capacity and isolation are validated."],
            "risk_notes": "Raising app thresholds does not solve host contention.",
        },
    },
    {
        "name": "Autoscaling did not trigger",
        "description": "Traffic or resource pressure increased but replicas/capacity did not scale because autoscaling signals, limits, or policies were wrong.",
        "signal_type": "autoscaling_failure",
        "affected_layer": "infra",
        "industry_category": "capacity",
        "default_remediation_type": "infra_change",
        "severity_hint": "high",
        "keywords": ["autoscale", "autoscaling", "replica", "hpa", "scale out", "capacity", "threshold"],
        "playbook": {
            "title": "Fix scaling policy and validate under load",
            "recommended_action": "Review scaling metrics, min/max replica limits, cooldowns, and resource requests; increase capacity immediately if impact is active.",
            "remediation_type": "infra_change",
            "validation_steps": ["Confirm scale-out occurs under synthetic or replayed load.", "Confirm replicas remain within safe limits.", "Confirm SLOs recover during peak load."],
            "rollback_steps": ["Restore previous scaling policy if new policy causes oscillation or runaway cost."],
            "risk_notes": "Autoscaling based on the wrong metric can scale too late or not at all.",
        },
    },
    {
        "name": "Storage I/O saturation",
        "description": "Latency or failures increase because disk IOPS, throughput, or storage latency is saturated.",
        "signal_type": "storage_io",
        "affected_layer": "infra",
        "industry_category": "saturation",
        "default_remediation_type": "infra_change",
        "severity_hint": "high",
        "keywords": ["iops", "disk latency", "storage latency", "read latency", "write latency", "io wait", "fsync"],
        "playbook": {
            "title": "Increase storage performance or reduce write amplification",
            "recommended_action": "Scale storage IOPS/throughput, move hot paths to faster storage, and inspect recent workloads for excessive writes or compaction pressure.",
            "remediation_type": "infra_change",
            "validation_steps": ["Confirm disk latency and IOPS utilization normalize.", "Confirm app p95/p99 latency recovers.", "Confirm no write amplification source remains active."],
            "rollback_steps": ["Rollback workload or storage class changes if latency worsens."],
            "risk_notes": "CPU-focused fixes will not resolve an I/O-bound service.",
        },
    },
    {
        "name": "Thread pool or worker pool exhaustion",
        "description": "Requests stall because server workers, threads, event loop tasks, or process pools are saturated.",
        "signal_type": "worker_pool",
        "affected_layer": "app",
        "industry_category": "saturation",
        "default_remediation_type": "config_change",
        "severity_hint": "high",
        "keywords": ["thread pool", "worker pool", "event loop", "blocked", "uvicorn workers", "gunicorn", "executor"],
        "playbook": {
            "title": "Right-size workers and remove blocking hot paths",
            "recommended_action": "Tune worker/thread limits for the runtime and inspect traces for blocking calls; move CPU-bound work off the request path or scale workers.",
            "remediation_type": "config_change",
            "validation_steps": ["Confirm in-flight requests and queue wait fall.", "Confirm worker utilization has headroom.", "Confirm blocking spans are removed or isolated."],
            "rollback_steps": ["Revert worker increase if memory or context-switch overhead increases error rate."],
            "risk_notes": "Adding workers can increase memory pressure if each worker is heavy.",
        },
    },
]


def init_knowledge_base() -> None:
    if not settings.RCA_KB_ENABLED:
        return
    _enable_pgvector_if_available()
    _seed_playbooks()


def find_matches_for_issue(db: Session, issue: Issue, limit: int = 5) -> list[KnowledgeMatch]:
    text_blob = " ".join(
        str(part or "")
        for part in (issue.title, issue.description, issue.issue_type, issue.rule_id, issue.app_name)
    ).lower()
    matches: list[KnowledgeMatch] = []

    patterns = db.query(RCAIncidentPattern).all()
    for pattern in patterns:
        keywords = _loads(pattern.keywords_json, [])
        hits = [kw for kw in keywords if str(kw).lower() in text_blob]
        if not hits:
            continue
        playbook = (
            db.query(RCAResolutionPlaybook)
            .filter(RCAResolutionPlaybook.pattern_id == pattern.id)
            .order_by(RCAResolutionPlaybook.priority.asc(), RCAResolutionPlaybook.id.asc())
            .first()
        )
        if not playbook:
            continue
        matches.append(KnowledgeMatch(
            source=playbook.source or "industry",
            title=playbook.title,
            remediation_type=playbook.remediation_type,
            confidence=min(0.95, 0.45 + 0.1 * len(hits)),
            reason=f"Matched industry pattern '{pattern.name}' via keywords: {', '.join(hits[:6])}",
            recommended_action=playbook.recommended_action,
            validation_steps=_loads(playbook.validation_steps_json, []),
        ))

    memories = (
        db.query(RCAIncidentMemory)
        .filter(RCAIncidentMemory.app_name == issue.app_name)
        .order_by(RCAIncidentMemory.created_at.desc())
        .limit(20)
        .all()
    )
    for memory in memories:
        memory_text = " ".join(str(part or "") for part in (memory.title, memory.summary, memory.root_cause)).lower()
        overlap = _word_overlap(text_blob, memory_text)
        if overlap < 0.08:
            continue
        succeeded = memory.resolution_status == "succeeded" and not memory.recurrence_after_fix
        confidence = 0.8 if succeeded else 0.55
        matches.append(KnowledgeMatch(
            source="organization",
            title=memory.title,
            remediation_type=memory.remediation_type or "unknown",
            confidence=confidence,
            reason=(
                "Similar past incident resolved successfully."
                if succeeded
                else "Similar past incident found, but the outcome was not clearly successful."
            ),
            recommended_action=memory.action_taken or memory.root_cause or memory.summary or "",
            validation_steps=[memory.validation_result] if memory.validation_result else [],
            prior_outcome=memory.resolution_status or "unknown",
        ))

    return sorted(matches, key=lambda item: item.confidence, reverse=True)[:limit]


def record_feedback(
    db: Session,
    *,
    issue_id: int | None,
    was_helpful: bool | None,
    was_correct: bool | None,
    actual_root_cause: str = "",
    actual_fix: str = "",
    notes: str = "",
    created_by: str = "",
) -> RCAKnowledgeFeedback:
    row = RCAKnowledgeFeedback(
        issue_id=issue_id,
        was_helpful=was_helpful,
        was_correct=was_correct,
        actual_root_cause=actual_root_cause,
        actual_fix=actual_fix,
        notes=notes,
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _seed_playbooks() -> None:
    from server.database.engine import SessionLocal

    db = SessionLocal()
    try:
        for seed in PLAYBOOK_SEEDS:
            pattern = (
                db.query(RCAIncidentPattern)
                .filter(RCAIncidentPattern.name == seed["name"])
                .first()
            )
            if not pattern:
                pattern = RCAIncidentPattern(name=seed["name"], description=seed["description"])
                db.add(pattern)
                db.flush()
            pattern.description = seed["description"]
            pattern.signal_type = seed["signal_type"]
            pattern.affected_layer = seed["affected_layer"]
            pattern.industry_category = seed["industry_category"]
            pattern.default_remediation_type = seed["default_remediation_type"]
            pattern.severity_hint = seed["severity_hint"]
            pattern.keywords_json = json.dumps(seed["keywords"])

            pb = (
                db.query(RCAResolutionPlaybook)
                .filter(RCAResolutionPlaybook.pattern_id == pattern.id)
                .filter(RCAResolutionPlaybook.title == seed["playbook"]["title"])
                .first()
            )
            if not pb:
                pb = RCAResolutionPlaybook(pattern_id=pattern.id, title=seed["playbook"]["title"])
                db.add(pb)
            pb.recommended_action = seed["playbook"]["recommended_action"]
            pb.remediation_type = seed["playbook"]["remediation_type"]
            pb.validation_steps_json = json.dumps(seed["playbook"]["validation_steps"])
            pb.rollback_steps_json = json.dumps(seed["playbook"]["rollback_steps"])
            pb.risk_notes = seed["playbook"]["risk_notes"]
            pb.source = "industry"
            pb.priority = 10
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _enable_pgvector_if_available() -> None:
    if not settings.DATABASE_URL.startswith(("postgresql://", "postgresql+")):
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        dim = int(settings.RCA_KB_VECTOR_DIMENSION)
        for table_name in ("rca_incident_patterns", "rca_resolution_playbooks", "rca_incident_memory"):
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding vector({dim})"))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rca_incident_patterns_embedding "
            "ON rca_incident_patterns USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rca_resolution_playbooks_embedding "
            "ON rca_resolution_playbooks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_rca_incident_memory_embedding "
            "ON rca_incident_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
        ))


def _loads(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _word_overlap(a: str, b: str) -> float:
    aw = {w for w in a.lower().split() if len(w) > 3}
    bw = {w for w in b.lower().split() if len(w) > 3}
    if not aw or not bw:
        return 0.0
    return len(aw & bw) / max(len(aw), 1)
