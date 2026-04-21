"""
Reason Analyzer
===============
Generates a structured LLM root-cause analysis for a detected issue by
correlating:
  • Issue metadata (type, severity, description, affected span/trace)
  • System metric snapshots around the time the issue was created
  • Related trace + span data (error messages, durations, token counts)

Uses OpenAI gpt-4o (falls back to rule-based analysis if no OpenAI key).
Analysis is stored in `issue_analyses` and can be retrieved via the API.
"""
import json
import logging
import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv()  # ensure .env is loaded before reading API keys

from sqlalchemy.orm import Session

from server.database.engine import SessionLocal
from server.database.models import Issue, Span, Trace, IssueAnalysis
from server.engine.metrics_collector import get_snapshots_around, get_recent_snapshots

logger = logging.getLogger("aiops.reason_analyzer")

_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
_OPENAI_KEY    = os.getenv("AIOPS_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")


# ── Public API ────────────────────────────────────────────────────────────────

async def analyze_issue(issue_id: int) -> dict:
    """
    Generate (or return cached) root-cause analysis for `issue_id`.
    Returns the IssueAnalysis as a dict.  Idempotent — will not regenerate
    a 'done' analysis unless force=True is used via the API.
    """
    db = SessionLocal()
    try:
        issue = db.query(Issue).filter(Issue.id == issue_id).first()
        if not issue:
            raise ValueError(f"Issue {issue_id} not found")

        # Return cached result if already done
        existing = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue_id).first()
        if existing and existing.status == "done":
            return _analysis_to_dict(existing)

        # Create or reset analysis record
        if not existing:
            existing = IssueAnalysis(issue_id=issue_id, status="pending")
            db.add(existing)
            db.commit()
            db.refresh(existing)

        analysis_id = existing.id
    finally:
        db.close()

    # Run analysis async (non-blocking)
    import asyncio
    asyncio.create_task(_run_analysis(analysis_id, issue_id))

    return {"id": analysis_id, "issue_id": issue_id, "status": "pending"}


async def _run_analysis(analysis_id: int, issue_id: int):
    db = SessionLocal()
    try:
        analysis = db.query(IssueAnalysis).filter(IssueAnalysis.id == analysis_id).first()
        issue    = db.query(Issue).filter(Issue.id == issue_id).first()

        if not analysis or not issue:
            return

        # Never overwrite a result that was already completed by another runner
        # (e.g. the external rca_client pipeline finished first).
        if analysis.status == "done":
            return

        # ── Build context ─────────────────────────────────────────────────────
        context = _build_context(db, issue)

        # Persist snapshot of what we're analyzing
        analysis.context_snapshot_json = json.dumps(context, default=str)
        db.commit()

        # ── Call LLM ──────────────────────────────────────────────────────────
        prompt = _build_prompt(issue, context)
        result = await _call_llm(prompt, context=context)

        # ── Parse and store ───────────────────────────────────────────────────
        analysis.likely_cause        = result.get("likely_cause", "")
        analysis.evidence            = result.get("evidence", "")
        analysis.recommended_action  = result.get("recommended_action", "")
        analysis.remediation_type    = result.get("remediation_type", "")
        analysis.handoff_plan        = result.get("handoff_plan", "")
        analysis.full_summary        = result.get("full_summary", "")
        analysis.model_used          = result.get("model", "unknown")
        analysis.status              = "done"
        analysis.generated_at        = datetime.utcnow()
        db.commit()

        logger.info("Analysis complete for issue #%d using %s", issue_id, analysis.model_used)

    except Exception as exc:
        logger.exception("Analysis failed for issue #%d: %s", issue_id, exc)
        try:
            analysis = db.query(IssueAnalysis).filter(IssueAnalysis.id == analysis_id).first()
            if analysis:
                analysis.status = "failed"
                analysis.full_summary = str(exc)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(db: Session, issue: Issue) -> dict:
    ctx: dict = {}

    # System metrics around the time of issue creation
    metrics = get_snapshots_around(issue.created_at, window_seconds=180)
    if not metrics:
        metrics = get_recent_snapshots(n=18)   # fallback: last 3 min
    ctx["system_metrics"] = _summarise_metrics(metrics)
    ctx["metric_snapshots_count"] = len(metrics)

    # Related trace details
    if issue.trace_id:
        trace = db.query(Trace).filter(Trace.id == issue.trace_id).first()
        if trace:
            ctx["trace"] = {
                "id": trace.id,
                "app_name": trace.app_name,
                "status": trace.status,
                "duration_ms": trace.total_duration_ms,
                "input_preview": (trace.input_preview or "")[:200],
                "output_preview": (trace.output_preview or "")[:200],
            }

    # Error spans in the related trace
    if issue.trace_id:
        error_spans = (
            db.query(Span)
            .filter(Span.trace_id == issue.trace_id, Span.status == "error")
            .all()
        )
        ctx["error_spans"] = [
            {
                "name": s.name,
                "type": s.span_type,
                "duration_ms": s.duration_ms,
                "error_message": (s.error_message or "")[:300],
                "tokens_in": s.tokens_input,
                "tokens_out": s.tokens_output,
            }
            for s in error_spans
        ]

    # Recent error pattern across the app (last 20 traces)
    recent_traces = (
        db.query(Trace)
        .filter(Trace.app_name == issue.app_name)
        .order_by(Trace.started_at.desc())
        .limit(20)
        .all()
    )
    if recent_traces:
        errors = sum(1 for t in recent_traces if t.status == "error")
        durations = [t.total_duration_ms for t in recent_traces if t.total_duration_ms]
        ctx["recent_app_stats"] = {
            "sample_size": len(recent_traces),
            "error_count": errors,
            "error_rate_pct": round(errors / len(recent_traces) * 100, 1),
            "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
            "max_duration_ms": max(durations) if durations else None,
        }

    return ctx


