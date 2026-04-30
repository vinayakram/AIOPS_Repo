from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from server.database.engine import SessionLocal, init_db
from server.database.models import Issue, IssueAnalysis
from server.engine.bilingual import issue_description_ja, issue_title_ja


CASES = [
    ("oom-sample-agent", "sample-agent", "container_oom_kill", "NFR-33", "critical", "Sample agent container killed by memory pressure", "The sample-agent container restarted after memory usage crossed its limit. Dependent triage calls saw 503 responses during the restart window.", "Container memory allocation is too low for the current request burst, with possible cache growth increasing the working set.", "Prometheus container memory working set approached the configured limit.\nDependent triage-agent spans failed on upstream sample-agent calls.\nContainer restart count increased in the same incident window.", "Increase memory headroom or replicas first, then inspect cache/batch growth and validate under the same load profile."),
    ("disk-full-medical-agent", "medical-agent", "disk_full", "NFR-41", "critical", "Medical agent log volume is almost full", "The medical-agent host volume has less than 5 percent free space and write operations are intermittently failing.", "Log retention is not rotating quickly enough for the current request volume.", "Filesystem usage crossed the critical threshold.\nApplication logs show write failures with no space left on device.\nLog directory growth accelerated after debug logging was enabled.", "Free or expand storage, restore log rotation, then verify disk and inode headroom remains below alert thresholds."),
    ("db-pool-triage", "triage-agent", "database_connection_pool_exhaustion", "NFR-22", "high", "Triage agent database connection pool exhausted", "Requests are timing out while waiting for database connections during concurrent RCA lookups.", "The app pool size and timeout are not aligned with current concurrency, and some sessions appear long-lived.", "Connection wait time increased before request timeouts.\nDatabase active connections stayed close to max.\nTraces show long spans around incident lookup queries.", "Tune pool size/timeouts within DB limits and close leaked or long-held sessions before increasing concurrency further."),
    ("dns-langfuse", "observability-gateway", "dns_resolution_failure", "NFR-18", "high", "Langfuse endpoint DNS resolution is failing", "The observability gateway cannot resolve the Langfuse hostname, so traces are buffered and then dropped.", "DNS resolver or service discovery path is unstable for the Langfuse endpoint.", "Trace export logs show getaddrinfo temporary failure.\nPrometheus export failure counter increased.\nPrometheus metrics still scrape locally, isolating the issue to the Langfuse route.", "Restore DNS resolution from the affected container and validate trace export succeeds without buffering."),
    ("tls-expiry-prometheus", "prometheus-bridge", "tls_certificate_expiry", "NFR-19", "critical", "Prometheus bridge certificate expired", "TLS handshakes to the metrics bridge are failing after a certificate expiry.", "The mounted certificate expired and the service did not reload a renewed secret.", "Client errors include x509 certificate has expired.\nMetrics scrape success dropped to zero after the expiry timestamp.\nNo matching application deployment occurred in the same window.", "Rotate the certificate or secret, restart/reload the bridge if required, and add expiry monitoring."),
    ("deploy-regression-chat", "joshu-chat", "deployment_regression", "NFR-8a", "high", "Chat interface started failing after latest deployment", "Users report blank responses after the latest frontend deployment.", "A JavaScript change introduced a missing handler path for implementation results.", "Browser console shows handler is not defined.\nFailures began immediately after the deployment timestamp.\nBackend status endpoints continue to return healthy responses.", "Rollback if impact is active, then patch the handler and add a UI smoke check for result buttons."),
    ("queue-backlog-rca", "rca-worker", "queue_backlog", "NFR-26", "high", "RCA worker queue backlog is growing", "RCA jobs are waiting longer than the SLO because worker throughput is below arrival rate.", "Worker capacity is saturated and downstream LLM calls are increasing job duration.", "Oldest job age crossed the alert threshold.\nWorker CPU remains high while queue depth increases.\nLLM latency rose during the same time window.", "Scale workers, protect downstream limits, and validate backlog drain rate returns to normal."),
    ("cache-stampede", "medical-search-api", "cache_stampede", "NFR-14", "medium", "Medical search API latency spike from cache misses", "Search latency increased sharply when many cached keys expired at the same time.", "Synchronized TTL expiry caused a cache stampede and overloaded the origin search dependency.", "Cache hit rate dropped while origin request rate spiked.\nP95 latency increased only on cacheable search routes.\nRedis remained reachable but miss rate was abnormal.", "Warm critical keys, add TTL jitter/request coalescing, and verify origin load returns to baseline."),
    ("autoscale-not-triggered", "sample-agent", "autoscaling_failure", "NFR-33", "high", "Sample agent did not scale during CPU pressure", "CPU stayed above threshold but no additional capacity was added before availability degraded.", "Autoscaling policy is using the wrong metric or replica limits are preventing scale-out.", "CPU stayed above threshold for the alert window.\nReplica count remained unchanged.\nDependent service saw upstream 503 responses.", "Review scaling metrics, min/max replicas and cooldowns; increase capacity immediately if impact is active."),
    ("storage-io-saturation", "trace-store", "storage_io_saturation", "NFR-42", "high", "Trace store write latency is saturated", "Trace ingestion is delayed because storage write latency and IOPS utilization are high.", "The trace store is I/O bound during burst ingestion.", "Write latency increased with IOPS utilization.\nTrace ingestion queue depth increased.\nCPU remained below saturation, pointing away from app CPU pressure.", "Increase storage IOPS/throughput or reduce write amplification, then verify ingestion latency recovers."),
    ("thread-pool-exhausted", "gateway-api", "worker_pool_exhaustion", "NFR-11", "medium", "Gateway worker pool is exhausted", "Requests are queued because all workers are busy during long upstream waits.", "Worker pool size is too small for the current blocking upstream call pattern.", "In-flight requests stayed at worker limit.\nQueue wait increased before response latency.\nUpstream spans show long blocking calls.", "Tune worker limits and remove or isolate blocking calls from the request path."),
    ("llm-rate-limit", "rca-assistant", "llm_rate_limit", "NFR-51", "high", "RCA assistant is hitting LLM rate limits", "RCA generation is failing intermittently with provider 429 responses.", "The RCA assistant is exceeding provider request or token throughput limits.", "LLM spans show 429 rate limit errors.\nRetry attempts increase total latency.\nQueue time rises during concurrent RCA generation.", "Add request shaping/backoff and adjust quota or concurrency limits before increasing retries."),
]


