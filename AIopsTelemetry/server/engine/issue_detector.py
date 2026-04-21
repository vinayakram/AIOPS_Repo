"""
NFR-based issue detector.

Rules sourced from non_functional_requirements.md (23 March 2026).
Severity mapping:  SEV1 → critical  |  SEV2 → high  |  SEV3 → medium
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from server.config import settings
from server.database.models import Trace, Span, Issue, EscalationRule

logger = logging.getLogger("aiops.issue_detector")

# Set once per detect_issues() call; None = rules not yet seeded (allow all)
_configured_nfr_ids: set[str] | None = None
_enabled_nfr_ids: set[str] | None = None

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}

# ── public entry point ────────────────────────────────────────────────────────

def detect_issues(db: Session) -> list[Issue]:
    """Run all NFR detectors. Returns newly created issues."""
    global _configured_nfr_ids, _enabled_nfr_ids
    # Load enabled NFR rule IDs from the EscalationRule table.
    # If no NFR rules are seeded yet, _enabled_nfr_ids stays None (all allowed).
    nfr_rows = (
        db.query(EscalationRule.nfr_id)
        .filter(EscalationRule.nfr_id.isnot(None))
        .all()
    )
    if nfr_rows:
        _configured_nfr_ids = {r[0] for r in nfr_rows}
        _enabled_nfr_ids = {
            r[0] for r in
            db.query(EscalationRule.nfr_id)
            .filter(EscalationRule.nfr_id.isnot(None), EscalationRule.enabled == True)
            .all()
        }
    else:
        _configured_nfr_ids = None
        _enabled_nfr_ids = None

    created = []
    window = settings.NFR_CHECK_WINDOW_MINUTES

    # Section 1 — Health & Availability
    created.extend(_detect_consecutive_trace_failures(db))         # NFR-2 / NFR-5
    created.extend(_detect_http_error_rate(db, window))            # NFR-8 / NFR-8a
    created.extend(_detect_exception_count_spike(db, window))      # NFR-9
    created.extend(_detect_response_time_with_llm(db, window))     # NFR-7 / NFR-7a
    created.extend(_detect_p95_response_time_under_load(db, window))  # NFR-7p95 / NFR-7p95a

    # Section 2 — Infrastructure
    # psutil-based host-metric detectors disabled on feature/rca-external-service
    # (they generate constant noise on dev machines and are unrelated to the RCA demo)
    # created.extend(_detect_cpu(db))                              # NFR-11 / NFR-11a
    # created.extend(_detect_memory(db))                           # NFR-12 / NFR-13
    # created.extend(_detect_storage(db))                          # NFR-14 / NFR-14a
    created.extend(_detect_pod_resource_threshold_breaches(db, window))  # NFR-33

    # Section 3 — AI & System
    created.extend(_detect_execution_time_drift(db))               # NFR-19
    created.extend(_detect_consecutive_ai_failures(db))            # NFR-22 / NFR-22a
    created.extend(_detect_genai_failure_rate(db, window))         # NFR-24 / NFR-24a
    created.extend(_detect_timeout_rate(db, window))               # NFR-25 / NFR-25a
    created.extend(_detect_token_spike(db))                        # NFR-26

    # Section 4 — Application-level output errors
    created.extend(_detect_output_errors(db, window))             # NFR-29
    created.extend(_detect_llm_disabled_query_burst(db))          # NFR-31
    created.extend(_detect_llm_rate_limit_errors(db, window))     # NFR-32
    created.extend(_detect_special_character_query_failures(db, window))  # NFR-30

    # Legacy detectors (kept for backwards compat)
    # _detect_high_latency disabled on feature/rca-external-service — fires continuously
    # from accumulated historical span data and masks the NFR-7 latency signal we care about
    # created.extend(_detect_high_latency(db))
    created.extend(_detect_error_spikes(db))

    return created


# ── Section 1 — Health & Availability ────────────────────────────────────────

def _detect_consecutive_trace_failures(db: Session) -> list[Issue]:
    """NFR-2 / NFR-5: 3 consecutive trace failures → SEV1."""
    created = []
    apps = [r[0] for r in db.query(Trace.app_name).distinct().all()]
    for app in apps:
        recent = (
            db.query(Trace.id, Trace.status)
            .filter(Trace.app_name == app)
            .order_by(Trace.started_at.desc())
            .limit(3)
            .all()
        )
        if len(recent) == 3 and all(r[1] == "error" for r in recent):
            # Keep a representative failing trace on the issue so RCA can call
            # the external service instead of falling back to reason_analyzer.
            representative_trace_id = recent[0][0]
            issue = _ensure_issue(
                db,
                app_name=app,
                rule_id="NFR-2",
                issue_type="nfr_consecutive_failures",
                severity="critical",
                title=f"3 consecutive trace failures in {app}",
                description="Last 3 traces all ended with status=error",
                trace_id=representative_trace_id,
            )
            if issue:
                created.append(issue)
    return created


def _detect_http_error_rate(db: Session, window_mins: int) -> list[Issue]:
    """NFR-8: ≥1% 5xx rate for window → SEV2; NFR-8a: ≥5% → SEV1."""
    created = []
    # Give the demo flow enough time for the user to trigger the error, open
    # telemetry, and run RCA/remediation without the occurrence falling out of
    # the short generic NFR polling window.
    cutoff = datetime.utcnow() - timedelta(minutes=max(window_mins, 180))
    apps = [r[0] for r in db.query(Trace.app_name).distinct().all()]
    for app in apps:
        rows = (
            db.query(Trace.status)
            .filter(Trace.app_name == app, Trace.started_at >= cutoff)
            .all()
        )
        total = len(rows)
        if total < 10:
            continue
        errors = sum(1 for r in rows if r[0] == "error")
        rate = errors / total
        if rate >= 0.05:
            issue = _ensure_issue(
                db, app_name=app, rule_id="NFR-8a",
                issue_type="nfr_http_error_rate",
                severity="critical",
                title=f"HTTP error rate ≥5% in {app}",
                description=f"{errors}/{total} traces failed ({rate*100:.1f}%) in last {window_mins} min",
            )
            if issue:
                created.append(issue)
        elif rate >= 0.01:
            issue = _ensure_issue(
                db, app_name=app, rule_id="NFR-8",
                issue_type="nfr_http_error_rate",
                severity="high",
                title=f"HTTP error rate ≥1% in {app}",
                description=f"{errors}/{total} traces failed ({rate*100:.1f}%) in last {window_mins} min",
            )
            if issue:
                created.append(issue)
    return created


def _detect_exception_count_spike(db: Session, window_mins: int) -> list[Issue]:
    """NFR-9: Recent exception count 2x vs previous window → SEV3.

    The previous implementation compared all current-week errors to last week.
    Demo incidents can leave that condition true for days, so every resolved
    NFR-9 immediately reappeared. Use adjacent rolling windows and ignore the
    synthetic pod-threshold traces, which are handled by the pod-specific rule.
    """
    created = []
    now = datetime.utcnow()
    window = timedelta(minutes=max(window_mins, 10))
    current_start = now - window
    previous_start = current_start - window
    apps = [r[0] for r in db.query(Trace.app_name).distinct().all()]
    for app in apps:
        current_q = db.query(Trace).filter(
            Trace.app_name == app, Trace.status == "error",
            Trace.started_at >= current_start,
        )
        previous_q = db.query(Trace).filter(
            Trace.app_name == app, Trace.status == "error",
            Trace.started_at >= previous_start,
            Trace.started_at < current_start,
        )

        if app == "medical-rag":
            current_q = current_q.filter(
                ~Trace.id.like("pod-threshold-%"),
                ~func.coalesce(Trace.metadata_json, "").contains("pod_threshold_breach"),
            )
            previous_q = previous_q.filter(
                ~Trace.id.like("pod-threshold-%"),
                ~func.coalesce(Trace.metadata_json, "").contains("pod_threshold_breach"),
            )

        current = current_q.count()
        previous = previous_q.count()
        if previous > 0 and current >= previous * 2 and current >= 5:
            issue = _ensure_issue(
                db, app_name=app, rule_id="NFR-9",
                issue_type="nfr_exception_count",
                severity="medium",
                title=f"Exception count doubled in {app}",
                description=(
                    f"Recent window: {current} errors vs previous window: "
                    f"{previous} errors over {int(window.total_seconds() // 60)} min "
                    "(2x increase)"
                ),
            )
            if issue:
                created.append(issue)
    return created


def _detect_response_time_with_llm(db: Session, window_mins: int) -> list[Issue]:
    """NFR-7: Response time target exceeded for window → SEV2; NFR-7a: 2x target → SEV1.

    The most recently ingested trace for the offending app is stored as the
    representative trace_id on the issue so the external RCA service can look it
    up in Langfuse.
    """
    created = []
    # Give the demo flow enough time for the user to trigger the error, open
    # telemetry, and run RCA/remediation without the occurrence falling out of
    # the short generic NFR polling window.
    cutoff = datetime.utcnow() - timedelta(minutes=max(window_mins, 180))
    target = settings.NFR_RESPONSE_TIME_TARGET_MS
    apps = [r[0] for r in db.query(Trace.app_name).distinct().all()]
    for app in apps:
        rows = (
            db.query(Trace.id, Trace.total_duration_ms)
            .filter(Trace.app_name == app, Trace.started_at >= cutoff,
                    Trace.total_duration_ms != None)
            .order_by(Trace.started_at.desc())
            .all()
        )
        if len(rows) < 5:
            continue
        avg = sum(r[1] for r in rows) / len(rows)
        # Most recent trace acts as the representative for RCA lookup
        representative_trace_id = rows[0][0]
        if avg >= target * 2:
            issue = _ensure_issue(
                db, app_name=app, rule_id="NFR-7a",
                issue_type="nfr_response_time",
                severity="critical",
                title=f"Response time 2x target in {app}",
                description=f"Avg {avg:.0f}ms exceeds 2x target ({target:.0f}ms) over last {window_mins} min",
                trace_id=representative_trace_id,
            )
            if issue:
                created.append(issue)
        elif avg >= target:
            issue = _ensure_issue(
                db, app_name=app, rule_id="NFR-7",
                issue_type="nfr_response_time",
                severity="high",
                title=f"Response time exceeds target in {app}",
                description=f"Avg {avg:.0f}ms exceeds target ({target:.0f}ms) over last {window_mins} min",
                trace_id=representative_trace_id,
            )
            if issue:
                created.append(issue)
    return created


def _detect_p95_response_time_under_load(db: Session, window_mins: int) -> list[Issue]:
    """NFR-7p95: p95 latency breach during concurrent-load windows.

    This supports the production demo where many users hit an app at once. p95 is
    a better user-impact signal than a simple average because it catches the slow
    tail that makes the application feel unavailable.
    """
    created = []
    cutoff = datetime.utcnow() - timedelta(minutes=max(window_mins, 180))
    target = settings.NFR_RESPONSE_TIME_TARGET_MS
    apps = [r[0] for r in db.query(Trace.app_name).distinct().all()]
    for app in apps:
        rows = (
            db.query(Trace.id, Trace.total_duration_ms, Trace.metadata_json)
            .filter(
                Trace.app_name == app,
                Trace.started_at >= cutoff,
                Trace.total_duration_ms != None,
            )
            .order_by(Trace.started_at.desc())
            .all()
        )
        if len(rows) < 20:
            continue

        durations = sorted(float(r[1]) for r in rows if r[1] is not None)
        if not durations:
            continue

        idx = max(0, min(len(durations) - 1, int(len(durations) * 0.95) - 1))
        p95 = durations[idx]
        representative_trace_id = rows[0][0]
        load_marker_count = sum(
            1
            for _trace_id, _duration, metadata_json in rows[:100]
            if metadata_json and "load_test" in metadata_json
        )
        load_context = (
            f"Detected {len(rows)} traces in the load window"
            + (f"; {load_marker_count} include load-test metadata" if load_marker_count else "")
        )

        if p95 >= target * 2:
            issue = _ensure_issue(
                db,
                app_name=app,
                rule_id="NFR-7p95a",
                issue_type="nfr_p95_response_time_under_load",
                severity="critical",
                title=f"p95 response time 2x target under load in {app}",
                description=(
                    f"p95 {p95:.0f}ms exceeds 2x target ({target:.0f}ms). "
                    f"{load_context}."
                ),
                trace_id=representative_trace_id,
            )
            if issue:
                created.append(issue)
        elif p95 >= target:
            issue = _ensure_issue(
                db,
                app_name=app,
                rule_id="NFR-7p95",
                issue_type="nfr_p95_response_time_under_load",
                severity="high",
                title=f"p95 response time exceeds target under load in {app}",
                description=(
                    f"p95 {p95:.0f}ms exceeds target ({target:.0f}ms). "
                    f"{load_context}."
                ),
                trace_id=representative_trace_id,
            )
            if issue:
                created.append(issue)
    return created


# ── Section 2 — Infrastructure ────────────────────────────────────────────────

def _detect_cpu(db: Session) -> list[Issue]:
    """NFR-11: CPU ≥80% → SEV2; NFR-11a: CPU ≥95% → SEV1."""
    created = []
    cpu = psutil.cpu_percent(interval=1)
    app = "system"
    if cpu >= 95:
        issue = _ensure_issue(
            db, app_name=app, rule_id="NFR-11a",
            issue_type="nfr_cpu",
            severity="critical",
            title="CPU utilization ≥95%",
            description=f"Current CPU: {cpu:.1f}%",
        )
        if issue:
            created.append(issue)
    elif cpu >= 80:
        issue = _ensure_issue(
            db, app_name=app, rule_id="NFR-11",
            issue_type="nfr_cpu",
            severity="high",
            title="CPU utilization ≥80%",
            description=f"Current CPU: {cpu:.1f}%",
        )
        if issue:
            created.append(issue)
    return created


def _detect_memory(db: Session) -> list[Issue]:
    """NFR-12: Memory ≥80% → SEV2; NFR-13: Memory pressure (>90%) → SEV1."""
    created = []
    mem = psutil.virtual_memory()
    pct = mem.percent
    app = "system"
    if pct >= 90:
        issue = _ensure_issue(
            db, app_name=app, rule_id="NFR-13",
            issue_type="nfr_memory_pressure",
            severity="critical",
            title="Memory pressure detected",
            description=f"Memory utilization: {pct:.1f}% (available: {mem.available // 1024 // 1024} MB)",
        )
        if issue:
            created.append(issue)
    elif pct >= 80:
        issue = _ensure_issue(
            db, app_name=app, rule_id="NFR-12",
            issue_type="nfr_memory",
            severity="high",
            title="Memory utilization ≥80%",
            description=f"Memory utilization: {pct:.1f}% (available: {mem.available // 1024 // 1024} MB)",
        )
        if issue:
            created.append(issue)
    return created


def _detect_storage(db: Session) -> list[Issue]:
    """NFR-14: Storage ≥80% → SEV3; NFR-14a: Storage ≥90% → SEV2."""
    created = []
    disk = psutil.disk_usage("/")
    pct = disk.percent
    app = "system"
    if pct >= 90:
        issue = _ensure_issue(
            db, app_name=app, rule_id="NFR-14a",
            issue_type="nfr_storage",
            severity="high",
            title="Storage utilization ≥90%",
            description=f"Disk usage: {pct:.1f}% (free: {disk.free // 1024 // 1024 // 1024} GB)",
        )
        if issue:
            created.append(issue)
    elif pct >= 80:
        issue = _ensure_issue(
            db, app_name=app, rule_id="NFR-14",
            issue_type="nfr_storage",
            severity="medium",
            title="Storage utilization ≥80%",
            description=f"Disk usage: {pct:.1f}% (free: {disk.free // 1024 // 1024 // 1024} GB)",
        )
        if issue:
            created.append(issue)
    return created


# ── Section 3 — AI & System ───────────────────────────────────────────────────

def _detect_pod_resource_threshold_breaches(db: Session, window_mins: int) -> list[Issue]:
    """NFR-33: 3 MedicalAgent pod resource-guard breaches → SEV1."""
    created = []
    cutoff = datetime.utcnow() - timedelta(minutes=max(window_mins, 5))
    rows = (
        db.query(
            Trace.id,
            Trace.output_preview,
            Trace.metadata_json,
            Span.error_message,
            Span.started_at,
        )
        .join(Span, Span.trace_id == Trace.id)
        .filter(
            Trace.app_name == "medical-rag",
            Trace.started_at >= cutoff,
            Span.name == "pod_resource_guard",
            Span.status == "error",
        )
        .order_by(Span.started_at.desc())
        .limit(20)
        .all()
    )
    if len(rows) < 3:
        return created

    latest_trace_id, output_preview, metadata_json, error_message, _started_at = rows[0]
    metadata = {}
    if metadata_json:
        try:
            metadata = json.loads(metadata_json)
        except (json.JSONDecodeError, TypeError):
            metadata = {}

    cpu = metadata.get("cpu_percent")
    cpu_threshold = metadata.get("cpu_threshold_percent")
    mem = metadata.get("memory_percent")
    mem_threshold = metadata.get("memory_threshold_percent")
    evidence = (error_message or output_preview or "application is not reachable").replace("\n", " ")[:260]
    metric_bits = []
    if cpu is not None:
        metric_bits.append(f"CPU {float(cpu):.1f}% / threshold {float(cpu_threshold or 0):.1f}%")
    if mem is not None:
        metric_bits.append(f"memory {float(mem):.1f}% / threshold {float(mem_threshold or 0):.1f}%")
    metrics_text = "; ".join(metric_bits) or "pod resource threshold exceeded"

    issue = _ensure_issue(
        db,
        app_name="medical-rag",
        rule_id="NFR-33",
        issue_type="nfr_pod_resource_threshold_breach",
        severity="critical",
        title="Medical RAG pod resource threshold breached 3 times",
        description=(
            "Medical RAG returned 'application is not reachable' after the pod "
            f"resource guard breached 3 times in the last 5 minutes. {metrics_text}. "
            "RCA should review Langfuse and Prometheus data for the last 5 minutes "
            "and recommend changing the pod threshold config "
            "(POD_CPU_THRESHOLD_PERCENT / POD_MEMORY_THRESHOLD_PERCENT). "
            f"Latest evidence: {evidence}"
        ),
        span_name="pod_resource_guard",
        trace_id=latest_trace_id,
    )
    if issue:
        created.append(issue)
    return created

def _detect_execution_time_drift(db: Session) -> list[Issue]:
    """NFR-19: Execution time +20% above expected → SEV2."""
    created = []
    apps = [r[0] for r in db.query(Trace.app_name).distinct().all()]
    for app in apps:
        # Baseline: older half of all completed traces
        all_durations = (
            db.query(Trace.total_duration_ms)
            .filter(Trace.app_name == app, Trace.total_duration_ms != None)
            .order_by(Trace.started_at)
            .all()
        )
        durations = [r[0] for r in all_durations]
        if len(durations) < 20:
            continue
        mid = len(durations) // 2
        baseline_avg = sum(durations[:mid]) / mid
        recent_avg = sum(durations[mid:]) / len(durations[mid:])
        if baseline_avg > 0 and recent_avg > baseline_avg * 1.2:
            pct_over = (recent_avg / baseline_avg - 1) * 100
            issue = _ensure_issue(
                db, app_name=app, rule_id="NFR-19",
                issue_type="nfr_execution_time_drift",
                severity="high",
                title=f"Execution time +{pct_over:.0f}% above baseline in {app}",
                description=f"Recent avg {recent_avg:.0f}ms vs baseline {baseline_avg:.0f}ms (+{pct_over:.0f}%)",
            )
            if issue:
                created.append(issue)
    return created


def _detect_consecutive_ai_failures(db: Session) -> list[Issue]:
    """NFR-22: 5 consecutive LLM span failures → SEV2; NFR-22a: 10 → SEV1."""
    created = []
    for threshold, rule_id, sev in [(10, "NFR-22a", "critical"), (5, "NFR-22", "high")]:
        rows = (
            db.query(Span.trace_id, Span.status)
            .filter(Span.span_type == "llm")
            .order_by(Span.started_at.desc())
            .limit(threshold)
            .all()
        )
        if len(rows) == threshold and all(r[1] == "error" for r in rows):
            issue = _ensure_issue(
                db, app_name="all",
                rule_id=rule_id,
                issue_type="nfr_consecutive_ai_failures",
                severity=sev,
                title=f"{threshold} consecutive LLM call failures",
                description=f"Last {threshold} LLM spans all returned status=error",
            )
            if issue:
                created.append(issue)
            break  # only raise the higher severity
    return created


def _detect_genai_failure_rate(db: Session, window_mins: int) -> list[Issue]:
    """NFR-24: GenAI failure ≥3% → SEV2; NFR-24a: ≥10% → SEV1."""
    created = []
    cutoff = datetime.utcnow() - timedelta(minutes=window_mins)
    rows = (
        db.query(Span.status)
        .filter(Span.span_type == "llm", Span.started_at >= cutoff)
        .all()
    )
    total = len(rows)
    if total < 5:
        return created
    errors = sum(1 for r in rows if r[0] == "error")
    rate = errors / total
    if rate >= 0.10:
        issue = _ensure_issue(
            db, app_name="all", rule_id="NFR-24a",
            issue_type="nfr_genai_failure_rate",
            severity="critical",
            title="GenAI call failure rate ≥10%",
            description=f"{errors}/{total} LLM calls failed ({rate*100:.1f}%) in last {window_mins} min",
        )
        if issue:
            created.append(issue)
    elif rate >= 0.03:
        issue = _ensure_issue(
            db, app_name="all", rule_id="NFR-24",
            issue_type="nfr_genai_failure_rate",
            severity="high",
            title="GenAI call failure rate ≥3%",
            description=f"{errors}/{total} LLM calls failed ({rate*100:.1f}%) in last {window_mins} min",
        )
        if issue:
            created.append(issue)
    return created


def _detect_timeout_rate(db: Session, window_mins: int) -> list[Issue]:
    """NFR-25: Timeout rate ≥3% → SEV2; NFR-25a: ≥10% → SEV1.
    Detected via error_message containing 'timeout'."""
    created = []
    cutoff = datetime.utcnow() - timedelta(minutes=window_mins)
    rows = (
        db.query(Span.status, Span.error_message)
        .filter(Span.started_at >= cutoff)
        .all()
    )
    total = len(rows)
    if total < 10:
        return created
    timeouts = sum(
        1 for r in rows
        if r[0] == "error" and r[1] and "timeout" in r[1].lower()
    )
    rate = timeouts / total
    if rate >= 0.10:
        issue = _ensure_issue(
            db, app_name="all", rule_id="NFR-25a",
            issue_type="nfr_timeout_rate",
            severity="critical",
            title="Timeout rate ≥10%",
            description=f"{timeouts}/{total} spans timed out ({rate*100:.1f}%) in last {window_mins} min",
        )
        if issue:
            created.append(issue)
    elif rate >= 0.03:
        issue = _ensure_issue(
            db, app_name="all", rule_id="NFR-25",
            issue_type="nfr_timeout_rate",
            severity="high",
            title="Timeout rate ≥3%",
            description=f"{timeouts}/{total} spans timed out ({rate*100:.1f}%) in last {window_mins} min",
        )
        if issue:
            created.append(issue)
    return created


def _detect_token_spike(db: Session) -> list[Issue]:
    """NFR-26: Average token count +50% vs previous week → SEV3."""
    created = []
    now = datetime.utcnow()
    this_week = now - timedelta(days=7)
    last_week_start = now - timedelta(days=14)

    def _avg_tokens(start, end):
        rows = (
            db.query(Span.tokens_input, Span.tokens_output)
            .filter(Span.span_type == "llm",
                    Span.started_at >= start, Span.started_at < end,
                    Span.tokens_input != None)
            .all()
        )
        if not rows:
            return None
        return sum((r[0] or 0) + (r[1] or 0) for r in rows) / len(rows)

    current_avg = _avg_tokens(this_week, now)
    previous_avg = _avg_tokens(last_week_start, this_week)
    if current_avg and previous_avg and previous_avg > 0:
        if current_avg >= previous_avg * 1.5:
            pct = (current_avg / previous_avg - 1) * 100
            issue = _ensure_issue(
                db, app_name="all", rule_id="NFR-26",
                issue_type="nfr_token_spike",
                severity="medium",
                title="Average token count +50% vs previous week",
                description=f"This week avg {current_avg:.0f} tokens vs last week {previous_avg:.0f} (+{pct:.0f}%)",
            )
            if issue:
                created.append(issue)
    return created


# ── Section 4 — Application-level output errors ───────────────────────────────

# Patterns that indicate a silently-caught error returned inside the output body.
# Checked case-insensitively against Trace.output_preview.
_OUTPUT_ERROR_PATTERNS = [
    "⚠️",                   # app-level warning/error prefix
    "error code:",           # API HTTP error codes  e.g. "Error code: 400"
    "invalid_request_error", # Anthropic/OpenAI API error type
    "invalid_api_key",       # auth failures
    "credit balance is too low",  # Anthropic billing
    "quota exceeded",        # OpenAI quota
    "rate limit exceeded",   # rate limiting
    "error generating response",  # common app error prefix
]

_LLM_DISABLED_OUTPUT = "llm access is currently disabled by admin"
_LLM_DISABLED_CALL_ERROR = "cannot perform llm call"


def _detect_output_errors(db: Session, window_mins: int) -> list[Issue]:
    """NFR-29: Trace status='ok' but output_preview contains a known error pattern.

    Covers the common case where an app catches an exception and returns the
    error text inside the response body instead of failing the trace.
    Severity: high (single occurrence is actionable — the end-user got an error).
    """
    created = []
    cutoff = datetime.utcnow() - timedelta(minutes=window_mins)
    apps = [r[0] for r in db.query(Trace.app_name).distinct().all()]

    for app in apps:
        # Only look at recent traces with a non-null output
        recent = (
            db.query(Trace.output_preview)
            .filter(
                Trace.app_name == app,
                Trace.started_at >= cutoff,
                Trace.output_preview != None,
            )
            .order_by(Trace.started_at.desc())
            .limit(50)
            .all()
        )

        matched_outputs = []
        for (output,) in recent:
            output_lower = output.lower()
            for pattern in _OUTPUT_ERROR_PATTERNS:
                if pattern.lower() in output_lower:
                    matched_outputs.append(output[:200])
                    break

        if not matched_outputs:
            continue

        count = len(matched_outputs)
        severity = "critical" if count >= 5 else "high" if count >= 2 else "medium"
        # Use the first matched snippet as evidence in the description
        snippet = matched_outputs[0].replace("\n", " ")
        issue = _ensure_issue(
            db,
            app_name=app,
            rule_id="NFR-29",
            issue_type="nfr_output_error",
            severity=severity,
            title=f"Application error returned in output by {app}",
            description=(
                f"{count} recent trace(s) returned an error inside the response body "
                f"(status was 'ok' but output contained error indicators). "
                f"Latest: {snippet}"
            ),
        )
        if issue:
            created.append(issue)

    return created


def _detect_llm_disabled_query_burst(db: Session) -> list[Issue]:
    """NFR-31: 3 Medical RAG queries hit disabled LLM fallback within 10 minutes.

    MedicalAgent intentionally returns source articles when the demo LLM toggle is
    disabled, so these traces are stored as status='ok'. This detector treats a
    burst of those fallback responses as an availability issue for the LLM path.
    """
    created = []
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    error_rows = (
        db.query(Trace.id, Trace.input_preview, Span.error_message, Span.started_at)
        .join(Span, Span.trace_id == Trace.id)
        .filter(
            Trace.app_name == "medical-rag",
            Trace.started_at >= cutoff,
            Span.status == "error",
            Span.error_message != None,
            func.lower(Span.error_message).contains(_LLM_DISABLED_OUTPUT),
        )
        .order_by(Span.started_at.desc())
        .limit(1)
        .all()
    )
    if error_rows:
        trace_id, query, error_message, _started_at = error_rows[0]
        msg_l = (error_message or "").lower()
        if _LLM_DISABLED_CALL_ERROR in msg_l or "3 user queries" in msg_l:
            issue = _ensure_trace_scoped_issue(
                db,
                app_name="medical-rag",
                rule_id="NFR-31",
                issue_type="nfr_llm_disabled_burst",
                severity="critical",
                title="LLM disabled for 3 medical-rag queries",
                description=(
                    "Medical RAG raised the disabled-LLM threshold error after "
                    "3 user queries within 10 minutes. "
                    f"Query: {(query or '').replace(chr(10), ' ')[:160]}. "
                    f"Latest error: {(error_message or '').replace(chr(10), ' ')[:240]}"
                ),
                span_name="openai_generation",
                trace_id=trace_id,
            )
            if issue:
                created.append(issue)
            return created

    rows = (
        db.query(Trace.id, Trace.input_preview, Trace.started_at)
        .filter(
            Trace.app_name == "medical-rag",
            Trace.started_at >= cutoff,
            Trace.output_preview != None,
            func.lower(Trace.output_preview).contains(_LLM_DISABLED_OUTPUT),
        )
        .order_by(Trace.started_at.desc())
        .limit(3)
        .all()
    )

    if len(rows) < 3:
        return created

    latest_trace_id = rows[0][0]
    query_examples = [
        (row[1] or "").replace("\n", " ")[:80]
        for row in rows
        if row[1]
    ]
    evidence = "; ".join(query_examples) or "recent user queries"
    issue = _ensure_issue(
        db,
        app_name="medical-rag",
        rule_id="NFR-31",
        issue_type="nfr_llm_disabled_burst",
        severity="critical",
        title="LLM disabled for 3 medical-rag queries",
        description=(
            "3 Medical RAG queries within the last 10 minutes returned the "
            "disabled-LLM fallback response instead of an LLM-generated answer. "
            f"Latest trace: {latest_trace_id}. Queries: {evidence}"
        ),
        span_name="openai_generation",
        trace_id=latest_trace_id,
    )
    if issue:
        created.append(issue)

    return created


def _detect_special_character_query_failures(db: Session, window_mins: int) -> list[Issue]:
    """NFR-30: Medical RAG input preprocessing exception.

    This demo detector intentionally keys off the exception emitted by the
    MedicalAgent. The agent itself only reports a generic preprocessing failure;
    RCA then explains that unsupported special characters are the likely cause.
    """
    created = []
    # Give the demo flow enough time for the user to trigger the error, open
    # telemetry, and run RCA/remediation without the occurrence falling out of
    # the short generic NFR polling window.
    cutoff = datetime.utcnow() - timedelta(minutes=max(window_mins, 180))
    rows = (
        db.query(
            Trace.app_name,
            Trace.id,
            Trace.input_preview,
            Span.name,
            Span.error_message,
            Span.started_at,
        )
        .join(Span, Span.trace_id == Trace.id)
        .filter(
            Trace.app_name == "medical-rag",
            Span.status == "error",
            Span.started_at >= cutoff,
        )
        .order_by(Span.started_at.desc())
        .limit(25)
        .all()
    )

    for app, trace_id, trace_input, span_name, error_message, _started_at in rows:
        msg = error_message or ""
        msg_l = msg.lower()
        is_validation_span = span_name == "query_validation"
        is_special_char_signal = (
            "invalid character sequence" in msg_l
            or "special character" in msg_l
            or "query preprocessing failed" in msg_l
        )
        if not (is_validation_span or is_special_char_signal):
            continue

        snippet = msg.replace("\n", " ")[:240] or "query_validation span failed"
        issue = _ensure_trace_scoped_issue(
            db,
            app_name=app,
            rule_id="NFR-30",
            issue_type="nfr_query_preprocessing_error",
            severity="high",
            title=f"Query preprocessing failed in {app}",
            description=(
                "Medical RAG raised a generic preprocessing exception for user input. "
                "RCA should inspect the failing query path and recommend input "
                "normalization or validation for unsupported special characters. "
                f"Query: {(trace_input or '').replace(chr(10), ' ')[:160]}. "
                f"Latest error: {snippet}"
            ),
            span_name="query_validation",
            trace_id=trace_id,
        )
        if issue:
            created.append(issue)
        break

    return created


def _detect_llm_rate_limit_errors(db: Session, window_mins: int) -> list[Issue]:
    """NFR-32: Medical RAG LLM deployment rate limit error.

    The app emits the actual rate-limit message from the failing LLM span, including
    observed requests in the rolling window and the configured limit. RCA should use
    that error text and Prometheus metrics rather than inventing generic failures.
    """
    created = []
    cutoff = datetime.utcnow() - timedelta(minutes=max(window_mins, 180))
    rows = (
        db.query(
            Trace.app_name,
            Trace.id,
            Trace.input_preview,
            Span.name,
            Span.error_message,
            Span.started_at,
        )
        .join(Span, Span.trace_id == Trace.id)
        .filter(
            Trace.app_name == "medical-rag",
            Span.name == "openai_generation",
            Span.status == "error",
            Span.error_message != None,
            Span.started_at >= cutoff,
        )
        .order_by(Span.started_at.desc())
        .limit(25)
        .all()
    )

    for app, trace_id, trace_input, span_name, error_message, _started_at in rows:
        msg = error_message or ""
        msg_l = msg.lower()
        if (
            "rate limit" not in msg_l
            and "requests/minute" not in msg_l
            and "status=429" not in msg_l
            and "too many requests" not in msg_l
        ):
            continue

        snippet = msg.replace("\n", " ")[:260]
        issue = _ensure_trace_scoped_issue(
            db,
            app_name=app,
            rule_id="NFR-32",
            issue_type="nfr_llm_rate_limit_exceeded",
            severity="high",
            title=f"LLM rate limit exceeded in {app}",
            description=(
                "Medical RAG hit the configured LLM deployment request limit. "
                "RCA should inspect the failing openai_generation span and "
                "Prometheus medical_rag_llm_* counters for observed request rate, "
                "remaining quota, deployment name, and model. "
                f"Query: {(trace_input or '').replace(chr(10), ' ')[:160]}. "
                f"Latest error: {snippet}"
            ),
            span_name="openai_generation",
            trace_id=trace_id,
        )
        if issue:
            created.append(issue)
        break

    return created


# ── Legacy detectors ──────────────────────────────────────────────────────────

def _detect_high_latency(db: Session) -> list[Issue]:
    """Flag spans where recent p95 > HIGH_LATENCY_MULTIPLIER * baseline p95."""
    created = []
    if settings.MIN_TRACES_FOR_LATENCY_BASELINE < 1:
        return created
    rows = (
        db.query(Trace.app_name, Span.name, Span.duration_ms)
        .join(Span, Span.trace_id == Trace.id)
        .filter(Span.duration_ms != None)
        .all()
    )
    by_key: dict[tuple, list[float]] = {}
    for app, span_name, dur in rows:
        by_key.setdefault((app, span_name), []).append(dur)
    for (app, span_name), durations in by_key.items():
        if len(durations) < settings.MIN_TRACES_FOR_LATENCY_BASELINE:
            continue
        durations.sort()
        n = len(durations)
        p95 = durations[min(int(n * 0.95), n - 1)]
        baseline = durations[:max(n // 2, 1)]
        baseline_p95 = baseline[min(int(len(baseline) * 0.95), len(baseline) - 1)]
        if baseline_p95 > 0 and p95 > settings.HIGH_LATENCY_MULTIPLIER * baseline_p95:
            issue = _ensure_issue(
                db, app_name=app,
                issue_type="high_latency", severity="high",
                title=f"High latency in {span_name}",
                description=f"p95 {p95:.0f}ms exceeds {settings.HIGH_LATENCY_MULTIPLIER}x baseline ({baseline_p95:.0f}ms)",
                span_name=span_name,
            )
            if issue:
                created.append(issue)
    return created


def _detect_error_spikes(db: Session) -> list[Issue]:
    """Flag (app, span) combos with recent error rate > 20%."""
    created = []
    recent_cutoff = datetime.utcnow() - timedelta(minutes=10)
    rows = (
        db.query(Trace.app_name, Span.name, Span.status)
        .join(Span, Span.trace_id == Trace.id)
        .filter(Span.started_at >= recent_cutoff)
        .all()
    )
    by_key: dict[tuple, dict] = {}
    for app, span_name, status in rows:
        key = (app, span_name)
        counters = by_key.setdefault(key, {"total": 0, "errors": 0})
        counters["total"] += 1
        if status == "error":
            counters["errors"] += 1
    for (app, span_name), counters in by_key.items():
        total, errors = counters["total"], counters["errors"]
        if total < 5:
            continue
        rate = errors / total
        if rate > 0.2:
            sev = "critical" if rate > 0.5 else "high"
            issue = _ensure_issue(
                db, app_name=app,
                issue_type="error_spike", severity=sev,
                title=f"Error spike in {span_name}",
                description=f"{errors}/{total} calls failed ({rate*100:.0f}%) in the last 10 min",
                span_name=span_name,
            )
            if issue:
                created.append(issue)
    return created


# ── shared helper ─────────────────────────────────────────────────────────────

def _rule_is_disabled(rule_id: str | None) -> bool:
    if not rule_id:
        return False
    return (
        _configured_nfr_ids is not None
        and rule_id in _configured_nfr_ids
        and _enabled_nfr_ids is not None
        and rule_id not in _enabled_nfr_ids
    )


def _ensure_trace_scoped_issue(
    db: Session,
    app_name: str,
    issue_type: str,
    severity: str,
    title: str,
    description: str,
    rule_id: str,
    span_name: str,
    trace_id: str,
) -> Optional[Issue]:
    """Create one visible issue per trace for demo-worthy user-input failures."""
    if _rule_is_disabled(rule_id):
        return None

    fp_key = f"{app_name}:{rule_id}:{span_name}:{trace_id}"
    base_fp = hashlib.sha256(fp_key.encode()).hexdigest()[:16]
    existing = (
        db.query(Issue)
        .filter(Issue.base_fingerprint == base_fp)
        .first()
    )
    if existing:
        return None

    prior = (
        db.query(Issue)
        .filter(Issue.app_name == app_name, Issue.rule_id == rule_id)
        .order_by(Issue.id.desc())
        .first()
    )
    recurrence_count = (prior.recurrence_count + 1) if prior else 0
    occurrence_fp = hashlib.sha256(f"{base_fp}:0".encode()).hexdigest()[:16]
    issue = Issue(
        app_name=app_name,
        issue_type=issue_type,
        rule_id=rule_id,
        severity=severity,
        status="OPEN",
        fingerprint=occurrence_fp,
        base_fingerprint=base_fp,
        previous_issue_id=prior.id if prior else None,
        recurrence_count=recurrence_count,
        title=title,
        description=description,
        span_name=span_name,
        trace_id=trace_id,
    )
    db.add(issue)
    db.flush()
    return issue

def _ensure_issue(
    db: Session,
    app_name: str,
    issue_type: str,
    severity: str,
    title: str,
    description: str,
    rule_id: str = None,
    span_name: str = None,
    trace_id: str = None,
) -> Optional[Issue]:
    """Create issue if no open duplicate exists (deduplicated by base_fingerprint).

    On recurrence (same condition fires after a prior issue was RESOLVED), a brand-new
    issue row is created so the resolved issue's history is preserved.  The new row
    carries previous_issue_id and an incremented recurrence_count so the dashboard can
    show that this condition has been seen before.

    Respects the EscalationRule enabled flag: if an NFR rule exists in the DB and is
    disabled, this returns None without creating an issue.
    """
    # Gate on EscalationRule only for rule IDs that are present in the table.
    # New detectors should not be silently disabled before their seed metadata
    # has been added.
    if _rule_is_disabled(rule_id):
        return None

    fp_key = f"{app_name}:{rule_id or issue_type}:{span_name or ''}"
    base_fp = hashlib.sha256(fp_key.encode()).hexdigest()[:16]

    # Dedup: if there's already an open (non-resolved) issue for this condition, skip
    open_existing = (
        db.query(Issue)
        .filter(Issue.base_fingerprint == base_fp, Issue.status != "RESOLVED")
        .first()
    )
    if open_existing:
        # Refresh trace_id with the latest representative trace so the RCA
        # service always analyses a current trace, not a stale one.
        if trace_id and trace_id != open_existing.trace_id:
            open_existing.trace_id = trace_id
            db.flush()
        return None

    # Find the most recently resolved issue for this condition (if any)
    prior = (
        db.query(Issue)
        .filter(Issue.base_fingerprint == base_fp, Issue.status == "RESOLVED")
        .order_by(Issue.id.desc())
        .first()
    )
    recurrence_count = (prior.recurrence_count + 1) if prior else 0
    previous_issue_id = prior.id if prior else None

    # Each occurrence gets a unique fingerprint so the unique constraint is satisfied
    occurrence_fp = hashlib.sha256(
        f"{base_fp}:{recurrence_count}".encode()
    ).hexdigest()[:16]

    issue = Issue(
        app_name=app_name,
        issue_type=issue_type,
        rule_id=rule_id,
        severity=severity,
        status="OPEN",
        fingerprint=occurrence_fp,
        base_fingerprint=base_fp,
        previous_issue_id=previous_issue_id,
        recurrence_count=recurrence_count,
        title=title,
        description=description,
        span_name=span_name,
        trace_id=trace_id,
    )
    db.add(issue)
    db.flush()
    return issue