def _summarise_metrics(snapshots: list[dict]) -> dict:
    """Compress a list of metric snapshots into min/avg/max summary per field."""
    if not snapshots:
        return {}

    def _stats(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return {"min": round(min(vals), 1), "avg": round(sum(vals) / len(vals), 1), "max": round(max(vals), 1)}

    fields = [
        "cpu_percent", "mem_percent", "swap_percent",
        "disk_read_bytes_sec", "disk_write_bytes_sec",
        "net_bytes_sent_sec", "net_bytes_recv_sec",
        "net_active_connections",
    ]

    summary = {}
    for f in fields:
        stats = _stats([s.get(f) for s in snapshots])
        if stats:
            summary[f] = stats

    # Timestamps of first and last sample
    summary["window_start"] = snapshots[0].get("collected_at")
    summary["window_end"]   = snapshots[-1].get("collected_at")
    summary["sample_count"] = len(snapshots)

    return summary


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_prompt(issue: Issue, context: dict) -> str:
    metrics = context.get("system_metrics", {})
    trace   = context.get("trace", {})
    errors  = context.get("error_spans", [])
    stats   = context.get("recent_app_stats", {})
    _sev_labels = {"critical": "SEV1", "high": "SEV2", "medium": "SEV3", "low": "SEV4"}
    sev_label = _sev_labels.get(issue.severity, "")

    def _fmt_metric(label, key, unit=""):
        m = metrics.get(key)
        if not m:
            return f"  {label}: N/A"
        return f"  {label}: min={m['min']}{unit}  avg={m['avg']}{unit}  max={m['max']}{unit}"

    metrics_section = "\n".join([
        _fmt_metric("CPU usage",        "cpu_percent",         "%"),
        _fmt_metric("Memory usage",     "mem_percent",         "%"),
        _fmt_metric("Swap usage",       "swap_percent",        "%"),
        _fmt_metric("Disk read",        "disk_read_bytes_sec", " B/s"),
        _fmt_metric("Disk write",       "disk_write_bytes_sec"," B/s"),
        _fmt_metric("Net sent",         "net_bytes_sent_sec",  " B/s"),
        _fmt_metric("Net recv",         "net_bytes_recv_sec",  " B/s"),
        _fmt_metric("TCP connections",  "net_active_connections",""),
    ])

    error_section = ""
    if errors:
        error_section = "\n### Error Spans\n" + "\n".join(
            f"  - [{e['type']}] {e['name']} | {e['duration_ms']}ms | {e['error_message']}"
            for e in errors[:5]
        )

    trace_section = ""
    if trace:
        trace_section = (
            f"\n### Related Trace\n"
            f"  - Status: {trace.get('status')}  Duration: {trace.get('duration_ms')}ms\n"
            f"  - Input: {trace.get('input_preview')}\n"
            f"  - Output: {trace.get('output_preview')}"
        )

    app_stats_section = ""
    if stats:
        app_stats_section = (
            f"\n### Recent App Performance ({stats.get('sample_size')} traces)\n"
            f"  - Error rate: {stats.get('error_rate_pct')}%  "
            f"Avg latency: {stats.get('avg_duration_ms')}ms  "
            f"Max latency: {stats.get('max_duration_ms')}ms"
        )

    return f"""You are an expert AIOps engineer performing root-cause analysis on a detected system issue.

## Detected Issue
- App: {issue.app_name}
- Type: {issue.issue_type}
- Severity: {issue.severity} ({sev_label})
- Title: {issue.title}
- Description: {issue.description or "No description"}
- Rule: {issue.rule_id or "N/A"}
- Detected at: {issue.created_at}
{f"- Affected span: {issue.span_name}" if issue.span_name else ""}

## System Metrics (±3 min around issue)
{metrics_section}
{trace_section}
{error_section}
{app_stats_section}

## Your Task
Analyse the above telemetry and produce a concise root-cause analysis.
Respond in this exact JSON format (no markdown fences, pure JSON):

{{
  "likely_cause": "<one-sentence most probable root cause>",
  "evidence": "<2-4 bullet points citing specific metric values or error messages that support the diagnosis>",
  "recommended_action": "<concrete next step the on-call engineer should take>",
  "remediation_type": "code_change | config_change | infra_change | runbook_change | investigation_only | human_handoff",
  "handoff_plan": "<if remediation_type is infra_change or human_handoff, provide exact operator steps, validation checks, and rollback guidance; otherwise empty string>",
  "confidence": "high | medium | low"
}}"""


# ── LLM caller ────────────────────────────────────────────────────────────────

async def _call_llm(prompt: str, context: dict = None) -> dict:
    """Try OpenAI, then rule-based fallback."""
    if _OPENAI_KEY:
        try:
            return await _call_openai(prompt)
        except Exception as e:
            logger.warning("OpenAI call failed (%s) — using rule-based analysis", str(e)[:80])
    # Rule-based fallback — no LLM credits needed
    return _rule_based_analysis(context or {})


async def _call_anthropic(prompt: str) -> dict:
    import asyncio
    import anthropic

    def _sync_call():
        client = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text

    raw = await asyncio.to_thread(_sync_call)
    return _parse_llm_response(raw, model="claude-sonnet-4-6")


async def _call_openai(prompt: str) -> dict:
    import asyncio
    from openai import OpenAI

    def _sync_call():
        client = OpenAI(api_key=_OPENAI_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content

    raw = await asyncio.to_thread(_sync_call)
    return _parse_llm_response(raw, model="gpt-4o")


def _rule_based_analysis(context: dict) -> dict:
    """
    Heuristic root-cause analysis based on metric thresholds.
    Used when no LLM API credits are available.
    """
    metrics  = context.get("system_metrics", {})
    stats    = context.get("recent_app_stats", {})
    errors   = context.get("error_spans", [])

    causes   = []
    evidence = []
    actions  = []
    remediation_type = "investigation_only"
    handoff_plan = ""

    def _avg(key):
        m = metrics.get(key)
        return m["avg"] if m else None

    def _max(key):
        m = metrics.get(key)
        return m["max"] if m else None

    cpu_avg = _avg("cpu_percent")
    mem_avg = _avg("mem_percent")
    disk_w  = _avg("disk_write_bytes_sec")
    net_r   = _avg("net_bytes_recv_sec")
    conns   = _avg("net_active_connections")
    err_rate = stats.get("error_rate_pct", 0)
    avg_dur  = stats.get("avg_duration_ms")

    # CPU pressure
    if cpu_avg and cpu_avg > 85:
        causes.append(f"High CPU utilization ({cpu_avg:.1f}% avg) is throttling agent processing")
        evidence.append(f"• CPU avg {cpu_avg:.1f}%, peak {_max('cpu_percent'):.1f}% during issue window")
        actions.append("Scale up compute or reduce parallel agent concurrency")
        remediation_type = "infra_change"

    # Memory pressure
    if mem_avg and mem_avg > 85:
        causes.append(f"Memory pressure ({mem_avg:.1f}% used) causing GC pauses or OOM risk")
        evidence.append(f"• Memory avg {mem_avg:.1f}%, peak {_max('mem_percent'):.1f}%")
        actions.append("Check for memory leaks; consider increasing heap or restarting the agent")
        remediation_type = "infra_change"

    # High error rate
    if err_rate and err_rate > 20:
        causes.append(f"Elevated error rate ({err_rate}%) across recent traces suggests upstream failures")
        evidence.append(f"• {err_rate}% of last {stats.get('sample_size', '?')} traces ended in error")
        if errors:
            sample_err = errors[0].get("error_message", "")[:120]
            evidence.append(f"• Error sample: {sample_err}")
        actions.append("Check upstream LLM / tool API status; review error messages in trace spans")
        remediation_type = "config_change"

    # Network saturation
    if net_r and net_r > 10_000_000:  # 10 MB/s
        mb = net_r / 1_048_576
        causes.append(f"High network receive rate ({mb:.1f} MB/s) may indicate payload bloat")
        evidence.append(f"• Net recv avg {mb:.1f} MB/s during issue window")
        actions.append("Review response payload sizes; check for large context windows being transferred")

    # High TCP connections
    if conns and conns > 200:
        causes.append(f"High number of active TCP connections ({conns:.0f}) suggests connection pool pressure")
        evidence.append(f"• {conns:.0f} active connections observed")
        actions.append("Tune connection pool size; check for connection leaks")
        remediation_type = "config_change"

    # Slow avg duration
    if avg_dur and avg_dur > 8000:
        causes.append(f"Agent average response time ({avg_dur:.0f}ms) is abnormally high")
        evidence.append(f"• Avg trace duration {avg_dur:.0f}ms, max {stats.get('max_duration_ms','?')}ms")
        actions.append("Profile slow spans; check LLM timeout config and tool response times")
        if remediation_type == "investigation_only":
            remediation_type = "config_change"

    # No specific signal found
    if not causes:
        causes.append("No dominant system-level cause detected from available metrics")
        evidence.append("• CPU, memory, and network metrics are within normal ranges")
        evidence.append("• The issue may stem from upstream API instability or application logic")
        actions.append("Review trace-level error messages and check upstream LLM / tool API status pages")

    if remediation_type == "infra_change":
        handoff_plan = (
            "1. Confirm current deployment capacity, worker count, CPU/memory limits, and autoscaling settings for the affected service.\n"
            "2. If a repo-managed IaC or Helm value exists, open a PR to raise replicas/resources within approved limits; otherwise have the platform owner apply the scaling change in the target environment.\n"
            "3. Validate by rerunning the concurrent-load test and confirming p95 latency and error rate return below Sev thresholds.\n"
            "4. Roll back by restoring the previous replica/resource values if saturation remains or cost/risk exceeds the approved window."
        )
    elif remediation_type == "config_change":
        handoff_plan = (
            "Repo-managed config change is preferred. If the runtime config is not accessible to Codex, ask the service owner to update worker, timeout, or connection-pool settings, then rerun the same load validation and keep the prior values ready for rollback."
        )

    return {
        "likely_cause":       causes[0],
        "evidence":           "\n".join(evidence),
        "recommended_action": actions[0] if actions else "Investigate trace-level errors.",
        "remediation_type":    remediation_type,
        "handoff_plan":        handoff_plan,
        "full_summary":       f"Rule-based analysis (no LLM credits). Causes identified: {len(causes)}.",
        "model":              "rule-based",
    }


def _parse_llm_response(raw: str, model: str) -> dict:
    """Parse JSON from LLM response; gracefully handle non-JSON output."""
    result = {"full_summary": raw, "model": model}
    try:
        # Strip markdown code fences if present
        clean = raw.strip()
        if clean.startswith("```"):
            clean = "\n".join(clean.split("\n")[1:])
            clean = clean.rstrip("`").strip()
        data = json.loads(clean)
        result["likely_cause"]       = data.get("likely_cause", "")
        result["evidence"]           = data.get("evidence", "")
        result["recommended_action"] = data.get("recommended_action", "")
        result["remediation_type"]   = data.get("remediation_type", "")
        result["handoff_plan"]       = data.get("handoff_plan", "")
    except (json.JSONDecodeError, KeyError):
        # LLM returned free text — use it as full_summary
        result["likely_cause"]       = raw[:300]
        result["evidence"]           = ""
        result["recommended_action"] = "Review the full summary for details."
        result["remediation_type"]   = "investigation_only"
        result["handoff_plan"]       = ""
    return result


# ── Dict serialiser ───────────────────────────────────────────────────────────

def _analysis_to_dict(a: IssueAnalysis) -> dict:
    return {
        "id": a.id,
        "issue_id": a.issue_id,
        "status": a.status,
        "model_used": a.model_used,
        "generated_at": a.generated_at.isoformat() if a.generated_at else None,
        "likely_cause": a.likely_cause,
        "evidence": a.evidence,
        "recommended_action": a.recommended_action,
        "remediation_type": a.remediation_type,
        "handoff_plan": a.handoff_plan,
        "full_summary": a.full_summary,
    }


def get_analysis(issue_id: int) -> Optional[dict]:
    db = SessionLocal()
    try:
        a = db.query(IssueAnalysis).filter(IssueAnalysis.issue_id == issue_id).first()
        return _analysis_to_dict(a) if a else None
    finally:
        db.close()
