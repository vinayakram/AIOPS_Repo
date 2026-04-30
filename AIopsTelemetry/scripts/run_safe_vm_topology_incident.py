
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import psutil

from server.database.engine import SessionLocal, init_db
from server.database.models import Issue, IssueAnalysis
from server.engine.bilingual import issue_description_ja, issue_title_ja
from server.engine.topology_agent import classify_container_node, discover_docker_containers, host_snapshot, http_probe, tcp_probe, utc_now_iso
from server.engine.topology_correlation_agent import correlate_recent_topology_issues


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[idx]


def start_safe_background_job(duration: int, duty_cycle: float) -> subprocess.Popen:
    code = (
        "import math, os, time\n"
        "try:\n    os.nice(10)\nexcept Exception:\n    pass\n"
        f"end = time.time() + {int(duration)}\n"
        "window = 0.10\n"
        f"busy = max(0.05, min(0.95, {float(duty_cycle)!r})) * window\n"
        "x = 0.123\n"
        "while time.time() < end:\n"
        "    stop = time.time() + busy\n"
        "    while time.time() < stop:\n"
        "        x = math.sin(x * 1.0001) + math.sqrt(abs(x) + 1.0)\n"
        "    time.sleep(max(0.0, window - busy))\n"
    )
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tone_for_percent(value: float) -> str:
    return "error" if value >= 85 else "warn" if value >= 60 else "ok"


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely recreate a VM-host CPU contention topology incident.")
    parser.add_argument("--duration", type=int, default=18, help="seconds to run the low-priority background job")
    parser.add_argument("--duty-cycle", type=float, default=0.82, help="fraction of one CPU core, capped 0.05..0.95")
    parser.add_argument("--agent-url", default=os.getenv("SAMPLE_URL", "http://localhost:8002"))
    parser.add_argument("--prometheus-url", default=os.getenv("PROMETHEUS_URL", "http://localhost:9092"))
    parser.add_argument("--pgvector-host", default=os.getenv("PGVECTOR_HOST", "localhost"))
    parser.add_argument("--pgvector-port", type=int, default=int(os.getenv("PGVECTOR_PORT", "5432")))
    parser.add_argument("--scenario-key", default="safe-live-vm-background-job-cpu-contention")
    args = parser.parse_args()

    duration = max(6, min(args.duration, 45))
    duty = max(0.05, min(args.duty_cycle, 0.95))
    started = datetime.utcnow()

    baseline_agent = [http_probe(args.agent_url.rstrip("/") + "/api/health", timeout=2.0) for _ in range(3)]
    baseline_latency = [p["latency_ms"] for p in baseline_agent if p.get("latency_ms") is not None]
    containers_before = discover_docker_containers()
    before_host = host_snapshot()

    proc = start_safe_background_job(duration, duty)
    ps_proc = psutil.Process(proc.pid)
    started_log = {"timestamp": utc_now_iso(), "level": "INFO", "message": f"started low-priority background job pid={proc.pid} duty_cycle={duty}"}

    host_samples: list[dict[str, Any]] = []
    process_cpu: list[float] = []
    agent_probes: list[dict[str, Any]] = []
    deadline = time.time() + duration
    while time.time() < deadline:
        try:
            process_cpu.append(ps_proc.cpu_percent(interval=0.4))
        except psutil.Error:
            pass
        host_samples.append(host_snapshot())
        agent_probes.append(http_probe(args.agent_url.rstrip("/") + "/api/demo/background-load", method="POST", body={"work_ms": 350}, timeout=4.0))
        time.sleep(0.4)

    proc.wait(timeout=5)
    finished_log = {"timestamp": utc_now_iso(), "level": "INFO", "message": f"background job exited rc={proc.returncode}"}

    containers_after = discover_docker_containers() or containers_before
    prometheus_probe = http_probe(args.prometheus_url.rstrip("/") + "/-/healthy", timeout=2.0)
    pgvector_probe = tcp_probe(args.pgvector_host, args.pgvector_port, timeout=1.0)
    agent_health = http_probe(args.agent_url.rstrip("/") + "/api/health", timeout=2.0)

    host_cpu_peak = max([s.get("cpu_percent") or 0 for s in host_samples] + [before_host.get("cpu_percent") or 0])
    host_mem_peak = max([s.get("memory_percent") or 0 for s in host_samples] + [before_host.get("memory_percent") or 0])
    process_cpu_peak = max(process_cpu or [0.0])
    latency_values = [p["latency_ms"] for p in agent_probes if p.get("latency_ms") is not None]
    agent_p99 = percentile(latency_values, 99)
    baseline_p99 = percentile(baseline_latency, 99)
    failed_calls = [p for p in agent_probes if not p.get("ok")]
    severity = "critical" if host_cpu_peak >= 85 or process_cpu_peak >= 70 or failed_calls else "high"

    app_name = "ai-agent"
    title = "AI agent latency correlated with host background job CPU contention"
    desc = (
        f"A low-priority host background job was observed consuming up to {process_cpu_peak:.1f}% of one CPU core while "
        f"the Docker-hosted agent was probed. Agent p99 probe latency was {agent_p99:.1f}ms "
        f"(baseline {baseline_p99:.1f}ms). Host CPU peak during the window was {host_cpu_peak:.1f}%."
    )
    cause = (
        f"Host process pid={proc.pid} consumed up to {process_cpu_peak:.1f}% CPU during the incident window. "
        "The topology collector correlated that host-side contention with Docker-hosted agent probes."
    )
    action = "Limit or schedule the background job, reserve CPU for the Docker runtime, and re-check agent latency under the same workload."

    nodes: list[dict[str, Any]] = [{
        "id": "host-background-job",
        "zone": "host",
        "name": "Background job",
        "process_name": f"python pid={proc.pid}",
        "role": "Measured host-side workload source",
        "status": "error" if severity == "critical" else "warn",
        "timestamp": started_log["timestamp"],
        "error_message": f"Host background job consumed {process_cpu_peak:.1f}% CPU during scenario window.",
        "root_cause_chain": cause,
        "metrics": [
            {"label": "Process CPU peak", "value": f"{process_cpu_peak:.1f}%"},
            {"label": "Host CPU peak", "value": f"{host_cpu_peak:.1f}%"},
            {"label": "Duty cycle", "value": f"{duty:.2f}"},
        ],
        "logs": [started_log, {"timestamp": utc_now_iso(), "level": "ERROR" if process_cpu_peak >= 80 else "WARN", "message": f"background process peak cpu={process_cpu_peak:.1f}% host_cpu_peak={host_cpu_peak:.1f}%"}, finished_log],
    }]

    agent_container_seen = False
    standard_component_seen: set[str] = set()
    for container in containers_after:
        node = classify_container_node(container)
        kind = container.get("kind")
        if kind == "agent":
            agent_container_seen = True
            degraded = agent_p99 > max(750, baseline_p99 * 2) or bool(failed_calls)
            node.update({
                "id": "ai-agent",
                "name": "AI Agent",
                "status": "warn" if degraded else "ok",
                "error_message": f"Measured p99 probe latency {agent_p99:.1f}ms; baseline {baseline_p99:.1f}ms; failed probes {len(failed_calls)}.",
                "root_cause_chain": cause,
                "metrics": [{"label": "Agent p99", "value": f"{agent_p99:.1f}ms"}, {"label": "Baseline p99", "value": f"{baseline_p99:.1f}ms"}, {"label": "Failed probes", "value": str(len(failed_calls))}],
                "logs": [{"timestamp": p.get("timestamp"), "level": "ERROR" if not p.get("ok") else "WARN" if p.get("latency_ms", 0) > max(750, baseline_p99 * 2) else "INFO", "message": f"agent probe status={p.get('status')} latency_ms={p.get('latency_ms')}"} for p in agent_probes[-6:]],
            })
        elif kind == "prometheus":
            node.update({"id": "prometheus", "name": "Prometheus", "status": "ok" if prometheus_probe.get("ok") else "warn", "error_message": "Prometheus health endpoint reachable." if prometheus_probe.get("ok") else f"Prometheus health probe failed: {prometheus_probe.get('error')}", "metrics": [{"label": "Health latency", "value": f"{prometheus_probe.get('latency_ms')}ms"}], "logs": [{"timestamp": prometheus_probe.get("timestamp"), "level": "INFO" if prometheus_probe.get("ok") else "WARN", "message": f"prometheus health ok={prometheus_probe.get('ok')} latency_ms={prometheus_probe.get('latency_ms')}"}]})
        elif kind == "pgvector":
            node.update({"id": "pgvector", "name": "PGVector DB", "status": "ok" if pgvector_probe.get("ok") else "warn", "error_message": "PGVector TCP port reachable." if pgvector_probe.get("ok") else f"PGVector TCP probe failed: {pgvector_probe.get('error')}", "metrics": [{"label": "TCP latency", "value": f"{pgvector_probe.get('latency_ms')}ms"}], "logs": [{"timestamp": pgvector_probe.get("timestamp"), "level": "INFO" if pgvector_probe.get("ok") else "WARN", "message": f"pgvector tcp ok={pgvector_probe.get('ok')} latency_ms={pgvector_probe.get('latency_ms')}"}]})
        elif kind == "langfuse":
            node.update({"id": "langfuse", "name": "Langfuse", "status": "ok", "error_message": "Langfuse container discovered in Docker topology.", "metrics": [{"label": "Container", "value": container.get("name")}], "logs": [{"timestamp": utc_now_iso(), "level": "INFO", "message": f"discovered langfuse container {container.get('name')}"}]})
        if node.get("id") in {"ai-agent", "prometheus", "pgvector", "langfuse"}:
            if node["id"] in standard_component_seen:
                continue
            standard_component_seen.add(node["id"])
        nodes.append(node)

    if not agent_container_seen:
        nodes.append({"id": "ai-agent", "zone": "runtime", "name": "AI Agent", "role": "Agent endpoint discovered by HTTP probe", "status": "warn" if failed_calls else "ok", "timestamp": utc_now_iso(), "error_message": f"Measured p99 probe latency {agent_p99:.1f}ms; baseline {baseline_p99:.1f}ms; failed probes {len(failed_calls)}.", "root_cause_chain": cause, "metrics": [{"label": "Agent p99", "value": f"{agent_p99:.1f}ms"}], "logs": [{"timestamp": p.get("timestamp"), "level": "INFO", "message": f"agent probe latency_ms={p.get('latency_ms')} status={p.get('status')}"} for p in agent_probes[-4:]]})

    if not any(n.get("id") == "pgvector" for n in nodes):
        nodes.append({"id": "pgvector", "zone": "runtime", "name": "PGVector DB", "role": "Vector store endpoint probe", "status": "ok" if pgvector_probe.get("ok") else "warn", "timestamp": pgvector_probe.get("timestamp"), "error_message": "PGVector port reachable." if pgvector_probe.get("ok") else "PGVector was not reachable or not running in this workspace.", "root_cause_chain": cause, "metrics": [{"label": "TCP latency", "value": f"{pgvector_probe.get('latency_ms')}ms"}], "logs": [{"timestamp": pgvector_probe.get("timestamp"), "level": "INFO" if pgvector_probe.get("ok") else "WARN", "message": f"pgvector tcp ok={pgvector_probe.get('ok')} latency_ms={pgvector_probe.get('latency_ms')}"}]})
    if not any(n.get("id") == "prometheus" for n in nodes):
        nodes.append({"id": "prometheus", "zone": "runtime", "name": "Prometheus", "role": "Metrics scraping endpoint probe", "status": "ok" if prometheus_probe.get("ok") else "warn", "timestamp": prometheus_probe.get("timestamp"), "error_message": "Prometheus health endpoint reachable." if prometheus_probe.get("ok") else "Prometheus was not reachable or not running in this workspace.", "root_cause_chain": cause, "metrics": [{"label": "Health latency", "value": f"{prometheus_probe.get('latency_ms')}ms"}], "logs": [{"timestamp": prometheus_probe.get("timestamp"), "level": "INFO" if prometheus_probe.get("ok") else "WARN", "message": f"prometheus health ok={prometheus_probe.get('ok')} latency_ms={prometheus_probe.get('latency_ms')}"}]})

    agent_body = agent_health.get("body") if isinstance(agent_health.get("body"), dict) else {}
    lf_enabled = agent_body.get("langfuse_enabled")
    if not any(n.get("id") == "langfuse" for n in nodes):
        nodes.append({"id": "langfuse", "zone": "runtime", "name": "Langfuse", "role": "LLM trace capture status from agent health", "status": "ok" if lf_enabled else "warn", "timestamp": agent_health.get("timestamp"), "error_message": "Langfuse enabled on agent." if lf_enabled else "Langfuse is not enabled/configured for the agent in this workspace.", "root_cause_chain": cause, "metrics": [{"label": "Enabled", "value": str(bool(lf_enabled)).lower()}], "logs": [{"timestamp": agent_health.get("timestamp"), "level": "INFO" if lf_enabled else "WARN", "message": f"agent health langfuse_enabled={lf_enabled}"}]})

    runtime_apps = [n["name"] for n in nodes if n.get("zone") == "runtime"]
    topology = {
        "timestamp": started.replace(microsecond=0).isoformat(),
        "propagation_label": "CPU contention observed",
        "impact": {"where": "host background job -> Docker runtime -> AI agent", "user_count": len(agent_probes), "applications": runtime_apps, "application_count": len(runtime_apps)},
        "root_cause_chain": cause,
        "recommended_action": action,
        "nodes": nodes,
        "metrics": [
            {"label": "Host CPU", "value": f"{host_cpu_peak:.1f}%", "percent": host_cpu_peak, "tone": tone_for_percent(host_cpu_peak)},
            {"label": "Host Mem", "value": f"{host_mem_peak:.1f}%", "percent": host_mem_peak, "tone": "warn" if host_mem_peak >= 60 else "ok"},
            {"label": "Job CPU", "value": f"{process_cpu_peak:.1f}%", "percent": min(100, process_cpu_peak), "tone": "error" if process_cpu_peak >= 80 else "warn"},
            {"label": "Agent p99", "value": f"{agent_p99:.1f}ms", "percent": min(100, agent_p99 / 40), "tone": "warn" if agent_p99 > max(750, baseline_p99 * 2) else "ok"},
        ],
        "traces": [{"timestamp": p.get("timestamp"), "status": "fail" if not p.get("ok") else "slow" if p.get("latency_ms", 0) > max(750, baseline_p99 * 2) else "ok", "label": "agent.probe"} for p in agent_probes[-4:]],
        "alerts": [{"name": "BackgroundJobHighCPU", "severity": "critical" if process_cpu_peak >= 80 else "warning", "tone": "critical" if process_cpu_peak >= 80 else "warning"}, {"name": "AgentLatencyDegraded", "severity": "warning", "tone": "warning"}],
    }

    evidence = "\n".join([
        f"{started_log['timestamp']} {started_log['message']}",
        f"{utc_now_iso()} process_cpu_peak={process_cpu_peak:.1f}% host_cpu_peak={host_cpu_peak:.1f}% host_mem_peak={host_mem_peak:.1f}%",
        f"{utc_now_iso()} agent_probe_p99_ms={agent_p99:.1f} baseline_p99_ms={baseline_p99:.1f} failed_calls={len(failed_calls)}",
        f"{utc_now_iso()} prometheus_ok={prometheus_probe.get('ok')} pgvector_tcp_ok={pgvector_probe.get('ok')} langfuse_enabled={lf_enabled}",
    ])

    init_db()
    db = SessionLocal()
    base_fp = hashlib.sha256(f"safe-live-topology:{args.scenario_key}".encode()).hexdigest()[:16]
    issue = db.query(Issue).filter(Issue.base_fingerprint == base_fp).first()
    if not issue:
        issue = Issue(fingerprint=base_fp, base_fingerprint=base_fp, recurrence_count=0)
        db.add(issue)
    issue.app_name = app_name
    issue.issue_type = "safe_host_cpu_contention"
    issue.rule_id = "TOPO-LIVE-CPU-CONTENTION"
    issue.severity = severity
    issue.status = "OPEN"
    issue.title = title
    issue.description = desc
    issue.title_en = title
    issue.title_ja = issue_title_ja(title, app_name=app_name, rule_id=issue.rule_id)
    issue.description_en = desc
    issue.description_ja = issue_description_ja(desc, app_name=app_name, rule_id=issue.rule_id)
    issue.span_name = "topology.safe_background_job"
    issue.trace_id = f"safe-topology-{int(started.timestamp())}"
    issue.created_at = started
    issue.updated_at = datetime.utcnow()
    issue.resolved_at = None
    issue.metadata_json = json.dumps({"seed": "safe-live-topology-scenario", "seed_key": args.scenario_key, "cpu_percent": host_cpu_peak, "memory_percent": host_mem_peak, "process_cpu_percent": process_cpu_peak, "topology": topology})
    db.flush()
    analysis = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue.id).first()
    if not analysis:
        analysis = IssueAnalysis(issue_id=issue.id)
        db.add(analysis)
    analysis.generated_at = datetime.utcnow()
    analysis.model_used = "safe-live-topology-agent"
    analysis.status = "done"
    analysis.likely_cause = cause
    analysis.evidence = evidence
    analysis.recommended_action = action
    analysis.remediation_type = "host_process_limit"
    analysis.likely_cause_en = cause
    analysis.evidence_en = evidence
    analysis.recommended_action_en = action
    analysis.likely_cause_ja = issue_description_ja(cause, app_name=app_name, rule_id=issue.rule_id)
    analysis.evidence_ja = issue_description_ja(evidence, app_name=app_name, rule_id=issue.rule_id)
    analysis.recommended_action_ja = issue_description_ja(action, app_name=app_name, rule_id=issue.rule_id)
    analysis.language_status = "ready"
    db.commit()
    correlations = correlate_recent_topology_issues(db, window_minutes=30)
    print(json.dumps({"issue_id": issue.id, "duration_seconds": duration, "job_cpu_peak": round(process_cpu_peak, 1), "host_cpu_peak": round(host_cpu_peak, 1), "agent_p99_ms": round(agent_p99, 1), "containers_discovered": [c.get("name") for c in containers_after], "langfuse_enabled": bool(lf_enabled), "pgvector_reachable": bool(pgvector_probe.get("ok")), "prometheus_reachable": bool(prometheus_probe.get("ok")), "correlations": correlations}))
    db.close()


if __name__ == "__main__":
    main()