def main() -> None:
    init_db()
    db = SessionLocal()
    created = 0
    refreshed = 0
    ids: list[int] = []
    try:
        for idx, case in enumerate(CASES):
            key, app, issue_type, rule, severity, title, desc, cause, evidence, action = case
            base_fp = hashlib.sha256(f"demo-open-issue:{key}".encode()).hexdigest()[:16]
            issue = db.query(Issue).filter(Issue.base_fingerprint == base_fp).first()
            if issue:
                refreshed += 1
            else:
                issue = Issue(fingerprint=base_fp, base_fingerprint=base_fp, recurrence_count=0)
                db.add(issue)
                created += 1
            issue.app_name = app
            issue.issue_type = issue_type
            issue.rule_id = rule
            issue.severity = severity
            issue.status = "OPEN" if idx % 4 else "ESCALATED"
            issue.title = title
            issue.description = desc
            issue.title_en = title
            issue.title_ja = issue_title_ja(title, app_name=app)
            issue.description_en = desc
            issue.description_ja = issue_description_ja(desc, app_name=app)
            issue.span_name = f"demo.{issue_type}"
            issue.trace_id = f"demo-{key}"
            issue.updated_at = datetime.utcnow()
            issue.resolved_at = None
            issue.metadata_json = json.dumps({"seed": "demo-open-issues", "seed_key": key})
            db.flush()

            analysis = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue.id).first()
            if not analysis:
                analysis = IssueAnalysis(issue_id=issue.id)
                db.add(analysis)
            analysis.status = "done"
            analysis.model_used = "seeded-rca-knowledge"
            analysis.likely_cause = cause
            analysis.evidence = evidence
            analysis.recommended_action = action
            analysis.likely_cause_en = cause
            analysis.evidence_en = evidence
            analysis.recommended_action_en = action
            analysis.likely_cause_ja = issue_description_ja(cause, app_name=app)
            analysis.evidence_ja = issue_description_ja(evidence, app_name=app)
            analysis.recommended_action_ja = issue_description_ja(action, app_name=app)
            analysis.language_status = "ready"
            analysis.generated_at = datetime.utcnow()
            ids.append(issue.id)
        db.commit()
    finally:
        db.close()
    print(json.dumps({"created": created, "refreshed": refreshed, "total": len(CASES), "issue_ids": ids}))


if __name__ == "__main__":
    main()
