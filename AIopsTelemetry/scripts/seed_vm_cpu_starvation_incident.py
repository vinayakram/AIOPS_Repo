from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from server.database.engine import SessionLocal, init_db
from server.database.models import Issue, IssueAnalysis
from server.engine.bilingual import issue_description_ja, issue_title_ja


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def main() -> None:
    init_db()
    db = SessionLocal()
    now = datetime.utcnow()
    key = "vm-background-job-cpu-starves-docker-agent"
    app = "ai-agent"
    rule = "TOPO-CPU-STARVATION"
    title = "AI agent latency caused by host background job CPU saturation"
    desc = (
        "A background job on the VM entered a runaway loop and consumed host CPU. "
        "Docker cgroup CPU throttling reduced CPU available to the AI agent, causing p99 latency and LLM tool call timeouts. "
        "PGVector, Prometheus, and Langfuse stayed healthy."
    )
    cause = (
        "background-job-runner consumed 94% host CPU, Linux throttled the Docker CPU cgroup, "
        "and the ai-agent process was starved while dependencies remained healthy."
    )
    evidence = (
        "2026-04-30T10:14:05Z background-job-runner cpu=94% loop watchdog exceeded\n"
        "2026-04-30T10:14:18Z docker cgroup cpu.throttled.periods increased\n"
        "2026-04-30T10:14:31Z ai-agent p99 latency rose from 420ms to 4.2s\n"
        "2026-04-30T10:14:34Z LLM tool call timeout after 4000ms\n"
        "2026-04-30T10:14:40Z pgvector p99 stayed 12ms; prometheus and langfuse healthy"
    )
    action = (
        "Stop or nice-limit the runaway background job, reserve CPU for the Docker runtime, "
        "then validate ai-agent p99 latency and LLM tool calls return to baseline."
    )
    base_fp = hashlib.sha256(f"topology-incident:{key}".encode()).hexdigest()[:16]
    issue = db.query(Issue).filter(Issue.base_fingerprint == base_fp).first()
    if not issue:
        issue = Issue(fingerprint=base_fp, base_fingerprint=base_fp, recurrence_count=0)
        db.add(issue)
    issue.app_name = app
    issue.issue_type = "host_cpu_starvation"
    issue.rule_id = rule
    issue.severity = "critical"
    issue.status = "OPEN"
    issue.title = title
    issue.description = desc
    issue.title_en = title
    issue.title_ja = issue_title_ja(title, app_name=app, rule_id=rule)
    issue.description_en = desc
    issue.description_ja = issue_description_ja(desc, app_name=app, rule_id=rule)
    issue.span_name = "vm.background_job.cpu_starvation"
    issue.trace_id = "topology-cpu-starvation-demo"
    issue.created_at = now - timedelta(minutes=8)
    issue.updated_at = now
    issue.resolved_at = None
    topology = {
        "timestamp": iso(now - timedelta(minutes=8)),
        "propagation_label": "CPU starvation",
        "impact": {
            "where": "background-job-runner -> docker-cgroup -> ai-agent",
            "user_count": 18,
            "applications": ["ai-agent", "pgvector", "prometheus", "langfuse"],
            "application_count": 4,
        },
        "root_cause_chain": cause,
        "recommended_action": action,
        "nodes": [
            {
                "id": "background-job",
                "zone": "host",
                "name": "Background job",
                "process_name": "background-job-runner",
                "role": "Root-cause process on VM host",
                "status": "error",
                "timestamp": iso(now - timedelta(minutes=8)),
                "error_message": "Runaway loop consumed 94% host CPU.",
                "root_cause_chain": cause,
                "metrics": [
                    {"label": "Host CPU", "value": "94%"},
                    {"label": "Process CPU", "value": "94%"},
                    {"label": "Loop watchdog", "value": "exceeded"},
                ],
                "logs": [
                    {"timestamp": iso(now - timedelta(minutes=8, seconds=12)), "level": "ERROR", "message": "background-job-runner loop watchdog exceeded; iteration count=9,812,441"},
                    {"timestamp": iso(now - timedelta(minutes=7, seconds=52)), "level": "ERROR", "message": "process cpu utilization 94%; scheduler pressure detected on host"},
                    {"timestamp": iso(now - timedelta(minutes=7, seconds=36)), "level": "WARN", "message": "docker.slice cpu throttling increased after host CPU saturation"},
                ],
            },
            {
                "id": "ai-agent",
                "zone": "runtime",
                "name": "AI Agent",
                "process_name": "ai-agent",
                "role": "LLM inference service impacted by CPU starvation",
                "status": "warn",
                "timestamp": iso(now - timedelta(minutes=7, seconds=20)),
                "error_message": "p99 latency rose to 4.2s and LLM tool calls timed out.",
                "root_cause_chain": cause,
                "metrics": [
                    {"label": "Agent p99", "value": "4.2s"},
                    {"label": "Baseline p99", "value": "420ms"},
                    {"label": "LLM timeout", "value": "4000ms"},
                ],
                "logs": [
                    {"timestamp": iso(now - timedelta(minutes=7, seconds=18)), "level": "WARN", "message": "request latency p99=4200ms above threshold=1000ms"},
                    {"timestamp": iso(now - timedelta(minutes=7, seconds=8)), "level": "ERROR", "message": "LLM tool call timed out after 4000ms while waiting for CPU"},
                ],
            },
            {
                "id": "pgvector",
                "zone": "runtime",
                "name": "PGVector DB",
                "role": "Vector database dependency",
                "status": "ok",
                "timestamp": iso(now - timedelta(minutes=7)),
                "error_message": "Healthy; query p99 remained 12ms.",
                "root_cause_chain": cause,
                "metrics": [{"label": "PGVector p99", "value": "12ms"}],
                "logs": [{"timestamp": iso(now - timedelta(minutes=7)), "level": "INFO", "message": "query latency p99=12ms; no connection errors"}],
            },
            {
                "id": "prometheus",
                "zone": "runtime",
                "name": "Prometheus",
                "role": "Metrics scraping",
                "status": "ok",
                "timestamp": iso(now - timedelta(minutes=7)),
                "error_message": "Healthy; scraping continued.",
                "root_cause_chain": cause,
                "metrics": [{"label": "Scrape success", "value": "100%"}],
                "logs": [{"timestamp": iso(now - timedelta(minutes=7)), "level": "INFO", "message": "scrape ai-agent succeeded; host cpu alert firing"}],
            },
            {
                "id": "langfuse",
                "zone": "runtime",
                "name": "Langfuse",
                "role": "Tracing LLM calls",
                "status": "ok",
                "timestamp": iso(now - timedelta(minutes=7)),
                "error_message": "Healthy; traces captured slow and failed LLM calls.",
                "root_cause_chain": cause,
                "metrics": [{"label": "Trace ingest", "value": "ok"}],
                "logs": [{"timestamp": iso(now - timedelta(minutes=7)), "level": "INFO", "message": "captured llm call timeout trace_id=topology-cpu-starvation-demo"}],
            },
        ],
        "metrics": [
            {"label": "Host CPU", "value": "94%", "percent": 94, "tone": "error"},
            {"label": "Host Mem", "value": "60%", "percent": 60, "tone": "warn"},
            {"label": "Agent p99", "value": "4.2s", "percent": 88, "tone": "warn"},
            {"label": "PGVector p99", "value": "12ms", "percent": 14, "tone": "ok"},
        ],
        "traces": [
            {"timestamp": iso(now - timedelta(minutes=7, seconds=30)), "status": "fail", "label": "llm.tool.timeout"},
            {"timestamp": iso(now - timedelta(minutes=7, seconds=5)), "status": "slow", "label": "agent.generate.slow"},
            {"timestamp": iso(now - timedelta(minutes=6, seconds=48)), "status": "fail", "label": "llm.tool.timeout"},
            {"timestamp": iso(now - timedelta(minutes=6, seconds=21)), "status": "ok", "label": "pgvector.search"},
        ],
        "alerts": [
            {"name": "HighCPUUsage", "severity": "critical", "tone": "critical"},
            {"name": "AgentHighLatency", "severity": "warning", "tone": "warning"},
        ],
    }
    issue.metadata_json = json.dumps({
        "seed": "vm-cpu-starvation-topology",
        "seed_key": key,
        "cpu_percent": 94,
        "memory_percent": 60,
        "topology": topology,
    })
    db.flush()
    analysis = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue.id).first()
    if not analysis:
        analysis = IssueAnalysis(issue_id=issue.id)
        db.add(analysis)
    analysis.generated_at = now
    analysis.model_used = "seeded-topology-incident"
    analysis.status = "done"
    analysis.likely_cause = cause
    analysis.evidence = evidence
    analysis.recommended_action = action
    analysis.remediation_type = "host_process_limit"
    analysis.likely_cause_en = cause
    analysis.evidence_en = evidence
    analysis.recommended_action_en = action
    analysis.likely_cause_ja = issue_description_ja(cause, app_name=app, rule_id=rule)
    analysis.evidence_ja = issue_description_ja(evidence, app_name=app, rule_id=rule)
    analysis.recommended_action_ja = issue_description_ja(action, app_name=app, rule_id=rule)
    analysis.language_status = "ready"
    db.commit()
    print(json.dumps({"issue_id": issue.id, "created_or_refreshed": True, "seed_key": key}))
    db.close()


if __name__ == "__main__":
    main()
